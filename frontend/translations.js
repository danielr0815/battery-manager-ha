// retain switching edges across the full 96-hour horizon

export const STRINGS = {
  en: {
    card_soc_read_for_plan: "SOC read for this plan",
    card_soc_entity_reported:
      "Entity-reported value (device freshness unconfirmed)",
    card_soc_cached: "Cached value",
    card_soc_source_unknown: "Source unknown",
    card_soc_publication_age: "Age of HA publication at plan capture",
    card_soc_age_unknown: "Unknown",

    report_gate_48: "48 V switching decision",
    report_gate_voltage: "Current battery voltage",
    report_gate_learning: "SOC threshold estimate",
    report_gate_samples: "Accepted observations",
    report_gate_soc: "Learned switch-on SOC during discharge",
    report_gate_spread: "Observed spread",
    report_soc_coordinate: "SOC observation and energy comparison",
    report_soc_coordinate_state: "Observation state",
    report_soc_coordinate_event: "Last observed adjustment",
    report_soc_reported_change: "Change reported by the BMS",
    report_soc_energy_change: "SOC equivalent of measured energy change",
    report_soc_model_residual: "Difference from the linear energy estimate",
    report_soc_covered_time: "Covered comparison interval",
    report_soc_coordinate_explanation:
      "The reported BMS SOC can drift and be recalibrated near the upper range. An adjustment is not itself a device fault. Measured energy change does not independently determine absolute SOC.",
    report_soc_conservative_forecast:
      "Conservative SOC forecast: future 48 V support is credited only where the model has valid evidence. A switched-on PSU does not guarantee future delivered power.",
    soc_phase_tracking: "Observing the current SOC coordinate",
    soc_safety_curve: "Planning and safety curve",
    soc_safety_decisions:
      "The solid curve governs planning and safety decisions.",
    soc_expected_curve: "With continued 48 V delivery",
    soc_expected_basis:
      "The dashed curve assumes continued 48 V delivery under the same switching conditions.",
    soc_expected_unqualified:
      "The expected curve needs at least 30 minutes of continuous 48 V DC output readings in the current supply mode.",
    soc_expected_no_window:
      "The observed 48 V delivery does not support an expected curve for the current schedule.",
    soc_expected_measured: "Minimum measured 48 V DC output",
    soc_expected_span: "Measurement span",
    soc_expected_relay_on:
      "Relay ON is confirmed. The relay state alone cannot establish DC output.",
    soc_expected_reason_missing_dc_meter:
      "No 48 V DC output meter is configured.",
    soc_expected_reason_invalid_dc_topology:
      "The configured measurement cannot isolate the 48 V power supply’s DC output.",
    soc_expected_reason_missing_dc_power_limit:
      "A positive, finite DC power limit is required to validate the readings. Check the configured output voltage and maximum current.",
    soc_expected_reason_episode_unproven:
      "Continuous DC output readings in the current 48 V supply mode are missing.",
    soc_expected_reason_episode_too_short:
      "Fewer than 30 minutes of continuous DC output readings are available in the current 48 V supply mode.",
    soc_expected_reason_no_positive_delivery:
      "The readings do not show sustained positive 48 V DC output.",
    soc_expected_reason_projection_unsupported:
      "The model cannot project this 48 V DC output in the current system configuration.",
    soc_expected_reason_no_matching_plan_window:
      "The measured delivery has no matching period in the current switching schedule.",
    soc_expected_reason_observation_shorter_than_plan_interval:
      "The DC readings do not cover the whole first matching plan interval.",
    soc_expected_outside_display:
      "The displayed period contains too few points to draw the expectation curve.",
    soc_expected_overlap:
      "The curves overlap within the chart’s resolution over the shared displayed period.",
    soc_expected_net_curve: "With continued net battery discharge",
    soc_expected_grid_curve: "Short-term SOC at sustained grid import",
    soc_expected_net_reason_grid_plan_margin_not_met:
      "With the higher PV forecast, the planned household load leaves too little grid import above the setpoint.",
    soc_expected_grid_basis:
      "The dashed curve continues the observed battery power while grid import is expected to stay above the effective setpoint.",
    soc_expected_grid_import: "Measured grid import",
    soc_expected_grid_setpoint: "Effective grid setpoint",
    soc_expected_grid_margin: "Required import margin",
    soc_expected_grid_until: "Short-term expectation ends at",
    soc_expected_net_reason_grid_topology_unavailable:
      "Check that the grid meter and setpoint are assigned to the correct electrical measurements.",
    soc_expected_net_reason_grid_power_unavailable:
      "A fresh signed measurement at the grid connection is required.",
    soc_expected_net_reason_grid_setpoint_unavailable:
      "A fresh readback of the effective grid setpoint is required.",
    soc_expected_net_reason_grid_not_importing:
      "The meter shows zero grid import or export.",
    soc_expected_net_reason_grid_margin_not_met:
      "Grid import is too close to zero or to the effective setpoint.",
    soc_expected_net_reason_battery_not_idle:
      "The battery power is outside the ±1 W idle range.",
    soc_expected_net_basis:
      "The dashed curve uses the measured net discharge rate from the current SOC in comparable plan intervals.",
    soc_expected_net_purpose:
      "This scenario uses the net discharge rate measured at the battery terminals.",
    soc_expected_net_measured: "Net battery discharge from terminal readings",
    soc_expected_net_until: "Comparable night window ends at",
    soc_expected_net_unqualified:
      "The scenario needs at least 30 minutes of stable net discharge readings in the confirmed supply mode.",
    soc_expected_net_no_window:
      "No comparable plan interval is available for the observed net battery discharge.",
    soc_expected_net_reason_no_matching_plan_window:
      "The current plan has no interval with comparable native load, switching and energy flows.",
    soc_expected_net_reason_observation_shorter_than_plan_interval:
      "The measured net discharge span is shorter than the first matching plan interval.",
    soc_expected_net_reason_projection_unsupported:
      "The current system configuration or measurement cannot support this net discharge scenario.",
    soc_expected_net_reason_discharge_exceeds_native_load:
      "The observed net discharge exceeds the planned native 48 V load.",
    soc_expected_net_reason_storage_limit:
      "The first projected interval would exceed the configured battery SOC limits.",
    soc_expected_net_reason_window_ends_before_interval:
      "The comparable night window ends before the first matching plan interval is complete.",
    soc_expected_net_reason_topology_unavailable:
      "This scenario requires a supported 48 V topology and a battery-terminal power meter.",
    soc_expected_net_reason_mode_unconfirmed:
      "Feedback from the relays, inverter or configured grid source has yet to confirm the required supply mode.",
    soc_expected_net_reason_ac_flow_incompatible:
      "The AC readings do not confirm grid pass-through for this period.",
    soc_expected_net_reason_coordinate_unstable:
      "The battery SOC reference is still settling.",
    soc_expected_net_reason_telemetry_unavailable:
      "Current battery, switching or PV readings are missing or invalid.",
    soc_expected_net_reason_publication_unconfirmed:
      "The measurements for the current plan publication are not yet confirmed.",
    soc_expected_net_reason_plan_outside_observation:
      "The plan starts outside the period with a confirmed supply mode.",
    soc_expected_net_reason_voltage_above_threshold:
      "The battery voltage is above the configured threshold for comparable support conditions.",
    soc_expected_net_reason_charging_detected:
      "The battery readings show charging.",
    soc_expected_net_reason_pv_detected: "The PV readings show production.",
    soc_expected_net_reason_daylight:
      "During daylight, assign the signed grid meter and effective grid setpoint to use the short-term SOC expectation.",
    soc_expected_net_reason_episode_too_short:
      "Fewer than 30 minutes of stable net discharge readings are available in the confirmed supply mode.",
    soc_expected_net_reason_direct_meter_configured:
      "The configured 48 V DC output meter is used for the expectation scenario.",
    load_grid_guard_active: "Grid import guard active",
    load_grid_guard_inactive: "Grid import guard inactive",
    load_grid_guard_fresh: "Fresh grid reading",
    load_grid_guard_stale: "Stale grid reading",
    load_grid_guard_unconfigured: "No grid meter configured",
    load_grid_guard_unavailable: "Grid reading unavailable",
    load_grid_guard_invalid: "Invalid grid reading",
    load_grid_guard_unknown: "Reading status unknown",
    load_grid_guard_age: "Age at check",
    load_grid_guard_cutoff: "Cutoff above",
    load_grid_guard_last_stop: "Last stop request by grid import guard",
    load_grid_guard_decided_at: "Decision at",
    load_grid_guard_decision_reading: "Grid reading at decision",
    soc_observation_unknown: "Unknown observation",
    soc_phase_reconciling: "SOC coordinate is being adjusted",
    soc_phase_settling: "Collecting stable observations after adjustment",
    report_gate_query: "Reference current of the native 48 V base load",
    report_gate_query_status: "Qualification for the reference current",
    report_gate_points: "nearby observations",
    gate_role_on: "Switch-on estimate",
    gate_role_delivery: "Delivery estimate",
    gate_role_off: "Switch-off estimate",
    gate_qualification_qualified: "Qualified observations",
    gate_qualification_insufficient_samples: "Too few comparable observations",
    gate_qualification_insufficient_span: "Observation span is too short",
    gate_qualification_conflicting_coordinates:
      "Older and newer SOC observations disagree",
    gate_qualification_out_of_current_range:
      "Reference current is outside the observed range",
    gate_qualification_expired: "Evidence has expired",
    gate_qualification_inconsistent_threshold_order:
      "Threshold observations are inconsistent",
    reserve_decision_soc_coordinate_hold:
      "Optional battery use waits for stable SOC observations",
    report_psu_observations: "Measured PSU observations",
    report_psu_24: "24 V PSU",
    report_psu_48: "48 V PSU",
    report_psu_confirmed_state: "Confirmed switch state",
    report_psu_ac_input: "Observed AC input",
    report_psu_dc_output: "Observed DC output",
    report_psu_present_delivery: "Qualified present DC delivery",
    report_psu_observed_efficiency: "Efficiency from overlapping measurements",
    report_psu_ac_input_status: "AC input measurement status",
    report_psu_dc_output_status: "DC output measurement status",
    psu_quality_measured: "Measurement available",
    psu_quality_unknown: "No qualified measurement",
    psu_quality_stale: "Measurement is stale",
    psu_quality_unconfirmed: "Source state is not confirmed",
    psu_quality_incompatible: "Measurement or source binding is invalid",
    psu_channel_not_configured: "Not configured",
    psu_channel_measured: "Fresh measurement",
    psu_channel_missing: "No measurement available",
    psu_channel_incompatible: "Incompatible measurement",
    psu_channel_hardware_limit: "Measurement exceeds hardware limit",
    psu_channel_unconfirmed: "Source state is unconfirmed",
    psu_channel_not_found: "Entity not found",
    psu_channel_unavailable: "Measurement unavailable",
    psu_channel_invalid: "Invalid measurement",
    psu_channel_invalid_unit: "Invalid measurement unit",
    psu_channel_stale: "Publication is stale",
    psu_channel_previous_source_generation:
      "Publication predates the confirmed source change",
    report_psu_present_only:
      "These observations describe the captured operating point. ON, nominal power and a fresh measurement do not establish future delivery. Missing values remain unknown.",
    gate_above_off_voltage: "Off: voltage above switch-off threshold",
    gate_below_on_voltage:
      "Support requested: voltage below switch-on threshold",
    gate_hysteresis_hold: "Retaining state within voltage hysteresis band",
    gate_voltage_unavailable:
      "Protective readiness: voltage unavailable or stale",
    gate_manual_readiness: "Readiness requested manually",
    gate_protection_readiness: "Readiness for battery protection",
    gate_no_request: "No support requested",
    gate_waiting_on_voltage: "Waiting for stable low voltage",
    gate_waiting_off_voltage: "Waiting for stable high voltage",
    gate_learned:
      "Learned from recent comparable observations; power is estimated",
    gate_configured_fallback:
      "Insufficient recent observations; configured SOC fallback, no assured power",

    dc_service: "additional DC supply deficit",
    forecast_last_plan: "Last plan captured",
    forecast_stale:
      "Forecast entity is unavailable. The last known plan is shown; current execution cannot be confirmed.",
    forecast_unavailable:
      "Forecast entity is unavailable. No last known plan is available.",
    selection_removed: "The selected time is no longer in this forecast.",
    report_inverter_limit_planned: "Inverter permission in the plan",
    report_inverter_control: "Inverter command and device feedback",
    report_inverter_last_known: "Last known inverter command and feedback",
    report_inverter_requested: "Requested inverter limit",
    report_inverter_observed: "Reported device limit",
    report_permission_not_power:
      "Permission is an upper limit, not measured inverter power. A command is confirmed only by device feedback.",
    report_plan_captured: "Plan inputs captured",
    report_plan_activated: "Plan activated",
    report_command_time: "Command time",
    report_feedback_time: "Device publication time",
    report_confirmation: "Device confirmation",
    report_confirmed: "Confirmed",
    report_live_reason: "Current permission reason",
    report_history_empty: "No recorded daily reports are available yet.",
    report_sources: "Sources and data quality",
    report_source: "Source role and entity",
    report_open_entity: "Open entity details",
    report_source_status: "Status",
    report_source_value: "Value",
    report_source_reported: "Publication and coverage",
    report_source_coverage: "Coverage",
    report_source_fallback: "Fallback",
    report_market_source: "Market data source",
    source_status_not_configured: "Not configured",
    source_status_not_found: "Entity not found",
    source_status_unknown: "Unknown measurement",
    source_status_unavailable: "Unavailable",
    source_status_invalid: "Invalid value or unit",
    source_status_stale: "Stale publication",
    source_status_expired: "Coverage expired",
    source_status_available: "Available",
    source_status_partial: "Partial coverage",
    source_status_cached: "Cached data",
    source_status_ambiguous: "Several sources; select one",
    source_role_pv: "PV power",
    source_role_ac: "House consumption",
    source_role_grid_import: "Grid import",
    source_role_grid_export: "Grid export",
    source_role_live_ac_grid: "Live AC grid power",
    source_role_live_ac_input: "Live AC input power",
    source_role_live_ac_output: "Live AC output power",
    source_role_market: "Market prices",
    profile_window_occupancy: "Learning window occupancy",
    profile_measurement_coverage: "Measurement coverage in the eligible period",
    profile_eligible_hours: "Eligible hours",
    profile_mature_bins: "Mature hourly bins",
    profile_learned_duration: "Learned share of forecast duration",
    profile_last_measurement: "Last usable measurement",
    profile_recent_hours: "Usable hours in the last seven complete days",
    profile_measurements_stale:
      "No recent usable measurements. A successful learning run does not mean fresh data.",
    profile_yesterday_empty: "No usable measurements yesterday.",
    card_tentative_wake:
      "Tentative plan: wake-up and valid SOC telemetry are required before execution.",
    live_ac_reason_measured_ac_demand:
      "Measured AC demand within the available reserve budget",
    live_ac_reason_soc_unavailable: "SOC telemetry unavailable or stale",
    live_ac_reason_soc_coordinate_hold:
      "Waiting for SOC coordinate stabilization and a fresh plan",
    live_ac_reason_grid_unavailable: "Grid supply unavailable",
    live_ac_reason_dc_supply: "DC supply has priority",
    live_ac_reason_measurement_unavailable:
      "Live AC measurements unavailable or stale",
    live_ac_reason_reserve_budget: "Reserve budget protected",
    live_ac_reason_inactive: "Control inactive",
    live_ac_reason_low_demand: "AC demand below the release threshold",
    live_ac_reason_no_ac_demand:
      "No measured AC demand requiring additional permission",
    live_ac_reason_off_delay: "Permission held during the low-demand delay",
    live_ac_reason_soc_protection: "SOC protection",
    live_ac_reason_awaiting_confirmation: "Waiting for device confirmation",
    live_ac_reason_inverter_limit_unconfirmed: "Inverter limit not confirmed",
    live_ac_reason_settled: "Requested source state confirmed",
    live_ac_reason_waiting_for_lock: "Waiting for source control",
    live_ac_reason_minimum_switch_interval:
      "Waiting for the minimum switching interval",
    live_ac_reason_command_failed: "Device command failed",
    live_ac_reason_grid_supply_unavailable: "Grid supply unavailable",
    inverter_lane: "Inverter",
    psu24_lane: "24 V power supply",
    psu48_lane: "48 V power supply",
    field_show_power_supplies: "Show 24/48 V power supplies",
    invalid_show_power_supplies: "show_power_supplies must be true or false",
    switching_hint: "Planned operation · colour: on · grey: off",
    switching_times: "Planned switching times",
    switching_unavailable: "No switching forecast",
    switching_until: "until",

    card_stored_in_battery: "stored in battery",
    card_battery_withdrawal_incl_losses: "battery withdrawal incl. losses",
    card_storage_unknown_source: "Storage (unknown source)",
    card_other_energy_balance_residual: "Other energy / balance residual",
    card_difference_between_root_input_charge_input_and_direct_terminal_supply_includes_modelled_overhead_and_rounding:
      "Difference between root input, charge input and direct terminal supply; includes modelled overhead and rounding.",
    card_state_of_charge: "State of charge",
    card_incomplete_energy_data: "Incomplete energy data",
    card_cumulative_energy: "Cumulative energy",
    card_planned_power: "Planned power",
    card_average_power_per_time_slot: "Average power per time slot",
    card_no_forecast_for_this_period: "No forecast for this period",
    card_enter_open_history: "Enter: open history. ",
    card_forecast_arrow_keys_to_select_time:
      "Forecast; arrow keys to select time",
    card_forecast_tab: "Forecast",
    card_on: "on",
    card_switching_state_unknown: "switching state unknown",
    card_off: "off",
    card_no_forecast_value_at_this_time: "No forecast value at this time",
    card_open_home_assistant_history: "Open Home Assistant history",
    card_running: "Running",
    card_charging: "Charging",
    card_discharging: "Discharging",
    card_ac_output: "AC output",
    card_planned_activity_bar_on_gap_off:
      "Planned activity · bar: on, gap: off",
    card_planned: "planned",
    card_time_slot_switching_times_unknown:
      "time slot; switching times unknown",
    card_from_root: "from root",
    card_from_storage: "from storage",
    card_source_change_preparation: "Source change / preparation",
    card_planned_sequence: "Planned sequence",
    card_no_activity_in_the_published_plan: "No activity in the published plan",
    card_ac_outputs_active: "AC outputs active",
    card_source_transition: "Source transition",
    card_from_storage_terminal: "From storage → terminal",
    card_charge_input: "Charge input",
    card_battery_withdrawal: "Battery withdrawal",
    card_close: "Close",
    card_power: "Power",
    card_energy: "Energy",
    card_forecast_curves_and_bars_follow_the_same_planned_charging_and_discharging_times_no_measurement_history:
      "Forecast · curves and bars follow the same planned charging and discharging times. No measurement history.",
    card_forecast_detailed_timing_unavailable_power_and_soc_are_averaged_over_time_slots_no_measurement_history:
      "Forecast · detailed timing unavailable: power and SOC are averaged over time slots. No measurement history.",
    card_source_recipient_selected_period:
      "Source → recipient · selected period",
    card_root_is_the_cascade_input_arrows_show_source_and_recipient_along_the_chain_above_ac_pass_through_is_not_battery_charging_energy_per_ac_output_is_unavailable_missing_values:
      "Root is the cascade input. Arrows show source and recipient along the chain above. AC pass-through is not battery charging; energy per AC output is unavailable. Missing values: —.",
    card_earliest_start_after_minimum_pause:
      "Earliest start after minimum pause",
    card_planning_decisions: "Planning decisions",
    card_no_cascades_configured: "No cascades configured",
    card_from_storage_to_terminal: "From storage · to terminal",
    card_root_today_forecast: "Root today · forecast",
    card_root_tomorrow_forecast: "Root tomorrow · forecast",
    card_plan_overview: "Plan overview",
    card_open_forecast: "Open forecast",
    card_underlined_values_and_chart_clicks_open_measurement_history_output_power_also_includes_pass_through:
      "Underlined values and chart clicks open measurement history. Output power also includes pass-through.",
    card_today_shows_the_remaining_plan_from_storage_refers_to_the_full_plan:
      "Today shows the remaining plan. From storage refers to the full plan.",
    card_actual_used_from_storage_today: "Actual · used from storage today",
    card_selected_period: "Selected period",
    card_today_from_now: "Today from now",
    card_tomorrow: "Tomorrow",
    card_full_plan: "Full plan",
    card_state_of_charge_at_plan_start: "State of charge at plan start",
    card_stored_in_battery_59: "Stored in battery",
    card_soc_energy_details: "SOC & energy · details",
    card_power_energy: "Power & energy ↗",
    card_battery_manager_loads: "Battery Manager · Loads",
    card_no_loads_outside_cascades_configured:
      "No loads outside cascades configured",
    card_configured: "configured",
    card_learned: "learned",
    card_from_measurements: "from measurements",
    card_power_draw_saturated: "power draw saturated",
    card_today_from_plan_start: "Today from plan start",
    card_planning_power: "Planning power",
    card_recommendation_on: "Recommendation: on",
    card_recommendation_off: "Recommendation: off",
    card_recommendation_unknown: "Recommendation unknown",
    card_unavailable: "Unavailable",
    card_power_draw_differs_from_expectation:
      "Power draw differs from expectation",
    card_stale_telemetry_blocks_execution: "Stale telemetry blocks execution",
    card_state_of_charge_at_plan_start_charge_target:
      "State of charge at plan start / charge target",
    card_robust_power_estimate_while_running:
      "Robust power estimate while running",
    card_last_learned_power: "Last learned power",
    card_forecast_planned_power_and_cumulative_energy_in_the_selected_period_the_recommendation_is_not_a_measured_switch_state_missing_values:
      "Forecast: planned power and cumulative energy in the selected period. The recommendation is not a measured switch state. Missing values: —.",
    card_planned_running_times: "Planned running times",
    card_no_running_time_planned_in_the_selected_period:
      "No running time planned in the selected period",
    reserve_no_emergency_benefit:
      "No proven overall benefit from emergency feed-in",
    execution_constraints: "Execution constraints",
    minimum_run_until: "Earliest regular stop",
    predrain_not_before: "Pre-drain no earlier than",
    check_at: "Check deadline, not a promised start",
    confirmation_pending: "Device confirmation pending",
    waiting_stability: "Waiting for stable pre-drain plan",
    stable_progress: "Matching proposals",
    feedin_decisions: "Feed-in decisions",
    feature_disabled: "Early feed-in disabled",
    runtime_paused: "Feed-in switch paused",
    manual_setpoint: "Manual setpoint",
    no_residual_export: "No residual export forecast",
    no_power_surplus: "No available power surplus",
    feedin_soc_floor: "SOC at feed-in floor",
    battery_already_full: "Battery full: natural surplus",
    feedin_deadline: "Feed-in window ended",
    continuous_load_or_peak_unproven:
      "Continuous load operation or maximum not proven",
    delayed_peak_uncovered: "Load operation does not cover delayed maximum",
    loads_exhausted_to_maximum:
      "Loads continuously planned until maximum; residual surplus",
    stress_reserve: "Forecast uncertainty requires reserve",
    export_budget_exhausted: "Daily feed-in amount already allocated",
    feedin_power_limit: "No feed-in power allowed",
    "waiting for stable plan": "Pre-drain stability waiting period",
    "confirmed minimum runtime": "Confirmed remaining minimum runtime",
    fault_unknown: "Cascade fault",
    fault_invalid_topology: "Invalid storage chain configuration",
    fault_safe_off_failed: "Safety shutdown failed",
    fault_restart_aux_reconciliation_failed:
      "Storage supply could not be restored after restart",
    fault_restart_wake_reconciliation_failed:
      "Storage wake-up could not be restored after restart",
    fault_exclusive_actor_changed_externally:
      "An exclusively controlled switch was changed externally",
    fault_root_transition_failed: "Switching to input supply failed",
    fault_wake_failed_after_retry: "Storage wake-up failed after retry",
    fault_source_power_proof_failed:
      "Storage output power could not be confirmed",
    fault_handover_failed_at_target:
      "Source change at the discharge target failed",
    fault_terminal_test_restore_failed:
      "Previous switch states could not be restored after the terminal test",
    fault_terminal_test_recovery_failed:
      "Interrupted terminal test could not be recovered",
    card_forecast: "Battery Manager Forecast",
    card_consumption: "Battery Manager Consumption",
    card_cascade: "Battery Manager Cascades",
    desc_forecast:
      "SOC forecast, inverter threshold and planned surplus loads.",
    desc_consumption:
      "Consumption forecast by voltage level and planned surplus loads.",
    desc_cascade:
      "Storage SOC, energy flows, planned sequence and cascade status.",
    field_entity: "Forecast sensor",
    field_title: "Title",
    field_hours: "Forecast horizon (hours)",
    invalid_config: "Invalid configuration",
    invalid_entity: '"entity" must be an entity id string',
    invalid_hours: '"hours" must be a finite number',
    cascade_phase_restart_reconciliation: "reconciling state after restart",
    cascade_phase_root: "input supply",
    cascade_phase_waking: "waking storage",
    cascade_phase_waking_members: "waking storage chain",
    cascade_phase_testing_terminal: "testing terminal load",
    cascade_phase_unknown: "unknown status",
    now: "now",
    threshold: "threshold",
    inverter_floor: "Inverter lower limit",
    import: "grid import",
    lost: "lost surplus",
    prevented: "prevented export",
    loads: "Surplus loads",
    today_tomorrow: "(kWh · today/tomorrow)",
    nothing_planned: "nothing planned",
    active: "active",
    feedin_wait: "feed-in waits for confirmed load start",
    "waiting for runtime release": "minimum pause has not elapsed",
    runtime_release: "earliest start after minimum pause",
    storage_action_too_small: "storage action below minimum duration or energy",
    terminal_priority: "continuous terminal operation has priority",
    same_day_export: "insufficient same-day export to repay charging",
    candidate_rejected: "tested start rejected",
    daily_peak: "daily battery target",
    additional_import: "additional grid import",
    slot_not_serviceable: "supply or cutoff limit",
    no_peak_fill_surplus: "no PV surplus for peak fill",
    soc_reserve: "SOC reserve",
    path_power_limit: "cascade power limit",
    feedin_lane: "early feed-in",
    feedin: "planned feed-in",
    realized: "measured",
    feedin_realized: "early feed-in",
    no_entity:
      "No entity configured. Pick the Battery Manager SOC forecast sensor.",
    not_found: "Entity not found:",
    no_data: "Waiting for a valid plan …",
    min_reserve: "reserve",
    render_error: "The forecast chart could not be rendered:",
    chart_label: "SOC forecast",
    sr_min: "minimum",
    sr_max: "maximum",
    kbd_hint: "Use the arrow keys to step through the forecast.",
    // Consumption card
    chart_label_consumption: "Consumption forecast",
    level_ac: "230 V AC",
    level_dc48: "48 V DC",
    level_dc24: "24 V DC",
    planned_loads: "planned loads",
    cascade: "Cascade",
    charging: "charging",
    root: "Root",
    aux: "Aux",
    cascade_chart_label: "Cascade schedule",
    cascade_no_data: "No cascade activity planned in this horizon.",
    cascade_root_input: "Root → cascade",
    discharging: "discharging",
    output: "AC output",
    terminal_load: "terminal load",
    on: "ON",
    stored: "stored",
    source: "source",
    soc: "SOC",
    total: "total",
    cascade_phase_idle: "waiting",
    cascade_phase_proving: "checking source",
    cascade_phase_running: "discharging storage",
    cascade_phase_recovering: "recharge pending",
    cascade_phase_complete: "cycle complete",
    cascade_phase_fault: "fault",
    cascade_phase_hands_off: "manual control",
    cascade_plan: "Plan",
    cascade_from_storage: "from storage",
    cascade_via_root: "from PV / Root",
    cascade_root_today_tomorrow: "Root today/tomorrow",
    cascade_used_today: "used today",
    cascade_discharge_target: "discharge limit",
    static_hint: "dimmed segments = static fallback profile",
    profile_valid_since: "AC history valid from",
    profile_reason_historical_context_missing:
      "Historical configuration or exclusion reason unavailable",
    profile_reason_state_unavailable:
      "Required switch or device state unavailable",
    profile_learned: "learned",
    profile_static: "fallback",
    profile_details: "Learning data and excluded hours",
    profile_samples_note:
      "Valid comparison days per hour / minimum for a measured uncertainty band. Fewer observations still provide a provisional estimate; with no observations in the window, the previous estimate is retained. Weekdays, weekends and absence are learned separately.",
    profile_quality_mature: "established",
    profile_quality_provisional: "provisional · uncertainty unknown",
    profile_quality_retained: "previous estimate · no observations in window",
    profile_quality_fallback: "standard profile",
    profile_daytype: "Day type",
    profile_hour: "Hour",
    profile_weekday: "Weekday",
    profile_weekend: "Weekend",
    profile_absence: "Absence",
    profile_exclusions_note:
      "Observed exclusions from the last seven days. Recent gaps are rechecked using their historical configuration; accepted values remain unchanged. Older gaps may have no recorded reason.",
    profile_no_exclusions:
      "No recorded exclusions. This does not prove complete history.",
    profile_reason_measurement_missing: "Measurement missing",
    profile_reason_negative_balance: "Negative meter balance",
    profile_reason_support_unresolved:
      "24 V supply or switch history unresolved",
    profile_reason_appliance_unresolved:
      "Appliance consumption cannot be separated",
    profile_reason_psu48_unresolved: "48 V supply energy cannot be determined",
    profile_reason_subtraction_missing: "Correction measurement missing",

    no_consumption:
      "No consumption forecast on this sensor — needs Battery Manager v0.25.5+.",
  },
  de: {
    card_soc_read_for_plan: "SOC-Ablesung für diesen Plan",
    card_soc_entity_reported:
      "Von der Entität gemeldet (Geräteaktualität unbestätigt)",
    card_soc_cached: "Zwischengespeicherter Wert",
    card_soc_source_unknown: "Quelle unbekannt",
    card_soc_publication_age: "Alter der HA-Meldung bei Planerfassung",
    card_soc_age_unknown: "Unbekannt",

    report_gate_48: "48-V-Schaltentscheidung",
    report_gate_voltage: "Aktuelle Batteriespannung",
    report_gate_learning: "SOC-Schwellenschätzung",
    report_gate_samples: "Verwertbare Beobachtungen",
    report_gate_soc: "Gelernter Einschalt-SOC beim Entladen",
    report_gate_spread: "Beobachtete Streuung",
    report_soc_coordinate: "SOC-Ablesung und Energievergleich",
    report_soc_coordinate_state: "Beobachtungszustand",
    report_soc_coordinate_event: "Letzte beobachtete Nachführung",
    report_soc_reported_change: "Vom BMS gemeldete Änderung",
    report_soc_energy_change: "SOC-Äquivalent der gemessenen Energieänderung",
    report_soc_model_residual: "Abweichung zur linearen Energierechnung",
    report_soc_covered_time: "Abgedeckter Vergleichszeitraum",
    report_soc_coordinate_explanation:
      "Der gemeldete BMS-SOC kann driften und im oberen Bereich nachkalibriert werden. Eine Nachführung ist für sich kein Gerätefehler. Die gemessene Energieänderung bestimmt keinen unabhängig bekannten absoluten SOC.",
    report_soc_conservative_forecast:
      "Konservative SOC-Prognose: Künftige 48-V-Stützung wird nur bei gültigem Modellnachweis angerechnet. Ein eingeschaltetes Netzteil garantiert keine zukünftige Lieferleistung.",
    soc_phase_tracking: "Aktuelle SOC-Koordinate wird beobachtet",
    soc_safety_curve: "Planungs- und Sicherheitskurve",
    soc_safety_decisions:
      "Für Planung und Schutzentscheidungen gilt die durchgezogene Kurve.",
    soc_expected_curve: "Bei anhaltender 48-V-Lieferung",
    soc_expected_basis:
      "Die gestrichelte Kurve setzt voraus, dass die 48-V-Lieferung bei gleicher Schaltung anhält.",
    soc_expected_unqualified:
      "Die Erwartungskurve benötigt mindestens 30 Minuten durchgehende Messwerte der 48-V-DC-Abgabe im aktuellen Versorgungsmodus.",
    soc_expected_no_window:
      "Aus der beobachteten 48-V-Lieferung ergibt sich für den aktuellen Schaltplan keine Erwartungskurve.",
    soc_expected_measured: "Kleinste gemessene 48-V-DC-Abgabe",
    soc_expected_span: "Messspanne",
    soc_expected_relay_on:
      "Relais EIN ist bestätigt. Der Relaiszustand allein belegt keine DC-Abgabe.",
    soc_expected_reason_missing_dc_meter:
      "Für die 48-V-DC-Abgabe ist kein Sensor konfiguriert.",
    soc_expected_reason_invalid_dc_topology:
      "Die konfigurierte Messung kann die DC-Abgabe des 48-V-Netzteils nicht eindeutig erfassen.",
    soc_expected_reason_missing_dc_power_limit:
      "Zur Plausibilitätsprüfung ist eine positive, endliche DC-Leistungsgrenze erforderlich. Ausgangsspannung und Maximalstrom prüfen.",
    soc_expected_reason_episode_unproven:
      "Durchgehende Messwerte der DC-Abgabe im aktuellen 48-V-Versorgungsmodus fehlen.",
    soc_expected_reason_episode_too_short:
      "Im aktuellen 48-V-Versorgungsmodus liegen weniger als 30 Minuten durchgehender Messwerte der DC-Abgabe vor.",
    soc_expected_reason_no_positive_delivery:
      "Die Messwerte belegen keine anhaltende positive 48-V-DC-Abgabe.",
    soc_expected_reason_projection_unsupported:
      "Das Modell kann diese 48-V-DC-Abgabe mit der aktuellen Anlagenkonfiguration nicht fortschreiben.",
    soc_expected_reason_no_matching_plan_window:
      "Für die gemessene Lieferung gibt es im aktuellen Schaltplan kein passendes Zeitfenster.",
    soc_expected_reason_observation_shorter_than_plan_interval:
      "Die DC-Messwerte decken das erste passende Planintervall nur teilweise ab.",
    soc_expected_outside_display:
      "Im dargestellten Zeitraum liegen zu wenige Punkte für die Erwartungskurve vor.",
    soc_expected_overlap:
      "Die Kurven liegen im gemeinsam dargestellten Zeitraum bis auf die Anzeigenauflösung übereinander.",
    soc_expected_net_curve: "Bei anhaltender Netto-Entladung",
    soc_expected_grid_curve: "Kurzfristiger SOC bei anhaltendem Netzbezug",
    soc_expected_net_reason_grid_plan_margin_not_met:
      "Bei der höheren PV-Prognose lässt die geplante Hauslast zu wenig Netzbezug oberhalb des Sollwerts erwarten.",
    soc_expected_grid_basis:
      "Die gestrichelte Kurve schreibt die gemessene Batterieleistung fort, solange der geplante Netzbezug über dem wirksamen Sollwert liegt.",
    soc_expected_grid_import: "Gemessener Netzbezug",
    soc_expected_grid_setpoint: "Wirksamer Netzbezugs-Sollwert",
    soc_expected_grid_margin: "Erforderlicher Abstand",
    soc_expected_grid_until: "Ende der kurzfristigen Erwartung um",
    soc_expected_net_reason_grid_topology_unavailable:
      "Bitte die Zuordnung von Netzanschlusspunkt und Sollwert zu den Messstellen prüfen.",
    soc_expected_net_reason_grid_power_unavailable:
      "Ein frischer vorzeichenrichtiger Messwert am Netzanschluss fehlt.",
    soc_expected_net_reason_grid_setpoint_unavailable:
      "Eine frische Rückmeldung des wirksamen Netzbezugs-Sollwerts fehlt.",
    soc_expected_net_reason_grid_not_importing:
      "Der Zähler meldet 0 W Netzbezug oder Einspeisung.",
    soc_expected_net_reason_grid_margin_not_met:
      "Der Netzbezug hat zu wenig Abstand zu null oder zum wirksamen Sollwert.",
    soc_expected_net_reason_battery_not_idle:
      "Die Batterieleistung liegt außerhalb des Ruhebereichs von ±1 W.",
    soc_expected_net_basis:
      "Die gestrichelte Kurve schreibt die gemessene Netto-Entladung ab dem aktuellen SOC für vergleichbare Planintervalle fort.",
    soc_expected_net_purpose:
      "Das Szenario verwendet die an den Batterieklemmen gemessene Netto-Entladung.",
    soc_expected_net_measured:
      "Netto-Entladung aus Messwerten an den Batterieklemmen",
    soc_expected_net_until: "Ende des vergleichbaren Nachtfensters um",
    soc_expected_net_unqualified:
      "Für das Szenario sind mindestens 30 Minuten stabiler Messwerte zur Netto-Entladung im bestätigten Versorgungsmodus erforderlich.",
    soc_expected_net_no_window:
      "Für die beobachtete Netto-Entladung der Batterie gibt es kein vergleichbares Planintervall.",
    soc_expected_net_reason_no_matching_plan_window:
      "Der aktuelle Plan enthält kein Intervall mit vergleichbarer nativer Last, Schaltung und Energieflüssen.",
    soc_expected_net_reason_observation_shorter_than_plan_interval:
      "Die Messspanne der Netto-Entladung ist kürzer als das erste passende Planintervall.",
    soc_expected_net_reason_projection_unsupported:
      "Die aktuelle Anlagenkonfiguration oder Messung reicht für dieses Netto-Entladungsszenario nicht aus.",
    soc_expected_net_reason_discharge_exceeds_native_load:
      "Die beobachtete Netto-Entladung übersteigt die geplante native 48-V-Last.",
    soc_expected_net_reason_storage_limit:
      "Im ersten projizierten Intervall würde der SOC außerhalb der eingestellten Batteriegrenzen liegen.",
    soc_expected_net_reason_window_ends_before_interval:
      "Das vergleichbare Nachtfenster endet vor dem Ende des ersten passenden Planintervalls.",
    soc_expected_net_reason_topology_unavailable:
      "Dieses Szenario erfordert eine unterstützte 48-V-Topologie und eine Leistungsmessung an den Batterieklemmen.",
    soc_expected_net_reason_mode_unconfirmed:
      "Die Bestätigung des benötigten Versorgungsmodus durch Relais, Wechselrichter oder die konfigurierte Netzquellen-Rückmeldung fehlt.",
    soc_expected_net_reason_ac_flow_incompatible:
      "Die AC-Messwerte bestätigen für diesen Zeitraum keine reine Netzdurchleitung.",
    soc_expected_net_reason_coordinate_unstable:
      "Die SOC-Koordinate der Batterie ist noch in der Stabilisierungsphase.",
    soc_expected_net_reason_telemetry_unavailable:
      "Aktuelle Batterie-, Schalt- oder PV-Messwerte fehlen oder sind ungültig.",
    soc_expected_net_reason_publication_unconfirmed:
      "Die Messwerte zum aktuellen veröffentlichten Plan sind noch nicht bestätigt.",
    soc_expected_net_reason_plan_outside_observation:
      "Der Plan beginnt außerhalb des Zeitraums mit bestätigtem Versorgungsmodus.",
    soc_expected_net_reason_voltage_above_threshold:
      "Die Batteriespannung liegt über dem eingestellten Grenzwert für vergleichbare Stützungsbedingungen.",
    soc_expected_net_reason_charging_detected:
      "Die Batteriemesswerte zeigen eine Ladung an.",
    soc_expected_net_reason_pv_detected:
      "Die PV-Messwerte zeigen Erzeugung an.",
    soc_expected_net_reason_daylight:
      "Für die kurzfristige SOC-Erwartung bei Tageslicht können die saldierte Netzleistung und der wirksame Netzbezugs-Sollwert zugeordnet werden.",
    soc_expected_net_reason_episode_too_short:
      "Im bestätigten Versorgungsmodus liegen weniger als 30 Minuten stabiler Messwerte zur Netto-Entladung vor.",
    soc_expected_net_reason_direct_meter_configured:
      "Für das Erwartungsszenario wird der konfigurierte Sensor für die 48-V-DC-Abgabe verwendet.",
    load_grid_guard_active: "Netzbezugsschutz aktiv",
    load_grid_guard_inactive: "Netzbezugsschutz inaktiv",
    load_grid_guard_fresh: "Aktueller Netzleistungswert",
    load_grid_guard_stale: "Veralteter Netzleistungswert",
    load_grid_guard_unconfigured: "Kein Netzleistungsmesser konfiguriert",
    load_grid_guard_unavailable: "Netzleistungswert nicht verfügbar",
    load_grid_guard_invalid: "Ungültiger Netzleistungswert",
    load_grid_guard_unknown: "Status der Messung unbekannt",
    load_grid_guard_age: "Alter bei Prüfung",
    load_grid_guard_cutoff: "Schaltschwelle über",
    load_grid_guard_last_stop:
      "Letzte Abschaltanforderung des Netzbezugsschutzes",
    load_grid_guard_decided_at: "Entscheidung um",
    load_grid_guard_decision_reading: "Netzleistungswert bei Entscheidung",
    soc_observation_unknown: "Beobachtung unbekannt",
    soc_phase_reconciling: "SOC-Koordinate wird nachgeführt",
    soc_phase_settling: "Stabile Beobachtungen nach der Nachführung sammeln",
    report_gate_query: "Referenzstrom der nativen 48-V-Grundlast",
    report_gate_query_status: "Qualifikation für den Referenzstrom",
    report_gate_points: "nahe Beobachtungen",
    gate_role_on: "Einschaltschätzung",
    gate_role_delivery: "Lieferschätzung",
    gate_role_off: "Ausschaltschätzung",
    gate_qualification_qualified: "Qualifizierte Beobachtungen",
    gate_qualification_insufficient_samples:
      "Zu wenige vergleichbare Beobachtungen",
    gate_qualification_insufficient_span: "Beobachtungszeitspanne zu kurz",
    gate_qualification_conflicting_coordinates:
      "Ältere und neuere SOC-Ablesungen widersprechen sich",
    gate_qualification_out_of_current_range:
      "Referenzstrom außerhalb des beobachteten Bereichs",
    gate_qualification_expired: "Nachweise abgelaufen",
    gate_qualification_inconsistent_threshold_order:
      "Schwellenbeobachtungen widersprechen sich",
    reserve_decision_soc_coordinate_hold:
      "Optionale Batterienutzung wartet auf stabile SOC-Beobachtungen",
    report_psu_observations: "Gemessene Netzteilbeobachtungen",
    report_psu_24: "24-V-Netzteil",
    report_psu_48: "48-V-Netzteil",
    report_psu_confirmed_state: "Bestätigter Schaltzustand",
    report_psu_ac_input: "Beobachtete AC-Aufnahme",
    report_psu_dc_output: "Beobachtete DC-Abgabe",
    report_psu_present_delivery: "Qualifizierte momentane DC-Lieferung",
    report_psu_observed_efficiency:
      "Wirkungsgrad aus zeitlich überdeckten Messungen",
    report_psu_ac_input_status: "Status der AC-Aufnahmemessung",
    report_psu_dc_output_status: "Status der DC-Abgabemessung",
    psu_quality_measured: "Messung vorhanden",
    psu_quality_unknown: "Keine qualifizierte Messung",
    psu_quality_stale: "Messung ist veraltet",
    psu_quality_unconfirmed: "Quellenzustand ist nicht bestätigt",
    psu_quality_incompatible: "Messung oder Quellenbindung ist ungültig",
    psu_channel_not_configured: "Nicht konfiguriert",
    psu_channel_measured: "Frische Messung",
    psu_channel_missing: "Keine Messung verfügbar",
    psu_channel_incompatible: "Unpassende Messung",
    psu_channel_hardware_limit: "Messung überschreitet Hardwaregrenze",
    psu_channel_unconfirmed: "Quellenzustand nicht bestätigt",
    psu_channel_not_found: "Entity nicht gefunden",
    psu_channel_unavailable: "Messung nicht verfügbar",
    psu_channel_invalid: "Ungültige Messung",
    psu_channel_invalid_unit: "Ungültige Messeinheit",
    psu_channel_stale: "Publikation ist veraltet",
    psu_channel_previous_source_generation:
      "Publikation liegt vor dem bestätigten Quellenwechsel",
    report_psu_present_only:
      "Diese Beobachtungen beschreiben den erfassten Betriebspunkt. ON, Nennleistung und eine frische Messung belegen keine künftige Lieferung. Fehlende Werte bleiben unbekannt.",
    gate_above_off_voltage: "Aus: Spannung oberhalb der Ausschaltgrenze",
    gate_below_on_voltage:
      "Stützung angefordert: Spannung unterhalb der Einschaltgrenze",
    gate_hysteresis_hold: "Zustand im Spannungs-Hystereseband beibehalten",
    gate_voltage_unavailable:
      "Schutzbereitschaft: Spannung fehlt oder ist veraltet",
    gate_manual_readiness: "Bereitschaft manuell angefordert",
    gate_protection_readiness: "Bereitschaft zum Batterieschutz",
    gate_no_request: "Keine Stützung angefordert",
    gate_waiting_on_voltage: "Warten auf stabil niedrige Spannung",
    gate_waiting_off_voltage: "Warten auf stabil hohe Spannung",
    gate_learned:
      "Aus aktuellen vergleichbaren Beobachtungen gelernt; Leistung geschätzt",
    gate_configured_fallback:
      "Zu wenig aktuelle Beobachtungen; konfigurierte SOC-Ersatzschwelle, keine gesicherte Leistung",

    dc_service: "zusätzliches DC-Versorgungsdefizit",
    forecast_last_plan: "Letzter Plan erfasst",
    forecast_stale:
      "Prognose-Entity ist nicht verfügbar. Der letzte bekannte Plan wird angezeigt; die aktuelle Ausführung ist unbestätigt.",
    forecast_unavailable:
      "Prognose-Entity ist nicht verfügbar. Es liegt noch kein letzter bekannter Plan vor.",
    selection_removed:
      "Die gewählte Zeit ist nicht mehr in dieser Prognose enthalten.",
    report_inverter_limit_planned: "Inverterfreigabe im Plan",
    report_inverter_control: "Inverterkommando und Geräterückmeldung",
    report_inverter_last_known:
      "Letztes bekanntes Inverterkommando und Rückmeldung",
    report_inverter_requested: "Angefragtes Inverterlimit",
    report_inverter_observed: "Gemeldetes Gerätelimit",
    report_permission_not_power:
      "Die Freigabe ist eine Obergrenze, keine gemessene Inverterleistung. Erst die Geräterückmeldung bestätigt ein Kommando.",
    report_plan_captured: "Planeingaben aufgenommen",
    report_plan_activated: "Plan aktiviert",
    report_command_time: "Kommandozeit",
    report_feedback_time: "Gerätepublikation",
    report_confirmation: "Gerätebestätigung",
    report_confirmed: "Bestätigt",
    report_live_reason: "Grund der aktuellen Freigabe",
    report_history_empty:
      "Es sind noch keine aufgezeichneten Tagesberichte verfügbar.",
    report_sources: "Quellen und Datenqualität",
    report_source: "Quellenrolle und Entity",
    report_open_entity: "Entity-Details öffnen",
    report_source_status: "Status",
    report_source_value: "Wert",
    report_source_reported: "Publikation und Abdeckung",
    report_source_coverage: "Abdeckung",
    report_source_fallback: "Fallback",
    report_market_source: "Marktdatenquelle",
    source_status_not_configured: "Nicht konfiguriert",
    source_status_not_found: "Entity nicht gefunden",
    source_status_unknown: "Messwert unbekannt",
    source_status_unavailable: "Nicht verfügbar",
    source_status_invalid: "Ungültiger Wert oder Einheit",
    source_status_stale: "Veraltete Publikation",
    source_status_expired: "Abdeckung abgelaufen",
    source_status_available: "Verfügbar",
    source_status_partial: "Teilweise Abdeckung",
    source_status_cached: "Daten aus dem Cache",
    source_status_ambiguous: "Mehrere Quellen; Auswahl erforderlich",
    source_role_pv: "PV-Leistung",
    source_role_ac: "Hausverbrauch",
    source_role_grid_import: "Netzbezug",
    source_role_grid_export: "Einspeisung",
    source_role_live_ac_grid: "Live-AC-Netzleistung",
    source_role_live_ac_input: "Live-AC-Eingangsleistung",
    source_role_live_ac_output: "Live-AC-Ausgangsleistung",
    source_role_market: "Marktpreise",
    profile_window_occupancy: "Belegung des Lernfensters",
    profile_measurement_coverage: "Messabdeckung im gültigen Zeitraum",
    profile_eligible_hours: "Gültige Zeitraumstunden",
    profile_mature_bins: "Reife Stunden-Bins",
    profile_learned_duration: "Gelernter Anteil der Prognosedauer",
    profile_last_measurement: "Letzte verwertbare Messung",
    profile_recent_hours:
      "Nutzbare Stunden der letzten sieben vollständigen Tage",
    profile_measurements_stale:
      "Keine aktuellen verwertbaren Messungen. Ein erfolgreicher Lernlauf bedeutet keine frischen Daten.",
    profile_yesterday_empty: "Gestern keine verwertbaren Messungen.",
    card_tentative_wake:
      "Vorläufiger Plan: Aufwecken und gültige SOC-Telemetrie sind vor der Ausführung erforderlich.",
    live_ac_reason_measured_ac_demand:
      "Gemessener AC-Bedarf innerhalb des verfügbaren Reservebudgets",
    live_ac_reason_soc_unavailable: "SOC-Telemetrie fehlt oder ist veraltet",
    live_ac_reason_soc_coordinate_hold:
      "Warte auf stabilisierte SOC-Koordinate und frischen Plan",
    live_ac_reason_grid_unavailable: "Netzversorgung nicht verfügbar",
    live_ac_reason_dc_supply: "DC-Versorgung hat Vorrang",
    live_ac_reason_measurement_unavailable:
      "Live-AC-Messung fehlt oder ist veraltet",
    live_ac_reason_reserve_budget: "Reservebudget wird geschützt",
    live_ac_reason_inactive: "Steuerung inaktiv",
    live_ac_reason_low_demand: "AC-Bedarf unter der Freigabeschwelle",
    live_ac_reason_no_ac_demand:
      "Kein gemessener AC-Bedarf für eine zusätzliche Freigabe",
    live_ac_reason_off_delay:
      "Freigabe bleibt während der Abschaltverzögerung bestehen",
    live_ac_reason_soc_protection: "SOC-Schutz",
    live_ac_reason_awaiting_confirmation: "Warte auf Gerätebestätigung",
    live_ac_reason_inverter_limit_unconfirmed: "Inverterlimit unbestätigt",
    live_ac_reason_settled: "Gewünschter Quellenzustand bestätigt",
    live_ac_reason_waiting_for_lock: "Warte auf Quellensteuerung",
    live_ac_reason_minimum_switch_interval: "Warte auf Mindestschaltabstand",
    live_ac_reason_command_failed: "Gerätekommando fehlgeschlagen",
    live_ac_reason_grid_supply_unavailable: "Netzversorgung nicht verfügbar",
    inverter_lane: "Inverter",
    psu24_lane: "24-V-Netzteil",
    psu48_lane: "48-V-Netzteil",
    field_show_power_supplies: "24/48-V-Netzteile anzeigen",
    invalid_show_power_supplies:
      "show_power_supplies muss true oder false sein",
    switching_hint: "Geplanter Betrieb · farbig: ein · grau: aus",
    switching_times: "Geplante Schaltzeiten",
    switching_unavailable: "Keine Schaltprognose",
    switching_until: "bis",

    card_stored_in_battery: "im Akku gespeichert",
    card_battery_withdrawal_incl_losses: "Akkuentnahme inkl. Verlusten",
    card_storage_unknown_source: "Speicher (Quelle unbekannt)",
    card_other_energy_balance_residual: "Weitere Energie / Bilanzrest",
    card_difference_between_root_input_charge_input_and_direct_terminal_supply_includes_modelled_overhead_and_rounding:
      "Differenz aus Eingang, Ladeaufnahme und direkter Endlastversorgung; enthält modellierten Eigenbedarf und Rundung.",
    card_state_of_charge: "Ladestand",
    card_incomplete_energy_data: "Keine vollständigen Energiedaten",
    card_cumulative_energy: "Energie kumuliert",
    card_planned_power: "Geplante Leistung",
    card_average_power_per_time_slot: "Ø Leistung je Zeitfenster",
    card_no_forecast_for_this_period: "Keine Prognose für diesen Zeitraum",
    card_enter_open_history: "Enter: Historie öffnen. ",
    card_forecast_arrow_keys_to_select_time:
      "Planung; Pfeiltasten zur Zeitauswahl",
    card_forecast_tab: "Planung",
    card_on: "ein",
    card_switching_state_unknown: "Schaltzustand unbekannt",
    card_off: "aus",
    card_no_forecast_value_at_this_time:
      "Kein Prognosewert zu diesem Zeitpunkt",
    card_open_home_assistant_history: "Home-Assistant-Historie öffnen",
    card_running: "Betrieb",
    card_charging: "Laden",
    card_discharging: "Entladen",
    card_ac_output: "AC-Ausgang",
    card_planned_activity_bar_on_gap_off:
      "Geplante Aktivität · Balken: ein, Lücke: aus",
    card_planned: "geplant",
    card_time_slot_switching_times_unknown:
      "Zeitfenster; Schaltzeiten unbekannt",
    card_from_root: "über Eingang",
    card_from_storage: "aus Speicher",
    card_source_change_preparation: "Quellenwechsel / Vorbereitung",
    card_planned_sequence: "Geplanter Ablauf",
    card_no_activity_in_the_published_plan:
      "Keine Aktivität im veröffentlichten Plan",
    card_ac_outputs_active: "AC-Ausgänge aktiv",
    card_source_transition: "Quellenwechsel",
    card_from_storage_terminal: "Aus Speichern → Endlast",
    card_charge_input: "Aufnahme",
    card_battery_withdrawal: "Akkuentnahme",
    card_close: "Schließen",
    card_power: "Leistung",
    card_energy: "Energie",
    card_forecast_curves_and_bars_follow_the_same_planned_charging_and_discharging_times_no_measurement_history:
      "Planung · Kurven und Balken folgen denselben geplanten Lade- und Entladezeiten. Keine Messhistorie.",
    card_forecast_detailed_timing_unavailable_power_and_soc_are_averaged_over_time_slots_no_measurement_history:
      "Planung · Genaue Zeitauflösung fehlt: Leistung und SOC sind über Zeitfenster gemittelt. Keine Messhistorie.",
    card_source_recipient_selected_period:
      "Quelle → Empfänger · ausgewählter Zeitraum",
    card_root_is_the_cascade_input_arrows_show_source_and_recipient_along_the_chain_above_ac_pass_through_is_not_battery_charging_energy_per_ac_output_is_unavailable_missing_values:
      "Eingang bezeichnet die Versorgung am Anfang der Kaskade. Pfeile zeigen Quelle und Empfänger entlang der oben dargestellten Kette. AC-Durchleitung ist keine Akkuladung; je AC-Ausgang liegt keine eigene Energiemenge vor. Fehlende Werte: —.",
    card_earliest_start_after_minimum_pause:
      "Frühester Start nach Mindestpause",
    card_planning_decisions: "Planungsgründe",
    card_no_cascades_configured: "Keine Kaskaden konfiguriert",
    card_from_storage_to_terminal: "Aus Speichern · an Endlast",
    card_root_today_forecast: "Eingang heute · Plan",
    card_root_tomorrow_forecast: "Eingang morgen · Plan",
    card_plan_overview: "Planübersicht",
    card_open_forecast: "Prognose öffnen",
    card_underlined_values_and_chart_clicks_open_measurement_history_output_power_also_includes_pass_through:
      "Unterstrichene Werte und Diagrammklicks öffnen die Messhistorie. Ausgangsleistung enthält auch Durchleitung.",
    card_today_shows_the_remaining_plan_from_storage_refers_to_the_full_plan:
      "Heute zeigt den verbleibenden Plan. Aus Speichern bezieht sich auf den gesamten Plan.",
    card_actual_used_from_storage_today: "Ist · heute aus Speichern genutzt",
    card_selected_period: "Ausgewählter Zeitraum",
    card_today_from_now: "Heute ab jetzt",
    card_tomorrow: "Morgen",
    card_full_plan: "Gesamter Plan",
    card_state_of_charge_at_plan_start: "Ladestand am Planstart",
    card_stored_in_battery_59: "Im Akku gespeichert",
    card_soc_energy_details: "SOC & Energie · Details",
    card_power_energy: "Leistung & Energie ↗",
    card_battery_manager_loads: "Battery Manager · Lasten",
    card_no_loads_outside_cascades_configured:
      "Keine Lasten außerhalb von Kaskaden konfiguriert",
    card_configured: "konfiguriert",
    card_learned: "gelernt",
    card_from_measurements: "aus Messwerten",
    card_power_draw_saturated: "Leistungsaufnahme gesättigt",
    card_today_from_plan_start: "Heute ab Planstart",
    card_planning_power: "Planungsleistung",
    card_recommendation_on: "Empfehlung: ein",
    card_recommendation_off: "Empfehlung: aus",
    card_recommendation_unknown: "Empfehlung unbekannt",
    card_unavailable: "Nicht verfügbar",
    card_power_draw_differs_from_expectation:
      "Leistungsaufnahme weicht von der Erwartung ab",
    card_stale_telemetry_blocks_execution:
      "Veraltete Telemetrie blockiert die Ausführung",
    card_state_of_charge_at_plan_start_charge_target:
      "Ladestand am Planstart / Ladeziel",
    card_robust_power_estimate_while_running:
      "Robuste Leistungsschätzung während des Betriebs",
    card_last_learned_power: "Zuletzt gelernte Leistung",
    card_forecast_planned_power_and_cumulative_energy_in_the_selected_period_the_recommendation_is_not_a_measured_switch_state_missing_values:
      "Prognose: geplante Leistung und kumulierte Energie im ausgewählten Zeitraum. Die Empfehlung ist kein gemessener Schaltzustand. Fehlende Werte: —.",
    card_planned_running_times: "Geplante Laufzeiten",
    card_no_running_time_planned_in_the_selected_period:
      "Keine Laufzeit im ausgewählten Zeitraum geplant",
    reserve_no_emergency_benefit:
      "Kein nachgewiesener Gesamtnutzen einer Notfalleinspeisung",
    execution_constraints: "Ausführbarkeit",
    minimum_run_until: "Frühestes reguläres Laufende",
    predrain_not_before: "Vorlauf frühestens",
    check_at: "Prüffrist, keine Startzusage",
    confirmation_pending: "Gerätebestätigung ausstehend",
    waiting_stability: "Warte auf stabilen Vorlaufplan",
    stable_progress: "Passende Vorschläge",
    feedin_decisions: "Einspeisungsgründe",
    feature_disabled: "Vorzeitige Einspeisung deaktiviert",
    runtime_paused: "Einspeisungsschalter pausiert",
    manual_setpoint: "Manueller Sollwert",
    no_residual_export: "Kein verbleibender Export geplant",
    no_power_surplus: "Kein verfügbarer Leistungsüberschuss",
    feedin_soc_floor: "SOC an der Einspeise-Untergrenze",
    battery_already_full: "Akku voll: natürlicher Überschuss",
    feedin_deadline: "Einspeise-Zeitfenster beendet",
    continuous_load_or_peak_unproven:
      "Durchgängiger Lastbetrieb oder Maximum nicht nachgewiesen",
    delayed_peak_uncovered:
      "Verschobenes Maximum nicht durch Lastbetrieb abgedeckt",
    loads_exhausted_to_maximum:
      "Lasten durchgängig bis zum Maximum eingeplant; verbleibender Überschuss",
    stress_reserve: "Prognoseunsicherheit erfordert Reserve",
    export_budget_exhausted: "Tages-Einspeisemenge bereits eingeplant",
    feedin_power_limit: "Keine Einspeiseleistung freigegeben",
    "waiting for stable plan": "Stabilitätswartezeit für Vorlauf",
    "confirmed minimum runtime": "Bestätigte verbleibende Mindestlaufzeit",
    fault_unknown: "Kaskadenstörung",
    fault_invalid_topology: "Ungültige Konfiguration der Speicherkette",
    fault_safe_off_failed: "Sicherheitsabschaltung fehlgeschlagen",
    fault_restart_aux_reconciliation_failed:
      "Speicherversorgung konnte nach Neustart nicht abgeglichen werden",
    fault_restart_wake_reconciliation_failed:
      "Speicheraktivierung konnte nach Neustart nicht abgeglichen werden",
    fault_exclusive_actor_changed_externally:
      "Ein exklusiv gesteuerter Schalter wurde extern geändert",
    fault_root_transition_failed:
      "Umschaltung auf Eingangsversorgung fehlgeschlagen",
    fault_wake_failed_after_retry:
      "Speicheraktivierung auch nach Wiederholung fehlgeschlagen",
    fault_source_power_proof_failed:
      "Ausgangsleistung des Speichers konnte nicht bestätigt werden",
    fault_handover_failed_at_target:
      "Quellenwechsel beim Entladeziel fehlgeschlagen",
    fault_terminal_test_restore_failed:
      "Vorherige Schalterzustände konnten nach dem Endlasttest nicht wiederhergestellt werden",
    fault_terminal_test_recovery_failed:
      "Unterbrochener Endlasttest konnte nicht wiederhergestellt werden",
    card_forecast: "Battery Manager Prognose",
    card_consumption: "Battery Manager Verbrauch",
    card_cascade: "Battery Manager Kaskaden",
    desc_forecast:
      "SOC-Prognose, Wechselrichterschwelle und geplante Überschusslasten.",
    desc_consumption:
      "Verbrauchsprognose nach Spannungsebene und geplante Überschusslasten.",
    desc_cascade:
      "Speicher-SOC, Energieflüsse, geplanter Ablauf und Kaskadenstatus.",
    field_entity: "Prognosesensor",
    field_title: "Titel",
    field_hours: "Prognosezeitraum (Stunden)",
    invalid_config: "Ungültige Konfiguration",
    invalid_entity: '"entity" muss eine Entitäts-ID als Text sein',
    invalid_hours: '"hours" muss eine endliche Zahl sein',
    cascade_phase_restart_reconciliation: "Zustandsabgleich nach Neustart",
    cascade_phase_root: "Versorgung über Eingang",
    cascade_phase_waking: "weckt Speicher",
    cascade_phase_waking_members: "weckt Speicherkette",
    cascade_phase_testing_terminal: "prüft Endlast",
    cascade_phase_unknown: "Status unbekannt",
    now: "jetzt",
    threshold: "Schwelle",
    inverter_floor: "Inverter-Untergrenze",
    import: "Netzimport",
    lost: "verlorener Überschuss",
    prevented: "verhinderter Export",
    loads: "Überschusslasten",
    today_tomorrow: "(kWh · heute/morgen)",
    nothing_planned: "nichts geplant",
    active: "aktiv",
    feedin_wait: "Einspeisung wartet auf bestätigten Laststart",
    "waiting for runtime release": "Mindestpause noch nicht abgelaufen",
    runtime_release: "Frühester Start nach Mindestpause",
    storage_action_too_small:
      "Speicheraktion unter Mindestlaufzeit oder Mindestenergie",
    terminal_priority: "durchgängiger Endlastbetrieb hat Vorrang",
    same_day_export:
      "zu wenig gleichzeitiger oder späterer Tagesexport für die Ladung",
    candidate_rejected: "geprüfter Start verworfen",
    daily_peak: "Batterie-Tagesziel",
    additional_import: "zusätzlicher Netzbezug",
    slot_not_serviceable: "Versorgung oder Abschaltgrenze",
    no_peak_fill_surplus: "kein PV-Überschuss für Spitzenfüllung",
    soc_reserve: "SOC-Reserve",
    path_power_limit: "Kaskaden-Leistungsgrenze",
    feedin_lane: "vorzeitige Einspeisung",
    feedin: "geplante Einspeisung",
    realized: "Ist",
    feedin_realized: "frühe Einspeisung",
    no_entity:
      "Keine Entität konfiguriert. Wähle den SOC-Prognose-Sensor des Battery Managers.",
    not_found: "Entität nicht gefunden:",
    no_data: "Warte auf eine gültige Planung …",
    min_reserve: "Reserve",
    render_error: "Das Prognosediagramm konnte nicht dargestellt werden:",
    chart_label: "SOC-Prognose",
    sr_min: "Minimum",
    sr_max: "Maximum",
    kbd_hint: "Mit den Pfeiltasten durch die Prognose gehen.",
    // Verbrauchs-Card
    chart_label_consumption: "Verbrauchsprognose",
    level_ac: "230 V AC",
    level_dc48: "48 V DC",
    level_dc24: "24 V DC",
    planned_loads: "geplante Lasten",
    cascade: "Kaskade",
    charging: "laden",
    root: "Eingang",
    aux: "Speicher",
    cascade_chart_label: "Kaskaden-Zeitplan",
    cascade_no_data: "In diesem Zeitraum ist keine Kaskadenaktivität geplant.",
    cascade_root_input: "Eingang → Kaskade",
    discharging: "entladen",
    output: "AC-Ausgang",
    terminal_load: "Endlast",
    on: "AN",
    stored: "gespeichert",
    source: "Quelle",
    soc: "SOC",
    total: "Summe",
    cascade_phase_idle: "wartet",
    cascade_phase_proving: "prüft Speicher",
    cascade_phase_running: "entlädt Speicher",
    cascade_phase_recovering: "Wiederaufladung ausstehend",
    cascade_phase_complete: "Zyklus abgeschlossen",
    cascade_phase_fault: "Störung",
    cascade_phase_hands_off: "manuelle Steuerung",
    cascade_plan: "Plan",
    cascade_from_storage: "aus Speichern",
    cascade_via_root: "aus PV / Eingang",
    cascade_root_today_tomorrow: "Eingang heute/morgen",
    cascade_used_today: "heute genutzt",
    cascade_discharge_target: "Entladegrenze",
    static_hint: "abgedunkelte Anteile = statisches Fallback-Profil",
    profile_valid_since: "AC-Historie gültig ab",
    profile_reason_historical_context_missing:
      "Historische Konfiguration oder Ausschlussgrund fehlt",
    profile_reason_state_unavailable:
      "Benötigter Schalter- oder Gerätezustand nicht verfügbar",
    profile_learned: "gelernt",
    profile_static: "Fallback",
    profile_details: "Lerndaten und ausgeschlossene Stunden",
    profile_samples_note:
      "Gültige Vergleichstage je Stunde / Mindestanzahl für ein gemessenes Streuungsband. Weniger Messungen liefern eine vorläufige Schätzung; ohne Messungen im Lernfenster bleibt die bisherige Schätzung erhalten. Werktage, Wochenenden und Abwesenheit werden getrennt gelernt.",
    profile_quality_mature: "etabliert",
    profile_quality_provisional: "vorläufig · Unsicherheit unbekannt",
    profile_quality_retained:
      "bisherige Schätzung · keine Messungen im Lernfenster",
    profile_quality_fallback: "Standardprofil",
    profile_daytype: "Tagtyp",
    profile_hour: "Stunde",
    profile_weekday: "Werktag",
    profile_weekend: "Wochenende",
    profile_absence: "Abwesenheit",
    profile_exclusions_note:
      "Beobachtete Ausschlüsse der letzten sieben Tage. Aktuelle Lücken werden mit der damaligen Konfiguration nachgeprüft; akzeptierte Werte bleiben erhalten. Für ältere Lücken kann der Grund fehlen.",
    profile_no_exclusions:
      "Keine Ausschlussgründe aufgezeichnet. Das bestätigt keine lückenlose Historie.",
    profile_reason_measurement_missing: "Messwert fehlt",
    profile_reason_negative_balance: "Negative Zählerbilanz",
    profile_reason_support_unresolved:
      "24-V-Versorgung oder Schalterhistorie nicht eindeutig",
    profile_reason_appliance_unresolved: "Geräteverbrauch nicht abgrenzbar",
    profile_reason_psu48_unresolved:
      "Energie des 48-V-Netzteils nicht bestimmbar",
    profile_reason_subtraction_missing:
      "Messwert zur Verbrauchsbereinigung fehlt",

    no_consumption:
      "Keine Verbrauchsprognose im Sensor — benötigt Battery Manager v0.25.5+.",
  },
};

