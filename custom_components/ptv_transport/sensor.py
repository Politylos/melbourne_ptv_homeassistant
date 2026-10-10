"""One timestamp sensor per route: when the best journey departs."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import PtvConfigEntry, RouteCoordinator
from .const import DOMAIN

ICONS = {"train": "mdi:train", "tram": "mdi:tram", "bus": "mdi:bus",
         "vline": "mdi:train-variant", "night_bus": "mdi:bus-clock"}


async def async_setup_entry(hass: HomeAssistant, entry: PtvConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, coord in entry.runtime_data.coordinators.items():
        async_add_entities([RouteSensor(coord)], config_subentry_id=subentry_id)


class RouteSensor(CoordinatorEntity[RouteCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_translation_key = "next_departure"

    def __init__(self, coordinator: RouteCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.subentry_id}_next_departure"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.subentry_id)},
            name=f"PTV {coordinator.route[CONF_NAME]}",
            manufacturer="Public Transport Victoria",
            model="Route",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _best(self) -> dict | None:
        journeys = (self.coordinator.data or {}).get("journeys") or []
        return journeys[0] if journeys else None

    @property
    def native_value(self) -> datetime | None:
        best = self._best
        return dt_util.utc_from_timestamp(best["departure_ts"]) if best else None

    @property
    def icon(self) -> str:
        best = self._best
        return ICONS.get(best["mode"], "mdi:bus-clock") if best else "mdi:bus-clock"

    @property
    def extra_state_attributes(self) -> dict:
        # Short summary only; the full structure (with stops) is on MQTT.
        data = self.coordinator.data or {}
        return {
            "mqtt_topic": self.coordinator.topic,
            "journeys": [
                {
                    "mode": j["mode"], "line": j["number"] or j["line"],
                    "towards": j["towards"], "from": j["origin"]["name"],
                    "platform": j["platform"], "departure": j["departure"],
                    "delay_min": j["delay_min"], "to": j["destination"]["name"],
                    "arrival": j["arrival"],
                }
                for j in data.get("journeys") or []
            ],
            "updated": data.get("updated"),
        }
