"""Battery Manager integration for Home Assistant."""

from __future__ import annotations

import json
import logging
import time
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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_AS_TABLE,
    CONF_GATE_SOC_PERCENT,
    CONF_LEARNING_WINDOW_DAYS,
    CONF_SUPPORT_DC24_ACTIVATE_SOC,
    CONF_SUPPORT_DC24_RECOVERY_SOC,
    CONF_SUPPORT_DC48_ACTIVATE_SOC,
    CONF_SUPPORT_DC48_RECOVERY_SOC,
    DEFAULT_CONFIG,
    DOMAIN,
    LEARNED_STORE_KEY,
    LEARNED_STORE_MAJOR,
    SERVICE_EXPORT_HOURLY_DETAILS,
    SERVICE_EXPORT_LEARNED_PROFILES,
    STORAGE_VERSION,
)
from .coordinator import BatteryManagerCoordinator
from .debug_utils import format_hourly_details_table, format_learned_profiles_table
from .entity import ensure_devices

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Config-entry-only integration; async_setup exists for the card and the
# download-export sweep.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILENAME = "battery-manager-forecast-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

# Export-service hardening (security review 2026-07-30): exports are confined
# to a per-integration subdirectory — plain exports under
# <config>/battery_manager/, downloads under <config>/www/battery_manager/.
# The www tree is served WITHOUT authentication under /local/, so every
# download file expires after one hour (per-file timer below; the startup
# sweep in async_setup removes files whose timer died with the last run).
_EXPORT_SUBDIR = "battery_manager"
_DOWNLOAD_TTL_S = 3600
_ALLOWED_EXPORT_SUFFIXES = frozenset({".txt", ".json"})

# Config keys removed in v2 (see docs/REQUIREMENTS.md, breaking change accepted)
_REMOVED_KEYS = {
    "ac_additional_load_w",
    "controller_target_soc_percent",
    "target_soc_percent",
    "controller_max_threshold_percent",
}


def _validate_file_path(file_path: str, base_dir: Path) -> Path:
    """Validate a user-provided path to prevent directory traversal.

    `base_dir` is the hard ceiling: nothing outside it is writable. On top of
    containment, HA-internal state (.storage) and anything but the documented
    export formats (.txt/.json) are refused — the export contains learned
    household behaviour and must never overwrite HA state or become an
    attacker-planted config file.
    """
    try:
        resolved_path = Path(file_path).resolve()
        resolved_base = base_dir.resolve()
        # Proper path containment: a string startswith() would also accept a
        # sibling dir sharing the prefix (e.g. '/config/exports_evil' vs
        # '/config/exports'). is_relative_to compares path components.
        if resolved_path != resolved_base and not resolved_path.is_relative_to(
            resolved_base
        ):
            raise ValueError(
                f"Path '{file_path}' is outside allowed directory '{base_dir}'"
            )
        if "\0" in file_path:
            raise ValueError("Path contains null bytes")
        if ".storage" in resolved_path.parts:
            raise ValueError("Path must not touch HA internal state (.storage)")
        if resolved_path.suffix.lower() not in _ALLOWED_EXPORT_SUFFIXES:
            raise ValueError(
                f"Unsupported file extension '{resolved_path.suffix}' — "
                "only .txt and .json are allowed"
            )
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
        _LOGGER.warning("Could not register the bundled dashboard card", exc_info=True)
    try:
        await _async_sweep_download_dir(hass)
    except Exception:
        _LOGGER.warning("Could not sweep stale download exports", exc_info=True)
    return True


