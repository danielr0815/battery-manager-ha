"""0.64.0 manual authority overrides planner, protection and stale feedback."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.battery_manager.actor_ownership import (
    COLLISION_OWNER,
    SUPPLY_OWNER,
)
from custom_components.battery_manager.const import CONF_LOAD_CONTROL_SWITCH
from custom_components.battery_manager.load_actuation import (
    ActorRequest,
    LoadAction,
    reconcile_feedback,
)
from custom_components.battery_manager.load_safety import stop_unsafe_loads
from tests.ha.test_load_switching import ENABLE, PLUG, _setup


@pytest.mark.parametrize("initial", ["on", "off"])
async def test_disabled_device_is_untouched_by_guards_cleanup_and_all_owners(
    hass, initial
):
    calls = []
    output = "switch.manual_output"
    c, lid, data = await _setup(hass, calls, output_switch=output)
    c._listeners_setup = False
    for entity in (PLUG, ENABLE, output):
        hass.states.async_set(entity, initial)
    c._load_plug_owned[lid] = True
    c._load_plug_owned_entities[lid] = PLUG
    c._load_input_on_since[lid] = dt_util.now()
    deadline = dt_util.now() + timedelta(minutes=1)
    c._load_run_deadline[lid] = deadline
    c._arm_off_timer(lid, deadline)
    c._load_actor_requests[PLUG] = ActorRequest(
        True, dt_util.now(), owner="load:" + lid
    )
    calls.clear()
    c.set_load_enabled(lid, False)
    assert not c._load_plug_owned[lid]
    assert lid not in c._load_input_on_since
    assert lid not in c._load_run_deadline
    assert lid not in c._load_off_timer
    assert PLUG not in c._load_actor_requests
    c._floor_guard_active = True
    c._stale_shed_active = True
    await c._execute_load_switching([LoadAction(lid, data, False, True)])
    await stop_unsafe_loads(c)
    for entity in (PLUG, ENABLE, output):
        for owner in (None, "load:" + lid, COLLISION_OWNER, SUPPLY_OWNER):
            for desired in (False, True):
                assert not await c._switch_entity(entity, desired, actor_owner=owner)
        assert hass.states.is_state(entity, initial)
    assert calls == []
    assert not c.power_calibration_button_available(lid)
    # Manual charging while released is also protected from late reconciliation.
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    reconcile_feedback(c)
    assert not c._load_plug_owned[lid]
    assert PLUG not in c._load_actor_requests
    assert calls == []


async def test_restart_revokes_legacy_requests_and_preserves_old_wiring(hass):
    calls = []
    c, lid, data = await _setup(hass, calls)
    c._listeners_setup = False
    hass.states.async_set(PLUG, "on")
    c.set_load_enabled(lid, False)
    payload = deepcopy(c._persistent_payload())
    # Legacy OFF stores may still contain input ownership and pending ON.
    payload["plug_owned"][lid] = True
    payload["plug_owned_entities"][lid] = PLUG
    payload["load_run_deadline"][lid] = dt_util.now().isoformat()
    payload["load_actor_requests"][PLUG] = {
        "desired": True,
        "requested_at": dt_util.now().isoformat(),
        "owner": "load:" + lid,
    }
    c._load_bm_enabled.clear()
    c._load_released_actors.clear()
    with patch.object(c._store, "async_load", AsyncMock(return_value=payload)):
        await c.async_load_persistent_state()
    assert not c.load_bm_enabled(lid)
    assert not c._load_plug_owned[lid]
    assert lid not in c._load_run_deadline
    assert PLUG not in c._load_actor_requests
    sub = c.entry.subentries[lid]
    c.hass.config_entries.async_update_subentry(
        c.entry, sub, data={**data, CONF_LOAD_CONTROL_SWITCH: "switch.new_manual_input"}
    )
    calls.clear()
    assert not await c._switch_entity(PLUG, False, actor_owner=COLLISION_OWNER)
    assert not await c._switch_entity(
        "switch.new_manual_input", True, actor_owner="load:" + lid
    )
    assert calls == []
    assert PLUG in c._persistent_payload()["load_released_actors"][lid]


async def test_off_on_during_confirmation_cannot_restore_old_input_ownership(hass):
    calls = []
    c, lid, data = await _setup(hass, calls)
    c._listeners_setup = False
    c._floor_guard_active = False
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(PLUG, "off")
    requested = asyncio.Event()

    async def acknowledge(call):
        requested.set()

    hass.services.async_register("homeassistant", "turn_on", acknowledge)
    task = asyncio.create_task(
        c._execute_load_switching([LoadAction(lid, data, True, False, run_h=1)])
    )
    await requested.wait()
    c.set_load_enabled(lid, False)
    c.set_load_enabled(lid, True)
    calls.clear()
    hass.states.async_set(PLUG, "on")
    await task
    reconcile_feedback(c)
    assert not c._load_plug_owned[lid]
    assert PLUG not in c._load_actor_requests
    assert lid not in c._load_run_deadline
    assert calls == []
    assert hass.states.is_state(ENABLE, "on")


async def test_release_aborts_calibration_without_cleanup_even_after_resume(hass):
    """Revocation invalidates the old probe's finally-block release obligation."""
    calls = []
    c, lid, data = await _setup(hass, calls)
    c._listeners_setup = False
    started = asyncio.Event()

    async def in_flight_probe(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    with (
        patch.object(
            c, "_execute_load_switching", side_effect=in_flight_probe
        ) as command,
        patch.object(c, "async_request_refresh", AsyncMock()) as refresh,
    ):
        c._load_power_calibration_id = lid
        task = asyncio.create_task(c._run_load_power_calibration(lid, data, 0))
        c._load_power_calibration_task = task
        await started.wait()
        c.set_load_enabled(lid, False)
        c.set_load_enabled(lid, True)
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert command.call_count == 1
    refresh.assert_not_awaited()
    assert c._load_power_calibration_id is None
    assert c._load_power_calibration_restored is None
    assert lid not in c._load_power_calibration_release


@pytest.mark.parametrize("boundary", ["flush", "task_start"])
async def test_calibration_start_off_on_cannot_use_old_permission(hass, boundary):
    """Permission belongs to the button invocation, before persistence yields."""
    calls = []
    c, lid, _ = await _setup(hass, calls)
    c._listeners_setup = False
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    entered, resume = asyncio.Event(), asyncio.Event()

    async def flush():
        entered.set()
        await resume.wait()

    if boundary == "flush":
        with (
            patch.object(c, "async_flush_persistent_state", side_effect=flush),
            patch.object(c, "_read_power_w", return_value=0),
        ):
            running = asyncio.create_task(c.async_toggle_load_power_calibration(lid))
            await entered.wait()
            c.set_load_enabled(lid, False)
            c.set_load_enabled(lid, True)
            calls.clear()
            resume.set()
            await running
        assert c._load_power_calibration_task is None
    else:
        generation = c._load_control_generation.get(lid, 0)
        c.set_load_enabled(lid, False)
        c.set_load_enabled(lid, True)
        calls.clear()
        await c._run_load_power_calibration(
            lid, dict(c.entry.subentries[lid].data), 0, control_generation=generation
        )
    assert calls == []
    assert c._load_power_calibration_id is None
    assert c._load_power_calibration_restored is None
    assert lid not in c._load_power_calibration_release


@pytest.mark.parametrize("activate", [False, True])
async def test_queued_coroutine_not_started_before_off_on_retains_old_permission(
    hass, activate
):
    """Queue creation captures the epoch before asyncio runs the coroutine body."""
    calls = []
    c, lid, data = await _setup(hass, calls)
    c._listeners_setup = False
    c._floor_guard_active = False
    initial = "off" if activate else "on"
    hass.states.async_set(PLUG, initial)
    hass.states.async_set(ENABLE, initial)
    c._load_plug_owned[lid] = not activate
    # Calling the production coroutine factory is the same boundary used by
    # planning, fast safety, calibration and detached background tasks.
    invocation = c._execute_load_switching(
        [LoadAction(lid, data, activate, not activate, run_h=1)]
    )
    started = asyncio.Event()

    async def start_later():
        await started.wait()
        await invocation

    running = asyncio.create_task(start_later())
    c.set_load_enabled(lid, False)
    c.set_load_enabled(lid, True)
    calls.clear()
    started.set()
    await running
    assert calls == []
    assert not c._load_plug_owned[lid]
    assert lid not in c._load_run_deadline
    assert PLUG not in c._load_actor_requests
    assert hass.states.is_state(PLUG, initial)
    assert hass.states.is_state(ENABLE, initial)


async def _persisted_revoked_input(hass, calls):
    from test_load_switching import _detach_listener

    c, lid, data = await _setup(hass, calls, charge_enable=None)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = c._stale_shed_active = False
    c.raw_config["operation_import_power_entity"] = "sensor.clear_grid"
    hass.states.async_set("sensor.clear_grid", "0", {"unit_of_measurement": "W"})
    hass.states.async_set(PLUG, "off")
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())

    async def accept(call):
        calls.append(("turn_on", call.data["entity_id"]))

    async def no_feedback(_hass, _entity, desired):
        if not desired:
            return _hass.states.is_state(_entity, "off")
        c.voltage_gate.monitor.phase = "settling"
        c.voltage_gate.monitor.has_reconciled = True
        c.voltage_gate.monitor.generation += 1
        return False

    hass.services.async_register("homeassistant", "turn_on", accept)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state", no_feedback
    ):
        await c._execute_load_switching(
            [
                LoadAction(
                    lid,
                    data,
                    True,
                    False,
                    soc_coordinate=stamp,
                    coordinate_captured=True,
                )
            ]
        )
    payload = deepcopy(c._persistent_payload())
    assert payload["load_actor_requests"][PLUG]["sent"] is True
    assert payload["revoked_load_starts"]["items"][lid]["sent"] is True
    assert payload["revoked_load_starts"]["items"][lid]["binding"]["plug"] == PLUG
    return c, lid, payload


