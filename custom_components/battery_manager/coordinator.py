"""DataUpdateCoordinator for Battery Manager integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .battery_manager import BatteryManagerSimulator
from .const import (
    DOMAIN,
    UPDATE_INTERVAL_SECONDS,
    DEBOUNCE_SECONDS,
    MAX_PV_FORECAST_AGE_HOURS,
    MAX_SOC_AGE_HOURS,
    CONF_SOC_ENTITY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_PV_FORECAST_DAY_AFTER,
    DEFAULT_CONFIG,
)

_LOGGER = logging.getLogger(__name__)


class BatteryManagerCoordinator(DataUpdateCoordinator):
    """Coordinator for Battery Manager data updates."""

    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        
        self.config = {**DEFAULT_CONFIG, **config}
        self.simulator = BatteryManagerSimulator(self.config)
        
        # Entity IDs for input data
        self.soc_entity_id = config[CONF_SOC_ENTITY]
        self.pv_forecast_entities = [
            config[CONF_PV_FORECAST_TODAY],
            config[CONF_PV_FORECAST_TOMORROW],
            config[CONF_PV_FORECAST_DAY_AFTER],
        ]
        
        # Cached input data
        self._last_valid_soc: Optional[float] = None
        self._last_valid_forecasts: Optional[List[float]] = None
        self._last_soc_update: Optional[datetime] = None
        self._last_forecast_update: Optional[datetime] = None
        
        # Debouncing
        self._debounce_task: Optional[asyncio.Task] = None
        
        # Subscribe to entity state changes
        self._setup_entity_listeners()

    def _setup_entity_listeners(self) -> None:
        """Set up listeners for input entity state changes."""
        entities_to_track = [self.soc_entity_id] + self.pv_forecast_entities
        
        try:
            # Set up state change tracking for immediate updates
            async_track_state_change_event(
                self.hass, 
                entities_to_track, 
                self._handle_entity_change
            )
            _LOGGER.debug("Entity listeners configured for: %s", entities_to_track)
        except Exception as err:
            _LOGGER.warning("Failed to set up entity listeners: %s. Will rely on periodic updates.", err)

    async def _handle_entity_change(self, event) -> None:
        """Handle state change of tracked entities."""
        if self._debounce_task:
            self._debounce_task.cancel()
        
        self._debounce_task = self.hass.async_create_task(
            self._debounced_update()
        )

    async def _debounced_update(self) -> None:
        """Debounced update after entity state changes."""
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await self.async_request_refresh()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from entities and run simulation."""
        try:
            # Get current input data
            current_soc = await self._get_current_soc()
            daily_forecasts = await self._get_daily_forecasts()
            
            # Check data validity
            data_valid = self._check_data_validity()
            
            if not data_valid:
                _LOGGER.warning("Input data is too old or invalid")
                return {
                    "valid": False,
                    "soc_threshold_percent": None,
                    "min_soc_forecast_percent": None,
                    "max_soc_forecast_percent": None,
                    "inverter_enabled": False,
                    "error": "Data too old or invalid",
                }
            
            # Run simulation
            current_time = dt_util.now()
            results = self.simulator.run_simulation(
                current_soc, daily_forecasts, current_time
            )
            
            # Add validity flag
            results["valid"] = True
            results["last_update"] = current_time
            
            _LOGGER.debug(
                "Battery Manager calculation completed: SOC=%s%%, Threshold=%s%%",
                current_soc,
                results["soc_threshold_percent"]
            )
            
            return results

        except Exception as err:
            _LOGGER.error("Error updating Battery Manager data: %s", err)
            raise UpdateFailed(f"Error updating data: {err}") from err

    async def _get_current_soc(self) -> float:
        """Get current SOC from entity."""
        state = self.hass.states.get(self.soc_entity_id)
        
        if state is None:
            if self._last_valid_soc is not None:
                _LOGGER.warning("SOC entity not found, using last valid value")
                return self._last_valid_soc
            raise UpdateFailed(f"SOC entity {self.soc_entity_id} not found")
        
        if state.state in ("unknown", "unavailable"):
            if self._last_valid_soc is not None:
                _LOGGER.warning("SOC entity unavailable, using last valid value")
                return self._last_valid_soc
            raise UpdateFailed(f"SOC entity {self.soc_entity_id} is unavailable")
        
        try:
            soc_value = float(state.state)
            if 0 <= soc_value <= 100:
                self._last_valid_soc = soc_value
                self._last_soc_update = dt_util.now()
                return soc_value
            else:
                _LOGGER.warning("SOC value out of range: %s", soc_value)
                if self._last_valid_soc is not None:
                    return self._last_valid_soc
                raise UpdateFailed(f"SOC value out of range: {soc_value}")
                
        except ValueError as err:
            if self._last_valid_soc is not None:
                _LOGGER.warning("Invalid SOC value, using last valid value")
                return self._last_valid_soc
            raise UpdateFailed(f"Invalid SOC value: {state.state}") from err

    async def _get_daily_forecasts(self) -> List[float]:
        """Get daily PV forecasts from entities."""
        forecasts = []
        
        for i, entity_id in enumerate(self.pv_forecast_entities):
            state = self.hass.states.get(entity_id)
            
            if state is None or state.state in ("unknown", "unavailable"):
                if self._last_valid_forecasts is not None:
                    _LOGGER.warning("PV forecast entity %s unavailable, using last valid value", entity_id)
                    forecasts.append(self._last_valid_forecasts[i])
                    continue
                raise UpdateFailed(f"PV forecast entity {entity_id} not available")
            
            try:
                forecast_value = float(state.state)
                if forecast_value < 0:
                    _LOGGER.warning("Negative forecast value: %s", forecast_value)
                    forecast_value = 0.0
                forecasts.append(forecast_value)
                
            except ValueError as err:
                if self._last_valid_forecasts is not None:
                    _LOGGER.warning("Invalid forecast value, using last valid value")
                    forecasts.append(self._last_valid_forecasts[i])
                    continue
                raise UpdateFailed(f"Invalid forecast value for {entity_id}: {state.state}") from err
        
        if len(forecasts) == 3:
            self._last_valid_forecasts = forecasts
            self._last_forecast_update = dt_util.now()
        
        return forecasts

    def _check_data_validity(self) -> bool:
        """Check if input data is still valid based on age."""
        now = dt_util.now()
        
        # Check SOC age
        if self._last_soc_update is None:
            return False
        
        soc_age = now - self._last_soc_update
        if soc_age > timedelta(hours=MAX_SOC_AGE_HOURS):
            _LOGGER.warning("SOC data too old: %s hours", soc_age.total_seconds() / 3600)
            return False
        
        # Check forecast age
        if self._last_forecast_update is None:
            return False
        
        forecast_age = now - self._last_forecast_update
        if forecast_age > timedelta(hours=MAX_PV_FORECAST_AGE_HOURS):
            _LOGGER.warning("Forecast data too old: %s hours", forecast_age.total_seconds() / 3600)
            return False
        
        return True

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """Update configuration and restart simulation."""
        self.config.update(new_config)
        self.simulator.update_config(self.config)
        
        # Trigger immediate update
        self.hass.async_create_task(self.async_request_refresh())

    def get_debug_info(self) -> Dict[str, Any]:
        """Get debug information."""
        return {
            "config": self.config,
            "last_valid_soc": self._last_valid_soc,
            "last_valid_forecasts": self._last_valid_forecasts,
            "last_soc_update": self._last_soc_update,
            "last_forecast_update": self._last_forecast_update,
            "data_valid": self._check_data_validity(),
        }
