"""Pure cascade planning layered on the legacy surplus allocator.

The legacy planner remains the source of truth for Root-boundary allocation and
global load priority.  This module adds isolated auxiliary discharge only when
a second legacy allocation, started from the post-discharge member SOCs, proves
that every member can recover under its normal priority before the local solar
day ends.  That structure deliberately avoids a second priority universe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from .model import (
    CascadeMember,
    CascadeMemberFlow,
    CascadePlan,
    CascadeRuntimeState,
    CascadeSlotFlow,
    CascadeSourceSegment,
    LoadCascade,
    LoadPlan,
    PlanInputs,
    PlanResult,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)

_EPS = 1e-6
_CACHE_MAX_AGE = timedelta(days=7)
_SOURCE_PROOF_H = 1.0 / 60.0


def _usable_soc(
    state: SurplusLoadState | None, now: datetime
) -> tuple[float | None, bool]:
    """Return a live/provisional SOC and whether the value is stale."""
    if state is None or state.soc_percent is None or state.soc_source == "missing":
        return None, False
    if state.soc_source == "cache":
        observed = state.soc_observed_at
        if observed is None or now - observed > _CACHE_MAX_AGE:
            return None, False
        return state.soc_percent, True
    return state.soc_percent, False


def _recovery_deadline(inputs: PlanInputs) -> datetime | None:
    """End of today's last slot with positive PV, in planner-local time."""
    day = inputs.now.date()
    candidates = [
        slot.start + timedelta(hours=slot.duration)
        for slot in inputs.slots
        if slot.start.date() == day and slot.pv_wh > _EPS
    ]
    return max(candidates, default=None)


def _hours_to_deadline(inputs: PlanInputs, deadline: datetime | None) -> set[int]:
    if deadline is None:
        return set()
    return {
        index
        for index, slot in enumerate(inputs.slots)
        if slot.start < deadline and slot.start.date() == inputs.now.date()
    }


def _runtime_for(inputs: PlanInputs, cascade_id: str) -> CascadeRuntimeState | None:
    return next(
        (
            state
            for state in inputs.cascade_runtime_states
            if state.cascade_id == cascade_id
        ),
        None,
    )


def _plan_by_id(result: PlanResult) -> dict[str, LoadPlan]:
    return {plan.load_id: plan for plan in result.load_plans}


def _charge_energy_before(
    plan: LoadPlan,
    load: SurplusLoad,
    member: CascadeMember,
    state: SurplusLoadState,
    inputs: PlanInputs,
    covered: set[int],
) -> float:
    power = state.planning_power_w(load)
    if member.max_charge_power_w is not None:
        power = min(power, member.max_charge_power_w)
    hours = sum(
        (
            plan.run_hours[index]
            if plan.run_hours and index < len(plan.run_hours)
            else inputs.slots[index].duration
        )
        for index in covered
        if index < len(plan.schedule) and plan.schedule[index]
    )
    return power * hours * member.eta_charge


def _verify_recovery(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    result: PlanResult,
    end_soc: dict[str, float],
    deadline: datetime | None,
) -> bool:
    """All members recover by the deadline at their ordinary load priority."""
    covered = _hours_to_deadline(inputs, deadline)
    if not covered:
        return False
    loads = {load.load_id: load for load in config.loads}
    states = {state.load_id: state for state in inputs.load_states}
    plans = _plan_by_id(result)
    for member in cascade.members:
        load = loads[member.load_id]
        state = states.get(member.load_id)
        plan = plans.get(member.load_id)
        if state is None or plan is None:
            return False
        if (
            not state.available
            and end_soc[member.load_id] < member.recovery_soc_percent
        ):
            return False
        stored = load.capacity_wh * end_soc[member.load_id] / 100.0
        stored += _charge_energy_before(plan, load, member, state, inputs, covered)
        recovery = load.capacity_wh * member.recovery_soc_percent / 100.0
        if stored + _EPS < recovery:
            return False
    return True


def _stress_inputs(config: SystemConfig, inputs: PlanInputs) -> PlanInputs:
    alpha = config.control.predrain_pv_confidence
    slots = []
    for slot in inputs.slots:
        stressed_pv = (
            slot.pv_p10_wh
            if slot.pv_p10_wh is not None and slot.pv_p10_wh <= slot.pv_wh
            else slot.pv_wh * alpha
        )
        slots.append(replace(slot, pv_wh=max(0.0, stressed_pv)))
    return replace(inputs, slots=tuple(slots))