// Picker/editor callbacks have no hass argument. Resolve the HA user language
// on access so neither module loading nor a sensor cache freezes the locale.
export function uiLanguage(hass) {
  return (
    hass?.language ||
    (typeof document !== "undefined" &&
      document.querySelector("home-assistant")?.hass?.language) ||
    (typeof navigator !== "undefined" && navigator.language) ||
    "en"
  );
}

export function localize(hass, key) {
  const lang = uiLanguage(hass).toLowerCase().split(/[-_]/)[0];
  return (STRINGS[lang] || STRINGS.en)[key] || STRINGS.en[key] || key;
}

Object.assign(STRINGS.en, {
  report_joint_market_available:
    "EPEX available · expensive intervals share the AC/DC budget",
  report_joint_title: "Shared AC/DC discharge budget",
  report_joint_explanation:
    "Battery energy is allocated to expensive intervals across AC and DC. Reserve and fixed supply phases remain protected; daily hours follow the available energy and prices.",
  report_joint_result: "Allocation result",
  report_joint_budget: "Total battery discharge budget",
  report_joint_unavoidable_dc: "Unavoidable DC battery draw",
  report_joint_shifted: "Battery energy shifted to expensive intervals",
  report_joint_rejections: "Reasons alternatives were rejected",
  report_joint_daily_hours: "Selected hours",
  report_joint_battery_draw: "Battery draw",
  report_joint_end_soc: "SOC at forecast end",
  report_joint_reference: "Reference plan",
  report_joint_selected: "Selected plan",
  report_joint_intervals: "Selected economic intervals · highest price first",
  report_joint_interval: "Interval",
  report_joint_allocated_battery: "Allocated battery energy",
  report_joint_avoided_import: "Grid import avoided by allocation",
  report_joint_import_change: "Grid import reduction versus reference",
  report_joint_no_intervals: "No economic intervals selected.",
  report_joint_reason_accepted:
    "Energy shifted within the existing discharge budget.",
  report_joint_reason_no_flex_budget:
    "No flexible discharge budget is available.",
  report_joint_reason_unknown_price:
    "Price coverage is incomplete; the existing allocation is retained.",
  report_joint_reason_uncertainty_dc_service:
    "Would reduce DC supply under the lower PV forecast.",
  report_joint_reason_uncertainty_import:
    "Would increase grid import under the lower PV forecast.",
  report_joint_reason_uncertainty_reserve:
    "Would reduce the protected reserve under the lower PV forecast.",
  report_joint_reason_dc_service: "Would reduce delivered DC supply.",
  report_joint_reason_extra_psu_charge:
    "Would increase incidental power supply charging.",
  report_joint_reason_source_inverter_loop:
    "Would feed power supply energy back through the inverter.",
  report_joint_reason_reserve_floor: "Would breach the protected reserve.",
  report_joint_reason_energy_budget:
    "Would exceed the battery discharge budget.",
  report_joint_reason_window_end_energy:
    "Would leave less battery energy at a fixed phase boundary.",
  report_joint_reason_extra_export: "Would increase grid export.",
  report_joint_reason_extra_import: "Would increase total grid import.",
  report_joint_reason_source_unavailable:
    "Required supply source is unavailable.",
  report_joint_reason_phase: "A fixed supply phase prevents this shift.",
  report_joint_reason_search_limit:
    "The comparison limit was reached; a better allocation may still exist.",
  report_joint_reason_construction_limit:
    "The proposal construction limit was reached.",
  report_joint_reason_unsearched_alternatives:
    "Other combinations remain unsearched; rejection reasons describe individual proposals.",
  report_joint_reason_no_improvement:
    "No improvement found among the compared allocations.",
  report_joint_reason_market_off: "Market price allocation is disabled.",
  report_joint_reason_flat_prices: "Available prices do not favor a shift.",
  report_joint_reason_missing_prices: "Usable market prices are missing.",
  reserve_decision_dc_priority:
    "Keep energy for DC: AC preparation would require additional later grid support.",
  report_market: "Market peak preference",
  report_market_available:
    "EPEX available · prefer expensive hours within the AC budget",
  report_market_fallback:
    "No usable EPEX series · useful load determines priority",
  report_market_off: "Disabled",
  report_market_windows: "Preferred market windows (no mandatory runtime)",
  report_market_avoided_import:
    "Forecast grid import avoided by inverter (after standby)",
  report_actual_soc: "Actual SOC",
  report_preparation_horizon_end: "Prepare for PV until",
  report_preparation_today_tomorrow:
    "PV preparation considers the full available forecast horizon in the Home Assistant time zone.",
  report_reserve_energy_policy:
    "Retain surplus energy; discharge only as needed to make room for forecast PV. The inverter lower limit is a technical AC discharge limit, not a reserve or discharge target.",
  report_unavoidable_export: "Unavoidable forecast export",
  report_reserve_decision: "Current decision",
  report_reserve_decision_unknown: "No decision reason available",
  report_dc24_transfer_unverified:
    "24 V grid takeover is blocked: independent DC/DC fallback during grid and Home Assistant outages has not been confirmed in the settings.",
  report_reserve_shadow_explanation:
    "Shadow calculation only. The existing control policy remains active.",
  reserve_decision_no_preparation_needed:
    "No additional AC discharge for PV preparation is needed now.",
  reserve_decision_pv_headroom_preparation:
    "Forecast PV requires additional battery headroom.",
  reserve_decision_dc_support_protection:
    "DC supply protection determines the current source switching and AC discharge limit.",
  reserve_decision_dc_reserve_holding:
    "DC power supplies preserve SOC because no battery discharge budget is available now.",
  reserve_decision_manual_support:
    "Manual PSU support blocks AC battery discharge.",
  reserve_decision_no_ac_demand:
    "No usable AC demand for battery discharge is forecast.",
  report_additional_headroom_needed: "Additional headroom needed now",
  report_preparation_from: "Preparation from",
  report_expected_minimum_soc: "Expected minimum SOC",
  report_additional_grid_import_for_reserve:
    "Additional grid import for reserve",
  report_remaining_battery_discharge: "Remaining battery discharge",
  report_live_dc_source: "Measured DC source transition",
  report_live_dc_pv_confirmed:
    "PV supply confirmed; automatic power supplies are off.",
  report_live_dc_pv_pending:
    "PV source transition pending; device confirmation or renewed permission is required.",
  report_dc_budget: "Available DC discharge budget (forecast horizon)",
  report_dc_shifted: "DC battery energy shifted to market peaks",
  reserve_decision_dc_pv_supply:
    "PV surplus supplies the DC consumers; automatic power supplies remain off.",
  reserve_decision_dc_market_supply:
    "Already permitted DC battery energy supplies consumers during a market peak.",
  report_incidental_psu_charging: "Incidental PSU charging",
  report_expected_48_v_support: "Expected 48 V support",
  report_inverter_limit_now: "Currently permitted inverter power",
  report_year_round_reserve: "Year-round reserve",
  report_shadow: "Shadow",
  report_active: "Active",
  report_forecast_driven_control_without_a_waiting_period:
    "Forecast-driven control without a waiting period.",
  report_observation_time_optional: "Observation time (optional)",
  report_the_available_psus_cannot_fully_hold_soc_at_present:
    "The available PSUs cannot fully hold SOC at present.",
  report_forecast_bands_otherwise_uncalibrated_pv_factor:
    "Headroom follows the expected forecast with bounded uncertainty. Upper-scenario fallback factor",
  report_no_targeted_grid_recharge_feed_in_requires_proven_emergency_benefit:
    "No targeted grid recharge. Feed-in requires proven emergency benefit.",
  report_house_consumption: "House consumption",
  report_grid_import: "Grid import",
  report_grid_export: "Grid export",
  report_ac_input_including_pass_through: "AC input including pass-through",
  report_actor_time_actual_planned: "Actor time actual/planned",
  report_runtime_power_component: "Runtime/power component",
  report_coverage_actor_time_energy: "Coverage actor time/energy",
  report_switch_requests: "Switch requests",
  report_state_changes: "State changes",
  report_metric: "Metric",
  report_planned: "Planned",
  report_actual: "Actual",
  report_coverage: "Coverage",
  report_observed_min_max: "observed min/max",
  report_observation_gap: "Observation gap",
  report_service_failures: "Service failures",
  report_daily_comparison_plan_and_operation:
    "Daily comparison · plan and operation",
  report_planned_and_actual_values_cover_the_same_measured_intervals_only_means_m:
    "Planned and actual values cover the same measured intervals only. — means missing measurements. Actor time does not prove useful energy; deviations alone do not establish a cause.",
  report_older_detailed_events_were_removed_by_the_retention_limit_daily_reports_:
    "Older detailed events were removed by the retention limit; daily reports remain.",
  report_a_recording_error_occurred: "A recording error occurred",
});
Object.assign(STRINGS.de, {
  report_joint_market_available:
    "EPEX verfügbar · teure Intervalle teilen sich das AC/DC-Budget",
  report_joint_title: "Gemeinsames AC/DC-Entnahmebudget",
  report_joint_explanation:
    "Batterieenergie wird über AC und DC auf teure Intervalle verteilt. Reserve und feste Versorgungsphasen bleiben geschützt; die täglichen Stunden ergeben sich aus verfügbarer Energie und Preisen.",
  report_joint_result: "Ergebnis der Verteilung",
  report_joint_budget: "Gesamtes Batterie-Entnahmebudget",
  report_joint_unavoidable_dc: "Unvermeidbare DC-Batterieentnahme",
  report_joint_shifted: "In teure Intervalle verschobene Batterieenergie",
  report_joint_rejections: "Gründe für verworfene Alternativen",
  report_joint_daily_hours: "Ausgewählte Stunden",
  report_joint_battery_draw: "Batterieentnahme",
  report_joint_end_soc: "SOC am Prognoseende",
  report_joint_reference: "Referenzplan",
  report_joint_selected: "Gewählter Plan",
  report_joint_intervals:
    "Ausgewählte wirtschaftliche Intervalle · höchster Preis zuerst",
  report_joint_interval: "Intervall",
  report_joint_allocated_battery: "Zugewiesene Batterieenergie",
  report_joint_avoided_import: "Durch Zuteilung vermiedener Netzbezug",
  report_joint_import_change: "Netzbezugsminderung gegenüber Referenz",
  report_joint_no_intervals: "Keine wirtschaftlichen Intervalle ausgewählt.",
  report_joint_reason_accepted:
    "Energie innerhalb des bestehenden Entnahmebudgets verschoben.",
  report_joint_reason_no_flex_budget:
    "Kein flexibles Entnahmebudget verfügbar.",
  report_joint_reason_unknown_price:
    "Die Preisabdeckung ist unvollständig; die bestehende Zuteilung bleibt erhalten.",
  report_joint_reason_uncertainty_dc_service:
    "Würde die DC-Versorgung bei niedrigerer PV-Prognose verringern.",
  report_joint_reason_uncertainty_import:
    "Würde den Netzbezug bei niedrigerer PV-Prognose erhöhen.",
  report_joint_reason_uncertainty_reserve:
    "Würde die geschützte Reserve bei niedrigerer PV-Prognose verringern.",
  report_joint_reason_dc_service:
    "Würde die bereitgestellte DC-Versorgung verringern.",
  report_joint_reason_extra_psu_charge:
    "Würde die technisch bedingte Netzteilladung erhöhen.",
  report_joint_reason_source_inverter_loop:
    "Würde Netzteilenergie über den Inverter zurückspeisen.",
  report_joint_reason_reserve_floor:
    "Würde die geschützte Reserve unterschreiten.",
  report_joint_reason_energy_budget:
    "Würde das Batterie-Entnahmebudget überschreiten.",
  report_joint_reason_window_end_energy:
    "Würde an einer festen Phasengrenze weniger Batterieenergie zurücklassen.",
  report_joint_reason_extra_export: "Würde die Netzeinspeisung erhöhen.",
  report_joint_reason_extra_import: "Würde den gesamten Netzbezug erhöhen.",
  report_joint_reason_source_unavailable:
    "Benötigte Versorgungsquelle ist nicht verfügbar.",
  report_joint_reason_phase:
    "Eine feste Versorgungsphase verhindert diese Verschiebung.",
  report_joint_reason_search_limit:
    "Die Vergleichsgrenze wurde erreicht; eine bessere Verteilung kann weiterhin möglich sein.",
  report_joint_reason_construction_limit:
    "Die Grenze für die Vorschlagskonstruktion wurde erreicht.",
  report_joint_reason_unsearched_alternatives:
    "Weitere Kombinationen bleiben ungeprüft; Ablehnungsgründe betreffen einzelne Vorschläge.",
  report_joint_reason_no_improvement:
    "Unter den verglichenen Verteilungen wurde keine Verbesserung gefunden.",
  report_joint_reason_market_off:
    "Verteilung nach Marktpreisen ist deaktiviert.",
  report_joint_reason_flat_prices:
    "Die verfügbaren Preise begünstigen keine Verschiebung.",
  report_joint_reason_missing_prices: "Nutzbare Marktpreise fehlen.",
  reserve_decision_dc_priority:
    "Energie für DC erhalten: AC-Vorbereitung würde spätere zusätzliche Netzteilversorgung erfordern.",
  report_market: "Marktspitzen bevorzugen",
  report_market_available:
    "EPEX verfügbar · teure Stunden innerhalb des AC-Budgets bevorzugen",
  report_market_fallback:
    "Keine nutzbare EPEX-Zeitreihe · AC-Last bestimmt die Priorität",
  report_market_off: "Deaktiviert",
  report_market_windows: "Bevorzugte Marktfenster (keine Pflichtlaufzeit)",
  report_market_avoided_import:
    "Prognostizierter vermiedener Netzbezug durch Inverter (nach Standby)",
  report_actual_soc: "Ist-SOC",
  report_preparation_horizon_end: "PV-Vorbereitung bis",
  report_preparation_today_tomorrow:
    "Die PV-Vorbereitung berücksichtigt den gesamten verfügbaren Prognosehorizont in der Home-Assistant-Zeitzone.",
  report_reserve_energy_policy:
    "Übrige Energie erhalten; nur so weit entladen, wie für die erwartete PV-Energie nötig. Die Inverter-Untergrenze ist eine technische AC-Entladegrenze, kein Reserve- oder Entladeziel.",
  report_unavoidable_export: "Unvermeidbare prognostizierte Einspeisung",
  report_reserve_decision: "Aktuelle Entscheidung",
  report_reserve_decision_unknown: "Kein Entscheidungsgrund verfügbar",
  report_dc24_transfer_unverified:
    "24-V-Netzübernahme gesperrt: Der unabhängige DC/DC-Rückfall bei Netz- und Home-Assistant-Ausfall ist in den Einstellungen nicht bestätigt.",
  report_reserve_shadow_explanation:
    "Nur Schattenrechnung. Die bisherige Steuerung bleibt aktiv.",
  reserve_decision_no_preparation_needed:
    "Aktuell ist keine zusätzliche AC-Entladung zur PV-Vorbereitung nötig.",
  reserve_decision_pv_headroom_preparation:
    "Die erwartete PV-Energie benötigt zusätzlichen Freiraum im Speicher.",
  reserve_decision_dc_support_protection:
    "Der Schutz der DC-Versorgung bestimmt die aktuelle Quellenumschaltung und AC-Entladegrenze.",
  reserve_decision_dc_reserve_holding:
    "Die DC-Netzteile erhalten den SOC, weil aktuell kein Batterie-Entnahmebudget verfügbar ist.",
  reserve_decision_manual_support:
    "Manuell angeforderte Netzteilstützung sperrt die AC-Batterieentladung.",
  reserve_decision_no_ac_demand:
    "Es ist kein nutzbarer AC-Verbrauch für die Batterieentladung prognostiziert.",
  report_additional_headroom_needed: "Jetzt zusätzlich benötigter Freiraum",
  report_preparation_from: "Vorbereitung ab",
  report_expected_minimum_soc: "Erwarteter Mindest-SOC",
  report_additional_grid_import_for_reserve:
    "Zusätzlicher Netzbezug für Reserve",
  report_remaining_battery_discharge: "Verbleibende Batterieentladung",
  report_live_dc_source: "Gemessene DC-Quellenumschaltung",
  report_live_dc_pv_confirmed:
    "PV-Versorgung bestätigt; automatische Netzteile sind aus.",
  report_live_dc_pv_pending:
    "PV-Quellenumschaltung ausstehend; Gerätebestätigung oder erneute Freigabe erforderlich.",
  report_dc_budget: "Verfügbares DC-Entnahmebudget (Prognosehorizont)",
  report_dc_shifted: "In Marktspitzen verschobene DC-Batterieenergie",
  reserve_decision_dc_pv_supply:
    "PV-Überschuss versorgt die DC-Verbraucher; automatische Netzteile bleiben aus.",
  reserve_decision_dc_market_supply:
    "Bereits freigegebene DC-Batterieenergie versorgt die Verbraucher während einer Marktspitze.",
  report_incidental_psu_charging: "Technisch bedingte Netzteilladung",
  report_expected_48_v_support: "Erwartete 48-V-Stützung",
  report_inverter_limit_now: "Aktuell erlaubte Inverterleistung",
  report_year_round_reserve: "Ganzjährige Reserve",
  report_shadow: "Schattenbetrieb",
  report_active: "Aktiv",
  report_forecast_driven_control_without_a_waiting_period:
    "Steuerung anhand der Prognosen, ohne Wartezeit.",
  report_observation_time_optional: "Beobachtungszeit (optional)",
  report_the_available_psus_cannot_fully_hold_soc_at_present:
    "Die vorhandenen Netzteile können den SOC derzeit nicht vollständig halten.",
  report_forecast_bands_otherwise_uncalibrated_pv_factor:
    "Speicherplatz folgt der normalen Prognose mit begrenzter Unsicherheit. Ersatzfaktor des oberen Szenarios",
  report_no_targeted_grid_recharge_feed_in_requires_proven_emergency_benefit:
    "Keine gezielte Netzladung. Einspeisung nur bei nachgewiesenem Notfallnutzen.",
  report_house_consumption: "Wohnungsverbrauch",
  report_grid_import: "Netzbezug",
  report_grid_export: "Netzeinspeisung",
  report_ac_input_including_pass_through:
    "AC-Eingang einschließlich Durchleitung",
  report_actor_time_actual_planned: "Aktorzeit Ist/Plan",
  report_runtime_power_component: "Laufzeit-/Leistungsanteil",
  report_coverage_actor_time_energy: "Abdeckung Aktorzeit/Energie",
  report_switch_requests: "Schaltanforderungen",
  report_state_changes: "Zustandswechsel",
  report_metric: "Messgröße",
  report_planned: "Plan",
  report_actual: "Ist",
  report_coverage: "Abdeckung",
  report_observed_min_max: "beobachtet Min/Max",
  report_observation_gap: "Beobachtungslücke",
  report_service_failures: "Servicefehler",
  report_daily_comparison_plan_and_operation:
    "Tagesvergleich · Plan und Betrieb",
  report_planned_and_actual_values_cover_the_same_measured_intervals_only_means_m:
    "Plan und Ist beziehen sich nur auf dieselben abgedeckten Messintervalle. — bedeutet fehlende Messdaten. Aktorzeit beweist keine Nutzenergie; Abweichungen allein beweisen keine Ursache.",
  report_older_detailed_events_were_removed_by_the_retention_limit_daily_reports_:
    "Ältere Detailereignisse wurden durch die Aufbewahrungsgrenze entfernt; Tagesberichte bleiben erhalten.",
  report_a_recording_error_occurred: "Aufzeichnungsfehler aufgetreten",
});

