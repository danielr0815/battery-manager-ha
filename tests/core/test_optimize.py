"""Scenario tests for the planner (docs/ALGORITHM.md §3, S1-S4 + regressions)."""

from dataclasses import replace
from datetime import datetime, timedelta

from core.model import (
    Appliance,
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    LoadProfile,
    PlanInputs,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.optimize import (
    MERGE_TERMINAL_RAMP_WH,
    _committed_hours,
    _crossday_daytime_bet,
    _degrades_min_soc,
    _quantised_hours,
    _refill_index,
    _slot_serviceable,
    _spread_energy,
    _terminal_credit_factor,
    _threshold_merge_probe,
    _windowed_min_soc,
    _z4_reject,
    allocate_loads,
    appliance_windows,
    plan,
    pv_windows,
    search_threshold,
    support_escalation,
)
from core.series import build_slots, insert_appliance_run
from core.simulate import simulate

FOSSIBOT_1 = SurplusLoad(
    load_id="fossibot_1",
    name="Fossibot 1",
    nominal_power_w=300.0,
    energy_limited=True,
    capacity_wh=2000.0,
)
FOSSIBOT_2 = SurplusLoad(
    load_id="fossibot_2",
    name="Fossibot 2",
    nominal_power_w=300.0,
    energy_limited=True,
    capacity_wh=2000.0,
)
DEHUMIDIFIER = SurplusLoad(
    load_id="dehumidifier",
    name="Entfeuchter",
    nominal_power_w=400.0,
)
ALL_LOADS = (FOSSIBOT_1, FOSSIBOT_2, DEHUMIDIFIER)

EMPTY_STATES = (
    SurplusLoadState(load_id="fossibot_1", soc_percent=0.0),
    SurplusLoadState(load_id="fossibot_2", soc_percent=0.0),
    SurplusLoadState(load_id="dehumidifier"),
)


def make_plan(config, now, soc, forecasts, load_states=()):
    inputs = build_slots(config, now, soc, forecasts, load_states=tuple(load_states))
    return plan(config, inputs), inputs


def daylight(slot):
    return 7 <= slot.hour_of_day < 18


def test_s1_evening_before_sunny_day_makes_room():
    """Evening, sunny tomorrow: discharge overnight (threshold below SOC)."""
    config = SystemConfig()
    now = datetime(2026, 7, 3, 20, 0)
    result, _ = make_plan(config, now, 80.0, [0.0, 14.0, 12.0])
    assert result.threshold_percent < 80.0
    assert result.inverter_on
    assert result.grid_import_kwh < 0.1


def test_s2_cloudy_days_reserve_battery_for_dc_path():
    """No surplus coming: the battery is worth more on the efficient DC path.

    Discharging via inverter costs eta_dis*eta_inv + standby, and once empty
    the DC rail imports through the charger at 1/eta penalty. The optimizer
    discovers this and keeps the inverter off — total cost is strictly lower
    than any discharge policy (verified numerically, see docs/ALGORITHM.md).
    """
    config = SystemConfig()
    now = datetime(2026, 7, 3, 20, 0)
    result, _ = make_plan(config, now, 60.0, [0.0, 1.5, 2.0])
    assert not result.inverter_on
    assert result.threshold_percent >= 60.0
    # And it must sit at the LOWER edge of the equal-cost plateau (D-A1b).
    assert result.threshold_percent == 60.0


def test_s3_regression_loads_never_cause_grid_import():
    """THE reported bug: load activation must never lead to grid purchases.

    Since the objective-based pass 2 (D-A4 v2), night hours ARE allowed when
    the re-simulation proves the energy comes from otherwise-lost surplus —
    but the hard guarantee stands: grid import must not increase, and
    energy-limited storage loads still prefer the sun window.
    """
    config = SystemConfig(loads=ALL_LOADS)
    now = datetime(2026, 7, 3, 21, 0)
    result, inputs = make_plan(
        config, now, 84.0, [0.0, 13.0, 11.0], load_states=EMPTY_STATES
    )
    # Z2: with huge surplus ahead, the plan must stay import-free.
    assert result.grid_import_kwh < 0.1
    # Fossibots saturate in daylight (pass 1); no night charging for them.
    for load_plan in result.load_plans:
        if load_plan.load_id.startswith("fossibot"):
            for i, active in enumerate(load_plan.schedule):
                if active:
                    assert daylight(inputs.slots[i]), (
                        f"{load_plan.load_id} scheduled at "
                        f"{inputs.slots[i].hour_of_day}:00 (night!)"
                    )
    # SOC buffer floor holds despite any preemptive dehumidifier hours.
    floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    assert result.min_soc_percent >= floor - 0.01


def test_s3_low_soc_night_stays_quiet():
    """At 50% SOC before a sunny day, night activation would hit the inverter
    floor and cause import — pass 2 must reject it (the 5-o'clock scenario)."""
    config = SystemConfig(loads=ALL_LOADS)
    now = datetime(2026, 7, 5, 5, 0)
    result, inputs = make_plan(
        config, now, 50.0, [14.0, 12.0, 13.0], load_states=EMPTY_STATES
    )
    assert result.grid_import_kwh < 0.1
    # Fossibots: strictly daylight (they saturate in the sun window).
    for load_plan in result.load_plans:
        if load_plan.load_id.startswith("fossibot"):
            for i, active in enumerate(load_plan.schedule):
                if active:
                    assert daylight(inputs.slots[i])


def test_s3_loads_do_get_scheduled_in_daylight():
    config = SystemConfig(loads=ALL_LOADS)
    now = datetime(2026, 7, 3, 21, 0)
    result, _ = make_plan(
        config, now, 84.0, [0.0, 13.0, 11.0], load_states=EMPTY_STATES
    )
    total_planned = sum(p.planned_energy_wh for p in result.load_plans)
    assert total_planned > 1000.0  # surplus is huge; loads must absorb some
    # Load allocation reduces lost surplus below the no-load export.
    assert result.lost_surplus_kwh < result.grid_export_kwh + 1e-9


def test_s4_midday_full_battery_runs_loads_now():
    config = SystemConfig(loads=ALL_LOADS)
    now = datetime(2026, 7, 4, 11, 0)
    result, _ = make_plan(
        config, now, 93.0, [10.0, 12.0, 11.0], load_states=EMPTY_STATES
    )
    assert any(p.active_now for p in result.load_plans)


def test_saturated_fossibot_is_skipped():
    config = SystemConfig(loads=(FOSSIBOT_1,))
    now = datetime(2026, 7, 4, 11, 0)
    states = (SurplusLoadState(load_id="fossibot_1", soc_percent=100.0),)
    result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
    assert result.load_plans[0].planned_energy_wh == 0.0


def test_unavailable_load_is_never_scheduled():
    config = SystemConfig(loads=(DEHUMIDIFIER,))
    now = datetime(2026, 7, 4, 11, 0)
    states = (SurplusLoadState(load_id="dehumidifier", available=False),)
    result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
    assert result.load_plans[0].planned_energy_wh == 0.0


def test_unknown_soc_means_full_charging_need():
    """No SOC ever read (sleeping powerstation, empty cache): the load is
    treated as needing a full charge — self-healing once it wakes (F-L2)."""
    config = SystemConfig(loads=(FOSSIBOT_1,))
    now = datetime(2026, 7, 4, 11, 0)
    states = (SurplusLoadState(load_id="fossibot_1", soc_percent=None),)
    result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
    assert result.load_plans[0].planned_energy_wh > 0


def test_measured_feedback_power_overrides_nominal():
    config = SystemConfig(loads=(FOSSIBOT_1,))
    now = datetime(2026, 7, 4, 11, 0)
    states = (
        SurplusLoadState(load_id="fossibot_1", soc_percent=0.0, measured_power_w=250.0),
    )
    result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
    lp = result.load_plans[0]
    assert sum(lp.schedule) > 0
    # Planned energy must reflect the measured 250 W, not the nominal 300 W —
    # summed over the actually booked run hours (a residual may book a sub-hour
    # quantum now that energy-limited loads quantise too, F-RESIDUAL-TOPUP R1).
    assert abs(lp.planned_energy_wh - 250.0 * sum(lp.run_hours)) < 1e-6


def test_appliance_window_open_on_sunny_day():
    washer = Appliance(
        appliance_id="washer",
        name="Waschmaschine",
        run_energy_wh=1000.0,
        run_duration_h=2.0,
        opportunistic_start=True,
    )
    config = SystemConfig(appliances=(washer,))
    now = datetime(2026, 7, 4, 10, 0)
    result, _ = make_plan(config, now, 90.0, [15.0, 15.0, 15.0])
    assert result.appliance_windows["washer"] is True


def test_appliance_window_closed_at_night_with_low_battery():
    washer = Appliance(
        appliance_id="washer",
        name="Waschmaschine",
        run_energy_wh=1000.0,
        run_duration_h=2.0,
        opportunistic_start=True,
    )
    config = SystemConfig(appliances=(washer,))
    now = datetime(2026, 7, 3, 22, 0)
    result, _ = make_plan(config, now, 25.0, [0.0, 2.0, 2.0])
    assert result.appliance_windows["washer"] is False


def test_appliance_window_empty_horizon_is_false():
    """An empty horizon must NOT green-light a start (code review 2026-07):
    the trial trajectory imports 0 Wh and cannot degrade the min SOC, so both
    advisor gates used to pass vacuously on zero evidence."""
    washer = Appliance(
        appliance_id="washer",
        name="Waschmaschine",
        run_energy_wh=1000.0,
        run_duration_h=2.0,
        opportunistic_start=True,
    )
    config = SystemConfig(appliances=(washer,))
    inputs = PlanInputs(
        now=datetime(2026, 7, 4, 12, 0), start_soc_percent=80.0, slots=()
    )
    planned = simulate(config, inputs, 20.0)
    assert not planned.flows  # the empty horizon under test
    windows = appliance_windows(config, inputs, 20.0, (), planned)
    assert windows == {"washer": False}


def test_appliance_window_evaluated_under_support_policy():
    """Review #2: the appliance advisor must simulate the hypothetical run under
    the SAME support-PSU schedules as the planned trajectory it compares against
    — otherwise it evaluates the run with the PSUs off and gives false window
    advisories whenever support is active."""
    washer = Appliance(
        appliance_id="washer",
        name="W",
        run_energy_wh=2000.0,
        run_duration_h=2.0,
        opportunistic_start=True,
    )
    config = SystemConfig(
        appliances=(washer,),
        support=SupportParams(configured=True, dc48_forced_on=True, dc48_power_w=600.0),
    )
    now = datetime(2026, 7, 4, 12, 0)
    inputs = build_slots(config, now, 95.0, [1.0, 0.0])
    threshold, base = search_threshold(config, inputs)
    _, extra_ac, traj = allocate_loads(config, inputs, threshold, base)
    dc24, dc48, traj = support_escalation(config, inputs, threshold, extra_ac, traj)

    win = appliance_windows(
        config,
        inputs,
        threshold,
        extra_ac,
        traj,
        dc24_schedule=dc24,
        dc48_schedule=dc48,
    )

    # The fixed advisor must equal a judgment recomputed under the SAME support
    # schedules as the baseline.
    test_inputs = insert_appliance_run(
        inputs, washer.run_energy_wh, washer.run_duration_h
    )
    run_traj = simulate(
        config,
        test_inputs,
        threshold,
        extra_ac_wh=extra_ac,
        dc24_schedule=dc24,
        dc48_schedule=dc48,
    )
    buffer_floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    expected = run_traj.total_import_wh <= traj.total_import_wh + 1e-9 and (
        not _degrades_min_soc(run_traj, traj, buffer_floor)
    )
    assert win["washer"] == expected

    # And prove the schedules materially change the run simulation (so the
    # PSU-off evaluation really was a different, wrong baseline).
    buggy_run = simulate(config, test_inputs, threshold, extra_ac_wh=extra_ac)
    assert abs(buggy_run.total_import_wh - run_traj.total_import_wh) > 1e-6


def test_support_escalates_when_battery_would_fall_through():
    config = SystemConfig(support=SupportParams(configured=True))
    now = datetime(2026, 7, 3, 22, 0)
    soc_start = config.battery.soc_min_percent + 2.0  # already below buffer floor
    result, _ = make_plan(config, now, soc_start, [0.0, 0.0, 0.0])
    assert result.support_dc24_now is True
    assert result.min_soc_percent >= config.battery.soc_min_percent - 0.5


def test_support_stays_off_when_unconfigured():
    config = SystemConfig()  # support not configured
    now = datetime(2026, 7, 3, 22, 0)
    result, _ = make_plan(config, now, 7.0, [0.0, 0.0, 0.0])
    assert result.support_dc24_now is False
    assert result.support_dc48_now is False


def _esc_slot(index, *, pv, dc, hour=0):
    """A bare simulation slot: no AC load, so only the DC path moves the SOC."""
    return HourSlot(
        index=index,
        start=datetime(2026, 1, 1, hour),
        duration=1.0,
        hour_of_day=hour,
        pv_wh=pv,
        ac_wh=0.0,
        dc_wh=dc,
    )


def test_support_dc24_recovery_soc_latches():
    """A higher 24 V recover-SOC keeps the grid support latched on through a
    partial SOC recovery instead of releasing at the default 11 % — the fix for
    the overnight chatter when the SOC parks just above the activate level."""
    base_cfg = SystemConfig(support=SupportParams(configured=True))
    # dc24 activate 10 %; narrow recover 11 %, wide recover 15 %.
    slots = (
        _esc_slot(0, pv=0.0, dc=350.0),  # drains below the 24 V activate level
        _esc_slot(1, pv=500.0, dc=0.0, hour=1),  # partial PV recovery into (11, 15)
        _esc_slot(2, pv=0.0, dc=0.0, hour=2),  # holds at the recovered level
    )
    inputs = PlanInputs(
        now=datetime(2026, 1, 1, 0), start_soc_percent=12.0, slots=slots
    )
    threshold = 100.0  # inverter parked off: isolates the DC path
    extra_ac = (0.0, 0.0, 0.0)

    def run(recover_soc):
        cfg = replace(
            base_cfg,
            control=replace(base_cfg.control, support_dc24_recovery_soc=recover_soc),
        )
        base = simulate(cfg, inputs, threshold, extra_ac_wh=extra_ac)
        dc24, _dc48, _ = support_escalation(cfg, inputs, threshold, extra_ac, base)
        return dc24

    narrow = run(11.0)  # historical default
    wide = run(15.0)
    # Both engage while the battery is below the activate level...
    assert narrow[0] is True and wide[0] is True
    # ...but only the higher recover-SOC stays latched across the recovery.
    assert narrow[1] is False
    assert wide[1] is True and wide[2] is True
    assert sum(wide) > sum(narrow)


def test_dc48_activate_soc_configurable():
    """A higher 48 V activate-SOC makes the last-resort PSU engage at a higher
    SOC — deeper protection triggers earlier. A constant native-48 V load drains
    the battery even when the 24 V rail is grid-fed, so the 48 V stage is the
    only relief left."""
    base_cfg = SystemConfig(
        support=SupportParams(configured=True, native48_base_w=121.0)
    )
    slots = (_esc_slot(0, pv=0.0, dc=300.0),)
    inputs = PlanInputs(now=datetime(2026, 1, 1, 0), start_soc_percent=9.0, slots=slots)
    threshold = 100.0
    extra_ac = (0.0,)

    def run(activate_soc):
        cfg = replace(
            base_cfg,
            control=replace(base_cfg.control, support_dc48_activate_soc=activate_soc),
        )
        base = simulate(cfg, inputs, threshold, extra_ac_wh=extra_ac)
        _dc24, dc48, _ = support_escalation(cfg, inputs, threshold, extra_ac, base)
        return dc48

    dc48_narrow = run(5.5)  # historical default: activate at 5.5 %
    dc48_wide = run(8.0)  # activate at 8 %
    # The 24 V-supported SOC sits ~6.5 %: only the raised threshold engages 48 V.
    assert not any(dc48_narrow)
    assert any(dc48_wide)


def _twilight(day: datetime, *hours: int) -> dict[datetime, float]:
    """A few Wh of dawn shoulder light for the given hours (F-GATE-PARITY).

    The synthetic two-window PV model leaves pre-window hours at EXACTLY
    0 Wh, which the daylight rule reads as night. Real hourly forecasts
    never do that — any morning hour carries a little light — so the
    short-peak scenarios model it explicitly: 5 Wh per hour is far below
    the 200 W strong-PV cutoff (the window stays short) and energetically
    negligible, but marks the hours as daylight. The uncovered rest of the
    day keeps its two-window shape via the residual spread."""
    return {day.replace(hour=h, minute=0): 5.0 for h in hours}


def test_pass2_preemptive_charging_when_sun_window_too_short():
    """Short, strong production peak: a powerstation cannot saturate within
    the window, so pass 2 pre-charges it from the battery — provably refilled
    from otherwise-lost surplus, without any grid import. Since F-GATE-PARITY
    the pre-charge may only sit in DAYLIGHT hours (dawn shoulder light before
    the window); zero-PV night hours are barred for energy-limited loads."""
    from core.model import PVParams

    short_peak = PVParams(
        peak_power_w=3200.0,
        morning_start_hour=11,
        morning_end_hour=13,
        afternoon_end_hour=14,
        morning_ratio=0.7,
    )
    config = SystemConfig(pv=short_peak, loads=(FOSSIBOT_1,))
    now = datetime(2026, 7, 5, 5, 0)
    states = (SurplusLoadState(load_id="fossibot_1", soc_percent=0.0),)
    # Single-day horizon: only 3 window hours exist — not enough for 2 kWh.
    inputs = build_slots(
        config,
        now,
        85.0,
        [8.0],
        load_states=states,
        pv_hourly=_twilight(now, 8, 9, 10),
    )
    result = plan(config, inputs)

    window_hours = sum(
        1
        for i, on in enumerate(result.load_plans[0].schedule)
        if on and 11 <= inputs.slots[i].hour_of_day < 14
    )
    preemptive_hours = sum(result.load_plans[0].schedule) - window_hours
    # More energy than the window alone could deliver, thanks to pass 2 ...
    assert preemptive_hours > 0
    # ... never in a zero-PV (night) slot (F-GATE-PARITY daylight rule) ...
    assert all(
        inputs.slots[i].pv_wh > 0.0
        for i, on in enumerate(result.load_plans[0].schedule)
        if on
    )
    # ... without increasing grid import over the no-load baseline (Z2) ...
    _, baseline = search_threshold(config, inputs)
    assert result.grid_import_kwh <= baseline.total_import_wh / 1000.0 + 1e-6
    # ... and with the buffer floor intact (Z3).
    floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    assert result.min_soc_percent >= floor - 0.01


def test_pass2_rejects_preemption_without_surplus_ahead():
    """Cloudy horizon: pass 2 must schedule nothing (no export to shift)."""
    config = SystemConfig(loads=ALL_LOADS)
    now = datetime(2026, 7, 3, 20, 0)
    result, _ = make_plan(config, now, 60.0, [0.0, 1.5, 2.0], load_states=EMPTY_STATES)
    assert all(p.planned_energy_wh == 0.0 for p in result.load_plans)


def test_load_allowed_despite_unrelated_future_soc_dip():
    """Operator insight (2026-07-04): once the battery reaches max SOC in both
    variants, their futures are identical — a cloudy-tail SOC dip late in the
    horizon exists with AND without the load, so it must not veto today's
    surplus hours."""
    from core.model import LoadProfile

    config = SystemConfig(
        loads=(DEHUMIDIFIER,),
        # Heavy DC load drains the battery through the cloudy tail.
        dc_profile=LoadProfile(base_w=150.0, variable_w=0.0),
    )
    now = datetime(2026, 7, 4, 11, 0)
    result, _ = make_plan(config, now, 90.0, [12.0, 0.0, 0.0])
    floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    # The base plan itself dips below the buffer floor on the cloudy days ...
    assert result.min_soc_percent < floor
    # ... yet today's surplus hours are still used by the load.
    assert result.load_plans[0].planned_energy_wh > 0


def test_threshold_search_is_policy_consistent():
    config = SystemConfig()
    now = datetime(2026, 7, 3, 18, 0)
    inputs = build_slots(config, now, 70.0, [5.0, 8.0, 6.0])
    threshold, traj = search_threshold(config, inputs)
    for flow in traj.flows:
        assert flow.inverter_on == (flow.soc_start_percent > threshold)


# ---------------------------------------------------------------------------
# Degenerate slot 0 / min-runtime commitment (live incident 2026-07-05 04:59:
# a 1-minute slot let a ~5 Wh plan pass every gate, the executor dwell then
# charged ~250 Wh from the house battery at night).
# ---------------------------------------------------------------------------

FOSSIBOT_B = SurplusLoad(
    load_id="fossibot_b",
    name="Fossibot F2400-B",
    nominal_power_w=300.0,
    energy_limited=True,
    capacity_wh=2000.0,
    target_soc_percent=90.0,
)


def test_degenerate_slot0_never_triggers_min_runtime_charge():
    """A load 6 Wh below target must not be activated in ANY minute of the
    hour — switching on would really charge min_runtime * power (~150 Wh)."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=89.7),)
    for minute in (0, 30, 45, 55, 58, 59):
        now = datetime(2026, 7, 5, 4, minute)
        result, _ = make_plan(config, now, 55.0, [6.0, 6.0], load_states=states)
        load_plan = result.load_plans[0]
        assert not load_plan.active_now, f"activated at 04:{minute:02d}"
        assert load_plan.planned_energy_wh == 0.0


def test_activation_books_at_least_min_runtime_energy():
    """Whenever slot 0 is activated, the plan must have booked the energy the
    executor's dwell will really deliver — never a sliver of the dying slot.

    Since F-PLANNER-HONESTY R7 an energy-limited load walks pass 1 latest-
    first, so a *now* activation only happens when slot 0 IS the latest
    feasible surplus hour: end of the day's window, battery at the cap, the
    last export dribble about to be lost."""
    config = SystemConfig(loads=(FOSSIBOT_1,))
    states = (SurplusLoadState(load_id="fossibot_1", soc_percent=0.0),)
    now = datetime(2026, 7, 4, 17, 1)  # last export hour of the day
    result, inputs = make_plan(config, now, 94.0, [12.0], load_states=states)
    load_plan = result.load_plans[0]
    assert load_plan.active_now
    min_commit_wh = FOSSIBOT_1.nominal_power_w * FOSSIBOT_1.min_runtime_min / 60.0
    slot0_alloc = [a for a in load_plan.allocations if a[0] == 0]
    assert slot0_alloc and slot0_alloc[0][3] >= min_commit_wh - 1e-6
    assert load_plan.run_hours[0] >= FOSSIBOT_1.min_runtime_min / 60.0 - 1e-9


def test_pass2_places_preemptive_hours_latest_first():
    """Operator decision 2026-07-05 (F-L5): preemptive (non-surplus) hours
    must sit as late as the constraints allow — directly before the next
    day's production window, NOT the evening before (the old earliest-first
    scan charged at 20:00-22:00 in this scenario)."""
    from core.model import PVParams

    short_peak = PVParams(
        peak_power_w=3200.0,
        morning_start_hour=11,
        morning_end_hour=13,
        afternoon_end_hour=14,
        morning_ratio=0.7,
    )
    config = SystemConfig(pv=short_peak, loads=(FOSSIBOT_1,))
    now = datetime(2026, 7, 4, 20, 0)
    states = (SurplusLoadState(load_id="fossibot_1", soc_percent=0.0),)
    # Dawn shoulder light marks 8-10 as daylight (F-GATE-PARITY: an
    # energy-limited pre-charge may not sit in zero-PV night slots).
    inputs = build_slots(
        config,
        now,
        90.0,
        [0.0, 8.0],
        load_states=states,
        pv_hourly=_twilight(datetime(2026, 7, 5, 0, 0), 8, 9, 10),
    )
    result = plan(config, inputs)

    preemptive = [
        inputs.slots[i].hour_of_day
        for i, on in enumerate(result.load_plans[0].schedule)
        if on and not (11 <= inputs.slots[i].hour_of_day < 14)
    ]
    assert preemptive, "short window must force preemptive hours"
    # No evening-before charging: everything preemptive sits in the morning
    # hours hugging the window start.
    assert not [h for h in preemptive if h >= 14], (
        f"evening-before hours scheduled: {sorted(preemptive)}"
    )
    window_pre = [h for h in preemptive if h < 11]
    assert window_pre
    assert max(window_pre) == 10
    assert min(window_pre) == 11 - len(window_pre), (
        f"preemptive hours {sorted(window_pre)} are not the latest block"
    )


def test_saturation_gate_floors_at_nominal_power():
    """A decayed feedback EMA (e.g. 40 W charge taper) must not weaken the
    saturation gate: remaining below one nominal commit block => no hours."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    states = (
        SurplusLoadState(load_id="fossibot_b", soc_percent=89.7, measured_power_w=40.0),
    )
    now = datetime(2026, 7, 5, 4, 59)
    result, _ = make_plan(config, now, 55.0, [6.0, 6.0], load_states=states)
    assert result.load_plans[0].planned_energy_wh == 0.0


