"""Critical review regressions: covered settling and legacy source authority."""

from datetime import timedelta
from itertools import permutations
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import DCDC, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_soc_coordinate_runtime import transition
from test_voltage_gate_runtime import fresh
from test_voltage_gate_runtime import gate as gate


@pytest.mark.parametrize("order", list(permutations(("soc", "power", "voltage"))))
async def test_runtime_settling_counts_600_seconds_once_in_every_packet_order(
    gate, hass, freezer, order
):
    c, calls, *_ = gate
    c._schedule_replan = Mock()
    start = dt_util.utcnow()
    freezer.move_to(start - timedelta(seconds=5))
    fresh(hass, soc=55, power=0)
    c.voltage_gate.observe(dt_util.now())
    await hass.async_block_till_done()
    initial_epoch = c.voltage_gate.monitor.epoch
    channels = {
        "soc": ("sensor.soc", 60, "%"),
        "power": ("sensor.battery_w", 0, "W"),
        "voltage": ("sensor.voltage", 50, "V"),
    }
    for tick in range(0, 661, 5):
        freezer.move_to(start + timedelta(seconds=tick))
        for channel in order:
            entity, value, unit = channels[channel]
            hass.states.async_set(entity, value, {"unit_of_measurement": unit})
            await hass.async_block_till_done()
            if 0 < tick < 660:
                assert c.voltage_gate.monitor.phase != "tracking"
                assert c.voltage_gate.monitor.diagnostic()[
                    "settled_covered_seconds"
                ] <= max(0, tick - 60)
        if tick == 655:
            assert c.voltage_gate.monitor.diagnostic()["settled_covered_seconds"] == 595
        if tick == 0:
            assert c.voltage_gate.monitor.phase == "reconciling"
    assert c.voltage_gate.monitor.phase == "tracking"
    assert c.voltage_gate.monitor.diagnostic()["settled_covered_seconds"] == 600
    assert c.voltage_gate.monitor.epoch == initial_epoch + 1
    assert len(c.voltage_gate.monitor.events) == 1
    assert (
        c._schedule_replan.call_count == 2
    )  # Episode and genuinely completed settling.
    assert not calls


def legacy(c):
    c.raw_config["inverter_discharge_limit_entity"] = None
    c._schedule_replan = Mock()
    c.voltage_gate.published_coordinate = c.voltage_gate.coordinate_snapshot(
        dt_util.now()
    )


def hold(c):
    transition(c, "settling", new_epoch=True)


@pytest.mark.parametrize("key,entity", [("dc24", PSU24), ("dc48", PSU48)])
async def test_legacy_queued_source_removal_cannot_rebind_to_a_successor_coordinate(
    gate, hass, key, entity
):
    c, calls, *_ = gate
    legacy(c)
    c._support_state[key] = True
    hass.states.async_set(entity, "on")
    if key == "dc24":
        hass.states.async_set(DCDC, "off")
    desired = dict(c._support_state)
    desired[key] = False
    queued = c._execute_support_switching(desired)
    hold(c)
    transition(c, "tracking")
    c.voltage_gate.published_coordinate = c.voltage_gate.coordinate_snapshot(
        dt_util.now()
    )
    await queued
    assert not calls
    assert hass.states.is_state(entity, "on")


@pytest.mark.parametrize("key,entity", [("dc24", PSU24), ("dc48", PSU48)])
async def test_legacy_revoked_source_await_preserves_the_previously_confirmed_supply(
    gate, hass, key, entity
):
    c, calls, *_ = gate
    legacy(c)
    c._support_state[key] = True
    hass.states.async_set(entity, "on")
    if key == "dc24":
        hass.states.async_set(DCDC, "off")
    original = c._switch_entity.side_effect
    revoked = False

    async def changing(actor, on, **kwargs):
        nonlocal revoked
        result = await original(actor, on, **kwargs)
        if not revoked and (actor == entity if key == "dc48" else actor == DCDC):
            revoked = True
            hold(c)
        return result

    c._switch_entity.side_effect = changing
    desired = dict(c._support_state)
    desired[key] = False
    await c._execute_support_switching(desired)
    assert hass.states.is_state(entity, "on")
    assert c._support_state[key]
    if key == "dc24":
        assert hass.states.is_state(DCDC, "off")
        assert (PSU24, False) not in calls
    else:
        assert calls == [(PSU48, False), (PSU48, True)]
    assert c._support_fail_count == 0  # A coordinate hold is not an actuator fault.


