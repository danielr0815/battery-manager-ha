"""Capture the physical supply episode and recheck it when publishing a forecast."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from .core.model import PlanInputs, PlanResult, Psu48SupplyEvidence, Trajectory
from .core.psu_expectation import MAX_PROJECTION_SECONDS
from .core.psu_supply import GRID_IMPORT_PROJECTION_SECONDS

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


def read_supply_evidence(
    owner: BatteryManagerCoordinator, inputs: PlanInputs, now: datetime
) -> tuple[Psu48SupplyEvidence | None, dict[str, Any]]:
    """Read qualified observations immediately after the PSU snapshot at `now`."""
    gate = owner.voltage_gate
    psu = gate.psu
    details = psu.expectation_explanation(owner, now)
    kind: Literal["direct", "grid_import", "night"] = "direct"
    if details.get("reason") == "missing_dc_meter":
        net = gate.net_discharge_diagnostic(now)
        # A zero PV reading can coincide with import. Prefer the import
        # evidence if forecast generation already rules out a zero-PV slot.
        first = inputs.slots[0]
        if (
            "net_discharge_w" in net
            and "evidence_mode" not in net
            and max(first.pv_wh, first.pv_p90_wh or 0.0) > 0
        ):
            grid = gate.net_discharge_diagnostic(now, grid_only=True)
            if "net_discharge_w" in grid:
                net = grid
        details = {
            "basis": "battery_net",
            "relay_confirmed_on": details["relay_confirmed_on"],
            **net,
        }
        if "net_discharge_w" not in net:
            return None, details
        kind = "grid_import" if net.get("evidence_mode") == "grid_import" else "night"
        watts = net["net_discharge_w"]
    else:
        direct = psu.expected48(now)
        if direct is None:
            return None, details
        watts, _ = direct
        details["delivery_w"] = watts

    if gate.monitor.phase != "tracking" or not gate.coordinate_matches(
        inputs.soc_coordinate
    ):
        return None, {**details, "reason": "net_coordinate_unstable"}
    if gate.forecast_soc(now) != inputs.start_soc_percent:
        return None, {**details, "reason": "supply_soc_anchor_changed"}
    # A new episode during the asynchronous plan cannot explain its start SOC.
    if not details["observed_from"] <= inputs.now.timestamp() <= now.timestamp():
        return None, {**details, "reason": "net_plan_outside_observation"}

    span = details["observed_seconds"]
    maximum = (
        GRID_IMPORT_PROJECTION_SECONDS
        if kind == "grid_import"
        else MAX_PROJECTION_SECONDS
    )
    until = min(
        details.get("valid_until", float("inf")),
        details["observed_until"] + min(span, maximum),
    )
    if until <= now.timestamp():
        return None, {**details, "reason": "supply_evidence_ends"}
    return Psu48SupplyEvidence(
        captured_at=now.timestamp(),
        observed_seconds=span,
        valid_until=until,
        kind=kind,
        power_w=watts,
        grid_import_w=details.get("grid_import_w"),
        grid_setpoint_w=details.get("grid_setpoint_w"),
    ), details


def published_supply_forecast(
    owner: BatteryManagerCoordinator,
    inputs: PlanInputs,
    result: PlanResult,
    now: datetime,
) -> tuple[Trajectory, dict[str, Any]]:
    """Publish measured support only while the original episode remains plausible."""
    owner.voltage_gate.psu.capture(owner, now)
    current, details = read_supply_evidence(owner, inputs, now)
    captured = inputs.psu48_supply
    if current is None:
        return result.trajectory, {**details, "status": "unqualified"}
    if (
        captured is None
        or current.kind != captured.kind
        or current.grid_setpoint_w != captured.grid_setpoint_w
        or (current.kind == "direct" and current.power_w < captured.power_w)
        # Net estimates are a measured rate. Ignore sub-watt report jitter
        # during CPU work but never retain support after a material load step.
        or (current.kind != "direct" and current.power_w > captured.power_w + 1.0)
        or now.timestamp() >= captured.valid_until
        or (
            result.psu48_supply_until is not None
            and now.timestamp() >= result.psu48_supply_until
        )
    ):
        return result.trajectory, {
            **details,
            "status": "no_window",
            "reason": "supply_evidence_changed",
        }
    if result.operating_trajectory is None or result.psu48_supply_until is None:
        return result.trajectory, {
            **details,
            "status": "no_window",
            "reason": result.psu48_supply_stop_reason,
        }
    return result.operating_trajectory, {
        **details,
        "status": "available",
        "operating_forecast": True,
        "delivery_w"
        if captured.kind == "direct"
        else "net_discharge_w": captured.power_w,
        "observed_seconds": captured.observed_seconds,
        "end_at": datetime.fromtimestamp(result.psu48_supply_until, UTC).isoformat(),
        "end_soc": result.psu48_supply_end_soc,
        "stop_reason": result.psu48_supply_stop_reason,
    }
