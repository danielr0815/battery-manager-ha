"""Constants for the Battery Manager integration."""

# De-minimis floor for the gate-stop final top-up (F-GATE-TOPUP R3): a
# constant, not a config key. Its consumer is the PURE planner core, which the
# standalone core test setup imports without this package — the authoritative
# definition therefore lives in core/optimize.py; re-exported here per R3's
# placement so HA-layer code has the canonical constants module to read.
# MERGE_TERMINAL_RAMP_WH (F-MERGE-HYSTERESIS) rides along for the same reason:
# it is consumed by the pure planner core, so it is defined next to its consumer
# in core/optimize.py and re-exported here as the canonical constants module.
from .core.optimize import (  # noqa: F401
    GATE_TOPUP_MIN_WH,
    MERGE_TERMINAL_RAMP_WH,
)

DOMAIN = "battery_manager"

INTEGRATION_NAME = "Battery Manager"
# The version is NOT hard-coded here (it drifted from manifest.json for
# releases): the device sw_version is read from the manifest at runtime —
# see BatteryManagerCoordinator.integration_version.

# Update behaviour
UPDATE_INTERVAL_SECONDS = 300
INITIAL_UPDATE_INTERVAL_SECONDS = 30
DEBOUNCE_SECONDS = 5
STARTUP_RETRY_ATTEMPTS = 5

# Data validity limits
MAX_PV_FORECAST_AGE_HOURS = 24
MAX_SOC_AGE_HOURS = 1
MAX_HISTORICAL_SOC_AGE_HOURS = 6
MAX_HISTORICAL_FORECAST_AGE_HOURS = 72
# A load's last-known SOC (cached while the device sleeps) is trusted for at
# most this long; beyond it the load plans as "empty" and self-heals on wake.
LOAD_SOC_CACHE_MAX_AGE_HOURS = 168  # 7 days

# --- Input entity config keys (base entry) ---
CONF_SOC_ENTITY = "soc_entity"
CONF_PV_FORECAST_TODAY = "pv_forecast_today_entity"
CONF_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow_entity"
CONF_PV_FORECAST_DAY_AFTER = "pv_forecast_day_after_entity"

# --- Learned consumption profiles (docs/CONSUMPTION_FORECAST.md) ---
# Measurement sources per path: a direct load sensor OR a generic counter
# balance (inflow/outflow entity lists, D-C1). All optional; learning is
# active per path as soon as a source is configured.
CONF_AC_LOAD_ENTITY = "ac_load_entity"
CONF_AC_BALANCE_IN = "ac_balance_in_entities"
CONF_AC_BALANCE_OUT = "ac_balance_out_entities"
CONF_DC_LOAD_ENTITY = "dc_load_entity"
CONF_DC_BALANCE_IN = "dc_balance_in_entities"
CONF_DC_BALANCE_OUT = "dc_balance_out_entities"
CONF_LEARNING_WINDOW_DAYS = "learning_window_days"
CONF_LEARNING_MAX_AGE_DAYS = "learning_max_age_days"
# Stufe 2 (D-C7/D-C8): recency weighting, dynamic-buffer clamps, holidays
CONF_PROFILE_HALF_LIFE_DAYS = "profile_half_life_days"
CONF_BUFFER_MIN_PERCENT = "buffer_min_percent"
CONF_BUFFER_MAX_PERCENT = "buffer_max_percent"
CONF_WORKDAY_ENTITY = "workday_entity"

# Hardcoded learning constants (documented in the spec, §4.1)
LEARNING_RUN_HOUR = 3  # local time of the nightly learning run
LEARNING_MIN_SAMPLES = 10  # per bin; absence bins need fewer
LEARNING_MIN_SAMPLES_ABSENCE = 5
LEARNING_RATE_LIMIT = 0.2  # max relative bin change per nightly run
LEARNING_CLAMP_AC_W = 3000.0  # plausibility clamp per hourly mean
LEARNING_CLAMP_DC_W = 1000.0
LEARNING_NEGATIVE_RESIDUAL_WH = 10.0  # below -x Wh counts as suspicious
LEARNING_VACATION_MIN_HOURS = 12.0  # vacation-mode share tagging a day
LEARNING_HOLIDAY_MIN_HOURS = 12.0  # workday-sensor "off" share tagging a day
LEARNING_BIAS_ALERT_DAYS = 14  # one-sided P50 bias for this long -> repair
LEARNING_BIAS_ALERT_SHARE = 0.15  # ... when |bias| exceeds this load share
VALIDATION_HISTORY_DAYS = 30  # kept watchdog entries per path
# Store ENVELOPE major version — pinned at 1 forever: bumping the Store
# major would make HA's default _async_migrate_func raise on old files and
# crash the whole entry setup after an update. Schema changes are handled
# solely via the INNER data["version"] field below (mismatch = discard +
# fresh backfill; the source data is always re-fetchable).
LEARNED_STORE_MAJOR = 1
# v2: bins carry {p50, p80} quantiles (Stufe 2).
LEARNED_STORE_VERSION = 2
LEARNED_STORE_KEY = "learned_profiles"  # f"{DOMAIN}.{key}.{entry_id}"

