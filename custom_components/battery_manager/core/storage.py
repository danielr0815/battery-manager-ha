"""Canonical optional-storage parameters and immutable physical evidence."""

from dataclasses import replace

from .model import (
    PlanInputs,
    PlanResult,
    StorageDiagnostics,
    SurplusLoadState,
    SystemConfig,
)


def effective_storage_config(config: SystemConfig) -> SystemConfig:
    """Fold legacy cascade-only parameters into the common storage contract.

    Old recordings carry these fields only on CascadeMember. New adapters set
    them on SurplusLoad as well. Consumers always see matching effective values.
    """
    members = {
        member.load_id: member
        for cascade in config.cascades
        for member in cascade.members
    }
    loads = []
    for load in config.loads:
        member = members.get(load.load_id)
        if member is not None and load.recovery_soc_percent is None:
            load = replace(
                load,
                recovery_soc_percent=member.recovery_soc_percent,
                eta_charge=member.eta_charge,
                max_charge_power_w=member.max_charge_power_w,
            )
        loads.append(load)
    by_id = {load.load_id: load for load in loads}
    cascades = []
    for cascade in config.cascades:
        effective_members = []
        for member in cascade.members:
            load = by_id[member.load_id]
            recovery = load.recovery_soc_percent
            assert recovery is not None  # Legacy member fallback above supplied it.
            effective_members.append(
                replace(
                    member,
                    recovery_soc_percent=recovery,
                    eta_charge=load.eta_charge,
                    max_charge_power_w=load.max_charge_power_w,
                )
            )
        cascades.append(replace(cascade, members=tuple(effective_members)))
    return replace(config, loads=tuple(loads), cascades=tuple(cascades))


def required_dwell_energy(config: SystemConfig, inputs: PlanInputs) -> PlanInputs:
    """Only confirmed physical ON dwell belongs to the house reference."""
    if inputs.recharge_required_ac_wh:
        return inputs
    loads = {load.load_id: load for load in config.loads}
    required = [0.0] * len(inputs.slots)
    confirmed_hours: dict[str, list[float]] = {}
    for state in inputs.load_states:
        if state.minimum_run_until is None or not state.available:
            continue
        load = loads.get(state.load_id)
        if load is None:
            continue
        power = state.planning_power_w(load)
        if load.max_charge_power_w is not None:
            power = min(power, load.max_charge_power_w)
        remaining = state.remaining_energy_wh(load)
        budget = None if remaining is None else remaining / load.eta_charge
        run_hours = [0.0] * len(inputs.slots)
        confirmed_hours[load.load_id] = run_hours
        for index, slot in enumerate(inputs.slots):
            hours = max(
                0.0,
                min(
                    slot.duration,
                    (state.minimum_run_until - slot.start).total_seconds() / 3600.0,
                ),
            )
            energy = power * hours
            if load.gate_stop_capable and budget is not None:
                energy = min(energy, budget)
                budget -= energy
            required[index] += energy
            run_hours[index] = energy / power if power > 0.0 else 0.0
    # The root also supplies each active output's losses. Every confirmed
    # downstream run is a prefix of its slot, so their union is the longest
    # capped prefix, not the sum of overlapping load durations.
    for cascade in config.cascades:
        for member_index, member in enumerate(cascade.members):
            downstream = tuple(
                item.load_id for item in cascade.members[member_index + 1 :]
            ) + (cascade.terminal_load_id,)
            for index in range(len(inputs.slots)):
                active_hours = max(
                    confirmed_hours[load_id][index]
                    if load_id in confirmed_hours
                    else 0.0
                    for load_id in downstream
                )
                required[index] += member.output_overhead_w * active_hours
    if not any(required):
        return inputs
    return replace(inputs, recharge_required_ac_wh=tuple(required))


def storage_diagnostics(
    config: SystemConfig, inputs: PlanInputs, result: PlanResult
) -> PlanResult:
    """Carry one physical stored-energy account through the entire horizon."""
    states = {state.load_id: state for state in inputs.load_states}
    loads = {load.load_id: load for load in config.loads}
    # Preserve every per-slot member observation, including real Aux discharge.
    physical = {
        (item.load_id, index): item
        for cascade in result.cascade_plans
        for index, flow in enumerate(cascade.flows)
        for item in flow.member_flows
    }
    plans = []
    for plan in result.load_plans:
        load = loads[plan.load_id]
        if not load.energy_limited or load.recovery_soc_percent is None:
            plans.append(plan)
            continue
        state = states.get(load.load_id, SurplusLoadState(load.load_id))
        energy = (state.soc_percent or 0.0) * load.capacity_wh / 100.0
        target = load.recovery_soc_percent * load.capacity_wh / 100.0
        deficit = max(0.0, target - energy)
        recovery = 0.0
        topup = 0.0
        ends = {}
        for index, slot in enumerate(inputs.slots):
            flow = physical.get((load.load_id, index))
            if flow is not None:
                charged = flow.battery_charge_wh
                # Aux creates a deficit only when the physical discharge occurs.
                energy = flow.soc_start_percent * load.capacity_wh / 100.0
                to_minimum = min(charged, max(0.0, target - energy))
                energy = flow.soc_end_percent * load.capacity_wh / 100.0
            else:
                hours = (
                    plan.run_hours[index]
                    if plan.run_hours
                    else slot.duration
                    if plan.schedule[index]
                    else 0.0
                )
                power = state.planning_power_w(load)
                if load.max_charge_power_w is not None:
                    power = min(power, load.max_charge_power_w)
                charged = hours * power * load.eta_charge
                to_minimum = min(charged, max(0.0, target - energy))
                energy += charged
            recovery += to_minimum / load.eta_charge
            topup += (charged - to_minimum) / load.eta_charge
            ends[slot.start.date()] = energy
        reasons = tuple(dict.fromkeys(reason for _, reason in plan.rejected_candidates))
        if state.soc_percent is None:
            reasons += ("soc_unknown_assumed_empty",)
        if not state.available:
            reasons += ("unavailable",)
        if energy + max(50.0, load.capacity_wh * 0.02) < target:
            # A rejected or unproved candidate is not proof that every physical
            # recovery opportunity is impossible. Retain the actual gate reason
            # without inventing a finer cause such as exhausted search budget.
            reasons += ("recovery_target_unmet",)
            if any(
                reason in ("house_recharge", "house_recharge_search_limit")
                for reason in reasons
            ):
                reasons += ("house_supply_priority",)
        plans.append(
            replace(
                plan,
                storage=StorageDiagnostics(
                    load.recovery_soc_percent,
                    deficit,
                    recovery,
                    topup,
                    load.capacity_wh,
                    tuple(ends.items()),
                    reasons,
                ),
            )
        )
    return replace(result, load_plans=tuple(plans))