Object.assign(STRINGS.en, {
  loads_picker_name: "Battery Manager · Loads",
  loads_picker_description: "Planning and execution for loads outside cascades",
});
Object.assign(STRINGS.de, {
  loads_picker_name: "Battery Manager · Lasten",
  loads_picker_description:
    "Planung und Ausführung für Lasten außerhalb von Kaskaden",
});

Object.assign(STRINGS.en, {
  report_joint_targets: "Observed alternatives by interval",
  report_joint_targets_note:
    "Local screening uses estimates; complete checks use sequential physics and the configured uncertainty checks. Validated alternatives may be replaced by a better plan. For rows marked “No recorded candidate”, this label refers to the exact interval and variant; overlapping partial intervals may have been checked. The search is bounded and does not establish global optimality or impossibility. Paired DC rows describe receivers in a shared exchange; their local screening is not recorded here.",
  report_joint_truncated:
    "The diagnostic list is limited; further intervals or outcomes are omitted.",
  report_joint_variant_dc_only: "DC only",
  report_joint_variant_ac_dc: "AC and DC",
  report_joint_variant_paired_dc: "DC in a paired exchange",
  report_joint_attempted: "Recorded candidates",
  report_joint_local_rejected: "Rejected in local screening",
  report_joint_proof_rejected: "Rejected after complete check",
  report_joint_proved: "Validated improvements",
  report_joint_not_searched: "No recorded candidate",
  report_joint_reason_duplicate: "Same proposed schedule already examined",
});
Object.assign(STRINGS.de, {
  report_joint_targets: "Beobachtete Alternativen je Intervall",
  report_joint_targets_note:
    "Lokale Vorprüfungen verwenden Schätzwerte; vollständige Prüfungen verwenden sequenzielle Physik und die eingerichteten Unsicherheitsprüfungen. Validierte Alternativen können einem besseren Plan weichen. Bei Zeilen mit „Kein aufgezeichneter Kandidat“ gilt die Kennzeichnung für genau dieses Intervall und diese Variante; überlappende Teilintervalle können geprüft worden sein. Die Suche ist begrenzt und beweist weder globale Optimalität noch Unmöglichkeit. Gekoppelte DC-Zeilen zeigen Empfänger eines gemeinsamen Tauschs; ihre lokalen Vorprüfungen werden hier nicht aufgezeichnet.",
  report_joint_truncated:
    "Die Diagnoseliste ist begrenzt; weitere Intervalle oder Ergebnisse fehlen.",
  report_joint_variant_dc_only: "Nur DC",
  report_joint_variant_ac_dc: "AC und DC",
  report_joint_variant_paired_dc: "DC im gekoppelten Tausch",
  report_joint_attempted: "Aufgezeichnete Kandidaten",
  report_joint_local_rejected: "Lokal abgelehnt",
  report_joint_proof_rejected: "Nach vollständiger Prüfung abgelehnt",
  report_joint_proved: "Validierte Verbesserungen",
  report_joint_not_searched: "Kein aufgezeichneter Kandidat",
  report_joint_reason_duplicate:
    "Derselbe vorgeschlagene Plan bereits untersucht",
});

