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
    assert (
        coordinator._appliance_is_running(data, latched=False) is False
    )  # would not start
    assert (
        coordinator._appliance_is_running(data, latched=True) is True
    )  # stays latched
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
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.last_update_success  # the map is plumbed into build_slots

    pv_map, _p10, _p90 = coordinator._get_pv_hourly(dt_util.now())
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


async def test_pv_hourly_per_entity_cache_survives_one_unavailable(hass):
    """FIX-4: a cycle where ONE forecast entity goes unavailable must not clobber
    the merged map with a partial read — each entity keeps its own last-good
    buckets. After a full read, today's entity going unavailable still yields
    today's buckets from the per-entity cache, alongside the still-fresh ones."""
    from datetime import datetime

    import homeassistant.util.dt as dt_util

    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set(
        "sensor.pv_today",
        "10.0",
        {"unit_of_measurement": "kWh", "wh_period": {"2026-07-10 10:00:00": 1000.0}},
    )
    hass.states.async_set(
        "sensor.pv_tomorrow",
        "12.0",
        {"unit_of_measurement": "kWh", "wh_period": {"2026-07-11 11:00:00": 700.0}},
    )
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    now = dt_util.now()

    full, _p10, _p90 = coordinator._get_pv_hourly(now)
    assert full[datetime(2026, 7, 10, 10, 0)] == 1000.0
    assert full[datetime(2026, 7, 11, 11, 0)] == 700.0

    # Today's entity drops out: the partial read must NOT overwrite the cached
    # full map — today's buckets survive from the per-entity cache.
    hass.states.async_set("sensor.pv_today", "unavailable")
    merged, _p10, _p90 = coordinator._get_pv_hourly(now)
    assert merged[datetime(2026, 7, 10, 10, 0)] == 1000.0  # cached, not lost
    assert merged[datetime(2026, 7, 11, 11, 0)] == 700.0  # still fresh


async def test_wh_period_skips_nonfinite_and_clamps_negative(hass):
    """FIX-9: NaN/±inf buckets are skipped and negative buckets clamped to 0, so a
    bad forecast attribute yields a finite, non-negative hourly map."""
    import math
    from datetime import datetime

    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set(
        "sensor.pv_today",
        "10.0",
        {
            "unit_of_measurement": "kWh",
            "wh_period": {
                "2026-07-10 10:00:00": float("nan"),
                "2026-07-10 11:00:00": float("inf"),
                "2026-07-10 12:00:00": -500.0,
                "2026-07-10 13:00:00": 800.0,
            },
        },
    )
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]

    m = coordinator._read_wh_period("sensor.pv_today")
    assert all(math.isfinite(v) and v >= 0.0 for v in m.values())  # finite, non-neg
    assert m[datetime(2026, 7, 10, 12, 0)] == 0.0  # -500 clamped to 0
    assert m[datetime(2026, 7, 10, 13, 0)] == 800.0  # good value kept
    assert datetime(2026, 7, 10, 10, 0) not in m  # NaN skipped
    assert datetime(2026, 7, 10, 11, 0) not in m  # inf skipped


async def test_hourly_mode_warns_once_when_no_wh_period(hass, caplog):
    """FIX-10: explicit "hourly" mode logs exactly ONE warning (state-change
    guarded, not per cycle) when no wh_period data is available; it re-arms only
    after data returns."""
    import logging

    import homeassistant.util.dt as dt_util

    from custom_components.battery_manager.const import PV_FORECAST_MODE_HOURLY

    entry = await _setup_entry(hass)  # entities carry no wh_period
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._pv_forecast_mode = PV_FORECAST_MODE_HOURLY
    coordinator._pv_hourly_empty_warned = False
    now = dt_util.now()

    def _warned():
        return [
            r
            for r in caplog.records
            if "hourly PV mode active but no wh_period" in r.getMessage()
        ]

    with caplog.at_level(logging.WARNING):
        assert coordinator._get_pv_hourly(now) == (None, None, None)
        # second cycle: no repeat
        assert coordinator._get_pv_hourly(now) == (None, None, None)
    assert len(_warned()) == 1  # state-change guarded: emitted exactly once


