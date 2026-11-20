"""Nominal 48 V supply along the independently accepted dispatch."""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta

from .model import HourFlows, HourSlot, PlanInputs, PlanResult, SystemConfig, Trajectory
from .planning_control import check_cancelled
from .psu_expectation import (
    GRID_IMPORT_MARGIN_W,
    MAX_PROJECTION_SECONDS,
    MIN_OBSERVED_SECONDS,
)
from .reserve_energy import ENERGY_EPSILON_WH, dc_loads
from .series import appliance_run_energy
from .simulate import step_hour
from .uncertainty import effective_uncertainty
from .voltage_gate import PSU_MEASUREMENT_MAX_AGE_S

# Daytime transfer is limited by short-term comparability of house demand and PV.
GRID_IMPORT_PROJECTION_SECONDS = 3600.0
_ENERGY_FIELDS = tuple(
    field.name for field in fields(HourFlows) if field.name.endswith("_wh")
)


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _slice(slot: HourSlot, start: datetime, hours: float) -> HourSlot:
    ratio = hours / slot.duration
    return replace(
        slot,
        start=start.astimezone(slot.start.tzinfo)
        if slot.start.tzinfo
        else start.replace(tzinfo=None),
        duration=hours,
        pv_wh=slot.pv_wh * ratio,
        ac_wh=slot.ac_wh * ratio,
        dc_wh=slot.dc_wh * ratio,
    )


def _settle_slot(before: HourFlows, parts: list[HourFlows]) -> HourFlows:
    return replace(
        before,
        **{name: sum(getattr(part, name) for part in parts) for name in _ENERGY_FIELDS},
        soc_start_percent=parts[0].soc_start_percent,
        soc_end_percent=parts[-1].soc_end_percent,
        gate_open=any(part.gate_open for part in parts),
        dc_deficit_intervals=tuple(
            interval for part in parts for interval in part.dc_deficit_intervals or ()
        ),
    )


