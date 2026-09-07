"""Operator 2026-09-07: continuous consumption outranks deliberate export."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from core.model import (
    BatteryParams,
    ControlParams,
    ConverterParams,
    FeedInParams,
    HourSlot,
    LoadPlan,
    PlanInputs,
    SurplusLoad,
    SystemConfig,
)
from core.optimize import allocate_loads, plan_feedin
from core.simulate import simulate


def _feedin_case(hours, *, power=100, pv=(1400, 1400, 1400, 1400), durations=None):
    now = datetime(2026, 9, 7, 8)
    durations = durations or (1,) * len(hours)
    config = SystemConfig(
        battery=BatteryParams(capacity_wh=5000, eta_charge=1, eta_discharge=1),
        charger=ConverterParams(eta=1, standby_power_w=0),
        inverter=ConverterParams(eta=1, standby_power_w=0),
        control=ControlParams(predrain_pv_confidence=1, upper_pv_reserve=1),
        feedin=FeedInParams(enabled=True, max_w=power, deadline_hour=12),
        loads=(SurplusLoad("deh", "Entfeuchter", 400, min_runtime_min=15),),
    )
    slots = tuple(
        HourSlot(i, now + timedelta(hours=i), dur, 8 + i, pv[i] * dur, 0, 0)
        for i, dur in enumerate(durations)
    )
    if durations[0] < 1:
        slots = (
            replace(slots[0], start=now + timedelta(hours=1 - durations[0])),
            *slots[1:],
        )
    inputs = PlanInputs(slots[0].start, 50, slots)
    extra = tuple(400 * h for h in hours)
    load_plan = LoadPlan(
        "deh", tuple(h > 0 for h in hours), sum(extra), run_hours=hours
    )
    trajectory = simulate(config, inputs, 20, extra_ac_wh=extra)
    return config, inputs, extra, load_plan, trajectory


def _book(case, *, plans=None):
    config, inputs, extra, load_plan, trajectory = case
    return plan_feedin(
        config,
        inputs,
        20,
        extra,
        trajectory,
        load_plans=(load_plan,) if plans is None else plans,
    )


@pytest.mark.parametrize("hours", [(0, 1, 1, 1), (1, 0.75, 1, 1), (1, 1, 0.75, 1)])
def test_feedin_waits_for_uninterrupted_load_service(hours):
    """R1: a late start, internal pause or partial peak slot forbids early export."""
    case = _feedin_case(hours)
    assert case[-1].total_export_wh > 0
    booked, _ = _book(case)
    assert booked[0] == 0


def test_feedin_allows_full_service_until_peak_but_not_required_after_peak():
    """R1: allow useful export shifting once consumption before full SOC is exhausted."""
    case = _feedin_case((1, 1, 1, 0))
    booked, totals = _book(case)
    assert booked[0] == 100
    assert totals["2026-09-07"] > 0
    config, inputs, extra, _, base = case
    trial = simulate(config, inputs, 20, extra_ac_wh=extra, feedin_wh=booked)
    assert trial.total_import_wh == 0
    assert trial.total_export_wh == pytest.approx(base.total_export_wh)


def test_feedin_checks_peak_after_export_delays_charging():
    """R1: the no-feed-in peak is insufficient when feed-in delays it past a gap."""
    case = _feedin_case((1, 1, 1, 0), power=2000)
    case = (
        replace(case[0], feedin=replace(case[0].feedin, deadline_hour=9)),
        *case[1:],
    )
    assert case[-1].flows[2].soc_end_percent == 95
    booked, _ = _book(case)
    assert booked[0] == 0


@pytest.mark.parametrize("hours", [(), (0.5, 1)])
def test_feedin_missing_or_truncated_duration_evidence_fails_closed(hours):
    """R1: neither missing load plans nor incomplete run durations prove consumption."""
    case = _feedin_case((1, 1, 1, 1))
    plans = () if not hours else (replace(case[3], run_hours=hours),)
    assert _book(case, plans=plans)[0] == (0, 0, 0, 0)


def test_feedin_requires_every_continuous_load_to_run():
    """R1: one running load cannot hide another continuous consumer's gaps."""
    case = _feedin_case((1, 1, 1, 1))
    config = replace(
        case[0], loads=(*case[0].loads, SurplusLoad("other", "Other", 100))
    )
    assert _book((config, *case[1:]))[0] == (0, 0, 0, 0)