async def test_night_predrain_logs_only_on_change(hass, caplog):
    """FIX-11: the F-PREDRAIN night-charge line is emitted only when the set of
    night-booked (load, slot-start) pairs CHANGES, not every 5-min cycle."""
    import logging
    from datetime import datetime
    from types import SimpleNamespace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        LoadPlan,
        SurplusLoad,
        SystemConfig,
    )

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    load = SurplusLoad(load_id="deh", name="Entfeuchter", nominal_power_w=400.0)
    config = SystemConfig(loads=(load,))
    slots = tuple(
        HourSlot(
            index=i,
            start=datetime(2026, 7, 10, 3 + i, 0),
            duration=1.0,
            hour_of_day=3 + i,
            pv_wh=0.0,
            ac_wh=0.0,
            dc_wh=0.0,
        )
        for i in range(3)
    )
    inputs = SimpleNamespace(slots=slots)

    def result(start):
        return SimpleNamespace(
            load_plans=(
                LoadPlan(
                    load_id="deh",
                    schedule=(True,) * 3,
                    planned_energy_wh=400.0,
                    allocations=((start, 1, 2, 400.0),),
                    run_hours=(1.0,) * 3,
                ),
            ),
            pv_window_ends={},  # no PV window -> every pass-2 slot counts as night
            import_trade_used_wh=10.0,
        )

    def _count():
        return sum(
            1 for r in caplog.records if "preemptive night charging" in r.getMessage()
        )

    with caplog.at_level(logging.INFO):
        coordinator._log_night_predrain(result(0), inputs, config)
        assert _count() == 1
        coordinator._log_night_predrain(result(0), inputs, config)  # identical set
        assert _count() == 1  # not repeated
        coordinator._log_night_predrain(result(1), inputs, config)  # changed slot
        assert _count() == 2


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
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={
                    CONF_APPLIANCE_DETECTION_ENTITY: "sensor.dw_power",
                    CONF_APPLIANCE_POWER_THRESHOLD_W: 20.0,
                    CONF_APPLIANCE_RUN_ENERGY_WH: 1000.0,
                    CONF_APPLIANCE_RUN_DURATION_H: 2.0,
                },
                subentry_type=SUBENTRY_TYPE_APPLIANCE,
                title="Dishwasher",
                unique_id=None,
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


# ---------------------------------------------------------------------------
# F-LOAD-PRIORITY R3/R7: SystemConfig.loads is built in effective priority
# order (stored per-load priority; legacy fallback: insertion position).
# ---------------------------------------------------------------------------


async def _setup_entry_with_load_data(hass, loads):
    """An entry with load subentries built from (title, data) pairs."""
    from homeassistant.config_entries import ConfigSubentryData

    from custom_components.battery_manager.const import SUBENTRY_TYPE_LOAD

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=data,
                subentry_type=SUBENTRY_TYPE_LOAD,
                title=title,
                unique_id=None,
            )
            for title, data in loads
        ],
    )
    entry.add_to_hass(hass)
    _set_input_states(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    title_by_id = {sid: sub.title for sid, sub in entry.subentries.items()}
    return coordinator, title_by_id


def _load_titles(coordinator, title_by_id):
    config = coordinator.build_system_config()
    return [title_by_id[load.load_id] for load in config.loads]


async def test_load_order_legacy_is_insertion_order(hass):
    """R3 regression anchor: with NO stored priorities anywhere the loads sort
    exactly like the raw insertion (creation) order — the pre-v0.8.2 behaviour."""
    from custom_components.battery_manager.const import CONF_LOAD_POWER_W

    coordinator, titles = await _setup_entry_with_load_data(
        hass,
        [(t, {CONF_LOAD_POWER_W: 300.0}) for t in ("A", "B", "C")],
    )
    assert _load_titles(coordinator, titles) == ["A", "B", "C"]


async def test_load_order_explicit_priorities_reorder(hass):
    """Planner effect smoke test: two loads with inverted stored priorities
    produce config.loads in inverted order (1 = highest priority first)."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_POWER_W,
        CONF_LOAD_PRIORITY,
    )

    coordinator, titles = await _setup_entry_with_load_data(
        hass,
        [
            ("A", {CONF_LOAD_POWER_W: 300.0, CONF_LOAD_PRIORITY: 2}),
            ("B", {CONF_LOAD_POWER_W: 400.0, CONF_LOAD_PRIORITY: 1}),
        ],
    )
    assert _load_titles(coordinator, titles) == ["B", "A"]


async def test_load_order_mixed_stored_wins_insertion_breaks_ties(hass):
    """R7 (legacy mix): stored values win positions, ties broken by insertion —
    a keyless A (pseudo-priority 1 by position) stays ahead of C's stored 1,
    while C overtakes the keyless B (pseudo 2)."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_POWER_W,
        CONF_LOAD_PRIORITY,
    )

    coordinator, titles = await _setup_entry_with_load_data(
        hass,
        [
            ("A", {CONF_LOAD_POWER_W: 300.0}),  # legacy: pseudo (1, pos 0)
            ("B", {CONF_LOAD_POWER_W: 300.0}),  # legacy: pseudo (2, pos 1)
            ("C", {CONF_LOAD_POWER_W: 300.0, CONF_LOAD_PRIORITY: 1}),  # (1, pos 2)
        ],
    )
    assert _load_titles(coordinator, titles) == ["A", "C", "B"]


