#!/usr/bin/env python3
"""Regenerate the golden planner snapshots (tests/core/golden_topology.json).

The golden file freezes the planner's output for a set of scenarios so that any
behaviour change is caught by tests/core/test_golden_topology.py. Run this ONLY
for an INTENTIONAL, reviewed behaviour change, then review the diff before
committing (a good change touches only the scenarios you expect, and no scenario
should import more energy without a reason). See CONTRIBUTING.md.

    python scripts/gen_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The core package imports as `core.*` (see tests/core/conftest.py); the golden
# test module lives under tests/core. Put both on the path.
sys.path.insert(0, str(ROOT / "custom_components" / "battery_manager"))
sys.path.insert(0, str(ROOT / "tests" / "core"))

import test_golden_topology as g  # noqa: E402


def main() -> None:
    data = {name: g._run(*args) for name, args in g._scenarios().items()}
    out = ROOT / "tests" / "core" / "golden_topology.json"
    out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(data)} scenarios to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
