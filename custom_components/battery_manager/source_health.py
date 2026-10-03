"""Explicit meter normalization and read-only source quality diagnostics.

Publication age describes reports received by HA, not an inferred upstream
sampling cadence. State and cumulative counters therefore have no generic
expiry; callers opt into a power hold bound for their particular role.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

POWER_SOURCE_MAX_AGE_S = 600
_UNITS = {
    "power": {"mW": 0.001, "W": 1.0, "kW": 1000.0, "MW": 1e6, "GW": 1e9},
    "energy": {"Wh": 1.0, "kWh": 1000.0, "MWh": 1e6},
}


def normalized_measurement(state, kind: str, *, signed: bool = False) -> float | None:
    """Return finite W/Wh with an explicit unit; never guess from entity names."""
    if state is None:
        return None
    factor = _UNITS[kind].get(state.attributes.get("unit_of_measurement"))
    try:
        value = float(state.state)
    except TypeError, ValueError:
        return None
    if factor is None or not math.isfinite(value) or (value < 0 and not signed):
        return None
    normalized = value * factor
    return normalized if math.isfinite(normalized) else None


def publication_age_s(state, now: datetime) -> float | None:
    """Separate the last actual HA publication from a local observation tick."""
    reported = getattr(state, "last_reported", None)
    if not isinstance(reported, datetime) or reported.tzinfo is None:
        return None
    return now.timestamp() - reported.timestamp()


def source_health(
    hass,
    bindings: Mapping[str, str | None],
    now: datetime,
    *,
    kinds: Mapping[str, str] | None = None,
    fallbacks: Mapping[str, str | None] | None = None,
    max_ages_s: Mapping[str, float | None] | None = None,
    boundaries: Mapping[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Describe explicitly wired roles without changing sources or HA state.

    Signed meters retain their sign for grid balance diagnostics. The caller
    supplies the measuring boundary and fallback rather than inferring either.
    """
    result = []
    for role, entity in bindings.items():
        state = hass.states.get(entity) if entity else None
        kind = (kinds or {}).get(role, "state")
        age = publication_age_s(state, now)
        value = (
            normalized_measurement(state, kind, signed=True)
            if state is not None and kind in _UNITS
            else state.state
            if state is not None
            else None
        )
        maximum = (max_ages_s or {}).get(
            role, POWER_SOURCE_MAX_AGE_S if kind == "power" else None
        )
        status = (
            "not_configured"
            if not entity
            else "not_found"
            if state is None
            else state.state
            if state.state in {"unknown", "unavailable"}
            else "invalid"
            if value is None
            else "stale"
            if maximum is not None and (age is None or age < 0 or age > maximum)
            else "available"
        )
        result.append(
            {
                "role": role,
                "entity_id": entity,
                "status": status,
                "unit": ("W" if kind == "power" else "Wh")
                if kind in _UNITS
                else state.attributes.get("unit_of_measurement")
                if state
                else None,
                "native_unit": state.attributes.get("unit_of_measurement")
                if state
                else None,
                "value": value,
                "reported_at": state.last_reported.isoformat() if state else None,
                "publication_age_s": age,
                "coverage_start": None,
                "coverage_end": None,
                "fallback": (fallbacks or {}).get(role),
                "boundary": (boundaries or {}).get(role),
                "error": status
                if status not in {"available", "not_configured"}
                else None,
            }
        )
    return result
