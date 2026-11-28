"""Explicit, per-load reachability and the physical boundary of its stop."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from test_load_switching import (
    ENABLE,
    PLUG,
    POWER_FEEDBACK,
    _active_plan,
    _detach_listener,
    _setup,
)

WITNESS = "sensor.cellar_socket_power"


async def test_independent_meter_and_optional_power_feedback(hass):
    c, load_id, _ = await _setup(
        hass, [], availability_entity=WITNESS, with_control_switch=False
    )
    _detach_listener(c)
    plan = _active_plan(load_id).load_plans[0]
    for state, expected in (
        (None, False),
        ("off", False),
        ("unavailable", False),
        ("unknown", False),
        ("0", True),
        ("382.1", True),
        ("on", True),
    ):
        if state is not None:
            hass.states.async_set(WITNESS, state)
        assert c._get_load_states()[0].available is expected
        assert c._effective_load_active(plan, dt_util.now()) is expected


async def test_power_feedback_opt_in_requires_usable_reading(hass):
    c, load_id, _ = await _setup(
        hass, [], availability_entity=WITNESS, require_power_feedback=True
    )
    _detach_listener(c)
    hass.states.async_set(WITNESS, "on")
    for reading, available in (
        (None, False),
        ("unknown", False),
        ("unavailable", False),
        ("not a number", False),
        ("-300", False),
        ("0", True),
        ("211", True),
    ):
        if reading is not None:
            hass.states.async_set(POWER_FEEDBACK, reading)
        assert c._get_load_states()[0].available is available
    for attribute in ("assumed_state", "restored"):
        hass.states.async_set(POWER_FEEDBACK, "0", {attribute: True})
        assert not c._get_load_states()[0].available
    hass.states.async_set(POWER_FEEDBACK, "0")
    hass.states.async_set(WITNESS, "off")
    assert not c._get_load_states()[0].available
    assert not c._effective_load_active(
        _active_plan(load_id).load_plans[0], dt_util.now()
    )


async def test_pending_on_dropped_and_foreign_or_unreachable_path_untouched(hass):
    calls = []
    c, load_id, data = await _setup(
        hass, calls, availability_entity=WITNESS, charge_enable=None
    )
    _detach_listener(c)
    calls.clear()
    hass.states.async_set(WITNESS, "0")
    hass.states.async_set(PLUG, "off")
    queued = c._execute_load_switching([(load_id, data, True, False)])
    hass.states.async_set(WITNESS, "unavailable")
    await queued
    for state in ("on", "unavailable"):
        hass.states.async_set(PLUG, state)
        c._load_charging_active[load_id] = True
        await c._apply_load_switching(_active_plan(load_id), dt_util.now())
    assert calls == []
    assert not c._load_plug_owned.get(load_id)


@pytest.mark.parametrize("availability_policy", [True, False])
async def test_existing_foreign_gate_does_not_start_availability_guarded_input(
    hass, availability_policy
):
    calls = []
    c, load_id, data = await _setup(
        hass, calls, availability_entity=WITNESS if availability_policy else None
    )
    _detach_listener(c)
    c._floor_guard_active = False
    hass.states.async_set(WITNESS, "0")
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    hass.states.async_set(ENABLE, "on", context=Context(user_id="operator"))
    calls.clear()

    await c._execute_load_switching([(load_id, data, True, False, 1.0)])

    assert calls == ([] if availability_policy else [("turn_on", PLUG)])
    assert (load_id in c._load_run_deadline) is not availability_policy
    assert hass.states.is_state(ENABLE, "on")


async def test_foreign_gate_edge_at_input_dispatch_never_sends_input_on(hass):
    calls = []
    c, load_id, data = await _setup(hass, calls, availability_entity=WITNESS)
    _detach_listener(c)
    c._floor_guard_active = False
    hass.states.async_set(WITNESS, "0")
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    calls.clear()
    switch_actor = c._switch_load_entity

    async def switch_with_gate_handoff(entity, desired, **kwargs):
        if entity == PLUG and desired:
            hass.states.async_set(ENABLE, "off")
            hass.states.async_set(ENABLE, "on", context=Context(user_id="operator"))
        return await switch_actor(entity, desired, **kwargs)

    with patch.object(c, "_switch_load_entity", side_effect=switch_with_gate_handoff):
        await c._execute_load_switching([(load_id, data, True, False, 1.0)])

    assert calls == [("turn_on", ENABLE)]
    assert hass.states.is_state(PLUG, "off")
    assert hass.states.is_state(ENABLE, "on")
    assert load_id not in c._load_run_deadline


@pytest.mark.parametrize("external_input", [True, False])
async def test_availability_loss_stops_owned_run_and_preserves_foreign_input(
    hass, external_input
):
    calls = []
    c, load_id, data = await _setup(
        hass,
        calls,
        availability_entity=WITNESS,
        input_power=WITNESS,
        min_runtime_min=30,
    )
    _detach_listener(c)
    c._floor_guard_active = False
    hass.states.async_set(WITNESS, "0")
    before = hass.states.get(WITNESS)
    hass.states.async_set(ENABLE, "off")
    hass.states.async_set(PLUG, "on" if external_input else "off")
    await c._execute_load_switching([(load_id, data, True, external_input)])
    assert hass.states.is_state(ENABLE, "on")
    c.data["load_plans"][load_id].update(available=True, active=True)
    calls.clear()
    hass.states.async_remove(WITNESS)
    event = SimpleNamespace(
        data={"entity_id": WITNESS, "old_state": before, "new_state": None}
    )
    c._listeners_setup = True
    with patch.object(c, "_schedule_replan") as replan:
        c._handle_entity_change(event)
    replan.assert_called_once()
    await c._load_safety_tasks[load_id]
    assert not c.data["load_plans"][load_id]["active"]
    assert not c.data["load_plans"][load_id]["available"]
    assert calls == (
        [("turn_off", ENABLE)]
        if external_input
        else [("turn_off", ENABLE), ("turn_off", PLUG)]
    )
    assert not c._load_last_off[load_id][1]
    assert hass.states.is_state(PLUG, "on" if external_input else "off")


async def test_cascade_planner_only_blocks_unavailable_suffix(hass, request):
    from homeassistant.config_entries import ConfigSubentry
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.battery_manager.const import (
        CONF_CASCADE_MEMBER_IDS,
        CONF_CASCADE_TERMINAL_LOAD_ID,
        CONF_LOAD_AVAILABILITY_ENTITY,
        CONF_LOAD_POWER_W,
        DOMAIN,
        SUBENTRY_TYPE_CASCADE,
        SUBENTRY_TYPE_LOAD,
    )
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Battery Manager", version=2)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    request.addfinalizer(c.cleanup)
    first = "b1"
    second_witness = "sensor.second_member"
    terminal_witness = "sensor.terminal"
    for load_id, witness in (
        (first, WITNESS),
        ("b2", second_witness),
        ("leaf", terminal_witness),
    ):
        hass.config_entries.async_add_subentry(
            c.entry,
            ConfigSubentry(
                subentry_id=load_id,
                title=load_id,
                subentry_type=SUBENTRY_TYPE_LOAD,
                data={CONF_LOAD_AVAILABILITY_ENTITY: witness, CONF_LOAD_POWER_W: 100},
                unique_id=None,
            ),
        )
    hass.config_entries.async_add_subentry(
        c.entry,
        ConfigSubentry(
            subentry_id="chain",
            title="chain",
            subentry_type=SUBENTRY_TYPE_CASCADE,
            data={
                CONF_CASCADE_MEMBER_IDS: [first, "b2"],
                CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
            },
            unique_id=None,
        ),
    )
    c.cascade_manager._state("chain")["enabled"] = True
    for witness in (WITNESS, second_witness, terminal_witness):
        hass.states.async_set(witness, "0")
    for witness, expected in (
        (terminal_witness, {first, "b2"}),
        (second_witness, {first}),
        (WITNESS, set()),
    ):
        hass.states.async_set(witness, "unavailable")
        assert {s.load_id for s in c._get_load_states() if s.available} == expected
        hass.states.async_set(witness, "0")
