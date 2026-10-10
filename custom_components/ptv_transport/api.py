"""Minimal async client for the PTV Timetable API v3."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

BASE_URL = "https://timetableapi.ptv.vic.gov.au"


class PtvError(Exception):
    """Raised when a PTV request fails."""


class PtvApi:
    """Signs and sends PTV requests.

    signature = HMAC-SHA1(api_key, "<path>?<query>&devid=<id>") as upper-case
    hex, appended as "&signature=<hex>".
    """

    def __init__(self, session: aiohttp.ClientSession, devid: str, api_key: str,
                 max_parallel: int = 4) -> None:
        self._session = session
        self._devid = str(devid)
        self._key = api_key.encode()
        self._sem = asyncio.Semaphore(max_parallel)

    def signed_url(self, path: str, params: list[tuple[str, Any]] | None = None) -> str:
        query = urlencode(params or [])
        request = f"{path}?{query}&devid={self._devid}" if query else f"{path}?devid={self._devid}"
        signature = hmac.new(self._key, request.encode(), hashlib.sha1).hexdigest().upper()
        return f"{BASE_URL}{request}&signature={signature}"

    async def _get(self, path: str, params: list[tuple[str, Any]] | None = None) -> dict:
        url = self.signed_url(path, params)
        async with self._sem:
            try:
                async with self._session.get(
                    url, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise PtvError(f"HTTP {resp.status} for {path}: {body[:200]}")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise PtvError(f"{path}: {err}") from err

    async def stops_near(self, lat: float, lon: float, route_type: int,
                         max_results: int, max_distance: int) -> list[dict]:
        data = await self._get(
            f"/v3/stops/location/{lat:.6f},{lon:.6f}",
            [("route_types", route_type), ("max_results", max_results),
             ("max_distance", max_distance)],
        )
        return data.get("stops") or []

    async def departures(self, route_type: int, stop_id: int, route_id: int,
                         max_results: int = 3) -> dict:
        return await self._get(
            f"/v3/departures/route_type/{route_type}/stop/{stop_id}/route/{route_id}",
            [("max_results", max_results), ("expand", "Run")],
        )

    async def pattern(self, run_ref: str, route_type: int) -> dict:
        return await self._get(
            f"/v3/pattern/run/{quote(str(run_ref), safe='')}/route_type/{route_type}",
            [("expand", "Stop")],
        )
