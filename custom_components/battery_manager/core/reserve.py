"""SOC preservation with reachable, latest preparation for today and tomorrow.

A maximum-useful-discharge reference measures unavoidable export. Its physical
inverse schedules only the remaining necessary AC, without carrying impossible
zero-export debt into earlier hours. Automatic support is selected independently
on the actual trajectory; reference switches never become future PSU commands.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime, timedelta

from .model import (
    HourFlows,
    HourSlot,
    PlanInputs,
    ReserveDecision,
    ReserveDecisionReason,
    SystemConfig,
    Trajectory,
)
from .reserve_energy import ENERGY_EPSILON_WH, BatteryStep, dc_loads
from .simulate import step_hour
from .simulation_steps import split_slot, switching_schedule
from .support import support_state

# A planning call probes many nearby load schedules. The cache expires with that
# call and cannot leak a source, forecast or cancellation context into the next.
MAX_CACHED_RESERVE_PROBES = 128
MAX_CACHED_BATTERY_STEPS = 8192


@dataclass
class _ReserveCache:
    references: dict[int, object] = field(default_factory=dict)
    budgets: dict[
        tuple[int, int, float, float, float], tuple[HourSlot, BatteryStep]
    ] = field(default_factory=dict)
    variants: dict[tuple[int, bool, bool], SystemConfig] = field(default_factory=dict)
    trajectories: dict[tuple[object, ...], Trajectory] = field(default_factory=dict)


_reserve_cache: ContextVar[_ReserveCache | None] = ContextVar(
    "reserve_cache", default=None
)


@contextmanager
def reserve_planning_scope() -> Iterator[None]:
    """Nested cascade planning shares the owner cache; errors always release it."""
    if _reserve_cache.get() is not None:
        yield
        return
    token = _reserve_cache.set(_ReserveCache())
    try:
        yield
    finally:
        _reserve_cache.reset(token)


def _budget(
    config: SystemConfig, slot: HourSlot, extra: float, upper: float, feedin: float
) -> BatteryStep:
    cache = _reserve_cache.get()
    if cache is None:
        return BatteryStep.build(config, slot, extra, upper, feedin)
    # Retain identity-keyed immutable objects until the owning plan ends.
    cache.references[id(config)] = config
    key = id(config), id(slot), extra, upper, feedin
    entry = cache.budgets.get(key)
    if entry is None:
        result = BatteryStep.build(config, slot, extra, upper, feedin)
        if len(cache.budgets) >= MAX_CACHED_BATTERY_STEPS:
            del cache.budgets[next(iter(cache.budgets))]
        cache.budgets[key] = (slot, result)
        return result
    return entry[1]


def _steps(config: SystemConfig, inputs: PlanInputs, extra: tuple[float, ...] | None):
    from .uncertainty import effective_uncertainty

    _, upper, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.reserve.upper_pv_factor
    )
    return [
        (i, small, (extra[i] if extra else 0.0) * ratio, upper[i])
        for i, small, ratio in _expanded_slots(inputs.slots)
    ]


def _expanded_slots(
    slots: tuple[HourSlot, ...],
) -> tuple[tuple[int, HourSlot, float], ...]:
    return tuple(
        (i, small, ratio)
        for i, slot in enumerate(slots)
        for small, ratio in split_slot(slot)
    )


def _slot_end(slot: HourSlot) -> datetime:
    """Add real elapsed time, including a repeated or skipped DST hour."""
    if slot.start.tzinfo is None:
        return slot.start + timedelta(hours=slot.duration)
    return (slot.start.astimezone(UTC) + timedelta(hours=slot.duration)).astimezone(
        slot.start.tzinfo
    )


def _envelope(
    budgets: list[BatteryStep], start: int, end: int, energy: float
) -> tuple[list[float], list[float], float]:
    """Two linear passes; each step enters at most two rolling daily windows.

    Reference energy/export concern the battery branch, not an assumed future
    protection latch. Physical support may supersede this budget in the actual
    forward run; unknown PSU output is never credited as guaranteed energy.
    """
    reachable = [energy]
    spill: list[float] = []
    for budget in budgets[start:end]:
        energy, exported = budget.project(energy)
        reachable.append(energy)
        spill.append(exported)
    ceiling = [budgets[start].maximum] * (end - start + 1)
    dc_ceiling = ceiling.copy()
    for offset in range(end - start - 1, -1, -1):
        budget = budgets[start + offset]
        ceiling[offset] = max(
            reachable[offset],
            budget.incoming_ceiling(ceiling[offset + 1], spill[offset]),
        )
        # DC has priority over discretionary AC use: do not buy DC support
        # now merely because the same energy could be dumped through AC later.
        dc_ceiling[offset] = max(
            reachable[offset],
            replace(budget, ac=0.0).incoming_ceiling(
                dc_ceiling[offset + 1], spill[offset]
            ),
        )
    return ceiling, dc_ceiling, sum(spill)


def simulate_reserve(
    config: SystemConfig,
    inputs: PlanInputs,
    extra_ac: tuple[float, ...] | None,
    pv_scale: float | Sequence[float],
    feedin: tuple[float, ...] | None,
) -> Trajectory:
    """Reuse only identical immutable probes within the current planner call."""
    cache = _reserve_cache.get()
    if cache is None:
        return _simulate_reserve(config, inputs, extra_ac, pv_scale, feedin)
    cache.references[id(config)] = config
    cache.references[id(inputs)] = inputs
    key = (
        id(config),
        id(inputs),
        extra_ac,
        pv_scale if isinstance(pv_scale, (int, float)) else tuple(pv_scale),
        feedin,
    )
    result = cache.trajectories.get(key)
    if result is None:
        result = _simulate_reserve(config, inputs, extra_ac, pv_scale, feedin)
        if len(cache.trajectories) >= MAX_CACHED_RESERVE_PROBES:
            del cache.trajectories[next(iter(cache.trajectories))]
        cache.trajectories[key] = result
    return result


def _simulate_reserve(
    config: SystemConfig,
    inputs: PlanInputs,
    extra_ac: tuple[float, ...] | None,
    pv_scale: float | Sequence[float],
    feedin: tuple[float, ...] | None,
) -> Trajectory:
    """Execute real source protection and bounded, rolling reserve preparation."""
    steps = _steps(config, inputs, extra_ac)
    budgets = [
        _budget(
            config,
            slot,
            extra,
            upper,
            (feedin[i] if feedin else 0.0) * slot.duration / inputs.slots[i].duration,
        )
        for i, slot, extra, upper in steps
    ]
    battery, support = config.battery, config.support
    soc = inputs.start_soc_percent
    # Economic holding must not become a protection latch on the next plan.
    # Only an active source below its recovery threshold retains that latch.
    protect24 = support.dc24_active and soc < config.control.support_dc24_recovery_soc
    protect48 = support.dc48_active and soc < config.control.support_dc48_recovery_soc
    buckets: list[list[HourFlows]] = [[] for _ in inputs.slots]
    cache = _reserve_cache.get()
    variants: dict[tuple[int, bool, bool], SystemConfig] = (
        cache.variants if cache is not None else {}
    )
    current_day: date | None = None
    window_start = 0
    ceiling: list[float] = []
    decision: ReserveDecision | None = None
    for j, (i, slot, extra, _) in enumerate(steps):
        energy = battery.energy_wh(soc)
        if slot.start.date() != current_day:
            current_day = slot.start.date()
            exclusive_day = current_day + timedelta(days=2)
            window_start = j
            end = j
            while end < len(steps) and steps[end][1].start.date() < exclusive_day:
                end += 1
            ceiling, dc_ceiling, unavoidable_export = _envelope(budgets, j, end, energy)
            horizon_end = _slot_end(steps[end - 1][1])
        following_ceiling = ceiling[j - window_start + 1]
        following_dc_ceiling = dc_ceiling[j - window_start + 1]
        scale = pv_scale if isinstance(pv_scale, (int, float)) else pv_scale[i]
        voltage_credit = (
            abs(soc - inputs.start_soc_percent) <= config.control.hysteresis_percent
            and scale >= 1
        )
        _, rail = dc_loads(config, slot)
        rail_available = support.dc24_available and not (
            support.psu24_max_power_w is not None
            and rail > support.psu24_max_power_w * slot.duration
        )
        key = id(config), voltage_credit, rail_available
        if key not in variants:
            variants[key] = replace(
                config,
                support=replace(
                    support,
                    gate_soc_percent=None,
                    psu48_bus_voltage_v=support.psu48_bus_voltage_v
                    if voltage_credit
                    else None,
                    dc24_available=rail_available,
                ),
            )
        effective = variants[key]
        source = effective.support
        export = (
            (feedin[i] if feedin else 0.0) * slot.duration / inputs.slots[i].duration
        )
        natural = step_hour(
            effective, soc, slot, 100, extra, pv_scale=scale, feedin_wh=export
        )
        pv_recovery = natural.battery_charge_wh >= natural.battery_discharge_wh and (
            natural.battery_charge_wh > 0
        )
        protect24, protect48 = support_state(
            effective, soc, protect24, protect48, pv_recovery
        )
        dc24, dc48 = protect24, protect48
        protected = (
            step_hour(effective, soc, slot, 100, extra, dc24, dc48, scale, export)
            if dc24 or dc48
            else natural
        )
        protect24, protect48 = support_state(
            effective,
            min(soc, protected.soc_end_percent),
            protect24,
            protect48,
            pv_recovery,
        )
        dc24, dc48 = protect24, protect48
        # Operator 2026-09-28: preserve DC energy too when it is not needed
        # for forecast headroom. Protection thresholds are a last resort, not
        # an economic discharge target. Never credit unknown PSU output.
        holding = False
        if natural.battery_discharge_wh > natural.battery_charge_wh + ENERGY_EPSILON_WH:
            held = step_hour(
                effective,
                soc,
                slot,
                100,
                extra,
                source.dc24_available,
                source.dc48_available,
                scale,
                export,
            )
            if (
                battery.energy_wh(held.soc_end_percent)
                <= following_dc_ceiling + ENERGY_EPSILON_WH
                # At the battery ceiling, capped end SOC can hide extra spill.
                # Never buy rail energy that displaces usable PV in this step.
                and held.grid_export_wh <= natural.grid_export_wh + ENERGY_EPSILON_WH
            ):
                holding = True
                dc24 = dc24 or source.dc24_available
                dc48 = dc48 or source.dc48_available
        pv = slot.pv_wh * scale
        if scale > 1:
            pv = min(pv, config.pv.peak_power_w * slot.duration)
        useful_ac = slot.ac_wh + extra > pv + ENERGY_EPSILON_WH
        ac_budget = (
            max(
                0.0,
                battery.energy_wh(natural.soc_end_percent)
                - max(following_ceiling, budgets[j].inverter_floor),
            )
            if useful_ac and not (dc24 or dc48)
            else 0.0
        )
        limit = min(
            config.inverter.max_power_w,
            ac_budget * battery.eta_discharge * config.inverter.eta / slot.duration,
        )
        if limit <= ENERGY_EPSILON_WH:
            limit = 0.0
        if not limit and (dc24, dc48) == (
            protected.support_dc24,
            protected.support_dc48,
        ):
            flow = protected
        else:
            flow = step_hour(
                effective,
                soc,
                slot,
                config.control.inverter_min_soc_percent if limit else 100,
                extra,
                dc24,
                dc48,
                scale,
                export,
                inverter_limit_w=limit,
            )
        # Unusual configured thresholds must receive the same look-ahead
        # protection as the coordinated legacy simulator, even after AC use.
        protect24, protect48 = support_state(
            effective, min(soc, flow.soc_end_percent), protect24, protect48, pv_recovery
        )
        next24, next48 = dc24 or protect24, dc48 or protect48
        if (next24, next48) != (dc24, dc48):
            dc24, dc48 = next24, next48
            limit = 0.0
            flow = step_hour(
                effective, soc, slot, 100, extra, dc24, dc48, scale, export
            )
        if decision is None:
            reason: ReserveDecisionReason = "no_preparation_needed"
            if (dc24 and source.dc24_forced_on) or (dc48 and source.dc48_forced_on):
                reason = "manual_support"
            elif protect24 or protect48:
                reason = "dc_support_protection"
            elif holding and (dc24 or dc48):
                reason = "dc_reserve_holding"
            elif limit:
                reason = "pv_headroom_preparation"
            elif not useful_ac:
                reason = "no_ac_demand"
            decision = ReserveDecision(
                preparation_horizon_end=horizon_end,
                inverter_limit_w=limit,
                headroom_wh=max(
                    0.0,
                    battery.energy_wh(natural.soc_end_percent)
                    - max(following_ceiling, budgets[j].inverter_floor),
                ),
                unavoidable_export_wh=unavoidable_export,
                reason=reason,
            )
        flow = replace(
            flow,
            reserve_preparation_start=slot.start
            if flow.inverter_output_wh > ENERGY_EPSILON_WH
            else None,
            inverter_start=flow.inverter_on,
            inverter_limit_w=limit,
            reserve_ceiling_percent=battery.soc_percent(following_ceiling),
            # Both paths use the physical preparation envelope, never an old
            # historical hold SOC or a new fixed night reserve.
            reserve_dc_ceiling_percent=battery.soc_percent(following_dc_ceiling),
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
    energy_fields = [
        field.name for field in fields(HourFlows) if field.name.endswith("_wh")
    ]
    for slot, parts in zip(inputs.slots, buckets, strict=True):
        first = parts[0]
        flows.append(
            replace(
                first,
                **{
                    name: sum(getattr(part, name) for part in parts)
                    for name in energy_fields
                },
                reserve_preparation_start=next(
                    (
                        part.reserve_preparation_start
                        for part in parts
                        if part.reserve_preparation_start is not None
                    ),
                    None,
                ),
                soc_end_percent=parts[-1].soc_end_percent,
                switching_schedule=switching_schedule(
                    (small for small, _ in split_slot(slot)), parts
                ),
                inverter_on=all(part.inverter_on for part in parts),
                support_dc24=any(part.support_dc24 for part in parts),
                support_dc48=any(part.support_dc48 for part in parts),
                gate_open=any(part.gate_open for part in parts),
            )
        )
    if decision is None:
        decision = ReserveDecision(inputs.now, 0.0, 0.0, 0.0, "no_preparation_needed")
    return Trajectory(
        tuple(flows),
        sum(flow.grid_import_wh for flow in flows),
        sum(flow.grid_export_wh for flow in flows),
        soc,
        reserve_decision=decision,
    )
