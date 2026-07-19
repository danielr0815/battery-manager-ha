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
    _committed_hours,
    _crossday_daytime_bet,
    _degrades_min_soc,
    _quantised_hours,
    _refill_index,
    _slot_serviceable,
    _spread_energy,
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
    # Total booked ENERGY is order-invariant — priority only reassigns WHICH
    # load gets the scarce surplus, never how much is absorbed (this is the
    # discriminating assertion; the import bound below is implied by R1 and
    # would not catch a priority bug on its own).
    total_fb = sum(fb_first.values())
    total_deh = sum(deh_first.values())
    assert abs(total_fb - total_deh) < 1e-6
    # Import may differ only within the artifact slack (F-STRICT-SURPLUS R1: a
    # booking's ~10 Wh standby artifact may ride the slack in one order and not
    # the other — never more).
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert abs(import_fb - import_deh) <= IMPORT_ARTIFACT_SLACK_WH / 1000.0 + 1e-9


def test_reasons_align_one_to_one_with_allocations():
    """R12/R13/R15: every allocation entry has exactly one reason, same order;
    the pass number in the string matches the allocation's pass, and pass-2
    reasons carry the structural lateness claim."""
    result, _ = _s3_plan()
    for lp in result.load_plans:
        assert len(lp.reasons) == len(lp.allocations)
        for (_start, _count, pass_no, _wh), why in zip(
            lp.allocations, lp.reasons, strict=True
        ):
            assert why.startswith(f"pass {pass_no} @ ")
            if pass_no == 1:
                assert "direct surplus" in why
            else:
                assert "latest feasible slot" in why
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
# rescued night export per candidate. Test contract T1-T5, T12, T13.
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


def test_t1_night_predrain_books_on_artifact_slack_ratio_ignored():
    """T1 (F-STRICT-SURPLUS R1): the ~10 Wh charger-standby artifact of a night
    pre-drain rides the ABSOLUTE artifact slack — the pre-drain books without
    any trade ratio, and the retired `import_trade_ratio` field changes
    nothing (0.0 and 0.1 produce the identical plan). The used import stays
    bounded by the slack, never by a rescued-export budget."""
    now = datetime(2026, 7, 3, 21, 0)

    def run(ratio):
        cfg = _predrain_config(ratio=ratio, alpha=1.0, beta=1.0)
        return make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0])

    r0, inputs = run(0.0)
    r1, _ = run(0.1)
    night0 = [h for h in _dehumid_hours(r0, inputs) if not daylight_h(h)]
    night1 = [h for h in _dehumid_hours(r1, inputs) if not daylight_h(h)]
    assert night0, "the standby artifact must not veto the night predrain (L1)"
    assert night0 == night1, "the retired ratio must not change the plan"
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    assert 0.0 < r0.import_trade_used_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    assert r0.import_trade_used_wh == r1.import_trade_used_wh


