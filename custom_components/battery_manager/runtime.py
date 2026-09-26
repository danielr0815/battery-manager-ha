"""The single typed runtime value owned by a config entry."""

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

type BatteryManagerConfigEntry = ConfigEntry[BatteryManagerCoordinator]
