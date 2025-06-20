"""Simulator module for the Battery Manager system."""

from datetime import datetime
from typing import Any, Dict, List

from .controller import MaximumBasedController


class BatteryManagerSimulator:
    """Main simulator for the Battery Manager system."""

    def __init__(self, config: Dict[str, Any] = None):
        """Initialize the simulator.

        Args:
            config: Configuration dictionary (uses defaults if None)
        """
        if config is None:
            config = {}

        # Validate configuration
        self._validate_config(config)

        self.controller = MaximumBasedController(config)
        self.config = config

    def _validate_config(self, config: Dict[str, Any]) -> None:
        """Validate configuration parameters.

        Args:
            config: Configuration dictionary

        Raises:
            ValueError: If configuration is invalid
        """
        # Battery validation
        if "battery_capacity_wh" in config:
            if config["battery_capacity_wh"] <= 0:
                raise ValueError(
                    f"Battery capacity must be positive, got {config['battery_capacity_wh']}"
                )

        if "battery_soc_min_percent" in config:
            min_soc = config["battery_soc_min_percent"]
            if not 0 <= min_soc <= 100:
                raise ValueError(
                    f"Battery min SOC must be between 0-100%, got {min_soc}"
                )

        if "battery_soc_max_percent" in config:
            max_soc = config["battery_soc_max_percent"]
            if not 0 <= max_soc <= 100:
                raise ValueError(
                    f"Battery max SOC must be between 0-100%, got {max_soc}"
                )

        # Check min <= max SOC
        if "battery_soc_min_percent" in config and "battery_soc_max_percent" in config:
            min_soc = config["battery_soc_min_percent"]
            max_soc = config["battery_soc_max_percent"]
            if min_soc >= max_soc:
                raise ValueError(
                    f"Battery min SOC ({min_soc}%) must be less than max SOC ({max_soc}%)"
                )

        # Efficiency validation
        efficiency_params = [
            "battery_charge_efficiency",
            "battery_discharge_efficiency",
            "charger_efficiency",
            "inverter_efficiency",
        ]
        for param in efficiency_params:
            if param in config:
                eff = config[param]
                if not 0 < eff <= 1.0:
                    raise ValueError(f"{param} must be between 0 and 1, got {eff}")

        # Power validation
        power_params = [
            "pv_peak_power_w",
            "ac_base_load_w",
            "dc_base_load_w",
            "extra_load_w",
        ]
        for param in power_params:
            if param in config:
                power = config[param]
                if power < 0:
                    raise ValueError(f"{param} cannot be negative, got {power}")

        # Threshold validation
        if "controller_max_threshold_percent" in config:
            threshold = config["controller_max_threshold_percent"]
            if not 0 <= threshold <= 100:
                raise ValueError(
                    f"Controller max threshold must be between 0-100%, got {threshold}"
                )

    def run_simulation(
        self,
        current_soc_percent: float,
        daily_forecasts: List[float],
        current_time: datetime = None,
    ) -> Dict[str, Any]:
        """Run a complete simulation and return results.

        Args:
            current_soc_percent: Current battery SOC in percent
            daily_forecasts: List of daily PV forecasts [today, tomorrow, day_after] in kWh
            current_time: Current time (defaults to now)

        Returns:
            Dictionary containing all simulation results
        """
        if current_time is None:
            current_time = datetime.now()

        # Validate inputs
        self._validate_inputs(current_soc_percent, daily_forecasts)

        # Run controller calculation
        results = self.controller.calculate_soc_threshold(
            current_soc_percent, daily_forecasts, current_time
        )

        # Second simulation with extra load
        extra_load_w = self.config.get("extra_load_w", 400.0)
        forecast_hours = results.get("forecast_hours", 0)
        soc_forecast_extra = self.controller.simulate_soc_with_extra_load(
            current_time,
            forecast_hours,
            daily_forecasts,
            current_soc_percent,
            current_time,
            extra_load_w,
        )

        target_soc = self.controller.target_soc_percent
        inverter_min = self.controller.inverter.min_soc_percent
        extra_active = False
        if soc_forecast_extra:
            start_soc = soc_forecast_extra[0]
            target_idx = None
            if start_soc >= target_soc:
                below = False
                for i, soc in enumerate(soc_forecast_extra):
                    if soc < target_soc:
                        below = True
                    elif below and soc >= target_soc:
                        target_idx = i
                        break
            else:
                for i, soc in enumerate(soc_forecast_extra):
                    if soc >= target_soc:
                        target_idx = i
                        break
            if target_idx is not None:
                if min(soc_forecast_extra[: target_idx + 1]) >= inverter_min:
                    extra_active = True

        results["extra_load"] = extra_active
        for detail in self.controller._last_hourly_details:
            detail["extra_load"] = extra_active

        # Add metadata
        results.update(
            {
                "simulation_time": current_time,
                "input_soc_percent": current_soc_percent,
                "input_forecasts_kwh": daily_forecasts.copy(),
                "config": self._get_current_config(),
            }
        )

        return results

    def _validate_inputs(
        self, current_soc_percent: float, daily_forecasts: List[float]
    ) -> None:
        """Validate simulation inputs.

        Args:
            current_soc_percent: Current SOC in percent
            daily_forecasts: Daily forecasts list

        Raises:
            ValueError: If inputs are invalid
        """
        if not 0 <= current_soc_percent <= 100:
            raise ValueError(
                f"SOC must be between 0 and 100%, got {current_soc_percent}"
            )

        if len(daily_forecasts) != 3:
            raise ValueError(f"Expected 3 daily forecasts, got {len(daily_forecasts)}")

        # Clamp negative forecasts to 0 (be forgiving with forecast data)
        for i in range(len(daily_forecasts)):
            if daily_forecasts[i] < 0:
                daily_forecasts[i] = 0.0

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """Update simulator configuration.

        Args:
            new_config: New configuration parameters
        """
        self.config.update(new_config)
        self.controller.update_config(self.config)

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self.config.copy()

    def _get_current_config(self) -> Dict[str, Any]:
        """Get current configuration from all components."""
        return {
            "battery": self.controller.battery.get_config(),
            "pv_system": self.controller.pv_system.get_config(),
            "ac_consumer": self.controller.ac_consumer.get_config(),
            "dc_consumer": self.controller.dc_consumer.get_config(),
            "charger": self.controller.charger.get_config(),
            "inverter": self.controller.inverter.get_config(),
            "target_soc_percent": self.controller.target_soc_percent,
        }

    def get_component_status(self) -> Dict[str, Any]:
        """Get status of all system components.

        Returns:
            Dictionary with component statuses
        """
        return {
            "battery_soc_percent": self.controller.battery.current_soc_percent,
            "battery_energy_wh": self.controller.battery.current_energy_wh,
            "battery_max_charge_wh": self.controller.battery.get_max_charge_energy_wh(),
            "battery_max_discharge_wh": self.controller.battery.get_max_discharge_energy_wh(),
            "inverter_enabled": self.controller.inverter.is_enabled,
            "inverter_max_output_wh": self.controller.inverter.get_max_ac_output_wh(),
            "charger_max_output_wh": self.controller.charger.get_max_dc_output_wh(),
        }

    def simulate_hour(
        self,
        pv_production_wh: float,
        ac_consumption_wh: float,
        dc_consumption_wh: float,
    ) -> Dict[str, float]:
        """Simulate one hour of operation.

        Args:
            pv_production_wh: PV production for the hour in Wh
            ac_consumption_wh: AC consumption for the hour in Wh
            dc_consumption_wh: DC consumption for the hour in Wh

        Returns:
            Dictionary with energy flows for the hour
        """
        return self.controller.energy_flow.calculate_energy_flow(
            pv_production_wh, ac_consumption_wh, dc_consumption_wh
        )

    def get_hourly_forecast(
        self,
        daily_forecasts: List[float],
        start_time: datetime,
        hours: int,
        reference_time: datetime = None,
    ) -> Dict[str, List[float]]:
        """Get detailed hourly forecast for analysis.

        Args:
            daily_forecasts: Daily PV forecasts in kWh
            start_time: Start time for forecast
            hours: Number of hours to forecast
            reference_time: Reference time for day offset calculation

        Returns:
            Dictionary with hourly forecasts
        """
        pv_forecast = self.controller.pv_system.get_production_forecast(
            daily_forecasts, start_time, hours, reference_time
        )

        ac_forecast = self.controller.ac_consumer.get_consumption_forecast(
            start_time, hours
        )

        dc_forecast = self.controller.dc_consumer.get_consumption_forecast(
            start_time, hours
        )

        return {
            "pv_production_wh": pv_forecast,
            "ac_consumption_wh": ac_forecast,
            "dc_consumption_wh": dc_forecast,
        }