def test_saturation_gate_floor_blocks_despite_surplus():
    """The nominal floor must gate even when the measured-power commitment
    would fit: remaining (100 Wh) sits between measured*commit (40-80 Wh)
    and nominal*commit (300+ Wh) while surplus is huge."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    states = (
        SurplusLoadState(load_id="fossibot_b", soc_percent=85.0, measured_power_w=40.0),
    )
    for minute in (0, 59):
        now = datetime(2026, 7, 4, 11, minute)
        result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
        assert result.load_plans[0].planned_energy_wh == 0.0, (
            f"allocated at 11:{minute:02d} despite nominal floor"
        )


def test_long_min_runtime_gates_interior_hours_consistently():
    """min_runtime > 60 min: interior hours must be booked as the full
    multi-hour block they will really execute as — a budget below one block
    yields no hours at all instead of phantom 1-h plans that evaporate when
    their hour arrives."""
    slow_burner = SurplusLoad(
        load_id="slow",
        name="Slow burner",
        nominal_power_w=300.0,
        min_runtime_min=240,
        energy_limited=True,
        capacity_wh=2000.0,
    )
    config = SystemConfig(loads=(slow_burner,))
    # remaining = 20% x 2000 = 400 Wh < 300 W x 4 h = 1200 Wh commit block.
    states = (SurplusLoadState(load_id="slow", soc_percent=80.0),)
    now = datetime(2026, 7, 4, 8, 0)
    result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
    assert result.load_plans[0].planned_energy_wh == 0.0
    assert not any(result.load_plans[0].schedule)


def test_forced_support_paths_are_simulated_as_always_on():
    """F-N2: a manually activated PSU must shape the whole trajectory —
    dc24 forced: DC load runs from grid everywhere; dc48 forced: constant
    injection. Both schedules come back all-True."""
    config = SystemConfig(
        support=SupportParams(configured=True, dc24_forced_on=True, dc48_forced_on=True)
    )
    now = datetime(2026, 7, 3, 22, 0)
    result, inputs = make_plan(config, now, 40.0, [0.0, 2.0, 2.0])
    n = len(inputs.slots)
    assert all(f.support_dc24 for f in result.trajectory.flows)
    assert all(f.support_dc48 for f in result.trajectory.flows)
    assert result.support_dc24_now and result.support_dc48_now

    # Reference without overrides: the same cloudy scenario drains deeper.
    base = SystemConfig(support=SupportParams(configured=True))
    base_result, _ = make_plan(base, now, 40.0, [0.0, 2.0, 2.0])
    assert result.min_soc_percent > base_result.min_soc_percent
    assert n == len(base_result.trajectory.flows)


def test_forced_dc48_feeds_stage1_decision():
    """A forced 48 V injection lifts the SOC path — the automatic 24 V
    stage must judge the supported trajectory, not the raw one."""
    config = SystemConfig(
        support=SupportParams(configured=True, dc48_power_w=400.0, dc48_forced_on=True)
    )
    now = datetime(2026, 7, 3, 22, 0)
    soc_start = config.battery.soc_min_percent + 6.0
    result, _ = make_plan(config, now, soc_start, [0.0, 0.0, 0.0])
    # The strong forced injection keeps the SOC above the 24 V activate level,
    # so no automatic 24 V hours are needed on top.
    floor = config.control.support_dc24_activate_soc
    assert result.min_soc_percent >= floor - 0.01
    assert not any(f.support_dc24 for f in result.trajectory.flows)


# ---------------------------------------------------------------------------
# F-SUBHOUR: sub-hour surplus-load allocation (docs/F-SUBHOUR-ALLOCATION.md)
# ---------------------------------------------------------------------------


def _slot(duration=1.0, hour=12):
    return HourSlot(
        index=0,
        start=datetime(2026, 7, 3, hour, 0),
        duration=duration,
        hour_of_day=hour,
        pv_wh=0.0,
        ac_wh=0.0,
        dc_wh=0.0,
    )


def _s3_plan():
    """The S3 night scenario (thin, spread surplus) — the case sub-hour helps."""
    cfg = SystemConfig(loads=(FOSSIBOT_1, DEHUMIDIFIER))
    states = (
        SurplusLoadState(load_id="fossibot_1", soc_percent=0.0),
        SurplusLoadState(load_id="dehumidifier"),
    )
    return make_plan(cfg, datetime(2026, 7, 3, 21, 0), 84.0, [0.0, 13.0, 11.0], states)


def test_quantised_hours_whole_slot_first_is_regression_anchor():
    # R6: the FIRST candidate is always _committed_hours, so a full-hour
    # placement is chosen exactly as before F-SUBHOUR.
    cands = _quantised_hours(DEHUMIDIFIER, _slot(1.0))
    assert cands[0] == _committed_hours(DEHUMIDIFIER, _slot(1.0)) == 1.0
    assert cands == [1.0, 0.5]  # then the 30-min min_runtime fallback


def test_quantised_hours_never_below_min_runtime():
    # R2: no candidate shorter than min_runtime_min.
    q = DEHUMIDIFIER.min_runtime_min / 60.0
    assert all(d >= q - 1e-9 for d in _quantised_hours(DEHUMIDIFIER, _slot(1.0)))


def test_quantised_hours_energy_limited_matches_continuous():
    # F-RESIDUAL-TOPUP R1: an energy-limited load now gets the SAME candidate
    # list as a continuous load of the same min_runtime — whole first, then k*q
    # fallbacks, none below one quantum (R2 unchanged).
    q = FOSSIBOT_1.min_runtime_min / 60.0  # 0.5 h
    cands = _quantised_hours(FOSSIBOT_1, _slot(1.0))
    assert cands[0] == _committed_hours(FOSSIBOT_1, _slot(1.0)) == 1.0
    assert cands == [1.0, 0.5]
    assert all(d >= q - 1e-9 for d in cands)
    # A same-min_runtime load that is NOT energy-limited yields the identical list.
    twin = replace(FOSSIBOT_1, energy_limited=False, capacity_wh=0.0)
    assert _quantised_hours(twin, _slot(1.0)) == cands
    # A partial slot shorter than one quantum still floors at the dwell quantum.
    assert _quantised_hours(FOSSIBOT_1, _slot(0.5)) == [0.5]


def test_quantised_hours_partial_first_slot_unchanged_head():
    # A partial slot 0 still offers the whole remaining slot first (regression).
    cands = _quantised_hours(DEHUMIDIFIER, _slot(0.75))
    assert cands[0] == _committed_hours(DEHUMIDIFIER, _slot(0.75)) == 0.75
    assert cands == [0.75, 0.5]


def test_subhour_captures_thin_surplus_with_min_runtime_chunks():
    # R1/R3: the dehumidifier books at least one 30-min chunk to soak a thin
    # surplus a whole hour could not, and NEVER a run below min_runtime (R2).
    result, _ = _s3_plan()
    d1 = next(lp for lp in result.load_plans if lp.load_id == "dehumidifier")
    q = DEHUMIDIFIER.min_runtime_min / 60.0
    assert any(abs(h - q) < 1e-9 for h in d1.run_hours), "expected a sub-hour chunk"
    assert all(h == 0.0 or h >= q - 1e-9 for h in d1.run_hours)


def test_run_hours_and_schedule_stay_consistent():
    # R5: schedule[i] == (run_hours[i] > 0), same length.
    result, _ = _s3_plan()
    for lp in result.load_plans:
        assert len(lp.run_hours) == len(lp.schedule)
        assert all(
            bool(s) == (h > 0) for s, h in zip(lp.schedule, lp.run_hours, strict=True)
        )


# --- F-SEAMLESS-PLAN (F9): raster-edge continuation of a running load --------
# A running surplus load lost its slot-0 recommendation whenever the partial
# first slot dipped below one min_runtime quantum: `_committed_hours` floors the
# commit at the dwell, the forced quantum then spilled past the slot boundary,
# and the overlap guard dropped slot 0 because the spill collided with the
# already-booked next slot. Live symptom: the dehumidifier recommendation was
# retracted at ~:31 every morning hour (slot 0 shorter than 30 min) though the
# surplus was unchanged, then re-booked one replan later. The fix trims such a
# spill to slot 0's own duration — the min_runtime dwell is met by the booked
# continuation — so the recommendation survives the raster edge.


def _f9_pv_hourly(day):
    """A strong sunny profile that fills the battery mid-morning and clips."""
    shape = {
        5: 200,
        6: 700,
        7: 1400,
        8: 2100,
        9: 2700,
        10: 3100,
        11: 3400,
        12: 3500,
        13: 3400,
        14: 3000,
        15: 2400,
        16: 1700,
        17: 1000,
        18: 400,
        19: 80,
    }
    return {
        datetime(day.year, day.month, day.day, h): float(w) for h, w in shape.items()
    }


def _f9_plan(minute, soc=70.0):
    cfg = SystemConfig(loads=(DEHUMIDIFIER,))
    now = datetime(2026, 7, 24, 7, minute)
    pv = {}
    for off in range(3):
        pv.update(_f9_pv_hourly(now.date() + timedelta(days=off)))
    states = (
        SurplusLoadState(
            load_id="dehumidifier", available=True, measured_power_w=400.0
        ),
    )
    inputs = build_slots(
        cfg, now, soc, [28.0, 28.0, 28.0], load_states=states, pv_hourly=pv
    )
    return plan(cfg, inputs).load_plans[0]


def test_f9_running_load_keeps_slot0_across_raster_edge():
    # At :30 the partial slot 0 is exactly one 30-min quantum (fits inside the
    # slot); at :31 it is shorter, so the pre-drain block alone would fall
    # below the min-runtime dwell. The own pass-1 booking in the peak slot
    # continues the run seamlessly and counts toward the dwell
    # (F-PREDRAIN-BLOCK R6), so the recommendation holds at both minutes.
    before = _f9_plan(30)
    after = _f9_plan(31)
    assert before.active_now, "slot 0 booked at :30 (quantum fits the slot)"
    assert after.active_now, "slot 0 must stay booked at :31 (seamless raster edge)"
    # The continuation the trim relies on: slot 1 also runs (contiguous block).
    assert after.schedule[0] and after.schedule[1]
    # And it is booked for what it is: the pre-drain block, not a gate top-up.
    assert any("pass 3" in r and "pre-drain block" in r for r in after.reasons)


def test_f9_no_edge_flip_across_the_whole_partial_hour():
    # The recommendation must not toggle off at ANY minute of the partial hour
    # while the surplus is unchanged (the live ~:31 retraction).
    assert all(_f9_plan(m).active_now for m in range(20, 60))


def test_f9_no_phantom_booking_when_surplus_absent():
    # Guard: the trim only rescues a GENUINE continuation (next slot already
    # this load's). With no surplus (weak PV) nothing is booked, so slot 0 has
    # no booked successor and the recommendation correctly stays off — the fix
    # never fabricates a booking.
    cfg = SystemConfig(loads=(DEHUMIDIFIER,))
    now = datetime(2026, 7, 24, 7, 31)
    pv = {}
    for off in range(3):
        d = now.date() + timedelta(days=off)
        pv.update(
            {
                datetime(d.year, d.month, d.day, h): float(w)
                for h, w in {7: 300, 8: 300, 9: 200, 10: 100}.items()
            }
        )
    states = (
        SurplusLoadState(
            load_id="dehumidifier", available=True, measured_power_w=400.0
        ),
    )
    inputs = build_slots(
        cfg, now, 30.0, [8.0, 8.0, 8.0], load_states=states, pv_hourly=pv
    )
    assert not plan(cfg, inputs).load_plans[0].active_now


def test_f9_seamless_spill_is_same_load_scoped():
    # The primitive only continues a spill into a slot the SAME load already
    # owns; a slot booked by another (e.g. higher-priority) load is a genuine
    # conflict -> None (rejected), so cross-load displacement is untouched.
    from core.optimize import _seamless_spill

    slots = [_slot(0.4833), _slot(1.0), _slot(1.0)]
    extra = [0.0, 0.0, 0.0]
    _, covered = _spread_energy(extra, slots, 0, 400.0, 0.5)  # 0.5h spills into slot 1
    assert covered[1][0] == 1
    # slot 1 is THIS load's booking -> trim to slot 0's own duration, no spill
    trimmed = _seamless_spill(covered, slots, 0, 400.0, extra, [False, True, False])
    assert trimmed is not None
    _, tcov = trimmed
    assert [j for j, _ in tcov] == [0]
    assert abs(tcov[0][1] - slots[0].duration) < 1e-9
    # slot 1 NOT this load's -> genuine double-booking, reject
    assert (
        _seamless_spill(covered, slots, 0, 400.0, extra, [False, False, False]) is None
    )


def test_energy_limited_booking_is_quantised_and_consistent():
    # F-RESIDUAL-TOPUP R1: an energy-limited load may now book a sub-hour run,
    # but every booked run is a whole slot or a k*q multiple (>= one quantum, R2),
    # and schedule[i] == (run_hours[i] > 0) stays exact.
    #
    # DOCUMENTED relaxation (F-GATE-TOPUP R7): a load with `gate_stop_capable`
    # may additionally book ONE final quantum BELOW q (rem / max(power,
    # nominal) — the stall-band top-up), covered by the dedicated gate-topup
    # tests. F1 here is plug-only (flag False), so the strict k*q form still
    # holds for it verbatim.
    result, inputs = _s3_plan()
    f1 = next(lp for lp in result.load_plans if lp.load_id == "fossibot_1")
    assert not FOSSIBOT_1.gate_stop_capable  # strict form applies to this load
    q = FOSSIBOT_1.min_runtime_min / 60.0
    for i, h in enumerate(f1.run_hours):
        assert bool(f1.schedule[i]) == (h > 0)
        if h > 0:
            whole = abs(h - inputs.slots[i].duration) < 1e-9
            multiple = h >= q - 1e-9 and abs(round(h / q) * q - h) < 1e-9
            assert whole or multiple, f"slot {i}: run {h} is neither whole nor k*q"


def test_active_run_hours_sums_contiguous_block_from_slot0():
    # R7: the executor's frozen run length is the contiguous run from slot 0.
    from core.model import LoadPlan

    lp = LoadPlan(
        load_id="x",
        schedule=(True, True, False, True),
        planned_energy_wh=0.0,
        run_hours=(0.5, 1.0, 0.0, 1.0),
    )
    assert lp.active_run_hours() == 1.5  # 0.5 + 1.0, stops at the gap
    off = LoadPlan(
        load_id="x", schedule=(False,), planned_energy_wh=0.0, run_hours=(0.0,)
    )
    assert off.active_run_hours() == 0.0
    # legacy fallback: run_hours empty -> count whole scheduled slots
    legacy = LoadPlan(load_id="x", schedule=(True, True, False), planned_energy_wh=0.0)
    assert legacy.active_run_hours() == 2.0


def test_active_run_hours_stops_at_partial_slot_with_durations():
    """F-SUBHOUR fix: a slot not filled to its own duration ends the real-time
    run block, so the executor's frozen deadline never spans a planned-OFF gap."""
    from core.model import LoadPlan

    # 30-min cap in a full hour, with the NEXT hour separately scheduled: the
    # real-time run is only 0.5 h (gap after), not 0.5+1.0=1.5 h.
    lp = LoadPlan(
        load_id="x",
        schedule=(True, True, False),
        planned_energy_wh=0.0,
        run_hours=(0.5, 1.0, 0.0),
    )
    assert lp.active_run_hours((1.0, 1.0, 1.0)) == 0.5
    assert lp.active_run_hours() == 1.5  # legacy no-durations path unchanged
    # a FULL slot 0 continues into slot 1 (partial cap there ends the block)
    lp2 = LoadPlan(
        load_id="x",
        schedule=(True, True, False),
        planned_energy_wh=0.0,
        run_hours=(1.0, 0.5, 0.0),
    )
    assert lp2.active_run_hours((1.0, 1.0, 1.0)) == 1.5
    # a PARTIAL first slot fully filled (0.5 == slot0 0.5 h) continues
    lp3 = LoadPlan(
        load_id="x",
        schedule=(True, True, False),
        planned_energy_wh=0.0,
        run_hours=(0.5, 1.0, 0.0),
    )
    assert lp3.active_run_hours((0.5, 1.0, 1.0)) == 1.5


# ---------------------------------------------------------------------------
# F-RESIDUAL-TOPUP: latest-feasible placement for energy-limited residual
# top-ups (docs/F-RESIDUAL-TOPUP.md). Root cause (live 2026-07-10 18:47): an
# energy-limited load a residual (< nominal x 1 h) short of target could only
# book slot 0 (its partial-hour geometry was the sole sub-hour commitment), so a
# ~150 Wh top-up night-charged from the house battery. R1 gives energy-limited
# loads the SAME sub-hour candidate list as continuous loads, so pass 2's
# latest-first order — not slot-0 geometry — decides the placement.
# ---------------------------------------------------------------------------


def test_r4_live_scene_residual_books_next_day_not_slot0():
    """R4: reproduce the 2026-07-10 18:47 incident. A fossibot 156 Wh short of
    its 90 % target, no PV left today, strong clipping PV tomorrow. v0.8.0 booked
    a 0.5 h / 150 Wh run at slot 0 (a night charge from the house battery); the
    fix books exactly one 0.5 h quantum the next day and nothing in the coming
    night (no covered slot starts before 06:00 next day)."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    now = datetime(2026, 7, 10, 18, 47)  # partial slot 0
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=82.2),)  # 156 Wh
    result, inputs = make_plan(config, now, 72.0, [0.0, 15.0], load_states=states)
    lp = result.load_plans[0]
    assert not lp.schedule[0], "booked at slot 0 (night charge) — the incident"
    six_am_next = datetime(2026, 7, 11, 6, 0)
    covered = [i for i, on in enumerate(lp.schedule) if on]
    for i in covered:
        assert inputs.slots[i].start >= six_am_next, (
            f"covered slot {i} starts {inputs.slots[i].start} (< 06:00 next day)"
        )
    assert len(covered) == 1
    assert abs(lp.run_hours[covered[0]] - 0.5) < 1e-9
    assert abs(lp.planned_energy_wh - 150.0) < 1.0
    # F-RESCUE-EXPORT R6: pass-1 energy-limited placement is earliest-first —
    # the top-up lands on the first (and here only) exporting day, 07-11, at
    # that day's FIRST exporting slot (rescue export as soon as it occurs).
    booked_day = inputs.slots[covered[0]].start.date()
    assert booked_day == datetime(2026, 7, 11, 0, 0).date()
    first_export_of_day = min(
        i
        for i, f in enumerate(result.trajectory.flows)
        if f.grid_export_wh > 1e-9 and inputs.slots[i].start.date() == booked_day
    )
    assert covered[0] == first_export_of_day


def test_pass2_residual_books_latest_of_two_feasible_slots():
    """Latest-first tiebreak (R3): a short power-limited peak (11-13) saturates
    pass 1 in-window; the energy-limited overflow spills to pass 2 as pre-window
    preemptive runs hugging the window start. An overflow of exactly one 0.5 h
    quantum books the LATEST feasible pre-window slot (hour 10), even though an
    earlier slot (hour 9) is also clip-refilled feasible for a larger remainder."""
    short_peak = PVParams(
        peak_power_w=3200.0,
        morning_start_hour=11,
        morning_end_hour=13,
        afternoon_end_hour=14,
        morning_ratio=0.7,
    )
    config = SystemConfig(pv=short_peak, loads=(FOSSIBOT_1,))
    now = datetime(2026, 7, 4, 20, 0)

    def pass2(soc):
        states = (SurplusLoadState(load_id="fossibot_1", soc_percent=soc),)
        # Dawn shoulder light: hours 9/10 must be daylight for the
        # energy-limited overflow to be placeable at all (F-GATE-PARITY).
        inputs = build_slots(
            config,
            now,
            90.0,
            [0.0, 8.0],
            load_states=states,
            pv_hourly=_twilight(datetime(2026, 7, 5, 0, 0), 8, 9, 10),
        )
        result = plan(config, inputs)
        lp = result.load_plans[0]
        hours = sorted(
            inputs.slots[s].hour_of_day for (s, _c, p, _wh) in lp.allocations if p == 2
        )
        return lp, inputs, hours

    # rem 1050 = 900 in-window (hours 11,12,13) + exactly one 0.5 h overflow.
    lp, inputs, hours = pass2(47.5)
    assert hours == [10], f"expected a single pass-2 run at hour 10, got {hours}"
    slot10 = next(
        i
        for i, on in enumerate(lp.schedule)
        if on and inputs.slots[i].hour_of_day == 10
    )
    assert abs(lp.run_hours[slot10] - 0.5) < 1e-9  # sub-hour quantum (R1)
    # A larger remainder proves hour 9 is ALSO a feasible pass-2 slot, so the
    # residual chose hour 10 over hour 9 by latest-first, not for lack of one.
    _lp2, _in2, bigger = pass2(20.0)
    assert 9 in bigger and 10 in bigger


def test_pass1_residual_capture_in_direct_surplus_hour():
    """Pass-1 residual capture (R6): midday, battery full, strong surplus. A
    fossibot 156 Wh short of target books a single 0.5 h pass-1 quantum in a
    direct-surplus hour — a capture class the whole-hour saturation gate used
    to reject entirely. Since F-RESCUE-EXPORT the quantum sits at the FIRST
    exporting slot of the day (earliest-export-first): rescue the surplus as
    soon as it is being lost, not as late as possible."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    now = datetime(2026, 7, 4, 11, 0)
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=82.2),)  # 156 Wh
    result, inputs = make_plan(
        config, now, 93.0, [12.0, 12.0, 12.0], load_states=states
    )
    lp = result.load_plans[0]
    assert result.grid_export_kwh > 0.128  # a real direct surplus to capture
    assert len(lp.allocations) == 1 and lp.allocations[0][2] == 1  # one pass-1 run
    booked = lp.allocations[0][0]
    assert abs(lp.run_hours[booked] - 0.5) < 1e-9
    assert abs(lp.planned_energy_wh - 150.0) < 1.0
    # Earliest-first (F-RESCUE-EXPORT): the booking stays on TODAY and takes
    # that day's FIRST exporting slot — no earlier surplus hour is left unused.
    assert inputs.slots[booked].start.date() == now.date()
    first_export_of_day = min(
        i
        for i, f in enumerate(result.trajectory.flows)
        if f.grid_export_wh > 1e-9 and inputs.slots[i].start.date() == now.date()
    )
    assert booked == first_export_of_day, (
        f"residual booked at slot {booked}, but its day already exports from "
        f"slot {first_export_of_day}"
    )


