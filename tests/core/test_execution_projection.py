"""F-EXECUTION-PROJECTION: physical dwell, stability floor and honest feed-in reasons."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from core.model import HourSlot, PlanInputs, SurplusLoad, SurplusLoadState, SystemConfig
from core.optimize import allocate_loads, plan, plan_feedin
from core.series import build_slots
from core.simulate import simulate
from test_feedin_load_priority import _feedin_case

NOW = datetime(2026, 9, 8, 8)


def case(*, soc=80, pv=0, minutes=20, available=True, power=300):
    load = SurplusLoad("load", "Load", power, min_runtime_min=30)
    config = SystemConfig(loads=(load,))
    state = SurplusLoadState(
        "load", available=available, minimum_run_until=NOW + timedelta(minutes=minutes)
    )
    inputs = PlanInputs(NOW, soc, (HourSlot(0, NOW, 1, 8, pv, 0, 0),), (state,))
    return config, inputs


def test_running_minimum_is_booked_without_new_surplus_or_double_energy():
    config, inputs = case()
    base = simulate(config, inputs, 20)
    loads, extra, trajectory = allocate_loads(config, inputs, 20, base)
    assert loads[0].run_hours == (1 / 3,)
    assert loads[0].planned_energy_wh == 100
    assert extra == (100,)
    assert loads[0].reasons == ("confirmed minimum runtime",)
    assert trajectory.total_import_wh == 0


@pytest.mark.parametrize(
    "kwargs", [{"soc": 5}, {"available": False}, {"minutes": -1}, {"power": 0}]
)
def test_remaining_dwell_never_overrides_unavailable_or_unserviceable_load(kwargs):
    config, inputs = case(**kwargs)
    result = plan(config, inputs)
    assert result.load_plans[0].planned_energy_wh == 0
    if kwargs.get("soc") == 5:
        assert result.load_plans[0].rejected_candidates


def test_known_end_and_stability_boundaries_preserve_hourly_energy():
    config, inputs = case()
    state = replace(
        inputs.load_states[0], predrain_not_before=NOW + timedelta(minutes=10)
    )
    plain = build_slots(config, NOW, 80, [5], load_states=())
    split = build_slots(config, NOW, 80, [5], load_states=(state,))
    assert [slot.start.minute for slot in split.slots[:3]] == [0, 10, 20]
    for key in ("pv_wh", "ac_wh", "dc_wh"):
        assert sum(getattr(slot, key) for slot in plain.slots) == pytest.approx(
            sum(getattr(slot, key) for slot in split.slots)
        )


def test_stability_floor_applies_only_to_predrain_not_direct_surplus():
    config, inputs = case(pv=3000, soc=95)
    inputs = replace(
        inputs,
        load_states=(
            SurplusLoadState("load", predrain_not_before=NOW + timedelta(hours=1)),
        ),
    )
    result = plan(config, inputs)
    assert result.load_plans[0].active_now
    assert all(pass_no != 3 for _, _, pass_no, _ in result.load_plans[0].allocations)


@pytest.mark.parametrize(
    "hours,reason",
    [
        ((1, 1, 1, 1), "loads_exhausted_to_maximum"),
        ((0, 1, 1, 1), "continuous_load_or_peak_unproven"),
    ],
)
def test_feedin_reason_is_recorded_at_actual_continuity_gate(hours, reason):
    config, inputs, extra, load_plan, trajectory = _feedin_case(hours)
    decisions = []
    booked, _ = plan_feedin(
        config,
        inputs,
        20,
        extra,
        trajectory,
        load_plans=(load_plan,),
        decisions=decisions,
    )
    assert decisions[0] == (0, reason)
    assert (booked[0] > 0) == (hours[0] > 0)


def test_paused_disabled_and_manual_feedin_are_distinguished():
    config, inputs, extra, load_plan, trajectory = _feedin_case((1, 1, 1, 1))
    decisions = []
    paused = replace(config, feedin=replace(config.feedin, automatic_enabled=False))
    assert not any(
        plan_feedin(
            paused,
            inputs,
            20,
            extra,
            trajectory,
            load_plans=(load_plan,),
            decisions=decisions,
        )[0]
    )
    assert {reason for _, reason in decisions} == {"runtime_paused"}
    disabled = plan(
        replace(config, feedin=replace(config.feedin, enabled=False)), inputs
    )
    assert {reason for _, reason in disabled.feedin_decisions} == {"feature_disabled"}
    decisions = []
    manual = replace(config, feedin=replace(config.feedin, manual_w=20))
    assert any(
        plan_feedin(
            manual, inputs, 20, extra, trajectory, load_plans=(), decisions=decisions
        )[0]
    )
    assert decisions[0][1] == "manual_setpoint"


@pytest.mark.parametrize("soc,expected", [(99, 10), (100, 0)])
def test_charge_gate_target_caps_remaining_dwell(soc, expected):
    config, inputs = case(minutes=90)
    load = replace(
        config.loads[0], energy_limited=True, capacity_wh=1000, gate_stop_capable=True
    )
    config = replace(config, loads=(load,))
    inputs = replace(
        inputs, load_states=(replace(inputs.load_states[0], soc_percent=soc),)
    )
    base = simulate(config, inputs, 20)
    plans, extra, trajectory = allocate_loads(config, inputs, 20, base)
    assert plans[0].planned_energy_wh == expected
    assert sum(extra) == expected
    assert trajectory.total_import_wh == 0


def test_stability_wait_vetoes_early_predrain_with_explicit_reason():
    config, inputs, _, _, _ = _feedin_case((0, 0, 0), pv=(0, 450, 170))
    config = replace(config, loads=(replace(config.loads[0], min_runtime_min=60),))
    inputs = replace(inputs, start_soc_percent=90)
    base = simulate(config, inputs, 20)
    before, _, _ = allocate_loads(config, inputs, 20, base)
    assert any(a[2] == 3 for a in before[0].allocations)
    release = inputs.slots[-1].start + timedelta(hours=1)
    inputs = replace(
        inputs, load_states=(SurplusLoadState("deh", predrain_not_before=release),)
    )
    after, _, trajectory = allocate_loads(config, inputs, 20, base)
    assert not any(a[2] == 3 for a in after[0].allocations)
    assert any(
        reason == "waiting for stable plan"
        for _, reason in after[0].rejected_candidates
    )
    assert trajectory.total_import_wh == 0


def test_aux_output_release_splits_slots_independently_of_root_pause():
    """A shorter output pause must not inherit Root's later restart deadline."""
    from core.model import CascadeRuntimeState

    config, inputs = case()
    root_release = NOW + timedelta(minutes=45)
    output_release = NOW + timedelta(minutes=20)
    runtime = CascadeRuntimeState(
        "chain",
        NOW.date(),
        "running",
        active_source_id="b1",
        aux_path_releases=(output_release,),
    )
    plain = build_slots(config, NOW, 80, [5])
    split = build_slots(
        config,
        NOW,
        80,
        [5],
        load_states=(SurplusLoadState("load", not_before=root_release),),
        cascade_runtime_states=(runtime,),
    )
    assert [slot.start.minute for slot in split.slots[:3]] == [0, 20, 45]
    assert split.cascade_runtime_states == (runtime,)
    for key in ("pv_wh", "ac_wh", "dc_wh"):
        assert sum(getattr(slot, key) for slot in plain.slots) == pytest.approx(
            sum(getattr(slot, key) for slot in split.slots)
        )
    only_output = build_slots(config, NOW, 80, [5], cascade_runtime_states=(runtime,))
    assert [slot.start.minute for slot in only_output.slots[:2]] == [0, 20]
