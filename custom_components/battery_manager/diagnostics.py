"""Config-entry diagnostics for the Battery Manager integration (V7).

Standard Home Assistant ``async_get_config_entry_diagnostics`` endpoint. It
bundles the entry options, every subentry (load/appliance/storage) with its
data and options, the effective core SystemConfig values, the coordinator's
learned/latched runtime state and the last plan's headline metrics — so a
forensic pass no longer has to guess which subentry options are active.

Entity IDs are kept (they carry no secret and are needed to read the state);
the redaction set covers the generic HA sensitive keys defensively, though the
integration stores no tokens.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BatteryManagerCoordinator

# Entity IDs are intentionally NOT redacted (needed to interpret the state, no
# secret). These keys are covered only defensively — the integration stores no
# tokens or coordinates, so redaction is normally a no-op.
TO_REDACT = {"latitude", "longitude", "api_key", "token", "password", "access_token"}


def _core_config(coordinator: BatteryManagerCoordinator) -> dict[str, Any]:
    """The effective SystemConfig core values (built from the entry + defaults),
    flattened to the operator-relevant knobs."""
    try:
        config = coordinator.build_system_config()
    except Exception as err:  # never let diagnostics fail the download
        return {"error": f"could not build system config: {err}"}
    return {
        "battery": asdict(config.battery),
        "charger": asdict(config.charger),
        "inverter": asdict(config.inverter),
        "pv": asdict(config.pv),
        "ac_profile": asdict(config.ac_profile),
        "dc_profile": asdict(config.dc_profile),
        "control": asdict(config.control),
        "loads": [asdict(load) for load in config.loads],
    }


def _subentries(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Every subentry with its type, title, data and options — the piece a
    forensic pass previously had to reconstruct by hand."""
    result: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        result.append(
            {
                "subentry_id": subentry_id,
                "subentry_type": subentry.subentry_type,
                "title": subentry.title,
                "unique_id": subentry.unique_id,
                "data": async_redact_data(dict(subentry.data), TO_REDACT),
            }
        )
    return result


def _last_plan_metrics(coordinator: BatteryManagerCoordinator) -> dict[str, Any]:
    """The last plan's headline numbers (T*, import/export, lost surplus,
    prevented export) from the coordinator data, or a not-ready note."""
    data = coordinator.data
    if not data or not data.get("valid"):
        return {"valid": False}
    daily = data.get("daily_surplus") or []
    prevented_export_kwh = round(
        sum(entry.get("prevented_export_kwh", 0.0) for entry in daily), 3
    )
    return {
        "valid": True,
        "last_update": data.get("last_update"),
        "soc_threshold_percent": data.get("soc_threshold_percent"),
        "inverter_recommendation": data.get("inverter_recommendation"),
        "floor_guard_active": data.get("floor_guard_active"),
        "input_soc_percent": data.get("input_soc_percent"),
        "grid_import_kwh": data.get("grid_import_kwh"),
        "grid_export_kwh": data.get("grid_export_kwh"),
        "lost_surplus_kwh": data.get("lost_surplus_kwh"),
        "prevented_export_kwh": prevented_export_kwh,
        "daily_surplus": daily,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Battery Manager config entry."""
    coordinator: BatteryManagerCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )

    diagnostics: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "subentries": _subentries(entry),
    }

    if coordinator is None:
        # Setup failed / entry unloaded: still emit the entry snapshot.
        diagnostics["coordinator"] = None
        return diagnostics

    diagnostics["integration_version"] = coordinator.integration_version
    diagnostics["core_config"] = _core_config(coordinator)
    diagnostics["learned_state"] = coordinator.learned_state_snapshot()
    diagnostics["last_plan_metrics"] = _last_plan_metrics(coordinator)
    return diagnostics
