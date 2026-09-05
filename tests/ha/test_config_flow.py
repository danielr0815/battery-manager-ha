"""Config/options flow smoke tests (schema construction must never raise)."""

from types import SimpleNamespace

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up. Since HA 2026.x (eager task
    scheduling) platform forwarding completes inside the test body, the
    coordinator's refresh-interval timer is armed already — and phacc's
    verify_cleanup flags it as a lingering timer at teardown unless the
    entry is unloaded (test_coordinator.py unloads explicitly for the same
    reason). The drain comes FIRST: creating a subentry / submitting the
    options flow fires an update listener that schedules an entry reload,
    and unloading a still-SETUP_IN_PROGRESS entry raises
    OperationNotAllowed. Module-level autouse so no call site changes.
    """
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


def _section_fields(schema, section_key):
    """The inner {marker: validator} dict of a collapsible options section."""
    marker = next(k for k in schema if str(k) == section_key)
    return schema[marker].schema.schema


def _marker_default(marker):
    """The resolved default of a schema marker, or vol.UNDEFINED when unset."""
    default = getattr(marker, "default", vol.UNDEFINED)
    if default is vol.UNDEFINED:
        return vol.UNDEFINED
    return default() if callable(default) else default


def _no_change_options_payload(schema):
    """Build the payload a no-change options submit produces: every section's
    fields at their RENDERED defaults; clearable (no-default) fields stay unset."""
    payload = {}
    for section_marker in schema:
        inner = schema[section_marker].schema.schema
        section_data = {}
        for marker in inner:
            default = _marker_default(marker)
            if default is not vol.UNDEFINED:
                section_data[str(marker)] = default
        payload[str(section_marker)] = section_data
    return payload


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
    # The tuning settings are grouped into collapsible sections now.
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert {
        "planner_tuning",
        "consumption_profile",
        "consumption_learning",
        "support_paths",
        "dc_devices",
    } <= schema_keys


async def test_options_flow_flattens_sections_on_submit(hass):
    """Sections nest their fields in the submitted data; the stored options
    must be flat (the rest of the integration reads a flat config)."""
    from custom_components.battery_manager.const import (
        CONF_DC24_SHARE_PERCENT,
        CONF_DCDC_EFFICIENCY,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Submit the nested section payload (as the HA frontend would).
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "battery": {
                "battery_capacity_wh": 5000.0,
                "battery_min_soc_percent": 5.0,
                "battery_max_soc_percent": 95.0,
            },
            "pv": {
                "pv_max_power_w": 3200.0,
                "pv_morning_start_hour": 7,
                "pv_morning_end_hour": 13,
                "pv_afternoon_end_hour": 18,
                "pv_morning_ratio": 0.8,
            },
            "power": {
                "charger_max_power_w": 2300.0,
                "charger_efficiency": 0.92,
                "charger_standby_power_w": 10.0,
                "inverter_max_power_w": 2300.0,
                "inverter_efficiency": 0.95,
                "inverter_standby_power_w": 15.0,
                "inverter_min_soc_percent": 20.0,
            },
            "planner_tuning": {
                "soc_buffer_percent": 6.0,
                "hysteresis_percent": 1.0,
                "threshold_inertia_percent": 2.0,
                "min_switch_interval_s": 60,
            },
            "consumption_profile": {
                "ac_base_load_w": 50.0,
                "ac_variable_load_w": 75.0,
                "ac_variable_start_hour": 6,
                "ac_variable_end_hour": 20,
                "dc_base_load_w": 50.0,
                "dc_variable_load_w": 25.0,
                "dc_variable_start_hour": 6,
                "dc_variable_end_hour": 22,
            },
            "consumption_learning": {
                "learning_window_days": 120,
                "learning_max_age_days": 14,
                "profile_half_life_days": 30,
                "buffer_min_percent": 3.0,
                "buffer_max_percent": 15.0,
            },
            "support_paths": {
                "support_dc48_power_w": 60.0,
                "support_switch_delay_s": 3,
            },
            "dc_devices": {
                CONF_DC24_SHARE_PERCENT: 80.0,
                CONF_DCDC_EFFICIENCY: 0.93,
                "dcdc_output_voltage_v": 24.3,
                "dcdc_max_current_a": 20.0,
                "psu24_output_voltage_v": 24.05,
                "psu24_efficiency": 0.89,
                "psu24_max_current_a": 25.0,
                "psu48_output_voltage_v": 49.56,
                "psu48_efficiency": 0.89,
                "psu48_max_current_a": 1.15,
                "battery_cells_series": 15,
                "gate_soc_percent": 100.0,
            },
            "early_feed_in": {},
            # F-REALIZED-SURPLUS: the new options section is vol.Required like
            # the others; empty = feature off (no export meter).
            "surplus_accounting": {},
            "notifications": {},
        },
    )
    assert result["type"] == "create_entry"
    opts = result["data"]
    # Stored flat, not nested — a real value from each section.
    assert opts["soc_buffer_percent"] == 6.0
    assert opts[CONF_DC24_SHARE_PERCENT] == 80.0
    assert opts[CONF_DCDC_EFFICIENCY] == 0.93
    assert opts["battery_cells_series"] == 15
    assert opts["battery_capacity_wh"] == 5000.0
    assert opts["inverter_min_soc_percent"] == 20.0
    assert "planner_tuning" not in opts  # section wrappers removed
    assert "battery" not in opts


async def test_options_flow_rejects_inverted_controller_band(hass):
    """Review finding: the R2 controller off-voltage must be validated ABOVE
    the on-voltage in the OPTIONS flow too (not only the setup wizard), else a
    collapsed hysteresis band saves silently and the controller chatters."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "battery": {
                "battery_capacity_wh": 5000.0,
                "battery_min_soc_percent": 5.0,
                "battery_max_soc_percent": 95.0,
            },
            "pv": {
                "pv_max_power_w": 3200.0,
                "pv_morning_start_hour": 7,
                "pv_morning_end_hour": 13,
                "pv_afternoon_end_hour": 18,
                "pv_morning_ratio": 0.8,
            },
            "power": {
                "charger_max_power_w": 2300.0,
                "charger_efficiency": 0.92,
                "charger_standby_power_w": 10.0,
                "inverter_max_power_w": 2300.0,
                "inverter_efficiency": 0.95,
                "inverter_standby_power_w": 15.0,
                "inverter_min_soc_percent": 20.0,
            },
            "planner_tuning": {
                "soc_buffer_percent": 6.0,
                "hysteresis_percent": 1.0,
                "threshold_inertia_percent": 2.0,
                "min_switch_interval_s": 60,
            },
            "consumption_profile": {
                "ac_base_load_w": 50.0,
                "ac_variable_load_w": 75.0,
                "ac_variable_start_hour": 6,
                "ac_variable_end_hour": 20,
                "dc_base_load_w": 50.0,
                "dc_variable_load_w": 25.0,
                "dc_variable_start_hour": 6,
                "dc_variable_end_hour": 22,
            },
            "consumption_learning": {
                "learning_window_days": 120,
                "learning_max_age_days": 14,
                "profile_half_life_days": 30,
                "buffer_min_percent": 3.0,
                "buffer_max_percent": 15.0,
            },
            "support_paths": {
                "support_dc48_power_w": 60.0,
                "support_switch_delay_s": 3,
            },
            "dc_devices": {
                "dc24_share_percent": 100.0,
                "dcdc_output_voltage_v": 24.0,
                "dcdc_efficiency": 1.0,
                "dcdc_max_current_a": 0.0,
                "psu24_output_voltage_v": 24.0,
                "psu24_efficiency": 1.0,
                "psu24_max_current_a": 0.0,
                "psu48_output_voltage_v": 49.56,
                "psu48_efficiency": 1.0,
                "psu48_max_current_a": 0.0,
                "battery_cells_series": 16,
                "gate_soc_percent": 100.0,
                "psu48_on_voltage_v": 49.8,  # inverted: on above off
                "psu48_off_voltage_v": 49.56,
                "psu48_controller_log_only": False,
            },
            "early_feed_in": {},
            # F-REALIZED-SURPLUS: the new options section is vol.Required like
            # the others; empty = feature off (no export meter).
            "surplus_accounting": {},
            "notifications": {},
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "controller_off_below_on"}


