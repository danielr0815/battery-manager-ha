"""Config flow for Battery Manager integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .const import (
    DOMAIN,
    DEFAULT_CONFIG,
    CONF_SOC_ENTITY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_PV_FORECAST_DAY_AFTER,
)

_LOGGER = logging.getLogger(__name__)


class BatteryManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Battery Manager."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self.config: Dict[str, Any] = {}

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Validate entity selections
            entity_registry = async_get_entity_registry(self.hass)
            
            for entity_key in [CONF_SOC_ENTITY, CONF_PV_FORECAST_TODAY, 
                             CONF_PV_FORECAST_TOMORROW, CONF_PV_FORECAST_DAY_AFTER]:
                entity_id = user_input.get(entity_key)
                if entity_id and entity_id not in entity_registry.entities:
                    errors[entity_key] = "entity_not_found"

            if not errors:
                self.config.update(user_input)
                return await self.async_step_battery_config()

        # Get available sensor entities
        sensor_entities = await self._get_sensor_entities()

        data_schema = vol.Schema({
            vol.Required(CONF_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    device_class="battery"
                )
            ),
            vol.Required(CONF_PV_FORECAST_TODAY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_PV_FORECAST_TOMORROW): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_PV_FORECAST_DAY_AFTER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "soc_description": "Entity that provides current battery SOC in %",
                "pv_today_description": "Entity with today's PV forecast in kWh",
                "pv_tomorrow_description": "Entity with tomorrow's PV forecast in kWh",
                "pv_day_after_description": "Entity with day after tomorrow's PV forecast in kWh",
            }
        )

    async def async_step_battery_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure battery parameters."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Validate battery configuration
            try:
                self._validate_battery_config(user_input)
                self.config.update(user_input)
                return await self.async_step_pv_config()
            except ValueError as err:
                errors["base"] = "invalid_battery_config"
                _LOGGER.error("Battery config validation error: %s", err)

        data_schema = vol.Schema({
            vol.Required(
                "battery_capacity_wh", 
                default=DEFAULT_CONFIG["battery_capacity_wh"]
            ): vol.All(vol.Coerce(float), vol.Range(min=100, max=1000000)),
            vol.Required(
                "battery_min_soc_percent", 
                default=DEFAULT_CONFIG["battery_min_soc_percent"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Required(
                "battery_max_soc_percent", 
                default=DEFAULT_CONFIG["battery_max_soc_percent"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Required(
                "battery_charge_efficiency", 
                default=DEFAULT_CONFIG["battery_charge_efficiency"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1.0)),
            vol.Required(
                "battery_discharge_efficiency", 
                default=DEFAULT_CONFIG["battery_discharge_efficiency"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1.0)),
        })

        return self.async_show_form(
            step_id="battery_config",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_pv_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure PV system parameters."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                self._validate_pv_config(user_input)
                self.config.update(user_input)
                return await self.async_step_consumer_config()
            except ValueError as err:
                errors["base"] = "invalid_pv_config"
                _LOGGER.error("PV config validation error: %s", err)

        data_schema = vol.Schema({
            vol.Required(
                "pv_max_power_w", 
                default=DEFAULT_CONFIG["pv_max_power_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100000)),
            vol.Required(
                "pv_morning_start_hour", 
                default=DEFAULT_CONFIG["pv_morning_start_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(
                "pv_morning_end_hour", 
                default=DEFAULT_CONFIG["pv_morning_end_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(
                "pv_afternoon_end_hour", 
                default=DEFAULT_CONFIG["pv_afternoon_end_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(
                "pv_morning_ratio", 
                default=DEFAULT_CONFIG["pv_morning_ratio"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        })

        return self.async_show_form(
            step_id="pv_config",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_consumer_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure consumer parameters."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                self._validate_consumer_config(user_input)
                self.config.update(user_input)
                return await self.async_step_power_config()
            except ValueError as err:
                errors["base"] = "invalid_consumer_config"
                _LOGGER.error("Consumer config validation error: %s", err)

        data_schema = vol.Schema({
            vol.Required(
                "ac_base_load_w", 
                default=DEFAULT_CONFIG["ac_base_load_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
            vol.Required(
                "ac_variable_load_w", 
                default=DEFAULT_CONFIG["ac_variable_load_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
            vol.Required(
                "ac_variable_start_hour", 
                default=DEFAULT_CONFIG["ac_variable_start_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(
                "ac_variable_end_hour", 
                default=DEFAULT_CONFIG["ac_variable_end_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(
                "dc_base_load_w", 
                default=DEFAULT_CONFIG["dc_base_load_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
            vol.Required(
                "dc_variable_load_w", 
                default=DEFAULT_CONFIG["dc_variable_load_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
            vol.Required(
                "dc_variable_start_hour", 
                default=DEFAULT_CONFIG["dc_variable_start_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(
                "dc_variable_end_hour", 
                default=DEFAULT_CONFIG["dc_variable_end_hour"]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        })

        return self.async_show_form(
            step_id="consumer_config",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_power_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure power equipment parameters."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                self._validate_power_config(user_input)
                self.config.update(user_input)
                return await self.async_step_controller_config()
            except ValueError as err:
                errors["base"] = "invalid_power_config"
                _LOGGER.error("Power config validation error: %s", err)

        data_schema = vol.Schema({
            vol.Required(
                "charger_max_power_w", 
                default=DEFAULT_CONFIG["charger_max_power_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=100, max=50000)),
            vol.Required(
                "charger_efficiency", 
                default=DEFAULT_CONFIG["charger_efficiency"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1.0)),
            vol.Required(
                "charger_standby_power_w", 
                default=DEFAULT_CONFIG["charger_standby_power_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
            vol.Required(
                "inverter_max_power_w", 
                default=DEFAULT_CONFIG["inverter_max_power_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=100, max=50000)),
            vol.Required(
                "inverter_efficiency", 
                default=DEFAULT_CONFIG["inverter_efficiency"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1.0)),
            vol.Required(
                "inverter_standby_power_w", 
                default=DEFAULT_CONFIG["inverter_standby_power_w"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
            vol.Required(
                "inverter_min_soc_percent", 
                default=DEFAULT_CONFIG["inverter_min_soc_percent"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        })

        return self.async_show_form(
            step_id="power_config",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_controller_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure controller parameters."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self.config.update(user_input)
            return self.async_create_entry(
                title="Battery Manager",
                data=self.config,
            )

        data_schema = vol.Schema({
            vol.Required(
                "controller_target_soc_percent", 
                default=DEFAULT_CONFIG["controller_target_soc_percent"]
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        })

        return self.async_show_form(
            step_id="controller_config",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> BatteryManagerOptionsFlow:
        """Create options flow."""
        return BatteryManagerOptionsFlow(config_entry)

    async def _get_sensor_entities(self) -> list[str]:
        """Get available sensor entities."""
        entity_registry = async_get_entity_registry(self.hass)
        return [
            entity.entity_id
            for entity in entity_registry.entities.values()
            if entity.domain == "sensor"
        ]

    def _validate_battery_config(self, config: Dict[str, Any]) -> None:
        """Validate battery configuration."""
        min_soc = config.get("battery_min_soc_percent", 0)
        max_soc = config.get("battery_max_soc_percent", 100)
        
        if min_soc >= max_soc:
            raise ValueError("Min SOC must be less than max SOC")

    def _validate_pv_config(self, config: Dict[str, Any]) -> None:
        """Validate PV configuration."""
        morning_start = config.get("pv_morning_start_hour", 0)
        morning_end = config.get("pv_morning_end_hour", 12)
        afternoon_end = config.get("pv_afternoon_end_hour", 18)
        
        if morning_start >= morning_end:
            raise ValueError("Morning start must be before morning end")
        if morning_end >= afternoon_end:
            raise ValueError("Morning end must be before afternoon end")

    def _validate_consumer_config(self, config: Dict[str, Any]) -> None:
        """Validate consumer configuration."""
        ac_start = config.get("ac_variable_start_hour", 0)
        ac_end = config.get("ac_variable_end_hour", 23)
        dc_start = config.get("dc_variable_start_hour", 0)
        dc_end = config.get("dc_variable_end_hour", 23)
        
        if ac_start >= ac_end:
            raise ValueError("AC variable start must be before end")
        if dc_start >= dc_end:
            raise ValueError("DC variable start must be before end")

    def _validate_power_config(self, config: Dict[str, Any]) -> None:
        """Validate power equipment configuration."""
        # Basic validation - more complex validation in components
        pass


class BatteryManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Battery Manager."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Create options schema with current values
        current_config = {**DEFAULT_CONFIG, **self.config_entry.data}
        
        options_schema = vol.Schema({
            vol.Required(
                "controller_target_soc_percent",
                default=current_config.get("controller_target_soc_percent", 85.0)
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Required(
                "battery_capacity_wh",
                default=current_config.get("battery_capacity_wh", 5000.0)
            ): vol.All(vol.Coerce(float), vol.Range(min=100, max=1000000)),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
