"""Appliance entities retain observed/configured values without an economic plan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.util import dt as dt_util

from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity

# key, device class, unit; cycle energy is not a lifetime energy counter.
APPLIANCE_SENSORS = (
    ("status", SensorDeviceClass.ENUM, None),
    ("program", None, None),
    ("remaining", SensorDeviceClass.DURATION, UnitOfTime.MINUTES),
    ("expected_end", SensorDeviceClass.TIMESTAMP, None),
    ("cycle_energy", SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    ("planning_energy", SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    ("planning_duration", SensorDeviceClass.DURATION, UnitOfTime.MINUTES),
    ("learning", SensorDeviceClass.ENUM, None),
)


class ApplianceSensor(BatteryManagerEntity, SensorEntity):
    """Compact entity attributes; profiles/history are fetched separately."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        subentry_id: str,
        description: tuple,
    ) -> None:
        key, device_class, unit = description
        super().__init__(coordinator, f"appliance_{key}_{subentry_id}", subentry_id)
        self._key = key
        self._appliance_id = subentry_id
        self._attr_translation_key = f"appliance_{key}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = (
            "mdi:dishwasher" if key == "status" else "mdi:information-outline"
        )
        if key == "status":
            self._attr_options = ["idle", "running", "paused", "finished", "error"]
        elif key == "learning":
            self._attr_options = ["no_data", "measuring", "learned", "invalid"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.appliances.subscribe(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.appliances.entity_snapshot(self._appliance_id) is not None
        )

    @property
    def native_value(self) -> str | float | datetime | None:
        snapshot = self.coordinator.appliances.entity_snapshot(self._appliance_id)
        if snapshot is None:
            return None
        observation = snapshot["observation"]
        match self._key:
            case "status":
                return None if snapshot["status"] == "unknown" else snapshot["status"]
            case "program":
                return snapshot["program"]
            case "remaining":
                return observation["remaining_minutes"]
            case "expected_end":
                end = observation["expected_end"]
                return dt_util.parse_datetime(end) if end else None
            case "cycle_energy":
                return observation["cycle_energy_wh"]
            case "planning_energy":
                return snapshot["planning"]["energy_wh"]
            case "planning_duration":
                return snapshot["planning"]["duration_minutes"]
            case _:
                return snapshot["learning"]["status"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self.coordinator.appliances.entity_snapshot(self._appliance_id)
        if snapshot is None:
            return {}
        attrs: dict[str, Any] = {
            "entry_id": self.coordinator.entry.entry_id,
            "appliance_id": self._appliance_id,
        }
        match self._key:
            case "status":
                attrs.update(
                    revision=snapshot["revision"],
                    recommendation=snapshot["recommendation"],
                )
            case "program":
                attrs["source"] = snapshot["program_source"]
            case "remaining" | "expected_end":
                attrs["source"] = snapshot["observation"]["remaining_source"]
            case "cycle_energy":
                attrs.update(
                    source=snapshot["observation"]["energy_source"],
                    complete=snapshot["observation"]["complete"],
                )
            case "planning_energy":
                attrs["source"] = snapshot["planning"]["energy_source"]
            case "planning_duration":
                attrs["source"] = snapshot["planning"]["duration_source"]
            case "learning":
                attrs.update(
                    sample_count=snapshot["learning"]["sample_count"],
                    last_learned_at=snapshot["learning"]["last_learned_at"],
                )
        return attrs
