#!/usr/bin/env python3
"""Replay a downloaded HA diagnostic or standalone planner recording locally."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "battery_manager"),
)
from core.replay import replay  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    args = parser.parse_args()
    data = json.loads(args.recording.read_text(encoding="utf-8"))
    data = data.get("data", data)  # HA wraps integration diagnostics.
    result, matches = replay(data.get("planner_recording", data))
    print(
        json.dumps(
            {
                "exact_match": matches,
                "import_kwh": result.grid_import_kwh,
                "export_kwh": result.grid_export_kwh,
                "min_soc_percent": result.min_soc_percent,
            },
            indent=2,
        )
    )
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
