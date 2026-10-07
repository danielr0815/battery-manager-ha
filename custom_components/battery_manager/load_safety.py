"""Measured no-import protection and completion of interrupted load stops."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import CONF_LOAD_CHARGE_ENABLE, CONF_LOAD_CONTROL_SWITCH, SUBENTRY_TYPE_LOAD
from .load_actuation import LoadAction

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

# Ignore meter noise around the ESS zero-import setpoint, never a load-sized
# deficit. This is an execution tolerance, not an economic import allowance.
LOAD_GRID_IMPORT_TOLERANCE_W = 50.0


def grid_import_w(c: BatteryManagerCoordinator) -> float | None:
    """Read the configured net connection; gross PV never proves load coverage."""
    entity = c.raw_config.get("live_ac_grid_power_entity") or c.raw_config.get(
        "operation_import_power_entity"
    )
    return c._reserve_power(entity)


def start_blocked(c: BatteryManagerCoordinator) -> bool:
    """Re-read fresh physical evidence at every asynchronous start boundary."""
    imported = grid_import_w(c)
    return bool(
        c._floor_guard_active
        or c._stale_shed_active
        or (imported is not None and imported > LOAD_GRID_IMPORT_TOLERANCE_W)
    )


async def stop_unsafe_loads(c: BatteryManagerCoordinator) -> None:
    """Stop only; the fast timer can neither allocate energy nor start a load.

    The July charging-path contract permits foreign passthrough inputs to stay
    ON. An OFF gate alone, however, must not erase a failed OFF of our own input
    (October incident: an external number action rejected its 20 % target).
    """
    if c._actuation_shutdown:
        return
    imported = grid_import_w(c)
    guard = imported is not None and imported > LOAD_GRID_IMPORT_TOLERANCE_W
    diag = {"active": guard, "grid_import_w": imported}
    if c.data:
        changed = c.data.get("load_grid_guard") != diag
        c.data["load_grid_guard"] = diag
        if guard:
            for plan in c.data.get("load_plans", {}).values():
                changed = changed or bool(plan.get("active"))
                plan["active"] = False
            c._load_plan_active = {key: False for key in c._load_plan_active}
        if changed:
            c.async_update_listeners()
    if c._load_switch_task is not None and not c._load_switch_task.done():
        return
    managed = c.cascade_manager.managed_load_ids()
    actions: list[LoadAction] = []
    for load_id, entry in c.entry.subentries.items():
        data = entry.data
        plug = data.get(CONF_LOAD_CONTROL_SWITCH)
        if (
            entry.subentry_type != SUBENTRY_TYPE_LOAD
            or not plug
            or load_id in managed
            or load_id == c._load_power_calibration_id
        ):
            continue
        owned = c._load_plug_owned.get(load_id, False)
        gate = data.get(CONF_LOAD_CHARGE_ENABLE)
        unfinished_stop = (
            owned
            and gate
            and c._entity_tristate(gate) is False
            and c._entity_tristate(plug) is True
        )
        if not (
            (guard and (c._charging_is_active(data) is not False or owned))
            or unfinished_stop
        ):
            continue
        actions.append(
            LoadAction(
                load_id,
                dict(data),
                False,
                c._entity_is_on(plug),
                reason="measured grid import" if guard else "unfinished input stop",
            )
        )
    if actions:
        c._load_switch_task = c.entry.async_create_background_task(
            c.hass,
            c._execute_load_switching(actions, dt_util.now()),
            name="battery_manager_load_safety",
        )
