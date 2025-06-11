"""Battery Manager integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any, Dict
import json
from pathlib import Path
import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.components.persistent_notification import async_create as persistent_notification_create

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS
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

        if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS):
            async def export_service(call: ServiceCall) -> None:
                """Handle export hourly details service call."""
                await _async_export_hourly_details(hass, call)

            hass.services.async_register(
                DOMAIN,
                SERVICE_EXPORT_HOURLY_DETAILS,
                export_service,
                schema=vol.Schema({
                    vol.Optional("entry_id"): str,
                    vol.Optional("file_path"): str,
                    vol.Optional("download", default=False): bool,
                }),
            )
        
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
            if hass.services.has_service(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS):
                hass.services.async_remove(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS)
        
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


async def _async_export_hourly_details(hass: HomeAssistant, call: ServiceCall) -> None:
    """Export last hourly details to a text file."""
    entry_id = call.data.get("entry_id")
    file_path = call.data.get("file_path")
    download = call.data.get("download", False)

    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        _LOGGER.error("No Battery Manager data available")
        return

    coordinator: BatteryManagerCoordinator | None = None

    if entry_id:
        coordinator = domain_data.get(entry_id)
        if coordinator is None:
            _LOGGER.error("Entry id %s not found", entry_id)
            return
    else:
        if len(domain_data) == 1:
            entry_id, coordinator = next(iter(domain_data.items()))
        else:
            _LOGGER.error("Multiple entries present; specify entry_id")
            return

    details = coordinator.simulator.controller.get_last_hourly_details()

    if not file_path:
        if download:
            file_path = hass.config.path(
                "www", f"battery_manager_hourly_{entry_id}.txt"
            )
        else:
            file_path = hass.config.path(f"battery_manager_hourly_{entry_id}.txt")

    try:
        path = Path(file_path)
        with path.open("w", encoding="utf-8") as f_handle:
            for item in details:
                f_handle.write(json.dumps(item) + "\n")
        _LOGGER.info("Exported hourly details to %s", file_path)

        message: str
        if download:
            public_dir = Path(hass.config.path("www"))
            public_dir.mkdir(exist_ok=True)
            public_path = public_dir / path.name
            if public_path != path:
                public_path.write_bytes(path.read_bytes())
            url = f"/local/{public_path.name}"
            message = f'Hourly details exported. <a href="{url}" download>Download file</a>'
        else:
            message = f"Hourly details exported to {file_path}"

        persistent_notification_create(
            hass,
            message,
            title="Battery Manager",
        )
    except Exception as err:  # pragma: no cover - file write errors
        _LOGGER.error("Failed to export hourly details: %s", err)
