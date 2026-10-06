"""Historical reserve observations; never a physical setpoint or control gate."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, TypedDict

from .core.model import PlanInputs, PlanResult, SystemConfig
from .core.uncertainty import reserve_preparation_scales

# Gaps must not invent a solar gain or hide involuntary loss. Observation time
# is diagnostic only, never a prerequisite for forecast-driven actuation.
MAX_OBSERVATION_GAP_SECONDS = 600
# Version 2 separates historical references from physical reserve decisions.
RESERVE_POLICY_VERSION = 2


class ReserveObservationState(TypedDict):
    """Legacy JSON shape retained for observations from earlier releases."""

    policy_version: int
    hold_soc: float | None
    observed_seconds: float
    signature: str | None


class ReserveRuntime:
    """Track a historical reference without authorizing or vetoing any actuator.

    ``hold_soc`` retains its stored name for compatibility. Its initial balance
    has unknown origin; subsequent verified solar observations only explain
    changes. Neither the balance nor missing meter data is a reserve setpoint.
    """

    def __init__(self) -> None:
        self.policy_version = RESERVE_POLICY_VERSION
        self.hold_soc: float | None = None
        self.observed_seconds = 0.0
        self._last_at: datetime | None = None
        self._last_soc: float | None = None
        self._last_solar_only = False
        self.signature: str | None = None

    def restore(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        version = value.get("policy_version")
        self.policy_version = (
            version if type(version) is int and version >= RESERVE_POLICY_VERSION else 1
        )
        hold = value.get("hold_soc")
        seconds = value.get("observed_seconds")
        hold = _finite_number(hold)
        if hold is not None and 0 <= hold <= 100:
            self.hold_soc = hold
        seconds = _finite_number(seconds)
        if seconds is not None and seconds >= 0:
            self.observed_seconds = seconds
        signature = value.get("signature")
        self.signature = signature if isinstance(signature, str) else None

    def export(self) -> ReserveObservationState:
        return {
            "policy_version": self.policy_version,
            "hold_soc": self.hold_soc,
            "observed_seconds": self.observed_seconds,
            "signature": self.signature,
        }

    def interrupt(self) -> None:
        self._last_at = None
        self._last_soc = None
        self._last_solar_only = False

    def observe(
        self,
        now: datetime,
        soc: float,
        *,
        solar_only: bool,
        preparing: bool,
        signature: str,
    ) -> None:
        if signature != self.signature:
            self.hold_soc = None
            self.observed_seconds = 0.0
            self.signature = signature
            self.interrupt()
        if self.hold_soc is None:
            self.hold_soc = soc
        elapsed = (now - self._last_at).total_seconds() if self._last_at else 0
        if 0 < elapsed <= MAX_OBSERVATION_GAP_SECONDS:
            self.observed_seconds += elapsed
            assert self._last_soc is not None
            delta = soc - self._last_soc
            # Both endpoints need verified solar-only supply. A rising SOC with
            # missing meters is unknown provenance, not evidence of solar gain.
            if delta > 0 and solar_only and self._last_solar_only:
                self.hold_soc = min(
                    100.0, self.hold_soc + max(0.0, min(delta, soc - self.hold_soc))
                )
            elif delta < 0 and preparing:
                self.hold_soc = max(0.0, self.hold_soc + delta)
        self._last_at, self._last_soc = now, soc
        self._last_solar_only = solar_only


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def reserve_diagnostics(
    config: SystemConfig,
    inputs: PlanInputs,
    result: PlanResult,
    baseline: PlanResult,
    runtime: ReserveRuntime,
    mode: str,
) -> dict[str, Any]:
    """Render the core's physical decision separately from historical provenance.

    Legacy hold fields remain readable for existing consumers. They describe a
    reference balance only and must not be interpreted as a desired setpoint.
    """
    flows = result.trajectory.flows
    decision = result.trajectory.reserve_decision
    b = config.battery
    hold = min(
        b.soc_max_percent,
        runtime.hold_soc if runtime.hold_soc is not None else inputs.start_soc_percent,
    )
    preparation = next(
        (
            f.reserve_preparation_start.isoformat()
            for f in flows
            if f.reserve_preparation_start is not None
        ),
        None,
    )
    minimum = result.trajectory.min_soc_percent
    pv_buffers_by_day: dict[str, float] = {}
    for slot, scale in zip(
        inputs.slots, reserve_preparation_scales(config, inputs), strict=True
    ):
        day = slot.start.date().isoformat()
        pv_buffers_by_day[day] = pv_buffers_by_day.get(day, 0.0) + (
            slot.pv_wh * (scale - 1) * b.eta_charge * config.charger.eta
        )
    return {
        "mode": mode,
        "shadow_ready": True,  # Compatible field: no observation prerequisite.
        "shadow_required_hours": 0,
        "control_basis": "forecast",
        "preparation_pv_basis": "expected_with_bounded_uncertainty",
        "soft_soc_ceiling_percent": config.reserve.soft_soc_ceiling_percent,
        "pv_uncertainty_budget_wh": round(sum(pv_buffers_by_day.values()), 1),
        "pv_uncertainty_budget_wh_by_day": {
            day: round(wh, 1) for day, wh in pv_buffers_by_day.items()
        },
        "consumption_buffer_wh": round(
            b.energy_wh(config.control.soc_buffer_percent), 1
        ),
        "shadow_observed_hours": round(runtime.observed_seconds / 3600, 2),
        "hold_soc_percent": round(hold, 2),
        "historical_reference_soc_percent": round(runtime.hold_soc, 2)
        if runtime.hold_soc is not None
        else None,
        "reference_semantics": "historical_observation_only",
        "preparation_horizon_end": decision.preparation_horizon_end.isoformat()
        if decision is not None
        else None,
        "decision_reason": decision.reason if decision is not None else None,
        "actual_soc_percent": inputs.start_soc_percent,
        "expected_min_soc_percent": round(minimum, 2),
        "headroom_wh": round(decision.headroom_wh, 1) if decision is not None else 0.0,
        "unavoidable_export_wh": round(decision.unavoidable_export_wh, 1)
        if decision is not None
        else 0.0,
        "preparation_start": preparation,
        "inverter_limit_w": round(decision.inverter_limit_w, 1)
        if decision is not None
        else 0.0,
        "hold_shortfall_wh": round(
            max(0.0, b.energy_wh(hold - inputs.start_soc_percent)), 1
        ),
        "remaining_discharge_wh": round(sum(f.battery_discharge_wh for f in flows), 1),
        "extra_grid_import_wh": round(
            max(
                0.0,
                result.trajectory.total_import_wh - baseline.trajectory.total_import_wh,
            ),
            1,
        ),
        "incidental_grid_charge_wh": round(
            sum(f.psu48_battery_charge_wh for f in flows), 1
        ),
        "psu48_delivered_wh": round(sum(f.psu48_delivered_wh for f in flows), 1),
        "psu48_power_source": "voltage_estimate"
        if config.support.psu48_bus_voltage_v is not None
        else "unknown",
        "hold_achievable": not flows
        or not (
            flows[0].reserve_dc_ceiling_percent >= inputs.start_soc_percent
            and flows[0].battery_discharge_wh > flows[0].battery_charge_wh + 1e-6
        ),
        "upper_pv_factor": config.reserve.upper_pv_factor,
        "uncertainty": "bounded_pv_and_consumption_buffer",
        "upper_pv_role": "bounded_uncertainty_and_diagnostics",
        "grid_recharge": False,
        "emergency_feed_in": "requires_proven_total_benefit",
        "curve": [
            {
                "t": s.start.isoformat(),
                "ceiling_soc": round(f.reserve_ceiling_percent, 2),
                "dc_ceiling_soc": round(f.reserve_dc_ceiling_percent, 2),
                "soc": round(f.soc_end_percent, 2),
                "inverter_limit_w": round(f.inverter_limit_w, 1),
            }
            for s, f in zip(inputs.slots, flows, strict=True)
        ],
    }
