"""Scenario tests for the planner (docs/ALGORITHM.md §3, S1-S4 + regressions)."""

from dataclasses import replace
from datetime import datetime

from core.model import (
    Appliance,
    HourSlot,
    PlanInputs,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.optimize import (
    _committed_hours,
    _degrades_min_soc,
    _quantised_hours,
    allocate_loads,
    appliance_windows,
    plan,
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
    scheduled_hours = sum(result.load_plans[0].schedule)
    assert scheduled_hours > 0
    # Planned energy must reflect the measured 250 W, not the nominal 300 W.
    assert (
        abs(
            result.load_plans[0].planned_energy_wh
            - sum(
                250.0 * s.duration
                for s, active in zip(
                    build_slots(config, now, 93.0, [12.0, 12.0, 12.0]).slots,
                    result.load_plans[0].schedule,
                    strict=True,
                )
                if active
            )
        )
        < 1e-6
    )


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


def test_pass2_preemptive_charging_when_sun_window_too_short():
    """Short, strong production peak: a powerstation cannot saturate within
    the window, so pass 2 pre-charges it from the battery — provably refilled
    from otherwise-lost surplus, without any grid import."""
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
    result, inputs = make_plan(config, now, 85.0, [8.0], load_states=states)

    window_hours = sum(
        1
        for i, on in enumerate(result.load_plans[0].schedule)
        if on and 11 <= inputs.slots[i].hour_of_day < 14
    )
    preemptive_hours = sum(result.load_plans[0].schedule) - window_hours
    # More energy than the window alone could deliver, thanks to pass 2 ...
    assert preemptive_hours > 0
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
    executor's dwell will really deliver — never a sliver of the dying slot."""
    config = SystemConfig(loads=(FOSSIBOT_1,))
    states = (SurplusLoadState(load_id="fossibot_1", soc_percent=0.0),)
    # Midday, battery full, huge surplus: activation right now is correct.
    now = datetime(2026, 7, 4, 11, 59)
    result, inputs = make_plan(
        config, now, 93.0, [12.0, 12.0, 12.0], load_states=states
    )
    load_plan = result.load_plans[0]
    assert load_plan.active_now
    min_commit_wh = FOSSIBOT_1.nominal_power_w * FOSSIBOT_1.min_runtime_min / 60.0
    slot0_alloc = [a for a in load_plan.allocations if a[0] == 0]
    assert slot0_alloc and slot0_alloc[0][3] >= min_commit_wh - 1e-6
    # The commitment spills past the 1-minute slot into the next hour.
    assert load_plan.schedule[0] and load_plan.schedule[1]


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
    result, inputs = make_plan(config, now, 90.0, [0.0, 8.0], load_states=states)

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


def test_quantised_hours_energy_limited_is_whole_slot_only():
    # R12: energy-limited loads keep a single whole-slot candidate.
    assert _quantised_hours(FOSSIBOT_1, _slot(1.0)) == [1.0]
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
            bool(s) == (h > 0)
            for s, h in zip(lp.schedule, lp.run_hours, strict=True)
        )


def test_energy_limited_load_books_only_whole_slots():
    # R12: F1 (energy-limited) only ever books whole slots, never a fraction.
    result, inputs = _s3_plan()
    f1 = next(lp for lp in result.load_plans if lp.load_id == "fossibot_1")
    for i, h in enumerate(f1.run_hours):
        if h > 0:
            assert abs(h - inputs.slots[i].duration) < 1e-9


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
    off = LoadPlan(load_id="x", schedule=(False,), planned_energy_wh=0.0, run_hours=(0.0,))
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
        load_id="x", schedule=(True, True, False), planned_energy_wh=0.0,
        run_hours=(0.5, 1.0, 0.0),
    )
    assert lp.active_run_hours((1.0, 1.0, 1.0)) == 0.5
    assert lp.active_run_hours() == 1.5  # legacy no-durations path unchanged
    # a FULL slot 0 continues into slot 1 (partial cap there ends the block)
    lp2 = LoadPlan(
        load_id="x", schedule=(True, True, False), planned_energy_wh=0.0,
        run_hours=(1.0, 0.5, 0.0),
    )
    assert lp2.active_run_hours((1.0, 1.0, 1.0)) == 1.5
    # a PARTIAL first slot fully filled (0.5 == slot0 0.5 h) continues
    lp3 = LoadPlan(
        load_id="x", schedule=(True, True, False), planned_energy_wh=0.0,
        run_hours=(0.5, 1.0, 0.0),
    )
    assert lp3.active_run_hours((0.5, 1.0, 1.0)) == 1.5