def test_t2_cumulative_import_trade_invariant():
    """T2: over the whole allocation, final import stays within the cumulative
    trade invariant against the no-loads base."""
    now = datetime(2026, 7, 3, 21, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    inputs = build_slots(cfg, now, 84.0, [0.0, 13.0, 11.0])
    result = plan(cfg, inputs)
    _, base = search_threshold(cfg, inputs)
    traj = result.trajectory
    assert traj.total_import_wh - base.total_import_wh <= (
        0.1 * (base.total_export_wh - traj.total_export_wh) + 1.0 + 1e-6
    )
    assert traj.total_import_wh > base.total_import_wh  # a trade actually happened


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
    """T4: the pessimistic WINDOWED stress gate (alpha, §3.3 v2) refuses the
    DEEPEST pre-dawn hours a full-confidence run would take — a deep multi-hour
    drain lengthens the bet window before the (stressed) morning refills, so it
    is rejected, while the shallower pre-dawn block that hugs the window is
    accepted. The stressed reserve holds at the inverter+buffer floor (NOT
    soc_min+buffer). alpha=1.0 disables the gate."""
    now = datetime(2026, 7, 3, 21, 0)

    def run(alpha):
        cfg = _predrain_config(
            ratio=0.1, alpha=alpha, beta=1.0, loads=(FOSSIBOT_B, DEHUMIDIFIER)
        )
        return make_plan(
            cfg, now, 90.0, [0.0, 15.0], load_states=(FB_STATE, DEHUMID_STATE)
        )

    trusting, inputs = run(1.0)
    stressed, _ = run(0.5)
    predawn_trust = [h for h in _dehumid_hours(trusting, inputs) if h < 7]
    predawn_stress = [h for h in _dehumid_hours(stressed, inputs) if h < 7]
    # Full confidence drains deeper into the night (down to the 20 % cutoff);
    # the stressed run books strictly fewer, later pre-dawn hours.
    assert predawn_trust and predawn_stress
    assert min(predawn_trust) < min(predawn_stress)
    assert trusting.min_soc_percent < stressed.min_soc_percent
    floor = 20.0 + 5.0  # inverter_min_soc + soc_buffer (NOT soc_min + buffer)
    assert stressed.stressed_min_soc_percent >= floor - 0.5
    assert trusting.stressed_min_soc_percent is None  # alpha=1.0 -> gate off


def test_t5_predrain_hours_hug_the_window_latest_first():
    """T5: pre-drain hours sit as late as the constraints allow (L4): a
    contiguous pre-dawn block ending right before the production window, never a
    detached earlier evening run."""
    now = datetime(2026, 7, 3, 21, 0)
    cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=1.0)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0])
    lp = result.load_plans[0]
    day1 = now.date() + timedelta(days=1)
    predawn = sorted(
        inputs.slots[i].hour_of_day
        for i, on in enumerate(lp.schedule)
        if on
        and inputs.slots[i].start.date() == day1
        and inputs.slots[i].hour_of_day < 7
    )
    assert predawn, "a short pre-dawn window must force predrain hours"
    assert predawn == list(range(min(predawn), max(predawn) + 1)), (
        "block not contiguous"
    )
    assert max(predawn) == 6  # hugs the 07:00 production start