def test_feedin_partial_first_slot_counts_actual_duration():
    """R1: a fully covered remaining half hour is not a load pause."""
    case = _feedin_case((0.5, 1, 1, 1), durations=(0.5, 1, 1, 1))
    assert _book(case)[0][0] == 50


def test_feedin_manual_setpoint_still_mirrors_operator_with_idle_load():
    """R9: the consumption prerequisite governs auto; manual reality remains visible."""
    case = _feedin_case((0, 0, 0, 0))
    config = replace(case[0], feedin=replace(case[0].feedin, manual_w=100))
    assert _book((config, *case[1:]))[0][0] == 100
    assert _book(case)[0] == (0, 0, 0, 0)


@pytest.mark.parametrize("pv_last,expected", [(170, True), (150, True), (149, False)])
def test_predrain_accepts_only_one_percentage_point_peak_shortfall(pv_last, expected):
    """Block R5: accept small forecast deviations, bound them at one SOC point.

    Starting at 90 %, the base has 350–370 Wh surplus after filling; a 400 W
    pre-drain can cost at most 50 Wh of the 5 kWh battery's daily peak.
    """
    config, inputs, _, _, _ = _feedin_case((0, 0, 0), pv=(0, 450, pv_last))
    config = replace(config, loads=(replace(config.loads[0], min_runtime_min=60),))
    inputs = replace(inputs, start_soc_percent=90)
    base = simulate(config, inputs, 20)
    plans, _, trajectory = allocate_loads(config, inputs, 20, base)
    has_block = any(a[2] == 3 for a in plans[0].allocations)
    assert has_block is expected
    assert trajectory.total_import_wh == 0
    assert trajectory.max_soc_percent >= 94
    if expected:
        assert trajectory.max_soc_percent < 95


def test_feedin_does_not_reuse_an_earlier_peak_after_battery_has_fallen():
    """R1: leftover export is no permission when no future same-day peak exists."""
    case = _feedin_case((1, 1, 1, 1, 0), pv=(1400, 1400, 1400, 0, 200))
    config = replace(case[0], feedin=replace(case[0].feedin, deadline_hour=14))
    booked, _ = _book((config, *case[1:]))
    assert booked[0] > 0
    assert booked[-1] == 0
    assert case[-1].flows[-1].soc_end_percent < 95


def test_feedin_consumption_gate_is_independent_for_each_day():
    """R1/R3: today's idle consumer cannot authorize export; tomorrow can qualify."""
    case = _feedin_case((0, 0, 0, 0))
    config, inputs, extra, lp, _ = case
    next_slots = tuple(
        replace(s, index=s.index + 4, start=s.start + timedelta(days=1))
        for s in inputs.slots
    )
    # A night discharge is represented in the first slot of the next day.
    next_slots = (replace(next_slots[0], ac_wh=1600), *next_slots[1:])
    inputs = replace(inputs, slots=(*inputs.slots, *next_slots))
    extra = (*extra, 400, 400, 400, 400)
    lp = replace(lp, schedule=(False,) * 4 + (True,) * 4, run_hours=(0,) * 4 + (1,) * 4)
    base = simulate(config, inputs, 20, extra_ac_wh=extra)
    booked, totals = _book((config, inputs, extra, lp, base))
    assert booked[:4] == (0, 0, 0, 0)
    assert "2026-09-07" not in totals
    assert totals["2026-09-08"] > 0


