"""Data update coordinator for the Battery Manager integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    APPLIANCE_RUNNING_STATES,
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_OPPORTUNISTIC,
    CONF_APPLIANCE_POWER_THRESHOLD_W,
    CONF_APPLIANCE_RUN_DURATION_H,
    CONF_APPLIANCE_RUN_ENERGY_WH,
    CONF_DCDC_SWITCH,
    CONF_LOAD_AVAILABILITY_ENTITY,
    CONF_LOAD_BATTERY_TOLERANCE,
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_POWER_W,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    DEBOUNCE_SECONDS,
    DEFAULT_CONFIG,
    DOMAIN,
    INITIAL_UPDATE_INTERVAL_SECONDS,
    MAX_HISTORICAL_FORECAST_AGE_HOURS,
    MAX_HISTORICAL_SOC_AGE_HOURS,
    STARTUP_RETRY_ATTEMPTS,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
    UPDATE_INTERVAL_SECONDS,
)
from .core import (
    Appliance,
    ApplianceRun,
    BatteryParams,
    ControlParams,
    ConverterParams,
    LoadProfile,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
    build_slots,
    plan,
)

_LOGGER = logging.getLogger(__name__)

_POWER_EMA_ALPHA = 0.3


class BatteryManagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads inputs, runs the core planner, applies hysteresis and switching."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=INITIAL_UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.raw_config = {**DEFAULT_CONFIG, **entry.data, **entry.options}

        # Input caching for graceful degradation
        self._last_valid_soc: float | None = None
        self._last_soc_update: datetime | None = None
        self._last_valid_forecasts: list[float] | None = None
        self._last_forecast_update: datetime | None = None

        # Hysteresis / switching state (docs/ALGORITHM.md D-A2)
        self._displayed_threshold: float | None = None
        self._inverter_recommendation = False
        self._last_inverter_switch: datetime | None = None
        self._support_state = {"dc24": False, "dc48": False}
        self._last_support_switch: datetime | None = None
        self._switch_lock = asyncio.Lock()
        self._switch_task: asyncio.Task | None = None
        self._assumed_state_warned = False

        # Appliance run tracking and load power smoothing
        self._appliance_started: dict[str, datetime] = {}
        self._load_power_ema: dict[str, float] = {}

        self._startup_complete = False
        self._successful_updates = 0

        self._debounce_task: asyncio.Task | None = None
        self._listeners_setup: bool = False
        self._unsub_state_listener = None
        self._setup_entity_listeners()

    # ------------------------------------------------------------------
    # Configuration assembly
    # ------------------------------------------------------------------

    def _tracked_entities(self) -> list[str]:
        cfg = self.raw_config
        entities = [
            cfg[CONF_SOC_ENTITY],
            cfg[CONF_PV_FORECAST_TODAY],
            cfg[CONF_PV_FORECAST_TOMORROW],
            cfg[CONF_PV_FORECAST_DAY_AFTER],
        ]
        for subentry in self.entry.subentries.values():
            data = subentry.data
            if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
                for key in (
                    CONF_LOAD_SOC_ENTITY,
                    CONF_LOAD_POWER_ENTITY,
                    CONF_LOAD_AVAILABILITY_ENTITY,
                ):
                    if data.get(key):
                        entities.append(data[key])
            elif subentry.subentry_type == SUBENTRY_TYPE_APPLIANCE and data.get(
                CONF_APPLIANCE_DETECTION_ENTITY
            ):
                entities.append(data[CONF_APPLIANCE_DETECTION_ENTITY])
        return entities

    def build_system_config(self) -> SystemConfig:
        """Translate entry data + subentries into the core SystemConfig."""
        cfg = self.raw_config
        loads = []
        appliances = []
        for subentry_id, subentry in self.entry.subentries.items():
            data = subentry.data
            if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
                loads.append(
                    SurplusLoad(
                        load_id=subentry_id,
                        name=subentry.title,
                        nominal_power_w=float(data[CONF_LOAD_POWER_W]),
                        battery_tolerance=float(
                            data.get(CONF_LOAD_BATTERY_TOLERANCE, 15.0)
                        )
                        / 100.0,
                        min_runtime_min=int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)),
                        energy_limited=bool(data.get(CONF_LOAD_ENERGY_LIMITED, False)),
                        capacity_wh=float(data.get(CONF_LOAD_CAPACITY_WH, 0.0)),
                        target_soc_percent=float(data.get(CONF_LOAD_TARGET_SOC, 100.0)),
                    )
                )
            elif subentry.subentry_type == SUBENTRY_TYPE_APPLIANCE:
                appliances.append(
                    Appliance(
                        appliance_id=subentry_id,
                        name=subentry.title,
                        run_energy_wh=float(data[CONF_APPLIANCE_RUN_ENERGY_WH]),
                        run_duration_h=float(data[CONF_APPLIANCE_RUN_DURATION_H]),
                        opportunistic_start=bool(
                            data.get(CONF_APPLIANCE_OPPORTUNISTIC, False)
                        ),
                    )
                )

        support_configured = bool(
            cfg.get(CONF_SUPPORT_DC24_SWITCH) or cfg.get(CONF_SUPPORT_DC48_SWITCH)
        )

        return SystemConfig(
            battery=BatteryParams(
                capacity_wh=float(cfg["battery_capacity_wh"]),
                soc_min_percent=float(cfg["battery_min_soc_percent"]),
                soc_max_percent=float(cfg["battery_max_soc_percent"]),
                eta_charge=float(cfg["battery_charge_efficiency"]),
                eta_discharge=float(cfg["battery_discharge_efficiency"]),
            ),
            charger=ConverterParams(
                max_power_w=float(cfg["charger_max_power_w"]),
                eta=float(cfg["charger_efficiency"]),
                standby_power_w=float(cfg["charger_standby_power_w"]),
            ),
            inverter=ConverterParams(
                max_power_w=float(cfg["inverter_max_power_w"]),
                eta=float(cfg["inverter_efficiency"]),
                standby_power_w=float(cfg["inverter_standby_power_w"]),
            ),
            pv=PVParams(
                peak_power_w=float(cfg["pv_max_power_w"]),
                morning_start_hour=int(cfg["pv_morning_start_hour"]),
                morning_end_hour=int(cfg["pv_morning_end_hour"]),
                afternoon_end_hour=int(cfg["pv_afternoon_end_hour"]),
                morning_ratio=float(cfg["pv_morning_ratio"]),
            ),
            ac_profile=LoadProfile(
                base_w=float(cfg["ac_base_load_w"]),
                variable_w=float(cfg["ac_variable_load_w"]),
                variable_start_hour=int(cfg["ac_variable_start_hour"]),
                variable_end_hour=int(cfg["ac_variable_end_hour"]),
            ),
            dc_profile=LoadProfile(
                base_w=float(cfg["dc_base_load_w"]),
                variable_w=float(cfg["dc_variable_load_w"]),
                variable_start_hour=int(cfg["dc_variable_start_hour"]),
                variable_end_hour=int(cfg["dc_variable_end_hour"]),
            ),
            control=ControlParams(
                inverter_min_soc_percent=float(cfg["inverter_min_soc_percent"]),
                soc_buffer_percent=float(cfg["soc_buffer_percent"]),
                hysteresis_percent=float(cfg["hysteresis_percent"]),
                threshold_inertia_percent=float(cfg["threshold_inertia_percent"]),
                min_switch_interval_s=int(cfg["min_switch_interval_s"]),
            ),
            support=SupportParams(
                configured=support_configured,
                dc48_power_w=float(cfg.get(CONF_SUPPORT_DC48_POWER_W, 60.0)),
            ),
            loads=tuple(loads),
            appliances=tuple(appliances),
        )

    # ------------------------------------------------------------------
    # Input reading
    # ------------------------------------------------------------------

    def _read_float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _get_soc(self, now: datetime) -> float | None:
        value = self._read_float(self.raw_config[CONF_SOC_ENTITY])
        if value is not None and 0.0 <= value <= 100.0:
            self._last_valid_soc = value
            self._last_soc_update = now
            return value
        if (
            self._last_valid_soc is not None
            and self._last_soc_update is not None
            and now - self._last_soc_update
            <= timedelta(hours=MAX_HISTORICAL_SOC_AGE_HOURS)
        ):
            return self._last_valid_soc
        return None

    def _get_forecasts(self, now: datetime) -> list[float] | None:
        cfg = self.raw_config
        values = [
            self._read_float(cfg[CONF_PV_FORECAST_TODAY]),
            self._read_float(cfg[CONF_PV_FORECAST_TOMORROW]),
            self._read_float(cfg[CONF_PV_FORECAST_DAY_AFTER]),
        ]
        if all(v is not None for v in values):
            forecasts = [max(0.0, v) for v in values]  # type: ignore[arg-type]
            self._last_valid_forecasts = forecasts
            self._last_forecast_update = now
            return forecasts
        if (
            self._last_valid_forecasts is not None
            and self._last_forecast_update is not None
            and now - self._last_forecast_update
            <= timedelta(hours=MAX_HISTORICAL_FORECAST_AGE_HOURS)
        ):
            return self._last_valid_forecasts
        return None

    def _get_load_states(self) -> tuple[SurplusLoadState, ...]:
        states = []
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            available = True
            if data.get(CONF_LOAD_AVAILABILITY_ENTITY):
                avail_state = self.hass.states.get(data[CONF_LOAD_AVAILABILITY_ENTITY])
                available = avail_state is not None and avail_state.state not in (
                    "unavailable",
                    "unknown",
                    "off",
                )
            soc = None
            if data.get(CONF_LOAD_SOC_ENTITY):
                soc = self._read_float(data[CONF_LOAD_SOC_ENTITY])
                if soc is None and data.get(CONF_LOAD_ENERGY_LIMITED):
                    # Energy-limited load without readable SOC: treat as unavailable
                    # rather than overcharging a possibly full storage.
                    available = False
            measured = None
            if data.get(CONF_LOAD_POWER_ENTITY):
                raw = self._read_float(data[CONF_LOAD_POWER_ENTITY])
                if raw is not None and raw > 10.0:
                    previous = self._load_power_ema.get(subentry_id, raw)
                    measured = (
                        _POWER_EMA_ALPHA * raw + (1 - _POWER_EMA_ALPHA) * previous
                    )
                    self._load_power_ema[subentry_id] = measured
                elif subentry_id in self._load_power_ema:
                    measured = self._load_power_ema[subentry_id]
            states.append(
                SurplusLoadState(
                    load_id=subentry_id,
                    available=available,
                    soc_percent=soc,
                    measured_power_w=measured,
                )
            )
        return tuple(states)

    def _appliance_is_running(self, data: dict[str, Any]) -> bool:
        entity_id = data.get(CONF_APPLIANCE_DETECTION_ENTITY)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return False
        try:
            power = float(state.state)
        except (ValueError, TypeError):
            return state.state.lower() in APPLIANCE_RUNNING_STATES
        return power >= float(data.get(CONF_APPLIANCE_POWER_THRESHOLD_W, 10.0))

    def _get_appliance_runs(self, now: datetime) -> tuple[ApplianceRun, ...]:
        runs = []
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_APPLIANCE:
                continue
            data = subentry.data
            if self._appliance_is_running(data):
                started = self._appliance_started.setdefault(subentry_id, now)
                duration = float(data[CONF_APPLIANCE_RUN_DURATION_H])
                elapsed_h = (now - started).total_seconds() / 3600.0
                remaining_h = max(0.0, duration - elapsed_h)
                if remaining_h > 0 and duration > 0:
                    runs.append(
                        ApplianceRun(
                            appliance_id=subentry_id,
                            remaining_energy_wh=float(data[CONF_APPLIANCE_RUN_ENERGY_WH])
                            * remaining_h
                            / duration,
                            remaining_hours=remaining_h,
                        )
                    )
            else:
                self._appliance_started.pop(subentry_id, None)
        return tuple(runs)

    # ------------------------------------------------------------------
    # Update cycle
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        soc = self._get_soc(now)
        forecasts = self._get_forecasts(now)

        if soc is None or forecasts is None:
            self._successful_updates = 0
            missing = "SOC" if soc is None else "PV forecasts"
            raise UpdateFailed(f"No valid input data available ({missing})")

        config = self.build_system_config()
        load_states = self._get_load_states()
        appliance_runs = self._get_appliance_runs(now)

        inputs = build_slots(
            config,
            now.replace(tzinfo=None),
            soc,
            forecasts,
            appliance_runs=appliance_runs,
            load_states=load_states,
        )
        result = await self.hass.async_add_executor_job(plan, config, inputs)

        threshold = self._apply_threshold_inertia(result.threshold_percent, config)
        recommendation = self._apply_hysteresis(soc, threshold, config, now)
        await self._apply_support_switching(result, config, now)

        self._successful_updates += 1
        if not self._startup_complete and (
            self._successful_updates >= 2
            or self._successful_updates >= STARTUP_RETRY_ATTEMPTS
        ):
            self._startup_complete = True
            self.update_interval = timedelta(seconds=UPDATE_INTERVAL_SECONDS)

        load_plans: dict[str, dict[str, Any]] = {}
        for load_plan, load in zip(result.load_plans, config.loads, strict=True):
            load_plans[load_plan.load_id] = {
                "name": load.name,
                "active": load_plan.active_now,
                "planned_hours": sum(load_plan.schedule),
                "planned_energy_kwh": round(load_plan.planned_energy_wh / 1000.0, 3),
            }

        hourly_details = [
            {
                "hour": slot.index,
                "datetime": slot.start.isoformat(),
                "duration_minutes": int(slot.duration * 60),
                "initial_soc_percent": flow.soc_start_percent,
                "final_soc_percent": flow.soc_end_percent,
                "pv_production_wh": slot.pv_wh,
                "ac_consumption_wh": slot.ac_wh + flow.extra_ac_wh,
                "dc_consumption_wh": slot.dc_wh,
                "surplus_load_wh": flow.extra_ac_wh,
                "grid_import_wh": flow.grid_import_wh,
                "grid_export_wh": flow.grid_export_wh,
                "battery_charge_wh": flow.battery_charge_wh,
                "battery_discharge_wh": flow.battery_discharge_wh,
                "inverter_enabled": flow.inverter_on,
                "support_dc24": flow.support_dc24,
                "support_dc48": flow.support_dc48,
            }
            for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True)
        ]

        return {
            "valid": True,
            "last_update": now,
            "input_soc_percent": soc,
            "input_forecasts_kwh": forecasts,
            "soc_threshold_percent": threshold,
            "inverter_recommendation": recommendation,
            "min_soc_forecast_percent": result.min_soc_percent,
            "max_soc_forecast_percent": result.max_soc_percent,
            "hours_to_max_soc": result.hours_to_max_soc,
            "grid_import_kwh": round(result.grid_import_kwh, 3),
            "grid_export_kwh": round(result.grid_export_kwh, 3),
            "lost_surplus_kwh": round(result.lost_surplus_kwh, 3),
            "load_plans": load_plans,
            "appliance_windows": dict(result.appliance_windows),
            "support_dc24": self._support_state["dc24"],
            "support_dc48": self._support_state["dc48"],
            "hourly_details": hourly_details,
        }

    # ------------------------------------------------------------------
    # Output post-processing (D-A2, D-A9/F-N1)
    # ------------------------------------------------------------------

    def _apply_threshold_inertia(
        self, new_threshold: float, config: SystemConfig
    ) -> float:
        if self._displayed_threshold is None or (
            abs(new_threshold - self._displayed_threshold)
            >= config.control.threshold_inertia_percent
        ):
            self._displayed_threshold = new_threshold
        return self._displayed_threshold

    def _apply_hysteresis(
        self, soc: float, threshold: float, config: SystemConfig, now: datetime
    ) -> bool:
        hyst = config.control.hysteresis_percent
        desired = self._inverter_recommendation
        if soc >= threshold + hyst:
            desired = True
        elif soc <= threshold - hyst:
            desired = False

        if desired != self._inverter_recommendation:
            interval = timedelta(seconds=config.control.min_switch_interval_s)
            if (
                self._last_inverter_switch is None
                or now - self._last_inverter_switch >= interval
            ):
                self._inverter_recommendation = desired
                self._last_inverter_switch = now
        return self._inverter_recommendation

    def _sync_support_state_from_entities(self) -> None:
        """Adopt the real switch states while no sequence is running.

        Heals desyncs from restarts, manual toggles and aborted sequences.
        'unavailable'/'unknown' states are ignored — never treated as 'off'.
        """
        for key, conf_key in (
            ("dc24", CONF_SUPPORT_DC24_SWITCH),
            ("dc48", CONF_SUPPORT_DC48_SWITCH),
        ):
            entity_id = self.raw_config.get(conf_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is not None and state.state in ("on", "off"):
                self._support_state[key] = state.state == "on"

    def _warn_unreliable_switches_once(self) -> None:
        """Warn if a support switch cannot confirm its real device state."""
        if self._assumed_state_warned:
            return
        self._assumed_state_warned = True
        for conf_key in (
            CONF_SUPPORT_DC24_SWITCH,
            CONF_SUPPORT_DC48_SWITCH,
            CONF_DCDC_SWITCH,
        ):
            entity_id = self.raw_config.get(conf_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is not None and state.attributes.get("assumed_state"):
                _LOGGER.warning(
                    "%s reports an assumed state only; the make-before-break"
                    " confirmation cannot detect an unresponsive device."
                    " Use state-reporting switches for the 24 V rail supplies.",
                    entity_id,
                )

    async def _switch_entity(self, entity_id: str, turn_on: bool) -> bool:
        service = "turn_on" if turn_on else "turn_off"
        try:
            await self.hass.services.async_call(
                "switch", service, {"entity_id": entity_id}, blocking=True
            )
        except Exception as err:
            _LOGGER.error("switch.%s failed for %s: %s", service, entity_id, err)
            return False
        return True

    def _entity_is_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    async def _sequence_dc24(self, activate: bool, psu_entity: str) -> bool:
        """Switch the 24 V rail supply make-before-break (docs/ALGORITHM.md D-A9).

        The rail is fed either by the DC/DC converter (from battery) or the
        grid PSU — never by neither. Activation: PSU on -> delay -> DC/DC off.
        Deactivation: DC/DC on -> delay -> PSU off. If the newly activated
        source does not confirm 'on', the switchover is aborted and the
        previous source stays on.
        """
        dcdc_entity = self.raw_config.get(CONF_DCDC_SWITCH)
        delay_s = float(self.raw_config.get(CONF_SUPPORT_SWITCH_DELAY_S, 3))

        if not dcdc_entity:
            # No switchable DC/DC configured: plain PSU toggle (parallel feed).
            return await self._switch_entity(psu_entity, activate)

        first_on, then_off = (
            (psu_entity, dcdc_entity) if activate else (dcdc_entity, psu_entity)
        )
        if not await self._switch_entity(first_on, True):
            return False
        await asyncio.sleep(delay_s)
        if not self._entity_is_on(first_on):
            _LOGGER.error(
                "New 24 V supply %s did not report 'on'; aborting switchover,"
                " %s stays on",
                first_on,
                then_off,
            )
            return False
        return await self._switch_entity(then_off, False)

    async def _apply_support_switching(
        self, result, config: SystemConfig, now: datetime
    ) -> None:
        """Evaluate support paths and start switching if needed (decision F-N1).

        The actual switching runs in an entry-scoped background task so that
        a cancelled debounce/refresh task can never abort a make-before-break
        sequence halfway (review finding: cancellation propagation). While a
        sequence is in flight, evaluation is skipped; afterwards the idle
        re-sync adopts the real switch states.
        """
        if not config.support.configured:
            return
        if self._switch_task is not None and not self._switch_task.done():
            return
        self._warn_unreliable_switches_once()
        self._sync_support_state_from_entities()

        desired = {"dc24": result.support_dc24_now, "dc48": result.support_dc48_now}
        if desired == self._support_state:
            return
        interval = timedelta(seconds=config.control.min_switch_interval_s)
        if (
            self._last_support_switch is not None
            and now - self._last_support_switch < interval
        ):
            return

        self._last_support_switch = now
        self._switch_task = self.entry.async_create_background_task(
            self.hass,
            self._execute_support_switching(desired),
            name="battery_manager_support_switching",
        )

    async def _execute_support_switching(self, desired: dict[str, bool]) -> None:
        """Carry out the switching sequences; runs detached from the refresh."""
        async with self._switch_lock:
            dc48_entity = self.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
            if (
                dc48_entity
                and desired["dc48"] != self._support_state["dc48"]
                and await self._switch_entity(dc48_entity, desired["dc48"])
            ):
                # A successful service call is no device confirmation; the
                # idle re-sync corrects _support_state on the next cycle.
                self._support_state["dc48"] = desired["dc48"]
                _LOGGER.info(
                    "48 V support PSU switched %s (%s)",
                    "on" if desired["dc48"] else "off",
                    dc48_entity,
                )

            dc24_entity = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
            if (
                dc24_entity
                and desired["dc24"] != self._support_state["dc24"]
                and await self._sequence_dc24(desired["dc24"], dc24_entity)
            ):
                self._support_state["dc24"] = desired["dc24"]
                _LOGGER.info(
                    "24 V rail now fed by %s",
                    "grid PSU" if desired["dc24"] else "DC/DC converter",
                )

        # Reflect the new state in the entities without waiting for a replan.
        if self.data:
            self.data["support_dc24"] = self._support_state["dc24"]
            self.data["support_dc48"] = self._support_state["dc48"]
            self.async_update_listeners()

    # ------------------------------------------------------------------
    # Entity listeners
    # ------------------------------------------------------------------

    def _setup_entity_listeners(self) -> None:
        try:
            self._unsub_state_listener = async_track_state_change_event(
                self.hass, self._tracked_entities(), self._handle_entity_change
            )
            self._listeners_setup = True
        except Exception as err:
            _LOGGER.warning(
                "Failed to set up entity listeners: %s. Relying on polling.", err
            )

    @callback
    def _handle_entity_change(self, event) -> None:
        if not self._listeners_setup:
            return
        if self._debounce_task:
            self._debounce_task.cancel()
        self._debounce_task = self.hass.async_create_task(self._debounced_update())

    async def _debounced_update(self) -> None:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await self.async_request_refresh()

    def cleanup(self) -> None:
        """Release entity listeners and cancel pending debounce work."""
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None
        self._listeners_setup = False
        if self._debounce_task:
            self._debounce_task.cancel()
            self._debounce_task = None

    def get_last_hourly_details(self) -> list[dict[str, Any]]:
        if self.data and self.data.get("hourly_details"):
            return list(self.data["hourly_details"])
        return []
