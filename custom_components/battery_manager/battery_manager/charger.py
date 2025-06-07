"""Charger module for the Battery Manager system."""

from typing import Dict, Any


class Charger:
    """Represents a charger that converts AC energy to DC energy."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the charger with configuration parameters.
        
        Args:
            config: Dictionary containing charger configuration
        """
        self.max_power_w = config.get("max_power_w", 2300.0)
        self.efficiency = config.get("efficiency", 0.92)
        self.standby_power_w = config.get("standby_power_w", 10.0)
        
        # Validate configuration
        self._validate_config()
    
    def _validate_config(self) -> None:
        """Validate charger configuration parameters."""
        if self.max_power_w <= 0:
            raise ValueError("Max power must be positive")
        if not 0 < self.efficiency <= 1:
            raise ValueError("Efficiency must be between 0 and 1")
        if self.standby_power_w < 0:
            raise ValueError("Standby power must be non-negative")
    
    def convert_ac_to_dc(self, ac_energy_wh: float) -> float:
        """Convert AC energy to DC energy (e.g., from PV surplus).
        
        Args:
            ac_energy_wh: AC energy input in Wh
            
        Returns:
            DC energy output in Wh (after efficiency losses)
        """
        if ac_energy_wh <= 0:
            return 0.0
        
        # Limit by maximum power (assuming 1-hour operation)
        limited_ac_energy_wh = min(ac_energy_wh, self.max_power_w)
        
        # Apply efficiency
        dc_energy_wh = limited_ac_energy_wh * self.efficiency
        
        return dc_energy_wh
    
    def provide_dc_from_ac(self, dc_energy_needed_wh: float) -> float:
        """Provide DC energy by drawing from AC grid (DC emergency operation).
        
        Args:
            dc_energy_needed_wh: Required DC energy in Wh
            
        Returns:
            AC energy consumed from grid in Wh (including efficiency losses)
        """
        if dc_energy_needed_wh <= 0:
            return 0.0
        
        # Limit by maximum power (assuming 1-hour operation)
        limited_dc_energy_wh = min(dc_energy_needed_wh, self.max_power_w * self.efficiency)
        
        # Calculate required AC energy (inverse of efficiency)
        ac_energy_wh = limited_dc_energy_wh / self.efficiency
        
        return ac_energy_wh
    
    def get_standby_consumption_wh(self, active: bool) -> float:
        """Get standby power consumption for the charger.
        
        Args:
            active: Whether the charger is actively charging/converting
            
        Returns:
            Standby consumption in Wh (for 1 hour)
        """
        if active:
            return self.standby_power_w
        else:
            return 0.0
    
    def get_max_dc_output_wh(self) -> float:
        """Get maximum DC output for 1 hour of operation.
        
        Returns:
            Maximum DC output in Wh
        """
        return self.max_power_w * self.efficiency
    
    def get_max_ac_input_wh(self) -> float:
        """Get maximum AC input for 1 hour of operation.
        
        Returns:
            Maximum AC input in Wh
        """
        return self.max_power_w
    
    def simulate_ac_to_dc_conversion(self, ac_energy_wh: float) -> tuple[float, float]:
        """Simulate AC to DC conversion without performing it.
        
        Args:
            ac_energy_wh: AC energy input in Wh
            
        Returns:
            Tuple of (dc_energy_output_wh, actual_ac_energy_consumed_wh)
        """
        if ac_energy_wh <= 0:
            return 0.0, 0.0
        
        # Limit by maximum power
        actual_ac_energy_wh = min(ac_energy_wh, self.max_power_w)
        
        # Calculate DC output
        dc_energy_wh = actual_ac_energy_wh * self.efficiency
        
        return dc_energy_wh, actual_ac_energy_wh
    
    def simulate_dc_from_ac_provision(self, dc_energy_needed_wh: float) -> tuple[float, float]:
        """Simulate providing DC energy from AC without performing it.
        
        Args:
            dc_energy_needed_wh: Required DC energy in Wh
            
        Returns:
            Tuple of (actual_dc_energy_provided_wh, ac_energy_consumed_wh)
        """
        if dc_energy_needed_wh <= 0:
            return 0.0, 0.0
        
        # Limit by maximum power
        max_dc_output_wh = self.max_power_w * self.efficiency
        actual_dc_energy_wh = min(dc_energy_needed_wh, max_dc_output_wh)
        
        # Calculate required AC energy
        ac_energy_wh = actual_dc_energy_wh / self.efficiency
        
        return actual_dc_energy_wh, ac_energy_wh
    
    def get_config(self) -> Dict[str, Any]:
        """Get current charger configuration."""
        return {
            "max_power_w": self.max_power_w,
            "efficiency": self.efficiency,
            "standby_power_w": self.standby_power_w,
        }
