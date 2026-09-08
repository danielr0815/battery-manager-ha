"""Read-only projection of known executor constraints, never a readiness promise."""

from datetime import timedelta

from homeassistant.util import dt as dt_util

from .const import (
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_HANDOVER_TIMEOUT_S,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_OUTPUT_SWITCH,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    PREDRAIN_BLOCK_STABLE_MINUTES,
    PREDRAIN_BLOCK_STABLE_PLANS,
    SUBENTRY_TYPE_CASCADE,
)
from .core.series import fixed_local_time


def load_execution(coordinator, load_id, data, now):
    """Return timed constraints separately from unconfirmed physical progress."""
    c = coordinator
    release = c._load_not_before(load_id, data, now)
    current = (
        c._charging_is_active(data) if data.get(CONF_LOAD_CONTROL_SWITCH) else None
    )
    result = {
        "phase": "running" if current is True else "idle",
        "not_before": release,
        "minimum_run_until": None,
        "predrain_not_before": None,
        "confirmation_pending": bool(
            data.get(CONF_LOAD_CONTROL_SWITCH) and current is not True
        ),
        "check_at": None,
        "stable_plans": 0,
        "required_stable_plans": PREDRAIN_BLOCK_STABLE_PLANS,
    }
    for cid, entry in c.entry.subentries.items():
        if entry.subentry_type != SUBENTRY_TYPE_CASCADE:
            continue
        members = entry.data.get("member_load_ids", [])
        terminal = entry.data.get("terminal_load_id")
        if load_id not in [*members, terminal]:
            continue
        state = c.cascade_manager._state(cid)
        phase = state.get("phase", "idle")
        pending = phase in (
            "waking",
            "waking_members",
            "proving",
            "testing_terminal",
        ) or bool(state.get("restart_reconcile_pending"))
        result["phase"] = (
            "restart_reconciliation"
            if state.get("restart_reconcile_pending")
            else phase
        )
        if load_id == terminal:
            upstream = c.entry.subentries.get(members[-1]) if members else None
            output = upstream.data.get(CONF_LOAD_OUTPUT_SWITCH) if upstream else None
            pending = (
                pending
                or not output
                or not c._entity_is_on(output)
                or (bool(data.get(CONF_LOAD_CONTROL_SWITCH)) and current is not True)
            )
        result["confirmation_pending"] = bool(
            pending or state.get("fault") or not state.get("enabled")
        )
        # Absolute failure/proof deadlines remain diagnostic; never use as not_before.
        deadline = None
        if phase in ("waking", "waking_members"):
            deadline = state.get("wake_actor_deadline") or state.get("wake_deadline")
        elif phase == "recovering":
            deadline = state.get("retry_at")
        result["check_at"] = dt_util.parse_datetime(deadline) if deadline else None
        proof = c.cascade_manager._proof.get(cid)
        if phase == "proving" and proof:
            source = c.entry.subentries.get(state.get("source"))
            if source is not None:
                # This is the executor's absolute timeout, not a fresh polling
                # interval: missing samples cannot slide it into the future.
                result["check_at"] = proof["started"] + timedelta(
                    seconds=float(source.data.get(CONF_LOAD_HANDOVER_TIMEOUT_S, 180))
                )
        return result
    if current is True:
        last = c._last_load_switch.get(load_id)
        target_stop = (
            data.get(CONF_LOAD_ENERGY_LIMITED)
            and data.get(CONF_LOAD_CHARGE_ENABLE)
            and data.get(CONF_LOAD_SOC_ENTITY)
            and (soc := c._read_float(data[CONF_LOAD_SOC_ENTITY])) is not None
            and soc >= float(data.get(CONF_LOAD_TARGET_SOC, 100))
        )
        if (
            last is not None
            and not target_stop
            and not c._floor_guard_active
            and not c._stale_shed_active
            and load_id not in c._load_power_calibration_release
            and c._load_power_calibration_id != load_id
        ):
            until = last + timedelta(
                minutes=int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30))
            )
            if until > now:
                result["minimum_run_until"] = fixed_local_time(dt_util.as_local(until))
    elif data.get(CONF_LOAD_CONTROL_SWITCH) and not data.get(CONF_LOAD_ENERGY_LIMITED):
        evidence = c._predrain_block_evidence.get(load_id)
        count, first_seen = (evidence[1], evidence[2]) if evidence else (0, now)
        result["stable_plans"] = count
        until = first_seen + timedelta(minutes=PREDRAIN_BLOCK_STABLE_MINUTES)
        if until > now:
            result["predrain_not_before"] = fixed_local_time(dt_util.as_local(until))
        if load_id not in c._predrain_block_stable:
            result["phase"] = "waiting_stability"
    if release is not None:
        result["phase"] = "minimum_pause"
    return result


def execution_attributes(value):
    """ISO timestamps for HA while the core receives datetime constraints."""
    return {
        key: item.isoformat() if hasattr(item, "isoformat") else item
        for key, item in value.items()
    }
