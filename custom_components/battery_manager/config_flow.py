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
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_AC_LOAD_ENTITY,
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_NAME,
    CONF_APPLIANCE_OFF_THRESHOLD_W,
    CONF_APPLIANCE_OPPORTUNISTIC,
    CONF_APPLIANCE_POWER_THRESHOLD_W,
    CONF_APPLIANCE_RUN_DURATION_H,
    CONF_APPLIANCE_RUN_ENERGY_WH,
    CONF_BATTERY_CELLS_SERIES,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_BUFFER_MAX_PERCENT,
    CONF_BUFFER_MIN_PERCENT,
    CONF_DC24_SHARE_PERCENT,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
    CONF_DC_LOAD_ENTITY,
    CONF_DCDC_EFFICIENCY,
    CONF_DCDC_MAX_CURRENT_A,
    CONF_DCDC_OUTPUT_VOLTAGE_V,
    CONF_DCDC_SWITCH,
    CONF_GATE_SOC_PERCENT,
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
    CONF_LOAD_MIN_OFF_MIN,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_NAME,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_POWER_WARNING_DWELL_MIN,
    CONF_LOAD_POWER_WARNING_PCT,
    CONF_LOAD_PRIORITY,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TANK_FULL_RUNTIME_MIN,
    CONF_LOAD_TARGET_SOC,
    CONF_NATIVE48_BASE_W,
    CONF_PREDRAIN_PV_CONFIDENCE,
    CONF_PROFILE_HALF_LIFE_DAYS,
    CONF_PSU24_EFFICIENCY,
    CONF_PSU24_MAX_CURRENT_A,
    CONF_PSU24_OUTPUT_VOLTAGE_V,
    CONF_PSU48_CTRL_LOG_ONLY,
    CONF_PSU48_EFFICIENCY,
    CONF_PSU48_MAX_CURRENT_A,
    CONF_PSU48_OFF_VOLTAGE_V,
    CONF_PSU48_ON_VOLTAGE_V,
    CONF_PSU48_OUTPUT_VOLTAGE_V,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_MODE,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_PV_WINDOW_END_HOUR,
    CONF_SOC_ENTITY,
    CONF_STRONG_PV_CUTOFF_W,
    CONF_SUPPORT_DC24_ACTIVATE_SOC,
    CONF_SUPPORT_DC24_POWER_ENTITY,
    CONF_SUPPORT_DC24_RECOVERY_SOC,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_ACTIVATE_SOC,
    CONF_SUPPORT_DC48_POWER_W,
    CONF_SUPPORT_DC48_RECOVERY_SOC,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    CONF_UPPER_PV_RESERVE,
    CONF_WARNING_NOTIFY_ON_RESOLVE,
    CONF_WARNING_NOTIFY_TARGETS,
    CONF_WORKDAY_ENTITY,
    DEFAULT_APPLIANCE_CONFIG,
    DEFAULT_CONFIG,
    DEFAULT_LOAD_CONFIG,
    DOMAIN,
    INPUT_OFF_POLICIES,
    INPUT_OFF_POLICY_KEEP,
    PV_FORECAST_MODES,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
)
from .coordinator import ordered_load_subentries

# Collapsible section groups for the options flow (visual grouping only;
# their fields are nested under the section key in the submitted data and
# flattened again by _flatten_sections).
SECTION_TUNING = "planner_tuning"
SECTION_PROFILE = "consumption_profile"
SECTION_LEARNING = "consumption_learning"
SECTION_SUPPORT = "support_paths"
SECTION_DEVICES = "dc_devices"
SECTION_NOTIFY = "notifications"
_OPTION_SECTIONS = (
    SECTION_TUNING,
    SECTION_PROFILE,
    SECTION_LEARNING,
    SECTION_SUPPORT,
    SECTION_DEVICES,
    SECTION_NOTIFY,
)


def _flatten_sections(user_input: dict[str, Any]) -> dict[str, Any]:
    """Merge collapsible section groups back into a flat dict.

    HA nests each section's fields under the section key in the submitted
    data; the rest of the integration reads a flat config, so undo it.
    """
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in _OPTION_SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


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


