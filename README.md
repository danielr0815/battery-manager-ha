# Battery Manager Home Assistant Integration

A comprehensive Home Assistant custom integration for battery energy storage optimization using photovoltaic (PV) forecasts and intelligent control algorithms.

## 🎯 Overview

The Battery Manager integration simulates and optimizes battery storage systems with the following components:
- **Battery**: SOC management with charge/discharge efficiency modeling
- **PV System**: Solar power generation with hourly distribution from daily forecasts
- **AC/DC Consumers**: Time-based load profiles for household consumption
- **Charger/Inverter**: AC↔DC conversion with efficiency losses
- **Maximum-Based Controller**: Optimal inverter operation threshold calculation

## 📊 Features

### Core Functionality
- **Intelligent SOC Threshold Calculation**: Determines optimal battery discharge threshold based on current SOC and PV forecasts
- **Energy Flow Simulation**: Models complex energy flows between grid, PV, battery, and loads
- **Real-time Optimization**: Updates calculations every 10 minutes or when input data changes
- **Robust Error Handling**: Comprehensive validation and graceful degradation
- **Standalone Testing**: Independent testing capability without Home Assistant

### Home Assistant Integration
- **4 Sensor Entities**:
  - `binary_sensor.battery_manager_inverter_status` - Inverter enable/disable state
  - `sensor.battery_manager_soc_threshold` - Calculated SOC threshold (%)
  - `sensor.battery_manager_min_soc_forecast` - Minimum forecasted SOC (%)
  - `sensor.battery_manager_max_soc_forecast` - Maximum forecasted SOC (%)
- **GUI Configuration**: Complete configuration via Home Assistant UI
- **Automatic Updates**: Entity monitoring with debounced updates
- **Device Grouping**: All entities grouped under single device

## 🔧 Installation

### Method 1: Manual Installation
1. Copy the `battery_manager` folder to your `custom_components` directory:
   ```
   custom_components/
   └── battery_manager/
       ├── __init__.py
       ├── config_flow.py
       ├── const.py
       ├── coordinator.py
       ├── manifest.json
       ├── sensor.py
       ├── battery_manager/
       │   ├── __init__.py
       │   ├── battery.py
       │   ├── charger.py
       │   ├── consumers.py
       │   ├── controller.py
       │   ├── energy_flow.py
       │   ├── inverter.py
       │   ├── pv_system.py
       │   └── simulator.py
       └── translations/
           └── en.json
   ```

2. Restart Home Assistant

3. Go to **Configuration** → **Integrations** → **Add Integration**

4. Search for "Battery Manager" and follow the configuration steps

### Method 2: HACS (Not yet available)
This integration is not yet available in HACS but may be added in the future.

## ⚙️ Configuration

### Required Input Entities
- **SOC Entity**: Sensor providing current battery State of Charge (0-100%)
- **PV Forecast Entities**: Three sensors providing daily PV energy forecasts in kWh:
  - Today's forecast
  - Tomorrow's forecast  
  - Day after tomorrow's forecast

### System Parameters

#### Battery Configuration
- **Capacity**: Total battery capacity in Wh (default: 10000 Wh)
- **SOC Limits**: Minimum and maximum SOC percentages (default: 10%-90%)
- **Efficiencies**: Charge and discharge efficiency factors (default: 95%)

#### PV System Configuration
- **Peak Power**: Maximum PV system power in W (default: 8000 W)

#### Load Configuration
- **AC Base Load**: Continuous AC consumption in W (default: 300 W)
- **DC Base Load**: Continuous DC consumption in W (default: 50 W)

#### Component Efficiencies
- **Charger Efficiency**: AC→DC conversion efficiency (default: 93%)
- **Inverter Efficiency**: DC→AC conversion efficiency (default: 93%)

#### Controller Settings
- **Maximum Threshold**: Upper limit for SOC threshold in % (default: 80%)

## 🧮 Algorithm Details

### Maximum-Based Controller
The system uses a Maximum-Based Controller algorithm that:

1. **Forecasts Energy Flows**: Simulates hourly energy production, consumption, and battery interactions
2. **Calculates SOC Range**: Determines minimum and maximum SOC over the forecast period
3. **Optimizes Threshold**: Sets inverter activation threshold to maximize self-consumption while maintaining battery reserves
4. **Validates Results**: Ensures thresholds are within acceptable bounds

### PV Production Modeling
- **Hourly Distribution**: Converts daily forecasts to hourly production using realistic solar curves
- **Seasonal Adjustment**: Accounts for daylight hours and sun angle variations
- **Peak Power Limiting**: Respects inverter and panel capacity limits

### Energy Flow Priorities
1. **PV to DC Load**: Direct DC consumption has highest priority
2. **PV to AC Load**: AC consumption via inverter
3. **PV to Battery**: Charging when excess production available
4. **PV to Grid**: Export when battery full and loads satisfied
5. **Battery to Loads**: Discharge when SOC above threshold
6. **Grid to Loads**: Import when insufficient local generation

## 📈 Usage Examples

