"""Config/options flow smoke tests (schema construction must never raise)."""

import voluptuous as vol
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
        },
    )
    assert result["type"] == "create_entry"
    opts = result["data"]
    # Stored flat, not nested — a real value from each section.
    assert opts["soc_buffer_percent"] == 6.0
    assert opts[CONF_DC24_SHARE_PERCENT] == 80.0
    assert opts[CONF_DCDC_EFFICIENCY] == 0.93
    assert opts["battery_cells_series"] == 15
    assert "planner_tuning" not in opts  # section wrappers removed


async def test_options_flow_rejects_inverted_controller_band(hass):
    """Review finding: the R2 controller off-voltage must be validated ABOVE
    the on-voltage in the OPTIONS flow too (not only the setup wizard), else a
    collapsed hysteresis band saves silently and the controller chatters."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
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
        CONF_IMPORT_TRADE_RATIO,
        CONF_PREDRAIN_PV_CONFIDENCE,
        CONF_PV_FORECAST_MODE,
        CONF_PV_WINDOW_END_HOUR,
        CONF_STRONG_PV_CUTOFF_W,
        CONF_UPPER_PV_RESERVE,
        IMPORT_TRADE_RATIO_DEFAULT,
        PREDRAIN_PV_CONFIDENCE_DEFAULT,
        PV_FORECAST_MODE_AUTO,
        STRONG_PV_CUTOFF_W_DEFAULT,
        UPPER_PV_RESERVE_DEFAULT,
    )

    entry = await _setup_entry(hass)  # no pre-drain options stored
    coord = hass.data[DOMAIN][entry.entry_id]

    # (a) The coordinator's absent-key fallback = the recommended live values,
    # so the feature is active right after the update.
    ctrl = coord.build_system_config().control
    assert ctrl.import_trade_ratio == IMPORT_TRADE_RATIO_DEFAULT
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
    assert defaults[CONF_IMPORT_TRADE_RATIO] == IMPORT_TRADE_RATIO_DEFAULT
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
    assert opts[CONF_IMPORT_TRADE_RATIO] == IMPORT_TRADE_RATIO_DEFAULT
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
    assert entry.minor_version == 3
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
    assert entry.minor_version == 3
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
    assert entry.minor_version == 3
    assert entry.options["support_dc24_activate_soc"] == 8.0
    assert "support_dc48_activate_soc" not in entry.options


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