def test_pass1_rescues_present_export_before_a_later_feasible_slot():
    """F-RESCUE-EXPORT live scene (2026-07-11): the house battery is full and
    exporting NOW while a Fossibot with room sits idle. The energy-limited
    load must book the CURRENT/earliest export slot (run now), not a later
    export slot — deferring past present, certain export to bet on a later
    forecast one is the regression this feature fixes."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    now = datetime(2026, 7, 4, 12, 0)  # midday, export happening in slot 0
    # House at 99 % (exporting now); Fossibot 73.9 % of a 90 % target -> ~322 Wh.
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=73.9),)
    result, inputs = make_plan(
        config, now, 99.0, [12.0, 12.0, 12.0], load_states=states
    )
    lp = result.load_plans[0]

    exporting = [
        i for i, f in enumerate(result.trajectory.flows) if f.grid_export_wh > 1e-9
    ]
    assert exporting and exporting[0] == 0, "slot 0 must be exporting in this scene"
    assert len(exporting) > 1, "a later feasible export slot must also exist"
    # The load runs NOW: slot 0 is booked, not a later export slot.
    assert lp.active_now and lp.schedule[0]
    assert lp.allocations[0][0] == 0  # first booking is at the current export slot
    booked = [i for i, on in enumerate(lp.schedule) if on]
    assert min(booked) == exporting[0]  # earliest export slot, not a later one
    assert result.grid_import_kwh < 0.1  # rescue never causes import


# ---------------------------------------------------------------------------
# F-GATE-TOPUP: final partial quantum for gate-equipped energy-limited loads
# (docs/F-GATE-TOPUP.md). The stall band: without it, a load can never be
# re-booked once rem < max(planning_power, nominal) * min_runtime/60 and parks
# below its target forever (live: F2400-B unbookable above 75 % SOC with a
# learned ~600 W, parked at ~85-89 % instead of 90 %). The G1 dwell-exempt
# target stop delivers exactly `rem` for gate-equipped loads, so the old
# dwell-overshoot rejection (F-RESIDUAL-TOPUP §8 D2) no longer applies there.
# ---------------------------------------------------------------------------

GATED_FB = SurplusLoad(
    load_id="fossibot_g",
    name="Fossibot (gated)",
    nominal_power_w=300.0,
    energy_limited=True,
    capacity_wh=2000.0,
    target_soc_percent=90.0,
    gate_stop_capable=True,
)


def test_gate_topup_books_final_quantum_in_stall_band():
    """R7 live scene: 2000 Wh / target 90 % / learned 600 W / min_runtime 30 at
    SOC 84.9 % -> rem 102 Wh sits below one quantum's commitment (300 Wh), so
    every k*q candidate fails the saturation gate. The gate-equipped load books
    the ONE final quantum 102/600 h (~0.17 h, ~102 Wh) with the explain-plan
    marker; the plug-only twin books nothing (old behaviour, D2 intact)."""
    now = datetime(2026, 7, 4, 11, 0)

    def run(load):
        cfg = SystemConfig(loads=(load,))
        states = (
            SurplusLoadState(
                load_id=load.load_id, soc_percent=84.9, learned_power_w=600.0
            ),
        )
        return make_plan(cfg, now, 93.0, [12.0, 12.0, 12.0], load_states=states)

    result, _inputs = run(GATED_FB)
    lp = result.load_plans[0]
    assert len(lp.allocations) == 1
    assert abs(lp.planned_energy_wh - 102.0) < 1.0
    booked = lp.allocations[0][0]
    assert abs(lp.run_hours[booked] - 102.0 / 600.0) < 1e-3  # ~0.17 h
    assert lp.reasons[0].endswith(", final top-up to target")

    plug_only = replace(GATED_FB, load_id="fossibot_g", gate_stop_capable=False)
    result_plug, _ = run(plug_only)
    assert result_plug.load_plans[0].planned_energy_wh == 0.0  # stall band stays


def test_gate_topup_de_minimis_floor_books_nothing():
    """R3: no final candidate below GATE_TOPUP_MIN_WH committed energy — a
    30 Wh residual (SOC 88.5 %) books nothing, sparing relay/gate churn."""
    config = SystemConfig(loads=(GATED_FB,))
    now = datetime(2026, 7, 4, 11, 0)
    states = (
        SurplusLoadState(load_id="fossibot_g", soc_percent=88.5, learned_power_w=600.0),
    )
    result, _ = make_plan(config, now, 93.0, [12.0, 12.0, 12.0], load_states=states)
    assert result.load_plans[0].planned_energy_wh == 0.0
    assert not any(result.load_plans[0].schedule)


def test_gate_topup_candidate_list_semantics():
    """R2: the final candidate is appended LAST and ONLY when every k*q
    candidate would fail the saturation gate; at rem >= one quantum's
    commitment the list is unchanged (largest-first anchor preserved), and
    plug-only / legacy 2-arg calls never see it."""
    from core.optimize import GATE_TOPUP_MIN_WH

    slot = _slot(1.0)
    q = GATED_FB.min_runtime_min / 60.0
    # Stall band: standard list + one final candidate < q, appended last.
    cands = _quantised_hours(GATED_FB, slot, 102.0, 600.0)
    assert cands[:2] == [1.0, 0.5]
    assert len(cands) == 3 and 0.0 < cands[2] < q
    assert abs(cands[2] - 102.0 / 600.0) < 1e-6
    # The by-construction commitment passes the strict saturation comparison.
    assert max(600.0, GATED_FB.nominal_power_w) * cands[2] <= 102.0
    # rem covers a full quantum: unchanged standard list, no final candidate.
    assert _quantised_hours(GATED_FB, slot, 600.0, 600.0) == [1.0, 0.5]
    # De-minimis floor: below GATE_TOPUP_MIN_WH nothing is appended.
    assert _quantised_hours(GATED_FB, slot, GATE_TOPUP_MIN_WH - 1.0, 600.0) == [
        1.0,
        0.5,
    ]
    # Plug-only and legacy call shapes stay bit-identical.
    plug_only = replace(GATED_FB, gate_stop_capable=False)
    assert _quantised_hours(plug_only, slot, 102.0, 600.0) == [1.0, 0.5]
    assert _quantised_hours(GATED_FB, slot) == [1.0, 0.5]


# ---------------------------------------------------------------------------
# F-PLANNER-HONESTY: learned planning power (F1), load-outer priority pass 1
# (F2 + F-RESCUE-EXPORT earliest-first), explain-plan (F3).
# docs/F-PLANNER-HONESTY.md, docs/F-RESCUE-EXPORT.md.
# ---------------------------------------------------------------------------


def test_planning_power_precedence_measured_learned_nominal():
    """R1/R6: measured (live) > learned (past runs) > nominal; a zero or absent
    learned value never masks the nominal fallback (bit-identical to v0.8.2
    when no learned value exists)."""
    both = SurplusLoadState(
        load_id="fossibot_1", measured_power_w=505.0, learned_power_w=480.0
    )
    assert both.planning_power_w(FOSSIBOT_1) == 505.0
    learned_only = SurplusLoadState(load_id="fossibot_1", learned_power_w=480.0)
    assert learned_only.planning_power_w(FOSSIBOT_1) == 480.0
    neither = SurplusLoadState(load_id="fossibot_1")
    assert neither.planning_power_w(FOSSIBOT_1) == FOSSIBOT_1.nominal_power_w
    zeroed = SurplusLoadState(load_id="fossibot_1", learned_power_w=0.0)
    assert zeroed.planning_power_w(FOSSIBOT_1) == FOSSIBOT_1.nominal_power_w
    # F5: a latched power warning (saturated_power_w) overrides everything —
    # the load plans at its measured ~0 W draw (full tank), clamped >= 0.
    saturated = SurplusLoadState(
        load_id="fossibot_1",
        saturated_power_w=2.0,
        measured_power_w=505.0,
        learned_power_w=480.0,
    )
    assert saturated.planning_power_w(FOSSIBOT_1) == 2.0
    saturated_zero = SurplusLoadState(load_id="fossibot_1", saturated_power_w=0.0)
    assert saturated_zero.planning_power_w(FOSSIBOT_1) == 0.0


def test_tank_prediction_has_no_planner_field():
    """V6 (F-TANK, operator rule 2026-07-24): the tank prediction must NEVER
    preemptively curtail planning — a dehumidifier stops ITSELF when the tank
    really is full, and that is detected via the power collapse (F5
    saturated_power_w). The core state therefore carries no tank budget field
    at all; the prediction lives coordinator-side (notification + diagnostics
    only)."""
    assert not hasattr(SurplusLoadState(load_id="dehumidifier"), "tank_remaining_min")


def test_pass1_energy_limited_residual_books_earlier_of_two_hours():
    """R11a, inverted by F-RESCUE-EXPORT: two feasible direct-surplus hours, a
    residual that fits only one 0.5 h quantum — the EARLIER hour hosts it
    (earliest-export-first). The later hour provably stays feasible: it still
    exports after the booking."""
    config = SystemConfig(loads=(FOSSIBOT_B,))
    now = datetime(2026, 7, 4, 16, 1)
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=82.2),)  # 156 Wh
    result, inputs = make_plan(config, now, 94.0, [12.0], load_states=states)
    lp = result.load_plans[0]
    assert lp.allocations == ((0, 1, 1, 150.0),)  # one pass-1 quantum at slot 0
    assert lp.active_now and lp.schedule[0]  # runs NOW, not the later hour
    # The later hour (slot 1) stayed feasible — it still exports.
    assert result.trajectory.flows[1].grid_export_wh > 1e-9


def test_pass1_load_outer_config_order_priority_scarce_surplus():
    """R7/R11b: with scarce surplus the load-outer pass 1 gives strict
    config-order priority — inverting the order shifts the surplus energy to
    the (new) first load; total import stays identical."""
    now = datetime(2026, 7, 4, 10, 0)
    states = (
        SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),
        SurplusLoadState(load_id="dehumidifier"),
    )

    def planned(loads):
        cfg = SystemConfig(loads=loads)
        result, _ = make_plan(cfg, now, 93.0, [6.0], load_states=states)
        by_id = {p.load_id: p.planned_energy_wh for p in result.load_plans}
        return by_id, result.grid_import_kwh

    fb_first, import_fb = planned((FOSSIBOT_B, DEHUMIDIFIER))
    deh_first, import_deh = planned((DEHUMIDIFIER, FOSSIBOT_B))
    assert fb_first["fossibot_b"] > deh_first["fossibot_b"]
    assert deh_first["dehumidifier"] > fb_first["dehumidifier"]
    # Total booked ENERGY is order-invariant up to ONE dehumidifier quantum:
    # priority reassigns WHICH load gets the scarce surplus, not how much is
    # absorbed — except at the marginal contested slot, whose atomic quanta do
    # not trade 1:1 between the classes. Here the 10:00 slot's export covers a
    # 400 Wh dehumidifier hour OR a 300 W fossibot hour; with the fossibot
    # first, the ~150 Wh it leaves behind falls ~19 Wh short of the cover the
    # dehumidifier's 0.5 h quantum (200 Wh) needs, so exactly one quantum
    # (400 W x 0.5 h) less is booked in that order. (Pass 3 never engages
    # here: today's first export slot IS slot 0, so there is no block.)
    total_fb = sum(fb_first.values())
    total_deh = sum(deh_first.values())
    quantum = DEHUMIDIFIER.nominal_power_w * DEHUMIDIFIER.min_runtime_min / 60.0
    assert abs(total_fb - total_deh) <= quantum + 1e-6
    # Import may differ only within the artifact slack (F-STRICT-SURPLUS R1:
    # an order-dependent import difference rides the absolute cap, never a
    # rescued-export budget).
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert abs(import_fb - import_deh) <= IMPORT_ARTIFACT_SLACK_WH / 1000.0 + 1e-9


def test_reasons_align_one_to_one_with_allocations():
    """R12/R13/R15: every allocation entry has exactly one reason, same order;
    the pass number in the string matches the allocation's pass, pass-1
    reasons name the booking kind (direct surplus or the F-PEAK-FILL at-max
    top-up), pass-2 reasons carry the structural lateness claim, and pass-3
    reasons the block's latest-start claim."""
    result, _ = _s3_plan()
    for lp in result.load_plans:
        assert len(lp.reasons) == len(lp.allocations)
        for (_start, _count, pass_no, _wh), why in zip(
            lp.allocations, lp.reasons, strict=True
        ):
            assert why.startswith(f"pass {pass_no} @ ")
            if pass_no == 1:
                assert "direct surplus" in why or "at-max top-up" in why
            elif pass_no == 2:
                assert "latest feasible slot" in why
            else:
                assert "latest feasible start" in why
    # The scenario books at least one allocation, so the check is not vacuous.
    assert any(lp.allocations for lp in result.load_plans)


def test_legacy_load_plan_defaults_to_empty_reasons():
    """R12: LoadPlan constructors without reasons stay valid (goldens and
    legacy callers must not gain the field implicitly)."""
    from core.model import LoadPlan

    lp = LoadPlan(load_id="x", schedule=(True,), planned_energy_wh=1.0)
    assert lp.reasons == ()


# ---------------------------------------------------------------------------
# F-PREDRAIN: import-trade rule + two-buffer pre-drain gates (docs/F-PREDRAIN.md
# §3, WP2). Root cause (live 2026-07-10): the 10 W charger standby of an
# extended morning charge modeled ~10 Wh of new import that vetoed 250-520 Wh of
# rescued night export per candidate. Test contract T1-T5, T13 — all rewritten
# same-day for F-PREDRAIN-BLOCK (operator 2026-08-01): the continuous load's
# pre-drain is pass 3's single block ending at TODAY's first export slot, never
# a cross-day night carve-out. T12's c2 machinery is energy-limited-only now and
# stays pinned by test_gate_parity_c2_beta_books_energy_limited_in_window.
# ---------------------------------------------------------------------------

FB_STATE = SurplusLoadState(load_id="fossibot_b", soc_percent=46.4)  # 872 Wh to go
DEHUMID_STATE = SurplusLoadState(load_id="dehumidifier")


def _predrain_config(
    ratio=0.1, alpha=0.5, beta=1.2, cutoff=200.0, end_hour=None, loads=(DEHUMIDIFIER,)
):
    control = replace(
        ControlParams(),
        import_trade_ratio=ratio,
        predrain_pv_confidence=alpha,
        upper_pv_reserve=beta,
        strong_pv_cutoff_w=cutoff,
        pv_window_end_hour=end_hour,
    )
    return SystemConfig(control=control, loads=loads)


def _dehumid_hours(result, inputs):
    lp = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    return [inputs.slots[i].hour_of_day for i, on in enumerate(lp.schedule) if on]


def _pass3_block(result, load_id="dehumidifier"):
    """The load's pass-3 pre-drain-block allocations (0 or 1 by design,
    F-PREDRAIN-BLOCK)."""
    lp = next(p for p in result.load_plans if p.load_id == load_id)
    return [a for a in lp.allocations if a[2] == 3]


def test_t1_night_predrain_books_on_artifact_slack_ratio_ignored():
    """T1 (F-STRICT-SURPLUS R1): the pre-drain block books without touching
    the ABSOLUTE artifact slack — since the standby accounting fix the
    block's charging hours mint no import at all (the charger standby is
    surplus-covered, so the ~10 Wh artifact class is gone) and the used
    import is exactly zero. The retired `import_trade_ratio` field changes
    nothing (0.0 and 0.1 produce the identical plan). (Same-day rewrite,
    F-PREDRAIN-BLOCK: 04:00 on the clip day, the block's pre-dawn hours
    drain toward today's 09:00 peak.)"""
    now = datetime(2026, 7, 4, 4, 0)

    def run(ratio):
        cfg = _predrain_config(ratio=ratio, alpha=1.0, beta=1.0)
        return make_plan(cfg, now, 50.0, [13.0, 11.0])

    r0, inputs = run(0.0)
    r1, _ = run(0.1)
    p3 = _pass3_block(r0)
    # Today's block [(2,3,3,1200)] is capped by the always-on nominal dynamic
    # floor (operator 2026-08-02); tomorrow's clip earns its OWN block
    # [(24,4,3,1600)] inside its own day (per-day R1, v0.22.0).
    assert p3 == [(2, 3, 3, 1200.0), (24, 4, 3, 1600.0)], (
        "the standby artifact must not veto the pre-drain block (L1)"
    )
    night = [h for h in _dehumid_hours(r0, inputs) if not daylight_h(h)]
    assert night, "the block's pre-dawn hours are part of the booking"
    assert r0.load_plans == r1.load_plans, "the retired ratio must not change the plan"
    # Exactly zero: no standby artifact rides the slack anymore (was
    # 0.0 < used <= IMPORT_ARTIFACT_SLACK_WH while the artifact existed).
    assert r0.import_trade_used_wh == 0.0
    assert r0.import_trade_used_wh == r1.import_trade_used_wh


def test_t2_cumulative_import_trade_invariant():
    """T2: over the whole allocation, final import stays within the Z2''
    invariant the code actually applies — at most IMPORT_ARTIFACT_SLACK_WH of
    added import over the no-loads base — and the pre-drain block booked real
    energy. Since the standby accounting fix the invariant is met with zero
    added import: the artifact trade it used to tolerate no longer exists."""
    now = datetime(2026, 7, 4, 4, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    inputs = build_slots(cfg, now, 50.0, [13.0, 11.0])
    result = plan(cfg, inputs)
    _, base = search_threshold(cfg, inputs)
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    traj = result.trajectory
    assert traj.total_import_wh - base.total_import_wh <= (
        IMPORT_ARTIFACT_SLACK_WH + 1e-6
    )
    # Standby-accounting fix: no artifact trade at all — the block books with
    # zero added import over the base (was: a few ~10 Wh standby artifacts).
    assert traj.total_import_wh == base.total_import_wh
    # Today's floor-capped block plus tomorrow's own (per-day R1, v0.22.0).
    assert _pass3_block(result) == [(2, 3, 3, 1200.0), (24, 4, 3, 1600.0)]


def test_t3_energy_limited_never_night_charged_with_ratio():
    """T3: even with a generous ratio, full c2/Z4 machinery access and maximum
    temptation, an energy-limited powerstation never night-charges. Since
    F-GATE-PARITY the mechanism is the DAYLIGHT rule (zero-PV pass-2 slots are
    barred for the class), no longer the strict import gate — the gates
    themselves are shared with continuous loads and trading import for
    in-daylight bookings is allowed."""
    now = datetime(2026, 7, 3, 21, 0)
    cfg = _predrain_config(ratio=0.5, alpha=0.5, beta=1.2, loads=(FOSSIBOT_B,))
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0], load_states=states)
    for i, on in enumerate(result.load_plans[0].schedule):
        if on:
            assert daylight(inputs.slots[i]), (
                f"fossibot night-charged at {inputs.slots[i].hour_of_day}:00"
            )
            # The binding predicate is PV, not the clock (F-GATE-PARITY).
            assert inputs.slots[i].pv_wh > 0.0, (
                f"fossibot booked a zero-PV slot at {inputs.slots[i].hour_of_day}:00"
            )


def test_t4_alpha_stress_gate_protects_inverter_reserve():
    """T4 RE-SCOPED (operator 2026-08-02): the pass-3 block's floor is the
    NOMINAL dynamic buffer — the alpha/band Z4 stress no longer applies to
    blocks (a forecast miss is caught by the intra-day replan loop and the
    G4 real-time floor guard instead). The block depth is therefore
    ALPHA-INDEPENDENT (identical at 0.5 and 1.0) and still floor-capped: the
    one-hour-earlier extension the walk refuses breaks the nominal ramped
    floor while staying off the 20 % cutoff — the dynamic buffer, not R2,
    is the binding floor here. The windowed Z4 stress survives unchanged for
    the slot-wise pass-2 bets of ENERGY-LIMITED loads — pinned by
    test_gate_parity_z4_stress_binds_energy_limited_bets. (Same-day
    geometry, F-PREDRAIN-BLOCK: 04:00 on the clip day, the block drains
    toward today's 09:00 peak.)"""
    now = datetime(2026, 7, 4, 4, 0)

    def run(alpha):
        cfg = _predrain_config(
            ratio=0.1, alpha=alpha, beta=1.0, loads=(FOSSIBOT_B, DEHUMIDIFIER)
        )
        return make_plan(
            cfg, now, 50.0, [13.0, 11.0], load_states=(FB_STATE, DEHUMID_STATE)
        )

    trusting, inputs = run(1.0)
    stressed, _ = run(0.5)
    # Alpha-independence: identical blocks, identical drain depth (today's
    # floor-capped block plus tomorrow's own, per-day R1 — both
    # alpha-independent).
    assert _pass3_block(trusting) == [(2, 3, 3, 1200.0), (24, 4, 3, 1600.0)]
    assert _pass3_block(stressed) == _pass3_block(trusting)
    assert stressed.min_soc_percent == trusting.min_soc_percent

    # Floor-capped by the nominal dynamic buffer: the accepted trough holds
    # the ramped floor (25 = inverter 20 + full buffer 5 at the pre-dawn
    # slots), and the refused one-hour-earlier extension dips below it while
    # staying off the 20 % cutoff (R2 would pass it).
    floor = 20.0 + 5.0  # inverter_min_soc + soc_buffer (NOT soc_min + buffer)
    assert trusting.min_soc_percent >= floor - 0.5
    cfg = _predrain_config(
        ratio=0.1, alpha=1.0, beta=1.0, loads=(FOSSIBOT_B, DEHUMIDIFIER)
    )
    threshold, _base = search_threshold(cfg, inputs)
    s, count, _, _ = _pass3_block(trusting)[0]
    peak = s + count
    lp = next(p for p in trusting.load_plans if p.load_id == "dehumidifier")
    extra_wo = [f.extra_ac_wh for f in trusting.trajectory.flows]
    for j in range(s, peak):
        extra_wo[j] -= 400.0 * lp.run_hours[j]
    trial = list(extra_wo)
    for j in range(s - 1, peak):
        trial[j] += 400.0 * inputs.slots[j].duration
    ext_traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    recovery = _refill_index(ext_traj, s - 1, cfg.battery.soc_max_percent - 0.1)
    ext_min = _windowed_min_soc(ext_traj, s - 1, max(recovery, peak - 1))
    assert 20.0 < ext_min < floor

    # The §3.5 diagnostic still reports the windowed reserve for the booking
    # — the stress machinery is alive (pass 2, the diagnostic); it just no
    # longer gates blocks.
    assert stressed.stressed_min_soc_percent is not None
    assert trusting.stressed_min_soc_percent is None  # alpha=1.0 -> no stress


def test_t5_predrain_hours_hug_the_window_latest_first():
    """T5: the block sits as late as the target allows (L4/R4): ONE
    contiguous block ending right before today's first export slot, covering
    exactly ceil(clip/power) hours — never extended earlier than the clip
    needs. (Same-day rewrite, F-PREDRAIN-BLOCK; a 250 W load on a modest clip
    day so the latest start is target-bound, not floor-bound.)"""
    import re

    deh250 = replace(DEHUMIDIFIER, nominal_power_w=250.0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0, loads=(deh250,))
    now = datetime(2026, 7, 4, 4, 0)
    # Geometry tuned for the standby-aware charger accounting (2026-08-03):
    # the ~9 Wh/h honest charge loss needs a touch more PV headroom so the
    # refill still provably succeeds and the block stays target-bound.
    result, inputs = make_plan(cfg, now, 67.0, [6.2, 11.0])
    lp = result.load_plans[0]
    p3 = _pass3_block(result)
    # ONE contiguous block [07:00, 10:00) ending the slot right before
    # today's first export slot (the 10:00 peak) — tomorrow's clip earns its
    # own block inside its own day (per-day R1, v0.22.0), which does not
    # change today's target-bound shape.
    assert p3 == [(3, 3, 3, 750.0), (22, 6, 3, 1500.0)]
    start, count, _, wh = p3[0]
    assert [inputs.slots[i].hour_of_day for i in range(start, start + count)] == [
        7,
        8,
        9,
    ]
    assert inputs.slots[start + count].hour_of_day == 10
    reason = next(r for r in lp.reasons if r.startswith("pass 3 @"))
    assert "pre-drain block" in reason and "latest feasible start" in reason
    # Latest start: one 250 W hour less would miss the clip target.
    target = int(re.search(r"against (\d+) Wh clip", reason).group(1))
    assert wh >= target > wh - deh250.nominal_power_w


def test_t13_pv_window_derivation_and_override():
    """T13: the PV window is derived from the (daily/two-window) slot series —
    an east-heavy profile ends early; the site override caps it earlier still; a
    day without strong PV has no window."""
    east = SystemConfig(
        pv=PVParams(
            peak_power_w=3200.0,
            morning_start_hour=6,
            morning_end_hour=11,
            afternoon_end_hour=19,
            morning_ratio=0.9,
        )
    )
    inputs = build_slots(east, datetime(2026, 7, 4, 0, 0), 50.0, [9.0])
    day = datetime(2026, 7, 4).date()
    first, last = pv_windows(inputs, 200.0, None)[day]
    # Strong PV only in the morning (hours 6-10); the weak afternoon is excluded.
    assert inputs.slots[first].hour_of_day == 6
    assert inputs.slots[last].hour_of_day == 10
    # The site override caps the end at the last slot starting before hour 9.
    capped = pv_windows(inputs, 200.0, 9)[day]
    assert inputs.slots[capped[1]].hour_of_day == 8
    # A cloudy day (all slots below the cutoff) has no window.
    cloudy = build_slots(east, datetime(2026, 7, 4, 0, 0), 50.0, [0.5])
    assert pv_windows(cloudy, 200.0, None) == {}


def test_refill_index_settles_at_first_actual_refill():
    """F-STRICT-SURPLUS R3: the bet window for a pre-drain at slot i ends at the
    first slot at/after i whose TRIAL trajectory reaches soc_max — not at the
    same-day PV window end (whose "refilled by then" premise is false on a day
    that never fills; live 2026-07-19 that blind spot let daytime bets escape
    the overnight stress test). No refill ahead -> horizon end."""
    from types import SimpleNamespace

    def traj(socs):
        return SimpleNamespace(flows=[SimpleNamespace(soc_end_percent=s) for s in socs])

    full = 94.9
    socs = [50, 60, 95, 95, 40, 30, 20, 60, 95, 70]
    t = traj(socs)
    assert _refill_index(t, 0, full) == 2  # first refill ahead
    assert _refill_index(t, 2, full) == 2  # already full at i
    assert _refill_index(t, 4, full) == 8  # next refill only next day
    assert _refill_index(t, 9, full) == 9  # no refill ahead -> horizon end
    assert _refill_index(traj([50, 60, 70]), 0, full) == 2  # never full


def test_t4_windowed_gate_scopes_stress_to_recovery_window():
    """§3.3 v2 → §3.5 (operator 2026-08-02): the block's reserve is judged on
    its own recovery window, never whole-horizon. The pass-3 gate itself is
    the NOMINAL dynamic floor now — today's block books regardless of the
    whole-horizon alpha dip on the later, weaker day — but the §3.5
    diagnostic must still report exactly the WINDOWED stressed reserve (not
    the whole-horizon min), so the sensor shows what a forecast miss would
    do to THIS bet's window. (Same-day rewrite, F-PREDRAIN-BLOCK: the bet is
    the pass-3 block and the diagnostic reports exactly its windowed
    reserve.)"""
    now = datetime(2026, 7, 4, 4, 0)
    cfg = _predrain_config(ratio=0.1, alpha=0.5, beta=1.0)
    result, inputs = make_plan(cfg, now, 50.0, [13.0, 11.0, 3.0])
    n = len(inputs.slots)

    # Today's block IS booked (the nominal floor permits it, stress or not) —
    # and tomorrow's clip earns its own block inside its own day (per-day R1);
    # the weak day 3 gets none (no peak there).
    p3 = _pass3_block(result)
    assert p3 == [(2, 3, 3, 1200.0), (24, 4, 3, 1600.0)], "today's block must book"

    # The WHOLE-HORIZON alpha sim of the final series breaks the inverter floor
    # (a later, weaker day dominates it) — the diagnostic must NOT report this.
    threshold = result.threshold_percent
    extra = tuple(f.extra_ac_wh for f in result.trajectory.flows)
    whole_horizon = simulate(cfg, inputs, threshold, extra_ac_wh=extra, pv_scale=0.5)
    floor = 20.0 + 5.0  # inverter_min_soc + buffer
    assert whole_horizon.min_soc_percent < floor, (
        "scenario must have a later whole-horizon stressed dip below the floor"
    )

    # Yet the WINDOWED reserve over the block's own bet window is held at the
    # floor, and the diagnostic reports exactly this windowed reserve.
    i0 = p3[0][0]
    recovery = _refill_index(result.trajectory, i0, cfg.battery.soc_max_percent - 0.1)
    scale_vec = [0.5 if i0 <= j <= recovery else 1.0 for j in range(n)]
    stressed = simulate(cfg, inputs, threshold, extra_ac_wh=extra, pv_scale=scale_vec)
    windowed_min = _windowed_min_soc(stressed, i0, recovery)
    assert windowed_min >= floor - 0.5
    assert result.stressed_min_soc_percent is not None
    assert abs(result.stressed_min_soc_percent - windowed_min) < 1e-6


def test_t4_bet_settles_at_refill_so_unrelated_tail_dip_cannot_veto():
    """F-STRICT-SURPLUS R3 settlement: a pre-drain whose trial refills to the
    ceiling at the very next slot has its bet window END there — the deep,
    unrelated DC-tail trough AFTER the refill is structurally outside the
    window and cannot veto the (fully refilled, export-covered) pre-drain.
    (Pre-R3 this scenario needed the Z4 relief clause to survive a window
    that ran to the same-day PV window end; the relief clause remains in
    force for base dips INSIDE [i, refill]. The settlement rule itself still
    scopes both the pass-3 NOMINAL floor window (operator 2026-08-02) and the
    pass-2 stress window.)"""
    deh = SurplusLoad(
        load_id="deh",
        name="E",
        nominal_power_w=400.0,
        battery_tolerance=0.15,
        min_runtime_min=60,
    )
    alpha = 0.5
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=alpha,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    config = SystemConfig(
        control=control,
        loads=(deh,),
        ac_profile=LoadProfile(0.0, 0.0),
        dc_profile=LoadProfile(0.0, 0.0),
    )
    start = datetime(2026, 7, 4, 8, 0)

    def slot(i, hour, pv, ac=0.0, dc=0.0):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=dc,
        )

    slots = (
        slot(0, 8, 300, ac=500),  # little export -> pass 1 skips; pre-drain here
        slot(1, 9, 5000, ac=50),  # spike -> refill to ceiling + big export
        *(slot(2 + k, 10 + k, 300, dc=1100) for k in range(5)),  # heavy DC tail
        slot(7, 15, 40, dc=80),
        slot(8, 16, 20, dc=80),
    )
    inputs = PlanInputs(
        now=start,
        start_soc_percent=70.0,
        slots=slots,
        load_states=(SurplusLoadState(load_id="deh"),),
    )
    n = len(inputs.slots)
    threshold, base = search_threshold(config, inputs)
    load_plans, extra, _ = allocate_loads(config, inputs, threshold, base)
    lp = load_plans[0]

    # The pre-drain at slot 0 (the pass-3 block) IS booked.
    assert any(a[0] == 0 and a[2] == 3 for a in lp.allocations)

    # Settlement: the trial refills to the ceiling at the spike slot, so the
    # bet window is [0, 1] — the heavy DC tail (slots 2+) sits OUTSIDE it.
    trial = simulate(config, inputs, threshold, extra_ac_wh=extra)
    recovery = _refill_index(trial, 0, config.battery.soc_max_percent - 0.1)
    assert recovery == 1
    # Inside the settled window the stressed reserve holds the floor, while
    # the whole-horizon stressed min (dominated by the tail) breaks it — the
    # tail dip is provably outside what the gate judges.
    scale_vec = [alpha if 0 <= j <= recovery else 1.0 for j in range(n)]
    floor = control.inverter_min_soc_percent + control.soc_buffer_percent  # 25
    trial_wmin = _windowed_min_soc(
        simulate(config, inputs, threshold, extra_ac_wh=extra, pv_scale=scale_vec),
        0,
        recovery,
    )
    assert trial_wmin >= floor - 0.5
    whole = simulate(config, inputs, threshold, extra_ac_wh=extra, pv_scale=alpha)
    assert whole.min_soc_percent < floor