def _allocate_aux_now(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    result: PlanResult,
) -> tuple[tuple[CascadeSourceSegment, ...], dict[str, float], bool] | None:
    """Allocate one full terminal dwell from storage sources, root to leaf."""
    if not inputs.slots:
        return None
    runtime = _runtime_for(inputs, cascade.cascade_id)
    today = inputs.now.date()
    if (
        runtime is not None
        and runtime.episode_day == today
        and runtime.phase
        in (
            "recovering",
            "complete",
        )
    ):
        return None
    plans = _plan_by_id(result)
    terminal_plan = plans[cascade.terminal_load_id]
    # Root/PV and house-battery service always outrank isolated storage.
    if terminal_plan.active_now:
        return None

    loads = {load.load_id: load for load in config.loads}
    states = {state.load_id: state for state in inputs.load_states}
    terminal = loads[cascade.terminal_load_id]
    required_h = max(_SOURCE_PROOF_H, terminal.min_runtime_min / 60.0)
    remaining_h = required_h
    offset_h = 0.0
    segments: list[CascadeSourceSegment] = []
    end_soc: dict[str, float] = {}
    provisional = False

    for member_index, member in enumerate(cascade.members):
        load = loads[member.load_id]
        state = states.get(member.load_id)
        soc, stale = _usable_soc(state, inputs.now)
        if soc is None:
            return None  # every member is part of the recovery guarantee
        provisional = provisional or stale
        end_soc[member.load_id] = soc
        if state is None or not state.available or remaining_h <= _EPS:
            continue

        downstream_overhead = sum(
            item.output_overhead_w for item in cascade.members[member_index:]
        )
        source_power = terminal.nominal_power_w + downstream_overhead
        if (
            member.max_output_power_w is not None
            and source_power > member.max_output_power_w
        ):
            continue
        passthrough_ok = all(
            item.max_passthrough_power_w is None
            or source_power <= item.max_passthrough_power_w
            for item in cascade.members[member_index + 1 :]
        )
        if not passthrough_ok:
            continue

        usable_store = max(
            0.0,
            (soc - member.discharge_floor_soc_percent) / 100.0 * load.capacity_wh,
        )
        available_h = usable_store * member.eta_discharge / source_power
        if available_h + _EPS < _SOURCE_PROOF_H:
            continue
        take_h = min(remaining_h, available_h)
        # Never spend a switching cycle on a source that cannot carry its
        # mandatory proof window.
        if take_h + _EPS < _SOURCE_PROOF_H:
            continue
        terminal_wh = terminal.nominal_power_w * take_h
        segments.append(
            CascadeSourceSegment(
                slot_index=0,
                start_offset_h=offset_h,
                run_hours=take_h,
                source="aux",
                source_load_id=member.load_id,
                root_input_on=False,
                terminal_energy_wh=terminal_wh,
            )
        )
        drawn = source_power * take_h / member.eta_discharge
        end_soc[member.load_id] = max(
            member.discharge_floor_soc_percent,
            soc - 100.0 * drawn / load.capacity_wh,
        )
        remaining_h -= take_h
        offset_h += take_h

    if remaining_h > _EPS:
        return None
    return tuple(segments), end_soc, provisional


def _replace_soc_states(inputs: PlanInputs, end_soc: dict[str, float]) -> PlanInputs:
    states = {state.load_id: state for state in inputs.load_states}
    for load_id, soc in end_soc.items():
        previous = states.get(load_id, SurplusLoadState(load_id=load_id))
        states[load_id] = replace(
            previous,
            soc_percent=soc,
            soc_source="live",
            soc_observed_at=inputs.now,
        )
    # Preserve the original ordering and append any synthetic state only once.
    ordered = [states.pop(state.load_id) for state in inputs.load_states]
    ordered.extend(states.values())
    return replace(inputs, load_states=tuple(ordered))


def _root_slot_energy(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    plans: dict[str, LoadPlan],
    index: int,
) -> tuple[float, float]:
    """Return (Root boundary Wh, terminal Root Wh) without internal summing."""
    loads = {load.load_id: load for load in config.loads}
    active_ids = [member.load_id for member in cascade.members]
    active_ids.append(cascade.terminal_load_id)
    own_wh = 0.0
    deepest = -1
    terminal_wh = 0.0
    for load_id in active_ids:
        plan = plans[load_id]
        if index >= len(plan.schedule) or not plan.schedule[index]:
            continue
        hours = (
            plan.run_hours[index]
            if plan.run_hours and index < len(plan.run_hours)
            else inputs.slots[index].duration
        )
        energy = loads[load_id].nominal_power_w * hours
        own_wh += energy
        if load_id == cascade.terminal_load_id:
            terminal_wh = energy
            deepest = len(cascade.members)
        else:
            member_index = active_ids.index(load_id)
            deepest = max(deepest, member_index)
    overhead_h = inputs.slots[index].duration
    overhead_wh = sum(
        member.output_overhead_w * overhead_h for member in cascade.members[:deepest]
    )
    return own_wh + overhead_wh, terminal_wh


