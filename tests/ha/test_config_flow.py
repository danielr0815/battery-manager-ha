"""Config/options flow smoke tests (schema construction must never raise)."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


async def _setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_flow_renders_form(hass):
    """Regression: unit-less number selectors raised vol.Invalid, which the
    HTTP layer turns into a bare '400: Bad Request' on opening the flow."""
    entry = await _setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"


async def test_config_flow_all_steps_render(hass):
    """Every base-flow step must build its schema without raising."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    steps = [
        (ENTRY_DATA, "battery"),
        (
            {
                "battery_capacity_wh": 5000.0,
                "battery_min_soc_percent": 5.0,
                "battery_max_soc_percent": 95.0,
                "battery_charge_efficiency": 0.97,
                "battery_discharge_efficiency": 0.97,
            },
            "pv",
        ),
        (
            {
                "pv_max_power_w": 3200.0,
                "pv_morning_start_hour": 7,
                "pv_morning_end_hour": 13,
                "pv_afternoon_end_hour": 18,
                "pv_morning_ratio": 0.8,
            },
            "consumers",
        ),
        (
            {
                "ac_base_load_w": 50.0,
                "ac_variable_load_w": 75.0,
                "ac_variable_start_hour": 6,
                "ac_variable_end_hour": 20,
                "dc_base_load_w": 50.0,
                "dc_variable_load_w": 25.0,
                "dc_variable_start_hour": 6,
                "dc_variable_end_hour": 22,
            },
            "power",
        ),
    ]
    for user_input, next_step in steps:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["type"] == "form", f"step before {next_step} failed"
        assert result["step_id"] == next_step
    hass.config_entries.flow.async_abort(result["flow_id"])
