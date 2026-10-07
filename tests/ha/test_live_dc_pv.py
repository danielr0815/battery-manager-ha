"""F-DC-PV-MARKET: measured PV uses only the confirmed source owner."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_live_ac_runtime import fresh
from test_live_ac_runtime import live as live


@pytest.fixture
async def solar(live, hass):
    c, calls, dead, failures = live
    c.live_ac.envelope = replace(
        c.live_ac.envelope,
        dc_forecast_w=100,
        dc_sources=(True, True),
        support_required=True,
    )
    for entity in (PSU24, PSU48):
        hass.states.async_set(entity, "on")
    hass.states.async_set(DCDC, "off")
    hass.states.async_set("sensor.house", "200", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.import", "0", {"unit_of_measurement": "W"})
    return c, calls, dead, failures


async def test_solar_returns_to_dc_converter_without_ac_discharge(solar, hass):
    c, calls, *_ = solar
    await c.live_ac.run()
    assert calls == [(PSU48, False), (DCDC, True), (PSU24, False)]
    assert hass.states.get(DCDC).state == "on"
    assert not c._support_state["dc24"] and not c._support_state["dc48"]
    assert c._coordinated_support_diag["pv_priority"]
    assert float(hass.states.get(LIMIT).state) == 0
    assert not c.data["inverter_recommendation"]


async def test_cloud_return_retries_after_existing_switch_dwell(solar, hass, freezer):
    c, calls, *_ = solar
    await c.live_ac.run()
    hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert c._coordinated_support_diag["reason"] == "minimum_switch_interval"
    assert c.live_ac._pv_restore_pending
    freezer.tick(timedelta(seconds=c.raw_config["min_switch_interval_s"]))
    fresh(hass)
    await c.live_ac.run()
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(PSU48).state == "on"
    assert hass.states.get(DCDC).state == "off"
    assert not c.live_ac._pv_restore_pending


@pytest.mark.parametrize(
    "fault",
    [
        "manual",
        "soc",
        "unknown_soc",
        "invalid_soc",
        "grid",
        "stale",
        "expired",
        "missing",
        "partial",
    ],
)
async def test_no_pv_override_without_full_fresh_permission(
    solar, hass, freezer, fault
):
    c, calls, *_ = solar
    if fault == "manual":
        c._support_manual["dc24"] = True
    elif fault == "soc":
        hass.states.async_set("sensor.test_soc", "6")
    elif fault in ("unknown_soc", "invalid_soc"):
        hass.states.async_set(
            "sensor.test_soc", "unavailable" if fault == "unknown_soc" else "200"
        )
    elif fault == "grid":
        hass.states.async_set("binary_sensor.grid", "off")
    elif fault == "stale":
        freezer.tick(timedelta(seconds=31))
    elif fault == "expired":
        c.live_ac.envelope = replace(c.live_ac.envelope, expires=dt_util.utcnow())
    elif fault == "missing":
        hass.states.async_set("sensor.pv", "unavailable")
    else:
        hass.states.async_set("sensor.pv", "250", {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert not c.live_ac.pv_active
    assert (PSU24, False) not in calls and (PSU48, False) not in calls


@pytest.mark.parametrize("entity", [PSU48, DCDC, PSU24])
async def test_failed_solar_return_never_releases_ac(solar, hass, entity):
    c, calls, dead, *_ = solar
    dead.add(entity)
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 0
    assert c._coordinated_support_diag["reason"] != "settled"
    if entity == PSU48:
        assert (DCDC, True) not in calls
    elif entity == DCDC:
        assert (PSU24, False) not in calls


async def test_cloud_during_confirmation_restores_through_same_owner(solar, hass):
    c, calls, *_ = solar
    switch = c._switch_entity.side_effect

    async def cloud(entity, on, **kwargs):
        result = await switch(entity, on)
        if entity == PSU24 and not on:
            hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
        return result

    c._switch_entity.side_effect = cloud
    await c.live_ac.run()
    assert c._coordinated_support_diag["reason"] == "pv_permission_expired"
    assert not c.data["inverter_recommendation"]
    c._last_support_switch = None
    await c.live_ac.run()
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(PSU48).state == "on"


async def test_expired_plan_restores_protection_and_requests_fresh_plan(solar, hass):
    c, *_ = solar
    await c.live_ac.run()
    c.live_ac.envelope = replace(c.live_ac.envelope, expires=dt_util.utcnow())
    c._last_support_switch = None
    await c.live_ac.run()
    c.async_request_refresh.assert_awaited_once()
    assert hass.states.get(PSU24).state == "off"
    assert hass.states.get(PSU48).state == "off"


async def test_signed_ac_balance_and_measured_dc_are_used_once(solar, hass):
    c, *_ = solar
    c.raw_config.update(
        live_ac_grid_power_entity="sensor.grid",
        live_ac_input_power_entity="sensor.input",
        live_ac_output_power_entity="sensor.output",
        dc_load_entity="sensor.dc",
    )
    for entity, value in (
        ("sensor.grid", 0),
        ("sensor.input", 0),
        ("sensor.output", -300),
        ("sensor.dc", 80),
    ):
        hass.states.async_set(entity, str(value), {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert c.live_ac.pv_active
    assert hass.states.get(PSU24).state == "off"
    # Increasing grid import cannot become extra PV; stale AC input is unknown.
    hass.states.async_set("sensor.grid", "400", {"unit_of_measurement": "W"})
    assert not c.live_ac.pv_source_permission(c.build_system_config())
    hass.states.async_set("sensor.input", "unavailable")
    assert not c.live_ac.pv_source_permission(c.build_system_config())


@pytest.mark.parametrize("step", [PSU48, DCDC, PSU24])
@pytest.mark.parametrize("fault", ["pv", "soc", "revision"])
async def test_each_confirmation_rechecks_permission_before_next_removal(
    solar, hass, step, fault
):
    c, calls, *_ = solar
    original = c._switch_entity.side_effect

    async def changed(entity, on, **kwargs):
        result = await original(entity, on, **kwargs)
        if entity == step and on == (step == DCDC):
            if fault == "pv":
                hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
            elif fault == "soc":
                hass.states.async_set("sensor.test_soc", "6")
            else:
                c.live_ac.plan_revision += 1
        return result

    c._switch_entity.side_effect = changed
    await c.live_ac.run()
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(PSU48).state == "on"
    assert float(hass.states.get(LIMIT).state) == 0
    assert c.live_ac._pv_restore_pending
    if step != PSU24:
        assert (PSU24, False) not in calls


async def test_pv_loss_during_overlap_preserves_previous_rail(solar, hass):
    c, calls, *_ = solar

    async def overlap(seconds):
        assert seconds == c.raw_config["support_switch_delay_s"]
        hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})

    # Restore the nested clock patch before rig tears down its outer patch.
    # A monkeypatch finalizer here would otherwise restore rig's AsyncMock
    # after rig already restored real asyncio.sleep, leaking into later tests.
    with patch("custom_components.battery_manager.coordinator.asyncio.sleep", overlap):
        await c.live_ac.run()
    assert (PSU24, False) not in calls
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(PSU48).state == "on"


async def test_soc_protection_bypasses_economic_dwell(solar, hass):
    c, calls, *_ = solar
    await c.live_ac.run()
    hass.states.async_set("sensor.test_soc", "6")
    await c.live_ac.run()
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(PSU48).state == "on"
    assert c._coordinated_support_diag["reason"] != "minimum_switch_interval"


async def test_failed_restore_remains_pending_without_removing_rail(solar, hass):
    c, calls, _, failures = solar
    original = c._switch_entity.side_effect

    async def cloud(entity, on, **kwargs):
        result = await original(entity, on, **kwargs)
        if entity == PSU48 and not on:
            hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
            failures.add(PSU48)
        return result

    c._switch_entity.side_effect = cloud
    await c.live_ac.run()
    assert (PSU24, False) not in calls
    assert c.live_ac._pv_restore_pending
    failures.clear()
    c._last_support_switch = None
    await c.live_ac.run()
    assert hass.states.get(PSU48).state == "on"


async def test_expired_refresh_uses_current_sources(solar, hass):
    c, calls, *_ = solar
    c.live_ac.envelope = replace(
        c.live_ac.envelope, expires=dt_util.utcnow(), dc_sources=(False, False)
    )
    hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})

    async def refresh():
        c.live_ac.plan_revision += 1
        c.live_ac.envelope = replace(
            c.live_ac.envelope,
            expires=dt_util.utcnow() + timedelta(minutes=5),
            dc_sources=(True, True),
        )

    c.async_request_refresh.side_effect = refresh
    await c.live_ac.run()
    assert (PSU24, False) not in calls
    assert (PSU48, False) not in calls


async def test_waiting_source_request_cannot_overwrite_new_plan(solar, hass):
    import asyncio

    c, calls, *_ = solar
    entered, resume = asyncio.Event(), asyncio.Event()

    class Gate:
        async def __aenter__(self):
            entered.set()
            await resume.wait()

        async def __aexit__(self, *args):
            pass

    c._switch_lock = Gate()
    hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
    task = asyncio.create_task(
        c._execute_coordinated_support(
            {"dc24": False, "dc48": False},
            False,
            c.build_system_config(),
            dt_util.now(),
        )
    )
    await entered.wait()
    c.live_ac.plan_revision += 1
    c.live_ac.envelope = replace(c.live_ac.envelope, dc_sources=(True, True))
    resume.set()
    await task
    assert (PSU24, False) not in calls
    assert (PSU48, False) not in calls


async def test_dc_fallback_is_independent_of_hour_boundary(live, freezer):
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs
    from custom_components.battery_manager.core.optimize import plan

    c, *_ = live
    config = c.build_system_config()
    values = []
    for at, duration in (
        ("2026-09-29T09:55:00+00:00", 1 / 12),
        ("2026-09-29T10:00:00+00:00", 1),
    ):
        freezer.move_to(at)
        now = dt_util.now()
        inputs = PlanInputs(
            now, 80, (HourSlot(0, now, duration, now.hour, 0, 0, 100 * duration),)
        )
        c.live_ac.set_plan(config, inputs, plan(config, inputs))
        values.append(c.live_ac.envelope.dc_forecast_w)
    assert (
        values[0]
        == values[1]
        == 100 + config.battery.energy_wh(config.control.soc_buffer_percent)
    )


@pytest.mark.parametrize("fault", ["soc", "revision"])
async def test_permission_loss_during_inverter_release_is_physically_revoked(
    live, hass, fault
):
    c, calls, *_ = live
    original = c._set_number_value

    async def release(entity, value, **kwargs):
        result = await original(entity, value, **kwargs)
        if value:
            if fault == "soc":
                hass.states.async_set("sensor.test_soc", "15")
            else:
                c.live_ac.plan_revision += 1
                c.live_ac.envelope = replace(
                    c.live_ac.envelope, dc_sources=(True, True)
                )
        return result

    c._set_number_value = release
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert float(hass.states.get(LIMIT).state) == 0
    assert not c._inverter_recommendation
    assert (LIMIT, 2300) in calls and calls[-1][1] != 2300


@pytest.mark.parametrize("fault", ["soc", "grid", "revision", "restore"])
async def test_non_solar_transition_rechecks_protection_and_plan(solar, hass, fault):
    c, calls, _, failures = solar
    hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
    original = c._switch_entity.side_effect

    async def changed(entity, on, **kwargs):
        result = await original(entity, on, **kwargs)
        if entity == PSU48 and not on:
            if fault == "soc":
                hass.states.async_set("sensor.test_soc", "6")
            elif fault == "grid":
                hass.states.async_set("binary_sensor.grid", "off")
            else:
                c.live_ac.plan_revision += 1
            if fault == "restore":
                hass.states.async_set(PSU24, "off")
                failures.add(PSU24)
        return result

    c._switch_entity.side_effect = changed
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert (PSU24, False) not in calls
    assert float(hass.states.get(LIMIT).state) == 0
    assert c.live_ac._pv_restore_pending


async def test_revision_after_source_activation_restores_current_targets(solar, hass):
    c, calls, *_ = solar
    hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
    hass.states.async_set(PSU48, "off")
    original = c._switch_entity.side_effect

    async def revised(entity, on, **kwargs):
        result = await original(entity, on, **kwargs)
        if entity == PSU48 and on:
            c.live_ac.plan_revision += 1
        return result

    c._switch_entity.side_effect = revised
    await c._execute_coordinated_support(
        {"dc24": True, "dc48": True}, False, c.build_system_config(), dt_util.now()
    )
    assert hass.states.get(PSU48).state == "on"
    assert hass.states.get(PSU24).state == "on"
    assert c._coordinated_support_diag["reason"] == "plan_changed"


async def test_protection_change_during_initial_block_keeps_old_sources(solar, hass):
    c, calls, *_ = solar
    original = c._set_number_value
    hass.states.async_set(LIMIT, "2300")

    async def blocked(entity, value, **kwargs):
        result = await original(entity, value, **kwargs)
        hass.states.async_set("sensor.test_soc", "6")
        return result

    c._set_number_value = blocked
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, False, c.build_system_config(), dt_util.now()
    )
    assert (PSU48, False) not in calls and (PSU24, False) not in calls
    assert c._coordinated_support_diag["reason"] == "pv_permission_expired"


@pytest.mark.parametrize("expired", [False, True])
async def test_nonreserve_queued_request_reads_current_published_sources(
    rig, hass, expired
):
    import asyncio
    from dataclasses import replace

    from custom_components.battery_manager.core import plan
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs

    c, calls, *_ = rig
    config = c.build_system_config()
    now = dt_util.now()
    inputs = PlanInputs(now, 80, (HourSlot(0, now, 1, now.hour, 0, 0, 0),))
    result = plan(config, inputs)
    c._last_planner_recording = (config, inputs, result)
    entered, resume = asyncio.Event(), asyncio.Event()

    class Gate:
        async def __aenter__(self):
            entered.set()
            await resume.wait()

        async def __aexit__(self, *args):
            pass

    c._switch_lock = Gate()
    task = asyncio.create_task(
        c._execute_coordinated_support(
            {"dc24": False, "dc48": False}, False, config, now
        )
    )
    await entered.wait()
    c.live_ac.plan_revision += 1
    if expired:
        inputs = replace(inputs, now=now - timedelta(minutes=6), slots=())
    c._last_planner_recording = (
        config,
        inputs,
        replace(result, support_dc24_now=True, support_dc48_now=True),
    )
    resume.set()
    await task
    assert ((PSU24, True) in calls) is not expired
    assert ((PSU48, True) in calls) is not expired
