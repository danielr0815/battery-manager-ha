"""Offline raster sensitivity: conservation, honest service and bounded work."""

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from core.model import (
    CascadePlan,
    CascadeSlotFlow,
    CascadeSourceSegment,
    HourSlot,
    PlanInputs,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.optimize import plan
from core.replay import recording

from scripts.compare_plan_grids import compare, main, metrics, refine


def case():
    now = datetime(2026, 9, 8, 9)
    config = SystemConfig(loads=(SurplusLoad("leaf", "Endlast", 300),))
    inputs = PlanInputs(now, 95, (HourSlot(0, now, 1, 9, 2000, 100, 50, 1500, 2500),))
    return config, inputs


def test_refinement_preserves_all_energy_and_runtime_constraints():
    config, inputs = case()
    state = SurplusLoadState("leaf", not_before=inputs.now + timedelta(minutes=45))
    inputs = replace(inputs, load_states=(state,))
    refined = refine(inputs, 15)
    assert [s.start.minute for s in refined.slots] == [0, 15, 30, 45]
    for key in ("pv_wh", "ac_wh", "dc_wh", "pv_p10_wh", "pv_p90_wh"):
        assert sum(getattr(s, key) for s in refined.slots) == getattr(
            inputs.slots[0], key
        )
    assert refined.load_states == inputs.load_states
    result = plan(config, refined)
    assert not any(result.load_plans[0].schedule[:3])


def test_existing_irregular_boundaries_and_unknown_bands_survive():
    _, inputs = case()
    slot = replace(
        inputs.slots[0],
        start=inputs.now + timedelta(minutes=7),
        duration=53 / 60,
        pv_p10_wh=None,
        pv_p90_wh=None,
    )
    refined = refine(replace(inputs, slots=(slot,)), 30)
    assert [s.start.minute for s in refined.slots] == [7, 30]
    assert sum(s.pv_wh for s in refined.slots) == pytest.approx(slot.pv_wh)
    assert all(s.pv_p10_wh is None for s in refined.slots)


def test_three_plans_keep_original_recording_and_do_not_claim_an_optimum():
    config, inputs = case()
    record = recording(config, inputs, plan(config, inputs))
    original = json.dumps(record, sort_keys=True)
    result = compare(record)
    assert result["planner_runs"] == 3
    assert not result["global_optimum_proven"]
    assert result["variants"][0]["recorded_result_matches"]
    for row in result["variants"]:
        assert row["loads"]["leaf"]["planned_blocks"] == 1
        assert row["loads"]["leaf"]["run_hours"] == 1
        assert row["loads"]["leaf"]["useful_energy_wh"] == 300
        assert row["import_wh"] == 0
    assert json.dumps(record, sort_keys=True) == original


def test_cascade_service_uses_segments_not_charge_throughput():
    config, inputs = case()
    baseline = plan(config, inputs)
    source = CascadeSourceSegment(0, 0.25, 0.25, "aux", "b1", False, 75)
    second = replace(source, start_offset_h=0.5, source_load_id="b2")
    cp = CascadePlan(
        "chain",
        "leaf",
        (True,),
        (0.5,),
        (CascadeSlotFlow(terminal_served_wh=150, segments=(source, second)),),
    )
    row = metrics(config, inputs, replace(baseline, cascade_plans=(cp,)))
    load = row["loads"]["leaf"]
    assert load["useful_energy_wh"] == 150
    assert load["run_hours"] == 0.5
    assert load["planned_blocks"] == 1
    assert load["intervals"] == [
        {"start": "2026-09-08T09:15:00", "end": "2026-09-08T09:45:00"}
    ]


@pytest.mark.parametrize(
    "invalid", ["schema", "empty", "budget", "duration", "gap", "nan"]
)
def test_invalid_or_oversized_input_fails_before_planning(invalid, monkeypatch):
    config, inputs = case()
    if invalid == "empty":
        inputs = replace(inputs, slots=())
    elif invalid == "budget":
        inputs = replace(
            inputs,
            slots=tuple(
                replace(inputs.slots[0], index=i, start=inputs.now + timedelta(hours=i))
                for i in range(129)
            ),
        )
    elif invalid == "duration":
        inputs = replace(inputs, slots=(replace(inputs.slots[0], duration=0),))
    elif invalid == "gap":
        inputs = replace(
            inputs,
            slots=(
                *inputs.slots,
                replace(inputs.slots[0], start=inputs.now + timedelta(hours=2)),
            ),
        )
    elif invalid == "nan":
        inputs = replace(inputs, slots=(replace(inputs.slots[0], pv_wh=float("nan")),))
    record = recording(config, inputs, plan(*case()))
    if invalid == "schema":
        record["schema_version"] = 99

    def forbidden(*args):
        pytest.fail("Invalid inputs must not invoke the planner")

    monkeypatch.setattr("scripts.compare_plan_grids.plan", forbidden)
    with pytest.raises(ValueError):
        compare(record)


def test_cli_reads_ha_wrapper_in_isolated_worker(tmp_path):
    config, inputs = case()
    path = tmp_path / "recording.json"
    path.write_text(
        json.dumps(
            {
                "data": {
                    "planner_recording": recording(config, inputs, plan(config, inputs))
                }
            }
        )
    )
    completed = subprocess.run(
        [sys.executable, "scripts/compare_plan_grids.py", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["planner_runs"] == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.json"]


def test_cli_timeout_is_bounded_without_waiting(tmp_path, monkeypatch, capsys):
    path = tmp_path / "recording.json"
    path.write_text("{}")
    monkeypatch.setattr(
        sys, "argv", ["compare_plan_grids", str(path), "--timeout", "7"]
    )

    def expire(command, **kwargs):
        assert kwargs["timeout"] == 7
        raise subprocess.TimeoutExpired(command, 7)

    monkeypatch.setattr(subprocess, "run", expire)
    assert main() == 2
    assert "time budget exceeded" in capsys.readouterr().err


def test_finer_grid_can_fragment_service_and_is_not_automatically_selected():
    config, inputs = case()
    config = replace(config, loads=(SurplusLoad("leaf", "Endlast", 400),))
    inputs = replace(
        inputs,
        start_soc_percent=90,
        slots=tuple(
            HourSlot(i, inputs.now + timedelta(hours=i), 1, 9 + i, pv, 100, 50)
            for i, pv in enumerate((600, 1800, 1800, 0))
        ),
    )
    rows = compare(recording(config, inputs, plan(config, inputs)))["variants"]
    assert rows[0]["loads"]["leaf"]["planned_blocks"] == 1
    assert rows[-1]["loads"]["leaf"]["planned_blocks"] == 2
    assert (
        rows[-1]["loads"]["leaf"]["useful_energy_wh"]
        > rows[0]["loads"]["leaf"]["useful_energy_wh"]
    )


def test_unsupported_grid_is_rejected():
    _, inputs = case()
    with pytest.raises(ValueError, match="15/30"):
        refine(inputs, 0)
