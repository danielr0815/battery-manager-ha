#!/usr/bin/env python3
"""Repeat complete offline plans and report timing, work and physical outcomes.

Use --core-dir for an isolated baseline checkout. Recordings stay local;
output contains counts and energy totals, no device identities or raw inputs.
"""

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument(
        "--core-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "custom_components/battery_manager/core",
    )
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    spec = importlib.util.spec_from_file_location(
        "core",
        args.core_dir / "__init__.py",
        submodule_search_locations=[str(args.core_dir)],
    )
    if spec is None or spec.loader is None:
        parser.error("Cannot import the selected core")
    package = importlib.util.module_from_spec(spec)
    sys.modules["core"] = package
    spec.loader.exec_module(package)
    from core import reserve
    from core.optimize import plan
    from core.replay import decode

    simulate = importlib.import_module("core.simulate")
    record = json.loads(args.recording.read_text())
    record = record.get("data", record)
    record = record.get("planner_recording", record)
    config, inputs = decode(record["config"]), decode(record["inputs"])
    counts: dict[str, int] = {}

    def counted(name, function):
        def run(*values, **options):
            counts[name] = counts.get(name, 0) + 1
            return function(*values, **options)

        return run

    original_step = reserve.step_hour
    reserve.step_hour = counted("physical_steps", original_step)
    # Both import locations share the same underlying physics; each execution
    # is counted once, including uncached legacy and cached reserve misses.
    simulate.step_hour = reserve.step_hour
    reserve._step_hour = counted("step_requests", reserve._step_hour)
    reserve._simulate_reserve_policy = counted(
        "policy_runs", reserve._simulate_reserve_policy
    )
    runs = []
    for _ in range(args.repeats):
        counts.clear()
        start = time.perf_counter()
        result = plan(config, inputs)
        seconds = time.perf_counter() - start
        trajectory = result.trajectory
        unserved = sum(flow.unserved_dc_wh for flow in trajectory.flows)
        runs.append(
            {
                "seconds": seconds,
                "work": dict(counts),
                "import_wh": trajectory.total_import_wh,
                "export_wh": trajectory.total_export_wh,
                "unserved_dc_wh": unserved,
                "served_dc_wh": sum(slot.dc_wh for slot in inputs.slots) - unserved,
                "ac_output_wh": sum(
                    flow.inverter_output_wh for flow in trajectory.flows
                ),
                "end_energy_wh": config.battery.energy_wh(trajectory.end_soc_percent),
            }
        )
    print(
        json.dumps(
            {
                "slots": len(inputs.slots),
                "loads": len(config.loads),
                "appliances": len(config.appliances),
                "median_seconds": statistics.median(run["seconds"] for run in runs),
                "runs": runs,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