async def test_options_flow_rejects_bad_support_hysteresis(hass):
    """v0.7.13: the four absolute escalation SOC thresholds must form a sane
    hysteresis ladder — each stage needs activate < recovery, and the 48 V
    last-resort stage must sit at/below the 24 V stage. Bad ladders are
    rejected; the operator's example ladder saves flat."""
    entry = await _setup_entry(hass)

    def payload(support_extra):
        support = {"support_dc48_power_w": 60.0, "support_switch_delay_s": 3}
        support.update(support_extra)
        return {
            "battery": {
                "battery_capacity_wh": 5000.0,
                "battery_min_soc_percent": 5.0,
                "battery_max_soc_percent": 95.0,
            },
            "pv": {
                "pv_max_power_w": 3200.0,
                "pv_morning_start_hour": 7,
                "pv_morning_end_hour": 13,
                "pv_afternoon_end_hour": 18,
                "pv_morning_ratio": 0.8,
            },
            "power": {
                "charger_max_power_w": 2300.0,
                "charger_efficiency": 0.92,
                "charger_standby_power_w": 10.0,
                "inverter_max_power_w": 2300.0,
                "inverter_efficiency": 0.95,
                "inverter_standby_power_w": 15.0,
                "inverter_min_soc_percent": 20.0,
            },
            "planner_tuning": {
                "soc_buffer_percent": 6.0,
                "hysteresis_percent": 1.0,
                "threshold_inertia_percent": 2.0,
                "min_switch_interval_s": 60,
            },
            "consumption_profile": {
                "ac_base_load_w": 50.0,
                "ac_variable_load_w": 75.0,
                "ac_variable_start_hour": 6,
                "ac_variable_end_hour": 20,
                "dc_base_load_w": 50.0,
                "dc_variable_load_w": 25.0,
                "dc_variable_start_hour": 6,
                "dc_variable_end_hour": 22,
            },
            "consumption_learning": {
                "learning_window_days": 120,
                "learning_max_age_days": 14,
                "profile_half_life_days": 30,
                "buffer_min_percent": 3.0,
                "buffer_max_percent": 15.0,
            },
            "support_paths": support,
            "dc_devices": {
                "dc24_share_percent": 80.0,
                "dcdc_efficiency": 0.93,
                "dcdc_output_voltage_v": 24.3,
                "dcdc_max_current_a": 20.0,
                "psu24_output_voltage_v": 24.05,
                "psu24_efficiency": 0.89,
                "psu24_max_current_a": 25.0,
                "psu48_output_voltage_v": 49.56,
                "psu48_efficiency": 0.89,
                "psu48_max_current_a": 1.15,
                "battery_cells_series": 15,
                "gate_soc_percent": 100.0,
            },
            "early_feed_in": {},
            # F-REALIZED-SURPLUS: the new options section is vol.Required like
            # the others; empty = feature off (no export meter).
            "surplus_accounting": {},
            "notifications": {},
        }

    async def submit(support_extra):
        res = await hass.config_entries.options.async_init(entry.entry_id)
        return await hass.config_entries.options.async_configure(
            res["flow_id"], payload(support_extra)
        )

    # 24 V recover-SOC not above its activate-SOC -> no dead band.
    res = await submit({"support_dc24_recovery_soc": 10.0})  # == default activate 10
    assert res["type"] == "form"
    assert res["errors"] == {"base": "support_dc24_recovery_not_above_activate"}

    # 48 V recover-SOC not above its activate-SOC.
    res = await submit({"support_dc48_recovery_soc": 4.0})  # < default activate 5.5
    assert res["type"] == "form"
    assert res["errors"] == {"base": "support_dc48_recovery_not_above_activate"}

    # 48 V activate above the 24 V activate -> deeper stage would fire later.
    res = await submit(
        {"support_dc48_activate_soc": 11.0, "support_dc48_recovery_soc": 13.0}
    )
    assert res["type"] == "form"
    assert res["errors"] == {"base": "support_dc48_activate_above_dc24"}

    # 48 V recover above the 24 V recover -> releases later than the 24 V stage.
    res = await submit({"support_dc48_recovery_soc": 13.0})  # > default 24 V recover 11
    assert res["type"] == "form"
    assert res["errors"] == {"base": "support_dc48_recovery_above_dc24"}

    # The operator's example ladder saves, stored flat.
    res = await submit(
        {
            "support_dc24_activate_soc": 10.0,
            "support_dc24_recovery_soc": 12.0,
            "support_dc48_activate_soc": 7.0,
            "support_dc48_recovery_soc": 10.0,
        }
    )
    assert res["type"] == "create_entry"
    assert res["data"]["support_dc24_recovery_soc"] == 12.0
    assert res["data"]["support_dc48_activate_soc"] == 7.0


async def test_predrain_options_no_change_reconfigure_is_behaviour_preserving(hass):
    """v0.7.15 review trap (F-PREDRAIN WP3): an existing install that never set
    the pre-drain options must (a) run with the RECOMMENDED live values via the
    coordinator's absent-key fallback, (b) get exactly those values as the
    options-form defaults, and (c) reproduce identical planner params after a
    no-change reconfigure — so re-saving the untouched form never alters
    behaviour."""
    from custom_components.battery_manager.const import (
        CONF_PREDRAIN_PV_CONFIDENCE,
        CONF_PV_FORECAST_MODE,
        CONF_PV_WINDOW_END_HOUR,
        CONF_STRONG_PV_CUTOFF_W,
        CONF_UPPER_PV_RESERVE,
        PREDRAIN_PV_CONFIDENCE_DEFAULT,
        PV_FORECAST_MODE_AUTO,
        STRONG_PV_CUTOFF_W_DEFAULT,
        UPPER_PV_RESERVE_DEFAULT,
    )

    entry = await _setup_entry(hass)  # no pre-drain options stored
    coord = hass.data[DOMAIN][entry.entry_id]

    # (a) The coordinator's absent-key fallback = the recommended live values,
    # so the feature is active right after the update. The retired
    # import_trade_ratio (F-STRICT-SURPLUS R1) stays at the neutral dataclass
    # default — the planner ignores it either way.
    ctrl = coord.build_system_config().control
    assert ctrl.import_trade_ratio == 0.0
    assert ctrl.predrain_pv_confidence == PREDRAIN_PV_CONFIDENCE_DEFAULT
    assert ctrl.upper_pv_reserve == UPPER_PV_RESERVE_DEFAULT
    assert ctrl.strong_pv_cutoff_w == STRONG_PV_CUTOFF_W_DEFAULT
    assert ctrl.pv_window_end_hour is None  # optional override unset
    assert coord._pv_forecast_mode == PV_FORECAST_MODE_AUTO

    # (b) The options form defaults the same fields to those exact values.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    tuning = _section_fields(result["data_schema"].schema, "planner_tuning")
    defaults = {
        str(m): _marker_default(m)
        for m in tuning
        if _marker_default(m) is not vol.UNDEFINED
    }
    assert defaults[CONF_PV_FORECAST_MODE] == PV_FORECAST_MODE_AUTO
    assert "import_trade_ratio" not in defaults  # retired field left the form
    assert defaults[CONF_PREDRAIN_PV_CONFIDENCE] == PREDRAIN_PV_CONFIDENCE_DEFAULT
    assert defaults[CONF_UPPER_PV_RESERVE] == UPPER_PV_RESERVE_DEFAULT
    assert defaults[CONF_STRONG_PV_CUTOFF_W] == STRONG_PV_CUTOFF_W_DEFAULT
    assert CONF_PV_WINDOW_END_HOUR not in defaults  # optional -> stays unset

    # (c) Submit the form untouched (its own rendered defaults) -> a coordinator
    # rebuilt from the stored options yields identical pre-drain params.
    payload = _no_change_options_payload(result["data_schema"].schema)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    opts = result["data"]
    assert "import_trade_ratio" not in opts  # retired field never re-persists
    assert CONF_PV_WINDOW_END_HOUR not in opts or opts[CONF_PV_WINDOW_END_HOUR] is None

    entry2 = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, options=opts, title="Battery Manager", version=2
    )
    entry2.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry2.entry_id)
    await hass.async_block_till_done()
    ctrl2 = hass.data[DOMAIN][entry2.entry_id].build_system_config().control
    assert ctrl2.import_trade_ratio == ctrl.import_trade_ratio
    assert ctrl2.predrain_pv_confidence == ctrl.predrain_pv_confidence
    assert ctrl2.upper_pv_reserve == ctrl.upper_pv_reserve
    assert ctrl2.strong_pv_cutoff_w == ctrl.strong_pv_cutoff_w
    assert ctrl2.pv_window_end_hour == ctrl.pv_window_end_hour


async def test_migrate_backfills_escalation_thresholds_from_soc_min(hass):
    """v2.2 -> 2.3 (v0.7.13): a pre-existing entry with a NON-default soc_min
    must keep its exact legacy grid-support switch points. The migration
    backfills the soc_min-derived absolute thresholds (not the fixed 10/11/5.5/10
    defaults, which would gut last-resort protection at soc_min=10)."""
    from custom_components.battery_manager import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            "battery_min_soc_percent": 10.0,
            "soc_buffer_percent": 5.0,
        },
        title="Battery Manager",
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 5
    # Legacy formula at soc_min=10, buffer=5: floor = 15.
    assert entry.options["support_dc24_activate_soc"] == 15.0
    assert entry.options["support_dc24_recovery_soc"] == 16.0
    assert entry.options["support_dc48_activate_soc"] == 10.5
    assert entry.options["support_dc48_recovery_soc"] == 15.0


async def test_migrate_is_neutral_at_default_soc_min(hass):
    """At the default battery config (soc_min 5, buffer 5) the backfilled values
    equal the absolute DEFAULT_CONFIG thresholds — the migration is a no-op."""
    from custom_components.battery_manager import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 5
    assert entry.options["support_dc24_activate_soc"] == 10.0
    assert entry.options["support_dc24_recovery_soc"] == 11.0
    assert entry.options["support_dc48_activate_soc"] == 5.5
    assert entry.options["support_dc48_recovery_soc"] == 10.0


async def test_migrate_preserves_explicit_escalation_values(hass):
    """A post-0.7.13 entry that already carries escalation thresholds must not be
    overwritten by the backfill — only the minor_version is advanced."""
    from custom_components.battery_manager import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={"support_dc24_activate_soc": 8.0},
        title="Battery Manager",
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 5
    assert entry.options["support_dc24_activate_soc"] == 8.0
    assert "support_dc48_activate_soc" not in entry.options


