"""Coordinated DC reserve policy (F-COORDINATED-DC-SUPPORT R1–R6).

The five-minute grid makes the same reserve decision before each small energy
transfer, rather than feeding a PSU for an entire hour before its threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, replace

from .model import HourFlows, PlanInputs, SystemConfig, Trajectory
from .simulate import step_hour
from .simulation_steps import SUPPORT_STEP_HOURS as SUPPORT_STEP_HOURS
from .simulation_steps import split_slot, switching_schedule


def support_state(
    config: SystemConfig, soc: float, dc24: bool, dc48: bool, recovering: bool
) -> tuple[bool, bool]:
    """Select support with hysteresis; PSU recovery cannot re-enable AC drain."""
    support, control = config.support, config.control
    if soc >= control.support_dc48_recovery_soc:
        dc48 = False
    if recovering and soc >= control.support_dc24_recovery_soc:
        dc24 = False
    dc48 = support.dc48_available and (
        dc48 or support.dc48_forced_on or soc <= control.support_dc48_activate_soc
    )
    dc24 = support.dc24_available and (
        dc24
        or dc48
        or support.dc24_forced_on
        or soc <= control.support_dc24_activate_soc
    )
    return dc24, dc48


def simulate_support(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    extra_ac: tuple[float, ...] | None,
    pv_scale: float | Sequence[float],
    feedin: tuple[float, ...] | None,
) -> Trajectory:
    """Run all candidate plans with the same coordinated support policy."""
    soc = inputs.start_soc_percent
    dc24, dc48 = config.support.dc24_active, config.support.dc48_active
    flows: list[HourFlows] = []
    energy_fields = [f.name for f in fields(HourFlows) if f.name.endswith("_wh")]
    for i, slot in enumerate(inputs.slots):
        scale = pv_scale if isinstance(pv_scale, (float, int)) else pv_scale[i]
        extra = extra_ac[i] if extra_ac else 0.0
        export = feedin[i] if feedin else 0.0
        # A stress run never treats an unmeasured PSU current as guaranteed.
        support = config.support
        if scale < 1:
            support = replace(support, psu48_bus_voltage_v=None)
        rail_wh = (
            max(0.0, slot.dc_wh - support.native48_base_w * slot.duration)
            * support.dc24_share
        )
        if (
            support.psu24_max_power_w is not None
            and rail_wh > support.psu24_max_power_w * slot.duration
        ):
            # Never switch away from a working DC/DC to an undersized PSU.
            support = replace(support, dc24_available=False)
        effective = (
            config if support is config.support else replace(config, support=support)
        )
        parts: list[HourFlows] = []
        for small, ratio in split_slot(slot):
            # Only PV sufficient for the full DC demand can release support.
            # The PSU's own charging must not create an AC cycling loop.
            recovering = slot.pv_wh * scale > (
                slot.ac_wh
                + extra
                + slot.dc_wh / support.dcdc_eta / config.charger.eta
                + config.charger.standby_power_w * slot.duration
            )
            dc24, dc48 = support_state(effective, soc, dc24, dc48, recovering)
            inv_threshold = max(threshold, config.control.inverter_min_soc_percent)
            flow = step_hour(
                effective,
                soc,
                small,
                inv_threshold,
                extra * ratio,
                dc24,
                dc48,
                scale,
                export * ratio,
            )
            # Reserve the next five-minute deficit before crossing a support
            # activation boundary, including response time in the grid.
            next24, next48 = support_state(
                effective, min(soc, flow.soc_end_percent), dc24, dc48, False
            )
            if (next24, next48) != (dc24, dc48):
                dc24, dc48 = next24, next48
                flow = step_hour(
                    effective,
                    soc,
                    small,
                    inv_threshold,
                    extra * ratio,
                    dc24,
                    dc48,
                    scale,
                    export * ratio,
                )
            parts.append(flow)
            soc = flow.soc_end_percent
        first = parts[0]
        flows.append(
            replace(
                first,
                **{
                    name: sum(getattr(p, name) for p in parts) for name in energy_fields
                },
                soc_end_percent=soc,
                inverter_on=all(p.inverter_on for p in parts),
                inverter_start=first.inverter_on,
                switching_schedule=switching_schedule(
                    (small for small, _ in split_slot(slot)), parts
                ),
                support_dc24=any(p.support_dc24 for p in parts),
                support_dc48=any(p.support_dc48 for p in parts),
                support_dc24_start=first.support_dc24,
                support_dc48_start=first.support_dc48,
                gate_open=any(p.gate_open for p in parts),
                support_mode=(
                    "dc48"
                    if first.support_dc48
                    else "dc24"
                    if first.support_dc24
                    else "battery"
                    if first.inverter_on
                    else "reserve"
                ),
            )
        )
    return Trajectory(
        tuple(flows),
        sum(f.grid_import_wh for f in flows),
        sum(f.grid_export_wh for f in flows),
        soc,
    )
