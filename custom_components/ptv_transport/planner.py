"""Journey planning on top of the PTV API.

Same approach as the ESP sketch (ptv.c):
  1. nearest stops of each mode around the origin and the destination
  2. pair stops that share a route (least walking per route)
  3. next departures on that route, skipping ones you can't walk to in time
  4. load each run's stopping pattern and keep it only if it calls at the
     destination stop after the origin stop
Kept free of Home Assistant imports so it can be tested on its own.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo

from .api import PtvApi, PtvError
from .const import (
    CANDIDATES_TRIED,
    MODE_NAMES,
    RUNS_TRIED,
    STOP_CACHE_SECONDS,
    WALK_SPEED_MPS,
)


@dataclass
class NearbyStop:
    stop_id: int
    route_type: int
    name: str
    distance_m: int
    routes: dict[int, tuple[str, str]] = field(default_factory=dict)  # id -> (name, number)


@dataclass
class Candidate:
    origin: NearbyStop
    dest: NearbyStop
    route_id: int
    walk_m: int

    @property
    def route_name(self) -> str:
        return self.origin.routes[self.route_id][0]

    @property
    def route_number(self) -> str:
        return self.origin.routes[self.route_id][1]


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_utc(value: str | None) -> int | None:
    """'2026-10-10T05:15:00Z' -> epoch seconds."""
    if not value:
        return None
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fmt_local(ts: int | None, tz: tzinfo) -> str | None:
    """Epoch -> '3:55pm' in the given time zone."""
    if ts is None:
        return None
    t = datetime.fromtimestamp(ts, tz)
    h12 = t.hour % 12 or 12
    return f"{h12}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


class StopCache:
    """Stops don't move: cache /stops/location results around ~100 m cells."""

    def __init__(self) -> None:
        self._data: dict[tuple, tuple[float, list[dict]]] = {}

    def get(self, key: tuple) -> list[dict] | None:
        hit = self._data.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def put(self, key: tuple, stops: list[dict]) -> None:
        self._data[key] = (time.monotonic() + STOP_CACHE_SECONDS, stops)


async def _stops_for_mode(api: PtvApi, cache: StopCache, lat: float, lon: float,
                          route_type: int, radius: int, count: int) -> list[NearbyStop]:
    clat, clon = round(lat, 3), round(lon, 3)
    key = (clat, clon, route_type, radius)
    raw = cache.get(key)
    if raw is None:
        # Search around the cell centre a bit wider, then measure from the
        # real position, so the cache stays valid as you move within the cell.
        raw = await api.stops_near(clat, clon, route_type, count * 2, radius + 150)
        cache.put(key, raw)

    stops = []
    for s in raw:
        d = distance_m(lat, lon, s["stop_latitude"], s["stop_longitude"])
        if d > radius:
            continue
        stop = NearbyStop(int(s["stop_id"]), int(s["route_type"]),
                          (s.get("stop_name") or "").strip(), int(d))
        for r in s.get("routes") or []:
            # Metro stations also list V/Line routes: keep the stop's own mode
            if r.get("route_type") == stop.route_type:
                stop.routes.setdefault(int(r["route_id"]), (
                    (r.get("route_name") or "").strip(),
                    (r.get("route_number") or "").strip(),
                ))
        stops.append(stop)
    stops.sort(key=lambda s: s.distance_m)
    return stops[:count]


async def find_nearby(api: PtvApi, cache: StopCache, lat: float, lon: float,
                      modes: list[int], max_walk: dict[int, int],
                      count: int) -> list[NearbyStop]:
    results = await asyncio.gather(*(
        _stops_for_mode(api, cache, lat, lon, rt, max_walk[rt], count) for rt in modes
    ))
    return [s for group in results for s in group]


def plan_candidates(origin: list[NearbyStop], dest: list[NearbyStop]) -> list[Candidate]:
    best: dict[int, Candidate] = {}
    for o in origin:
        for route_id in o.routes:
            for d in dest:
                if d.route_type != o.route_type or d.stop_id == o.stop_id:
                    continue
                if route_id not in d.routes:
                    continue
                walk = o.distance_m + d.distance_m
                if route_id not in best or walk < best[route_id].walk_m:
                    best[route_id] = Candidate(o, d, route_id, walk)
    return sorted(best.values(), key=lambda c: c.walk_m)


