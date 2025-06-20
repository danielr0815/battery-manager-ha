"""Sensor platform for Battery Manager integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DATA_VALIDITY,
    ATTR_GRID_EXPORT_KWH,
    ATTR_GRID_IMPORT_KWH,
    ATTR_LAST_UPDATE,
    ATTR_SIMULATION_END,
    DOMAIN,
    ENTITY_DISCHARGE,
    ENTITY_HOURS_TO_MAX_SOC,
    ENTITY_INVERTER_STATUS,
    ENTITY_EXTRA_LOAD,
    ENTITY_MAX_SOC_FORECAST,
    ENTITY_MIN_SOC_FORECAST,
    ENTITY_SOC_THRESHOLD,
    INTEGRATION_NAME,
    INTEGRATION_VERSION,
)
from .coordinator import BatteryManagerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Battery Manager sensors from a config entry."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Create SOC Threshold first to make it the primary entity for the device card
    entities = [
        BatteryManagerSOCThreshold(coordinator, config_entry),
        BatteryManagerInverterStatus(coordinator, config_entry),
        BatteryManagerMinSOCForecast(coordinator, config_entry),
        BatteryManagerMaxSOCForecast(coordinator, config_entry),
        BatteryManagerHoursToMaxSOC(coordinator, config_entry),
        BatteryManagerDischarge(coordinator, config_entry),
        BatteryManagerExtraLoad(coordinator, config_entry),
    ]

    async_add_entities(entities)


class BatteryManagerEntityBase(CoordinatorEntity):
    """Base class for Battery Manager entities."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
        entity_id_suffix: str,
        name_suffix: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_{entity_id_suffix}"
        self._attr_name = f"Battery Manager {name_suffix}"

        # Enhanced debugging
        self._last_state_value = None
        self._state_change_count = 0

        # Device info for grouping entities
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name="Battery Manager",
            manufacturer="Battery Manager",
            model="Battery Management System",
            sw_version=INTEGRATION_VERSION,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.coordinator.data.get("valid", False)
        )

    def _log_state_change(self, entity_type: str, new_value, old_value=None) -> None:
        """Log significant state changes for debugging."""
        if old_value is None:
            old_value = self._last_state_value

        if old_value != new_value:
            self._state_change_count += 1

            # Log significant changes
            if isinstance(new_value, (int, float)) and isinstance(
                old_value, (int, float)
            ):
                if abs(new_value - old_value) > 1.0:  # Log changes > 1 unit
                    _LOGGER.debug(
                        "%s state changed: %s → %s (change #%d)",
                        entity_type,
                        old_value,
                        new_value,
                        self._state_change_count,
                    )
            elif old_value != new_value:  # For boolean or other types
                _LOGGER.debug(
                    "%s state changed: %s → %s (change #%d)",
                    entity_type,
                    old_value,
                    new_value,
                    self._state_change_count,
                )

        self._last_state_value = new_value


class BatteryManagerInverterStatus(BatteryManagerEntityBase, BinarySensorEntity):
    """Binary sensor for inverter status."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the inverter status sensor."""
        super().__init__(
            coordinator, config_entry, ENTITY_INVERTER_STATUS, "Inverter Status"
        )
        self._attr_icon = "mdi:power-plug"

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if the inverter is enabled."""
        if not self.available:
            return None

        new_state = self.coordinator.data.get("inverter_enabled", False)
        self._log_state_change("Inverter Status", new_state)
        return new_state

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        if not self.available:
            return {}

        data = self.coordinator.data
        return {
            ATTR_GRID_IMPORT_KWH: data.get("grid_import_kwh", 0.0),
            ATTR_GRID_EXPORT_KWH: data.get("grid_export_kwh", 0.0),
            ATTR_SIMULATION_END: data.get("forecast_end_time"),
            ATTR_LAST_UPDATE: data.get("last_update"),
            ATTR_DATA_VALIDITY: data.get("valid", False),
        }


class BatteryManagerSOCThreshold(BatteryManagerEntityBase, SensorEntity):
    """Sensor for SOC threshold."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the SOC threshold sensor."""
        super().__init__(
            coordinator, config_entry, ENTITY_SOC_THRESHOLD, "SOC Threshold"
        )
        # Override the name to make this the primary entity
        self._attr_name = "Battery Manager"  # Main entity name without suffix
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:battery-charging"
        self._attr_suggested_display_precision = 1  # Show 1 decimal place
        # Mark as primary entity for the device
        self._attr_entity_registry_enabled_default = True

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        if not self.available:
            return None

        new_value = self.coordinator.data.get("soc_threshold_percent")
        self._log_state_change("SOC Threshold", new_value)
        return new_value

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        if not self.available:
            return {}

        data = self.coordinator.data
        return {
            "forecast_hours": data.get("forecast_hours", 0),
            "input_soc_percent": data.get("input_soc_percent"),
        }


