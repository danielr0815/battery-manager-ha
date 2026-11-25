"""Historical reserve references, forecast control and confirmed source transfers."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48, execute
from test_coordinated_actuation import rig as rig

from custom_components.battery_manager.actor_ownership import SUPPLY_OWNER
from custom_components.battery_manager.config_flow import _validate_support_entities
from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_RESERVE_GRID_ENTITY,
    CONF_RESERVE_MODE,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
)
from custom_components.battery_manager.coordinated_supply import (
    reconcile_supply_feedback,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.reserve_runtime import (
    MAX_OBSERVATION_GAP_SECONDS,
    ReserveRuntime,
)

START = datetime(2026, 9, 26, tzinfo=UTC)


def observe(r, seconds, soc=80, solar=True, preparing=False, signature="plant"):
    r.observe(
        START + timedelta(seconds=seconds),
        soc,
        solar_only=solar,
        preparing=preparing,
        signature=signature,
    )


def test_observation_time_is_diagnostic_and_excludes_outages():
    r = ReserveRuntime()
    observe(r, 0)
    observe(r, MAX_OBSERVATION_GAP_SECONDS)
    assert r.observed_seconds == MAX_OBSERVATION_GAP_SECONDS
    r.interrupt()
    observe(r, 48 * 3600)
    assert r.observed_seconds == MAX_OBSERVATION_GAP_SECONDS
    observe(r, 48 * 3600 + 10, signature="replacement_battery")
    assert r.observed_seconds == 0
    assert r.hold_soc == 80


def test_intent_does_not_follow_involuntary_loss_or_grid_charge():
    r = ReserveRuntime()
    observe(r, 0, 80)
    observe(r, 300, 79)
    assert r.hold_soc == 80
    observe(r, 600, 78, preparing=True)
    assert r.hold_soc == 79
    observe(r, 900, 80, solar=False)
    observe(r, 1200, 82, solar=True)
    assert r.hold_soc == 79
    observe(r, 1500, 83, solar=True)
    assert r.hold_soc == 80
    # Neither downtime nor a backwards clock may award observed time/energy.
    before = r.export()
    observe(r, 7200, 90)
    observe(r, 7000, 90)
    assert r.export() == before


def test_restart_restores_intent_without_counting_downtime():
    r = ReserveRuntime()
    observe(r, 0, 80)
    observe(r, 300, 80)
    restored = ReserveRuntime()
    restored.restore(r.export())
    observe(restored, 4000, 90)
    assert restored.hold_soc == 80
    assert restored.observed_seconds == 300


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"hold_soc": float("nan"), "observed_seconds": -1},
        {"hold_soc": 101, "observed_seconds": "bad"},
    ],
)
def test_corrupt_state_cannot_invent_reserve_intent(value):
    r = ReserveRuntime()
    r.restore(value)
    assert r.observed_seconds == 0
    assert r.hold_soc is None


def test_forecast_control_needs_no_shadow_or_additional_sensor_setup():
    assert (
        _validate_support_entities({CONF_RESERVE_MODE: "shadow"})
        == "reserve_requires_coordinated_support"
    )
    c = {CONF_RESERVE_MODE: "active", CONF_INVERTER_LIMIT_ENTITY: LIMIT}
    assert _validate_support_entities(c) is None
    c |= {
        CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
        CONF_SUPPORT_DC24_SWITCH: PSU24,
        CONF_DCDC_SWITCH: DCDC,
    }
    assert _validate_support_entities(c) is None
    c[CONF_RESERVE_TRANSFER_VERIFIED] = True
    assert _validate_support_entities(c) is None


async def test_partial_feedback_does_not_confirm_full_inverter_permission(rig, hass):
    c, calls, _, _ = rig
    c._reserve_inverter_limit_w = 2300
    hass.states.async_set(LIMIT, "125")
    assert not c._inverter_limit_confirmed(False)
    await execute(c, inverter=True)
    assert calls == [(LIMIT, 2300)]
    assert c._inverter_limit_confirmed(False)


async def test_active_reserve_builds_the_operator_soft_85_target(rig):
    c, *_ = rig
    c.raw_config[CONF_RESERVE_MODE] = "active"
    config = c.build_system_config()
    assert config.reserve.enabled
    assert config.reserve.soft_soc_ceiling_percent == 85


async def test_grid_loss_restores_dc_before_waiting_for_dead_inverter(rig, hass):
    c, calls, dead, _ = rig
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
        }
    )
    hass.states.async_set("binary_sensor.grid", "off")
    hass.states.async_set(DCDC, "off")
    hass.states.async_set(PSU24, "on")
    dead.add(LIMIT)
    await execute(c, dc24=True, dc48=True)
    assert calls[:2] == [(DCDC, True), (PSU24, False)]
    assert calls.index((DCDC, True)) < calls.index((LIMIT, 0))
    assert c._coordinated_support_diag["reason"] == "grid_supply_unavailable"


async def test_failed_dc_restore_keeps_existing_source(rig, hass):
    c, calls, dead, _ = rig
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
        }
    )
    hass.states.async_set(DCDC, "off")
    hass.states.async_set(PSU24, "on")
    dead.add(DCDC)
    await execute(c, dc24=True)
    assert calls == [(DCDC, True), (LIMIT, 0)]
    assert hass.states.get(PSU24).state == "on"
    assert float(hass.states.get(LIMIT).state) == 0


def _outage_with_support(rig, hass, *, soc=4, verified=True):
    c, *_ = rig
    c.raw_config.update(
        {
            CONF_SOC_ENTITY: "sensor.test_soc",
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: verified,
        }
    )
    hass.states.async_set("sensor.test_soc", str(soc))
    hass.states.async_set("binary_sensor.grid", "off")
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(PSU48, "on")
    hass.states.async_set(DCDC, "off")
    c._support_state = {"dc24": True, "dc48": True}


async def test_grid_returns_during_psu24_off_confirmation(rig, hass):
    c, calls, *_ = rig
    _outage_with_support(rig, hass)
    c.raw_config[CONF_SUPPORT_SWITCH_DELAY_S] = 7.5
    off_sent = asyncio.Event()
    confirm_off = asyncio.Event()

    async def switch(call):
        entity = call.data["entity_id"]
        on = call.service == "turn_on"
        calls.append((entity, on))
        if entity == PSU24 and not on:
            off_sent.set()
            await confirm_off.wait()
        hass.states.async_set(entity, "on" if on else "off")
        assert hass.states.is_state(PSU24, "on") or hass.states.is_state(DCDC, "on")

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("homeassistant", service, switch)

    async def overlap(seconds):
        assert seconds == 7.5
        assert hass.states.is_state(PSU24, "on") and hass.states.is_state(DCDC, "on")
        calls.append(("overlap", seconds))

    with (
        patch.object(
            c, "_switch_entity", BatteryManagerCoordinator._switch_entity.__get__(c)
        ),
        patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            side_effect=overlap,
        ),
    ):
        task = asyncio.create_task(execute(c, dc24=True, dc48=True))
        try:
            await off_sent.wait()
            assert c._supply_actor_requests[PSU24].state == "pending"
            hass.states.async_set("binary_sensor.grid", "on")
        finally:
            confirm_off.set()
            await task

    assert calls == [
        (DCDC, True),
        ("overlap", 7.5),
        (PSU24, False),
        (LIMIT, 0),
        (PSU24, True),
        ("overlap", 7.5),
        (DCDC, False),
    ]
    assert c._supply_actor_requests[PSU24].state == "confirmed"
    assert c._supply_actor_requests[DCDC].state == "confirmed"
    assert c._support_state == {"dc24": True, "dc48": True}
    assert c.data["coordinated_support"]["reason"] == "grid_supply_restored"


@pytest.mark.parametrize(
    ("point", "expected", "recovered"),
    [
        (
            "before_overlap",
            [(DCDC, True), (LIMIT, 0), (PSU24, True), (DCDC, False)],
            True,
        ),
        (
            "during_overlap",
            [(DCDC, True), (LIMIT, 0), (PSU24, True), (DCDC, False)],
            True,
        ),
        (
            "after_last_off",
            [
                (DCDC, True),
                (PSU24, False),
                (PSU48, False),
                (LIMIT, 0),
                (PSU24, True),
                (DCDC, False),
                (PSU48, True),
            ],
            True,
        ),
        ("normal_soc", [(DCDC, True), (PSU24, False), (LIMIT, 0)], False),
        (
            "stays_off",
            [(DCDC, True), (PSU24, False), (PSU48, False), (LIMIT, 0)],
            False,
        ),
        ("unknown", [(DCDC, True), (PSU24, False), (PSU48, False), (LIMIT, 0)], False),
        (
            "lost_while_blocking",
            [(DCDC, True), (LIMIT, 0), (DCDC, True), (PSU24, False), (PSU48, False)],
            False,
        ),
        (
            "lost_again",
            [
                (DCDC, True),
                (PSU24, False),
                (LIMIT, 0),
                (PSU24, True),
                (DCDC, True),
                (PSU24, False),
                (PSU48, False),
            ],
            False,
        ),
        (
            "lost_after_transfer",
            [
                (DCDC, True),
                (PSU24, False),
                (LIMIT, 0),
                (PSU24, True),
                (DCDC, False),
                (DCDC, True),
                (PSU24, False),
                (PSU48, False),
            ],
            False,
        ),
    ],
)
async def test_grid_return_checkpoints(rig, hass, point, expected, recovered):
    c, calls, *_ = rig
    _outage_with_support(rig, hass)
    switch = c._switch_entity
    confirm_limit = c._confirm_inverter_limit
    first_overlap = True

    def grid(state):
        hass.states.async_set("binary_sensor.grid", state)

    async def overlap(seconds):
        nonlocal first_overlap
        assert seconds == 3
        assert hass.states.is_state(PSU24, "on") and hass.states.is_state(DCDC, "on")
        if first_overlap and point == "during_overlap":
            grid("on")
        elif not first_overlap and point == "lost_again":
            grid("off")
        first_overlap = False

    async def change_grid(entity, on, **kwargs):
        assert kwargs["actor_owner"] == SUPPLY_OWNER
        result = await switch(entity, on, **kwargs)
        if point in ("before_overlap", "lost_while_blocking") and calls == [
            (DCDC, True)
        ]:
            grid("on")
        elif (
            point in ("lost_again", "lost_after_transfer", "normal_soc")
            and (entity, on) == (PSU24, False)
            and calls.count((PSU24, False)) == 1
        ):
            grid("on")
            if point == "normal_soc":
                hass.states.async_set("sensor.test_soc", "80")
        elif point == "after_last_off" and (entity, on) == (PSU48, False):
            grid("on")
        elif point == "lost_after_transfer" and (entity, on) == (DCDC, False):
            grid("off")
        assert hass.states.is_state(PSU24, "on") or hass.states.is_state(DCDC, "on")
        return result

    async def change_grid_at_limit(blocked, diag):
        confirmed = await confirm_limit(blocked, diag)
        if point == "lost_while_blocking":
            grid("off")
        return confirmed

    if point == "unknown":
        grid("unknown")
    with (
        patch.object(c, "_switch_entity", change_grid),
        patch.object(c, "_confirm_inverter_limit", change_grid_at_limit),
        patch.object(c, "_schedule_replan"),
        patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            side_effect=overlap,
        ),
    ):
        await execute(c, dc24=True, dc48=True)

    assert calls == expected
    assert c._support_state["dc24"] is recovered
    assert hass.states.is_state(DCDC, "off" if recovered else "on")
    assert c._support_state["dc48"] is (recovered or point == "normal_soc")
    assert c.data["coordinated_support"]["grid_restore_pending"] == []


@pytest.mark.parametrize("next_grid", ["on", "off"])
async def test_grid_update_revokes_release_from_initial_unknown_state(
    rig, hass, next_grid
):
    c, calls, *_ = rig
    _outage_with_support(rig, hass, soc=80)
    hass.states.async_set("binary_sensor.grid", "unknown")
    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    confirm_limit = c._confirm_inverter_limit

    async def publish_grid(blocked, diag):
        confirmed = await confirm_limit(blocked, diag)
        if not blocked:
            hass.states.async_set("binary_sensor.grid", next_grid)
        return confirmed

    with patch.object(c, "_confirm_inverter_limit", publish_grid):
        await execute(c, inverter=True)

    assert calls == [
        (DCDC, True),
        (PSU24, False),
        (PSU48, False),
        (LIMIT, 2300),
        (LIMIT, 0),
    ]
    assert hass.states.is_state(DCDC, "on")
    assert c.inverter_control_snapshot()["confirmed"]
    assert not c._inverter_recommendation


@pytest.mark.parametrize(
    ("unconfirmed", "command_error"),
    [
        (LIMIT, False),
        (BLOCK, False),
        (PSU24, False),
        (DCDC, False),
        (LIMIT, True),
        (PSU24, True),
        (DCDC, True),
    ],
)
async def test_grid_restore_pending_retries_without_breaking_rail(
    rig, hass, unconfirmed, command_error
):
    c, calls, dead, failures = rig
    _outage_with_support(rig, hass)
    switch = c._switch_entity
    problems = failures if command_error else dead

    async def returned_grid(entity, on, **kwargs):
        result = await switch(entity, on, **kwargs)
        if (entity, on) == (PSU24, False):
            problems.add(unconfirmed)
            hass.states.async_set("binary_sensor.grid", "on")
        return result

    with (
        patch.object(c, "_switch_entity", returned_grid),
        patch.object(c, "_schedule_replan") as retry,
    ):
        await execute(c, dc24=True, dc48=True)
        retry.assert_called_once()

    assert (PSU48, False) not in calls
    assert ((PSU24, True) in calls) is (unconfirmed in (PSU24, DCDC))
    assert ((DCDC, False) in calls) is (unconfirmed == DCDC)
    assert hass.states.is_state(DCDC, "on")
    assert c._support_state == {"dc24": False, "dc48": True}
    assert c.data["coordinated_support"]["grid_restore_pending"] == ["dc24"]
    problems.clear()
    if unconfirmed == BLOCK:
        hass.states.async_set(BLOCK, "on")
    await c._coordinated_data_loss(c._get_soc(dt_util.now()), dt_util.now())
    await c._switch_task
    assert c._support_state == {"dc24": True, "dc48": True}
    assert c.data["coordinated_support"]["grid_restore_pending"] == []


@pytest.mark.parametrize("timeout", [False, True])
async def test_late_psu24_off_cannot_authorize_dc_dc_shutdown(rig, hass, timeout):
    c, calls, *_ = rig
    _outage_with_support(rig, hass)

    async def switch(call):
        entity = call.data["entity_id"]
        on = call.service == "turn_on"
        calls.append((entity, on))
        if (entity, on) == (PSU24, False):
            hass.states.async_set("binary_sensor.grid", "on")
            if timeout:
                raise TimeoutError("PSU24 OFF accepted without response")
            return
        hass.states.async_set(entity, "on" if on else "off")

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("homeassistant", service, switch)
    with (
        patch.object(
            c, "_switch_entity", BatteryManagerCoordinator._switch_entity.__get__(c)
        ),
        patch.object(c, "_schedule_replan"),
    ):
        await execute(c, dc24=True, dc48=True)
        assert c.data["coordinated_support"]["grid_restore_pending"] == ["dc24"]
        assert c._supply_actor_requests[PSU24].state == (
            "service_failed" if timeout else "pending"
        )
        await execute(c, dc24=True, dc48=True)
        assert calls == [(DCDC, True), (PSU24, False), (LIMIT, 0)]
        assert hass.states.is_state(DCDC, "on")

        hass.states.async_set(PSU24, "off")
        assert reconcile_supply_feedback(c, PSU24)
        assert c._supply_actor_requests[PSU24].state == "confirmed"
        await execute(c, dc24=True, dc48=True)
        assert calls[-2:] == [(PSU24, True), (DCDC, False)]


@pytest.mark.parametrize("renewed_outage", [False, True])
@pytest.mark.parametrize("timeout", [False, True])
async def test_pending_dcdc_off_preserves_psu24_until_rail_is_confirmed(
    rig, hass, renewed_outage, timeout
):
    c, calls, *_ = rig
    _outage_with_support(rig, hass)
    c.raw_config["min_switch_interval_s"] = 0

    async def switch(call):
        entity = call.data["entity_id"]
        on = call.service == "turn_on"
        calls.append((entity, on))
        if (entity, on) == (DCDC, False):
            if renewed_outage:
                hass.states.async_set("binary_sensor.grid", "off")
            if timeout:
                raise TimeoutError("DC/DC OFF accepted without response")
            return  # Accepted OFF without new feedback.
        if (entity, on) == (PSU24, False) and calls.count((PSU24, False)) == 1:
            hass.states.async_set("binary_sensor.grid", "on")
        hass.states.async_set(entity, "on" if on else "off")
        assert hass.states.is_state(PSU24, "on") or hass.states.is_state(DCDC, "on")

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("homeassistant", service, switch)
    with (
        patch.object(
            c, "_switch_entity", BatteryManagerCoordinator._switch_entity.__get__(c)
        ),
        patch.object(c, "_schedule_replan") as retry,
    ):
        await execute(c, dc24=True, dc48=True)
        if not renewed_outage:
            hass.states.async_set("sensor.test_soc", "80")
            await execute(c)

        dcdc_off = calls.index((DCDC, False))
        assert (PSU24, False) not in calls[dcdc_off + 1 :]
        assert (DCDC, True) not in calls[dcdc_off + 1 :]
        assert hass.states.is_state(PSU24, "on")
        pending = c._supply_actor_requests[DCDC]
        assert pending.desired is False
        assert pending.state == ("service_failed" if timeout else "pending")
        before_retry = list(calls)
        await execute(c)
        assert calls == before_retry
        retry.assert_called()

        hass.states.async_set(DCDC, "off")
        assert reconcile_supply_feedback(c, DCDC)
        assert hass.states.is_state(PSU24, "on")
        await execute(c)
        assert calls.index((DCDC, True), dcdc_off + 1) < calls.index(
            (PSU24, False), dcdc_off + 1
        )
        assert hass.states.is_state(DCDC, "on")
        assert hass.states.is_state(PSU24, "off")


@pytest.mark.parametrize("first_ack", [0, 1])
async def test_failed_dcdc_off_can_retry_when_grid_again_supports_takeover(
    rig, hass, first_ack
):
    c, calls, *_ = rig
    _outage_with_support(rig, hass)
    off_contexts = []

    async def switch(call):
        entity = call.data["entity_id"]
        on = call.service == "turn_on"
        calls.append((entity, on))
        if (entity, on) == (DCDC, False):
            off_contexts.append(call.context)
            if len(off_contexts) == 1:
                hass.states.async_set("binary_sensor.grid", "off")
                raise TimeoutError("DC/DC OFF never took effect")
        if (entity, on) == (PSU24, False):
            hass.states.async_set("binary_sensor.grid", "on")
        hass.states.async_set(entity, "on" if on else "off")

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("homeassistant", service, switch)
    with (
        patch.object(
            c, "_switch_entity", BatteryManagerCoordinator._switch_entity.__get__(c)
        ),
        patch.object(c, "_schedule_replan"),
    ):
        await execute(c, dc24=True, dc48=True)
        assert c._supply_actor_requests[DCDC].state == "service_failed"
        assert hass.states.is_state(PSU24, "on")
        assert hass.states.is_state(DCDC, "on")
        initial = list(calls)
        await execute(c)
        assert calls == initial  # Pending OFF is still unresolved.

        hass.states.async_set("binary_sensor.grid", "on")
        await execute(c, dc24=True, dc48=True)
        assert calls[len(initial) :] == [(DCDC, False)]
        assert c._supply_actor_requests[DCDC].state == "confirmed"
        assert hass.states.is_state(PSU24, "on")
        assert hass.states.is_state(DCDC, "off")
        c.async_request_refresh = AsyncMock()
        with pytest.raises(HomeAssistantError, match="previous automatic command"):
            await c.async_set_support_manual("dc24", True)

        # Neither of the two distinct acknowledgements can discharge both
        # writes. While one remains, restore DC/DC without removing the PSU.
        hass.states.async_set(
            DCDC, "off", force_update=True, context=off_contexts[first_ack]
        )
        with pytest.raises(HomeAssistantError, match="previous automatic command"):
            await c.async_set_support_manual("dc24", True)
        hass.states.async_set("sensor.test_soc", "80")
        before_recovery = len(calls)
        await execute(c)
        assert (PSU24, False) not in calls[before_recovery:]
        assert hass.states.is_state(PSU24, "on")
        assert hass.states.is_state(DCDC, "on")

        hass.states.async_set(
            DCDC, "off", force_update=True, context=off_contexts[1 - first_ack]
        )
        await c.async_set_support_manual("dc24", True)
        assert c.support_handed_over("dc24")


@pytest.mark.parametrize("returning", [False, True])
@pytest.mark.parametrize("blocker", [None, LIMIT, BLOCK])
async def test_unverified_transfer_allows_only_confirmed_48v_protection(
    rig, hass, returning, blocker
):
    c, calls, dead, _ = rig
    _outage_with_support(rig, hass, verified=False)
    hass.states.async_set(PSU24, "off")
    hass.states.async_set(PSU48, "off")
    if blocker:
        dead.add(blocker)
    switch = c._switch_entity

    async def returned_grid(entity, on, **kwargs):
        result = await switch(entity, on, **kwargs)
        if (entity, on) == (DCDC, True):
            hass.states.async_set("binary_sensor.grid", "on")
        return result

    if not returning:
        hass.states.async_set(DCDC, "on")
        hass.states.async_set("binary_sensor.grid", "on")
    with (
        patch.object(c, "_switch_entity", returned_grid),
        patch.object(c, "_schedule_replan"),
    ):
        await execute(c, dc24=True, dc48=True)
    assert (PSU24, True) not in calls
    assert (DCDC, False) not in calls
    assert ((PSU48, True) in calls) is (blocker is None)
    if blocker is None:
        assert calls.index((LIMIT, 0)) < calls.index((PSU48, True))
        assert c.inverter_control_snapshot()["confirmed"]
    assert hass.states.is_state(DCDC, "on")


@pytest.mark.parametrize("returning", [False, True])
async def test_discharge_confirmation_revoked_during_takeover(rig, hass, returning):
    c, calls, *_ = rig
    _outage_with_support(rig, hass)
    hass.states.async_set(PSU24, "off")
    hass.states.async_set(PSU48, "off")
    switch = c._switch_entity

    async def returned_grid(entity, on, **kwargs):
        result = await switch(entity, on, **kwargs)
        if (entity, on) == (DCDC, True):
            hass.states.async_set("binary_sensor.grid", "on")
        return result

    async def revoke_block(_seconds):
        assert c.inverter_control_snapshot()["confirmed"]
        assert hass.states.is_state(DCDC, "on")
        hass.states.async_set(BLOCK, "off")

    if not returning:
        hass.states.async_set(DCDC, "on")
        hass.states.async_set("binary_sensor.grid", "on")
    with (
        patch.object(c, "_switch_entity", returned_grid),
        patch.object(c, "_schedule_replan"),
        patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            side_effect=revoke_block,
        ),
    ):
        await execute(c, dc24=True, dc48=True)

    assert calls[-2:] == [(LIMIT, 0), (PSU24, True)]
    assert hass.states.is_state(DCDC, "on")
    assert not c.inverter_control_snapshot()["confirmed"]
    if returning:
        assert c.data["coordinated_support"]["grid_restore_pending"] == ["dc24", "dc48"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("on", True),
        ("AC_INPUT_1", True),
        ("AC_INPUT_2", True),
        ("off", False),
        ("DISCONNECTED", False),
        ("unknown", None),
    ],
)
async def test_grid_signal_requires_known_fresh_state(rig, hass, value, expected):
    c, *_ = rig
    c.raw_config[CONF_RESERVE_GRID_ENTITY] = "sensor.grid"
    hass.states.async_set("sensor.grid", value)
    assert c._reserve_grid_available() is expected
    with patch(
        "custom_components.battery_manager.coordinator.dt_util.utcnow",
        return_value=dt_util.utcnow() + timedelta(seconds=31),
    ):
        assert c._reserve_grid_available() is None


async def test_solar_evidence_requires_fresh_power_units(rig, hass):
    c, *_ = rig
    assert c._reserve_power(None) is None
    hass.states.async_set("sensor.power", "1.2", {"unit_of_measurement": "kW"})
    assert c._reserve_power("sensor.power") == 1200
    hass.states.async_set("sensor.power", "12", {"unit_of_measurement": "kWh"})
    assert c._reserve_power("sensor.power") is None


@pytest.mark.parametrize(
    "initial_mode,verified", [("shadow", True), ("active", True), ("active", False)]
)
async def test_immediate_active_and_optional_shadow_use_real_plans(
    rig, hass, freezer, initial_mode, verified
):
    from test_coordinator import ENTRY_DATA

    c, calls, _, _ = rig
    c.raw_config.update(
        {
            **ENTRY_DATA,
            CONF_RESERVE_MODE: initial_mode,
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: verified,
            "operation_pv_power_entity": "sensor.actual_pv",
            "operation_import_power_entity": "sensor.actual_import",
        }
    )
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.actual_pv", "100", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.actual_import", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.test_soc", "80")
    for entity in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})
    # Foreground plan uses real core/actuation. No production delays or
    # state listeners that could immediately replan test-generated reports.
    c._save_persistent_state = Mock()
    # The freezer fixture keeps foreground plans in their original time slot:
    # disabling boundary scheduling alone still lets the stale-input guard
    # reject a calculation that crosses a real hour boundary under coverage.
    # This test owns each transition; boundary behavior is covered separately.
    c._arm_plan_boundary = Mock()
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    assert c.data["reserve"]["mode"] == initial_mode
    assert c.data["reserve"]["dc24_transfer_verified"] is verified
    assert c.data["reserve"]["inverter_limit_w"] == 0
    assert c.data["reserve"]["shadow_required_hours"] == 0
    # Active preservation requests DC sources immediately; shadow only reports.
    assert c.data["support_dc24"] is (initial_mode == "active" and verified)
    assert c.data["support_dc48"] is (initial_mode == "active")
    if initial_mode == "shadow":
        assert not any(entity in (PSU24, PSU48) and value for entity, value in calls)
    c.raw_config[CONF_RESERVE_MODE] = "active"
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    assert c.data["reserve"]["mode"] == "active"
    assert c.data["inverter_recommendation"] is False
    assert (LIMIT, 0) in calls
    assert ((PSU24, True) in calls) is verified
    assert (PSU48, True) in calls
    assert c._persistent_payload()["reserve"]["hold_soc"] == 80
    assert c._reserve_runtime.observed_seconds < 60
    # Turning the policy off clears the retained policy state.
    c.raw_config[CONF_RESERVE_MODE] = "off"
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    assert c.data["reserve"]["mode"] == "off"
    assert c._reserve_runtime.observed_seconds == 0
    # Forecast or SOC loss interrupts observed shadow time as well.
    c._get_soc = Mock(return_value=None)
    c._first_success_done = True
    with pytest.raises(Exception, match="No valid input data"):
        await c._async_update_data()
    assert c._reserve_runtime._last_at is None


@pytest.mark.parametrize(
    "actor,mode,expected",
    [
        (None, None, "off"),
        (LIMIT, None, "active"),
        (LIMIT, "off", "off"),
        (LIMIT, "shadow", "shadow"),
    ],
)
async def test_existing_coordinated_installation_defaults_active_without_history(
    hass, actor, mode, expected
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.battery_manager.config_flow import _d
    from custom_components.battery_manager.const import DOMAIN
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    data = {CONF_INVERTER_LIMIT_ENTITY: actor}
    if mode is not None:
        data[CONF_RESERVE_MODE] = mode
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    assert c.raw_config[CONF_RESERVE_MODE] == expected
    assert _d(data, CONF_RESERVE_MODE) == expected
    assert c.build_system_config().reserve.enabled is (expected == "active")
    assert c._reserve_runtime.observed_seconds == 0


async def test_missing_grid_evidence_cannot_release_unbudgeted_full_power(rig, hass):
    c, calls, *_ = rig
    c.raw_config[CONF_RESERVE_MODE] = "active"
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c._reserve_inverter_limit_w = 2300
    await execute(c, dc24=True, dc48=True, inverter=True)
    assert (LIMIT, 0) in calls
    assert not any(entity in (PSU24, PSU48) and value for entity, value in calls)
    assert hass.states.get(DCDC).state == "on"
    assert not c._inverter_recommendation


async def test_unverified_rail_cannot_be_transferred_by_active_policy(rig, hass):
    c, calls, *_ = rig
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c.raw_config.update(
        {CONF_RESERVE_MODE: "active", CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid"}
    )
    hass.states.async_set("binary_sensor.grid", "on")
    await execute(c, dc24=True, dc48=True)
    assert (PSU24, True) not in calls
    assert (DCDC, False) not in calls
    assert (PSU48, True) in calls
    assert c._coordinated_support_diag["dc24_block_reason"] == "transfer_unverified"
    assert c._coordinated_support_diag["desired"]["dc24"] is False


def test_legacy_zero_reference_survives_restart_but_unknown_solar_never_earns_credit():
    runtime = ReserveRuntime()
    runtime.restore({"hold_soc": 0, "observed_seconds": 300, "signature": "plant"})
    observe(runtime, 3600, 6, solar=False)
    observe(runtime, 3900, 30, solar=False)
    assert runtime.hold_soc == 0
    assert runtime.observed_seconds == 600
    assert runtime.export() == {
        "policy_version": 1,
        "hold_soc": 0,
        "observed_seconds": 600,
        "signature": "plant",
    }
    # The first newly verifiable point does not credit the preceding unknown
    # interval; only a second verified endpoint can attribute a subsequent gain.
    observe(runtime, 4200, 50, solar=True)
    assert runtime.hold_soc == 0
    observe(runtime, 4500, 51, solar=True)
    assert runtime.hold_soc == 1


@pytest.mark.parametrize("invalid", [True, 10**1000, float("inf"), "6", [], {}])
def test_malformed_reference_is_discarded_without_inventing_a_control_target(invalid):
    runtime = ReserveRuntime()
    runtime.restore({"hold_soc": invalid, "observed_seconds": invalid, "signature": []})
    assert runtime.export() == {
        "policy_version": 1,
        "hold_soc": None,
        "observed_seconds": 0,
        "signature": None,
    }


@pytest.mark.parametrize(
    "stored, expected", [(None, 1), (True, 1), (0, 1), (1, 1), (2, 2), (3, 3)]
)
def test_policy_migration_marker_survives_observation_until_confirmed(stored, expected):
    runtime = ReserveRuntime()
    assert runtime.policy_version == 2
    runtime.restore({"policy_version": stored, "hold_soc": 80, "signature": "plant"})
    observe(runtime, 0, 80)
    assert runtime.policy_version == expected
    restored = ReserveRuntime()
    restored.restore(runtime.export())
    assert restored.policy_version == expected
    runtime.policy_version = 2
    assert runtime.export()["policy_version"] == 2


def test_diagnostics_use_typed_decision_and_keep_history_out_of_physical_values():
    from dataclasses import replace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        PlanInputs,
        ReserveDecision,
        SystemConfig,
    )
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.reserve_runtime import reserve_diagnostics

    config = SystemConfig()
    inputs = PlanInputs(START, 80, (HourSlot(0, START, 1, 0, 0, 0, 0),))
    baseline = plan(config, inputs)
    decision = ReserveDecision(
        preparation_horizon_end=START + timedelta(days=2),
        inverter_limit_w=125.26,
        headroom_wh=345.67,
        unavoidable_export_wh=678.91,
        reason="pv_headroom_preparation",
    )
    result = replace(
        baseline, trajectory=replace(baseline.trajectory, reserve_decision=decision)
    )
    runtime = ReserveRuntime()
    runtime.restore({"hold_soc": 0, "observed_seconds": 300, "signature": "old"})
    diagnostics = reserve_diagnostics(
        config, inputs, result, baseline, runtime, "active"
    )
    assert (
        diagnostics["preparation_horizon_end"]
        == (START + timedelta(days=2)).isoformat()
    )
    assert diagnostics["decision_reason"] == "pv_headroom_preparation"
    assert diagnostics["headroom_wh"] == 345.7
    assert diagnostics["unavoidable_export_wh"] == 678.9
    assert diagnostics["inverter_limit_w"] == 125.3
    assert diagnostics["historical_reference_soc_percent"] == 0
    assert diagnostics["reference_semantics"] == "historical_observation_only"
    assert diagnostics["preparation_pv_basis"] == "expected_with_bounded_uncertainty"
    assert diagnostics["upper_pv_role"] == "bounded_uncertainty_and_diagnostics"
    assert diagnostics["pv_uncertainty_budget_wh"] == 0
    assert diagnostics["soft_soc_ceiling_percent"] is None
    assert diagnostics["pv_uncertainty_budget_wh_by_day"] == {"2026-09-26": 0}
    assert diagnostics["consumption_buffer_wh"] == 250
    assert runtime.hold_soc == 0
    historical = reserve_diagnostics(
        config, inputs, baseline, baseline, ReserveRuntime(), "shadow"
    )
    assert historical["preparation_horizon_end"] is None
    assert historical["decision_reason"] is None
    assert historical["headroom_wh"] == 0
    assert historical["unavoidable_export_wh"] == 0
    assert historical["inverter_limit_w"] == 0
    assert historical["historical_reference_soc_percent"] is None


def test_pv_insurance_diagnostics_distinguish_each_days_forecast_spread():
    from dataclasses import replace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        PlanInputs,
        SystemConfig,
    )
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.reserve_runtime import reserve_diagnostics

    config = SystemConfig()
    config = replace(
        config,
        battery=replace(config.battery, eta_charge=0.9),
        charger=replace(config.charger, eta=0.8),
    )
    inputs = PlanInputs(
        START,
        80,
        tuple(
            HourSlot(
                i,
                START + timedelta(days=i),
                1,
                0,
                1000,
                0,
                0,
                pv_p10_wh=500,
                pv_p90_wh=upper,
            )
            for i, upper in enumerate((1200, 2000, 1400))
        ),
    )
    result = plan(config, inputs)
    diagnostics = reserve_diagnostics(
        config, inputs, result, result, ReserveRuntime(), "active"
    )
    assert diagnostics["pv_uncertainty_budget_wh_by_day"] == {
        "2026-09-26": 36,
        "2026-09-27": 180,
        "2026-09-28": 72,
    }
    assert diagnostics["pv_uncertainty_budget_wh"] == 288


def test_diagnostics_use_actual_psu_grid_attribution_under_solar():
    from dataclasses import replace
    from datetime import datetime

    from custom_components.battery_manager.core import plan
    from custom_components.battery_manager.core.model import (
        HourSlot,
        PlanInputs,
        ReserveParams,
        SystemConfig,
    )
    from custom_components.battery_manager.reserve_runtime import (
        ReserveRuntime,
        reserve_diagnostics,
    )

    config = SystemConfig()
    config = replace(
        config,
        reserve=ReserveParams(True, 1),
        support=replace(
            config.support, configured=True, coordinated=True, dc24_forced_on=True
        ),
        charger=replace(config.charger, eta=1, standby_power_w=0),
    )
    now = datetime(2026, 10, 7, 12)
    inputs = PlanInputs(now, 80, (HourSlot(0, now, 1, 12, 200, 100, 60),))
    result = plan(config, inputs)
    diagnostics = reserve_diagnostics(
        config, inputs, result, result, ReserveRuntime(), "active"
    )
    assert diagnostics["psu_grid_import_wh"] == 0
    assert result.trajectory.flows[0].psu24_delivered_wh > 0


def test_recharge_diagnostics_publish_fixed_iso_anchors_without_inventing_legacy_credit():
    """Certified extra energy and its immutable repayment time survive HA JSON output."""
    import json
    from dataclasses import replace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        PlanInputs,
        RechargeCertificate,
        RechargeSummary,
        ReserveDecision,
        SystemConfig,
    )
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.reserve_runtime import reserve_diagnostics

    config = SystemConfig()
    inputs = PlanInputs(START, 80, (HourSlot(0, START, 1, 0, 0, 0, 0),))
    baseline = plan(config, inputs)
    anchor = START + timedelta(hours=1)
    reference_id = "ab" * 32
    certificate = RechargeCertificate(
        START, anchor, anchor, "dc24", 137, reference_id=reference_id
    )
    decision = ReserveDecision(
        anchor,
        0,
        0,
        0,
        "no_preparation_needed",
        recharge=RechargeSummary(
            "accepted", (anchor,), (certificate,), 2, reference_id=reference_id
        ),
    )
    result = replace(
        baseline, trajectory=replace(baseline.trajectory, reserve_decision=decision)
    )
    payload = reserve_diagnostics(
        config, inputs, result, baseline, ReserveRuntime(), "active"
    )
    credit = json.loads(json.dumps(payload))["recharge"]
    assert credit["reference_id"] == reference_id
    assert credit["certificates"][0]["reference_id"] == reference_id
    assert credit["certificates"][0]["anchor"] == anchor.isoformat()
    assert credit["certificates"][0]["battery_draw_wh"] == 137
    assert credit["validations"] == 2
    assert (
        reserve_diagnostics(
            config, inputs, baseline, baseline, ReserveRuntime(), "active"
        )["recharge"]
        is None
    )
