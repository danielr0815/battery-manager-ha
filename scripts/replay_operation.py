#!/usr/bin/env python3
"""Replay a local operating archive and optionally compare another archive.

Requires the repository's Python environment, never a live Home Assistant.
A retained observed trajectory is replayed, not an invented counterfactual.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

# Expose the pure submodules without executing the HA integration __init__.
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "battery_manager"
package = types.ModuleType("bm_operation_offline")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "bm_operation_offline.operation_history", ROOT / "operation_history.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def read(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    data = data.get("data", data)
    return data.get("operation_history", data)


def compare(left, right):
    """Keep coverage beside every comparison; never rank disjoint days silently."""
    differences = {}
    for day in sorted(set(left["daily"]) & set(right["daily"])):
        a, b = left["daily"][day], right["daily"][day]
        differences[day] = {
            "switch_requests_delta": b["switch_requests"] - a["switch_requests"],
            "state_changes_delta": b["state_changes"] - a["state_changes"],
            "service_failures_delta": b["service_failures"] - a["service_failures"],
            "left_gap_hours": a["gap_hours"],
            "right_gap_hours": b["gap_hours"],
            "soc": {
                "left": {
                    key: a.get(key)
                    for key in ("soc_min_percent", "soc_max_percent", "storage_soc")
                },
                "right": {
                    key: b.get(key)
                    for key in ("soc_min_percent", "soc_max_percent", "storage_soc")
                },
            },
            "loads": {"left": a["loads"], "right": b["loads"]},
            "metrics": {
                key: {
                    "actual_wh_delta": b["metrics"][key]["actual_wh"]
                    - a["metrics"][key]["actual_wh"],
                    "planned_wh_delta": b["metrics"][key]["planned_wh"]
                    - a["metrics"][key]["planned_wh"],
                    "left_coverage_hours": a["metrics"][key]["coverage_hours"],
                    "right_coverage_hours": b["metrics"][key]["coverage_hours"],
                }
                for key in sorted(set(a["metrics"]) & set(b["metrics"]))
            },
        }
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument(
        "--observations-only",
        action="store_true",
        help="Rebuild reports without recomputing planner calls",
    )
    args = parser.parse_args()
    try:
        data = read(args.archive)
        report = module.replay_history(data, check_plans=not args.observations_only)
        checked = [report]
        if args.compare:
            other = read(args.compare)
            comparison = module.replay_history(
                other, check_plans=not args.observations_only
            )
            report["comparison"] = compare(data, other)
            report["comparison_replay"] = comparison
            checked.append(comparison)
    except (ValueError, KeyError, TypeError, OSError) as err:
        parser.exit(2, f"Invalid operating archive: {err}\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return (
        0
        if all(
            all(item["exact_plans"].values())
            and (item["daily_matches"] or not item["complete_event_history"])
            for item in checked
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