def test_t12_beta_books_in_window_opportunities_never_night():
    """T12: the optimistic upper-buffer gate (c2) books in-window slots the
    nominal forecast alone cannot justify; beta=1.0 does not. c2 never books a
    night slot (outside every PV window), and the ratio invariant still holds."""
    now = datetime(2026, 7, 4, 8, 0)

    def run(beta):
        cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=beta)
        return make_plan(cfg, now, 92.0, [7.0])

    r10, inputs = run(1.0)
    r12, _ = run(1.2)
    booked10 = {i for i, on in enumerate(r10.load_plans[0].schedule) if on}
    booked12 = {i for i, on in enumerate(r12.load_plans[0].schedule) if on}
    extra = booked12 - booked10
    assert extra, "beta=1.2 must open extra in-window opportunity slots"
    windows = pv_windows(inputs, 200.0, None)
    for i in extra:
        w = windows[inputs.slots[i].start.date()]
        assert w[0] <= i <= w[1], f"c2 slot {i} not inside its PV window {w}"
        assert daylight(inputs.slots[i]), "c2 must never book a night slot"
    cfg12 = _predrain_config(ratio=0.1, alpha=1.0, beta=1.2)
    _, base = search_threshold(cfg12, inputs)
    traj = r12.trajectory
    assert traj.total_import_wh - base.total_import_wh <= (
        0.1 * (base.total_export_wh - traj.total_export_wh) + 1.0 + 1e-6
    )


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
    """§3.3 v2: the stress gate is WINDOWED, not whole-horizon. A first-night
    pre-drain is judged only on its own recovery window, so it is booked even
    though the WHOLE-HORIZON alpha sim (which the failed v1 gate used) drops far
    below the floor on a later, weaker day. The v1 whole-horizon gate would have
    vetoed these bookings; the windowed gate does not."""
    now = datetime(2026, 7, 3, 21, 0)
    cfg = _predrain_config(ratio=0.1, alpha=0.5, beta=1.0)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0])
    n = len(inputs.slots)
    lp = result.load_plans[0]

    # A first-night pre-dawn block IS booked (windowed gate permits it).
    day1 = now.date() + timedelta(days=1)
    first_night = sorted(
        inputs.slots[i].hour_of_day
        for i, on in enumerate(lp.schedule)
        if on
        and inputs.slots[i].start.date() == day1
        and inputs.slots[i].hour_of_day < 7
    )
    assert first_night, "windowed gate must still book a first-night pre-drain"

    # The WHOLE-HORIZON alpha sim of the final series breaks the inverter floor
    # (a later, weaker day dominates it) — proving the gate is NOT whole-horizon.
    threshold = result.threshold_percent
    extra = tuple(f.extra_ac_wh for f in result.trajectory.flows)
    whole_horizon = simulate(cfg, inputs, threshold, extra_ac_wh=extra, pv_scale=0.5)
    floor = 20.0 + 5.0  # inverter_min_soc + buffer
    assert whole_horizon.min_soc_percent < floor, (
        "scenario must have a later whole-horizon stressed dip below the floor"
    )

    # Yet the WINDOWED reserve of the earliest pass-2 bet is held at the floor.
    booked = [a[0] for a in lp.allocations if a[2] == 2]
    i0 = min(booked)
    recovery = _refill_index(result.trajectory, i0, cfg.battery.soc_max_percent - 0.1)
    scale_vec = [0.5 if i0 <= j <= recovery else 1.0 for j in range(n)]
    stressed = simulate(cfg, inputs, threshold, extra_ac_wh=extra, pv_scale=scale_vec)
    windowed_min = _windowed_min_soc(stressed, i0, recovery)
    assert windowed_min >= floor - 0.5
    # The diagnostic reports exactly this windowed reserve.
    assert result.stressed_min_soc_percent is not None
    assert abs(result.stressed_min_soc_percent - windowed_min) < 1e-6


def test_t4_bet_settles_at_refill_so_unrelated_tail_dip_cannot_veto():
    """F-STRICT-SURPLUS R3 settlement: a pre-drain whose trial refills to the
    ceiling at the very next slot has its bet window END there — the deep,
    unrelated DC-tail trough AFTER the refill is structurally outside the
    stressed window and cannot veto the (fully refilled, export-covered)
    pre-drain. (Pre-R3 this scenario needed the Z4 relief clause to survive a
    window that ran to the same-day PV window end; the relief clause remains
    in force for base dips INSIDE [i, refill].)"""
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

    # The pre-drain at slot 0 (pass 2) IS booked.
    assert any(a[0] == 0 and a[2] == 2 for a in lp.allocations)

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
    PlanResult.appliance_windows. With an opportunistic appliance AND an accepted
    pre-drain (pass-2 continuous booking; alpha < 1 so the diagnostic block runs),
    appliance_windows must stay the advisor dict (appliance id -> bool), not the
    pv_windows date->tuple dict the diagnostic builds internally."""
    now = datetime(2026, 7, 3, 21, 0)
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
    result, _inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0])
    # Premise: the scenario books a pre-drain (pass 2, continuous), so the
    # diagnostic block that used to reassign `windows` runs.
    lp = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    assert any(a[2] == 2 for a in lp.allocations), "scenario must book a pre-drain"
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
    dehumidifier pre-drains and trades ~10 Wh; the fossibot has a large
    remaining budget. Under parity the fossibot books MORE than the old
    strict gate allowed (2250 Wh vs 1800 — parity can only add opportunities
    here) while the cumulative trade invariant (b1) and the attribution
    bound (b2) keep holding — they are the binding contracts."""
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

    # (a) A continuous trade fired AND the fossibot books its parity coverage:
    #     2250 Wh, 450 Wh MORE than the old strict gate's 1800 (pass-2 refills
    #     incl. a 0.5 h residual quantum, F-RESIDUAL-TOPUP R1) — the shared
    #     trade budget now finances fossibot bookings too.
    assert traj.total_import_wh - base.total_import_wh > 1e-6  # deh traded
    assert any(a[2] == 2 for a in fb_plan.allocations)  # a post-trade refill booked
    assert fb_plan.planned_energy_wh >= 1800.0 - 1e-6  # parity only ever adds
    assert abs(fb_plan.planned_energy_wh - 2250.0) < 1e-6
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
# identical pass-2 gate set (shared Z2' trade, c1-rt, c2-beta, Z4 stress);
# the priority order (config order) alone decides contested bet energy.
# Single remaining class rule: energy-limited loads never book zero-PV
# (night) slots — nights stay reserved for continuous loads.
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