def _forget_runtime_cleanup(c):
    c.live_ac.revoked_load_starts.clear()
    c.live_ac._pv_start_reservations.clear()
    c._load_actor_requests.clear()
    c._load_control_generation.clear()
    c._load_charging_active.clear()
    c._load_plug_owned.clear()
    c._load_plug_owned_entities.clear()
    c._load_input_on_since.clear()
    c._last_load_switch.clear()


@pytest.mark.parametrize("arrival", ["surviving_on", "after_restore"])
async def test_revoked_own_input_survives_real_persistence_restore_without_dwell(
    hass, freezer, arrival
):
    calls = []
    c, lid, payload = await _persisted_revoked_input(hass, calls)
    requested = payload["revoked_load_starts"]["items"][lid]["requested_at"]
    payload["last_load_switch"][lid] = requested
    payload["load_run_deadline"][lid] = (
        dt_util.now() + timedelta(minutes=10)
    ).isoformat()
    _forget_runtime_cleanup(c)
    calls.clear()
    freezer.tick(timedelta(seconds=5))
    if arrival == "surviving_on":
        hass.states.async_set(PLUG, "on")
    with patch.object(c._store, "async_load", AsyncMock(return_value=payload)):
        await c.async_load_persistent_state()
    assert lid in c.live_ac.revoked_load_starts
    assert "load:" + lid in c.live_ac._pv_start_reservations
    assert not c._load_charging_active[lid]
    assert lid not in c._load_run_deadline
    assert lid not in c._last_load_switch
    if arrival == "after_restore":
        assert not c._load_plug_owned.get(lid, False)
        freezer.tick(timedelta(seconds=5))
        hass.states.async_set(PLUG, "on")
    reconcile_feedback(c)
    if c.live_ac.coordinate_cleanup_task is not None:
        await c.live_ac.coordinate_cleanup_task
    assert hass.states.is_state(PLUG, "off")
    assert ("turn_off", PLUG) in calls
    assert not c._load_plug_owned.get(lid, False)
    assert not c._load_charging_active[lid]
    assert lid not in c._last_load_switch