def test_predrain_peak_tolerance_survives_later_day_allocation():
    """Block R5: an accepted near-full day must not veto tomorrow's valid block."""
    config, inputs, _, _, _ = _feedin_case((0, 0, 0), pv=(0, 450, 170))
    config = replace(config, loads=(replace(config.loads[0], min_runtime_min=60),))
    night = HourSlot(3, datetime(2026, 9, 7, 20), 1, 20, 0, 220, 0)
    tomorrow = tuple(
        replace(s, index=s.index + 4, start=s.start + timedelta(days=1))
        for s in inputs.slots
    )
    inputs = replace(
        inputs, start_soc_percent=90, slots=(*inputs.slots, night, *tomorrow)
    )
    base = simulate(config, inputs, 20)
    plans, _, trial = allocate_loads(config, inputs, 20, base)
    block_days = {
        inputs.slots[start].start.date().isoformat()
        for start, _, pass_no, _ in plans[0].allocations
        if pass_no == 3
    }
    assert block_days == {"2026-09-07", "2026-09-08"}
    assert trial.total_import_wh == 0
    for day in (7, 8):
        peak = max(
            flow.soc_end_percent
            for slot, flow in zip(inputs.slots, trial.flows, strict=True)
            if slot.start.day == day
        )
        assert peak == pytest.approx(94.4)


def test_runtime_pause_preserves_natural_export_and_unshifted_soc():
    """R8: OFF removes deliberate export over the horizon, not natural overflow."""
    from core.optimize import plan
    from core.series import build_slots

    config = SystemConfig(
        feedin=FeedInParams(enabled=True, max_w=1000, deadline_hour=12)
    )
    inputs = build_slots(config, datetime(2026, 9, 7, 7), 60, [10, 10, 10])
    on = plan(config, inputs)
    paused = plan(
        replace(config, feedin=replace(config.feedin, automatic_enabled=False)), inputs
    )
    disabled = plan(
        replace(config, feedin=replace(config.feedin, enabled=False)), inputs
    )
    assert len(on.feedin_by_day_wh) == 3
    assert paused.feedin_by_day_wh == {}
    assert not any(paused.feedin_schedule_w)
    assert paused.trajectory == disabled.trajectory
    assert paused.grid_export_kwh > 0


def test_runtime_pause_keeps_manual_reality_today_without_automatic_tomorrow():
    """R8/R9: pausing automation neither hides manual export nor resumes at midnight."""
    from core.optimize import plan
    from core.series import build_slots

    config = SystemConfig(
        feedin=FeedInParams(
            enabled=True,
            max_w=1000,
            deadline_hour=12,
            automatic_enabled=False,
            manual_w=100,
        )
    )
    inputs = build_slots(config, datetime(2026, 9, 7, 7), 60, [10, 10, 10])
    result = plan(config, inputs)
    assert set(result.feedin_by_day_wh) == {"2026-09-07"}
    assert result.feedin_by_day_wh["2026-09-07"] > 0
    assert all(
        w == 0
        for w, slot in zip(result.feedin_schedule_w, inputs.slots, strict=True)
        if slot.start.date() > inputs.now.date()
    )


@pytest.mark.parametrize("available,ready", [(True, False), (False, True)])
def test_unconfirmed_or_unavailable_consumer_cannot_authorize_export(available, ready):
    from core.model import SurplusLoadState

    case = _feedin_case((1, 1, 1, 1))
    inputs = replace(
        case[1],
        load_states=(
            SurplusLoadState(
                "deh",
                available=available,
                feedin_ready=ready,
            ),
        ),
    )
    assert _book((case[0], inputs, *case[2:]))[0] == (0, 0, 0, 0)
    confirmed = replace(
        inputs, load_states=(SurplusLoadState("deh", feedin_ready=True),)
    )
    assert _book((case[0], confirmed, *case[2:]))[0][0] > 0


@pytest.mark.parametrize("capacity", [2000, 5000, 10000])
def test_peak_allowance_scales_with_capacity_without_weakening_import(capacity):
    factor = capacity / 5000
    config, inputs, _, _, _ = _feedin_case(
        (0, 0, 0), pv=(0, 450 * factor, 149 * factor)
    )
    config = replace(
        config,
        battery=replace(config.battery, capacity_wh=capacity),
        loads=(
            replace(config.loads[0], nominal_power_w=400 * factor, min_runtime_min=60),
        ),
    )
    inputs = replace(inputs, start_soc_percent=90)
    plans, _, trajectory = allocate_loads(
        config, inputs, 20, simulate(config, inputs, 20)
    )
    assert not any(a[2] == 3 for a in plans[0].allocations)
    assert (0, "daily_peak") in plans[0].rejected_candidates
    assert trajectory.total_import_wh == 0