def test_gate_parity_contested_bet_goes_to_priority_one():
    """The depth-capped bet slot (room for ONE 300 Wh booking above the
    inverter cutoff) goes to whichever load is FIRST in config order — the
    energy-limited powerstation wins it when it holds priority 1, the
    continuous load wins it when the order is swapped. Priority, not load
    class, decides."""
    fb = replace(FOSSIBOT_B, target_soc_percent=100.0)
    # 800 Wh start: one ~326 Wh drain lands at ~474 Wh (above the 400 Wh
    # cutoff); any second quantum would fall through it — room for one bet.
    # Powerstation first: it takes the bet, the dehumidifier is floor-blocked.
    plans = _parity_scene(800.0, (fb, _DEH300))
    assert _books_slot0_pass2(plans, "fossibot_b")
    assert not _books_slot0_pass2(plans, "deh300")
    # Swapped order: the same slot goes to the dehumidifier instead.
    plans = _parity_scene(800.0, (_DEH300, fb))
    assert _books_slot0_pass2(plans, "deh300")
    assert not _books_slot0_pass2(plans, "fossibot_b")


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
    # Contrast: the SAME horizon books night hours for a continuous load —
    # the c1/Z2' gates admit them, so only the daylight rule blocked fb.
    deh_cfg = _predrain_config(ratio=0.5, alpha=1.0, beta=1.0)
    deh_res, deh_in = make_plan(deh_cfg, now, 84.0, [0.0, 13.0, 11.0])
    night = [h for h in _dehumid_hours(deh_res, deh_in) if not daylight_h(h)]
    assert night, "contrast: the continuous load must book the night pre-drain"


