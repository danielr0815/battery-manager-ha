"""Switch platform: manual vacation mode (docs/CONSUMPTION_FORECAST.md D-C4)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_VACATION_MODE
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the vacation-mode switch."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BatteryManagerVacationSwitch(coordinator)])


class BatteryManagerVacationSwitch(BatteryManagerEntity, SwitchEntity):
    """While on, the planner forecasts with the learned absence profile.

    The state is persisted in the learner store and its recorder history is
    used to tag past days as absence days for learning (D-C4).
    """

    _attr_translation_key = ENTITY_VACATION_MODE
    _attr_icon = "mdi:beach"

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_VACATION_MODE)

    @property
    def available(self) -> bool:
        # The switch reflects persisted state, not planner output — it must
        # stay usable even while inputs are missing.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.learner.vacation_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.learner.async_set_vacation(True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.learner.async_set_vacation(False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