@pytest.mark.parametrize("key,entity", [("dc24", PSU24), ("dc48", PSU48)])
async def test_legacy_coordinate_hold_preserves_manual_source(gate, hass, key, entity):
    c, calls, *_ = gate
    legacy(c)
    c._support_state[key] = c._support_manual[key] = True
    hass.states.async_set(entity, "on")
    hold(c)
    desired = dict(c._support_state)
    desired[key] = False
    await c._execute_support_switching(desired)
    assert not calls and hass.states.is_state(entity, "on")


@pytest.mark.parametrize("key,entity", [("dc24", PSU24), ("dc48", PSU48)])
async def test_legacy_coordinate_hold_keeps_low_soc_protective_start(
    gate, hass, key, entity
):
    c, calls, *_ = gate
    legacy(c)
    hold(c)
    fresh(hass, soc=5)
    desired = {"dc24": False, "dc48": False}
    desired[key] = True
    await c._execute_support_switching(desired)
    assert (entity, True) in calls and hass.states.is_state(entity, "on")


async def test_legacy_coordinate_hold_can_restore_a_dead_24v_rail(gate, hass):
    c, calls, *_ = gate
    legacy(c)
    hold(c)
    hass.states.async_set(DCDC, "off")
    await c._execute_support_switching({"dc24": True, "dc48": False})
    assert (PSU24, True) in calls
    assert hass.states.is_state(PSU24, "on")


async def test_legacy_real_plan_hold_is_not_recreated_by_hysteresis_or_floor_guard(
    gate, hass
):
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.load_safety import start_blocked

    c, calls, *_ = gate
    legacy(c)
    hold(c)
    fresh(hass, soc=80)
    for key, entity in (
        ("pv_forecast_today_entity", "sensor.hold_today"),
        ("pv_forecast_tomorrow_entity", "sensor.hold_tomorrow"),
        ("pv_forecast_day_after_entity", "sensor.hold_later"),
    ):
        c.raw_config[key] = entity
        hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})
    accepted = []

    async def calculate(_phase, config, inputs):
        result = plan(config, inputs)
        assert not result.inverter_on
        assert inputs.soc_coordinate.holds_optional_discharge
        assert result.threshold_percent < inputs.start_soc_percent
        accepted.append((config, inputs, result))
        return result

    class Observed(Exception):
        pass

    async def first_await(*_args):
        config, inputs, result = accepted[-1]
        assert not c._inverter_recommendation
        assert not c._update_floor_guard(80, config, 0, 300, dt_util.now())
        # Independent measured import and the physical SOC floor still protect.
        c.raw_config["operation_import_power_entity"] = "sensor.real_import"
        hass.states.async_set("sensor.real_import", "100", {"unit_of_measurement": "W"})
        assert start_blocked(c)
        assert c._update_floor_guard(5, config, 0, 300, dt_util.now())
        assert c._last_planner_recording == (config, inputs, result)
        raise Observed

    c._async_plan = AsyncMock(side_effect=calculate)
    c._latch_shadow_active = AsyncMock(side_effect=first_await)
    with pytest.raises(Observed):
        await c._async_update_data_serial()
    assert not calls


