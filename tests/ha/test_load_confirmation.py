"""Physical actor feedback and safety races; no production delays elapse."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed_exact

from custom_components.battery_manager import load_actuation
from custom_components.battery_manager.load_actuation import LoadAction
from tests.ha.test_load_switching import (
    ENABLE,
    PLUG,
    _active_plan,
    _detach_listener,
    _setup,
)


async def test_service_acknowledgement_does_not_start_dwell(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._floor_guard_active = False
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(PLUG, "off")
    coordinator._last_load_switch.clear()
    coordinator._load_charging_active.clear()
    requested = asyncio.Event()

    async def acknowledge(call):
        requested.set()

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    action = LoadAction(load_id, data, True, False)
    task = asyncio.create_task(coordinator._execute_load_switching([action]))
    await requested.wait()
    assert load_id not in coordinator._last_load_switch
    assert not coordinator._load_charging_active.get(load_id, False)
    hass.states.async_set(PLUG, "on")
    await task
    assert coordinator._load_charging_active[load_id]
    assert load_id in coordinator._last_load_switch


async def test_queued_on_is_dropped_after_pause(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._floor_guard_active = False
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    calls.clear()
    async with coordinator._switch_lock:
        task = asyncio.create_task(
            coordinator._execute_load_switching(
                [LoadAction(load_id, data, True, False)]
            )
        )
        coordinator.set_load_enabled(load_id, False)
    await task
    assert not any(service == "turn_on" for service, _ in calls)
    assert hass.states.is_state(PLUG, "off")


async def test_control_off_relinquishes_without_a_planner_refresh(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._last_load_switch[load_id] = dt_util.now() - timedelta(hours=1)
    coordinator._load_plug_owned[load_id] = True
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        coordinator.set_load_enabled(load_id, False)
        assert hass.states.is_state(PLUG, "on")
    assert hass.states.is_state(ENABLE, "on")
    assert not coordinator._load_plug_owned[load_id]


async def test_confirmation_timeout_uses_production_deadline(hass):
    class Expired:
        async def __aenter__(self):
            raise TimeoutError

        async def __aexit__(self, *args):
            return False

    with patch.object(
        load_actuation.asyncio, "timeout", return_value=Expired()
    ) as timeout:
        assert not await load_actuation.confirm_state(hass, PLUG, True)
    timeout.assert_called_once_with(30)


async def test_failed_off_retries_only_known_state_after_sixty_seconds(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "on")
    now = dt_util.now()
    with patch.object(
        coordinator, "_switch_entity", AsyncMock(return_value=False)
    ) as command:
        with patch.object(dt_util, "now", return_value=now):
            assert not await coordinator._switch_load_entity(PLUG, False)
        with patch.object(dt_util, "now", return_value=now + timedelta(seconds=59)):
            assert not await coordinator._switch_load_entity(PLUG, False)
        assert command.await_count == 1
        hass.states.async_set(PLUG, "unavailable")
        with patch.object(dt_util, "now", return_value=now + timedelta(seconds=60)):
            assert not await coordinator._switch_load_entity(PLUG, False)
            assert command.await_count == 1
            hass.states.async_set(PLUG, "on")
            assert not await coordinator._switch_load_entity(PLUG, False)
            assert command.await_count == 2


async def test_late_on_feedback_after_release_is_left_to_operator(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._floor_guard_active = False
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(PLUG, "off")
    requested = asyncio.Event()

    async def acknowledge(call):
        requested.set()

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    task = asyncio.create_task(
        coordinator._execute_load_switching([LoadAction(load_id, data, True, False)])
    )
    await requested.wait()
    coordinator.set_load_enabled(load_id, False)
    hass.states.async_set(PLUG, "on")
    await task
    assert hass.states.is_state(ENABLE, "on")
    assert hass.states.is_state(PLUG, "on")
    assert not coordinator._load_charging_active.get(load_id, False)


async def test_release_cancels_deadline_and_resume_does_not_adopt_manual_input(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls, min_runtime_min=30)
    coordinator._floor_guard_active = False
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[load_id] = True
    deadline = dt_util.now() + timedelta(minutes=20)
    coordinator._load_run_deadline[load_id] = deadline
    coordinator._arm_off_timer(load_id, deadline)
    calls.clear()
    coordinator.set_load_enabled(load_id, False)
    assert load_id not in coordinator._load_run_deadline
    assert load_id not in coordinator._load_off_timer
    assert not coordinator._pause_tasks
    coordinator.set_load_enabled(load_id, True)
    await coordinator._execute_load_switching([LoadAction(load_id, data, False, True)])
    assert calls == [("turn_off", ENABLE)]
    assert hass.states.is_state(PLUG, "on")
    assert not coordinator._load_plug_owned[load_id]


async def test_unconfirmed_off_does_not_block_another_load_shutdown(hass):
    from custom_components.battery_manager.const import (
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_POWER_W,
    )

    calls = []
    other = "switch.independent_load"
    other_data = {CONF_LOAD_CONTROL_SWITCH: other, CONF_LOAD_POWER_W: 300.0}
    coordinator, load_id, data = await _setup(
        hass,
        calls,
        charge_enable=None,
        additional_load_data=(other_data,),
    )
    coordinator._floor_guard_active = False
    other_id = next(
        key
        for key, sub in coordinator.entry.subentries.items()
        if sub.data.get(CONF_LOAD_CONTROL_SWITCH) == other
    )
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(other, "on")
    first_requested = asyncio.Event()
    second_stopped = asyncio.Event()

    async def acknowledge(call):
        entity = call.data["entity_id"]
        if entity == PLUG:
            first_requested.set()
        else:
            hass.states.async_set(entity, "off")
            second_stopped.set()

    hass.services.async_register("homeassistant", "turn_off", acknowledge)
    task = asyncio.create_task(
        coordinator._execute_load_switching(
            [
                LoadAction(load_id, data, False, True),
                LoadAction(
                    other_id,
                    dict(coordinator.entry.subentries[other_id].data),
                    False,
                    True,
                ),
            ]
        )
    )
    try:
        # Bound actual asynchronous service publication, never wait out the
        # production confirmation timeout to prove concurrency.
        async with asyncio.timeout(1):
            await first_requested.wait()
            await second_stopped.wait()
        assert hass.states.is_state(other, "off")
        assert not task.done()
        hass.states.async_set(PLUG, "off")
        async with asyncio.timeout(1):
            await task
        assert coordinator._load_charging_active[load_id] is False
        assert coordinator._load_charging_active[other_id] is False
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_gate_confirmation_followed_by_pause_never_starts_input(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._floor_guard_active = False
    hass.states.async_set(ENABLE, "off")
    hass.states.async_set(PLUG, "off")
    requested = asyncio.Event()

    async def acknowledge(call):
        assert call.data["entity_id"] == ENABLE
        requested.set()

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    task = asyncio.create_task(
        coordinator._execute_load_switching(
            [
                LoadAction(load_id, data, True, False),
            ]
        )
    )
    await requested.wait()
    coordinator.set_load_enabled(load_id, False)
    hass.states.async_set(ENABLE, "on")
    await task
    assert hass.states.is_state(ENABLE, "on")
    assert hass.states.is_state(PLUG, "off")


@pytest.mark.freeze_time(real_asyncio=True)
@pytest.mark.parametrize(
    ("delayed_actor", "remaining_authority"),
    [(ENABLE, None), (PLUG, None), (ENABLE, "local_pv"), (PLUG, "manual")],
)
async def test_forecast_withdrawal_during_load_confirmation(
    hass, freezer, delayed_actor, remaining_authority
):
    from custom_components.battery_manager.const import (
        CONF_DC_LOAD_ENTITY,
        CONF_RESERVE_GRID_ENTITY,
    )

    calls = []
    c, load_id, _ = await _setup(hass, calls)
    _detach_listener(c)
    c._floor_guard_active = False
    c._last_load_switch.clear()
    c._load_actor_requests.clear()
    c._load_charging_active[load_id] = False
    hass.states.async_set(ENABLE, "off")
    hass.states.async_set(PLUG, "off")
    config, inputs, _ = c._last_planner_recording
    result = _active_plan(load_id)
    c._last_planner_recording = config, inputs, result
    assert c.pv_plan_evidence_valid(config, inputs)
    requested = asyncio.Event()

    async def turn_on(call):
        entity = call.data["entity_id"]
        calls.append(("turn_on", entity))
        if entity == delayed_actor:
            requested.set()
        else:
            hass.states.async_set(entity, "on")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    calls.clear()
    await c._apply_load_switching(
        result, dt_util.now(), (1.0,), pv_plan=(config, inputs)
    )
    try:
        await asyncio.wait_for(requested.wait(), timeout=5)
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(c.raw_config["pv_forecast_tomorrow_entity"], "0")
        assert not c.pv_plan_evidence_valid(config, inputs)
        c._last_planner_recording = None
        c.live_ac.envelope = None
        if remaining_authority == "local_pv":
            c.raw_config.update(
                {
                    CONF_RESERVE_GRID_ENTITY: "binary_sensor.test_grid",
                    CONF_DC_LOAD_ENTITY: "sensor.test_dc",
                    "operation_pv_power_entity": "sensor.test_actual_pv",
                    "operation_house_power_entity": "sensor.test_house_power",
                }
            )
            hass.states.async_set("binary_sensor.test_grid", "on")
            for entity, value in (
                ("sensor.test_dc", "0"),
                ("sensor.test_actual_pv", "3500"),
                ("sensor.test_house_power", "100"),
            ):
                hass.states.async_set(entity, value, {"unit_of_measurement": "W"})
            assert c.live_ac.load_pv_permission(500, config=config)
        elif remaining_authority == "manual":
            c.set_load_enabled(load_id, False)
    finally:
        hass.states.async_set(delayed_actor, "on")
        await c._load_switch_task
        await hass.async_block_till_done(wait_background_tasks=True)
    if remaining_authority == "local_pv":
        assert hass.states.is_state(ENABLE, "on")
        assert hass.states.is_state(PLUG, "on")
        assert load_id in c._load_run_deadline
        return
    if remaining_authority == "manual":
        assert hass.states.is_state(PLUG, "on")
        assert not any(service == "turn_off" for service, _ in calls)
        assert load_id not in c._load_run_deadline
        return
    if delayed_actor == ENABLE:
        assert ("turn_on", PLUG) not in calls
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")
    assert load_id not in c._load_run_deadline


async def test_failed_confirmation_remains_open_in_persistent_state(hass):
    calls = []
    coordinator, _, _ = await _setup(hass, calls)
    hass.states.async_set(PLUG, "on")
    with (
        patch.object(coordinator, "_switch_entity", AsyncMock(return_value=True)),
        patch(
            "custom_components.battery_manager.coordinator.confirm_state",
            AsyncMock(return_value=False),
        ),
    ):
        assert not await coordinator._switch_load_entity(PLUG, False)
    saved = coordinator._persistent_payload()["load_actor_requests"][PLUG]
    assert saved["desired"] is False
    assert saved["state"] == "confirmation_failed"
    hass.states.async_set(PLUG, "off")
    assert await coordinator._switch_load_entity(PLUG, False)
    assert PLUG not in coordinator._persistent_payload()["load_actor_requests"]


async def test_reload_keeps_pending_off_and_retry_timestamp(hass):
    from copy import deepcopy

    calls = []
    coordinator, load_id, _ = await _setup(hass, calls)
    now = dt_util.now()
    coordinator._data_stale_since = now - timedelta(hours=3)
    coordinator._stale_shed_active = True
    coordinator._stale_shed_pending = {load_id}
    hass.states.async_set(PLUG, "on")
    with (
        patch.object(dt_util, "now", return_value=now),
        patch.object(coordinator, "_switch_entity", AsyncMock(return_value=False)),
    ):
        assert not await coordinator._switch_load_entity(PLUG, False)
    payload = deepcopy(coordinator._persistent_payload())
    coordinator._load_actor_requests.clear()
    coordinator._stale_shed_pending.clear()
    coordinator._stale_shed_active = False
    with patch.object(
        coordinator._store, "async_load", AsyncMock(return_value=payload)
    ):
        await coordinator.async_load_persistent_state()
    assert coordinator._stale_shed_active
    assert coordinator._stale_shed_pending == {load_id}
    assert coordinator._load_actor_requests[PLUG].requested_at == now
    with patch.object(
        coordinator, "_switch_entity", AsyncMock(return_value=False)
    ) as command:
        with patch.object(dt_util, "now", return_value=now + timedelta(seconds=59)):
            assert not await coordinator._switch_load_entity(PLUG, False)
        command.assert_not_awaited()
        with patch.object(dt_util, "now", return_value=now + timedelta(seconds=60)):
            assert not await coordinator._switch_load_entity(PLUG, False)
        command.assert_awaited_once()


async def test_on_arriving_after_timeout_cannot_reclaim_released_input(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._floor_guard_active = False
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(PLUG, "off")

    async def acknowledge(call):
        pass

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        AsyncMock(side_effect=lambda _hass, _entity, desired: not desired),
    ):
        await coordinator._execute_load_switching(
            [LoadAction(load_id, data, True, False)]
        )
    assert coordinator._load_actor_requests[PLUG].state == "confirmation_failed"
    assert not coordinator._load_charging_active.get(load_id, False)
    # The input reports the accepted ON after the grace period, while paused.
    coordinator.set_load_enabled(load_id, False)
    hass.states.async_set(PLUG, "on")
    load_actuation.reconcile_feedback(coordinator)
    assert hass.states.is_state(PLUG, "on")
    assert not coordinator._load_plug_owned[load_id]


async def test_late_input_feedback_starts_dwell_at_the_reported_edge(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls, charge_enable=None)
    coordinator._floor_guard_active = False
    hass.states.async_set(PLUG, "off")

    async def acknowledge(call):
        pass

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        AsyncMock(return_value=False),
    ):
        await coordinator._execute_load_switching(
            [LoadAction(load_id, data, True, False)]
        )
    assert load_id not in coordinator._last_load_switch
    hass.states.async_set(PLUG, "on")
    load_actuation.reconcile_feedback(coordinator)
    assert coordinator._load_plug_owned[load_id]
    assert coordinator._load_charging_active[load_id]
    assert coordinator._last_load_switch[load_id] == hass.states.get(PLUG).last_changed


async def test_repeated_pause_of_an_off_load_preserves_its_dwell_stamp(hass):
    calls = []
    coordinator, load_id, _ = await _setup(hass, calls)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    stamp = dt_util.now() - timedelta(minutes=5)
    coordinator._last_load_switch[load_id] = stamp
    calls.clear()
    with patch(
        "custom_components.battery_manager.coordinator.asyncio.sleep", AsyncMock()
    ) as sleep:
        coordinator.set_load_enabled(load_id, False)
        sleep.assert_not_awaited()
    assert coordinator._last_load_switch[load_id] == stamp
    assert calls == []


async def test_late_off_feedback_starts_minimum_off_at_the_reported_edge(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls, charge_enable=None)
    coordinator._floor_guard_active = False
    coordinator._load_plug_owned[load_id] = True
    coordinator._load_charging_active[load_id] = True
    earlier = dt_util.now() - timedelta(hours=1)
    coordinator._last_load_switch[load_id] = earlier
    hass.states.async_set(PLUG, "on")

    async def acknowledge(call):
        pass

    hass.services.async_register("homeassistant", "turn_off", acknowledge)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        AsyncMock(return_value=False),
    ):
        await coordinator._execute_load_switching(
            [LoadAction(load_id, data, False, True)]
        )
    assert coordinator._last_load_switch[load_id] == earlier
    hass.states.async_set(PLUG, "off")
    load_actuation.reconcile_feedback(coordinator)
    assert not coordinator._load_charging_active[load_id]
    assert not coordinator._load_plug_owned[load_id]
    assert coordinator._last_load_switch[load_id] == hass.states.get(PLUG).last_changed


async def test_queued_on_does_not_claim_an_input_turned_on_externally(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._floor_guard_active = False
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    action = LoadAction(load_id, data, True, False)
    hass.states.async_set(PLUG, "on")
    calls.clear()
    await coordinator._execute_load_switching([action])
    assert calls == [("turn_on", ENABLE)]
    assert not coordinator._load_plug_owned.get(load_id, False)
    await coordinator._execute_load_switching([LoadAction(load_id, data, False, True)])
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "on")


async def test_confirmed_off_can_stop_a_new_external_on_without_failure_backoff(hass):
    from custom_components.battery_manager.load_actuation import ActorRequest

    calls = []
    coordinator, load_id, _ = await _setup(hass, calls, charge_enable=None)
    coordinator._listeners_setup = False
    hass.states.async_set(PLUG, "on")
    coordinator._load_actor_requests[PLUG] = ActorRequest(
        False, dt_util.now(), "confirmed", "load:" + load_id
    )
    calls.clear()
    assert await coordinator._switch_load_entity(
        PLUG, False, actor_owner="load:" + load_id
    )
    assert calls == [("turn_off", PLUG)]
    assert hass.states.is_state(PLUG, "off")


@pytest.mark.parametrize("old_activate", [True, False])
@pytest.mark.parametrize("boundary", ["gate", "input"])
async def test_newer_charge_intent_wins_after_older_confirmation(
    hass, monkeypatch, old_activate, boundary
):
    """A newer opposite action owns bookkeeping after an awaited device reply."""
    from custom_components.battery_manager.load_safety import stop_unsafe_loads

    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._listeners_setup = False
    coordinator._floor_guard_active = False
    initial = "off" if old_activate else "on"
    hass.states.async_set(PLUG, initial)
    hass.states.async_set(ENABLE, initial)
    coordinator._load_charging_active[load_id] = not old_activate
    coordinator._load_plug_owned[load_id] = not old_activate
    if not old_activate:
        coordinator._load_plug_owned_entities[load_id] = PLUG
    original = coordinator._switch_load_entity
    overtaken = False
    boundary_entity = ENABLE if boundary == "gate" else PLUG

    async def newer_action_after_confirmation(entity, desired, **kwargs):
        nonlocal overtaken
        confirmed = await original(entity, desired, **kwargs)
        if (
            confirmed
            and entity == boundary_entity
            and desired == old_activate
            and not overtaken
        ):
            overtaken = True
            await coordinator._execute_load_switching(
                [
                    LoadAction(
                        load_id, data, not old_activate, coordinator._entity_is_on(PLUG)
                    ),
                ]
            )
        return confirmed

    monkeypatch.setattr(
        coordinator, "_switch_load_entity", newer_action_after_confirmation
    )
    calls.clear()
    await coordinator._execute_load_switching(
        [
            LoadAction(load_id, data, old_activate, not old_activate),
        ]
    )
    assert overtaken
    assert hass.states.is_state(ENABLE, "off" if old_activate else "on")
    assert coordinator._load_charging_active[load_id] is not old_activate
    if old_activate and boundary == "input":
        # Our delayed ON still owns its input, even though a newer gate stop
        # overtook it. Independent cleanup must finish that obligation.
        assert coordinator._load_plug_owned[load_id]
        await asyncio.gather(*await stop_unsafe_loads(coordinator))
    assert hass.states.is_state(PLUG, "off" if old_activate else "on")


async def test_stale_wiring_action_and_deleted_load_cannot_switch_an_actor(hass):
    from custom_components.battery_manager.const import CONF_LOAD_CONTROL_SWITCH
    from custom_components.battery_manager.load_actuation import (
        input_decision,
        input_diagnostics,
        input_off_allowed,
    )

    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._listeners_setup = False
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    calls.clear()
    await coordinator._execute_load_switching(
        [
            LoadAction(
                load_id,
                {**data, CONF_LOAD_CONTROL_SWITCH: "switch.old_wiring"},
                False,
                True,
            ),
            LoadAction("deleted_load", data, False, True),
        ]
    )
    assert calls == []
    assert hass.states.is_state(PLUG, "on")
    assert not input_off_allowed(coordinator, "deleted_load")
    assert input_decision(coordinator, "deleted_load") == "foreign"
    assert input_diagnostics(coordinator, "deleted_load") == {}


async def test_runtime_wiring_change_revokes_old_input_without_claiming_new_on(hass):
    from custom_components.battery_manager.load_actuation import reconcile_feedback

    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._listeners_setup = False
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "off")
    coordinator._load_plug_owned[load_id] = True
    coordinator._load_plug_owned_entities[load_id] = "switch.previous_input"
    coordinator._load_input_on_since[load_id] = dt_util.now() - timedelta(hours=1)
    reconcile_feedback(coordinator)
    assert coordinator._load_plug_owned[load_id] is False
    assert load_id not in coordinator._load_plug_owned_entities
    assert load_id not in coordinator._load_input_on_since
    calls.clear()
    await coordinator._execute_load_switching([LoadAction(load_id, data, False, True)])
    assert ("turn_off", PLUG) not in calls
    assert hass.states.is_state(PLUG, "on")


async def test_pending_gate_start_prevents_stale_input_cleanup(hass):
    from custom_components.battery_manager.load_actuation import (
        ActorRequest,
        input_off_allowed,
    )

    calls = []
    coordinator, load_id, _ = await _setup(hass, calls)
    coordinator._listeners_setup = False
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[load_id] = True
    assert not input_off_allowed(coordinator, load_id)
    hass.states.async_set(ENABLE, "off")
    coordinator._load_actor_requests[ENABLE] = ActorRequest(
        True, dt_util.now(), "pending", "load:" + load_id
    )
    assert not input_off_allowed(coordinator, load_id)
    coordinator._load_actor_requests[ENABLE].state = "service_failed"
    assert input_off_allowed(coordinator, load_id)


async def test_late_on_arms_booked_runtime_at_physical_edge(hass, freezer):
    calls = []
    c, load_id, data = await _setup(hass, calls, min_runtime_min=1, charge_enable=None)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._load_run_deadline.clear()
    c._floor_guard_active = False
    hass.states.async_set(PLUG, "off")

    async def accepted_without_feedback(call):
        calls.append(("turn_on", call.data["entity_id"]))

    hass.services.async_register("homeassistant", "turn_on", accepted_without_feedback)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        AsyncMock(return_value=False),
    ):
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=0.5)]
        )
    freezer.tick(timedelta(seconds=load_actuation.LOAD_CONFIRM_TIMEOUT_S + 5))
    # A replan happens before the transport is eligible to retry. The
    # original command still owns a delayed reply and its booking.
    await c._execute_load_switching([LoadAction(load_id, data, True, False, run_h=1)])
    assert calls.count(("turn_on", PLUG)) == 1
    hass.states.async_set(PLUG, "on")
    with patch.object(c, "_arm_off_timer", wraps=c._arm_off_timer) as arm:
        load_actuation.reconcile_feedback(c)
        edge = hass.states.get(PLUG).last_changed
        deadline = edge + timedelta(minutes=30)
        assert c._load_charging_active[load_id]
        assert c._load_plug_owned[load_id]
        assert c._last_load_switch[load_id] == edge
        assert c._load_run_deadline.get(load_id) == deadline
        arm.assert_called_once_with(load_id, deadline)
    await c._apply_load_switching(_active_plan(load_id), edge, (1.0,))
    assert hass.states.is_state(PLUG, "on")

    async def apply_current_plan():
        await c._apply_load_switching(_active_plan(load_id), dt_util.now(), (1.0,))

    with (
        patch.object(c, "async_request_refresh", side_effect=apply_current_plan),
        patch.object(hass, "async_create_task", wraps=hass.async_create_task) as spawn,
    ):
        freezer.move_to(deadline - timedelta(seconds=1))
        async_fire_time_changed_exact(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert load_id in c._load_off_timer
        assert hass.states.is_state(PLUG, "on")

        freezer.move_to(deadline + timedelta(milliseconds=1))
        async_fire_time_changed_exact(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        # Other HA timers can independently request a refresh. Observe the
        # actual task created by the point-in-time callback for this load.
        assert (
            sum(
                len(call.args) > 1 and call.args[1] == "battery_manager_subhour_off"
                for call in spawn.call_args_list
            )
            == 1
        )
        assert load_id not in c._load_off_timer
        if c._load_switch_task is not None:
            await c._load_switch_task
    assert hass.states.is_state(PLUG, "off")


@pytest.mark.parametrize(
    "stored", ["current", "different_binding", "stale_pv", "moved_soc"]
)
async def test_booked_late_on_recovers_runtime_after_reload(hass, freezer, stored):
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    previous, load_id, data = await _setup(
        hass, [], min_runtime_min=1, charge_enable=None
    )
    _detach_listener(previous)
    previous._load_actor_requests.clear()
    previous._load_run_deadline.clear()
    previous._floor_guard_active = False
    hass.states.async_set(PLUG, "off")

    async def accepted_without_feedback(call):
        pass

    hass.services.async_register("homeassistant", "turn_on", accepted_without_feedback)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        AsyncMock(return_value=False),
    ):
        await previous._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=0.5)]
        )
    payload = previous._persistent_payload()
    if stored != "current":
        booking = payload["load_actor_requests"][PLUG]["booking"]
        if stored == "different_binding":
            booking["binding"]["plug"] = "switch.replacement"
        elif stored == "stale_pv":
            booking["requires_pv_revalidation"] = True
        else:
            booking["coordinate"] = {"epoch": 1, "generation": 3, "phase": "tracking"}
    current = BatteryManagerCoordinator(hass, previous.entry)
    try:
        with patch.object(
            current._store, "async_load", AsyncMock(return_value=payload)
        ):
            await current.async_load_persistent_state()
        freezer.tick(timedelta(seconds=load_actuation.LOAD_CONFIRM_TIMEOUT_S + 5))
        hass.states.async_set(PLUG, "on")
        load_actuation.reconcile_feedback(current)
        edge = hass.states.get(PLUG).last_changed
        if stored != "current":
            if current.live_ac.coordinate_cleanup_task is not None:
                await current.live_ac.coordinate_cleanup_task
            assert hass.states.is_state(
                PLUG, "on" if stored == "different_binding" else "off"
            )
            assert not current._load_plug_owned.get(load_id, False)
            assert not current._load_charging_active.get(load_id, False)
            assert load_id not in current._load_run_deadline
            return
        assert current._load_plug_owned[load_id]
        assert current._load_charging_active[load_id]
        deadline = edge + timedelta(minutes=30)
        assert current._load_run_deadline.get(load_id) == deadline
        assert load_id in current._load_off_timer
        freezer.tick(timedelta(minutes=30))
        await current._apply_load_switching(_active_plan(load_id), deadline, (1.0,))
        if current._load_switch_task is not None:
            await current._load_switch_task
        assert hass.states.is_state(PLUG, "off")
    finally:
        await current.async_cancel_actuation_tasks()
        current.cleanup()


@pytest.mark.parametrize(
    "scenario",
    ["confirmed", "rollback", "revoked", "reload", "revoked_reload", "republished"],
)
@pytest.mark.parametrize("origin_type", ["own", "operator", "other_service"])
async def test_only_our_on_edge_can_acquire_load_input(
    hass, freezer, scenario, origin_type
):
    from types import SimpleNamespace

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from custom_components.battery_manager.load_safety import stop_unsafe_loads

    calls = []
    c, load_id, data = await _setup(hass, calls, min_runtime_min=1)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = "sensor.ownership_grid"
    hass.states.async_set("sensor.ownership_grid", "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    calls.clear()

    origin = None

    def reply_context():
        if origin_type == "operator":
            return Context(user_id="operator")
        if origin_type == "other_service":
            return Context(parent_id=Context().id)
        assert origin is not None
        return Context(parent_id=origin.id)

    async def accept(call):
        nonlocal origin
        entity = call.data["entity_id"]
        calls.append((call.service, entity))
        if entity == ENABLE:
            hass.states.async_set(entity, "on", context=call.context)
        else:
            origin = call.context
            if scenario == "confirmed":
                hass.states.async_set(entity, "on", context=reply_context())

    async def read_physical_state(_hass, entity, desired):
        return _hass.states.is_state(entity, "on" if desired else "off")

    hass.services.async_register("homeassistant", "turn_on", accept)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        read_physical_state,
    ):
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=0.5)]
        )
        if scenario in ("revoked", "revoked_reload", "republished"):
            hass.states.async_set(
                "sensor.ownership_grid", "2000", {"unit_of_measurement": "W"}
            )
            await asyncio.gather(*await stop_unsafe_loads(c))
            hass.states.async_set(
                "sensor.ownership_grid", "0", {"unit_of_measurement": "W"}
            )
        current = c
        if scenario in ("reload", "revoked_reload"):
            payload = c._persistent_payload()
            current = BatteryManagerCoordinator(hass, c.entry)
            with patch.object(
                current._store, "async_load", AsyncMock(return_value=payload)
            ):
                await current.async_load_persistent_state()
        try:
            if scenario != "confirmed":
                assert hass.states.is_state(ENABLE, "off")
                freezer.tick(timedelta(seconds=35))
                old_state = hass.states.get(PLUG)
                hass.states.async_set(PLUG, "on", context=reply_context())
                if scenario == "republished":
                    edge = hass.states.get(PLUG)
                    freezer.tick(timedelta(seconds=1))
                    hass.states.async_set(PLUG, "on", {"updated": True})
                    load_actuation.reconcile_feedback(
                        current,
                        SimpleNamespace(
                            data={
                                "entity_id": PLUG,
                                "old_state": old_state,
                                "new_state": edge,
                            }
                        ),
                    )
            await asyncio.gather(*await stop_unsafe_loads(current))
            if current.live_ac.coordinate_cleanup_task is not None:
                await current.live_ac.coordinate_cleanup_task
            if origin_type != "own":
                assert hass.states.is_state(PLUG, "on")
                assert hass.states.is_state(ENABLE, "off")
                assert ("turn_off", PLUG) not in calls
                assert not current._load_plug_owned.get(load_id, False)
                assert load_id not in current._load_run_deadline
            elif scenario == "confirmed":
                assert current._load_plug_owned[load_id]
                assert current._load_charging_active[load_id]
                assert load_id in current._load_run_deadline
            else:
                assert hass.states.is_state(PLUG, "off")
                assert ("turn_off", PLUG) in calls
                assert load_id not in current._load_run_deadline
        finally:
            if current is not c:
                await current.async_cancel_actuation_tasks()
                current.cleanup()


@pytest.mark.parametrize("scenario", ["confirmed", "late", "revoked"])
@pytest.mark.parametrize("reload", [False, True])
async def test_user_attribute_update_preserves_the_own_on_edge(
    hass, freezer, scenario, reload
):
    from types import SimpleNamespace

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from custom_components.battery_manager.load_safety import stop_unsafe_loads

    calls = []
    c, load_id, data = await _setup(hass, calls, min_runtime_min=1)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    c.raw_config["operation_import_power_entity"] = "sensor.ownership_grid"
    hass.states.async_set("sensor.ownership_grid", "0", {"unit_of_measurement": "W"})
    for entity in (ENABLE, PLUG):
        hass.states.async_set(entity, "off")
    origin = None

    async def switch(call):
        nonlocal origin
        entity = call.data["entity_id"]
        calls.append((call.service, entity))
        if call.service == "turn_on":
            if entity == PLUG:
                origin = call.context
                if scenario != "confirmed":
                    return
            hass.states.async_set(entity, "on", context=call.context)
        else:
            hass.states.async_set(entity, "off", context=call.context)

    async def read_physical_state(_hass, entity, desired):
        return _hass.states.is_state(entity, "on" if desired else "off")

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("homeassistant", service, switch)

    with patch(
        "custom_components.battery_manager.coordinator.confirm_state",
        read_physical_state,
    ):
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=0.5)]
        )
        if scenario == "revoked":
            hass.states.async_set(
                "sensor.ownership_grid", "2000", {"unit_of_measurement": "W"}
            )
            await asyncio.gather(*await stop_unsafe_loads(c))
            hass.states.async_set(
                "sensor.ownership_grid", "0", {"unit_of_measurement": "W"}
            )
        if scenario != "confirmed":
            old = hass.states.get(PLUG)
            freezer.tick(timedelta(seconds=35))
            hass.states.async_set(PLUG, "on", context=Context(parent_id=origin.id))
            on = hass.states.get(PLUG)
        else:
            old = on = hass.states.get(PLUG)
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(
            PLUG, "on", {"updated": True}, context=Context(user_id="operator")
        )
        attr = hass.states.get(PLUG)
        assert (
            attr.last_changed == on.last_changed and attr.last_updated > on.last_updated
        )
        # Keep the revoked device ON until the new coordinator can use the
        # stored edge. The state-machine snapshot now only has the attribute context.
        with patch.object(load_actuation, "_schedule_coordinate_cleanup"):
            if scenario != "confirmed":
                load_actuation.reconcile_feedback(
                    c,
                    SimpleNamespace(
                        data={"entity_id": PLUG, "old_state": old, "new_state": on}
                    ),
                )
            load_actuation.reconcile_feedback(
                c,
                SimpleNamespace(
                    data={"entity_id": PLUG, "old_state": on, "new_state": attr}
                ),
            )
        current = c
        try:
            if reload:
                current = BatteryManagerCoordinator(hass, c.entry)
                with patch.object(
                    current._store, "async_load", return_value=c._persistent_payload()
                ):
                    await current.async_load_persistent_state()
            load_actuation.reconcile_feedback(current)
            if scenario == "confirmed":
                assert current._load_plug_owned[load_id]
                await current._execute_load_switching(
                    [LoadAction(load_id, data, False, True)]
                )
            else:
                await asyncio.gather(*await stop_unsafe_loads(current))
                if current.live_ac.coordinate_cleanup_task is not None:
                    await current.live_ac.coordinate_cleanup_task
        finally:
            if current is not c:
                await current.async_cancel_actuation_tasks()
                current.cleanup()
    assert ("turn_off", PLUG) in calls
    assert hass.states.is_state(PLUG, "off")


@pytest.mark.parametrize("storage", ["live", "before_flush", "after_flush", "legacy"])
@pytest.mark.parametrize(
    "changed_actor", ["plug", "gate", "passthrough_gate", "attributes"]
)
async def test_only_the_confirmed_run_can_use_its_deadline_after_manual_edges(
    hass, freezer, storage, changed_actor
):
    import json

    from homeassistant.helpers.event import async_track_state_change_event

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from tests.ha.test_load_switching import POWER_FEEDBACK

    calls = []
    passthrough = changed_actor == "passthrough_gate"
    with_gate = changed_actor in ("gate", "passthrough_gate")
    c, load_id, data = await _setup(
        hass, calls, charge_enable=ENABLE if with_gate else None, min_runtime_min=1
    )
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = False
    hass.states.async_set(
        PLUG,
        "on" if passthrough else "off",
        context=Context(user_id="operator") if passthrough else None,
    )
    if with_gate:
        hass.states.async_set(ENABLE, "off")
    hass.states.async_set(POWER_FEEDBACK, "unavailable")
    calls.clear()

    async def switch(call):
        entity = call.data["entity_id"]
        calls.append((call.service, entity))
        hass.states.async_set(
            entity, "on" if call.service == "turn_on" else "off", context=call.context
        )

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("homeassistant", service, switch)
    c._listeners_setup = True
    c._unsub_state_listener = async_track_state_change_event(
        hass, [PLUG, ENABLE], c._handle_entity_change
    )
    current = c
    try:
        await c._execute_load_switching(
            [LoadAction(load_id, data, True, False, run_h=1.0)]
        )
        await hass.async_block_till_done()
        assert c._load_plug_owned.get(load_id, False) is not passthrough
        deadline = c._load_run_deadline[load_id]
        assert load_id in c._load_off_timer
        old_payload = json.loads(json.dumps(c._persistent_payload()))
        if storage == "legacy":
            old_payload.pop("load_on_edges", None)
        freezer.tick(timedelta(seconds=5))
        actor = ENABLE if with_gate else PLUG
        operator = Context(user_id="operator")
        if changed_actor == "attributes":
            hass.states.async_set(actor, "on", {"updated": True}, context=operator)
        else:
            hass.states.async_set(actor, "off", context=operator)
            freezer.tick(timedelta(seconds=1))
            hass.states.async_set(actor, "on", context=operator)
        await hass.async_block_till_done()
        payload = (
            old_payload
            if storage in ("after_flush", "legacy")
            else json.loads(json.dumps(c._persistent_payload()))
        )
        if storage != "live":
            _detach_listener(c)
            current = BatteryManagerCoordinator(hass, c.entry)
            with patch.object(current._store, "async_load", return_value=payload):
                await current.async_load_persistent_state()
        if changed_actor == "attributes":
            assert current._load_plug_owned[load_id]
            assert current._load_run_deadline[load_id] == deadline
        else:
            assert load_id not in current._load_run_deadline
            assert load_id not in current._load_off_timer
            assert not current._load_charging_active.get(load_id, False)
            if with_gate:
                assert current._load_plug_owned.get(load_id, False) is not passthrough
                assert hass.states.is_state(PLUG, "on")
            else:
                assert not current._load_plug_owned.get(load_id, False)

        if storage != "legacy" or changed_actor != "attributes":
            freezer.move_to(deadline + timedelta(seconds=1))
            await current._apply_load_switching(
                _active_plan(load_id), dt_util.now(), (1.0,)
            )
            if current._load_switch_task is not None:
                await current._load_switch_task
            assert hass.states.is_state(
                actor, "off" if changed_actor == "attributes" else "on"
            )
            assert (("turn_off", actor) in calls) is (changed_actor == "attributes")
    finally:
        if current is not c:
            await current.async_cancel_actuation_tasks()
            current.cleanup()
        if c._unsub_state_listener is not None:
            _detach_listener(c)
