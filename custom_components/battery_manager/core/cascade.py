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


def _effective_power_w(
    load_id: str,
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
) -> float:
    """Return the same robust, hard-capped power used by the allocator."""
    loads = {load.load_id: load for load in config.loads}
    states = {state.load_id: state for state in inputs.load_states}
    load = loads[load_id]
    state = states.get(load_id, SurplusLoadState(load_id=load_id))
    power_w = state.planning_power_w(load)
    member = next((item for item in cascade.members if item.load_id == load_id), None)
    if member is not None and member.max_charge_power_w is not None:
        power_w = min(power_w, member.max_charge_power_w)
    return power_w


def _allocate_aux_now(
    cascade: LoadCascade,
    config: SystemConfig,
    inputs: PlanInputs,
    result: PlanResult,
    target_soc_by_id: dict[str, float] | None = None,
    *,
    prefer_early: bool = False,
) -> tuple[tuple[CascadeSourceSegment, ...], dict[str, float], bool] | None:
    """Allocate feasible Aux episodes down to all member targets.

    Only slot 0 is executable now; later segments are a rolling forecast.  A
    new episode is aligned as late as possible inside each electrically free
    window; Root/PV may split the plan into more than one episode on the same
    local day.  A running episode remains anchored at slot 0.  The full useful
    service plus every required transition is booked so the following legacy
    replan sees only physically reachable recharge headroom before residual PV
    is sent to early feed-in.
    """
    if not inputs.slots:
        return None
    runtime = _runtime_for(inputs, cascade.cascade_id)
    today = inputs.now.date()
    plans = _plan_by_id(result)
    terminal_plan = plans[cascade.terminal_load_id]
    # Root/PV and house-battery service always outrank isolated storage.
    if terminal_plan.active_now:
        return None

    loads = {load.load_id: load for load in config.loads}
    states = {state.load_id: state for state in inputs.load_states}
    terminal = loads[cascade.terminal_load_id]
    terminal_power_w = _effective_power_w(
        cascade.terminal_load_id, cascade, config, inputs
    )
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

    # Every electrically free window of the remaining local day is eligible.
    # Root slots split episodes but do not suppress a later, freshly proven
    # episode; this is how an early discharge/recovery/discharge day reaches a
    # fixed point instead of feeding the second export peak into the grid.
    slot_indexes: list[int] = []
    slot_capacity_h: list[float] = []
    cascade_load_ids = (
        *(member.load_id for member in cascade.members),
        cascade.terminal_load_id,
    )
    today = inputs.now.date()
    for index, slot in enumerate(inputs.slots):
        if slot.start.date() != today:
            break
        if any(
            index < len(plans[load_id].schedule) and plans[load_id].schedule[index]
            for load_id in cascade_load_ids
        ):
            continue
        slot_indexes.append(index)
        slot_capacity_h.append(slot.duration)
    if not slot_capacity_h:
        return None
    windows: list[tuple[int, int]] = []
    window_start = 0
    for position in range(1, len(slot_indexes) + 1):
        if position == len(slot_indexes) or (
            slot_indexes[position] > slot_indexes[position - 1] + 1
        ):
            windows.append((window_start, position))
            window_start = position
    transition_h = 0.0 if continuing else cascade.startup_transition_s / 3600.0
    window_capacities = [
        max(0.0, sum(slot_capacity_h[start:end]) - transition_h)
        for start, end in windows
    ]
    service_capacity_h = sum(
        capacity for capacity in window_capacities if capacity + _EPS >= required_h
    )
    if service_capacity_h + _EPS < required_h:
        return None
    source_runs: list[tuple[str, float]] = []
    allocated_h = 0.0

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
        source_power = terminal_power_w + downstream_overhead
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
        episode_capacity_h = max(0.0, service_capacity_h - allocated_h)
        take_h = min(episode_capacity_h, available_h)
        # Never spend a switching cycle on a source that cannot carry its own
        # mandatory proof window.
        if take_h + _EPS < _SOURCE_PROOF_H:
            continue
        source_runs.append((member.load_id, take_h))
        allocated_h += take_h

    if allocated_h + _EPS < required_h:
        return None

    # Pack service into the minimum suffix of electrically free windows.  A
    # fragmented day may have more free capacity than can legally be used: if
    # the available source energy cannot pay the useful minimum runtime in one
    # more window, leave that earliest fragment unused instead of rejecting the
    # otherwise feasible later episodes altogether.
    eligible = [
        index
        for index, capacity in enumerate(window_capacities)
        if capacity + _EPS >= required_h
    ]
    selected: list[int] = []
    capacity_sum = 0.0
    max_episode_count = max(1, int((allocated_h + _EPS) / required_h))
    window_order = eligible if continuing or prefer_early else reversed(eligible)
    for window_index in window_order:
        if len(selected) >= max_episode_count:
            break
        selected.append(window_index)
        capacity_sum += window_capacities[window_index]
        if capacity_sum + _EPS >= allocated_h:
            break
    allocated_h = min(allocated_h, capacity_sum)
    if not continuing and not prefer_early:
        selected.reverse()

    # Trim the source vector to the service that actually fits.  SOC must be
    # derived from this physical vector, not from capacity discarded because a
    # further episode could not meet its minimum runtime.
    remaining_source_h = allocated_h
    trimmed_source_runs: list[tuple[str, float]] = []
    for load_id, available_h in source_runs:
        take_h = min(available_h, remaining_source_h)
        if take_h > _EPS:
            trimmed_source_runs.append((load_id, take_h))
            remaining_source_h -= take_h
        if remaining_source_h <= _EPS:
            break
    source_runs = trimmed_source_runs
    member_indexes = {
        member.load_id: index for index, member in enumerate(cascade.members)
    }
    for load_id, take_h in source_runs:
        member_index = member_indexes[load_id]
        member = cascade.members[member_index]
        load = loads[load_id]
        soc = end_soc[load_id]
        source_power = terminal_power_w + sum(
            item.output_overhead_w for item in cascade.members[member_index:]
        )
        drawn = source_power * take_h / member.eta_discharge
        target_soc = max(
            member.discharge_floor_soc_percent,
            min(
                member.recovery_soc_percent,
                (target_soc_by_id or {}).get(
                    member.load_id, member.recovery_soc_percent
                ),
            ),
        )
        end_soc[load_id] = max(
            target_soc,
            soc - 100.0 * drawn / load.capacity_wh,
        )

    # Give every selected episode its minimum useful runtime first and place
    # all remaining service as late as possible.  This construction is always
    # feasible because the episode count was bounded by available source time.
    service_by_window = {window_index: required_h for window_index in selected}
    remaining_service_h = allocated_h - required_h * len(selected)
    placement_order = selected if prefer_early else reversed(selected)
    for window_index in placement_order:
        extra_h = min(
            window_capacities[window_index] - required_h,
            remaining_service_h,
        )
        service_by_window[window_index] += extra_h
        remaining_service_h -= extra_h

    source_cursor = 0
    source_remaining_h = source_runs[0][1]

    def append_interval(
        start: int,
        end: int,
        absolute_start_h: float,
        duration_h: float,
        source_load_id: str,
        *,
        transition: bool,
    ) -> None:
        """Append one continuous window interval split at slot boundaries."""
        elapsed_h = 0.0
        remaining_h = duration_h
        for position in range(start, end):
            slot_h = slot_capacity_h[position]
            overlap_start_h = max(absolute_start_h, elapsed_h)
            overlap_end_h = min(absolute_start_h + duration_h, elapsed_h + slot_h)
            take_h = max(0.0, overlap_end_h - overlap_start_h)
            if take_h > _EPS:
                segments.append(
                    CascadeSourceSegment(
                        slot_index=slot_indexes[position],
                        start_offset_h=overlap_start_h - elapsed_h,
                        run_hours=take_h,
                        source="aux",
                        source_load_id=source_load_id,
                        root_input_on=False,
                        terminal_energy_wh=(
                            0.0 if transition else terminal_power_w * take_h
                        ),
                        transition_hours=take_h if transition else 0.0,
                    )
                )
                remaining_h -= take_h
            elapsed_h += slot_h
            if remaining_h <= _EPS:
                break

    for window_index in selected:
        start, end = windows[window_index]
        service_h = service_by_window[window_index]
        window_h = sum(slot_capacity_h[start:end])
        episode_transition_h = transition_h
        episode_start_h = (
            0.0
            if prefer_early or (continuing and window_index == selected[0])
            else window_h - service_h - episode_transition_h
        )
        source_load_id = source_runs[source_cursor][0]
        if episode_transition_h > _EPS:
            append_interval(
                start,
                end,
                episode_start_h,
                episode_transition_h,
                source_load_id,
                transition=True,
            )
        service_start_h = episode_start_h + episode_transition_h
        served_in_window_h = 0.0
        while served_in_window_h + _EPS < service_h:
            take_h = min(
                source_remaining_h,
                service_h - served_in_window_h,
            )
            append_interval(
                start,
                end,
                service_start_h + served_in_window_h,
                take_h,
                source_runs[source_cursor][0],
                transition=False,
            )
            served_in_window_h += take_h
            source_remaining_h -= take_h
            if source_remaining_h <= _EPS and source_cursor + 1 < len(source_runs):
                source_cursor += 1
                source_remaining_h = source_runs[source_cursor][1]
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
    active_ids = [member.load_id for member in cascade.members]
    active_ids.append(cascade.terminal_load_id)
    own_wh = 0.0
    terminal_wh = 0.0
    active_hours: dict[str, float] = {}
    for load_id in active_ids:
        plan = plans[load_id]
        if index >= len(plan.schedule) or not plan.schedule[index]:
            continue
        hours = (
            plan.run_hours[index]
            if plan.run_hours and index < len(plan.run_hours)
            else inputs.slots[index].duration
        )
        energy = _effective_power_w(load_id, cascade, config, inputs) * hours
        own_wh += energy
        active_hours[load_id] = hours
        if load_id == cascade.terminal_load_id:
            terminal_wh = energy
    # Each output is needed for the longest downstream activity, not for the
    # whole slot. This keeps partial-slot Root energy and the member SOC vector
    # on one physical time base and preserves exact zero for an idle slot.
    overhead_wh = 0.0
    for member_index, member in enumerate(cascade.members):
        downstream_ids = [
            item.load_id for item in cascade.members[member_index + 1 :]
        ] + [cascade.terminal_load_id]
        output_h = max(active_hours.get(load_id, 0.0) for load_id in downstream_ids)
        overhead_wh += member.output_overhead_w * output_h
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
    terminal_power_w = _effective_power_w(
        cascade.terminal_load_id, cascade, config, original_inputs
    )
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
            power = _effective_power_w(member.load_id, cascade, config, original_inputs)
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
                    service_h = (
                        segment.terminal_energy_wh / terminal_power_w
                        if terminal_power_w > _EPS
                        else 0.0
                    )
                    discharged += (
                        segment.terminal_energy_wh + overhead * service_h
                    ) / member.eta_discharge
            energy = load.capacity_wh * start / 100.0 + stored - discharged
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
                    soc_known=_usable_soc(
                        states.get(member.load_id), original_inputs.now
                    )[0]
                    is not None,
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
    active_aux_segments = tuple(
        segment for segment in aux_segments if segment.slot_index == 0
    )
    if active_aux_segments:
        runtime = CascadeRuntimeState(
            cascade_id=cascade.cascade_id,
            episode_day=original_inputs.now.date(),
            phase="running",
            source_cursor=next(
                index
                for index, member in enumerate(cascade.members)
                if member.load_id == active_aux_segments[0].source_load_id
            ),
            active_source_id=active_aux_segments[0].source_load_id,
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
    replan: Callable[[PlanInputs, tuple[CascadeSourceSegment, ...]], PlanResult],
    target_soc_by_id: dict[str, float] | None = None,
    *,
    prior_segments: tuple[CascadeSourceSegment, ...] = (),
    prefer_early: bool = False,
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
        cascade,
        config,
        inputs,
        base_result,
        target_soc_by_id,
        prefer_early=prefer_early,
    )
    if allocation is None:
        return None
    segments, end_soc, provisional = allocation
    trial_inputs = _replace_soc_states(inputs, end_soc)
    trial_result = replan(trial_inputs, (*prior_segments, *segments))
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
            prefer_early=prefer_early,
        )
        if allocation is None:
            return None
        segments, end_soc, provisional = allocation
        trial_inputs = _replace_soc_states(inputs, end_soc)
        trial_result = replan(trial_inputs, (*prior_segments, *segments))
        trial_plans = _plan_by_id(trial_result)
        if any(
            segment.slot_index < len(trial_plans[load_id].schedule)
            and trial_plans[load_id].schedule[segment.slot_index]
            for segment in segments
            for load_id in cascade_load_ids
        ):
            return None
    return segments, end_soc, provisional, trial_inputs, trial_result


