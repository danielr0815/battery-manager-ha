"""Consumer modules (AC and DC) for the Battery Manager system."""

from typing import Dict, Any
from datetime import datetime


class BaseConsumer:
    """Base class for energy consumers."""
    
    def __init__(self, config: Dict[str, Any], consumer_type: str):
        """Initialize the consumer with configuration parameters.
        
        Args:
            config: Dictionary containing consumer configuration
            consumer_type: Type of consumer ('ac' or 'dc')
        """
        self.consumer_type = consumer_type
        self.base_load_w = config.get("base_load_w", 50.0)
        self.variable_load_w = config.get("variable_load_w", 25.0)
        self.variable_start_hour = config.get("variable_start_hour", 6)
        self.variable_end_hour = config.get("variable_end_hour", 22)
        
        # Validate configuration
        self._validate_config()
    
    def _validate_config(self) -> None:
        """Validate consumer configuration parameters."""
        if self.base_load_w < 0:
            raise ValueError("Base load must be non-negative")
        if self.variable_load_w < 0:
            raise ValueError("Variable load must be non-negative")
        if not 0 <= self.variable_start_hour <= 23:
            raise ValueError("Variable start hour must be between 0 and 23")
        if not 0 <= self.variable_end_hour <= 23:
            raise ValueError("Variable end hour must be between 0 and 23")
        if self.variable_start_hour >= self.variable_end_hour:
            raise ValueError("Variable start hour must be before end hour")
    
    def calculate_hourly_consumption_wh(self, target_datetime: datetime) -> float:
        """Calculate hourly consumption for a specific datetime.
        
        Args:
            target_datetime: The datetime for which to calculate consumption
            
        Returns:
            Hourly consumption in Wh
        """
        hour = target_datetime.hour
        
        # Base load is always present
        consumption_w = self.base_load_w
        
        # Add variable load if within active hours
        if self.variable_start_hour <= hour < self.variable_end_hour:
            consumption_w += self.variable_load_w
        
        # Convert to Wh (power for 1 hour)
        return consumption_w
    
    def get_consumption_forecast(
        self, 
        start_datetime: datetime, 
        hours: int
    ) -> list[float]:
        """Get hourly consumption forecast for a specified time range.
        
        Args:
            start_datetime: Start datetime for the forecast
            hours: Number of hours to forecast
            
        Returns:
            List of hourly consumption values in Wh
        """
        forecast = []
        current_datetime = start_datetime
        
        for _ in range(hours):
            consumption_wh = self.calculate_hourly_consumption_wh(current_datetime)
            forecast.append(consumption_wh)
            current_datetime = current_datetime.replace(
                hour=(current_datetime.hour + 1) % 24
            )
            if current_datetime.hour == 0:
                current_datetime = current_datetime.replace(
                    day=current_datetime.day + 1
                )
        
        return forecast
    
    def get_config(self) -> Dict[str, Any]:
        """Get current consumer configuration."""
        return {
            "base_load_w": self.base_load_w,
            "variable_load_w": self.variable_load_w,
            "variable_start_hour": self.variable_start_hour,
            "variable_end_hour": self.variable_end_hour,
        }


class ACConsumer(BaseConsumer):
    """AC (Alternating Current) consumer."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize AC consumer.
        
        Args:
            config: Dictionary containing AC consumer configuration
        """
        # Use AC-specific defaults if not provided
        ac_config = {
            "base_load_w": config.get("base_load_w", 50.0),
            "variable_load_w": config.get("variable_load_w", 75.0),
            "variable_start_hour": config.get("variable_start_hour", 6),
            "variable_end_hour": config.get("variable_end_hour", 20),
        }
        super().__init__(ac_config, "ac")


class DCConsumer(BaseConsumer):
    """DC (Direct Current) consumer."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize DC consumer.
        
        Args:
            config: Dictionary containing DC consumer configuration
        """
        # Use DC-specific defaults if not provided
        dc_config = {
            "base_load_w": config.get("base_load_w", 50.0),
            "variable_load_w": config.get("variable_load_w", 25.0),
            "variable_start_hour": config.get("variable_start_hour", 6),
            "variable_end_hour": config.get("variable_end_hour", 22),
        }
        super().__init__(dc_config, "dc")
