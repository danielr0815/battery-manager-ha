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


async def test_forecast_curve_carries_support_flags(hass):
    """Phase 7: when the plan engages a grid-support PSU (last-resort protection
    at low SOC), the SOC forecast curve marks the affected points so the card
    can draw a support lane."""
    from custom_components.battery_manager.const import (
        CONF_SUPPORT_DC24_SWITCH,
        CONF_SUPPORT_DC48_SWITCH,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            CONF_SUPPORT_DC24_SWITCH: "switch.psu24",
            CONF_SUPPORT_DC48_SWITCH: "switch.psu48",
        },
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    # Low SOC + no PV: the DC base load drains the battery below the buffer
    # floor, so the planner escalates grid support.
    hass.states.async_set(
        "sensor.test_soc", "6", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "0.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    curve = coordinator.data["soc_forecast"]
    assert any(p.get("dc24") or p.get("dc48") for p in curve)


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
    device-parameter fields — now inside the collapsible 'dc_devices'
    section."""
    from custom_components.battery_manager.const import CONF_DC24_SHARE_PERCENT

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    schema = result["data_schema"].schema
    section_marker = next((k for k in schema if str(k) == "dc_devices"), None)
    assert section_marker is not None  # grouped into a section
    inner = schema[section_marker].schema.schema
    assert CONF_DC24_SHARE_PERCENT in {str(k) for k in inner}


async def test_gate_soc_maps_and_full_open(hass):
    """gate_soc < 100 maps through; >= 100 means no gate (None)."""
    from custom_components.battery_manager.const import (
        CONF_GATE_SOC_PERCENT,
        CONF_SUPPORT_DC48_SWITCH,
    )

    for entered, expected in ((40.0, 40.0), (100.0, None)):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=ENTRY_DATA,
            options={
                CONF_GATE_SOC_PERCENT: entered,
                CONF_SUPPORT_DC48_SWITCH: "switch.psu48",
            },
            title="Battery Manager",
            version=2,
        )
        entry.add_to_hass(hass)
        _set_input_states(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        sp = hass.data[DOMAIN][entry.entry_id].build_system_config().support
        assert sp.gate_soc_percent == expected
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_gate_calibration_brackets_the_crossing(hass):
    """The calibration tracks the highest SOC still below the PSU output
    voltage and the lowest SOC already above it."""
    from custom_components.battery_manager.const import (
        CONF_BATTERY_VOLTAGE_ENTITY,
        CONF_SUPPORT_DC48_SWITCH,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={
            CONF_BATTERY_VOLTAGE_ENTITY: "sensor.batt_v",
            CONF_SUPPORT_DC48_SWITCH: "switch.psu48",  # support configured
        },
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    _set_input_states(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()  # psu48 output 49.56 V default

    # Below the threshold at SOC 40 and 55 -> below_max_soc = 55.
    hass.states.async_set("sensor.batt_v", "49.2")
    coordinator._update_gate_calibration(config, 40.0)
    coordinator._update_gate_calibration(config, 55.0)
    # Above the threshold at SOC 80 and 70 -> above_min_soc = 70.
    hass.states.async_set("sensor.batt_v", "49.9")
    coordinator._update_gate_calibration(config, 80.0)
    coordinator._update_gate_calibration(config, 70.0)

    diag = coordinator._gate_calibration_diag(config)
    assert diag["delivering_below_soc_max"] == 55.0
    assert diag["gated_above_soc_min"] == 70.0
    assert diag["suggested_gate_soc"] == 62.5

    # Implausible reading is ignored.
    hass.states.async_set("sensor.batt_v", "5.0")
    coordinator._update_gate_calibration(config, 10.0)
    assert coordinator._gate_cal["below_max_soc"] == 55.0
