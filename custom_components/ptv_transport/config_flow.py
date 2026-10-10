"""Setup screens: PTV credentials, settings, and one sub-entry per route."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .api import PtvApi, PtvError
from .const import (
    CONF_API_KEY,
    CONF_DEVID,
    CONF_FROM,
    CONF_FROM_ENTITY,
    CONF_FROM_LOCATION,
    CONF_MAX_JOURNEYS,
    CONF_MAX_WALK,
    CONF_MODES,
    CONF_ONLY_RELEVANT,
    CONF_ROUTES,
    CONF_SCAN_SECONDS,
    CONF_STOPS_PER_MODE,
    CONF_TO,
    CONF_TO_ENTITY,
    CONF_TO_LOCATION,
    CONF_TO_NAME,
    CONF_TOPIC_PREFIX,
    DEFAULT_MAX_JOURNEYS,
    DEFAULT_MAX_WALK,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STOPS_PER_MODE,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
    MODES,
    SECTION_ADVANCED,
    SECTION_WALKING,
    SUBENTRY_ROUTE,
)

LOCATION_ENTITY_DOMAINS = ["person", "device_tracker", "zone"]


async def _validate_credentials(hass, devid: str, api_key: str) -> str | None:
    """Make one cheap signed request. Returns an error key or None."""
    api = PtvApi(async_get_clientsession(hass), devid, api_key)
    try:
        await api.stops_near(-37.8183, 144.9671, 0, 1, 300)   # Flinders Street
    except PtvError as err:
        return "invalid_auth" if "HTTP 403" in str(err) or "HTTP 401" in str(err) else "cannot_connect"
    return None


def _credentials_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_DEVID, default=defaults.get(CONF_DEVID, "")): TextSelector(),
        vol.Required(CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")):
            TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    })


class PtvConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add the PTV account (one per API user ID)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PtvOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROUTE: RouteSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            devid = user_input[CONF_DEVID].strip()
            key = user_input[CONF_API_KEY].strip()
            await self.async_set_unique_id(devid)
            self._abort_if_unique_id_configured()
            if (error := await _validate_credentials(self.hass, devid, key)) is None:
                return self.async_create_entry(
                    title=f"PTV ({devid})",
                    data={CONF_DEVID: devid, CONF_API_KEY: key},
                    options={CONF_TOPIC_PREFIX: DEFAULT_TOPIC_PREFIX,
                             CONF_SCAN_SECONDS: DEFAULT_SCAN_INTERVAL},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None
                                     ) -> ConfigFlowResult:
        """Change the user ID or API key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            devid = user_input[CONF_DEVID].strip()
            key = user_input[CONF_API_KEY].strip()
            if (error := await _validate_credentials(self.hass, devid, key)) is None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=devid, title=f"PTV ({devid})",
                    data={CONF_DEVID: devid, CONF_API_KEY: key},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """One-time import of an existing configuration.yaml setup."""
        devid = str(import_data[CONF_DEVID])
        await self.async_set_unique_id(devid)
        self._abort_if_unique_id_configured()

        subentries: list[ConfigSubentryData] = []
        seen: set[str] = set()
        for route in import_data[CONF_ROUTES]:
            slug = slugify(route[CONF_NAME])
            if slug in seen:      # route names must be unique
                continue
            seen.add(slug)
            subentries.append(ConfigSubentryData(
                data=route, subentry_type=SUBENTRY_ROUTE,
                title=route[CONF_NAME], unique_id=slug,
            ))
        return self.async_create_entry(
            title=f"PTV ({devid})",
            data={CONF_DEVID: devid, CONF_API_KEY: import_data[CONF_API_KEY]},
            options={CONF_TOPIC_PREFIX: import_data[CONF_TOPIC_PREFIX],
                     CONF_SCAN_SECONDS: import_data[CONF_SCAN_SECONDS]},
            subentries=subentries,
        )


