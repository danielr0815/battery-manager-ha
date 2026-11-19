"""Measured no-import protection and completion of interrupted load stops."""

from __future__ import annotations

from asyncio import Task
from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.util import dt as dt_util

from .const import (
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_POWER_W,
    SUBENTRY_TYPE_LOAD,
)
from .load_actuation import LoadAction

if TYPE_CHECKING:
    from datetime import datetime

    from .coordinator import BatteryManagerCoordinator

# Ignore meter noise around the ESS zero-import setpoint, never a load-sized
# deficit. This is an execution tolerance, not an economic import allowance.
LOAD_GRID_IMPORT_TOLERANCE_W = 50.0
POWER_MEASUREMENT_MAX_AGE_SECONDS = 30


@dataclass(frozen=True)
class GridStopEvidence:
    decided_at: datetime
    path: Literal["plan", "fast", "confirmation"]
    observation: dict[str, Any]


def effective_start_power_w(
    c: BatteryManagerCoordinator, load_id: str, data: Any
) -> float | None:
    """A small nameplate cannot override larger learned/published start demand."""
    values = [data.get(CONF_LOAD_POWER_W)]
    values.append(getattr(c, "_load_learned_power_w", {}).get(load_id))
    plans = (getattr(c, "data", None) or {}).get("load_plans", {})
    values.append(plans.get(load_id, {}).get("planning_power_w"))
    powers = [
        float(value)
        for value in values
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value > 0
    ]
    return max(powers) if powers else None


def _grid_entity(c: BatteryManagerCoordinator) -> str | None:
    return c.raw_config.get("live_ac_grid_power_entity") or c.raw_config.get(
        "operation_import_power_entity"
    )


def grid_import_w(c: BatteryManagerCoordinator) -> float | None:
    """Read the configured net connection; gross PV never proves load coverage."""
    entity = _grid_entity(c)
    return c._reserve_power(entity) if entity else None


def grid_guard_diagnostic(
    c: BatteryManagerCoordinator, *, checked_at: datetime | None = None
) -> dict[str, Any]:
    """Keep the last meter reading visible even after the guard stops using it."""
    entity = _grid_entity(c)
    state = c.hass.states.get(entity) if entity else None
    measured_at = state.last_reported if state is not None else None
    now = checked_at or dt_util.utcnow()
    age = (now - measured_at).total_seconds() if measured_at else None
    watts = c._reserve_power_value(state) if state is not None else None
    fresh = age is not None and 0 <= age <= POWER_MEASUREMENT_MAX_AGE_SECONDS
    if not entity:
        status = "unconfigured"
    elif state is None or state.state in ("unknown", "unavailable"):
        status = "unavailable"
    elif watts is None or not isfinite(watts) or (age is not None and age < 0):
        status = "invalid"
    else:
        status = "fresh" if fresh else "stale"
    return {
        "status": status,
        # A numeric conversion overflow must still trip the existing guard.
        # No infinite value is a credible measurement or a state attribute.
        "active": fresh and watts is not None and watts > LOAD_GRID_IMPORT_TOLERANCE_W,
        "grid_import_w": watts if status == "fresh" else None,
        "measured_w": watts if status in ("fresh", "stale") else None,
        "entity_id": entity,
        "measured_at": measured_at.isoformat() if measured_at else None,
        "age_seconds": age,
        "threshold_w": LOAD_GRID_IMPORT_TOLERANCE_W,
        "max_age_seconds": POWER_MEASUREMENT_MAX_AGE_SECONDS,
        **(
            {"last_action": c._last_load_grid_action}
            if c._last_load_grid_action
            else {}
        ),
    }


def record_grid_stop(
    c: BatteryManagerCoordinator,
    evidence: GridStopEvidence,
    load_id: str,
) -> None:
    """Publish a dispatched grid stop with the measurement that triggered it."""
    # This runs synchronously at the service boundary. A delayed ON reply
    # can cause a new OFF even when its underlying grid decision is older.
    c._last_load_grid_action = {
        **{
            key: value
            for key, value in evidence.observation.items()
            if key not in ("last_action", "grid_import_w")
        },
        "action": "stop_requested",
        "path": evidence.path,
        "decided_at": evidence.decided_at.isoformat(),
        "requested_at": dt_util.utcnow().isoformat(),
        "load_ids": [load_id],
    }
    if c.data:
        c.data["load_grid_guard"] = {
            **(c.data.get("load_grid_guard") or grid_guard_diagnostic(c)),
            "last_action": c._last_load_grid_action,
        }
        # Let the authorized service call enter HA before notifying listeners.
        c.hass.loop.call_soon(c.async_update_listeners)


