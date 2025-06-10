#!/usr/bin/env python3
"""Test script to verify the new discharge sensor value."""

import sys
from pathlib import Path

# Add the battery_manager module to the path
sys.path.insert(0, str(Path(__file__).parent / "custom_components" / "battery_manager"))

from battery_manager.simulator import BatteryManagerSimulator
from datetime import datetime


def test_discharge_calculation():
    """Test the new discharge percentage calculation."""
    print("🔋 Testing New Discharge Sensor")
    print("=" * 50)
    
    # Create simulator with test configuration
    config = {
        'controller_target_soc_percent': 85.0,
        'battery_capacity_wh': 10000.0,
        'battery_min_soc_percent': 10.0,
        'battery_max_soc_percent': 90.0,
        'battery_charge_efficiency': 0.95,
        'battery_discharge_efficiency': 0.95,
        'inverter_min_soc_percent': 20.0,
    }
    
    simulator = BatteryManagerSimulator(config)
    
    # Test scenarios
    test_cases = [
        {"soc": 30.0, "forecasts": [5.0, 5.0, 5.0], "description": "Low PV, moderate SOC"},
        {"soc": 50.0, "forecasts": [15.0, 12.0, 10.0], "description": "Medium PV, medium SOC"},
        {"soc": 70.0, "forecasts": [25.0, 20.0, 18.0], "description": "High PV, high SOC"},
        {"soc": 20.0, "forecasts": [8.0, 10.0, 6.0], "description": "Low SOC at threshold"},
    ]
    
    current_time = datetime(2025, 6, 10, 10, 0, 0)
    
    print(f"📊 Test Configuration:")
    print(f"   Battery Capacity: {config['battery_capacity_wh']} Wh")
    print(f"   Target SOC: {config['controller_target_soc_percent']}%")
    print(f"   Inverter Min SOC: {config['inverter_min_soc_percent']}%")
    print(f"   Test Time: {current_time}")
    print()
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"🧪 Test Case {i}: {test_case['description']}")
        
        results = simulator.run_simulation(
            test_case["soc"], 
            test_case["forecasts"], 
            current_time
        )
        
        # Extract key values
        soc_threshold = results["soc_threshold_percent"]
        discharge_percent = results["discharge_forecast_percent"]
        min_soc_forecast = results["min_soc_forecast_percent"]
        max_soc_forecast = results["max_soc_forecast_percent"]
        inverter_enabled = results["inverter_enabled"]
        
        print(f"   Input:")
        print(f"     SOC: {test_case['soc']:.1f}%")
        print(f"     PV Forecasts: {test_case['forecasts']} kWh")
        print()
        print(f"   Results:")
        print(f"     SOC Threshold: {soc_threshold:.1f}%")
        print(f"     Discharge Value: {discharge_percent:.1f}% ← NEW SENSOR")
        print(f"     Min SOC Forecast: {min_soc_forecast:.1f}%")
        print(f"     Max SOC Forecast: {max_soc_forecast:.1f}%")
        print(f"     Inverter Enabled: {'✅ Yes' if inverter_enabled else '❌ No'}")
        print()
        print(f"   Analysis:")
        
        # Simple validation of discharge value
        if discharge_percent is not None:
            if -50 <= discharge_percent <= 100:  # Reasonable range
                print(f"     ✅ Discharge value is within reasonable range")
            else:
                print(f"     ⚠️  Discharge value seems unusual: {discharge_percent:.1f}%")
        else:
            print(f"     ❌ Discharge value is None")
        
        print("-" * 50)
    
    print("\n🎯 Summary:")
    print("   The new 'discharge_forecast_percent' sensor represents:")
    print("   forecast_safety_margin - forced_charger_soc_percent")
    print("   This indicates how much energy margin is available for discharge")
    print("   while maintaining battery protection.")


if __name__ == "__main__":
    test_discharge_calculation()
