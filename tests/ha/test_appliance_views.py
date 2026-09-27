"""Household device views stay truthful and responsive without a valid planner."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import WSMsgType
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_appliance_learning import _coordinator
from test_coordinator import ENTRY_DATA, _set_input_states

from custom_components.battery_manager.appliance_api import async_register_appliance_api
from custom_components.battery_manager.appliance_runtime import APPLIANCE_VIEW_INTERVAL
from custom_components.battery_manager.appliance_sensor import (
    APPLIANCE_SENSORS,
    ApplianceSensor,
)
from custom_components.battery_manager.const import DOMAIN
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.core.model import ApplianceAdvisory, HourSlot

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


@pytest.fixture
def appliance(hass, freezer):
    freezer.move_to(NOW)
    coordinator, key = _coordinator(
        hass,
        opportunistic_start=False,
        program_entity="sensor.program",
        selected_program_entity="select.program",
        total_time_entity="sensor.total",
    )
    # Drive only the appliance observer; these unit scenarios deliberately have
    # no forecast inputs and must not schedule unrelated economic refreshes.
    coordinator.cleanup()
    yield coordinator, key
    coordinator.cleanup()


def _sensors(coordinator, key):
    return {
        description[0]: ApplianceSensor(coordinator, key, description)
        for description in APPLIANCE_SENSORS
    }


def _observe(hass, freezer, coordinator, minute, status, *, energy=None, power=600):
    freezer.move_to(NOW + timedelta(minutes=minute))
    hass.states.async_set("sensor.status", status)
    hass.states.async_set("sensor.power", str(power), {"unit_of_measurement": "W"})
    if energy is not None:
        hass.states.async_set(
            "sensor.energy", str(energy), {"unit_of_measurement": "Wh"}
        )
    return coordinator.appliances.update(dt_util.utcnow())


async def test_sensors_remain_available_without_forecast_and_keep_attributes_small(
    hass, freezer, appliance
):
    coordinator, key = appliance
    sensors = _sensors(coordinator, key)
    assert len(sensors) == 8
    assert all(not sensor.available for sensor in sensors.values())
    assert all(sensor.native_value is None for sensor in sensors.values())
    assert all(sensor.extra_state_attributes == {} for sensor in sensors.values())
    coordinator._appliance_learning.restore({key: [400, 600]})
    coordinator._appliance_learning.restore_programs({key: {"eco": [[600, 2]]}})
    hass.states.async_set("select.program", "eco")
    hass.states.async_set("sensor.total", "270", {"unit_of_measurement": "min"})
    _observe(hass, freezer, coordinator, 0, "ready")
    assert all(sensor.available for sensor in sensors.values())
    assert sensors["status"].native_value == "idle"
    assert sensors["program"].native_value == "eco"
    assert sensors["program"].extra_state_attributes["source"] == "selected"
    assert sensors["planning_energy"].native_value == 600
    assert sensors["planning_duration"].native_value == 120
    assert sensors["remaining"].native_value is None
    assert sensors["expected_end"].native_value is None
    assert sensors["cycle_energy"].native_value is None
    assert sensors["learning"].native_value == "learned"
    assert sensors["learning"].extra_state_attributes["sample_count"] == 2
    assert sensors["status"].extra_state_attributes["recommendation"] == {
        "allowed": None,
        "reasons": ["disabled"],
    }
    assert coordinator.data is None
    for sensor in sensors.values():
        attrs = sensor.extra_state_attributes
        assert "history" not in attrs and "profiles" not in attrs
        assert len(attrs) <= 4
    view = coordinator.appliances.snapshot(key)
    assert view["learning"]["profiles"][0]["selected"]
    assert not view["learning"]["device_profile"]["selected"]
    compact = coordinator.appliances.entity_snapshot(key)
    assert "profiles" not in compact["learning"]
    assert "history" not in compact["learning"]
    assert "device_profile" not in compact["learning"]
    assert compact["planning"] == view["planning"]
    assert view["learning"]["device_profile"]["last_learned_at"] is None
    assert "duration_h" not in view["learning"]["device_profile"]


async def test_active_program_and_reported_times_match_planner_inputs(
    hass, freezer, appliance
):
    coordinator, key = appliance
    learner = coordinator._appliance_learning
    learner.restore({key: [400]})
    learner.restore_programs({key: {"eco": [[600, 2]], "quick": [[200, 0.5]]}})
    hass.states.async_set("select.program", "quick")
    hass.states.async_set("sensor.program", "eco")
    _observe(hass, freezer, coordinator, 0, "ready", energy=1000)
    hass.states.async_set("sensor.total", "90", {"unit_of_measurement": "min"})
    hass.states.async_set("sensor.remaining", "30", {"unit_of_measurement": "min"})
    runs = _observe(hass, freezer, coordinator, 1, "running", energy=1000)
    view = coordinator.appliances.snapshot(key)
    assert view["program"] == "eco"
    assert view["program_source"] == "active"
    assert view["planning"] == {
        "energy_wh": 600,
        "energy_source": "program_profile",
        "duration_minutes": 90,
        "duration_source": "reported",
    }
    assert runs[0].remaining_hours == 0.5
    assert runs[0].remaining_energy_wh == 200
    sensors = _sensors(coordinator, key)
    assert sensors["remaining"].native_value == 30
    assert sensors["expected_end"].native_value == NOW + timedelta(minutes=31)
    assert sensors["remaining"].extra_state_attributes["source"] == "reported"
    assert sensors["expected_end"].extra_state_attributes["source"] == "reported"
    _observe(hass, freezer, coordinator, 3, "paused", energy=1060)
    assert sensors["status"].native_value == "paused"
    assert sensors["cycle_energy"].native_value == 60
    assert sensors["cycle_energy"].extra_state_attributes["complete"]
    assert sensors["cycle_energy"].extra_state_attributes["source"] == "energy_counter"
    assert (
        sensors["planning_energy"].extra_state_attributes["source"] == "program_profile"
    )
    assert sensors["planning_duration"].extra_state_attributes["source"] == "reported"
    assert sensors["learning"].native_value == "measuring"
    hass.states.async_set("sensor.remaining", "unavailable")
    _observe(hass, freezer, coordinator, 4, "running", energy=1090)
    assert sensors["remaining"].native_value == 87
    assert sensors["remaining"].extra_state_attributes["source"] == "estimated"
    assert sensors["expected_end"].native_value == NOW + timedelta(minutes=91)
    _observe(hass, freezer, coordinator, 6, "finished", energy=1150)
    assert sensors["status"].native_value == "finished"
    assert sensors["learning"].native_value == "learned"
    assert learner.samples[key] == [400, 150]
    assert coordinator.build_system_config().appliances[0].run_energy_wh == 200


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("unknown", None),
        ("unavailable", None),
        ("mystery", None),
        ("paused", None),
        ("error", "error"),
        ("aborted", "error"),
        ("ready", "idle"),
        ("initial", "idle"),
        ("end", "finished"),
    ],
)
async def test_idle_unknown_and_error_states_are_not_reported_as_running(
    hass, freezer, appliance, raw, expected
):
    coordinator, key = appliance
    _observe(hass, freezer, coordinator, 0, raw)
    sensors = _sensors(coordinator, key)
    assert sensors["status"].native_value == expected
    assert sensors["remaining"].native_value is None
    assert sensors["cycle_energy"].native_value is None


async def test_configured_and_device_profile_fallbacks_and_source_health(
    hass, freezer, appliance
):
    coordinator, key = appliance
    _observe(hass, freezer, coordinator, 0, "ready")
    view = coordinator.appliances.snapshot(key)
    assert view["planning"] == {
        "energy_wh": 1000,
        "energy_source": "configured",
        "duration_minutes": 270,
        "duration_source": "configured",
    }
    coordinator._appliance_learning.restore({key: [300, 500]})
    hass.states.async_set("select.program", "new")
    hass.states.async_set("sensor.remaining", "unavailable")
    coordinator.appliances.publish(NOW)
    view = coordinator.appliances.snapshot(key)
    assert view["planning"]["energy_wh"] == 400
    assert view["planning"]["energy_source"] == "device_profile"
    assert view["learning"]["device_profile"]["selected"]
    assert view["planning"]["duration_source"] == "configured"
    sources = {source["kind"]: source for source in view["sources"]}
    assert sources["detection"]["available"]
    assert (
        sources["detection"]["last_reported"]
        == hass.states.get("sensor.status").last_reported.isoformat()
    )
    assert not sources["remaining_time"]["available"]
    assert sources["remaining_time"]["state"] == "unavailable"
    assert sources["total_time"]["entity_id"] == "sensor.total"
    assert sources["total_time"]["last_reported"] is None
    assert not sources["total_time"]["available"]


async def test_restart_partial_measurement_remains_unknown_and_never_teaches(
    hass, freezer, appliance
):
    coordinator, key = appliance
    _observe(hass, freezer, coordinator, 0, "running", energy=1000, power="unavailable")
    view = coordinator.appliances.snapshot(key)
    assert not view["observation"]["complete"]
    assert view["learning"]["status"] == "invalid"
    assert view["learning"]["reasons"] == ["missing_start"]
    assert view["learning"]["history"] == []
    _observe(
        hass, freezer, coordinator, 5, "finished", energy=1100, power="unavailable"
    )
    assert coordinator._appliance_learning.samples == {}
    history = coordinator.appliances.snapshot(key)["learning"]["history"]
    assert history[-1]["reasons"] == ["missing_start"]
    assert history[-1]["energy_wh"] == 100
    _observe(hass, freezer, coordinator, 6, "running", energy=1100)
    _observe(hass, freezer, coordinator, 7, "unavailable", energy=1110)
    view = coordinator.appliances.snapshot(key)
    assert view["status"] == "unknown"
    assert not view["observation"]["complete"]
    assert view["learning"]["status"] == "invalid"


async def test_publication_deduplicates_reads_and_never_rewinds_cycle(
    hass, freezer, appliance
):
    coordinator, key = appliance
    listener = Mock()
    unsubscribe = coordinator.appliances.subscribe(listener)
    _observe(hass, freezer, coordinator, 0, "ready", energy=1000)
    _observe(hass, freezer, coordinator, 1, "running", energy=1000)
    _observe(hass, freezer, coordinator, 3, "running", energy=1050)
    revision = coordinator.appliances.revision
    snapshot = coordinator.appliances.snapshot(key)
    samples = deepcopy(coordinator._appliance_learning.samples)
    listener.reset_mock()
    for _ in range(3):
        coordinator.appliances.payload([key])["appliances"][0]["learning"][
            "profiles"
        ].append({"external": True})
        coordinator.appliances.snapshot(key)["observation"]["cycle_energy_wh"] = -1
        coordinator.appliances.publish(NOW + timedelta(minutes=3))
    assert coordinator.appliances.revision == revision
    listener.assert_not_called()
    assert coordinator.appliances.snapshot(key) == snapshot
    assert coordinator._appliance_learning.samples == samples
    coordinator.appliances.update(NOW + timedelta(minutes=2))
    assert coordinator.appliances.snapshot(key) == snapshot
    listener.assert_not_called()
    assert coordinator._appliance_learning.active[key]["at"] == NOW + timedelta(
        minutes=3
    )
    unsubscribe()
    _observe(hass, freezer, coordinator, 4, "finished", energy=1100)
    listener.assert_not_called()
    assert coordinator._appliance_learning.samples[key] == [100]
    coordinator.appliances.update(NOW + timedelta(minutes=4))
    assert coordinator._appliance_learning.samples[key] == [100]
    coordinator.entry.subentries = {}
    coordinator.appliances.publish(NOW + timedelta(minutes=4))
    assert coordinator.appliances.payload()["appliances"] == []


async def test_advisories_expire_and_do_not_hide_profiles_after_plan_failure(
    hass, freezer
):
    freezer.move_to(NOW)
    coordinator, key = _coordinator(hass, opportunistic_start=True)
    coordinator.cleanup()
    hass.states.async_set("sensor.status", "ready")
    coordinator._appliance_learning.restore({key: [500]})
    coordinator.appliances.update(NOW)
    assert coordinator.appliances.snapshot(key)["recommendation"]["reasons"] == [
        "no_valid_plan"
    ]
    result = SimpleNamespace(
        appliance_advisories={
            key: ApplianceAdvisory(False, ("extra_grid_import", "soc_condition"))
        }
    )
    inputs = SimpleNamespace(slots=(HourSlot(0, NOW, 1, NOW.hour, 0, 0, 0),))
    config = coordinator.build_system_config()
    signature = coordinator.appliances.planning_signature()
    coordinator.appliances.plan_updated(result, inputs, False, config, signature)
    recommendation = coordinator.appliances.snapshot(key)["recommendation"]
    assert recommendation == {
        "allowed": False,
        "reasons": ["extra_grid_import", "soc_condition"],
    }
    result.appliance_advisories[key] = ApplianceAdvisory(True)
    coordinator.appliances.plan_updated(result, inputs, False, config, signature)
    assert coordinator.appliances.snapshot(key)["recommendation"]["allowed"] is True
    coordinator.appliances.plan_updated(result, inputs, True, config, signature)
    assert coordinator.appliances.snapshot(key)["recommendation"]["reasons"] == [
        "safety_block"
    ]
    coordinator.appliances.plan_updated(result, inputs, False, config, signature)
    coordinator.appliances._tick(NOW + timedelta(hours=1))
    assert coordinator.appliances.snapshot(key)["recommendation"]["reasons"] == [
        "no_valid_plan"
    ]
    coordinator.appliances.plan_updated(
        result, SimpleNamespace(slots=()), False, config, signature
    )
    assert coordinator.appliances.snapshot(key)["recommendation"]["allowed"] is None
    coordinator.appliances.plan_failed()
    assert coordinator.appliances.snapshot(key)["planning"]["energy_wh"] == 500
    assert coordinator.appliances.snapshot(key)["learning"]["sample_count"] == 1
    coordinator.cleanup()


def _entry(hass, **options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={
                    "detection_entity": "sensor.status",
                    "power_entity": "sensor.power",
                    "run_duration_h": 2,
                    "run_energy_wh": 1000,
                    "opportunistic_start": False,
                    **options,
                },
                subentry_type="appliance",
                title="Washer",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    _set_input_states(hass)
    return entry, next(iter(entry.subentries))


async def test_entity_setup_event_and_minute_updates_continue_while_plan_is_blocked(
    hass, freezer
):
    freezer.move_to(NOW)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def blocked(*args):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        raise UpdateFailed("Forecast unavailable")

    hass.states.async_set("sensor.status", "ready")
    hass.states.async_set("sensor.power", "600", {"unit_of_measurement": "W"})
    entry, key = _entry(hass)
    with (
        patch.object(BatteryManagerCoordinator, "_async_plan", blocked),
        patch(
            "custom_components.battery_manager.appliance_runtime.async_track_time_interval"
        ) as interval,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await entered.wait()
        coordinator = entry.runtime_data
        registry = er.async_get(hass)
        ids = {
            name: registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_appliance_{name}_{key}"
            )
            for name, *_ in APPLIANCE_SENSORS
        }
        assert all(ids.values())
        assert all(
            registry.async_get(entity_id).config_subentry_id == key
            for entity_id in ids.values()
        )
        assert hass.states.get(ids["status"]).state == "idle"
        assert hass.states.get(ids["planning_energy"]).state == "1000.0"
        assert hass.states.get(ids["planning_duration"]).state == "120.0"
        assert not coordinator.data["valid"]
        assert (
            interval.call_args.args[2]
            == APPLIANCE_VIEW_INTERVAL
            == timedelta(minutes=1)
        )
        coordinator.appliances.start()
        interval.assert_called_once()
        freezer.move_to(NOW + timedelta(seconds=1))
        hass.states.async_set("sensor.status", "running")
        await hass.async_block_till_done(wait_background_tasks=False)
        assert hass.states.get(ids["status"]).state == "running"
        freezer.move_to(NOW + timedelta(minutes=1, seconds=1))
        interval.call_args.args[1](dt_util.utcnow())
        assert float(hass.states.get(ids["cycle_energy"]).state) == 10
        assert float(hass.states.get(ids["remaining"]).state) == 119
        assert calls == 1  # UI cadence never starts a second optimizer.
        release.set()
        await coordinator._initial_refresh_task
        await hass.async_block_till_done(wait_background_tasks=False)
        assert not coordinator.last_update_success
        assert hass.states.get(ids["status"]).state == "running"
        assert hass.states.get(ids["planning_energy"]).state == "1000.0"
        assert await hass.config_entries.async_unload(entry.entry_id)
        interval.return_value.assert_called_once()


async def test_websocket_discovery_filter_validation_and_reads_do_not_observe(
    hass, appliance, hass_ws_client
):
    coordinator, key = appliance
    coordinator.appliances.update(NOW)
    hass.data[DOMAIN] = {coordinator.entry.entry_id: coordinator}
    websocket = await hass_ws_client(hass)
    async_register_appliance_api(hass)

    async def request(**parameters):
        await websocket.send_json_auto_id(
            {"type": "battery_manager/appliances", **parameters}
        )
        return await websocket.receive_json()

    response = await request()
    assert response["success"]
    assert response["result"]["entries"] == [
        {"entry_id": coordinator.entry.entry_id, "title": coordinator.entry.title}
    ]
    before = coordinator._appliance_learning.metadata_payload()
    with patch.object(
        coordinator,
        "_observe_appliance_runs",
        side_effect=AssertionError("A read observed the device"),
    ):
        response = await request(entry_id=coordinator.entry.entry_id)
        assert response["result"]["appliances"][0]["id"] == key
        assert (
            await request(entry_id=coordinator.entry.entry_id, appliance_ids=[key])
        )["success"]
        assert (await request(entry_id=coordinator.entry.entry_id, appliance_ids=[]))[
            "result"
        ]["appliances"] == []
    assert coordinator._appliance_learning.metadata_payload() == before
    assert (await request(entry_id="unloaded"))["error"]["code"] == "not_loaded"
    assert (
        await request(entry_id=coordinator.entry.entry_id, appliance_ids=["foreign"])
    )["error"]["code"] == "not_found"
    assert (
        await request(entry_id=coordinator.entry.entry_id, appliance_ids="wrong type")
    )["error"]["code"] == "invalid_format"
    coordinator._actuation_shutdown = True
    assert (await request())["result"]["entries"] == []
    assert (await request(entry_id=coordinator.entry.entry_id))["error"][
        "code"
    ] == "not_loaded"


@patch("aiohttp.web_ws.WebSocketResponse._reset_heartbeat", return_value=None)
async def test_appliance_api_requires_websocket_authentication(
    _heartbeat, hass, hass_client_no_auth
):
    # Authentication is exercised on a real socket. Its unrelated 55-second
    # aiohttp heartbeat is disabled so rejecting auth leaves no framework timer.
    assert await async_setup_component(hass, "websocket_api", {})
    async_register_appliance_api(hass)
    client = await hass_client_no_auth()
    websocket = await client.ws_connect("/api/websocket")
    assert (await websocket.receive_json())["type"] == "auth_required"
    await websocket.send_json({"id": 1, "type": "battery_manager/appliances"})
    assert (await websocket.receive_json())["type"] == "auth_invalid"
    assert (await websocket.receive()).type is WSMsgType.CLOSE
    await websocket.close()
    await client.close()
    await hass.async_block_till_done()


async def test_same_timestamp_state_change_starts_cycle_once(hass, freezer, appliance):
    coordinator, key = appliance
    _observe(hass, freezer, coordinator, 0, "ready", energy=1000)
    runs = _observe(hass, freezer, coordinator, 0, "running", energy=1000)
    assert runs and runs[0].appliance_id == key
    assert coordinator.appliances.snapshot(key)["observation"]["complete"]
    _observe(hass, freezer, coordinator, 5, "finished", energy=1100)
    coordinator.appliances.update(NOW + timedelta(minutes=5))
    assert coordinator._appliance_learning.samples[key] == [100]
    assert len(coordinator.appliances.snapshot(key)["learning"]["history"]) == 1


@pytest.mark.parametrize("change", ["program", "energy", "duration"])
async def test_advisory_for_inputs_changed_during_planning_is_not_published(
    hass, freezer, change
):
    freezer.move_to(NOW)
    coordinator, key = _coordinator(
        hass, opportunistic_start=True, selected_program_entity="select.program"
    )
    coordinator.cleanup()
    learner = coordinator._appliance_learning
    learner.restore_programs({key: {"eco": [[600, 2]], "quick": [[600, 2]]}})
    hass.states.async_set("sensor.status", "ready")
    hass.states.async_set("select.program", "eco")
    coordinator.appliances.update(NOW)
    config = coordinator.build_system_config()
    signature = coordinator.appliances.planning_signature()
    inputs = SimpleNamespace(slots=(HourSlot(0, NOW, 1, NOW.hour, 0, 0, 0),))
    result = SimpleNamespace(appliance_advisories={key: ApplianceAdvisory(True)})
    if change == "program":
        hass.states.async_set("select.program", "quick")
    elif change == "energy":
        learner.restore_programs({key: {"eco": [[900, 2]]}})
    else:
        learner.restore_programs({key: {"eco": [[600, 3]]}})
    coordinator.appliances.plan_updated(result, inputs, False, config, signature)
    assert coordinator.appliances.snapshot(key)["recommendation"] == {
        "allowed": None,
        "reasons": ["no_valid_plan"],
    }
    coordinator.cleanup()


async def test_program_change_and_floor_guard_revoke_current_start_advice(
    hass, freezer
):
    freezer.move_to(NOW)
    coordinator, key = _coordinator(
        hass, opportunistic_start=True, selected_program_entity="select.program"
    )
    coordinator.cleanup()
    hass.states.async_set("sensor.status", "ready")
    hass.states.async_set("select.program", "eco")
    coordinator.appliances.update(NOW)
    config = coordinator.build_system_config()
    signature = coordinator.appliances.planning_signature()
    inputs = SimpleNamespace(slots=(HourSlot(0, NOW, 1, NOW.hour, 0, 0, 0),))
    result = SimpleNamespace(appliance_advisories={key: ApplianceAdvisory(True)})
    coordinator.appliances.plan_updated(result, inputs, False, config, signature)
    assert coordinator.appliances.snapshot(key)["recommendation"]["allowed"]
    listener = Mock()
    unsubscribe = coordinator.appliances.subscribe(listener)
    coordinator.appliances.set_floor_guard(True)
    assert coordinator.appliances.snapshot(key)["recommendation"] == {
        "allowed": False,
        "reasons": ["safety_block"],
    }
    listener.assert_called_once()
    coordinator.appliances.set_floor_guard(False)
    assert coordinator.appliances.snapshot(key)["recommendation"]["allowed"]
    hass.states.async_set("select.program", "quick")
    coordinator.appliances.update(NOW)
    assert coordinator.appliances.snapshot(key)["recommendation"]["reasons"] == [
        "no_valid_plan"
    ]
    unsubscribe()
    coordinator.cleanup()


async def test_removing_appliance_removes_all_eight_entities_without_orphans(hass):
    hass.states.async_set("sensor.status", "ready")
    entry, key = _entry(hass)
    with patch.object(
        BatteryManagerCoordinator,
        "_async_plan",
        AsyncMock(side_effect=UpdateFailed("No forecast")),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        device_entities = [
            registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_appliance_{name}_{key}"
            )
            for name, *_ in APPLIANCE_SENSORS
        ]
        base_entities = [
            row.entity_id
            for row in er.async_entries_for_config_entry(registry, entry.entry_id)
            if row.config_subentry_id is None
        ]
        assert all(device_entities) and base_entities
        assert hass.config_entries.async_remove_subentry(entry, key)
        await hass.async_block_till_done()
        await hass.async_block_till_done()
        assert all(
            registry.async_get(entity_id) is None for entity_id in device_entities
        )
        assert all(
            registry.async_get(entity_id) is not None for entity_id in base_entities
        )
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_frequent_measurements_persist_progress_once_per_minute_and_cycle_edges(
    hass, freezer, appliance
):
    from custom_components.battery_manager.appliance_runtime import (
        APPLIANCE_PERSIST_INTERVAL,
    )

    coordinator, key = appliance
    assert timedelta(minutes=1) == APPLIANCE_PERSIST_INTERVAL
    with patch.object(coordinator, "_save_persistent_state") as save:
        _observe(hass, freezer, coordinator, 0, "ready", energy=1000)
        assert save.call_count == 1
        _observe(hass, freezer, coordinator, 1 / 60, "running", energy=1000)
        assert save.call_count == 2  # Start marker is saved immediately.
        for second in range(6, 117, 5):
            _observe(
                hass, freezer, coordinator, second / 60, "running", energy=1000 + second
            )
        assert (
            save.call_count == 3
        )  # Continuous reports cannot postpone the checkpoint.
        _observe(hass, freezer, coordinator, 121 / 60, "running", energy=1121)
        assert save.call_count == 4
        _observe(hass, freezer, coordinator, 126 / 60, "finished", energy=1126)
        assert save.call_count == 5  # Completed sample does not wait another minute.
    assert coordinator._appliance_learning.samples[key] == [126]
    history = coordinator.appliances.snapshot(key)["learning"]["history"]
    assert len(history) == 1 and history[0]["accepted"]


async def test_failed_platform_setup_unsubscribes_appliance_observers(hass):
    hass.states.async_set("sensor.status", "ready")
    entry, _ = _entry(hass)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(side_effect=RuntimeError("Failed platform setup")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    assert coordinator.appliances._unsub == []
    assert coordinator._initial_refresh_task is None
    with patch.object(coordinator.appliances, "update") as observe:
        hass.states.async_set("sensor.status", "running")
        await hass.async_block_till_done(wait_background_tasks=False)
        observe.assert_not_called()
    coordinator.cleanup()


async def test_registered_start_window_updates_on_program_change_and_floor_guard(
    hass, freezer
):
    freezer.move_to(NOW)
    hass.states.async_set("sensor.status", "ready")
    hass.states.async_set("select.program", "eco")
    entry, key = _entry(
        hass, opportunistic_start=True, selected_program_entity="select.program"
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.data["valid"]
    # Keep real appliance events/entities, isolate the old completed plan from
    # automatic re-planning so this test proves publication before another plan.
    coordinator.cleanup()
    coordinator.appliances.start()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_appliance_{key}"
    )
    assert entity_id is not None
    result = SimpleNamespace(appliance_advisories={key: ApplianceAdvisory(True)})
    inputs = SimpleNamespace(slots=(HourSlot(0, NOW, 1, NOW.hour, 0, 0, 0),))
    config = coordinator.build_system_config()
    signature = coordinator.appliances.planning_signature()
    coordinator.data["appliance_windows"] = {key: True}
    coordinator.appliances.plan_updated(result, inputs, False, config, signature)
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["reasons"] == []
    hass.states.async_set("select.program", "quick")
    await hass.async_block_till_done(wait_background_tasks=False)
    assert coordinator.data["appliance_windows"][key] is True
    assert hass.states.get(entity_id).state == "unknown"
    assert hass.states.get(entity_id).attributes["reasons"] == ["no_valid_plan"]
    coordinator.appliances.set_floor_guard(True)
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["reasons"] == ["safety_block"]
    assert await hass.config_entries.async_unload(entry.entry_id)