def confirmation_grid_stop(c: BatteryManagerCoordinator) -> GridStopEvidence | None:
    """Capture the physical cause once for a revoked or interrupted start."""
    checked_at = dt_util.utcnow()
    observation = grid_guard_diagnostic(c, checked_at=checked_at)
    if not observation["active"]:
        return None
    return GridStopEvidence(checked_at, "confirmation", observation)


def start_blocked(c: BatteryManagerCoordinator) -> bool:
    """Re-read fresh physical evidence at every asynchronous start boundary."""
    imported = grid_import_w(c)
    return bool(
        c._floor_guard_active
        or c._stale_shed_active
        or (imported is not None and imported > LOAD_GRID_IMPORT_TOLERANCE_W)
    )


async def stop_unsafe_loads(c: BatteryManagerCoordinator) -> tuple[Task[None], ...]:
    """Stop only; the fast timer can neither allocate energy nor start a load.

    The July charging-path contract permits foreign passthrough inputs to stay
    ON. An OFF gate alone, however, must not erase a failed OFF of our own input
    (October incident: an external number action rejected its 20 % target).
    """
    if c._actuation_shutdown:
        return ()
    from .load_actuation import coordinate_cleanup_pending, reconcile_feedback

    decided_at = dt_util.utcnow()
    diag = grid_guard_diagnostic(c, checked_at=decided_at)
    guard = diag["active"]
    reconcile_feedback(c)
    # Reconciling a late ON may run its cleanup eagerly and dispatch an OFF
    # before this timer resumes publishing the current meter observation.
    if c._last_load_grid_action is not None:
        diag["last_action"] = c._last_load_grid_action
    changed = False
    if c.data:
        previous = c.data.get("load_grid_guard") or {}
        # Repeated HA reports keep an equal reading fresh. Retain the current
        # evidence without notifying all entities just for a new timestamp.
        changed = previous != {
            **diag,
            "measured_at": previous.get("measured_at"),
            "age_seconds": previous.get("age_seconds"),
        }
        c.data["load_grid_guard"] = diag
        if guard:
            for plan in c.data.get("load_plans", {}).values():
                changed = changed or bool(plan.get("active"))
                plan["active"] = False
            c._load_plan_active = {key: False for key in c._load_plan_active}
    managed = c.cascade_manager.managed_load_ids()
    tasks: list[Task[None]] = []
    grid_stop = GridStopEvidence(decided_at, "fast", diag) if guard else None
    for load_id, entry in c.entry.subentries.items():
        data = entry.data
        plug = data.get(CONF_LOAD_CONTROL_SWITCH)
        if (
            entry.subentry_type != SUBENTRY_TYPE_LOAD
            or not plug
            or not c.load_bm_enabled(load_id)
            or load_id in managed
            or load_id == c._load_power_calibration_id
        ):
            continue
        owned = c._load_plug_owned.get(load_id, False)
        gate = data.get(CONF_LOAD_CHARGE_ENABLE)
        revoked_start = coordinate_cleanup_pending(c, load_id)
        unfinished_stop = (
            owned
            and gate
            and c._entity_tristate(gate) is False
            and c._entity_tristate(plug) is True
        )
        if not (
            (guard and (c._charging_is_active(data) is not False or owned))
            or unfinished_stop
            or revoked_start
        ):
            continue
        running = c._load_safety_tasks.get(load_id)
        if running is not None and not running.done():
            tasks.append(running)
            continue
        reason = (
            "revoked load start"
            if revoked_start
            else "measured grid import"
            if guard
            else "unfinished input stop"
        )
        pending = c.live_ac.revoked_load_starts[load_id] if revoked_start else None
        task = c.entry.async_create_background_task(
            c.hass,
            c._execute_load_switching(
                [
                    LoadAction(
                        load_id,
                        dict(data),
                        False,
                        c._entity_is_on(plug),
                        reason=reason,
                        coordinate_cleanup=pending.revision if pending else None,
                        grid_stop=grid_stop if pending is None else None,
                        report_only_changes=True,
                    )
                ],
                dt_util.now(),
            ),
            name="battery_manager_load_safety",
        )
        c._load_safety_tasks[load_id] = task
        tasks.append(task)
    if c.data and changed:
        c.async_update_listeners()
    return tuple(tasks)
