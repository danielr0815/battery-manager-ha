"""Button platform: per-load runtime-counter reset (v0.7.18)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_LOAD
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity, async_add_by_subentry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a runtime-reset button per surplus load."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    # One reset button per surplus load, scoped to its subentry so it is removed
    # automatically when the load subentry is deleted (v0.7.19).
    per_subentry: dict[str, list[Entity]] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
            per_subentry[subentry_id] = [
                SurplusLoadRuntimeResetButton(coordinator, subentry_id, subentry.title)
            ]
    async_add_by_subentry(async_add_entities, [], per_subentry)


class SurplusLoadRuntimeResetButton(BatteryManagerEntity, ButtonEntity):
    """Resets the matching load's active-runtime counter to zero."""

    _attr_translation_key = "load_runtime_reset"
    _attr_icon = "mdi:timer-refresh-outline"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(coordinator, f"load_runtime_reset_{subentry_id}")
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def available(self) -> bool:
        # Always usable — resets the persisted counter regardless of plan state.
        return True

    async def async_press(self) -> None:
        self.coordinator.reset_load_runtime(self._subentry_id)
