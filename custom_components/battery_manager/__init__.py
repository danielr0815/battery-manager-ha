"""Battery Manager integration for Home Assistant."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.persistent_notification import (
    async_create as persistent_notification_create,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import CONF_AS_TABLE, DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS
from .coordinator import BatteryManagerCoordinator
from .debug_utils import format_hourly_details_table

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Config keys removed in v2 (see docs/REQUIREMENTS.md, breaking change accepted)
_REMOVED_KEYS = {
    "ac_additional_load_w",
    "controller_target_soc_percent",
    "target_soc_percent",
    "controller_max_threshold_percent",
}


def _validate_file_path(file_path: str, base_dir: Path) -> Path:
    """Validate a user-provided path to prevent directory traversal."""
    try:
        resolved_path = Path(file_path).resolve()
        resolved_base = base_dir.resolve()
        if not str(resolved_path).startswith(str(resolved_base)):
            raise ValueError(
                f"Path '{file_path}' is outside allowed directory '{base_dir}'"
            )
        if "\0" in file_path:
            raise ValueError("Path contains null bytes")
        filename = resolved_path.name
        if filename.startswith(".") or "/" in filename or "\\" in filename:
            raise ValueError(f"Invalid filename: {filename}")
        return resolved_path
    except (OSError, RuntimeError) as err:
        raise ValueError(f"Invalid path: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Manager from a config entry."""
    coordinator = BatteryManagerCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # First refresh; failure is tolerated (fast retry interval during startup).
    await coordinator.async_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS):

        async def export_service(call: ServiceCall) -> None:
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator.cleanup()
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            if hass.services.has_service(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS):
                hass.services.async_remove(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when config or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 entries: same base keys, removed controller/additional-load keys."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        data = {k: v for k, v in entry.data.items() if k not in _REMOVED_KEYS}
        options = {k: v for k, v in entry.options.items() if k not in _REMOVED_KEYS}
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=2
        )
        _LOGGER.info("Migrated Battery Manager entry to version 2")
    return True


async def _async_export_hourly_details(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write the last plan's hourly details to a file."""
    domain_data: dict[str, BatteryManagerCoordinator] = hass.data.get(DOMAIN, {})
    if not domain_data:
        _LOGGER.error("No Battery Manager instances available for export")
        return

    entry_id = call.data.get("entry_id") or next(iter(domain_data))
    coordinator = domain_data.get(entry_id)
    if coordinator is None:
        _LOGGER.error("Unknown entry_id for export: %s", entry_id)
        return

    details = coordinator.get_last_hourly_details()
    if not details:
        _LOGGER.warning("No hourly details available yet")
        return

    config_dir = Path(hass.config.config_dir)
    download = call.data.get("download", False)
    base_dir = config_dir / "www" if download else config_dir
    default_name = f"battery_manager_hourly_{entry_id}.txt"
    file_path = call.data.get("file_path") or str(base_dir / default_name)

    try:
        target = _validate_file_path(file_path, base_dir)
    except ValueError as err:
        _LOGGER.error("Refusing to export: %s", err)
        return

    if call.data.get(CONF_AS_TABLE, True):
        content = format_hourly_details_table(details)
    else:
        content = "\n".join(json.dumps(row) for row in details)

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    try:
        await hass.async_add_executor_job(_write)
    except OSError as err:  # pragma: no cover
        _LOGGER.error("Failed to write hourly details: %s", err)
        return

    _LOGGER.info("Hourly details exported to %s", target)
    if download:
        persistent_notification_create(
            hass,
            f"[Download {target.name}](/local/{target.name})",
            title="Battery Manager Export",
        )
