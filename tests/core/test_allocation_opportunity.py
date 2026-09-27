"""Prove export-opportunity pruning preserves the full physical gate search."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from core import allocation
from core.model import (
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.simulate import simulate


def scenario(energies, start_soc, *, beta=1.0):
    config = SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(3000, 1, 0),
        inverter=ConverterParams(3000, 1, 0),
        control=ControlParams(predrain_pv_confidence=1, upper_pv_reserve=beta),
        loads=(
            SurplusLoad(
                "storage",
                "Storage",
                1200,
                min_runtime_min=5,
                energy_limited=True,
                capacity_wh=5000,
                target_soc_percent=100,
                battery_tolerance=0.2,
            ),
        ),
    )
    start = datetime(2026, 9, 27, 8)
    inputs = PlanInputs(
        start,
        start_soc,
        tuple(
            HourSlot(i, start + timedelta(hours=i), 1, start.hour + i, pv, ac, 0)
            for i, (pv, ac) in enumerate(energies)
        ),
        load_states=(SurplusLoadState("storage", soc_percent=0),),
    )
    return config, inputs


def compare_with_unpruned_search(config, inputs):
    threshold = config.control.inverter_min_soc_percent
    baseline = simulate(config, inputs, threshold)
    with patch.object(allocation, "simulate", wraps=simulate) as tracked:
        actual = allocation.allocate_loads(config, inputs, threshold, baseline)
        actual_calls = tracked.call_count
    # Disable only the cheap necessary-condition check. All physical, import,
    # SOC, optimistic and scheduling gates continue to use the real simulator.
    with (
        patch.object(
            allocation, "_preconditioning_opportunity_possible", return_value=True
        ),
        patch.object(allocation, "simulate", wraps=simulate) as tracked,
    ):
        exhaustive = allocation.allocate_loads(config, inputs, threshold, baseline)
        exhaustive_calls = tracked.call_count
    # Include reasons and rejected candidates: pruning must not quietly change
    # the published diagnostics while retaining the same on/off schedule.
    assert actual == exhaustive
    return actual, actual_calls, exhaustive_calls


def test_small_export_residue_avoids_impossible_storage_trials():
    config, inputs = scenario([(10, 50), (100, 50)], 95)
    result, calls, exhaustive_calls = compare_with_unpruned_search(config, inputs)
    plans, _, trajectory = result
    assert plans[0].planned_energy_wh == 0
    assert trajectory.total_export_wh == pytest.approx(10)
    assert calls < exhaustive_calls


@pytest.mark.parametrize("beta", [1.0, 1.2])
def test_later_sun_and_smaller_quanta_remain_available(beta):
    config, inputs = scenario([(20, 100), (20, 100), (2200, 100)], 90, beta=beta)
    result, _, _ = compare_with_unpruned_search(config, inputs)
    plans, _, trajectory = result
    assert plans[0].planned_energy_wh > 0
    assert any(pass_number == 2 for _, _, pass_number, _ in plans[0].allocations)
    assert trajectory.total_import_wh == pytest.approx(0)
