"""F-DC-SOURCE-STABILITY: fewer physical edges and bound rolling phases."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.market import slot_weights
from core.model import MarketPrice, SupportParams
from core.optimize import plan
from core.reserve import (
    _simulate_reserve_policy,
    _simulate_reserve_reference,
    reserve_planning_scope,
    source_schedule_mode,
)
from core.reserve_sources import (
    market_sources,
    phase_locks,
    valid_shift,
)
from core.simulate import simulate
from core.simulation_steps import switching_schedule
from core.source_stability import smooth_sources, switches
from test_dc_pv_market import dc_plant
from test_dc_source_blocks import source_steps
from test_market import priced


def edges(states, initial=(False, False)):
    return sum(switches(a, b) for a, b in zip((initial, *states), states, strict=False))


def forecast_steps(count=12):
    config, steps = source_steps(count, 6)
    return config, tuple(
        replace(s, slot=replace(s.slot, index=i // 12)) for i, s in enumerate(steps)
    )


@pytest.mark.parametrize("both", [False, True])
def test_equal_duration_combinations_join_both_on_and_off_islands(both):
    c, steps = forecast_steps()
    states = tuple((i % 4 in (0, 1), both and i % 3 == 0) for i in range(12))
    result = smooth_sources(c, steps, states, [2] * 12)
    assert Counter(result) == Counter(states)
    assert edges(result) < edges(states)
    assert smooth_sources(c, steps, result, [2] * 12) == result
    assert valid_shift(
        c,
        tuple(
            replace(s, flow=replace(s.flow, support_dc24=a, support_dc48=b))
            for s, (a, b) in zip(steps, states, strict=True)
        ),
        tuple(
            replace(s, flow=replace(s.flow, support_dc24=a, support_dc48=b))
            for s, (a, b) in zip(steps, result, strict=True)
        ),
        [2] * 12,
    )


@pytest.mark.parametrize(
    "boundary", ["price", "weight", "duration", "gap", "phase", "slot", "limit"]
)
def test_smoothing_respects_price_partial_time_phase_and_input_boundaries(boundary):
    c, steps = forecast_steps(24 if boundary == "limit" else 12)
    steps = list(steps)
    split = 12 if boundary == "limit" else 6
    states = tuple((i < split // 2 or i == split + 1, False) for i in range(len(steps)))
    weights = [2] * len(steps)
    prices = ()
    start = steps[0].slot.start
    if boundary == "price":
        prices = (MarketPrice(start, start + timedelta(minutes=30), 100),)
    elif boundary == "weight":
        weights[split:] = [1] * (len(steps) - split)
    elif boundary == "phase":
        c = replace(
            c,
            support=replace(
                c.support, dc24_phase_until=(start + timedelta(minutes=30)).timestamp()
            ),
        )
    else:
        for i in range(split, len(steps)):
            slot = steps[i].slot
            if boundary == "duration":
                slot = replace(slot, duration=slot.duration / 2)
            elif boundary == "gap":
                slot = replace(slot, start=slot.start + timedelta(minutes=5))
            elif boundary == "slot":
                slot = replace(slot, index=1)
            elif boundary == "limit":
                slot = replace(slot, index=0)
            steps[i] = replace(steps[i], slot=slot)
    result = smooth_sources(c, steps, states, weights, prices)
    assert Counter(result[:split]) == Counter(states[:split])
    assert Counter(result[split:]) == Counter(states[split:])
    if boundary == "phase":
        assert result[:split] == states[:split]
    assert edges(result) <= edges(states)


def test_source_smoothing_keeps_partial_price_steps_and_legacy_dst_times():
    c, steps = forecast_steps()
    states = tuple((i % 2 == 0, False) for i in range(12))
    start = steps[0].slot.start
    # The first price changes inside a simulation step: neither mixed step
    # may be exchanged with a wholly covered or wholly unknown interval.
    price = MarketPrice(start + timedelta(minutes=2), start + timedelta(minutes=7), 100)
    result = smooth_sources(c, steps, states, [1] * 12, (price,))
    assert result[:2] == states[:2]
    for zone in (None, ZoneInfo("Europe/Berlin")):
        at = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        adjusted = tuple(
            replace(
                s,
                slot=replace(
                    s.slot,
                    start=(at + timedelta(minutes=5 * i)).astimezone(zone)
                    if zone
                    else s.slot.start.replace(tzinfo=None),
                ),
            )
            for i, s in enumerate(steps)
        )
        smoothed = smooth_sources(c, adjusted, states, [1] * 12)
        assert Counter(smoothed) == Counter(states)
        assert edges(smoothed) < edges(states)
    assert smooth_sources(c, (), (), ()) == ()


def test_reordered_sources_pass_sequential_physics_with_identical_hourly_import():
    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [300, 100, 100])
    traces = {}
    reference = _simulate_reserve_reference(c, i, None, 1, None, traces=traces)
    steps = traces[id(reference.flows)]
    states = tuple((j in (0, 1, 6) or 12 <= j < 21, False) for j in range(36))
    original = _simulate_reserve_policy(
        c, i, None, 1, None, fixed_sources=states, fixed_limits=(0,) * 36, traces=traces
    )
    weights = [2] * 12 + [1] * 24
    proposed = smooth_sources(c, steps, states, weights, i.market_prices)
    result = _simulate_reserve_policy(
        c,
        i,
        None,
        1,
        None,
        fixed_sources=proposed,
        fixed_limits=(0,) * 36,
        traces=traces,
    )
    assert edges(proposed) < edges(states)
    assert [f.grid_import_wh for f in result.flows] == pytest.approx(
        [f.grid_import_wh for f in original.flows]
    )
    assert result.end_soc_percent == pytest.approx(original.end_soc_percent)
    assert result.total_export_wh == original.total_export_wh == 0
    assert sum(f.unserved_dc_wh for f in result.flows) == 0
    assert valid_shift(c, traces[id(original.flows)], traces[id(result.flows)], weights)


def test_final_schedule_is_smoothed_once_after_booking_and_probe_mode_resets():
    c = dc_plant()
    c = replace(
        c,
        support=replace(c.support, dc48_available=False, psu24_eta=0.89, dcdc_eta=0.93),
    )
    i = priced(
        [(0, 0, dc) for dc in (70, 60, 60, 100, 90, 90, 70, 100)] + [(700, 0, 0)],
        [250, 300, 250, 200, 150, 150, 300, 250, 100],
    )
    with pytest.raises(RuntimeError), source_schedule_mode(False):
        probe = simulate(c, i, 20)
        assert len(switching_schedule(i.slots, probe.flows)) - 1 == 9
        raise RuntimeError("cancelled probe")
    result = plan(c, i)
    assert len(switching_schedule(i.slots, result.trajectory.flows)) - 1 == 5
    assert result.trajectory.total_import_wh == pytest.approx(probe.total_import_wh)
    assert len(switching_schedule(i.slots, simulate(c, i, 20).flows)) - 1 == 5
    with reserve_planning_scope():
        with source_schedule_mode(False):
            cached_probe = simulate(c, i, 20)
        cached_final = simulate(c, i, 20)
        assert len(switching_schedule(i.slots, cached_probe.flows)) - 1 == 9
        assert len(switching_schedule(i.slots, cached_final.flows)) - 1 == 5


def test_rolling_market_plans_retain_confirmed_on_phase_until_expiry():
    c = dc_plant()
    initial = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [300, 100, 100])
    until = (initial.now + timedelta(minutes=30)).timestamp()
    c = replace(c, support=replace(c.support, dc24_active=True, dc24_phase_until=until))
    # Same hourly forecast and prices, different remaining first-slot budget.
    for minutes in (0, 4, 9, 14, 19, 24):
        now = initial.now + timedelta(minutes=minutes)
        first = replace(
            initial.slots[0],
            start=now,
            duration=(60 - minutes) / 60,
            dc_wh=100 * (60 - minutes) / 60,
        )
        i = replace(initial, now=now, slots=(first, *initial.slots[1:]))
        result = simulate(c, i, 20)
        assert result.flows[0].support_dc24_start
        assert sum(f.unserved_dc_wh for f in result.flows) == 0
        assert result.min_soc_percent >= c.battery.soc_min_percent
    now = initial.now + timedelta(minutes=31)
    remaining = replace(
        initial.slots[0], start=now, duration=29 / 60, dc_wh=100 * 29 / 60
    )
    after = replace(initial, now=now, slots=(remaining, *initial.slots[1:]))
    neutral = replace(c, support=replace(c.support, dc24_phase_until=0))
    assert simulate(c, after, 20) == simulate(neutral, after, 20)
    assert not simulate(neutral, initial, 20).flows[0].support_dc24_start


def test_market_pipeline_removes_four_edges_at_identical_hourly_energy():
    """Small physical counterpart of October's 20 -> 16 live-plan regression."""
    c = dc_plant()
    c = replace(
        c,
        support=replace(c.support, dc48_available=False, psu24_eta=0.89, dcdc_eta=0.93),
    )
    i = priced(
        [(0, 0, dc) for dc in (70, 60, 60, 100, 90, 90, 70, 100)] + [(700, 0, 0)],
        [250, 300, 250, 200, 150, 150, 300, 250, 100],
    )
    traces = {}
    reference = _simulate_reserve_reference(c, i, None, 1, None, traces=traces)
    steps = traces[id(reference.flows)]
    weights = slot_weights(tuple(s.slot for s in steps), i.market_prices)
    raw = _simulate_reserve_policy(
        c,
        i,
        None,
        1,
        None,
        fixed_sources=market_sources(c, steps, weights),
        fixed_limits=tuple(s.flow.inverter_limit_w for s in steps),
        traces=traces,
    )
    result = simulate(c, i, 20)
    assert len(switching_schedule(i.slots, raw.flows)) - 1 == 9
    assert len(switching_schedule(i.slots, result.flows)) - 1 == 5
    assert [f.grid_import_wh for f in result.flows] == pytest.approx(
        [f.grid_import_wh for f in raw.flows]
    )
    assert result.end_soc_percent == pytest.approx(raw.end_soc_percent)
    assert result.min_soc_percent == pytest.approx(raw.min_soc_percent)
    assert result.total_export_wh == 0
    assert sum(f.unserved_dc_wh for f in result.flows) == 0


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("source", ["dc24", "dc48"])
def test_market_cannot_reverse_bound_source_independently_of_other_psu(active, source):
    c, steps = source_steps()
    until = (steps[0].slot.start + timedelta(minutes=30)).timestamp()
    c = replace(
        c,
        support=replace(
            c.support, **{source + "_active": active, source + "_phase_until": until}
        ),
    )
    if source == "dc48":

        def swap(flow):
            return replace(
                flow, support_dc24=flow.support_dc48, support_dc48=flow.support_dc24
            )

        steps = tuple(
            replace(s, flow=swap(s.flow), choices=tuple(swap(f) for f in s.choices))
            for s in steps
        )
    steps = tuple(
        replace(s, flow=s.choices[0 if active else 1]) if j < 6 else s
        for j, s in enumerate(steps)
    )
    selected = market_sources(c, steps, [2] * 24)
    assert all(state[0 if source == "dc24" else 1] is active for state in selected[:6])
    assert phase_locks(c, steps[6].slot) == (False, False)


