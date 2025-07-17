"""Battery module for the Battery Manager system."""

from typing import Any, Dict


class Battery:
    """Represents a battery storage system."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize the battery with configuration parameters.

        Args:
            config: Dictionary containing battery configuration
        """
        self.capacity_wh = config.get("capacity_wh", 5000.0)
        self.min_soc_percent = config.get("min_soc_percent", 5.0)
        self.max_soc_percent = config.get("max_soc_percent", 95.0)
        self.charge_efficiency = config.get("charge_efficiency", 0.97)
        self.discharge_efficiency = config.get("discharge_efficiency", 0.97)

        # Current state
        self._current_soc_percent = config.get("initial_soc_percent", 50.0)

        # Validate configuration
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate battery configuration parameters."""
        if self.capacity_wh <= 0:
            raise ValueError("Battery capacity must be positive")
        if not 0 <= self.min_soc_percent <= 100:
            raise ValueError("Min SOC must be between 0 and 100%")
        if not 0 <= self.max_soc_percent <= 100:
            raise ValueError("Max SOC must be between 0 and 100%")
        if self.min_soc_percent >= self.max_soc_percent:
            raise ValueError("Min SOC must be less than max SOC")
        if not 0 < self.charge_efficiency <= 1:
            raise ValueError("Charge efficiency must be between 0 and 1")
        if not 0 < self.discharge_efficiency <= 1:
            raise ValueError("Discharge efficiency must be between 0 and 1")

    @property
    def current_soc_percent(self) -> float:
        """Get current state of charge in percent."""
        return self._current_soc_percent

    @current_soc_percent.setter
    def current_soc_percent(self, value: float) -> None:
        """Set current state of charge in percent."""
        self._current_soc_percent = max(0, min(100, value))

    @property
    def current_energy_wh(self) -> float:
        """Get current stored energy in Wh."""
        return (self._current_soc_percent / 100.0) * self.capacity_wh

    @property
    def usable_capacity_wh(self) -> float:
        """Get usable capacity between min and max SOC."""
        return (
            (self.max_soc_percent - self.min_soc_percent) / 100.0
        ) * self.capacity_wh

    def get_max_charge_energy_wh(self) -> float:
        """Get maximum energy that can be charged until max SOC is reached.

        Returns:
            Maximum chargeable energy in Wh
        """
        max_energy_wh = (self.max_soc_percent / 100.0) * self.capacity_wh
        current_energy_wh = self.current_energy_wh
        return max(0, max_energy_wh - current_energy_wh)

    def get_max_discharge_energy_wh(self) -> float:
        """Get maximum energy that can be discharged until min SOC is reached.

        Returns:
            Maximum dischargeable energy in Wh
        """
        min_energy_wh = (self.min_soc_percent / 100.0) * self.capacity_wh
        current_energy_wh = self.current_energy_wh
        return max(0, current_energy_wh - min_energy_wh)

    def charge_discharge(self, requested_energy_wh: float) -> float:
        """Charge or discharge the battery with requested energy.

        Args:
            requested_energy_wh: Positive for charging, negative for discharging

        Returns:
            Actually transferred energy in Wh (with efficiency losses)
        """
        if requested_energy_wh > 0:
            # Charging
            max_charge_wh = self.get_max_charge_energy_wh()
            actual_charge_wh = min(
                requested_energy_wh * self.charge_efficiency, max_charge_wh
            )

            # Update SOC
            new_energy_wh = self.current_energy_wh + actual_charge_wh
            self._current_soc_percent = (new_energy_wh / self.capacity_wh) * 100.0

            return actual_charge_wh

        elif requested_energy_wh < 0:
            # Discharging (requested_energy_wh is negative)
            requested_output_wh = abs(
                requested_energy_wh
            )  # This is the desired output energy

            # Calculate how much energy needs to be taken from battery to deliver requested output
            required_battery_wh = requested_output_wh / self.discharge_efficiency

            # Limit by maximum discharge capacity
            max_discharge_wh = self.get_max_discharge_energy_wh()
            actual_battery_wh = min(required_battery_wh, max_discharge_wh)

            # Calculate actual delivered energy based on what can be taken from battery
            delivered_energy_wh = actual_battery_wh * self.discharge_efficiency

            # Update SOC based on energy actually taken from battery
            new_energy_wh = self.current_energy_wh - actual_battery_wh
            self._current_soc_percent = (new_energy_wh / self.capacity_wh) * 100.0

            return -delivered_energy_wh  # Return negative for discharge

        else:
            # No energy transfer requested
            return 0.0

    def simulate_charge_discharge(
        self, requested_energy_wh: float
    ) -> tuple[float, float]:
        """Simulate charge/discharge without changing the battery state.

        Args:
            requested_energy_wh: Positive for charging, negative for discharging

        Returns:
            Tuple of (actually_transferred_energy_wh, resulting_soc_percent)
        """
        if requested_energy_wh > 0:
            # Charging simulation
            max_charge_wh = self.get_max_charge_energy_wh()
            actual_charge_wh = min(
                requested_energy_wh * self.charge_efficiency, max_charge_wh
            )

            new_energy_wh = self.current_energy_wh + actual_charge_wh
            new_soc_percent = (new_energy_wh / self.capacity_wh) * 100.0

            return actual_charge_wh, new_soc_percent

        elif requested_energy_wh < 0:
            # Discharging simulation
            max_discharge_wh = self.get_max_discharge_energy_wh()
            requested_discharge_wh = abs(requested_energy_wh)
            actual_discharge_wh = min(requested_discharge_wh, max_discharge_wh)

            delivered_energy_wh = actual_discharge_wh * self.discharge_efficiency

            new_energy_wh = self.current_energy_wh - actual_discharge_wh
            new_soc_percent = (new_energy_wh / self.capacity_wh) * 100.0

            return -delivered_energy_wh, new_soc_percent

        else:
            return 0.0, self._current_soc_percent

    def get_config(self) -> Dict[str, Any]:
        """Get current battery configuration."""
        return {
            "capacity_wh": self.capacity_wh,
            "min_soc_percent": self.min_soc_percent,
            "max_soc_percent": self.max_soc_percent,
            "charge_efficiency": self.charge_efficiency,
            "discharge_efficiency": self.discharge_efficiency,
            "current_soc_percent": self._current_soc_percent,
        }
