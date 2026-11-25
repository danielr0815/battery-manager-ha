"""Fast measured-demand permission within the latest planner's DC envelope."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DC_LOAD_ENTITY,
    CONF_DCDC_SWITCH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_RESERVE_MODE,
    CONF_SOC_ENTITY,
    LIVE_AC_POWER_KEYS,
    SUBENTRY_TYPE_LOAD,
)
from .core.live_ac import (
    LIVE_AC_INTERVAL_S,
    LIVE_AC_MARKET_START_RATIO,
    LIVE_AC_PLAN_MAX_AGE_S,
    LIVE_AC_SAMPLE_MAX_AGE_S,
    LiveACState,
    live_ac_decision,
)
from .core.live_dc import pv_covers_dc
from .core.model import PlanInputs, PlanResult, SocCoordinateSnapshot, SystemConfig
from .load_actuation import RevokedLoadStart
from .load_safety import effective_start_power_w, stop_unsafe_loads
from .voltage_gate import requested_dc48

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
    dc_forecast_w: float | None = None
    dc_sources: tuple[bool, bool] = (False, False)
    accepted_budget_wh: float | None = None
    soc_coordinate: SocCoordinateSnapshot | None = None
    plan_inputs: PlanInputs | None = None


# Keep an energy buffer independent of a partial calendar-hour slot.
DC_FALLBACK_BUFFER_HOURS = 1.0


@dataclass
class PvStartReservation:
    """An issued load remains budgeted until measured in the common balance."""

    power_w: float
    input_entity: str | None
    confirmed_at: float | None = None


class LiveACRuntime:
    """No independent PSU owner, persisted permission, or CPU planning task.

    The five-second timer also revokes permission when sensors stop updating.
    Forecast intervals remain forecasts; live decisions are published separately.
    """

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        self.owner = coordinator
        self.plan_revision = 0
        self.envelope: LiveACEnvelope | None = None
        self.state = LiveACState()
        self.limit_w = 0
        self.diagnostics: dict[str, Any] = {"reason": "no_plan", "limit_w": 0}
        self.task: asyncio.Task | None = None
        self._cancel: Callable[[], None] | None = None
        # Retain command responsibility until a revoked limit confirms OFF.
        self._owns_limit = False
        self.pv_active = False
        self._pv_restore_pending = False
        self._pv_start_reservations: dict[str, PvStartReservation] = {}
        self.revoked_load_starts: dict[str, RevokedLoadStart] = {}
        self.coordinate_cleanup_task: asyncio.Task | None = None
        self.coordinate_cleanup_scheduled = False

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
        if self.coordinate_cleanup_task is not None:
            self.coordinate_cleanup_task.cancel()
        self.state = LiveACState()
        self.limit_w = 0
        self.plan_revision += 1
        self.envelope = None
        self.pv_active = False
        self._pv_restore_pending = False

    def set_plan(
        self, config: SystemConfig, inputs: PlanInputs, result: PlanResult
    ) -> None:
        self.plan_revision += 1
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
                result.support_dc24_now or requested_dc48(result),
                inputs.start_soc_percent
                - config.battery.soc_percent(decision.headroom_wh),
                decision.live_ac_override_demand_w,
                decision.live_ac_override_floor_percent,
                inputs.slots[0].dc_wh / inputs.slots[0].duration
                + config.battery.energy_wh(config.control.soc_buffer_percent)
                / DC_FALLBACK_BUFFER_HOURS,
                (result.support_dc24_now, requested_dc48(result)),
                decision.headroom_wh
                if decision.joint is not None
                and decision.joint.joint_status
                not in ("missing_prices", "flat_prices", "market_off")
                else None,
                inputs.soc_coordinate,
                inputs,
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

    def load_pv_permission(
        self,
        extra_ac_w: float,
        *,
        exclude_load_id: str | None = None,
        reservation_owner: str | None = None,
        config: SystemConfig | None = None,
    ) -> bool:
        """Fresh complete physical coverage, independent of any SOC headroom."""
        c = self.owner
        envelope = self.envelope
        record = c._last_planner_recording
        config = (
            envelope.config if envelope is not None else record[0] if record else config
        )
        if (
            config is None
            or not isfinite(extra_ac_w)
            or extra_ac_w < 0
            or c._reserve_grid_available() is not True
        ):
            return False
        now = dt_util.utcnow()
        soc = c._get_soc(dt_util.now())
        state = c.hass.states.get(c.raw_config.get(CONF_SOC_ENTITY) or "")
        if (
            soc is None
            or state is None
            or not 0
            <= (now - state.last_reported).total_seconds()
            <= LIVE_AC_SAMPLE_MAX_AGE_S
        ):
            return False
        pv = c._reserve_power(c.raw_config.get("operation_pv_power_entity"))
        dc = c._reserve_power(c.raw_config.get(CONF_DC_LOAD_ENTITY))
        if pv is None or pv <= 0 or dc is None:
            return False
        if any(c.raw_config.get(key) for key in LIVE_AC_POWER_KEYS):
            values = [
                c._reserve_power(c.raw_config.get(key)) for key in LIVE_AC_POWER_KEYS
            ]
            if any(value is None for value in values):
                return False
            grid, ac_input, ac_output = values
            assert grid is not None and ac_input is not None and ac_output is not None
            surplus = -(grid + ac_output - ac_input)
        else:
            house = c._reserve_power(c.raw_config.get("operation_house_power_entity"))
            if house is None:
                return False
            surplus = pv - house
        self._retire_measured_pv_reservations()
        # Concurrent starts can share the same still-fresh meter publication.
        # Reserve other issued ON intents until their physical demand is visible.
        pending_w = 0.0
        for load_id, subentry in c.entry.subentries.items():
            if (
                load_id == exclude_load_id
                or "load:" + load_id in self._pv_start_reservations
                or subentry.subentry_type != SUBENTRY_TYPE_LOAD
            ):
                continue
            actors = (
                subentry.data.get(CONF_LOAD_CONTROL_SWITCH),
                subentry.data.get(CONF_LOAD_CHARGE_ENABLE),
            )
            if any(
                actor in c._load_actor_requests
                and c._load_actor_requests[actor].desired
                and c._load_actor_requests[actor].state != "service_failed"
                for actor in actors
                if actor
            ):
                power = effective_start_power_w(c, load_id, subentry.data)
                if power is None:
                    return False
                pending_w += power
        return pv_covers_dc(
            config,
            soc,
            now,
            surplus
            - extra_ac_w
            - pending_w
            - sum(
                item.power_w
                for owner, item in self._pv_start_reservations.items()
                if owner != reservation_owner
            ),
            dc,
            False,
        )

    def note_start_power(
        self, owner: str, power_w: float, *, input_entity: str | None = None
    ) -> None:
        previous = self._pv_start_reservations.get(owner)
        if previous is None:
            self._pv_start_reservations[owner] = PvStartReservation(
                power_w, input_entity
            )
        else:
            previous.power_w = max(previous.power_w, power_w)

    def reserve_pv_start(
        self,
        owner: str,
        power_w: float,
        *,
        input_entity: str | None = None,
        exclude_load_id: str | None = None,
        config: SystemConfig | None = None,
    ) -> bool:
        """Atomically claim shared PV before an optional actor can await."""
        previous = self._pv_start_reservations.get(owner)
        power_w = max(power_w, previous.power_w) if previous is not None else power_w
        if not self.load_pv_permission(
            power_w,
            exclude_load_id=exclude_load_id,
            reservation_owner=owner,
            config=config,
        ):
            return False
        if previous is None:
            self._pv_start_reservations[owner] = PvStartReservation(
                power_w, input_entity
            )
        else:
            previous.power_w = power_w
        return True

    def confirm_pv_start(self, owner: str) -> None:
        reservation = self._pv_start_reservations.get(owner)
        if reservation is not None and reservation.confirmed_at is None:
            reservation.confirmed_at = dt_util.utcnow().timestamp()

    def release_pv_start(self, owner: str) -> None:
        self._pv_start_reservations.pop(owner, None)

    def _retire_measured_pv_reservations(self) -> None:
        c = self.owner
        entities = (
            [c.raw_config.get(key) for key in LIVE_AC_POWER_KEYS]
            if any(c.raw_config.get(key) for key in LIVE_AC_POWER_KEYS)
            else [
                c.raw_config.get("operation_house_power_entity"),
                c.raw_config.get("operation_pv_power_entity"),
            ]
        )
        entities.append(c.raw_config.get(CONF_DC_LOAD_ENTITY))
        for owner, reservation in tuple(self._pv_start_reservations.items()):
            if reservation.confirmed_at is None or not reservation.input_entity:
                continue
            power = c._reserve_power(reservation.input_entity)
            input_state = c.hass.states.get(reservation.input_entity)
            observations = [
                c.hass.states.get(entity) if entity else None for entity in entities
            ]
            if (
                power is not None
                and power >= reservation.power_w
                and input_state is not None
                and input_state.last_reported.timestamp() > reservation.confirmed_at
                and all(
                    state is not None
                    and state.last_reported.timestamp()
                    > input_state.last_reported.timestamp()
                    for state in observations
                )
            ):
                # Fresh input demand now belongs to the fresh signed house
                # balance. Until then even confirmed ON reserves the full load.
                self._pv_start_reservations.pop(owner, None)

    def coordinate_permission(self) -> bool:
        """Optional AC budgets require the exact published coordinate snapshot."""
        gate = getattr(self.owner, "voltage_gate", None)
        return gate is None or gate.coordinate_permission_valid(
            self.envelope.soc_coordinate if self.envelope else None
        )

    def pv_source_permission(self, config: SystemConfig) -> bool:
        """Fresh signed meters may release only economic PSU holding."""
        c, envelope = self.owner, self.envelope
        now = dt_util.utcnow()
        if envelope is None or not config.reserve.enabled:
            self.pv_active = False
            return False
        gate = getattr(c, "voltage_gate", None)
        if gate is not None and not gate.coordinate_permission_valid(
            envelope.soc_coordinate, direct_pv_proven=True
        ):
            self.pv_active = False
            return False
        soc = c._get_soc(dt_util.now())
        measured_soc = c._read_float(c.raw_config[CONF_SOC_ENTITY])
        soc_state = c.hass.states.get(c.raw_config[CONF_SOC_ENTITY])
        if (
            envelope is None
            or now >= envelope.expires
            or c.raw_config.get(CONF_RESERVE_MODE) != "active"
            or not config.reserve.enabled
            or soc is None
            or measured_soc is None
            or not 0 <= measured_soc <= 100
            or soc_state is None
            or not 0
            <= (now - soc_state.last_reported).total_seconds()
            <= LIVE_AC_SAMPLE_MAX_AGE_S
            or c._reserve_grid_available() is not True
            or soc
            <= max(
                config.control.support_dc24_recovery_soc,
                config.control.support_dc48_recovery_soc,
            )
        ):
            self.pv_active = False
            return False
        # A positive solar measurement also prevents an unrelated negative meter
        # balance from masquerading as PV. PSU draw is never added to surplus.
        pv = c._reserve_power(c.raw_config.get("operation_pv_power_entity"))
        surplus = None
        if pv is not None and pv > 0:
            if any(c.raw_config.get(key) for key in LIVE_AC_POWER_KEYS):
                values = [
                    c._reserve_power(c.raw_config.get(key))
                    for key in LIVE_AC_POWER_KEYS
                ]
                if all(value is not None for value in values):
                    grid, ac_input, ac_output = values
                    assert (
                        grid is not None
                        and ac_input is not None
                        and ac_output is not None
                    )
                    surplus = -(grid + ac_output - ac_input)
            else:
                house = c._reserve_power(
                    c.raw_config.get("operation_house_power_entity")
                )
                if house is not None:
                    surplus = pv - house
        dc = c._reserve_power(c.raw_config.get(CONF_DC_LOAD_ENTITY))
        if dc is None:
            if not self.coordinate_permission():
                self.pv_active = False
                return False
            dc = envelope.dc_forecast_w
        self.pv_active = pv_covers_dc(config, soc, now, surplus, dc, self.pv_active)
        return self.pv_active

    async def _reconcile_pv_sources(self) -> None:
        c, envelope = self.owner, self.envelope
        if envelope is None or envelope.dc_forecast_w is None:
            return
        previous = self.pv_active
        permission = self.pv_source_permission(envelope.config)
        actual_support = c.automatic_support_active_or_unknown()
        self._pv_restore_pending |= previous and not permission
        if not ((permission and actual_support) or self._pv_restore_pending):
            return
        valid_plan = dt_util.utcnow() < envelope.expires
        desired = dict(
            zip(
                ("dc24", "dc48"),
                envelope.dc_sources if valid_plan else (False, False),
                strict=True,
            )
        )
        if not valid_plan:
            await c.async_request_refresh()
            # The refreshed planner owns its own actuator task. Never send the
            # expired snapshot after waiting for a new plan to be published.
            envelope = self.envelope
            if envelope is None:
                return
            desired = dict(
                zip(
                    ("dc24", "dc48"),
                    envelope.dc_sources
                    if dt_util.utcnow() < envelope.expires
                    else (False, False),
                    strict=True,
                )
            )
        # The existing source owner acquires its own lock and rechecks PV after
        # every delayed transition. This timer never switches a PSU directly.
        await c._execute_coordinated_support(
            desired, False, envelope.config, dt_util.now()
        )
        if c._coordinated_support_diag.get("reason") == "settled":
            self._pv_restore_pending = False

    def _forecast_evidence_valid(self, envelope: LiveACEnvelope, now: datetime) -> bool:
        inputs = envelope.plan_inputs
        return inputs is None or self.owner.pv_plan_evidence_valid(
            envelope.config, inputs, now
        )

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
        elif not self._forecast_evidence_valid(envelope, now):
            # Measured demand proves a consumer is present. The envelope still
            # funds its discharge from the forecast-derived battery budget.
            reason = "pv_forecast_evidence_lost"
        elif not self.coordinate_permission():
            reason = "soc_coordinate_hold"
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
                (
                    envelope.support_required
                    and any(
                        required and not c.support_handed_over(key)
                        for key, required in zip(
                            ("dc24", "dc48"), envelope.dc_sources, strict=True
                        )
                    )
                )
                or c.automatic_support_active_or_unknown()
                or (
                    not c.support_handed_over("dc24")
                    and c.raw_config.get(CONF_DCDC_SWITCH)
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
                if envelope.accepted_budget_wh is not None:
                    available_wh = min(available_wh, envelope.accepted_budget_wh)
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
        now = dt_util.utcnow()
        if (
            not c._reserve_inverter_limit_w
            or envelope is None
            or now >= envelope.expires
            or not self._forecast_evidence_valid(envelope, now)
        ):
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
                "soc_coordinate_hold",
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
            now,
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
        c.voltage_gate.observe(dt_util.now())
        await stop_unsafe_loads(c)
        await self._reconcile_pv_sources()
        envelope = self.envelope
        if (
            envelope is not None
            and envelope.config.support.adaptive_voltage_gate
            and dt_util.utcnow() < envelope.expires
            and self._forecast_evidence_valid(envelope, dt_util.utcnow())
            and (c._switch_task is None or c._switch_task.done())
            and (envelope.dc_sources[1] or c._support_state["dc48"])
        ):
            await c._execute_coordinated_support(
                dict(zip(("dc24", "dc48"), envelope.dc_sources, strict=True)),
                self.planned_limit() > 0,
                envelope.config,
                dt_util.now(),
            )
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
                "soc_coordinate_hold",
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
                "soc_coordinate_hold",
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
