"""Performance contracts use work counts and exact energy outcomes, not CPU speed."""

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from core.model import HourSlot, PlanInputs, ReserveParams, SupportParams, SystemConfig
from core.planning_control import PlanningCancelled, cancellation_scope, check_cancelled
from core.reserve import _expanded_slots
from core.simulate import simulate, step_hour


def test_operating_limit_matches_equivalent_plant_configuration():
    config = SystemConfig()
    slot = HourSlot(0, datetime(2026, 9, 27), 0.25, 8, 0, 200, 10)
    for limit in (0, 100, config.inverter.max_power_w):
        expected = step_hour(
            replace(config, inverter=replace(config.inverter, max_power_w=limit)),
            80,
            slot,
            20,
        )
        assert step_hour(config, 80, slot, 20, inverter_limit_w=limit) == expected
    for invalid in (-1, float("nan"), float("inf"), config.inverter.max_power_w + 1):
        with pytest.raises(ValueError, match="Inverter limit"):
            step_hour(config, 80, slot, 20, inverter_limit_w=invalid)


def test_reserve_configuration_construction_is_bounded_by_modes_not_horizon():
    import core.reserve as reserve

    now = datetime(2026, 9, 27)
    config = SystemConfig(
        reserve=ReserveParams(True),
        support=SupportParams(
            configured=True, coordinated=True, psu48_bus_voltage_v=48
        ),
    )
    inputs = PlanInputs(
        now,
        10,
        tuple(
            HourSlot(i, now + timedelta(hours=i), 1, i % 24, 0, 100, 60)
            for i in range(72)
        ),
    )
    made = []
    real_replace = reserve.replace

    def counted(value, **changes):
        if isinstance(value, SystemConfig):
            made.append(value)
        return real_replace(value, **changes)

    with patch.object(reserve, "replace", counted):
        result = simulate(config, inputs, 20)
    assert len(made) <= 4
    assert len(result.flows) == 72
    assert result.total_import_wh > 0
    assert all(flow.inverter_output_wh == 0 for flow in result.flows)


def test_expanded_horizon_reuses_only_unchanged_slot_inputs():
    slot = HourSlot(0, datetime(2026, 9, 27), 0.25, 8, 100, 20, 10)
    first = _expanded_slots((slot,))
    assert _expanded_slots((slot,))[0][1] is first[0][1]
    altered = _expanded_slots((replace(slot, pv_wh=200),))
    assert sum(part.pv_wh for _, part, _ in first) == pytest.approx(100)
    assert sum(part.pv_wh for _, part, _ in altered) == pytest.approx(200)
    assert sum(part.duration for _, part, _ in first) == pytest.approx(0.25)


def test_cancellation_scope_stops_simulation_and_restores_outer_context():
    now = datetime(2026, 9, 27)
    inputs = PlanInputs(now, 50, (HourSlot(0, now, 1, 8, 0, 100, 10),))
    with cancellation_scope(lambda: False):
        with pytest.raises(PlanningCancelled), cancellation_scope(lambda: True):
            pytest.fail("cancelled scope must not start work")
        check_cancelled()
        stop = [False]
        with cancellation_scope(lambda: stop[0]):
            stop[0] = True
            with pytest.raises(PlanningCancelled):
                simulate(SystemConfig(), inputs, 20)
    check_cancelled()
    assert simulate(SystemConfig(), inputs, 20).total_import_wh >= 0


def test_cached_grid_distinguishes_repeated_autumn_hour():
    from zoneinfo import ZoneInfo

    from core.simulation_steps import split_slot

    first = HourSlot(
        0,
        datetime(2026, 10, 25, 2, tzinfo=ZoneInfo("Europe/Berlin"), fold=0),
        1,
        2,
        0,
        100,
        10,
    )
    second = replace(first, start=first.start.replace(fold=1))
    assert (
        first.start == second.start
    )  # Why a dataclass-only cache key is insufficient.
    assert split_slot(first)[0][0].start.utcoffset() == timedelta(hours=2)
    assert split_slot(second)[0][0].start.utcoffset() == timedelta(hours=1)