async def test_migrate_legacy_gate_default_to_40_percent(hass):
    """v2.4 -> 2.5 moves only the auto-persisted 100 % gate default to 40 %."""
    from custom_components.battery_manager import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={"gate_soc_percent": 100.0},
        title="Battery Manager",
        version=2,
        minor_version=4,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 5
    assert entry.options["gate_soc_percent"] == 40.0


async def test_migrate_preserves_calibrated_gate_soc(hass):
    """A calibrated pre-2.5 gate remains operator-owned."""
    from custom_components.battery_manager import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={"gate_soc_percent": 55.0},
        title="Battery Manager",
        version=2,
        minor_version=4,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 5
    assert entry.options["gate_soc_percent"] == 55.0


async def test_migrate_v1_removes_retired_keys_and_preserves_legacy_defaults(hass):
    """The complete v1 migration is behavioural, not a version-only smoke test:
    retired controller keys disappear, the learning window widens, and the
    auto-persisted voltage gate moves while unrelated data survives."""
    from custom_components.battery_manager import async_migrate_entry
    from custom_components.battery_manager.const import (
        CONF_GATE_SOC_PERCENT,
        CONF_LEARNING_WINDOW_DAYS,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            "controller_target_soc_percent": 80,
            CONF_GATE_SOC_PERCENT: 100.0,
            "operator_note": "keep",
        },
        options={
            "ac_additional_load_w": 250,
            CONF_LEARNING_WINDOW_DAYS: 42,
        },
        title="Battery Manager",
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 5)
    assert "controller_target_soc_percent" not in entry.data
    assert "ac_additional_load_w" not in entry.options
    assert entry.data["operator_note"] == "keep"
    assert entry.data[CONF_GATE_SOC_PERCENT] == 40.0
    assert entry.options[CONF_LEARNING_WINDOW_DAYS] == 120


async def test_migrate_rejects_unknown_future_major_version(hass):
    """A newer schema must remain untouched instead of being guessed at by an
    older integration version."""
    from custom_components.battery_manager import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, "future": True},
        title="Battery Manager",
        version=3,
        minor_version=0,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False
    assert entry.version == 3
    assert entry.data["future"] is True


async def test_pv_step_rejects_misordered_windows(hass):
    """Review #5: the PV step must reject windows that are not strictly ordered
    (morning_start < morning_end < afternoon_end), else a degenerate window
    silently discards forecast energy."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "battery_capacity_wh": 5000.0,
            "battery_min_soc_percent": 5.0,
            "battery_max_soc_percent": 95.0,
            "battery_charge_efficiency": 0.97,
            "battery_discharge_efficiency": 0.97,
        },
    )
    assert result["step_id"] == "pv"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "pv_max_power_w": 3200.0,
            "pv_morning_start_hour": 13,  # start after end: mis-ordered
            "pv_morning_end_hour": 7,
            "pv_afternoon_end_hour": 18,
            "pv_morning_ratio": 0.8,
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "pv"
    assert result["errors"] == {"base": "pv_windows_out_of_order"}


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
                # The consumers step is grouped into sections now.
                "consumption_profile": {
                    "ac_base_load_w": 50.0,
                    "ac_variable_load_w": 75.0,
                    "ac_variable_start_hour": 6,
                    "ac_variable_end_hour": 20,
                    "dc_base_load_w": 50.0,
                    "dc_variable_load_w": 25.0,
                    "dc_variable_start_hour": 6,
                    "dc_variable_end_hour": 22,
                },
                "consumption_learning": {},
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


async def test_setup_wizard_completes_with_sectioned_steps(hass):
    """The whole wizard (incl. the grouped consumers + control steps) must
    complete and store a FLAT config, not nested section wrappers."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    payloads = [
        ENTRY_DATA,
        {
            "battery_capacity_wh": 5000.0,
            "battery_min_soc_percent": 5.0,
            "battery_max_soc_percent": 95.0,
            "battery_charge_efficiency": 0.97,
            "battery_discharge_efficiency": 0.97,
        },
        {
            "pv_max_power_w": 3200.0,
            "pv_morning_start_hour": 7,
            "pv_morning_end_hour": 13,
            "pv_afternoon_end_hour": 18,
            "pv_morning_ratio": 0.8,
        },
        {  # consumers (sectioned)
            "consumption_profile": {
                "ac_base_load_w": 50.0,
                "ac_variable_load_w": 75.0,
                "ac_variable_start_hour": 6,
                "ac_variable_end_hour": 20,
                "dc_base_load_w": 50.0,
                "dc_variable_load_w": 25.0,
                "dc_variable_start_hour": 6,
                "dc_variable_end_hour": 22,
            },
            "consumption_learning": {},
        },
        {  # power
            "charger_max_power_w": 2300.0,
            "charger_efficiency": 0.92,
            "charger_standby_power_w": 10.0,
            "inverter_max_power_w": 2300.0,
            "inverter_efficiency": 0.95,
            "inverter_standby_power_w": 15.0,
            "inverter_min_soc_percent": 20.0,
        },
        {  # control (sectioned)
            "planner_tuning": {
                "soc_buffer_percent": 5.0,
                "hysteresis_percent": 1.0,
                "threshold_inertia_percent": 2.0,
                "min_switch_interval_s": 60,
            },
            "support_paths": {
                "support_dc48_power_w": 60.0,
                "support_switch_delay_s": 3,
            },
            "dc_devices": {
                "dc24_share_percent": 100.0,
                "dcdc_output_voltage_v": 24.0,
                "dcdc_efficiency": 1.0,
                "dcdc_max_current_a": 0.0,
                "psu24_output_voltage_v": 24.0,
                "psu24_efficiency": 1.0,
                "psu24_max_current_a": 0.0,
                "psu48_output_voltage_v": 49.56,
                "psu48_efficiency": 1.0,
                "psu48_max_current_a": 0.0,
                "battery_cells_series": 16,
                "gate_soc_percent": 100.0,
            },
        },
    ]
    for payload in payloads:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], payload
        )
    assert result["type"] == "create_entry"
    data = result["data"]
    # Flattened: real values present at the top level, no section wrappers.
    assert data["soc_buffer_percent"] == 5.0
    assert data["ac_base_load_w"] == 50.0
    assert data["dc24_share_percent"] == 100.0
    assert not any(
        k in data for k in ("planner_tuning", "dc_devices", "consumption_profile")
    )


BASIC_CONTINUOUS = {
    "name": "Entfeuchter Test",
    "power_w": 400.0,
    "battery_tolerance_percent": 15.0,
    "min_runtime_min": 30,
    "energy_limited": False,
    "in_house_measurement": False,
    "power_warning_percent": 50.0,
}


async def test_load_subentry_flow_skips_storage_for_continuous_loads(hass):
    """Operator wish (2026-07-05): capacity/target-SOC/SOC-sensor and the
    charging-path fields make no sense for a continuous consumer — the
    storage step must not appear."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    # Storage-only fields stay hidden for a continuous load...
    for storage_only in (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_TARGET_SOC,
        CONF_LOAD_CHARGE_ENABLE,
    ):
        assert storage_only not in schema_keys, f"{storage_only} belongs to storage"
    # ...but the control switch + off policy are on the basic step now, so a
    # continuous load (dehumidifier) can be switched directly by BM (F-SUBHOUR).
    assert CONF_LOAD_CONTROL_SWITCH in schema_keys
    assert CONF_LOAD_INPUT_OFF_POLICY in schema_keys

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(BASIC_CONTINUOUS)
    )
    # No storage step: the subentry is created directly, with preserved
    # defaults for the hidden fields.
    assert result["type"] == "create_entry"
    sub = next(iter(entry.subentries.values()))
    assert sub.title == "Entfeuchter Test"
    assert sub.data[CONF_LOAD_CAPACITY_WH] == 2000.0
    assert sub.data[CONF_LOAD_TARGET_SOC] == 100.0
    assert sub.data[CONF_LOAD_INPUT_OFF_POLICY] == "auto"


async def test_load_subentry_flow_shows_storage_for_energy_limited(hass):
    """Energy-limited loads get the second step with the storage and
    charging-path fields."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        CONF_LOAD_SOC_ENTITY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **BASIC_CONTINUOUS,
            "name": "Fossibot Test",
            "energy_limited": True,
            CONF_LOAD_CONTROL_SWITCH: "switch.fossibot_plug",
            CONF_LOAD_INPUT_OFF_POLICY: "auto",
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "storage"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_LOAD_CAPACITY_WH: 2000.0,
            CONF_LOAD_TARGET_SOC: 90.0,
            CONF_LOAD_SOC_ENTITY: "sensor.fossibot_soc",
        },
    )
    assert result["type"] == "create_entry"
    sub = next(iter(entry.subentries.values()))
    assert sub.data[CONF_LOAD_TARGET_SOC] == 90.0
    assert sub.data[CONF_LOAD_SOC_ENTITY] == "sensor.fossibot_soc"
    assert sub.data[CONF_LOAD_CONTROL_SWITCH] == "switch.fossibot_plug"


