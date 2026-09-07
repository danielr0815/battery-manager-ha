"""Measured errors retain provenance and cannot turn missing data into zero."""

from datetime import datetime

import pytest
from core.model import HourSlot, PlanInputs, SurplusLoad, SystemConfig
from core.optimize import plan
from core.replay import recording

from scripts.evaluate_plan import evaluate


def _case():
    now = datetime(2026, 9, 7, 12)
    config = SystemConfig(loads=(SurplusLoad("deh", "Deh", 400, 0, 15, 15, False),))
    inputs = PlanInputs(now, 95, (HourSlot(0, now, 1, 12, 1500, 100, 0),))
    result = plan(config, inputs)
    assert result.load_plans[0].run_hours == (1,)
    return recording(config, inputs, result), {
        "schema_version": 1,
        "slots": [
            {
                "start": now.isoformat(),
                "duration_h": 1,
                "pv_wh": 1200,
                "loads": {"deh": {"energy_wh": 150, "run_hours": 0.5}},
                "soc_percent": 94,
                "switch_count": 2,
            }
        ],
    }


def test_runtime_and_power_errors_sum_to_measured_energy_difference():
    record, observations = _case()
    report = evaluate(record, observations)
    row = report["intervals"][0]
    assert row["errors_wh"]["pv_wh"] == -300
    assert row["errors_wh"]["ac_wh"] is None
    errors = row["loads"]["deh"]
    assert errors["execution_error_wh"] == -200
    assert errors["power_error_wh"] == -50
    assert errors["energy_error_wh"] == -250
    day = report["daily"]["2026-09-07"]
    assert "ac_wh" not in day["coverage_hours"]
    assert day["sums"]["switch_count"] == 2
    assert report["peak_tolerance_wh"] == 50


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration_h", 0.5),
        ("pv_wh", float("nan")),
        ("pv_wh", True),
        ("soc_percent", 101),
        ("switch_count", -1),
    ],
)
def test_invalid_or_misaligned_measurements_are_rejected(field, value):
    record, observations = _case()
    observations["slots"][0][field] = value
    with pytest.raises(ValueError):
        evaluate(record, observations)


def test_duplicate_intervals_cannot_double_count_a_day():
    record, observations = _case()
    observations["slots"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate(record, observations)
