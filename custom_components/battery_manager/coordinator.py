"""DataUpdateCoordinator for Battery Manager integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .battery_manager import BatteryManagerSimulator
from .const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DEBOUNCE_SECONDS,
    DEFAULT_CONFIG,
    DOMAIN,
    MAX_PV_FORECAST_AGE_HOURS,
    MAX_SOC_AGE_HOURS,
    UPDATE_INTERVAL_SECONDS,
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

        # Enhanced debugging data
        self._last_calculation_inputs: Optional[Dict[str, Any]] = None
        self._last_calculation_results: Optional[Dict[str, Any]] = None
        self._value_change_count = 0
        self._startup_values: Optional[Dict[str, Any]] = None

        # Debouncing
        self._debounce_task: Optional[asyncio.Task] = None
        self._listeners_setup: bool = False

        # Don't subscribe to entity state changes yet - wait for first refresh

    def _setup_entity_listeners(self) -> None:
        """Set up listeners for input entity state changes."""
        entities_to_track = [self.soc_entity_id] + self.pv_forecast_entities

        try:
            # Set up state change tracking for immediate updates
            async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )
            _LOGGER.debug("Entity listeners configured for: %s", entities_to_track)
        except Exception as err:
            _LOGGER.warning(
                "Failed to set up entity listeners: %s. Will rely on periodic updates.",
                err,
            )

    async def _handle_entity_change(self, event) -> None:
        """Handle state change of tracked entities."""
        # Skip handling during setup phase
        if not self._listeners_setup:
            return

        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        # Enhanced debugging for entity changes
        _LOGGER.debug(
            "Entity change detected: %s from %s to %s",
            entity_id,
            old_state.state if old_state else "None",
            new_state.state if new_state else "None",
        )

        # Track changes that might cause calculation drift
        if old_state and new_state and old_state.state != new_state.state:
            try:
                old_val = (
                    float(old_state.state)
                    if old_state.state not in ("unknown", "unavailable")
                    else None
                )
                new_val = (
                    float(new_state.state)
                    if new_state.state not in ("unknown", "unavailable")
                    else None
                )

                if old_val is not None and new_val is not None:
                    change_percent = (
                        abs((new_val - old_val) / old_val * 100) if old_val != 0 else 0
                    )
                    if change_percent > 5:  # Log significant changes
                        _LOGGER.info(
                            "Significant input change in %s: %.2f → %.2f (%.1f%% change)",
                            entity_id,
                            old_val,
                            new_val,
                            change_percent,
                        )
            except (ValueError, TypeError):
                pass

        if self._debounce_task:
            self._debounce_task.cancel()

        self._debounce_task = self.hass.async_create_task(self._debounced_update())

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

            # Store current inputs for debugging
            current_inputs = {
                "soc_percent": current_soc,
                "daily_forecasts_kwh": daily_forecasts,
                "timestamp": dt_util.now(),
            }

            # Check for input data changes that might cause drift
            if self._last_calculation_inputs:
                soc_changed = (
                    abs(
                        current_inputs["soc_percent"]
                        - self._last_calculation_inputs["soc_percent"]
                    )
                    > 0.1
                )
                forecasts_changed = any(
                    abs(new - old) > 0.01
                    for new, old in zip(
                        current_inputs["daily_forecasts_kwh"],
                        self._last_calculation_inputs["daily_forecasts_kwh"],
                    )
                )

                if soc_changed or forecasts_changed:
                    _LOGGER.debug(
                        "Input data changed - SOC: %.1f → %.1f, Forecasts: %s → %s",
                        self._last_calculation_inputs["soc_percent"],
                        current_inputs["soc_percent"],
                        [
                            f"{f:.1f}"
                            for f in self._last_calculation_inputs[
                                "daily_forecasts_kwh"
                            ]
                        ],
                        [f"{f:.1f}" for f in current_inputs["daily_forecasts_kwh"]],
                    )

            self._last_calculation_inputs = current_inputs

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

            # Enhanced debugging for calculation results
            self._track_calculation_results(results)

            _LOGGER.debug(
                "Battery Manager calculation completed: SOC=%s%%, Threshold=%s%%, Min=%s%%, Max=%s%%, Discharge=%s%%",
                current_soc,
                results["soc_threshold_percent"],
                results["min_soc_forecast_percent"],
                results["max_soc_forecast_percent"],
                results["discharge_forecast_percent"],
            )

            # Set up entity listeners after first successful update
            if not self._listeners_setup:
                self._setup_entity_listeners()
                self._listeners_setup = True

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
                # Log significant SOC changes
                if self._last_valid_soc is not None:
                    soc_change = abs(soc_value - self._last_valid_soc)
                    if soc_change > 1.0:  # Log changes > 1%
                        _LOGGER.debug(
                            "SOC changed significantly: %.1f%% → %.1f%% (Δ%.1f%%)",
                            self._last_valid_soc,
                            soc_value,
                            soc_change,
                        )

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
                    _LOGGER.warning(
                        "PV forecast entity %s unavailable, using last valid value",
                        entity_id,
                    )
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
                raise UpdateFailed(
                    f"Invalid forecast value for {entity_id}: {state.state}"
                ) from err

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
            _LOGGER.warning(
                "SOC data too old: %s hours", soc_age.total_seconds() / 3600
            )
            return False

        # Check forecast age
        if self._last_forecast_update is None:
            return False

        forecast_age = now - self._last_forecast_update
        if forecast_age > timedelta(hours=MAX_PV_FORECAST_AGE_HOURS):
            _LOGGER.warning(
                "Forecast data too old: %s hours", forecast_age.total_seconds() / 3600
            )
            return False

        return True

    def _track_calculation_results(self, results: Dict[str, Any]) -> None:
        """Track calculation results for debugging stability issues."""
        current_results = {
            "soc_threshold_percent": results.get("soc_threshold_percent"),
            "min_soc_forecast_percent": results.get("min_soc_forecast_percent"),
            "max_soc_forecast_percent": results.get("max_soc_forecast_percent"),
            "discharge_forecast_percent": results.get("discharge_forecast_percent"),
            "inverter_enabled": results.get("inverter_enabled"),
            "timestamp": dt_util.now(),
        }

        # Store startup values for comparison
        if self._startup_values is None:
            self._startup_values = current_results.copy()
            _LOGGER.info(
                "Startup values recorded - Threshold: %.1f%%, Min: %.1f%%, Max: %.1f%%, Inverter: %s",
                current_results["soc_threshold_percent"],
                current_results["min_soc_forecast_percent"],
                current_results["max_soc_forecast_percent"],
                current_results["inverter_enabled"],
            )

        # Check for implausible values (potential drift indicators)
        if self._last_calculation_results:
            threshold_change = (
                abs(
                    current_results["soc_threshold_percent"]
                    - self._last_calculation_results["soc_threshold_percent"]
                )
                if current_results["soc_threshold_percent"] is not None
                else 0
            )

            min_change = (
                abs(
                    current_results["min_soc_forecast_percent"]
                    - self._last_calculation_results["min_soc_forecast_percent"]
                )
                if current_results["min_soc_forecast_percent"] is not None
                else 0
            )

            max_change = (
                abs(
                    current_results["max_soc_forecast_percent"]
                    - self._last_calculation_results["max_soc_forecast_percent"]
                )
                if current_results["max_soc_forecast_percent"] is not None
                else 0
            )

            # Log significant changes that might indicate drift
            if threshold_change > 5.0:
                _LOGGER.warning(
                    "Large threshold change detected: %.1f%% → %.1f%% (Δ%.1f%%)",
                    self._last_calculation_results["soc_threshold_percent"],
                    current_results["soc_threshold_percent"],
                    threshold_change,
                )

            if min_change > 10.0 or max_change > 10.0:
                _LOGGER.warning(
                    "Large forecast change detected - Min: %.1f%% → %.1f%% (Δ%.1f%%), Max: %.1f%% → %.1f%% (Δ%.1f%%)",
                    self._last_calculation_results["min_soc_forecast_percent"],
                    current_results["min_soc_forecast_percent"],
                    min_change,
                    self._last_calculation_results["max_soc_forecast_percent"],
                    current_results["max_soc_forecast_percent"],
                    max_change,
                )

            # Check for implausible values compared to startup
            if (
                current_results["soc_threshold_percent"]
                and abs(
                    current_results["soc_threshold_percent"]
                    - self._startup_values["soc_threshold_percent"]
                )
                > 20
            ):
                _LOGGER.error(
                    "Potentially implausible threshold detected: %.1f%% (startup was %.1f%%)",
                    current_results["soc_threshold_percent"],
                    self._startup_values["soc_threshold_percent"],
                )

        self._last_calculation_results = current_results
        self._value_change_count += 1

        # Periodic stability check
        if self._value_change_count % 20 == 0:  # Every 20 updates
            _LOGGER.info(
                "Stability check (%d updates) - Current: T=%.1f%%, Min=%.1f%%, Max=%.1f%% | Startup: T=%.1f%%, Min=%.1f%%, Max=%.1f%%",
                self._value_change_count,
                current_results["soc_threshold_percent"],
                current_results["min_soc_forecast_percent"],
                current_results["max_soc_forecast_percent"],
                self._startup_values["soc_threshold_percent"],
                self._startup_values["min_soc_forecast_percent"],
                self._startup_values["max_soc_forecast_percent"],
            )

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """Update configuration and restart simulation."""
        old_config = self.config.copy()
        self.config.update(new_config)
        self.simulator.update_config(self.config)

        # Update entity IDs if they changed
        entity_ids_changed = False
        if CONF_SOC_ENTITY in new_config and new_config[CONF_SOC_ENTITY] != old_config.get(CONF_SOC_ENTITY):
            self.soc_entity_id = new_config[CONF_SOC_ENTITY]
            entity_ids_changed = True
        
        for conf_key, attr_name in [
            (CONF_PV_FORECAST_TODAY, 0),
            (CONF_PV_FORECAST_TOMORROW, 1),
            (CONF_PV_FORECAST_DAY_AFTER, 2),
        ]:
            if conf_key in new_config and new_config[conf_key] != old_config.get(conf_key):
                self.pv_forecast_entities[attr_name] = new_config[conf_key]
                entity_ids_changed = True

        # Re-setup listeners if entity IDs changed
        if entity_ids_changed and self._listeners_setup:
            self._setup_entity_listeners()

        # Reset cached data when config changes
        self._last_valid_soc = None
        self._last_valid_forecasts = None
        self._last_soc_update = None
        self._last_forecast_update = None
        self._startup_values = None

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
            "last_calculation_inputs": self._last_calculation_inputs,
            "last_calculation_results": self._last_calculation_results,
            "startup_values": self._startup_values,
            "value_change_count": self._value_change_count,
        }