async def test_load_subentry_storage_step_validates_charging_path(hass):
    """The keep_on-requires-enable rule now lives in the storage step."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **BASIC_CONTINUOUS,
            "name": "Fossibot Test",
            "energy_limited": True,
            CONF_LOAD_CONTROL_SWITCH: "switch.fossibot_plug",
            CONF_LOAD_INPUT_OFF_POLICY: "keep_on",  # no enable entity!
        },
    )
    assert result["step_id"] == "storage"
    # keep_on needs a charge-enable; the storage step submitted without one is
    # rejected (the rule is validated across both steps' combined data).
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_LOAD_CAPACITY_WH: 2000.0, CONF_LOAD_TARGET_SOC: 90.0},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "storage"
    assert result["errors"] == {"base": "keep_on_requires_enable"}


async def test_continuous_load_can_have_control_switch(hass):
    """F-SUBHOUR: a continuous consumer (dehumidifier) can now be assigned a
    control switch on the basic step, so BM switches it directly (sub-hour)."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_ENERGY_LIMITED,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**BASIC_CONTINUOUS, CONF_LOAD_CONTROL_SWITCH: "switch.dehumidifier_plug"},
    )
    assert result["type"] == "create_entry"  # continuous load: no storage step
    sub = next(iter(entry.subentries.values()))
    assert sub.data[CONF_LOAD_ENERGY_LIMITED] is False
    assert sub.data[CONF_LOAD_CONTROL_SWITCH] == "switch.dehumidifier_plug"


async def test_continuous_load_keep_on_without_enable_rejected_on_basic(hass):
    """A continuous load with keep_on but no charge-enable is rejected on the
    basic step (the charging-path rule is validated there for continuous loads)."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_INPUT_OFF_POLICY,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **BASIC_CONTINUOUS,
            CONF_LOAD_CONTROL_SWITCH: "switch.dehumidifier_plug",
            CONF_LOAD_INPUT_OFF_POLICY: "keep_on",
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "keep_on_requires_enable"}


async def test_load_subentry_flow_offers_energy_entity(hass):
    """F-REALIZED-SURPLUS: the optional per-load kWh energy counter (e.g. a
    Fritz!Powerline energy sensor) feeds the realized surplus accounting —
    the selector is offered on the basic step and stored on the subentry."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_ENERGY_ENTITY,
        SUBENTRY_TYPE_LOAD,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_LOAD_ENERGY_ENTITY in schema_keys

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**BASIC_CONTINUOUS, CONF_LOAD_ENERGY_ENTITY: "sensor.fritz_energy"},
    )
    assert result["type"] == "create_entry"
    sub = next(iter(entry.subentries.values()))
    assert sub.data[CONF_LOAD_ENERGY_ENTITY] == "sensor.fritz_energy"


async def test_options_flow_surplus_accounting_stores_export_meter(hass):
    """F-REALIZED-SURPLUS: the surplus-accounting options section renders the
    export-meter selector; a submitted meter is stored FLAT (the coordinator
    reads a flat config). Empty = feature off — covered by the no-change
    payloads of the options tests above."""
    from custom_components.battery_manager.const import CONF_EXPORT_METER_ENTITY

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    assert "surplus_accounting" in {str(k) for k in schema}
    assert CONF_EXPORT_METER_ENTITY in {
        str(k) for k in _section_fields(schema, "surplus_accounting")
    }

    payload = _no_change_options_payload(schema)
    payload["surplus_accounting"] = {
        CONF_EXPORT_METER_ENTITY: "sensor.grid_export_total"
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_EXPORT_METER_ENTITY] == "sensor.grid_export_total"


# ---------------------------------------------------------------------------
# F-LOAD-PRIORITY: explicit per-load priority with insert-shift renumbering
# (docs/F-LOAD-PRIORITY.md R2/R4-R7). Priority materialises as the order of
# SystemConfig.loads; the flow stores dense 1..N values and only writes the
# siblings whose effective priority actually changes (every write reloads).
# ---------------------------------------------------------------------------


async def _setup_entry_with_loads(hass, titles):
    """An entry with LEGACY load subentries (pre-v0.8.2: no priority key)."""
    from homeassistant.config_entries import ConfigSubentryData

    from custom_components.battery_manager.const import (
        CONF_LOAD_POWER_W,
        SUBENTRY_TYPE_LOAD,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={CONF_LOAD_POWER_W: 300.0},
                subentry_type=SUBENTRY_TYPE_LOAD,
                title=title,
                unique_id=None,
            )
            for title in titles
        ],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    load_ids = [
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_LOAD
    ]
    return entry, load_ids


