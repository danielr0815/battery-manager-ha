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


# ---------------------------------------------------------------------------
# F-SUBHOUR Feature-2 hardening: appliance detection (H1 persist, H2 hysteresis)
# ---------------------------------------------------------------------------


async def test_appliance_started_survives_restart(hass):
    """H1: the appliance run start is persisted so a restart mid-run keeps the
    real elapsed instead of re-latching at now and re-injecting full energy."""
    from datetime import timedelta

    import homeassistant.util.dt as dt_util

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    started = dt_util.utcnow() - timedelta(minutes=40)
    coordinator._appliance_started["dw"] = started
    await coordinator.async_flush_persistent_state()
    coordinator._appliance_started.clear()
    await coordinator.async_load_persistent_state()
    assert coordinator._appliance_started.get("dw") == started


async def test_appliance_detection_hysteresis(hass):
    """H2: a run stays latched until power drops below the OFF threshold; a brief
    dip above it does not reset the run; a sensor dropout holds the last state."""
    from custom_components.battery_manager.const import (
        CONF_APPLIANCE_DETECTION_ENTITY,
        CONF_APPLIANCE_OFF_THRESHOLD_W,
        CONF_APPLIANCE_POWER_THRESHOLD_W,
    )

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    dw = "sensor.dishwasher_power"
    data = {
        CONF_APPLIANCE_DETECTION_ENTITY: dw,
        CONF_APPLIANCE_POWER_THRESHOLD_W: 20.0,
        CONF_APPLIANCE_OFF_THRESHOLD_W: 5.0,
    }
    hass.states.async_set(dw, "50")
    assert coordinator._appliance_is_running(data, latched=False) is True  # start >= 20
    hass.states.async_set(dw, "10")  # dip: below on (20), above off (5)
    assert coordinator._appliance_is_running(data, latched=False) is False  # would not start
    assert coordinator._appliance_is_running(data, latched=True) is True  # stays latched
    hass.states.async_set(dw, "3")  # below off threshold
    assert coordinator._appliance_is_running(data, latched=True) is False  # run ends
    hass.states.async_set(dw, "unavailable")  # sensor dropout
    assert coordinator._appliance_is_running(data, latched=True) is True  # holds last
    assert coordinator._appliance_is_running(data, latched=False) is False

    # Back-compat: no off threshold configured -> off == on threshold (no hysteresis).
    data_no_hys = {
        CONF_APPLIANCE_DETECTION_ENTITY: dw,
        CONF_APPLIANCE_POWER_THRESHOLD_W: 20.0,
    }
    hass.states.async_set(dw, "10")
    assert coordinator._appliance_is_running(data_no_hys, latched=True) is False


# ---------------------------------------------------------------------------
# F-PREDRAIN F1 (WP1): hourly PV forecast attribute reading
# ---------------------------------------------------------------------------


async def test_coordinator_reads_wh_period_hourly_forecast(hass):
    """The coordinator reads each PV entity's hourly `wh_period` attribute, parses
    naive keys as local and aware/UTC keys with conversion, sums sub-hour buckets,
    skips malformed entries, and labels the per-day PV source."""
    from datetime import datetime

    import homeassistant.util.dt as dt_util

    await hass.config.async_set_time_zone("Europe/Berlin")

    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.test_soc",
        "55",
        {"unit_of_measurement": "%", "device_class": "battery"},
    )
    # Open-Meteo shape: naive-local hourly keys, plus a malformed key and a
    # malformed value that must both be skipped.
    hass.states.async_set(
        "sensor.pv_today",
        "10.0",
        {
            "unit_of_measurement": "kWh",
            "wh_period": {
                "2026-07-10 10:00:00": 1000.0,
                "2026-07-10 11:00:00": 1500.0,
                "not a datetime": 42.0,
                "2026-07-10 12:00:00": "bad-value",
            },
        },
    )
    # Balcony-forecast shape: aware UTC 15-min buckets. UTC 08:00 = local 10:00
    # (CEST, +2 h); the four quarter-hours must sum into that one local hour.
    hass.states.async_set(
        "sensor.pv_tomorrow",
        "12.0",
        {
            "unit_of_measurement": "kWh",
            "wh_period": {
                "2026-07-11T08:00:00+00:00": 100.0,
                "2026-07-11T08:15:00+00:00": 150.0,
                "2026-07-11T08:30:00+00:00": 200.0,
                "2026-07-11T08:45:00+00:00": 50.0,
            },
        },
    )
    # No wh_period -> this day stays two-window.
    hass.states.async_set(
        "sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"}
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.last_update_success  # the map is plumbed into build_slots

    pv_map = coordinator._get_pv_hourly(dt_util.now())
    assert pv_map is not None
    # Naive-local keys parsed as local.
    assert pv_map[datetime(2026, 7, 10, 10, 0)] == 1000.0
    assert pv_map[datetime(2026, 7, 10, 11, 0)] == 1500.0
    # Malformed key and malformed value are both skipped.
    assert datetime(2026, 7, 10, 12, 0) not in pv_map
    # Aware UTC keys converted to local (+2 h); the 15-min buckets are summed.
    assert pv_map[datetime(2026, 7, 11, 10, 0)] == 500.0
    # All keys are naive (tzinfo dropped).
    assert all(key.tzinfo is None for key in pv_map)

    # Per-day source labelling for a horizon anchored on the fixture day.
    fixture_now = datetime(2026, 7, 10, 9, 0, tzinfo=dt_util.get_default_time_zone())
    sources = coordinator._pv_day_sources(fixture_now, 3, pv_map)
    assert sources == {
        "2026-07-10": "hourly",
        "2026-07-11": "hourly",
        "2026-07-12": "two_window",
    }


async def test_appliance_stale_start_reanchors_after_restart(hass):
    """H1 restart edge: a persisted start whose run already fully elapsed (run
    finished during downtime while a NEW run is active) is re-anchored to now on
    the first post-restart evaluation, so the active run is not omitted at 0."""
    from datetime import timedelta

    import homeassistant.util.dt as dt_util
    from homeassistant.config_entries import ConfigSubentryData

    from custom_components.battery_manager.const import (
        CONF_APPLIANCE_DETECTION_ENTITY,
        CONF_APPLIANCE_POWER_THRESHOLD_W,
        CONF_APPLIANCE_RUN_DURATION_H,
        CONF_APPLIANCE_RUN_ENERGY_WH,
        SUBENTRY_TYPE_APPLIANCE,
    )

    _set_input_states(hass)
    hass.states.async_set("sensor.dw_power", "500")  # appliance running
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2,
        subentries_data=[
            ConfigSubentryData(
                data={
                    CONF_APPLIANCE_DETECTION_ENTITY: "sensor.dw_power",
                    CONF_APPLIANCE_POWER_THRESHOLD_W: 20.0,
                    CONF_APPLIANCE_RUN_ENERGY_WH: 1000.0,
                    CONF_APPLIANCE_RUN_DURATION_H: 2.0,
                },
                subentry_type=SUBENTRY_TYPE_APPLIANCE, title="Dishwasher", unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    sub_id = next(iter(entry.subentries))
    now = dt_util.utcnow()
    # Restored start 3 h ago (run 2 h finished during downtime); a new run is on.
    coordinator._appliance_started[sub_id] = now - timedelta(hours=3)
    coordinator._appliance_started_restored.add(sub_id)
    runs = coordinator._get_appliance_runs(now)
    assert len(runs) == 1  # NOT omitted at 0 remaining
    assert runs[0].remaining_hours > 1.9  # re-anchored -> ~full 2 h run
    assert coordinator._appliance_started[sub_id] == now
