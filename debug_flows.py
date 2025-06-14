#!/usr/bin/env python3
"""
Direct test to debug battery discharge calculations
"""

import sys
import os
sys.path.append('/home/jj/repo/battery-manager-ha/custom_components/battery_manager')

from battery_manager.energy_flow import EnergyFlowCalculator
from battery_manager.battery import Battery
from battery_manager.charger import Charger
from battery_manager.inverter import Inverter

# Setup components with test parameters
battery_config = {
    "capacity_wh": 5000.0,
    "charge_efficiency": 0.9,
    "discharge_efficiency": 0.5,
    "min_soc_percent": 5.0,
    "max_soc_percent": 95.0
}
battery = Battery(battery_config)
battery.current_soc_percent = 50.0  # 50% SOC

charger_config = {"efficiency": 0.9}
charger = Charger(charger_config)

inverter_config = {"efficiency": 0.95, "max_power_w": 2000}
inverter = Inverter(inverter_config)
inverter.set_enabled(True)

# Create calculator
calculator = EnergyFlowCalculator(battery, charger, inverter)

print("=== DIRECT ENERGY FLOW TEST ===")
print(f"Initial SOC: {battery.current_soc_percent}%")
print(f"Initial Energy: {battery.current_energy_wh:.1f} Wh")

# Test scenario: 125 Wh AC + 75 Wh DC consumption (no PV)
flows = calculator.calculate_energy_flow(
    pv_production_wh=0,
    ac_consumption_wh=125,
    dc_consumption_wh=75
)

print(f"\nFinal SOC: {battery.current_soc_percent}%")
print(f"Final Energy: {battery.current_energy_wh:.1f} Wh")

print(f"\nSOC Change: {50.0 - battery.current_soc_percent:.1f}%")

print("\n=== ENERGY FLOWS ===")
for key, value in flows.items():
    if value != 0:
        print(f"{key}: {value:.1f} Wh")

net_battery = flows.get("battery_charge_wh", 0.0) - flows.get("battery_discharge_wh", 0.0)
print(f"\nNet Battery Flow: {net_battery:.1f} Wh")

expected_soc_change = abs(net_battery) / 5000 * 100
print(f"Expected SOC change: {expected_soc_change:.1f}%")
print(f"Actual SOC change: {50.0 - battery.current_soc_percent:.1f}%")