def operating_supply(
    config: SystemConfig, inputs: PlanInputs, result: PlanResult
) -> PlanResult:
    """Settle nominal physics with supply evidence while retaining actuator decisions.

    An unsupported or interrupted observation cannot start again in the horizon.
    Re-settle later steps from the resulting storage energy, including PV charge
    and export at the battery ceiling. The conservative trajectory still owns
    every permission, comparison, stress proof and reserve budget.
    """
    evidence = inputs.psu48_supply
    if evidence is None:
        return result
    accepted_dispatch = result.trajectory._dispatch
    if accepted_dispatch:
        result = replace(result, trajectory=replace(result.trajectory, _dispatch=()))

    support = config.support
    now = _utc(inputs.now)
    if not 0 <= now.timestamp() - evidence.captured_at <= PSU_MEASUREMENT_MAX_AGE_S:
        return replace(result, psu48_supply_stop_reason="supply_evidence_stale")
    if evidence.observed_seconds < MIN_OBSERVED_SECONDS:
        return replace(result, psu48_supply_stop_reason="supply_evidence_short")
    if not (
        support.configured
        and support.coordinated
        and support.adaptive_voltage_gate
        and support.dc24_available
        and support.dc48_available
        and support.dc24_active
        and support.dc48_active
        and support.native48_base_w > 0
        and accepted_dispatch
    ):
        return replace(result, psu48_supply_stop_reason="no_matching_plan_window")
    native_w = support.native48_base_w
    delivery_w = (
        evidence.power_w if evidence.kind == "direct" else native_w - evidence.power_w
    )
    if delivery_w == 0.0:
        return replace(result, psu48_supply_stop_reason="no_positive_delivery")
    limit_w = support.psu48_max_power_w
    if delivery_w < 0 or (limit_w is not None and delivery_w > limit_w):
        return replace(result, psu48_supply_stop_reason="supply_power_implausible")
    delivery_w = min(native_w, delivery_w)

    required_import_w = 0.0
    maximum_seconds = MAX_PROJECTION_SECONDS
    if evidence.kind == "grid_import":
        if evidence.grid_import_w is None or evidence.grid_setpoint_w is None:
            return replace(
                result, psu48_supply_stop_reason="net_grid_plan_margin_not_met"
            )
        required_import_w = max(0.0, evidence.grid_setpoint_w) + GRID_IMPORT_MARGIN_W
        if evidence.grid_import_w <= required_import_w:
            return replace(
                result, psu48_supply_stop_reason="net_grid_plan_margin_not_met"
            )
        maximum_seconds = GRID_IMPORT_PROJECTION_SECONDS
    expires = datetime.fromtimestamp(
        min(
            evidence.valid_until,
            evidence.captured_at + min(evidence.observed_seconds, maximum_seconds),
        ),
        UTC,
    )
    if expires <= now:
        return replace(result, psu48_supply_stop_reason="supply_evidence_ends")
    expiry_reason = "supply_evidence_ends"
    if evidence.kind != "direct" and evidence.power_w > 0:
        # An indirect balance assumes storage still supplies the observed
        # residual at the terminals. Once it cannot, the observations no
        # longer establish how the native bus behaves. The comparable prefix
        # permits neither charging nor a change in this fixed native load.
        battery = config.battery
        available_wh = (
            max(
                0.0,
                battery.energy_wh(inputs.start_soc_percent)
                - battery.energy_wh(battery.soc_min_percent),
            )
            * battery.eta_discharge
        )
        if available_wh <= evidence.power_w * (expires - now).total_seconds() / 3600:
            expires = now + timedelta(hours=available_wh / evidence.power_w)
            expiry_reason = "net_storage_limit"
            if expires <= now:
                return replace(result, psu48_supply_stop_reason=expiry_reason)

    # Switch decisions were already validated without nominal supply. Reusing
    # the SOC/voltage switch proxy here could silently change the dispatch.
    fixed = replace(
        config,
        support=replace(support, adaptive_voltage_gate=False, gate_soc_percent=None),
    )
    upper_factor = (
        config.reserve.upper_pv_factor
        if config.reserve.enabled
        else config.control.upper_pv_reserve
    )
    _, upper, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, upper_factor
    )
    appliances_wh = appliance_run_energy(inputs.slots, inputs.appliance_runs)
    flows = []
    soc = inputs.start_soc_percent
    covered_end = now
    covered_soc = None
    stop = None
    for index, (slot, accepted, dispatch) in enumerate(
        zip(inputs.slots, result.trajectory.flows, accepted_dispatch, strict=True)
    ):
        check_cancelled()
        if stop is None:
            native_wh, rail_wh = dc_loads(config, slot)
            upper_pv_wh = max(
                slot.pv_wh,
                min(
                    config.pv.peak_power_w * slot.duration,
                    max(slot.pv_wh * upper[index], slot.pv_p90_wh or 0.0),
                ),
            )
            # A short appliance run can inflate the average of a long slot.
            # Its energy stays in the AC balance, but only house base demand
            # establishes continuous AC deficit under the upper PV forecast.
            base_ac_wh = max(0.0, slot.ac_wh - appliances_wh[index])
            if (
                inputs.recharge_required_ac_wh
                and inputs.recharge_required_ac_wh[index] > ENERGY_EPSILON_WH
            ):
                stop = "additional_load"
            elif abs(native_wh - native_w * slot.duration) > ENERGY_EPSILON_WH:
                stop = "native_load_changed"
            elif (
                support.psu24_max_power_w is not None
                and rail_wh > support.psu24_max_power_w * slot.duration
            ):
                stop = "topology_changed"
            elif (
                evidence.kind == "grid_import"
                and base_ac_wh - upper_pv_wh <= required_import_w * slot.duration
            ):
                stop = "net_grid_plan_margin_not_met"
            elif upper_pv_wh > (
                ENERGY_EPSILON_WH if evidence.kind == "night" else base_ac_wh
            ):
                stop = "pv_window"
        parts = []
        for small, commanded, limit_w in dispatch:
            start = _utc(small.start)
            end = start + timedelta(hours=small.duration)
            if stop is None:
                if start != covered_end:
                    stop = "no_matching_plan_window"
                elif commanded.inverter_on or not (
                    commanded.support_dc24 and commanded.support_dc48
                ):
                    stop = "topology_changed"
                elif commanded.extra_ac_wh > ENERGY_EPSILON_WH:
                    stop = "additional_load"
                elif commanded.battery_charge_wh > ENERGY_EPSILON_WH:
                    stop = "pv_window"
            if stop is not None and covered_soc is None:
                return replace(result, psu48_supply_stop_reason=stop)
            segments: tuple[tuple[HourSlot, datetime, float], ...]
            if stop is None and start < expires < end:
                hours = (expires - start).total_seconds() / 3600
                segments = (
                    (_slice(small, start, hours), expires, delivery_w),
                    (_slice(small, expires, small.duration - hours), end, 0.0),
                )
            else:
                segments = ((small, end, delivery_w if stop is None else 0.0),)
            # Storage retained by nominal support cannot enlarge already
            # allocated AC delivery even if the inverter was ON at the floor.
            limit_w = min(limit_w, commanded.inverter_output_wh / small.duration)
            for segment, segment_end, power in segments:
                ratio = segment.duration / small.duration
                flow = step_hour(
                    fixed,
                    soc,
                    segment,
                    0.0 if commanded.inverter_on else 100.0,
                    commanded.extra_ac_wh * ratio,
                    commanded.support_dc24,
                    commanded.support_dc48,
                    feedin_wh=commanded.feedin_wh * ratio,
                    inverter_limit_w=limit_w,
                    psu48_native_delivery_wh=power * segment.duration,
                )
                parts.append(flow)
                soc = flow.soc_end_percent
                if power > 0:
                    covered_end = segment_end
                    covered_soc = soc
                    if covered_end >= expires:
                        stop = expiry_reason
        flows.append(_settle_slot(accepted, parts))
    assert covered_soc is not None
    return replace(
        result,
        operating_trajectory=Trajectory(
            tuple(flows),
            sum(flow.grid_import_wh for flow in flows),
            sum(flow.grid_export_wh for flow in flows),
            soc,
        ),
        psu48_supply_until=covered_end.timestamp(),
        psu48_supply_end_soc=covered_soc,
        psu48_supply_stop_reason=stop or "forecast_ends",
    )
