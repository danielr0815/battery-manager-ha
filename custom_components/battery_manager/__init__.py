"""Battery Manager integration for Home Assistant."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.components.persistent_notification import (
    async_create as persistent_notification_create,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import CONF_AS_TABLE, DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS, STORAGE_VERSION
from .coordinator import BatteryManagerCoordinator
from .debug_utils import format_hourly_details_table

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Config-entry-only integration; async_setup exists solely for the card.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILENAME = "battery-manager-forecast-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the bundled Lovelace card and register it as a resource.

    The card ships inside the integration (frontend/), so users get the SOC
    forecast chart without installing anything from HACS frontend. The card
    is optional sugar: any failure here must never break the planner setup.
    """
    try:
        await _async_setup_card(hass)
    except Exception:
        _LOGGER.warning(
            "Could not register the bundled dashboard card", exc_info=True
        )
    return True


async def _async_setup_card(hass: HomeAssistant) -> None:
    """Register the static path and schedule the resource registration."""
    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL,
                str(Path(__file__).parent / "frontend" / CARD_FILENAME),
                True,
            )
        ]
    )
    # The versioned URL busts browser and companion-app caches on updates.
    versioned_url = f"{CARD_URL}?v={integration.version}"

    async def _on_started(_event: Event | None = None) -> None:
        try:
            await _async_register_card_resource(hass, versioned_url)
        except Exception:
            _LOGGER.warning(
                "Could not register the dashboard card resource", exc_info=True
            )

    if hass.is_running:
        await _on_started()
    else:
        # Wait for full startup so the resource collection exists and is
        # safe to modify.
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)


async def _async_register_card_resource(
    hass: HomeAssistant, versioned_url: str
) -> None:
    """Add or update the card module in the Lovelace resource registry."""
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if not isinstance(resources, ResourceStorageCollection):
        # Dashboard resources managed via YAML (or no lovelace at all):
        # no storage collection to write to — load the module globally.
        if "frontend" in hass.config.components:
            add_extra_js_url(hass, versioned_url)
        return
    # Creating an item on a not-yet-loaded collection would wipe the
    # user's resource list (home-assistant/core#165767) — load first.
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    for item in resources.async_items():
        url = item.get("url", "")
        if url.split("?")[0] == CARD_URL:
            if url != versioned_url:
                await resources.async_update_item(
                    item["id"], {"url": versioned_url}
                )
                _LOGGER.info("Updated card resource to %s", versioned_url)
            return
    await resources.async_create_item({"res_type": "module", "url": versioned_url})
    _LOGGER.info("Registered card resource %s", versioned_url)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Manager from a config entry."""
    coordinator = BatteryManagerCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Restore SOC cache / plug ownership, then first refresh; a refresh
    # failure is tolerated (fast retry interval during startup).
    await coordinator.async_load_persistent_state()
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the per-entry storage (SOC cache, plug ownership)."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


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