def _build_plan(
    cascade: LoadCascade,
    config: SystemConfig,
    original_inputs: PlanInputs,
    result: PlanResult,
    aux_segments: tuple[CascadeSourceSegment, ...],
    provisional: bool,
) -> CascadePlan:
    loads = {load.load_id: load for load in config.loads}
    states = {state.load_id: state for state in original_inputs.load_states}
    plans = _plan_by_id(result)
    current_soc: dict[str, float] = {}
    stale = provisional
    weighted = 0.0
    capacity = 0.0
    aggregate_ok = True
    for member in cascade.members:
        soc, member_stale = _usable_soc(states.get(member.load_id), original_inputs.now)
        if soc is None:
            aggregate_ok = False
            soc = member.discharge_floor_soc_percent
        current_soc[member.load_id] = soc
        stale = stale or member_stale
        load = loads[member.load_id]
        weighted += load.capacity_wh * soc
        capacity += load.capacity_wh

    flows: list[CascadeSlotFlow] = []
    schedule: list[bool] = []
    run_hours: list[float] = []
    recovery_reached: dict[str, datetime | None] = {
        member.load_id: None for member in cascade.members
    }
    planned_root = 0.0
    planned_aux = sum(segment.terminal_energy_wh for segment in aux_segments)
    for index, slot in enumerate(original_inputs.slots):
        root_wh, terminal_root_wh = _root_slot_energy(
            cascade, config, original_inputs, plans, index
        )
        planned_root += root_wh
        slot_segments: list[CascadeSourceSegment] = []
        root_hours = 0.0
        terminal_plan = plans[cascade.terminal_load_id]
        if terminal_root_wh > _EPS:
            root_hours = (
                terminal_plan.run_hours[index]
                if terminal_plan.run_hours
                else slot.duration
            )
            source: Literal["direct_pv", "house_battery"] = (
                "direct_pv"
                if slot.pv_wh + _EPS >= slot.ac_wh + root_wh
                else "house_battery"
            )
            slot_segments.append(
                CascadeSourceSegment(
                    slot_index=index,
                    start_offset_h=0.0,
                    run_hours=root_hours,
                    source=source,
                    source_load_id=None,
                    root_input_on=True,
                    terminal_energy_wh=terminal_root_wh,
                )
            )
        if index == 0:
            slot_segments.extend(aux_segments)

        member_flows: list[CascadeMemberFlow] = []
        for member in cascade.members:
            load = loads[member.load_id]
            start = current_soc[member.load_id]
            load_plan = plans[member.load_id]
            charge_h = (
                load_plan.run_hours[index]
                if index < len(load_plan.schedule)
                and load_plan.schedule[index]
                and load_plan.run_hours
                else slot.duration
                if index < len(load_plan.schedule) and load_plan.schedule[index]
                else 0.0
            )
            power = states.get(
                member.load_id, SurplusLoadState(load_id=member.load_id)
            ).planning_power_w(load)
            if member.max_charge_power_w is not None:
                power = min(power, member.max_charge_power_w)
            input_wh = power * charge_h
            stored = input_wh * member.eta_charge
            discharged = 0.0
            if index == 0:
                for segment in aux_segments:
                    if segment.source_load_id == member.load_id:
                        overhead = sum(
                            item.output_overhead_w
                            for item in cascade.members[
                                next(
                                    i
                                    for i, item in enumerate(cascade.members)
                                    if item.load_id == member.load_id
                                ) :
                            ]
                        )
                        discharged += (
                            segment.terminal_energy_wh + overhead * segment.run_hours
                        ) / member.eta_discharge
            energy = load.capacity_wh * start / 100.0 + stored - discharged
            energy = min(load.capacity_wh * load.target_soc_percent / 100.0, energy)
            floor_energy = load.capacity_wh * member.discharge_floor_soc_percent / 100.0
            energy = max(floor_energy, energy)
            end = 100.0 * energy / load.capacity_wh
            current_soc[member.load_id] = end
            if (
                recovery_reached[member.load_id] is None
                and end + _EPS >= member.recovery_soc_percent
            ):
                recovery_reached[member.load_id] = slot.start + timedelta(
                    hours=slot.duration
                )
            member_flows.append(
                CascadeMemberFlow(
                    load_id=member.load_id,
                    soc_start_percent=start,
                    soc_end_percent=end,
                    input_wh=input_wh,
                    own_charge_input_wh=input_wh,
                    battery_charge_wh=stored,
                    battery_discharge_wh=discharged,
                )
            )
        aux_wh = sum(
            segment.terminal_energy_wh
            for segment in slot_segments
            if segment.source == "aux"
        )
        terminal_wh = terminal_root_wh + aux_wh
        flows.append(
            CascadeSlotFlow(
                root_input_wh=root_wh,
                terminal_served_wh=terminal_wh,
                aux_terminal_wh=aux_wh,
                segments=tuple(slot_segments),
                member_flows=tuple(member_flows),
            )
        )
        hours = root_hours + sum(
            segment.run_hours for segment in slot_segments if segment.source == "aux"
        )
        run_hours.append(min(slot.duration, hours))
        schedule.append(hours > _EPS)

    runtime = _runtime_for(original_inputs, cascade.cascade_id)
    if aux_segments:
        runtime = CascadeRuntimeState(
            cascade_id=cascade.cascade_id,
            episode_day=original_inputs.now.date(),
            phase="running",
            source_cursor=next(
                index
                for index, member in enumerate(cascade.members)
                if member.load_id == aux_segments[0].source_load_id
            ),
            active_source_id=aux_segments[0].source_load_id,
            recovery_pending_ids=tuple(member.load_id for member in cascade.members),
        )
    elif runtime is None:
        runtime = CascadeRuntimeState(cascade_id=cascade.cascade_id)

    return CascadePlan(
        cascade_id=cascade.cascade_id,
        terminal_load_id=cascade.terminal_load_id,
        schedule=tuple(schedule),
        run_hours=tuple(run_hours),
        flows=tuple(flows),
        planned_root_energy_wh=planned_root,
        planned_aux_energy_wh=planned_aux,
        recovery_deadline=_recovery_deadline(original_inputs),
        recovery_reached_at=tuple(recovery_reached.items()),
        aggregate_soc_percent=(
            weighted / capacity if aggregate_ok and capacity else None
        ),
        aggregate_soc_stale=stale,
        provisional_live_soc_required=provisional and bool(aux_segments),
        proposed_runtime=runtime,
    )


