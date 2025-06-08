"""Controller module for the Battery Manager system."""

from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta
from .battery import Battery
from .pv_system import PVSystem
from .consumers import ACConsumer, DCConsumer
from .charger import Charger
from .inverter import Inverter
from .energy_flow import EnergyFlowCalculator


class MaximumBasedController:
    """Maximum-Based Controller for battery management optimization."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the controller with configuration.
        
        Args:
            config: Configuration dictionary containing all system parameters
        """
        self.target_soc_percent = config.get("target_soc_percent", 85.0)
        
        # Initialize system components
        self.battery = Battery(config)
        self.pv_system = PVSystem(config)
        self.ac_consumer = ACConsumer(config)
        self.dc_consumer = DCConsumer(config)
        self.charger = Charger(config)
        self.inverter = Inverter(config)
        
        # Initialize energy flow calculator
        self.energy_flow = EnergyFlowCalculator(self.battery, self.charger, self.inverter)
        
        # Results cache
        self._last_calculation_time = None
        self._last_results = None
        
        # Detailed hourly calculation data (for debugging)
        self._last_hourly_details = []
    
    def calculate_soc_threshold(
        self, 
        current_soc_percent: float,
        daily_forecasts: List[float],
        current_time: datetime = None
    ) -> Dict[str, Any]:
        """Calculate the SOC threshold for inverter operation.
        
        Args:
            current_soc_percent: Current battery SOC in percent
            daily_forecasts: List of daily PV forecasts [today, tomorrow, day_after] in kWh
            current_time: Current time (defaults to now)
            
        Returns:
            Dictionary containing threshold and forecast results
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Set current SOC
        self.battery.current_soc_percent = current_soc_percent
        self.inverter.update_soc(current_soc_percent)
        
        # Clear any previous simulation threshold override
        self.energy_flow.clear_simulation_threshold()
        
        # Calculate forecast period (until 8 AM in 2 days)
        forecast_start = current_time.replace(minute=0, second=0, microsecond=0)
        target_end = self._get_forecast_end_time(current_time)
        forecast_hours = int((target_end - forecast_start).total_seconds() / 3600)
        
        # Run simulation
        soc_forecast = self._simulate_soc_progression(
            forecast_start, forecast_hours, daily_forecasts, current_soc_percent, current_time
        )
        
        # Calculate threshold based on the algorithm
        threshold_soc = self._calculate_threshold_from_forecast(soc_forecast)
        
        # Find min and max SOC in forecast
        min_soc = min(soc_forecast) if soc_forecast else current_soc_percent
        max_soc = max(soc_forecast) if soc_forecast else current_soc_percent
        
        # Calculate grid flows for the entire forecast period
        grid_flows = self._calculate_total_grid_flows(
            forecast_start, forecast_hours, daily_forecasts, current_soc_percent, current_time
        )
        
        results = {
            "soc_threshold_percent": threshold_soc,
            "min_soc_forecast_percent": min_soc,
            "max_soc_forecast_percent": max_soc,
            "inverter_enabled": current_soc_percent > threshold_soc,
            "forecast_hours": forecast_hours,
            "forecast_end_time": target_end,
            "grid_import_kwh": grid_flows["import_kwh"],
            "grid_export_kwh": grid_flows["export_kwh"],
            "soc_forecast": soc_forecast,  # For debugging
        }
        
        # Cache results
        self._last_calculation_time = current_time
        self._last_results = results
        
        return results
    
    def _get_forecast_end_time(self, current_time: datetime) -> datetime:
        """Get the forecast end time (8 AM in 2 days).
        
        Args:
            current_time: Current time
            
        Returns:
            Forecast end time
        """
        # Calculate 2 days from current date
        target_date = current_time.date() + timedelta(days=2)
        
        # Set to 8 AM
        forecast_end = datetime.combine(target_date, datetime.min.time().replace(hour=8))
        
        # Ensure timezone consistency with input
        if current_time.tzinfo is not None:
            forecast_end = forecast_end.replace(tzinfo=current_time.tzinfo)
        
        return forecast_end
    
    def _simulate_soc_progression(
        self, 
        start_time: datetime, 
        hours: int, 
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime
    ) -> List[float]:
        """Simulate SOC progression over the forecast period.
        
        Args:
            start_time: Start time for simulation
            hours: Number of hours to simulate
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for simulation
            reference_time: Reference time for day offset calculations
            
        Returns:
            List of hourly SOC values
        """
        soc_forecast = []
        current_soc = initial_soc_percent
        current_time = start_time
        
        # Clear previous hourly details
        self._last_hourly_details = []

        for hour in range(hours):
            # Get hourly values - pass reference_time for consistent day offset calculation
            pv_production_wh = self.pv_system.calculate_hourly_production_wh(
                daily_forecasts, current_time, reference_time
            )
            ac_consumption_wh = self.ac_consumer.calculate_hourly_consumption_wh(current_time)
            dc_consumption_wh = self.dc_consumer.calculate_hourly_consumption_wh(current_time)
            
            # Simulate energy flow for this hour
            flows, new_soc = self.energy_flow.simulate_energy_flow(
                pv_production_wh, ac_consumption_wh, dc_consumption_wh, current_soc
            )
            
            # Store detailed hourly data
            hourly_detail = {
                "hour": hour,
                "datetime": current_time.isoformat(),
                "initial_soc_percent": current_soc,
                "final_soc_percent": new_soc,
                "pv_production_wh": pv_production_wh,
                "ac_consumption_wh": ac_consumption_wh,
                "dc_consumption_wh": dc_consumption_wh,
                "grid_import_wh": flows.get("grid_import_wh", 0.0),
                "grid_export_wh": flows.get("grid_export_wh", 0.0),
                "battery_charge_wh": flows.get("battery_charge_wh", 0.0),
                "battery_discharge_wh": flows.get("battery_discharge_wh", 0.0),
                "charger_ac_to_dc_wh": flows.get("charger_ac_to_dc_wh", 0.0),
                "charger_dc_from_ac_wh": flows.get("charger_dc_from_ac_wh", 0.0),
                "charger_forced_wh": flows.get("charger_forced_wh", 0.0),
                "charger_voluntary_wh": flows.get("charger_voluntary_wh", 0.0),
                "inverter_dc_to_ac_wh": flows.get("inverter_dc_to_ac_wh", 0.0),
                "inverter_enabled": flows.get("inverter_enabled", False),
                "net_grid_wh": flows.get("grid_import_wh", 0.0) - flows.get("grid_export_wh", 0.0),
                "net_battery_wh": flows.get("battery_charge_wh", 0.0) - flows.get("battery_discharge_wh", 0.0),
            }
            self._last_hourly_details.append(hourly_detail)
            
            current_soc = new_soc
            soc_forecast.append(current_soc)
            
            # Move to next hour
            current_time += timedelta(hours=1)

        return soc_forecast
    
    def _calculate_threshold_from_forecast(self, soc_forecast: List[float]) -> float:
        """Calculate SOC threshold based on forecast algorithm.
        
        Args:
            soc_forecast: List of forecasted SOC values
            
        Returns:
            SOC threshold for inverter operation
        """
        if not soc_forecast:
            return self.target_soc_percent
        
        current_soc = self.battery.current_soc_percent
        min_battery_soc = self.battery.min_soc_percent
        
        # Find the earliest time when SOC reaches target (85%)
        target_reached_index = None
        for i, soc in enumerate(soc_forecast):
            if soc >= self.target_soc_percent:
                target_reached_index = i
                break
        
        # If target is never reached, set threshold to target value
        if target_reached_index is None:
            return self.target_soc_percent
        
        # Find minimum SOC in the entire forecast period
        min_soc_forecast = min(soc_forecast)
        
        # Calculate total forced charger energy before target is reached
        total_forced_charger_wh = 0.0
        if self._last_hourly_details and target_reached_index is not None:
            # Sum forced charger energy from start until target is reached
            end_index = min(target_reached_index + 1, len(self._last_hourly_details))
            for hour_detail in self._last_hourly_details[:end_index]:
                total_forced_charger_wh += hour_detail.get("charger_forced_wh", 0.0)
        
        # Convert forced charger energy to SOC percentage
        battery_capacity_wh = self.battery.capacity_wh
        forced_charger_soc_percent = (total_forced_charger_wh / battery_capacity_wh) * 100.0
        
        # New optimized threshold strategy:
        # Maximize battery discharge while ensuring we never go below the minimum forecasted SOC
        # Formula: current_soc - (min_soc_forecast - min_battery_soc) + forced_charger_adjustment
        
        # Calculate the safety margin from the forecast
        forecast_safety_margin = min_soc_forecast - min_battery_soc
        
        # Calculate optimized threshold that allows maximum safe discharge
        threshold_soc = current_soc - forecast_safety_margin + forced_charger_soc_percent
        
        # Get minimum inverter threshold from configuration
        inverter_min_soc = self.inverter.min_soc_percent
        
        # Apply boundary constraints:
        # 1. Never below battery minimum (5%)
        # 2. Never below inverter minimum (configurable, default 20%)
        # 3. Never above target SOC (85%)
        # 4. Never above current SOC (can't discharge to higher level)
        min_allowed_threshold = max(min_battery_soc, inverter_min_soc)
        max_allowed_threshold = min(self.target_soc_percent, current_soc)
        
        threshold_soc = max(min_allowed_threshold, min(threshold_soc, max_allowed_threshold))
        
        return threshold_soc
    
    def _calculate_total_grid_flows(
        self, 
        start_time: datetime, 
        hours: int, 
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime
    ) -> Dict[str, float]:
        """Calculate total grid import/export for the forecast period.
        
        Args:
            start_time: Start time for calculation
            hours: Number of hours to calculate
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for calculation
            reference_time: Reference time for day offset calculations
            
        Returns:
            Dictionary with total import and export in kWh
        """
        total_import_wh = 0.0
        total_export_wh = 0.0
        
        current_soc = initial_soc_percent
        current_time = start_time
        
        for hour in range(hours):
            # Get hourly values - pass reference_time for consistent day offset calculation
            pv_production_wh = self.pv_system.calculate_hourly_production_wh(
                daily_forecasts, current_time, reference_time
            )
            ac_consumption_wh = self.ac_consumer.calculate_hourly_consumption_wh(current_time)
            dc_consumption_wh = self.dc_consumer.calculate_hourly_consumption_wh(current_time)
            
            # Simulate energy flow for this hour
            flows, new_soc = self.energy_flow.simulate_energy_flow(
                pv_production_wh, ac_consumption_wh, dc_consumption_wh, current_soc
            )
            
            total_import_wh += flows.get("grid_import_wh", 0.0)
            total_export_wh += flows.get("grid_export_wh", 0.0)
            current_soc = new_soc
            
            # Move to next hour
            current_time += timedelta(hours=1)
        
        return {
            "import_kwh": total_import_wh / 1000.0,
            "export_kwh": total_export_wh / 1000.0,
        }
    
    def get_last_results(self) -> Dict[str, Any]:
        """Get the results from the last calculation."""
        return self._last_results.copy() if self._last_results else None
    
    def get_last_hourly_details(self) -> List[Dict[str, Any]]:
        """Get detailed hourly calculation data from the last simulation.
        
        Returns:
            List of hourly calculation data for debugging/analysis
        """
        return self._last_hourly_details.copy() if self._last_hourly_details else []
    
    def update_config(self, config: Dict[str, Any]) -> None:
        """Update system configuration.
        
        Args:
            config: New configuration parameters
        """
        # Update target SOC
        if "target_soc_percent" in config:
            self.target_soc_percent = config["target_soc_percent"]
        
        # Update component configurations
        if any(key.startswith("battery_") for key in config):
            battery_config = {k[8:]: v for k, v in config.items() if k.startswith("battery_")}
            if battery_config:
                self.battery = Battery({**self.battery.get_config(), **battery_config})
        
        if any(key.startswith("pv_") for key in config):
            pv_config = {k[3:]: v for k, v in config.items() if k.startswith("pv_")}
            if pv_config:
                self.pv_system = PVSystem({**self.pv_system.get_config(), **pv_config})
        
        if any(key.startswith("ac_") for key in config):
            ac_config = {k[3:]: v for k, v in config.items() if k.startswith("ac_")}
            if ac_config:
                self.ac_consumer = ACConsumer({**self.ac_consumer.get_config(), **ac_config})
        
        if any(key.startswith("dc_") for key in config):
            dc_config = {k[3:]: v for k, v in config.items() if k.startswith("dc_")}
            if dc_config:
                self.dc_consumer = DCConsumer({**self.dc_consumer.get_config(), **dc_config})
        
        if any(key.startswith("charger_") for key in config):
            charger_config = {k[8:]: v for k, v in config.items() if k.startswith("charger_")}
            if charger_config:
                self.charger = Charger({**self.charger.get_config(), **charger_config})
        
        if any(key.startswith("inverter_") for key in config):
            inverter_config = {k[9:]: v for k, v in config.items() if k.startswith("inverter_")}
            if inverter_config:
                self.inverter = Inverter({**self.inverter.get_config(), **inverter_config})
        
        # Recreate energy flow calculator with updated components
        self.energy_flow = EnergyFlowCalculator(self.battery, self.charger, self.inverter)
        
        # Clear cache since configuration changed
        self._last_calculation_time = None
        self._last_results = None
    
    def get_hourly_detailed_results(self) -> List[Dict[str, Any]]:
        """Get the detailed hourly results from the last calculation."""
        return [result.copy() for result in self._last_hourly_details] if self._last_hourly_details else []