def daylight_h(hour):
    return 7 <= hour < 18


# ---------------------------------------------------------------------------
# Adversarial-review fixes (v0.8.0): FIX-1 (windows shadowing), FIX-2 (energy-
# limited starvation), FIX-6 (ratio-0 slack), FIX-7 (Z4 spill past recovery).
# ---------------------------------------------------------------------------


def test_fix1_appliance_windows_survive_predrain_booking():
    """FIX-1: the stressed_min_soc diagnostic must NOT clobber
    PlanResult.appliance_windows. With an opportunistic appliance AND an
    accepted pre-drain (the pass-3 block of a continuous load; alpha < 1 so
    the diagnostic block runs), appliance_windows must stay the advisor dict
    (appliance id -> bool), not the pv_windows date->tuple dict the
    diagnostic builds internally. (Same-day rewrite, F-PREDRAIN-BLOCK.)"""
    now = datetime(2026, 7, 4, 4, 0)
    dishwasher = Appliance(
        appliance_id="dishwasher",
        name="Dishwasher",
        run_energy_wh=600.0,
        run_duration_h=2.0,
        opportunistic_start=True,
    )
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=0.5,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(control=control, loads=(DEHUMIDIFIER,), appliances=(dishwasher,))
    result, _inputs = make_plan(cfg, now, 50.0, [13.0, 11.0])
    # Premise: the scenario books the pass-3 pre-drain block, so the
    # diagnostic block that used to reassign `windows` runs.
    lp = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    assert any(a[2] == 3 for a in lp.allocations), "scenario must book the block"
    assert result.stressed_min_soc_percent is not None  # diagnostic path taken
    # appliance_windows is the advisor dict: appliance id -> bool (NOT clobbered).
    assert set(result.appliance_windows) == {"dishwasher"}
    assert isinstance(result.appliance_windows["dishwasher"], bool)


def test_gate_parity_shared_trade_budget_across_classes():
    """F-GATE-PARITY R1 (rewrites FIX-2): both classes share ONE Z2' trade
    invariant anchored at the no-loads base. The former energy-limited strict
    gate (current-anchored, FIX-2) is superseded — under a single shared
    budget there is no per-class anchor left to inherit, and the fossibot may
    now trade import for daylight bookings exactly like the dehumidifier.

    Scenario (unchanged from FIX-2): modest day-1 then a strong day-2. The
    dehumidifier pre-drains (its ~10 Wh standby artifact is gone since the
    accounting fix — no import is traded at all); the fossibot has a large
    remaining budget. Under parity the fossibot books MORE than the old
    strict gate allowed (parity can only add opportunities here) while the
    cumulative trade invariant (b1) and the attribution bound (b2) keep
    holding — they are the binding contracts."""
    now = datetime(2026, 7, 3, 21, 0)
    fb = SurplusLoad(
        load_id="fb",
        name="B",
        nominal_power_w=300.0,
        battery_tolerance=0.05,
        min_runtime_min=30,
        energy_limited=True,
        capacity_wh=6000.0,
        target_soc_percent=100.0,
    )
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(control=control, loads=(DEHUMIDIFIER, fb))
    states = (
        SurplusLoadState(load_id="dehumidifier"),
        SurplusLoadState(load_id="fb", soc_percent=0.0),  # remaining 6000 Wh
    )
    inputs = build_slots(cfg, now, 60.0, [0.0, 5.0, 18.0], load_states=states)
    threshold, base = search_threshold(cfg, inputs)
    load_plans, extra, traj = allocate_loads(cfg, inputs, threshold, base)
    fb_plan = next(p for p in load_plans if p.load_id == "fb")

    # (a) No import trade fires at all (the standby accounting fix removed
    #     the dehumidifier's ~10 Wh artifact) AND the fossibot books its
    #     parity coverage: 2400 Wh (was 1800 under the pre-parity strict
    #     gate, 2250 before F-PEAK-FILL). Parity only ever ADDS
    #     opportunities, and R2 adds +150 net on top: three at-max top-ups
    #     (13/15/17:00, battery-buffered pass-1 bookings at the max line) —
    #     the 13:00 slot upgraded from a 150 Wh pass-2 bet to a 300 Wh
    #     top-up and a new 15:00 top-up, against the 14:00 pass-2 bet whose
    #     refill the top-ups consumed. (The third top-up moved 16:00 ->
    #     17:00 with the standby-cleaned export trajectory.)
    assert traj.total_import_wh - base.total_import_wh == 0.0  # no trade needed
    assert any(a[2] == 2 for a in fb_plan.allocations)  # a post-trade refill booked
    assert fb_plan.planned_energy_wh >= 1800.0 - 1e-6  # parity only ever adds
    assert abs(fb_plan.planned_energy_wh - 2400.0) < 1e-6
    assert any("at-max top-up" in r for r in fb_plan.reasons)  # the R2 additions
    # Daylight rule: every fossibot slot carries PV (never a night booking).
    assert all(
        inputs.slots[i].pv_wh > 0.0 for i, on in enumerate(fb_plan.schedule) if on
    )

    # (b) Import stays bounded by the trade contract for the WHOLE plan. The
    #     binding guarantees are the two trade-gate invariants below.
    ratio = control.import_trade_ratio
    assert fb_plan.planned_energy_wh < 6000.0  # gates still bound the top-up
    # (b1) Cumulative trade invariant over the whole plan vs the no-loads base.
    assert traj.total_import_wh - base.total_import_wh <= (
        ratio * (base.total_export_wh - traj.total_export_wh) + 1.0 + 1e-6
    )
    # (b2) The fossibot's OWN attribution delta (vs the deh-only counterfactual)
    #      stays within the trade allowance — it cannot transitively import past
    #      the ratio contract either.
    deh_only = list(extra)
    for a in fb_plan.allocations:
        for j in range(a[0], a[0] + a[1]):
            deh_only[j] = max(0.0, deh_only[j] - 300.0 * fb_plan.run_hours[j])
    without_fb = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(deh_only))
    assert traj.total_import_wh - without_fb.total_import_wh <= (
        ratio * (without_fb.total_export_wh - traj.total_export_wh) + 1.0 + 1e-6
    )


# ---------------------------------------------------------------------------
# F-GATE-PARITY (operator decision 2026-07-17): both load classes face the
# identical pass-2 gate set (shared Z2' trade, c1-rt, c2-beta, Z4 stress).
# Single remaining class rule: energy-limited loads never book zero-PV
# (night) slots. Since F-PREDRAIN-BLOCK (operator 2026-08-01) continuous
# loads no longer bet slot-wise at all — their pre-drain is pass 3's
# today-only block — so contested bet depth goes to the pass-2 (energy-
# limited) class structurally; config order discriminates among same-class
# contenders (see the F-PREDRAIN-BLOCK section below).
# ---------------------------------------------------------------------------


def _parity_scene(start_soc_wh: float, loads):
    """Two-slot bet scene: a weakly lit make-room slot (06:00, 10 Wh dawn
    light) ahead of one big clipping slot (07:00). The c1 refill pool is
    huge; bet depth is scarce because the simulator's inverter cutoff at
    T*=20 % (400 Wh of the 2000 Wh battery) turns any deeper drain into
    grid import, which the Z2' trade budget cannot finance — at 800 Wh
    start there is room for exactly ONE ~326 Wh battery drain (one 300 Wh
    AC booking), and a second booking of ANY quantum falls through the
    cutoff and is rejected. Who gets the one booking is purely a question
    of config order."""
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=0.5,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        control=control,
        loads=loads,
        battery=BatteryParams(capacity_wh=2000.0),
        ac_profile=LoadProfile(0.0, 0.0),
        dc_profile=LoadProfile(0.0, 0.0),
    )
    start = datetime(2026, 7, 4, 6, 0)

    def slot(i, hour, pv):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=hour,
            pv_wh=pv,
            ac_wh=0.0,
            dc_wh=0.0,
        )

    slots = (slot(0, 6, 10.0), slot(1, 7, 2500.0), slot(2, 8, 100.0))
    states = tuple(
        SurplusLoadState(load_id=ld.load_id, soc_percent=0.0)
        if ld.energy_limited
        else SurplusLoadState(load_id=ld.load_id)
        for ld in loads
    )
    inputs = PlanInputs(
        now=start,
        start_soc_percent=start_soc_wh / 2000.0 * 100.0,
        slots=slots,
        load_states=states,
    )
    threshold, base = search_threshold(cfg, inputs)
    plans, _extra, _traj = allocate_loads(cfg, inputs, threshold, base)
    return plans


_DEH300 = SurplusLoad(
    load_id="deh300",
    name="Entfeuchter 300",
    nominal_power_w=300.0,
    battery_tolerance=0.05,
)


def _books_slot0_pass2(plans, load_id):
    lp = next(p for p in plans if p.load_id == load_id)
    return any(a[0] == 0 and a[2] == 2 for a in lp.allocations)


def _books_slot0_bet(plans, load_id):
    """Slot-0 bet coverage via EITHER bet pass (2 = energy-limited slot bet,
    3 = the continuous load's pass-3 pre-drain block, F-PREDRAIN-BLOCK)."""
    lp = next(p for p in plans if p.load_id == load_id)
    return any(a[0] == 0 and a[2] in (2, 3) for a in lp.allocations)


def test_gate_parity_contested_bet_prefers_the_pass2_bet():
    """The depth-capped bet slot (room for ONE 300 Wh booking above the
    inverter cutoff) goes to the energy-limited load's pass-2 bet in BOTH
    config orders. Since F-PREDRAIN-BLOCK the two classes bet in DIFFERENT
    passes and pass 2 evaluates before pass 3: the fossibot's bet is accepted
    against a horizon that does not yet contain the dehumidifier's block, and
    the block's own gate stack then vetoes it (a second 300 Wh drain would
    fall through the cutoff). The operator's F-LOAD-PRIORITY preference —
    "lieber den Fossibot laden, als den Luftentfeuchter betreiben, wenn die
    Wahl besteht" — is thus STRUCTURAL for contested bet depth; config order
    discriminates only among same-class contenders now (pinned by
    test_predrain_block_two_continuous_loads_follow_config_order). The depth
    cap still discriminates: without the fossibot's accepted bet the
    dehumidifier's block books exactly that slot."""
    fb = replace(FOSSIBOT_B, target_soc_percent=100.0)
    # 800 Wh start: one ~326 Wh drain lands at ~474 Wh (above the 400 Wh
    # cutoff); any second quantum would fall through it — room for one bet.
    for loads in ((fb, _DEH300), (_DEH300, fb)):
        plans = _parity_scene(800.0, loads)
        assert _books_slot0_pass2(plans, "fossibot_b")
        assert not _books_slot0_bet(plans, "deh300")
    # The cap is the binding constraint: without the fossibot's bet, the same
    # slot hosts the dehumidifier's pass-3 block.
    plans = _parity_scene(800.0, (_DEH300,))
    assert _books_slot0_bet(plans, "deh300")


def test_gate_parity_z4_stress_binds_energy_limited_bets():
    """Z4 now gates energy-limited bets too. The bet must end in the gap
    between the inverter cutoff (20 % = 400 Wh) and the RAMPED stress floor
    (20 % + 5 % buffer = 500 Wh; the buffer ramps with the slot's stressed
    house deficit, so the bet slot carries 200 Wh of house load): a 950 Wh
    start minus ~217 Wh house minus ~326 Wh bet lands at ~407 Wh — above
    the cutoff (no import, c1/Z2'/Z3 all pass) but below the stressed
    floor. alpha=0.5 rejects the full quantum (the half-quantum, landing
    at ~570 Wh, may still book); alpha=1.0 disables the gate and books the
    full quantum — Z4 is the only discriminating constraint."""
    fb = replace(FOSSIBOT_B, target_soc_percent=100.0)

    def run(alpha):
        control = replace(
            ControlParams(),
            import_trade_ratio=0.1,
            predrain_pv_confidence=alpha,
            upper_pv_reserve=1.0,
            strong_pv_cutoff_w=200.0,
        )
        cfg = SystemConfig(
            control=control,
            loads=(fb,),
            battery=BatteryParams(capacity_wh=2000.0),
            ac_profile=LoadProfile(0.0, 0.0),
            dc_profile=LoadProfile(0.0, 0.0),
        )
        start = datetime(2026, 7, 4, 6, 0)
        slots = tuple(
            HourSlot(
                index=i,
                start=start + timedelta(hours=i),
                duration=1.0,
                hour_of_day=6 + i,
                pv_wh=pv,
                ac_wh=ac,
                dc_wh=0.0,
            )
            for i, (pv, ac) in enumerate(((10.0, 200.0), (2500.0, 0.0), (100.0, 0.0)))
        )
        inputs = PlanInputs(
            now=start,
            start_soc_percent=47.5,  # 950 Wh
            slots=slots,
            load_states=(SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),),
        )
        threshold, base = search_threshold(cfg, inputs)
        plans, _extra, _traj = allocate_loads(cfg, inputs, threshold, base)
        return sum(
            a[3]
            for p in plans
            if p.load_id == "fossibot_b"
            for a in p.allocations
            if a[0] == 0 and a[2] == 2
        )

    stressed_p2 = run(0.5)
    trusting_p2 = run(1.0)
    assert trusting_p2 >= 300.0 - 1e-6, (
        f"alpha=1.0 must book the full quantum, got {trusting_p2}"
    )
    assert stressed_p2 <= 150.0 + 1e-6, (
        f"alpha=0.5 must cap the slot-0 bet below the full quantum, got {stressed_p2}"
    )
    assert stressed_p2 < trusting_p2  # the stress gate visibly bound


def test_gate_parity_daylight_rule_blocks_fb_night_predrain():
    """Maximum temptation (generous ratio, hungry powerstation, T1-shaped
    horizon whose night pre-drain a continuous load DOES book): the
    energy-limited load never books a zero-PV slot — only the daylight rule
    separates the classes now."""
    now = datetime(2026, 7, 3, 21, 0)
    fb_hungry = replace(FOSSIBOT_B, capacity_wh=8000.0, target_soc_percent=100.0)
    cfg = _predrain_config(ratio=0.5, alpha=1.0, beta=1.0, loads=(fb_hungry,))
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0], load_states=states)
    # Guard against a vacuous pass: the hungry fossibot must book SOMETHING
    # (its daylight hours) — only then does the night exclusion mean anything.
    assert result.load_plans[0].planned_energy_wh > 0.0
    for i, on in enumerate(result.load_plans[0].schedule):
        if on:
            assert inputs.slots[i].pv_wh > 0.0, (
                f"fossibot booked zero-PV slot at {inputs.slots[i].hour_of_day}:00"
            )
    # Contrast (F-PREDRAIN-BLOCK per-day blocks, v0.22.0): in the SAME
    # horizon the continuous load books NOTHING tonight before midnight —
    # the retired cross-day carve-out — while TOMORROW's own block runs
    # pre-dawn, confined to tomorrow (each day gets its own block). The
    # daylight rule nonetheless stays the binding class rule for the pass-2
    # bet machinery: the hungry fossibot above never takes a zero-PV slot,
    # although its gates would admit the same rescue energy.
    deh_cfg = _predrain_config(ratio=0.5, alpha=1.0, beta=1.0)
    deh_res, deh_in = make_plan(deh_cfg, now, 84.0, [0.0, 13.0, 11.0])
    tomorrow = now.date() + timedelta(days=1)
    tonight = [
        deh_in.slots[i].hour_of_day
        for i, on in enumerate(deh_res.load_plans[0].schedule)
        if on and deh_in.slots[i].start.date() < tomorrow
    ]
    assert not tonight, "nothing tonight — the cross-day drain stays retired"
    # Tomorrow's own block may exist, but every block stays inside ONE day.
    p3_blocks = [a for a in deh_res.load_plans[0].allocations if a[2] == 3]
    assert p3_blocks, "tomorrow's own pre-drain block is expected (per-day)"
    for start, count, _pass, _wh in p3_blocks:
        days = {deh_in.slots[j].start.date() for j in range(start, start + count)}
        assert len(days) == 1, f"block spans midnight: {days}"
        assert next(iter(days)) >= tomorrow, f"block starts tonight: {days}"
    assert deh_res.load_plans[0].planned_energy_wh > 0.0, (
        "contrast is not vacuous: the clip day is still absorbed in daylight"
    )


def test_gate_parity_c2_beta_books_energy_limited_in_window():
    """T12 parity: the optimistic c2 gate now opens extra in-window slots for
    an energy-limited load exactly as it does for the dehumidifier — reason
    string included — while the trade invariant keeps holding. (Geometry
    re-scoped for F-PEAK-FILL: a 600 W powerstation on a 9 kWh day, so the
    c2 bet's dip LEAVES the at-max top-up band (slot ends < 90 %) and the
    cheap pass-1 top-up cannot shadow it — with the old 300 W / 7 kWh
    geometry the bet dips only to ~92 %, sits inside the band, and the
    top-up books the slot regardless of beta.)"""
    now = datetime(2026, 7, 4, 8, 0)
    fb_hungry = replace(
        FOSSIBOT_B, nominal_power_w=600.0, capacity_wh=8000.0, target_soc_percent=100.0
    )
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)

    def run(beta):
        cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=beta, loads=(fb_hungry,))
        return make_plan(cfg, now, 92.0, [9.0], load_states=states)

    r10, inputs = run(1.0)
    r12, _ = run(1.2)
    booked10 = {i for i, on in enumerate(r10.load_plans[0].schedule) if on}
    booked12 = {i for i, on in enumerate(r12.load_plans[0].schedule) if on}
    extra = booked12 - booked10
    assert extra, "beta=1.2 must open extra in-window slots for the fossibot"
    windows = pv_windows(inputs, 200.0, None)
    for i in extra:
        w = windows[inputs.slots[i].start.date()]
        assert w[0] <= i <= w[1], f"c2 slot {i} not inside its PV window {w}"
        assert inputs.slots[i].pv_wh > 0.0
    assert any("in-window insurance" in r for r in r12.load_plans[0].reasons)
    cfg12 = _predrain_config(ratio=0.1, alpha=1.0, beta=1.2, loads=(fb_hungry,))
    _, base = search_threshold(cfg12, inputs)
    traj = r12.trajectory
    assert traj.total_import_wh - base.total_import_wh <= (
        0.1 * (base.total_export_wh - traj.total_export_wh) + 1.0 + 1e-6
    )


def test_r1_import_capped_at_slack_never_scales_with_rescue():
    """F-STRICT-SURPLUS R1 (supersedes FIX-6): the import gate is the absolute
    IMPORT_ARTIFACT_SLACK_WH, in BOTH directions. (a) A sub-Wh standby artifact
    (0.5 W charger) no longer vetoes the pre-drain block — L1 is solved by the
    slack, not by a trade ratio. (b) On a strong clip day with kWh of rescuable
    export, the whole allocation still may not add import beyond the slack —
    the retired proportional budget (0.1 * rescued + 1) would have minted
    hundreds of Wh here. (Same-day rewrite, F-PREDRAIN-BLOCK.)"""
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    now = datetime(2026, 7, 4, 4, 0)
    charger = ConverterParams(max_power_w=2300.0, eta=0.92, standby_power_w=0.5)
    control = replace(
        ControlParams(),
        import_trade_ratio=0.0,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
    )
    cfg = SystemConfig(control=control, loads=(DEHUMIDIFIER,), charger=charger)
    result, inputs = make_plan(cfg, now, 50.0, [13.0, 11.0])
    assert _pass3_block(result), (
        "a sub-Wh standby artifact must not veto the pre-drain block"
    )
    assert result.import_trade_used_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6

    # (b) the hard cap: rescued export is huge, yet added import <= slack.
    _thr, base = search_threshold(cfg, inputs)
    rescued = base.total_export_wh - result.trajectory.total_export_wh
    assert rescued > 1000.0  # kWh-class rescue on this clip day
    added = result.trajectory.total_import_wh - base.total_import_wh
    assert added <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    assert added < 0.1 * rescued  # the retired budget would have allowed more


def test_fix7_stress_window_extends_over_spill_past_recovery():
    """FIX-7 under F-STRICT-SURPLUS R3: the Z4 stress window must cover a run's
    SPILL past its settlement slot. A 150-min commitment at the bet slot rides
    through the refill spike (recovery = the spike slot, where the trial hits
    soc_max) and spills into a heavy-drain slot AFTER it; the settlement-only
    window [i, recovery] never sees that drain (its stressed reserve stays at
    the refilled SOC, so the candidate would pass), while the extended window
    [i, max(recovery, covered[-1][0])] stresses the spill and finds it breaks
    the inverter floor — the reject the gate now makes. Exercised with the
    gate's own primitives on a small battery so the single spill slot alone
    can cross the floor."""
    control = replace(
        ControlParams(),
        predrain_pv_confidence=0.5,
        upper_pv_reserve=1.2,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        control=control,
        loads=(),
        battery=BatteryParams(capacity_wh=2000.0),
        ac_profile=LoadProfile(0.0, 0.0),
        dc_profile=LoadProfile(0.0, 0.0),
    )
    start = datetime(2026, 7, 4, 6, 0)

    def slot(i, hour, pv, ac=0.0, dc=0.0):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=dc,
        )

    slots = (
        slot(0, 6, 300, ac=0),  # bet slot
        slot(1, 7, 3000, ac=0),  # refill spike -> trial reaches soc_max here
        slot(2, 8, 60, dc=1400),  # post-refill: heavy drain near the floor
        slot(3, 9, 50, dc=200),
    )
    inputs = PlanInputs(now=start, start_soc_percent=55.0, slots=slots)
    n = len(slots)
    threshold, _base = search_threshold(cfg, inputs)
    alpha = 0.5
    floor = cfg.control.inverter_min_soc_percent + cfg.control.soc_buffer_percent  # 25

    i = 0
    # A 150-min (2.5 h) commitment at slot 0 spills 0.5 h into slot 2.
    trial, covered = _spread_energy([0.0] * n, slots, i, 400.0, 2.5)
    traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    recovery = _refill_index(traj, i, cfg.battery.soc_max_percent - 0.1)
    hi = max(recovery, covered[-1][0])
    assert recovery == 1 and covered[-1][0] == 2 and hi == 2  # spills PAST refill

    def windowed(lo):
        sv = [alpha if i <= j <= lo else 1.0 for j in range(n)]
        t = _windowed_min_soc(
            simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial), pv_scale=sv),
            i,
            lo,
        )
        b = _windowed_min_soc(
            simulate(cfg, inputs, threshold, extra_ac_wh=(0.0,) * n, pv_scale=sv),
            i,
            lo,
        )
        return t, b

    # Settlement-only window (the pre-FIX-7 scope) never sees the spill: the
    # reserve stays at the refilled SOC, so the candidate would be ACCEPTED.
    t_rec, _b_rec = windowed(recovery)
    assert t_rec >= floor
    # The extended window stresses the spill: its reserve breaks the floor AND is
    # worse than the base, so FIX-7 rejects the candidate.
    t_hi, b_hi = windowed(hi)
    assert t_hi < floor - 1e-6
    assert t_hi < b_hi - 1e-6


# ---------------------------------------------------------------------------
# F-PREDRAIN-BLOCK (operator 2026-08-01): the continuous load's pre-drain is
# ONE contiguous block ending at TODAY's first export slot, started as late
# as the gates allow — never cross-day (the 19-min forecast-flicker battery
# run of 2026-08-01 ended the slot-wise night betting). Energy-limited loads
# keep the pass-2 slot-bet machinery unchanged.
# ---------------------------------------------------------------------------


