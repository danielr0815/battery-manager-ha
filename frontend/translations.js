// retain switching edges across the full 96-hour horizon

export const STRINGS = {
  en: {
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
    card_tentative_wake:
      "Tentative plan: wake-up and valid SOC telemetry are required before execution.",
    live_ac_reason_measured_ac_demand:
      "Measured AC demand within the available reserve budget",
    live_ac_reason_soc_unavailable: "SOC telemetry unavailable or stale",
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
      "Valid comparison days per hour / required minimum. Weekdays, weekends and absence are learned separately.",
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
    card_tentative_wake:
      "Vorläufiger Plan: Aufwecken und gültige SOC-Telemetrie sind vor der Ausführung erforderlich.",
    live_ac_reason_measured_ac_demand:
      "Gemessener AC-Bedarf innerhalb des verfügbaren Reservebudgets",
    live_ac_reason_soc_unavailable: "SOC-Telemetrie fehlt oder ist veraltet",
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
      "Gültige Vergleichstage je Stunde / erforderliche Mindestanzahl. Werktage, Wochenenden und Abwesenheit werden getrennt gelernt.",
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
    "DC power supplies preserve battery energy that is not needed for forecast PV headroom.",
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
    "Forecast bands, otherwise uncalibrated PV factor",
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
    "Die DC-Netzteile erhalten Batterieenergie, deren Entnahme für den erwarteten PV-Speicherbedarf nicht nötig ist.",
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
    "Prognosebänder, sonst unkalibrierter PV-Faktor",
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
