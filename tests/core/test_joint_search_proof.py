"""A generated proposal earns permission only from its returned physical trace."""

from dataclasses import replace

import pytest
from core.joint_dispatch import dispatch
from core.reserve import (
    _simulate_reserve_policy,
    _simulate_reserve_reference,
    source_schedule_mode,
)
from test_joint_dispatch import forecast, plant


@pytest.mark.parametrize(
    "physical_result", ["accepted", "unchanged", "extra_import", "unknown_price"]
)
def test_search_proves_returned_actuations_including_unknown_intervals(physical_result):
    c = plant()
    i = forecast([(0, 0, 100), (0, 100, 0), (0, 0, 0), (100, 0, 0)], [0, 300, 0, 0])
    i = replace(
        i,
        market_prices=tuple(price for n, price in enumerate(i.market_prices) if n != 2),
    )
    traces = {}
    reference = _simulate_reserve_reference(c, i, None, 1, None, traces=traces)
    original = traces[id(reference.flows)]

    def evaluate(proposal):
        if physical_result == "unchanged":
            # An actuator or compulsory protection may retain the safe trace.
            return reference, original
        local = {}
        trial = _simulate_reserve_policy(
            c,
            i,
            None,
            1,
            None,
            fixed_sources=tuple(
                (flow.support_dc24, flow.support_dc48) for flow in proposal
            ),
            fixed_limits=tuple(flow.inverter_limit_w for flow in proposal),
            traces=local,
        )
        steps = local[id(trial.flows)]
        if physical_result == "extra_import":
            trial = replace(trial, total_import_wh=trial.total_import_wh + 1)
        elif physical_result == "unknown_price":
            # A source that ignores OFF during an unknown interval must not
            # gain permission merely because known receivers met their budget.
            steps = tuple(
                replace(step, flow=replace(step.flow, support_dc24=True))
                if step.slot.index == 2
                else step
                for step in steps
            )
        return trial, steps

    result = dispatch(c, reference, original, i.market_prices, evaluate)
    summary = result.reserve_decision.joint
    assert 0 < summary.joint_validations <= 32
    if physical_result == "accepted":
        assert result.flows[1].inverter_output_wh == pytest.approx(100)
        assert result.total_import_wh == pytest.approx(reference.total_import_wh)
    else:
        reason = "no_improvement" if physical_result == "unchanged" else physical_result
        assert reason in summary.joint_rejection_reasons
        assert result.flows == reference.flows


def test_no_flexible_dc_with_unavailable_sources_has_distinct_diagnosis():
    from core.simulate import simulate

    c = plant()
    c = replace(
        c, support=replace(c.support, dc24_available=False, dc48_available=False)
    )
    i = forecast([(0, 100, 100), (0, 100, 0)], [0, 300])
    result = simulate(c, i, 20)
    assert result.reserve_decision.joint.joint_status == "source_unavailable"
    assert result.reserve_decision.joint.unavoidable_dc_draw_wh == pytest.approx(100)


def test_donor_block_cannot_assume_a_source_unavailable_in_its_next_step():
    from datetime import timedelta

    from core.model import HourSlot, MarketPrice
    from core.simulate import simulate

    c = plant()
    c = replace(c, support=replace(c.support, psu24_max_power_w=101))
    i = forecast([(0, 0, 0)] * 4, [0, 0, 300, 0])
    now = i.now
    slots = (
        HourSlot(0, now, 1 / 12, 0, 0, 0, 100 / 12),
        HourSlot(1, now + timedelta(minutes=5), 5 / 12, 0, 0, 0, 200 * 5 / 12),
        HourSlot(2, now + timedelta(minutes=30), 1, 0, 0, 100, 0),
        HourSlot(3, now + timedelta(minutes=90), 1, 0, 100, 0, 0),
    )
    prices = tuple(
        MarketPrice(slot.start, slot.start + timedelta(hours=slot.duration), price)
        for slot, price in zip(slots, (0, 0, 300, 0), strict=True)
    )
    i = replace(i, slots=slots, market_prices=prices)
    with source_schedule_mode(False):
        reference = simulate(c, i, 20)
    result = simulate(c, i, 20)
    assert result.flows[2].inverter_output_wh == pytest.approx(
        reference.flows[2].inverter_output_wh
    )
    assert not result.flows[1].support_dc24
    assert result.reserve_decision.joint.joint_shifted_wh == 0


def test_short_cheap_donor_cannot_create_a_ten_minute_source_phase():
    from datetime import timedelta

    from core.model import HourSlot, MarketPrice
    from core.simulate import simulate

    c = plant()
    i = forecast([(0, 0, 0)] * 3, [0, 300, 0])
    slots = (
        HourSlot(0, i.now, 1 / 6, 0, 0, 0, 100 / 6),
        HourSlot(1, i.now + timedelta(minutes=10), 1, 0, 0, 100, 0),
        HourSlot(2, i.now + timedelta(minutes=70), 1, 0, 100 / 6, 0, 0),
    )
    prices = tuple(
        MarketPrice(slot.start, slot.start + timedelta(hours=slot.duration), value)
        for slot, value in zip(slots, (0, 300, 0), strict=True)
    )
    i = replace(i, slots=slots, market_prices=prices)
    result = simulate(c, i, 20)
    assert result.flows[0].psu24_delivered_wh == 0
    assert result.flows[1].inverter_output_wh == 0
    assert result.reserve_decision.joint.joint_shifted_wh == 0