# ---------------------------------------------------------------------------
# F-PLANNER-HONESTY F3: explain-plan surfaced through the coordinator data
# dict and the SOC-forecast sensor (docs/F-PLANNER-HONESTY.md R5/R14/R15).
# ---------------------------------------------------------------------------


async def test_load_plan_dict_carries_why_and_learned_power(hass):
    """R14: every per-load schedule entry carries the acceptance reason as
    `why` (matching its `pass`), and the dict exposes `learned_power_w` next
    to the existing diagnostics (R5); the SOC-forecast sensor attribute
    passes the schedule entries through verbatim.

    Placement-agnostic on purpose: an EMPTY energy-limited load with a high
    house SOC and strong PV on every horizon day books pass-1 surplus hours
    at any wall-clock hour — the assertions iterate whatever entries exist
    instead of pinning slots."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_ENERGY_LIMITED,
        CONF_LOAD_POWER_W,
        ENTITY_SOC_FORECAST_CURVE,
    )

    coordinator, _titles = await _setup_entry_with_load_data(
        hass,
        [
            (
                "F1",
                {
                    CONF_LOAD_POWER_W: 300.0,
                    CONF_LOAD_ENERGY_LIMITED: True,
                    CONF_LOAD_CAPACITY_WH: 2000.0,
                },
            )
        ],
    )
    entry = coordinator.entry
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(
        "sensor.test_soc", "93", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    coordinator._load_learned_power_w[sub_id] = 505.4
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    plan_dict = coordinator.data["load_plans"][sub_id]
    assert plan_dict["learned_power_w"] == 505.4  # R5 diagnostics
    schedule = plan_dict["schedule"]
    assert schedule, "scenario must book at least one slot"
    for entry_row in schedule:
        assert entry_row["why"].startswith(f"pass {entry_row['pass']} @ ")

    # The sensor's `loads` attribute carries the same schedule entries.
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_SOC_FORECAST_CURVE}"
    )
    assert eid is not None
    attrs = hass.states.get(eid).attributes
    sensor_schedule = attrs["loads"][0]["schedule"]
    assert sensor_schedule and all("why" in row for row in sensor_schedule)


# ---------------------------------------------------------------------------
# F-PERDAY-SURPLUS: per-calendar-day lost-surplus / grid-import breakdown
# (docs/F-PERDAY-SURPLUS.md R1-R3).
# ---------------------------------------------------------------------------


async def test_daily_surplus_breakdown_splits_by_start_day(hass):
    """R1: the trajectory is grouped by each slot's planner-local START day, so a
    23:00 slot lands on its start day even where it conceptually crosses midnight
    (D-A7); the per-day kWh match a hand computation and their sums equal the
    existing totals (rounding aside)."""
    from datetime import datetime
    from types import SimpleNamespace

    from custom_components.battery_manager.core.model import HourSlot

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Two calendar days: day-1 22:00 + 23:00, day-2 00:00 + 01:00. The 23:00
    # slot carries export and must be attributed to day-1, not day-2.
    starts = [
        datetime(2026, 7, 10, 22, 0),
        datetime(2026, 7, 10, 23, 0),
        datetime(2026, 7, 11, 0, 0),
        datetime(2026, 7, 11, 1, 0),
    ]
    slots = tuple(
        HourSlot(
            index=i,
            start=s,
            duration=1.0,
            hour_of_day=s.hour,
            pv_wh=0.0,
            ac_wh=0.0,
            dc_wh=0.0,
        )
        for i, s in enumerate(starts)
    )
    # (export_wh, import_wh, extra_ac_wh) per slot — extra_ac_wh is the
    # surplus-load energy (F-PERDAY-SURPLUS §5 v2).
    flows_wh = [
        (300.0, 0.0, 150.0),
        (700.0, 50.0, 0.0),
        (0.0, 200.0, 400.0),
        (1000.0, 0.0, 0.0),
    ]
    flows = tuple(
        SimpleNamespace(grid_export_wh=e, grid_import_wh=i, extra_ac_wh=x)
        for e, i, x in flows_wh
    )
    inputs = SimpleNamespace(slots=slots)
    result = SimpleNamespace(trajectory=SimpleNamespace(flows=flows))

    daily = coordinator._daily_surplus_breakdown(inputs, result)

    assert [d["date"] for d in daily] == ["2026-07-10", "2026-07-11"]  # chronological
    # Day-1: export 300 + 700 = 1000 Wh (23:00 counts here), import 50 Wh,
    # surplus-load energy 150 Wh.
    assert daily[0] == {
        "date": "2026-07-10",
        "lost_surplus_kwh": 1.0,
        "grid_import_kwh": 0.05,
        "loads_kwh": 0.15,
    }
    # Day-2: export 0 + 1000 = 1000 Wh, import 200 Wh, loads 400 Wh.
    assert daily[1] == {
        "date": "2026-07-11",
        "lost_surplus_kwh": 1.0,
        "grid_import_kwh": 0.2,
        "loads_kwh": 0.4,
    }
    # R1 / R-V2-3 invariant: the sums equal the trajectory totals.
    total_export = sum(e for e, _i, _x in flows_wh) / 1000.0
    total_import = sum(i for _e, i, _x in flows_wh) / 1000.0
    total_loads = sum(x for _e, _i, x in flows_wh) / 1000.0
    assert sum(d["lost_surplus_kwh"] for d in daily) == total_export
    assert sum(d["grid_import_kwh"] for d in daily) == total_import
    assert sum(d["loads_kwh"] for d in daily) == total_loads


async def test_forecast_sensors_expose_per_day_attributes(hass):
    """R2/R3: the lost-surplus and grid-import forecast sensors carry
    today_kwh/tomorrow_kwh/daily and the SOC-forecast sensor carries the same
    daily list; on real plan data the per-day sums equal the totals (R1)."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.battery_manager.const import (
        ENTITY_GRID_IMPORT_FORECAST,
        ENTITY_LOST_SURPLUS,
        ENTITY_SOC_FORECAST_CURVE,
    )

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    daily = coordinator.data["daily_surplus"]
    assert daily and all(
        {"date", "lost_surplus_kwh", "grid_import_kwh", "loads_kwh"} <= d.keys()
        for d in daily
    )
    today = daily[0]["date"]  # today = date of slot 0

    reg = er.async_get(hass)

    def _attrs(entity_key):
        eid = reg.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{entity_key}"
        )
        assert eid is not None
        return hass.states.get(eid).attributes

    for entity_key, value_key in (
        (ENTITY_LOST_SURPLUS, "lost_surplus_kwh"),
        (ENTITY_GRID_IMPORT_FORECAST, "grid_import_kwh"),
    ):
        attrs = _attrs(entity_key)
        assert attrs["daily"] == daily  # single source
        # today_kwh is the value_key metric of the slot-0 day.
        assert attrs["today_kwh"] == daily[0][value_key]
        assert isinstance(attrs["tomorrow_kwh"], float)
        # R1 invariant on real data: Σ daily == the sensor's own total, rounding
        # aside — each day is rounded to 3 decimals independently of the total,
        # so the sum can differ by up to half a milli-kWh per day.
        total = coordinator.data[value_key]
        assert abs(sum(d[value_key] for d in daily) - total) <= 0.0005 * len(daily)
        assert today  # slot-0 day present

    # R3: the SOC-forecast sensor exposes the same daily list.
    soc_attrs = _attrs(ENTITY_SOC_FORECAST_CURVE)
    assert soc_attrs["daily"] == daily
    # §5 v2 (R-V2-2): loads_today/tomorrow follow the exact same slot-0-day
    # convention; (R-V2-3) the per-day loads sum equals the horizon total of
    # extra_ac_wh (surplus loads only — appliances enter the AC forecast).
    assert soc_attrs["loads_today_kwh"] == daily[0]["loads_kwh"]
    assert isinstance(soc_attrs["loads_tomorrow_kwh"], float)
    total_loads_wh = sum(
        h["surplus_load_wh"] for h in coordinator.data["hourly_details"]
    )
    assert abs(
        sum(d["loads_kwh"] for d in daily) - total_loads_wh / 1000.0
    ) <= 0.0005 * len(daily)


