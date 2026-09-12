"""F-APPLIANCE-TELEMETRY: units, cycle quality and future planner inputs."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.appliance_learning import (
    ApplianceLearning,
    duration_hours,
    measurement,
    program_name,
)
from custom_components.battery_manager.const import DOMAIN
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (90, "min", 1.5),
        (5400, "s", 1.5),
        (1.5, "h", 1.5),
        (90, None, 1.5),
        ("01:30", None, 1.5),
        ("01:30:36", None, 1.51),
        ("unknown", None, None),
        ("unavailable", None, None),
        ("", None, None),
        ("NaN", "min", None),
        ("inf", "h", None),
        (-1, "min", None),
        (90, "W", None),
        ("bad", None, None),
        ("1:2:3:4", None, None),
        ("-1:20", None, None),
    ],
)
def test_duration_units_and_invalid_values(value, unit, expected):
    state = State("sensor.duration", str(value), {"unit_of_measurement": unit})
    assert duration_hours(state, NOW) == expected


def test_duration_end_timestamp():
    def state(value):
        return State("sensor.end", value, {"device_class": "timestamp"})

    assert duration_hours(state("2026-09-11T13:00:00+00:00"), NOW, remaining=True) == 1
    assert duration_hours(state("2026-09-11T11:00:00+00:00"), NOW, remaining=True) == 0
    assert duration_hours(state("2026-09-11T13:00:00"), NOW, remaining=True) is None
    assert duration_hours(state("bad"), NOW, remaining=True) is None
    assert duration_hours(None, NOW) is None


@pytest.mark.parametrize(
    ("kind", "value", "unit", "expected"),
    [
        ("power", "0.5", "kW", 500),
        ("power", "500", "W", 500),
        ("energy", "1.2", "kWh", 1200),
        ("energy", "1200", "Wh", 1200),
        ("power", "500", None, None),
        ("energy", "500", "W", None),
        ("power", "unavailable", "W", None),
        ("power", "NaN", "W", None),
        ("energy", "-1", "Wh", None),
    ],
)
def test_meter_normalization(kind, value, unit, expected):
    assert (
        measurement(State("sensor.meter", value, {"unit_of_measurement": unit}), kind)
        == expected
    )
    assert measurement(None, kind) is None


@pytest.mark.parametrize("use_energy", [True, False])
def test_complete_cycle_learns_meter_or_integrated_power(use_energy):
    learner = ApplianceLearning()
    for minute in range(0, 31, 5):
        learner.observe(
            "washer",
            NOW + timedelta(minutes=minute),
            minute < 30,
            600 if minute < 30 else 0,
            1000 + minute * 20 if use_energy else None,
            complete_start=True,
        )
    assert learner.energy("washer", 1000) == (600 if use_energy else 300)
    restored = ApplianceLearning()
    restored.restore(learner.samples)
    assert restored.energy("washer", 1000) == learner.energy("washer", 1000)
    assert restored.active == {}


@pytest.mark.parametrize(
    "fault",
    ["gap", "dropout", "reset", "missing", "backwards", "partial", "zero", "huge"],
)
def test_incomplete_or_invalid_cycles_do_not_teach(fault):
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, None, 1000, complete_start=fault != "partial")
    time = NOW + timedelta(
        minutes=20 if fault == "gap" else -1 if fault == "backwards" else 5
    )
    end = {"reset": 0, "missing": None, "zero": 1000, "huge": 20000}.get(fault, 1100)
    learner.observe(
        "washer", time, False, None, end, complete_start=False, valid=fault != "dropout"
    )
    assert learner.energy("washer", 900) == 900


def test_reset_counter_can_fall_back_to_complete_power_measurement():
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, 600, 1000, complete_start=True)
    learner.observe(
        "washer", NOW + timedelta(minutes=5), False, 0, 0, complete_start=False
    )
    assert learner.energy("washer", 900) == 50


def test_learning_is_bounded_robust_and_restores_only_valid_samples():
    learner = ApplianceLearning()
    learner.restore(None)
    learner.restore(
        {
            "bad": "invalid",
            "washer": [None, "a", -1, float("nan"), 20000] + [500] * 30 + [9000],
        }
    )
    assert len(learner.samples["washer"]) == 20
    assert learner.energy("washer", 1000) == 500


async def test_coordinator_uses_live_duration_and_learned_energy(hass):
    data = {
        "detection_entity": "sensor.status",
        "power_entity": "sensor.power",
        "energy_entity": "sensor.energy",
        "total_time_entity": "sensor.total",
        "remaining_time_entity": "sensor.remaining",
        "run_duration_h": 2,
        "run_energy_wh": 1000,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            ConfigSubentryData(
                data=data, subentry_type="appliance", title="Washer", unique_id=None
            )
        ],
    )
    coordinator = BatteryManagerCoordinator(hass, entry)
    key = next(iter(entry.subentries))
    hass.states.async_set("sensor.status", "off")
    assert coordinator._get_appliance_runs(NOW) == ()
    hass.states.async_set("sensor.total", "60", {"unit_of_measurement": "min"})
    hass.states.async_set("sensor.remaining", "30", {"unit_of_measurement": "min"})
    for minute in range(0, 31, 5):
        hass.states.async_set("sensor.status", "running" if minute < 30 else "off")
        hass.states.async_set(
            "sensor.energy", str(10 + minute / 100), {"unit_of_measurement": "kWh"}
        )
        runs = coordinator._get_appliance_runs(NOW + timedelta(minutes=minute))
        if minute == 0:
            assert runs[0].remaining_hours == 0.5
            assert runs[0].remaining_energy_wh == 500
    assert runs == ()
    assert coordinator.build_system_config().appliances[
        0
    ].run_energy_wh == pytest.approx(300)
    assert coordinator.build_system_config().appliances[0].run_duration_h == 1
    assert coordinator._persistent_payload()["appliance_energy_samples"][
        key
    ] == pytest.approx([300])
    hass.states.async_set("sensor.status", "running")
    run = coordinator._get_appliance_runs(NOW + timedelta(minutes=35))[0]
    assert run.remaining_energy_wh == pytest.approx(150)
    hass.states.async_set("sensor.remaining", "unavailable")
    run = coordinator._get_appliance_runs(NOW + timedelta(minutes=40))[0]
    assert run.remaining_hours == pytest.approx(55 / 60)


async def test_duration_does_not_detect_activity_and_kw_is_normalized(hass):
    coordinator = SimpleNamespace(hass=hass)
    for value, kind in [
        ("4.5", "duration"),
        ("2026-09-11T13:00:00+00:00", "timestamp"),
    ]:
        hass.states.async_set("sensor.time", value, {"device_class": kind})
        assert not BatteryManagerCoordinator._appliance_is_running(
            coordinator, {"detection_entity": "sensor.time"}, True
        )
    hass.states.async_set("sensor.time", "90")
    assert not BatteryManagerCoordinator._appliance_is_running(
        coordinator,
        {"detection_entity": "sensor.time", "total_time_entity": "sensor.time"},
        False,
    )
    hass.states.async_set("sensor.power", ".5", {"unit_of_measurement": "kW"})
    assert BatteryManagerCoordinator._appliance_is_running(
        coordinator,
        {"detection_entity": "sensor.power", "power_threshold_w": 100},
        False,
    )


def _coordinator(hass, **options):
    data = {
        "detection_entity": "sensor.status",
        "power_entity": "sensor.power",
        "energy_entity": "sensor.energy",
        "remaining_time_entity": "sensor.remaining",
        "run_duration_h": 4.5,
        "run_energy_wh": 1000,
        **options,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            ConfigSubentryData(
                data=data, subentry_type="appliance", title="Appliance", unique_id=None
            )
        ],
    )
    return BatteryManagerCoordinator(hass, entry), next(iter(entry.subentries))


async def test_anonymized_historical_cycle_replay(hass):
    """An observed cycle: low-power phases must not split the energy sample."""
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/appliance_cycle_anonymized.json").read_text()
    )
    coordinator, key = _coordinator(hass)
    starts = set()
    for row in fixture["records"]:
        hass.states.async_set("sensor.status", row["status"])
        hass.states.async_set(
            "sensor.remaining",
            str(row["remaining_h"]),
            {"device_class": "duration", "unit_of_measurement": "h"},
        )
        hass.states.async_set(
            "sensor.power", str(row["power_w"]), {"unit_of_measurement": "W"}
        )
        hass.states.async_set(
            "sensor.energy", str(row["energy_wh"]), {"unit_of_measurement": "Wh"}
        )
        runs = coordinator._get_appliance_runs(NOW + timedelta(minutes=row["minute"]))
        if row["status"] == "run":
            assert len(runs) == 1
            assert runs[0].remaining_hours == row["remaining_h"]
            starts.add(coordinator._appliance_started[key])
        else:
            assert runs == ()  # ready with 4.5 h is not a new program
    assert len(starts) == 1
    assert coordinator._appliance_learning.samples[key] == [462]
    assert coordinator.build_system_config().appliances[0].run_energy_wh == 462


async def test_pause_is_one_cycle_and_abort_does_not_teach(hass):
    coordinator, key = _coordinator(hass)
    for minute, phase in enumerate(
        [
            "off",
            "detecting",
            "running",
            "pause",
            "running",
            "rinsing",
            "spinning",
            "end",
        ]
    ):
        hass.states.async_set("sensor.status", phase)
        hass.states.async_set(
            "sensor.energy", str(minute * 100), {"unit_of_measurement": "Wh"}
        )
        runs = coordinator._get_appliance_runs(NOW + timedelta(minutes=minute))
        assert bool(runs) == (phase not in {"off", "end"})
    assert coordinator._appliance_learning.samples[key] == [600]
    for minute, phase in enumerate(
        ["running", "error", "running", "aborting"], start=8
    ):
        hass.states.async_set("sensor.status", phase)
        hass.states.async_set(
            "sensor.energy", str(minute * 100), {"unit_of_measurement": "Wh"}
        )
        coordinator._get_appliance_runs(NOW + timedelta(minutes=minute))
    assert coordinator._appliance_learning.samples[key] == [600]


async def test_timestamp_live_snapshot_restored_mid_run_and_invalid_total(hass):
    coordinator, key = _coordinator(hass, total_time_entity="sensor.total")
    hass.states.async_set("sensor.status", "running")
    hass.states.async_set("sensor.total", "265", {"unit_of_measurement": "min"})
    hass.states.async_set(
        "sensor.remaining",
        (NOW + timedelta(minutes=192)).isoformat(),
        {"device_class": "timestamp"},
    )
    run = coordinator._get_appliance_runs(NOW)[0]
    assert run.remaining_hours == pytest.approx(3.2)
    assert run.remaining_energy_wh == pytest.approx(1000 * 192 / 265)
    assert key not in coordinator._appliance_learning.active  # startup mid-cycle
    hass.states.async_set("sensor.total", "unavailable")
    run = coordinator._get_appliance_runs(NOW + timedelta(minutes=5))[0]
    assert run.remaining_hours == pytest.approx(187 / 60)
    assert run.remaining_energy_wh == pytest.approx(1000 * 187 / 270)


async def test_learning_store_roundtrip_and_sensor_tracking(hass):
    from unittest.mock import AsyncMock, patch

    coordinator, key = _coordinator(hass, total_time_entity="sensor.total")
    coordinator._appliance_learning.restore({key: [400, 500, 600]})
    payload = coordinator._persistent_payload()
    restored, _ = _coordinator(hass)
    with (
        patch.object(restored._store, "async_load", AsyncMock(return_value=payload)),
        patch.object(restored.learner, "async_load", AsyncMock()),
    ):
        await restored.async_load_persistent_state()
    assert restored._appliance_learning.energy(key, 1000) == 500
    assert {
        "sensor.status",
        "sensor.power",
        "sensor.energy",
        "sensor.total",
        "sensor.remaining",
    } <= set(_tracked_with_required_inputs(coordinator))
    _, appliances = coordinator.learner._subentries()
    assert appliances[0]["detection_entity"] == "sensor.power"


@pytest.mark.parametrize("phase", ["unknown", "unavailable"])
async def test_detection_dropout_invalidates_measured_cycle(hass, phase):
    coordinator, key = _coordinator(hass)
    for minute, status in enumerate(["off", "running", phase, "running", "finished"]):
        hass.states.async_set("sensor.status", status)
        hass.states.async_set(
            "sensor.energy", str(minute * 100), {"unit_of_measurement": "Wh"}
        )
        coordinator._get_appliance_runs(NOW + timedelta(minutes=minute))
    assert coordinator._appliance_learning.energy(key, 1000) == 1000


def test_missing_and_nonfinite_detection_cannot_complete_learning():
    assert not BatteryManagerCoordinator._appliance_finished(None)
    assert not BatteryManagerCoordinator._appliance_finished(
        State("sensor.power", "nan")
    )
    assert BatteryManagerCoordinator._appliance_finished(State("sensor.power", "0"))


def _tracked_with_required_inputs(coordinator):
    from unittest.mock import patch

    with patch.dict(
        coordinator.raw_config,
        {
            "soc_entity": "sensor.soc",
            "pv_forecast_today_entity": "sensor.today",
            "pv_forecast_tomorrow_entity": "sensor.tomorrow",
            "pv_forecast_day_after_entity": "sensor.after",
        },
    ):
        return coordinator._tracked_entities()


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("unknown", None),
        ("unavailable", None),
        (" NONE ", None),
        ("", None),
        ("x" * 256, None),
        (" Eco50 ", "Eco50"),
    ],
)
def test_program_names_require_an_explicit_valid_state(value, expected):
    assert (
        program_name(State("sensor.program", value) if value is not None else None)
        == expected
    )


def _program_cycle(learner, program, wh=600, minutes=60):
    for minute in range(0, minutes + 1, 5):
        learner.observe(
            "a",
            NOW + timedelta(minutes=minute),
            minute < minutes,
            None,
            wh * minute / minutes,
            complete_start=minute == 0,
            program=program if minute < minutes else None,
        )


def test_program_profiles_keep_separate_energy_duration_and_aggregate_fallback():
    learner = ApplianceLearning()
    _program_cycle(learner, "eco", 600, 60)
    _program_cycle(learner, "auto", 900, 30)
    assert learner.energy("a", 1200, "eco") == 600
    assert learner.energy("a", 1200, "auto") == 900
    assert learner.duration("a", 4, "eco") == 1
    assert learner.duration("a", 4, "auto") == 0.5
    assert learner.energy("a", 1200, "new") == 750
    assert learner.duration("a", 4, "new") == 4
    assert learner.programs == {}


def test_late_program_arrival_and_conflict_do_not_mislabel_cycles():
    learner = ApplianceLearning()
    for minute, program in [(0, None), (5, "eco"), (10, None), (15, None)]:
        learner.observe(
            "a",
            NOW + timedelta(minutes=minute),
            minute < 15,
            None,
            minute * 10,
            complete_start=minute == 0,
            program=program,
        )
    assert learner.program_samples["a"]["eco"] == [[150, 0.25]]
    for minute, program in [(0, "eco"), (5, "auto"), (10, None)]:
        learner.observe(
            "a",
            NOW + timedelta(minutes=minute),
            minute < 10,
            None,
            minute * 10,
            complete_start=minute == 0,
            program=program,
        )
    assert learner.samples["a"] == [150]
    assert "auto" not in learner.program_samples["a"]


def test_program_storage_validation_and_bounds():
    learner = ApplianceLearning()
    learner.restore_programs(None)
    learner.restore_programs(
        {
            "bad": [],
            "a": {
                "": [],
                "x" * 256: [],
                "invalid": None,
                "empty": [None, [], [True, 1], [20, float("nan")], [-1, 2], [20, 25]],
                "eco": [[100, 1]] * 25,
            },
        }
    )
    assert learner.program_samples == {"a": {"eco": [[100, 1]] * 20}}
    for i in range(34):
        _program_cycle(learner, f"p{i}")
    assert len(learner.program_samples["a"]) == 32
    assert "eco" not in learner.program_samples["a"]
    for _ in range(22):
        _program_cycle(learner, "p33")
    assert len(learner.program_samples["a"]["p33"]) == 20


async def test_selected_program_preview_and_active_program_learning(hass):
    coordinator, key = _coordinator(
        hass,
        program_entity="sensor.program",
        selected_program_entity="select.program",
        total_time_entity="sensor.total",
    )
    learner = coordinator._appliance_learning
    learner.restore_programs({key: {"eco": [[600, 3]], "auto": [[900, 1]]}})
    hass.states.async_set("sensor.status", "ready")
    hass.states.async_set("sensor.total", "270")
    hass.states.async_set("select.program", "eco")
    coordinator._get_appliance_runs(NOW)
    preview = coordinator.build_system_config().appliances[0]
    assert (preview.run_energy_wh, preview.run_duration_h) == (600, 3)
    hass.states.async_set("sensor.total", "unknown")
    for minute in range(0, 31, 5):
        hass.states.async_set("sensor.status", "running" if minute < 30 else "finished")
        hass.states.async_set("sensor.program", "auto" if minute < 25 else "unknown")
        hass.states.async_set(
            "sensor.energy", str(minute * 10), {"unit_of_measurement": "Wh"}
        )
        if minute == 30:
            # Config is built before run observation: the new preview must not
            # inherit the previous run's still-latched program for one refresh.
            assert coordinator.build_system_config().appliances[0].run_energy_wh == 600
        runs = coordinator._get_appliance_runs(NOW + timedelta(minutes=minute))
        if minute == 25:
            assert (
                coordinator._appliance_program(
                    key, coordinator.entry.subentries[key].data
                )
                == "auto"
            )
            assert runs[0].remaining_energy_wh == pytest.approx(900 * (1 - 25 / 60))
    assert learner.program_samples[key]["eco"] == [[600, 3]]
    assert learner.program_samples[key]["auto"][-1] == [300, 0.5]
    assert coordinator.build_system_config().appliances[0].run_energy_wh == 600
    assert {"sensor.program", "select.program"} <= set(
        _tracked_with_required_inputs(coordinator)
    )


async def test_program_profile_persistence_and_midrun_start(hass):
    from unittest.mock import AsyncMock, patch

    coordinator, key = _coordinator(hass, program_entity="sensor.program")
    coordinator._appliance_learning.restore_programs({key: {"eco": [[600, 2]]}})
    restored, _ = _coordinator(hass)
    with (
        patch.object(
            restored._store,
            "async_load",
            AsyncMock(return_value=coordinator._persistent_payload()),
        ),
        patch.object(restored.learner, "async_load", AsyncMock()),
    ):
        await restored.async_load_persistent_state()
    assert restored._appliance_learning.energy(key, 1000, "eco") == 600
    assert restored._appliance_learning.duration(key, 4, "eco") == 2
    hass.states.async_set("sensor.status", "running")
    hass.states.async_set("sensor.program", "eco")
    coordinator._get_appliance_runs(NOW)
    assert coordinator._appliance_learning.programs[key] == "eco"
    assert coordinator._appliance_learning.active == {}
    hass.states.async_set("sensor.status", "unavailable")
    coordinator._get_appliance_runs(NOW)
    coordinator._get_appliance_runs(NOW + timedelta(minutes=31))
    assert coordinator._appliance_learning.programs == {}
