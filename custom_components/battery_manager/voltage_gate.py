"""Live voltage authority and telemetry adapter for the single supply owner."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
from .core.model import PlanResult, SystemConfig, VoltageGateBand
from .core.voltage_gate import voltage_request
from .gate_learning import GateLearner, GateSample

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

# Shared with the documented legacy controller; all tests use virtual time.
ON_DWELL_SECONDS = 60
OFF_DWELL_SECONDS = 300
MEASUREMENT_MAX_AGE_SECONDS = 30


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
        self.ready = False
        self.diagnostics: dict[str, Any] = {"reason": "no_request"}
        self._candidate: bool | None = None
        self._since: float | None = None

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
        self.learner.bind(json.dumps([cfg.get(k) for k in keys]))

    def observe(self, now: datetime) -> None:
        if not self.ready:
            return  # Startup telemetry must not overwrite unrestored evidence.
        c = self.owner
        self._bind()
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
        if self.learner.observe(
            sample,
            eligible=eligible,
            capacity_wh=float(c.raw_config["battery_capacity_wh"]),
        ):
            c._save_persistent_state()
            if self.learner.reason == "recalibration_suspected":
                # A cached economic permission belongs to the old SOC scale.
                # Expire it before the next fast AC tick, while retaining the
                # captured record as evidence for the replacement plan.
                envelope = c.live_ac.envelope
                if envelope is not None:
                    c.live_ac.envelope = replace(envelope, expires=dt_util.as_utc(now))
                    c.live_ac.plan_revision += 1
                if envelope is not None or c._last_planner_recording is not None:
                    c._schedule_replan()

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
        bands = self.bands(now)
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
        }
