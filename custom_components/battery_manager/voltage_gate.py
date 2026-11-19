"""Live voltage authority and telemetry adapter for the single supply owner."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_PSU48_OFF_VOLTAGE_V,
    CONF_PSU48_ON_VOLTAGE_V,
    CONF_PSU48_OUTPUT_VOLTAGE_V,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC48_SWITCH,
)
from .core.model import PlanResult, SocCoordinateSnapshot, SystemConfig, VoltageGateBand
from .core.voltage_gate import voltage_request
from .gate_learning import GateLearner, GateSample
from .net_discharge import (
    BATTERY_COORDINATE_KEYS,
    NetDischargeCapture,
    NetDischargeDiagnostic,
)
from .psu_observation import PsuObservationCapture
from .soc_consistency import SocConsistencyMonitor, SocObservation

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

# Shared with the documented legacy controller; all tests use virtual time.
ON_DWELL_SECONDS = 60
OFF_DWELL_SECONDS = 300
MEASUREMENT_MAX_AGE_SECONDS = 30
_LOGGER = logging.getLogger(__name__)


class CoordinatePlanChanged(Exception):
    """Informational invalidation: a fresh plan is needed, not an integration fault."""


def requested_dc48(result: PlanResult) -> bool:
    requested = (
        result.trajectory.flows[0].support_dc48_requested
        if result.trajectory.flows
        else None
    )
    return result.support_dc48_now if requested is None else requested


class VoltageGateRuntime:
    def __init__(self, owner: BatteryManagerCoordinator) -> None:
        self.owner = owner
        self.learner = GateLearner()
        self.monitor = SocConsistencyMonitor()
        self.psu = PsuObservationCapture()
        self._net_discharge = NetDischargeCapture(owner, self.monitor)
        self.published_coordinate: SocCoordinateSnapshot | None = None
        self._replan_after_task: Any = None
        self.ready = False
        self.diagnostics: dict[str, Any] = {"reason": "no_request"}
        self._candidate: bool | None = None
        self._since: float | None = None
        self._coordinate_unsub: list[Callable[[], None]] = []
        self._coordinate_entities: tuple[str, ...] = ()
        self._coordinate_readings: dict[str, tuple[str, Any, float] | None] = {}
        self._coordinate_last_at: float | None = None

    def start_observing(self) -> None:
        """Keep original publications before planner debounce / five-second ticks."""
        self.psu.start(self.owner)
        if self.ready:
            self._net_discharge.start()
        entities = tuple(
            entity.lower()
            for key in BATTERY_COORDINATE_KEYS
            if (entity := self.owner.raw_config.get(key))
        )
        if self._coordinate_unsub and entities == self._coordinate_entities:
            return
        self._stop_coordinate_observing()
        self._coordinate_entities = entities
        for entity in entities:
            state = self.owner.hass.states.get(entity)
            self._coordinate_readings[entity] = self._reading(state)
        if entities:
            self._coordinate_unsub = [
                async_track_state_change_event(
                    self.owner.hass, entities, self._coordinate_publication
                ),
                async_track_state_report_event(
                    self.owner.hass, entities, self._coordinate_publication
                ),
            ]

    def stop_observing(self) -> None:
        self.psu.stop()
        self._net_discharge.stop()
        self._stop_coordinate_observing()

    def _stop_coordinate_observing(self) -> None:
        for unsubscribe in self._coordinate_unsub:
            unsubscribe()
        self._coordinate_unsub.clear()
        self._coordinate_entities = ()
        self._coordinate_readings.clear()
        self._coordinate_last_at = None

    @staticmethod
    def _reading(
        state: State | None, event_at: float | None = None
    ) -> tuple[str, Any, float] | None:
        if state is None:
            return None
        # An unchanged HA State can be republished/mutated before its queued
        # callback runs. Retain this event's time, not a later report's clock.
        published = state.last_reported.timestamp()
        return (
            state.state,
            state.attributes.get("unit_of_measurement"),
            min(published, event_at) if event_at is not None else published,
        )

    @callback
    def _coordinate_publication(self, event: Any) -> None:
        entity = event.data["entity_id"]
        if entity not in self._coordinate_entities:
            return
        at = event.time_fired.timestamp()
        reading = self._reading(event.data.get("new_state"), at)
        before = self._coordinate_readings.get(entity)
        if before is not None and reading is not None and reading[2] < before[2]:
            self._coordinate_gap()
            self._net_discharge.publication(event)
            return
        self._coordinate_readings[entity] = reading
        if not self.ready:
            return
        self._bind()
        self._sample_coordinate(at)
        self._net_discharge.publication(event)

    def _sample_coordinate(self, at: float) -> None:
        cfg = self.owner.raw_config
        readings = [
            self._coordinate_readings.get(entity.lower())
            if isinstance((entity := cfg.get(key)), str)
            else None
            for key in BATTERY_COORDINATE_KEYS
        ]
        sample = None
        if all(reading is not None for reading in readings):
            soc, power, voltage = readings
            assert soc is not None and power is not None and voltage is not None
            try:
                if soc[1] == "%" and power[1] in ("W", "kW") and voltage[1] == "V":
                    sample = SocObservation(
                        at,
                        float(soc[0]),
                        float(power[0]) * (1000 if power[1] == "kW" else 1),
                        float(voltage[0]),
                        soc[2],
                        power[2],
                        voltage[2],
                    )
            except TypeError, ValueError:
                pass
        self._coordinate_last_at = at
        before = (self.monitor.epoch, self.monitor.phase)
        changed = self.monitor.observe(
            sample,
            capacity_wh=float(cfg["battery_capacity_wh"]),
            eta_charge=float(cfg.get("battery_charge_efficiency", 1.0)),
            eta_discharge=float(cfg.get("battery_discharge_efficiency", 1.0)),
            power_trace_complete=True,
        )
        if changed:
            self.learner.expire(at)
            self.learner.begin_epoch(self.monitor.epoch, "soc_coordinate_adjustment")
            if before[1] != "tracking" and self.monitor.phase == "tracking":
                self.learner.reset_stability()
            self._coordinate_transition(before)

    def _coordinate_gap(self) -> None:
        before = (self.monitor.epoch, self.monitor.phase)
        if self.monitor.observe(None, capacity_wh=1, power_trace_complete=True):
            self._coordinate_transition(before)

    def _coordinate_transition(self, before: tuple[int, str]) -> None:
        c = self.owner
        new_epoch = before[0] != self.monitor.epoch
        resumed = before[1] != "tracking" and self.monitor.phase == "tracking"
        if new_epoch or resumed:
            if appliances := getattr(c, "appliances", None):
                # Withdraw forecast start advice, not running appliance service.
                appliances.plan_failed()
            # Preserve source requests/manual protection. Expire only the
            # economic certificate, which actors re-check independently.
            envelope = c.live_ac.envelope
            if envelope is not None:
                c.live_ac.envelope = replace(envelope, expires=dt_util.utcnow())
                c.live_ac.plan_revision += 1
            if new_epoch:
                _LOGGER.info(
                    "SOC coordinate adjustment: epoch %s; fresh permissions required",
                    self.monitor.epoch,
                )
            self.request_fresh_plan()
        c._save_persistent_state()

    def request_fresh_plan(self) -> None:
        """An in-progress debounced search must not swallow its replacement."""
        c = self.owner
        task = getattr(c, "_debounce_task", None)
        if task is not None and not task.done():
            if self._replan_after_task is not task:
                self._replan_after_task = task

                def finished(_task: Any) -> None:
                    self._replan_after_task = None
                    if not getattr(c, "_actuation_shutdown", False):
                        c._schedule_replan()

                task.add_done_callback(finished)
        elif not getattr(c, "_actuation_shutdown", False):
            c._schedule_replan()

    def coordinate_snapshot(self, now: datetime) -> SocCoordinateSnapshot:
        self._bind()
        return SocCoordinateSnapshot(
            self.monitor.epoch,
            self.monitor.phase,
            self.monitor.generation,
            now.timestamp(),
        )

    def net_discharge_diagnostic(
        self, now: datetime, *, grid_only: bool = False
    ) -> NetDischargeDiagnostic:
        """Read qualified battery terminal evidence for display projection."""
        if not self.ready:
            return {"reason": "net_telemetry_unavailable"}
        self._bind()
        return self._net_discharge.diagnostic(now, grid_only=grid_only)

    def coordinate_matches(self, snapshot: SocCoordinateSnapshot | None) -> bool:
        if snapshot is None:
            return not self.monitor.has_reconciled and self.monitor.phase == "tracking"
        return snapshot.same_coordinate(self.coordinate_snapshot(dt_util.now()))

    def coordinate_permission_valid(
        self,
        snapshot: SocCoordinateSnapshot | None,
        *,
        direct_pv_proven: bool = False,
    ) -> bool:
        return self.coordinate_matches(snapshot) and (
            self.monitor.phase == "tracking" or direct_pv_proven
        )

    def export(self) -> dict[str, Any]:
        return {**self.learner.export(), "coordinate": self.monitor.export()}

    def restore(self, data: Any) -> None:
        self.stop_observing()
        self.learner.restore(data)
        coordinate = data.get("coordinate") if isinstance(data, dict) else None
        if isinstance(data, dict) and data.get("version") == 2 and coordinate is None:
            coordinate = {}  # Lost v2 provenance cannot revive an old grant.
        self.monitor.restore(
            coordinate,
            initial_epoch=self.learner.epoch,
            fingerprint=self.learner.fingerprint,
        )
        if self.monitor.epoch != self.learner.epoch:
            self.learner.begin_epoch(self.monitor.epoch, "restored_coordinate_epoch")
        if self.monitor.phase != "tracking":
            self.learner.quarantine_active("restored_coordinate_hold")
        self.published_coordinate = None

    def voltage(self) -> float | None:
        c = self.owner
        entity = c.raw_config.get(CONF_BATTERY_VOLTAGE_ENTITY)
        state = c.hass.states.get(entity) if entity else None
        if (
            not entity
            or state is None
            or state.attributes.get("unit_of_measurement") != "V"
            or not 0
            <= (dt_util.utcnow() - state.last_reported).total_seconds()
            <= MEASUREMENT_MAX_AGE_SECONDS
        ):
            return None
        value = c._read_float(entity)
        return value if value is not None and 40 <= value <= 60 else None

    def _soc(self, now: datetime) -> float | None:
        entity = self.owner.raw_config.get(CONF_SOC_ENTITY)
        state = self.owner.hass.states.get(entity) if entity else None
        if (
            not entity
            or state is None
            or self.owner._house_soc_stale is not None
            or not 0
            <= (now - state.last_reported).total_seconds()
            <= MEASUREMENT_MAX_AGE_SECONDS
        ):
            return None
        # Do not substitute the coordinator's historical fallback for a fresh
        # gate observation or permission to remove a protective source.
        value = self.owner._read_float(entity)
        return value if value is not None and 0 <= value <= 100 else None

    def _bind(self) -> None:
        cfg = self.owner.raw_config
        keys = (
            CONF_SOC_ENTITY,
            CONF_BATTERY_VOLTAGE_ENTITY,
            CONF_FEEDIN_BATTERY_POWER_ENTITY,
            CONF_SUPPORT_DC48_SWITCH,
            CONF_PSU48_OUTPUT_VOLTAGE_V,
            CONF_PSU48_ON_VOLTAGE_V,
            CONF_PSU48_OFF_VOLTAGE_V,
            "battery_capacity_wh",
            "battery_cells_series",
        )
        fingerprint = json.dumps([cfg.get(k) for k in keys])
        self.monitor.bind(fingerprint, initial_epoch=max(1, self.learner.epoch))
        self.learner.bind(fingerprint, epoch=self.monitor.epoch)

    def observe(self, now: datetime) -> None:
        if not self.ready:
            return  # Startup telemetry must not overwrite unrestored evidence.
        c = self.owner
        self._bind()
        self.start_observing()
        if self._coordinate_last_at is None:
            self._sample_coordinate(now.timestamp())
        elif now.timestamp() - self._coordinate_last_at > MEASUREMENT_MAX_AGE_SECONDS:
            self._coordinate_gap()
        voltage = self.voltage()
        soc = self._soc(now)
        power_entity = c.raw_config.get(CONF_FEEDIN_BATTERY_POWER_ENTITY)
        power = c._reserve_power(power_entity)
        soc_entity = c.raw_config.get(CONF_SOC_ENTITY)
        soc_state = c.hass.states.get(soc_entity) if soc_entity else None
        fresh_soc = (
            soc_state is not None
            and 0
            <= (now - soc_state.last_reported).total_seconds()
            <= MEASUREMENT_MAX_AGE_SECONDS
        )
        sample = (
            GateSample(now.timestamp(), soc, voltage, -power / voltage)
            if voltage is not None
            and soc is not None
            and power is not None
            and fresh_soc
            and abs(power / voltage) < 100
            else None
        )
        psu = c.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
        # An unknown or energised source can be regulating. Learn only natural
        # voltage with a confirmed OFF; this requires no probing or new hardware.
        eligible = bool(psu and c._entity_tristate(psu) is False)
        expired = self.learner.expire(now.timestamp())
        if self.monitor.phase != "tracking":
            changed = (
                self.learner.quarantine(sample, self.monitor.phase)
                if sample is not None and eligible
                else False
            )
            self.learner.reason = "coordinate_quarantined"
        else:
            changed = self.learner.observe(
                sample,
                eligible=eligible,
                capacity_wh=float(c.raw_config["battery_capacity_wh"]),
                detect_recalibration=False,
            )
        if changed or expired:
            c._save_persistent_state()

    def bands(self, now: datetime) -> tuple[VoltageGateBand, ...]:
        self._bind()
        cfg = self.owner.raw_config
        return self.learner.bands(
            now.timestamp(),
            (
                float(cfg[CONF_PSU48_ON_VOLTAGE_V]),
                float(cfg[CONF_PSU48_OUTPUT_VOLTAGE_V]),
                float(cfg[CONF_PSU48_OFF_VOLTAGE_V]),
            ),
        )

    def apply(
        self, config: SystemConfig, desired: dict[str, bool], now: datetime
    ) -> dict[str, bool]:
        """Filter economic intent, retaining manual/protection readiness."""
        if not config.support.adaptive_voltage_gate:
            return desired
        c = self.owner
        entity = c.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
        active = bool(entity and c._entity_tristate(entity) is True)
        voltage = self.voltage()
        soc = self._soc(now)
        protection = (
            soc is None
            or soc <= config.control.support_dc48_activate_soc
            or (active and soc < config.control.support_dc48_recovery_soc)
        )
        manual = c._support_manual["dc48"]
        wanted = desired["dc48"] or manual or protection
        allowed, reason = voltage_request(
            voltage,
            active,
            config.support.psu48_on_voltage_v,
            config.support.psu48_off_voltage_v,
        )
        if manual or protection:
            allowed, reason = (
                True,
                "manual_readiness" if manual else "protection_readiness",
            )
        if not wanted:
            allowed, reason = False, "no_request"
        target = allowed and wanted
        if target == active or manual or protection or not wanted or voltage is None:
            self._candidate = None
            self._since = None
        else:
            if self._candidate is not target:
                self._candidate, self._since = target, now.timestamp()
            dwell = ON_DWELL_SECONDS if target else OFF_DWELL_SECONDS
            if self._since is None or now.timestamp() - self._since < dwell:
                target, reason = (
                    active,
                    "waiting_on_voltage" if target else "waiting_off_voltage",
                )
        self.diagnostics = {
            "reason": reason,
            "voltage_v": voltage,
            "requested": wanted,
            "target": target,
            "on_voltage_v": config.support.psu48_on_voltage_v,
            "off_voltage_v": config.support.psu48_off_voltage_v,
        }
        return {**desired, "dc48": target}

    def diagnostic(self, now: datetime) -> dict[str, Any]:
        self._bind()
        self.psu.capture(self.owner, now)
        cfg = self.owner.raw_config
        bands, details = self.learner.evaluate(
            now.timestamp(),
            (
                float(cfg[CONF_PSU48_ON_VOLTAGE_V]),
                float(cfg[CONF_PSU48_OUTPUT_VOLTAGE_V]),
                float(cfg[CONF_PSU48_OFF_VOLTAGE_V]),
            ),
        )
        # A named reference query, not a claim that every future bus load has
        # this current. Full per-bin evidence lives in the private download.
        current = float(cfg.get("native48_base_w", 0)) / float(
            cfg[CONF_PSU48_OUTPUT_VOLTAGE_V]
        )
        detail = next(
            (
                item
                for item in details
                if item["current_min_a"] <= current < item["current_max_a"]
            ),
            None,
        )
        current_covered = any(
            band.current_min_a <= current < band.current_max_a for band in bands
        )
        trimmed_miss = bool(
            detail
            and not current_covered
            and any(item["reason"] == "qualified" for item in detail["roles"])
        )
        return {
            **self.diagnostics,
            "learning_reason": self.learner.reason,
            "epoch": self.learner.epoch,
            "sample_count": len(self.learner.samples),
            "last_observation_at": self.learner.samples[-1].at
            if self.learner.samples
            else None,
            "bands": [asdict(band) for band in bands],
            "source": "learned" if bands else "configured_fallback",
            "power_credit": "estimate_only",
            "coordinate": {"mode": "active", **self.monitor.diagnostic()},
            "psu_observations": self.psu.diagnostic(),
            "psu48_on_delivery": self.psu.expected48_diagnostic(now),
            "qualification": {
                "query_current_a": current,
                "query_basis": "native48_base_only",
                "current_covered": current_covered,
                "roles": detail["roles"] if detail else [],
                "reason": "out_of_current_range"
                if detail is None or trimmed_miss
                else None,
            },
        }

    def private_diagnostic(self, now: datetime) -> dict[str, Any]:
        """Bounded raw learning evidence belongs in a requested download only."""
        self._bind()
        self.psu.capture(self.owner, now)
        cfg = self.owner.raw_config
        _, details = self.learner.evaluate(
            now.timestamp(),
            (
                float(cfg[CONF_PSU48_ON_VOLTAGE_V]),
                float(cfg[CONF_PSU48_OUTPUT_VOLTAGE_V]),
                float(cfg[CONF_PSU48_OFF_VOLTAGE_V]),
            ),
        )
        return {
            "natural_voltage": self.learner.export(),
            "qualification": details,
            "soc_coordinate": self.monitor.diagnostic(),
            "coordinate_events": list(self.monitor.events),
            "psu_observations": self.psu.private_diagnostic(),
            "psu48_on_delivery": self.psu.expected48_diagnostic(now),
        }
