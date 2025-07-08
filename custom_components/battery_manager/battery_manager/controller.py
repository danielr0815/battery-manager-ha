"""Controller module for the Battery Manager system."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from .battery import Battery
from .charger import Charger
from .consumers import ACConsumer, DCConsumer
from .energy_flow import EnergyFlowCalculator
from .inverter import Inverter
from .pv_system import PVSystem


class MaximumBasedController:
    """Maximum-Based Controller for battery management optimization."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize the controller with configuration.

        Args:
            config: Configuration dictionary containing all system parameters
        """
        self.target_soc_percent = config.get("target_soc_percent", 85.0)

        # Initialize system components with filtered configs
        self.battery = Battery(config)
        self.pv_system = PVSystem(config)
        self.ac_consumer = ACConsumer(config)
        self.dc_consumer = DCConsumer(config)
        
        # Create charger config with correct efficiency parameter
        charger_config = dict(config)
        if "charger_efficiency" in config:
            charger_config["efficiency"] = config["charger_efficiency"]
        self.charger = Charger(charger_config)
        
        # Create inverter config with correct efficiency parameter  
        inverter_config = dict(config)
        if "inverter_efficiency" in config:
            inverter_config["efficiency"] = config["inverter_efficiency"]
        if "inverter_min_soc_percent" in config:
            inverter_config["min_soc_percent"] = config["inverter_min_soc_percent"]
        self.inverter = Inverter(inverter_config)

        # Initialize energy flow calculator
        self.energy_flow = EnergyFlowCalculator(
            self.battery, self.charger, self.inverter
        )

        # Results cache
        self._last_calculation_time = None
        self._last_results = None

        # Detailed hourly calculation data (for debugging)
        self._last_hourly_details = []

    def calculate_soc_threshold(
        self,
        current_soc_percent: float,
        daily_forecasts: List[float],
        current_time: datetime = None,
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

        # Calculate forecast period (until 8 AM in 2 days)
        # Keep original time for accurate first-hour calculation
        forecast_start = current_time
        target_end = self._get_forecast_end_time(current_time)
        # Calculate total forecast duration in hours (including partial first hour)
        total_seconds = (target_end - forecast_start).total_seconds()
        forecast_hours = int(total_seconds / 3600) + (1 if total_seconds % 3600 > 0 else 0)

        # Run simulation with additional load optimization
        additional_load_result = self._calculate_additional_load_optimization(
            forecast_start,
            forecast_hours,
            daily_forecasts,
            current_soc_percent,
            current_time,
        )
        
        # Use optimized hourly details
        self._last_hourly_details = additional_load_result["hourly_details"]
        
        # Extract SOC forecast from the hourly details
        soc_forecast = [detail["final_soc_percent"] for detail in self._last_hourly_details]

        # Calculate threshold based on the algorithm
        threshold_result = self._calculate_threshold_from_forecast(soc_forecast)
        threshold_soc = threshold_result["threshold_soc"]
        discharge_forecast_percent = threshold_result["discharge_forecast_percent"]

        # Find min and max SOC in forecast - use same logic as threshold calculation
        min_soc = self._get_relevant_min_soc(soc_forecast, current_soc_percent)
        max_soc = max(soc_forecast) if soc_forecast else current_soc_percent

        # Calculate hours until max SOC is reached
        if soc_forecast:
            max_index = soc_forecast.index(max_soc)
            # Number of whole hours from the forecast start until max is reached
            hours_until_max = max_index + 1
        else:
            hours_until_max = 0

        # Calculate grid flows for the entire forecast period
        grid_flows = self._calculate_total_grid_flows(
            forecast_start,
            forecast_hours,
            daily_forecasts,
            current_soc_percent,
            current_time,
        )

        results = {
            "soc_threshold_percent": threshold_soc,
            "min_soc_forecast_percent": min_soc,
            "max_soc_forecast_percent": max_soc,
            "discharge_forecast_percent": discharge_forecast_percent,
            "inverter_enabled": current_soc_percent > threshold_soc,
            "forecast_hours": forecast_hours,
            "forecast_end_time": target_end,
            "hours_until_max_soc": hours_until_max,
            "grid_import_kwh": grid_flows["import_kwh"],
            "grid_export_kwh": grid_flows["export_kwh"],
            "soc_forecast": soc_forecast,  # For debugging
            "additional_load_active": additional_load_result["additional_load_active"],
            "additional_load_schedule": additional_load_result["schedule"],
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
        forecast_end = datetime.combine(
            target_date, datetime.min.time().replace(hour=8)
        )

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
        reference_time: datetime,
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
            # Calculate duration multiplier for partial hours
            if hour == 0:
                # First hour: calculate remaining minutes until end of hour
                minutes_remaining = 60 - start_time.minute
                duration_fraction = minutes_remaining / 60.0
            else:
                # Full hours after the first
                duration_fraction = 1.0

            # Get hourly values - pass reference_time for consistent day offset calculation
            pv_production_wh_full = self.pv_system.calculate_hourly_production_wh(
                daily_forecasts, current_time, reference_time
            )
            ac_consumption_wh_full = self.ac_consumer.calculate_hourly_consumption_wh(
                current_time
            )
            dc_consumption_wh_full = self.dc_consumer.calculate_hourly_consumption_wh(
                current_time
            )

            # Scale energy values by duration fraction for partial hours
            pv_production_wh = pv_production_wh_full * duration_fraction
            ac_consumption_wh = ac_consumption_wh_full * duration_fraction
            dc_consumption_wh = dc_consumption_wh_full * duration_fraction

            # Simulate energy flow for this interval
            flows, new_soc = self.energy_flow.simulate_energy_flow(
                pv_production_wh, ac_consumption_wh, dc_consumption_wh, current_soc
            )

            # Store detailed hourly data with duration_minutes for display
            duration_minutes = int(duration_fraction * 60)
            hourly_detail = {
                "hour": hour,
                "datetime": current_time.isoformat(),
                "duration_fraction": duration_fraction,
                "duration_minutes": duration_minutes,
                "initial_soc_percent": current_soc,
                "final_soc_percent": new_soc,
                "pv_production_wh": pv_production_wh,
                "ac_consumption_wh": ac_consumption_wh,
                "dc_consumption_wh": dc_consumption_wh,
                "additional_load_active": False,  # Always false in base simulation
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
                "net_grid_wh": flows.get("grid_import_wh", 0.0)
                - flows.get("grid_export_wh", 0.0),
                "net_battery_wh": flows.get("battery_charge_wh", 0.0)
                - flows.get("battery_discharge_wh", 0.0),
            }
            self._last_hourly_details.append(hourly_detail)

            current_soc = new_soc
            soc_forecast.append(current_soc)

            # Move to next hour, but keep consistent hourly boundaries
            if hour == 0:
                # After first partial hour, jump to the next full hour boundary
                current_time = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                # Subsequent hours are full hours
                current_time += timedelta(hours=1)

        return soc_forecast

    def _calculate_threshold_from_forecast(
        self, soc_forecast: List[float]
    ) -> Dict[str, float]:
        """Calculate SOC threshold based on forecast algorithm.

        Args:
            soc_forecast: List of forecasted SOC values

        Returns:
            SOC threshold for inverter operation
        """
        if not soc_forecast:
            return {
                "threshold_soc": self.target_soc_percent,
                "discharge_forecast_percent": 0.0,
            }

        current_soc = self.battery.current_soc_percent
        min_battery_soc = self.battery.min_soc_percent

        # Find the relevant time when SOC reaches target (85%)
        target_reached_index = None
        start_soc = soc_forecast[0] if soc_forecast else current_soc
        
        if start_soc >= self.target_soc_percent:
            # Battery already at/above target - find NEXT time it reaches target after dropping below
            below_target_seen = False
            for i, soc in enumerate(soc_forecast):
                if soc < self.target_soc_percent:
                    below_target_seen = True
                elif below_target_seen and soc >= self.target_soc_percent:
                    target_reached_index = i
                    break
        else:
            # Battery below target - find FIRST time it reaches target
            for i, soc in enumerate(soc_forecast):
                if soc >= self.target_soc_percent:
                    target_reached_index = i
                    break

        # If target is never reached, set threshold to target value
        if target_reached_index is None:
            return {
                "threshold_soc": self.target_soc_percent,
                "discharge_forecast_percent": 0.0,
            }

        # Find minimum SOC only until target is reached (or entire period if target never reached)
        if target_reached_index is not None:
            # Only consider SOC values until target is reached
            relevant_soc_values = soc_forecast[:target_reached_index + 1]
        else:
            # Target never reached, consider entire forecast
            relevant_soc_values = soc_forecast
        
        min_soc_forecast = min(relevant_soc_values)

        # Calculate total forced charger energy before target is reached
        total_forced_charger_wh = 0.0
        if self._last_hourly_details and target_reached_index is not None:
            # Sum forced charger energy from start until target is reached
            end_index = min(target_reached_index + 1, len(self._last_hourly_details))
            for hour_detail in self._last_hourly_details[:end_index]:
                total_forced_charger_wh += hour_detail.get("charger_forced_wh", 0.0)

        # Convert forced charger energy to SOC percentage
        battery_capacity_wh = self.battery.capacity_wh
        forced_charger_soc_percent = (
            total_forced_charger_wh / battery_capacity_wh
        ) * 100.0

        # New optimized threshold strategy:
        # Maximize battery discharge while ensuring we never go below the minimum forecasted SOC
        # Formula: current_soc - (min_soc_forecast - min_battery_soc) + forced_charger_adjustment

        # Calculate the safety margin from the forecast
        forecast_safety_margin = min_soc_forecast - min_battery_soc

        # Calculate optimized threshold that allows maximum safe discharge
        threshold_soc = (
            current_soc - forecast_safety_margin + forced_charger_soc_percent
        )

        # Calculate discharge forecast value (forecast_safety_margin - forced_charger_soc_percent)
        discharge_forecast_percent = forecast_safety_margin - forced_charger_soc_percent

        # Get minimum inverter threshold from configuration
        inverter_min_soc = self.inverter.min_soc_percent

        # Apply boundary constraints:
        # 1. Never below battery minimum (5%)
        # 2. Never below inverter minimum (configurable, default 20%)
        # 3. Never above target SOC (85%)
        # 4. Never above current SOC (can't discharge to higher level)
        min_allowed_threshold = max(min_battery_soc, inverter_min_soc)
        max_allowed_threshold = min(self.target_soc_percent, current_soc)

        threshold_soc = max(
            min_allowed_threshold, min(threshold_soc, max_allowed_threshold)
        )

        return {
            "threshold_soc": threshold_soc,
            "discharge_forecast_percent": discharge_forecast_percent,
        }

    def _calculate_total_grid_flows(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
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
            # Calculate duration multiplier for partial hours
            if hour == 0:
                # First hour: calculate remaining minutes until end of hour
                minutes_remaining = 60 - start_time.minute
                duration_fraction = minutes_remaining / 60.0
            else:
                # Full hours after the first
                duration_fraction = 1.0

            # Get hourly values - pass reference_time for consistent day offset calculation
            pv_production_wh_full = self.pv_system.calculate_hourly_production_wh(
                daily_forecasts, current_time, reference_time
            )
            ac_consumption_wh_full = self.ac_consumer.calculate_hourly_consumption_wh(
                current_time
            )
            dc_consumption_wh_full = self.dc_consumer.calculate_hourly_consumption_wh(
                current_time
            )

            # Scale energy values by duration fraction for partial hours
            pv_production_wh = pv_production_wh_full * duration_fraction
            ac_consumption_wh = ac_consumption_wh_full * duration_fraction
            dc_consumption_wh = dc_consumption_wh_full * duration_fraction

            # Simulate energy flow for this interval
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
            battery_config = {
                k[8:]: v for k, v in config.items() if k.startswith("battery_")
            }
            if battery_config:
                self.battery = Battery({**self.battery.get_config(), **battery_config})

        if any(key.startswith("pv_") for key in config):
            pv_config = {k[3:]: v for k, v in config.items() if k.startswith("pv_")}
            if pv_config:
                self.pv_system = PVSystem({**self.pv_system.get_config(), **pv_config})

        if any(key.startswith("ac_") for key in config):
            ac_config = {k[3:]: v for k, v in config.items() if k.startswith("ac_")}
            if ac_config:
                self.ac_consumer = ACConsumer(
                    {**self.ac_consumer.get_config(), **ac_config}
                )

        if any(key.startswith("dc_") for key in config):
            dc_config = {k[3:]: v for k, v in config.items() if k.startswith("dc_")}
            if dc_config:
                self.dc_consumer = DCConsumer(
                    {**self.dc_consumer.get_config(), **dc_config}
                )

        if any(key.startswith("charger_") for key in config):
            charger_config = {
                k[8:]: v for k, v in config.items() if k.startswith("charger_")
            }
            if charger_config:
                self.charger = Charger({**self.charger.get_config(), **charger_config})

        if any(key.startswith("inverter_") for key in config):
            inverter_config = {
                k[9:]: v for k, v in config.items() if k.startswith("inverter_")
            }
            if inverter_config:
                self.inverter = Inverter(
                    {**self.inverter.get_config(), **inverter_config}
                )

        # Recreate energy flow calculator with updated components
        self.energy_flow = EnergyFlowCalculator(
            self.battery, self.charger, self.inverter
        )

        # Clear cache since configuration changed
        self._last_calculation_time = None
        self._last_results = None

    def get_hourly_detailed_results(self) -> List[Dict[str, Any]]:
        """Get the detailed hourly results from the last calculation."""
        return (
            [result.copy() for result in self._last_hourly_details]
            if self._last_hourly_details
            else []
        )

    def _calculate_hourly_energy_balance(
        self, daily_forecasts: List[float], current_time: datetime, hours: int, initial_soc: float
    ) -> List[Dict[str, Any]]:
        """Calculate hourly energy balance for the forecast period."""
        hourly_details = []
        current_soc = initial_soc

        # Calculate remaining minutes in current hour for partial first interval
        current_minute = current_time.minute
        remaining_minutes_first_hour = 60 - current_minute

        for hour in range(hours):
            hour_start = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=hour
            )

            # Determine actual duration for this hour
            if hour == 0:
                # First hour is partial - only remaining minutes
                duration_minutes = remaining_minutes_first_hour
                duration_factor = duration_minutes / 60.0
            else:
                # All other hours are full 60-minute intervals
                duration_minutes = 60
                duration_factor = 1.0

            # Get full-hour forecasts and scale by duration
            pv_production_full_hour = self.pv_system.get_production_forecast(
                daily_forecasts, hour_start, 1, current_time
            )[0]

            ac_consumption_full_hour = self.ac_consumer.get_consumption_forecast(
                hour_start, 1
            )[0]

            dc_consumption_full_hour = self.dc_consumer.get_consumption_forecast(
                hour_start, 1
            )[0]

            # Scale by actual duration
            pv_production_wh = pv_production_full_hour * duration_factor
            ac_consumption_wh = ac_consumption_full_hour * duration_factor
            dc_consumption_wh = dc_consumption_full_hour * duration_factor

            # Simulate energy flow for this interval
            flows, new_soc = self.energy_flow.simulate_energy_flow(
                pv_production_wh, ac_consumption_wh, dc_consumption_wh, current_soc
            )

            hourly_details.append(
                {
                    "hour": hour,
                    "hour_start": hour_start,
                    "duration_minutes": duration_minutes,
                    "duration_factor": duration_factor,
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
                    "net_grid_wh": flows.get("grid_import_wh", 0.0)
                    - flows.get("grid_export_wh", 0.0),
                    "net_battery_wh": flows.get("battery_charge_wh", 0.0)
                    - flows.get("battery_discharge_wh", 0.0),
                }
            )
            
            # Update SOC for next iteration
            current_soc = new_soc

        return hourly_details

    def _get_relevant_min_soc(self, soc_forecast: List[float], current_soc_percent: float) -> float:
        """Get minimum SOC considering only values until target is reached.
        
        Uses the same logic as _calculate_threshold_from_forecast to ensure consistency.
        
        Args:
            soc_forecast: List of forecasted SOC values
            current_soc_percent: Current SOC for fallback
            
        Returns:
            Minimum SOC value considering target threshold logic
        """
        if not soc_forecast:
            return current_soc_percent
            
        # Use same target reaching logic as threshold calculation
        target_reached_index = None
        start_soc = soc_forecast[0]
        
        if start_soc >= self.target_soc_percent:
            # Battery already at/above target - find NEXT time it reaches target after dropping below
            below_target_seen = False
            for i, soc in enumerate(soc_forecast):
                if soc < self.target_soc_percent:
                    below_target_seen = True
                elif below_target_seen and soc >= self.target_soc_percent:
                    target_reached_index = i
                    break
        else:
            # Battery below target - find FIRST time it reaches target
            for i, soc in enumerate(soc_forecast):
                if soc >= self.target_soc_percent:
                    target_reached_index = i
                    break
        
        # Get relevant SOC values using same logic
        if target_reached_index is not None:
            relevant_soc_values = soc_forecast[:target_reached_index + 1]
        else:
            relevant_soc_values = soc_forecast
            
        return min(relevant_soc_values)

    def _calculate_additional_load_optimization(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
    ) -> Dict[str, Any]:
        """Calculate optimal additional load activation schedule using iterative hourly approach.

        The algorithm works as follows:
        1. For each hour, simulate WITHOUT additional load
        2. If SOC would reach 85% without additional load AND min SOC (20%) is maintained:
           a. Test simulation WITH additional load from this hour
           b. If min SOC is still maintained with additional load, activate it
           c. Continue with additional load active until deactivation condition
        3. Deactivation: If >50% of additional load power comes from battery

        Args:
            start_time: Start time for simulation
            hours: Number of hours to simulate
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for simulation
            reference_time: Reference time for day offset calculations

        Returns:
            Dictionary containing additional load schedule and updated forecast
        """
        # Configuration - use config parameters instead of magic numbers
        SOC_TARGET_THRESHOLD = self.target_soc_percent  # Use configured target SOC instead of hardcoded 85.0
        MIN_INVERTER_THRESHOLD = self.inverter.min_soc_percent  # Use configured inverter minimum
        BATTERY_CONTRIBUTION_THRESHOLD = 0.5  # 50%
        
        # Initialize
        additional_load_schedule = [False] * hours
        current_soc = initial_soc_percent
        current_time = start_time
        additional_load_active = False
        
        # Store hourly details for final result
        final_hourly_details = []
        
        # Iterative hourly simulation
        for hour in range(hours):
            # Calculate duration fraction for first hour
            if hour == 0:
                minutes_remaining = 60 - start_time.minute
                duration_fraction = minutes_remaining / 60.0
            else:
                duration_fraction = 1.0
                
            # Get energy values for this hour
            pv_production_wh_full = self.pv_system.calculate_hourly_production_wh(
                daily_forecasts, current_time, reference_time
            )
            dc_consumption_wh_full = self.dc_consumer.calculate_hourly_consumption_wh(
                current_time
            )
            
            # Scale by duration fraction
            pv_production_wh = pv_production_wh_full * duration_fraction
            dc_consumption_wh = dc_consumption_wh_full * duration_fraction
            
            # Decision logic for additional load
            if not additional_load_active:
                # Test WITHOUT additional load first
                self.ac_consumer.set_additional_load_active(False)
                ac_consumption_wh_without = (
                    self.ac_consumer.calculate_hourly_consumption_wh(current_time) * duration_fraction
                )
                
                # Simulate this hour without additional load
                flows_without, soc_without = self.energy_flow.simulate_energy_flow(
                    pv_production_wh, ac_consumption_wh_without, dc_consumption_wh, current_soc
                )
                
                # Check activation condition: Use only the comprehensive safety check
                # The safety check verifies both conditions:
                # 1. Target SOC would be reached even WITH additional load  
                # 2. Minimum SOC would never be violated WITH additional load
                safety_check_passed = self._verify_additional_load_safety(
                    current_time, hours - hour, daily_forecasts, current_soc, reference_time
                )
                
                if safety_check_passed:
                    # Test WITH additional load to see if it's safe
                    self.ac_consumer.set_additional_load_active(True)
                    ac_consumption_wh_with = (
                        self.ac_consumer.calculate_hourly_consumption_wh(current_time) * duration_fraction
                    )
                    
                    # Activate additional load
                    additional_load_active = True
                    additional_load_schedule[hour] = True
                    
                    # Use the simulation WITH additional load
                    flows, new_soc = self.energy_flow.simulate_energy_flow(
                        pv_production_wh, ac_consumption_wh_with, dc_consumption_wh, current_soc
                    )
                    ac_consumption_wh = ac_consumption_wh_with
                else:
                    # Keep additional load off
                    flows, new_soc = flows_without, soc_without
                    ac_consumption_wh = ac_consumption_wh_without
                    
            else:
                # Additional load is already active - check deactivation condition
                self.ac_consumer.set_additional_load_active(True)
                ac_consumption_wh_with = (
                    self.ac_consumer.calculate_hourly_consumption_wh(current_time) * duration_fraction
                )
                
                # Calculate how much additional load power comes from battery
                additional_load_power = self.ac_consumer.additional_load_w * duration_fraction
                available_pv_for_additional = max(0, pv_production_wh - dc_consumption_wh - 
                                                 (ac_consumption_wh_with - additional_load_power))
                battery_contribution = max(0, additional_load_power - available_pv_for_additional)
                battery_contribution_ratio = battery_contribution / additional_load_power if additional_load_power > 0 else 0
                
                # Deactivation condition: >50% from battery AND SOC target has been reached
                # Only deactivate if we have successfully reached the target SOC (85%)
                battery_contribution_too_high = battery_contribution_ratio > BATTERY_CONTRIBUTION_THRESHOLD
                soc_target_reached = current_soc >= SOC_TARGET_THRESHOLD
                
                if battery_contribution_too_high and soc_target_reached:
                    # Deactivate additional load - too much battery drain
                    additional_load_active = False
                    additional_load_schedule[hour] = False
                    
                    # Debug output
                    # if hour < 20:
                    #     print(f"  Deactivating additional load: battery_contribution={battery_contribution_ratio:.1%} > {BATTERY_CONTRIBUTION_THRESHOLD:.1%}")
                    
                    # Recalculate without additional load
                    self.ac_consumer.set_additional_load_active(False)
                    ac_consumption_wh = (
                        self.ac_consumer.calculate_hourly_consumption_wh(current_time) * duration_fraction
                    )
                    flows, new_soc = self.energy_flow.simulate_energy_flow(
                        pv_production_wh, ac_consumption_wh, dc_consumption_wh, current_soc
                    )
                else:
                    # Keep additional load active - mostly powered by PV/grid
                    additional_load_schedule[hour] = True
                    flows, new_soc = self.energy_flow.simulate_energy_flow(
                        pv_production_wh, ac_consumption_wh_with, dc_consumption_wh, current_soc
                    )
                    ac_consumption_wh = ac_consumption_wh_with
            
            # Store hourly detail
            duration_minutes = int(duration_fraction * 60)
            hourly_detail = {
                "hour": hour,
                "datetime": current_time.isoformat(),
                "duration_fraction": duration_fraction,
                "duration_minutes": duration_minutes,
                "initial_soc_percent": current_soc,
                "final_soc_percent": new_soc,
                "pv_production_wh": pv_production_wh,
                "ac_consumption_wh": ac_consumption_wh,
                "dc_consumption_wh": dc_consumption_wh,
                "additional_load_active": additional_load_schedule[hour],
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
            final_hourly_details.append(hourly_detail)
            
            # Update for next hour
            current_soc = new_soc
            if hour == 0:
                current_time = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                current_time += timedelta(hours=1)
        
        # Reset AC consumer state
        self.ac_consumer.set_additional_load_active(False)
        
        return {
            "additional_load_active": additional_load_schedule[0] if additional_load_schedule else False,
            "any_activation_planned": any(additional_load_schedule),
            "activation_start": start_time if any(additional_load_schedule) else None,
            "schedule": additional_load_schedule,
            "hourly_details": final_hourly_details,
        }

    def _verify_additional_load_safety(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
    ) -> bool:
        """Verify that activating additional load maintains minimum SOC until 85% target is reached.

        Args:
            start_time: Start time for verification
            hours: Number of hours to verify
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for verification
            reference_time: Reference time for day offset calculations

        Returns:
            True if additional load can be safely activated (SOC stays above 20% until 85% is reached)
        """
        MIN_INVERTER_THRESHOLD = self.inverter.min_soc_percent  # Use configured inverter minimum
        SOC_TARGET_THRESHOLD = self.target_soc_percent  # Use configured target SOC
        
        # Save current state
        original_state = self.ac_consumer.is_additional_load_active()
        
        try:
            # Simulate with additional load active
            self.ac_consumer.set_additional_load_active(True)
            soc_forecast = self._simulate_soc_progression(
                start_time, hours, daily_forecasts, initial_soc_percent, reference_time
            )
            
            if not soc_forecast:
                return False
            
            # Check SOC progression until target is reached or forecast ends
            for soc in soc_forecast:
                # Check if SOC goes below minimum threshold
                if soc < MIN_INVERTER_THRESHOLD:
                    return False
                
                # If we've reached the target SOC, safety check is complete
                if soc >= SOC_TARGET_THRESHOLD:
                    return True
            
            return True
            
        finally:
            # Restore original state
            self.ac_consumer.set_additional_load_active(original_state)

    def _test_additional_load_activation(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        reference_time: datetime,
    ) -> Dict[str, Any]:
        """Test if additional load can be activated from start_time.

        Args:
            start_time: Start time for activation test
            hours: Number of hours to test
            daily_forecasts: Daily PV forecasts in kWh
            reference_time: Reference time for day offset calculations

        Returns:
            Dictionary with test results
        """
        # Get current SOC at start_time (we already have this from main calculation)
        initial_soc = self.battery.current_soc_percent
        
        # Activate additional load for testing
        self.ac_consumer.set_additional_load_active(True)
        
        # Run simulation with additional load
        test_forecast = self._simulate_soc_progression(
            start_time, hours, daily_forecasts, initial_soc, reference_time
        )
        test_details = self._last_hourly_details.copy()
        
        # Reset additional load
        self.ac_consumer.set_additional_load_active(False)
        
        # Check conditions:
        # 1. Target SOC (85%) is reached within the forecast period
        # 2. Minimum inverter threshold (20%) is never violated
        target_reached = any(soc >= self.target_soc_percent for soc in test_forecast)
        min_soc_ok = all(soc >= self.inverter.min_soc_percent for soc in test_forecast)
        
        if not (target_reached and min_soc_ok):
            return {"can_activate": False, "active_hours": 0}
        
        # Find when to deactivate: when more than half of additional load power comes from battery
        additional_load_wh = self.ac_consumer.additional_load_w
        deactivation_hour = hours  # Default: active until end
        
        for hour_idx, detail in enumerate(test_details):
            battery_discharge_wh = detail.get("battery_discharge_wh", 0.0)
            duration_fraction = detail.get("duration_fraction", 1.0)
            
            # Calculate additional load energy for this hour
            additional_load_energy_wh = additional_load_wh * duration_fraction
            
            # Check if more than half of additional load energy comes from battery
            # This is a simplified check - in reality we'd need to determine what portion 
            # of battery discharge is attributable to the additional load
            if battery_discharge_wh > (additional_load_energy_wh / 2):
                deactivation_hour = hour_idx
                break
        
        return {
            "can_activate": True,
            "active_hours": deactivation_hour,
        }

    def _simulate_with_additional_load_schedule(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
        schedule: List[bool],
    ) -> List[Dict[str, Any]]:
        """Simulate SOC progression with given additional load schedule.

        Args:
            start_time: Start time for simulation
            hours: Number of hours to simulate
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for simulation
            reference_time: Reference time for day offset calculations
            schedule: List of booleans indicating when additional load is active

        Returns:
            List of hourly details with additional load status
        """
        soc_forecast = []
        current_soc = initial_soc_percent
        current_time = start_time
        hourly_details = []

        for hour in range(hours):
            # Set additional load status for this hour
            additional_load_active = schedule[hour] if hour < len(schedule) else False
            self.ac_consumer.set_additional_load_active(additional_load_active)
            
            # Calculate duration multiplier for partial hours
            if hour == 0:
                minutes_remaining = 60 - start_time.minute
                duration_fraction = minutes_remaining / 60.0
            else:
                duration_fraction = 1.0

            # Get hourly values
            pv_production_wh_full = self.pv_system.calculate_hourly_production_wh(
                daily_forecasts, current_time, reference_time
            )
            ac_consumption_wh_full = self.ac_consumer.calculate_hourly_consumption_wh(
                current_time
            )
            dc_consumption_wh_full = self.dc_consumer.calculate_hourly_consumption_wh(
                current_time
            )

            # Scale energy values by duration fraction for partial hours
            pv_production_wh = pv_production_wh_full * duration_fraction
            ac_consumption_wh = ac_consumption_wh_full * duration_fraction
            dc_consumption_wh = dc_consumption_wh_full * duration_fraction

            # Simulate energy flow for this interval
            flows, new_soc = self.energy_flow.simulate_energy_flow(
                pv_production_wh, ac_consumption_wh, dc_consumption_wh, current_soc
            )

            # Store detailed hourly data including additional load status
            duration_minutes = int(duration_fraction * 60)
            hourly_detail = {
                "hour": hour,
                "datetime": current_time.isoformat(),
                "duration_fraction": duration_fraction,
                "duration_minutes": duration_minutes,
                "initial_soc_percent": current_soc,
                "final_soc_percent": new_soc,
                "pv_production_wh": pv_production_wh,
                "ac_consumption_wh": ac_consumption_wh,
                "dc_consumption_wh": dc_consumption_wh,
                "additional_load_active": additional_load_active,
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
                "net_grid_wh": flows.get("grid_import_wh", 0.0)
                - flows.get("grid_export_wh", 0.0),
                "net_battery_wh": flows.get("battery_charge_wh", 0.0)
                - flows.get("battery_discharge_wh", 0.0),
            }
            hourly_details.append(hourly_detail)

            current_soc = new_soc
            soc_forecast.append(current_soc)

            # Move to next hour
            if hour == 0:
                current_time = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                current_time += timedelta(hours=1)

        # Reset additional load state
        self.ac_consumer.set_additional_load_active(False)
        return hourly_details

    def _project_soc_without_additional_load(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
    ) -> List[float]:
        """Project SOC development without additional load for lookahead.

        Args:
            start_time: Start time for projection
            hours: Number of hours to project
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for projection
            reference_time: Reference time for day offset calculations

        Returns:
            List of projected SOC values
        """
        # Save current state
        original_state = self.ac_consumer.is_additional_load_active()
        
        try:
            # Ensure additional load is OFF for this projection
            self.ac_consumer.set_additional_load_active(False)
            soc_forecast = self._simulate_soc_progression(
                start_time, hours, daily_forecasts, initial_soc_percent, reference_time
            )
            return soc_forecast
            
        finally:
            # Restore original state
            self.ac_consumer.set_additional_load_active(original_state)

    def _project_soc_with_additional_load(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
    ) -> List[float]:
        """Project SOC development with additional load active for lookahead.

        Args:
            start_time: Start time for projection
            hours: Number of hours to project
            daily_forecasts: Daily PV forecasts in kWh
            initial_soc_percent: Initial SOC for projection
            reference_time: Reference time for day offset calculations

        Returns:
            List of projected SOC values with additional load active
        """
        # Save current state
        original_state = self.ac_consumer.is_additional_load_active()
        
        try:
            # Ensure additional load is ON for this projection
            self.ac_consumer.set_additional_load_active(True)
            soc_forecast = self._simulate_soc_progression(
                start_time, hours, daily_forecasts, initial_soc_percent, reference_time
            )
            return soc_forecast
            
        finally:
            # Restore original state
            self.ac_consumer.set_additional_load_active(original_state)