def augment_cascade_plans(
    config: SystemConfig,
    inputs: PlanInputs,
    base_result: PlanResult,
    replan: Callable[[PlanInputs], PlanResult],
) -> PlanResult:
    """Add cascade contracts while preserving the legacy Root allocator."""
    if not config.cascades:
        return base_result

    working_inputs = inputs
    working_result = base_result
    accepted: dict[str, tuple[tuple[CascadeSourceSegment, ...], bool]] = {}
    for cascade in config.cascades:
        deadline = _recovery_deadline(working_inputs)
        if deadline is None:
            continue
        allocation = _allocate_aux_now(cascade, config, working_inputs, working_result)
        if allocation is None:
            continue
        segments, end_soc, provisional = allocation
        trial_inputs = _replace_soc_states(working_inputs, end_soc)
        trial_result = replan(trial_inputs)
        # A newly available Root booking outranks the isolated candidate.
        if _plan_by_id(trial_result)[cascade.terminal_load_id].active_now:
            continue
        if not _verify_recovery(
            cascade, config, trial_inputs, trial_result, end_soc, deadline
        ):
            continue
        stressed_inputs = _stress_inputs(config, trial_inputs)
        stressed_result = replan(stressed_inputs)
        if not _verify_recovery(
            cascade, config, stressed_inputs, stressed_result, end_soc, deadline
        ):
            continue
        accepted[cascade.cascade_id] = (segments, provisional)
        working_inputs = trial_inputs
        working_result = trial_result

    managed = {
        load_id
        for cascade in config.cascades
        for load_id in (
            *(member.load_id for member in cascade.members),
            cascade.terminal_load_id,
        )
    }
    marked_plans = tuple(
        replace(plan, managed_by_cascade=plan.load_id in managed)
        for plan in working_result.load_plans
    )
    marked_result = replace(working_result, load_plans=marked_plans)
    cascade_plans = tuple(
        _build_plan(
            cascade,
            config,
            inputs,
            marked_result,
            accepted.get(cascade.cascade_id, ((), False))[0],
            accepted.get(cascade.cascade_id, ((), False))[1],
        )
        for cascade in config.cascades
    )
    return replace(marked_result, cascade_plans=cascade_plans)
