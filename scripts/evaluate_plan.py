#!/usr/bin/env python3
"""Compare a planner recording with measured, interval-aligned observations.

Input observations: {"schema_version": 1, "slots": [{"start": ISO datetime,
"duration_h": 1, "pv_wh": 500, "ac_wh": 100, "grid_import_wh": 0,
"grid_export_wh": 0, "soc_percent": 70, "switch_count": 0,
"loads": {"load-id": {"energy_wh": 200, "run_hours": 0.5}}}]}
Missing measurements stay unknown. Each row must cover exactly one recorded
slot; never compare overlapping forecasts or infer measured consumption from a
switch alone. AC observations must use the same house-only boundary as inputs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "battery_manager"),
)
from core.replay import decode  # noqa: E402


def _number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"Invalid measurement: {name}")
    return float(value)


def evaluate(record: dict, observations: dict) -> dict:
    """Attribute measured differences without changing any planner tolerance."""
    if record.get("schema_version") != 1 or observations.get("schema_version") != 1:
        raise ValueError("Unsupported evaluation schema")
    config, inputs, result = (
        decode(record[key]) for key in ("config", "inputs", "result")
    )
    slots = {slot.start: (i, slot) for i, slot in enumerate(inputs.slots)}
    seen = set()
    rows = []
    daily: dict[str, dict] = {}
    plans = {item.load_id: item for item in result.load_plans}
    states = {item.load_id: item for item in inputs.load_states}
    loads = {item.load_id: item for item in config.loads}
    for measured in observations["slots"]:
        start = datetime.fromisoformat(measured["start"])
        if start in seen or start not in slots:
            raise ValueError("Duplicate or unrecorded observation interval")
        seen.add(start)
        i, slot = slots[start]
        duration = _number(measured.get("duration_h"), "duration_h")
        if duration is None or abs(duration - slot.duration) > 1e-9:
            raise ValueError("Observation must cover exactly the recorded slot")
        day = slot.start.date().isoformat()
        totals = daily.setdefault(
            day,
            {
                "observed_hours": 0.0,
                "sums": defaultdict(float),
                "coverage_hours": defaultdict(float),
            },
        )
        totals["observed_hours"] += duration
        errors = {}
        for key, expected in (
            ("pv_wh", slot.pv_wh),
            ("ac_wh", slot.ac_wh),
            ("grid_import_wh", result.trajectory.flows[i].grid_import_wh),
            ("grid_export_wh", result.trajectory.flows[i].grid_export_wh),
        ):
            actual = _number(measured.get(key), key)
            errors[key] = None if actual is None else actual - expected
            if actual is not None:
                totals["sums"][f"actual_{key}"] += actual
                totals["sums"][f"predicted_{key}"] += expected
                totals["coverage_hours"][key] += duration
        load_errors = {}
        for key, measurement in measured.get("loads", {}).items():
            if key not in plans:
                raise ValueError(f"Unknown load: {key}")
            load_plan = plans[key]
            energy = _number(measurement.get("energy_wh"), "energy_wh")
            hours = _number(measurement.get("run_hours"), "run_hours")
            if hours is not None and not 0 <= hours <= duration:
                raise ValueError("Measured runtime exceeds its interval")
            planned_h = (
                load_plan.run_hours[i]
                if load_plan.run_hours
                else duration * load_plan.schedule[i]
            )
            total_h = sum(load_plan.run_hours)
            # Recover the effective power actually used, including cascade
            # caps. An unplanned load has no such evidence; use its explicitly
            # identified state/config fallback, never divide by zero.
            power = (
                load_plan.planned_energy_wh / total_h
                if total_h
                else (
                    states[key].planning_power_w(loads[key])
                    if key in states
                    else loads[key].nominal_power_w
                )
            )
            planned_e = power * planned_h
            load_errors[key] = {
                "energy_error_wh": None if energy is None else energy - planned_e,
                "execution_error_wh": None
                if hours is None
                else power * (hours - planned_h),
                "power_error_wh": None
                if energy is None or hours is None
                else energy - power * hours,
                "power_source": "recorded_allocation"
                if total_h
                else "state_or_config_fallback",
            }
            if energy is not None:
                totals["sums"][f"load:{key}:actual_wh"] += energy
                totals["sums"][f"load:{key}:predicted_wh"] += planned_e
                totals["coverage_hours"][f"load:{key}"] += duration
        switches = _number(measured.get("switch_count"), "switch_count")
        if switches is not None:
            if switches < 0 or not switches.is_integer():
                raise ValueError("Switch count must be a non-negative integer")
            totals["sums"]["switch_count"] += switches
            totals["coverage_hours"]["switch_count"] += duration
        soc = _number(measured.get("soc_percent"), "soc_percent")
        if soc is not None:
            if not 0 <= soc <= 100:
                raise ValueError("Invalid measured SOC")
            totals["min_soc_percent"] = min(totals.get("min_soc_percent", soc), soc)
            totals["max_soc_percent"] = max(totals.get("max_soc_percent", soc), soc)
        rows.append(
            {"start": slot.start.isoformat(), "errors_wh": errors, "loads": load_errors}
        )
    return {
        "schema_version": 1,
        "plan_time": inputs.now.isoformat(),
        "peak_tolerance_wh": config.battery.capacity_wh / 100,
        "recorded_horizon_hours": sum(slot.duration for slot in inputs.slots),
        "daily": daily,
        "intervals": rows,
        "interpretation": "Positive error means measured minus predicted. Missing values are unknown; component errors are not proof of a root cause.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("observations", type=Path)
    args = parser.parse_args()
    record = json.loads(args.recording.read_text(encoding="utf-8"))
    record = record.get("data", record)
    print(
        json.dumps(
            evaluate(
                record.get("planner_recording", record),
                json.loads(args.observations.read_text(encoding="utf-8")),
            ),
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