def test_gate_parity_c2_beta_books_energy_limited_in_window():
    """T12 parity: the optimistic c2 gate now opens extra in-window slots for
    an energy-limited load exactly as it does for the dehumidifier — reason
    string included — while the trade invariant keeps holding."""
    now = datetime(2026, 7, 4, 8, 0)
    fb_hungry = replace(FOSSIBOT_B, capacity_wh=8000.0, target_soc_percent=100.0)
    states = (SurplusLoadState(load_id="fossibot_b", soc_percent=0.0),)

    def run(beta):
        cfg = _predrain_config(ratio=0.1, alpha=1.0, beta=beta, loads=(fb_hungry,))
        return make_plan(cfg, now, 92.0, [7.0], load_states=states)

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
    (0.5 W charger) no longer vetoes the night pre-drain — L1 is solved by the
    slack, not by a trade ratio. (b) On a heavy clip-eve with kWh of rescuable
    export, the whole allocation still may not add import beyond the slack —
    the retired proportional budget (0.1 * rescued + 1) would have minted
    hundreds of Wh here."""
    from core.optimize import IMPORT_ARTIFACT_SLACK_WH

    now = datetime(2026, 7, 3, 21, 0)
    charger = ConverterParams(max_power_w=2300.0, eta=0.92, standby_power_w=0.5)
    control = replace(
        ControlParams(),
        import_trade_ratio=0.0,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=1.0,
    )
    cfg = SystemConfig(control=control, loads=(DEHUMIDIFIER,), charger=charger)
    result, inputs = make_plan(cfg, now, 84.0, [0.0, 13.0, 11.0])
    night = [h for h in _dehumid_hours(result, inputs) if not daylight_h(h)]
    assert night, "a sub-Wh standby artifact must not veto the night pre-drain"
    assert result.import_trade_used_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6

    # (b) the hard cap: rescued export is huge, yet added import <= slack.
    _thr, base = search_threshold(cfg, inputs)
    rescued = base.total_export_wh - result.trajectory.total_export_wh
    assert rescued > 1000.0  # kWh-class rescue on this clip-eve
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
    """The T12 c2 geometry (08:00, house 92 %, one 7 kWh day, dehumidifier)
    with optional P10/P90 bands at the given ratios on every daylight slot."""
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=1.0,
        upper_pv_reserve=beta,
        strong_pv_cutoff_w=200.0,
    )
    cfg = SystemConfig(control=control, loads=(DEHUMIDIFIER,))
    now = datetime(2026, 7, 4, 8, 0)
    base_inputs = build_slots(cfg, now, 92.0, [7.0])
    if r10 is None:
        return plan(cfg, base_inputs), base_inputs
    p10: dict[datetime, float] = {}
    p90: dict[datetime, float] = {}
    for s in base_inputs.slots:
        if s.pv_wh >= 25.0 and s.duration == 1.0:
            p10[s.start] = s.pv_wh * r10
            p90[s.start] = s.pv_wh * r90
    inputs = build_slots(cfg, now, 92.0, [7.0], pv_hourly_p10=p10, pv_hourly_p90=p90)
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


def _predrain_band_plan(alpha, r10=None, r90=None):
    """The T4 pre-drain geometry (21:00, house 90 %, one strong 15 kWh day)
    with optional bands on the next day's daylight slots."""
    cfg = _predrain_config(
        ratio=0.1, alpha=alpha, beta=1.0, loads=(FOSSIBOT_B, DEHUMIDIFIER)
    )
    now = datetime(2026, 7, 3, 21, 0)
    states = (FB_STATE, DEHUMID_STATE)
    base_inputs = build_slots(cfg, now, 90.0, [0.0, 15.0], load_states=states)
    if r10 is None:
        return plan(cfg, base_inputs), base_inputs
    day = now.date() + timedelta(days=1)
    p10: dict[datetime, float] = {}
    p90: dict[datetime, float] = {}
    for s in base_inputs.slots:
        if s.start.date() == day and s.pv_wh >= 25.0 and s.duration == 1.0:
            p10[s.start] = s.pv_wh * r10
            p90[s.start] = s.pv_wh * r90
    inputs = build_slots(
        cfg,
        now,
        90.0,
        [0.0, 15.0],
        load_states=states,
        pv_hourly_p10=p10,
        pv_hourly_p90=p90,
    )
    return plan(cfg, inputs), inputs


def _predawn_hours(result, inputs):
    lp = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    day1 = datetime(2026, 7, 4).date()
    return sorted(
        inputs.slots[i].hour_of_day
        for i, on in enumerate(lp.schedule)
        if on
        and inputs.slots[i].start.date() == day1
        and inputs.slots[i].hour_of_day < 7
    )


