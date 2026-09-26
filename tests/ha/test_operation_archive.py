"""Timezone changes preserve old daily attribution and exact observation replay."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.battery_manager.operation_archive import OperationArchive
from custom_components.battery_manager.operation_history import (
    OperationHistory,
    replay_history,
)


def test_schema_one_migrates_without_relabelling_midnight():
    at = datetime(2026, 9, 1, 23, tzinfo=UTC)
    old = OperationHistory("UTC")
    old.event(at, "command_requested", {"entity_id": "switch.test"})
    archive = OperationArchive("Europe/Berlin")
    archive.restore(old.export())
    archive.event(
        at + timedelta(minutes=1), "command_requested", {"entity_id": "switch.test"}
    )
    payload = archive.export()
    assert payload["schema_version"] == 2
    assert [part["timezone"] for part in payload["segments"]] == [
        "UTC",
        "Europe/Berlin",
    ]
    assert [list(part["daily"]) for part in payload["segments"]] == [
        ["2026-09-01"],
        ["2026-09-02"],
    ]
    assert replay_history(payload)["daily_matches"]
    restored = OperationArchive("Europe/Berlin")
    restored.restore(payload)
    assert restored.export() == payload
    assert [report["timezone"] for report in restored.reports()] == [
        "UTC",
        "Europe/Berlin",
    ]


def test_segments_share_global_event_budget(monkeypatch):
    from custom_components.battery_manager import operation_history

    monkeypatch.setattr(operation_history, "MAX_EVENTS", 2)
    at = datetime(2026, 9, 1, 23, tzinfo=UTC)
    old = OperationHistory("UTC")
    old.event(at, "state_changed", {})
    old.event(at, "state_changed", {})
    archive = OperationArchive("Europe/Berlin")
    archive.restore(old.export())
    archive.event(at + timedelta(minutes=1), "state_changed", {})
    assert sum(len(part["events"]) for part in archive.export()["segments"]) == 2
    assert archive.dropped_events == 1
    assert not replay_history(archive.export())["complete_event_history"]


def test_invalid_segment_does_not_mutate_active_history():
    archive = OperationArchive()
    before = archive.export()
    for invalid in (
        None,
        {"schema_version": 2, "segments": []},
        {"schema_version": 2, "segments": before["segments"] * 2},
    ):
        with pytest.raises(ValueError):
            archive.restore(invalid)
        assert archive.export() == before


def test_timezone_changes_share_the_report_budget(monkeypatch):
    from custom_components.battery_manager import operation_history

    monkeypatch.setattr(operation_history, "REPORT_DAYS", 2)
    at = datetime(2026, 9, 1, 12, tzinfo=UTC)
    archive = OperationArchive("UTC")
    archive.event(at, "state_changed", {})
    for zone in ("Europe/Berlin", "America/New_York"):
        following = OperationArchive(zone)
        following.restore(archive.export())
        following.event(at + timedelta(minutes=1), "state_changed", {})
        archive = following
    assert len(archive.reports()) == 2
    assert len(archive.export()["segments"]) == 2
    assert all(report["timezone"] != "UTC" for report in archive.reports())
    assert replay_history(archive.export())["daily_matches"]


def test_offline_comparison_retains_segment_identity(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    at = datetime(2026, 9, 1, 23, tzinfo=UTC)
    archive = OperationArchive("UTC")
    archive.event(at, "state_changed", {})
    migrated = OperationArchive("Europe/Berlin")
    migrated.restore(archive.export())
    migrated.event(at + timedelta(minutes=1), "command_requested", {})
    path = tmp_path / "archive.json"
    path.write_text(json.dumps(migrated.export()))
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[2] / "scripts/replay_operation.py"),
            str(path),
            "--compare",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    comparison = json.loads(result.stdout)["comparison"]
    assert set(comparison) == {archive.segment_id, migrated.segment_id}
    assert comparison[archive.segment_id]["timezone"] == "UTC"
    assert comparison[migrated.segment_id]["timezone"] == "Europe/Berlin"
