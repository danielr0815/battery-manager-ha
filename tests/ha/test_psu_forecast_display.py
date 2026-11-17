"""The forecast sensor renders display evidence without changing PlanResult."""

from unittest.mock import AsyncMock, Mock

from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_voltage_gate_runtime import fresh
from test_voltage_gate_runtime import gate as gate

from custom_components.battery_manager.core.optimize import plan
from custom_components.battery_manager.sensor import BatteryManagerSocForecastSensor


async def test_qualified_psu_evidence_only_changes_the_display_forecast(
    gate, hass, monkeypatch
):
    c, _, *_ = gate
    # Publication also records load forecasts. Keep that code path but avoid
    # leaving its delayed persistence timer behind the fixture's frozen clock.
    monkeypatch.setattr(c.learner._store, "async_delay_save", Mock())
    c.raw_config.update(
        reserve_mode="active",
        reserve_transfer_verified=True,
        reserve_grid_available_entity="binary_sensor.synthetic_grid",
        psu48_max_current_a=1.15,
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
    fresh(hass, voltage=49.49, soc=69, power=0)

    # Run the real planner with fixed test telemetry. Supply proof belongs to
    # the event listener tests; this test checks the publication boundary.
    async def calculate(_phase, config, inputs):
        return plan(config, inputs)

    c._async_plan = AsyncMock(side_effect=calculate)
    c.voltage_gate.psu.expected48 = Mock(return_value=(35, 24 * 3600))
    data = await c._async_update_data_serial()
    c.data = data
    sensor = BatteryManagerSocForecastSensor(c)
    attrs = sensor.extra_state_attributes
    _, inputs, result = c._last_planner_recording
    assert attrs["soc_expectation"] == {"status": "available", "delivery_w": 35}, (
        result.trajectory.flows[0]
    )
    expected = [p for p in attrs["forecast"] if "expected_soc" in p]
    assert len(expected) > 2
    assert expected[-1]["expected_soc"] > expected[-1]["soc"]
    assert [p["soc"] for p in attrs["forecast"]] == [
        inputs.start_soc_percent,
        *(round(f.soc_end_percent, 1) for f in result.trajectory.flows),
    ]
    assert sensor.native_value == attrs["forecast"][1]["soc"]
    assert "soc_expectation" in sensor._unrecorded_attributes