@pytest.mark.parametrize(
    "interruption",
    [
        "none",
        "unconfirmed",
        "manual_on_ack",
        "binding_on_ack",
        "manual_overlap",
        "binding_overlap",
        "mode_overlap",
        "lost_psu_overlap",
    ],
)
async def test_legacy_revoked_psu_off_ack_requires_new_confirmed_overlap(
    gate, hass, freezer, interruption
):
    """The first transfer's overlap cannot fund the reverse restoration."""
    c, calls, *_ = gate
    legacy(c)
    c.raw_config["support_switch_delay_s"] = 7
    c._support_state["dc24"] = True
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    original = c._switch_entity.side_effect
    events = []
    start = dt_util.utcnow()
    restored = False

    async def switching(entity, on, **kwargs):
        nonlocal restored
        events.append((entity, on, (dt_util.utcnow() - start).total_seconds()))
        if entity == PSU24 and on:
            restored = True
            if interruption == "unconfirmed":
                return True
        result = await original(entity, on, **kwargs)
        if entity == PSU24 and not on:
            hold(c)  # Revocation arrives only inside the destructive OFF ACK.
        if restored and entity == PSU24 and on:
            if interruption == "manual_on_ack":
                c._support_manual["dc24"] = True
            if interruption == "binding_on_ack":
                c.raw_config["support_dc24_switch_entity"] = "switch.reassigned_psu"
        return result

    async def overlap(seconds):
        events.append(("overlap", seconds, (dt_util.utcnow() - start).total_seconds()))
        freezer.tick(seconds)
        if restored:
            if interruption == "manual_overlap":
                c._support_manual["dc24"] = True
            if interruption == "binding_overlap":
                c.raw_config["dcdc_switch_entity"] = "switch.reassigned_dcdc"
            if interruption == "mode_overlap":
                c.raw_config["inverter_discharge_limit_entity"] = "number.new_control"
            if interruption == "lost_psu_overlap":
                hass.states.async_set(PSU24, "off")

    c._switch_entity.side_effect = switching
    # Nest within the rig's sleep patch; monkeypatch's later fixture teardown
    # would otherwise restore the rig AsyncMock after its context had ended.
    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep", new=overlap
    ):
        await c._execute_support_switching({"dc24": False, "dc48": False})
    assert events[:4] == [
        (DCDC, True, 0),
        ("overlap", 7, 0),
        (PSU24, False, 7),
        (PSU24, True, 7),
    ]
    if interruption == "none":
        assert events[4:] == [("overlap", 7, 7), (DCDC, False, 14)]
        assert hass.states.is_state(PSU24, "on")
        assert hass.states.is_state(DCDC, "off")
    else:
        assert (DCDC, False) not in calls
        assert hass.states.is_state(DCDC, "on")
    assert c._support_fail_count == 0


@pytest.mark.parametrize("generation", [3, True, -1, "3", None])
async def test_restart_restores_only_explicit_valid_generation_without_legacy_cleanup(
    hass, monkeypatch, generation
):
    from copy import deepcopy

    from test_load_switching import PLUG, _detach_listener, _setup

    from custom_components.battery_manager.load_actuation import ActorRequest

    calls = []
    c, load_id, _ = await _setup(hass, calls)
    _detach_listener(c)
    c._load_bm_enabled[load_id] = True
    c._load_control_generation[load_id] = 3
    c._load_actor_requests[PLUG] = ActorRequest(
        True, dt_util.now(), "coordinate_changed", "load:" + load_id
    )
    payload = deepcopy(c._persistent_payload())
    assert payload["load_control_generation"][load_id] == 3
    assert payload["load_actor_requests"][PLUG]["sent"] is False
    payload["load_control_generation"] = {load_id: generation, "orphan": 8}
    payload.pop("revoked_load_starts")  # Older versions have no proven ledger.
    c._load_control_generation.clear()
    c._load_actor_requests.clear()
    monkeypatch.setattr(c._store, "async_load", AsyncMock(return_value=payload))
    await c.async_load_persistent_state()
    assert c._load_control_generation == (
        {load_id: 3} if type(generation) is int and generation >= 0 else {}
    )
    assert not c._load_actor_requests[PLUG].sent
    assert not c.live_ac.revoked_load_starts
    assert not c._load_charging_active.get(load_id, False)
    await c.async_cancel_actuation_tasks()
