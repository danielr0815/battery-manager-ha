"""Energy Flow Calculator for the Battery Manager system."""

from typing import Dict, Any, Tuple
from .battery import Battery
from .charger import Charger
from .inverter import Inverter


class EnergyFlowCalculator:
    """Calculates energy flows between different system components."""
    
    def __init__(self, battery: Battery, charger: Charger, inverter: Inverter):
        """Initialize the energy flow calculator.
        
        Args:
            battery: Battery instance
            charger: Charger instance
            inverter: Inverter instance
        """
        self.battery = battery
        self.charger = charger
        self.inverter = inverter
        
        # Track energy flows for reporting
        self.last_flows = {
            "grid_import_wh": 0.0,
            "grid_export_wh": 0.0,
            "battery_charge_wh": 0.0,
            "battery_discharge_wh": 0.0,
            "charger_ac_to_dc_wh": 0.0,
            "charger_dc_from_ac_wh": 0.0,
            "inverter_dc_to_ac_wh": 0.0,
        }
    
    def calculate_energy_flow(
        self, 
        pv_production_wh: float,
        ac_consumption_wh: float,
        dc_consumption_wh: float
    ) -> Dict[str, float]:
        """Calculate energy flows for one hour of operation.
        
        Args:
            pv_production_wh: PV production in Wh
            ac_consumption_wh: AC consumption in Wh
            dc_consumption_wh: DC consumption in Wh
            
        Returns:
            Dictionary containing all energy flows and final states
        """
        # Reset flow tracking
        flows = {
            "grid_import_wh": 0.0,
            "grid_export_wh": 0.0,
            "battery_charge_wh": 0.0,
            "battery_discharge_wh": 0.0,
            "charger_ac_to_dc_wh": 0.0,
            "charger_dc_from_ac_wh": 0.0,
            "inverter_dc_to_ac_wh": 0.0,
            "charger_standby_wh": 0.0,
            "inverter_standby_wh": 0.0,
        }
        
        # Step 1: Calculate AC balance (PV production vs AC consumption)
        ac_balance_wh = pv_production_wh - ac_consumption_wh
        
        # Step 2: Handle AC surplus or deficit
        if ac_balance_wh > 0:
            # AC surplus - try to charge battery and supply DC loads
            flows.update(self._handle_ac_surplus(ac_balance_wh, dc_consumption_wh))
        else:
            # AC deficit - use inverter if available
            ac_deficit_wh = abs(ac_balance_wh)
            flows.update(self._handle_ac_deficit(ac_deficit_wh, dc_consumption_wh))
        
        # Update battery SOC in inverter
        self.inverter.update_soc(self.battery.current_soc_percent)
        
        # Store flows for reporting
        self.last_flows = flows.copy()
        
        # Add final states
        flows.update({
            "final_soc_percent": self.battery.current_soc_percent,
            "inverter_enabled": self.inverter.is_enabled,
        })
        
        return flows
    
    def _handle_ac_surplus(self, ac_surplus_wh: float, dc_consumption_wh: float) -> Dict[str, float]:
        """Handle AC energy surplus.
        
        Args:
            ac_surplus_wh: Available AC surplus in Wh
            dc_consumption_wh: DC consumption requirement in Wh
            
        Returns:
            Dictionary with energy flows
        """
        flows = {
            "grid_import_wh": 0.0,
            "grid_export_wh": 0.0,
            "battery_charge_wh": 0.0,
            "battery_discharge_wh": 0.0,
            "charger_ac_to_dc_wh": 0.0,
            "charger_dc_from_ac_wh": 0.0,
            "inverter_dc_to_ac_wh": 0.0,
            "charger_standby_wh": 0.0,
            "inverter_standby_wh": 0.0,
        }
        
        remaining_ac_wh = ac_surplus_wh
        
        # Calculate total DC demand (consumption + potential battery charging)
        max_battery_charge_wh = self.battery.get_max_charge_energy_wh()
        total_dc_demand_wh = dc_consumption_wh
        
        # If battery can still be charged, add charging demand
        if max_battery_charge_wh > 0:
            total_dc_demand_wh += max_battery_charge_wh / self.battery.charge_efficiency
        
        # Use charger to convert AC to DC
        charger_input_wh = min(remaining_ac_wh, self.charger.get_max_ac_input_wh())
        if charger_input_wh > 0:
            dc_output_wh = self.charger.convert_ac_to_dc(charger_input_wh)
            flows["charger_ac_to_dc_wh"] = dc_output_wh
            flows["charger_standby_wh"] = self.charger.get_standby_consumption_wh(True)
            remaining_ac_wh -= charger_input_wh
            
            # Use DC output for consumption first, then battery charging
            remaining_dc_wh = dc_output_wh
            
            # Supply DC consumption
            dc_supplied_wh = min(remaining_dc_wh, dc_consumption_wh)
            remaining_dc_wh -= dc_supplied_wh
            
            # Charge battery with remaining DC energy
            if remaining_dc_wh > 0 and max_battery_charge_wh > 0:
                actual_charge_wh = self.battery.charge_discharge(remaining_dc_wh)
                flows["battery_charge_wh"] = actual_charge_wh
        
        # Export remaining AC surplus to grid
        if remaining_ac_wh > 0:
            flows["grid_export_wh"] = remaining_ac_wh
        
        return flows
    
    def _handle_ac_deficit(self, ac_deficit_wh: float, dc_consumption_wh: float) -> Dict[str, float]:
        """Handle AC energy deficit.
        
        Args:
            ac_deficit_wh: AC energy deficit in Wh
            dc_consumption_wh: DC consumption requirement in Wh
            
        Returns:
            Dictionary with energy flows
        """
        flows = {
            "grid_import_wh": 0.0,
            "grid_export_wh": 0.0,
            "battery_charge_wh": 0.0,
            "battery_discharge_wh": 0.0,
            "charger_ac_to_dc_wh": 0.0,
            "charger_dc_from_ac_wh": 0.0,
            "inverter_dc_to_ac_wh": 0.0,
            "charger_standby_wh": 0.0,
            "inverter_standby_wh": 0.0,
        }
        
        remaining_ac_deficit_wh = ac_deficit_wh
        remaining_dc_consumption_wh = dc_consumption_wh
        
        # Try to use inverter to supply AC deficit
        if self.inverter.is_enabled and remaining_ac_deficit_wh > 0:
            max_inverter_output_wh = self.inverter.get_max_ac_output_wh()
            inverter_output_wh = min(remaining_ac_deficit_wh, max_inverter_output_wh)
            
            # Check if battery has enough energy
            dc_needed_wh = self.inverter.provide_ac_from_dc(inverter_output_wh)
            max_discharge_wh = self.battery.get_max_discharge_energy_wh()
            
            if dc_needed_wh <= max_discharge_wh:
                # Battery can supply the needed DC energy
                actual_discharge_wh = abs(self.battery.charge_discharge(-dc_needed_wh))
                flows["battery_discharge_wh"] = actual_discharge_wh
                flows["inverter_dc_to_ac_wh"] = inverter_output_wh
                flows["inverter_standby_wh"] = self.inverter.get_standby_consumption_wh()
                remaining_ac_deficit_wh -= inverter_output_wh
            else:
                # Battery cannot supply enough energy - inverter automatically disabled
                self.inverter.set_enabled(False)
        
        # Handle remaining AC deficit from grid
        if remaining_ac_deficit_wh > 0:
            flows["grid_import_wh"] += remaining_ac_deficit_wh
        
        # Handle DC consumption
        if remaining_dc_consumption_wh > 0:
            # Try to supply from battery first
            max_discharge_wh = self.battery.get_max_discharge_energy_wh()
            battery_dc_wh = min(remaining_dc_consumption_wh, max_discharge_wh)
            
            if battery_dc_wh > 0:
                actual_discharge_wh = abs(self.battery.charge_discharge(-battery_dc_wh))
                flows["battery_discharge_wh"] += actual_discharge_wh
                remaining_dc_consumption_wh -= battery_dc_wh
            
            # Use charger for remaining DC consumption (DC emergency mode)
            if remaining_dc_consumption_wh > 0:
                ac_needed_wh = self.charger.provide_dc_from_ac(remaining_dc_consumption_wh)
                flows["charger_dc_from_ac_wh"] = remaining_dc_consumption_wh
                flows["charger_standby_wh"] = self.charger.get_standby_consumption_wh(True)
                flows["grid_import_wh"] += ac_needed_wh
        
        return flows
    
    def simulate_energy_flow(
        self, 
        pv_production_wh: float,
        ac_consumption_wh: float,
        dc_consumption_wh: float,
        initial_soc_percent: float
    ) -> Tuple[Dict[str, float], float]:
        """Simulate energy flows without changing system state.
        
        Args:
            pv_production_wh: PV production in Wh
            ac_consumption_wh: AC consumption in Wh
            dc_consumption_wh: DC consumption in Wh
            initial_soc_percent: Starting SOC for simulation
            
        Returns:
            Tuple of (flows_dict, final_soc_percent)
        """
        # Store current states
        original_soc = self.battery.current_soc_percent
        original_inverter_enabled = self.inverter.is_enabled
        
        # Set simulation state
        self.battery.current_soc_percent = initial_soc_percent
        self.inverter.update_soc(initial_soc_percent)
        
        # Run simulation
        flows = self.calculate_energy_flow(pv_production_wh, ac_consumption_wh, dc_consumption_wh)
        final_soc = flows["final_soc_percent"]
        
        # Restore original states
        self.battery.current_soc_percent = original_soc
        self.inverter.set_enabled(original_inverter_enabled)
        self.inverter.update_soc(original_soc)
        
        return flows, final_soc
    
    def get_last_flows(self) -> Dict[str, float]:
        """Get the energy flows from the last calculation."""
        return self.last_flows.copy()
