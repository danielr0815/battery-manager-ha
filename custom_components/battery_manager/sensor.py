"""Sensor platform for the Battery Manager integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_GRID_EXPORT_KWH,
    ATTR_LAST_UPDATE,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    DOMAIN,
    ENTITY_GRID_IMPORT_FORECAST,
    ENTITY_HOURS_TO_MAX_SOC,
    ENTITY_LOST_SURPLUS,
    ENTITY_MAX_SOC_FORECAST,
    ENTITY_MIN_SOC_FORECAST,
    ENTITY_SOC_FORECAST_CURVE,
    ENTITY_SOC_THRESHOLD,
    ENTITY_SUPPORT_DC24_MODE,
    ENTITY_SUPPORT_DC48_MODE,
    SUPPORT_MODE_AUTO,
    SUPPORT_MODE_MANUAL,
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
    entities: list[SensorEntity] = [
        BatteryManagerSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(BatteryManagerSocForecastSensor(coordinator))
    # Manual/automatic mode per support PSU (F-N2) — only when the
    # respective switch is configured; a leftover sensor of a removed
    # switch is dropped from the registry instead of lingering.
    ent_reg = er.async_get(hass)
    for entity_key, conf_key, data_key in (
        (ENTITY_SUPPORT_DC24_MODE, CONF_SUPPORT_DC24_SWITCH, "support_dc24_mode"),
        (ENTITY_SUPPORT_DC48_MODE, CONF_SUPPORT_DC48_SWITCH, "support_dc48_mode"),
    ):
        if coordinator.raw_config.get(conf_key):
            entities.append(SupportModeSensor(coordinator, entity_key, data_key))
        else:
            stale = ent_reg.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{entity_key}"
            )
            if stale:
                ent_reg.async_remove(stale)
    async_add_entities(entities)


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


class SupportModeSensor(BatteryManagerEntity, SensorEntity):
    """Manual/automatic control mode of a support PSU (F-N2).

    'manual' while the PSU was switched on externally: the integration
    keeps hands off until it is switched off externally again.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [SUPPORT_MODE_AUTO, SUPPORT_MODE_MANUAL]
    _attr_icon = "mdi:hand-back-right-outline"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, key: str, data_key: str
    ) -> None:
        super().__init__(coordinator, key)
        self._data_key = data_key
        self._attr_translation_key = key

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._data_key)


class BatteryManagerSocForecastSensor(BatteryManagerEntity, SensorEntity):
    """Forecasted SOC curve: state = SOC in one hour, attribute = full curve.

    The `forecast` attribute contains [{t, soc}, ...] over the whole planning
    horizon (final trajectory incl. scheduled loads). The remaining attributes
    carry the full plan context (threshold, SOC limits, per-load schedules)
    so the bundled forecast card can render everything from this one entity;
    third-party cards such as ApexCharts work too (see README).
    """

    # No state_class: forecast values must not feed long-term statistics.
    # The bulky per-hour attributes are also kept out of the recorder.
    _unrecorded_attributes = frozenset({"forecast", "loads", "consumption_profile"})
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_translation_key = "soc_forecast"

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_SOC_FORECAST_CURVE)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        curve = data.get("soc_forecast") or []
        if len(curve) > 1:
            return curve[1]["soc"]
        if curve:
            return curve[0]["soc"]
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        loads = [
            {
                "name": plan.get("name"),
                "active": plan.get("active"),
                "planned_energy_kwh": plan.get("planned_energy_kwh"),
                "schedule": plan.get("schedule") or [],
            }
            for plan in (data.get("load_plans") or {}).values()
        ]
        return {
            "forecast": data.get("soc_forecast") or [],
            "soc_threshold_percent": data.get("soc_threshold_percent"),
            "grid_import_kwh": data.get("grid_import_kwh"),
            "lost_surplus_kwh": data.get("lost_surplus_kwh"),
            "loads": loads,
            "consumption_profile": data.get("consumption_profile") or {},
            "gate_calibration": data.get("gate_calibration") or {},
            **(data.get("plan_params") or {}),
        }
