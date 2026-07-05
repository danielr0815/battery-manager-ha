"""Tests for the Battery Manager coordinator wiring."""

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


def _set_input_states(hass):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})


async def _setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    _set_input_states(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_coordinator_with_active_listeners(hass):
    """Entry setup must arm the entity-change listeners (regression: was never set)."""
    entry = await _setup_entry(hass)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._listeners_setup is True
    assert coordinator._unsub_state_listener is not None
    assert coordinator.last_update_success


async def test_entity_change_schedules_debounced_update(hass):
    """A SOC state change must schedule a debounced refresh task."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator._debounce_task is None
    hass.states.async_set(
        "sensor.test_soc", "60", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    await hass.async_block_till_done(wait_background_tasks=False)

    assert coordinator._debounce_task is not None
    coordinator._debounce_task.cancel()


async def test_unload_releases_listeners(hass):
    """Unloading the entry must unsubscribe listeners and clear state."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert coordinator._listeners_setup is False
    assert coordinator._unsub_state_listener is None


async def test_default_config_builds_neutral_support_params(hass):
    """F-N3 phase 2: an entry without device params yields NEUTRAL
    SupportParams (share 1.0, efficiencies 1.0, uncapped) — the upgrade
    must not change planning until the operator enters real values."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    sp = coordinator.build_system_config().support
    assert sp.dc24_share == 1.0
    assert sp.dcdc_eta == 1.0 and sp.psu24_eta == 1.0 and sp.psu48_eta == 1.0
    assert sp.dcdc_max_power_w is None
    assert sp.psu24_max_power_w is None
    assert sp.psu48_max_power_w is None
    assert sp.gate_soc_percent is None


async def test_device_params_map_current_to_power_cap(hass):
    """V_out x I_max becomes the rail-side power cap; 0 A stays uncapped."""
    from custom_components.battery_manager.const import (
        CONF_DC24_SHARE_PERCENT,
        CONF_DCDC_MAX_CURRENT_A,
        CONF_DCDC_OUTPUT_VOLTAGE_V,
        CONF_PSU24_EFFICIENCY,
        CONF_PSU24_MAX_CURRENT_A,
        CONF_PSU24_OUTPUT_VOLTAGE_V,
        CONF_PSU48_MAX_CURRENT_A,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={
            CONF_DC24_SHARE_PERCENT: 70.0,
            CONF_DCDC_OUTPUT_VOLTAGE_V: 24.3,
            CONF_DCDC_MAX_CURRENT_A: 20.0,
            CONF_PSU24_OUTPUT_VOLTAGE_V: 24.05,
            CONF_PSU24_MAX_CURRENT_A: 25.0,
            CONF_PSU24_EFFICIENCY: 0.89,
            CONF_PSU48_MAX_CURRENT_A: 0.0,  # uncapped
        },
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    _set_input_states(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sp = hass.data[DOMAIN][entry.entry_id].build_system_config().support
    assert sp.dc24_share == 0.7
    assert abs(sp.dcdc_max_power_w - 24.3 * 20.0) < 1e-6
    assert abs(sp.psu24_max_power_w - 24.05 * 25.0) < 1e-6
    assert sp.psu24_eta == 0.89
    assert sp.psu48_max_power_w is None  # 0 A -> uncapped


async def test_options_flow_renders_with_device_params(hass):
    """The options form must build (regression guard) and expose the new
    device-parameter fields."""
    from custom_components.battery_manager.const import CONF_DC24_SHARE_PERCENT

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_DC24_SHARE_PERCENT in schema_keys