def test_per_day_attrs_falls_back_to_zero_for_missing_day():
    """R2: today_kwh/tomorrow_kwh fall back to 0.0 when the horizon lacks that
    day; today is the date of slot 0 (the first entry) and tomorrow is today+1."""
    from custom_components.battery_manager.sensor import _per_day_attrs

    # Horizon covers only today (slot-0 day); tomorrow is absent.
    daily = [{"date": "2026-07-11", "lost_surplus_kwh": 2.5, "grid_import_kwh": 0.4}]
    attrs = _per_day_attrs(daily, "lost_surplus_kwh")
    assert attrs["today_kwh"] == 2.5
    assert attrs["tomorrow_kwh"] == 0.0  # day absent -> 0.0
    assert attrs["daily"] == daily

    # Empty horizon -> both scalars fall back to 0.0.
    empty = _per_day_attrs([], "grid_import_kwh")
    assert empty["today_kwh"] == 0.0 and empty["tomorrow_kwh"] == 0.0
    assert empty["daily"] == []


# ---------------------------------------------------------------------------
# F-QUANTILE-BANDS R13: quantile-attribute ingestion (same parse/cache path as
# the median wh_period, docs/F-QUANTILE-BANDS.md R1) and the per-day coverage
# diagnostics (R7).
# ---------------------------------------------------------------------------