async def test_load_priority_create_default_leaves_siblings_untouched(hass):
    """R5: adding a load at the default priority (N = append) is exactly the
    old creation semantics — the new load stores priority N, and NO sibling is
    written (no reloads, not even a priority key added to legacy loads)."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_PRIORITY,
        SUBENTRY_TYPE_LOAD,
    )

    entry, (id_a, id_b) = await _setup_entry_with_loads(hass, ["A", "B"])
    data_before = {sid: entry.subentries[sid].data for sid in (id_a, id_b)}

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    # Default priority is N (= 3 incl. the load being created).
    marker = next(
        k for k in result["data_schema"].schema if str(k) == CONF_LOAD_PRIORITY
    )
    assert marker.default() == 3
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**BASIC_CONTINUOUS, "name": "C"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    id_c = next(sid for sid, sub in entry.subentries.items() if sub.title == "C")
    assert entry.subentries[id_c].data[CONF_LOAD_PRIORITY] == 3
    for sid in (id_a, id_b):
        assert entry.subentries[sid].data is data_before[sid]  # unwritten
        assert CONF_LOAD_PRIORITY not in entry.subentries[sid].data


async def test_load_priority_insert_shift_on_reconfigure(hass):
    """R4/R7: legacy loads A,B,C (no stored keys) — setting C to priority 1
    shifts the others and densifies every stored value: C=1, A=2, B=3."""
    from custom_components.battery_manager.const import CONF_LOAD_PRIORITY

    entry, (id_a, id_b, id_c) = await _setup_entry_with_loads(hass, ["A", "B", "C"])

    result = await entry.start_subentry_reconfigure_flow(hass, id_c)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_LOAD_PRIORITY: 1}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.subentries[id_c].data[CONF_LOAD_PRIORITY] == 1
    assert entry.subentries[id_a].data[CONF_LOAD_PRIORITY] == 2
    assert entry.subentries[id_b].data[CONF_LOAD_PRIORITY] == 3


async def test_load_priority_clamps_and_resave_is_write_free(hass, monkeypatch):
    """Clamp (R4): the selector already bounds the input to 1..N at render
    time, so the server-side clamp guards the race where the load set SHRINKS
    between form render and submit — the stale position lands on the last
    valid slot. R6: re-saving the untouched form afterwards performs ZERO
    sibling updates (dense values already match) — only the flow's own no-op
    update runs."""
    from custom_components.battery_manager.const import CONF_LOAD_PRIORITY

    entry, (id_a, id_b, id_c) = await _setup_entry_with_loads(hass, ["A", "B", "C"])

    # Render with N=3, then delete a sibling: the submitted position 3 is
    # valid for the stale form but out of range for the 2 remaining loads.
    result = await entry.start_subentry_reconfigure_flow(hass, id_a)
    hass.config_entries.async_remove_subentry(entry, id_c)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_LOAD_PRIORITY: 3}
    )
    assert result["type"] == "abort"
    await hass.async_block_till_done()
    # Clamped to the last position (2); the remaining sibling densifies to 1.
    assert entry.subentries[id_a].data[CONF_LOAD_PRIORITY] == 2
    assert entry.subentries[id_b].data[CONF_LOAD_PRIORITY] == 1

    # Idempotent re-save (R6): the rendered default is the effective position;
    # submitting it unchanged must not write any sibling.
    calls: list[str] = []
    original = hass.config_entries.async_update_subentry

    def spy(*args, **kwargs):
        subentry = kwargs.get("subentry", args[1] if len(args) > 1 else None)
        calls.append(subentry.subentry_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(hass.config_entries, "async_update_subentry", spy)
    result = await entry.start_subentry_reconfigure_flow(hass, id_a)
    marker = next(
        k for k in result["data_schema"].schema if str(k) == CONF_LOAD_PRIORITY
    )
    assert marker.default() == 2  # current effective position
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["type"] == "abort"
    await hass.async_block_till_done()
    assert [sid for sid in calls if sid != id_a] == []  # zero sibling writes
    assert entry.subentries[id_a].data[CONF_LOAD_PRIORITY] == 2  # unchanged


async def test_load_priority_create_at_position_one_shifts_all(hass):
    """R4 (create path): a new load at priority 1 takes the top spot and every
    legacy sibling shifts down (and densifies)."""
    from custom_components.battery_manager.const import (
        CONF_LOAD_PRIORITY,
        SUBENTRY_TYPE_LOAD,
    )

    entry, (id_a, id_b) = await _setup_entry_with_loads(hass, ["A", "B"])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**BASIC_CONTINUOUS, "name": "C", CONF_LOAD_PRIORITY: 1}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    id_c = next(sid for sid, sub in entry.subentries.items() if sub.title == "C")
    assert entry.subentries[id_c].data[CONF_LOAD_PRIORITY] == 1
    assert entry.subentries[id_a].data[CONF_LOAD_PRIORITY] == 2
    assert entry.subentries[id_b].data[CONF_LOAD_PRIORITY] == 3


# ---------------------------------------------------------------------------
# F-RECONFIGURE-PV: base-entry reconfigure repoints SOC + the three PV forecast
# sources without re-adding the entry (docs/F-RECONFIGURE-PV.md R1/R2/R5).
# ---------------------------------------------------------------------------


async def test_reconfigure_repoints_pv_and_preserves_everything_else(hass):
    """R5: the reconfigure flow pre-fills the current SOC/PV entities, and a
    submit with new PV sources rewrites only those keys — every other data key
    and all load subentries survive (the whole point of the cutover path)."""
    from homeassistant.config_entries import ConfigSubentryData

    from custom_components.battery_manager.const import (
        CONF_LOAD_POWER_W,
        SUBENTRY_TYPE_LOAD,
    )

    extra_data = {**ENTRY_DATA, "battery_min_soc_percent": 7.0, "soc_buffer_percent": 5}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=extra_data,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={CONF_LOAD_POWER_W: 300.0},
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="Fossibot",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    load_ids = [
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_LOAD
    ]

    assert entry.supports_reconfigure  # the flow exists -> HA offers Reconfigure
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    # Fields are pre-filled with the CURRENT pick (suggested_value).
    def _suggested(schema, key):
        marker = next(k for k in schema if str(k) == key)
        return (marker.description or {}).get("suggested_value")

    assert (
        _suggested(result["data_schema"].schema, CONF_SOC_ENTITY) == "sensor.test_soc"
    )
    assert (
        _suggested(result["data_schema"].schema, CONF_PV_FORECAST_TODAY)
        == "sensor.pv_today"
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOC_ENTITY: "sensor.test_soc",
            CONF_PV_FORECAST_TODAY: "sensor.balcony_today",
            CONF_PV_FORECAST_TOMORROW: "sensor.balcony_tomorrow",
            CONF_PV_FORECAST_DAY_AFTER: "sensor.balcony_d2",
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    # PV keys repointed; SOC and every non-PV key preserved.
    assert entry.data[CONF_PV_FORECAST_TODAY] == "sensor.balcony_today"
    assert entry.data[CONF_PV_FORECAST_DAY_AFTER] == "sensor.balcony_d2"
    assert entry.data[CONF_SOC_ENTITY] == "sensor.test_soc"
    assert entry.data["battery_min_soc_percent"] == 7.0
    assert entry.data["soc_buffer_percent"] == 5
    # The load subentry is untouched by a base-entry reconfigure.
    assert [
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_LOAD
    ] == load_ids
    assert entry.subentries[load_ids[0]].data[CONF_LOAD_POWER_W] == 300.0


def test_notify_services_filters_non_targets(hass):
    """The notify-target picker offers push services (mobile_app_*) but hides
    the non-target dispatchers (send_message needs an entity_id; the
    persistent_notification service is not a phone)."""
    from custom_components.battery_manager.config_flow import _notify_services

    async def _noop(call):
        return None

    for name in ("mobile_app_pixel", "send_message", "persistent_notification"):
        hass.services.async_register("notify", name, _noop)

    offered = _notify_services(hass)
    assert "mobile_app_pixel" in offered
    assert "send_message" not in offered
    assert "persistent_notification" not in offered


async def test_options_flow_renders_notifications_section(hass):
    """The new notifications section renders (schema construction must not
    raise) and carries the notify-targets + resolve-toggle fields."""
    from custom_components.battery_manager.const import (
        CONF_WARNING_NOTIFY_ON_RESOLVE,
        CONF_WARNING_NOTIFY_TARGETS,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {
        str(k) for k in _section_fields(result["data_schema"].schema, "notifications")
    }
    assert CONF_WARNING_NOTIFY_TARGETS in fields
    assert CONF_WARNING_NOTIFY_ON_RESOLVE in fields


async def test_options_flow_stores_notify_targets(hass):
    """The notify targets + resolve toggle round-trip and are stored flat
    (the coordinator reads them from a flat raw_config)."""
    from custom_components.battery_manager.const import (
        CONF_WARNING_NOTIFY_ON_RESOLVE,
        CONF_WARNING_NOTIFY_TARGETS,
    )

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["notifications"] = {
        CONF_WARNING_NOTIFY_TARGETS: ["mobile_app_alice", "mobile_app_bob"],
        CONF_WARNING_NOTIFY_ON_RESOLVE: False,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    opts = result["data"]
    assert opts[CONF_WARNING_NOTIFY_TARGETS] == ["mobile_app_alice", "mobile_app_bob"]
    assert opts[CONF_WARNING_NOTIFY_ON_RESOLVE] is False
    assert "notifications" not in opts  # section wrapper flattened away


async def test_second_entry_aborts_single_instance(hass):
    """One site = one battery system: with an entry present, a second user
    flow aborts instead of running a second planner against the same
    hardware."""
    await _setup_entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.parametrize("blank_name", ["", "   "])
async def test_load_subentry_rejects_blank_name(hass, blank_name):
    """The name becomes the subentry title; a blank one (even whitespace
    that only LOOKS filled) would create a nameless entry in the UI."""
    from custom_components.battery_manager.const import SUBENTRY_TYPE_LOAD

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_LOAD), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**BASIC_CONTINUOUS, "name": blank_name}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"name": "name_required"}


@pytest.mark.parametrize("blank_name", ["", "   "])
async def test_appliance_subentry_rejects_blank_name(hass, blank_name):
    """Same blank-name guard for appliances (user step; the name is popped
    into the title there)."""
    from custom_components.battery_manager.const import SUBENTRY_TYPE_APPLIANCE

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_APPLIANCE), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": blank_name, "detection_entity": "sensor.washer_power"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"name": "name_required"}


APPLIANCE_PAYLOAD = {
    "name": "Waschmaschine",
    "detection_entity": "sensor.washer_power",
    "power_threshold_w": 10.0,
    "off_threshold_w": 5.0,
    "run_energy_wh": 800.0,
    "run_duration_h": 2.0,
    "opportunistic_start": True,
}


async def test_appliance_subentry_user_step_creates_entry(hass):
    """Happy path of the single-step appliance flow: the form renders all
    fields and a valid submit creates the subentry with the name popped into
    the title (not duplicated into the data)."""
    from custom_components.battery_manager.const import SUBENTRY_TYPE_APPLIANCE

    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_APPLIANCE), context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert {str(k) for k in result["data_schema"].schema} == set(APPLIANCE_PAYLOAD)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(APPLIANCE_PAYLOAD)
    )
    assert result["type"] == "create_entry"
    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == SUBENTRY_TYPE_APPLIANCE
    assert sub.title == "Waschmaschine"
    assert "name" not in sub.data
    assert sub.data == {k: v for k, v in APPLIANCE_PAYLOAD.items() if k != "name"}


async def _setup_entry_with_appliance(hass):
    """An entry carrying one appliance subentry (title DW)."""
    from homeassistant.config_entries import ConfigSubentryData

    from custom_components.battery_manager.const import (
        CONF_APPLIANCE_DETECTION_ENTITY,
        CONF_APPLIANCE_RUN_DURATION_H,
        CONF_APPLIANCE_RUN_ENERGY_WH,
        SUBENTRY_TYPE_APPLIANCE,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={
                    CONF_APPLIANCE_DETECTION_ENTITY: "sensor.dishwasher_power",
                    CONF_APPLIANCE_RUN_ENERGY_WH: 500.0,
                    CONF_APPLIANCE_RUN_DURATION_H: 1.0,
                },
                subentry_type=SUBENTRY_TYPE_APPLIANCE,
                title="DW",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry_id = next(iter(entry.subentries))
    return entry, subentry_id


async def test_appliance_subentry_reconfigure_updates_title_and_data(hass):
    """The reconfigure step renders prefilled from the stored subentry (the
    name default is the TITLE) and a submit updates data + title in place."""
    from custom_components.battery_manager.const import (
        CONF_APPLIANCE_NAME,
        CONF_APPLIANCE_RUN_ENERGY_WH,
    )

    entry, subentry_id = await _setup_entry_with_appliance(hass)

    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
    name_marker = next(
        k for k in result["data_schema"].schema if str(k) == CONF_APPLIANCE_NAME
    )
    assert name_marker.default() == "DW"
    energy_marker = next(
        k
        for k in result["data_schema"].schema
        if str(k) == CONF_APPLIANCE_RUN_ENERGY_WH
    )
    assert energy_marker.default() == 500.0

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**APPLIANCE_PAYLOAD, "name": "DW 2026"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    sub = entry.subentries[subentry_id]
    assert sub.title == "DW 2026"
    assert "name" not in sub.data
    assert sub.data == {k: v for k, v in APPLIANCE_PAYLOAD.items() if k != "name"}


async def test_appliance_subentry_reconfigure_rejects_blank_name(hass):
    """The blank-name guard applies to the reconfigure step too — a nameless
    rename must not silently empty the title."""
    entry, subentry_id = await _setup_entry_with_appliance(hass)

    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**APPLIANCE_PAYLOAD, "name": "   "}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"name": "name_required"}
    assert entry.subentries[subentry_id].title == "DW"  # untouched


# ---------------------------------------------------------------------------
# Base plant dimensions in the options flow (battery / PV / power): previously
# changeable only by deleting and re-adding the entry — losing subentries and
# learned state. Stored in entry.options they override entry.data via the
# coordinator's raw_config merge ({**DEFAULT_CONFIG, **data, **options}).
# ---------------------------------------------------------------------------


async def test_options_flow_renders_base_dimension_sections(hass):
    """The three base-dimension sections render (schema construction must not
    raise) and their defaults come from the effective config — here the
    DEFAULT_CONFIG fallbacks, since ENTRY_DATA carries no dimension keys."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema

    battery = {str(m): _marker_default(m) for m in _section_fields(schema, "battery")}
    assert battery == {
        "battery_capacity_wh": 5000.0,
        "battery_min_soc_percent": 5.0,
        "battery_max_soc_percent": 95.0,
        "house_soc_stale_mid_percent": 3.0,
        "house_soc_stale_edge_percent": 7.0,
        "house_soc_stale_edge_low_soc": 13.0,
        "house_soc_stale_edge_high_soc": 88.0,
    }
    pv = {str(m): _marker_default(m) for m in _section_fields(schema, "pv")}
    assert pv == {
        "pv_max_power_w": 3200.0,
        "pv_morning_start_hour": 7,
        "pv_morning_end_hour": 13,
        "pv_afternoon_end_hour": 18,
        "pv_morning_ratio": 0.8,
    }
    power = {str(m): _marker_default(m) for m in _section_fields(schema, "power")}
    assert power == {
        "charger_max_power_w": 2300.0,
        "charger_efficiency": 0.92,
        "charger_standby_power_w": 10.0,
        "inverter_max_power_w": 2300.0,
        "inverter_efficiency": 0.95,
        "inverter_standby_power_w": 15.0,
        "inverter_min_soc_percent": 20.0,
    }