def test_predrain_block_books_one_block_ending_at_todays_peak():
    """R1-R4: EVERY clipping day books exactly ONE pass-3 allocation — today a
    contiguous block [start, peak) ending at today's first export slot,
    latest-first, and named in the reason string; tomorrow's clip earns its
    own block inside tomorrow (per-day R1, v0.22.0)."""
    now = datetime(2026, 7, 4, 4, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    result, inputs = make_plan(cfg, now, 50.0, [13.0, 11.0])
    lp = result.load_plans[0]
    p3 = _pass3_block(result)
    assert p3 == [(2, 3, 3, 1200.0), (24, 4, 3, 1600.0)]
    start, count, _, _wh = p3[0]
    # Today's block is one unbroken range (06:00-08:00) whose last slot sits
    # right before today's first export slot (the 09:00 peak). (06:00 start
    # since the always-on nominal dynamic floor caps the block, operator
    # 2026-08-02.)
    assert [inputs.slots[i].hour_of_day for i in range(start, start + count)] == [
        6,
        7,
        8,
    ]
    assert inputs.slots[start + count].hour_of_day == 9
    # Tomorrow's block is equally confined to its own day (04:00-07:00).
    start2, count2, _, _wh2 = p3[1]
    assert {inputs.slots[i].start.date() for i in range(start2, start2 + count2)} == {
        inputs.slots[start2].start.date()
    }
    assert inputs.slots[start2].start.date() != inputs.slots[start].start.date()
    reason = next(r for r in lp.reasons if r.startswith("pass 3 @"))
    assert "pre-drain block" in reason
    assert "latest feasible start" in reason


def test_predrain_block_drains_to_the_nominal_ramped_floor():
    """Operator model (2026-08-02): on a strong day with high start SOC the
    block drains to the NOMINAL ramped floor — no alpha/band stress, so the
    depth is identical at any alpha — while R2 still vetoes anything that
    would ride the inverter cutoff.

    Part A (floor-bound): a 200 W load from 45 % books [05:00, 09:00) with
    the trough just above the saturated ramped floor (inverter 20 + full
    buffer 5); the refused 04:00 extension would dip to ~22.5 % — below the
    floor but clear of the 20 % cutoff, so the dynamic buffer, not R2, caps
    the depth. Identical block at alpha 0.5 and 1.0 (stress-independence).

    Part B (cutoff-bound): from 30 % the same scene books only the PV-served
    morning tail [07:00, 09:00) — the pre-dawn extension would ride the
    20 % cutoff, so R2 (inside `_gate_trial`) refuses it."""
    deh200 = replace(DEHUMIDIFIER, nominal_power_w=200.0)
    now = datetime(2026, 7, 4, 4, 0)

    def run(soc, alpha):
        cfg = _predrain_config(ratio=0.1, alpha=alpha, beta=1.0, loads=(deh200,))
        result, inputs = make_plan(cfg, now, soc, [13.0, 11.0])
        return cfg, result, inputs

    # Part A: the block drains to the nominal ramped floor, alpha-independently.
    cfg, result, inputs = run(45.0, 1.0)
    block = _pass3_block(result)
    # Today's floor-bound block [05:00, 09:00); tomorrow's own block starts
    # at its midnight (per-day R1, v0.22.0).
    assert block == [(1, 4, 3, 800.0), (20, 8, 3, 1600.0)]
    _cfg_h, half, _inputs_h = run(45.0, 0.5)
    assert _pass3_block(half) == block  # stress-independent depth
    s, count, _, _ = block[0]
    peak = s + count
    soc_full = cfg.battery.soc_max_percent - 0.1
    recovery = _refill_index(result.trajectory, s, soc_full)
    trough = _windowed_min_soc(result.trajectory, s, max(recovery, peak - 1))
    floor = 20.0 + 5.0  # inverter cutoff + full buffer (ramp saturates pre-dawn)
    assert floor <= trough < floor + 2.0  # drained to the nominal floor

    # The refused 04:00 extension (replayed on the accepted series) dips
    # below the floor but stays off the 20 % cutoff — the dynamic buffer,
    # not R2, is the binding cap.
    threshold, _base = search_threshold(cfg, inputs)
    lp = result.load_plans[0]
    extra_wo = [f.extra_ac_wh for f in result.trajectory.flows]
    for j in range(s, peak):
        extra_wo[j] -= 200.0 * lp.run_hours[j]
    trial = list(extra_wo)
    for j in range(s - 1, peak):
        trial[j] += 200.0 * inputs.slots[j].duration
    ext = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    ext_rec = _refill_index(ext, s - 1, soc_full)
    ext_min = _windowed_min_soc(ext, s - 1, max(ext_rec, peak - 1))
    assert 20.0 < ext_min < floor

    # Part B: lower start SOC -> R2 shortens the block to the PV-served tail.
    cfg_b, low, inputs_b = run(30.0, 1.0)
    block_b = _pass3_block(low)
    # Today's block is R2-shortened to [07:00, 09:00); tomorrow's own block
    # (per-day R1) books normally from its midnight.
    assert block_b == [(3, 2, 3, 400.0), (20, 8, 3, 1600.0)]
    s_b, count_b, _, _ = block_b[0]
    peak_b = s_b + count_b
    for j in range(s_b, peak_b):
        flow = low.trajectory.flows[j]
        assert flow.soc_start_percent > 20.0 and flow.soc_end_percent > 20.0
    lp_b = low.load_plans[0]
    threshold_b, _ = search_threshold(cfg_b, inputs_b)
    extra_wo = [f.extra_ac_wh for f in low.trajectory.flows]
    for j in range(s_b, peak_b):
        extra_wo[j] -= 200.0 * lp_b.run_hours[j]
    trial = list(extra_wo)
    for j in range(s_b - 1, peak_b):
        trial[j] += 200.0 * inputs_b.slots[j].duration
    ext_b = simulate(cfg_b, inputs_b, threshold_b, extra_ac_wh=tuple(trial))
    # R2's veto: the pre-dawn extension would ride the 20 % cutoff.
    assert (
        min(ext_b.flows[j].soc_end_percent for j in range(s_b - 1, peak_b))
        <= 20.0 + 1e-6
    )


def test_predrain_block_latest_start_covers_ceil_of_target():
    """R4 latest start: when the floors allow it, the block covers exactly
    ceil(clip/power) hours — one quantum less would miss today's remaining
    clip, one more would start earlier than the target needs."""
    import re

    deh250 = replace(DEHUMIDIFIER, nominal_power_w=250.0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0, loads=(deh250,))
    now = datetime(2026, 7, 4, 4, 0)
    # Same tuned geometry as test_t5 (standby-aware charger accounting).
    result, _inputs = make_plan(cfg, now, 67.0, [6.2, 11.0])
    lp = result.load_plans[0]
    p3 = _pass3_block(result)
    # Today's block is the target-bound one; tomorrow's clip earns its own
    # (per-day R1, v0.22.0) — the reason inspected below is today's (first).
    assert p3 == [(3, 3, 3, 750.0), (22, 6, 3, 1500.0)]
    reason = next(r for r in lp.reasons if r.startswith("pass 3 @"))
    target = int(re.search(r"against (\d+) Wh clip", reason).group(1))
    assert 750.0 >= target > 750.0 - deh250.nominal_power_w


def test_predrain_block_skipped_when_today_has_no_export():
    """R2: no export slot today -> no block TODAY; a weak today never
    pre-drains for tomorrow's clip and nothing is booked on it at all.
    Tomorrow's clip earns its OWN block starting at its own midnight
    (per-day R1, v0.22.0) — never a drain from the empty day."""
    now = datetime(2026, 7, 4, 4, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    result, inputs = make_plan(cfg, now, 50.0, [0.8, 13.0])
    lp = result.load_plans[0]
    today = inputs.slots[0].start.date()
    # No pass-3 block touches today (today has no peak); tomorrow's own
    # block [(28,2,3,800)] exists and is confined to tomorrow.
    p3 = _pass3_block(result)
    assert p3 == [(28, 2, 3, 800.0)]
    assert all(inputs.slots[a[0]].start.date() != today for a in p3)
    assert {inputs.slots[i].start.date() for i in range(28, 30)} == {
        datetime(2026, 7, 5).date()
    }
    # Nothing at all is booked on the weak today.
    assert all(
        inputs.slots[i].start.date() != today for i, on in enumerate(lp.schedule) if on
    )
    assert all(inputs.slots[i].pv_wh > 0.0 for i, on in enumerate(lp.schedule) if on)
    # Not vacuous: tomorrow's clip is still absorbed, in its own daylight.
    assert lp.planned_energy_wh > 0.0


def test_predrain_block_never_crosses_into_tomorrow():
    """R1 confinement, the retired F-NIGHT-RESCUE carve-out: on the 21:00
    clipping-eve geometry (today export-free, tomorrow clips hard) the
    continuous load books NOTHING before tomorrow's midnight — nothing ever
    drains one day for the next (operator 2026-08-01). Per-day R1 (v0.22.0):
    tomorrow's and the day-after's clips DO earn their own blocks — each
    starting at its own midnight and staying inside its own day."""
    now = datetime(2026, 7, 3, 21, 0)
    cfg = _predrain_config(ratio=0.1, alpha=0.5, beta=1.0)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0])
    lp = result.load_plans[0]
    today = inputs.slots[0].start.date()
    # (a) Nothing at all is booked before tomorrow's midnight — the cross-day
    # night pre-drain stays retired.
    assert all(
        inputs.slots[i].start.date() != today for i, on in enumerate(lp.schedule) if on
    )
    # (b) Each future clipping day gets its OWN confined block.
    p3 = _pass3_block(result)
    assert p3 == [(7, 4, 3, 1600.0), (31, 4, 3, 1600.0)]
    for start, count, _, _wh in p3:
        days = {inputs.slots[i].start.date() for i in range(start, start + count)}
        assert len(days) == 1, "a block must never cross midnight"
        assert days.pop() > today
    assert lp.planned_energy_wh > 0.0  # the clips are still absorbed


def test_predrain_block_energy_limited_keeps_pass2_bets():
    """Regression anchor: in the SAME 21:00 clipping-eve horizon the
    energy-limited load still bets via pass 2 (its machinery is unchanged) —
    in daylight, never in a zero-PV slot."""
    now = datetime(2026, 7, 3, 21, 0)
    fb_hungry = replace(FOSSIBOT_B, capacity_wh=8000.0, target_soc_percent=100.0)
    cfg = _predrain_config(ratio=0.5, alpha=1.0, beta=1.0, loads=(fb_hungry,))
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0], load_states=states)
    lp = result.load_plans[0]
    assert any(a[2] == 2 for a in lp.allocations)
    assert all(inputs.slots[i].pv_wh > 0.0 for i, on in enumerate(lp.schedule) if on)


def test_predrain_block_skipped_when_the_peak_is_now():
    """R2: today's first export slot IS slot 0 (the battery is already
    exporting) -> no block TODAY; pass 1 owns the present surplus. Tomorrow's
    clip still earns its own block inside its own day (per-day R1, v0.22.0)."""
    now = datetime(2026, 7, 4, 8, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    result, inputs = make_plan(cfg, now, 93.0, [13.0, 11.0])
    lp = result.load_plans[0]
    assert result.trajectory.flows[0].grid_export_wh > 0.0
    today = inputs.slots[0].start.date()
    p3 = _pass3_block(result)
    assert all(inputs.slots[a[0]].start.date() != today for a in p3)
    assert p3 == [(20, 4, 3, 1600.0)]  # tomorrow's own block only
    assert lp.active_now and lp.allocations[0][2] == 1


def test_predrain_block_two_continuous_loads_follow_config_order():
    """Priority among continuous loads: the FIRST load's blocks see the
    unmodified (pass-1-adjusted) export and today's block is identical to the
    one it would book alone; the second load's own pass-1 bookings sit right
    before the (shifted) peak, so its backward walk immediately hits its own
    booking and no second block exists. Swapping the config order swaps the
    winner. (Per-day R1, v0.22.0: the winner ALSO books tomorrow's block —
    shorter than the solo scenario's, because the loser's pass-1 bookings
    reshape tomorrow's peak before any block walk runs.)"""
    deh1 = replace(DEHUMIDIFIER, load_id="deh1", name="D1")
    deh2 = replace(DEHUMIDIFIER, load_id="deh2", name="D2")
    now = datetime(2026, 7, 4, 4, 0)

    def blocks(order):
        cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0, loads=order)
        states = tuple(SurplusLoadState(load_id=ld.load_id) for ld in order)
        result, _ = make_plan(cfg, now, 50.0, [13.0, 11.0], load_states=states)
        return {ld.load_id: _pass3_block(result, ld.load_id) for ld in order}

    first = blocks((deh1, deh2))
    assert first["deh1"] == [(2, 3, 3, 1200.0), (25, 3, 3, 1200.0)]
    assert first["deh2"] == []
    swapped = blocks((deh2, deh1))
    assert swapped["deh2"] == [(2, 3, 3, 1200.0), (25, 3, 3, 1200.0)]
    assert swapped["deh1"] == []


def test_predrain_block_today_never_predrains_for_tomorrow():
    """R1 over multiple days: today's block ends at TODAY's peak even though
    tomorrow's clip is bigger, and nothing books today's late hours — today
    never lends a drain to tomorrow. Tomorrow's bigger clip is absorbed by
    its OWN confined block plus its daylight pass-1 runs (per-day R1,
    v0.22.0)."""
    now = datetime(2026, 7, 4, 4, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    result, inputs = make_plan(cfg, now, 50.0, [13.0, 15.0])
    lp = result.load_plans[0]
    # Today's nominal-floor-capped block plus tomorrow's own (its midnight).
    assert _pass3_block(result) == [(2, 3, 3, 1200.0), (24, 4, 3, 1600.0)]
    today = inputs.slots[0].start.date()
    # Nothing books today past its peak (18:00+) — no night bridge between
    # the days.
    assert all(
        inputs.slots[i].hour_of_day < 18
        for i, on in enumerate(lp.schedule)
        if on and inputs.slots[i].start.date() == today
    )
    after = [
        (i, a)
        for a in lp.allocations
        for i in range(a[0], a[0] + a[1])
        if inputs.slots[i].start.date() != today
    ]
    assert after, "tomorrow's clip is still absorbed"
    # Tomorrow's block is confined to tomorrow (04:00-07:00); everything
    # tomorrow books BEYOND its block is pass-1 daylight, as before.
    assert {inputs.slots[i].start.date() for i in range(24, 28)} == {
        datetime(2026, 7, 5).date()
    }
    assert all(a[2] == 1 and inputs.slots[i].pv_wh > 0.0 for i, a in after if a[2] != 3)


def test_predrain_block_shorter_than_min_runtime_is_dropped():
    """R6: a block whose committed run (block hours + an own booking
    continuing in the peak slot) is shorter than min_runtime is never booked
    — the executor dwell would deliver more than the plan accounts. Here the
    gates accept a two-hour block ending at the 11:00 peak whose thin export
    never triggered a pass-1 booking (no continuation), and two hours is
    below the 150 min dwell, so the block is dropped wholesale. (Geometry
    shifted by the standby accounting fix: the candidate was a single hour
    ending at the 10:00 peak under the old export trajectory — same drop
    rule, re-pinned dwell.)"""
    deh150 = replace(DEHUMIDIFIER, min_runtime_min=150)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0, loads=(deh150,))
    now = datetime(2026, 7, 4, 4, 0)
    result, inputs = make_plan(cfg, now, 60.0, [5.5, 11.0])
    lp = result.load_plans[0]
    assert not _pass3_block(result)
    today = inputs.slots[0].start.date()
    assert all(
        inputs.slots[i].start.date() != today for i, on in enumerate(lp.schedule) if on
    )


def test_predrain_block_detour_not_repaid_is_refused():
    """R5/c1: a block whose battery detour cannot be repaid from lost export
    at the physical round trip is refused — even with every hard gate
    (Z2''/R2/R5/Z3) passing. Geometry: a PV-served ramp slot ahead of a
    charger-saturated clip; the charger runs at its cap in every strong hour,
    so the charge the block steals never comes back (export drop 0) and the
    (1-tol)*block*rt need is unmet."""
    deh = SurplusLoad(
        load_id="deh",
        name="E",
        nominal_power_w=400.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
    )
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        control=control,
        loads=(deh,),
        battery=BatteryParams(capacity_wh=18000.0),
        ac_profile=LoadProfile(50.0, 75.0, 6, 20),
        dc_profile=LoadProfile(50.0, 25.0, 6, 22),
    )
    start = datetime(2026, 7, 4, 7, 0)

    def slot(i, pv, ac=125.0, dc=75.0):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=(start + timedelta(hours=i)).hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=dc,
        )

    # 07:00 ramp (the block candidate), 08:00-09:00 charger-capped (the peak),
    # then a cloudy tail: the big battery never fills, so the refill can only
    # ever come from capped hours — where no headroom exists.
    slots = tuple(
        slot(i, pv) for i, pv in enumerate([800.0, 5000.0, 5000.0] + [0.0] * 10)
    )
    inputs = PlanInputs(
        now=start,
        start_soc_percent=50.0,
        slots=slots,
        load_states=(SurplusLoadState(load_id="deh"),),
    )
    threshold, base = search_threshold(cfg, inputs)
    load_plans, extra, current = allocate_loads(cfg, inputs, threshold, base)
    lp = load_plans[0]
    assert not any(a[2] == 3 for a in lp.allocations)

    # Prove the veto is c1's: the refused one-slot block [0, 1) passes the
    # hard gates (no import, PV-served, no max-day robbed, floor intact) ...
    trial = list(extra)
    trial[0] += 400.0
    traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert (
        traj.total_import_wh - base.total_import_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    )
    # ... yet rescues NOTHING: the export drop is zero against a (1-tol)*rt
    # need of ~280 Wh, because the capped charger can never refill the drain.
    drop = current.total_export_wh - traj.total_export_wh
    rt = (
        cfg.charger.eta
        * cfg.battery.eta_charge
        * cfg.battery.eta_discharge
        * cfg.inverter.eta
    )
    assert drop < (1.0 - deh.battery_tolerance) * 400.0 * rt


def test_predrain_block_stops_at_own_pass1_booking():
    """R4: the backward walk never crosses a slot the load itself already
    booked. When pass 1 consumes the export of the slot right before the
    (shifted) peak, there is no contiguous room for a block at all and none
    is booked THAT DAY — the block machinery yields to the pass-1 plan
    instead of double-covering the hour. (Tomorrow, without such an own
    booking at its peak's shoulder, gets its own block — per-day R1.)"""
    now = datetime(2026, 7, 4, 4, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    result, inputs = make_plan(cfg, now, 84.0, [13.0, 11.0])
    lp = result.load_plans[0]
    today = inputs.slots[0].start.date()
    p3 = _pass3_block(result)
    assert p3 == [(24, 4, 3, 1600.0)]  # tomorrow's own block; none today
    assert all(inputs.slots[a[0]].start.date() != today for a in p3)
    # The slot right before today's first export slot is a pass-1 booking
    # (07:00): pass 1 ate its export, the peak moved one slot later, and the
    # walk breaks at the own booking.
    peak = next(
        j
        for j, f in enumerate(result.trajectory.flows)
        if inputs.slots[j].start.date() == today and f.grid_export_wh > 1e-6
    )
    assert lp.schedule[peak - 1]
    assert any(a[0] == peak - 1 and a[2] == 1 for a in lp.allocations)


# ---------------------------------------------------------------------------
# F-QUANTILE-BANDS: per-slot P10/P90 bands replace scalar alpha/beta where
# evidence exists (docs/F-QUANTILE-BANDS.md). THE safety rule (D2): a COLLAPSED
# band (p10 == p90, the balcony cold-start signature) means "no evidence", NOT
# "no uncertainty" — it must fall back to the scalars. THE regression anchor
# (R8): with no band data anywhere, plans are bit-identical to the scalar era.
# ---------------------------------------------------------------------------


def _band_slot(pv, p10, p90):
    return HourSlot(
        index=0,
        start=datetime(2026, 7, 4, 12, 0),
        duration=1.0,
        hour_of_day=12,
        pv_wh=pv,
        ac_wh=0.0,
        dc_wh=0.0,
        pv_p10_wh=p10,
        pv_p90_wh=p90,
    )


def test_effective_uncertainty_band_detection_and_clamps():
    """R11/R10: band presence per D2 (real spread, enough PV, data present),
    COLLAPSED bands and low-PV/missing slots fall back to the scalars, ratios
    are clamped, and a partially covered vector mixes evidence with scalars
    (R9) in the same pass."""
    from types import SimpleNamespace

    from core.optimize import _effective_uncertainty

    slots = (
        _band_slot(1000.0, 700.0, 1300.0),  # real band: ratios 0.7 / 1.3
        _band_slot(1000.0, 800.0, 800.0),  # COLLAPSED (p10==p90): no evidence
        _band_slot(10.0, 5.0, 20.0),  # pv < QUANTILE_RATIO_MIN_WH: noise
        _band_slot(1000.0, None, None),  # no data
        _band_slot(1000.0, 50.0, 2500.0),  # junk ratios -> clamped
        _band_slot(1000.0, 995.0, 1004.0),  # spread 9 Wh < 1% of pv: no band
    )
    inputs = SimpleNamespace(slots=slots)
    stress, optimism, band = _effective_uncertainty(inputs, 0.5, 1.2)
    assert band == [True, False, False, False, True, False]
    assert abs(stress[0] - 0.7) < 1e-9 and abs(optimism[0] - 1.3) < 1e-9
    # Collapsed / low-PV / missing / thin-spread slots keep the scalar dials.
    for j in (1, 2, 3, 5):
        assert stress[j] == 0.5 and optimism[j] == 1.2
    # D3 clamps: stress floors at 0.1, optimism caps at 2.0.
    assert stress[4] == 0.1 and optimism[4] == 2.0


def _t12_band_plan(beta, r10=None, r90=None):
    """The T12 c2 geometry (08:00, house 92 %, one 9 kWh day, a 600 W hungry
    powerstation) with optional P10/P90 bands at the given ratios on every
    daylight slot. The load is ENERGY-LIMITED: since F-PREDRAIN-BLOCK the
    c2/beta insurance machinery is pass-2-only, and pass 2 is its class.
    Re-scoped for F-PEAK-FILL: the 600 W quantum makes the c2 bet's dip leave
    the at-max top-up band (slot ends < 90 %), so the cheap pass-1 top-up
    cannot shadow the insurance slots — the banded optimism stays the
    discriminating mechanism (see the parity T12 test)."""
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=beta,
        strong_pv_cutoff_w=200.0,
    )
    fb_hungry = replace(
        FOSSIBOT_B, nominal_power_w=600.0, capacity_wh=8000.0, target_soc_percent=100.0
    )
    cfg = SystemConfig(control=control, loads=(fb_hungry,))
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)
    now = datetime(2026, 7, 4, 8, 0)
    base_inputs = build_slots(cfg, now, 92.0, [9.0], load_states=states)
    if r10 is None:
        return plan(cfg, base_inputs), base_inputs
    p10: dict[datetime, float] = {}
    p90: dict[datetime, float] = {}
    for s in base_inputs.slots:
        if s.pv_wh >= 25.0 and s.duration == 1.0:
            p10[s.start] = s.pv_wh * r10
            p90[s.start] = s.pv_wh * r90
    inputs = build_slots(
        cfg, now, 92.0, [9.0], load_states=states, pv_hourly_p10=p10, pv_hourly_p90=p90
    )
    return plan(cfg, inputs), inputs


def _booked(result):
    return {i for i, on in enumerate(result.load_plans[0].schedule) if on}


def test_c2_insurance_follows_p90_evidence_not_the_dial():
    """R12a/R12b/R6: with P90 evidence present, the c2 insurance follows the
    band, not the beta dial — bookings that exist at beta=1.2 disappear when
    the evidence says "no upside" (p90_ratio 1.0), and appear at beta=1.0
    when the evidence says "real upside" (p90_ratio 1.3), with the "(p90)"
    reason wording."""
    scalar10, _ = _t12_band_plan(1.0)
    scalar12, _ = _t12_band_plan(1.2)
    beta_extras = _booked(scalar12) - _booked(scalar10)
    assert beta_extras, "the base geometry must book beta-only insurance slots"

    # (a) beta stays 1.2, but every band says p90 == median: no upside, no
    # insurance — the dial no longer books runs the evidence cannot justify.
    evidence_flat, _ = _t12_band_plan(1.2, 0.98, 1.0)
    assert _booked(evidence_flat) - _booked(scalar10) == set()

    # (b) beta 1.0 (dial says never), but the bands carry real 30 % upside:
    # insurance appears FROM EVIDENCE, worded "(p90)" (R6).
    evidence_up, _ = _t12_band_plan(1.0, 0.98, 1.3)
    assert _booked(evidence_up) - _booked(scalar10), "evidence must open slots"
    insurance = [
        r for r in evidence_up.load_plans[0].reasons if "in-window insurance" in r
    ]
    assert insurance and all("(p90)" in r for r in insurance)


def test_collapsed_bands_keep_the_scalar_plan():
    """R10 (THE cold-start rule, dedicated): collapsed bands (p10 == p90 ==
    median) on every daylight slot leave the plan IDENTICAL to the scalar run
    — including the beta-only insurance bookings. If a collapsed band were
    read as "no uncertainty" (optimism 1.0), those bookings would vanish and
    Z4 would weaken below the scalar alpha on exactly the evidence-free bins."""
    scalar12, _ = _t12_band_plan(1.2)
    collapsed, _ = _t12_band_plan(1.2, 1.0, 1.0)  # p10 == p50 == p90
    assert _booked(collapsed) == _booked(scalar12)
    assert collapsed.load_plans[0].reasons == scalar12.load_plans[0].reasons
    assert collapsed.grid_import_kwh == scalar12.grid_import_kwh
    assert collapsed.grid_export_kwh == scalar12.grid_export_kwh


def _el_z4_band_plan(alpha, r10=None, r90=None):
    """The energy-limited Z4 stress scene: a thin dawn slot (50 Wh PV, 200 Wh
    house — band-capable at >= 25 Wh) ahead of a big refill, on a 2 kWh
    battery; the fossibot's pass-2 bet quantum (300 Wh full / 150 Wh half) is
    gated ONLY by the windowed stress (c1/Z2''/R2/Z3 all pass), so band
    evidence on the dawn slot moves the booking directly. Start SOC 51.5 sits
    inside the discrimination window: the scalar 0.5 stress caps the full
    quantum, 0.85 P10 evidence admits it."""
    fb = replace(FOSSIBOT_B, target_soc_percent=100.0)
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=alpha,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        control=control,
        loads=(fb,),
        battery=BatteryParams(capacity_wh=2000.0),
        ac_profile=LoadProfile(0.0, 0.0),
        dc_profile=LoadProfile(0.0, 0.0),
    )
    start = datetime(2026, 7, 4, 6, 0)

    def slot(i, pv, ac):
        s = HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=6 + i,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=0.0,
        )
        if r10 is not None and pv >= 25.0:
            s = replace(s, pv_p10_wh=pv * r10, pv_p90_wh=pv * r90)
        return s

    slots = (slot(0, 50.0, 200.0), slot(1, 2500.0, 0.0), slot(2, 100.0, 0.0))
    inputs = PlanInputs(
        now=start,
        start_soc_percent=51.5,
        slots=slots,
        load_states=(SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),),
    )
    return plan(cfg, inputs)


def _slot0_bet_wh(result):
    lp = next(p for p in result.load_plans if p.load_id == "fossibot_b")
    return sum(a[3] for a in lp.allocations if a[0] == 0 and a[2] == 2)


def test_z4_stress_follows_p10_evidence():
    """R12c RE-SCOPED (operator 2026-08-02): the Z4 stress follows P10
    evidence in BOTH directions — but since the pass-3 block floor went
    NOMINAL, the stress lives ONLY in the slot-wise pass-2 bets of
    energy-limited loads, so the discriminating booking is a fossibot dawn
    bet, not a block start. A stable-dawn band (p10_ratio 0.85 on the bet
    slot) admits the FULL 300 Wh quantum the scalar alpha caps at the half;
    a volatile-dawn band (p10_ratio 0.4) caps the quantum the trusting
    alpha=1.0 dial accepts. (Pass-3 blocks are alpha/band-independent now —
    pinned by test_t4_alpha_stress_gate_protects_inverter_reserve.)"""
    # Milder-than-scalar: scalar 0.5 caps the bet at the half quantum; 0.85
    # P10 evidence on the bet slot admits the full one (stable dawn -> the
    # reserve provably holds).
    assert _slot0_bet_wh(_el_z4_band_plan(0.5)) == 150.0
    assert _slot0_bet_wh(_el_z4_band_plan(0.5, 0.85, 1.0)) == 300.0

    # Harsher-than-scalar: alpha=1.0 trusts and books the full quantum; 0.4
    # P10 evidence (volatile dawn) caps it despite the dial.
    trusting = _el_z4_band_plan(1.0)
    evidence_low = _el_z4_band_plan(1.0, 0.4, 1.0)
    assert _slot0_bet_wh(trusting) == 300.0
    assert _slot0_bet_wh(evidence_low) == 150.0
    # The diagnostic reports the SAME stressed reserve the gate used (R5):
    # engaged by evidence even though the alpha dial is 1.0.
    assert evidence_low.stressed_min_soc_percent is not None
    assert trusting.stressed_min_soc_percent is None


