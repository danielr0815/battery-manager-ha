#!/usr/bin/env python3
"""Measure a recorded planning problem without Home Assistant or actuator access.

Use the planner_recording object from an integration diagnostic, or a standalone
planner recording. Timings are observations, never a machine-dependent CI gate.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "battery_manager"),
)
from core.optimize import plan  # noqa: E402
from core.replay import decode  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--policy", choices=("recorded", "baseline", "reserve"), default="recorded"
    )
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    record = json.loads(args.recording.read_text(encoding="utf-8"))
    record = record.get("data", record)
    record = record.get("planner_recording", record)
    if not record or record.get("schema_version") != 1:
        parser.error("a completed schema-1 planner recording is required")
    config, inputs = decode(record["config"]), decode(record["inputs"])
    if args.policy != "recorded":
        config = replace(
            config, reserve=replace(config.reserve, enabled=args.policy == "reserve")
        )
    durations = []
    profiler = cProfile.Profile()
    for _ in range(args.repeat):
        start = perf_counter()
        if args.profile:
            profiler.enable()
        result = plan(config, inputs)
        profiler.disable()
        durations.append(perf_counter() - start)
    print(
        json.dumps(
            {
                "policy": args.policy,
                "slots": len(inputs.slots),
                "seconds": durations,
                "median_seconds": median(durations),
                "grid_import_wh": result.trajectory.total_import_wh,
                "grid_export_wh": result.trajectory.total_export_wh,
            },
            indent=2,
        )
    )
    if args.profile:
        pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(20)


if __name__ == "__main__":
    main()
