"""PTV Transport: plans Melbourne public transport journeys with the PTV
Timetable API and publishes each route to MQTT as <topic_prefix>/<route>
(retained JSON), plus a sensor per route.

Set up from Settings -> Devices & services (one entry per PTV account, one
"route" sub-entry per route). A `ptv_transport:` block in configuration.yaml
is imported once into that UI setup.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import PtvApi, PtvError
from .const import (
    CONF_API_KEY,
    CONF_DEVID,
    CONF_FROM,
    CONF_MAX_JOURNEYS,
    CONF_MAX_WALK,
    CONF_MODES,
    CONF_ONLY_RELEVANT,
    CONF_ROUTES,
    CONF_SCAN_SECONDS,
    CONF_STOPS_PER_MODE,
    CONF_TO,
    CONF_TOPIC_PREFIX,
    DEFAULT_MAX_JOURNEYS,
    DEFAULT_MAX_WALK,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STOPS_PER_MODE,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
    MODES,
    SERVICE_REFRESH,
    SUBENTRY_ROUTE,
)
from .planner import StopCache, plan

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

# ---- configuration.yaml (imported once into the UI setup) -----------------
LOCATION_SCHEMA = vol.Any(
    cv.entity_id,
    vol.Schema({
        vol.Required(CONF_LATITUDE): cv.latitude,
        vol.Required(CONF_LONGITUDE): cv.longitude,
        vol.Optional(CONF_NAME): cv.string,
    }),
)

ROUTE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_FROM): LOCATION_SCHEMA,
    vol.Required(CONF_TO): LOCATION_SCHEMA,
    vol.Optional(CONF_MODES, default=["train", "tram", "bus"]):
        vol.All(cv.ensure_list, [vol.In(MODES)]),
    vol.Optional(CONF_MAX_WALK, default={}): {vol.In(MODES): cv.positive_int},
    vol.Optional(CONF_ONLY_RELEVANT, default=True): cv.boolean,
    vol.Optional(CONF_MAX_JOURNEYS, default=DEFAULT_MAX_JOURNEYS):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
    vol.Optional(CONF_STOPS_PER_MODE, default=DEFAULT_STOPS_PER_MODE):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
})

CONFIG_SCHEMA = vol.Schema({
    vol.Optional(DOMAIN): vol.Schema({
        vol.Required(CONF_DEVID): cv.string,
        vol.Required(CONF_API_KEY): cv.string,
        vol.Optional(CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=timedelta(seconds=DEFAULT_SCAN_INTERVAL)):
            cv.time_period,
        vol.Optional(CONF_ROUTES, default=[]): vol.All(cv.ensure_list, [ROUTE_SCHEMA]),
    })
}, extra=vol.ALLOW_EXTRA)


@dataclass
class PtvRuntimeData:
    coordinators: dict[str, RouteCoordinator]   # subentry_id -> coordinator


type PtvConfigEntry = ConfigEntry[PtvRuntimeData]


def _resolve(hass: HomeAssistant, spec) -> tuple[float, float, str] | None:
    """(lat, lon, name) for an entity id or a fixed location."""
    if isinstance(spec, str):
        state = hass.states.get(spec)
        if state is None:
            return None
        lat, lon = state.attributes.get("latitude"), state.attributes.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon), state.name
    return spec[CONF_LATITUDE], spec[CONF_LONGITUDE], spec.get(CONF_NAME) or ""


class RouteCoordinator(DataUpdateCoordinator[dict]):
    """Plans one route and publishes it to MQTT on every refresh."""

    def __init__(self, hass: HomeAssistant, entry: PtvConfigEntry, api: PtvApi,
                 cache: StopCache, subentry_id: str, route: dict, topic: str,
                 interval: timedelta) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry,
                         name=f"{DOMAIN} {route[CONF_NAME]}", update_interval=interval)
        self.api = api
        self.cache = cache
        self.subentry_id = subentry_id
        self.route = route
        self.slug = slugify(route[CONF_NAME])
        self.topic = topic
        self.modes = [MODES[m] for m in route[CONF_MODES]]
        walk = route.get(CONF_MAX_WALK) or {}
        self.max_walk = {MODES[m]: walk.get(m, DEFAULT_MAX_WALK[m]) for m in route[CONF_MODES]}

    async def _async_update_data(self) -> dict:
        origin = _resolve(self.hass, self.route[CONF_FROM])
        dest = _resolve(self.hass, self.route[CONF_TO])
        if origin is None or dest is None:
            raise UpdateFailed("origin or destination has no latitude/longitude yet")

        try:
            journeys = await plan(
                self.api, self.cache, origin[:2], dest[:2], self.modes, self.max_walk,
                self.route.get(CONF_STOPS_PER_MODE, DEFAULT_STOPS_PER_MODE),
                self.route.get(CONF_ONLY_RELEVANT, True),
                self.route.get(CONF_MAX_JOURNEYS, DEFAULT_MAX_JOURNEYS),
                dt_util.get_default_time_zone(),
            )
        except PtvError as err:
            raise UpdateFailed(str(err)) from err

        now = dt_util.now()
        payload = {
            "route": self.slug,
            "name": self.route[CONF_NAME],
            "updated": now.isoformat(timespec="seconds"),
            "updated_ts": int(now.timestamp()),
            "from": {"lat": origin[0], "lon": origin[1], "name": origin[2]},
            "to": {"lat": dest[0], "lon": dest[1], "name": dest[2]},
            "count": len(journeys),
            "journeys": journeys,
        }

        if await mqtt.async_wait_for_mqtt_client(self.hass):
            await mqtt.async_publish(
                self.hass, self.topic, json.dumps(payload, separators=(",", ":")),
                qos=0, retain=True,
            )
        else:
            _LOGGER.warning("MQTT not available; %s not published", self.topic)
        return payload


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN in config:
        conf = dict(config[DOMAIN])
        conf[CONF_SCAN_SECONDS] = int(conf.pop(CONF_SCAN_INTERVAL).total_seconds())
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf))
        _LOGGER.warning(
            "ptv_transport in configuration.yaml has been imported into the UI "
            "setup (Settings -> Devices & services). Later YAML edits are ignored; "
            "you can remove the ptv_transport block."
        )

    async def _refresh(call: ServiceCall) -> None:
        wanted = call.data.get("route")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            for coord in entry.runtime_data.coordinators.values():
                if wanted in (None, coord.slug):
                    await coord.async_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _refresh,
        schema=vol.Schema({vol.Optional("route"): cv.string}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PtvConfigEntry) -> bool:
    api = PtvApi(async_get_clientsession(hass), entry.data[CONF_DEVID],
                 entry.data[CONF_API_KEY])
    cache = StopCache()
    prefix = entry.options.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX).strip("/")
    interval = timedelta(seconds=entry.options.get(CONF_SCAN_SECONDS, DEFAULT_SCAN_INTERVAL))

    coordinators: dict[str, RouteCoordinator] = {}
    for subentry_id, sub in entry.subentries.items():
        if sub.subentry_type != SUBENTRY_ROUTE:
            continue
        route = dict(sub.data)
        coordinators[subentry_id] = RouteCoordinator(
            hass, entry, api, cache, subentry_id, route,
            f"{prefix}/{slugify(route[CONF_NAME])}", interval)
    entry.runtime_data = PtvRuntimeData(coordinators)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async def _first_refresh() -> None:
        if await mqtt.async_wait_for_mqtt_client(hass):
            # Index of routes, so a device can discover what's available
            await mqtt.async_publish(
                hass, f"{prefix}/routes",
                json.dumps([c.slug for c in coordinators.values()]), qos=0, retain=True)
        for coord in coordinators.values():
            await coord.async_refresh()

    # Don't block startup on the API; sensors fill in when the first plan lands.
    entry.async_create_background_task(hass, _first_refresh(), f"{DOMAIN} first refresh")
    return True


async def _async_reload(hass: HomeAssistant, entry: PtvConfigEntry) -> None:
    """Options changed or a route was added, edited or removed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PtvConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
