#!/usr/bin/env python3
"""Standalone test script for Battery Manager core logic."""

import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

# Add the battery_manager module to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "battery_manager"))

from battery_manager import BatteryManagerSimulator
from const import DEFAULT_CONFIG


def test_basic_simulation():
    """Test basic simulation functionality."""
    print("=== Basic Simulation Test ===")
    
    # Create simulator with default config
    simulator = BatteryManagerSimulator(DEFAULT_CONFIG.copy())
    
    # Test inputs
    current_soc = 50.0
    daily_forecasts = [10.0, 12.0, 8.0]  # kWh for today, tomorrow, day after
    current_time = datetime(2025, 6, 8, 14, 0, 0)  # Example time
    
    # Run simulation
    results = simulator.run_simulation(current_soc, daily_forecasts, current_time)
    
    print(f"Current SOC: {current_soc}%")
    print(f"Daily forecasts: {daily_forecasts} kWh")
    print(f"Current time: {current_time}")
    print("\nResults:")
    print(f"  SOC Threshold: {results['soc_threshold_percent']:.1f}%")
    print(f"  Min SOC Forecast: {results['min_soc_forecast_percent']:.1f}%")
    print(f"  Max SOC Forecast: {results['max_soc_forecast_percent']:.1f}%")
    print(f"  Hours to Max SOC: {int(results['hours_until_max_soc'])}")
    print(f"  Inverter Enabled: {results['inverter_enabled']}")
    print(f"  Forecast Hours: {results['forecast_hours']}")
    print(f"  Grid Import: {results['grid_import_kwh']:.2f} kWh")
    print(f"  Grid Export: {results['grid_export_kwh']:.2f} kWh")
    print()


def test_low_soc_scenario():
    """Test scenario with low SOC."""
    print("=== Low SOC Scenario Test ===")
    
    simulator = BatteryManagerSimulator(DEFAULT_CONFIG.copy())
    
    # Low SOC test
    current_soc = 15.0
    daily_forecasts = [2.0, 3.0, 4.0]  # Low PV forecasts
    current_time = datetime(2025, 6, 8, 20, 0, 0)  # Evening
    
    results = simulator.run_simulation(current_soc, daily_forecasts, current_time)
    
    print(f"Current SOC: {current_soc}%")
    print(f"Daily forecasts: {daily_forecasts} kWh")
    print(f"Current time: {current_time}")
    print("\nResults:")
    print(f"  SOC Threshold: {results['soc_threshold_percent']:.1f}%")
    print(f"  Min SOC Forecast: {results['min_soc_forecast_percent']:.1f}%")
    print(f"  Max SOC Forecast: {results['max_soc_forecast_percent']:.1f}%")
    print(f"  Inverter Enabled: {results['inverter_enabled']}")
    print()


def test_high_pv_scenario():
    """Test scenario with high PV production."""
    print("=== High PV Production Scenario Test ===")
    
    simulator = BatteryManagerSimulator(DEFAULT_CONFIG.copy())
    
    # High PV test
    current_soc = 30.0
    daily_forecasts = [25.0, 20.0, 18.0]  # High PV forecasts
    current_time = datetime(2025, 6, 8, 8, 0, 0)  # Morning
    
    results = simulator.run_simulation(current_soc, daily_forecasts, current_time)
    
    print(f"Current SOC: {current_soc}%")
    print(f"Daily forecasts: {daily_forecasts} kWh")
    print(f"Current time: {current_time}")
    print("\nResults:")
    print(f"  SOC Threshold: {results['soc_threshold_percent']:.1f}%")
    print(f"  Min SOC Forecast: {results['min_soc_forecast_percent']:.1f}%")
    print(f"  Max SOC Forecast: {results['max_soc_forecast_percent']:.1f}%")
    print(f"  Inverter Enabled: {results['inverter_enabled']}")
    print()


def test_hourly_simulation():
    """Test hourly simulation."""
    print("=== Hourly Simulation Test ===")
    
    simulator = BatteryManagerSimulator(DEFAULT_CONFIG.copy())
    
    # Set initial SOC
    simulator.controller.battery.current_soc_percent = 50.0
    
    # Test one hour of operation
    pv_production_wh = 1500.0  # 1.5 kW for 1 hour
    ac_consumption_wh = 200.0   # 200W for 1 hour
    dc_consumption_wh = 100.0   # 100W for 1 hour
    
    print(f"Initial SOC: {simulator.controller.battery.current_soc_percent}%")
    print(f"PV Production: {pv_production_wh} Wh")
    print(f"AC Consumption: {ac_consumption_wh} Wh")
    print(f"DC Consumption: {dc_consumption_wh} Wh")
    
    flows = simulator.simulate_hour(pv_production_wh, ac_consumption_wh, dc_consumption_wh)
    
    print("\nEnergy Flows:")
    for key, value in flows.items():
        if isinstance(value, float) and value != 0:
            print(f"  {key}: {value:.1f}")
        elif isinstance(value, bool):
            print(f"  {key}: {value}")
    print()


def test_component_status():
    """Test component status reporting."""
    print("=== Component Status Test ===")
    
    simulator = BatteryManagerSimulator(DEFAULT_CONFIG.copy())
    simulator.controller.battery.current_soc_percent = 75.0
    
    status = simulator.get_component_status()
    
    print("Component Status:")
    for key, value in status.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.1f}")
        else:
            print(f"  {key}: {value}")
    print()


def main():
    """Run all tests."""
    print("Battery Manager Core Logic Test")
    print("=" * 50)
    print()
    
    try:
        test_basic_simulation()
        test_low_soc_scenario()
        test_high_pv_scenario()
        test_hourly_simulation()
        test_component_status()
        
        print("All tests completed successfully!")
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