# --- Support paths (docs/ALGORITHM.md D-A9) ---
CONF_SUPPORT_DC48_SWITCH = "support_dc48_switch_entity"
CONF_SUPPORT_DC48_POWER_W = "support_dc48_power_w"
CONF_SUPPORT_DC24_SWITCH = "support_dc24_switch_entity"
# Optional power sensor of the 24 V grid PSU: makes the DC->AC load shift
# during PSU operation exactly correctable while learning consumption
# profiles (docs/CONSUMPTION_FORECAST.md D-C2 step 3). Without it, hours
# with a PSU-fed 24 V rail cannot be learned.
CONF_SUPPORT_DC24_POWER_ENTITY = "support_dc24_power_entity"
CONF_DCDC_SWITCH = "dcdc_switch_entity"
CONF_SUPPORT_SWITCH_DELAY_S = "support_switch_delay_s"
# Escalation thresholds (D-A9): four ABSOLUTE battery-SOC % that drive the
# two grid-support hysteresis loops, independent of the planning buffer.
# Defaults 10 / 11 / 5.5 / 10 reproduce the legacy hard-coded thresholds at
# the default battery config. See ControlParams for the ordering rule.
CONF_SUPPORT_DC24_ACTIVATE_SOC = "support_dc24_activate_soc"
CONF_SUPPORT_DC24_RECOVERY_SOC = "support_dc24_recovery_soc"
CONF_SUPPORT_DC48_ACTIVATE_SOC = "support_dc48_activate_soc"
CONF_SUPPORT_DC48_RECOVERY_SOC = "support_dc48_recovery_soc"

# --- F-N3 two-bus device parameters (docs/DC_TOPOLOGY.md, phase 2) ---
# All NEUTRAL by default (share 100 %, efficiencies 1.0, 0 A = uncapped),
# so the upgrade changes nothing until the operator enters real values.
# Battery voltage sensor for the (later) voltage-gated 48 V controller.
CONF_BATTERY_VOLTAGE_ENTITY = "battery_voltage_entity"
# Fixed native-48 V base load (W) carved off before the rail split.
CONF_NATIVE48_BASE_W = "native48_base_w"
# Fraction of the remaining DC load on the 24 V rail (rest = native 48 V bus).
CONF_DC24_SHARE_PERCENT = "dc24_share_percent"
# DC/DC converter (battery 48 V -> 24 V rail).
CONF_DCDC_OUTPUT_VOLTAGE_V = "dcdc_output_voltage_v"
CONF_DCDC_EFFICIENCY = "dcdc_efficiency"
CONF_DCDC_MAX_CURRENT_A = "dcdc_max_current_a"
# Grid-fed 24 V support PSU (feeds the rail when the DC/DC is off).
CONF_PSU24_OUTPUT_VOLTAGE_V = "psu24_output_voltage_v"
CONF_PSU24_EFFICIENCY = "psu24_efficiency"
CONF_PSU24_MAX_CURRENT_A = "psu24_max_current_a"
# 48 V support PSU (nameplate; voltage gate lives from phase 3).
CONF_PSU48_OUTPUT_VOLTAGE_V = "psu48_output_voltage_v"
CONF_PSU48_EFFICIENCY = "psu48_efficiency"
CONF_PSU48_MAX_CURRENT_A = "psu48_max_current_a"
# 48 V PSU voltage gate as an SOC proxy (phase 3): the PSU only delivers
# while the battery SOC is below this value. 100 = always open (neutral).
# Calibrate from the observed voltage-crossing bracket (diagnostic).
CONF_GATE_SOC_PERCENT = "gate_soc_percent"
# Series cell count (informational: derives V/cell for the gate hint).
CONF_BATTERY_CELLS_SERIES = "battery_cells_series"

