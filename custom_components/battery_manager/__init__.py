"""Battery Manager integration for Home Assistant."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

import voluptuous as vol
from homeassistant.components.persistent_notification import (
    async_create as persistent_notification_create,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_AS_TABLE, DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS
from .coordinator import BatteryManagerCoordinator
from .debug_utils import format_hourly_details_table

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def _validate_file_path(file_path: str, base_dir: Path) -> Path:
    """Validate and sanitize file path to prevent directory traversal attacks.

    Args:
        file_path: User-provided file path
        base_dir: Base directory that the file must be within

    Returns:
        Validated Path object

    Raises:
        ValueError: If path is invalid or attempts directory traversal
    """
    try:
        # Normalize the path to resolve any .. or symbolic links
        resolved_path = Path(file_path).resolve()
        resolved_base = base_dir.resolve()

        # Ensure the resolved path is within the base directory
        if not str(resolved_path).startswith(str(resolved_base)):
            raise ValueError(
                f"Path '{file_path}' is outside allowed directory '{base_dir}'"
            )

        # Additional validation: check for null bytes
        if '\0' in file_path:
            raise ValueError("Path contains null bytes")

        # Validate filename doesn't contain dangerous characters
        filename = resolved_path.name
        if filename.startswith('.') or '/' in filename or '\\' in filename:
            raise ValueError(f"Invalid filename: {filename}")

        return resolved_path

    except (OSError, RuntimeError) as err:
        raise ValueError(f"Invalid path: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Manager from a config entry."""
    try:
        config = {**entry.data, **entry.options}
        coordinator = BatteryManagerCoordinator(hass, config)

        # Store coordinator in hass data first
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Trigger first refresh and wait for completion during setup
        await coordinator.async_refresh()

        if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS):

            async def export_service(call: ServiceCall) -> None:
                """Handle export hourly details service call."""
                await _async_export_hourly_details(hass, call)

            hass.services.async_register(
                DOMAIN,
                SERVICE_EXPORT_HOURLY_DETAILS,
                export_service,
                schema=vol.Schema(
                    {
                        vol.Optional("entry_id"): str,
                        vol.Optional("file_path"): str,
                        vol.Optional("download", default=False): bool,
                        vol.Optional(CONF_AS_TABLE, default=True): bool,
                    }
                ),
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
    reload_set: set[str] = hass.data.setdefault(f"{DOMAIN}_reloading", set())

    if entry.entry_id in reload_set:
        return

    reload_set.add(entry.entry_id)

    try:
        # Update existing coordinator instead of reloading the entire entry
        coordinator: BatteryManagerCoordinator = hass.data[DOMAIN].get(entry.entry_id)
        if coordinator:
            config = {**entry.data, **entry.options}
            coordinator.update_config(config)
            _LOGGER.info("Battery Manager configuration updated")
        else:
            # Fallback to full reload if coordinator doesn't exist
            await async_unload_entry(hass, entry)
            await async_setup_entry(hass, entry)
    finally:
        reload_set.discard(entry.entry_id)


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
    as_table = call.data.get(CONF_AS_TABLE, True)

    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        _LOGGER.error("No Battery Manager data available")
        return

    coordinator: BatteryManagerCoordinator | None = None

    # Validate entry_id if provided
    if entry_id:
        # Security: Validate entry_id is alphanumeric to prevent injection
        if not entry_id.replace("-", "").replace("_", "").isalnum():
            _LOGGER.error("Invalid entry_id format: %s", entry_id)
            return

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

    # Determine base directory for file operations
    base_dir = Path(hass.config.path())

    # Generate default file path if not provided
    if not file_path:
        if download:
            file_path = str(
                base_dir / "www" / f"battery_manager_hourly_{entry_id}.txt"
            )
        else:
            file_path = str(base_dir / f"battery_manager_hourly_{entry_id}.txt")

    try:
        # Security: Validate file path to prevent directory traversal
        validated_path = _validate_file_path(file_path, base_dir)

        # Ensure parent directory exists
        validated_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file with proper encoding
        with validated_path.open("w", encoding="utf-8") as f_handle:
            if as_table:
                f_handle.write(
                    format_hourly_details_table(details, include_color=False) + "\n"
                )
            else:
                for item in details:
                    f_handle.write(json.dumps(item) + "\n")

        _LOGGER.info("Exported hourly details to %s", validated_path)

        message: str
        if download:
            public_dir = Path(hass.config.path("www"))
            public_dir.mkdir(exist_ok=True)
            public_path = public_dir / validated_path.name
            if public_path != validated_path:
                # Validate the copy destination as well
                validated_public_path = _validate_file_path(str(public_path), base_dir)
                validated_public_path.write_bytes(validated_path.read_bytes())
            url = f"/local/{validated_path.name}"
            message = (
                f'Hourly details exported. <a href="{url}" download>Download file</a>'
            )
        else:
            message = f"Hourly details exported to {validated_path}"

        persistent_notification_create(
            hass,
            message,
            title="Battery Manager",
        )
    except ValueError as err:
        # Security validation failed
        _LOGGER.error("File path validation failed: %s", err)
        persistent_notification_create(
            hass,
            f"Export failed: {err}",
            title="Battery Manager - Error",
        )
    except Exception as err:  # pragma: no cover - file write errors
        _LOGGER.error("Failed to export hourly details: %s", err)
        persistent_notification_create(
            hass,
            f"Export failed: {err}",
            title="Battery Manager - Error",
        )
