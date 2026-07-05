"""Config flow for the Battery Manager integration (v2, with subentries)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_AC_LOAD_ENTITY,
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_NAME,
    CONF_APPLIANCE_OPPORTUNISTIC,
    CONF_APPLIANCE_POWER_THRESHOLD_W,
    CONF_APPLIANCE_RUN_DURATION_H,
    CONF_APPLIANCE_RUN_ENERGY_WH,
    CONF_BUFFER_MAX_PERCENT,
    CONF_BUFFER_MIN_PERCENT,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
    CONF_DC_LOAD_ENTITY,
    CONF_DCDC_SWITCH,
    CONF_LEARNING_MAX_AGE_DAYS,
    CONF_LEARNING_WINDOW_DAYS,
    CONF_LOAD_AVAILABILITY_ENTITY,
    CONF_LOAD_BATTERY_TOLERANCE,
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_IN_HOUSE,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_NAME,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_POWER_WARNING_PCT,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    CONF_PROFILE_HALF_LIFE_DAYS,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_POWER_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_POWER_W,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    CONF_WORKDAY_ENTITY,
    DEFAULT_APPLIANCE_CONFIG,
    DEFAULT_CONFIG,
    DEFAULT_LOAD_CONFIG,
    DOMAIN,
    INPUT_OFF_POLICIES,
    INPUT_OFF_POLICY_KEEP,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
)


def _number(minimum: float, maximum: float, step: float = 1.0, unit: str | None = None):
    config = selector.NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=selector.NumberSelectorMode.BOX,
    )
    if unit is not None:
        # unit_of_measurement=None fails the selector's config validation
        # (vol.Invalid surfaces as a bare "400: Bad Request" on flow load).
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _entity(domain: str | list[str] | None = None, multiple: bool = False):
    config = selector.EntitySelectorConfig(multiple=multiple)
    if domain:
        config["domain"] = domain
    return selector.EntitySelector(config)


def _d(config: dict[str, Any], key: str) -> Any:
    return config.get(key, DEFAULT_CONFIG.get(key))


_SUPPORT_SWITCH_KEYS = (
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_DCDC_SWITCH,
)

# Learned-consumption measurement sources (docs/CONSUMPTION_FORECAST.md §4.1)
_LEARNING_SINGLE_KEYS = (CONF_AC_LOAD_ENTITY, CONF_DC_LOAD_ENTITY)
_LEARNING_MULTI_KEYS = (
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
)

# The eight static profile values stay the fallback profile (D-C6) and are
# editable in the options flow as well.
_PROFILE_KEYS = (
    "ac_base_load_w",
    "ac_variable_load_w",
    "ac_variable_start_hour",
    "ac_variable_end_hour",
    "dc_base_load_w",
    "dc_variable_load_w",
    "dc_variable_start_hour",
    "dc_variable_end_hour",
)


def _validate_learning_sources(data: dict[str, Any]) -> str | None:
    """A counter balance needs at least one inflow entity (D-C1)."""
    for in_key, out_key in (
        (CONF_AC_BALANCE_IN, CONF_AC_BALANCE_OUT),
        (CONF_DC_BALANCE_IN, CONF_DC_BALANCE_OUT),
    ):
        if data.get(out_key) and not data.get(in_key):
            return "balance_out_without_in"
    return None


def _validate_buffer_clamps(data: dict[str, Any]) -> str | None:
    """min >= max would silently pin the dynamic buffer to the max (D-C8)."""
    low = data.get(CONF_BUFFER_MIN_PERCENT)
    high = data.get(CONF_BUFFER_MAX_PERCENT)
    if low is not None and high is not None and float(low) >= float(high):
        return "buffer_min_above_max"
    return None


def _profile_schema_fields(current: dict[str, Any]) -> dict[Any, Any]:
    """Static fallback-profile fields (shared: consumers step + options)."""
    hours = {
        "ac_variable_start_hour",
        "ac_variable_end_hour",
        "dc_variable_start_hour",
        "dc_variable_end_hour",
    }
    return {
        vol.Required(key, default=_d(current, key)): (
            _number(0, 23) if key in hours else _number(0, 10_000, 5, "W")
        )
        for key in _PROFILE_KEYS
    }


def _learning_schema_fields(current: dict[str, Any]) -> dict[Any, Any]:
    """Measurement-source fields (shared: consumers step + options)."""
    schema: dict[Any, Any] = {}
    for key in _LEARNING_SINGLE_KEYS:
        # suggested_value (not default) keeps the field clearable in the UI.
        schema[vol.Optional(key, description={"suggested_value": current.get(key)})] = (
            _entity("sensor")
        )
    for key in _LEARNING_MULTI_KEYS:
        schema[
            vol.Optional(key, description={"suggested_value": current.get(key) or []})
        ] = _entity("sensor", multiple=True)
    schema[
        vol.Optional(
            CONF_WORKDAY_ENTITY,
            description={"suggested_value": current.get(CONF_WORKDAY_ENTITY)},
        )
    ] = _entity("binary_sensor")
    return schema


def _validate_load_control(data: dict[str, Any]) -> str | None:
    """Reject charging-path combinations that cannot work (LOAD_CONTROL.md §7)."""
    if data.get(CONF_LOAD_INPUT_OFF_POLICY) == INPUT_OFF_POLICY_KEEP and not data.get(
        CONF_LOAD_CHARGE_ENABLE
    ):
        return "keep_on_requires_enable"
    control = data.get(CONF_LOAD_CONTROL_SWITCH)
    if control and control == data.get(CONF_LOAD_CHARGE_ENABLE):
        return "control_entities_not_distinct"
    return None


def _validate_support_entities(data: dict[str, Any]) -> str | None:
    """The three support switches must point to distinct entities.

    A shared entity would make the make-before-break sequence switch the
    rail's only supply off (review finding, docs/ALGORITHM.md D-A9).
    """
    chosen = [data.get(key) for key in _SUPPORT_SWITCH_KEYS if data.get(key)]
    if len(chosen) != len(set(chosen)):
        return "support_entities_not_distinct"
    return None


class BatteryManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step base configuration."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BatteryManagerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_TYPE_LOAD: SurplusLoadSubentryFlow,
            SUBENTRY_TYPE_APPLIANCE: ApplianceSubentryFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Input entities."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_battery()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOC_ENTITY): _entity("sensor"),
                    vol.Required(CONF_PV_FORECAST_TODAY): _entity("sensor"),
                    vol.Required(CONF_PV_FORECAST_TOMORROW): _entity("sensor"),
                    vol.Required(CONF_PV_FORECAST_DAY_AFTER): _entity("sensor"),
                }
            ),
        )

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if (
                user_input["battery_min_soc_percent"]
                >= user_input["battery_max_soc_percent"]
            ):
                return self.async_show_form(
                    step_id="battery",
                    data_schema=self._battery_schema(),
                    errors={"base": "min_soc_above_max"},
                )
            self._data.update(user_input)
            return await self.async_step_pv()
        return self.async_show_form(
            step_id="battery", data_schema=self._battery_schema()
        )

    def _battery_schema(self) -> vol.Schema:
        d = self._data
        return vol.Schema(
            {
                vol.Required(
                    "battery_capacity_wh", default=_d(d, "battery_capacity_wh")
                ): _number(100, 1_000_000, 100, "Wh"),
                vol.Required(
                    "battery_min_soc_percent", default=_d(d, "battery_min_soc_percent")
                ): _number(0, 100, 1, "%"),
                vol.Required(
                    "battery_max_soc_percent", default=_d(d, "battery_max_soc_percent")
                ): _number(0, 100, 1, "%"),
                vol.Required(
                    "battery_charge_efficiency",
                    default=_d(d, "battery_charge_efficiency"),
                ): _number(0.1, 1.0, 0.01),
                vol.Required(
                    "battery_discharge_efficiency",
                    default=_d(d, "battery_discharge_efficiency"),
                ): _number(0.1, 1.0, 0.01),
            }
        )

    async def async_step_pv(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_consumers()
        d = self._data
        return self.async_show_form(
            step_id="pv",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "pv_max_power_w", default=_d(d, "pv_max_power_w")
                    ): _number(0, 100_000, 50, "W"),
                    vol.Required(
                        "pv_morning_start_hour", default=_d(d, "pv_morning_start_hour")
                    ): _number(0, 23),
                    vol.Required(
                        "pv_morning_end_hour", default=_d(d, "pv_morning_end_hour")
                    ): _number(0, 23),
                    vol.Required(
                        "pv_afternoon_end_hour", default=_d(d, "pv_afternoon_end_hour")
                    ): _number(0, 23),
                    vol.Required(
                        "pv_morning_ratio", default=_d(d, "pv_morning_ratio")
                    ): _number(0.0, 1.0, 0.05),
                }
            ),
        )

    async def async_step_consumers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_learning_sources(user_input)
            if error is None:
                self._data.update(user_input)
                return await self.async_step_power()
            errors["base"] = error
        # On a validation error, re-render with the just-entered values.
        d = {**self._data, **(user_input or {})}
        return self.async_show_form(
            step_id="consumers",
            errors=errors,
            data_schema=vol.Schema(
                {**_profile_schema_fields(d), **_learning_schema_fields(d)}
            ),
        )

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_control()
        d = self._data
        return self.async_show_form(
            step_id="power",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "charger_max_power_w", default=_d(d, "charger_max_power_w")
                    ): _number(0, 50_000, 50, "W"),
                    vol.Required(
                        "charger_efficiency", default=_d(d, "charger_efficiency")
                    ): _number(0.1, 1.0, 0.01),
                    vol.Required(
                        "charger_standby_power_w",
                        default=_d(d, "charger_standby_power_w"),
                    ): _number(0, 500, 1, "W"),
                    vol.Required(
                        "inverter_max_power_w", default=_d(d, "inverter_max_power_w")
                    ): _number(0, 50_000, 50, "W"),
                    vol.Required(
                        "inverter_efficiency", default=_d(d, "inverter_efficiency")
                    ): _number(0.1, 1.0, 0.01),
                    vol.Required(
                        "inverter_standby_power_w",
                        default=_d(d, "inverter_standby_power_w"),
                    ): _number(0, 500, 1, "W"),
                    vol.Required(
                        "inverter_min_soc_percent",
                        default=_d(d, "inverter_min_soc_percent"),
                    ): _number(0, 100, 1, "%"),
                }
            ),
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_support_entities(user_input)
            if error is None:
                self._data.update(user_input)
                return self.async_create_entry(title="Battery Manager", data=self._data)
            errors["base"] = error
        d = self._data
        return self.async_show_form(
            step_id="control",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "soc_buffer_percent", default=_d(d, "soc_buffer_percent")
                    ): _number(0, 30, 1, "%"),
                    vol.Required(
                        "hysteresis_percent", default=_d(d, "hysteresis_percent")
                    ): _number(0, 10, 0.5, "%"),
                    vol.Required(
                        "threshold_inertia_percent",
                        default=_d(d, "threshold_inertia_percent"),
                    ): _number(0, 10, 0.5, "%"),
                    vol.Required(
                        "min_switch_interval_s", default=_d(d, "min_switch_interval_s")
                    ): _number(0, 3600, 10, "s"),
                    vol.Optional(CONF_SUPPORT_DC48_SWITCH): _entity("switch"),
                    vol.Required(
                        CONF_SUPPORT_DC48_POWER_W,
                        default=_d(d, CONF_SUPPORT_DC48_POWER_W),
                    ): _number(0, 1000, 5, "W"),
                    vol.Optional(CONF_SUPPORT_DC24_SWITCH): _entity("switch"),
                    vol.Optional(CONF_SUPPORT_DC24_POWER_ENTITY): _entity("sensor"),
                    vol.Optional(CONF_DCDC_SWITCH): _entity("switch"),
                    vol.Required(
                        CONF_SUPPORT_SWITCH_DELAY_S,
                        default=_d(d, CONF_SUPPORT_SWITCH_DELAY_S),
                    ): _number(1, 30, 1, "s"),
                }
            ),
        )


class BatteryManagerOptionsFlow(OptionsFlow):
    """Adjust planner tuning and support paths after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = (
                _validate_support_entities(user_input)
                or _validate_learning_sources(user_input)
                or _validate_buffer_clamps(user_input)
            )
            if error is None:
                # Cleared selector fields are absent from user_input. Store an
                # explicit None/[] so the options override the value still
                # present in entry.data (raw_config merges data + options).
                for key in (
                    *_SUPPORT_SWITCH_KEYS,
                    CONF_SUPPORT_DC24_POWER_ENTITY,
                    *_LEARNING_SINGLE_KEYS,
                    CONF_WORKDAY_ENTITY,
                ):
                    user_input.setdefault(key, None)
                for key in _LEARNING_MULTI_KEYS:
                    user_input.setdefault(key, [])
                return self.async_create_entry(title="", data=user_input)
            errors["base"] = error

        # On a validation error, re-render with the just-entered values.
        current = {
            **self.config_entry.data,
            **self.config_entry.options,
            **(user_input or {}),
        }
        schema: dict[Any, Any] = {
            vol.Required(
                "soc_buffer_percent", default=_d(current, "soc_buffer_percent")
            ): _number(0, 30, 1, "%"),
            vol.Required(
                "hysteresis_percent", default=_d(current, "hysteresis_percent")
            ): _number(0, 10, 0.5, "%"),
            vol.Required(
                "threshold_inertia_percent",
                default=_d(current, "threshold_inertia_percent"),
            ): _number(0, 10, 0.5, "%"),
            vol.Required(
                "min_switch_interval_s", default=_d(current, "min_switch_interval_s")
            ): _number(0, 3600, 10, "s"),
            vol.Required(
                CONF_SUPPORT_DC48_POWER_W,
                default=_d(current, CONF_SUPPORT_DC48_POWER_W),
            ): _number(0, 1000, 5, "W"),
            vol.Required(
                CONF_SUPPORT_SWITCH_DELAY_S,
                default=_d(current, CONF_SUPPORT_SWITCH_DELAY_S),
            ): _number(1, 30, 1, "s"),
        }
        for key in _SUPPORT_SWITCH_KEYS:
            # suggested_value (not default) keeps the field clearable in the UI.
            schema[
                vol.Optional(key, description={"suggested_value": current.get(key)})
            ] = _entity("switch")
        schema[
            vol.Optional(
                CONF_SUPPORT_DC24_POWER_ENTITY,
                description={
                    "suggested_value": current.get(CONF_SUPPORT_DC24_POWER_ENTITY)
                },
            )
        ] = _entity("sensor")

        # Fallback profile + learned-consumption sources (CONSUMPTION_FORECAST)
        schema.update(_profile_schema_fields(current))
        schema.update(_learning_schema_fields(current))
        schema[
            vol.Required(
                CONF_LEARNING_WINDOW_DAYS,
                default=_d(current, CONF_LEARNING_WINDOW_DAYS),
            )
        ] = _number(14, 120, 1, "d")
        schema[
            vol.Required(
                CONF_LEARNING_MAX_AGE_DAYS,
                default=_d(current, CONF_LEARNING_MAX_AGE_DAYS),
            )
        ] = _number(3, 60, 1, "d")
        schema[
            vol.Required(
                CONF_PROFILE_HALF_LIFE_DAYS,
                default=_d(current, CONF_PROFILE_HALF_LIFE_DAYS),
            )
        ] = _number(7, 120, 1, "d")
        schema[
            vol.Required(
                CONF_BUFFER_MIN_PERCENT,
                default=_d(current, CONF_BUFFER_MIN_PERCENT),
            )
        ] = _number(0, 10, 0.5, "%")
        schema[
            vol.Required(
                CONF_BUFFER_MAX_PERCENT,
                default=_d(current, CONF_BUFFER_MAX_PERCENT),
            )
        ] = _number(5, 30, 0.5, "%")

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )


class SurplusLoadSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a surplus load (Fossibot, dehumidifier, ...).

    Two steps: the storage fields (capacity, target SOC, SOC sensor) only
    appear when the load is energy-limited — for continuous consumers like
    a dehumidifier they are meaningless (operator wish, 2026-07-05).
    """

    _basic: dict[str, Any]
    _existing: dict[str, Any]
    _is_reconfigure: bool

    # Storage-step keys, preserved across the dialog when the step is
    # skipped so toggling "energy limited" off and on keeps the values.
    _STORAGE_KEYS = (CONF_LOAD_CAPACITY_WH, CONF_LOAD_TARGET_SOC)

    def _basic_schema(self, data: dict[str, Any]) -> vol.Schema:
        def dv(key):
            return data.get(key, DEFAULT_LOAD_CONFIG.get(key))

        schema: dict[Any, Any] = {
            vol.Required(CONF_LOAD_NAME, default=data.get(CONF_LOAD_NAME, "")): str,
            vol.Required(CONF_LOAD_POWER_W, default=dv(CONF_LOAD_POWER_W)): _number(
                1, 10_000, 10, "W"
            ),
            vol.Required(
                CONF_LOAD_BATTERY_TOLERANCE, default=dv(CONF_LOAD_BATTERY_TOLERANCE)
            ): _number(0, 50, 1, "%"),
            vol.Required(
                CONF_LOAD_MIN_RUNTIME_MIN, default=dv(CONF_LOAD_MIN_RUNTIME_MIN)
            ): _number(0, 240, 5, "min"),
            vol.Required(
                CONF_LOAD_ENERGY_LIMITED, default=dv(CONF_LOAD_ENERGY_LIMITED)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_LOAD_IN_HOUSE, default=dv(CONF_LOAD_IN_HOUSE)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_LOAD_POWER_WARNING_PCT, default=dv(CONF_LOAD_POWER_WARNING_PCT)
            ): _number(0, 200, 5, "%"),
        }
        for key, domain in (
            (CONF_LOAD_POWER_ENTITY, "sensor"),
            (CONF_LOAD_AVAILABILITY_ENTITY, None),
            (CONF_LOAD_CONTROL_SWITCH, "switch"),
            (CONF_LOAD_CHARGE_ENABLE, ["input_boolean", "switch"]),
        ):
            # suggested_value (not default) keeps the field clearable in the UI.
            schema[
                vol.Optional(key, description={"suggested_value": data.get(key)})
            ] = _entity(domain)
        schema[
            vol.Required(
                CONF_LOAD_INPUT_OFF_POLICY, default=dv(CONF_LOAD_INPUT_OFF_POLICY)
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=INPUT_OFF_POLICIES,
                translation_key="input_off_policy",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        return vol.Schema(schema)

    def _storage_schema(self, data: dict[str, Any]) -> vol.Schema:
        def dv(key):
            return data.get(key, DEFAULT_LOAD_CONFIG.get(key))

        return vol.Schema(
            {
                vol.Required(
                    CONF_LOAD_CAPACITY_WH, default=dv(CONF_LOAD_CAPACITY_WH)
                ): _number(0, 100_000, 100, "Wh"),
                vol.Required(
                    CONF_LOAD_TARGET_SOC, default=dv(CONF_LOAD_TARGET_SOC)
                ): _number(0, 100, 1, "%"),
                vol.Optional(
                    CONF_LOAD_SOC_ENTITY,
                    description={"suggested_value": data.get(CONF_LOAD_SOC_ENTITY)},
                ): _entity("sensor"),
            }
        )

    def _finish(self, storage_input: dict[str, Any]) -> SubentryFlowResult:
        # Preserved storage values (add: defaults) underlie the new input,
        # so a load toggled to unlimited keeps them for a later toggle back.
        data = {
            key: self._existing.get(key, DEFAULT_LOAD_CONFIG.get(key))
            for key in self._STORAGE_KEYS
        }
        if CONF_LOAD_SOC_ENTITY in self._existing:
            data[CONF_LOAD_SOC_ENTITY] = self._existing[CONF_LOAD_SOC_ENTITY]
        data.update(self._basic)
        data.update(storage_input)
        title = data.pop(CONF_LOAD_NAME)
        if self._is_reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                data=data,
            )
        return self.async_create_entry(title=title, data=data)

    async def _handle_basic(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_load_control(user_input)
            if error is None:
                self._basic = user_input
                if user_input.get(CONF_LOAD_ENERGY_LIMITED):
                    return await self.async_step_storage()
                return self._finish({})
            errors["base"] = error
        data = user_input if user_input is not None else self._existing
        return self.async_show_form(
            step_id=step_id, data_schema=self._basic_schema(data), errors=errors
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if not hasattr(self, "_existing"):
            self._existing = {}
            self._is_reconfigure = False
        return await self._handle_basic("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if not hasattr(self, "_existing"):
            subentry = self._get_reconfigure_subentry()
            self._existing = {**subentry.data, CONF_LOAD_NAME: subentry.title}
            self._is_reconfigure = True
        return await self._handle_basic("reconfigure", user_input)

    async def async_step_storage(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self._finish(user_input)
        return self.async_show_form(
            step_id="storage", data_schema=self._storage_schema(self._existing)
        )


class ApplianceSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a household appliance (washer, dishwasher)."""

    def _schema(self, data: dict[str, Any]) -> vol.Schema:
        def dv(key):
            return data.get(key, DEFAULT_APPLIANCE_CONFIG.get(key))

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_APPLIANCE_NAME, default=data.get(CONF_APPLIANCE_NAME, "")
            ): str,
        }
        schema[
            vol.Required(
                CONF_APPLIANCE_DETECTION_ENTITY,
                description={
                    "suggested_value": data.get(CONF_APPLIANCE_DETECTION_ENTITY)
                },
            )
        ] = _entity()
        schema.update(
            {
                vol.Required(
                    CONF_APPLIANCE_POWER_THRESHOLD_W,
                    default=dv(CONF_APPLIANCE_POWER_THRESHOLD_W),
                ): _number(1, 3000, 1, "W"),
                vol.Required(
                    CONF_APPLIANCE_RUN_ENERGY_WH,
                    default=dv(CONF_APPLIANCE_RUN_ENERGY_WH),
                ): _number(10, 10_000, 10, "Wh"),
                vol.Required(
                    CONF_APPLIANCE_RUN_DURATION_H,
                    default=dv(CONF_APPLIANCE_RUN_DURATION_H),
                ): _number(0.25, 12, 0.25, "h"),
                vol.Required(
                    CONF_APPLIANCE_OPPORTUNISTIC,
                    default=dv(CONF_APPLIANCE_OPPORTUNISTIC),
                ): selector.BooleanSelector(),
            }
        )
        return vol.Schema(schema)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            title = user_input.pop(CONF_APPLIANCE_NAME)
            return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=self._schema({}))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            title = user_input.pop(CONF_APPLIANCE_NAME)
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=title, data=user_input
            )
        data = {**subentry.data, CONF_APPLIANCE_NAME: subentry.title}
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._schema(data)
        )
