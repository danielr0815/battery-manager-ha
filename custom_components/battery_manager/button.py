"""Button platform: per-load runtime-counter reset (v0.7.18)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_LOAD
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a runtime-reset button per surplus load."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    # One reset button per current surplus load. (Like the other per-load
    # entities it is not subentry-scoped, so a removed load leaves a stale
    # registry entry until cleaned up — tracked as a follow-up.)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
            entities.append(
                SurplusLoadRuntimeResetButton(coordinator, subentry_id, subentry.title)
            )
    async_add_entities(entities)


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
