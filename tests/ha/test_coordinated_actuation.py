"""F-COORDINATED-DC-SUPPORT R5: real state confirmation and single ownership."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.battery_manager.actor_ownership import SUPPLY_OWNER
from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_INVERTER_BLOCK_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_RESERVE_GRID_ENTITY,
    CONF_RESERVE_MODE,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    DOMAIN,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.live_ac import LiveACEnvelope
from custom_components.battery_manager.load_actuation import ActorRequest

LIMIT = "number.discharge_limit"
BLOCK = "switch.block_ac"
PSU24 = "switch.psu24"
PSU48 = "switch.psu48"
DCDC = "switch.dcdc"


@pytest.fixture
async def rig(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            # This module verifies the original source-transfer policy.
            CONF_RESERVE_MODE: "off",
            CONF_INVERTER_BLOCK_SWITCH: BLOCK,
            CONF_INVERTER_LIMIT_ENTITY: LIMIT,
            CONF_SUPPORT_DC24_SWITCH: PSU24,
            CONF_SUPPORT_DC48_SWITCH: PSU48,
            CONF_DCDC_SWITCH: DCDC,
        },
    )
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c._save_persistent_state = Mock()
    c.data = {"valid": True}
    calls = []
    dead = set()
    failures = set()

    async def switch(entity, on, **kwargs):
        calls.append((entity, on))
        if entity in failures:
            return False
        if entity not in dead:
            hass.states.async_set(entity, "on" if on else "off")
        return True

    async def set_limit(call):
        target = call.data["value"]
        calls.append((LIMIT, target))
        if LIMIT in failures:
            raise HomeAssistantError("number unreachable")
        if LIMIT not in dead:
            hass.states.async_set(LIMIT, str(target))
            if BLOCK not in dead:
                hass.states.async_set(BLOCK, "on" if target == 0 else "off")

    hass.services.async_register("number", "set_value", set_limit)
    hass.states.async_set(LIMIT, "2300")
    c._switch_entity = AsyncMock(side_effect=switch)
    for entity, on in ((BLOCK, False), (PSU24, False), (PSU48, False), (DCDC, True)):
        hass.states.async_set(entity, "on" if on else "off")
    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep", new=AsyncMock()
    ):
        yield c, calls, dead, failures
    await c.async_cancel_actuation_tasks()


async def execute(c, dc24=False, dc48=False, inverter=False):
    await c._execute_coordinated_support(
        {"dc24": dc24, "dc48": dc48}, inverter, c.build_system_config(), dt_util.now()
    )


async def test_block_then_transfer_then_48_support(rig, hass):
    c, calls, _, _ = rig
    await execute(c, True, True)
    assert calls == [(LIMIT, 0), (PSU24, True), (DCDC, False), (PSU48, True)]
    assert c.data["inverter_recommendation"] is False
    assert c._support_state == {"dc24": True, "dc48": True}


@pytest.mark.parametrize("entity", [BLOCK, LIMIT, PSU24, DCDC, PSU48])
async def test_unconfirmed_actuator_prevents_next_transition(rig, hass, entity):
    c, calls, dead, _ = rig
    dead.add(entity)
    await execute(c, True, True)
    assert c.data["inverter_recommendation"] is False
    assert c._coordinated_support_diag["reason"] != "settled"
    if entity in (BLOCK, LIMIT):
        assert calls == [(LIMIT, 0)]
    elif entity == PSU24:
        assert (DCDC, False) not in calls and (PSU48, True) not in calls
    elif entity == DCDC:
        assert (PSU48, True) not in calls
    else:
        assert not c._support_state["dc48"]


async def test_return_removes_support_before_releasing_inverter(rig, hass):
    c, calls, _, _ = rig
    hass.states.async_set(LIMIT, "0")
    for entity in (BLOCK, PSU24, PSU48):
        hass.states.async_set(entity, "on")
    hass.states.async_set(DCDC, "off")
    await execute(c, inverter=True)
    assert calls == [(PSU48, False), (DCDC, True), (PSU24, False), (LIMIT, 2300)]
    assert c.data["inverter_recommendation"] is True


async def test_dark_24v_rail_is_restored_on_handback_even_without_ac_feedback(
    rig, hass
):
    c, calls, dead, _ = rig
    c.async_request_refresh = AsyncMock()
    await c.async_set_support_manual("dc24", True)
    hass.states.async_set(DCDC, "off")
    dead.add(BLOCK)
    await c.async_set_support_manual("dc24", False)
    await execute(c, dc24=True)

    assert calls == [(DCDC, True), (LIMIT, 0)]
    assert hass.states.is_state(DCDC, "on")
    assert hass.states.is_state(PSU24, "off")
    assert c._coordinated_support_diag["reason"] == "inverter_block_unconfirmed"


@pytest.mark.parametrize("entity", [PSU48, PSU24, DCDC, BLOCK, LIMIT])
async def test_failed_return_cannot_release_inverter(rig, hass, entity):
    c, calls, dead, _ = rig
    for item in (BLOCK, PSU24, PSU48):
        hass.states.async_set(item, "on")
    hass.states.async_set(DCDC, "off")
    dead.add(entity)
    await execute(c, inverter=True)
    assert c.data["inverter_recommendation"] is False
    assert c._coordinated_support_diag["reason"] != "settled"


async def test_service_error_is_reported(rig):
    c, calls, _, failures = rig
    failures.add(PSU48)
    await execute(c, True, True)
    assert c._coordinated_support_diag["reason"] == "command_failed"
    assert c._support_state["dc24"] is True
    c._save_persistent_state.assert_called()


async def test_minimum_interval_never_delays_inverter_protection(rig, hass):
    c, calls, _, _ = rig
    c._last_support_switch = dt_util.now()
    await execute(c, True, True)
    assert calls == [(LIMIT, 0)]
    assert c._coordinated_support_diag["reason"] == "minimum_switch_interval"
    c._last_support_switch -= timedelta(minutes=2)
    await execute(c, True, True)
    assert (PSU48, True) in calls


async def test_external_activation_is_corrected_not_adopted_as_manual(rig, hass):
    c, calls, _, _ = rig
    hass.states.async_set(PSU48, "on")
    c._update_support_modes()
    assert c._support_manual == {"dc24": False, "dc48": False}
    await execute(c, inverter=True)
    assert calls == [(LIMIT, 0), (PSU48, False), (LIMIT, 2300)]
    assert not c._dc48_controller_engaged()


@pytest.mark.parametrize("key,psu", [("dc24", PSU24), ("dc48", PSU48)])
async def test_manual_handover_and_return_leave_psu_to_current_owner(
    rig, hass, key, psu
):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    hass.states.async_set(psu, "on")
    if key == "dc24":
        hass.states.async_set(DCDC, "off")

    await c.async_set_support_manual(key, True)

    assert calls == []
    assert c.support_manual(key)
    assert hass.states.is_state(DCDC, "off" if key == "dc24" else "on")
    assert hass.states.is_state(psu, "on")
    config = c.build_system_config()
    assert not getattr(config.support, f"{key}_available")
    assert config.inverter.max_power_w == 2300
    c._save_persistent_state.assert_called_once()
    c.async_request_refresh.assert_awaited_once()

    calls.clear()
    await c.async_set_support_manual(key, False)

    assert not c.support_manual(key)
    assert calls == []
    assert hass.states.is_state(psu, "on")
    await execute(c, inverter=True)
    rail = [(DCDC, True)] if key == "dc24" else []
    assert calls == [(LIMIT, 0), *rail, (psu, False), (LIMIT, 2300)]
    assert hass.states.is_state(psu, "off")


@pytest.mark.parametrize("state", ["off", "on", "unavailable"])
async def test_manual_24_hands_over_converter_regardless_of_feedback(rig, hass, state):
    c, calls, *_ = rig
    c.async_request_refresh = AsyncMock()
    hass.states.async_set(DCDC, state)

    await c.async_set_support_manual("dc24", True)
    await execute(c, dc24=True, inverter=True)

    assert calls == []
    assert c.support_manual("dc24")
    assert hass.states.is_state(DCDC, state)
    assert c._inverter_recommendation


async def test_manual_48_handover_is_independent_of_inverter_feedback(rig, hass):
    c, calls, *_ = rig
    c.async_request_refresh = AsyncMock()
    hass.states.async_set(LIMIT, "unavailable")
    hass.states.async_set(BLOCK, "unavailable")

    await c.async_set_support_manual("dc48", True)

    assert calls == []
    assert c.support_manual("dc48")


async def test_coordinate_recovery_cannot_replay_pre_handover_psu_target(rig, hass):
    c, calls, *_ = rig
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_SOC_ENTITY: "sensor.test_soc",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
        }
    )
    hass.states.async_set("sensor.test_soc", "80")
    hass.states.async_set("binary_sensor.grid", "on")
    c.async_request_refresh = AsyncMock()
    c.live_ac.envelope = LiveACEnvelope(
        c.build_system_config(),
        dt_util.utcnow() + timedelta(minutes=1),
        30,
        True,
        dc_sources=(False, True),
    )
    await c.async_set_support_manual("dc48", True)
    await c.async_set_support_manual("dc48", False)
    c.voltage_gate.coordinate_permission_valid = Mock(return_value=False)

    await execute(c, dc48=True)

    assert (PSU48, True) not in calls
    assert hass.states.is_state(PSU48, "off")
    assert c._coordinated_support_diag["reason"] == "soc_coordinate_hold"


async def test_24_handover_persists_through_converter_state_change(rig, hass):
    c, calls, *_ = rig
    c.async_request_refresh = AsyncMock()
    save = c._support_handover_store.async_save

    async def change_converter_feedback(handovers):
        await save(handovers)
        if handovers:
            hass.states.async_set(DCDC, "unavailable")

    c._support_handover_store.async_save = AsyncMock(
        side_effect=change_converter_feedback
    )
    await c.async_set_support_manual("dc24", True)

    assert c.support_manual("dc24")
    assert await c._support_handover_store.async_load() == {
        "dc24": c.support_handover_binding("dc24")
    }
    assert hass.states.is_state(DCDC, "unavailable")
    assert calls == []


async def test_definitive_non_dispatch_can_be_retried(rig, hass):
    c, *_ = rig
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)

    async def rejected(call):
        raise ServiceNotFound(call.domain, call.service)

    async def delivered(call):
        hass.states.async_set(PSU48, "on")

    hass.services.async_register("homeassistant", "turn_on", rejected)
    assert not await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    hass.services.async_register("homeassistant", "turn_on", delivered)
    assert await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    assert hass.states.is_state(PSU48, "on")


@pytest.mark.parametrize(
    "entity,requested,state",
    [
        (PSU24, False, "pending"),
        (PSU48, False, "pending"),
        (PSU48, True, "pending"),
        (DCDC, False, "pending"),
        (DCDC, True, "pending"),
        (DCDC, False, "service_failed"),
        (DCDC, True, "service_failed"),
    ],
)
async def test_manual_handover_rejects_an_unsettled_actor_command(
    rig, hass, entity, requested, state
):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    hass.states.async_set(entity, "off" if requested else "on")
    c._supply_actor_requests[entity] = ActorRequest(requested, dt_util.now(), state)
    key = "dc48" if entity == PSU48 else "dc24"

    with pytest.raises(HomeAssistantError):
        await c.async_set_support_manual(key, True)

    assert not c.support_manual(key)
    assert calls == []
    c._save_persistent_state.assert_not_called()


@pytest.mark.parametrize(
    "key,entity,desired",
    [("dc48", PSU48, True), ("dc24", DCDC, False)],
)
async def test_handover_waits_for_single_real_automatic_actor_acknowledgement(
    rig, hass, key, entity, desired
):
    c, _, _, _ = rig
    c.async_request_refresh = AsyncMock()
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)
    services = {
        True: async_mock_service(hass, "homeassistant", "turn_on"),
        False: async_mock_service(hass, "homeassistant", "turn_off"),
    }

    assert await c._switch_entity(entity, desired, actor_owner=SUPPLY_OWNER)
    await c._switch_entity(entity, desired, actor_owner=SUPPLY_OWNER)
    assert not await c._switch_entity(entity, not desired, actor_owner=SUPPLY_OWNER)
    assert [call.data["entity_id"] for call in services[desired]] == [entity]
    assert services[not desired] == []

    with pytest.raises(HomeAssistantError, match="previous automatic command"):
        await c.async_set_support_manual(key, True)
    assert not c.support_manual(key)

    target_state = "on" if desired else "off"
    hass.states.async_set(entity, target_state, {"assumed_state": True})
    with pytest.raises(HomeAssistantError, match="previous automatic command"):
        await c.async_set_support_manual(key, True)
    assert not c.support_manual(key)

    hass.states.async_set(entity, target_state)
    await c.async_set_support_manual(key, True)

    assert c.support_manual(key)
    assert hass.states.is_state(entity, target_state)
    assert not await c._switch_entity(entity, not desired, actor_owner=SUPPLY_OWNER)
    await c.async_set_support_manual(key, False)
    assert await c._switch_entity(entity, desired, actor_owner=SUPPLY_OWNER)
    assert [call.data["entity_id"] for call in services[desired]] == [entity]
    assert services[not desired] == []


@pytest.mark.parametrize("key,psu", [("dc24", PSU24), ("dc48", PSU48)])
@pytest.mark.parametrize("state", ["off", "on", "unavailable"])
async def test_queued_auto_intent_cannot_touch_a_handed_over_psu(
    rig, hass, key, psu, state
):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    # The queued automatic intent would write the opposite of the current state.
    desired = {"dc24": False, "dc48": False}
    desired[key] = state == "off"
    queued = c._execute_coordinated_support(
        desired, True, c.build_system_config(), dt_util.now()
    )
    await c.async_set_support_manual(key, True)
    hass.states.async_set(psu, state)
    hass.states.async_set(LIMIT, "2300")
    hass.states.async_set(BLOCK, "off")
    if key == "dc24":
        hass.states.async_set(DCDC, "off")
    calls.clear()

    await queued

    assert all(actor != psu for actor, _ in calls)
    assert hass.states.is_state(psu, state)
    assert hass.states.is_state(DCDC, "off" if key == "dc24" else "on")
    assert c.support_manual(key)
    assert (
        c.support_active(key) is {"on": True, "off": False, "unavailable": None}[state]
    )


@pytest.mark.parametrize("key,psu", [("dc24", PSU24), ("dc48", PSU48)])
@pytest.mark.parametrize("state", ["off", "on", "unavailable"])
async def test_auto_inverter_permission_is_independent_of_manual_psu_state(
    rig, hass, key, psu, state
):
    c, calls, *_ = rig
    c.async_request_refresh = AsyncMock()
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
            CONF_SOC_ENTITY: "sensor.test_soc",
        }
    )
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.test_soc", "80")
    await execute(c, inverter=True)
    assert c._inverter_recommendation
    calls.clear()

    await c.async_set_support_manual(key, True)

    assert calls == []
    assert c._inverter_recommendation
    hass.states.async_set(psu, state)
    await execute(c, inverter=True)

    assert calls == []
    assert c._inverter_limit_confirmed(False)
    assert c._inverter_recommendation
    assert hass.states.is_state(psu, state)

    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    await execute(c, inverter=True)

    assert calls == [(LIMIT, 2300)]
    assert c._inverter_limit_confirmed(False)
    assert c._inverter_recommendation
    assert hass.states.is_state(psu, state)


async def test_manual_24_handover_does_not_require_automatic_transfer_verification(
    rig, hass
):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    c.raw_config[CONF_RESERVE_MODE] = "active"
    c.raw_config[CONF_RESERVE_TRANSFER_VERIFIED] = False
    hass.states.async_set(DCDC, "off")

    await c.async_set_support_manual("dc24", True)

    assert calls == []
    assert c.support_manual("dc24")
    assert hass.states.is_state(DCDC, "off")


async def test_manual_24_converter_state_does_not_change_auto48_interlock(rig, hass):
    c, calls, *_ = rig
    c.async_request_refresh = AsyncMock()
    await c.async_set_support_manual("dc24", True)
    hass.states.async_set(DCDC, "unavailable")
    calls.clear()

    await execute(c, dc48=True, inverter=True)

    assert calls == [(LIMIT, 0), (PSU48, True)]
    assert hass.states.is_state(DCDC, "unavailable")
    assert hass.states.is_state(PSU48, "on")
    assert c._inverter_limit_confirmed(True)
    assert not c._inverter_recommendation


@pytest.mark.parametrize("grid_state", ["off", "unknown"])
async def test_grid_loss_leaves_handed_over_actors_untouched(rig, hass, grid_state):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
            CONF_SOC_ENTITY: "sensor.test_soc",
        }
    )
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.test_soc", "80")
    await c.async_set_support_manual("dc24", True)
    await c.async_set_support_manual("dc48", True)
    for psu in (PSU24, PSU48):
        hass.states.async_set(psu, "on")
    hass.states.async_set("binary_sensor.grid", grid_state)
    hass.states.async_set(DCDC, "off")
    calls.clear()

    await execute(c, True, True, True)

    assert calls == ([(LIMIT, 0)] if grid_state == "off" else [])
    assert hass.states.is_state(PSU24, "on")
    assert hass.states.is_state(PSU48, "on")
    assert hass.states.is_state(DCDC, "off")
    assert c._inverter_limit_confirmed(grid_state == "off")
    assert c._inverter_recommendation is (grid_state == "unknown")


async def test_manual_48_during_blackout_allows_automatic_24_rail_rescue(rig, hass):
    c, calls, dead, _ = rig
    c.async_request_refresh = AsyncMock()
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
            CONF_SOC_ENTITY: "sensor.test_soc",
        }
    )
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.test_soc", "80")
    await c.async_set_support_manual("dc48", True)
    for psu in (PSU24, PSU48):
        hass.states.async_set(psu, "on")
    hass.states.async_set("binary_sensor.grid", "off")
    hass.states.async_set(BLOCK, "unavailable")
    dead.add(BLOCK)
    hass.states.async_set(DCDC, "off")
    calls.clear()

    await execute(c, inverter=True)

    assert calls == [(DCDC, True), (PSU24, False), (LIMIT, 0)]
    assert hass.states.is_state(DCDC, "on")
    assert hass.states.is_state(PSU24, "off")
    assert hass.states.is_state(PSU48, "on")
    assert not c._inverter_recommendation


async def test_missing_rail_transfer_actuator_disables_that_path(rig, hass):
    c, calls, _, _ = rig
    c.raw_config.pop(CONF_DCDC_SWITCH)
    hass.states.async_set(PSU24, "on")
    assert not c.build_system_config().support.dc24_available
    await execute(c, inverter=True)
    assert (PSU24, False) not in calls
    assert c._coordinated_support_diag["reason"] == "missing_rail_transfer_actuator"
    assert c._inverter_recommendation is False


async def test_no_sources_still_controls_inverter(rig, hass):
    c, calls, _, _ = rig
    c.raw_config.pop(CONF_SUPPORT_DC24_SWITCH)
    c.raw_config.pop(CONF_SUPPORT_DC48_SWITCH)
    hass.states.async_set(DCDC, "off")
    await execute(c, inverter=True)
    assert calls == [(DCDC, True)]
    assert c._inverter_recommendation


async def test_background_entrypoint_and_data_loss_protection(rig, hass):
    c, calls, _, _ = rig
    result = SimpleNamespace(
        support_dc24_now=False,
        support_dc48_now=False,
        inverter_on=True,
        trajectory=SimpleNamespace(flows=()),
    )
    await c._apply_support_switching(result, c.build_system_config(), dt_util.now())
    await c._switch_task
    assert c._inverter_recommendation
    calls.clear()
    await c._coordinated_data_loss(None, dt_util.now())
    await c._switch_task
    assert calls[0] == (LIMIT, 0)
    assert (PSU24, True) in calls and (PSU48, True) in calls
    assert not c._inverter_recommendation


async def test_invalid_coordinated_configuration_is_rejected():
    from custom_components.battery_manager.config_flow import _validate_support_entities
    from custom_components.battery_manager.const import CONF_PSU48_MAX_CURRENT_A

    base = {CONF_INVERTER_BLOCK_SWITCH: BLOCK, CONF_INVERTER_LIMIT_ENTITY: LIMIT}
    assert _validate_support_entities(base) is None
    assert (
        _validate_support_entities({**base, CONF_SUPPORT_DC24_SWITCH: PSU24})
        == "coordinated_requires_dc24_transfer"
    )
    assert (
        _validate_support_entities({**base, CONF_SUPPORT_DC48_SWITCH: PSU48})
        == "coordinated_requires_psu48_current"
    )
    assert (
        _validate_support_entities(
            {**base, CONF_SUPPORT_DC48_SWITCH: PSU48, CONF_PSU48_MAX_CURRENT_A: 1.15}
        )
        is None
    )
    assert (
        _validate_support_entities({**base, "battery_min_soc_percent": 12})
        == "coordinated_invalid_reserve_order"
    )
    assert (
        _validate_support_entities({**base, CONF_SUPPORT_DC48_SWITCH: BLOCK})
        == "support_entities_not_distinct"
    )


@pytest.mark.parametrize("mode", [None, "legacy", "coordinated"])
async def test_old_manual_requests_are_cleared_on_upgrade(rig, mode):
    c, _, _, _ = rig
    c.learner.async_load = AsyncMock()
    previous = {
        "support_manual": {"dc24": True, "dc48": True},
        "support_state": {"dc24": False, "dc48": True},
    }
    if mode is not None:
        previous["support_control_mode"] = mode
    c._store.async_load = AsyncMock(return_value=previous)
    await c.async_load_persistent_state()
    assert c._support_manual == {"dc24": False, "dc48": False}
    cleared = (
        "forced_on_requests_cleared"
        if mode == "coordinated"
        else "legacy_manual_requests_cleared"
    )
    assert c._support_migration[cleared] == {
        "dc24": True,
        "dc48": True,
    }
    assert c._support_state["dc48"]


async def test_manual_handover_survives_restart(rig, hass):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    await c.async_set_support_manual("dc24", True)
    await c.async_set_support_manual("dc48", True)
    assert await c._support_handover_store.async_load() == {
        key: c.support_handover_binding(key) for key in ("dc24", "dc48")
    }
    hass.states.async_set(DCDC, "off")
    restarted = BatteryManagerCoordinator(hass, c.entry)
    restarted.learner.async_load = AsyncMock()
    restarted._save_persistent_state = Mock()
    restarted._switch_entity = c._switch_entity
    await restarted.async_load_persistent_state()
    assert restarted.support_manual("dc24")
    assert restarted.support_manual("dc48")
    calls.clear()

    await execute(restarted, True, True, True)

    assert calls == []
    assert hass.states.is_state(PSU24, "off")
    assert hass.states.is_state(PSU48, "off")
    assert hass.states.is_state(DCDC, "off")
    assert restarted._inverter_limit_confirmed(False)
    assert restarted._inverter_recommendation


async def test_learning_does_not_invent_constant_psu_energy(rig):
    from datetime import datetime

    from custom_components.battery_manager.const import CONF_PSU48_OUTPUT_VOLTAGE_V

    c, _, _, _ = rig
    day = "2026-09-14"
    cfg = {
        CONF_INVERTER_BLOCK_SWITCH: BLOCK,
        CONF_INVERTER_LIMIT_ENTITY: LIMIT,
        CONF_SUPPORT_DC48_SWITCH: PSU48,
        CONF_PSU48_OUTPUT_VOLTAGE_V: 49.56,
    }
    result = c.learner._psu48_series(
        day,
        cfg,
        {PSU48: {(day, h): 1 for h in range(23)}},
        {PSU48: datetime(2026, 9, 1, tzinfo=dt_util.UTC)},
        dt_util.UTC,
        {(day, 0): (48, 49), (day, 1): (50, 51), (day, 2): (49, 50)},
    )
    assert result[0] is None and result[2] is None and result[3] is None
    assert result[1] == 0 and result[23] == 0


async def test_live_update_builds_coordinated_plan_and_publishes_diagnostics(hass):
    from test_support_switching import _setup

    from custom_components.battery_manager.const import CONF_PSU48_MAX_CURRENT_A

    for entity, state in ((BLOCK, "off"), (PSU24, "off"), (PSU48, "off"), (DCDC, "on")):
        hass.states.async_set(entity, state)
    hass.states.async_set(LIMIT, "2300")

    async def set_limit(call):
        target = call.data["value"]
        hass.states.async_set(LIMIT, str(target))
        hass.states.async_set(BLOCK, "on" if target == 0 else "off")

    hass.services.async_register("number", "set_value", set_limit)
    calls = []
    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep", new=AsyncMock()
    ):
        c = await _setup(
            hass,
            calls,
            extra_data={
                CONF_INVERTER_BLOCK_SWITCH: BLOCK,
                CONF_INVERTER_LIMIT_ENTITY: LIMIT,
                CONF_SUPPORT_DC24_SWITCH: PSU24,
                CONF_SUPPORT_DC48_SWITCH: PSU48,
                CONF_DCDC_SWITCH: DCDC,
                CONF_PSU48_MAX_CURRENT_A: 1.15,
            },
        )
        assert c.data["valid"]
        assert c.data["coordinated_support"]["enabled"]
        assert c.data["coordinated_support"]["psu48_power_source"] == "unknown"
        assert "support_mode" in c.data["soc_forecast"][1]
        assert BLOCK in c._tracked_entities()
        assert LIMIT in c._tracked_entities()
        assert not any(entity == BLOCK for _, entity in calls)


@pytest.mark.parametrize("watts", [500, 1700, 2300, 3100])
@pytest.mark.parametrize("planned", [None, 0, 30, 700, 2300])
async def test_inverter_limit_uses_configured_power_and_never_writes_feedback(
    rig, hass, watts, planned
):
    c, calls, _, _ = rig
    c.raw_config["inverter_max_power_w"] = watts
    c._reserve_inverter_limit_w = planned
    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    await execute(c, inverter=True)
    if planned == 0:
        assert calls == []
        assert float(hass.states.get(LIMIT).state) == 0
        assert not c._inverter_recommendation
        return
    assert calls == [(LIMIT, float(watts))]
    assert c._inverter_recommendation
    calls.clear()
    await execute(c, inverter=False)
    assert calls == [(LIMIT, 0)]
    assert not c._inverter_recommendation


async def test_inverter_limit_failure_prevents_support(rig):
    c, calls, _, failures = rig
    failures.add(LIMIT)
    await execute(c, True, True)
    assert calls == [(LIMIT, 0)]
    assert not c._support_state["dc24"] and not c._support_state["dc48"]


async def test_delayed_feedback_is_read_only_and_does_not_repeat_number_write(
    rig, hass
):
    c, calls, dead, _ = rig
    dead.add(BLOCK)
    await execute(c, True, True)
    await execute(c, True, True)
    assert calls == [(LIMIT, 0)]
    hass.states.async_set(BLOCK, "on")
    await execute(c, True, True)
    assert calls[1:] == [(PSU24, True), (DCDC, False), (PSU48, True)]


async def test_optional_feedback_can_be_omitted(rig):
    c, calls, _, _ = rig
    c.raw_config.pop(CONF_INVERTER_BLOCK_SWITCH)
    await execute(c, inverter=False)
    assert calls == [(LIMIT, 0)]
    assert c._inverter_limit_confirmed(True)


async def test_stale_coordinate_cannot_release_psu_or_inverter(rig, hass):
    c, calls, *_ = rig
    c._schedule_replan = Mock()
    c.voltage_gate.coordinate_snapshot(dt_util.now())
    c.voltage_gate.monitor.epoch = 1
    c.voltage_gate.monitor.generation = 2
    c.voltage_gate.monitor.phase = "settling"
    c.voltage_gate.monitor.has_reconciled = True
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    c._support_state["dc24"] = True
    calls.clear()
    await execute(c, False, False, True)
    assert (PSU24, False) not in calls
    assert (LIMIT, 2300) not in calls
    assert not c._inverter_recommendation


@pytest.mark.parametrize("fresh_record", [False, True])
async def test_queued_coordinate_requires_complete_fresh_successor_plan(
    rig, hass, fresh_record
):
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs

    c, calls, *_ = rig
    c._schedule_replan = Mock()
    config = c.build_system_config()
    now = dt_util.now()
    c.voltage_gate.coordinate_snapshot(now)
    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    # Capture legacy None before the episode, then delay executing that intent.
    queued = c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, config, now
    )
    monitor = c.voltage_gate.monitor
    monitor.epoch, monitor.phase, monitor.generation, monitor.has_reconciled = (
        1,
        "tracking",
        4,
        True,
    )
    c.live_ac.plan_revision += 1
    if fresh_record:
        coordinate = c.voltage_gate.coordinate_snapshot(now)
        inputs = PlanInputs(
            now,
            80,
            (
                HourSlot(0, now, 1, now.hour, 0, 100, 80),
                HourSlot(
                    1, now + timedelta(hours=1), 1, (now.hour + 1) % 24, 4000, 100, 80
                ),
            ),
            soc_coordinate=coordinate,
        )
        from custom_components.battery_manager.core.optimize import plan

        successor = plan(config, inputs)
        assert successor.inverter_on
        assert not successor.support_dc24_now and not successor.support_dc48_now
        c._last_planner_recording = (config, inputs, successor)
    await queued
    assert ((LIMIT, 2300) in calls) is fresh_record
    assert c._inverter_recommendation is fresh_record


@pytest.mark.parametrize("record_revision,allowed", [(None, True), (1, False)])
async def test_known_stale_legacy_record_cannot_fund_current_revision(
    rig, hass, record_revision, allowed
):
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs
    from custom_components.battery_manager.core.optimize import plan

    c, calls, *_ = rig
    c._schedule_replan = Mock()
    config = c.build_system_config()
    now = dt_util.now()
    inputs = PlanInputs(
        now,
        80,
        (
            HourSlot(0, now, 1, now.hour, 0, 100, 80),
            HourSlot(
                1, now + timedelta(hours=1), 1, (now.hour + 1) % 24, 4000, 100, 80
            ),
        ),
    )
    result = plan(config, inputs)
    assert result.inverter_on
    c._last_planner_recording = (config, inputs, result)
    c.live_ac.plan_revision = 2
    c._last_planner_recording_revision = record_revision
    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    await execute(c, False, False, True)
    assert ((LIMIT, 2300) in calls) is allowed
