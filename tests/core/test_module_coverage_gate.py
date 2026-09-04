"""Contract tests for the per-module coverage gate itself."""

from pathlib import Path

from scripts.check_module_coverage import evaluate_modules


def _report(*entries: tuple[str, float]) -> dict:
    return {
        "files": {
            path: {"summary": {"percent_covered": percent}} for path, percent in entries
        }
    }


def test_each_ha_module_must_reach_95_percent(tmp_path: Path):
    source_root = tmp_path / "custom_components" / "battery_manager"
    source_root.mkdir(parents=True)
    good = source_root / "good.py"
    weak = source_root / "weak.py"
    good.touch()
    weak.touch()
    results = evaluate_modules(
        _report((good.as_posix(), 95.0), (weak.as_posix(), 94.99)), source_root
    )

    assert [(result.path, result.passes) for result in results] == [
        (good.as_posix(), True),
        (weak.as_posix(), False),
    ]
    assert {result.required for result in results} == {95.0}


def test_core_modules_require_100_percent_and_missing_data_fails(tmp_path: Path):
    source_root = tmp_path / "custom_components" / "battery_manager"
    core = source_root / "core"
    core.mkdir(parents=True)
    complete = core / "complete.py"
    incomplete = core / "incomplete.py"
    missing = source_root / "missing.py"
    for source in (complete, incomplete, missing):
        source.touch()

    results = evaluate_modules(
        _report(
            (complete.as_posix(), 100.0),
            (incomplete.as_posix(), 99.999),
        ),
        source_root,
    )

    by_name = {Path(result.path).name: result for result in results}
    assert by_name["complete.py"].passes
    assert not by_name["incomplete.py"].passes
    assert by_name["incomplete.py"].required == 100.0
    assert not by_name["missing.py"].passes