async def test_pv_hourly_parses_quantile_attributes_with_stale_cache(hass):
    """R1/R13: wh_period_p10/p90 are parsed from the SAME entities with the
    same tolerance rules; garbage quantile attributes yield nothing (never an
    error); a dropout serves median AND bands from the same per-entity cache
    entry, so stale medians can never pair with other-day bands."""
    from datetime import datetime

    import homeassistant.util.dt as dt_util

    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set(
        "sensor.pv_today",
        "10.0",
        {
            "unit_of_measurement": "kWh",
            "wh_period": {"2026-07-10 10:00:00": 1000.0},
            "wh_period_p10": {"2026-07-10 10:00:00": 600.0},
            "wh_period_p90": {"2026-07-10 10:00:00": 1400.0},
        },
    )
    hass.states.async_set(
        "sensor.pv_tomorrow",
        "12.0",
        {
            "unit_of_measurement": "kWh",
            "wh_period": {"2026-07-11 11:00:00": 700.0},
            "wh_period_p10": "garbage, not a dict",  # tolerated -> no p10
            # no wh_period_p90 at all -> no p90 for this day
        },
    )
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    now = dt_util.now()

    median, p10, p90 = coordinator._get_pv_hourly(now)
    assert median[datetime(2026, 7, 10, 10, 0)] == 1000.0
    assert median[datetime(2026, 7, 11, 11, 0)] == 700.0
    # Quantiles cover only where the attributes did (partial coverage is fine).
    assert p10 == {datetime(2026, 7, 10, 10, 0): 600.0}
    assert p90 == {datetime(2026, 7, 10, 10, 0): 1400.0}

    # Dropout: median AND bands survive together from the same cache entry.
    hass.states.async_set("sensor.pv_today", "unavailable")
    median2, p10_2, p90_2 = coordinator._get_pv_hourly(now)
    assert median2[datetime(2026, 7, 10, 10, 0)] == 1000.0
    assert p10_2 == {datetime(2026, 7, 10, 10, 0): 600.0}
    assert p90_2 == {datetime(2026, 7, 10, 10, 0): 1400.0}


async def test_quantile_coverage_attribute_wiring(hass):
    """R7/R13: the coordinator computes per-day daylight band coverage from
    the planner's own D2 predicate, the data dict and the SOC-forecast sensor
    carry it, and a partially banded day reads "mixed"."""
    from datetime import datetime

    from homeassistant.helpers import entity_registry as er

    from custom_components.battery_manager.const import ENTITY_SOC_FORECAST_CURVE
    from custom_components.battery_manager.core import SystemConfig, build_slots

    entry = await _setup_entry(hass)  # no band data anywhere
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Live wiring: without any p10/p90 data every day reads "scalar".
    coverage = coordinator.data["quantile_coverage"]
    assert coverage and all(day["source"] == "scalar" for day in coverage.values())
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_SOC_FORECAST_CURVE}"
    )
    assert eid is not None
    attrs = hass.states.get(eid).attributes
    assert attrs["quantile_coverage"] == coverage

    # Deterministic mixed-coverage computation on hand-built inputs: bands on
    # two of the daylight hours of day 1 only.
    cfg = SystemConfig()
    now = datetime(2026, 7, 4, 8, 0)
    base = build_slots(cfg, now, 55.0, [10.0, 12.0])
    banded = [s for s in base.slots if s.pv_wh >= 25.0 and s.duration == 1.0][:2]
    p10 = {s.start: s.pv_wh * 0.7 for s in banded}
    p90 = {s.start: s.pv_wh * 1.3 for s in banded}
    inputs = build_slots(
        cfg, now, 55.0, [10.0, 12.0], pv_hourly_p10=p10, pv_hourly_p90=p90
    )
    mixed = coordinator._quantile_coverage(inputs)
    assert mixed["2026-07-04"]["source"] == "mixed"
    assert 0.0 < mixed["2026-07-04"]["coverage"] < 1.0
    assert mixed["2026-07-05"] == {"coverage": 0.0, "source": "scalar"}