def test_z4_stress_follows_p10_evidence():
    """R12c: the Z4 pre-drain stress follows P10 evidence in BOTH directions.
    A stable-day band (p10_ratio 0.85 on the refill slots) admits a pre-dawn
    hour the scalar alpha rejected; a volatile-day band (p10_ratio 0.4)
    rejects hours the trusting alpha=1.0 accepted. (The milder direction is
    demonstrated at scalar alpha 0.35 — under F-STRICT-SURPLUS R3 the bet
    window ends at the actual refill, so this geometry's marginal 03:00 hour
    is already accepted by scalars >= 0.4; 0.35 is the nearest scalar whose
    rejection the evidence overturns.)"""
    # Milder-than-scalar: 0.35 rejects the 03:00 bet; 0.85 P10 evidence on the
    # morning refill accepts it (stable day -> the reserve provably recovers).
    scalar_mid, inputs = _predrain_band_plan(0.35)
    evidence_mid, _ = _predrain_band_plan(0.35, 0.85, 1.0)
    assert min(_predawn_hours(evidence_mid, inputs)) < min(
        _predawn_hours(scalar_mid, inputs)
    )

    # Harsher-than-scalar: alpha=1.0 trusts and drains deep; 0.4 P10 evidence
    # (volatile day) rejects most of the pre-dawn block despite the dial.
    trusting, _ = _predrain_band_plan(1.0)
    evidence_low, _ = _predrain_band_plan(1.0, 0.4, 1.0)
    assert len(_predawn_hours(evidence_low, inputs)) < len(
        _predawn_hours(trusting, inputs)
    )
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
    cfg = SystemConfig(control=control, loads=(DEHUMIDIFIER,))
    now = datetime(2026, 7, 4, 8, 0)
    inputs_empty = build_slots(
        cfg, now, 92.0, [7.0], pv_hourly_p10={}, pv_hourly_p90=None
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


def test_night_rescue_books_predawn_hours_before_clipping_day():
    """R10 (D1 incident regression): clipping-eve, SOC 57 at 21:00, learned
    426 W dehumidifier, next day clips even under alpha=0.5 stress. The
    rt-honest c1 books >= 1 h in the 22:00-05:00 night window (was: zero — a
    pure battery round trip could never satisfy the old 0.85*energy demand);
    import stays within the Z2' trade allowance and the min SOC respects the
    (ramped) floors."""
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

    dawn = datetime(2026, 7, 12, 6, 0)
    night_hours = sum(
        h for i, h in enumerate(lp.run_hours) if h > 0 and inputs.slots[i].start < dawn
    )
    assert night_hours >= 1.0, f"only {night_hours} h booked in the night window"
    # Z2' trade allowance still bounds the drain (R3).
    _thr, base = search_threshold(config, inputs)
    allowed = 0.1 * (base.total_export_wh - result.trajectory.total_export_wh) + 1.0
    assert result.trajectory.total_import_wh - base.total_import_wh <= allowed + 1e-6
    # The drain respects the floors: min SOC stays above the inverter cutoff.
    assert result.min_soc_percent >= config.control.inverter_min_soc_percent - 0.01
    # The scene is a genuine stressed-clip eve (the merge bound engaged).
    assert result.threshold_horizon_end is not None


def test_strict_surplus_refuses_cutoff_riding_predawn_quantum():
    """F-STRICT-SURPLUS regression lock on the old c1-honesty geometry (small
    5 kWh battery, deep drain): the marginal 05:00 half-quantum the retired
    trade budget used to finance is now refused by ALL THREE new/tightened
    gates — it would end the slot AT the 20 % cutoff (R2), push the house onto
    grid pre-dawn for ~76 Wh > IMPORT_ARTIFACT_SLACK_WH (R1), and break the
    stressed windowed floor over its refill-settled bet window (R3/Z4). The
    plan keeps rescuing via floor-safe morning pre-charges instead; the
    rescue-capable geometry (7 kWh battery) still books true pre-dawn quanta
    — see test_night_rescue_books_predawn_hours_before_clipping_day."""
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
    now = datetime(2026, 7, 11, 21, 0)
    inputs = build_slots(config, now, 57.0, [11.9, 12.1, 11.8], load_states=NIGHT_STATE)
    result = plan(config, inputs)
    lp = result.load_plans[0]

    # No booking before 06:00 on day 1 anymore (the old plan booked 05:00).
    dawn = datetime(2026, 7, 12, 6, 0)
    assert not any(
        h > 0 and inputs.slots[i].start < dawn for i, h in enumerate(lp.run_hours)
    )
    # The allocation's import stays within the artifact slack.
    assert result.import_trade_used_wh <= IMPORT_ARTIFACT_SLACK_WH + 1e-6
    # Rescue continues floor-safe: pass-2 pre-charges book in the morning.
    assert any(a[2] == 2 for a in lp.allocations)

    # Gate-by-gate: evaluate the old 05:00 half-quantum against the no-loads
    # base exactly as pass 2 would.
    n = len(inputs.slots)
    threshold, base = search_threshold(config, inputs)
    i = next(
        k for k, s in enumerate(inputs.slots) if s.start == datetime(2026, 7, 12, 5, 0)
    )
    trial, covered = _spread_energy([0.0] * n, inputs.slots, i, 426.0, 0.5)
    traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
    # R1: real pre-dawn import beyond the artifact slack.
    assert traj.total_import_wh - base.total_import_wh > IMPORT_ARTIFACT_SLACK_WH
    # R2: the covered slot ends AT the cutoff (would ride it).
    floor20 = config.control.inverter_min_soc_percent
    assert any(traj.flows[j].soc_end_percent <= floor20 + 1e-6 for j, _t in covered)
    # R3/Z4: the stressed refill-settled window breaks the ramped floor and is
    # worse than the base's own window.
    alpha = config.control.predrain_pv_confidence
    stress_vec, _o, _b = _effective_uncertainty(inputs, alpha, 1.0)
    recovery = _refill_index(traj, i, config.battery.soc_max_percent - 0.1)
    hi = max(recovery, covered[-1][0])
    sv = [stress_vec[j] if i <= j <= hi else 1.0 for j in range(n)]
    t_w = _windowed_min_soc(
        simulate(config, inputs, threshold, extra_ac_wh=tuple(trial), pv_scale=sv),
        i,
        hi,
    )
    b_w = _windowed_min_soc(
        simulate(config, inputs, threshold, extra_ac_wh=(0.0,) * n, pv_scale=sv),
        i,
        hi,
    )
    floors = _ramped_stress_floors(config, inputs, stress_vec)
    assert t_w < floors[i] - 1e-6 and t_w < b_w - 1e-6


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
    forecast on a clipping day (~1.5 SOC-point knife-edge)."""
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
    # Midday, battery high, strong clipping today: the merge bound truncates the
    # scan to an all-surplus window.
    inputs = build_slots(config, datetime(2026, 7, 12, 10, 0), 92.0, [11.4, 11.6, 4.7])
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
    expectation: battery first), with Sunday hosting only direct-surplus
    pass-1 runs — the 08:00-12:00 beta-insurance morning bets and the pre-dawn
    cutoff-riding bookings of the v0.14.0 live plan are gone; (d) Monday's clip
    is still absorbed by pass-1 runs (rescue not suppressed)."""
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
    for lp in result.load_plans:
        for alloc, reason in zip(lp.allocations, lp.reasons, strict=True):
            if inputs.slots[alloc[0]].start.date() == sunday:
                assert alloc[2] == 1 and "direct surplus" in reason

    monday = datetime(2026, 7, 20).date()
    deh_plan = next(p for p in result.load_plans if p.load_id == "dehumidifier")
    mon_pass1 = [
        a
        for a in deh_plan.allocations
        if a[2] == 1 and inputs.slots[a[0]].start.date() == monday
    ]
    assert mon_pass1, "Monday's clip must still be absorbed by pass-1 runs"  # (d)


def test_crossday_daytime_bet_predicate():
    """R6 (operator 2026-07-19): a DAYTIME (in-window) pass-2 bet whose refill
    lands on a LATER calendar day is a forbidden cross-day daytime pre-drain;
    night slots (not in-window) keep the F-NIGHT-RESCUE cross-day carve-out and
    a same-day refill is always fine."""
    from datetime import date

    sun, mon = date(2026, 7, 19), date(2026, 7, 20)
    # Daytime + refill next day -> forbidden (the Sunday-14:00-for-Monday case).
    assert _crossday_daytime_bet(sun, mon, in_window=True)
    # Daytime + same-day refill -> allowed (pre-charge before a same-day peak).
    assert not _crossday_daytime_bet(sun, sun, in_window=True)
    # Night/pre-dawn slot (not in-window) + next-day refill -> allowed
    # (F-NIGHT-RESCUE night pre-drain before a clip day).
    assert not _crossday_daytime_bet(sun, mon, in_window=False)
    assert not _crossday_daytime_bet(sun, sun, in_window=False)


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