# --- R2 voltage controller for the manual 48 V mode (docs/DC_TOPOLOGY.md §6) ---
# While the 48 V PSU is in manual mode AND a battery-voltage sensor is set,
# the controller switches the PSU on below `on_voltage` and off above
# `off_voltage` (asymmetric hysteresis + dwell). Log-only by default: it
# logs its decisions without actuating until the operator arms it after a
# shakedown. Without a voltage sensor the manual mode stays F-N2 hands-off.
CONF_PSU48_ON_VOLTAGE_V = "psu48_on_voltage_v"
CONF_PSU48_OFF_VOLTAGE_V = "psu48_off_voltage_v"
CONF_PSU48_CTRL_LOG_ONLY = "psu48_controller_log_only"
# Dwell/plausibility constants (not per-install configurable in v1).
DC48_CTRL_DWELL_ON_S = 60
DC48_CTRL_DWELL_OFF_S = 300
DC48_CTRL_FAILSAFE_MIN = 10  # invalid voltage this long -> fail-safe PSU on
DC48_CTRL_VOLTAGE_MIN = 40.0  # plausibility window for the sensor
DC48_CTRL_VOLTAGE_MAX = 60.0

# --- Surplus load subentry keys ---
SUBENTRY_TYPE_LOAD = "surplus_load"
CONF_LOAD_NAME = "name"
# Explicit per-load priority (int >= 1, 1 = highest; F-LOAD-PRIORITY R1).
# Deliberately NOT in DEFAULT_LOAD_CONFIG: the effective order is resolved
# centrally (ordered_load_subentries, R3) with the insertion position as the
# legacy fallback, so a per-load default would shadow that fallback.
CONF_LOAD_PRIORITY = "priority"
CONF_LOAD_POWER_W = "power_w"
CONF_LOAD_BATTERY_TOLERANCE = "battery_tolerance_percent"
CONF_LOAD_MIN_RUNTIME_MIN = "min_runtime_min"
CONF_LOAD_MIN_OFF_MIN = "min_off_min"
CONF_LOAD_ENERGY_LIMITED = "energy_limited"
CONF_LOAD_CAPACITY_WH = "capacity_wh"
CONF_LOAD_TARGET_SOC = "target_soc_percent"
CONF_LOAD_SOC_ENTITY = "soc_entity"
CONF_LOAD_POWER_ENTITY = "power_entity"
CONF_LOAD_AVAILABILITY_ENTITY = "availability_entity"
CONF_LOAD_CONTROL_SWITCH = "control_switch_entity"
CONF_LOAD_CHARGE_ENABLE = "charge_enable_entity"
CONF_LOAD_INPUT_OFF_POLICY = "input_off_policy"
# Load is included in the consumption measurement point (§2.3): True means
# the learner subtracts it from the measured load; False (load fed outside
# the measured node, e.g. via a feed-in setpoint) means no subtraction.
CONF_LOAD_IN_HOUSE = "in_house_measurement"

# Power-feedback samples below this fraction of the load's nominal power
# are standby/idle draw (e.g. a 400 W dehumidifier reading ~20 W) and must
# not replace the planning power via the EMA: the planner would book hours
# at standby watts while the device really pulls its nominal power
# (2026-07-05 live incident: 11 h × 22 Wh planned vs. ~4.4 kWh real).
STANDBY_FRACTION = 0.25

# Stale-SOC guard (F-EXECUTOR-GUARDS G2): a load SOC that stays EXACTLY
# unchanged for this many minutes while the device charges above the standby
# bar is latched as stale (the fossibot integration serves cached values with
# FRESH timestamps, so age checks cannot catch it; its cadence is ~1 min, so
# 12 min of frozen SOC while drawing ~500 W is unambiguous). Deliberately a
# constant, not a config key.
STALE_LOAD_SOC_MIN = 12

# Power-deviation warning (operator requirement F-L7, 2026-07-05): while a
# load runs at the integration's request but its real draw deviates from the
# configured power by more than this percentage (per-load setting, 0 =
# disabled, the default) for the per-load dwell in sustained minutes, a
# per-load warning binary sensor turns on (full water tank, wrong nominal
# power, foreign consumer). Short defrost pauses stay below the dwell. The
# warning latches once on and clears only when the load runs at its configured
# power again (coordinator._update_power_warnings).
CONF_LOAD_POWER_WARNING_PCT = "power_warning_percent"
CONF_LOAD_POWER_WARNING_DWELL_MIN = "power_warning_dwell_min"