def test_bit_identity_without_band_data():
    """R12d/R8: passing empty/None band series produces a bit-identical plan
    to not passing them at all — the whole existing corpus and the goldens
    stay untouched by construction."""
    plain, _ = _t12_band_plan(1.2)
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.2,
        strong_pv_cutoff_w=200.0,
    )
    fb_hungry = replace(
        FOSSIBOT_B, nominal_power_w=600.0, capacity_wh=8000.0, target_soc_percent=100.0
    )
    cfg = SystemConfig(control=control, loads=(fb_hungry,))
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)
    now = datetime(2026, 7, 4, 8, 0)
    inputs_empty = build_slots(
        cfg, now, 92.0, [9.0], load_states=states, pv_hourly_p10={}, pv_hourly_p90=None
    )
    empty = plan(cfg, inputs_empty)
    assert _booked(empty) == _booked(plain)
    assert empty.load_plans[0].run_hours == plain.load_plans[0].run_hours
    assert empty.load_plans[0].reasons == plain.load_plans[0].reasons
    assert empty.threshold_percent == plain.threshold_percent
    assert empty.grid_import_kwh == plain.grid_import_kwh
    assert empty.grid_export_kwh == plain.grid_export_kwh


# ---------------------------------------------------------------------------
# F-NIGHT-RESCUE: round-trip-honest c1, merge-bounded threshold search,
# crossover buffer ramp (docs/F-NIGHT-RESCUE.md; incident 2026-07-11/12: no
# night pre-drain despite ~3.3 kWh forecast clipping, then the 04:13 T* jump
# 20->58 shut everything down).
# ---------------------------------------------------------------------------

NIGHT_DEH = SurplusLoad(
    load_id="dehumidifier",
    name="Entfeuchter",
    nominal_power_w=400.0,
    battery_tolerance=0.15,
    min_runtime_min=30,
)
NIGHT_STATE = (SurplusLoadState(load_id="dehumidifier", learned_power_w=426.0),)
NIGHT_CONTROL = ControlParams(
    import_trade_ratio=0.1,
    predrain_pv_confidence=0.5,
    upper_pv_reserve=1.0,
    strong_pv_cutoff_w=200.0,
)


def test_night_rescue_no_crossday_predrain_for_continuous_loads():
    """R10 REVERSED, per-day form (v0.22.0): in the exact 21:00 clipping-eve
    incident geometry the continuous load books ZERO hours TONIGHT before
    midnight — the cross-day night drain stays retired (the 19-min flicker
    run of 2026-08-01). TOMORROW gets its own block though (per-day R1):
    it may run pre-dawn but never crosses its own midnight. Pass-1 slots
    stay strictly daylight, the floors and the T* drain regime (the
    operational D1 outcome) are untouched."""
    config = SystemConfig(
        control=NIGHT_CONTROL,
        loads=(NIGHT_DEH,),
        ac_profile=LoadProfile(30.0, 45.0, 6, 22),
        dc_profile=LoadProfile(40.0, 20.0, 6, 22),
        battery=BatteryParams(capacity_wh=7000.0),
    )
    now = datetime(2026, 7, 11, 21, 0)
    inputs = build_slots(config, now, 57.0, [13.0, 12.0], load_states=NIGHT_STATE)
    result = plan(config, inputs)
    lp = result.load_plans[0]

    tomorrow = now.date() + timedelta(days=1)
    tonight_hours = sum(
        h
        for i, h in enumerate(lp.run_hours)
        if h > 0 and inputs.slots[i].start.date() < tomorrow
    )
    assert tonight_hours == 0.0, (
        f"{tonight_hours} h booked tonight — cross-day pre-drain stays retired"
    )
    p3_blocks = [a for a in lp.allocations if a[2] == 3]
    assert p3_blocks, "tomorrow's own pre-drain block is expected (per-day)"
    for start, count, _pass, _wh in p3_blocks:
        days = {inputs.slots[j].start.date() for j in range(start, start + count)}
        assert days == {tomorrow}, f"block spans midnight: {days}"
    # The clip day is still rescued — in its own daylight by pass 1 (strictly
    # daylight), plus tomorrow's own block.
    assert lp.planned_energy_wh > 0.0
    p1_slots = {
        j for a in lp.allocations if a[2] == 1 for j in range(a[0], a[0] + a[1])
    }
    assert p1_slots and all(inputs.slots[j].pv_wh > 0.0 for j in p1_slots)
    # The allocation's import stays within the artifact slack (R1).
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert result.import_trade_used_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    # The floors hold: min SOC stays above the inverter cutoff.
    assert result.min_soc_percent >= config.control.inverter_min_soc_percent - 0.01
    # T* drains to the low-import choice — the operational D1 outcome. (Under
    # F-MERGE-HYSTERESIS the ancillary merge bound stays disengaged here: this
    # eve's STRESSED clip is only ~70 Wh, sub-decisive, so the scan keeps the
    # full horizon and drains on its own merit — no weak final day pulls it up.
    # R11 pins the merge-engaged direction on a decisive clip.)
    assert result.threshold_percent <= 22.0
    assert result.threshold_horizon_end is None


def test_strict_surplus_refuses_cutoff_riding_predawn_quantum():
    """F-STRICT-SURPLUS regression lock on the small-battery geometry, same-day
    rewrite (F-PREDRAIN-BLOCK): the pre-drain block books only floor-safe
    hours. No booked slot rides the 20 % cutoff at either endpoint (R2), the
    allocation's import stays within IMPORT_ARTIFACT_SLACK_WH (R1), and the
    one-slot-earlier extension the block REFUSES is vetoed by the NOMINAL
    dynamic floor over its refill-settled bet window (operator 2026-08-02:
    pass 3 no longer stresses blocks) — not by R1 or R2, which the
    gate-by-gate check below shows passing for it."""
    from core.optimize import (
        IMPORT_ARTIFACT_SLACK_WH,
        _effective_uncertainty,
        _ramped_stress_floors,
        _refill_index,
    )

    config = SystemConfig(
        control=NIGHT_CONTROL,
        loads=(NIGHT_DEH,),
        ac_profile=LoadProfile(60.0, 90.0, 6, 20),
        dc_profile=LoadProfile(100.0, 40.0, 6, 22),
    )
    now = datetime(2026, 7, 12, 4, 0)
    inputs = build_slots(config, now, 57.0, [12.1, 11.8], load_states=NIGHT_STATE)
    result = plan(config, inputs)
    lp = result.load_plans[0]
    p3 = _pass3_block(result)
    assert p3, "the floor-safe part of the block still books"

    # R2: no booked slot rides the 20 % cutoff at either endpoint.
    floor20 = config.control.inverter_min_soc_percent
    for i, on in enumerate(lp.schedule):
        if on:
            flow = result.trajectory.flows[i]
            assert flow.soc_start_percent > floor20 + 1e-6
            assert flow.soc_end_percent > floor20 + 1e-6
    # R1: the allocation's import stays within the artifact slack.
    assert result.import_trade_used_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    # The booked block's stressed windowed reserve holds the ramped floor.
    assert result.stressed_min_soc_percent >= floor20 + 5.0 - 0.5

    # Gate-by-gate: evaluate the refused one-slot-earlier block extension
    # [start-1, peak) against the accepted series exactly as pass 3 would.
    threshold, base = search_threshold(config, inputs)
    start, count, _, _ = p3[0]
    peak = start + count
    extra_wo = [f.extra_ac_wh for f in result.trajectory.flows]
    for j in range(start, peak):
        extra_wo[j] -= 426.0 * lp.run_hours[j]
    trial = list(extra_wo)
    covered = []
    for j in range(start - 1, peak):
        take = inputs.slots[j].duration
        trial[j] += 426.0 * take
        covered.append((j, take))
    traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
    # R1 is NOT the veto: the extension's import stays within the slack.
    assert (
        traj.total_import_wh - base.total_import_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    )
    # R2 is not either: the covered slots stay off the 20 % cutoff.
    assert all(
        traj.flows[j].soc_start_percent > floor20 + 1e-6
        and traj.flows[j].soc_end_percent > floor20 + 1e-6
        for j, _t in covered
    )
    # The dynamic floor IS: the NOMINAL refill-settled window (pass 3 no
    # longer stresses blocks, operator 2026-08-02) breaks the ramped floor
    # AND is worse than the accepted series' own window — the extension would
    # deepen the dip the block is allowed to cause. (The window covers only
    # zero-PV pre-dawn slots, so the nominal and stressed mins coincide.)
    alpha = config.control.predrain_pv_confidence
    stress_vec, _o, _b = _effective_uncertainty(inputs, alpha, 1.0)
    recovery = _refill_index(traj, start - 1, config.battery.soc_max_percent - 0.1)
    hi = max(recovery, covered[-1][0])
    t_w = _windowed_min_soc(traj, start - 1, hi)
    b_w = _windowed_min_soc(
        simulate(config, inputs, threshold, extra_ac_wh=tuple(extra_wo)),
        start - 1,
        hi,
    )
    floors = _ramped_stress_floors(config, inputs, stress_vec)
    assert t_w < floors[start - 1] - 1e-6 and t_w < b_w - 1e-6


def test_merge_bounded_threshold_ignores_post_clip_hoarding():
    """R11 (D2 incident regression, both directions): with a strong day 1 that
    clips even under stress and a weak final day, T* stays at the low-import
    choice and `threshold_horizon_end` sits inside day 1 (the 04:13 jump
    20->58 came from full-horizon hoarding for the weak Tuesday). Control:
    the same geometry WITHOUT any stressed clip keeps the full horizon —
    hoarding remains allowed there (existing behaviour)."""
    config = SystemConfig(
        control=NIGHT_CONTROL,
        loads=(NIGHT_DEH,),
        ac_profile=LoadProfile(30.0, 45.0, 6, 22),
        dc_profile=LoadProfile(70.0, 20.0, 6, 22),
    )
    now = datetime(2026, 7, 12, 4, 0)

    clipped = plan(
        config,
        build_slots(config, now, 57.0, [11.9, 12.1, 2.0], load_states=NIGHT_STATE),
    )
    assert clipped.threshold_percent <= 22.0  # stays at the low-import choice
    assert clipped.threshold_horizon_end is not None
    assert clipped.threshold_horizon_end.date() == now.date()  # inside day 1

    control_run = plan(
        config,
        build_slots(config, now, 57.0, [2.0, 2.5, 2.0], load_states=NIGHT_STATE),
    )
    assert control_run.threshold_horizon_end is None  # no stressed clip
    assert control_run.threshold_percent > 40.0  # hoarding still allowed


def test_merge_bounded_threshold_drains_not_hoards_with_dc_load():
    """F-NIGHT-RESCUE F2 v2 (live 2026-07-12 midday T*=95 regression): on a
    merge-truncated window the terminal-value credit is DROPPED, so the cost is
    monotonic in the threshold and the scan deterministically drains to `lo`
    before the clip.

    Without that, a substantial DC load breaks the terminal/import cancellation
    — DC is served from the battery at `eta_discharge` WITHOUT the inverter,
    while the terminal value credits retained energy at
    `eta_discharge * eta_inverter` — so hoarding looked artificially good and the
    scan pinned T* at soc_max (95), hoarding the battery across the whole
    forecast on a clipping day (~1.5 SOC-point knife-edge).

    F-MERGE-HYSTERESIS (2026-07-24): the drain regime now engages once the
    stressed clip is DECISIVE (margin >= MERGE_TERMINAL_RAMP_WH). The original
    11.4 kWh repro produced only a ~49 Wh stressed clip — genuinely marginal, so
    it now stays (correctly) hoard-capable; a real strong-clipping day (15 kWh
    here, ~289 Wh stressed margin) still truncates and drains to `lo` with the
    DC load, which is the regime this test pins."""
    from core.optimize import _search_lo, _threshold_merge_bound

    config = SystemConfig(
        control=replace(
            ControlParams(),
            predrain_pv_confidence=0.5,
            upper_pv_reserve=1.0,
            strong_pv_cutoff_w=200.0,
        ),
        dc_profile=LoadProfile(300.0, 40.0, 6, 22),  # substantial DC base load
    )
    # Midday, battery high, decisively clipping today (stressed margin >= ramp):
    # the merge bound truncates the scan to an all-surplus window.
    inputs = build_slots(config, datetime(2026, 7, 12, 10, 0), 92.0, [15.0, 11.6, 4.7])
    assert _threshold_merge_bound(config, inputs) is not None  # scan IS truncated
    thr, _base = search_threshold(config, inputs)
    assert thr == float(_search_lo(config))  # drains before the clip, not hoard@95


def test_threshold_merge_bound_floor_and_absence():
    """R4/R5 unit: no stressed clip -> None (full horizon); a clip within the
    first slots still leaves at least 6 scan slots; a merge at the horizon end
    truncates nothing."""
    from core.optimize import _threshold_merge_bound

    config = SystemConfig(
        control=NIGHT_CONTROL,
        loads=(NIGHT_DEH,),
        ac_profile=LoadProfile(30.0, 45.0, 6, 22),
        dc_profile=LoadProfile(40.0, 20.0, 6, 22),
    )
    # Cloudy horizon: never full under stress -> no bound.
    cloudy = build_slots(
        config, datetime(2026, 7, 12, 4, 0), 57.0, [1.0, 1.5], load_states=NIGHT_STATE
    )
    assert _threshold_merge_bound(config, cloudy) is None
    # Battery already nearly full at dawn of a strong day: the stressed clip
    # arrives within the first hours, but the bound never dips below 6 slots.
    early = build_slots(
        config, datetime(2026, 7, 12, 7, 0), 94.0, [13.0, 12.0], load_states=NIGHT_STATE
    )
    merge = _threshold_merge_bound(config, early)
    assert merge is None or merge >= 5  # R5: at least 6 slots (indices 0..5)


def test_terminal_credit_factor_ramp_monotone_continuous():
    """F-MERGE-HYSTERESIS unit: the terminal credit is a CONTINUOUS, monotone
    non-increasing ramp of the stressed clip margin — full credit at margin 0
    (no stressed clip decouples tonight), zero at margin >= the ramp (guaranteed
    clip), no knife-edge in between. This is the property that replaces the old
    binary 0 / eta*eta flip."""
    full = 0.97 * 0.95  # eta_discharge * eta_inverter
    # Endpoints reproduce the old binary exactly.
    assert _terminal_credit_factor(full, 0.0) == full
    assert _terminal_credit_factor(full, -5.0) == full  # clamped, no negative clip
    assert _terminal_credit_factor(full, MERGE_TERMINAL_RAMP_WH) == 0.0
    assert _terminal_credit_factor(full, MERGE_TERMINAL_RAMP_WH * 3) == 0.0
    # Midpoint is exactly half the credit (linear ramp).
    mid = _terminal_credit_factor(full, MERGE_TERMINAL_RAMP_WH / 2.0)
    assert abs(mid - full / 2.0) < 1e-9
    # Monotone non-increasing and Lipschitz-bounded (a 1 Wh tick moves the credit
    # by at most full / ramp — no discontinuity).
    step = MERGE_TERMINAL_RAMP_WH / 200.0
    prev = _terminal_credit_factor(full, 0.0)
    m = step
    while m <= MERGE_TERMINAL_RAMP_WH + step:
        cur = _terminal_credit_factor(full, m)
        assert cur <= prev + 1e-12
        assert abs(cur - prev) <= full / MERGE_TERMINAL_RAMP_WH * step + 1e-12
        prev = cur
        m += step


def test_merge_ramp_no_t_star_bistability_at_clip_onset():
    """F-MERGE-HYSTERESIS regression (live 2026-07-24, 24 T* flanks 20<->61 in
    34 min): at the stressed-clip ONSET — where the old binary merge bound
    flipped None<->truncated and T* jumped between the hoard and drain regimes on
    a bare SOC/forecast tick — a +-1 Wh forecast / +-0.1 % SOC jitter must NOT
    move T*. Geometry: a strong-ish day 1 whose stressed clip is just appearing,
    plus a weak final day (day 3 = 2 kWh) — the hoard incentive that made the old
    edge genuinely bistable (~1.8 kWh apart), not a tie."""
    config = SystemConfig(
        control=NIGHT_CONTROL,
        loads=(NIGHT_DEH,),
        ac_profile=LoadProfile(30.0, 45.0, 6, 22),
        dc_profile=LoadProfile(70.0, 20.0, 6, 22),
    )
    now = datetime(2026, 7, 12, 4, 0)
    base_soc = 57.0

    # Self-calibrate: scan day-1 PV for the value that puts the stressed clip
    # margin just past onset but well below the ramp (so the scan stays on the
    # full horizon and the edge is the OLD knife-edge, not the new truncation
    # edge at margin >= ramp). The window's lower bound is 20 Wh: with the
    # standby-compensated charge accounting (cap-limited hours store ~9 Wh
    # less) the full-horizon hoard/drain cost tie moved a few Wh of margin
    # past the bare clip onset, and margins below ~20 Wh still sit on that
    # tie (T* flips 20<->50 there — a base-scan edge the ramp cannot and
    # should not smooth); from ~20 Wh up the fade separates the regimes.
    edge_kwh = None
    edge_margin = None
    milli = 8000
    while milli <= 13000:  # 8.0 .. 13.0 kWh, 5 Wh steps
        d1 = milli / 1000.0
        inputs = build_slots(
            config, now, base_soc, [d1, 12.0, 2.0], load_states=NIGHT_STATE
        )
        end, margin = _threshold_merge_probe(config, inputs)
        if end is not None and 20.0 < margin < MERGE_TERMINAL_RAMP_WH / 2.0:
            edge_kwh = d1
            edge_margin = margin
            break
        milli += 5
    assert edge_kwh is not None, "scene never crossed a sub-ramp stressed clip"
    assert 0.0 < edge_margin < MERGE_TERMINAL_RAMP_WH

    def t_star(day1_kwh: float, soc: float) -> float:
        inputs = build_slots(
            config, now, soc, [day1_kwh, 12.0, 2.0], load_states=NIGHT_STATE
        )
        thr, _base = search_threshold(config, inputs)
        return thr

    center = t_star(edge_kwh, base_soc)
    # +-1 Wh forecast jitter (0.001 kWh) and +-0.1 % SOC jitter around the edge.
    jittered = [
        t_star(edge_kwh + dk, base_soc + ds)
        for dk in (-0.001, 0.0, 0.001)
        for ds in (-0.1, 0.0, 0.1)
    ]
    # The whole neighbourhood collapses onto one threshold — no 20<->61 flip.
    assert max(jittered) - min(jittered) <= 1.0, (
        f"T* bistable around the clip onset: {sorted(set(jittered))}"
    )
    assert abs(center - jittered[0]) <= 1.0


def test_ramped_stress_floor_follows_stressed_crossover():
    """R13 (F3 unit): the Z4 buffer ramps with the remaining stressed deficit
    — a slot 1 h before the stressed crossover needs only ~a percent of
    buffer, an evening slot with 8 h of dark deficit keeps the full buffer,
    no crossover ahead keeps it too, and STRESSED (not nominal) PV decides."""
    from core.optimize import _ramped_stress_floors

    config = SystemConfig(control=NIGHT_CONTROL)  # capacity 5000, buffer 5
    inverter_min = config.control.inverter_min_soc_percent
    full_buffer = config.control.soc_buffer_percent

    def slot(i, hour, pv):
        return HourSlot(
            index=i,
            start=datetime(2026, 7, 12, hour, 0),
            duration=1.0,
            hour_of_day=hour,
            pv_wh=pv,
            ac_wh=100.0,
            dc_wh=0.0,
        )

    # Slots 0..7: dark night (100 Wh deficit each); slot 8: strong PV.
    slots = tuple(slot(i, (21 + i) % 24, 0.0) for i in range(8)) + (slot(8, 5, 1000.0),)
    inputs = PlanInputs(now=slots[0].start, start_soc_percent=50.0, slots=slots)
    floors = _ramped_stress_floors(config, inputs, [1.0] * len(slots))
    # Evening slot: 8 x 100 Wh deficit = 16 % of 5 kWh -> clamped to the full
    # buffer; the slot 1 h before the crossover needs only 100/5000 = 2 %.
    assert floors[0] == inverter_min + full_buffer
    assert abs(floors[7] - (inverter_min + 2.0)) < 1e-9
    assert floors[8] == inverter_min  # at the crossover: no deficit left

    # No crossover ahead (all-dark horizon): the full static buffer holds.
    dark = tuple(slot(i, (21 + i) % 24, 0.0) for i in range(6))
    dark_inputs = PlanInputs(now=dark[0].start, start_soc_percent=50.0, slots=dark)
    dark_floors = _ramped_stress_floors(config, dark_inputs, [1.0] * 6)
    assert all(f == inverter_min + full_buffer for f in dark_floors)

    # Stressed — not nominal — PV decides: nominal 300 Wh would cover the
    # 100 Wh consumption, but stress 0.2 turns it into a 40 Wh deficit slot.
    stressed_slots = (slot(0, 4, 300.0), slot(1, 5, 1000.0))
    s_inputs = PlanInputs(
        now=stressed_slots[0].start, start_soc_percent=50.0, slots=stressed_slots
    )
    s_floors = _ramped_stress_floors(config, s_inputs, [0.2, 1.0])
    assert abs(s_floors[0] - (inverter_min + 100.0 * 40.0 / 5000.0)) < 1e-9


# ---------------------------------------------------------------------------
# F-STRICT-SURPLUS (operator decision 2026-07-19): loads never buy import
# (R1, absolute artifact slack), never book planned-grid-fed / cutoff-riding
# slots (R2, planner-G4), and bets settle at the true refill (R3).
# docs/F-STRICT-SURPLUS.md; live incident: the 2026-07-19 card.
# ---------------------------------------------------------------------------


def test_r2_pv_served_inverter_off_allowed_grid_fed_and_cutoff_refused():
    """R2 (with the HIGH-fix, gates review 2026-07-19): a booking is refused
    iff its slot is GRID-FED (inverter off AND PV cannot cover the AC load) or
    touches the cutoff (either slot endpoint <= inverter_min). A PV-covered
    inverter-off slot — the full-battery hoard regime, where T*=soc_max makes
    inverter_on False on EVERY slot — is PV-served with zero import and MUST be
    allowed: the earlier inverter_on-only R2 vetoed it and thereby disabled the
    whole allocator on hoard days, re-exporting multi-kWh."""
    deh = SurplusLoad(
        load_id="deh",
        name="E",
        nominal_power_w=400.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
    )
    cfg = SystemConfig(
        control=ControlParams(),
        loads=(deh,),
        battery=BatteryParams(capacity_wh=5000.0, soc_max_percent=95.0),
        ac_profile=LoadProfile(100.0, 0.0),
        dc_profile=LoadProfile(0.0, 0.0),
    )
    start = datetime(2026, 7, 20, 8, 0)

    def slot(i, hour, pv, ac=100.0):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=0.0,
        )

    # (A) HOARD: battery pinned at soc_max, T*=soc_max -> inverter OFF on every
    # slot, yet strong PV exports. The load is PV-served; R2 must allow it.
    slots = tuple(slot(i, 8 + i, 2500) for i in range(5))
    inputs = PlanInputs(
        now=start,
        start_soc_percent=95.0,
        slots=slots,
        load_states=(SurplusLoadState(load_id="deh"),),
    )
    base = simulate(cfg, inputs, 95.0)
    assert all(not f.inverter_on for f in base.flows)  # hoard: inverter off
    assert base.total_export_wh > 1000.0  # real surplus to absorb
    plans, _extra, traj = allocate_loads(cfg, inputs, 95.0, base)
    assert plans[0].planned_energy_wh > 100.0, (
        "R2 must not disable the allocator when PV serves an inverter-off slot"
    )
    inv_min = cfg.control.inverter_min_soc_percent
    for j, on in enumerate(plans[0].schedule):
        if on:
            f, s = traj.flows[j], inputs.slots[j]
            assert f.soc_end_percent > inv_min and f.soc_start_percent > inv_min
            assert s.pv_wh + 1e-6 >= s.ac_wh + f.extra_ac_wh  # PV-served, not grid-fed

    # (B) CUTOFF: a slot the battery ENTERS below the cutoff (soc_start < 20)
    # must be refused even though its PV covers the load and it ends above 20 —
    # the executor's G4 would not actuate a load at SOC <= 20 (phantom energy).
    cut = (
        slot(0, 5, 0, ac=200),  # night: drains toward the floor
        slot(1, 6, 0, ac=200),
        slot(2, 7, 3000),  # dawn clip: refills from below cutoff, would export
        slot(3, 8, 3000),
    )
    cut_inputs = PlanInputs(
        now=start,
        start_soc_percent=22.0,
        slots=cut,
        load_states=(SurplusLoadState(load_id="deh"),),
    )
    cbase = simulate(cfg, cut_inputs, 20.0)
    assert (
        cbase.flows[2].soc_start_percent <= inv_min + 1e-6
    )  # enters slot 2 sub-cutoff
    cplans, _e, ctraj = allocate_loads(cfg, cut_inputs, 20.0, cbase)
    assert not cplans[0].schedule[2], "R2 must refuse a slot entered below the cutoff"


