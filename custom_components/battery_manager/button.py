"""Button platform: per-load runtime-counter reset (v0.7.18)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_CASCADE, SUBENTRY_TYPE_LOAD
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
    ent_reg = er.async_get(hass)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
            load_buttons: list[Entity] = [
                SurplusLoadRuntimeResetButton(coordinator, subentry_id, subentry.title)
            ]
            if coordinator.power_calibration_supported(subentry_id):
                load_buttons.append(
                    SurplusLoadPowerCalibrationButton(
                        coordinator, subentry_id, subentry.title
                    )
                )
            else:
                stale = ent_reg.async_get_entity_id(
                    "button",
                    DOMAIN,
                    f"{entry.entry_id}_load_power_calibrate_{subentry_id}",
                )
                if stale:
                    ent_reg.async_remove(stale)
            per_subentry[subentry_id] = load_buttons
        elif subentry.subentry_type == SUBENTRY_TYPE_CASCADE:
            per_subentry[subentry_id] = [
                CascadeFaultResetButton(coordinator, subentry_id, subentry.title)
            ]
    async_add_by_subentry(async_add_entities, [], per_subentry)


class SurplusLoadRuntimeResetButton(BatteryManagerEntity, ButtonEntity):
    """Resets the matching load's active-runtime counter to zero."""

    _attr_translation_key = "load_runtime_reset"
    _attr_icon = "mdi:timer-refresh-outline"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(coordinator, f"load_runtime_reset_{subentry_id}", subentry_id)
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def available(self) -> bool:
        # Always usable — resets the persisted counter regardless of plan state.
        return True

    async def async_press(self) -> None:
        await self.coordinator.reset_load_runtime(self._subentry_id)


class SurplusLoadPowerCalibrationButton(BatteryManagerEntity, ButtonEntity):
    """Starts a bounded grid-powered planning-power probe; press again aborts."""

    _attr_translation_key = "load_power_calibrate"
    _attr_icon = "mdi:flash-auto"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(
            coordinator, f"load_power_calibrate_{subentry_id}", subentry_id
        )
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def available(self) -> bool:
        return self.coordinator.power_calibration_button_available(self._subentry_id)

    async def async_press(self) -> None:
        await self.coordinator.async_toggle_load_power_calibration(self._subentry_id)


class CascadeFaultResetButton(BatteryManagerEntity, ButtonEntity):
    """Safe-off a cascade and clear its hard fault on success."""

    _attr_translation_key = "cascade_fault_reset"
    _attr_icon = "mdi:shield-refresh"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(coordinator, f"cascade_fault_reset_{subentry_id}", subentry_id)
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_reset_cascade_fault(self._subentry_id)
