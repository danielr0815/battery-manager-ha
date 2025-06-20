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

        # Run simulation
        soc_forecast = self._simulate_soc_progression(
            forecast_start,
            forecast_hours,
            daily_forecasts,
            current_soc_percent,
            current_time,
        )

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
        *,
        extra_ac_load_w: float = 0.0,
        store_details: bool = True,
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

        if store_details:
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
            ac_consumption_wh_full = (
                self.ac_consumer.calculate_hourly_consumption_wh(current_time)
                + extra_ac_load_w
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

            if store_details:
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
        *,
        extra_ac_load_w: float = 0.0,
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
            ac_consumption_wh_full = (
                self.ac_consumer.calculate_hourly_consumption_wh(current_time)
                + extra_ac_load_w
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

    def simulate_soc_with_extra_load(
        self,
        start_time: datetime,
        hours: int,
        daily_forecasts: List[float],
        initial_soc_percent: float,
        reference_time: datetime,
        extra_ac_load_w: float,
    ) -> List[float]:
        """Public wrapper to simulate SOC progression with additional AC load."""

        return self._simulate_soc_progression(
            start_time,
            hours,
            daily_forecasts,
            initial_soc_percent,
            reference_time,
            extra_ac_load_w=extra_ac_load_w,
            store_details=False,
        )

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
