"""Qualified support reaches the primary HA forecast while dispatch stays conservative."""

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.sensor import DATA_COMPONENT
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_voltage_gate_runtime import fresh
from test_voltage_gate_runtime import gate as gate

from custom_components.battery_manager.core.optimize import plan
from custom_components.battery_manager.sensor import BatteryManagerSocForecastSensor
from custom_components.battery_manager.supply_forecast import (
    published_supply_forecast,
    read_supply_evidence,
)

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
        # Keep the already confirmed automatic sources in their recovery band
        # while the tests collect measured forecast evidence at 69% SOC.
        support_dc24_recovery_soc=75,
        support_dc48_recovery_soc=75,
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
            "available",
            None,
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
    if reports == 61:
        # The real planner starts with a slot just under one hour.
        freezer.tick(timedelta(minutes=30))
    c.voltage_gate.start_observing()
    for i in range(reports):
        freezer.tick(timedelta(seconds=30))
        hass.states.async_set("binary_sensor.synthetic_grid", "on")
        watts = lowest_w if i == 1 else 40
        hass.states.async_set(DC_OUTPUT, str(watts), {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
    hass.states.async_set(AC_INPUT, "50", {"unit_of_measurement": "W"})
    hass.states.async_set("binary_sensor.synthetic_grid", "on")
    start_soc = c.build_system_config().battery.soc_min_percent if reports == 61 else 69
    fresh(hass, voltage=49.49, soc=start_soc, power=0)
    c.data = await c._async_update_data_serial()
    c.async_update_listeners()

    state = hass.states.get(sensor.entity_id)
    attrs = state.attributes
    _, inputs, result = c._last_planner_recording
    diagnostic = attrs["soc_expectation"]
    assert diagnostic["status"] == status
    assert diagnostic["relay_confirmed_on"]
    assert diagnostic.get("reason") == reason
    assert diagnostic.get("observed_seconds") == observed_seconds
    if status != "unqualified":
        assert diagnostic["delivery_w"] == lowest_w
    if status == "available":
        assert diagnostic["operating_forecast"]
        assert diagnostic["end_soc"] == pytest.approx(start_soc)
        assert result.operating_trajectory is not None
        if reports == 61:
            # At the battery floor both curves have the same SOC. The
            # observed PSU still serves native demand during the safe window.
            expected_deficit = sum(
                f.unserved_dc_wh for f in result.operating_trajectory.flows
            )
            assert expected_deficit < sum(
                f.unserved_dc_wh for f in result.trajectory.flows
            )
            assert c.data["coordinated_support"]["forecast_unserved_dc_wh"] == round(
                expected_deficit, 2
            )
        else:
            assert attrs["forecast"][1]["soc"] > attrs["forecast"][1]["safety_soc"]
        assert c.data["coordinated_support"]["psu48_power_source"] == "direct"
        assert c.data["reserve"]["psu48_power_source"] == "unknown"
        assert (
            datetime.fromisoformat(diagnostic["end_at"]).timestamp()
            == result.psu48_supply_until
        )
        weaker = replace(
            inputs, psu48_supply=replace(inputs.psu48_supply, power_w=lowest_w + 1)
        )
        fallback, stale = published_supply_forecast(c, weaker, result, dt_util.now())
        assert fallback is result.trajectory
        assert stale["reason"] == "supply_evidence_changed"
    else:
        assert result.operating_trajectory is None
        assert all("safety_soc" not in p for p in attrs["forecast"])
    published = result.operating_trajectory or result.trajectory
    assert [p["soc"] for p in attrs["forecast"]] == [
        inputs.start_soc_percent,
        *(round(f.soc_end_percent, 1) for f in published.flows),
    ]
    assert float(state.state) == attrs["forecast"][1]["soc"]
    assert "soc_expectation" in sensor._unrecorded_attributes


async def test_battery_net_expectation_is_limited_to_observed_night(
    forecast_sensor, hass, freezer, monkeypatch
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
    assert result.operating_trajectory is not None
    assert forecast[1]["soc"] == 69
    assert forecast[1]["safety_soc"] < 69
    assert diagnostic["end_soc"] == pytest.approx(69)
    assert c.data["grid_import_kwh"] == round(
        result.operating_trajectory.total_import_wh / 1000, 3
    )
    assert c.data["grid_import_kwh"] > round(
        result.trajectory.total_import_wh / 1000, 3
    )
    assert [p["safety_soc"] for p in forecast] == [
        inputs.start_soc_percent,
        *(round(f.soc_end_percent, 1) for f in result.trajectory.flows),
    ]
    assert float(state.state) == forecast[1]["soc"]

    # A later qualified episode cannot retrospectively explain an older
    # plan, even if the BMS coordinate and all current readings still agree.
    older_inputs = replace(inputs, now=inputs.now - timedelta(hours=3))
    points, evidence = read_supply_evidence(c, older_inputs, dt_util.now())
    assert points is None
    assert evidence["reason"] == "net_plan_outside_observation"

    # A completed CPU run may already be past its first planned switch,
    # even though the measured episode itself would last much longer.
    expired_plan = replace(result, psu48_supply_until=dt_util.now().timestamp())
    reference, rejected = published_supply_forecast(
        c, inputs, expired_plan, dt_util.now()
    )
    assert reference is result.trajectory
    assert rejected["reason"] == "supply_evidence_changed"
    with monkeypatch.context() as m:
        m.setattr(c.voltage_gate, "forecast_soc", lambda now: None)
        reference, rejected = published_supply_forecast(
            c, inputs, result, dt_util.now()
        )
        assert reference is result.trajectory
        assert rejected["reason"] == "supply_soc_anchor_changed"
    with monkeypatch.context() as m:
        m.setattr(c.voltage_gate, "coordinate_matches", lambda snapshot: False)
        reference, rejected = published_supply_forecast(
            c, inputs, result, dt_util.now()
        )
        assert reference is result.trajectory
        assert rejected["reason"] == "net_coordinate_unstable"
    # An observation can end exactly at astronomical dawn.
    expired = {
        **c.voltage_gate.net_discharge_diagnostic(dt_util.now()),
        "valid_until": dt_util.now().timestamp(),
    }
    with monkeypatch.context() as m:
        m.setattr(c.voltage_gate, "net_discharge_diagnostic", lambda now: expired)
        observation, rejected = read_supply_evidence(c, inputs, dt_util.now())
        assert observation is None
        assert rejected["reason"] == "supply_evidence_ends"

    # An old qualification cannot survive a source change at publication.
    hass.states.async_set(DCDC, "on")
    await hass.async_block_till_done()
    fresh(hass, voltage=49.49, soc=69, power=0)
    c.data = await c._async_update_data_serial()
    c.async_update_listeners()
    attrs = hass.states.get(sensor.entity_id).attributes
    assert attrs["soc_expectation"]["status"] == "unqualified"
    assert all("safety_soc" not in p for p in attrs["forecast"])


@pytest.mark.parametrize("measured_pv_w", [0, 200])
async def test_grid_import_expectation_publishes_only_observed_partial_slot(
    forecast_sensor, hass, freezer, monkeypatch, measured_pv_w
):
    c, sensor = forecast_sensor
    hass.config.latitude = 52.5
    hass.config.longitude = 13.4
    freezer.move_to("2026-11-19T10:40:00Z")
    grid_power = "sensor.synthetic_victron_grid_power"
    grid_setpoint = "number.synthetic_victron_grid_setpoint"
    pv_power = "sensor.synthetic_solar_power"
    c.raw_config.update(
        ac_base_load_w=600,
        soc_expectation_grid_power_entity=grid_power,
        soc_expectation_grid_setpoint_entity=grid_setpoint,
        operation_pv_power_entity=pv_power,
    )

    def pv_series(now):
        hour = now.replace(minute=0, second=0, microsecond=0)
        return {hour: 200}, {hour: 150}, {hour: 300}

    monkeypatch.setattr(c, "_get_pv_hourly", pv_series)
    c.voltage_gate.start_observing()
    for _ in range(62):
        freezer.tick(timedelta(seconds=30))
        hass.states.async_set("binary_sensor.synthetic_grid", "on")
        hass.states.async_set(grid_power, "400", {"unit_of_measurement": "W"})
        hass.states.async_set(grid_setpoint, "30", {"unit_of_measurement": "W"})
        hass.states.async_set(
            pv_power, str(measured_pv_w), {"unit_of_measurement": "W"}
        )
        fresh(hass, voltage=49.49, soc=69, power=0)
        await hass.async_block_till_done()
    for period in ("today", "tomorrow", "day_after"):
        hass.states.async_set(
            f"sensor.synthetic_pv_{period}",
            "0",
            {"unit_of_measurement": "kWh"},
            force_update=True,
        )
    c.data = await c._async_update_data_serial()
    c.async_update_listeners()

    state = hass.states.get(sensor.entity_id)
    diagnostic = state.attributes["soc_expectation"]
    forecast = state.attributes["forecast"]
    config, inputs, result = c._last_planner_recording
    raw_evidence = c.voltage_gate.net_discharge_diagnostic(dt_util.now())
    assert raw_evidence["net_discharge_w"] == 0
    assert raw_evidence.get("evidence_mode") == (
        "grid_import" if measured_pv_w else None
    )
    assert diagnostic["status"] == "available"
    assert diagnostic["basis"] == "battery_net"
    assert diagnostic["evidence_mode"] == "grid_import"
    assert diagnostic["net_discharge_w"] == 0
    assert diagnostic["grid_import_w"] == 400
    assert diagnostic["grid_setpoint_w"] == 30
    assert diagnostic["grid_margin_w"] == 50
    assert "delivery_w" not in diagnostic
    assert 1800 <= diagnostic["observed_seconds"] <= 1830
    end_at = datetime.fromisoformat(diagnostic["end_at"])
    assert end_at == dt_util.now() + timedelta(seconds=diagnostic["observed_seconds"])
    assert end_at < datetime.fromisoformat(forecast[1]["t"])
    assert diagnostic["end_soc"] == 69
    assert forecast[1]["safety_soc"] < forecast[1]["soc"] < 69
    assert float(state.state) == forecast[1]["soc"]
    assert result.operating_trajectory is not None

    # The setpoint can report later than the other channels. Even a long
    # qualifying episode cannot extend the hour from its actual last sample.
    if measured_pv_w:
        common_end = dt_util.now().timestamp() - 60
        original_grid = {
            **raw_evidence,
            "observed_until": common_end,
            "observed_from": common_end - 7200,
            "observed_seconds": 7200,
        }
        with monkeypatch.context() as m:
            m.setattr(
                c.voltage_gate, "net_discharge_diagnostic", lambda now: original_grid
            )
            observation, _ = read_supply_evidence(c, inputs, dt_util.now())
            assert observation.valid_until == common_end + 3600

    first, *rest = inputs.slots
    unmatched = replace(inputs, slots=(replace(first, pv_p90_wh=first.ac_wh), *rest))
    rejected = plan(config, unmatched)
    trajectory, details = published_supply_forecast(
        c, unmatched, rejected, dt_util.now()
    )
    assert trajectory is rejected.trajectory
    assert details["reason"] == "net_grid_plan_margin_not_met"

    if measured_pv_w == 0:
        hass.states.async_set(grid_power, "30", {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
        trajectory, details = published_supply_forecast(
            c, inputs, result, dt_util.now()
        )
        assert trajectory is result.trajectory
        assert details["reason"] == "supply_evidence_changed"
