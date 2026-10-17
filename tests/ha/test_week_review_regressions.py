"""October recorder findings: source continuity and measured learning freshness."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import DCDC, PSU24, PSU48, execute
from test_coordinated_actuation import rig as rig

from custom_components.battery_manager.const import (
    CONF_RESERVE_GRID_ENTITY,
    CONF_RESERVE_MODE,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_SWITCH_DELAY_S,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.core import HourSlot, PlanInputs
from custom_components.battery_manager.load_actuation import ActorRequest


def configure_reserve(c, hass, soc=12):
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
            CONF_SOC_ENTITY: "sensor.test_soc",
        }
    )
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.test_soc", str(soc))


@pytest.mark.parametrize("late", [DCDC, PSU48])
async def test_floor_protection_preserves_valid_economic_source_after_late_feedback(
    rig, hass, freezer, late
):
    c, calls, dead, _ = rig
    configure_reserve(c, hass)
    now = dt_util.now()
    inputs = PlanInputs(now, 12, (HourSlot(0, now, 1, now.hour, 0, 100, 60),))
    result = SimpleNamespace(
        support_dc24_now=True,
        support_dc48_now=True,
        trajectory=SimpleNamespace(flows=()),
    )
    c._last_planner_recording = (c.build_system_config(), inputs, result)
    dead.add(late)
    await execute(c, dc24=True, dc48=True)
    dead.remove(late)
    hass.states.async_set(late, "off" if late == DCDC else "on")
    freezer.tick(timedelta(seconds=65))
    hass.states.async_set("sensor.test_soc", "12", force_update=True)
    hass.states.async_set("binary_sensor.grid", "on", force_update=True)
    calls.clear()
    await c._coordinated_data_loss(12, dt_util.now())
    await c._switch_task
    assert (PSU24, False) not in calls
    assert (PSU48, False) not in calls
    assert hass.states.is_state(PSU24, "on")
    assert hass.states.is_state(DCDC, "off")
    assert c._support_state == {"dc24": True, "dc48": True}
    assert not c._inverter_recommendation


@pytest.mark.parametrize("grid", ["off", "unknown"])
async def test_grid_fallback_honours_confirmed_rail_overlap(rig, hass, grid):
    c, calls, _, _ = rig
    configure_reserve(c, hass, 80)
    hass.states.async_set("binary_sensor.grid", grid)
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    c.raw_config[CONF_SUPPORT_SWITCH_DELAY_S] = 3

    async def overlap(seconds):
        assert seconds == 3
        assert hass.states.is_state(DCDC, "on")
        assert hass.states.is_state(PSU24, "on")

    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep",
        side_effect=overlap,
    ) as delay:
        await execute(c, dc24=True)
    delay.assert_awaited_once_with(3.0)
    assert calls.index((DCDC, True)) < calls.index((PSU24, False))
    assert hass.states.is_state(DCDC, "on")
    assert hass.states.is_state(PSU24, "off")


async def test_protective_plan_expiry_does_not_reuse_economic_holding(rig, hass):
    c, calls, _, _ = rig
    configure_reserve(c, hass, 30)
    now = dt_util.now()
    inputs = PlanInputs(
        now - timedelta(minutes=6), 30, (HourSlot(0, now, 1, now.hour, 0, 100, 60),)
    )
    c._last_planner_recording = (
        c.build_system_config(),
        inputs,
        SimpleNamespace(support_dc24_now=True, support_dc48_now=True),
    )
    await c._coordinated_data_loss(30, now)
    await c._switch_task
    assert (PSU24, True) not in calls
    assert (PSU48, True) not in calls


async def test_late_owned_feedback_updates_source_without_a_cpu_replan(
    rig, hass, freezer
):
    c, _, _, _ = rig
    configure_reserve(c, hass)
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)

    async def on(call):
        hass.states.async_set(call.data["entity_id"], "on")

    async def off(call):
        entity = call.data["entity_id"]
        if entity != DCDC:
            hass.states.async_set(entity, "off")

    hass.services.async_register("homeassistant", "turn_on", on)
    hass.services.async_register("homeassistant", "turn_off", off)
    await execute(c, dc24=True)
    assert c._supply_actor_requests[DCDC].state == "pending"
    assert not c._support_state["dc24"]
    before = c._last_support_switch
    c._schedule_replan = Mock()
    c._debounced_fast_update = AsyncMock()
    c._listeners_setup = True
    c._tracked_entities = Mock(return_value={DCDC, PSU24})
    unsubscribe = async_track_state_change_event(
        hass, [DCDC, PSU24], c._handle_entity_change
    )
    try:
        freezer.tick(timedelta(seconds=10))
        hass.states.async_set(DCDC, "off")
        await hass.async_block_till_done(wait_background_tasks=True)
        assert c._support_state["dc24"]
        assert c._supply_actor_requests[DCDC].state == "confirmed"
        assert c._last_support_switch == before
        c._schedule_replan.assert_not_called()
        c._debounced_fast_update.assert_awaited_once()
        hass.states.async_set(PSU24, "off")
        await hass.async_block_till_done(wait_background_tasks=True)
        c._schedule_replan.assert_called_once()
    finally:
        unsubscribe()
        c._listeners_setup = False


async def test_grid_return_during_overlap_cannot_remove_required_psu(rig, hass):
    c, calls, _, _ = rig
    configure_reserve(c, hass, 80)
    hass.states.async_set("binary_sensor.grid", "unknown")
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")

    async def overlap(_seconds):
        hass.states.async_set("binary_sensor.grid", "on")

    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep",
        side_effect=overlap,
    ):
        await execute(c, dc24=True)
    assert (PSU24, False) not in calls
    assert hass.states.is_state(PSU24, "on")
    assert hass.states.is_state(DCDC, "off")
    assert c._coordinated_support_diag["reason"] == "grid_supply_changed"


async def test_immediate_owned_confirmation_does_not_request_full_planning(rig, hass):
    c, _, _, _ = rig
    configure_reserve(c, hass)
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)
    c._schedule_replan = Mock()
    c._debounced_fast_update = AsyncMock()
    c._tracked_entities = Mock(return_value={PSU48})
    c._listeners_setup = True

    async def on(call):
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("homeassistant", "turn_on", on)
    unsubscribe = async_track_state_change_event(hass, [PSU48], c._handle_entity_change)
    try:
        from custom_components.battery_manager.actor_ownership import SUPPLY_OWNER

        assert await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
        await hass.async_block_till_done(wait_background_tasks=True)
        c._schedule_replan.assert_not_called()
        c._debounced_fast_update.assert_awaited_once()
    finally:
        unsubscribe()
        c._listeners_setup = False


async def test_other_pending_confirmation_cannot_hide_an_external_source_edge(
    rig, hass
):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    c._listeners_setup = True
    c._tracked_entities = Mock(return_value={PSU24, PSU48})
    c._schedule_replan = Mock()
    c._supply_actor_requests = {
        PSU24: ActorRequest(True, dt_util.now(), "confirmed"),
        PSU48: ActorRequest(True, dt_util.now()),
    }
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(PSU48, "on")
    unsubscribe = async_track_state_change_event(hass, [PSU24], c._handle_entity_change)
    try:
        hass.states.async_set(PSU24, "off")
        await hass.async_block_till_done()
        c._schedule_replan.assert_called_once()
    finally:
        unsubscribe()
        c._listeners_setup = False


async def test_late_first_source_receives_full_overlap_before_old_source_off(rig, hass):
    c, calls, dead, _ = rig
    dead.add(PSU24)
    with patch("custom_components.battery_manager.coordinator.asyncio.sleep") as delay:
        assert not await c._sequence_dc24(True, PSU24)
    delay.assert_not_awaited()
    assert (DCDC, False) not in calls
    dead.remove(PSU24)
    hass.states.async_set(PSU24, "on")

    async def overlap(seconds):
        assert seconds == 3
        assert hass.states.is_state(PSU24, "on")
        assert hass.states.is_state(DCDC, "on")

    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep",
        side_effect=overlap,
    ) as delay:
        assert await c._sequence_dc24(True, PSU24)
    delay.assert_awaited_once_with(3.0)
    assert hass.states.is_state(DCDC, "off")
