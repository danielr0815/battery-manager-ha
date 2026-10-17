"""Voltage-gated economic support uses the existing ordered source owner."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import DCDC, LIMIT, PSU24, PSU48, execute
from test_coordinated_actuation import rig as rig

from custom_components.battery_manager.live_ac import LiveACEnvelope
from custom_components.battery_manager.runtime_persistence import persistent_payload


@pytest.fixture
async def gate(rig, hass, freezer):
    freezer.move_to("2026-10-14T18:00:00Z")
    c, calls, dead, failures = rig
    c.raw_config.update(
        battery_voltage_entity="sensor.voltage",
        soc_entity="sensor.soc",
        feedin_battery_power_entity="sensor.battery_w",
        native48_base_w=35,
        min_switch_interval_s=0,
    )
    c.voltage_gate.ready = True
    fresh(hass)
    return c, calls, dead, failures


def fresh(hass, voltage=50, soc=82, power=-35):
    for entity, value, unit in [
        ("sensor.voltage", voltage, "V"),
        ("sensor.soc", soc, "%"),
        ("sensor.battery_w", power, "W"),
    ]:
        hass.states.async_set(
            entity, str(value), {"unit_of_measurement": unit}, force_update=True
        )


async def test_high_voltage_keeps_idle_psu_off_without_removing_rail(gate, hass):
    c, calls, *_ = gate
    await execute(c, True, True)
    assert calls == [(LIMIT, 0), (PSU24, True), (DCDC, False)]
    assert c._support_state == {"dc24": True, "dc48": False}
    assert (
        c.data["coordinated_support"]["voltage_gate"]["reason"] == "above_off_voltage"
    )


async def test_live_hysteresis_dwells_and_return_to_battery(gate, hass, freezer):
    c, calls, *_ = gate
    await execute(c, True, True)
    fresh(hass, 49.5)
    await execute(c, True, True)
    assert hass.states.get(PSU48).state == "off"
    freezer.tick(61)
    fresh(hass, 49.5)
    await execute(c, True, True)
    assert hass.states.get(PSU48).state == "on"
    fresh(hass, 49.7)
    await execute(c, True, True)
    assert c.voltage_gate.diagnostics["reason"] == "hysteresis_hold"
    fresh(hass, 50)
    await execute(c, True, True)
    assert c.voltage_gate.diagnostics["reason"] == "waiting_off_voltage"
    freezer.tick(301)
    fresh(hass, 50)
    await execute(c, True, True)
    assert hass.states.get(PSU48).state == "off"
    assert hass.states.get(PSU24).state == "on"
    await execute(c, inverter=True)
    assert hass.states.get(DCDC).state == "on" and hass.states.get(PSU24).state == "off"
    assert float(hass.states.get(LIMIT).state) == 2300


async def test_no_request_does_not_start_on_low_voltage(gate, hass):
    c, *_ = gate
    fresh(hass, 49)
    await execute(c)
    assert hass.states.get(PSU48).state == "off"
    assert c.voltage_gate.diagnostics["reason"] == "no_request"


@pytest.mark.parametrize(
    "manual,soc,reason",
    [(True, 82, "manual_readiness"), (False, 5, "protection_readiness")],
)
async def test_manual_and_protection_override_economic_gate(
    gate, hass, manual, soc, reason
):
    c, *_ = gate
    fresh(hass, 50, soc)
    c._support_manual["dc48"] = manual
    await execute(c, True, True)
    assert hass.states.get(PSU48).state == "on"
    assert c.voltage_gate.diagnostics["reason"] == reason


async def test_invalid_voltage_allows_readiness_but_never_power_credit(
    gate, hass, freezer
):
    c, *_ = gate
    freezer.tick(31)
    hass.states.async_set(
        "sensor.soc", "82", {"unit_of_measurement": "%"}, force_update=True
    )
    await execute(c, True, True)
    assert hass.states.get(PSU48).state == "on"
    assert c.voltage_gate.diagnostics["reason"] == "voltage_unavailable"
    assert c.build_system_config().support.psu48_bus_voltage_v is None
    hass.states.async_set("sensor.voltage", "50", {"unit_of_measurement": "mV"})
    assert c.voltage_gate.voltage() is None
    fresh(hass, 90)
    assert c.voltage_gate.voltage() is None
    fresh(hass, "unknown")
    assert c.voltage_gate.voltage() is None
    c.raw_config.pop("battery_voltage_entity")
    assert c.voltage_gate.voltage() is None


async def test_live_timer_rechecks_deferred_need_without_new_plan(gate, hass, freezer):
    c, calls, *_ = gate
    # The plan's need remains true even though its switch timeline starts OFF.
    c.live_ac.envelope = LiveACEnvelope(
        c.build_system_config(),
        dt_util.utcnow() + timedelta(minutes=5),
        50,
        True,
        dc_sources=(True, True),
    )
    await c.live_ac.run()
    fresh(hass, 49.5)
    await c.live_ac.run()
    freezer.tick(61)
    fresh(hass, 49.5)
    await c.live_ac.run()
    assert (PSU48, True) in calls and not c._inverter_recommendation


async def test_voltage_change_during_rail_confirmation_revokes_psu_start(
    gate, hass, freezer
):
    c, calls, *_ = gate
    fresh(hass, 49)
    c.voltage_gate.apply(
        c.build_system_config(), {"dc24": True, "dc48": True}, dt_util.now()
    )
    freezer.tick(61)
    fresh(hass, 49)
    switch = c._switch_entity.side_effect

    async def changed(entity, on, **kw):
        result = await switch(entity, on, **kw)
        if entity == PSU24:
            fresh(hass, 50)
        return result

    c._switch_entity = AsyncMock(side_effect=changed)
    await execute(c, True, True)
    assert (PSU48, True) not in calls
    assert not c._inverter_recommendation


async def test_adapter_learning_freshness_clamping_persistence(gate, hass, freezer):
    c, *_ = gate
    for _ in range(37):
        fresh(hass, 49.56, 65, -35)
        c.voltage_gate.observe(dt_util.now())
        freezer.tick(60)
    assert c.voltage_gate.bands(dt_util.now())[0].on_soc == 65
    payload = persistent_payload(c)["voltage_gate"]
    assert payload["samples"]
    count = len(c.voltage_gate.learner.samples)
    hass.states.async_set(PSU48, "on")
    fresh(hass, 49.56, 65, -35)
    c.voltage_gate.observe(dt_util.now())
    assert len(c.voltage_gate.learner.samples) == count
    assert c.voltage_gate.learner.reason == "source_clamped_or_unknown"
    freezer.tick(31)
    c.voltage_gate.observe(dt_util.now())
    assert c.voltage_gate.learner.reason == "measurement_unavailable"
    assert c.voltage_gate.diagnostic(dt_util.now())["source"] == "learned"
    c.raw_config["battery_voltage_entity"] = "sensor.other"
    assert c.voltage_gate.bands(dt_util.now()) == ()


async def test_unknown_soc_cannot_release_source(gate, hass, freezer):
    c, *_ = gate
    freezer.tick(31)
    hass.states.async_set("sensor.voltage", "50", {"unit_of_measurement": "V"})
    await execute(c, True, True)
    assert c.voltage_gate.diagnostics["reason"] == "protection_readiness"
    assert hass.states.get(PSU48).state == "on"


async def test_telemetry_waits_for_restoration_and_rejects_frozen_soc(gate, hass):
    c, *_ = gate
    c.voltage_gate.ready = False
    c.voltage_gate.observe(dt_util.now())
    assert c.voltage_gate.learner.fingerprint == ""
    c._house_soc_stale = dt_util.now()
    assert c.voltage_gate._soc(dt_util.now()) is None
    c._house_soc_stale = None
    hass.states.async_set("sensor.soc", "unknown")
    assert c.voltage_gate._soc(dt_util.now()) is None
    c.raw_config.pop("soc_entity")
    assert c.voltage_gate._soc(dt_util.now()) is None


async def test_recalibration_expires_old_soc_permission_and_replans(
    gate, hass, freezer
):
    from unittest.mock import Mock

    c, *_ = gate
    c._schedule_replan = Mock()
    c.live_ac.envelope = LiveACEnvelope(
        c.build_system_config(), dt_util.utcnow() + timedelta(minutes=5), 50, False
    )
    c.voltage_gate.observe(dt_util.now())
    freezer.tick(5)
    fresh(hass, 50, 90)
    c.voltage_gate.observe(dt_util.now())
    assert c.live_ac.envelope.expires == dt_util.utcnow()
    c._schedule_replan.assert_called_once()
    assert c.voltage_gate.learner.reason == "recalibration_suspected"


async def test_coordinator_restores_learning_before_enabling_observation(gate):
    from unittest.mock import Mock

    from custom_components.battery_manager.gate_learning import GateSample

    c, *_ = gate
    c.voltage_gate.bands(dt_util.now())
    sample = GateSample(dt_util.utcnow().timestamp() - 60, 70, 49.56, 0.7)
    c.voltage_gate.learner.samples = [sample]
    payload = c.voltage_gate.learner.export()
    c.voltage_gate.learner.samples.clear()
    c.voltage_gate.ready = False
    c.learner.async_load = AsyncMock()
    c._store.async_load = AsyncMock(return_value={"voltage_gate": payload})
    c.operation_recorder.async_restore = AsyncMock()
    c.operation_recorder.start = Mock()
    await c.async_load_persistent_state()
    assert c.voltage_gate.ready
    assert c.voltage_gate.learner.samples == [sample]


async def test_real_plan_preserves_deferred_request_in_fast_envelope(gate):
    from dataclasses import replace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        PlanInputs,
        ReserveParams,
    )
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.voltage_gate import requested_dc48

    c, *_ = gate
    now = dt_util.now()
    config = replace(c.build_system_config(), reserve=ReserveParams(enabled=True))
    inputs = PlanInputs(now, 82, (HourSlot(0, now, 1, now.hour, 0, 100, 60),))
    result = plan(config, inputs)
    assert not result.support_dc48_now
    assert requested_dc48(result)
    c.live_ac.set_plan(config, inputs, result)
    assert c.live_ac.envelope.dc_sources == (True, True)
    assert c.live_ac.envelope.support_required
