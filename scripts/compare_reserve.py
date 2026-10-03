#!/usr/bin/env python3
"""Compare bounded recorded reserve alternatives without Home Assistant access.

Accept a standalone planner recording or an integration diagnostic. Output
reports planned energy and terminal value; it never changes a live policy.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "battery_manager"),
)
from core.model import PlanInputs, PlanResult, SystemConfig  # noqa: E402
from core.replay import decode  # noqa: E402
from core.reserve_comparison import compare_reserve_alternatives  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument(
        "--margins-wh", type=float, nargs="+", default=[0, 250, 500, 750, 1000]
    )
    args = parser.parse_args()
    record = json.loads(args.recording.read_text(encoding="utf-8"))
    record = record.get("data", record)
    record = record.get("planner_recording", record)
    if not record or record.get("schema_version") != 1:
        parser.error("a completed schema-1 planner recording is required")
    config, inputs, result = (
        decode(record[name]) for name in ("config", "inputs", "result")
    )
    if (
        not isinstance(config, SystemConfig)
        or not isinstance(inputs, PlanInputs)
        or not isinstance(result, PlanResult)
    ):
        parser.error("recording must contain SystemConfig, PlanInputs and PlanResult")
    try:
        comparisons = compare_reserve_alternatives(
            config,
            inputs,
            tuple(args.margins_wh),
            extra_ac_wh=tuple(f.extra_ac_wh for f in result.trajectory.flows),
            feedin_wh=tuple(f.feedin_wh for f in result.trajectory.flows),
        )
    except ValueError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "snapshot_time": inputs.now.isoformat(),
                "variants": len(comparisons),
                "valuation": "Import reduction minus lost terminal stored energy recoverable through battery and inverter; forecast counterfactual, no observed savings or live permission.",
                "comparisons": [asdict(row) for row in comparisons],
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
