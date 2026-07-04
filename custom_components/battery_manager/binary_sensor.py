"""Binary sensor platform for the Battery Manager integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_PLANNED_ENERGY_KWH,
    ATTR_PLANNED_HOURS,
    ATTR_THRESHOLD,
    CONF_APPLIANCE_OPPORTUNISTIC,
    DOMAIN,
    ENTITY_INVERTER_STATUS,
    ENTITY_SUPPORT_DC24,
    ENTITY_SUPPORT_DC48,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
)
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Battery Manager binary sensors (incl. per-subentry entities)."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        InverterRecommendationSensor(coordinator),
        SupportPathSensor(coordinator, ENTITY_SUPPORT_DC24, "support_dc24"),
        SupportPathSensor(coordinator, ENTITY_SUPPORT_DC48, "support_dc48"),
    ]

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
            entities.append(
                SurplusLoadRecommendationSensor(coordinator, subentry_id, subentry.title)
            )
        elif subentry.subentry_type == SUBENTRY_TYPE_APPLIANCE and subentry.data.get(
            CONF_APPLIANCE_OPPORTUNISTIC
        ):
            entities.append(
                ApplianceStartWindowSensor(coordinator, subentry_id, subentry.title)
            )

    async_add_entities(entities)


class InverterRecommendationSensor(BatteryManagerEntity, BinarySensorEntity):
    """Recommended state for the real discharge inverter (with hysteresis)."""

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_translation_key = "inverter_status"

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_INVERTER_STATUS)

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("inverter_recommendation")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {ATTR_THRESHOLD: data.get("soc_threshold_percent")}


class SurplusLoadRecommendationSensor(BatteryManagerEntity, BinarySensorEntity):
    """Per-load recommendation: 'on' when the load should consume surplus now."""

    _attr_translation_key = "surplus_load"

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        subentry_id: str,
        title: str,
    ) -> None:
        super().__init__(coordinator, f"load_{subentry_id}")
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    def _plan(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        return (data.get("load_plans") or {}).get(self._subentry_id)

    @property
    def is_on(self) -> bool | None:
        load_plan = self._plan()
        return load_plan["active"] if load_plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        load_plan = self._plan() or {}
        return {
            ATTR_PLANNED_HOURS: load_plan.get("planned_hours"),
            ATTR_PLANNED_ENERGY_KWH: load_plan.get("planned_energy_kwh"),
        }


class ApplianceStartWindowSensor(BatteryManagerEntity, BinarySensorEntity):
    """'On' when a full appliance run could start now without grid import."""

    _attr_translation_key = "appliance_window"

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        subentry_id: str,
        title: str,
    ) -> None:
        super().__init__(coordinator, f"appliance_{subentry_id}")
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        windows = data.get("appliance_windows") or {}
        return windows.get(self._subentry_id)


class SupportPathSensor(BatteryManagerEntity, BinarySensorEntity):
    """Status of an emergency support path switched by the integration."""

    def __init__(
        self, coordinator: BatteryManagerCoordinator, key: str, data_key: str
    ) -> None:
        super().__init__(coordinator, key)
        self._data_key = data_key
        self._attr_translation_key = key

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._data_key)
