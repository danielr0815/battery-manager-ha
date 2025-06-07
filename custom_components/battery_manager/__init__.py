"""Battery Manager integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import BatteryManagerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Manager from a config entry."""
    try:
        coordinator = BatteryManagerCoordinator(hass, entry.data)
        
        # Fetch initial data
        await coordinator.async_config_entry_first_refresh()
        
        # Store coordinator in hass data
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = coordinator
        
        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Set up options update listener
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        
        _LOGGER.info("Battery Manager integration successfully set up")
        return True
        
    except Exception as err:
        _LOGGER.error("Error setting up Battery Manager: %s", err)
        raise ConfigEntryNotReady from err


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
        
        # Cancel any pending debounce tasks
        if coordinator._debounce_task:
            coordinator._debounce_task.cancel()
        
        # Remove from hass data
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Remove domain data if empty
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
        
        _LOGGER.info("Battery Manager integration successfully unloaded")
    
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        # No migration needed yet
        _LOGGER.info("Migration to version %s successful", config_entry.version)
        return True

    _LOGGER.error("Unknown configuration version %s", config_entry.version)
    return False
