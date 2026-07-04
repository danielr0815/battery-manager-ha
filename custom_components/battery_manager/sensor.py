"""Sensor platform for the Battery Manager integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_GRID_EXPORT_KWH,
    ATTR_LAST_UPDATE,
    DOMAIN,
    ENTITY_GRID_IMPORT_FORECAST,
    ENTITY_HOURS_TO_MAX_SOC,
    ENTITY_LOST_SURPLUS,
    ENTITY_MAX_SOC_FORECAST,
    ENTITY_MIN_SOC_FORECAST,
    ENTITY_SOC_THRESHOLD,
)
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity

SENSOR_DESCRIPTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": ENTITY_SOC_THRESHOLD,
        "data_key": "soc_threshold_percent",
        "translation_key": "soc_threshold",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-arrow-down",
    },
    {
        "key": ENTITY_MIN_SOC_FORECAST,
        "data_key": "min_soc_forecast_percent",
        "translation_key": "min_soc_forecast",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-low",
    },
    {
        "key": ENTITY_MAX_SOC_FORECAST,
        "data_key": "max_soc_forecast_percent",
        "translation_key": "max_soc_forecast",
        "unit": PERCENTAGE,
        "icon": "mdi:battery-high",
    },
    {
        "key": ENTITY_HOURS_TO_MAX_SOC,
        "data_key": "hours_to_max_soc",
        "translation_key": "hours_to_max_soc",
        "unit": UnitOfTime.HOURS,
        "icon": "mdi:clock-outline",
    },
    {
        "key": ENTITY_GRID_IMPORT_FORECAST,
        "data_key": "grid_import_kwh",
        "translation_key": "grid_import_forecast",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:transmission-tower-import",
    },
    {
        "key": ENTITY_LOST_SURPLUS,
        "data_key": "lost_surplus_kwh",
        "translation_key": "lost_surplus",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:transmission-tower-export",
    },
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Battery Manager sensors."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BatteryManagerSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class BatteryManagerSensor(BatteryManagerEntity, SensorEntity):
    """A value from the last planning run."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BatteryManagerCoordinator, description: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, description["key"])
        self._data_key = description["data_key"]
        self._attr_translation_key = description["translation_key"]
        self._attr_native_unit_of_measurement = description["unit"]
        self._attr_icon = description.get("icon")
        if "device_class" in description:
            self._attr_device_class = description["device_class"]

    @property
    def native_value(self) -> float | int | None:
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get(self._data_key)
        if isinstance(value, float):
            return round(value, 2)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs = {ATTR_LAST_UPDATE: str(data.get("last_update", ""))}
        if self._data_key == "grid_import_kwh":
            attrs[ATTR_GRID_EXPORT_KWH] = data.get("grid_export_kwh")
        return attrs
