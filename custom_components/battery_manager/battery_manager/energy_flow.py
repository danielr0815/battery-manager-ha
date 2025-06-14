"""Energy Flow Calculator for the Battery Manager system."""

from typing import Any, Dict, Tuple

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

    def calculate_energy_flow(
        self,
        pv_production_wh: float,
        ac_consumption_wh: float,
        dc_consumption_wh: float,
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
            "charger_forced_wh": 0.0,
            "charger_voluntary_wh": 0.0,
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
        
        # Add final states
        flows.update(
            {
                "final_soc_percent": self.battery.current_soc_percent,
                "inverter_enabled": self.inverter.is_enabled,
            }
        )

        return flows

    def _handle_ac_surplus(
        self, ac_surplus_wh: float, dc_consumption_wh: float
    ) -> Dict[str, float]:
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
            "charger_forced_wh": 0.0,
            "charger_voluntary_wh": 0.0,
            "inverter_dc_to_ac_wh": 0.0,
            "charger_standby_wh": 0.0,
            "inverter_standby_wh": 0.0,
        }

        remaining_ac_wh = ac_surplus_wh

        # Calculate actual DC needs
        max_battery_charge_wh = self.battery.get_max_charge_energy_wh()
        dc_demand_for_consumption = dc_consumption_wh
        
        # Only add battery charging demand if battery can actually be charged
        dc_demand_for_charging = 0.0
        if max_battery_charge_wh > 0:
            dc_demand_for_charging = max_battery_charge_wh / self.battery.charge_efficiency

        total_dc_demand_wh = dc_demand_for_consumption + dc_demand_for_charging

        # Use charger to convert AC to DC, but only for actual demand
        if total_dc_demand_wh > 0:
            # Convert only what we can actually use
            charger_input_wh = min(remaining_ac_wh, self.charger.get_max_ac_input_wh())
            charger_input_wh = min(charger_input_wh, total_dc_demand_wh / self.charger.efficiency)
            
            if charger_input_wh > 0:
                dc_output_wh = self.charger.convert_ac_to_dc(charger_input_wh)
                flows["charger_ac_to_dc_wh"] = dc_output_wh
                flows["charger_voluntary_wh"] = dc_output_wh  # This is voluntary (from PV surplus)
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
        else:
            # No DC demand - convert minimum needed for DC consumption only
            if dc_consumption_wh > 0:
                # Convert only what's needed for DC consumption
                charger_input_needed = dc_consumption_wh / self.charger.efficiency
                charger_input_wh = min(remaining_ac_wh, charger_input_needed)
                charger_input_wh = min(charger_input_wh, self.charger.get_max_ac_input_wh())
                
                if charger_input_wh > 0:
                    dc_output_wh = self.charger.convert_ac_to_dc(charger_input_wh)
                    flows["charger_ac_to_dc_wh"] = dc_output_wh
                    flows["charger_voluntary_wh"] = dc_output_wh
                    flows["charger_standby_wh"] = self.charger.get_standby_consumption_wh(True)
                    remaining_ac_wh -= charger_input_wh
                    
                    # Use for DC consumption (should fully cover it if charger is sized correctly)
                    dc_supplied_wh = min(dc_output_wh, dc_consumption_wh)

        # Export remaining AC surplus to grid
        if remaining_ac_wh > 0:
            flows["grid_export_wh"] = remaining_ac_wh

        return flows

    def _handle_ac_deficit(
        self, ac_deficit_wh: float, dc_consumption_wh: float
    ) -> Dict[str, float]:
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
            "charger_forced_wh": 0.0,
            "charger_voluntary_wh": 0.0,
            "inverter_dc_to_ac_wh": 0.0,
            "charger_standby_wh": 0.0,
            "inverter_standby_wh": 0.0,
        }

        remaining_ac_deficit_wh = ac_deficit_wh
        total_dc_needed_wh = dc_consumption_wh
        inverter_ac_output_wh = 0.0

        # Try to use inverter to supply AC deficit
        if self.inverter.is_enabled and remaining_ac_deficit_wh > 0:
            max_inverter_output_wh = self.inverter.get_max_ac_output_wh()
            inverter_ac_output_wh = min(remaining_ac_deficit_wh, max_inverter_output_wh)

            # Calculate DC energy needed for this AC output
            dc_needed_for_ac_wh = self.inverter.provide_ac_from_dc(inverter_ac_output_wh)
            total_dc_needed_wh += dc_needed_for_ac_wh
            
            flows["inverter_dc_to_ac_wh"] = inverter_ac_output_wh
            flows["inverter_standby_wh"] = self.inverter.get_standby_consumption_wh()
            remaining_ac_deficit_wh -= inverter_ac_output_wh

        # Handle all DC consumption (direct DC + inverter DC) from battery in one transaction
        if total_dc_needed_wh > 0:
            # Calculate required battery energy considering discharge efficiency
            required_battery_wh = total_dc_needed_wh / self.battery.discharge_efficiency
            max_discharge_wh = self.battery.get_max_discharge_energy_wh()

            if required_battery_wh <= max_discharge_wh:
                # Battery can supply all needed energy
                discharge_result = self.battery.charge_discharge(-total_dc_needed_wh)
                flows["battery_discharge_wh"] = required_battery_wh
            else:
                # Battery cannot supply enough energy - use what's available
                if max_discharge_wh > 0:
                    available_dc_output = max_discharge_wh * self.battery.discharge_efficiency
                    discharge_result = self.battery.charge_discharge(-available_dc_output)
                    flows["battery_discharge_wh"] = max_discharge_wh
                    
                    # Adjust inverter output if not enough DC available
                    if available_dc_output < total_dc_needed_wh:
                        dc_shortage = total_dc_needed_wh - available_dc_output
                        # Reduce inverter output proportionally if needed
                        if dc_needed_for_ac_wh > 0:
                            reduction_factor = max(0, (dc_needed_for_ac_wh - dc_shortage) / dc_needed_for_ac_wh)
                            flows["inverter_dc_to_ac_wh"] *= reduction_factor

        # Handle remaining AC deficit from grid
        if remaining_ac_deficit_wh > 0:
            flows["grid_import_wh"] += remaining_ac_deficit_wh

        # Handle DC consumption that couldn't be supplied by battery
        remaining_dc_wh = max(0, dc_consumption_wh - (total_dc_needed_wh - dc_consumption_wh))
        if remaining_dc_wh > 0 and total_dc_needed_wh > dc_consumption_wh:
            # Some DC was used for inverter, check if direct DC needs are still unmet
            battery_dc_available = flows["battery_discharge_wh"] * self.battery.discharge_efficiency
            dc_used_for_inverter = total_dc_needed_wh - dc_consumption_wh
            remaining_dc_for_direct = max(0, dc_consumption_wh - max(0, battery_dc_available - dc_used_for_inverter))
            
            if remaining_dc_for_direct > 0:
                # Use charger for remaining DC consumption (DC emergency mode)
                ac_needed_wh = self.charger.provide_dc_from_ac(remaining_dc_for_direct)
                flows["charger_dc_from_ac_wh"] = remaining_dc_for_direct
                flows["charger_forced_wh"] = remaining_dc_for_direct
                flows["charger_standby_wh"] = self.charger.get_standby_consumption_wh(True)
                flows["grid_import_wh"] += ac_needed_wh

        return flows

    def simulate_energy_flow(
        self,
        pv_production_wh: float,
        ac_consumption_wh: float,
        dc_consumption_wh: float,
        initial_soc_percent: float,
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
        flows = self.calculate_energy_flow(
            pv_production_wh, ac_consumption_wh, dc_consumption_wh
        )
        final_soc = flows["final_soc_percent"]

        # Restore original states
        self.battery.current_soc_percent = original_soc
        self.inverter.set_enabled(original_inverter_enabled)
        self.inverter.update_soc(original_soc)

        return flows, final_soc
