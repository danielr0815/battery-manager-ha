"""Inverter module for the Battery Manager system."""

from typing import Any, Dict


class Inverter:
    """Represents an inverter that converts DC energy to AC energy."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize the inverter with configuration parameters.

        Args:
            config: Dictionary containing inverter configuration
        """
        self.max_power_w = config.get("max_power_w", 2300.0)
        self.efficiency = config.get("efficiency", 0.95)
        self.standby_power_w = config.get("standby_power_w", 15.0)
        self.min_soc_percent = config.get("min_soc_percent", 20.0)

        # Internal state
        self._enabled = True
        self._current_soc_percent = 50.0  # Will be updated externally

        # Validate configuration
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate inverter configuration parameters."""
        if self.max_power_w <= 0:
            raise ValueError("Max power must be positive")
        if not 0 < self.efficiency <= 1:
            raise ValueError("Efficiency must be between 0 and 1")
        if self.standby_power_w < 0:
            raise ValueError("Standby power must be non-negative")
        if not 0 <= self.min_soc_percent <= 100:
            raise ValueError("Min SOC must be between 0 and 100%")

    def update_soc(self, soc_percent: float) -> None:
        """Update the current SOC and check if inverter should be enabled/disabled.

        Args:
            soc_percent: Current battery SOC in percent
        """
        self._current_soc_percent = soc_percent

        # Automatically disable inverter if SOC falls below minimum threshold
        if soc_percent < self.min_soc_percent:
            self._enabled = False
        # Re-enable inverter if SOC is at or above minimum threshold and was disabled due to low SOC
        elif soc_percent >= self.min_soc_percent:
            self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the inverter.

        Args:
            enabled: Whether to enable the inverter
        """
        # Can only enable if SOC is above minimum threshold
        if enabled and self._current_soc_percent <= self.min_soc_percent:
            self._enabled = False
        else:
            self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        """Check if the inverter is currently enabled."""
        # Inverter is enabled if explicitly enabled AND SOC is above minimum threshold
        return self._enabled and self._current_soc_percent > self.min_soc_percent

    def provide_ac_from_dc(self, ac_energy_needed_wh: float) -> float:
        """Provide AC energy by converting from DC (battery).

        Args:
            ac_energy_needed_wh: Required AC energy in Wh

        Returns:
            DC energy output needed (before battery efficiency losses) in Wh
        """
        if not self.is_enabled or ac_energy_needed_wh <= 0:
            return 0.0

        # Limit by maximum power (assuming 1-hour operation)
        limited_ac_energy_wh = min(ac_energy_needed_wh, self.max_power_w)

        # Calculate required DC input for this AC output (inverse of efficiency)
        dc_energy_needed_wh = limited_ac_energy_wh / self.efficiency

        # Return the DC energy output that needs to be provided (before battery losses)
        return dc_energy_needed_wh

    def get_standby_consumption_wh(self) -> float:
        """Get standby power consumption for the inverter.

        Returns:
            Standby consumption in Wh (for 1 hour) if enabled, 0 if disabled
        """
        if self.is_enabled:
            return self.standby_power_w
        else:
            return 0.0

    def get_max_ac_output_wh(self) -> float:
        """Get maximum AC output for 1 hour of operation.

        Returns:
            Maximum AC output in Wh if enabled, 0 if disabled
        """
        if self.is_enabled:
            return self.max_power_w
        else:
            return 0.0

    def get_max_dc_input_wh(self) -> float:
        """Get maximum DC input for 1 hour of operation.

        Returns:
            Maximum DC input in Wh if enabled, 0 if disabled
        """
        if self.is_enabled:
            return self.max_power_w / self.efficiency
        else:
            return 0.0

    def simulate_ac_provision(self, ac_energy_needed_wh: float) -> tuple[float, float]:
        """Simulate providing AC energy without performing the conversion.

        Args:
            ac_energy_needed_wh: Required AC energy in Wh

        Returns:
            Tuple of (actual_ac_energy_provided_wh, dc_energy_consumed_wh)
        """
        if not self.is_enabled or ac_energy_needed_wh <= 0:
            return 0.0, 0.0

        # Limit by maximum power
        actual_ac_energy_wh = min(ac_energy_needed_wh, self.max_power_w)

        # Calculate required DC energy
        dc_energy_wh = actual_ac_energy_wh / self.efficiency

        return actual_ac_energy_wh, dc_energy_wh

    def check_soc_threshold(self, threshold_soc_percent: float) -> bool:
        """Check if the current SOC is above a specific threshold.

        Args:
            threshold_soc_percent: SOC threshold in percent

        Returns:
            True if SOC is above threshold, False otherwise
        """
        return self._current_soc_percent > threshold_soc_percent

    def get_config(self) -> Dict[str, Any]:
        """Get current inverter configuration."""
        return {
            "max_power_w": self.max_power_w,
            "efficiency": self.efficiency,
            "standby_power_w": self.standby_power_w,
            "min_soc_percent": self.min_soc_percent,
            "enabled": self._enabled,
            "current_soc_percent": self._current_soc_percent,
        }
