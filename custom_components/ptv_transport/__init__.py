"""PTV Transport: plans Melbourne public transport journeys with the PTV
Timetable API and publishes each configured route to MQTT as
<topic_prefix>/<route> (retained JSON), plus a sensor per route.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.start import async_at_started
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
)
from .planner import StopCache, plan

_LOGGER = logging.getLogger(__name__)

# A place is either an entity with latitude/longitude attributes
# (person.*, device_tracker.* from your phone's GPS, zone.*) or fixed coords.
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
    DOMAIN: vol.Schema({
        vol.Required(CONF_DEVID): cv.string,
        vol.Required(CONF_API_KEY): cv.string,
        vol.Optional(CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=timedelta(seconds=DEFAULT_SCAN_INTERVAL)):
            cv.time_period,
        vol.Required(CONF_ROUTES): vol.All(cv.ensure_list, [ROUTE_SCHEMA]),
    })
}, extra=vol.ALLOW_EXTRA)


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
    return spec[CONF_LATITUDE], spec[CONF_LONGITUDE], spec.get(CONF_NAME, "")


class RouteCoordinator(DataUpdateCoordinator[dict]):
    """Plans one route and publishes it to MQTT on every refresh."""

    def __init__(self, hass: HomeAssistant, api: PtvApi, cache: StopCache,
                 route: dict, topic: str, interval: timedelta) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {route[CONF_NAME]}",
                         update_interval=interval)
        self.api = api
        self.cache = cache
        self.route = route
        self.slug = slugify(route[CONF_NAME])
        self.topic = topic
        self.modes = [MODES[m] for m in route[CONF_MODES]]
        self.max_walk = {
            MODES[m]: route[CONF_MAX_WALK].get(m, DEFAULT_MAX_WALK[m])
            for m in route[CONF_MODES]
        }

    async def _async_update_data(self) -> dict:
        origin = _resolve(self.hass, self.route[CONF_FROM])
        dest = _resolve(self.hass, self.route[CONF_TO])
        if origin is None or dest is None:
            raise UpdateFailed("origin or destination has no latitude/longitude yet")

        try:
            journeys = await plan(
                self.api, self.cache, origin[:2], dest[:2], self.modes, self.max_walk,
                self.route[CONF_STOPS_PER_MODE], self.route[CONF_ONLY_RELEVANT],
                self.route[CONF_MAX_JOURNEYS], dt_util.get_default_time_zone(),
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
    conf = config[DOMAIN]
    api = PtvApi(async_get_clientsession(hass), conf[CONF_DEVID], conf[CONF_API_KEY])
    cache = StopCache()
    prefix = conf[CONF_TOPIC_PREFIX].rstrip("/")

    coordinators: dict[str, RouteCoordinator] = {}
    for route in conf[CONF_ROUTES]:
        slug = slugify(route[CONF_NAME])
        if slug in coordinators:
            _LOGGER.error("Duplicate route name %s, skipped", route[CONF_NAME])
            continue
        coord = RouteCoordinator(hass, api, cache, route, f"{prefix}/{slug}",
                                 conf[CONF_SCAN_INTERVAL])
        coord.async_add_listener(lambda: None)   # keep polling even without sensors
        coordinators[slug] = coord
    hass.data[DOMAIN] = coordinators

    async def _first_refresh(_hass: HomeAssistant) -> None:
        if await mqtt.async_wait_for_mqtt_client(hass):
            # Index of routes, so a device can discover what's available
            await mqtt.async_publish(hass, f"{prefix}/routes",
                                     json.dumps(list(coordinators)), qos=0, retain=True)
        for coord in coordinators.values():
            await coord.async_refresh()

    async_at_started(hass, _first_refresh)

    async def _refresh(call: ServiceCall) -> None:
        wanted = call.data.get("route")
        for slug, coord in coordinators.items():
            if wanted in (None, slug):
                await coord.async_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _refresh,
        schema=vol.Schema({vol.Optional("route"): cv.string}),
    )

    hass.async_create_task(async_load_platform(hass, "sensor", DOMAIN, {}, config))
    return True
