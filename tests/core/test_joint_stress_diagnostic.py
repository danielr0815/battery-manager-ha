"""Published stress reserve describes accepted AC/DC actuations and bookings."""

from datetime import UTC, datetime

import pytest
from core.model import (
    ControlParams,
    FeedInParams,
    ReserveParams,
    SupportParams,
    SurplusLoad,
    SystemConfig,
)
from core.optimize import _refill_index, _windowed_min_soc, plan
from core.policy import FULL_SOC_TOLERANCE_PERCENT
from core.reserve import simulate_fixed_dispatch
from core.series import build_slots
from core.simulate import simulate


def test_joint_stress_reserve_replays_final_actuations_in_accepted_recovery_window():
    config = SystemConfig(
        control=ControlParams(
            import_trade_ratio=0.1,
            predrain_pv_confidence=0.3,
            upper_pv_reserve=1.0,
        ),
        reserve=ReserveParams(True, 1),
        support=SupportParams(configured=True, coordinated=True),
        loads=(SurplusLoad("dehumidifier", "Entfeuchter", 400.0),),
        feedin=FeedInParams(enabled=True, max_w=1000, manual_w=1000),
    )
    inputs = build_slots(
        config, datetime(2026, 7, 4, 4, tzinfo=UTC), 50.0, [13.0, 11.0, 3.0]
    )
    result = plan(config, inputs)
    assert result.trajectory.reserve_decision.joint is not None
    booked = [
        alloc[0]
        for load in result.load_plans
        for alloc in load.allocations
        if alloc[2] in (2, 3)
    ]
    assert booked, "The scenario must exercise an accepted pre-drain booking"
    first = min(booked)
    recovery = _refill_index(
        result.trajectory,
        first,
        config.battery.soc_max_percent - FULL_SOC_TOLERANCE_PERCENT,
    )
    scale = [0.3 if first <= i <= recovery else 1.0 for i in range(len(inputs.slots))]
    extra = tuple(flow.extra_ac_wh for flow in result.trajectory.flows)
    feedin = tuple(flow.feedin_wh for flow in result.trajectory.flows)
    assert sum(feedin) > 1000, "The accepted plan must include material booked feed-in"
    accepted_stress = simulate_fixed_dispatch(
        config, inputs, extra, result.trajectory, feedin, pv_scale=scale
    )
    expected = _windowed_min_soc(accepted_stress, first, recovery)
    conservative_stress = simulate(
        config, inputs, result.threshold_percent, extra_ac_wh=extra, pv_scale=scale
    )
    assert abs(expected - _windowed_min_soc(conservative_stress, first, recovery)) > 0.1
    assert result.stressed_min_soc_percent == pytest.approx(expected)
