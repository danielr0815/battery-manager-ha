"""Year-round preservation with DC-first, latest feasible PV preparation.

Two backward envelopes are essential: using only the all-load envelope would
buy DC grid energy tonight and empty the battery through AC tomorrow morning.
The DC envelope spends the required headroom on native consumption first; the
all-load envelope identifies the last opportunity for additional AC discharge.
Neither envelope assumes that an enabled PSU actually supplies its nameplate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, replace
from datetime import timedelta

from .model import HourFlows, HourSlot, PlanInputs, SystemConfig, Trajectory
from .simulate import step_hour
from .support import SUPPORT_STEP_HOURS, support_state


def _steps(config: SystemConfig, inputs: PlanInputs, extra: tuple[float, ...] | None):
    # Reuse the existing collapsed-band rule rather than calling a cold-start
    # P90 a reliable upper bound. The scalar remains explicitly uncalibrated.
    from .uncertainty import effective_uncertainty

    _, upper, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.reserve.upper_pv_factor
    )
    steps: list[tuple[int, HourSlot, float, float]] = []
    for i, slot in enumerate(inputs.slots):
        elapsed = 0.0
        while elapsed < slot.duration - 1e-9:
            duration = min(SUPPORT_STEP_HOURS, slot.duration - elapsed)
            ratio = duration / slot.duration
            small = replace(
                slot,
                start=slot.start + timedelta(hours=elapsed),
                duration=duration,
                pv_wh=slot.pv_wh * ratio,
                ac_wh=slot.ac_wh * ratio,
                dc_wh=slot.dc_wh * ratio,
            )
            steps.append((i, small, (extra[i] if extra else 0.0) * ratio, upper[i]))
            elapsed += duration
    return steps


def _envelopes(config: SystemConfig, steps):
    battery, support = config.battery, config.support
    maximum = battery.energy_wh(battery.soc_max_percent)
    dc = [maximum] * (len(steps) + 1)
    all_loads = dc.copy()
    for j in range(len(steps) - 1, -1, -1):
        _, slot, extra, upper = steps[j]
        rail = max(0.0, slot.dc_wh - support.native48_base_w * slot.duration)
        rail *= support.dc24_share
        served_rail = (
            min(rail, support.dcdc_max_power_w * slot.duration)
            if support.dcdc_max_power_w is not None
            else rail
        )
        bus = slot.dc_wh - rail + served_rail / support.dcdc_eta
        pv = min(slot.pv_wh * upper, config.pv.peak_power_w * slot.duration)
        balance = pv - slot.ac_wh - extra
        charge = (
            max(
                0.0,
                min(max(0.0, balance), config.charger.max_power_w * slot.duration)
                - config.charger.standby_power_w * slot.duration,
            )
            * config.charger.eta
            * battery.eta_charge
        )
        discharge = bus / battery.eta_discharge
        ac = min(max(0.0, -balance), config.inverter.max_power_w * slot.duration) / (
            config.inverter.eta * battery.eta_discharge
        )
        # Do not clamp at the protection floor: a negative envelope is useful
        # evidence of impossible absorption, not permission to cross that floor.
        dc[j] = min(maximum, dc[j + 1] - charge + discharge)
        all_loads[j] = min(maximum, all_loads[j + 1] - charge + discharge + ac)
    return dc, all_loads


def simulate_reserve(
    config: SystemConfig,
    inputs: PlanInputs,
    extra_ac: tuple[float, ...] | None,
    pv_scale: float | Sequence[float],
    feedin: tuple[float, ...] | None,
) -> Trajectory:
    """Apply the same physical reserve policy in every allocation/stress probe."""
    steps = _steps(config, inputs, extra_ac)
    dc_envelope, ac_envelope = _envelopes(config, steps)
    battery, support = config.battery, config.support
    soc = inputs.start_soc_percent
    dc24, dc48 = support.dc24_active, support.dc48_active
    buckets: list[list[HourFlows]] = [[] for _ in inputs.slots]
    for j, (i, slot, extra, _) in enumerate(steps):
        scale = pv_scale if isinstance(pv_scale, (int, float)) else pv_scale[i]
        # A measured voltage is useful at the observed SOC, not a voltage
        # forecast for an arbitrarily different future SOC. In this local band
        # it supersedes the old fixed 40% proxy; outside it no power is credited.
        source = replace(support, gate_soc_percent=None)
        if abs(soc - inputs.start_soc_percent) > config.control.hysteresis_percent:
            source = replace(source, psu48_bus_voltage_v=None)
        if scale < 1:
            source = replace(source, psu48_bus_voltage_v=None)
        rail = max(0.0, slot.dc_wh - source.native48_base_w * slot.duration)
        rail *= source.dc24_share
        if source.psu24_max_power_w is not None and rail > (
            source.psu24_max_power_w * slot.duration
        ):
            source = replace(source, dc24_available=False)
        effective = replace(config, support=source)
        export = (
            (feedin[i] if feedin else 0.0) * slot.duration / inputs.slots[i].duration
        )
        natural = step_hour(
            effective, soc, slot, 100, extra, pv_scale=scale, feedin_wh=export
        )
        energy = battery.energy_wh(soc)
        after_dc = battery.energy_wh(natural.soc_end_percent)
        # A planned deficit belongs to DC first, even when AC discharge could
        # be postponed further. This avoids paying for DC to discharge AC later.
        need_dc = energy + natural.battery_charge_wh > dc_envelope[j + 1] + 1e-6
        ac_budget = max(
            0.0,
            after_dc
            - max(
                ac_envelope[j + 1],
                battery.energy_wh(config.control.inverter_min_soc_percent),
            ),
        )
        pv_recovery = natural.battery_charge_wh >= natural.battery_discharge_wh and (
            natural.battery_charge_wh > 0
        )
        hold = not need_dc and not pv_recovery and ac_budget <= 1e-6
        emergency24, emergency48 = support_state(
            effective, min(soc, natural.soc_end_percent), dc24, dc48, pv_recovery
        )
        dc24 = source.dc24_available and (hold or emergency24 or source.dc24_forced_on)
        dc48 = source.dc48_available and (hold or emergency48 or source.dc48_forced_on)
        # Preserve existing low-SOC protection, but do not let its latch turn
        # reserve holding into grid charging after solar recovery/preparation.
        if need_dc or pv_recovery:
            dc24 = source.dc24_available and (
                source.dc24_forced_on or soc <= config.control.support_dc24_activate_soc
            )
            dc48 = source.dc48_available and (
                source.dc48_forced_on or soc <= config.control.support_dc48_activate_soc
            )
        intended = inputs.reserve_hold_soc_percent
        if intended is not None and soc > intended + config.control.hysteresis_percent:
            dc48 = source.dc48_available and source.dc48_forced_on
        limit = min(
            config.inverter.max_power_w,
            ac_budget * battery.eta_discharge * config.inverter.eta / slot.duration,
        )
        if dc24 or dc48:
            limit = 0.0
        effective = replace(
            effective, inverter=replace(config.inverter, max_power_w=limit)
        )
        flow = step_hour(
            effective,
            soc,
            slot,
            config.control.inverter_min_soc_percent if limit > 1e-6 else 100,
            extra,
            dc24,
            dc48,
            scale,
            export,
        )
        flow = replace(
            flow,
            reserve_preparation_start=slot.start
            if (need_dc or limit > 1e-6) and flow.battery_discharge_wh > 1e-6
            else None,
            inverter_start=flow.inverter_on,
            inverter_limit_w=limit,
            reserve_ceiling_percent=battery.soc_percent(max(0.0, ac_envelope[j + 1])),
            reserve_dc_ceiling_percent=battery.soc_percent(
                max(0.0, dc_envelope[j + 1])
            ),
            support_dc24_start=dc24,
            support_dc48_start=dc48,
            support_mode="dc48"
            if dc48
            else "dc24"
            if dc24
            else "battery"
            if flow.inverter_on
            else "reserve",
        )
        buckets[i].append(flow)
        soc = flow.soc_end_percent
    flows: list[HourFlows] = []
    energy_fields = [f.name for f in fields(HourFlows) if f.name.endswith("_wh")]
    for parts in buckets:
        first = parts[0]
        flows.append(
            replace(
                first,
                **{
                    name: sum(getattr(p, name) for p in parts) for name in energy_fields
                },
                reserve_preparation_start=next(
                    (
                        p.reserve_preparation_start
                        for p in parts
                        if p.reserve_preparation_start is not None
                    ),
                    None,
                ),
                soc_end_percent=parts[-1].soc_end_percent,
                inverter_on=all(p.inverter_on for p in parts),
                support_dc24=any(p.support_dc24 for p in parts),
                support_dc48=any(p.support_dc48 for p in parts),
                gate_open=any(p.gate_open for p in parts),
            )
        )
    return Trajectory(
        tuple(flows),
        sum(f.grid_import_wh for f in flows),
        sum(f.grid_export_wh for f in flows),
        soc,
    )
