"""Reserve memoization belongs to one plan and never bypasses cancellation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from core import reserve
from core.model import (
    BatteryParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.planning_control import PlanningCancelled, cancellation_scope
from core.simulate import simulate, step_hour


def context():
    config = SystemConfig(
        battery=BatteryParams(1000),
        support=SupportParams(configured=True, coordinated=True),
        reserve=ReserveParams(True),
    )
    now = datetime(2026, 9, 27, 20)
    inputs = PlanInputs(now, 80, (HourSlot(0, now, 1, 20, 0, 100, 60),))
    return config, inputs


def test_nested_scopes_reuse_probes_but_the_next_worker_recomputes(monkeypatch):
    config, inputs = context()
    real_step = reserve.step_hour
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_step(*args, **kwargs)

    monkeypatch.setattr(reserve, "step_hour", counted)
    with reserve.reserve_planning_scope():
        expected = simulate(config, inputs, 20)
        completed = calls
        with reserve.reserve_planning_scope():
            assert simulate(config, inputs, 20) == expected
        assert simulate(config, inputs, 20) == expected
        assert calls == completed
    with reserve.reserve_planning_scope():
        assert simulate(config, inputs, 20) == expected
    assert calls > completed


def test_cancelled_worker_cannot_return_cached_result_or_leak_its_scope():
    config, inputs = context()
    with pytest.raises(PlanningCancelled), reserve.reserve_planning_scope():
        simulate(config, inputs, 20)
        with cancellation_scope(lambda: True):
            simulate(config, inputs, 20)
    assert reserve._reserve_cache.get() is None
    assert simulate(config, inputs, 20).flows


def test_eviction_and_changed_physics_keep_uncached_energy_results(monkeypatch):
    config, inputs = context()
    monkeypatch.setattr(reserve, "MAX_CACHED_RESERVE_PROBES", 1)
    monkeypatch.setattr(reserve, "MAX_CACHED_BATTERY_STEPS", 1)
    monkeypatch.setattr(reserve, "MAX_CACHED_RESERVE_FLOWS", 1)
    other = replace(config, charger=replace(config.charger, max_power_w=0))
    sunny = replace(inputs, slots=(replace(inputs.slots[0], pv_wh=200),))
    variants = [
        (config, inputs, (0.0,), (1.0,), (0.0,)),
        (other, sunny, (10.0,), (1.2,), (5.0,)),
        (config, sunny, (5.0,), (0.5,), (0.0,)),
    ]
    expected = [
        simulate(c, i, 20, x, pv_scale=p, feedin_wh=f) for c, i, x, p, f in variants
    ]
    with reserve.reserve_planning_scope():
        for index in [0, 1, 2, 0]:
            c, i, x, p, f = variants[index]
            assert simulate(c, i, 20, x, pv_scale=p, feedin_wh=f) == expected[index]


def test_envelope_reused_for_ac_off_and_margin_retries_but_not_changed_inputs(
    monkeypatch,
):
    config, base = context()
    inputs = replace(
        base,
        slots=tuple(
            HourSlot(index, base.now + timedelta(hours=index), 1, index, pv, ac, 60)
            for index, (pv, ac) in enumerate([(0, 600), (700, 0), (0, 400)])
        ),
    )
    variants = [
        (config, inputs, None, 1.0, None, True, 0.0),
        (config, inputs, None, 1.0, None, False, 0.0),
        (config, inputs, None, 1.0, None, True, 100.0),
        (config, replace(inputs, start_soc_percent=60), None, 1.0, None, True, 0.0),
        (config, inputs, (10.0, 0.0, 0.0), 1.0, None, True, 0.0),
        (config, inputs, None, (0.8, 0.8, 0.8), None, True, 0.0),
        (config, inputs, None, 1.0, (0.0, 10.0, 0.0), True, 0.0),
        (
            replace(config, inverter=replace(config.inverter, eta=0.8)),
            inputs,
            None,
            1.0,
            None,
            True,
            0.0,
        ),
    ]

    def run(variant):
        c, i, extra, scale, feedin, allow_ac, margin = variant
        return reserve._simulate_reserve_policy(
            c, i, extra, scale, feedin, allow_ac=allow_ac, ac_margin_wh=margin
        )

    expected = [run(variant) for variant in variants]
    assert expected[0].total_import_wh < expected[1].total_import_wh
    calls = 0
    real_envelope = reserve.preparation_envelope

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_envelope(*args, **kwargs)

    monkeypatch.setattr(reserve, "preparation_envelope", counted)
    with reserve.reserve_planning_scope():
        for index, variant in enumerate(variants):
            assert run(variant) == expected[index]
            if index == 2:
                assert calls == 1
        completed = calls
        for index, variant in enumerate(variants):
            assert run(variant) == expected[index]
        assert calls == completed == len(variants) - 2
    with reserve.reserve_planning_scope():
        assert run(variants[0]) == expected[0]
    assert calls == completed + 1


def test_cached_physics_distinguishes_every_operating_input(monkeypatch):
    config, inputs = context()
    slot = replace(inputs.slots[0], pv_wh=50, ac_wh=100, dc_wh=60)
    base = {"soc_percent": 80.0, "threshold_percent": 20.0}
    variants = [
        base,
        {**base, "soc_percent": 50.0},
        {**base, "threshold_percent": 90.0},
        {**base, "extra_ac_wh": 10.0},
        {**base, "dc24_from_grid": True},
        {**base, "dc48_support": True},
        {**base, "pv_scale": 0.5},
        {**base, "feedin_wh": 10.0},
        {**base, "inverter_limit_w": 100.0},
    ]
    expected = [step_hour(config, slot=slot, **variant) for variant in variants]
    calls = 0
    real_step = reserve.step_hour

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_step(*args, **kwargs)

    monkeypatch.setattr(reserve, "step_hour", counted)
    with reserve.reserve_planning_scope():
        for index, variant in enumerate(variants):
            result = reserve._step_hour(config, slot=slot, **variant)
            assert result == expected[index]
            assert reserve._step_hour(config, slot=slot, **variant) is result
        assert calls == len(variants)
        other = replace(config, inverter=ConverterParams(0))
        assert reserve._step_hour(other, slot=slot, **base) == step_hour(
            other, slot=slot, **base
        )
        assert calls == len(variants) + 1
    assert reserve._step_hour(config, slot=slot, **base) == expected[0]
    assert calls == len(variants) + 2


def test_physics_cache_eviction_preserves_recent_results_and_recomputes_old_ones(
    monkeypatch,
):
    monkeypatch.setattr(reserve, "MAX_CACHED_RESERVE_FLOWS", 2)
    config, inputs = context()
    a, b, c = (
        replace(inputs.slots[0], start=inputs.now + timedelta(hours=offset))
        for offset in range(3)
    )
    with reserve.reserve_planning_scope():
        first = reserve._step_hour(config, 80, a, 20)
        second = reserve._step_hour(config, 80, b, 20)
        assert reserve._step_hour(config, 80, a, 20) is first
        reserve._step_hour(config, 80, c, 20)
        assert reserve._step_hour(config, 80, a, 20) is first
        recomputed = reserve._step_hour(config, 80, b, 20)
        assert recomputed == second
        assert recomputed is not second
        cache = reserve._reserve_cache.get()
        assert cache is not None and len(cache.flows) == 2


def test_cached_physics_stops_after_cancellation_and_keeps_dst_deficits_separate():
    from zoneinfo import ZoneInfo

    config, inputs = context()
    config = replace(config, support=replace(config.support, dcdc_max_power_w=0))
    a = replace(
        inputs.slots[0],
        start=datetime(2026, 10, 25, 2, tzinfo=ZoneInfo("Europe/Berlin"), fold=0),
        duration=1 / 12,
    )
    b = replace(a, start=a.start.replace(fold=1))
    cancelled = False
    with reserve.reserve_planning_scope(), cancellation_scope(lambda: cancelled):
        first = reserve._step_hour(config, 80, a, 20)
        second = reserve._step_hour(config, 80, b, 20)
        assert first.dc_deficit_intervals and second.dc_deficit_intervals
        first_start = first.dc_deficit_intervals[0].start.astimezone(UTC)
        second_start = second.dc_deficit_intervals[0].start.astimezone(UTC)
        assert second_start - first_start == timedelta(hours=1)
        cancelled = True
        with pytest.raises(PlanningCancelled):
            reserve._step_hour(config, 80, a, 20)
    assert reserve._reserve_cache.get() is None


def test_market_publication_reused_across_candidates_but_changed_prices_recompute(
    monkeypatch,
):
    from datetime import UTC, timedelta

    from core.model import MarketPrice

    config, inputs = context()
    now = inputs.now.replace(tzinfo=UTC)
    inputs = replace(
        inputs,
        now=now,
        slots=(replace(inputs.slots[0], start=now),),
        market_prices=(MarketPrice(now, now + timedelta(hours=1), 100),),
    )
    real_weights = reserve.slot_weights
    calls = []

    def counted(*args):
        calls.append(args)
        return real_weights(*args)

    monkeypatch.setattr(reserve, "slot_weights", counted)
    with reserve.reserve_planning_scope():
        for extra in [(0.0,), (10.0,), (20.0,)]:
            simulate(config, inputs, 20, extra)
        assert len(calls) == 1
        changed = replace(inputs, market_prices=())
        simulate(config, changed, 20)
        assert len(calls) == 2
    simulate(config, inputs, 20, (30.0,))
    assert len(calls) > 2


def test_cached_market_grid_keeps_repeated_dst_hours_and_mutable_fallbacks_separate():
    from datetime import UTC, timedelta
    from zoneinfo import ZoneInfo

    from core.model import MarketPrice

    _, base = context()
    tz = ZoneInfo("Europe/Berlin")
    first = datetime(2026, 10, 25, 2, tzinfo=tz, fold=0)
    second = first.replace(fold=1)
    prices = tuple(
        MarketPrice(at, at + timedelta(hours=1), price)
        for at, price in [
            (first.astimezone(UTC), 100),
            (second.astimezone(UTC), 300),
        ]
    )
    a = replace(
        base,
        now=first,
        slots=(replace(base.slots[0], start=first),),
        market_prices=prices,
    )
    b = replace(a, now=second, slots=(replace(a.slots[0], start=second),))
    with reserve.reserve_planning_scope():
        steps_a = reserve._steps(SystemConfig(), a, None)
        steps_b = reserve._steps(SystemConfig(), b, None)
        before = reserve._market_weights(a, steps_a)
        after = reserve._market_weights(b, steps_b)
        assert before == [1.0] * 12
        assert after == [3.0] * 12
        before[0] = None
        assert reserve._market_weights(a, steps_a)[0] == 1.0