def test_r4_prevented_export_reports_the_no_loads_counterfactual():
    """R4: PlanResult.prevented_export_by_day_wh = max(0, base - alloc) per day,
    both PRE support-escalation — the export the loads prevented that day, the
    counterfactual behind the dashboard's prevented_export_kwh. It must be
    non-negative, positive where loads absorbed, and never exceed the base's
    own per-day export."""
    now = datetime(2026, 7, 3, 21, 0)
    cfg = _predrain_config(ratio=0.0, alpha=1.0, beta=1.0)
    inputs = build_slots(cfg, now, 84.0, [0.0, 13.0, 11.0])
    result = plan(cfg, inputs)
    _thr, base = search_threshold(cfg, inputs)

    base_by_day: dict[str, float] = {}
    for slot, flow in zip(inputs.slots, base.flows, strict=True):
        day = slot.start.date().isoformat()
        base_by_day[day] = base_by_day.get(day, 0.0) + flow.grid_export_wh
    prevented = result.prevented_export_by_day_wh
    assert set(prevented) == set(base_by_day)
    for day, wh in prevented.items():
        assert 0.0 <= wh <= base_by_day[day] + 1e-6  # bounded by base export
    assert sum(prevented.values()) > 0.0  # loads absorbed some export

    # It equals base minus the ALLOCATION (pre-escalation) export, not the
    # final trajectory — with no support configured the two coincide here.
    final: dict[str, float] = {}
    for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):
        day = slot.start.date().isoformat()
        final[day] = final.get(day, 0.0) + flow.grid_export_wh
    for day in base_by_day:
        assert abs(prevented[day] - max(0.0, base_by_day[day] - final[day])) < 1e-6


def test_r4_prevented_export_uses_pre_escalation_alloc_not_final():
    """R4 discriminator (re-review finding: the pre-escalation capture was
    unpinned). With a forced 48 V support PSU active, the FINAL trajectory
    exports more than the pre-escalation allocation (the PSU lifts SOC and
    fills sooner), so `prevented_export_by_day_wh` computed from `alloc_traj`
    (pre-escalation) must DIFFER from the post-escalation `base - final` — a
    maintainer swapping `alloc_traj` for the final trajectory would deflate the
    counterfactual and this test would catch it."""
    from core.model import SupportParams

    deh = SurplusLoad(
        load_id="deh",
        name="E",
        nominal_power_w=400.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
    )
    cfg = SystemConfig(
        control=replace(
            ControlParams(),
            predrain_pv_confidence=0.7,
            upper_pv_reserve=1.2,
            strong_pv_cutoff_w=200.0,
        ),
        loads=(deh,),
        battery=BatteryParams(capacity_wh=5000.0, soc_max_percent=95.0),
        support=SupportParams(configured=True, dc48_forced_on=True, dc48_power_w=300.0),
        ac_profile=LoadProfile(200.0, 100.0, 6, 22),
        dc_profile=LoadProfile(300.0, 0.0, 0, 0),
    )
    now = datetime(2026, 7, 19, 8, 0)
    pv = {
        datetime(2026, 7, 19, h, 0): float(w)
        for h, w in {
            9: 1600,
            10: 1800,
            11: 1900,
            12: 1800,
            13: 1500,
            14: 900,
            15: 400,
        }.items()
    }
    inputs = build_slots(
        cfg,
        now,
        50.0,
        [sum(pv.values()) / 1000.0],
        load_states=(SurplusLoadState(load_id="deh"),),
        pv_hourly=pv,
    )
    result = plan(cfg, inputs)
    _thr, base = search_threshold(cfg, inputs)

    # The forced PSU is actually active in the final trajectory (else vacuous).
    assert any(f.support_dc48 for f in result.trajectory.flows)
    day = "2026-07-19"
    base_exp = sum(f.grid_export_wh for f in base.flows)
    final_exp = sum(f.grid_export_wh for f in result.trajectory.flows)
    post_would_be = max(0.0, base_exp - final_exp)
    # The reported (pre-escalation) prevented export is strictly larger than
    # the post-escalation figure the swap would yield.
    assert result.prevented_export_by_day_wh[day] > post_would_be + 100.0


def _card_20260719_plan():
    """Compact reconstruction of the 2026-07-19 operator-card geometry (the
    F-STRICT-SURPLUS motivating incident): Sunday clips midday WITHOUT loads,
    Monday clips ~2 kWh even WITH loads, Tuesday moderate; Victron 5 kWh,
    T* 20, alpha 0.7 / beta 1.2 scalar fallback, learned powers 505/433 W."""
    fossibot = SurplusLoad(
        load_id="fossibot",
        name="F",
        nominal_power_w=300.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
        energy_limited=True,
        capacity_wh=2000.0,
        target_soc_percent=90.0,
    )
    deh = SurplusLoad(
        load_id="dehumidifier",
        name="E",
        nominal_power_w=400.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
    )
    control = ControlParams(
        inverter_min_soc_percent=20.0,
        soc_buffer_percent=5.0,
        hysteresis_percent=1.0,
        predrain_pv_confidence=0.7,
        upper_pv_reserve=1.2,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        battery=BatteryParams(
            capacity_wh=5000.0, soc_min_percent=5.0, soc_max_percent=95.0
        ),
        control=control,
        ac_profile=LoadProfile(200.0, 100.0, 6, 22),
        dc_profile=LoadProfile(35.0, 0.0, 0, 0),
        loads=(fossibot, deh),
    )
    states = (
        SurplusLoadState(load_id="fossibot", soc_percent=50.0, learned_power_w=505.0),
        SurplusLoadState(load_id="dehumidifier", learned_power_w=433.0),
    )
    sun = {
        9: 1300,
        10: 1500,
        11: 1450,
        12: 1300,
        13: 800,
        14: 300,
        15: 150,
        16: 80,
        17: 40,
    }
    mon = {
        5: 80,
        6: 400,
        7: 900,
        8: 1400,
        9: 1750,
        10: 1900,
        11: 1950,
        12: 1850,
        13: 1500,
        14: 900,
        15: 400,
        16: 180,
        17: 70,
    }
    tue = {
        6: 100,
        7: 300,
        8: 600,
        9: 800,
        10: 900,
        11: 950,
        12: 900,
        13: 700,
        14: 400,
        15: 200,
        16: 100,
    }
    pv_hourly = {}
    for day, shape in ((19, sun), (20, mon), (21, tue)):
        for hour, wh in shape.items():
            pv_hourly[datetime(2026, 7, day, hour, 0)] = float(wh)
    daily = [sum(sun.values()) / 1e3, sum(mon.values()) / 1e3, sum(tue.values()) / 1e3]
    now = datetime(2026, 7, 19, 9, 0)
    inputs = build_slots(cfg, now, 44.0, daily, load_states=states, pv_hourly=pv_hourly)
    return cfg, inputs, plan(cfg, inputs)


def test_card_20260719_strict_invariants_hold_end_to_end():
    """The incident card under F-STRICT-SURPLUS, end to end: (a) R1 — the
    whole allocation adds at most the artifact slack of import over the
    no-loads base; (b) R2 — no slot carrying booked load energy is GRID-FED
    (inverter off AND PV below the AC load) or touches the 20 % cutoff at
    either endpoint; (c) the SOC touches soc_max on Sunday (the operator's
    expectation: battery first), with Sunday hosting only pass-1 runs —
    direct-surplus bookings and, for the energy-limited fossibot with budget
    left, F-PEAK-FILL at-max top-ups (the battery-buffered variant of the
    same pass-1 machinery, judged by the identical (a)-(c) invariants) — the
    08:00-12:00 beta-insurance morning bets and the pre-dawn cutoff-riding
    bookings of the v0.14.0 live plan are gone; (d) Monday's clip is still
    absorbed by pass-1 runs (rescue not suppressed)."""
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    cfg, inputs, result = _card_20260719_plan()
    assert result.threshold_percent == 20.0

    _thr, base = search_threshold(cfg, inputs)
    added = result.trajectory.total_import_wh - base.total_import_wh
    assert added <= IMPORT_ARTIFACT_SLACK_WH + 1e-6  # (a)

    inv_min = cfg.control.inverter_min_soc_percent
    for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):  # (b)
        if flow.extra_ac_wh > 0.0:
            assert flow.soc_start_percent > inv_min and flow.soc_end_percent > inv_min
            # not grid-fed: inverter on, or PV covers the AC load in that slot
            assert (
                flow.inverter_on or slot.pv_wh + 1e-6 >= slot.ac_wh + flow.extra_ac_wh
            )

    sunday = datetime(2026, 7, 19).date()
    sunday_max = max(
        f.soc_end_percent
        for s, f in zip(inputs.slots, result.trajectory.flows, strict=True)
        if s.start.date() == sunday
    )
    assert sunday_max >= cfg.battery.soc_max_percent - 0.1  # (c) battery first
    energy_limited = {ld.load_id for ld in cfg.loads if ld.energy_limited}
    topups = 0
    for lp in result.load_plans:
        for alloc, reason in zip(lp.allocations, lp.reasons, strict=True):
            if inputs.slots[alloc[0]].start.date() == sunday:
                # Pass 1 only; the at-max top-up wording is legitimate for
                # energy-limited loads (F-PEAK-FILL R2), everything else stays
                # direct surplus.
                assert alloc[2] == 1
                if lp.load_id in energy_limited and "at-max top-up" in reason:
                    topups += 1
                else:
                    assert "direct surplus" in reason
    assert topups > 0, "the card's full midday hosts at-max top-ups now"

    monday = datetime(2026, 7, 20).date()
    deh_plan = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    mon_pass1 = [
        a
        for a in deh_plan.allocations
        if a[2] == 1 and inputs.slots[a[0]].start.date() == monday
    ]
    assert mon_pass1, "Monday's clip must still be absorbed by pass-1 runs"  # (d)


def test_crossday_daytime_bet_predicate():
    """R6 (operator 2026-07-19): a DAYLIGHT (pv_wh > 0) pass-2 bet whose refill
    lands on a LATER calendar day is a forbidden cross-day daytime pre-drain;
    night slots (pv_wh == 0) keep the F-NIGHT-RESCUE cross-day carve-out and a
    same-day refill is always fine."""
    from datetime import date

    sun, mon = date(2026, 7, 19), date(2026, 7, 20)
    # Daylight + refill next day -> forbidden (the Sunday-14:00-for-Monday case).
    assert _crossday_daytime_bet(sun, mon, is_daylight=True)
    # Daylight + same-day refill -> allowed (pre-charge before a same-day peak).
    assert not _crossday_daytime_bet(sun, sun, is_daylight=True)
    # Night/pre-dawn slot (pv == 0) + next-day refill -> allowed
    # (F-NIGHT-RESCUE night pre-drain before a clip day).
    assert not _crossday_daytime_bet(sun, mon, is_daylight=False)
    assert not _crossday_daytime_bet(sun, sun, is_daylight=False)


def test_r6_suppresses_daylight_afternoon_crossday_below_strong_cutoff():
    """R6 keys on DAYLIGHT (pv_wh > 0), NOT the strong-PV window (re-review
    finding: keying on `in_window` let the afternoon taper below
    strong_pv_cutoff_w leak the cross-day bet one slot past the window edge —
    exactly where the live 14:00 slot sits). Geometry: Sunday tops off midday
    then tapers below the 200 W cutoff (14:00-19:00 = 180..20 W), Monday clips
    enormously (saturated), so the latest-first walk reaches back to Sunday's
    afternoon-taper slots. Those slots are DAYLIGHT (pv > 0) but NOT in-window;
    R6 must still suppress them. Discriminating (mutation-tight at plan level):
    with R6 disabled the same afternoon bets book.

    Re-scoped to an ENERGY-LIMITED load (F-PREDRAIN-BLOCK, operator
    2026-08-01): pass 2's slot-wise bets — and with them the R6 guard — are
    its class's machinery now; the continuous class's pre-drain moved to pass
    3's today-only block, so it can never bet a Sunday afternoon for Monday
    at all. The night half of the old scenario is gone with it: energy-
    limited loads never book zero-PV slots anyway (daylight rule), and the
    predicate's night branch stays pinned by
    test_crossday_daytime_bet_predicate."""
    import core.optimize as opt

    fb = SurplusLoad(
        load_id="fb",
        name="F",
        nominal_power_w=300.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
        energy_limited=True,
        capacity_wh=8000.0,
        target_soc_percent=100.0,
    )
    cfg = SystemConfig(
        control=replace(
            ControlParams(),
            predrain_pv_confidence=0.7,
            upper_pv_reserve=1.2,
            strong_pv_cutoff_w=200.0,
        ),
        loads=(fb,),
        battery=BatteryParams(
            capacity_wh=18000.0, soc_min_percent=5.0, soc_max_percent=95.0
        ),
        ac_profile=LoadProfile(300.0, 100.0, 6, 22),
        dc_profile=LoadProfile(35.0, 0.0, 0, 0),
    )
    now = datetime(2026, 7, 19, 8, 0)
    sun_pv = {
        9: 1500,
        10: 1900,
        11: 2000,
        12: 1800,
        13: 1200,
        14: 180,
        15: 150,
        16: 120,
        17: 90,
        18: 50,
        19: 20,
    }  # afternoon taper < 200 W
    mon_pv = {
        3: 150,
        4: 400,
        5: 900,
        6: 1800,
        7: 3000,
        8: 4200,
        9: 5000,
        10: 5000,
        11: 5000,
        12: 5000,
        13: 4500,
        14: 3400,
        15: 1800,
        16: 800,
        17: 300,
    }
    pv = {}
    for day, shape in ((19, sun_pv), (20, mon_pv)):
        for h, w in shape.items():
            pv[datetime(2026, 7, day, h, 0)] = float(w)
    inputs = build_slots(
        cfg,
        now,
        78.0,
        [sum(sun_pv.values()) / 1e3, sum(mon_pv.values()) / 1e3],
        load_states=(SurplusLoadState(load_id="fb", soc_percent=0.0),),
        pv_hourly=pv,
    )
    sunday = datetime(2026, 7, 19).date()

    def daylight_afternoon_crossday(result):
        return [
            inputs.slots[a[0]].start.hour
            for lp in result.load_plans
            for a, r in zip(lp.allocations, lp.reasons, strict=True)
            if inputs.slots[a[0]].start.date() == sunday
            and 14 <= inputs.slots[a[0]].start.hour <= 19
            and inputs.slots[a[0]].pv_wh > 0.0
            and "otherwise-lost export" in r
        ]

    # R6 ON (current code): no daylight afternoon-taper cross-day bet.
    result = plan(cfg, inputs)
    assert not daylight_afternoon_crossday(result), (
        "R6 must suppress the daylight afternoon-taper cross-day pre-drain"
    )
    # The afternoon-taper slots are below the strong cutoff (NOT in-window), so
    # only the daylight-based R6 catches them — an in_window-based R6 would not.
    for s in inputs.slots:
        if s.start.date() == sunday and 14 <= s.start.hour <= 19:
            assert 0.0 < s.pv_wh < 200.0  # daylight yet sub-cutoff

    # Discriminating: disable R6 -> the daylight afternoon bets reappear.
    orig = opt._crossday_daytime_bet
    opt._crossday_daytime_bet = lambda *a, **k: False
    try:
        result_off = plan(cfg, inputs)
    finally:
        opt._crossday_daytime_bet = orig
    assert daylight_afternoon_crossday(result_off), (
        "geometry must produce the daylight afternoon cross-day bet without R6"
    )


def test_slot_serviceable_grid_fed_and_both_cutoff_endpoints():
    """R2 per-slot rule pinned directly (re-review finding: the soc_end cutoff
    endpoint was untested — a floor-only-on-entry mutation passed the suite).
    Reject iff grid-fed (inverter off AND PV below the AC load) OR EITHER SOC
    endpoint touches the cutoff."""
    from types import SimpleNamespace

    def flow(start, end, inv, extra=400.0):
        return SimpleNamespace(
            soc_start_percent=start,
            soc_end_percent=end,
            inverter_on=inv,
            extra_ac_wh=extra,
        )

    def slot(pv, ac=100.0):
        return SimpleNamespace(pv_wh=pv, ac_wh=ac)

    fl = 20.0
    # Healthy: inverter on, both endpoints well above cutoff -> serviceable.
    assert _slot_serviceable(flow(60, 55, True), slot(0), fl)
    # Hoard: inverter OFF but PV covers the load -> PV-served, serviceable.
    assert _slot_serviceable(flow(95, 95, False), slot(2000), fl)
    # Grid-fed: inverter off AND PV below the AC load -> reject.
    assert not _slot_serviceable(flow(60, 55, False), slot(100), fl)
    # Cutoff ENTRY: soc_start at/below the cutoff (even if it recovers) -> reject.
    assert not _slot_serviceable(flow(19, 30, False), slot(2000), fl)
    assert not _slot_serviceable(flow(20, 40, True), slot(0), fl)
    # Cutoff EXIT: inverter ON, battery-served discharge rides soc_end to the
    # floor (soc_start > cutoff) -> reject. THIS is the endpoint the re-review
    # found unpinned; a floor-only-on-entry mutation returns True here.
    assert not _slot_serviceable(flow(30, 19, True), slot(0), fl)
    assert not _slot_serviceable(flow(30, 20, True), slot(0), fl)


def test_z4_reject_relief_clause():
    """The Z4 veto's relief conjunct pinned directly (medium review finding: the
    rewrite left it untested, so a floor-only mutation passed the suite). Reject
    iff the stressed windowed reserve breaks the floor AND is worse than the
    accepted-series windowed min."""
    floor = 25.0
    # Above the floor -> never a veto, regardless of the baseline.
    assert not _z4_reject(30.0, floor, 22.0)
    assert not _z4_reject(30.0, floor, 40.0)
    # Below the floor AND worse than the base -> veto (the bet deepened the dip).
    assert _z4_reject(19.0, floor, 22.0)
    # Below the floor but NOT worse than the base -> RELIEF, must NOT veto.
    # (A floor-only gate — the mutant — would return True here and wrongly veto.)
    assert not _z4_reject(19.0, floor, 19.0)
    assert not _z4_reject(19.0, floor, 18.0)


def test_r5_plan_reaches_soc_max_on_every_day_the_base_does():
    """F-STRICT-SURPLUS R5 (operator 2026-07-19): pre-conditioning is welcome,
    but the plan must still reach soc_max on every day the no-loads base
    reaches it — a bet that stops the fill (the card's 77 % peak) is refused.
    Contract check over the incident geometry (the DISCRIMINATING pin — a
    geometry where R5 alone protects the fill — is
    test_r5_vetoes_a_max_robbing_bet_via_the_gate_directly): every base-max day
    is a plan-max day."""
    cfg, inputs, result = _card_20260719_plan()
    _thr, base = search_threshold(cfg, inputs)
    soc_full = cfg.battery.soc_max_percent - 0.1

    def max_days(traj):
        days = set()
        for slot, flow in zip(inputs.slots, traj.flows, strict=True):
            if flow.soc_end_percent >= soc_full:
                days.add(slot.start.date())
        return days

    base_max = max_days(base)
    plan_max = max_days(result.trajectory)
    assert base_max, "geometry must have at least one base-max day to protect"
    assert base_max <= plan_max, (
        f"R5 violated: base reaches max on {base_max}, plan only on {plan_max}"
    )


def test_r5_vetoes_a_max_robbing_bet_via_the_gate_directly():
    """R5 unit: preserves_daily_max is the discriminant. A single-day clip
    geometry where the no-loads base reaches soc_max; a hypothetical extra-AC
    series that drains the morning so the battery never reaches max that day
    must be rejected by the gate, while the no-loads (all-zero) series passes.
    Exercised by building the two trial trajectories and the gate's own rule."""
    deh = SurplusLoad(
        load_id="deh",
        name="E",
        nominal_power_w=2000.0,
        battery_tolerance=1.0,
        min_runtime_min=60,
    )
    cfg = SystemConfig(
        control=replace(
            ControlParams(), predrain_pv_confidence=1.0, upper_pv_reserve=1.0
        ),
        loads=(deh,),
        battery=BatteryParams(capacity_wh=5000.0, soc_max_percent=95.0),
        ac_profile=LoadProfile(0.0, 0.0),
        dc_profile=LoadProfile(0.0, 0.0),
    )
    start = datetime(2026, 7, 4, 8, 0)

    def slot(i, hour, pv):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=hour,
            pv_wh=pv,
            ac_wh=0.0,
            dc_wh=0.0,
        )

    # A day that reaches soc_max at midday then declines (surplus margin small
    # enough that a heavy morning drain prevents the fill).
    slots = tuple(
        slot(i, 8 + i, pv) for i, pv in enumerate([1200, 1100, 700, 200, 100, 50])
    )
    inputs = PlanInputs(
        now=start,
        start_soc_percent=50.0,
        slots=slots,
        load_states=(SurplusLoadState(load_id="deh"),),
    )
    n = len(slots)
    threshold, base = search_threshold(cfg, inputs)
    soc_full = cfg.battery.soc_max_percent - 0.1
    assert base.max_soc_percent >= soc_full  # base reaches max

    # Drain the morning heavily (3 kWh over slots 0-1) so the battery never maxes.
    robbing = [0.0] * n
    robbing[0], robbing[1] = 2000.0, 1000.0
    robbed = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(robbing))
    assert robbed.max_soc_percent < soc_full  # the bet robs the fill

    # The gate's rule (base_max_days must all still reach soc_full) rejects it.
    assert any(f.soc_end_percent >= soc_full for f in base.flows)  # base maxes that day
    assert not any(
        f.soc_end_percent >= soc_full for f in robbed.flows
    )  # trial does not
    # -> preserves_daily_max would return False for `robbed`, True for the base.
    plans, _extra, traj = allocate_loads(cfg, inputs, threshold, base)
    assert traj.max_soc_percent >= soc_full  # the real plan preserves the fill


def test_energy_limited_priority_load_reaches_target_when_surplus_permits():
    """Operator 2026-07-19: the controllable battery storages (energy-limited,
    priority over the dehumidifier) must ALSO reach their target SOC — from
    surplus, never grid. With a gate-stop-capable powerstation (live: a
    charge-enable input_boolean) and ample surplus, it charges to its exact
    target via the final top-up quantum, BEFORE the dehumidifier, while the
    house battery still reaches its own max (R5)."""
    fossibot = SurplusLoad(
        load_id="fb",
        name="Fossibot",
        nominal_power_w=300.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
        energy_limited=True,
        capacity_wh=2000.0,
        target_soc_percent=90.0,
        gate_stop_capable=True,
    )
    deh = SurplusLoad(
        load_id="deh",
        name="E",
        nominal_power_w=400.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
    )
    cfg = SystemConfig(
        control=replace(
            ControlParams(),
            predrain_pv_confidence=0.7,
            upper_pv_reserve=1.2,
            strong_pv_cutoff_w=200.0,
        ),
        loads=(fossibot, deh),  # fossibot first = priority
        battery=BatteryParams(capacity_wh=5000.0, soc_max_percent=95.0),
        ac_profile=LoadProfile(200.0, 100.0, 6, 22),
        dc_profile=LoadProfile(35.0, 0.0, 0, 0),
    )
    now = datetime(2026, 7, 19, 8, 0)
    pv = {
        datetime(2026, 7, 19, h, 0): float(wh)
        for h, wh in {
            9: 1600,
            10: 1800,
            11: 1900,
            12: 1800,
            13: 1500,
            14: 900,
            15: 400,
        }.items()
    }
    states = (
        SurplusLoadState(
            load_id="fb", soc_percent=40.0
        ),  # needs (90-40)%*2000 = 1000 Wh
        SurplusLoadState(load_id="deh"),
    )
    inputs = build_slots(
        cfg, now, 50.0, [sum(pv.values()) / 1000.0], load_states=states, pv_hourly=pv
    )
    result = plan(cfg, inputs)
    fb_plan = next(p for p in result.load_plans if p.load_id == "fb")
    deh_plan = next(p for p in result.load_plans if p.load_id == "deh")
    _thr, base = search_threshold(cfg, inputs)

    remaining = SurplusLoadState(load_id="fb", soc_percent=40.0).remaining_energy_wh(
        fossibot
    )
    assert base.total_export_wh > 2000.0  # ample surplus for both
    assert fb_plan.planned_energy_wh >= remaining - 1.0  # fossibot REACHES target
    assert any("final top-up to target" in r for r in fb_plan.reasons)
    assert deh_plan.planned_energy_wh > 0.0  # dehumidifier gets the rest, after
    # house battery still reaches its own max (R5) and no grid import beyond slack
    assert result.max_soc_percent >= cfg.battery.soc_max_percent - 0.1
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert result.trajectory.total_import_wh - base.total_import_wh <= (
        IMPORT_ARTIFACT_SLACK_WH + 1e-6
    )


# ---------------------------------------------------------------------------
# Coverage-targeted edge branches (helpers + degenerate horizons)
# ---------------------------------------------------------------------------


def test_pv_windows_skips_zero_duration_slots():
    """A degenerate zero-duration slot must be skipped BEFORE the pv/duration
    division — it can never count as a strong-PV slot."""
    slots = (
        HourSlot(
            index=0,
            start=datetime(2026, 7, 4, 7),
            duration=0.0,
            hour_of_day=7,
            pv_wh=999999.0,
            ac_wh=0.0,
            dc_wh=0.0,
        ),
        _esc_slot(1, pv=300.0, dc=0.0, hour=8),
        _esc_slot(2, pv=300.0, dc=0.0, hour=9),
    )
    inputs = PlanInputs(
        now=datetime(2026, 7, 4, 7), start_soc_percent=50.0, slots=slots
    )
    assert pv_windows(inputs, 200.0, None) == {datetime(2026, 1, 1).date(): (1, 2)}


def test_pv_windows_end_hour_drops_window_starting_after_it():
    """The site override caps the absorption window at the last slot starting
    before `end_hour`; a day whose strong slots all begin at/after the
    override hour has NO window (night/cloudy slots keep only the nominal
    opportunity gate)."""
    slots = tuple(
        _esc_slot(i, pv=300.0 if hour >= 13 else 50.0, dc=0.0, hour=hour)
        for i, hour in enumerate(range(7, 18))
    )
    inputs = PlanInputs(
        now=datetime(2026, 7, 4, 7), start_soc_percent=50.0, slots=slots
    )
    day = datetime(2026, 1, 1).date()  # _esc_slot's hardcoded day
    assert pv_windows(inputs, 200.0, None) == {day: (6, 10)}
    assert pv_windows(inputs, 200.0, 12) == {}


def test_threshold_merge_probe_empty_horizon():
    """No slots -> no stressed clip exists -> (None, 0.0): full-horizon
    behaviour with zero margin."""
    inputs = PlanInputs(
        now=datetime(2026, 7, 4, 12, 0), start_soc_percent=55.0, slots=()
    )
    assert _threshold_merge_probe(SystemConfig(), inputs) == (None, 0.0)


def test_plan_empty_horizon():
    """An empty slot list is a legal (degenerate) plan: the summary fields
    read the start SOC, nothing is "to max", the inverter stays off."""
    inputs = PlanInputs(
        now=datetime(2026, 7, 4, 12, 0), start_soc_percent=55.0, slots=()
    )
    result = plan(SystemConfig(), inputs)
    assert result.trajectory.flows == ()
    assert result.min_soc_percent == 55.0
    assert result.max_soc_percent == 55.0
    assert result.hours_to_max_soc == 0
    assert result.inverter_on is False