def test_off_phase_yields_to_safety_manual_and_full_pv():
    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (150, 0, 0)], [300, 100, 100])
    until = (i.now + timedelta(minutes=30)).timestamp()
    c = replace(
        c, support=replace(c.support, dc24_phase_until=until, dc48_phase_until=until)
    )
    ordinary = simulate(c, i, 20)
    unbound = simulate(
        replace(c, support=replace(c.support, dc24_phase_until=0, dc48_phase_until=0)),
        i,
        20,
    )
    assert ordinary.flows == unbound.flows
    assert ordinary.flows[0].support_dc24_start
    for cfg, inputs in (
        (c, replace(i, start_soc_percent=5)),
        (replace(c, support=replace(c.support, dc24_forced_on=True)), i),
    ):
        result = simulate(cfg, inputs, 20)
        assert result.flows[0].support_dc24_start
        assert sum(f.unserved_dc_wh for f in result.flows) == 0
    on = replace(c, support=replace(c.support, dc24_active=True, dc48_active=True))
    solar = replace(i, slots=(replace(i.slots[0], pv_wh=200),))
    assert not simulate(on, solar, 20).flows[0].support_dc24_start


def test_confirmed_off_uses_existing_budget_instead_of_reversing_in_cheap_hour():
    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [100, 300, 100])
    unbound = simulate(c, i, 20)
    c = replace(
        c,
        support=replace(
            c.support, dc24_phase_until=(i.now + timedelta(minutes=30)).timestamp()
        ),
    )
    bound = simulate(c, i, 20)
    assert unbound.flows[0].support_dc24_start
    assert not bound.flows[0].support_dc24_start
    assert bound.total_import_wh <= unbound.total_import_wh + 1e-6
    assert bound.end_soc_percent >= unbound.end_soc_percent - 1e-6
    assert bound.reserve_decision.dc_budget_wh == unbound.reserve_decision.dc_budget_wh
    assert sum(f.unserved_dc_wh for f in bound.flows) == 0


@pytest.mark.parametrize("deadline", [-1, float("nan"), float("inf")])
def test_phase_deadlines_reject_invalid_recordings(deadline):
    with pytest.raises(ValueError):
        SupportParams(dc24_phase_until=deadline)