def _notify_services(hass) -> list[str]:
    """Registered `notify` service names, minus the ones that are not push
    targets: `send_message` needs an entity_id (would raise) and the
    persistent_notification dispatcher is not a phone."""
    try:
        services = hass.services.async_services_for_domain("notify")
    except AttributeError:  # pre-2024.7 fallback
        services = hass.services.async_services().get("notify", {})
    return sorted(
        name
        for name in services
        if name not in ("send_message", "persistent_notification")
    )


def _notify_targets(options: list[str]):
    """Multi-select of notify service names. custom_value keeps a stored
    target that isn't registered right now (e.g. a phone offline at setup)."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


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


def _validate_pv_windows(data: dict[str, Any]) -> str | None:
    """The two PV windows must be strictly ordered
    (morning_start < morning_end < afternoon_end); a mis-ordered/degenerate
    window would otherwise silently discard a fixed fraction of every day's
    forecast (the core also renormalizes defensively)."""
    ms = data.get("pv_morning_start_hour")
    me = data.get("pv_morning_end_hour")
    ae = data.get("pv_afternoon_end_hour")
    if (
        ms is not None
        and me is not None
        and ae is not None
        and not (int(ms) < int(me) < int(ae))
    ):
        return "pv_windows_out_of_order"
    return None


def _validate_controller_voltages(data: dict[str, Any]) -> str | None:
    """The R2 controller needs off_voltage strictly above on_voltage, else
    the hysteresis band collapses and it would chatter (docs/DC_TOPOLOGY §6)."""
    on = data.get(CONF_PSU48_ON_VOLTAGE_V)
    off = data.get(CONF_PSU48_OFF_VOLTAGE_V)
    if on is not None and off is not None and float(off) <= float(on):
        return "controller_off_below_on"
    return None


def _validate_support_hysteresis(data: dict[str, Any]) -> str | None:
    """The four absolute escalation SOC thresholds must form a sane hysteresis
    ladder (D-A9): each stage needs activate < recovery for a real dead band,
    and the deeper 48 V last-resort stage must sit at or below the 24 V stage
    (both its activate and its recovery), so it engages no later and releases
    no later than the 24 V support.
    """
    a24 = data.get(CONF_SUPPORT_DC24_ACTIVATE_SOC)
    r24 = data.get(CONF_SUPPORT_DC24_RECOVERY_SOC)
    a48 = data.get(CONF_SUPPORT_DC48_ACTIVATE_SOC)
    r48 = data.get(CONF_SUPPORT_DC48_RECOVERY_SOC)
    if any(v is None for v in (a24, r24, a48, r48)):
        return None
    a24, r24, a48, r48 = float(a24), float(r24), float(a48), float(r48)
    if a24 >= r24:
        return "support_dc24_recovery_not_above_activate"
    if a48 >= r48:
        return "support_dc48_recovery_not_above_activate"
    if a48 > a24:
        return "support_dc48_activate_above_dc24"
    if r48 > r24:
        return "support_dc48_recovery_above_dc24"
    return None


def _device_param_fields(current: dict[str, Any]) -> dict[Any, Any]:
    """F-N3 two-bus device parameters (docs/DC_TOPOLOGY.md, phase 2).

    Shared by the base control step and the options flow. Neutral defaults
    (share 100 %, efficiency 1.0, 0 A = uncapped) leave planning unchanged
    until the operator enters real nameplate values.
    """
    schema: dict[Any, Any] = {
        vol.Optional(
            CONF_BATTERY_VOLTAGE_ENTITY,
            description={"suggested_value": current.get(CONF_BATTERY_VOLTAGE_ENTITY)},
        ): _entity("sensor"),
        vol.Required(
            CONF_NATIVE48_BASE_W, default=_d(current, CONF_NATIVE48_BASE_W)
        ): _number(0, 10_000, 1, "W"),
        vol.Required(
            CONF_DC24_SHARE_PERCENT, default=_d(current, CONF_DC24_SHARE_PERCENT)
        ): _number(0, 100, 5, "%"),
    }
    for volt_key, eta_key, amp_key, amp_max in (
        (
            CONF_DCDC_OUTPUT_VOLTAGE_V,
            CONF_DCDC_EFFICIENCY,
            CONF_DCDC_MAX_CURRENT_A,
            100,
        ),
        (
            CONF_PSU24_OUTPUT_VOLTAGE_V,
            CONF_PSU24_EFFICIENCY,
            CONF_PSU24_MAX_CURRENT_A,
            100,
        ),
        (
            CONF_PSU48_OUTPUT_VOLTAGE_V,
            CONF_PSU48_EFFICIENCY,
            CONF_PSU48_MAX_CURRENT_A,
            20,
        ),
    ):
        schema[vol.Required(volt_key, default=_d(current, volt_key))] = _number(
            0, 60, 0.01, "V"
        )
        schema[vol.Required(eta_key, default=_d(current, eta_key))] = _number(
            0.5, 1.0, 0.01
        )
        schema[vol.Required(amp_key, default=_d(current, amp_key))] = _number(
            0, amp_max, 0.05, "A"
        )
    schema[
        vol.Required(
            CONF_BATTERY_CELLS_SERIES, default=_d(current, CONF_BATTERY_CELLS_SERIES)
        )
    ] = _number(4, 20, 1)
    schema[
        vol.Required(CONF_GATE_SOC_PERCENT, default=_d(current, CONF_GATE_SOC_PERCENT))
    ] = _number(0, 100, 1, "%")
    # R2 voltage controller for the manual 48 V mode (docs/DC_TOPOLOGY.md §6).
    schema[
        vol.Required(
            CONF_PSU48_ON_VOLTAGE_V, default=_d(current, CONF_PSU48_ON_VOLTAGE_V)
        )
    ] = _number(40, 60, 0.01, "V")
    schema[
        vol.Required(
            CONF_PSU48_OFF_VOLTAGE_V, default=_d(current, CONF_PSU48_OFF_VOLTAGE_V)
        )
    ] = _number(40, 60, 0.01, "V")
    schema[
        vol.Required(
            CONF_PSU48_CTRL_LOG_ONLY, default=_d(current, CONF_PSU48_CTRL_LOG_ONLY)
        )
    ] = selector.BooleanSelector()
    return schema


def _predrain_schema_fields(current: dict[str, Any]) -> dict[Any, Any]:
    """F-PREDRAIN two-buffer pre-drain tuning (docs/F-PREDRAIN.md §3).

    Shared by the base control step and the options flow, appended to the
    planner-tuning section. The recommended live values live in DEFAULT_CONFIG
    (resolved via _d), so the form default and the coordinator absent-key
    fallback resolve to the SAME value — a no-change reconfigure keeps the
    pre-drain behaviour unchanged (v0.7.15 review trap). pv_window_end_hour has
    no default: cleared/empty = derive the window end from the forecast shape.
    """
    return {
        vol.Required(
            CONF_PV_FORECAST_MODE, default=_d(current, CONF_PV_FORECAST_MODE)
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=PV_FORECAST_MODES,
                translation_key="pv_forecast_mode",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        # import_trade_ratio was retired by F-STRICT-SURPLUS R1 (2026-07-19):
        # loads may never buy grid import, so there is nothing left to tune.
        vol.Required(
            CONF_PREDRAIN_PV_CONFIDENCE,
            default=_d(current, CONF_PREDRAIN_PV_CONFIDENCE),
        ): _number(0.2, 1.0, 0.05),
        vol.Required(
            CONF_UPPER_PV_RESERVE, default=_d(current, CONF_UPPER_PV_RESERVE)
        ): _number(1.0, 1.5, 0.05),
        vol.Required(
            CONF_STRONG_PV_CUTOFF_W, default=_d(current, CONF_STRONG_PV_CUTOFF_W)
        ): _number(50, 1000, 10, "W"),
        # suggested_value (not default) keeps the optional override clearable.
        vol.Optional(
            CONF_PV_WINDOW_END_HOUR,
            description={"suggested_value": current.get(CONF_PV_WINDOW_END_HOUR)},
        ): _number(10, 20, 1, "h"),
    }


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
    MINOR_VERSION = 3

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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Repoint the four base entities (SOC + the three PV forecast sources)
        without re-adding the entry (F-RECONFIGURE-PV). Only these keys change;
        every other data key (battery, control, support, DC) and all load
        subentries — which live in `entry.subentries`, not `entry.data` — are
        preserved, so a PV-source cutover keeps the loads' priorities, learned
        power and runtime counters. Fields are pre-filled with the current pick
        via `suggested_value` (not `default`, so the field stays clearable)."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry, data={**entry.data, **user_input}
            )
        current = entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        key,
                        description={"suggested_value": current.get(key)},
                    ): _entity("sensor")
                    for key in (
                        CONF_SOC_ENTITY,
                        CONF_PV_FORECAST_TODAY,
                        CONF_PV_FORECAST_TOMORROW,
                        CONF_PV_FORECAST_DAY_AFTER,
                    )
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
            error = _validate_pv_windows(user_input)
            if error is not None:
                return self.async_show_form(
                    step_id="pv",
                    data_schema=self._pv_schema(),
                    errors={"base": error},
                )
            self._data.update(user_input)
            return await self.async_step_consumers()
        return self.async_show_form(step_id="pv", data_schema=self._pv_schema())

    def _pv_schema(self) -> vol.Schema:
        d = self._data
        return vol.Schema(
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
        )

    async def async_step_consumers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _flatten_sections(user_input)
            error = _validate_learning_sources(data)
            if error is None:
                self._data.update(data)
                return await self.async_step_power()
            errors["base"] = error
        # On a validation error, re-render with the just-entered values.
        d = {**self._data, **(_flatten_sections(user_input) if user_input else {})}
        return self.async_show_form(
            step_id="consumers",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(SECTION_PROFILE): section(
                        vol.Schema(_profile_schema_fields(d)), {"collapsed": False}
                    ),
                    vol.Required(SECTION_LEARNING): section(
                        vol.Schema(_learning_schema_fields(d)), {"collapsed": True}
                    ),
                }
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
            data = _flatten_sections(user_input)
            error = (
                _validate_support_entities(data)
                or _validate_controller_voltages(data)
                or _validate_support_hysteresis(data)
            )
            if error is None:
                self._data.update(data)
                return self.async_create_entry(title="Battery Manager", data=self._data)
            errors["base"] = error
        d = {**self._data, **(_flatten_sections(user_input) if user_input else {})}
        tuning = {
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
        }
        tuning.update(_predrain_schema_fields(d))  # F-PREDRAIN pre-drain (WP3)
        support = {
            vol.Optional(CONF_SUPPORT_DC48_SWITCH): _entity("switch"),
            vol.Required(
                CONF_SUPPORT_DC48_POWER_W, default=_d(d, CONF_SUPPORT_DC48_POWER_W)
            ): _number(0, 1000, 5, "W"),
            vol.Optional(CONF_SUPPORT_DC24_SWITCH): _entity("switch"),
            vol.Optional(CONF_SUPPORT_DC24_POWER_ENTITY): _entity("sensor"),
            vol.Optional(CONF_DCDC_SWITCH): _entity("switch"),
            vol.Required(
                CONF_SUPPORT_SWITCH_DELAY_S, default=_d(d, CONF_SUPPORT_SWITCH_DELAY_S)
            ): _number(1, 30, 1, "s"),
            vol.Required(
                CONF_SUPPORT_DC24_ACTIVATE_SOC,
                default=_d(d, CONF_SUPPORT_DC24_ACTIVATE_SOC),
            ): _number(0, 100, 0.5, "%"),
            vol.Required(
                CONF_SUPPORT_DC24_RECOVERY_SOC,
                default=_d(d, CONF_SUPPORT_DC24_RECOVERY_SOC),
            ): _number(0, 100, 0.5, "%"),
            vol.Required(
                CONF_SUPPORT_DC48_ACTIVATE_SOC,
                default=_d(d, CONF_SUPPORT_DC48_ACTIVATE_SOC),
            ): _number(0, 100, 0.5, "%"),
            vol.Required(
                CONF_SUPPORT_DC48_RECOVERY_SOC,
                default=_d(d, CONF_SUPPORT_DC48_RECOVERY_SOC),
            ): _number(0, 100, 0.5, "%"),
        }
        return self.async_show_form(
            step_id="control",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(SECTION_TUNING): section(
                        vol.Schema(tuning), {"collapsed": False}
                    ),
                    vol.Required(SECTION_SUPPORT): section(
                        vol.Schema(support), {"collapsed": True}
                    ),
                    vol.Required(SECTION_DEVICES): section(
                        vol.Schema(_device_param_fields(d)), {"collapsed": True}
                    ),
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
            data = _flatten_sections(user_input)
            error = (
                _validate_support_entities(data)
                or _validate_learning_sources(data)
                or _validate_buffer_clamps(data)
                or _validate_controller_voltages(data)
                or _validate_support_hysteresis(data)
            )
            if error is None:
                # Cleared selector fields are absent from the input. Store an
                # explicit None/[] so the options override the value still
                # present in entry.data (raw_config merges data + options).
                for key in (
                    *_SUPPORT_SWITCH_KEYS,
                    CONF_SUPPORT_DC24_POWER_ENTITY,
                    CONF_BATTERY_VOLTAGE_ENTITY,
                    # Cleared = unset the site override so the window end derives
                    # from the forecast again (F-PREDRAIN F4).
                    CONF_PV_WINDOW_END_HOUR,
                    *_LEARNING_SINGLE_KEYS,
                    CONF_WORKDAY_ENTITY,
                ):
                    data.setdefault(key, None)
                for key in (*_LEARNING_MULTI_KEYS, CONF_WARNING_NOTIFY_TARGETS):
                    data.setdefault(key, [])
                return self.async_create_entry(title="", data=data)
            errors["base"] = error

        # On a validation error, re-render with the just-entered values.
        current = {
            **self.config_entry.data,
            **self.config_entry.options,
            **(_flatten_sections(user_input) if user_input else {}),
        }

        tuning = {
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
        }
        tuning.update(_predrain_schema_fields(current))  # F-PREDRAIN pre-drain (WP3)

        support: dict[Any, Any] = {
            vol.Required(
                CONF_SUPPORT_DC48_POWER_W,
                default=_d(current, CONF_SUPPORT_DC48_POWER_W),
            ): _number(0, 1000, 5, "W"),
            vol.Required(
                CONF_SUPPORT_SWITCH_DELAY_S,
                default=_d(current, CONF_SUPPORT_SWITCH_DELAY_S),
            ): _number(1, 30, 1, "s"),
            vol.Required(
                CONF_SUPPORT_DC24_ACTIVATE_SOC,
                default=_d(current, CONF_SUPPORT_DC24_ACTIVATE_SOC),
            ): _number(0, 100, 0.5, "%"),
            vol.Required(
                CONF_SUPPORT_DC24_RECOVERY_SOC,
                default=_d(current, CONF_SUPPORT_DC24_RECOVERY_SOC),
            ): _number(0, 100, 0.5, "%"),
            vol.Required(
                CONF_SUPPORT_DC48_ACTIVATE_SOC,
                default=_d(current, CONF_SUPPORT_DC48_ACTIVATE_SOC),
            ): _number(0, 100, 0.5, "%"),
            vol.Required(
                CONF_SUPPORT_DC48_RECOVERY_SOC,
                default=_d(current, CONF_SUPPORT_DC48_RECOVERY_SOC),
            ): _number(0, 100, 0.5, "%"),
        }
        for key in _SUPPORT_SWITCH_KEYS:
            # suggested_value (not default) keeps the field clearable in the UI.
            support[
                vol.Optional(key, description={"suggested_value": current.get(key)})
            ] = _entity("switch")
        support[
            vol.Optional(
                CONF_SUPPORT_DC24_POWER_ENTITY,
                description={
                    "suggested_value": current.get(CONF_SUPPORT_DC24_POWER_ENTITY)
                },
            )
        ] = _entity("sensor")

        learning = dict(_learning_schema_fields(current))
        learning[
            vol.Required(
                CONF_LEARNING_WINDOW_DAYS,
                default=_d(current, CONF_LEARNING_WINDOW_DAYS),
            )
        ] = _number(14, 120, 1, "d")
        learning[
            vol.Required(
                CONF_LEARNING_MAX_AGE_DAYS,
                default=_d(current, CONF_LEARNING_MAX_AGE_DAYS),
            )
        ] = _number(3, 60, 1, "d")
        learning[
            vol.Required(
                CONF_PROFILE_HALF_LIFE_DAYS,
                default=_d(current, CONF_PROFILE_HALF_LIFE_DAYS),
            )
        ] = _number(7, 120, 1, "d")
        learning[
            vol.Required(
                CONF_BUFFER_MIN_PERCENT, default=_d(current, CONF_BUFFER_MIN_PERCENT)
            )
        ] = _number(0, 10, 0.5, "%")
        learning[
            vol.Required(
                CONF_BUFFER_MAX_PERCENT, default=_d(current, CONF_BUFFER_MAX_PERCENT)
            )
        ] = _number(5, 30, 0.5, "%")

        # Push notifications for load power warnings (operator wish 2026-07-12):
        # a single global list of notify targets + a resolve-ping toggle.
        notify = {
            vol.Optional(
                CONF_WARNING_NOTIFY_TARGETS,
                description={
                    "suggested_value": current.get(CONF_WARNING_NOTIFY_TARGETS)
                },
            ): _notify_targets(_notify_services(self.hass)),
            vol.Required(
                CONF_WARNING_NOTIFY_ON_RESOLVE,
                default=_d(current, CONF_WARNING_NOTIFY_ON_RESOLVE),
            ): selector.BooleanSelector(),
        }

        # Grouped into collapsible sections for readability (F-N UX request).
        schema = {
            vol.Required(SECTION_TUNING): section(
                vol.Schema(tuning), {"collapsed": False}
            ),
            vol.Required(SECTION_PROFILE): section(
                vol.Schema(_profile_schema_fields(current)), {"collapsed": True}
            ),
            vol.Required(SECTION_LEARNING): section(
                vol.Schema(learning), {"collapsed": True}
            ),
            vol.Required(SECTION_SUPPORT): section(
                vol.Schema(support), {"collapsed": True}
            ),
            vol.Required(SECTION_DEVICES): section(
                vol.Schema(_device_param_fields(current)), {"collapsed": True}
            ),
            vol.Required(SECTION_NOTIFY): section(
                vol.Schema(notify), {"collapsed": True}
            ),
        }
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )


class SurplusLoadSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a surplus load (Fossibot, dehumidifier, ...).

    Two steps: the storage fields — capacity, target SOC, SOC sensor and
    the whole charging-path block (input switch, charge enable, input-off
    policy, docs/LOAD_CONTROL.md §2/§3) — only appear when the load is
    energy-limited. For continuous consumers like a dehumidifier they are
    meaningless (operator wish, 2026-07-05).
    """

    _basic: dict[str, Any]
    _existing: dict[str, Any]
    _is_reconfigure: bool

    # Storage-step keys, preserved across the dialog when the step is
    # skipped so toggling "energy limited" off and on keeps the values.
    _STORAGE_KEYS = (
        CONF_LOAD_CAPACITY_WH,
        CONF_LOAD_TARGET_SOC,
    )
    _STORAGE_ENTITY_KEYS = (
        CONF_LOAD_SOC_ENTITY,
        CONF_LOAD_CHARGE_ENABLE,
    )

    def _basic_schema(self, data: dict[str, Any]) -> vol.Schema:
        def dv(key):
            return data.get(key, DEFAULT_LOAD_CONFIG.get(key))

        # F-LOAD-PRIORITY R2: the selector range is honest — 1..N over all load
        # subentries, counting the one this flow is about to create. Default:
        # the CURRENT effective position (reconfigure, injected by
        # async_step_reconfigure) or N (create — new loads append, exactly the
        # pre-v0.8.2 semantics).
        n_loads = sum(
            1
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_LOAD
        ) + (0 if self._is_reconfigure else 1)
        schema: dict[Any, Any] = {
            vol.Required(CONF_LOAD_NAME, default=data.get(CONF_LOAD_NAME, "")): str,
            vol.Required(
                CONF_LOAD_PRIORITY,
                default=int(data.get(CONF_LOAD_PRIORITY, n_loads)),
            ): _number(1, max(n_loads, 1), 1),
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
                # Back-compat: a pre-0.7.15 load lacks the key — mirror the
                # coordinator fallback (min_off == min_runtime) so a no-change
                # reconfigure never silently shortens the OFF dwell.
                CONF_LOAD_MIN_OFF_MIN,
                default=data.get(
                    CONF_LOAD_MIN_OFF_MIN,
                    data.get(
                        CONF_LOAD_MIN_RUNTIME_MIN,
                        DEFAULT_LOAD_CONFIG[CONF_LOAD_MIN_OFF_MIN],
                    ),
                ),
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
            vol.Required(
                CONF_LOAD_POWER_WARNING_DWELL_MIN,
                default=dv(CONF_LOAD_POWER_WARNING_DWELL_MIN),
            ): _number(0, 240, 5, "min"),
            # V6 (F-TANK): opt-in consumable-tank runtime. 0 = off (default);
            # only meaningful for a load with power feedback. A generous upper
            # bound (a large dehumidifier tank can be many hours of runtime).
            vol.Required(
                CONF_LOAD_TANK_FULL_RUNTIME_MIN,
                default=dv(CONF_LOAD_TANK_FULL_RUNTIME_MIN),
            ): _number(0, 6000, 15, "min"),
        }
        for key, domain in (
            (CONF_LOAD_POWER_ENTITY, "sensor"),
            (CONF_LOAD_AVAILABILITY_ENTITY, None),
        ):
            # suggested_value (not default) keeps the field clearable in the UI.
            schema[
                vol.Optional(key, description={"suggested_value": data.get(key)})
            ] = _entity(domain)
        # The control switch and its off policy apply to ANY controlled load
        # (a continuous consumer like a dehumidifier is switched by BM too), so
        # they live on the basic step; the charge-enable gate stays on the
        # storage step (only energy-limited powerstations gate charging).
        schema[
            vol.Optional(
                CONF_LOAD_CONTROL_SWITCH,
                description={"suggested_value": data.get(CONF_LOAD_CONTROL_SWITCH)},
            )
        ] = _entity("switch")
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

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_LOAD_CAPACITY_WH, default=dv(CONF_LOAD_CAPACITY_WH)
            ): _number(0, 100_000, 100, "Wh"),
            vol.Required(
                CONF_LOAD_TARGET_SOC, default=dv(CONF_LOAD_TARGET_SOC)
            ): _number(0, 100, 1, "%"),
        }
        for key, domain in (
            (CONF_LOAD_SOC_ENTITY, "sensor"),
            (CONF_LOAD_CHARGE_ENABLE, ["input_boolean", "switch"]),
        ):
            schema[
                vol.Optional(key, description={"suggested_value": data.get(key)})
            ] = _entity(domain)
        return vol.Schema(schema)

    def _finish(self, storage_input: dict[str, Any]) -> SubentryFlowResult:
        # Preserved storage values (add: defaults) underlie the new input,
        # so a load toggled to unlimited keeps them for a later toggle back.
        data = {
            key: self._existing.get(key, DEFAULT_LOAD_CONFIG.get(key))
            for key in self._STORAGE_KEYS
        }
        for key in self._STORAGE_ENTITY_KEYS:
            if key in self._existing:
                data[key] = self._existing[key]
        data.update(self._basic)
        data.update(storage_input)
        title = data.pop(CONF_LOAD_NAME)
        data[CONF_LOAD_PRIORITY] = self._renumber_siblings(
            int(data[CONF_LOAD_PRIORITY])
        )
        if self._is_reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                data=data,
            )
        return self.async_create_entry(title=title, data=data)

    def _renumber_siblings(self, priority: int) -> int:
        """Insert-shift renumber (F-LOAD-PRIORITY R4): place the edited load at
        `priority` within the effective order of its SIBLINGS, renumber all
        loads densely 1..N, and return the edited load's clamped position.

        Only siblings whose EFFECTIVE priority (stored value, else the R3
        insertion fallback) actually changes are written: every sibling write
        reloads the entry, and a create at the default position (R5) or an
        untouched re-save of a dense state (R6) must stay write-free. The
        flow's own update/create afterwards carries the edited load's value and
        triggers the final reload that picks everything up.
        """
        entry = self._get_entry()
        edited_id = (
            self._get_reconfigure_subentry().subentry_id
            if self._is_reconfigure
            else None
        )
        # R3 insertion fallback per sibling: its position among ALL load
        # subentries (a created load appends after them, so positions hold).
        insertion_pos = {
            subentry_id: pos
            for pos, subentry_id in enumerate(
                sid
                for sid, sub in entry.subentries.items()
                if sub.subentry_type == SUBENTRY_TYPE_LOAD
            )
        }
        siblings = [
            (subentry_id, sub)
            for subentry_id, sub in ordered_load_subentries(entry)
            if subentry_id != edited_id
        ]
        position = max(1, min(priority, len(siblings) + 1))
        for index, (subentry_id, sub) in enumerate(siblings):
            new_priority = index + 1 if index + 1 < position else index + 2
            current = int(
                sub.data.get(CONF_LOAD_PRIORITY, insertion_pos[subentry_id] + 1)
            )
            if current != new_priority:
                self.hass.config_entries.async_update_subentry(
                    entry, sub, data={**sub.data, CONF_LOAD_PRIORITY: new_priority}
                )
        return position

    async def _handle_basic(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._basic = user_input
            if user_input.get(CONF_LOAD_ENERGY_LIMITED):
                # charge-enable is captured on the storage step -> validate there.
                return await self.async_step_storage()
            # Continuous load: no storage step, so the charging-path rules
            # (control switch + off policy, no charge-enable) are validated here.
            error = _validate_load_control(user_input)
            if error is None:
                return self._finish({})
            return self.async_show_form(
                step_id=step_id,
                data_schema=self._basic_schema(user_input),
                errors={"base": error},
            )
        return self.async_show_form(
            step_id=step_id, data_schema=self._basic_schema(self._existing)
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
            # F-LOAD-PRIORITY R2: the form default is the CURRENT effective
            # position — not the raw stored value, which can differ in a
            # legacy/mixed state (R7) — so an untouched save keeps the order.
            position = next(
                index + 1
                for index, (subentry_id, _sub) in enumerate(
                    ordered_load_subentries(self._get_entry())
                )
                if subentry_id == subentry.subentry_id
            )
            self._existing = {
                **subentry.data,
                CONF_LOAD_NAME: subentry.title,
                CONF_LOAD_PRIORITY: position,
            }
            self._is_reconfigure = True
        return await self._handle_basic("reconfigure", user_input)

    async def async_step_storage(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Validate the FULL charging path: control switch + off policy come
            # from the basic step, charge-enable from this one.
            error = _validate_load_control({**self._basic, **user_input})
            if error is None:
                return self._finish(user_input)
            errors["base"] = error
        data = user_input if user_input is not None else self._existing
        return self.async_show_form(
            step_id="storage",
            data_schema=self._storage_schema(data),
            errors=errors,
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
                    # Back-compat: a pre-0.7.15 appliance lacks the key — mirror
                    # the coordinator fallback (off == on threshold = no
                    # hysteresis) so a no-change reconfigure keeps run-end
                    # detection unchanged.
                    CONF_APPLIANCE_OFF_THRESHOLD_W,
                    default=data.get(
                        CONF_APPLIANCE_OFF_THRESHOLD_W,
                        data.get(
                            CONF_APPLIANCE_POWER_THRESHOLD_W,
                            DEFAULT_APPLIANCE_CONFIG[CONF_APPLIANCE_OFF_THRESHOLD_W],
                        ),
                    ),
                ): _number(0, 3000, 1, "W"),
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
