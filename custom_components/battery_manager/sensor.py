"""Sensor platform for Battery Manager integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    INTEGRATION_NAME,
    INTEGRATION_VERSION,
    ENTITY_INVERTER_STATUS,
    ENTITY_SOC_THRESHOLD,
    ENTITY_MIN_SOC_FORECAST,
    ENTITY_MAX_SOC_FORECAST,
    ATTR_GRID_IMPORT_KWH,
    ATTR_GRID_EXPORT_KWH,
    ATTR_SIMULATION_END,
    ATTR_LAST_UPDATE,
    ATTR_DATA_VALIDITY,
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


class BatteryManagerInverterStatus(BatteryManagerEntityBase, BinarySensorEntity):
    """Binary sensor for inverter status."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the inverter status sensor."""
        super().__init__(
            coordinator,
            config_entry,
            ENTITY_INVERTER_STATUS,
            "Inverter Status"
        )
        self._attr_icon = "mdi:power-plug"

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if the inverter is enabled."""
        if not self.available:
            return None
        return self.coordinator.data.get("inverter_enabled", False)

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
            coordinator,
            config_entry,
            ENTITY_SOC_THRESHOLD,
            "SOC Threshold"
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
        return self.coordinator.data.get("soc_threshold_percent")

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
            coordinator,
            config_entry,
            ENTITY_MIN_SOC_FORECAST,
            "Min SOC Forecast"
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
        return self.coordinator.data.get("min_soc_forecast_percent")


class BatteryManagerMaxSOCForecast(BatteryManagerEntityBase, SensorEntity):
    """Sensor for maximum SOC forecast."""

    def __init__(
        self,
        coordinator: BatteryManagerCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the max SOC forecast sensor."""
        super().__init__(
            coordinator,
            config_entry,
            ENTITY_MAX_SOC_FORECAST,
            "Max SOC Forecast"
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
        return self.coordinator.data.get("max_soc_forecast_percent")
