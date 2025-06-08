"""PV System module for the Battery Manager system."""

from typing import Dict, Any, List
from datetime import datetime, timedelta


class PVSystem:
    """Represents a photovoltaic system with forecasting capabilities."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the PV system with configuration parameters.
        
        Args:
            config: Dictionary containing PV system configuration
        """
        self.max_power_w = config.get("max_power_w", 3200.0)
        self.morning_start_hour = config.get("morning_start_hour", 7)
        self.morning_end_hour = config.get("morning_end_hour", 13)
        self.afternoon_end_hour = config.get("afternoon_end_hour", 18)
        self.morning_ratio = config.get("morning_ratio", 0.8)
        
        # Validate configuration
        self._validate_config()
    
    def _validate_config(self) -> None:
        """Validate PV system configuration parameters."""
        if self.max_power_w < 0:
            raise ValueError("Max power must be non-negative")
        if not 0 <= self.morning_start_hour <= 23:
            raise ValueError("Morning start hour must be between 0 and 23")
        if not 0 <= self.morning_end_hour <= 23:
            raise ValueError("Morning end hour must be between 0 and 23")
        if not 0 <= self.afternoon_end_hour <= 23:
            raise ValueError("Afternoon end hour must be between 0 and 23")
        if self.morning_start_hour >= self.morning_end_hour:
            raise ValueError("Morning start must be before morning end")
        if self.morning_end_hour >= self.afternoon_end_hour:
            raise ValueError("Morning end must be before afternoon end")
        if not 0 <= self.morning_ratio <= 1:
            raise ValueError("Morning ratio must be between 0 and 1")
    
    def calculate_hourly_production_wh(
        self, 
        daily_forecasts: List[float], 
        target_datetime: datetime,
        reference_datetime: datetime = None
    ) -> float:
        """Calculate hourly PV production based on daily forecasts.
        
        Args:
            daily_forecasts: List of daily production forecasts in kWh [today, tomorrow, day_after]
            target_datetime: The datetime for which to calculate production
            reference_datetime: Reference time for day offset calculation (defaults to target_datetime for consistency)
            
        Returns:
            Hourly production in Wh
        """
        # Use reference_datetime for day calculations, or target_datetime if not provided
        if reference_datetime is None:
            reference_datetime = target_datetime
            
        # Determine which day offset we need based on reference time
        reference_start = reference_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
        target_start = target_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
        
        day_offset = (target_start - reference_start).days
        hour = target_datetime.hour
        
        return self._calculate_hourly_pv(daily_forecasts, day_offset, hour)
    
    def _calculate_hourly_pv(self, daily_forecasts: List[float], day_offset: int, hour: int) -> float:
        """Calculate hourly PV production for a specific day offset and hour.
        
        This implements the algorithm from the specification.
        
        Args:
            daily_forecasts: List of daily forecasts in kWh
            day_offset: Day offset (0=today, 1=tomorrow, 2=day after)
            hour: Hour of the day (0-23)
            
        Returns:
            Hourly production in Wh
        """
        # Check if we have a valid forecast for this day
        if day_offset >= len(daily_forecasts) or day_offset < 0:
            return 0.0
        
        daily_kwh = daily_forecasts[day_offset]
        if daily_kwh <= 0:
            return 0.0
        
        # Outside PV production hours
        if hour < self.morning_start_hour or hour >= self.afternoon_end_hour:
            return 0.0
        
        # Morning production
        if hour < self.morning_end_hour:
            morning_hours = self.morning_end_hour - self.morning_start_hour
            morning_energy_kwh = daily_kwh * self.morning_ratio
            return (morning_energy_kwh * 1000) / morning_hours  # Convert to Wh per hour
        
        # Afternoon production
        else:
            afternoon_hours = self.afternoon_end_hour - self.morning_end_hour
            afternoon_energy_kwh = daily_kwh * (1 - self.morning_ratio)
            return (afternoon_energy_kwh * 1000) / afternoon_hours  # Convert to Wh per hour
    
    def get_production_forecast(
        self, 
        daily_forecasts: List[float], 
        start_datetime: datetime, 
        hours: int,
        reference_datetime: datetime = None
    ) -> List[float]:
        """Get hourly production forecast for a specified time range.
        
        Args:
            daily_forecasts: List of daily production forecasts in kWh
            start_datetime: Start datetime for the forecast
            hours: Number of hours to forecast
            reference_datetime: Reference time for day offset calculation
            
        Returns:
            List of hourly production values in Wh
        """
        forecast = []
        current_datetime = start_datetime
        
        for _ in range(hours):
            production_wh = self.calculate_hourly_production_wh(
                daily_forecasts, current_datetime, reference_datetime
            )
            forecast.append(production_wh)
            current_datetime += timedelta(hours=1)
        
        return forecast
    
    def get_daily_production_estimate(self, daily_forecasts: List[float], day_offset: int) -> float:
        """Get total daily production estimate for a specific day.
        
        Args:
            daily_forecasts: List of daily production forecasts in kWh
            day_offset: Day offset (0=today, 1=tomorrow, 2=day after)
            
        Returns:
            Total daily production in Wh
        """
        if day_offset >= len(daily_forecasts) or day_offset < 0:
            return 0.0
        
        return daily_forecasts[day_offset] * 1000  # Convert kWh to Wh
    
    def get_config(self) -> Dict[str, Any]:
        """Get current PV system configuration."""
        return {
            "max_power_w": self.max_power_w,
            "morning_start_hour": self.morning_start_hour,
            "morning_end_hour": self.morning_end_hour,
            "afternoon_end_hour": self.afternoon_end_hour,
            "morning_ratio": self.morning_ratio,
        }