# End-of-charge policies for the load's input plug (docs/LOAD_CONTROL.md §3)
INPUT_OFF_POLICY_AUTO = "auto"
INPUT_OFF_POLICY_ALWAYS = "always_off"
INPUT_OFF_POLICY_KEEP = "keep_on"
INPUT_OFF_POLICIES = [
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_KEEP,
]

# Power-warning push notifications (operator wish 2026-07-12): a single global
# list of `notify` service names (e.g. "mobile_app_pixel") the coordinator
# pushes to when ANY load's power warning trips (and, unless silenced, when it
# clears). Empty list = no push. Stored in the integration options.
CONF_WARNING_NOTIFY_TARGETS = "power_warning_notify_targets"
CONF_WARNING_NOTIFY_ON_RESOLVE = "power_warning_notify_on_resolve"

# Persistent state (SOC cache, plug ownership) survives HA restarts
STORAGE_VERSION = 1

# --- Appliance subentry keys ---
SUBENTRY_TYPE_APPLIANCE = "appliance"
CONF_APPLIANCE_NAME = "name"
CONF_APPLIANCE_DETECTION_ENTITY = "detection_entity"
CONF_APPLIANCE_POWER_THRESHOLD_W = "power_threshold_w"
CONF_APPLIANCE_OFF_THRESHOLD_W = "off_threshold_w"
CONF_APPLIANCE_RUN_ENERGY_WH = "run_energy_wh"
CONF_APPLIANCE_RUN_DURATION_H = "run_duration_h"
CONF_APPLIANCE_OPPORTUNISTIC = "opportunistic_start"

# States of a detection entity considered "running" (non-numeric entities)
APPLIANCE_RUNNING_STATES = {"on", "run", "running", "washing", "active", "wash"}

# --- F-PREDRAIN two-buffer pre-drain (docs/F-PREDRAIN.md §3, WP3) ---
# System-level planner options. The core dataclass defaults are NEUTRAL (ratio
# 0.0, confidences 1.0, gate off) so the goldens stay frozen; these RECOMMENDED
# live values are the coordinator/config-flow fallbacks instead (via
# DEFAULT_CONFIG below), so an un-reconfigured install activates the feature
# after the update (F-PREDRAIN §3.2 note).
CONF_PV_FORECAST_MODE = "pv_forecast_mode"
# "import_trade_ratio" was retired by F-STRICT-SURPLUS R1 (2026-07-19): loads
# may never buy grid import, the planner uses a fixed artifact slack instead
# (core/optimize.py IMPORT_ARTIFACT_SLACK_WH). A stored key is ignored.
CONF_PREDRAIN_PV_CONFIDENCE = "predrain_pv_confidence"
CONF_UPPER_PV_RESERVE = "upper_pv_reserve"
CONF_STRONG_PV_CUTOFF_W = "strong_pv_cutoff_w"
# Optional site override with NO default (unset/empty = derive purely from the
# forecast shape), so it is deliberately absent from DEFAULT_CONFIG.
CONF_PV_WINDOW_END_HOUR = "pv_window_end_hour"

# PV forecast ingestion mode (docs/F-PREDRAIN.md F1): "auto" uses the hourly
# wh_period attributes when present and falls back to the two-window synthesis,
# "hourly" is the same today (hourly when present), "daily" ignores the
# attributes entirely (golden-anchor behaviour). Replaces WP1's internal
# PV_FORECAST_MODE_DEFAULT constant.
PV_FORECAST_MODE_AUTO = "auto"
PV_FORECAST_MODE_HOURLY = "hourly"
PV_FORECAST_MODE_DAILY = "daily"
PV_FORECAST_MODES = [
    PV_FORECAST_MODE_AUTO,
    PV_FORECAST_MODE_HOURLY,
    PV_FORECAST_MODE_DAILY,
]

# Recommended live fallbacks (NOT the neutral core dataclass defaults). Used as
# the coordinator absent-key fallback AND the config-flow form default so a
# no-change reconfigure keeps the pre-drain behaviour unchanged.
PREDRAIN_PV_CONFIDENCE_DEFAULT = 0.5
UPPER_PV_RESERVE_DEFAULT = 1.2
STRONG_PV_CUTOFF_W_DEFAULT = 200.0

