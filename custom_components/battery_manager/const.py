"""Constants for the Battery Manager integration."""

DOMAIN = "battery_manager"

INTEGRATION_NAME = "Battery Manager"
INTEGRATION_VERSION = "0.6.0"

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

# --- Surplus load subentry keys ---
SUBENTRY_TYPE_LOAD = "surplus_load"
CONF_LOAD_NAME = "name"
CONF_LOAD_POWER_W = "power_w"
CONF_LOAD_BATTERY_TOLERANCE = "battery_tolerance_percent"
CONF_LOAD_MIN_RUNTIME_MIN = "min_runtime_min"
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

# Power-deviation warning (operator requirement F-L7, 2026-07-05): while a
# load runs at the integration's request but its real draw deviates from the
# configured power by more than this percentage (per-load setting, 0 =
# disabled) for POWER_WARNING_DWELL_MIN sustained minutes, a per-load
# warning binary sensor turns on (full water tank, wrong nominal power,
# foreign consumer). Short defrost pauses stay below the dwell.
CONF_LOAD_POWER_WARNING_PCT = "power_warning_percent"
POWER_WARNING_DWELL_MIN = 30

# End-of-charge policies for the load's input plug (docs/LOAD_CONTROL.md §3)
INPUT_OFF_POLICY_AUTO = "auto"
INPUT_OFF_POLICY_ALWAYS = "always_off"
INPUT_OFF_POLICY_KEEP = "keep_on"
INPUT_OFF_POLICIES = [
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_KEEP,
]

# Persistent state (SOC cache, plug ownership) survives HA restarts
STORAGE_VERSION = 1

# --- Appliance subentry keys ---
SUBENTRY_TYPE_APPLIANCE = "appliance"
CONF_APPLIANCE_NAME = "name"
CONF_APPLIANCE_DETECTION_ENTITY = "detection_entity"
CONF_APPLIANCE_POWER_THRESHOLD_W = "power_threshold_w"
CONF_APPLIANCE_RUN_ENERGY_WH = "run_energy_wh"
CONF_APPLIANCE_RUN_DURATION_H = "run_duration_h"
CONF_APPLIANCE_OPPORTUNISTIC = "opportunistic_start"

# States of a detection entity considered "running" (non-numeric entities)
APPLIANCE_RUNNING_STATES = {"on", "run", "running", "washing", "active", "wash"}

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
    # Support paths
    CONF_SUPPORT_DC48_POWER_W: 60.0,
    CONF_SUPPORT_SWITCH_DELAY_S: 3,
}

DEFAULT_LOAD_CONFIG = {
    CONF_LOAD_POWER_W: 300.0,
    CONF_LOAD_BATTERY_TOLERANCE: 15.0,
    CONF_LOAD_MIN_RUNTIME_MIN: 30,
    CONF_LOAD_ENERGY_LIMITED: False,
    CONF_LOAD_CAPACITY_WH: 2000.0,
    CONF_LOAD_TARGET_SOC: 100.0,
    CONF_LOAD_INPUT_OFF_POLICY: INPUT_OFF_POLICY_AUTO,
    CONF_LOAD_IN_HOUSE: True,
    CONF_LOAD_POWER_WARNING_PCT: 50.0,
}

DEFAULT_APPLIANCE_CONFIG = {
    CONF_APPLIANCE_POWER_THRESHOLD_W: 10.0,
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