async def test_options_flow_updates_battery_dimensions(hass):
    """Changed battery dimensions persist FLAT to entry.options and reach the
    planner: the options save fires the update listener, and the reloaded
    coordinator's SystemConfig carries the new values."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["battery"] = {
        "battery_capacity_wh": 10000.0,
        "battery_min_soc_percent": 10.0,
        "battery_max_soc_percent": 90.0,
        # 4.0 (not the 3.0 default): the assertion below must be able to tell
        # a persisted change apart from the DEFAULT_CONFIG fallback.
        "house_soc_stale_mid_percent": 4.0,
        "house_soc_stale_edge_percent": 8.0,
        "house_soc_stale_edge_low_soc": 11.0,
        "house_soc_stale_edge_high_soc": 86.0,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    assert "battery" not in result["data"]  # section wrapper flattened away
    assert result["data"]["battery_capacity_wh"] == 10000.0
    await hass.async_block_till_done()  # update listener reloads the entry

    assert entry.options["battery_capacity_wh"] == 10000.0
    battery = hass.data[DOMAIN][entry.entry_id].build_system_config().battery
    assert battery.capacity_wh == 10000.0
    assert battery.soc_min_percent == 10.0
    assert battery.soc_max_percent == 90.0
    # The stale-watchdog tuning lives in the same section and reaches the
    # coordinator through the same flat options merge.
    raw_config = hass.data[DOMAIN][entry.entry_id].raw_config
    assert raw_config["house_soc_stale_mid_percent"] == 4.0
    assert raw_config["house_soc_stale_edge_percent"] == 8.0
    assert raw_config["house_soc_stale_edge_low_soc"] == 11.0
    assert raw_config["house_soc_stale_edge_high_soc"] == 86.0


async def test_options_flow_rejects_inverted_stale_edge_bounds(hass):
    """The stale-SOC edge bounds must stay ordered — an inversion would
    silently turn the whole SOC range into the tight mid band."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["battery"]["house_soc_stale_edge_low_soc"] = 90.0
    payload["battery"]["house_soc_stale_edge_high_soc"] = 80.0
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "stale_edge_low_not_below_high"}


async def test_options_flow_updates_pv_dimensions(hass):
    """PV peak power + yield windows persist to entry.options and reach the
    planner's PVParams after the options-triggered reload."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["pv"] = {
        "pv_max_power_w": 5600.0,
        "pv_morning_start_hour": 6,
        "pv_morning_end_hour": 12,
        "pv_afternoon_end_hour": 19,
        "pv_morning_ratio": 0.6,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    assert "pv" not in result["data"]
    assert result["data"]["pv_max_power_w"] == 5600.0
    await hass.async_block_till_done()

    assert entry.options["pv_max_power_w"] == 5600.0
    pv = hass.data[DOMAIN][entry.entry_id].build_system_config().pv
    assert pv.peak_power_w == 5600.0
    assert pv.morning_start_hour == 6
    assert pv.morning_end_hour == 12
    assert pv.afternoon_end_hour == 19
    assert pv.morning_ratio == 0.6


async def test_options_flow_updates_power_dimensions(hass):
    """Charger/inverter dimensions persist to entry.options and reach the
    planner's ConverterParams / control after the options-triggered reload."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["power"] = {
        "charger_max_power_w": 3000.0,
        "charger_efficiency": 0.9,
        "charger_standby_power_w": 12.0,
        "inverter_max_power_w": 3600.0,
        "inverter_efficiency": 0.94,
        "inverter_standby_power_w": 18.0,
        "inverter_min_soc_percent": 25.0,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    assert "power" not in result["data"]
    assert result["data"]["inverter_max_power_w"] == 3600.0
    await hass.async_block_till_done()

    assert entry.options["charger_max_power_w"] == 3000.0
    config = hass.data[DOMAIN][entry.entry_id].build_system_config()
    assert config.charger.max_power_w == 3000.0
    assert config.charger.eta == 0.9
    assert config.charger.standby_power_w == 12.0
    assert config.inverter.max_power_w == 3600.0
    assert config.inverter.eta == 0.94
    assert config.inverter.standby_power_w == 18.0
    assert config.control.inverter_min_soc_percent == 25.0


async def test_options_flow_rejects_inverted_soc_bounds(hass):
    """The wizard's min<max SOC rule applies in the options flow too (same
    error key) — an inverted window would leave the planner zero usable SOC."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["battery"]["battery_min_soc_percent"] = 95.0
    payload["battery"]["battery_max_soc_percent"] = 90.0
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "min_soc_above_max"}


async def test_options_flow_rejects_misordered_pv_windows(hass):
    """The wizard's PV-window ordering rule applies in the options flow too —
    a degenerate window would silently discard forecast energy."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["pv"]["pv_morning_start_hour"] = 13
    payload["pv"]["pv_morning_end_hour"] = 7
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "pv_windows_out_of_order"}


async def test_options_base_update_preserves_subentries_and_learned_state(hass):
    """The point of the feature: re-dimensioning via options must NOT lose
    subentries or learned state (the old delete + re-add path did). The
    options save fires the entry's update listener → a reload; the unload
    flushes the learned planning power to the store and the reloaded
    coordinator restores it (keyed by the surviving subentry id)."""
    entry, (sub_id,) = await _setup_entry_with_loads(hass, ["Fossibot"])
    coord_before = hass.data[DOMAIN][entry.entry_id]
    coord_before._load_learned_power_w[sub_id] = 409.0
    sub_data_before = entry.subentries[sub_id].data

    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["battery"]["battery_capacity_wh"] = 10000.0
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    # The update listener fired: the entry was reloaded (new coordinator).
    coord_after = hass.data[DOMAIN][entry.entry_id]
    assert coord_after is not coord_before
    # Subentry untouched (same id, same data object), learned power restored.
    assert entry.subentries[sub_id].data is sub_data_before
    assert coord_after._load_learned_power_w[sub_id] == 409.0
    # And the new base dimension reached the planner.
    assert coord_after.build_system_config().battery.capacity_wh == 10000.0


# ---------------------------------------------------------------------------
# F-FEEDIN early feed-in section (config_flow._validate_feedin): enabled
# requires both wiring entities, the power cap must sit in (0, 2000] W and
# the SOC floor must sit ABOVE the battery minimum — else the planner would
# book feed-in the executor can never deliver (or fight the deep-discharge
# floor). Valid sections save flat and wire the executor on the reload.
# ---------------------------------------------------------------------------


def _feedin_section(**overrides):
    """A valid early_feed_in options section; override keys per test case."""
    section = {
        "feedin_enabled": True,
        "feedin_setpoint_entity": "input_number.acpowersetpoint",
        "feedin_battery_power_entity": "sensor.victron_system_battery_power",
        "feedin_max_w": 1500.0,
        "feedin_min_soc_percent": 40.0,
        "feedin_deadline_hour": 10,
    }
    section.update(overrides)
    return section


async def _submit_feedin_options(hass, section):
    """Options-flow submit with the given early_feed_in section (all other
    sections at their rendered defaults)."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    payload["early_feed_in"] = section
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    return entry, result


async def test_options_flow_rejects_feedin_enabled_without_entities(hass):
    """Enabled without the setpoint/battery-power wiring can never drive
    anything — rejected instead of saved dead."""
    _, result = await _submit_feedin_options(
        hass,
        {
            "feedin_enabled": True,
            "feedin_max_w": 1000.0,
            "feedin_min_soc_percent": 30.0,
            "feedin_deadline_hour": 9,
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "feedin_entities_required"}


async def test_options_flow_rejects_feedin_max_w_out_of_range(hass):
    """The cap must be a usable POSITIVE power — a 0 would book feed-in the
    executor can never deliver. (The 2000 W ceiling is already enforced by
    the number selector's schema; the validator guards the lower bound.)"""
    _, result = await _submit_feedin_options(hass, _feedin_section(feedin_max_w=0.0))
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "feedin_max_w_out_of_range"}


