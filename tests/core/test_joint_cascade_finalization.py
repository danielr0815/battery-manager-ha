"""Cascade alternatives share one final joint dispatch after accepted bookings."""

from datetime import UTC, datetime, timedelta

import pytest
from core import optimize as optimize_core
from core import reserve as reserve_core
from core.model import (
    CascadeMember,
    FeedInParams,
    HourSlot,
    LoadCascade,
    MarketPrice,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.reserve import simulate_fixed_dispatch


def test_cascade_probes_do_not_restart_joint_search_and_finalize_accepted_bookings(
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime(2026, 8, 23, 6, tzinfo=UTC)
    config = SystemConfig(
        loads=(
            SurplusLoad(
                "b1",
                "B1",
                300,
                min_runtime_min=30,
                min_off_min=30,
                energy_limited=True,
                capacity_wh=2000,
                target_soc_percent=90,
                gate_stop_capable=True,
            ),
            SurplusLoad("leaf", "Leaf", 300, min_runtime_min=30, min_off_min=30),
        ),
        cascades=(LoadCascade("chain", (CascadeMember("b1", 20, 50),), "leaf"),),
        reserve=ReserveParams(True, 1),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        feedin=FeedInParams(enabled=True, max_w=1000, manual_w=200),
    )
    slots = tuple(
        HourSlot(
            index,
            now + timedelta(hours=index),
            1,
            6 + index,
            0 if index == 0 else 2000,
            0,
            0,
        )
        for index in range(6)
    )
    inputs = PlanInputs(
        now,
        25,
        slots,
        (
            SurplusLoadState(
                "b1", soc_percent=90, soc_source="live", soc_observed_at=now
            ),
            SurplusLoadState("leaf"),
        ),
        market_prices=tuple(
            MarketPrice(slot.start, slot.start + timedelta(hours=1), index * 50)
            for index, slot in enumerate(slots)
        ),
    )
    probes = []
    joint_searches = []
    finalized = []
    accepted = []
    legacy = optimize_core._plan_legacy
    dispatch = reserve_core.joint_dispatch
    finalize = optimize_core._finalize_joint_plan
    augment = optimize_core.augment_cascade_plans

    def legacy_spy(config, inputs, **kwargs):
        result = legacy(config, inputs, **kwargs)
        assert result.trajectory.reserve_decision.joint is None
        probes.append((inputs, result))
        return result

    def dispatch_spy(*args, **kwargs):
        joint_searches.append(args)
        return dispatch(*args, **kwargs)

    def finalize_spy(config, inputs, result):
        finalized.append((config, inputs, result))
        return finalize(config, inputs, result)

    def augment_spy(*args):
        result = augment(*args)
        accepted.append(result)
        return result

    monkeypatch.setattr(optimize_core, "_plan_legacy", legacy_spy)
    monkeypatch.setattr(reserve_core, "joint_dispatch", dispatch_spy)
    monkeypatch.setattr(optimize_core, "_finalize_joint_plan", finalize_spy)
    monkeypatch.setattr(optimize_core, "augment_cascade_plans", augment_spy)
    result = optimize_core.plan(config, inputs)

    assert len(probes) == 3, "Exercise ordinary Aux and a rejected shortened replan"
    assert len(joint_searches) == len(finalized) == 1
    selected = accepted[0]
    assert selected.trajectory is probes[1][1].trajectory
    assert selected.trajectory is not probes[-1][1].trajectory
    final_config, final_inputs, final_reference = finalized[0]
    assert final_inputs is probes[1][0]
    assert final_reference is selected
    assert result.load_plans == selected.load_plans
    assert result.cascade_plans == selected.cascade_plans
    assert all(load.managed_by_cascade for load in result.load_plans)
    assert result.cascade_plans[0].planned_aux_energy_wh == pytest.approx(300)
    assert result.cascade_plans[0].planned_root_energy_wh == pytest.approx(1800)
    assert result.feedin_schedule_w == selected.feedin_schedule_w
    assert sum(flow.feedin_wh for flow in result.trajectory.flows) > 500
    assert tuple(flow.extra_ac_wh for flow in result.trajectory.flows) == pytest.approx(
        tuple(flow.extra_ac_wh for flow in selected.trajectory.flows)
    )
    extra = tuple(flow.extra_ac_wh for flow in selected.trajectory.flows)
    feedin = tuple(
        power * slot.duration
        for power, slot in zip(
            result.feedin_schedule_w, final_inputs.slots, strict=True
        )
    )
    physical = simulate_fixed_dispatch(
        final_config, final_inputs, extra, result.trajectory, feedin
    )
    assert physical.total_import_wh == pytest.approx(result.trajectory.total_import_wh)
    assert physical.end_soc_percent == pytest.approx(result.trajectory.end_soc_percent)
    assert result.grid_import_kwh == pytest.approx(physical.total_import_wh / 1000)
    assert result.min_soc_percent == pytest.approx(physical.min_soc_percent)
