"""SOC preservation with DC-first preparation across the available forecast.

Only energy beyond the nominal DC obligation can prepare upper-PV headroom.
Necessary AC uses higher useful house demand first, and later times on ties.
Automatic support follows the same DC envelope, never an AC reference curve.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta

from .dc_service import preserves_dc_service
from .market import slot_weights
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
from .reserve_schedule import coherent_market_weights, preparation_envelope
from .simulate import step_hour
from .simulation_steps import SUPPORT_STEP_HOURS, split_slot, switching_schedule
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
    """AC timing may cost efficiency, but cannot purchase later DC support.

    An upper-PV preparation can be physically safe yet buy back DC energy
    under the nominal forecast. Compare the entire candidate against the same
    sources/loads with no AC discharge. If it buys additional DC supply, keep
    that DC-only plan until a new forecast makes AC affordable. This bounded
    fallback deliberately favours DC over speculative upper-PV headroom.
    """
    candidate = _simulate_reserve_policy(config, inputs, extra_ac, pv_scale, feedin)
    dc_import = sum(
        f.psu24_delivered_wh / config.support.psu24_eta
        + f.psu48_delivered_wh / config.support.psu48_eta
        for f in candidate.flows
    )
    unserved = sum(f.unserved_dc_wh for f in candidate.flows)
    if dc_import + unserved > ENERGY_EPSILON_WH and any(
        f.inverter_output_wh > ENERGY_EPSILON_WH for f in candidate.flows
    ):
        reference = _simulate_reserve_policy(
            config, inputs, extra_ac, pv_scale, feedin, allow_ac=False
        )
        reference_dc = sum(
            f.psu24_delivered_wh / config.support.psu24_eta
            + f.psu48_delivered_wh / config.support.psu48_eta
            for f in reference.flows
        )
        # Complete ON/OFF quanta can move one source-transfer boundary. Do
        # not discard useful AC (and export the same PV) over that rounding
        # effect. Additional sustained DC support remains disallowed.
        transfer_quantum = (
            max(slot.dc_wh / slot.duration for slot in inputs.slots)
            * SUPPORT_STEP_HOURS
            / min(config.support.psu24_eta, config.support.psu48_eta)
        )
        if (
            dc_import > reference_dc + transfer_quantum + ENERGY_EPSILON_WH
            or not preserves_dc_service(candidate, baseline=reference)
        ):
            # Keep useful AC where a small additional retained-energy margin
            # cures the later DC shortfall. At most one retry, never an
            # unbounded solver or a blanket loss of all valuable AC windows.
            retained = max(0.0, dc_import - reference_dc) + unserved
            reduced = _simulate_reserve_policy(
                config,
                inputs,
                extra_ac,
                pv_scale,
                feedin,
                ac_margin_wh=retained + ENERGY_EPSILON_WH,
            )
            reduced_dc = sum(
                f.psu24_delivered_wh / config.support.psu24_eta
                + f.psu48_delivered_wh / config.support.psu48_eta
                for f in reduced.flows
            )
            if (
                reduced_dc <= reference_dc + transfer_quantum + ENERGY_EPSILON_WH
                and preserves_dc_service(reduced, baseline=reference)
            ):
                return reduced
            assert reference.reserve_decision is not None
            return replace(
                reference,
                reserve_decision=replace(
                    reference.reserve_decision, reason="dc_priority"
                ),
            )
    return candidate


def _simulate_reserve_policy(
    config: SystemConfig,
    inputs: PlanInputs,
    extra_ac: tuple[float, ...] | None,
    pv_scale: float | Sequence[float],
    feedin: tuple[float, ...] | None,
    *,
    allow_ac: bool = True,
    ac_margin_wh: float = 0.0,
) -> Trajectory:
    """Execute source protection and the shared forecast preparation envelope."""
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
    nominal_budgets = [
        _budget(
            config,
            slot,
            extra,
            pv_scale if isinstance(pv_scale, (int, float)) else pv_scale[i],
            (feedin[i] if feedin else 0.0) * slot.duration / inputs.slots[i].duration,
        )
        for i, slot, extra, _ in steps
    ]
    # Group equal demand densities despite harmless division roundoff. PV
    # already serving the house is not a useful battery discharge opportunity.
    priorities = [
        round(
            min(config.inverter.max_power_w, max(0.0, -budget.balance) / slot.duration),
            6,
        )
        for budget, (_, slot, _, _) in zip(nominal_budgets, steps, strict=True)
    ]
    battery, support = config.battery, config.support
    protection_floor = battery.energy_wh(
        max(
            battery.soc_min_percent,
            config.control.support_dc24_activate_soc if support.dc24_available else 0,
            config.control.support_dc48_activate_soc if support.dc48_available else 0,
        )
    )
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
    raw_weights = slot_weights(tuple(step[1] for step in steps), inputs.market_prices)
    weights = coherent_market_weights(priorities, raw_weights)
    ranking_reason = (
        "load_priority_no_prices"
        if not any(
            w is not None for p, w in zip(priorities, raw_weights, strict=True) if p > 0
        )
        else "load_priority_incomplete"
        if any(
            p > 0 and w is None for p, w in zip(priorities, raw_weights, strict=True)
        )
        else "weighted"
        if any(w is not None and w > 1 for w in weights)
        else "load_priority_flat"
    )
    market_active = allow_ac and any(
        weight is not None and weight > 1 for weight in weights
    )
    # One horizon prevents a simulated midnight from introducing a PV deadline
    # after the preceding evening's better AC opportunities have been discarded.
    envelope = (
        preparation_envelope(
            budgets,
            nominal_budgets,
            priorities,
            protection_floor,
            battery.energy_wh(soc),
            weights,
        )
        if steps
        else None
    )
    decision: ReserveDecision | None = None
    future_ac_wh = 0.0
    future_demand_w = 0.0
    before_recharge = True
    for j, (i, slot, extra, _) in enumerate(steps):
        assert envelope is not None
        following_ceiling = envelope.ac_ceiling[j]
        following_dc_ceiling = envelope.dc_ceiling[j]
        following_minimum = envelope.dc_minimum[j]
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
        held: HourFlows | None = None
        if natural.battery_discharge_wh > natural.battery_charge_wh + ENERGY_EPSILON_WH:
            held = (
                protected
                if (source.dc24_available, source.dc48_available)
                == (protected.support_dc24, protected.support_dc48)
                else step_hour(
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
                - max(
                    following_ceiling + ac_margin_wh,
                    following_minimum,
                    budgets[j].inverter_floor,
                ),
            )
            if useful_ac and not (dc24 or dc48)
            else 0.0
        )
        # F-BINARY-INVERTER: budget complete ON steps at the forecast load,
        # including standby, rather than holding the inverter at a tiny limit.
        # ESS still follows demand when its maximum power is permitted.
        required_ac = min(
            config.inverter.max_power_w * slot.duration,
            max(0.0, slot.ac_wh + extra - pv)
            + config.inverter.standby_power_w * slot.duration,
        )
        available_ac = ac_budget * battery.eta_discharge * config.inverter.eta
        limit = (
            config.inverter.max_power_w
            if allow_ac
            and ac_budget > ENERGY_EPSILON_WH
            and available_ac + ENERGY_EPSILON_WH >= required_ac
            else 0.0
        )
        if not limit and (dc24, dc48) == (
            protected.support_dc24,
            protected.support_dc48,
        ):
            flow = protected
        elif (
            not limit
            and held is not None
            and (dc24, dc48) == (held.support_dc24, held.support_dc48)
        ):
            # Identical immutable source/energy probe already computed for
            # economic holding. Reuse physics, never a different SOC or limit.
            flow = held
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
            flow = (
                held
                if held is not None
                and (dc24, dc48) == (held.support_dc24, held.support_dc48)
                else step_hour(
                    effective, soc, slot, 100, extra, dc24, dc48, scale, export
                )
            )
        # Measured loads can replace an actual scheduled AC allocation, not
        # energy that will only arrive with the next solar recharge. Export
        # preparation and DC obligations remain bounded by the same envelope.
        before_recharge = (
            before_recharge
            and natural.battery_charge_wh
            <= natural.battery_discharge_wh + ENERGY_EPSILON_WH
        )
        if (
            market_active
            and j > 0
            and before_recharge
            and flow.inverter_output_wh > ENERGY_EPSILON_WH
        ):
            future_ac_wh += flow.inverter_output_wh / (
                battery.eta_discharge * config.inverter.eta
            )
            current_weight, future_weight = weights[0], weights[j]
            required_demand = (
                priorities[j] * future_weight / current_weight
                if current_weight is not None and future_weight is not None
                else priorities[j]
            )
            future_demand_w = max(future_demand_w, required_demand)
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
                preparation_horizon_end=_slot_end(steps[-1][1]),
                inverter_limit_w=limit,
                headroom_wh=max(
                    0.0,
                    battery.energy_wh(natural.soc_end_percent)
                    - max(
                        following_ceiling + ac_margin_wh,
                        following_minimum,
                        budgets[j].inverter_floor,
                    ),
                ),
                unavoidable_export_wh=envelope.unavoidable_export_wh,
                market_ranking_reason=ranking_reason,
                reason=reason,
                live_ac_floor_percent=battery.soc_percent(
                    max(
                        (following_ceiling if market_active else following_dc_ceiling)
                        + ac_margin_wh,
                        following_minimum,
                        budgets[j].inverter_floor,
                    )
                    + nominal_budgets[j].dc
                ),
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
    if decision is not None and future_ac_wh > ENERGY_EPSILON_WH:
        assert envelope is not None
        decision = replace(
            decision,
            live_ac_override_demand_w=future_demand_w,
            live_ac_override_floor_percent=battery.soc_percent(
                max(
                    battery.energy_wh(inputs.start_soc_percent) - future_ac_wh,
                    max(
                        envelope.dc_ceiling[0],
                        envelope.dc_minimum[0],
                        budgets[0].inverter_floor,
                    )
                    + nominal_budgets[0].dc,
                )
            ),
        )
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
                dc_deficit_intervals=tuple(
                    interval
                    for part in parts
                    for interval in part.dc_deficit_intervals or ()
                ),
                inverter_on=all(part.inverter_on for part in parts),
                support_dc24=any(part.support_dc24 for part in parts),
                support_dc48=any(part.support_dc48 for part in parts),
                gate_open=any(part.gate_open for part in parts),
            )
        )
    if not allow_ac and decision is not None:
        decision = replace(decision, headroom_wh=0.0, live_ac_floor_percent=100.0)
    if decision is None:
        decision = ReserveDecision(inputs.now, 0.0, 0.0, 0.0, "no_preparation_needed")
    return Trajectory(
        tuple(flows),
        sum(flow.grid_import_wh for flow in flows),
        sum(flow.grid_export_wh for flow in flows),
        soc,
        reserve_decision=decision,
    )