async def _async_sweep_download_dir(hass: HomeAssistant) -> None:
    """Delete leftover download exports older than the TTL.

    The per-file async_call_later timer dies with the HA process, so a file
    written shortly before a restart would otherwise linger in the
    unauthenticated /local/ tree indefinitely. Runs on the executor (pure
    filesystem work) and, like the card, must never break the setup.
    """
    download_dir = Path(hass.config.config_dir) / "www" / _EXPORT_SUBDIR
    cutoff = time.time() - _DOWNLOAD_TTL_S

    def _sweep() -> None:
        try:
            children = list(download_dir.iterdir())
        except OSError:  # nothing exported yet (or directory unreadable)
            return
        for child in children:
            try:
                if child.is_file() and child.stat().st_mtime < cutoff:
                    child.unlink()
                    _LOGGER.info("Removed stale download export %s", child)
            except OSError as err:
                _LOGGER.warning("Could not remove stale export %s: %s", child, err)

    await hass.async_add_executor_job(_sweep)


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
                await resources.async_update_item(item["id"], {"url": versioned_url})
                _LOGGER.info("Updated card resource to %s", versioned_url)
            return
    await resources.async_create_item({"res_type": "module", "url": versioned_url})
    _LOGGER.info("Registered card resource %s", versioned_url)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Manager from a config entry."""
    coordinator = BatteryManagerCoordinator(hass, entry)
    # Device sw_version from the manifest (single source of truth, no drift).
    # Integration.version is an AwesomeVersion — DeviceInfo needs a plain str.
    _mf_version = (await async_get_integration(hass, DOMAIN)).version
    coordinator.integration_version = str(_mf_version) if _mf_version else None
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Restore SOC cache / plug ownership, then first refresh; a refresh
    # failure is tolerated (fast retry interval during startup).
    await coordinator.async_load_persistent_state()
    await coordinator.async_refresh()

    # HA 2026.8: one device per config subentry (core PR #175785). Created
    # before the platforms so subentry devices carry via_device_id on the main
    # device and every entity attaches to an already-existing device.
    ensure_devices(hass, entry, coordinator.integration_version)

    export_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("file_path"): str,
            vol.Optional("download", default=False): bool,
            vol.Optional(CONF_AS_TABLE, default=True): bool,
        }
    )
    if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS):

        async def export_service(call: ServiceCall) -> None:
            await _async_export_hourly_details(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_EXPORT_HOURLY_DETAILS,
            export_service,
            schema=export_schema,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_LEARNED_PROFILES):

        async def export_profiles_service(call: ServiceCall) -> None:
            await _async_export_learned_profiles(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_EXPORT_LEARNED_PROFILES,
            export_profiles_service,
            schema=export_schema,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # After the platforms: the learner looks up the vacation switch entity.
    coordinator.async_setup_learning()
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
        # Cancel in-flight actuation tasks BEFORE the flush so none can mutate
        # the persisted state after the flush captures the payload (review #7).
        await coordinator.async_cancel_actuation_tasks()
        # Flush any pending delayed save before teardown: a config-entry reload
        # does not fire EVENT_HOMEASSISTANT_FINAL_WRITE, so the persisted
        # support-mode / caused-off record would otherwise be lost if the
        # reload beats the 10 s delayed write (review round 3).
        await coordinator.async_flush_persistent_state()
        coordinator.cleanup()
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            for service in (
                SERVICE_EXPORT_HOURLY_DETAILS,
                SERVICE_EXPORT_LEARNED_PROFILES,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when config or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the per-entry storage (SOC cache, learned profiles)."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    await Store(
        hass,
        LEARNED_STORE_MAJOR,
        f"{DOMAIN}.{LEARNED_STORE_KEY}.{entry.entry_id}",
    ).async_remove()


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
    if entry.version == 2 and entry.minor_version < 2:
        # Stufe 2 (D-C7): the learning-window default widened from 42 to
        # 120 days. vol.Required auto-persisted the old default into the
        # options, so only the exact old default is raised — deliberate
        # operator choices stay untouched.
        options = dict(entry.options)
        for container in (options,):
            if container.get(CONF_LEARNING_WINDOW_DAYS) == 42:
                container[CONF_LEARNING_WINDOW_DAYS] = 120
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
        _LOGGER.info("Migrated Battery Manager entry to version 2.2")
    if entry.version == 2 and entry.minor_version < 3:
        # v0.7.13: the grid-support escalation switched from soc_min-derived
        # thresholds to four ABSOLUTE SOC values. The new absolute DEFAULT_CONFIG
        # values (10/11/5.5/10) are only neutral for the default battery config
        # (soc_min 5 %); a pre-0.7.13 entry with a different soc_min would
        # otherwise silently change (or disable) its grid support. Backfill the
        # exact legacy-equivalent thresholds, computed from the entry's own
        # soc_min/buffer, so the upgrade never moves the switch points.
        escalation_keys = (
            CONF_SUPPORT_DC24_ACTIVATE_SOC,
            CONF_SUPPORT_DC24_RECOVERY_SOC,
            CONF_SUPPORT_DC48_ACTIVATE_SOC,
            CONF_SUPPORT_DC48_RECOVERY_SOC,
        )
        already_set = any(
            k in entry.data or k in entry.options for k in escalation_keys
        )
        if already_set:
            hass.config_entries.async_update_entry(entry, minor_version=3)
        else:
            merged = {**DEFAULT_CONFIG, **entry.data, **entry.options}
            soc_min = float(merged["battery_min_soc_percent"])
            buffer = float(merged["soc_buffer_percent"])
            floor = soc_min + buffer
            options = dict(entry.options)
            options[CONF_SUPPORT_DC24_ACTIVATE_SOC] = floor
            options[CONF_SUPPORT_DC24_RECOVERY_SOC] = floor + 1.0
            options[CONF_SUPPORT_DC48_ACTIVATE_SOC] = soc_min + 0.5
            options[CONF_SUPPORT_DC48_RECOVERY_SOC] = floor
            hass.config_entries.async_update_entry(
                entry, options=options, minor_version=3
            )
        _LOGGER.info("Migrated Battery Manager entry to version 2.3")
    if entry.version == 2 and entry.minor_version < 4:
        _migrate_to_subentry_devices(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=4)
        _LOGGER.info("Migrated Battery Manager entry to version 2.4")
    if entry.version == 2 and entry.minor_version < 5:
        # The original voltage-gate rollout auto-persisted 100 % as its neutral
        # default.  It is indistinguishable from an explicit selection, so move
        # only that exact legacy value to the new physical default; every
        # calibrated value below 100 % remains operator-owned and untouched.
        data = dict(entry.data)
        options = dict(entry.options)
        if options.get(CONF_GATE_SOC_PERCENT) == 100.0:
            options[CONF_GATE_SOC_PERCENT] = 40.0
        elif (
            CONF_GATE_SOC_PERCENT not in options
            and data.get(CONF_GATE_SOC_PERCENT) == 100.0
        ):
            data[CONF_GATE_SOC_PERCENT] = 40.0
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, minor_version=5
        )
        _LOGGER.info("Migrated Battery Manager entry to version 2.5")
    return True


def _migrate_to_subentry_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move subentry entity rows onto per-subentry devices (entry version 2.4).

    HA 2026.8.0 enforces one device per config subentry (core PR #175785, no
    compat shim; telegram_bot migration: core PR #176606). Before, every
    entity shared the single entry device, so existing registry rows of
    subentry entities point at the main device — with config_subentry_id=None
    (pre-v0.7.19 installs) or already set (v0.7.19+). Both cases are re-pointed
    to a new device per subentry; entity ids and unique ids stay stable, so
    dashboards keep working. Rows removed by the broken 2026.8.0 ping-pong are
    NOT touched: they linger as deleted registry entries and are resurrected
    with their original entity id when the fixed setup re-adds them.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    main = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if main is None:
        return  # never fully set up — devices are created on first setup
    for subentry_id, subentry in entry.subentries.items():
        sub_device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_{subentry_id}")},
            name=subentry.title,
            via_device_id=main.id,
        )
        # Subentry entity unique ids all END with their subentry id
        # (load_/load_power_warning_/load_runtime_/load_runtime_reset_/
        # load_control_/appliance_<subentry_id>); entry-level keys never do.
        for row in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if row.unique_id.endswith(subentry_id) and (
                row.device_id != sub_device.id or row.config_subentry_id != subentry_id
            ):
                ent_reg.async_update_entity(
                    row.entity_id,
                    device_id=sub_device.id,
                    config_subentry_id=subentry_id,
                )
    # Detach the main device from any subentry LAST: the 2026.8.0 ping-pong may
    # have left it scoped to the last-added subentry, and clearing that scope
    # while rows still reference it makes the entity registry remove them
    # (async_device_modified on the device update event). After the re-pointing
    # above no row on the main device carries a subentry scope anymore.
    if main.config_subentry_id is not None:
        dev_reg.async_update_device(main.id, new_config_subentry_id=None)


def _export_coordinator(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[str, BatteryManagerCoordinator]:
    """Resolve the target coordinator for an export service call.

    Failures raise instead of only logging: a service call that silently does
    nothing leaves the operator believing the export happened.
    """
    domain_data: dict[str, BatteryManagerCoordinator] = hass.data.get(DOMAIN, {})
    if not domain_data:
        raise ServiceValidationError("No Battery Manager entry is set up")
    entry_id = call.data.get("entry_id") or next(iter(domain_data))
    coordinator = domain_data.get(entry_id)
    if coordinator is None:
        raise ServiceValidationError(f"Unknown entry_id for export: {entry_id}")
    return entry_id, coordinator


def _schedule_download_cleanup(hass: HomeAssistant, target: Path) -> None:
    """Delete a download export once its TTL expires.

    The /local/ tree is served without login, so the export (learned
    household behaviour) must not stay reachable longer than the download
    takes. FileNotFoundError is tolerated: the operator or the startup sweep
    may have removed the file first.
    """

    def _delete() -> None:
        try:
            target.unlink()
            _LOGGER.info("Deleted expired download export %s", target)
        except FileNotFoundError:
            pass
        except OSError as err:
            _LOGGER.warning("Could not delete expired download export: %s", err)

    async def _on_ttl_expired(_now) -> None:
        await hass.async_add_executor_job(_delete)

    async_call_later(hass, _DOWNLOAD_TTL_S, _on_ttl_expired)


async def _async_write_export(
    hass: HomeAssistant, call: ServiceCall, content: str, default_name: str
) -> None:
    """Validate the target path, write the export, notify on download.

    Plain exports stay under <config>/battery_manager/; downloads go to
    <config>/www/battery_manager/ — the only subtree the unauthenticated
    /local/ handler may ever serve for this integration, and every file in
    it expires after _DOWNLOAD_TTL_S.
    """
    config_dir = Path(hass.config.config_dir)
    download = call.data.get("download", False)
    base_dir = (
        config_dir / "www" / _EXPORT_SUBDIR if download else config_dir / _EXPORT_SUBDIR
    )
    file_path = call.data.get("file_path") or str(base_dir / default_name)

    try:
        target = _validate_file_path(file_path, base_dir)
    except ValueError as err:
        raise ServiceValidationError(f"Refusing to export: {err}") from err

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    try:
        await hass.async_add_executor_job(_write)
    except OSError as err:  # pragma: no cover
        raise HomeAssistantError(f"Failed to write export: {err}") from err

    _LOGGER.info("Export written to %s", target)
    if download:
        _schedule_download_cleanup(hass, target)
        persistent_notification_create(
            hass,
            f"[Download {target.name}](/local/{_EXPORT_SUBDIR}/{target.name}) — "
            "the file is reachable under /local/ without login and is "
            "deleted automatically after 1 hour.",
            title="Battery Manager Export",
        )


async def _async_export_hourly_details(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write the last plan's hourly details to a file."""
    entry_id, coordinator = _export_coordinator(hass, call)

    details = coordinator.get_last_hourly_details()
    if not details:
        raise HomeAssistantError(
            "No hourly details available yet — the planner has not produced "
            "a plan since startup"
        )

    if call.data.get(CONF_AS_TABLE, True):
        content = format_hourly_details_table(details)
    else:
        content = "\n".join(json.dumps(row) for row in details)
    await _async_write_export(
        hass, call, content, f"battery_manager_hourly_{entry_id}.txt"
    )


async def _async_export_learned_profiles(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Write the learned consumption profiles to a file (CONSUMPTION_FORECAST)."""
    entry_id, coordinator = _export_coordinator(hass, call)

    snapshot = coordinator.learner.export_snapshot()
    # A fresh learner stores {"ac": None, "dc": None} — truthy but empty, so
    # the check must look at the per-path bins, not the container.
    profiles = snapshot.get("profiles") or {}
    if not any(profiles.values()):
        raise HomeAssistantError(
            "No learned consumption profiles available yet — the learner "
            "needs long-term statistics first"
        )
    if call.data.get(CONF_AS_TABLE, True):
        content = format_learned_profiles_table(snapshot)
    else:
        content = json.dumps(snapshot, indent=2, ensure_ascii=False)
    await _async_write_export(
        hass, call, content, f"battery_manager_profiles_{entry_id}.txt"
    )
