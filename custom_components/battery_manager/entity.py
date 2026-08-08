"""Base entity for the Battery Manager integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
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
    subentry is deleted (otherwise they orphan as stale registry rows).
    ``config_subentry_id`` is per call, hence one call per subentry. Re-adding
    an entity that already exists with ``config_subentry_id=None`` (older
    installs) updates the existing row in place — no duplicate — so this
    migrates transparently on the next setup."""
    base = list(base)
    if base:
        async_add_entities(base)
    for subentry_id, entities in per_subentry.items():
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


def ensure_devices(
    hass: HomeAssistant, entry: ConfigEntry, sw_version: str | None
) -> None:
    """Create/update the main device plus ONE device per config subentry.

    HA 2026.8 (core PR #175785, no compat shim): a device belongs to a single
    config subentry. The devices are created here — before the platforms add
    entities — so each subentry device can carry ``via_device_id`` pointing at
    the main device: at entity construction time the main device's registry id
    may not exist yet (fresh install), so the entities' DeviceInfo only carries
    identifiers. Idempotent: ``async_get_or_create`` refreshes name/sw_version
    on every setup; devices of deleted subentries are removed by HA itself
    (they are scoped to their subentry).
    """
    dev_reg = dr.async_get(hass)
    main = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=INTEGRATION_NAME,
        manufacturer="Battery Manager",
        model="Energy Optimizer",
        sw_version=sw_version,
    )
    for subentry_id, subentry in entry.subentries.items():
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_{subentry_id}")},
            name=subentry.title,
            via_device_id=main.id,
            manufacturer="Battery Manager",
            model="Energy Optimizer",
            sw_version=sw_version,
        )


class BatteryManagerEntity(CoordinatorEntity[BatteryManagerCoordinator]):
    """Common device grouping and availability handling."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        key: str,
        subentry_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        if subentry_id is None:
            # Entry-level entities keep the original main device — unchanged
            # since v1, so the existing device stays stable.
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry_id)},
                name=INTEGRATION_NAME,
                manufacturer="Battery Manager",
                model="Energy Optimizer",
                sw_version=coordinator.integration_version,
            )
        else:
            # HA 2026.8 (core PR #175785): a device belongs to ONE config
            # subentry. Sharing the main device between the entry and all
            # subentries made the registry ping-pong it on every add and delete
            # the 'old' side's entities — nearly all BM entities vanished from
            # the state machine on 2026.8.0 (production incident). Each
            # subentry therefore gets its own device; the via_device_id link to
            # the main device is set by ensure_devices() (the main device's
            # registry id does not exist yet at entity construction time).
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry_id}_{subentry_id}")},
                name=coordinator.entry.subentries[subentry_id].title,
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
