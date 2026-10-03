"""Fast measured-demand permission within the latest planner's DC envelope."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DCDC_SWITCH,
    CONF_RESERVE_MODE,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    LIVE_AC_POWER_KEYS,
)
from .core.live_ac import (
    LIVE_AC_INTERVAL_S,
    LIVE_AC_MARKET_START_RATIO,
    LIVE_AC_PLAN_MAX_AGE_S,
    LIVE_AC_SAMPLE_MAX_AGE_S,
    LiveACState,
    live_ac_decision,
)
from .core.model import PlanInputs, PlanResult, SystemConfig

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


@dataclass(frozen=True)
class LiveACEnvelope:
    config: SystemConfig
    expires: datetime
    floor_percent: float
    support_required: bool
    planned_floor_percent: float | None = None
    override_demand_w: float | None = None
    override_floor_percent: float = 100.0


class LiveACRuntime:
    """No independent PSU owner, persisted permission, or CPU planning task.

    The five-second timer also revokes permission when sensors stop updating.
    Forecast intervals remain forecasts; live decisions are published separately.
    """

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        self.owner = coordinator
        self.envelope: LiveACEnvelope | None = None
        self.state = LiveACState()
        self.limit_w = 0
        self.diagnostics: dict[str, Any] = {"reason": "no_plan", "limit_w": 0}
        self.task: asyncio.Task | None = None
        self._cancel: Callable[[], None] | None = None
        # Retain command responsibility until a revoked limit confirms OFF.
        self._owns_limit = False

    def start(self) -> None:
        if self._cancel is None:
            self._cancel = async_track_time_interval(
                self.owner.hass, self._tick, timedelta(seconds=LIVE_AC_INTERVAL_S)
            )

    @callback
    def _tick(self, _now: datetime) -> None:
        if not self.owner._actuation_shutdown and (
            self.task is None or self.task.done()
        ):
            self.task = self.owner.entry.async_create_background_task(
                self.owner.hass, self.run(), name="battery_manager_live_ac"
            )

    def stop(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        if self.task is not None:
            self.task.cancel()
        self.state = LiveACState()
        self.limit_w = 0
        self.envelope = None

    def set_plan(
        self, config: SystemConfig, inputs: PlanInputs, result: PlanResult
    ) -> None:
        decision = result.trajectory.reserve_decision
        self.envelope = (
            LiveACEnvelope(
                config,
                min(
                    dt_util.as_utc(inputs.now)
                    + timedelta(seconds=LIVE_AC_PLAN_MAX_AGE_S),
                    dt_util.as_utc(inputs.slots[0].start)
                    + timedelta(hours=inputs.slots[0].duration),
                    min(
                        (
                            dt_util.as_utc(at)
                            for price in inputs.market_prices
                            for at in (price.start, price.end)
                            if at.timestamp() > inputs.now.timestamp()
                        ),
                        default=dt_util.as_utc(inputs.now)
                        + timedelta(seconds=LIVE_AC_PLAN_MAX_AGE_S),
                    ),
                ),
                decision.live_ac_floor_percent,
                result.support_dc24_now or result.support_dc48_now,
                inputs.start_soc_percent
                - config.battery.soc_percent(decision.headroom_wh),
                decision.live_ac_override_demand_w,
                decision.live_ac_override_floor_percent,
            )
            if config.reserve.enabled and decision is not None
            else None
        )
        self.refresh()

    def sources_configured(self) -> bool:
        cfg = self.owner.raw_config
        return all(cfg.get(key) for key in LIVE_AC_POWER_KEYS) or bool(
            cfg.get("operation_house_power_entity")
            and cfg.get("operation_pv_power_entity")
        )

    def measured_demand(self) -> float | None:
        c = self.owner
        if any(c.raw_config.get(key) for key in LIVE_AC_POWER_KEYS):
            grid, ac_input, ac_output = (
                c._reserve_power(c.raw_config.get(key)) for key in LIVE_AC_POWER_KEYS
            )
            # AC-side measurements exclude DC consumers and conversion losses.
            # Negative output is solar feeding the converter, already included
            # in this balance. Never subtract that solar a second time.
            if grid is None or ac_input is None or ac_output is None:
                return None
            return max(0.0, grid + ac_output - ac_input)
        house = c._reserve_power(c.raw_config.get("operation_house_power_entity"))
        pv = c._reserve_power(c.raw_config.get("operation_pv_power_entity"))
        imported = c._reserve_power(c.raw_config.get("operation_import_power_entity"))
        # Import alone collapses to zero after a successful release. Gross AC
        # minus PV keeps real demand visible while ESS serves the consumer.
        if house is None or pv is None:
            return None
        return max(0.0, house - max(0.0, pv), imported or 0.0)

    def refresh(self) -> int:
        c, envelope = self.owner, self.envelope
        now = dt_util.utcnow()
        reason = None
        available_wh = 0.0
        demand = None
        if c._actuation_shutdown or c.raw_config.get(CONF_RESERVE_MODE) != "active":
            reason = "inactive"
        elif envelope is None or now >= envelope.expires:
            reason = "plan_expired"
        else:
            config = envelope.config
            soc = c._get_soc(dt_util.now())
            measured_soc = c._read_float(c.raw_config[CONF_SOC_ENTITY])
            soc_state = c.hass.states.get(c.raw_config[CONF_SOC_ENTITY])
            if (
                soc is None
                or measured_soc is None
                or not 0 <= measured_soc <= 100
                or soc_state is None
                or not 0
                <= (now - soc_state.last_reported).total_seconds()
                <= LIVE_AC_SAMPLE_MAX_AGE_S
            ):
                reason = "soc_unavailable"
            elif c._reserve_grid_available() is not True:
                reason = "grid_unavailable"
            elif (
                envelope.support_required
                or any(c._support_manual.values())
                or any(
                    c.raw_config.get(key)
                    and c._entity_tristate(c.raw_config[key]) is not False
                    for key in (CONF_SUPPORT_DC24_SWITCH, CONF_SUPPORT_DC48_SWITCH)
                )
                or (
                    c.raw_config.get(CONF_DCDC_SWITCH)
                    and c._entity_tristate(c.raw_config[CONF_DCDC_SWITCH]) is not True
                )
            ):
                reason = "dc_supply"
            else:
                demand = self.measured_demand()
                reserve_floor = envelope.floor_percent
                if (
                    envelope.override_demand_w is not None
                    and demand is not None
                    and min(demand, config.inverter.max_power_w)
                    >= envelope.override_demand_w
                    * (1.0 if self.state.active else LIVE_AC_MARKET_START_RATIO)
                ):
                    reserve_floor = envelope.override_floor_percent
                floor = max(
                    reserve_floor,
                    config.control.inverter_min_soc_percent,
                    config.control.support_dc24_activate_soc,
                    config.control.support_dc48_activate_soc,
                ) + max(
                    config.control.soc_buffer_percent, config.control.hysteresis_percent
                )
                available_wh = config.battery.energy_wh(max(0.0, soc - floor))
        decision = live_ac_decision(
            self.state,
            now,
            demand,
            available_wh,
            envelope.config.inverter.max_power_w if envelope else 0,
            envelope.config.battery.eta_discharge * envelope.config.inverter.eta
            if envelope
            else 1,
            reason,
        )
        self.state, self.limit_w = decision.state, decision.limit_w
        self.diagnostics = {
            "reason": decision.reason,
            "limit_w": self.limit_w,
            "residual_demand_w": demand,
            "available_wh": round(available_wh, 1),
            "market_override_demand_w": envelope.override_demand_w
            if envelope
            else None,
            "low_since": self.state.low_since.isoformat()
            if self.state.low_since
            else None,
        }
        self.diagnostics["decided_at"] = now.isoformat()
        self.diagnostics["planned_limit_w"] = self.planned_limit()
        self.owner.operation_recorder.decision("live_ac", self.diagnostics)
        return self.limit_w

    def planned_limit(self) -> int:
        """Recheck a full-power forecast permission without requiring AC meters.

        F-BINARY-INVERTER: a load jump must not spend the DC reserve while the
        slower planner is computing. The headroom floor already reserves the
        first step's DC draw; SOC hysteresis additionally covers measurement
        granularity, and the same 35-second full-power window covers freshness.
        """
        c, envelope = self.owner, self.envelope
        if not c._reserve_inverter_limit_w or envelope is None:
            return 0
        soc = c._read_float(c.raw_config[CONF_SOC_ENTITY])
        blocked = self.diagnostics["reason"]
        if (
            blocked
            in (
                "inactive",
                "plan_expired",
                "soc_unavailable",
                "grid_unavailable",
                "dc_supply",
            )
            or soc is None
        ):
            return 0
        config = envelope.config
        floor = (
            max(
                envelope.planned_floor_percent
                if envelope.planned_floor_percent is not None
                else envelope.floor_percent,
                config.control.inverter_min_soc_percent,
                config.control.support_dc24_activate_soc,
                config.control.support_dc48_activate_soc,
            )
            + config.control.hysteresis_percent
        )
        available = config.battery.energy_wh(max(0.0, soc - floor))
        return live_ac_decision(
            LiveACState(),
            dt_util.utcnow(),
            config.inverter.max_power_w,
            available,
            config.inverter.max_power_w,
            config.battery.eta_discharge * config.inverter.eta,
        ).limit_w

    def note_command(self, target_w: float) -> None:
        """A plan executor can also issue the extra permission before our tick."""
        if target_w > 0 and self.owner._reserve_inverter_limit_w is not None:
            self._owns_limit = True

    async def run(self) -> None:
        c = self.owner
        if c._actuation_shutdown:
            return
        # Same ownership lock as planned source transfers; never race a PSU ON.
        async with c._switch_lock:
            live = self.refresh()
            planned = self.planned_limit()
            # Planned starts still belong to the source owner and its dwell.
            # This timer only maintains/revokes an already commanded plan.
            if not live and not self._owns_limit:
                if c.data and c.data.get("live_ac") != self.diagnostics:
                    c.data["live_ac"] = dict(self.diagnostics)
                    c.async_update_listeners()
                return
            self._owns_limit = True
            protected = self.diagnostics["reason"] in (
                "inactive",
                "soc_unavailable",
                "grid_unavailable",
                "dc_supply",
            )
            target = 0 if protected else max(planned, live)
            diag: dict[str, Any] = {}
            confirmed = await c._confirm_inverter_limit(not target, diag)
            # A delayed command can outlive a meter/source change. Re-evaluate
            # before publishing success and revoke an obsolete extra permission.
            still_live = self.refresh()
            protection_changed = self.diagnostics["reason"] in (
                "inactive",
                "soc_unavailable",
                "grid_unavailable",
                "dc_supply",
            )
            if target and (
                protection_changed or (not still_live and not self.planned_limit())
            ):
                confirmed = await c._confirm_inverter_limit(True, diag)
                target = 0
            c._inverter_recommendation = bool(target and confirmed)
            self._owns_limit = bool(target or not confirmed)
            self.diagnostics["confirmed"] = confirmed
            self.diagnostics["requested_limit_w"] = target
            self.diagnostics["command_reason"] = diag.get("reason")
            if c.data:
                c.data["inverter_recommendation"] = c._inverter_recommendation
                c.data["live_ac"] = dict(self.diagnostics)
                c.data["inverter_control"] = c.inverter_control_snapshot()
                c.async_update_listeners()