def test_quantised_hours_without_min_runtime_is_whole_slot():
    """min_runtime_min = 0 (no dwell) means no quantisation: only the
    committed (whole-slot) candidate exists."""
    load = SurplusLoad(load_id="x", name="X", nominal_power_w=300.0, min_runtime_min=0)
    slot = HourSlot(
        index=0,
        start=datetime(2026, 7, 4),
        duration=0.3,
        hour_of_day=0,
        pv_wh=0.0,
        ac_wh=0.0,
        dc_wh=0.0,
    )
    assert _quantised_hours(load, slot) == [0.3]


def test_appliance_windows_skips_non_opportunistic():
    """An appliance without opportunistic_start never gets a start advisory —
    it is not even simulated."""
    washer = Appliance(
        appliance_id="washer",
        name="W",
        run_energy_wh=1000.0,
        run_duration_h=2.0,
        opportunistic_start=False,
    )
    config = SystemConfig(appliances=(washer,))
    result, _ = make_plan(config, datetime(2026, 7, 4, 10, 0), 90.0, [15.0, 15.0])
    assert result.appliance_windows == {}


def test_support_dc48_recovers_above_recovery_soc():
    """48 V hysteresis: once the SOC recovers to the recover level the PSU
    releases — it must not stay latched for the rest of the horizon."""
    cfg = SystemConfig(support=SupportParams(configured=True, native48_base_w=250.0))
    slots = (
        _esc_slot(0, pv=0.0, dc=300.0),  # native 48 V drain below the 5.5 % activate
        _esc_slot(1, pv=2000.0, dc=0.0, hour=1),  # strong PV: recovers >= 10 %
        _esc_slot(2, pv=0.0, dc=0.0, hour=2),  # stays high: 48 V released
    )
    inputs = PlanInputs(now=datetime(2026, 1, 1, 0), start_soc_percent=9.0, slots=slots)
    threshold = 100.0  # inverter parked off: isolates the DC path
    extra_ac = (0.0, 0.0, 0.0)
    base = simulate(cfg, inputs, threshold, extra_ac_wh=extra_ac)
    _dc24, dc48, _ = support_escalation(cfg, inputs, threshold, extra_ac, base)
    assert dc48 == (True, False, False)


def test_saturated_load_books_no_phantom_energy():
    """F5: a load whose power warning latched at ~0 W (full tank) plans at
    planning_power_w == 0 — every candidate is discarded as zero-energy in
    BOTH passes, so no phantom full-power slot enters the plan."""
    config = SystemConfig(loads=(DEHUMIDIFIER,))
    states = (SurplusLoadState(load_id="dehumidifier", saturated_power_w=0.0),)
    result, _inputs = make_plan(
        config, datetime(2026, 7, 4, 20, 0), 80.0, [0.0, 14.0, 12.0], states
    )
    plan_dehumid = result.load_plans[0]
    assert plan_dehumid.planned_energy_wh == 0.0
    assert not any(plan_dehumid.schedule)


def test_pass2_bet_breaking_buffer_floor_is_vetoed():
    """Z3 in the shared _gate_trial: a pre-drain bet whose trial trajectory
    dips below soc_min + buffer (AND below the no-bet plan's own minimum) is
    vetoed — even with a clip day coming that would refill it. The inverter
    floor is set to soc_min so the veto is provably Z3's, not the planner-G4
    serviceability floor (which would reject first otherwise)."""
    control = replace(ControlParams(), inverter_min_soc_percent=5.0)
    config = SystemConfig(control=control, loads=(DEHUMIDIFIER,))
    states = (SurplusLoadState(load_id="dehumidifier"),)
    # 35 % at 22:00: the bare night drain ends ~11 % (above the 10 % buffer
    # floor); any additional night bet would deepen it below the floor.
    result, _inputs = make_plan(
        config, datetime(2026, 7, 3, 22, 0), 35.0, [0.0, 14.0, 12.0], states
    )
    lp = result.load_plans[0]
    # No night slot (22:00-06:00, slots 0-8) is booked — every candidate
    # there broke the buffer floor in the trial re-simulation.
    assert not any(lp.schedule[:9])
    # Bets ARE still placed where the floor holds (daylight pre-drain).
    assert lp.planned_energy_wh > 0.0
    # And the accepted plan never falls through the inverter floor.
    assert result.min_soc_percent >= 5.0


def test_energy_limited_pass2_never_spills_into_night():
    """Daylight rule, spill guard: an energy-limited load's min-runtime
    commitment starting in the last daylight slot must not spill into the
    following zero-PV (night) slot — the whole candidate is dropped, shorter
    quantised candidates keep their chance (here: none exists, so the slot
    stays unbooked)."""
    fossibot = SurplusLoad(
        load_id="fossibot",
        name="F",
        nominal_power_w=300.0,
        energy_limited=True,
        capacity_wh=20000.0,  # large enough that rem never saturates the gate
        min_runtime_min=60,
    )
    config = SystemConfig(loads=(fossibot,))
    states = (SurplusLoadState(load_id="fossibot", soc_percent=0.0),)
    # 17:30: slot 0 (17:30-18:00, 40 Wh PV) is daylight, slot 1 (18:00) is
    # night. The 1 h commitment would spill 30 min into the night slot.
    result, inputs = make_plan(
        config, datetime(2026, 7, 3, 17, 30), 80.0, [2.0, 14.0, 12.0], states
    )
    lp = result.load_plans[0]
    assert inputs.slots[0].pv_wh > 0.0 and inputs.slots[1].pv_wh <= 0.0
    assert not lp.schedule[0]
    # The invariant the guard protects: NO booked slot is a zero-PV slot.
    assert all(
        inputs.slots[i].pv_wh > 0.0 for i, booked in enumerate(lp.schedule) if booked
    )


def test_pass2_candidate_overlapping_pass1_booking_is_dropped():
    """The shared _spread_candidate never lets a min-runtime commitment
    double-book a slot the load already owns: a 2.5 h pass-2 candidate
    spilling into the pass-1 block is trimmed to a seamless single hour (F9),
    and nothing lands twice. Re-scoped to an ENERGY-LIMITED load
    (F-PREDRAIN-BLOCK): pass 2's candidates — and with them the overlap guard
    — are its class's machinery now. (Re-pinned 2026-08 for the standby
    accounting: the cleaned export trajectory — no standby subtracted from
    the PV balance — leaves more covered export, so pass 1 now books a
    FOURTH 2.5 h block at 12:00 the next morning; the overlap guard's
    contract is unchanged — both pass-2 bets are still seamless 1 h trims
    and nothing double-books.)"""
    elslow = SurplusLoad(
        load_id="elslow",
        name="EL slow",
        nominal_power_w=400.0,
        min_runtime_min=150,  # 2.5 h quantum -> every commitment spans 3 slots
        energy_limited=True,
        capacity_wh=20000.0,  # large enough that rem never saturates the gate
        target_soc_percent=100.0,
    )
    config = SystemConfig(loads=(elslow,))
    states = (SurplusLoadState(load_id="elslow", soc_percent=0.0),)
    result, inputs = make_plan(
        config, datetime(2026, 7, 4, 9, 0), 90.0, [8.0, 8.0], states
    )
    lp = result.load_plans[0]
    # Pinned allocation: two 2.5 h pass-1 blocks in the morning surplus, two
    # seamless 1 h pass-2 daylight bets plus two 2.5 h pass-1 blocks the next
    # morning — and NOTHING double-booked in between, where the overlap guard
    # dropped or trimmed the spilling candidates.
    assert [i for i, booked in enumerate(lp.schedule) if booked] == [
        0,
        1,
        2,
        3,
        4,
        5,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
    ]
    assert lp.planned_energy_wh == 4800.0
    # Nothing double-booked: run hours never exceed a slot's duration.
    assert all(
        run_h <= inputs.slots[i].duration + 1e-9 for i, run_h in enumerate(lp.run_hours)
    )


def test_pass2_bet_vetoed_by_z3_buffer_floor_inside_gate_trial():
    """Z3 inside the shared _gate_trial, reached by an energy-limited pass-2
    bet (F-PREDRAIN-BLOCK: pass 2 is its class's machinery now). The
    candidate passes Z2''/R2/R5, but its trial deepens the overnight dip
    below soc_min + buffer while the accepted series stays above it — vetoed
    before any pass-specific gate runs. Geometry: a big battery behind a
    charger-capped midday (the charge the bet steals can never be refilled)
    and a heavy overnight DC drain ending at ~10.3 % without the bet, ~8.8 %
    with it."""
    fb = SurplusLoad(
        load_id="fb",
        name="F",
        nominal_power_w=300.0,
        battery_tolerance=0.15,
        min_runtime_min=30,
        energy_limited=True,
        capacity_wh=8000.0,
        target_soc_percent=100.0,
    )
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        control=control,
        loads=(fb,),
        battery=BatteryParams(capacity_wh=18000.0),
        ac_profile=LoadProfile(50.0, 75.0, 6, 20),
        dc_profile=LoadProfile(50.0, 25.0, 6, 22),
    )
    start = datetime(2026, 7, 4, 7, 0)

    def slot(i, pv, ac=125.0):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=(start + timedelta(hours=i)).hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=450.0,
        )

    # 07:00 ramp (the bet candidate), 08:00-09:00 charger-capped, then a heavy
    # night tail into a cloudy day.
    slots = tuple(
        slot(i, pv) for i, pv in enumerate([500.0, 5000.0, 5000.0] + [0.0] * 15)
    )
    inputs = PlanInputs(
        now=start,
        start_soc_percent=32.0,
        slots=slots,
        load_states=(SurplusLoadState(load_id="fb", soc_percent=0.0),),
    )
    threshold, base = search_threshold(cfg, inputs)
    load_plans, extra, current = allocate_loads(cfg, inputs, threshold, base)
    lp = load_plans[0]
    # The pass-1 surplus hours book; the slot-0 bet (the only free daylight
    # slot) does NOT — Z3 vetoes it.
    assert lp.planned_energy_wh > 0.0
    assert all(a[0] != 0 for a in lp.allocations)

    # Gate-by-gate: the slot-0 candidate exactly as pass 2 evaluated it —
    # Z2'' passes (no added import), the slot is PV-served (R2), no base-max
    # day exists (R5 vacuous), and only Z3's rule rejects.
    buffer_floor = cfg.battery.soc_min_percent + cfg.control.soc_buffer_percent
    trial = list(extra)
    trial[0] += 300.0
    traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert (
        traj.total_import_wh - base.total_import_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    )
    assert inputs.slots[0].pv_wh >= inputs.slots[0].ac_wh + 300.0  # PV-served
    assert base.max_soc_percent < cfg.battery.soc_max_percent - 0.1  # R5 vacuous
    assert current.min_soc_percent >= buffer_floor  # the accepted series is legal
    assert traj.min_soc_percent < buffer_floor - 1e-6
    assert _degrades_min_soc(traj, current, buffer_floor)  # -> Z3 vetoes


def test_pass2_candidate_spilling_over_free_slot_into_own_booking_is_dropped():
    """_spread_candidate's None path (a genuine double-booking, not a seamless
    raster-edge continuation): the 2.5 h pass-2 commitment at slot 0 would
    cover [0, 1, 2] — slot 1 is a dark hour the energy-limited load can never
    book (daylight rule), slot 2 is already its own pass-1 block. Trimming to
    slot 0 would strand the run below its dwell with no continuation, so the
    candidate is dropped and slot 0 stays unbooked."""
    from core.optimize import _seamless_spill

    fb = SurplusLoad(
        load_id="fb",
        name="F",
        nominal_power_w=300.0,
        battery_tolerance=0.15,
        min_runtime_min=150,
        energy_limited=True,
        capacity_wh=20000.0,
        target_soc_percent=100.0,
    )
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(
        control=control,
        loads=(fb,),
        battery=BatteryParams(capacity_wh=5000.0),
        ac_profile=LoadProfile(50.0, 75.0, 6, 20),
        dc_profile=LoadProfile(50.0, 25.0, 6, 22),
    )
    start = datetime(2026, 7, 4, 7, 0)

    def slot(i, pv, ac=125.0, dc=75.0):
        return HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=(start + timedelta(hours=i)).hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=dc,
        )

    # 07:00 ramp, 08:00 dark, 09:00-11:00 surplus: pass 1 books only from
    # 09:00 on (the ramp hour's covered export stays below the soft gate).
    slots = tuple(
        slot(i, pv)
        for i, pv in enumerate([500.0, 0.0, 1500.0, 800.0, 2500.0, 0.0, 0.0])
    )
    inputs = PlanInputs(
        now=start,
        start_soc_percent=50.0,
        slots=slots,
        load_states=(SurplusLoadState(load_id="fb", soc_percent=0.0),),
    )
    threshold, base = search_threshold(cfg, inputs)
    load_plans, _extra, _current = allocate_loads(cfg, inputs, threshold, base)
    lp = load_plans[0]
    assert lp.allocations == ((2, 3, 1, 750.0),)  # slot 0 dropped, nothing else

    # The guard's verdict on the pass-2 state: covered [0, 1, 2] with slot 1
    # FREE and slot 2 booked -> None (a trim is only legal into an own
    # booking, F9).
    sched = [
        any(a[0] <= j < a[0] + a[1] for a in lp.allocations) for j in range(len(slots))
    ]
    trial, covered = _spread_energy([0.0] * len(slots), slots, 0, 300.0, 2.5)
    assert [j for j, _t in covered] == [0, 1, 2]
    assert _seamless_spill(covered, slots, 0, 300.0, [0.0] * len(slots), sched) is None


def test_saturated_energy_limited_load_offers_no_pass2_energy():
    """F5 for pass 2 (F-PREDRAIN-BLOCK: pass 2 is energy-limited-only now): a
    powerstation whose power warning latched at 0 W plans at
    planning_power_w == 0 — every pass-2 candidate is discarded as
    zero-energy, so even with strong export ahead nothing books."""
    fb = replace(FOSSIBOT_B, target_soc_percent=100.0)
    config = SystemConfig(loads=(fb,))
    states = (
        SurplusLoadState(load_id="fossibot_b", soc_percent=0.0, saturated_power_w=0.0),
    )
    result, _inputs = make_plan(config, datetime(2026, 7, 4, 8, 0), 92.0, [7.0], states)
    lp = result.load_plans[0]
    assert lp.planned_energy_wh == 0.0
    assert not any(lp.schedule)


# ---------------------------------------------------------------------------
# F-PEAK-FILL R2 (operator 2026-08-01): the at-max top-up — an energy-limited
# load with budget left may book a slot whose PV surplus does NOT cover its
# power (the house battery buffers the difference) as long as the slot
# carries positive PV surplus and ends inside the hysteresis band at the max
# line (soc_max - AT_MAX_TOPUP_BAND_PERCENT). Export spikes become load
# charge instead of the load pulsing at the max line; R5/Z2'' prove the dip
# refills from otherwise-lost export. Continuous loads are excluded by
# construction.
# ---------------------------------------------------------------------------

_TOPUP_FB = SurplusLoad(
    load_id="fb",
    name="F",
    nominal_power_w=300.0,
    battery_tolerance=0.15,
    min_runtime_min=30,
    energy_limited=True,
    capacity_wh=8000.0,
    target_soc_percent=100.0,
)
_TOPUP_TAIL = [(0.0, 50.0, 75.0)] * 5


def _topup_scene(spec, soc, loads, states, start=datetime(2026, 7, 4, 12, 0)):
    """Hand-built top-up scene: thin PV slots ahead of a strong 3 kWh slot
    (the merge engages, so T* drains and the inverter really serves the dip)
    plus a dark tail. `spec` entries are (pv_wh, ac_wh, dc_wh) per slot."""
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(control=control, loads=loads)
    slots = tuple(
        HourSlot(
            index=i,
            start=start + timedelta(hours=i),
            duration=1.0,
            hour_of_day=(start + timedelta(hours=i)).hour,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=dc,
        )
        for i, (pv, ac, dc) in enumerate(spec)
    )
    inputs = PlanInputs(
        now=start, start_soc_percent=soc, slots=slots, load_states=states
    )
    return cfg, inputs


def test_at_max_topup_books_at_max_with_budget_left():
    """R2: near house SOC max with PV surplus below the load's power, an
    energy-limited load with budget left books the slot anyway — battery-
    buffered, as an `at-max top-up (peak fill)` — because the dip stays
    inside the 5 % hysteresis band and provably refills (R5/Z2'')."""
    cfg, inputs = _topup_scene(
        [(250.0, 125.0, 75.0), (3000.0, 125.0, 75.0)] + _TOPUP_TAIL,
        93.0,
        (_TOPUP_FB,),
        (SurplusLoadState(load_id="fb", soc_percent=0.0),),
    )
    threshold, base = search_threshold(cfg, inputs)
    load_plans, _extra, _current = allocate_loads(cfg, inputs, threshold, base)
    lp = load_plans[0]
    assert lp.allocations == ((0, 1, 1, 150.0), (1, 1, 1, 300.0))
    topup = lp.reasons[0]
    assert topup.startswith("pass 1 @ ")
    assert "at-max top-up (peak fill)" in topup
    # Battery-buffered: the share is far above the 15 % tolerance, so the
    # bare soft gate would have skipped the slot outright.
    assert "battery share 100%" in topup
    assert "direct surplus" in lp.reasons[1]  # the strong slot stays direct


def test_at_max_topup_quantum_auto_limited_to_fit_the_band():
    """Quantum auto-limiting: the 60 min candidate dips below the band floor
    and is rejected, the 30 min candidate fits the band and books — the dwell
    quantum is never stretched past the band."""
    from core.optimize import AT_MAX_TOPUP_BAND_PERCENT, IMPORT_ARTIFACT_SLACK_WH

    cfg, inputs = _topup_scene(
        [(250.0, 125.0, 75.0), (3000.0, 125.0, 75.0)] + _TOPUP_TAIL,
        93.0,
        (_TOPUP_FB,),
        (SurplusLoadState(load_id="fb", soc_percent=0.0),),
    )
    threshold, base = search_threshold(cfg, inputs)
    load_plans, _extra, _current = allocate_loads(cfg, inputs, threshold, base)
    lp = load_plans[0]
    assert lp.allocations[0] == (0, 1, 1, 150.0)  # 30 min booked, not 60

    # Evaluate both quanta at slot 0 exactly as pass 1 did (slot 0 is the
    # first candidate of the ascending walk -> the all-zero series).
    floor = cfg.battery.soc_max_percent - AT_MAX_TOPUP_BAND_PERCENT
    for commit_h, fits in ((1.0, False), (0.5, True)):
        trial = [0.0] * len(inputs.slots)
        trial[0] += _TOPUP_FB.nominal_power_w * commit_h
        traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
        # The hard gates pass for BOTH quanta (battery-served dip that
        # refills at the strong slot; no import) — only the band discriminates.
        assert (
            traj.total_import_wh - base.total_import_wh
            <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
        )
        assert (traj.flows[0].soc_end_percent >= floor - 1e-6) == fits


def test_at_max_topup_needs_budget_and_the_energy_limited_class():
    """Scope: no top-up without remaining budget (target reached -> the
    saturation gate skips the load entirely), and never for a CONTINUOUS
    load — in the same scene its slot-0 coverage is the pass-3 pre-drain
    block, not a pass-1 top-up."""
    cfg, inputs = _topup_scene(
        [(250.0, 125.0, 75.0), (3000.0, 125.0, 75.0)] + _TOPUP_TAIL,
        93.0,
        (_TOPUP_FB, DEHUMIDIFIER),
        (
            SurplusLoadState(load_id="fb", soc_percent=100.0),  # rem == 0
            SurplusLoadState(load_id="dehumidifier"),
        ),
    )
    result = plan(cfg, inputs)
    fb_plan = next(p for p in result.load_plans if p.load_id == "fb")
    assert fb_plan.allocations == ()
    deh_plan = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    assert deh_plan.planned_energy_wh > 0.0
    assert all("at-max top-up" not in r for r in deh_plan.reasons)
    # The continuous load's slot-0 coverage is the pass-3 block machinery.
    assert any(a[2] == 3 for a in deh_plan.allocations)


def test_at_max_topup_needs_positive_pv_surplus():
    """No top-up on a slot whose PV does not cover the house load
    (pv <= ac+dc): the PV-surplus predicate vetoes a dip the band itself
    would admit — with a few Wh more PV the identical slot books."""
    from core.optimize import AT_MAX_TOPUP_BAND_PERCENT

    def run(pv0):
        cfg, inputs = _topup_scene(
            [(pv0, 125.0, 75.0), (3000.0, 125.0, 75.0)] + _TOPUP_TAIL,
            94.0,
            (_TOPUP_FB,),
            (SurplusLoadState(load_id="fb", soc_percent=0.0),),
        )
        threshold, base = search_threshold(cfg, inputs)
        plans, _extra, _current = allocate_loads(cfg, inputs, threshold, base)
        return plans[0], cfg, inputs, threshold, base

    lp, cfg, inputs, threshold, base = run(190.0)
    assert all("at-max top-up" not in r for r in lp.reasons)
    # The band would have admitted the dip — the PV predicate is the veto.
    assert inputs.slots[0].pv_wh <= (inputs.slots[0].ac_wh + inputs.slots[0].dc_wh)
    trial = [0.0] * len(inputs.slots)
    trial[0] += 150.0
    traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    assert (
        traj.flows[0].soc_end_percent
        >= cfg.battery.soc_max_percent - AT_MAX_TOPUP_BAND_PERCENT - 1e-6
    )
    lp2, *_ = run(210.0)
    assert any("at-max top-up" in r for r in lp2.reasons)


def test_at_max_topup_unpaid_dip_is_vetoed_by_z2pp():
    """Z2'': an unpaid dip is not booked. The dusk slot sits inside the band
    and the day maxed before it (R5 fine), but the hard night after leaves no
    export to refill the drain, so the trial's import exceeds the artifact
    slack and the candidate dies in the hard gate stack. (The fb commits
    whole hours only — min_runtime 60 — so no smaller quantum slips under
    the cap.)"""
    from core.optimize import AT_MAX_TOPUP_BAND_PERCENT, IMPORT_ARTIFACT_SLACK_WH

    fb60 = replace(_TOPUP_FB, min_runtime_min=60)
    cfg, inputs = _topup_scene(
        [(3000.0, 125.0, 75.0)] * 3
        + [(350.0, 125.0, 75.0)]
        + [(0.0, 125.0, 400.0)] * 10,
        95.0,
        (fb60,),
        (SurplusLoadState(load_id="fb", soc_percent=0.0),),
    )
    threshold, base = search_threshold(cfg, inputs)
    load_plans, extra, _current = allocate_loads(cfg, inputs, threshold, base)
    lp = load_plans[0]
    assert all(a[0] != 3 for a in lp.allocations)  # the dusk slot is refused

    # Gate-by-gate at the dusk slot: the band passes, R5 passes (the day
    # maxed before the dip), Z2'' is the veto.
    trial = list(extra)  # the pass-1 bookings at slots 0-2 are in `extra`
    trial[3] += fb60.nominal_power_w * 1.0
    traj = simulate(cfg, inputs, threshold, extra_ac_wh=tuple(trial))
    assert (
        traj.flows[3].soc_end_percent
        >= cfg.battery.soc_max_percent - AT_MAX_TOPUP_BAND_PERCENT - 1e-6
    )
    soc_full = cfg.battery.soc_max_percent - 0.1
    assert max(f.soc_end_percent for f in traj.flows[:3]) >= soc_full
    assert traj.total_import_wh - base.total_import_wh > IMPORT_ARTIFACT_SLACK_WH


def test_at_max_topup_hysteresis_rejects_until_pv_refills():
    """Hysteresis shape over consecutive plans: the top-up dips the SOC to
    the band floor; planning the SAME thin slot again right after rejects
    the next top-up (its trial would leave the band) — until strong PV
    refills the SOC above the floor, when the slot books again. (A pass-2
    bet with a PROVEN refill may still take the rejected slot: the band is
    the cheap pass-1 heuristic, the floors themselves are Z3/Z4's job.)"""
    from core.optimize import AT_MAX_TOPUP_BAND_PERCENT

    start = datetime(2026, 7, 4, 12, 0)
    # Plan A: the top-up books and dips to the band floor.
    cfg, inputs = _topup_scene(
        [(3000.0, 125.0, 75.0), (210.0, 125.0, 75.0), (3000.0, 125.0, 75.0)]
        + _TOPUP_TAIL,
        94.0,
        (_TOPUP_FB,),
        (SurplusLoadState(load_id="fb", soc_percent=0.0),),
        start,
    )
    result_a = plan(cfg, inputs)
    lp_a = result_a.load_plans[0]
    assert any("at-max top-up" in r for r in lp_a.reasons)
    dip_soc = result_a.trajectory.flows[1].soc_end_percent

    # Plan B: the same thin slot fed the post-dip SOC — the top-up is
    # rejected because its trial would leave the band.
    cfg_b, inputs_b = _topup_scene(
        [(210.0, 125.0, 75.0), (3000.0, 125.0, 75.0)] + _TOPUP_TAIL,
        dip_soc,
        (_TOPUP_FB,),
        (SurplusLoadState(load_id="fb", soc_percent=20.0),),
        start + timedelta(hours=1),
    )
    result_b = plan(cfg_b, inputs_b)
    lp_b = result_b.load_plans[0]
    assert all("at-max top-up" not in r for r in lp_b.reasons)
    threshold_b, _base_b = search_threshold(cfg_b, inputs_b)
    trial = [0.0] * len(inputs_b.slots)
    trial[0] += 150.0
    traj_b = simulate(cfg_b, inputs_b, threshold_b, extra_ac_wh=tuple(trial))
    assert (
        traj_b.flows[0].soc_end_percent
        < cfg_b.battery.soc_max_percent - AT_MAX_TOPUP_BAND_PERCENT
    )
    # The documented contrast: pass 2 takes the slot with a proven refill.
    assert any(a[2] == 2 for a in lp_b.allocations)

    # Plan C: SOC refilled above the floor -> the top-up books again.
    result_c = plan(cfg_b, replace(inputs_b, start_soc_percent=95.0))
    lp_c = result_c.load_plans[0]
    assert any("at-max top-up" in r for r in lp_c.reasons)
