#!/usr/bin/env python3
"""Enforce the repository's coverage policy for every integration module."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

HA_MIN_PERCENT = 95.0
CORE_MIN_PERCENT = 100.0


@dataclass(frozen=True)
class ModuleCoverage:
    """One source module and its measured statement coverage."""

    path: str
    percent: float | None
    required: float

    @property
    def passes(self) -> bool:
        return self.percent is not None and self.percent + 1e-9 >= self.required


def evaluate_modules(report: dict, source_root: Path) -> list[ModuleCoverage]:
    """Return coverage results for every Python module below ``source_root``."""
    measured = report.get("files", {})
    results = []
    for source in sorted(source_root.rglob("*.py")):
        relative = source.as_posix()
        summary = measured.get(relative, {}).get("summary")
        required = (
            CORE_MIN_PERCENT
            if source_root / "core" in source.parents
            else HA_MIN_PERCENT
        )
        results.append(
            ModuleCoverage(
                relative,
                float(summary["percent_covered"]) if summary is not None else None,
                required,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        default=Path("coverage.json"),
        help="pytest-cov JSON report (default: coverage.json)",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("custom_components/battery_manager"),
    )
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    results = evaluate_modules(report, args.source_root)
    failures = [result for result in results if not result.passes]

    for result in failures:
        actual = "not measured" if result.percent is None else f"{result.percent:.2f}%"
        print(f"FAIL {result.path}: {actual}, required {result.required:.0f}%")
    if failures:
        print(f"Module coverage failed for {len(failures)} of {len(results)} modules")
        return 1
    print(
        f"Module coverage OK: {len(results)} modules; "
        f"HA >= {HA_MIN_PERCENT:.0f}%, core = {CORE_MIN_PERCENT:.0f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
