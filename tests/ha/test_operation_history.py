"""Operating evidence: chronological accounting, loss limits and replay."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.battery_manager import operation_history as module
from custom_components.battery_manager.core import (
    HourSlot,
    PlanInputs,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
    plan,
)
from custom_components.battery_manager.operation_history import (
    OperationHistory,
    expected_interval,
    measured_value,
    replay_history,
    unpack_plan,
)

NOW = datetime(2026, 9, 8, 9, tzinfo=UTC)


def planned(now=NOW, power=300):
    config = SystemConfig(
        loads=(SurplusLoad("load", "Load", power, min_runtime_min=15),)
    )
    inputs = PlanInputs(
        now,
        95,
        (HourSlot(0, now, 1, now.hour, 1200, 100, 0),),
        load_states=(SurplusLoadState("load"),),
    )
    return config, inputs, plan(config, inputs)


def sample(at=NOW, power=300):
    return {
        "pv": {"state": "1200", "unit": "W", "reported_at": at.isoformat()},
        "load:load": {"state": str(power), "unit": "W", "reported_at": at.isoformat()},
        "soc": {"state": "70", "unit": "%", "reported_at": at.isoformat()},
    }


def test_replan_closes_previous_interval_and_replay_is_exact():
    history = OperationHistory()
    history.sample(NOW, sample(), {"load:load": True})
    history.activate_plan(NOW, *planned(), "test")
    later = NOW + timedelta(minutes=5)
    history.sample(later, sample(later, 600), {"load:load": True})
    first = deepcopy(history.daily)
    history.activate_plan(later, *planned(later, 600), "test2")
    end = later + timedelta(minutes=5)
    history.sample(end, sample(end), {"load:load": False})
    report = history.daily["2026-09-08"]
    assert first["2026-09-08"]["metrics"]["load:load"]["actual_wh"] == 25
    assert report["metrics"]["load:load"]["actual_wh"] == 75
    assert report["loads"]["load"]["actual_run_hours"] == pytest.approx(1 / 6)
    assert report["loads"]["load"]["execution_error_wh"] + report["loads"]["load"][
        "power_error_wh"
    ] == pytest.approx(report["metrics"]["load:load"]["error_wh"])
    replayed = replay_history(history.export())
    assert replayed["daily_matches"] and all(replayed["exact_plans"].values())
    assert replayed["complete_event_history"]


@pytest.mark.parametrize(
    "state,unit,age",
    [
        ("unknown", "W", 0),
        ("nan", "W", 0),
        ("-1", "W", 0),
        ("10", "kWh", 0),
        ("10", "W", 301),
        ("10", "W", -1),
    ],
)
def test_missing_or_invalid_measurement_stays_unknown(state, unit, age):
    assert (
        measured_value(
            {
                "state": state,
                "unit": unit,
                "reported_at": (NOW - timedelta(seconds=age)).isoformat(),
            },
            NOW,
        )
        is None
    )


def test_units_and_soc_bounds():
    assert measured_value(None, NOW) is None
    assert measured_value({}, NOW) is None
    assert (
        measured_value({"state": 1, "unit": "kW", "reported_at": NOW.isoformat()}, NOW)
        == 1000
    )
    assert (
        measured_value({"state": 101, "reported_at": NOW.isoformat()}, NOW, power=False)
        is None
    )


def test_gap_is_not_integrated_and_restart_has_no_phantom_energy():
    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    history.sample(NOW, sample(), {"load:load": True})
    history.sample(
        NOW + timedelta(minutes=30),
        sample(NOW + timedelta(minutes=30)),
        {"load:load": True},
    )
    day = history.daily["2026-09-08"]
    assert day["metrics"]["load:load"]["actual_wh"] == 25
    assert day["gap_hours"] == pytest.approx(25 / 60)
    restored = OperationHistory()
    restored.restore(history.export())
    restored.break_observation(NOW + timedelta(hours=1), "startup")
    restored.sample(NOW + timedelta(hours=2), sample(NOW + timedelta(hours=2)), {})
    assert restored.daily["2026-09-08"]["metrics"]["load:load"]["actual_wh"] == 25
    assert replay_history(restored.export())["daily_matches"]


def test_local_midnight_splits_the_same_plan_without_double_counting():
    now = datetime(2026, 9, 8, 21, 58, tzinfo=UTC)
    history = OperationHistory("Europe/Berlin")
    history.activate_plan(now, *planned(now), "test")
    history.sample(now, sample(now), {})
    history.sample(now + timedelta(minutes=4), sample(now + timedelta(minutes=4)), {})
    assert history.daily["2026-09-08"]["metrics"]["pv"]["actual_wh"] == 40
    assert history.daily["2026-09-09"]["metrics"]["pv"]["actual_wh"] == 40
    assert replay_history(history.export())["daily_matches"]


@pytest.mark.parametrize(
    "day,hours",
    [
        (datetime(2026, 3, 28, 23, tzinfo=UTC), 23),
        (datetime(2026, 10, 24, 22, tzinfo=UTC), 25),
    ],
)
def test_day_length_uses_elapsed_time_across_dst(day, hours):
    history = OperationHistory("Europe/Berlin")
    history.sample(day, {}, {})
    history.sample(day + timedelta(hours=hours), {}, {})
    report = history.daily[history._day(day)]
    assert report["gap_hours"] + report["observed_hours"] == pytest.approx(hours)


def test_commands_passive_changes_and_failures_are_separate():
    history = OperationHistory()
    history.event(NOW, "command_requested", {"entity_id": "switch.one"})
    history.event(NOW, "command_result", {"success": False})
    history.event(NOW, "command_result", {"success": True})
    history.event(NOW, "state_changed", {"old": "off", "new": "on"})
    history.event(NOW, "state_changed", {"old": "on", "new": "on"})
    day = history.daily["2026-09-08"]
    assert (day["switch_requests"], day["state_changes"], day["service_failures"]) == (
        1,
        1,
        1,
    )
    assert replay_history(history.export())["daily_matches"]


def test_retention_prunes_details_but_keeps_reports(monkeypatch):
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    for i in range(5):
        history.sample(
            NOW + timedelta(seconds=i), sample(NOW + timedelta(seconds=i)), {}
        )
    assert len(history.events) == 3 and history.dropped == 3
    assert not replay_history(history.export())["complete_event_history"]
    history.break_observation(NOW + timedelta(days=8), "startup")
    assert len(history.events) == 1 and not history.plans
    for i in range(35):
        history.event(NOW + timedelta(days=9 + i), "test", {})
    assert len(history.daily) == 30
    monkeypatch.setattr(module, "MAX_BYTES", 100)
    history.event(NOW + timedelta(days=44), "large", {"payload": "x" * 200})
    assert not history.events


def test_archive_validation_and_defensive_export(monkeypatch):
    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    data = history.export()
    data["events"][0]["data"]["version"] = "changed"
    assert history.events[0]["data"]["version"] == "test"
    with pytest.raises(ValueError):
        history.restore({"schema_version": 100})
    with pytest.raises(ValueError):
        replay_history({"schema_version": 100, "timezone": "UTC"})
    blob = next(iter(data["plans"].values()))
    with pytest.raises(ValueError):
        history.restore({**data, "plans": {"wrong": blob}})
    with pytest.raises(ValueError):
        replay_history({**data, "events": data["events"] * 2})
    monkeypatch.setattr(module, "MAX_PLAN_BYTES", 10)
    with pytest.raises(ValueError):
        unpack_plan(blob)
    with pytest.raises(ValueError):
        history.activate_plan(NOW, *planned(), "test")


def test_unknown_measurement_or_unplanned_interval_is_not_zero():
    history = OperationHistory()
    history.sample(NOW, sample(), {})
    history.sample(NOW + timedelta(minutes=5), sample(), {})
    assert history.daily["2026-09-08"]["metrics"] == {}
    history.activate_plan(NOW + timedelta(minutes=5), *planned(), "test")
    history.sample(
        NOW + timedelta(minutes=10),
        {"other": {"state": 5, "unit": "W", "reported_at": NOW.isoformat()}},
        {},
    )
    assert history.daily["2026-09-08"]["metrics"] == {}
    assert expected_interval(None, NOW, NOW) == {}


def test_runtime_without_power_and_clock_correction_do_not_invent_energy():
    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    history.sample(NOW, {}, {"load:load": True})
    history.sample(NOW - timedelta(seconds=30), {}, {"load:load": False})
    history.sample(NOW + timedelta(minutes=5), {}, {"load:load": None})
    history.sample(NOW + timedelta(minutes=10), {}, {"load:load": True})
    day = history.daily["2026-09-08"]
    assert day["loads"]["load"]["actual_run_hours"] == pytest.approx(1 / 12)
    assert day["loads"]["load"]["runtime_coverage_hours"] == pytest.approx(1 / 12)
    assert "execution_error_wh" not in day["loads"]["load"]
    assert day["metrics"] == {}
    assert replay_history(history.export())["daily_matches"]


@pytest.mark.parametrize(
    "damage",
    ["naive", "order", "counter", "reference", "payload", "report", "reports_type"],
)
def test_invalid_archive_is_rejected_atomically(damage):
    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    original = history.export()
    data = deepcopy(original)
    if damage == "naive":
        data["events"][0]["at"] = "2026-09-08T00:00:00"
    elif damage == "order":
        data["events"][0]["sequence"] = 0
    elif damage == "counter":
        data["sequence"] = 0
    elif damage == "reference":
        data["events"][0]["plan_id"] = "missing"
    elif damage == "payload":
        data["events"][0]["data"] = []
    elif damage == "report":
        data["daily"]["2026-09-08"]["day"] = "wrong"
    else:
        data["daily"] = []
    with pytest.raises(ValueError):
        history.restore(data)
    assert history.export() == original


def test_cascade_input_meter_is_not_confused_with_stored_energy():
    from custom_components.battery_manager.core import CascadeMember, LoadCascade
    from custom_components.battery_manager.core.replay import recording

    config = SystemConfig(
        loads=(
            SurplusLoad("b1", "B1", 300, 0, 15, 15, True, 2000, 90, True),
            SurplusLoad("leaf", "Leaf", 300, 0, 15, 15, False),
        ),
        cascades=(LoadCascade("chain", (CascadeMember("b1", 20, 50),), "leaf"),),
    )
    inputs = PlanInputs(
        NOW,
        95,
        (HourSlot(0, NOW, 1, 9, 2000, 100, 0),),
        (
            SurplusLoadState(
                "b1", soc_percent=50, soc_source="live", soc_observed_at=NOW
            ),
            SurplusLoadState("leaf"),
        ),
    )
    result = plan(config, inputs)
    expected = expected_interval(
        recording(config, inputs, result), NOW, NOW + timedelta(hours=1)
    )
    flow = result.cascade_plans[0].flows[0]
    assert flow.terminal_served_wh > 0
    assert expected["cascade_input:b1"] == pytest.approx(flow.root_input_wh)
    assert expected["load:b1"] == pytest.approx(
        flow.member_flows[0].own_charge_input_wh
    )
    assert expected["cascade_input:b1"] > expected["load:b1"]
    assert expected["load:leaf"] == pytest.approx(flow.terminal_served_wh)
    assert (
        expected_interval(
            recording(config, inputs, result),
            NOW + timedelta(hours=2),
            NOW + timedelta(hours=3),
        )
        == {}
    )


def test_offline_operation_cli_replays_compares_and_detects_changed_reports(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    history.sample(NOW, sample(), {"load:load": True})
    history.sample(NOW + timedelta(minutes=5), sample(), {})
    path = tmp_path / "diagnostics.json"
    path.write_text(json.dumps({"data": {"operation_history": history.export()}}))
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/replay_operation.py"),
        str(path),
    ]
    result = subprocess.run(
        [*command, "--compare", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["daily_matches"]
    assert report["comparison"]["2026-09-08"]["metrics"]["pv"]["actual_wh_delta"] == 0
    data = history.export()
    data["daily"]["2026-09-08"]["switch_requests"] += 1
    path.write_text(json.dumps(data))
    result = subprocess.run(
        [*command, "--observations-only"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["exact_plans"] == {}


def test_partial_freshness_keeps_valid_minutes_and_soc_includes_latest_point():
    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "test")
    first = sample(NOW - timedelta(minutes=2))
    history.sample(NOW, first, {"load:load": True})
    end = NOW + timedelta(minutes=5)
    last = sample(end)
    last["soc"]["state"] = "80"
    history.sample(end, last, {"load:load": False})
    day = history.daily["2026-09-08"]
    assert day["metrics"]["load:load"]["actual_wh"] == 15  # three valid minutes
    assert day["metrics"]["load:load"]["coverage_hours"] == pytest.approx(0.05)
    assert day["loads"]["load"]["runtime_coverage_hours"] == pytest.approx(1 / 12)
    assert day["soc_min_percent"] == 70 and day["soc_max_percent"] == 80
    assert replay_history(history.export())["daily_matches"]


def test_byte_limit_releases_old_plan_before_discarding_new_evidence(monkeypatch):
    import json

    history = OperationHistory()
    history.activate_plan(NOW, *planned(), "one")
    first_key = history.events[-1]["plan_id"]
    initial_size = history._bytes
    monkeypatch.setattr(module, "MAX_BYTES", initial_size + 900)
    later = NOW + timedelta(minutes=5)
    history.activate_plan(later, *planned(later, 600), "two")
    history.sample(later, sample(later), {})
    assert first_key not in history.plans
    assert any(
        row["kind"] == "plan" and row["data"]["version"] == "two"
        for row in history.events
    )
    assert history.events[-1]["kind"] == "sample"
    assert history._bytes <= module.MAX_BYTES
    assert not replay_history(history.export())["complete_event_history"]
    monkeypatch.setattr(module, "MAX_BYTES", 100)
    history.event(later, "too_large", {"payload": "x" * 200})
    assert not history.events and not history.plans
    assert len(json.dumps({"events": history.events, "plans": history.plans})) < 100


@pytest.mark.parametrize(
    "section,value",
    [
        ("switch_requests", None),
        ("metrics", []),
        ("loads", {"load": {"actual_run_hours": "wrong"}}),
    ],
)
def test_damaged_report_cannot_poison_future_control_observations(section, value):
    history = OperationHistory()
    history.event(NOW, "test", {})
    original = history.export()
    damaged = deepcopy(original)
    damaged["daily"]["2026-09-08"][section] = value
    with pytest.raises(ValueError):
        history.restore(damaged)
    history.event(NOW, "command_requested", {})
    assert history.daily["2026-09-08"]["switch_requests"] == 1


def test_aux_input_boundary_has_downstream_delivery_but_no_source_input():
    from dataclasses import replace

    from custom_components.battery_manager.core import (
        CascadeMember,
        CascadeMemberFlow,
        CascadeSlotFlow,
        CascadeSourceSegment,
        LoadCascade,
    )
    from custom_components.battery_manager.core.replay import recording

    config = SystemConfig(
        loads=(
            SurplusLoad("b1", "B1", 300, 0, 15, 15, True, 2000, 90, True),
            SurplusLoad("b2", "B2", 300, 0, 15, 15, True, 2000, 90, True),
            SurplusLoad("leaf", "Leaf", 300),
        ),
        cascades=(
            LoadCascade(
                "chain",
                (
                    CascadeMember("b1", 20, 50, output_overhead_w=10),
                    CascadeMember("b2", 20, 50, output_overhead_w=20),
                ),
                "leaf",
            ),
        ),
    )
    inputs = PlanInputs(
        NOW,
        95,
        (HourSlot(0, NOW, 1, 9, 0, 0, 0),),
        tuple(
            SurplusLoadState(
                lid, soc_percent=80, soc_source="live", soc_observed_at=NOW
            )
            for lid in ("b1", "b2", "leaf")
        ),
    )
    result = plan(config, inputs)
    # Contract fixture: B1 supplies the last half-hour through B2, including
    # B2's 20 W overhead. B1's own overhead never appears at B2's input.
    flow = CascadeSlotFlow(
        terminal_served_wh=150,
        aux_terminal_wh=150,
        segments=(CascadeSourceSegment(0, 0.5, 0.5, "aux", "b1", False, 150),),
        member_flows=(
            CascadeMemberFlow("b1", 80, 71.75, battery_discharge_wh=165),
            CascadeMemberFlow("b2", 80, 80),
        ),
    )
    result = replace(
        result, cascade_plans=(replace(result.cascade_plans[0], flows=(flow,)),)
    )
    record = recording(config, inputs, result)
    early = expected_interval(record, NOW, NOW + timedelta(minutes=30))
    late = expected_interval(
        record, NOW + timedelta(minutes=30), NOW + timedelta(hours=1)
    )
    assert early["cascade_input:b2"] == 0
    assert late["cascade_input:b1"] == 0
    assert late["cascade_input:b2"] == 160
    assert late["load:leaf"] == 150


def test_offline_compare_checks_the_second_archive_and_reports_invalid_input(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    history = OperationHistory()
    history.event(NOW, "state_changed", {"old": "off", "new": "on"})
    valid = tmp_path / "valid.json"
    other = tmp_path / "other.json"
    valid.write_text(json.dumps(history.export()))
    changed = history.export()
    changed["daily"]["2026-09-08"]["state_changes"] = 2
    other.write_text(json.dumps(changed))
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/replay_operation.py"),
        str(valid),
        "--compare",
        str(other),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["daily_matches"] and not data["comparison_replay"]["daily_matches"]
    assert data["comparison"]["2026-09-08"]["soc"]["left"]["soc_min_percent"] is None
    other.write_text('{"schema_version": 999}')
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "Invalid operating archive" in result.stderr
    assert "Traceback" not in result.stderr