async def test_options_flow_rejects_feedin_min_soc_not_above_battery_min(hass):
    """The no-change payload carries battery_min_soc_percent 5.0 — a feed-in
    floor at/below it would fight the battery's deep-discharge floor."""
    _, result = await _submit_feedin_options(
        hass, _feedin_section(feedin_min_soc_percent=5.0)
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "feedin_min_soc_not_above_battery_min"}


async def test_options_flow_saves_feedin_section(hass):
    """A valid section saves FLAT (options win over entry.data), the reloaded
    coordinator maps it into FeedInParams and the runtime switch + mode
    sensor appear with the enabled feature."""
    from homeassistant.helpers import entity_registry as er

    entry, result = await _submit_feedin_options(hass, _feedin_section())
    assert result["type"] == "create_entry"
    assert "early_feed_in" not in result["data"]  # section wrapper flattened
    assert result["data"]["feedin_enabled"] is True
    assert result["data"]["feedin_setpoint_entity"] == "input_number.acpowersetpoint"
    assert (
        result["data"]["feedin_battery_power_entity"]
        == "sensor.victron_system_battery_power"
    )
    await hass.async_block_till_done()  # update listener reloads the entry

    feedin = hass.data[DOMAIN][entry.entry_id].build_system_config().feedin
    assert feedin.enabled is True
    assert feedin.max_w == 1500.0
    assert feedin.min_soc_percent == 40.0
    assert feedin.deadline_hour == 10

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "switch", DOMAIN, f"{entry.entry_id}_early_feed_in"
        )
        is not None
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_feedin_mode")
        is not None
    )


async def test_cascade_subentry_creates_ordered_topology(hass):
    """A complete storage plus terminal can be bound into one safe chain."""
    from homeassistant.config_entries import ConfigSubentryData

    from custom_components.battery_manager.const import (
        CONF_CASCADE_ACTOR_TIMEOUT_S,
        CONF_CASCADE_MEMBER_IDS,
        CONF_CASCADE_TERMINAL_LOAD_ID,
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_ENERGY_LIMITED,
        CONF_LOAD_OUTPUT_POWER_ENTITY,
        CONF_LOAD_OUTPUT_SWITCH,
        CONF_LOAD_POWER_W,
        CONF_LOAD_SOC_ENTITY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_CASCADE,
        SUBENTRY_TYPE_LOAD,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={
                    CONF_LOAD_POWER_W: 300.0,
                    CONF_LOAD_ENERGY_LIMITED: True,
                    CONF_LOAD_CAPACITY_WH: 2000.0,
                    CONF_LOAD_TARGET_SOC: 90.0,
                    CONF_LOAD_SOC_ENTITY: "sensor.b1_soc",
                    CONF_LOAD_CONTROL_SWITCH: "switch.b1_input",
                    CONF_LOAD_CHARGE_ENABLE: "switch.b1_charge",
                    CONF_LOAD_OUTPUT_SWITCH: "switch.b1_output",
                    CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.b1_output_power",
                },
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="B1",
                unique_id=None,
            ),
            ConfigSubentryData(
                data={
                    CONF_LOAD_POWER_W: 300.0,
                    CONF_LOAD_ENERGY_LIMITED: False,
                    CONF_LOAD_CONTROL_SWITCH: "switch.dehumidifier",
                },
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="Dehumidifier",
                unique_id=None,
            ),
        ],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55")
    hass.states.async_set("sensor.b1_soc", "90")
    hass.states.async_set("sensor.b1_output_power", "0")
    for entity_id in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(entity_id, "10")
    for entity_id in (
        "switch.b1_input",
        "switch.b1_charge",
        "switch.b1_output",
        "switch.dehumidifier",
    ):
        hass.states.async_set(entity_id, "off")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    storage_id = next(sid for sid, sub in entry.subentries.items() if sub.title == "B1")
    terminal_id = next(
        sid for sid, sub in entry.subentries.items() if sub.title == "Dehumidifier"
    )

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CASCADE), context={"source": "user"}
    )
    assert result["type"] == "form"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "B1 → Dehumidifier",
            CONF_CASCADE_MEMBER_IDS: [storage_id],
            CONF_CASCADE_TERMINAL_LOAD_ID: terminal_id,
            CONF_CASCADE_ACTOR_TIMEOUT_S: 30,
        },
    )
    assert result["type"] == "create_entry"
    cascade = next(
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CASCADE
    )
    assert cascade.data[CONF_CASCADE_MEMBER_IDS] == [storage_id]
    assert cascade.data[CONF_CASCADE_TERMINAL_LOAD_ID] == terminal_id


def test_standalone_config_validators_pin_cross_field_requirements():
    """D-C1/D-C8/D-A9 and LOAD_CONTROL §7 are cross-field contracts; exercise
    their failing combinations directly so form plumbing cannot mask them."""
    from custom_components.battery_manager import config_flow
    from custom_components.battery_manager.const import (
        CONF_AC_BALANCE_OUT,
        CONF_BUFFER_MAX_PERCENT,
        CONF_BUFFER_MIN_PERCENT,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_SUPPORT_DC24_ACTIVATE_SOC,
        CONF_SUPPORT_DC24_SWITCH,
        CONF_SUPPORT_DC48_ACTIVATE_SOC,
        CONF_SUPPORT_DC48_SWITCH,
    )

    assert config_flow._flatten_sections({"future_key": 7}) == {"future_key": 7}
    assert (
        config_flow._validate_learning_sources({CONF_AC_BALANCE_OUT: ["sensor.out"]})
        == "balance_out_without_in"
    )
    assert (
        config_flow._validate_buffer_clamps(
            {CONF_BUFFER_MIN_PERCENT: 20, CONF_BUFFER_MAX_PERCENT: 20}
        )
        == "buffer_min_above_max"
    )
    assert (
        config_flow._validate_support_hysteresis({CONF_SUPPORT_DC24_ACTIVATE_SOC: 10})
        is None
    )
    assert (
        config_flow._validate_load_control(
            {
                CONF_LOAD_CONTROL_SWITCH: "switch.same",
                CONF_LOAD_CHARGE_ENABLE: "switch.same",
            }
        )
        == "control_entities_not_distinct"
    )
    assert (
        config_flow._validate_support_entities(
            {
                CONF_SUPPORT_DC24_SWITCH: "switch.same",
                CONF_SUPPORT_DC48_SWITCH: "switch.same",
            }
        )
        == "support_entities_not_distinct"
    )
    # A partial support ladder is intentionally deferred until all four
    # values exist; this guards upgrades carrying only one new option.
    assert (
        config_flow._validate_support_hysteresis({CONF_SUPPORT_DC48_ACTIVATE_SOC: 5})
        is None
    )


def test_cascade_validator_rejects_each_unsafe_topology_contract():
    """F-CASCADE-STORAGE configuration is fail-closed: every topology rule
    reports a specific operator-facing error before any actor can be owned."""
    from custom_components.battery_manager.config_flow import CascadeSubentryFlow
    from custom_components.battery_manager.const import (
        CONF_CASCADE_MEMBER_IDS,
        CONF_CASCADE_TERMINAL_LOAD_ID,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_DISCHARGE_FLOOR_SOC,
        CONF_LOAD_ENERGY_LIMITED,
        CONF_LOAD_HANDOVER_TIMEOUT_S,
        CONF_LOAD_NAME,
        CONF_LOAD_OUTPUT_POWER_ENTITY,
        CONF_LOAD_OUTPUT_SWITCH,
        CONF_LOAD_RECOVERY_SOC,
        CONF_LOAD_SOC_ENTITY,
        CONF_LOAD_TARGET_SOC,
        SUBENTRY_TYPE_LOAD,
    )

    root_data = {
        CONF_LOAD_ENERGY_LIMITED: True,
        CONF_LOAD_SOC_ENTITY: "sensor.root_soc",
        CONF_LOAD_CONTROL_SWITCH: "switch.root",
        CONF_LOAD_CHARGE_ENABLE: "switch.root_gate",
        CONF_LOAD_OUTPUT_SWITCH: "switch.root_output",
        CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.root_output_power",
        CONF_LOAD_DISCHARGE_FLOOR_SOC: 20,
        CONF_LOAD_RECOVERY_SOC: 50,
        CONF_LOAD_TARGET_SOC: 90,
        CONF_LOAD_HANDOVER_TIMEOUT_S: 180,
    }
    terminal_data = {CONF_LOAD_ENERGY_LIMITED: False}
    entry = SimpleNamespace(
        subentries={
            "root": SimpleNamespace(subentry_type=SUBENTRY_TYPE_LOAD, data=root_data),
            "leaf": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_LOAD, data=terminal_data
            ),
        }
    )
    flow = SimpleNamespace(_get_entry=lambda: entry)

    def validate(member_ids, terminal="leaf", **overrides):
        payload = {
            CONF_LOAD_NAME: "Safe chain",
            CONF_CASCADE_MEMBER_IDS: member_ids,
            CONF_CASCADE_TERMINAL_LOAD_ID: terminal,
            **overrides,
        }
        return CascadeSubentryFlow._validate(flow, payload, None)

    assert validate([], **{CONF_LOAD_NAME: " "}) == "name_required"
    assert validate([]) == "cascade_members_required"
    assert validate(["root", "root"]) == "cascade_duplicate_member"
    assert validate(["root"], terminal="root") == "cascade_duplicate_member"
    assert validate(["root"], terminal="missing") == "cascade_terminal_invalid"

    entry.subentries["bad"] = SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_LOAD,
        data={CONF_LOAD_ENERGY_LIMITED: False},
    )
    assert validate(["bad"]) == "cascade_member_invalid"

    original = dict(root_data)
    root_data.pop(CONF_LOAD_SOC_ENTITY)
    assert validate(["root"]) == "cascade_member_incomplete"
    root_data.clear()
    root_data.update(original)
    root_data.pop(CONF_LOAD_CONTROL_SWITCH)
    assert validate(["root"]) == "cascade_root_input_required"
    root_data.clear()
    root_data.update(original)

    entry.subentries["second"] = SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_LOAD,
        data={
            **root_data,
            CONF_LOAD_SOC_ENTITY: "sensor.second_soc",
            CONF_LOAD_CONTROL_SWITCH: "switch.second_input",
            CONF_LOAD_CHARGE_ENABLE: "switch.second_gate",
            CONF_LOAD_OUTPUT_SWITCH: "switch.second_output",
            CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.second_output_power",
        },
    )
    assert validate(["root", "second"]) == "cascade_nonroot_input_forbidden"
    entry.subentries["second"].data.pop(CONF_LOAD_CONTROL_SWITCH)

    root_data[CONF_LOAD_RECOVERY_SOC] = 10
    assert validate(["root"]) == "cascade_soc_order"
    root_data[CONF_LOAD_RECOVERY_SOC] = 50
    root_data[CONF_LOAD_HANDOVER_TIMEOUT_S] = 59
    assert validate(["root"]) == "cascade_handover_timeout"
    root_data[CONF_LOAD_HANDOVER_TIMEOUT_S] = 180

    terminal_data[CONF_LOAD_CONTROL_SWITCH] = root_data[CONF_LOAD_CONTROL_SWITCH]
    assert validate(["root"]) == "cascade_actor_in_use"
    terminal_data.clear()
    terminal_data.update({CONF_LOAD_ENERGY_LIMITED: False})
    assert validate(["root"]) is None