Object.assign(STRINGS.en, {
  input_on: "On",
  input_off: "Off",
  input_control_title: "Input and charging state",
  input_connection: "Input switch",
  input_charge_enable: "Charging permission",
  input_physical_charging: "Reported battery charging",
  input_output: "AC output",
  input_output_power: "Output power",
  input_ownership: "Input control",
  input_owned: "Battery Manager",
  input_foreign: "External / manual",
  input_stop_reason: "Input retention / stop reason",
  input_paused_power: "Measured input power while paused",
  input_daily_estimate: "24-hour estimate at constant power",
  input_reason_charging: "Charging requested",
  input_reason_held_passthrough: "Input retained: AC output is on",
  input_reason_held_policy: "Input retained by keep_on policy",
  input_reason_held_unknown: "Input retained: output feedback unknown",
  input_reason_pending_off: "Input stop pending",
  input_reason_off: "Input off",
  input_reason_foreign: "External input retained",
  storage_title: "Storage minimum target",
  storage_minimum_target: "Preferred minimum SOC",
  storage_deficit: "Stored-energy deficit",
  storage_recovery_energy: "Recovery AC energy",
  storage_topup_energy: "Top-up AC energy",
  storage_day: "Local day",
  storage_end_soc: "Forecast end-of-day SOC",
  recharge_title: "Recharge-backed house supply",
  recharge_explanation:
    "Additional battery draw is certified against fixed solar repayment times. A search limit does not prove that further supply is impossible.",
  recharge_status: "Proof result",
  recharge_validations: "Complete validation runs",
  recharge_dc_energy: "Certified extra DCDC battery draw",
  recharge_ac_energy: "Certified extra AC battery draw",
  recharge_accepted: "Additional supply certified",
  recharge_no_release: "No additional release proved",
  recharge_search_limit: "Search limit reached",
  recharge_manual_feed_in: "Manual feed-in takes priority",
  recharge_unproved: "No complete proof was obtained for this alternative.",
  recharge_reason_anchor_energy:
    "Stored energy would not be repaid by the fixed solar deadline.",
  recharge_reason_end_energy:
    "Stored energy would be lower at the forecast end.",
  recharge_reason_consumption_coverage:
    "Consumption uncertainty does not cover the complete repayment period.",
  recharge_reason_consumption_reserve:
    "The consumption uncertainty reserve would be insufficient.",
  recharge_reason_dc_priority:
    "Additional AC supply would displace protected DC supply.",
  recharge_reason_search_limit:
    "The search limit was reached before all alternatives were checked.",
  recharge_reason_manual_feed_in:
    "Additional battery draw is paused while positive manual feed-in is active.",
});