@pytest.mark.parametrize(
    "invalid",
    [
        "legacy",
        "sent",
        "generation",
        "disabled",
        "binding",
        "policy",
        "version",
        "naive_time",
        "invalid_time",
        "invalid_policy",
        "invalid_actors",
    ],
)
async def test_restore_does_not_claim_unproved_legacy_or_changed_manual_binding(
    hass, invalid
):
    calls = []
    c, lid, payload = await _persisted_revoked_input(hass, calls)
    item = payload["revoked_load_starts"]["items"][lid]
    if invalid == "legacy":
        payload.pop("revoked_load_starts")
        payload["load_actor_requests"][PLUG].pop("sent")
    elif invalid == "sent":
        item["sent"] = False
    elif invalid == "generation":
        payload["load_control_generation"][lid] = item["control_generation"] + 1
    elif invalid == "disabled":
        payload["load_bm_enabled"][lid] = False
    elif invalid == "binding":
        item["binding"]["plug"] = "switch.foreign"
    elif invalid == "policy":
        item["binding"]["input_off_policy"] = "keep_on"
    elif invalid == "version":
        payload["revoked_load_starts"]["version"] = 99
    elif invalid == "invalid_time":
        item["requested_at"] = "invalid"
    elif invalid == "invalid_policy":
        item["binding"]["input_off_policy"] = "unknown"
    elif invalid == "invalid_actors":
        item["actors"] = [PLUG, PLUG]
    else:
        item["requested_at"] = "2026-02-03T12:00:00"
    _forget_runtime_cleanup(c)
    hass.states.async_set(PLUG, "on")
    calls.clear()
    with patch.object(c._store, "async_load", AsyncMock(return_value=payload)):
        await c.async_load_persistent_state()
    reconcile_feedback(c)
    await asyncio.gather(*await stop_unsafe_loads(c))
    assert lid not in c.live_ac.revoked_load_starts
    assert hass.states.is_state(PLUG, "on")
    assert calls == []
    assert not c._load_plug_owned.get(lid, False)
    assert not c._load_charging_active.get(lid, False)


