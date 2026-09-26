"""Persistent intent and observed shadow time; no actuator ownership here."""

from __future__ import annotations

import math
from datetime import datetime

# Two observed days are required before a new reserve policy may own actors.
# Downtime and missing observations must never silently count as a soak test.
SHADOW_SECONDS = 48 * 3600
MAX_OBSERVATION_GAP_SECONDS = 600


class ReserveRuntime:
    """Keep the requested reserve distinct from involuntary battery loss."""

    def __init__(self):
        self.hold_soc = None
        self.observed_seconds = 0.0
        self._last_at = None
        self._last_soc = None
        self._last_solar_only = False
        self.signature = None

    @property
    def ready(self):
        return self.observed_seconds >= SHADOW_SECONDS

    def restore(self, value):
        if not isinstance(value, dict):
            return
        hold = value.get("hold_soc")
        seconds = value.get("observed_seconds")
        if isinstance(hold, (int, float)) and math.isfinite(hold) and 0 <= hold <= 100:
            self.hold_soc = float(hold)
        if (
            isinstance(seconds, (int, float))
            and math.isfinite(seconds)
            and seconds >= 0
        ):
            self.observed_seconds = min(float(seconds), SHADOW_SECONDS)
        self.signature = value.get("signature")

    def export(self):
        return {
            "hold_soc": self.hold_soc,
            "observed_seconds": self.observed_seconds,
            "signature": self.signature,
        }

    def interrupt(self):
        self._last_at = self._last_soc = None
        self._last_solar_only = False

    def observe(
        self,
        now: datetime,
        soc: float,
        *,
        solar_only: bool,
        preparing: bool,
        signature: str,
    ):
        if signature != self.signature:
            self.hold_soc = None
            self.observed_seconds = 0.0
            self.signature = signature
            self.interrupt()
        if self.hold_soc is None:
            self.hold_soc = soc
        elapsed = (now - self._last_at).total_seconds() if self._last_at else 0
        if 0 < elapsed <= MAX_OBSERVATION_GAP_SECONDS:
            self.observed_seconds = min(SHADOW_SECONDS, self.observed_seconds + elapsed)
            delta = soc - self._last_soc
            # Only both endpoints with PSUs off can raise the solar intent.
            # No gap/restart and no PSU-origin increment buys future AC supply.
            if delta > 0 and solar_only and self._last_solar_only:
                self.hold_soc = min(
                    100.0, self.hold_soc + max(0.0, min(delta, soc - self.hold_soc))
                )
            elif delta < 0 and preparing:
                self.hold_soc = max(0.0, self.hold_soc + delta)
        self._last_at, self._last_soc = now, soc
        self._last_solar_only = solar_only


def reserve_diagnostics(config, inputs, result, baseline, runtime, mode):
    """Expose requested and reachable energy separately, also in shadow mode."""
    flows = result.trajectory.flows
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
    return {
        "mode": mode,
        "shadow_ready": runtime.ready,
        "shadow_observed_hours": round(runtime.observed_seconds / 3600, 2),
        "hold_soc_percent": round(hold, 2),
        "actual_soc_percent": inputs.start_soc_percent,
        "expected_min_soc_percent": round(minimum, 2),
        "headroom_wh": round(
            max(
                0.0,
                b.energy_wh(
                    inputs.start_soc_percent - flows[0].reserve_ceiling_percent
                ),
            ),
            1,
        )
        if flows
        else 0.0,
        "preparation_start": preparation,
        "inverter_limit_w": round(flows[0].inverter_limit_w, 1) if flows else 0.0,
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
        "uncertainty": "p90_or_uncalibrated_scalar",
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