Object.assign(STRINGS.de, {
  input_on: "Ein",
  input_off: "Aus",
  input_control_title: "Eingang und Ladezustand",
  input_connection: "Eingangsschalter",
  input_charge_enable: "Ladefreigabe",
  input_physical_charging: "Gemeldete Batterieladung",
  input_output: "AC-Ausgang",
  input_output_power: "Ausgangsleistung",
  input_ownership: "Eingangs-Schalthoheit",
  input_owned: "Battery Manager",
  input_foreign: "Extern / manuell",
  input_stop_reason: "Halte- / Abschaltgrund",
  input_paused_power: "Gemessene Eingangsaufnahme während Ladepause",
  input_daily_estimate: "24-Stunden-Hochrechnung bei gleicher Leistung",
  input_reason_charging: "Laden angefordert",
  input_reason_held_passthrough: "Eingang erhalten: AC-Ausgang ist ein",
  input_reason_held_policy: "Eingang durch keep_on-Politik erhalten",
  input_reason_held_unknown: "Eingang erhalten: Ausgangsrückmeldung unbekannt",
  input_reason_pending_off: "Eingangsabschaltung ausstehend",
  input_reason_off: "Eingang aus",
  input_reason_foreign: "Fremden Eingang erhalten",
  storage_title: "Speicher-Mindestziel",
  storage_minimum_target: "Bevorzugtes Mindest-SOC",
  storage_deficit: "Fehlende gespeicherte Energie",
  storage_recovery_energy: "Recovery-Energie am AC-Eingang",
  storage_topup_energy: "Top-up-Energie am AC-Eingang",
  storage_day: "Lokaler Tag",
  storage_end_soc: "Prognostiziertes Tagesend-SOC",
  recharge_title: "Rückladegesicherte Hausversorgung",
  recharge_explanation:
    "Zusätzliche Batterieentnahme ist gegen feste solare Rückladetermine geprüft. Ein Suchlimit beweist keine Unmöglichkeit weiterer Versorgung.",
  recharge_status: "Prüfergebnis",
  recharge_validations: "Vollständige Prüfläufe",
  recharge_dc_energy: "Zertifizierte zusätzliche DCDC-Entnahme",
  recharge_ac_energy: "Zertifizierte zusätzliche AC-Entnahme",
  recharge_accepted: "Zusätzliche Versorgung zertifiziert",
  recharge_no_release: "Keine zusätzliche Freigabe nachgewiesen",
  recharge_search_limit: "Suchlimit erreicht",
  recharge_manual_feed_in: "Manuelle Einspeisung hat Vorrang",
  recharge_unproved:
    "Für diese Alternative liegt kein vollständiger Nachweis vor.",
  recharge_reason_anchor_energy:
    "Die gespeicherte Energie wäre bis zum festen solaren Rückladetermin nicht wiederhergestellt.",
  recharge_reason_end_energy:
    "Die gespeicherte Energie wäre am Prognoseende geringer.",
  recharge_reason_consumption_coverage:
    "Die Verbrauchsunsicherheit deckt den Rückladezeitraum nicht vollständig ab.",
  recharge_reason_consumption_reserve:
    "Der Vorhalt für Verbrauchsunsicherheit würde nicht ausreichen.",
  recharge_reason_dc_priority:
    "Zusätzliche AC-Versorgung würde geschützte DC-Versorgung verdrängen.",
  recharge_reason_search_limit:
    "Das Suchlimit wurde erreicht, bevor alle Alternativen geprüft waren.",
  recharge_reason_manual_feed_in:
    "Zusätzliche Batterieentnahme pausiert bei aktiver positiver manueller Einspeisung.",
});

Object.assign(STRINGS.en, {
  storage_unproved: "No further permitted booking was proved",
  recovery_target_unreachable:
    "Recovery remains incomplete in the evaluated plan",
  recovery_target_unmet: "Recovery target remains unmet in the evaluated plan",
  house_supply_priority:
    "House supply and its repayment commitments take priority",
  house_recharge: "House-battery repayment is protected",
});
Object.assign(STRINGS.de, {
  storage_unproved: "Keine weitere zulässige Buchung nachgewiesen",
  recovery_target_unreachable:
    "Recovery bleibt im geprüften Plan unvollständig",
  recovery_target_unmet: "Recovery-Ziel bleibt im geprüften Plan unerfüllt",
  house_supply_priority:
    "Hausversorgung und ihre Rückladezusagen haben Vorrang",
  house_recharge: "Rückladung der Hausbatterie bleibt geschützt",
});
Object.assign(STRINGS.en, {
  house_recharge_search_limit: "Recharge proof search limit reached",
});
Object.assign(STRINGS.de, {
  house_recharge_search_limit: "Suchlimit für den Rückladenachweis erreicht",
});
