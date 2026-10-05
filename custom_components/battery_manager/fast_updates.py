"""React to telemetry without repeating a multi-day economic search."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import SUPPORT_MODE_AUTO, SUPPORT_MODE_MANUAL

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


async def async_fast_update(c: BatteryManagerCoordinator, *, trim: bool = True) -> None:
    """Use only a valid current-slot plan; never authorize from stale inputs."""
    if c._actuation_shutdown or c.hass.is_stopping:
        return
    await c._async_planning_protection()
    now = dt_util.now()
    c._update_load_runtime(now)
    if c._feedin_entities() is not None:
        c._feedin_tick(now)
    c._update_feedin_mode(now)
    c.appliances.update(dt_util.as_utc(now))
    captured = c._last_planner_recording
    if (
        captured is None
        or not c.last_update_success
        or not c.data
        or not c.data.get("valid")
    ):
        await c._feedin_force_zero("No valid plan for telemetry update")
        return
    planned_config, inputs, result = captured
    config = c.build_system_config()
    # Own setpoint confirmations and standby watt fluctuations leave the
    # assumptions unchanged. A manual export or an observed appliance cycle
    # changes them and must go through the full economic planner.
    changed = (
        config.feedin != planned_config.feedin
        or c.appliances.planning_signature() != c._planned_appliance_signature
    )
    try:
        c._ensure_planning_inputs(inputs, check_soc_drift=False)
    except UpdateFailed:
        changed = True
    if changed:
        if c._update_task is None:
            c._schedule_replan()
        await c._feedin_force_zero("Planning inputs changed during telemetry update")
    elif c._get_forecasts(now) is None:
        await c._feedin_force_zero("PV forecasts unavailable during telemetry update")
    elif trim:
        soc = c._get_soc(now)
        # _ensure_planning_inputs proved a numeric SOC above. Re-read after
        # any protection work so a telemetry pass never fabricates a reading.
        if soc is not None:
            today = now.date().isoformat()
            # The stored schedule books the remaining export AT CAPTURE.
            # Deduct only delivery since then, never the whole day's integral.
            delivered = c._feedin_tick(now)
            remaining = max(
                0.0,
                result.feedin_by_day_wh.get(today, 0.0)
                - max(0.0, delivered - c._planned_feedin_delivered_wh),
            )
            schedule = result.feedin_schedule_w
            if remaining <= 0 and schedule:
                schedule = (0.0, *schedule[1:])
            trimmed = replace(
                result,
                feedin_schedule_w=schedule,
                feedin_by_day_wh={**result.feedin_by_day_wh, today: remaining},
            )
            await c._apply_feedin(trimmed, config, soc, now)
        else:
            await c._feedin_force_zero("SOC unavailable during telemetry trim")
    realized = c._update_realized_surplus(now, c.data.get("daily_surplus", []))
    data = {
        **c.data,
        "feedin_mode": SUPPORT_MODE_MANUAL if c.feedin_manual() else SUPPORT_MODE_AUTO,
    }
    if realized is not None:
        data["realized"] = realized
    c.data = data
    # Preserve plan capture/activation and last_update: a measurement is not
    # a new forecast. Listeners still see live accounting and manual ownership.
    c.async_update_listeners()
