"""Confirmed economic phases survive rolling plans and reloads, never failures."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_week_review_regressions import configure_reserve

from custom_components.battery_manager.actor_ownership import SUPPLY_OWNER
from custom_components.battery_manager.coordinated_supply import (
    reconcile_supply_feedback,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.source_phases import (
    ECONOMIC_PHASE_SECONDS,
    SourcePhases,
)


def use_real_switch(c, hass):
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)

    async def switch(call):
        hass.states.async_set(
            call.data["entity_id"], "on" if call.service == "turn_on" else "off"
        )

    hass.services.async_register("homeassistant", "turn_on", switch)
    hass.services.async_register("homeassistant", "turn_off", switch)


async def test_confirmed_psu_phases_are_independent_and_not_renewed_by_replanning(
    rig, hass, freezer
):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    use_real_switch(c, hass)
    start = dt_util.utcnow()
    await c._switch_entity(PSU24, True, actor_owner=SUPPLY_OWNER)
    hass.states.async_set(DCDC, "off")
    reconcile_supply_feedback(c)
    phase24 = start.timestamp() + ECONOMIC_PHASE_SECONDS
    assert c.build_system_config().support.dc24_phase_until == phase24
    freezer.tick(timedelta(minutes=4))
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    phase48 = dt_util.utcnow().timestamp() + ECONOMIC_PHASE_SECONDS
    for _ in range(3):
        await c._switch_entity(PSU24, True, actor_owner=SUPPLY_OWNER)
        config = c.build_system_config()
        assert config.support.dc24_phase_until == phase24
        assert config.support.dc48_phase_until == phase48
    freezer.tick(timedelta(minutes=26))
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, phase48)
    c._support_manual["dc48"] = True
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, 0)


async def test_late_confirmation_starts_phase_only_after_observed_edge(
    rig, hass, freezer
):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)

    async def delayed(_call):
        pass

    hass.services.async_register("homeassistant", "turn_on", delayed)
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, 0)
    freezer.tick(timedelta(minutes=2))
    hass.states.async_set(PSU48, "on")
    assert reconcile_supply_feedback(c, PSU48)
    assert (
        c.source_phases.deadlines(dt_util.utcnow())[1]
        == dt_util.utcnow().timestamp() + ECONOMIC_PHASE_SECONDS
    )
    assert not reconcile_supply_feedback(c, PSU48)
    context = c.operation_recorder.command_context()
    assert context["source_phases"]["dc48"]["enabled"]
    assert "coordinated_support" in context


async def test_source_phase_restore_retains_deadline_and_invalidates_changed_actor(
    rig, hass, freezer
):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    use_real_switch(c, hass)
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    before = c.source_phases.deadlines(dt_util.utcnow())
    payload = c._persistent_payload()
    assert payload["source_phases"]["schema_version"] == 1
    freezer.tick(timedelta(minutes=10))
    c.source_phases = SourcePhases(c)
    c._store.async_load = AsyncMock(return_value=payload)
    await c.async_load_persistent_state()
    assert c.source_phases.deadlines(dt_util.utcnow()) == before
    hass.states.async_set(PSU48, "unknown")
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, 0)
    hass.states.async_set(PSU48, "off")
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, 0)
    c.raw_config["support_dc48_switch_entity"] = "switch.other"
    c.source_phases.restore(payload["source_phases"], dt_util.utcnow())
    assert c.source_phases.export()["sources"] == {}


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"schema_version": 2},
        {"schema_version": 1, "sources": []},
        {"schema_version": 1, "sources": {"dc48": "bad"}},
        {
            "schema_version": 1,
            "sources": {"dc48": {"enabled": True, "confirmed_at": "bad"}},
        },
    ],
)
async def test_corrupt_and_legacy_phase_payloads_leave_control_available(rig, value):
    c, _, _, _ = rig
    c.source_phases.restore(value, dt_util.utcnow())
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, 0)
    assert c.source_phases.export()["sources"] == {}


@pytest.mark.parametrize(
    "invalid", ["naive", "future", "expired", "state", "timestamp"]
)
async def test_invalid_phase_evidence_cannot_bind_a_new_plan(rig, hass, invalid):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    use_real_switch(c, hass)
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    payload = c.source_phases.export()
    source = payload["sources"]["dc48"]
    now = dt_util.utcnow()
    if invalid == "naive":
        source["confirmed_at"] = now.replace(tzinfo=None).isoformat()
    elif invalid == "future":
        source["confirmed_at"] = (now + timedelta(seconds=1)).isoformat()
    elif invalid == "expired":
        source["confirmed_at"] = (
            now - timedelta(seconds=ECONOMIC_PHASE_SECONDS)
        ).isoformat()
    elif invalid == "state":
        source["enabled"] = "on"
    else:
        source["confirmed_at"] = 42
    c.source_phases.restore(payload, now)
    assert c.source_phases.deadlines(now) == (0, 0)


async def test_plan_captured_before_a_new_confirmation_cannot_reverse_it(rig, hass):
    c, calls, _, _ = rig
    configure_reserve(c, hass, 80)
    use_real_switch(c, hass)
    stale = c.build_system_config()
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    c._schedule_replan = Mock()
    calls.clear()
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, False, stale, dt_util.now()
    )
    assert hass.states.is_state(PSU48, "on")
    assert c._coordinated_support_diag["reason"] == "source_phase_changed"
    c._schedule_replan.assert_called_once()
    assert not c._inverter_recommendation


async def test_phase_duration_is_an_economic_planning_contract():
    assert ECONOMIC_PHASE_SECONDS == 1800


async def test_late_phase_during_an_await_cannot_be_undone_by_old_envelope(
    rig, hass, freezer
):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    c._switch_entity = BatteryManagerCoordinator._switch_entity.__get__(c)

    async def pending(_call):
        pass

    hass.services.async_register("homeassistant", "turn_on", pending)
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    captured = c.build_system_config()

    async def late_confirmation(call):
        hass.states.async_set(LIMIT, str(call.data["value"]))
        hass.states.async_set(BLOCK, "on")
        hass.states.async_set(PSU48, "on")
        reconcile_supply_feedback(c, PSU48)
        freezer.tick(timedelta(seconds=65))
        configure_reserve(c, hass, 80)

    hass.services.async_register("number", "set_value", late_confirmation)
    c._schedule_replan = Mock()
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, False, captured, dt_util.now()
    )
    assert hass.states.is_state(PSU48, "on")
    assert c._coordinated_support_diag["reason"] == "source_phase_changed"
    c._schedule_replan.assert_called_once()
    assert not c._inverter_recommendation


async def test_another_psus_new_phase_cannot_delay_low_soc_protection(rig, hass):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    use_real_switch(c, hass)
    captured = c.build_system_config()
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    configure_reserve(c, hass, 9)
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, False, captured, dt_util.now()
    )
    assert hass.states.is_state(PSU24, "on")
    assert hass.states.is_state(DCDC, "off")
    assert not c._inverter_recommendation


@pytest.mark.parametrize("release", ["voltage", "pv", "manual"])
async def test_non_economic_release_cannot_delay_voltage_or_cloud_recovery(
    rig, hass, release
):
    c, _, _, _ = rig
    configure_reserve(c, hass, 80)
    use_real_switch(c, hass)
    await c._switch_entity(PSU48, True, actor_owner=SUPPLY_OWNER)
    assert c.source_phases.deadlines(dt_util.utcnow())[1] > 0
    c._coordinated_support_diag = {"requested": {"dc48": True}}
    if release == "pv":
        c._coordinated_support_diag = {
            "requested": {"dc48": False},
            "pv_priority": True,
        }
    elif release == "manual":
        c._support_manual["dc48"] = True
    await c._switch_entity(PSU48, False, actor_owner=SUPPLY_OWNER)
    assert c.source_phases.deadlines(dt_util.utcnow()) == (0, 0)
    assert c.source_phases.export()["sources"] == {}