class BatteryManagerMinSOCForecast(BatteryManagerEntityBase, SensorEntity):
    """Sensor for minimum SOC forecast."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the min SOC forecast sensor."""
        super().__init__(
            coordinator, config_entry, ENTITY_MIN_SOC_FORECAST, "Min SOC Forecast"
        )
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:battery-low"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC  # Mark as diagnostic
        self._attr_entity_registry_enabled_default = False  # Disable by default
        self._attr_suggested_display_precision = 1  # Show 1 decimal place

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        if not self.available:
            return None

        new_value = self.coordinator.data.get("min_soc_forecast_percent")
        self._log_state_change("Min SOC Forecast", new_value)
        return new_value


class BatteryManagerMaxSOCForecast(BatteryManagerEntityBase, SensorEntity):
    """Sensor for maximum SOC forecast."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the max SOC forecast sensor."""
        super().__init__(
            coordinator, config_entry, ENTITY_MAX_SOC_FORECAST, "Max SOC Forecast"
        )
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:battery-high"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC  # Mark as diagnostic
        self._attr_entity_registry_enabled_default = False  # Disable by default
        self._attr_suggested_display_precision = 1  # Show 1 decimal place

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        if not self.available:
            return None

        new_value = self.coordinator.data.get("max_soc_forecast_percent")
        self._log_state_change("Max SOC Forecast", new_value)
        return new_value


class BatteryManagerHoursToMaxSOC(BatteryManagerEntityBase, SensorEntity):
    """Sensor for hours until maximum SOC is reached."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the hours to max SOC sensor."""
        super().__init__(
            coordinator,
            config_entry,
            ENTITY_HOURS_TO_MAX_SOC,
            "Hours to Max SOC",
        )
        self._attr_native_unit_of_measurement = "h"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:clock-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        if not self.available:
            return None

        new_value = self.coordinator.data.get("hours_until_max_soc")
        self._log_state_change("Hours to Max SOC", new_value)
        return new_value


class BatteryManagerDischarge(BatteryManagerEntityBase, SensorEntity):
    """Sensor for discharge forecast percentage."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the discharge forecast sensor."""
        super().__init__(
            coordinator, config_entry, ENTITY_DISCHARGE, "Discharge Forecast"
        )
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:battery-minus"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC  # Mark as diagnostic
        self._attr_entity_registry_enabled_default = False  # Disable by default
        self._attr_suggested_display_precision = 1  # Show 1 decimal place

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        if not self.available:
            return None

        new_value = self.coordinator.data.get("discharge_forecast_percent")
        self._log_state_change("Discharge Forecast", new_value)
        return new_value


class BatteryManagerExtraLoad(BatteryManagerEntityBase, BinarySensorEntity):
    """Binary sensor indicating if extra load can be enabled."""

    def __init__(self, coordinator: BatteryManagerCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, ENTITY_EXTRA_LOAD, "Extra Load")
        self._attr_icon = "mdi:flash"

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if extra load should be active."""
        if not self.available:
            return None

        new_state = self.coordinator.data.get("extra_load", False)
        self._log_state_change("Extra Load", new_state)
        return new_state