### Basic Setup
```yaml
# Example configuration.yaml entries
sensor:
  - platform: template
    sensors:
      battery_soc:
        friendly_name: "Battery SOC"
        value_template: "{{ states('sensor.battery_state_of_charge') }}"
        unit_of_measurement: "%"
      
      pv_forecast_today:
        friendly_name: "PV Forecast Today"
        value_template: "{{ states('sensor.solcast_forecast_today') }}"
        unit_of_measurement: "kWh"
```

### CLI Testing
```bash
# Run basic test
python test_battery_manager_cli.py --soc 60 --forecasts 25.0,30.0,20.0

# Run with custom battery
python test_battery_manager_cli.py --battery-capacity 20000 --battery-soc-min 15

# Run all scenarios
python test_battery_manager_cli.py --run-scenarios

# Verbose output with JSON export
python test_battery_manager_cli.py --verbose --output-json results.json
```

## 🧪 Testing

### Standalone Testing
The system includes comprehensive standalone testing capabilities:

#### Basic Test Script
```bash
cd standalone_test
python test_battery_manager.py
```

#### CLI Test Script
```bash
cd standalone_test
python test_battery_manager_cli.py --help
```

#### Error Handling Tests
```bash
cd standalone_test
python test_error_handling.py
```

### Test Scenarios
The system includes predefined test scenarios:
- **Basic Operation**: Normal conditions with moderate SOC and forecasts
- **Low SOC Critical**: Emergency charging scenarios
- **High PV Production**: Excellent weather conditions
- **Edge Cases**: Boundary conditions and extreme values
- **Configuration Validation**: Invalid parameter handling

## 🔍 Monitoring & Debugging

### Entity States
Monitor the integration through Home Assistant:
- Check entity states in **Developer Tools** → **States**
- View entity history in **History** panel
- Use **Logbook** to track state changes

### Debug Information
Enable debug logging in `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.battery_manager: debug
```

### Configuration Validation
The system validates all configuration parameters and provides clear error messages for:
- Invalid capacity values
- Efficiency values outside 0-1 range
- SOC limits outside 0-100% range
- Negative power values
- Inconsistent min/max SOC settings

## 🔧 Troubleshooting

### Common Issues

#### Entities Not Updating
- Check that input entities exist and have valid states
- Verify entity IDs in integration configuration
- Check logs for coordinator errors

#### Invalid SOC Values
- Ensure SOC entity provides values between 0-100
- Check for "unknown" or "unavailable" states
- Verify entity state format (numeric)

#### Forecast Data Issues
- Confirm forecast entities provide numeric values in kWh
- Check for negative forecast values (automatically clamped to 0)
- Verify forecast data freshness (max age: 24 hours)

#### Configuration Errors
- Review configuration parameters for valid ranges
- Check efficiency values are between 0-1
- Verify capacity and power values are positive

### Error Recovery
The system implements robust error recovery:
- **Graceful Degradation**: Uses last valid data when entities unavailable
- **Data Validation**: Clamps invalid values to acceptable ranges  
- **Age Checking**: Warns when data becomes stale
- **Automatic Retry**: Attempts recovery on next update cycle

## 📚 Technical Architecture

### Component Structure
```
Battery Manager
├── BatteryManagerSimulator (Main orchestrator)
├── MaximumBasedController (Algorithm implementation)
├── EnergyFlowCalculator (Energy balance calculations)
├── Battery (SOC and energy management)
├── PVSystem (Solar production modeling)
├── Consumers (AC/DC load modeling)
├── Charger (AC→DC conversion)
└── Inverter (DC→AC conversion with SOC control)
```

### Home Assistant Integration
```
Integration
├── ConfigFlow (GUI configuration)
├── DataUpdateCoordinator (Entity monitoring & updates)
├── BinarySensor (Inverter status)
└── Sensors (SOC values)
```

### Data Flow
1. **Input Collection**: Coordinator reads SOC and forecast entities
2. **Validation**: Input data validated and cleaned
3. **Simulation**: Controller runs energy flow simulation
4. **Optimization**: SOC threshold calculated using Maximum-Based algorithm
5. **Entity Updates**: Results published to Home Assistant entities
6. **Monitoring**: Continuous monitoring for entity changes

## 🤝 Contributing

### Development Setup
1. Clone the repository
2. Install dependencies (none required - pure Python)
3. Run tests to verify functionality
4. Make changes and test thoroughly

### Code Quality
- Follow Python PEP 8 style guidelines
- Add comprehensive docstrings
- Include unit tests for new features
- Validate with error handling tests

### Testing Guidelines
- Test all configuration combinations
- Verify edge cases and error conditions
- Ensure backward compatibility
- Test integration with Home Assistant

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🏷️ Version History

### v1.0.0 (Current)
- Initial release
- Complete battery management system
- Home Assistant integration
- Comprehensive testing suite
- CLI testing tools
- Full documentation

## 🙏 Acknowledgments

- Home Assistant community for integration patterns
- Contributors to solar forecasting APIs
- Battery management research community
- Open source energy management projects