# --- Default configuration (base entry) ---
DEFAULT_CONFIG = {
    # Battery
    "battery_capacity_wh": 5000.0,
    "battery_min_soc_percent": 5.0,
    "battery_max_soc_percent": 95.0,
    "battery_charge_efficiency": 0.97,
    "battery_discharge_efficiency": 0.97,
    # PV system
    "pv_max_power_w": 3200.0,
    "pv_morning_start_hour": 7,
    "pv_morning_end_hour": 13,
    "pv_afternoon_end_hour": 18,
    "pv_morning_ratio": 0.8,
    # AC consumer profile
    "ac_base_load_w": 50.0,
    "ac_variable_load_w": 75.0,
    "ac_variable_start_hour": 6,
    "ac_variable_end_hour": 20,
    # DC consumer profile
    "dc_base_load_w": 50.0,
    "dc_variable_load_w": 25.0,
    "dc_variable_start_hour": 6,
    "dc_variable_end_hour": 22,
    # Charger
    "charger_max_power_w": 2300.0,
    "charger_efficiency": 0.92,
    "charger_standby_power_w": 10.0,
    # Inverter
    "inverter_max_power_w": 2300.0,
    "inverter_efficiency": 0.95,
    "inverter_standby_power_w": 15.0,
    "inverter_min_soc_percent": 20.0,
    # Learned consumption profiles (docs/CONSUMPTION_FORECAST.md)
    # Stufe 2: recency weighting replaces the hard window edge, so the
    # window widens to 120 d (old days fade via the half-life instead).
    CONF_LEARNING_WINDOW_DAYS: 120,
    CONF_LEARNING_MAX_AGE_DAYS: 14,
    CONF_PROFILE_HALF_LIFE_DAYS: 30,
    CONF_BUFFER_MIN_PERCENT: 3.0,
    CONF_BUFFER_MAX_PERCENT: 15.0,
    # Planner tuning (docs/ALGORITHM.md D-A1..D-A4)
    "soc_buffer_percent": 5.0,
    "hysteresis_percent": 1.0,
    "threshold_inertia_percent": 2.0,
    "min_switch_interval_s": 60,
    # F-PREDRAIN pre-drain (docs/F-PREDRAIN.md §3) — RECOMMENDED live values, so
    # an un-reconfigured install runs with the feature active. pv_window_end_hour
    # is intentionally omitted (no default = unset/derive from the forecast).
    CONF_PV_FORECAST_MODE: PV_FORECAST_MODE_AUTO,
    CONF_PREDRAIN_PV_CONFIDENCE: PREDRAIN_PV_CONFIDENCE_DEFAULT,
    CONF_UPPER_PV_RESERVE: UPPER_PV_RESERVE_DEFAULT,
    CONF_STRONG_PV_CUTOFF_W: STRONG_PV_CUTOFF_W_DEFAULT,
    # Support paths
    CONF_SUPPORT_DC48_POWER_W: 60.0,
    CONF_SUPPORT_SWITCH_DELAY_S: 3,
    # Escalation thresholds (absolute SOC %) — neutral defaults = legacy.
    CONF_SUPPORT_DC24_ACTIVATE_SOC: 10.0,
    CONF_SUPPORT_DC24_RECOVERY_SOC: 11.0,
    CONF_SUPPORT_DC48_ACTIVATE_SOC: 5.5,
    CONF_SUPPORT_DC48_RECOVERY_SOC: 10.0,
    # F-N3 two-bus device parameters — neutral defaults (docs/DC_TOPOLOGY.md).
    CONF_NATIVE48_BASE_W: 0.0,
    CONF_DC24_SHARE_PERCENT: 100.0,
    CONF_DCDC_OUTPUT_VOLTAGE_V: 24.0,
    CONF_DCDC_EFFICIENCY: 1.0,
    CONF_DCDC_MAX_CURRENT_A: 0.0,  # 0 = uncapped
    CONF_PSU24_OUTPUT_VOLTAGE_V: 24.0,
    CONF_PSU24_EFFICIENCY: 1.0,
    CONF_PSU24_MAX_CURRENT_A: 0.0,
    CONF_PSU48_OUTPUT_VOLTAGE_V: 49.56,
    CONF_PSU48_EFFICIENCY: 1.0,
    CONF_PSU48_MAX_CURRENT_A: 0.0,
    CONF_GATE_SOC_PERCENT: 100.0,  # 100 = gate always open (neutral)
    CONF_BATTERY_CELLS_SERIES: 16,
    CONF_PSU48_ON_VOLTAGE_V: 49.56,
    CONF_PSU48_OFF_VOLTAGE_V: 49.8,
    CONF_PSU48_CTRL_LOG_ONLY: True,  # arm only after a shakedown
    # Power-warning push notifications (operator wish 2026-07-12)
    CONF_WARNING_NOTIFY_TARGETS: [],
    CONF_WARNING_NOTIFY_ON_RESOLVE: True,
}

