"""October incident: 2 kW import while a logically stopped Fossibot charges."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from test_frontend import _find_forecast_state
from test_load_switching import (
    ENABLE,
    PLUG,
    _active_plan,
    _detach_listener,
    _register_switch_services,
    _setup,
)

from custom_components.battery_manager.const import (
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_POWER_W,
    INPUT_OFF_POLICY_KEEP,
)
from custom_components.battery_manager.load_actuation import (
    LoadAction,
    reconcile_feedback,
)
from custom_components.battery_manager.load_safety import (
    grid_import_w,
    start_blocked,
    stop_unsafe_loads,
)

GRID = "sensor.net_grid"


async def rig(hass, calls):
    c, load_id, data = await _setup(hass, calls, min_runtime_min=30)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
    calls.clear()
    return c, load_id, data


async def drain(c):
    if c._load_switch_task:
        await c._load_switch_task


async def test_actual_import_overrides_pv_forecast_inverter_and_min_runtime(hass):
    calls = []
    c, load_id, _ = await rig(hass, calls)
    c._inverter_recommendation = True
    c._last_load_switch[load_id] = dt_util.now()
    c._load_plug_owned[load_id] = True
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    await c._apply_load_switching(_active_plan(load_id), dt_util.now(), pv_power_w=3500)
    await drain(c)
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")
    assert not c._effective_load_active(
        _active_plan(load_id).load_plans[0], dt_util.now()
    )
    assert c._load_last_off[load_id][1] is False


@pytest.mark.parametrize("failure", ["service", "confirmation"])
async def test_owned_failed_input_stop_retries_despite_gate_off(hass, freezer, failure):
    calls = []
    c, load_id, data = await rig(hass, calls)
    c._load_plug_owned[load_id] = True
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")

    async def fail_input(call):
        entity = call.data["entity_id"]
        if entity == PLUG:
            if failure == "service":
                raise HomeAssistantError("device rejected stop")
            return
        hass.states.async_set(entity, "off")

    hass.services.async_register("homeassistant", "turn_off", fail_input)

    async def observed(_hass, entity, _desired):
        return entity != PLUG

    with patch("custom_components.battery_manager.coordinator.confirm_state", observed):
        await c._execute_load_switching([LoadAction(load_id, data, False, True)])
    assert hass.states.is_state(ENABLE, "off")
    assert c._load_actor_requests[PLUG].state == (
        "service_failed" if failure == "service" else "confirmation_failed"
    )
    # The OFF helper makes logical charging false, but ownership must survive.
    assert c._charging_is_active(data) is False
    _register_switch_services(hass, calls)
    await asyncio.gather(*await stop_unsafe_loads(c))
    assert calls == []  # bounded retry, no real-time waits
    freezer.tick(timedelta(seconds=61))
    # No usable grid sample or fresh planner is needed to finish our old stop.
    c._last_planner_recording = None
    await asyncio.gather(*await stop_unsafe_loads(c))
    assert calls == [("turn_off", PLUG)]
    assert hass.states.is_state(PLUG, "off")
    assert not c._load_plug_owned[load_id]


async def test_normal_plan_also_finishes_owned_input_stop(hass):
    calls = []
    c, load_id, _ = await rig(hass, calls)
    c.raw_config.pop("operation_import_power_entity")
    c._load_plug_owned[load_id] = True
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "off")
    await c._apply_load_switching(SimpleNamespace(load_plans=[]), dt_util.now())
    await drain(c)
    assert calls == [("turn_off", PLUG)]
    assert not c._load_plug_owned[load_id]


@pytest.mark.parametrize("after", ["gate", "input"])
async def test_import_during_confirmation_revokes_start(hass, freezer, after):
    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")

    async def activate(call):
        entity = call.data["entity_id"]
        calls.append(("turn_on", entity))
        hass.states.async_set(entity, "on")
        if entity == (ENABLE if after == "gate" else PLUG):
            freezer.tick(timedelta(seconds=1))
            hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
            freezer.tick(timedelta(seconds=2))

    async def deactivate(call):
        entity = call.data["entity_id"]
        calls.append(("turn_off", entity))
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        hass.states.async_set(entity, "off")

    hass.services.async_register("homeassistant", "turn_on", activate)
    hass.services.async_register("homeassistant", "turn_off", deactivate)
    started_at = dt_util.utcnow()
    await c._execute_load_switching([LoadAction(load_id, data, True, False)])
    assert hass.states.is_state(PLUG, "off")
    assert hass.states.is_state(ENABLE, "off")
    assert (
        ("turn_on", PLUG) in calls
        if after == "input"
        else ("turn_on", PLUG) not in calls
    )
    await stop_unsafe_loads(c)
    guard = _find_forecast_state(hass).attributes["load_grid_guard"]
    assert guard["measured_w"] == 0
    stop = guard["last_action"]
    assert stop["path"] == "confirmation"
    assert stop["load_ids"] == [load_id]
    assert stop["measured_w"] == 2000
    assert stop["measured_at"] == (started_at + timedelta(seconds=1)).isoformat()
    assert stop["age_seconds"] == 2
    assert stop["decided_at"] == (started_at + timedelta(seconds=3)).isoformat()


async def test_revoked_pending_on_keeps_grid_evidence_until_late_cleanup(hass, freezer):
    c, load_id, data = await rig(hass, [])
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    sent = asyncio.Event()
    respond = asyncio.Event()

    async def activate(call):
        sent.set()
        await respond.wait()
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("homeassistant", "turn_on", activate)
    action = asyncio.create_task(
        c._execute_load_switching([LoadAction(load_id, data, True, False)])
    )
    try:
        await sent.wait()
        hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
        measured_at = dt_util.utcnow().isoformat()
        reconcile_feedback(c)
        assert "last_action" not in (
            c.data.get("load_grid_guard") or {}
        )  # No OFF has been issued yet.
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    finally:
        respond.set()
        await action
    await asyncio.gather(*await stop_unsafe_loads(c))
    assert hass.states.is_state(ENABLE, "off")
    guard = _find_forecast_state(hass).attributes["load_grid_guard"]
    assert guard["measured_w"] == 0
    assert guard["last_action"]["measured_w"] == 2000
    assert guard["last_action"]["measured_at"] == measured_at


async def test_keep_on_timer_rechecks_do_not_replace_dispatched_grid_stop(
    hass, freezer
):
    calls = []
    c, load_id, _ = await _setup(hass, calls, policy=INPUT_OFF_POLICY_KEEP)
    _detach_listener(c)
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c._load_plug_owned[load_id] = True
    calls.clear()
    await asyncio.gather(*await stop_unsafe_loads(c))
    assert calls == [("turn_off", ENABLE)]
    previous = _find_forecast_state(hass).attributes["load_grid_guard"]["last_action"]
    with patch.object(
        c, "async_update_listeners", wraps=c.async_update_listeners
    ) as publish:
        for _ in range(3):
            freezer.tick(timedelta(seconds=5))
            hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
            await asyncio.gather(*await stop_unsafe_loads(c))
        publish.assert_not_called()
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        await asyncio.gather(*await stop_unsafe_loads(c))
        publish.assert_called_once()  # The current reading and guard changed.
        publish.reset_mock()
        for _ in range(3):
            freezer.tick(timedelta(seconds=5))
            await asyncio.gather(*await stop_unsafe_loads(c))
        publish.assert_not_called()
    assert calls == [("turn_off", ENABLE)]
    assert hass.states.is_state(PLUG, "on")
    assert c._load_plug_owned[load_id]
    assert (
        _find_forecast_state(hass).attributes["load_grid_guard"]["last_action"]
        == previous
    )


@pytest.mark.parametrize(
    ("value", "unit", "blocked"),
    [
        ("2000", "W", True),
        ("2", "kW", True),
        ("-2000", "W", False),
        ("50", "W", False),
        ("51", "W", True),
        ("unknown", "W", False),
        ("2000", "Wh", False),
    ],
)
async def test_meter_sign_units_and_noise(hass, value, unit, blocked):
    c, *_ = await rig(hass, [])
    hass.states.async_set(GRID, value, {"unit_of_measurement": unit})
    assert start_blocked(c) is blocked


async def test_stale_or_partial_measurements_do_not_fabricate_import(hass, freezer):
    c, *_ = await rig(hass, [])
    c.raw_config["live_ac_grid_power_entity"] = GRID
    freezer.tick(timedelta(seconds=31))
    assert grid_import_w(c) is None
    assert not start_blocked(c)
    # A configured net meter takes precedence; house/PV cannot replace its sign.
    c.raw_config["operation_import_power_entity"] = "sensor.other"
    hass.states.async_set("sensor.other", "2000", {"unit_of_measurement": "W"})
    assert grid_import_w(c) is None


@pytest.mark.parametrize("path", ["plan", "fast"])
@pytest.mark.parametrize(
    ("binding", "value", "unit", "age", "status", "watts", "active"),
    [
        (None, None, None, 0, "unconfigured", None, False),
        ("preferred", None, None, 0, "unavailable", None, False),
        ("preferred", "unknown", "W", 0, "unavailable", None, False),
        ("preferred", "51", "Wh", 0, "invalid", None, False),
        ("preferred", "nan", "W", 0, "invalid", None, False),
        ("preferred", "1e308", "kW", 0, "invalid", None, True),
        ("preferred", "51", "W", -1, "invalid", None, False),
        ("preferred", "51", "W", 31, "stale", 51, False),
        ("preferred", "-12", "W", 31, "stale", -12, False),
        ("preferred", "50", "W", 30, "fresh", 50, False),
        ("fallback", "0.051", "kW", 0, "fresh", 51, True),
    ],
)
async def test_grid_guard_forecast_sensor(
    hass, freezer, path, binding, value, unit, age, status, watts, active
):
    c, load_id, _ = await rig(hass, [])
    # These cases exercise the current measurement. Stop evidence is tested
    # separately with a running load and an observed switch transition.
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    c._load_plug_owned.pop(load_id, None)
    entity = None
    if binding == "preferred":
        # Even a missing preferred meter cannot borrow a fallback reading.
        entity = "sensor.preferred_grid"
        c.raw_config["live_ac_grid_power_entity"] = entity
    elif binding == "fallback":
        entity = GRID
    else:
        c.raw_config.pop("operation_import_power_entity")
    measured_at = None
    if value is not None:
        if age < 0:
            freezer.tick(timedelta(seconds=2))
        hass.states.async_set(entity, value, {"unit_of_measurement": unit})
        measured_at = hass.states.get(entity).last_reported.isoformat()
    freezer.tick(timedelta(seconds=age))

    if path == "plan":
        await c.async_refresh()
    else:
        c.live_ac.envelope = None
        with patch.object(c, "_async_plan", side_effect=AssertionError("CPU search")):
            await c.live_ac.run()

    state = _find_forecast_state(hass)
    assert state.attributes["load_grid_guard"] == {
        "status": status,
        "active": active,
        "grid_import_w": watts if status == "fresh" else None,
        "measured_w": watts,
        "entity_id": entity,
        "measured_at": measured_at,
        "age_seconds": age if measured_at else None,
        "threshold_w": 50,
        "max_age_seconds": 30,
    }


async def test_fast_guard_publishes_expiration_but_not_age_only(hass, freezer):
    c, *_ = await rig(hass, [])
    hass.states.async_set(GRID, "20", {"unit_of_measurement": "W"})
    await stop_unsafe_loads(c)
    entity_id = _find_forecast_state(hass).entity_id
    with patch.object(
        c, "async_update_listeners", wraps=c.async_update_listeners
    ) as publish:
        for _ in range(2):
            freezer.tick(timedelta(seconds=5))
            hass.states.async_set(GRID, "20", {"unit_of_measurement": "W"})
            await stop_unsafe_loads(c)
            publish.assert_not_called()
        freezer.tick(timedelta(seconds=30))
        await stop_unsafe_loads(c)
        publish.assert_not_called()
        freezer.tick(timedelta(seconds=1))
        await stop_unsafe_loads(c)
        publish.assert_called_once()
    guard = hass.states.get(entity_id).attributes["load_grid_guard"]
    assert guard["status"] == "stale"
    assert guard["age_seconds"] == 31


@pytest.mark.parametrize("path", ["plan", "fast"])
async def test_grid_stop_keeps_the_decision_sample_after_a_zero_w_report(
    hass, freezer, path
):
    c, load_id, _ = await rig(hass, [])
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c._load_plug_owned[load_id] = True
    measured_at = hass.states.get(GRID).last_reported.isoformat()
    freezer.tick(timedelta(seconds=2))
    decided_at = dt_util.utcnow().isoformat()

    def clear_import():
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})

    if path == "plan":
        apply = c.cascade_manager.async_apply

        async def report_during_planning(*args, **kwargs):
            clear_import()
            await apply(*args, **kwargs)

        with (
            patch.object(c, "_update_floor_guard", return_value=False),
            patch.object(c.cascade_manager, "async_apply", report_during_planning),
        ):
            await c.async_refresh()
            await drain(c)
    else:
        await asyncio.gather(*await stop_unsafe_loads(c))
        # An active guard with no second stop to request must retain the
        # original observation even if the meter reports a different value.
        hass.states.async_set(GRID, "2200", {"unit_of_measurement": "W"})
        assert not await stop_unsafe_loads(c)
        clear_import()
        await stop_unsafe_loads(c)

    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")
    guard = _find_forecast_state(hass).attributes["load_grid_guard"]
    assert guard["status"] == "fresh"
    assert guard["measured_w"] == 0
    assert guard["active"] is False
    last = dict(guard["last_action"])
    assert decided_at <= last.pop("requested_at") <= dt_util.utcnow().isoformat()
    assert last == {
        "action": "stop_requested",
        "path": path,
        "decided_at": decided_at,
        "load_ids": [load_id],
        "entity_id": GRID,
        "measured_at": measured_at,
        "measured_w": 2000,
        "age_seconds": 2,
        "status": "fresh",
        "active": True,
        "threshold_w": 50,
        "max_age_seconds": 30,
    }


async def test_fast_timer_stops_without_cpu_plan_and_caps_recommendation(hass):
    calls = []
    c, load_id, _ = await rig(hass, calls)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c._load_plug_owned[load_id] = True
    c.data["load_plans"] = {load_id: {"active": True}}
    c.live_ac.envelope = None
    with patch.object(c, "_async_plan", side_effect=AssertionError("CPU search")):
        await c.live_ac.run()
        await drain(c)
    assert hass.states.is_state(PLUG, "off")
    assert not c.data["load_plans"][load_id]["active"]
    assert c.data["load_grid_guard"]["active"]
    assert c.data["load_grid_guard"]["grid_import_w"] == 2000


async def test_fast_guard_respects_cascade_and_calibration_owners(hass):
    calls = []
    c, load_id, _ = await rig(hass, calls)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c._load_plug_owned[load_id] = True
    with patch.object(c.cascade_manager, "managed_load_ids", return_value={load_id}):
        await stop_unsafe_loads(c)
    c._load_power_calibration_id = load_id
    await stop_unsafe_loads(c)
    assert calls == []


async def test_gate_off_foreign_passthrough_is_not_an_owned_stop(hass):
    calls = []
    c, _, _ = await rig(hass, calls)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "off")
    await asyncio.gather(*await stop_unsafe_loads(c))
    assert calls == []
    assert hass.states.is_state(PLUG, "on")


async def test_queued_start_cannot_escape_measured_guard(hass):
    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    await c._execute_load_switching([LoadAction(load_id, data, True, False)])
    assert calls == []


async def test_shutdown_never_queues_a_new_safety_command(hass):
    calls = []
    c, _, _ = await rig(hass, calls)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c._actuation_shutdown = True
    await stop_unsafe_loads(c)
    assert calls == []


@pytest.mark.parametrize("phase", ["reconciling", "settling"])
async def test_coordinate_hold_rejects_optional_load_before_service(hass, phase):
    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    c.voltage_gate.coordinate_snapshot(dt_util.now())
    monitor = c.voltage_gate.monitor
    monitor.epoch, monitor.phase, monitor.generation, monitor.has_reconciled = (
        1,
        phase,
        2,
        True,
    )
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())
    await c._execute_load_switching(
        [
            LoadAction(
                load_id,
                data,
                True,
                False,
                soc_coordinate=stamp,
                coordinate_captured=True,
            )
        ]
    )
    assert calls == []
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")


async def test_coordinate_changed_during_gate_ack_prevents_input_start(hass):
    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())

    async def late_gate(call):
        entity = call.data["entity_id"]
        calls.append(("turn_on", entity))
        hass.states.async_set(entity, "on")
        c.voltage_gate.monitor.phase = "reconciling"
        c.voltage_gate.monitor.epoch += 1
        c.voltage_gate.monitor.generation += 1
        c.voltage_gate.monitor.has_reconciled = True

    hass.services.async_register("homeassistant", "turn_on", late_gate)
    await c._execute_load_switching(
        [
            LoadAction(
                load_id,
                data,
                True,
                False,
                soc_coordinate=stamp,
                coordinate_captured=True,
            )
        ]
    )
    assert ("turn_on", PLUG) not in calls
    assert hass.states.is_state(ENABLE, "off")
    assert not c._load_charging_active.get(load_id, False)


async def test_settling_completion_requires_new_load_plan_provenance(hass):
    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    c.voltage_gate.coordinate_snapshot(dt_util.now())
    monitor = c.voltage_gate.monitor
    monitor.epoch, monitor.phase, monitor.generation, monitor.has_reconciled = (
        1,
        "settling",
        2,
        True,
    )
    old = c.voltage_gate.coordinate_snapshot(dt_util.now())
    monitor.phase, monitor.generation = "tracking", 3
    c.voltage_gate.published_coordinate = c.voltage_gate.coordinate_snapshot(
        dt_util.now()
    )
    await c._execute_load_switching(
        [
            LoadAction(
                load_id, data, True, False, soc_coordinate=old, coordinate_captured=True
            )
        ]
    )
    assert calls == []
    await c._execute_load_switching(
        [
            LoadAction(
                load_id,
                data,
                True,
                False,
                soc_coordinate=c.voltage_gate.published_coordinate,
                coordinate_captured=True,
            )
        ]
    )
    assert ("turn_on", ENABLE) in calls and ("turn_on", PLUG) in calls


@pytest.mark.parametrize("pv,expected", [(400, False), (3000, True)])
async def test_load_during_settling_requires_independent_complete_measured_pv(
    hass, pv, expected
):
    from custom_components.battery_manager.const import (
        CONF_DC_LOAD_ENTITY,
        CONF_RESERVE_GRID_ENTITY,
    )
    from custom_components.battery_manager.live_ac import LiveACEnvelope

    calls = []
    c, load_id, data = await rig(hass, calls)
    c.raw_config.update(
        {
            CONF_DC_LOAD_ENTITY: "sensor.actual_dc",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.actual_grid",
            "operation_house_power_entity": "sensor.actual_house",
            "operation_pv_power_entity": "sensor.actual_pv",
        }
    )
    for entity, value in (
        (GRID, 0),
        ("sensor.actual_house", 100),
        ("sensor.actual_pv", pv),
        ("sensor.actual_dc", 100),
    ):
        hass.states.async_set(entity, str(value), {"unit_of_measurement": "W"})
    hass.states.async_set("binary_sensor.actual_grid", "on")
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    c.voltage_gate.coordinate_snapshot(dt_util.now())
    monitor = c.voltage_gate.monitor
    monitor.epoch, monitor.phase, monitor.generation, monitor.has_reconciled = (
        1,
        "settling",
        2,
        True,
    )
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())
    c.live_ac.envelope = LiveACEnvelope(
        c.build_system_config(),
        dt_util.utcnow() + timedelta(minutes=5),
        100,
        False,
        soc_coordinate=stamp,
    )
    await c._execute_load_switching(
        [
            LoadAction(
                load_id,
                data,
                True,
                False,
                soc_coordinate=stamp,
                coordinate_captured=True,
            )
        ]
    )
    assert hass.states.is_state(ENABLE, "on") is expected
    assert hass.states.is_state(PLUG, "on") is expected


async def test_coordinate_hold_keeps_confirmed_dwell_and_explicit_manual_start(hass):
    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c.voltage_gate.coordinate_snapshot(dt_util.now())
    c.voltage_gate.monitor.phase = "settling"
    c.voltage_gate.monitor.has_reconciled = True
    await c._execute_load_switching([LoadAction(load_id, data, True, True)])
    assert calls == []
    assert hass.states.is_state(PLUG, "on") and hass.states.is_state(ENABLE, "on")
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    await c._execute_load_switching(
        [LoadAction(load_id, data, True, False, bypass_guards=True)]
    )
    assert hass.states.is_state(PLUG, "on") and hass.states.is_state(ENABLE, "on")


@pytest.mark.parametrize("manual_handoff", [False, True])
async def test_gateless_input_ack_revoked_during_service_is_cleaned_without_dwell(
    hass, manual_handoff
):
    calls = []
    c, load_id, data = await _setup(hass, calls, charge_enable=None)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = c._stale_shed_active = False
    hass.states.async_set(PLUG, "off")
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())
    before = c._last_load_switch.get(load_id)
    calls.clear()

    async def accepted(call):
        entity = call.data["entity_id"]
        calls.append(("turn_on", entity))
        hass.states.async_set(entity, "on")
        if manual_handoff:
            c.set_load_enabled(load_id, False)
        c.voltage_gate.monitor.phase = "settling"
        c.voltage_gate.monitor.has_reconciled = True
        c.voltage_gate.monitor.generation += 1

    hass.services.async_register("homeassistant", "turn_on", accepted)
    await c._execute_load_switching(
        [
            LoadAction(
                load_id,
                data,
                True,
                False,
                soc_coordinate=stamp,
                coordinate_captured=True,
            )
        ]
    )
    if c.live_ac.coordinate_cleanup_task is not None:
        await c.live_ac.coordinate_cleanup_task
    assert hass.states.is_state(PLUG, "on") is manual_handoff
    assert not c._load_charging_active.get(load_id, False)
    assert c._last_load_switch.get(load_id) == before
    assert not c._load_plug_owned.get(load_id, False)
    assert (("turn_off", PLUG) in calls) is not manual_handoff


@pytest.mark.parametrize("manual_handoff", [False, True])
async def test_gateless_truly_late_on_after_confirmation_timeout_never_adopts_dwell(
    hass, freezer, manual_handoff
):
    from custom_components.battery_manager.load_actuation import (
        LOAD_CONFIRM_TIMEOUT_S,
        reconcile_feedback,
    )

    calls = []
    c, load_id, data = await _setup(hass, calls, charge_enable=None)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = c._stale_shed_active = False
    hass.states.async_set(PLUG, "off")
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())
    before = c._last_load_switch.get(load_id)
    calls.clear()

    async def accepted_without_feedback(call):
        calls.append(("turn_on", call.data["entity_id"]))

    async def confirmation(_hass, entity, desired):
        if not desired:
            return _hass.states.is_state(entity, "off")
        c.voltage_gate.monitor.phase = "settling"
        c.voltage_gate.monitor.has_reconciled = True
        c.voltage_gate.monitor.generation += 1
        return False

    hass.services.async_register("homeassistant", "turn_on", accepted_without_feedback)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state", confirmation
    ):
        await c._execute_load_switching(
            [
                LoadAction(
                    load_id,
                    data,
                    True,
                    False,
                    soc_coordinate=stamp,
                    coordinate_captured=True,
                )
            ]
        )
        assert hass.states.is_state(PLUG, "off")
        assert not c._load_plug_owned.get(load_id, False)
        assert c._load_actor_requests[PLUG].state == "coordinate_changed"
        if manual_handoff:
            c.set_load_enabled(load_id, False)
        freezer.tick(timedelta(seconds=LOAD_CONFIRM_TIMEOUT_S + 5))
        hass.states.async_set(PLUG, "on")
        reconcile_feedback(
            c,
            SimpleNamespace(
                data={"entity_id": PLUG, "new_state": hass.states.get(PLUG)}
            ),
        )
        if c.live_ac.coordinate_cleanup_task is not None:
            await c.live_ac.coordinate_cleanup_task
    assert hass.states.is_state(PLUG, "on") is manual_handoff
    assert not c._load_charging_active.get(load_id, False)
    assert c._last_load_switch.get(load_id) == before
    assert not c._load_plug_owned.get(load_id, False)


@pytest.mark.parametrize("feedback_before_stop", [False, True])
async def test_grid_guard_stops_independent_load_while_start_waits(
    hass, feedback_before_stop
):
    other = "switch.independent"
    calls = []
    c, load_id, data = await _setup(
        hass,
        calls,
        charge_enable=None,
        additional_load_data=[
            {CONF_LOAD_CONTROL_SWITCH: other, CONF_LOAD_POWER_W: 500}
        ],
    )
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(other, "on")
    confirming, resume = asyncio.Event(), asyncio.Event()

    async def accepted(call):
        calls.append(("turn_on", call.data["entity_id"]))

    async def confirm(_hass, entity, desired):
        if entity == PLUG and desired:
            confirming.set()
            await resume.wait()
            return True  # A superseded confirmation coroutine returns success.
        return _hass.states.is_state(entity, "on" if desired else "off")

    hass.services.async_register("homeassistant", "turn_on", accepted)
    calls.clear()
    with patch("custom_components.battery_manager.coordinator.confirm_state", confirm):
        start = asyncio.create_task(
            c._execute_load_switching(
                [LoadAction(load_id, data, True, False, run_h=0.5)]
            )
        )
        c._load_switch_task = start
        try:
            await confirming.wait()
            if feedback_before_stop:
                hass.states.async_set(PLUG, "on")
            hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
            tasks = await stop_unsafe_loads(c)
            if tasks:
                await asyncio.gather(*tasks)
            assert not start.done()
            assert c.data["load_grid_guard"]["active"]
            assert c.data["load_grid_guard"]["grid_import_w"] == 2000.0
            assert ("turn_off", other) in calls
            assert hass.states.is_state(other, "off")

            hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
            if not feedback_before_stop:
                hass.states.async_set(PLUG, "on")
                await asyncio.gather(*await stop_unsafe_loads(c))
            assert hass.states.is_state(PLUG, "off")
        finally:
            resume.set()
            await start
    assert calls.count(("turn_off", PLUG)) == 1
    assert not c._load_charging_active.get(load_id, False)
    assert not c._load_plug_owned.get(load_id, False)
    assert load_id not in c._load_run_deadline


@pytest.mark.parametrize(
    "replacement", ["backoff", "failed", "accepted", "early_own", "early_old"]
)
async def test_revoked_start_requires_dispatched_replacement(
    hass, freezer, replacement
):
    calls = []
    c, load_id, data = await _setup(hass, calls, charge_enable=None, min_runtime_min=1)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    calls.clear()
    from homeassistant.core import Context

    origin = latest = None

    async def accept_on_without_feedback(call):
        nonlocal origin, latest
        latest = call.context
        calls.append((call.service, call.data["entity_id"]))
        if origin is None:
            origin = call.context
        elif replacement == "failed":
            raise HomeAssistantError("replacement could not dispatch")
        elif replacement in ("early_own", "early_old"):
            hass.states.async_set(
                PLUG,
                "on",
                context=call.context if replacement == "early_own" else origin,
            )
            await asyncio.gather(*await stop_unsafe_loads(c))

    async def read_physical_state(_hass, entity, desired):
        return _hass.states.is_state(entity, "on" if desired else "off")

    hass.services.async_register("homeassistant", "turn_on", accept_on_without_feedback)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        read_physical_state,
    ):
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=0.5)]
        )
        hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
        await asyncio.gather(*await stop_unsafe_loads(c))
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        freezer.tick(timedelta(seconds=35 if replacement == "backoff" else 65))
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=1.0)]
        )
        assert calls.count(("turn_on", PLUG)) == (1 if replacement == "backoff" else 2)
        if replacement not in ("early_own", "early_old"):
            context = (
                Context(parent_id=latest.id) if replacement == "accepted" else None
            )
            hass.states.async_set(PLUG, "on", context=context)
        await asyncio.gather(*await stop_unsafe_loads(c))
        if c.live_ac.coordinate_cleanup_task is not None:
            await c.live_ac.coordinate_cleanup_task

    if replacement in ("accepted", "early_own"):
        assert hass.states.is_state(PLUG, "on")
        assert c._load_plug_owned[load_id]
        assert c._load_run_deadline[load_id] == (
            hass.states.get(PLUG).last_changed + timedelta(hours=1)
        )
    else:
        assert ("turn_off", PLUG) in calls
        assert hass.states.is_state(PLUG, "off")
        assert not c._load_plug_owned.get(load_id, False)
        assert load_id not in c._load_run_deadline


async def _late_start_rig(hass, *, other=None):
    calls = []
    additional = (
        [{CONF_LOAD_CONTROL_SWITCH: other, CONF_LOAD_POWER_W: 500}] if other else []
    )
    c, load_id, data = await _setup(
        hass, calls, charge_enable=None, additional_load_data=additional
    )
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = c._stale_shed_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    origins = []

    async def acknowledge(call):
        origins.append(call.context)

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    calls.clear()
    return c, load_id, data, calls, origins


async def _reported_switch_state(hass, entity, desired):
    return hass.states.is_state(entity, "on" if desired else "off")


async def _stop_and_settle(c):
    await asyncio.gather(*await stop_unsafe_loads(c))
    if c.live_ac.coordinate_cleanup_task is not None:
        await c.live_ac.coordinate_cleanup_task


async def test_grid_stop_order_follows_dispatch_after_older_start_confirms(
    hass, freezer
):
    from homeassistant.core import Context

    other = "switch.independent"
    c, load_id, data, calls, origins = await _late_start_rig(hass, other=other)
    other_id = next(key for key in c.entry.subentries if key != load_id)
    hass.states.async_set(other, "off")
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        _reported_switch_state,
    ):
        await c._execute_load_switching([LoadAction(load_id, data, True, False)])
        hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
        early = dt_util.utcnow().isoformat()
        await _stop_and_settle(c)

        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(GRID, "500", {"unit_of_measurement": "W"})
        hass.states.async_set(other, "on")
        await _stop_and_settle(c)
        interim = c.data["load_grid_guard"]["last_action"]
        assert interim["load_ids"] == [other_id]
        assert interim["measured_w"] == 500

        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        hass.states.async_set(PLUG, "on", context=Context(parent_id=origins[0].id))
        await _stop_and_settle(c)

    assert calls == [("turn_off", other), ("turn_off", PLUG)]
    last = _find_forecast_state(hass).attributes["load_grid_guard"]["last_action"]
    assert last["load_ids"] == [load_id]
    assert last["measured_w"] == 2000
    assert last["decided_at"] == early
    assert last["requested_at"] == dt_util.utcnow().isoformat()
    assert last["requested_at"] > interim["requested_at"]


@pytest.mark.parametrize(
    ("second_cause", "reply"),
    [
        ("grid", "first"),
        ("grid", "second"),
        ("soc", "second"),
        ("grid", "neutral"),
        ("grid", "two_links"),
        ("grid", "two_links_then_unique"),
        ("grid", "restored"),
        ("grid", "restored_state"),
        ("grid", "assumed_state"),
    ],
)
async def test_grid_stop_uses_exact_revoked_on_context(
    hass, freezer, second_cause, reply
):
    from homeassistant.core import Context

    from custom_components.battery_manager.load_actuation import (
        export_coordinate_cleanup,
        restore_coordinate_cleanup,
    )

    c, load_id, data, calls, origins = await _late_start_rig(hass)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        _reported_switch_state,
    ):
        await c._execute_load_switching([LoadAction(load_id, data, True, False)])
        hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
        await _stop_and_settle(c)
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        freezer.tick(timedelta(seconds=65))

        await c._execute_load_switching([LoadAction(load_id, data, True, False)])
        assert len(origins) == 2
        if second_cause == "grid":
            hass.states.async_set(GRID, "500", {"unit_of_measurement": "W"})
            await _stop_and_settle(c)
            hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        else:
            with patch.object(c.voltage_gate, "coordinate_matches", return_value=False):
                reconcile_feedback(c)

        if reply == "restored":
            restore_coordinate_cleanup(c, export_coordinate_cleanup(c))
        parent = origins[0 if reply in ("first", "restored") else 1].id
        if reply == "neutral":
            context = Context()
        elif reply in ("two_links", "two_links_then_unique"):
            context = Context(id=origins[0].id, parent_id=origins[1].id)
        else:
            context = Context(parent_id=parent)
        # A reused, matching context does not turn a restored/estimated
        # switch state into evidence of a physical ON edge.
        attrs = {
            "restored_state": {"restored": True},
            "assumed_state": {"assumed_state": True},
        }.get(reply)
        hass.states.async_set(PLUG, "on", attrs, context=context)
        await _stop_and_settle(c)

    assert calls == [("turn_off", PLUG)]
    assert hass.states.is_state(PLUG, "off")
    last = _find_forecast_state(hass).attributes["load_grid_guard"].get("last_action")
    if reply == "first":
        assert last["measured_w"] == 2000
    elif reply == "second" and second_cause == "grid":
        assert last["measured_w"] == 500
    else:
        assert last is None
    if reply in ("first", "neutral", "two_links", "two_links_then_unique"):
        # A second exact reply still carries its own measurement. Replaying
        # the double-linked reply after one origin retired stays ambiguous.
        freezer.tick(timedelta(seconds=1))
        second_context = (
            context if reply == "two_links" else Context(parent_id=origins[1].id)
        )
        hass.states.async_set(PLUG, "on", context=second_context)
        with patch(
            "custom_components.battery_manager.coordinator.confirm_state",
            _reported_switch_state,
        ):
            await _stop_and_settle(c)
        assert calls == [("turn_off", PLUG), ("turn_off", PLUG)]
        assert hass.states.is_state(PLUG, "off")
        last = c.data["load_grid_guard"].get("last_action")
        if reply == "two_links":
            assert last is None
        else:
            assert last["measured_w"] == 500


@pytest.mark.parametrize("attempts", [2, 3])
@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("reload", [False, True])
async def test_reply_to_first_own_start_uses_current_booking_or_protection(
    hass, freezer, attempts, revoked, reload
):
    from homeassistant.core import Context

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    calls = []
    c, load_id, data = await _setup(hass, calls, charge_enable=None, min_runtime_min=1)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    origins = []

    async def acknowledge(call):
        origins.append(call.context)

    async def read_physical_state(_hass, entity, desired):
        return _hass.states.is_state(entity, "on" if desired else "off")

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    current = c
    try:
        with patch(
            "custom_components.battery_manager.coordinator.confirm_state",
            read_physical_state,
        ):
            for attempt in range(attempts):
                await c._execute_load_switching(
                    [LoadAction(load_id, data, True, False, run_h=0.5 * (attempt + 1))]
                )
                if attempt < attempts - 1 or revoked:
                    hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
                    await asyncio.gather(*await stop_unsafe_loads(c))
                    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
                    freezer.tick(timedelta(seconds=65))
            assert len(origins) == attempts
            if reload:
                current = BatteryManagerCoordinator(hass, c.entry)
                with patch.object(
                    current._store, "async_load", return_value=c._persistent_payload()
                ):
                    await current.async_load_persistent_state()

            hass.states.async_set(PLUG, "on", context=Context(parent_id=origins[0].id))
            await asyncio.gather(*await stop_unsafe_loads(current))
            if current.live_ac.coordinate_cleanup_task is not None:
                await current.live_ac.coordinate_cleanup_task
            assert hass.states.is_state(PLUG, "off")
            assert load_id not in current._load_run_deadline
            # The protection OFF for A supersedes the later pending command.
            # Its sent origin remains actionable if the device replies anyway.
            freezer.tick(timedelta(seconds=1))
            hass.states.async_set(PLUG, "on", context=Context(parent_id=origins[-1].id))
            await asyncio.gather(*await stop_unsafe_loads(current))
            if current.live_ac.coordinate_cleanup_task is not None:
                await current.live_ac.coordinate_cleanup_task
            assert hass.states.is_state(PLUG, "off")
            assert load_id not in current._load_run_deadline
            # A fresh, linked confirmation still books while another old
            # origin (in the three-attempt case) is unresolved.
            freezer.tick(timedelta(seconds=65))
            await current._execute_load_switching(
                [LoadAction(load_id, data, True, False, run_h=2.0)]
            )
            hass.states.async_set(PLUG, "on", context=Context(parent_id=origins[-1].id))
            await asyncio.gather(*await stop_unsafe_loads(current))
            assert hass.states.is_state(PLUG, "on")
            assert current._load_run_deadline[load_id] == (
                hass.states.get(PLUG).last_changed + timedelta(hours=2)
            )
        assert calls.count(("turn_off", PLUG)) == 2
    finally:
        if current is not c:
            await current.async_cancel_actuation_tasks()
            current.cleanup()


async def test_revoked_cleanup_rechecks_input_after_waiting_for_gate_off(hass, freezer):
    from homeassistant.core import Context

    calls = []
    c, load_id, data = await rig(hass, calls)
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    origin = None
    wait_for_off = False
    gate_off_requested = asyncio.Event()
    continue_gate_off = asyncio.Event()

    async def turn_on(call):
        nonlocal origin
        entity = call.data["entity_id"]
        calls.append((call.service, entity))
        if entity == PLUG:
            origin = call.context
        else:
            hass.states.async_set(entity, "on", context=call.context)

    async def turn_off(call):
        entity = call.data["entity_id"]
        calls.append((call.service, entity))
        if entity == ENABLE and wait_for_off:
            gate_off_requested.set()
            await continue_gate_off.wait()
        hass.states.async_set(entity, "off", context=call.context)

    async def read_physical_state(_hass, entity, desired):
        return _hass.states.is_state(entity, "on" if desired else "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        read_physical_state,
    ):
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=0.5)]
        )
        hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
        await asyncio.gather(*await stop_unsafe_loads(c))
        hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
        hass.states.async_set(ENABLE, "on")
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(PLUG, "on", context=Context(parent_id=origin.id))
        wait_for_off = True
        tasks = await stop_unsafe_loads(c)
        try:
            await gate_off_requested.wait()
            hass.states.async_set(PLUG, "off")
            freezer.tick(timedelta(seconds=1))
            hass.states.async_set(PLUG, "on", context=Context(user_id="operator"))
        finally:
            continue_gate_off.set()
            await asyncio.gather(*tasks)
        await asyncio.gather(*await stop_unsafe_loads(c))

    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "on")
    assert ("turn_off", PLUG) not in calls
    assert not c._load_plug_owned.get(load_id, False)
    assert load_id not in c._load_run_deadline


@pytest.mark.parametrize("linkage", ["direct", "parent"])
@pytest.mark.parametrize("attempts", [2, 3])
@pytest.mark.parametrize(
    "boundary",
    [
        "confirmed",
        "neutral_on",
        "neutral_off",
        "legacy_observed",
        "disabled",
        "rebound",
        "foreign",
    ],
)
async def test_reload_preserves_confirmed_or_unresolved_on_origins(
    hass, freezer, linkage, attempts, boundary
):
    import json

    from homeassistant.core import Context

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from custom_components.battery_manager.load_actuation import reconcile_feedback

    calls = []
    c, load_id, data = await _setup(hass, calls, charge_enable=None, min_runtime_min=1)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = GRID
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    origins = []

    async def acknowledge(call):
        origins.append(call.context)

    def reported_origin(index):
        return (
            origins[index]
            if linkage == "direct"
            else Context(parent_id=origins[index].id)
        )

    async def read_physical_state(_hass, entity, desired):
        return _hass.states.is_state(entity, "on" if desired else "off")

    async def finish_safety(current):
        await asyncio.gather(*await stop_unsafe_loads(current))
        if current.live_ac.coordinate_cleanup_task is not None:
            await current.live_ac.coordinate_cleanup_task

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        read_physical_state,
    ):
        for attempt in range(attempts):
            run_h = 1.0 if attempt == attempts - 1 else 0.5
            await c._execute_load_switching(
                [LoadAction(load_id, data, True, False, run_h=run_h)]
            )
            if run_h == 0.5:
                hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})
                await finish_safety(c)
                hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
                freezer.tick(timedelta(seconds=65))
        assert len(origins) == attempts
        if boundary in ("neutral_on", "neutral_off", "legacy_observed"):
            hass.states.async_set(PLUG, "on")
            if boundary == "neutral_off":
                await finish_safety(c)
                assert hass.states.is_state(PLUG, "off")
            else:
                with patch(
                    "custom_components.battery_manager.load_actuation._schedule_coordinate_cleanup"
                ):
                    reconcile_feedback(c)
        else:
            hass.states.async_set(PLUG, "on", context=reported_origin(attempts - 1))
            await finish_safety(c)
            assert c._load_plug_owned[load_id]
            deadline = hass.states.get(PLUG).last_changed + timedelta(hours=1)
            assert c._load_run_deadline[load_id] == deadline
        if boundary == "disabled":
            c.set_load_enabled(load_id, False)
        if boundary == "rebound":
            sub = c.entry.subentries[load_id]
            hass.config_entries.async_update_subentry(
                c.entry, sub, data={**data, CONF_LOAD_CONTROL_SWITCH: "switch.rebound"}
            )
        if boundary == "foreign":
            hass.states.async_set(PLUG, "off")
            freezer.tick(timedelta(seconds=1))
            hass.states.async_set(PLUG, "on", context=Context(user_id="operator"))
        payload = json.loads(json.dumps(c._persistent_payload()))
        if boundary == "legacy_observed":
            marker = payload["revoked_load_starts"]["items"][load_id]
            marker.pop("exact_observations")
            marker["observed"] = [[PLUG, origins[0].id]]
        current = BatteryManagerCoordinator(hass, c.entry)
        try:
            with patch.object(current._store, "async_load", return_value=payload):
                await current.async_load_persistent_state()
            if boundary in ("neutral_on", "neutral_off", "legacy_observed"):
                await finish_safety(current)
                assert hass.states.is_state(PLUG, "off")
                for attempt in range(attempts - 1):
                    freezer.tick(timedelta(seconds=1))
                    hass.states.async_set(PLUG, "on", context=reported_origin(attempt))
                    await finish_safety(current)
                    assert hass.states.is_state(PLUG, "off")
                assert calls.count(("turn_off", PLUG)) == attempts
                assert load_id not in current._load_run_deadline
            else:
                await finish_safety(current)
                assert hass.states.is_state(PLUG, "on")
                assert ("turn_off", PLUG) not in calls
                if boundary == "confirmed":
                    assert current._load_plug_owned[load_id]
                    assert current._load_run_deadline[load_id] == deadline
                    assert load_id in current._load_off_timer
                else:
                    assert not current._load_plug_owned.get(load_id, False)
                    assert load_id not in current._load_run_deadline
                    assert load_id not in current._load_off_timer
        finally:
            await current.async_cancel_actuation_tasks()
            current.cleanup()