async def test_inflight_listener_never_adopts_revoked_input_as_minimum_dwell(hass):
    from test_load_switching import _detach_listener

    calls = []
    c, lid, data = await _setup(hass, calls, charge_enable=None)
    _detach_listener(c)
    c._load_actor_requests.clear()
    c._floor_guard_active = c._stale_shed_active = False
    hass.states.async_set(PLUG, "off")
    before = c._last_load_switch.get(lid)
    stamp = c.voltage_gate.coordinate_snapshot(dt_util.now())
    waiting, finish = asyncio.Event(), asyncio.Event()

    async def accepted(call):
        calls.append(("turn_on", call.data["entity_id"]))

    async def confirmation(_hass, entity, desired):
        if not desired:
            return _hass.states.is_state(entity, "off")
        waiting.set()
        await finish.wait()
        return _hass.states.is_state(entity, "on")

    hass.services.async_register("homeassistant", "turn_on", accepted)
    with patch(
        "custom_components.battery_manager.coordinator.confirm_state", confirmation
    ):
        running = asyncio.create_task(
            c._execute_load_switching(
                [
                    LoadAction(
                        lid,
                        data,
                        True,
                        False,
                        soc_coordinate=stamp,
                        coordinate_captured=True,
                    )
                ]
            )
        )
        c._load_switch_task = running
        await waiting.wait()
        try:
            c.voltage_gate.monitor.phase = "settling"
            c.voltage_gate.monitor.has_reconciled = True
            c.voltage_gate.monitor.generation += 1
            hass.states.async_set(PLUG, "on")
            reconcile_feedback(c)
            assert not c._load_charging_active.get(lid, False)
            assert c._last_load_switch.get(lid) == before
            finish.set()
            await running
            if c.live_ac.coordinate_cleanup_task is not None:
                await c.live_ac.coordinate_cleanup_task
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    assert hass.states.is_state(PLUG, "off")
    assert not c._load_charging_active.get(lid, False)
    assert c._last_load_switch.get(lid) == before


async def test_reload_releases_deleted_load_binding_for_replacement(hass):
    from types import MappingProxyType

    from homeassistant.config_entries import ConfigSubentry
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.battery_manager.const import SUBENTRY_TYPE_LOAD
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    calls = []
    previous, old_id, data = await _setup(hass, calls)
    previous.set_load_enabled(old_id, False)
    prior = deepcopy(previous._persistent_payload())
    assert PLUG in previous.released_load_actors()

    replacement = ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=SUBENTRY_TYPE_LOAD,
        title="Replacement",
        unique_id=None,
    )
    entry = MockConfigEntry(
        domain="battery_manager",
        data=previous.entry.data,
        subentries_data=[replacement.as_dict()],
    )
    entry.add_to_hass(hass)
    current = BatteryManagerCoordinator(hass, entry)
    try:
        with patch.object(current._store, "async_load", AsyncMock(return_value=prior)):
            await current.async_load_persistent_state()
        new_id = next(iter(entry.subentries))
        assert new_id != old_id and old_id not in entry.subentries
        assert current.load_bm_enabled(new_id)
        assert PLUG not in current.released_load_actors()
        assert old_id not in current._persistent_payload()["load_released_actors"]
        calls.clear()
        hass.states.async_set(PLUG, "off")
        assert await current._switch_entity(PLUG, True, actor_owner="load:" + new_id)
        assert calls == [("turn_on", PLUG)]
    finally:
        await current.async_cancel_actuation_tasks()
        current.cleanup()
