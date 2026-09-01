"""Pure cascade planning layered on the legacy surplus allocator.

The legacy planner remains the source of truth for Root-boundary allocation and
global load priority.  This module adds isolated auxiliary discharge down to
each member's configured cascade target.  Energy above that target is available
without a recharge promise; a second pass may use the hard-floor reserve only
when the global plan proves that otherwise-exported energy restores the target.
At the cascade's global priority position, direct terminal use precedes later
Root charging so useful consumption does not take a lossy storage detour.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Literal

from .model import (
    CascadeMemberFlow,
    CascadePlan,
    CascadeRuntimeState,
    CascadeSlotFlow,
    CascadeSourceSegment,
    LoadCascade,
    LoadPlan,
    PlanInputs,
    PlanResult,
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


def _allocate_aux_now(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    result: PlanResult,
    target_soc_by_id: dict[str, float] | None = None,
) -> tuple[tuple[CascadeSourceSegment, ...], dict[str, float], bool] | None:
    """Allocate one contiguous terminal episode down to all member targets.

    Only slot 0 is executable now; later segments are a rolling forecast.  The
    full episode is nevertheless required here so the following legacy replan
    sees the recharge headroom before it decides that residual PV must be sent
    to early feed-in.
    """
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
    continuing = (
        runtime is not None
        and runtime.phase in ("running", "proving")
        and runtime.episode_day in (None, today)
    )
    # Operator rule 2026-08-30: once the terminal has started, keep consuming
    # the remaining energy above the cascade targets instead of demanding a
    # fresh full dwell on every rolling replan.  A new episode still needs the
    # configured terminal dwell; every handover retains its proof minute.
    required_h = (
        _SOURCE_PROOF_H
        if continuing
        else max(_SOURCE_PROOF_H, terminal.min_runtime_min / 60.0)
    )
    segments: list[CascadeSourceSegment] = []
    end_soc: dict[str, float] = {}
    provisional = False

    # An Aux episode starts now and stays contiguous until Root/PV takes the
    # terminal load over.  Restrict it to the local day: the persisted daily
    # episode contract is evaluated again after midnight.
    slot_capacity_h: list[float] = []
    today = inputs.now.date()
    for index, slot in enumerate(inputs.slots):
        if slot.start.date() != today:
            break
        if index < len(terminal_plan.schedule) and terminal_plan.schedule[index]:
            break
        slot_capacity_h.append(slot.duration)
    if not slot_capacity_h:
        return None
    used_h = [0.0] * len(slot_capacity_h)

    for member_index, member in enumerate(cascade.members):
        load = loads[member.load_id]
        state = states.get(member.load_id)
        soc, stale = _usable_soc(state, inputs.now)
        if soc is None:
            return None  # every member is part of the protected target vector
        provisional = provisional or stale
        end_soc[member.load_id] = soc
        if state is None or not state.available:
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

        # The normal target is unconditional.  A lower caller-provided target
        # is reserved for the export-backed pre-drain pass and remains clamped
        # to the hard safety floor.
        target_soc = max(
            member.discharge_floor_soc_percent,
            min(
                member.recovery_soc_percent,
                (target_soc_by_id or {}).get(
                    member.load_id, member.recovery_soc_percent
                ),
            ),
        )
        usable_store = max(
            0.0,
            (soc - target_soc) / 100.0 * load.capacity_wh,
        )
        available_h = usable_store * member.eta_discharge / source_power
        if available_h + _EPS < _SOURCE_PROOF_H:
            continue
        episode_capacity_h = sum(
            max(0.0, capacity_h - used_h[index])
            for index, capacity_h in enumerate(slot_capacity_h)
        )
        take_h = min(episode_capacity_h, available_h)
        # Never spend a switching cycle on a source that cannot carry its own
        # mandatory proof window.
        if take_h + _EPS < _SOURCE_PROOF_H:
            continue
        remaining_source_h = take_h
        for slot_index, capacity_h in enumerate(slot_capacity_h):
            free_h = max(0.0, capacity_h - used_h[slot_index])
            if free_h <= _EPS:
                continue
            segment_h = min(free_h, remaining_source_h)
            segments.append(
                CascadeSourceSegment(
                    slot_index=slot_index,
                    start_offset_h=used_h[slot_index],
                    run_hours=segment_h,
                    source="aux",
                    source_load_id=member.load_id,
                    root_input_on=False,
                    terminal_energy_wh=terminal.nominal_power_w * segment_h,
                )
            )
            used_h[slot_index] += segment_h
            remaining_source_h -= segment_h
            if remaining_source_h <= _EPS:
                break
        drawn = source_power * take_h / member.eta_discharge
        end_soc[member.load_id] = max(
            target_soc,
            soc - 100.0 * drawn / load.capacity_wh,
        )

    if sum(segment.run_hours for segment in segments) + _EPS < required_h:
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
    # F-CASCADE-STORAGE idle-slot observability: ``deepest == -1`` means that no member or
    # terminal load is active.  Python's ``members[:-1]`` would otherwise count
    # every output except the last as phantom Root energy, which started B1 even
    # though the whole cascade schedule was idle (live incident 2026-08-29).
    overhead_wh = (
        sum(
            member.output_overhead_w * overhead_h
            for member in cascade.members[:deepest]
        )
        if deepest >= 0
        else 0.0
    )
    return own_wh + overhead_wh, terminal_wh


def _build_plan(
    cascade: LoadCascade,
    config: SystemConfig,
    original_inputs: PlanInputs,
    result: PlanResult,
    aux_segments: tuple[CascadeSourceSegment, ...],
    provisional: bool,
    recovery_deadline: datetime | None = None,
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
    recovery_needed = {
        member.load_id: recovery_deadline is None
        or current_soc[member.load_id] + _EPS < member.recovery_soc_percent
        for member in cascade.members
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
        slot_segments.extend(
            segment for segment in aux_segments if segment.slot_index == index
        )

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
            for segment in slot_segments:
                if segment.source == "aux" and segment.source_load_id == member.load_id:
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
            if end + _EPS < member.recovery_soc_percent:
                recovery_needed[member.load_id] = True
                recovery_reached[member.load_id] = None
            if (
                recovery_needed[member.load_id]
                and recovery_reached[member.load_id] is None
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
            recovery_pending_ids=tuple(
                member.load_id
                for member_index, member in enumerate(cascade.members)
                if (
                    soc := _usable_soc(states.get(member.load_id), original_inputs.now)[
                        0
                    ]
                )
                is not None
                and (
                    soc + _EPS < member.recovery_soc_percent
                    or any(
                        member_index < len(flow.member_flows)
                        and flow.member_flows[member_index].soc_end_percent + _EPS
                        < member.recovery_soc_percent
                        for flow in flows
                    )
                )
            ),
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
        recovery_deadline=recovery_deadline,
        recovery_reached_at=tuple(recovery_reached.items()),
        aggregate_soc_percent=(
            weighted / capacity if aggregate_ok and capacity else None
        ),
        aggregate_soc_stale=stale,
        provisional_live_soc_required=provisional and bool(aux_segments),
        proposed_runtime=runtime,
    )


def _replan_candidate(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    base_result: PlanResult,
    replan: Callable[[PlanInputs], PlanResult],
    target_soc_by_id: dict[str, float] | None = None,
) -> (
    tuple[
        tuple[CascadeSourceSegment, ...],
        dict[str, float],
        bool,
        PlanInputs,
        PlanResult,
    ]
    | None
):
    """Build one electrically exclusive Aux candidate and its Root replan."""
    allocation = _allocate_aux_now(
        cascade, config, inputs, base_result, target_soc_by_id
    )
    if allocation is None:
        return None
    segments, end_soc, provisional = allocation
    trial_inputs = _replace_soc_states(inputs, end_soc)
    trial_result = replan(trial_inputs)
    trial_plans = _plan_by_id(trial_result)
    cascade_load_ids = (
        *(member.load_id for member in cascade.members),
        cascade.terminal_load_id,
    )
    conflict_indexes = [
        segment.slot_index
        for segment in segments
        if any(
            segment.slot_index < len(trial_plans[load_id].schedule)
            and trial_plans[load_id].schedule[segment.slot_index]
            for load_id in cascade_load_ids
        )
    ]
    if conflict_indexes:
        # Root is electrically exclusive with Aux.  Rebuild the candidate only
        # up to its first newly booked Root slot and value that smaller
        # discharge headroom again.
        first_conflict = min(conflict_indexes)
        shortened_inputs = replace(inputs, slots=inputs.slots[:first_conflict])
        allocation = _allocate_aux_now(
            cascade,
            config,
            shortened_inputs,
            base_result,
            target_soc_by_id,
        )
        if allocation is None:
            return None
        segments, end_soc, provisional = allocation
        trial_inputs = _replace_soc_states(inputs, end_soc)
        trial_result = replan(trial_inputs)
        trial_plans = _plan_by_id(trial_result)
        if any(
            segment.slot_index < len(trial_plans[load_id].schedule)
            and trial_plans[load_id].schedule[segment.slot_index]
            for segment in segments
            for load_id in cascade_load_ids
        ):
            return None
    return segments, end_soc, provisional, trial_inputs, trial_result


def _first_export_budget(
    result: PlanResult, inputs: PlanInputs
) -> tuple[date, float] | None:
    """Return the first forecast day and AC Wh that would still be exported."""
    by_day: dict[date, float] = {}
    for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=False):
        by_day[slot.start.date()] = by_day.get(slot.start.date(), 0.0) + max(
            0.0, flow.grid_export_wh
        )
    return next(
        ((day, export_wh) for day, export_wh in by_day.items() if export_wh > _EPS),
        None,
    )


def _conditional_targets(
    cascade: LoadCascade,
    config: SystemConfig,
    end_soc: dict[str, float],
    export_budget_wh: float,
) -> dict[str, float]:
    """Spend otherwise-exported AC energy on recoverable SOC headroom."""
    loads = {load.load_id: load for load in config.loads}
    remaining_input_wh = export_budget_wh
    targets: dict[str, float] = {}
    for member in cascade.members:
        load = loads[member.load_id]
        start = end_soc[member.load_id]
        available_battery_wh = max(
            0.0,
            (start - member.discharge_floor_soc_percent) / 100.0 * load.capacity_wh,
        )
        battery_wh = min(
            available_battery_wh,
            remaining_input_wh * member.eta_charge,
        )
        targets[member.load_id] = max(
            member.discharge_floor_soc_percent,
            start - 100.0 * battery_wh / load.capacity_wh,
        )
        remaining_input_wh -= battery_wh / member.eta_charge
    return targets


def _member_soc_vector(
    cascade: LoadCascade,
    inputs: PlanInputs,
) -> tuple[dict[str, float], bool] | None:
    """Return the current complete target vector for a below-target pass."""
    states = {state.load_id: state for state in inputs.load_states}
    values: dict[str, float] = {}
    provisional = False
    for member in cascade.members:
        soc, stale = _usable_soc(states.get(member.load_id), inputs.now)
        if soc is None:
            return None
        values[member.load_id] = soc
        provisional = provisional or stale
    return values, provisional


def _recovers_by(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    result: PlanResult,
    segments: tuple[CascadeSourceSegment, ...],
    provisional: bool,
    end_soc: dict[str, float],
    recovery_day: date,
) -> bool:
    """Prove every member below its normal target recovers by export-day end."""
    deadline_index = max(
        index
        for index, slot in enumerate(inputs.slots)
        if slot.start.date() == recovery_day
    )
    candidate = _build_plan(cascade, config, inputs, result, segments, provisional)
    deadline_flow = candidate.flows[deadline_index]
    return all(
        end_soc[member.load_id] + _EPS >= member.recovery_soc_percent
        or (
            member_index < len(deadline_flow.member_flows)
            and deadline_flow.member_flows[member_index].soc_end_percent + _EPS
            >= member.recovery_soc_percent
        )
        for member_index, member in enumerate(cascade.members)
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
    accepted: dict[
        str, tuple[tuple[CascadeSourceSegment, ...], bool, datetime | None]
    ] = {}
    for cascade in config.cascades:
        segments: tuple[CascadeSourceSegment, ...]
        end_soc: dict[str, float]
        provisional: bool
        trial_inputs: PlanInputs
        trial_result: PlanResult
        candidate = _replan_candidate(
            cascade, config, working_inputs, working_result, replan
        )
        if candidate is None:
            # A member may already be below the unconditional 50 % target.  It
            # can still continue an export-backed episode, but only the second
            # pass below may create that discharge; the ordinary pass remains
            # unable to spend this protected reserve.
            current = _member_soc_vector(cascade, working_inputs)
            if current is None:
                continue
            end_soc, provisional = current
            segments = ()
            trial_inputs = working_inputs
            trial_result = working_result
        else:
            segments, end_soc, provisional, trial_inputs, trial_result = candidate
        recovery_deadline = None

        export = _first_export_budget(trial_result, working_inputs)
        if export is not None:
            recovery_day, export_budget_wh = export
            targets = _conditional_targets(cascade, config, end_soc, export_budget_wh)
            conditional = _replan_candidate(
                cascade,
                config,
                working_inputs,
                working_result,
                replan,
                targets,
            )
            if conditional is not None:
                (
                    deep_segments,
                    deep_end_soc,
                    deep_provisional,
                    deep_inputs,
                    deep_result,
                ) = conditional
                deadline = max(
                    (
                        slot.start + timedelta(hours=slot.duration)
                        for slot in working_inputs.slots
                        if slot.start.date() == recovery_day
                    ),
                    default=None,
                )
                if (
                    deadline is not None
                    and deep_result.grid_import_kwh
                    <= trial_result.grid_import_kwh + _EPS
                    and deep_result.lost_surplus_kwh + _EPS
                    < trial_result.lost_surplus_kwh
                    and _recovers_by(
                        cascade,
                        config,
                        working_inputs,
                        deep_result,
                        deep_segments,
                        deep_provisional,
                        deep_end_soc,
                        recovery_day,
                    )
                ):
                    segments = deep_segments
                    end_soc = deep_end_soc
                    provisional = deep_provisional
                    trial_inputs = deep_inputs
                    trial_result = deep_result
                    recovery_deadline = deadline

        if not segments:
            continue
        accepted[cascade.cascade_id] = (
            segments,
            provisional,
            recovery_deadline,
        )
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
            accepted.get(cascade.cascade_id, ((), False, None))[0],
            accepted.get(cascade.cascade_id, ((), False, None))[1],
            accepted.get(cascade.cascade_id, ((), False, None))[2],
        )
        for cascade in config.cascades
    )
    return replace(marked_result, cascade_plans=cascade_plans)