def _walk_pattern(pattern: dict, cand: Candidate, only_relevant: bool,
                  live_delay: int) -> dict:
    """Follow one run's stopping pattern from origin to destination.

    The pattern often lacks live estimates even when the departures endpoint
    has one, so `live_delay` (seconds) is applied to stops without their own.
    """
    names = pattern.get("stops") or {}
    out = {"reached_origin": False, "at_dest": False, "dest_before_origin": False,
           "stops": [], "platform": None, "scheduled": None, "estimated": None,
           "arrival": None}

    for dep in pattern.get("departures") or []:
        sid = int(dep["stop_id"])
        sch = parse_utc(dep.get("scheduled_departure_utc"))
        est = parse_utc(dep.get("estimated_departure_utc"))
        if est is None and sch is not None and live_delay:
            est = sch + live_delay

        if not out["reached_origin"]:
            if sid == cand.dest.stop_id:
                out["dest_before_origin"] = True
            if sid != cand.origin.stop_id:
                continue
            out.update(reached_origin=True, scheduled=sch, estimated=est,
                       platform=dep.get("platform_number"))
        elif out["at_dest"] and only_relevant:
            break

        if sid == cand.dest.stop_id:
            out["at_dest"] = True
            out["arrival"] = est or sch

        stop = names.get(str(sid)) or {}
        out["stops"].append({
            "id": sid,
            "name": (stop.get("stop_name") or "").strip(),
            "ts": est or sch,
            "platform": dep.get("platform_number"),
        })
    return out


async def _plan_candidate(api: PtvApi, cand: Candidate, now: int,
                          only_relevant: bool, tz: tzinfo) -> dict | None:
    rt = cand.origin.route_type
    data = await api.departures(rt, cand.origin.stop_id, cand.route_id)
    runs = data.get("runs") or {}
    earliest = now + cand.origin.distance_m / WALK_SPEED_MPS

    deps = []
    for d in data.get("departures") or []:
        sch = parse_utc(d.get("scheduled_departure_utc"))
        est = parse_utc(d.get("estimated_departure_utc"))
        when = est or sch
        if when and when >= earliest:
            delay = est - sch if est and sch else 0
            deps.append((when, delay, d))
    deps.sort(key=lambda x: x[0])   # PTV groups results by direction

    bad_dirs: set[int] = set()
    tried = 0
    for _, delay, d in deps:
        if d.get("direction_id") in bad_dirs:
            continue
        if tried >= RUNS_TRIED:
            break
        tried += 1

        run_ref = d["run_ref"]
        walked = _walk_pattern(await api.pattern(run_ref, rt), cand, only_relevant, delay)
        if walked["reached_origin"] and walked["at_dest"]:
            return _journey(cand, run_ref, runs.get(run_ref) or {}, walked, now, tz)
        if walked["dest_before_origin"]:
            bad_dirs.add(d.get("direction_id"))
    return None


def _journey(cand: Candidate, run_ref: str, run: dict, w: dict, now: int,
             tz: tzinfo) -> dict:
    """The published departure structure for one journey."""
    departure = w["estimated"] or w["scheduled"]
    delay = (w["estimated"] - w["scheduled"]) // 60 if w["estimated"] and w["scheduled"] else 0
    return {
        "mode": MODE_NAMES.get(cand.origin.route_type, "unknown"),
        "route_type": cand.origin.route_type,
        "route_id": cand.route_id,
        "line": cand.route_name,
        "number": cand.route_number,
        "run_ref": run_ref,
        "towards": (run.get("destination_name") or "").strip(),
        "express_stops": run.get("express_stop_count", 0),
        "origin": {"id": cand.origin.stop_id, "name": cand.origin.name,
                   "walk_m": cand.origin.distance_m},
        "destination": {"id": cand.dest.stop_id, "name": cand.dest.name,
                        "walk_m": cand.dest.distance_m},
        "platform": w["platform"] or "",
        "scheduled_ts": w["scheduled"],
        "estimated_ts": w["estimated"],
        "departure_ts": departure,
        "departure": fmt_local(departure, tz),
        "minutes": max(0, (departure - now) // 60),
        "delay_min": delay,
        "arrival_ts": w["arrival"],
        "arrival": fmt_local(w["arrival"], tz),
        "stops": [{**s, "time": fmt_local(s["ts"], tz)} for s in w["stops"]],
    }


async def plan(api: PtvApi, cache: StopCache, origin: tuple[float, float],
               dest: tuple[float, float], modes: list[int], max_walk: dict[int, int],
               stops_per_mode: int, only_relevant: bool, max_journeys: int,
               tz: tzinfo) -> list[dict]:
    """Return up to `max_journeys` journeys, best arrival first."""
    now = int(datetime.now(timezone.utc).timestamp())
    near_origin, near_dest = await asyncio.gather(
        find_nearby(api, cache, *origin, modes, max_walk, stops_per_mode),
        find_nearby(api, cache, *dest, modes, max_walk, stops_per_mode),
    )
    candidates = plan_candidates(near_origin, near_dest)[:CANDIDATES_TRIED]

    results = await asyncio.gather(
        *(_plan_candidate(api, c, now, only_relevant, tz) for c in candidates),
        return_exceptions=True,
    )
    journeys = [r for r in results if isinstance(r, dict)]
    errors = [r for r in results if isinstance(r, PtvError)]
    if not journeys and errors:
        raise errors[0]
    for r in results:   # don't swallow programming errors
        if isinstance(r, Exception) and not isinstance(r, PtvError):
            raise r

    # Best = earliest arrival at the actual destination (incl. the walk)
    journeys.sort(key=lambda j: j["arrival_ts"] + j["destination"]["walk_m"] / WALK_SPEED_MPS)
    return journeys[:max_journeys]