def test_cascade_validator_rejects_load_or_actor_owned_by_another_chain():
    """Two cascades may neither share logical loads nor aliases of the same
    physical actor, including stale references to a removed load."""
    from custom_components.battery_manager.config_flow import CascadeSubentryFlow
    from custom_components.battery_manager.const import (
        CONF_CASCADE_MEMBER_IDS,
        CONF_CASCADE_TERMINAL_LOAD_ID,
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_ENERGY_LIMITED,
        CONF_LOAD_HANDOVER_TIMEOUT_S,
        CONF_LOAD_NAME,
        CONF_LOAD_OUTPUT_POWER_ENTITY,
        CONF_LOAD_OUTPUT_SWITCH,
        CONF_LOAD_SOC_ENTITY,
        SUBENTRY_TYPE_CASCADE,
        SUBENTRY_TYPE_LOAD,
    )

    def storage(prefix, control):
        return SimpleNamespace(
            subentry_type=SUBENTRY_TYPE_LOAD,
            data={
                CONF_LOAD_ENERGY_LIMITED: True,
                CONF_LOAD_SOC_ENTITY: f"sensor.{prefix}_soc",
                CONF_LOAD_CONTROL_SWITCH: control,
                CONF_LOAD_CHARGE_ENABLE: f"switch.{prefix}_gate",
                CONF_LOAD_OUTPUT_SWITCH: f"switch.{prefix}_output",
                CONF_LOAD_OUTPUT_POWER_ENTITY: f"sensor.{prefix}_output_power",
                CONF_LOAD_HANDOVER_TIMEOUT_S: 180,
            },
        )

    leaf = SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_LOAD, data={CONF_LOAD_ENERGY_LIMITED: False}
    )
    entry = SimpleNamespace(
        subentries={
            "candidate": storage("candidate", "switch.shared_root"),
            "candidate_leaf": leaf,
            "owned": storage("owned", "switch.shared_root"),
            "owned_leaf": leaf,
            "other": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_CASCADE,
                data={
                    CONF_CASCADE_MEMBER_IDS: ["owned", "removed"],
                    CONF_CASCADE_TERMINAL_LOAD_ID: "owned_leaf",
                },
            ),
        }
    )
    flow = SimpleNamespace(_get_entry=lambda: entry)
    payload = {
        CONF_LOAD_NAME: "Candidate",
        CONF_CASCADE_MEMBER_IDS: ["candidate"],
        CONF_CASCADE_TERMINAL_LOAD_ID: "candidate_leaf",
    }

    assert CascadeSubentryFlow._validate(flow, payload, None) == "cascade_actor_in_use"
    payload[CONF_CASCADE_MEMBER_IDS] = ["owned"]
    assert CascadeSubentryFlow._validate(flow, payload, None) == "cascade_member_in_use"


def test_load_reconfigure_checks_cascade_before_writing_and_clears_optional_fields():
    from unittest.mock import Mock

    from custom_components.battery_manager import const as c
    from custom_components.battery_manager.config_flow import (
        SurplusLoadSubentryFlow as Flow,
    )

    member = SimpleNamespace(
        subentry_id="member", subentry_type=c.SUBENTRY_TYPE_LOAD, data={}
    )
    chain = SimpleNamespace(
        subentry_type=c.SUBENTRY_TYPE_CASCADE,
        title="Chain",
        data={
            c.CONF_CASCADE_MEMBER_IDS: ["member"],
            c.CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
        },
    )
    leaf = SimpleNamespace(
        subentry_type=c.SUBENTRY_TYPE_LOAD, data={c.CONF_LOAD_ENERGY_LIMITED: False}
    )
    entry = SimpleNamespace(subentries={"member": member, "leaf": leaf, "chain": chain})
    flow = SimpleNamespace(
        _STORAGE_KEYS=Flow._STORAGE_KEYS,
        _STORAGE_ENTITY_KEYS=Flow._STORAGE_ENTITY_KEYS,
        _existing={
            c.CONF_LOAD_CHARGE_ENABLE: "switch.old",
            c.CONF_LOAD_MAX_CHARGE_POWER_W: 500,
        },
        _basic={
            c.CONF_LOAD_NAME: "Member",
            c.CONF_LOAD_PRIORITY: 1,
            c.CONF_LOAD_ENERGY_LIMITED: False,
        },
        _is_reconfigure=True,
        _get_entry=lambda: entry,
        _get_reconfigure_subentry=lambda: member,
        _renumber_siblings=Mock(return_value=1),
        async_update_and_abort=Mock(),
        _basic_schema=lambda _: None,
        _storage_schema=lambda _: None,
        async_show_form=lambda **kw: kw,
    )
    result = Flow._finish(flow, {})
    assert result["errors"]["base"] == "cascade_member_invalid"
    flow._renumber_siblings.assert_not_called()
    flow.async_update_and_abort.assert_not_called()
    # With the chain removed, clearing selectors really removes them.
    del entry.subentries["chain"]
    flow._basic[c.CONF_LOAD_ENERGY_LIMITED] = True
    Flow._finish(flow, {})
    saved = flow.async_update_and_abort.call_args.kwargs["data"]
    assert c.CONF_LOAD_CHARGE_ENABLE not in saved
    assert c.CONF_LOAD_MAX_CHARGE_POWER_W not in saved


def test_separate_cascades_cannot_alias_one_terminal_switch():
    from custom_components.battery_manager import const as c
    from custom_components.battery_manager.config_flow import CascadeSubentryFlow

    def storage(prefix):
        return SimpleNamespace(
            subentry_type=c.SUBENTRY_TYPE_LOAD,
            data={
                c.CONF_LOAD_ENERGY_LIMITED: True,
                c.CONF_LOAD_SOC_ENTITY: f"sensor.{prefix}",
                c.CONF_LOAD_CONTROL_SWITCH: f"switch.{prefix}_input",
                c.CONF_LOAD_CHARGE_ENABLE: f"switch.{prefix}_gate",
                c.CONF_LOAD_OUTPUT_SWITCH: f"switch.{prefix}_output",
                c.CONF_LOAD_OUTPUT_POWER_ENTITY: f"sensor.{prefix}_power",
            },
        )

    def leaf():
        return SimpleNamespace(
            subentry_type=c.SUBENTRY_TYPE_LOAD,
            data={c.CONF_LOAD_CONTROL_SWITCH: "switch.shared"},
        )

    entry = SimpleNamespace(
        subentries={
            "a": storage("a"),
            "b": storage("b"),
            "la": leaf(),
            "lb": leaf(),
            "ca": SimpleNamespace(
                subentry_type=c.SUBENTRY_TYPE_CASCADE,
                data={
                    c.CONF_CASCADE_MEMBER_IDS: ["a"],
                    c.CONF_CASCADE_TERMINAL_LOAD_ID: "la",
                },
            ),
        }
    )
    result = CascadeSubentryFlow._validate(
        SimpleNamespace(_get_entry=lambda: entry),
        {
            c.CONF_LOAD_NAME: "B",
            c.CONF_CASCADE_MEMBER_IDS: ["b"],
            c.CONF_CASCADE_TERMINAL_LOAD_ID: "lb",
        },
        None,
    )
    assert result == "cascade_actor_in_use"
