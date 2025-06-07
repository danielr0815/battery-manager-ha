"""Constants for the Battery Manager integration."""

DOMAIN = "battery_manager"

# Integration metadata
INTEGRATION_NAME = "Battery Manager"
INTEGRATION_VERSION = "1.0.0"

# Update intervals
UPDATE_INTERVAL_SECONDS = 600  # 10 minutes
DEBOUNCE_SECONDS = 5  # Debounce input changes

# Data validation
MAX_PV_FORECAST_AGE_HOURS = 24
MAX_SOC_AGE_HOURS = 1

# Default configuration values
DEFAULT_CONFIG = {
    # Battery defaults
    "battery_capacity_wh": 5000.0,
    "battery_min_soc_percent": 5.0,
    "battery_max_soc_percent": 95.0,
    "battery_charge_efficiency": 0.97,
    "battery_discharge_efficiency": 0.97,
    
    # PV system defaults
    "pv_max_power_w": 3200.0,
    "pv_morning_start_hour": 7,
    "pv_morning_end_hour": 13,
    "pv_afternoon_end_hour": 18,
    "pv_morning_ratio": 0.8,
    
    # AC consumer defaults
    "ac_base_load_w": 50.0,
    "ac_variable_load_w": 75.0,
    "ac_variable_start_hour": 6,
    "ac_variable_end_hour": 20,
    
    # DC consumer defaults
    "dc_base_load_w": 50.0,
    "dc_variable_load_w": 25.0,
    "dc_variable_start_hour": 6,
    "dc_variable_end_hour": 22,
    
    # Charger defaults
    "charger_max_power_w": 2300.0,
    "charger_efficiency": 0.92,
    "charger_standby_power_w": 10.0,
    
    # Inverter defaults
    "inverter_max_power_w": 2300.0,
    "inverter_efficiency": 0.95,
    "inverter_standby_power_w": 15.0,
    "inverter_min_soc_percent": 20.0,
    
    # Controller defaults
    "controller_target_soc_percent": 85.0,
}

# Entity configuration keys
CONF_SOC_ENTITY = "soc_entity"
CONF_PV_FORECAST_TODAY = "pv_forecast_today_entity"
CONF_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow_entity"
CONF_PV_FORECAST_DAY_AFTER = "pv_forecast_day_after_entity"

# Entity names and IDs
ENTITY_INVERTER_STATUS = "inverter_status"
ENTITY_SOC_THRESHOLD = "soc_threshold" 
ENTITY_MIN_SOC_FORECAST = "min_soc_forecast"
ENTITY_MAX_SOC_FORECAST = "max_soc_forecast"

# Attributes
ATTR_GRID_IMPORT_KWH = "grid_import_kwh"
ATTR_GRID_EXPORT_KWH = "grid_export_kwh"
ATTR_SIMULATION_END = "simulation_end"
ATTR_LAST_UPDATE = "last_update"
ATTR_DATA_VALIDITY = "data_validity"
