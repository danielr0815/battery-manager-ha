"""The forecast sensor renders display evidence without changing PlanResult."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.sensor import DATA_COMPONENT
from homeassistant.setup import async_setup_component
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_voltage_gate_runtime import fresh
from test_voltage_gate_runtime import gate as gate

from custom_components.battery_manager.core.optimize import plan
from custom_components.battery_manager.sensor import BatteryManagerSocForecastSensor

AC_INPUT = "sensor.synthetic_psu_48_ac"
DC_OUTPUT = "sensor.synthetic_psu_48_dc"


@pytest.fixture
async def forecast_sensor(gate, hass, monkeypatch):
    c, *_ = gate
    # Publication also records load forecasts. Keep that code path but avoid
    # leaving its delayed persistence timer behind the fixture's frozen clock.
    monkeypatch.setattr(c.learner._store, "async_delay_save", Mock())
    c.raw_config.update(
        reserve_mode="active",
        reserve_transfer_verified=True,
        reserve_grid_available_entity="binary_sensor.synthetic_grid",
        psu48_max_current_a=1.15,
        support_dc48_power_entity=AC_INPUT,
    )
    hass.states.async_set("binary_sensor.synthetic_grid", "on")
    for entity, value in (
        (PSU24, "on"),
        (PSU48, "on"),
        (DCDC, "off"),
        (BLOCK, "on"),
        (LIMIT, "0"),
    ):
        hass.states.async_set(entity, value)
    c._support_state.update(dc24=True, dc48=True)
    c._support_manual.update(dc24=True, dc48=True)
    for period in ("today", "tomorrow", "day_after"):
        entity = f"sensor.synthetic_pv_{period}"
        c.raw_config[f"pv_forecast_{period}_entity"] = entity
        hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})

    # Run the real planner with fixed test telemetry, including the original
    # state reports consumed by the production DC episode listener.
    async def calculate(_phase, config, inputs):
        return plan(config, inputs)

    monkeypatch.setattr(c, "_async_plan", AsyncMock(side_effect=calculate))
    # Drive plan publication explicitly so polling cannot act on intentionally
    # stale inputs during the compressed observation episode.
    monkeypatch.setattr(c, "update_interval", None)
    sensor = BatteryManagerSocForecastSensor(c)
    assert await async_setup_component(hass, "sensor", {})
    await hass.data[DATA_COMPONENT].async_add_entities([sensor])
    try:
        yield c, sensor
    finally:
        await sensor.async_remove()
        c.voltage_gate.stop_observing()


@pytest.mark.parametrize(
    ("dc_meter", "reports", "lowest_w", "status", "reason", "observed_seconds"),
    [
        (AC_INPUT, 0, 35, "unqualified", "invalid_dc_topology", None),
        (DC_OUTPUT, 0, 35, "unqualified", "missing_dc_power_limit", None),
        (DC_OUTPUT, 0, 35, "unqualified", "episode_unproven", None),
        (DC_OUTPUT, 3, 35, "unqualified", "episode_too_short", 60),
        (
            DC_OUTPUT,
            61,
            35,
            "no_window",
            "observation_shorter_than_plan_interval",
            1800,
        ),
        (DC_OUTPUT, 90, 0, "no_window", "no_positive_delivery", 2670),
        (DC_OUTPUT, 241, 35, "available", None, 7200),
    ],
)
async def test_psu_evidence_is_explained_on_forecast_sensor(
    forecast_sensor,
    hass,
    freezer,
    dc_meter,
    reports,
    lowest_w,
    status,
    reason,
    observed_seconds,
):
    c, sensor = forecast_sensor
    if dc_meter:
        c.raw_config["support_dc48_output_power_entity"] = dc_meter
    if reason == "missing_dc_power_limit":
        c.raw_config["psu48_max_current_a"] = 0
    if reason == "observation_shorter_than_plan_interval":
        # The real planner starts with a slot just under one hour.
        freezer.tick(timedelta(minutes=30))
    c.voltage_gate.start_observing()
    for i in range(reports):
        freezer.tick(timedelta(seconds=30))
        watts = lowest_w if i == 1 else 40
        hass.states.async_set(DC_OUTPUT, str(watts), {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
    hass.states.async_set(AC_INPUT, "50", {"unit_of_measurement": "W"})
    hass.states.async_set("binary_sensor.synthetic_grid", "on")
    fresh(hass, voltage=49.49, soc=69, power=0)
    c.data = await c._async_update_data_serial()
    c.async_update_listeners()

    state = hass.states.get(sensor.entity_id)
    attrs = state.attributes
    _, inputs, result = c._last_planner_recording
    assert attrs["soc_expectation"] == {
        "status": status,
        "relay_confirmed_on": True,
        **({"reason": reason} if reason else {}),
        **({"observed_seconds": observed_seconds} if observed_seconds else {}),
        **({"delivery_w": lowest_w} if status != "unqualified" else {}),
    }
    expected = [p for p in attrs["forecast"] if "expected_soc" in p]
    if status == "available":
        assert len(expected) > 2
        assert expected[-1]["expected_soc"] > expected[-1]["soc"]
    else:
        assert not expected
    assert [p["soc"] for p in attrs["forecast"]] == [
        inputs.start_soc_percent,
        *(round(f.soc_end_percent, 1) for f in result.trajectory.flows),
    ]
    assert float(state.state) == attrs["forecast"][1]["soc"]
    assert "soc_expectation" in sensor._unrecorded_attributes


async def test_battery_net_expectation_is_limited_to_observed_night(
    forecast_sensor, hass, freezer
):
    """Real HA publications, with no PSU output or solar power meter configured."""
    c, sensor = forecast_sensor
    hass.config.latitude = 52.5
    hass.config.longitude = 13.4
    freezer.move_to("2026-10-14T20:20:00Z")
    # As on a site with no optional block switch or PSU AC meter, the actual
    # inverter discharge-limit feedback still confirms the supply mode.
    c.raw_config.pop("inverter_block_switch_entity", None)
    c.raw_config.pop("support_dc48_power_entity", None)
    c.voltage_gate.start_observing()
    for _ in range(241):
        freezer.tick(timedelta(seconds=30))
        hass.states.async_set("binary_sensor.synthetic_grid", "on")
        fresh(hass, voltage=49.49, soc=69, power=0)
        await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.synthetic_grid", "on")
    c.data = await c._async_update_data_serial()
    c.async_update_listeners()

    state = hass.states.get(sensor.entity_id)
    diagnostic = state.attributes["soc_expectation"]
    assert diagnostic["basis"] == "battery_net"
    config, inputs, result = c._last_planner_recording
    assert diagnostic["status"] == "available"
    assert diagnostic["net_discharge_w"] == 0
    assert diagnostic["observed_seconds"] == 7200
    assert "delivery_w" not in diagnostic
    forecast = state.attributes["forecast"]
    covered = [p for p in forecast if "expected_soc" in p]
    assert len(covered) >= 2
    assert all(p["expected_soc"] == 69 for p in covered)
    assert covered[-1]["soc"] < 69
    assert "expected_soc" not in forecast[-1]
    assert [p["soc"] for p in forecast] == [
        inputs.start_soc_percent,
        *(round(f.soc_end_percent, 1) for f in result.trajectory.flows),
    ]
    assert float(state.state) == forecast[1]["soc"]

    # A later qualified episode cannot retrospectively explain an older
    # plan, even if the BMS coordinate and all current readings still agree.
    older_inputs = replace(inputs, now=inputs.now - timedelta(hours=3))
    points, evidence = c._forecast_soc_expectation(config, older_inputs, result)
    assert points == ()
    assert evidence["reason"] == "net_plan_outside_observation"

    # An old qualification cannot survive a source change at publication.
    hass.states.async_set(DCDC, "on")
    await hass.async_block_till_done()
    fresh(hass, voltage=49.49, soc=69, power=0)
    c.data = await c._async_update_data_serial()
    c.async_update_listeners()
    attrs = hass.states.get(sensor.entity_id).attributes
    assert attrs["soc_expectation"]["status"] == "unqualified"
    assert all("expected_soc" not in p for p in attrs["forecast"])
