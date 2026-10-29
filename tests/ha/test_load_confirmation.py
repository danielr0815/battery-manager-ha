"""Physical actor feedback and safety races; no production delays elapse."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.battery_manager import load_actuation
from custom_components.battery_manager.load_actuation import LoadAction
from tests.ha.test_load_switching import ENABLE, PLUG, _setup


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
    await coordinator._pause_tasks[load_id]
    assert not any(service == "turn_on" for service, _ in calls)
    assert hass.states.is_state(PLUG, "off")


async def test_pause_stops_without_a_planner_refresh(hass):
    calls = []
    coordinator, load_id, data = await _setup(hass, calls)
    coordinator._last_load_switch[load_id] = dt_util.now() - timedelta(hours=1)
    coordinator._load_plug_owned[load_id] = True
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        coordinator.set_load_enabled(load_id, False)
        await coordinator._pause_tasks[load_id]
    assert hass.states.is_state(PLUG, "off")
    assert hass.states.is_state(ENABLE, "off")


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


async def test_late_on_feedback_after_pause_is_immediately_stopped(hass):
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
    coordinator._load_bm_enabled[load_id] = False
    hass.states.async_set(PLUG, "on")
    await task
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")
    assert not coordinator._load_charging_active.get(load_id, False)


async def test_pause_honours_remaining_runtime_and_resume_cancels_stop(hass):
    calls = []
    coordinator, load_id, _ = await _setup(hass, calls, min_runtime_min=30)
    coordinator._floor_guard_active = False
    coordinator._stale_shed_active = False
    now = dt_util.now()
    coordinator._last_load_switch[load_id] = now - timedelta(minutes=10)
    coordinator._load_plug_owned[load_id] = True
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    waiting = asyncio.Event()
    resume_clock = asyncio.Event()

    async def controlled_sleep(seconds):
        assert seconds == 20 * 60
        waiting.set()
        await resume_clock.wait()

    with (
        patch.object(dt_util, "now", return_value=now),
        patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            side_effect=controlled_sleep,
        ),
    ):
        coordinator.set_load_enabled(load_id, False)
        task = coordinator._pause_tasks[load_id]
        await waiting.wait()
        assert hass.states.is_state(PLUG, "on")
        coordinator.set_load_enabled(load_id, True)
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert hass.states.is_state(PLUG, "on")


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
    coordinator._load_bm_enabled[load_id] = False
    hass.states.async_set(ENABLE, "on")
    await task
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")


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


async def test_on_arriving_after_timeout_retains_input_ownership_for_pause(hass):
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
    coordinator._load_bm_enabled[load_id] = False
    hass.states.async_set(PLUG, "on")
    await coordinator._async_pause_load(load_id)
    assert hass.states.is_state(PLUG, "off")
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
        await coordinator._pause_tasks[load_id]
        await coordinator._async_pause_load(load_id)
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
        await stop_unsafe_loads(coordinator)
        if coordinator._load_switch_task is not None:
            await coordinator._load_switch_task
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
