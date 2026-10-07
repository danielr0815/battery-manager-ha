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