# A load counts as "really running" for the runtime counter when its measured
# power exceeds this (W) — above typical smart-plug/appliance standby, below any
# real draw. Falls back to the BM charging state when no power sensor is set.
LOAD_RUNTIME_MIN_W = 5.0
# Max time a single runtime tick may add (s). Above the 300 s update interval so
# a normal cycle is never clipped, but bounds any inflation from a stalled loop
# or a clock jump within a session — a longer gap adds nothing. (A restart gap is
# handled separately by not persisting the tick cursor.)
LOAD_RUNTIME_TICK_MAX_S = 900.0

DEFAULT_LOAD_CONFIG = {
    CONF_LOAD_POWER_W: 300.0,
    CONF_LOAD_BATTERY_TOLERANCE: 15.0,
    CONF_LOAD_MIN_RUNTIME_MIN: 30,
    CONF_LOAD_MIN_OFF_MIN: 30,
    CONF_LOAD_ENERGY_LIMITED: False,
    CONF_LOAD_CAPACITY_WH: 2000.0,
    CONF_LOAD_TARGET_SOC: 100.0,
    CONF_LOAD_INPUT_OFF_POLICY: INPUT_OFF_POLICY_AUTO,
    CONF_LOAD_IN_HOUSE: True,
    # Off by default (0 %); the operator opts a load in per device. Existing
    # loads keep their stored value, so a load already warning stays on.
    CONF_LOAD_POWER_WARNING_PCT: 0.0,
    CONF_LOAD_POWER_WARNING_DWELL_MIN: 15,
}

DEFAULT_APPLIANCE_CONFIG = {
    CONF_APPLIANCE_POWER_THRESHOLD_W: 10.0,
    CONF_APPLIANCE_OFF_THRESHOLD_W: 5.0,
    CONF_APPLIANCE_RUN_ENERGY_WH: 1000.0,
    CONF_APPLIANCE_RUN_DURATION_H: 2.5,
    CONF_APPLIANCE_OPPORTUNISTIC: False,
}

# --- Entity keys ---
ENTITY_INVERTER_STATUS = "inverter_status"
ENTITY_SOC_THRESHOLD = "soc_threshold"
ENTITY_MIN_SOC_FORECAST = "min_soc_forecast"
ENTITY_MAX_SOC_FORECAST = "max_soc_forecast"
ENTITY_HOURS_TO_MAX_SOC = "hours_to_max_soc"
ENTITY_GRID_IMPORT_FORECAST = "grid_import_forecast"
ENTITY_LOST_SURPLUS = "lost_surplus"
ENTITY_SOC_FORECAST_CURVE = "soc_forecast"
ENTITY_SUPPORT_DC24 = "support_dc24"
ENTITY_SUPPORT_DC48 = "support_dc48"
ENTITY_SUPPORT_DC24_MODE = "support_dc24_mode"
ENTITY_SUPPORT_DC48_MODE = "support_dc48_mode"
# Operator manual-override switches per support PSU (F-N2/R3).
ENTITY_SUPPORT_DC24_MANUAL = "support_dc24_manual"
ENTITY_SUPPORT_DC48_MANUAL = "support_dc48_manual"
SUPPORT_MODE_AUTO = "auto"
SUPPORT_MODE_MANUAL = "manual"
ENTITY_VACATION_MODE = "vacation_mode"

# --- Attributes ---
ATTR_GRID_IMPORT_KWH = "grid_import_kwh"
ATTR_GRID_EXPORT_KWH = "grid_export_kwh"
ATTR_LAST_UPDATE = "last_update"
ATTR_DATA_VALIDITY = "data_validity"
ATTR_PLANNED_HOURS = "planned_hours"
ATTR_PLANNED_ENERGY_KWH = "planned_energy_kwh"
ATTR_THRESHOLD = "soc_threshold_percent"
ATTR_EXPECTED_POWER_W = "expected_power_w"
ATTR_MEASURED_POWER_W = "measured_power_w"
ATTR_DEVIATING_SINCE = "deviating_since"

# --- Services ---
SERVICE_EXPORT_HOURLY_DETAILS = "export_hourly_details"
SERVICE_EXPORT_LEARNED_PROFILES = "export_learned_profiles"
CONF_AS_TABLE = "as_table"
