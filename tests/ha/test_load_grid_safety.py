"""October incident: 2 kW import while a logically stopped Fossibot charges."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from test_load_switching import (
    ENABLE,
    PLUG,
    _active_plan,
    _detach_listener,
    _register_switch_services,
    _setup,
)

from custom_components.battery_manager.load_actuation import LoadAction
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
    await stop_unsafe_loads(c)
    await drain(c)
    assert calls == []  # bounded retry, no real-time waits
    freezer.tick(timedelta(seconds=61))
    # No usable grid sample or fresh planner is needed to finish our old stop.
    c._last_planner_recording = None
    await stop_unsafe_loads(c)
    await drain(c)
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
async def test_import_during_confirmation_revokes_start(hass, after):
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
            hass.states.async_set(GRID, "2000", {"unit_of_measurement": "W"})

    hass.services.async_register("homeassistant", "turn_on", activate)
    await c._execute_load_switching([LoadAction(load_id, data, True, False)])
    assert hass.states.is_state(PLUG, "off")
    assert hass.states.is_state(ENABLE, "off")
    assert (
        ("turn_on", PLUG) in calls
        if after == "input"
        else ("turn_on", PLUG) not in calls
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
    assert c.data["load_grid_guard"] == {"active": True, "grid_import_w": 2000}


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
    await stop_unsafe_loads(c)
    await drain(c)
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
