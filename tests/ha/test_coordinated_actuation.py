"""F-COORDINATED-DC-SUPPORT R5: real state confirmation and single ownership."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_INVERTER_BLOCK_SWITCH,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    DOMAIN,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

BLOCK = "switch.block_ac"
PSU24 = "switch.psu24"
PSU48 = "switch.psu48"
DCDC = "switch.dcdc"


@pytest.fixture
async def rig(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_BLOCK_SWITCH: BLOCK,
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

    async def switch(entity, on):
        calls.append((entity, on))
        if entity in failures:
            return False
        if entity not in dead:
            hass.states.async_set(entity, "on" if on else "off")
        return True

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
    assert calls == [(BLOCK, True), (PSU24, True), (DCDC, False), (PSU48, True)]
    assert c.data["inverter_recommendation"] is False
    assert c._support_state == {"dc24": True, "dc48": True}


@pytest.mark.parametrize("entity", [BLOCK, PSU24, DCDC, PSU48])
async def test_unconfirmed_actuator_prevents_next_transition(rig, hass, entity):
    c, calls, dead, _ = rig
    dead.add(entity)
    await execute(c, True, True)
    assert c.data["inverter_recommendation"] is False
    assert c._coordinated_support_diag["reason"] != "settled"
    if entity == BLOCK:
        assert calls == [(BLOCK, True)]
    elif entity == PSU24:
        assert (DCDC, False) not in calls and (PSU48, True) not in calls
    elif entity == DCDC:
        assert (PSU48, True) not in calls
    else:
        assert not c._support_state["dc48"]


async def test_return_removes_support_before_releasing_inverter(rig, hass):
    c, calls, _, _ = rig
    for entity in (BLOCK, PSU24, PSU48):
        hass.states.async_set(entity, "on")
    hass.states.async_set(DCDC, "off")
    await execute(c, inverter=True)
    assert calls == [(PSU48, False), (DCDC, True), (PSU24, False), (BLOCK, False)]
    assert c.data["inverter_recommendation"] is True


@pytest.mark.parametrize("entity", [PSU48, PSU24, DCDC, BLOCK])
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
    assert calls == [(BLOCK, True)]
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
    assert calls == [(BLOCK, True), (PSU48, False), (BLOCK, False)]
    assert not c._dc48_controller_engaged()


async def test_manual_is_request_not_uncoordinated_actuation(rig):
    c, calls, _, _ = rig
    c.async_request_refresh = AsyncMock()
    await c.async_set_support_manual("dc48", True)
    assert c._support_manual["dc48"]
    assert calls == []
    c.async_request_refresh.assert_awaited_once()


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
        support_dc24_now=False, support_dc48_now=False, inverter_on=True
    )
    await c._apply_support_switching(result, c.build_system_config(), dt_util.now())
    await c._switch_task
    assert c._inverter_recommendation
    calls.clear()
    await c._coordinated_data_loss(None, dt_util.now())
    await c._switch_task
    assert calls[0] == (BLOCK, True)
    assert (PSU24, True) in calls and (PSU48, True) in calls
    assert not c._inverter_recommendation


async def test_invalid_coordinated_configuration_is_rejected():
    from custom_components.battery_manager.config_flow import _validate_support_entities
    from custom_components.battery_manager.const import CONF_PSU48_MAX_CURRENT_A

    base = {CONF_INVERTER_BLOCK_SWITCH: BLOCK}
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


async def test_manual_migration_is_visible_and_new_requests_survive_reload(rig):
    c, _, _, _ = rig
    c.learner.async_load = AsyncMock()
    c._store.async_load = AsyncMock(
        return_value={
            "support_manual": {"dc24": False, "dc48": True},
            "support_state": {"dc24": False, "dc48": True},
        }
    )
    await c.async_load_persistent_state()
    assert c._support_manual == {"dc24": False, "dc48": False}
    assert c._support_migration["legacy_manual_requests_cleared"]["dc48"]
    assert c._support_state["dc48"]
    c._support_manual["dc24"] = True
    c._store.async_load = AsyncMock(return_value=c._persistent_payload())
    await c.async_load_persistent_state()
    assert c._support_manual["dc24"]


async def test_learning_does_not_invent_constant_psu_energy(rig):
    from datetime import datetime

    from custom_components.battery_manager.const import CONF_PSU48_OUTPUT_VOLTAGE_V

    c, _, _, _ = rig
    day = "2026-09-14"
    cfg = {
        CONF_INVERTER_BLOCK_SWITCH: BLOCK,
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
    calls = []
    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep", new=AsyncMock()
    ):
        c = await _setup(
            hass,
            calls,
            extra_data={
                CONF_INVERTER_BLOCK_SWITCH: BLOCK,
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