class PtvOptionsFlow(OptionsFlow):
    """MQTT topic prefix and refresh interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_TOPIC_PREFIX] = user_input[CONF_TOPIC_PREFIX].strip("/ ") \
                or DEFAULT_TOPIC_PREFIX
            user_input[CONF_SCAN_SECONDS] = int(user_input[CONF_SCAN_SECONDS])
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_TOPIC_PREFIX,
                             default=opts.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)):
                    TextSelector(),
                vol.Required(CONF_SCAN_SECONDS,
                             default=opts.get(CONF_SCAN_SECONDS, DEFAULT_SCAN_INTERVAL)):
                    NumberSelector(NumberSelectorConfig(
                        min=30, max=3600, step=10, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX)),
            }),
        )


def _location_defaults(spec: Any, hass) -> tuple[str | None, dict, str]:
    """Split a stored location into (entity, map point, name) form defaults."""
    if isinstance(spec, str):
        return spec, {CONF_LATITUDE: hass.config.latitude,
                      CONF_LONGITUDE: hass.config.longitude}, ""
    if isinstance(spec, dict):
        return None, {CONF_LATITUDE: spec[CONF_LATITUDE],
                      CONF_LONGITUDE: spec[CONF_LONGITUDE]}, spec.get(CONF_NAME, "")
    return None, {CONF_LATITUDE: hass.config.latitude,
                  CONF_LONGITUDE: hass.config.longitude}, ""


def _route_schema(hass, d: dict[str, Any]) -> vol.Schema:
    """The route form, pre-filled from stored route data `d`."""
    from_entity, from_point, _ = _location_defaults(d.get(CONF_FROM), hass)
    to_entity, to_point, to_name = _location_defaults(d.get(CONF_TO), hass)
    walk = {**DEFAULT_MAX_WALK, **d.get(CONF_MAX_WALK, {})}

    def optional_entity(key: str, value: str | None):
        return vol.Optional(key, description={"suggested_value": value}) if value \
            else vol.Optional(key)

    entity_selector = EntitySelector(EntitySelectorConfig(domain=LOCATION_ENTITY_DOMAINS))
    point_selector = LocationSelector(LocationSelectorConfig(radius=False))
    metres = NumberSelector(NumberSelectorConfig(
        min=100, max=5000, step=50, unit_of_measurement="m", mode=NumberSelectorMode.BOX))

    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): TextSelector(),
        optional_entity(CONF_FROM_ENTITY, from_entity): entity_selector,
        vol.Required(CONF_FROM_LOCATION, default=from_point): point_selector,
        optional_entity(CONF_TO_ENTITY, to_entity): entity_selector,
        vol.Required(CONF_TO_LOCATION, default=to_point): point_selector,
        vol.Optional(CONF_TO_NAME, default=to_name): TextSelector(),
        vol.Required(CONF_MODES, default=d.get(CONF_MODES, ["train", "tram", "bus"])):
            SelectSelector(SelectSelectorConfig(
                options=list(MODES), multiple=True, translation_key="mode",
                mode=SelectSelectorMode.LIST)),
        vol.Required(SECTION_WALKING): section(vol.Schema({
            vol.Required(mode, default=walk[mode]): metres for mode in MODES
        }), {"collapsed": True}),
        vol.Required(SECTION_ADVANCED): section(vol.Schema({
            vol.Required(CONF_ONLY_RELEVANT, default=d.get(CONF_ONLY_RELEVANT, True)):
                BooleanSelector(),
            vol.Required(CONF_MAX_JOURNEYS,
                         default=d.get(CONF_MAX_JOURNEYS, DEFAULT_MAX_JOURNEYS)):
                NumberSelector(NumberSelectorConfig(min=1, max=20, step=1,
                                                    mode=NumberSelectorMode.BOX)),
            vol.Required(CONF_STOPS_PER_MODE,
                         default=d.get(CONF_STOPS_PER_MODE, DEFAULT_STOPS_PER_MODE)):
                NumberSelector(NumberSelectorConfig(min=1, max=10, step=1,
                                                    mode=NumberSelectorMode.BOX)),
        }), {"collapsed": True}),
    })


def _route_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Form input -> stored route data (same shape as the YAML routes)."""
    def place(entity_key: str, point_key: str, name: str = "") -> Any:
        if user_input.get(entity_key):
            return user_input[entity_key]
        point = user_input[point_key]
        return {CONF_LATITUDE: point[CONF_LATITUDE], CONF_LONGITUDE: point[CONF_LONGITUDE],
                CONF_NAME: name}

    modes = user_input[CONF_MODES]
    walking, advanced = user_input[SECTION_WALKING], user_input[SECTION_ADVANCED]
    return {
        CONF_NAME: user_input[CONF_NAME].strip(),
        CONF_FROM: place(CONF_FROM_ENTITY, CONF_FROM_LOCATION),
        CONF_TO: place(CONF_TO_ENTITY, CONF_TO_LOCATION,
                       user_input.get(CONF_TO_NAME, "").strip()),
        CONF_MODES: modes,
        CONF_MAX_WALK: {m: int(walking[m]) for m in modes},
        CONF_ONLY_RELEVANT: advanced[CONF_ONLY_RELEVANT],
        CONF_MAX_JOURNEYS: int(advanced[CONF_MAX_JOURNEYS]),
        CONF_STOPS_PER_MODE: int(advanced[CONF_STOPS_PER_MODE]),
    }


class RouteSubentryFlow(ConfigSubentryFlow):
    """Add or edit one route ("Add route" on the integration page)."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None
                              ) -> SubentryFlowResult:
        return await self._route_form("user", user_input, {}, None)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None
                                     ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        return await self._route_form("reconfigure", user_input, dict(subentry.data),
                                      subentry.subentry_id)

    async def _route_form(self, step_id: str, user_input: dict[str, Any] | None,
                          current: dict[str, Any], subentry_id: str | None
                          ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _route_data(user_input)
            slug = slugify(data[CONF_NAME])
            taken = {
                s.unique_id for s in self._get_entry().subentries.values()
                if s.subentry_type == SUBENTRY_ROUTE and s.subentry_id != subentry_id
            }
            if not slug:
                errors[CONF_NAME] = "name_required"
            elif slug in taken:
                errors[CONF_NAME] = "duplicate_name"
            elif not data[CONF_MODES]:
                errors[CONF_MODES] = "no_modes"
            elif subentry_id is None:
                return self.async_create_entry(title=data[CONF_NAME], data=data,
                                               unique_id=slug)
            else:
                return self.async_update_and_abort(
                    self._get_entry(), self._get_reconfigure_subentry(),
                    title=data[CONF_NAME], data=data, unique_id=slug,
                )
            current = data

        return self.async_show_form(
            step_id=step_id,
            data_schema=_route_schema(self.hass, current),
            errors=errors,
        )
