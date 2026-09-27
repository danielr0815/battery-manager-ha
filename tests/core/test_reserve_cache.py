"""Reserve memoization belongs to one plan and never bypasses cancellation."""

from dataclasses import replace
from datetime import datetime

import pytest
from core import reserve
from core.model import (
    BatteryParams,
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.planning_control import PlanningCancelled, cancellation_scope
from core.simulate import simulate


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