def _today_export_budget(
    result: PlanResult, inputs: PlanInputs
) -> tuple[date, float] | None:
    """Return today's residual export; a later day cannot back a discharge."""
    today = inputs.now.date()
    export_wh = 0.0
    for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=False):
        if slot.start.date() == today:
            export_wh += max(0.0, flow.grid_export_wh)
    return (today, export_wh) if export_wh > _EPS else None


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
    replan: Callable[[PlanInputs, tuple[CascadeSourceSegment, ...]], PlanResult],
) -> PlanResult:
    """Add cascade contracts while preserving the legacy Root allocator."""
    if not config.cascades:
        return base_result

    working_inputs = inputs
    working_result = base_result
    accepted: dict[
        str, tuple[tuple[CascadeSourceSegment, ...], bool, datetime | None]
    ] = {}
    accepted_segments: tuple[CascadeSourceSegment, ...] = ()
    for cascade in config.cascades:
        segments: tuple[CascadeSourceSegment, ...]
        end_soc: dict[str, float]
        provisional: bool
        trial_inputs: PlanInputs
        trial_result: PlanResult
        candidate = _replan_candidate(
            cascade,
            config,
            working_inputs,
            working_result,
            replan,
            prior_segments=accepted_segments,
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

        export = _today_export_budget(trial_result, working_inputs)
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
                prior_segments=accepted_segments,
                prefer_early=True,
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
        accepted_segments = (*accepted_segments, *segments)

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
