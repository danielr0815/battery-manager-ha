"""Base entity for the Battery Manager integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INTEGRATION_NAME
from .coordinator import BatteryManagerCoordinator


class BatteryManagerEntity(CoordinatorEntity[BatteryManagerCoordinator]):
    """Common device grouping and availability handling."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BatteryManagerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=INTEGRATION_NAME,
            manufacturer="Battery Manager",
            model="Energy Optimizer",
            sw_version=coordinator.integration_version,
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get("valid", False)
        )
