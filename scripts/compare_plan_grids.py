#!/usr/bin/env python3
"""Offline sensitivity check: recorded intervals versus 30/15 minute boundaries.

This compares the real planner, not a global optimum or an execution guarantee.
No configuration, entity, stored recording or operating strategy is modified.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "battery_manager"),
)
from core.model import PlanInputs, SystemConfig  # noqa: E402
from core.optimize import plan  # noqa: E402
from core.replay import SCHEMA_VERSION, decode, encode  # noqa: E402

MAX_SLOTS = 512
MAX_LOADS = 12
ENERGIES = ("pv_wh", "ac_wh", "dc_wh", "pv_p10_wh", "pv_p90_wh")


def refine(inputs, minutes):
    """Keep existing boundaries and every Wh; never invent intrahour weather."""
    if minutes not in (15, 30):
        raise ValueError("Comparison supports only 15/30 minute boundaries")
    slots = []
    previous_end = None
    for slot in inputs.slots:
        if not math.isfinite(slot.duration) or not 0 < slot.duration <= 1:
            raise ValueError("Slot duration must be finite and in (0, 1]")
        if previous_end is not None and slot.start != previous_end:
            raise ValueError("Comparison requires contiguous, ordered slots")
        if any(
            value is not None and not math.isfinite(value)
            for key in ENERGIES
            if (value := getattr(slot, key)) is not None
        ):
            raise ValueError("Slot energy must be finite")
        end = slot.start + timedelta(hours=slot.duration)
        previous_end = end
        cursor = slot.start
        while cursor < end:
            boundary = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
                minutes=(cursor.minute // minutes + 1) * minutes
            )
            stop = min(end, boundary)
            duration = (stop - cursor).total_seconds() / 3600
            fraction = duration / slot.duration
            slots.append(
                replace(
                    slot,
                    index=len(slots),
                    start=cursor,
                    duration=duration,
                    hour_of_day=cursor.hour,
                    **{
                        key: None
                        if (value := getattr(slot, key)) is None
                        else value * fraction
                        for key in ENERGIES
                    },
                )
            )
            if len(slots) > MAX_SLOTS:
                raise ValueError("Comparison exceeds 512 refined slots")
            cursor = stop
    return replace(inputs, slots=tuple(slots))


def metrics(config, inputs, result):
    """Separate useful terminal service from storage throughput, with exact intervals."""
    cascades = {c.terminal_load_id: c for c in result.cascade_plans}
    loads = {}
    for load in config.loads:
        if load.energy_limited:
            continue
        lp = next(p for p in result.load_plans if p.load_id == load.load_id)
        cp = cascades.get(load.load_id)
        intervals = []
        if cp is not None:
            for i, flow in enumerate(cp.flows):
                for segment in flow.segments:
                    start = inputs.slots[i].start + timedelta(
                        hours=segment.start_offset_h
                    )
                    intervals.append(
                        (start, start + timedelta(hours=segment.run_hours))
                    )
            energy = sum(f.terminal_served_wh for f in cp.flows)
        else:
            intervals = [
                (s.start, s.start + timedelta(hours=h))
                for s, h in zip(inputs.slots, lp.run_hours, strict=True)
                if h > 1e-9
            ]
            energy = lp.planned_energy_wh
        merged = []
        for start, stop in sorted(intervals):
            if stop <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
            else:
                merged.append((start, stop))
        loads[load.load_id] = {
            "useful_energy_wh": energy,
            "run_hours": sum((b - a).total_seconds() / 3600 for a, b in merged),
            "planned_blocks": len(merged),
            "intervals": [
                {"start": a.isoformat(), "end": b.isoformat()} for a, b in merged
            ],
        }
    daily_peaks = {}
    for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):
        day = slot.start.date().isoformat()
        daily_peaks[day] = max(daily_peaks.get(day, 0), flow.soc_end_percent)
    return {
        "slots": len(inputs.slots),
        "import_wh": result.grid_import_kwh * 1000,
        "export_wh": result.grid_export_kwh * 1000,
        "minimum_soc_percent": result.min_soc_percent,
        "daily_peak_soc_percent": daily_peaks,
        "loads": loads,
    }


def compare(record):
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported planner recording schema")
    config, inputs = decode(record["config"]), decode(record["inputs"])
    if not isinstance(config, SystemConfig) or not isinstance(inputs, PlanInputs):
        raise ValueError("Recording must contain SystemConfig and PlanInputs")
    if (
        not inputs.slots
        or len(inputs.slots) > MAX_SLOTS
        or len(config.loads) > MAX_LOADS
    ):
        raise ValueError("Comparison requires 1..512 slots and at most 12 loads")
    # Validate all alternatives before spending any planner calls.
    variants = [
        ("recorded", inputs),
        ("30_minutes", refine(inputs, 30)),
        ("15_minutes", refine(inputs, 15)),
    ]
    rows = []
    for name, variant in variants:
        result = plan(config, variant)
        row = {"grid": name, **metrics(config, variant, result)}
        if name == "recorded":
            row["recorded_result_matches"] = encode(result) == record.get("result")
        else:
            row["import_delta_wh"] = row["import_wh"] - rows[0]["import_wh"]
        rows.append(row)
    return {
        "schema_version": 1,
        "planner_runs": 3,
        "global_optimum_proven": False,
        "assumption": "Constant power within recorded slots; planned blocks are not measured switches",
        "variants": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, nargs="?")
    parser.add_argument(
        "--timeout", type=int, default=60, choices=range(1, 121), metavar="1..120"
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            print(json.dumps(compare(json.load(sys.stdin)), indent=2, allow_nan=False))
            return 0
        if args.recording is None:
            parser.error("recording is required")
        data = json.loads(args.recording.read_text(encoding="utf-8"))
        data = data.get("data", data)
        data = data.get("planner_recording", data)
        # Bound actual runtime, including an unexpectedly expensive cascade.
        worker = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=json.dumps(data),
            text=True,
            capture_output=True,
            timeout=args.timeout,
        )
        if worker.returncode:
            print(worker.stderr.strip(), file=sys.stderr)
            return worker.returncode
        print(worker.stdout, end="")
        return 0
    except subprocess.TimeoutExpired:
        print(
            "Comparison time budget exceeded; no result or optimum claimed",
            file=sys.stderr,
        )
        return 2
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Comparison failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
