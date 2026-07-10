"""Base entity for the Battery Manager integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INTEGRATION_NAME
from .coordinator import BatteryManagerCoordinator


def async_add_by_subentry(
    async_add_entities: AddConfigEntryEntitiesCallback,
    base: Iterable[Entity],
    per_subentry: Mapping[str, list[Entity]],
) -> None:
    """Add config-entry-level entities plus per-subentry entities.

    Per-subentry entities are added with ``config_subentry_id`` so HA scopes them
    to their subentry and removes them automatically when the load/appliance
    subentry is deleted (otherwise they orphan as stale registry rows, since they
    all share the single config-entry device). ``config_subentry_id`` is per call,
    hence one call per subentry. Re-adding an entity that already exists with
    ``config_subentry_id=None`` (older installs) updates the existing row in place
    — no duplicate — so this migrates transparently on the next setup."""
    base = list(base)
    if base:
        async_add_entities(base)
    for subentry_id, entities in per_subentry.items():
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


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
