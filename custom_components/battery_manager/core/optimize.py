"""Planner: threshold search, surplus allocation, appliance advisor, support
escalation. Implements docs/ALGORITHM.md §1 with decisions D-A1..D-A9."""

from __future__ import annotations

import math

from .model import (
    LoadPlan,
    PlanInputs,
    PlanResult,
    SurplusLoadState,
    SystemConfig,
    Trajectory,
)
from .series import insert_appliance_run
from .simulate import simulate

_EPS = 1e-6


def _degrades_min_soc(
    trial: Trajectory, reference: Trajectory, floor_percent: float
) -> bool:
    """True if the trial dips below the floor AND made things worse.

    Dips the reference plan already contains (e.g. a cloudy tail late in the
    horizon) must not veto a load hour: once both variants reach the same SOC
    (typically the full battery before the surplus), their futures are
    identical, so such dips are not caused by the load (operator insight,
    2026-07-04: everything after reaching max SOC is irrelevant for the
    decision because the battery cannot get any fuller).
    """
    return (
        trial.min_soc_percent < floor_percent - _EPS
        and trial.min_soc_percent < reference.min_soc_percent - _EPS
    )


def search_threshold(
    config: SystemConfig, inputs: PlanInputs
) -> tuple[float, Trajectory]:
    """Find the SOC threshold minimizing import − terminal value + export tiebreak.

    Ties prefer the LOWER threshold ("Nutzen", D-A1b): drain the battery ahead
    of the next surplus rather than hoarding charge.
    """
    battery = config.battery
    control = config.control
    terminal_factor = battery.eta_discharge * config.inverter.eta

    lo = int(
        math.ceil(
            max(
                control.inverter_min_soc_percent,
                battery.soc_min_percent + control.soc_buffer_percent,
            )
        )
    )
    hi = int(math.floor(battery.soc_max_percent))

    best_threshold = float(hi)
    best_cost = math.inf
    best_traj: Trajectory | None = None

    for candidate in range(lo, hi + 1):
        traj = simulate(config, inputs, float(candidate))
        end_wh = battery.energy_wh(traj.end_soc_percent)
        cost = (
            traj.total_import_wh
            - terminal_factor * end_wh
            + control.export_tiebreak * traj.total_export_wh
        )
        if cost < best_cost - _EPS:  # strict: ascending scan keeps lowest on ties
            best_cost = cost
            best_threshold = float(candidate)
            best_traj = traj

    if best_traj is None:  # no slots — degenerate but valid
        best_traj = simulate(config, inputs, best_threshold)
    return best_threshold, best_traj


def _committed_hours(load, slot) -> float:
    """Runtime one activation decision really commits the executor to.

    Switching a load on holds the real switch for at least `min_runtime_min`
    (coordinator dwell), so the planner must evaluate and book that energy —
    not the sliver left in a nearly elapsed slot. Without this, a 1-minute
    slot 0 made ~5 Wh pass every gate while the dwell then charged ~250 Wh
    unaccounted (degenerate-slot-0 artifact, observed live 2026-07-05 04:59).
    The floor applies to interior slots too: with `min_runtime_min` > 60 the
    hour would otherwise be booked smaller than it can ever execute.
    """
    return max(slot.duration, load.min_runtime_min / 60.0)


def _spread_energy(
    extra: list[float],
    slots,
    start: int,
    power_w: float,
    hours: float,
) -> tuple[list[float], list[tuple[int, float]]]:
    """Lay `power_w` running for `hours` into a copy of `extra`.

    The energy is placed in real time from slot `start` on, spilling across
    slot boundaries (a min-runtime commitment near the end of an hour lands
    partly in the next slot). Returns the trial series and the covered
    (slot index, occupied hours) pairs.
    """
    trial = list(extra)
    covered: list[tuple[int, float]] = []
    remaining_h = hours
    j = start
    while remaining_h > _EPS and j < len(slots):
        take = min(remaining_h, slots[j].duration)
        trial[j] += power_w * take
        covered.append((j, take))
        remaining_h -= take
        j += 1
    return trial, covered


def allocate_loads(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    base_trajectory: Trajectory,
) -> tuple[list[LoadPlan], tuple[float, ...], Trajectory]:
    """Assign surplus loads to hours in two passes.

    Pass 1 fills hours with direct surplus (battery share within the load's
    tolerance across the committed runtime). Pass 2 ("zielbasiert", decision
    2026-07-04) additionally allows hours WITHOUT direct surplus — e.g.
    pre-charging to make room before a strong production peak — but only when
    the full-horizon re-simulation proves the energy is, time-shifted through
    the battery, covered by otherwise-lost surplus. Pass 2 runs LATEST-FIRST
    (operator decision 2026-07-05): preemptive hours are placed as late as
    the constraints allow, because catching up on better information always
    beats an early bet on the forecast.

    Every candidate is evaluated with the energy the executor will really
    deliver (`_committed_hours`), and the saturation gate is floored at the
    nominal power so a decayed/empty feedback EMA can never weaken it.

    Loads run in parallel when surplus suffices; config order = priority when
    it does not. Every assignment is validated by re-simulation over the FULL
    horizon: no additional grid import (Z2) and the SOC buffer floor holds (Z3).
    """
    n = len(inputs.slots)
    states = {s.load_id: s for s in inputs.load_states}
    schedules: dict[str, list[bool]] = {ld.load_id: [False] * n for ld in config.loads}
    planned_wh: dict[str, float] = dict.fromkeys(schedules, 0.0)
    allocations: dict[str, list[tuple[int, int, int, float]]] = {
        ld.load_id: [] for ld in config.loads
    }
    remaining: dict[str, float | None] = {}
    for load in config.loads:
        state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
        remaining[load.load_id] = state.remaining_energy_wh(load)

    extra = [0.0] * n
    base_import = base_trajectory.total_import_wh
    buffer_floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    current = base_trajectory

    for i, slot in enumerate(inputs.slots):
        surplus = current.flows[i].grid_export_wh
        for load in config.loads:
            state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
            if not state.available or schedules[load.load_id][i]:
                continue
            power_w = state.planning_power_w(load)
            commit_h = _committed_hours(load, slot)
            power_wh = power_w * commit_h
            if power_wh <= _EPS:
                continue
            rem = remaining[load.load_id]
            if rem is not None and rem < max(power_w, load.nominal_power_w) * commit_h:
                continue  # saturated (or nearly): skip
            trial, covered = _spread_energy(extra, inputs.slots, i, power_w, commit_h)
            if any(schedules[load.load_id][j] for j, _ in covered):
                continue  # commitment overlaps an already-scheduled slot
            # Soft surplus condition (D-A4): battery may cover at most
            # `battery_tolerance` of the committed energy. Spilled slots
            # contribute their export prorated by the occupied share.
            surplus_cov = surplus + sum(
                current.flows[j].grid_export_wh * (take / inputs.slots[j].duration)
                for j, take in covered[1:]
            )
            battery_share = max(0.0, power_wh - surplus_cov) / power_wh
            if battery_share > load.battery_tolerance + _EPS:
                continue
            # Hard conditions via full re-simulation (Z2/Z3).
            traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
            if traj.total_import_wh > base_import + _EPS:
                continue
            if _degrades_min_soc(traj, current, buffer_floor):
                continue
            extra = trial
            current = traj
            for j, _ in covered:
                schedules[load.load_id][j] = True
            # Book what actually landed in the horizon (a commitment may be
            # truncated at the horizon end); the gates above deliberately
            # used the full committed energy.
            placed_wh = power_w * sum(take for _, take in covered)
            planned_wh[load.load_id] += placed_wh
            allocations[load.load_id].append((i, len(covered), 1, placed_wh))
            if rem is not None:
                remaining[load.load_id] = rem - placed_wh
            # Only the slot-local share draws on this slot's surplus; the
            # spilled share is already reflected in the re-simulated flows.
            surplus = max(0.0, surplus - power_w * covered[0][1])

    # Pass 2: objective-based preemptive hours (docs/ALGORITHM.md D-A4 v2).
    # A load may run without direct surplus when the re-simulation proves:
    # (a) grid import does not increase, (b) the buffer floor holds, and
    # (c) lost surplus drops by >= (1 - tolerance) x the load's energy —
    # i.e. the battery drain is provably refilled from would-be-lost export.
    # Energy-limited loads only get here with budget left over from pass 1
    # (saturating in the sun window is always preferred). Iterated latest-
    # first; slots after the last export can never satisfy (c), so they are
    # skipped outright, as is the whole pass on an export-free horizon.
    if current.total_export_wh > _EPS:
        last_export = max(
            (j for j, f in enumerate(current.flows) if f.grid_export_wh > _EPS),
            default=-1,
        )
        for i in range(last_export, -1, -1):
            slot = inputs.slots[i]
            for load in config.loads:
                state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
                if not state.available or schedules[load.load_id][i]:
                    continue
                power_w = state.planning_power_w(load)
                commit_h = _committed_hours(load, slot)
                power_wh = power_w * commit_h
                if power_wh <= _EPS:
                    continue
                rem = remaining[load.load_id]
                if (
                    rem is not None
                    and rem < max(power_w, load.nominal_power_w) * commit_h
                ):
                    continue
                trial, covered = _spread_energy(
                    extra, inputs.slots, i, power_w, commit_h
                )
                if any(schedules[load.load_id][j] for j, _ in covered):
                    continue
                traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
                if traj.total_import_wh > base_import + _EPS:
                    continue
                if _degrades_min_soc(traj, current, buffer_floor):
                    continue
                export_drop = current.total_export_wh - traj.total_export_wh
                if export_drop + _EPS < (1.0 - load.battery_tolerance) * power_wh:
                    continue
                extra = trial
                current = traj
                for j, _ in covered:
                    schedules[load.load_id][j] = True
                placed_wh = power_w * sum(take for _, take in covered)
                planned_wh[load.load_id] += placed_wh
                allocations[load.load_id].append((i, len(covered), 2, placed_wh))
                if rem is not None:
                    remaining[load.load_id] = rem - placed_wh

    plans = [
        LoadPlan(
            load_id=load.load_id,
            schedule=tuple(schedules[load.load_id]),
            planned_energy_wh=planned_wh[load.load_id],
            allocations=tuple(allocations[load.load_id]),
        )
        for load in config.loads
    ]
    return plans, tuple(extra), current


def appliance_windows(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    extra_ac: tuple[float, ...],
    planned_trajectory: Trajectory,
) -> dict[str, bool]:
    """Advisor (G3): could a full appliance run start now without extra import?"""
    windows: dict[str, bool] = {}
    buffer_floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    for appliance in config.appliances:
        if not appliance.opportunistic_start:
            continue
        test_inputs = insert_appliance_run(
            inputs, appliance.run_energy_wh, appliance.run_duration_h
        )
        traj = simulate(config, test_inputs, threshold, extra_ac_wh=extra_ac)
        windows[appliance.appliance_id] = (
            traj.total_import_wh <= planned_trajectory.total_import_wh + _EPS
            and not _degrades_min_soc(traj, planned_trajectory, buffer_floor)
        )
    return windows


def support_escalation(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    extra_ac: tuple[float, ...],
    trajectory: Trajectory,
) -> tuple[tuple[bool, ...], tuple[bool, ...], Trajectory]:
    """Last-resort protection (D-A9): shift DC loads to grid PSUs when the
    battery would otherwise fall through the buffer floor / hard minimum.

    Manually overridden PSUs (F-N2, `dc24_forced_on`/`dc48_forced_on`) are
    treated as permanently active: the trajectory must reflect the real
    winter operation even though the executor does not control them.
    """
    n = len(inputs.slots)
    dc24 = [False] * n
    dc48 = [False] * n
    if not config.support.configured or n == 0:
        return tuple(dc24), tuple(dc48), trajectory

    battery = config.battery
    # Deliberately the FIXED buffer (D-C8): a dynamically widened planning
    # buffer must not make the grid PSUs switch earlier/more often.
    buffer_floor = battery.soc_min_percent + config.control.support_buffer_percent
    recovery = buffer_floor + 1.0

    # A forced 48 V injection changes the whole SOC path — stage 1 must
    # judge the already-supported trajectory.
    base = trajectory
    if config.support.dc48_forced_on:
        dc48 = [True] * n
        base = simulate(
            config, inputs, threshold, extra_ac_wh=extra_ac, dc48_schedule=tuple(dc48)
        )

    # Stage 1: 24 V PSU replaces the DC/DC while SOC sits below the buffer floor.
    if config.support.dc24_forced_on:
        dc24 = [True] * n
    else:
        active = False
        for i, flow in enumerate(base.flows):
            if flow.soc_end_percent < buffer_floor:
                active = True
            elif active and flow.soc_end_percent >= recovery:
                active = False
            dc24[i] = active
    if not any(dc24):
        return tuple(dc24), tuple(dc48), base

    traj = simulate(
        config,
        inputs,
        threshold,
        extra_ac_wh=extra_ac,
        dc24_schedule=tuple(dc24),
        dc48_schedule=tuple(dc48),
    )

    # Stage 2: 48 V support PSU on top wherever the hard minimum is still hit.
    if (
        not config.support.dc48_forced_on
        and traj.min_soc_percent < battery.soc_min_percent + 0.5
    ):
        active = False
        for i, flow in enumerate(traj.flows):
            if flow.soc_end_percent < battery.soc_min_percent + 0.5:
                active = True
            elif active and flow.soc_end_percent >= buffer_floor:
                active = False
            dc48[i] = active
        if any(dc48):
            traj = simulate(
                config,
                inputs,
                threshold,
                extra_ac_wh=extra_ac,
                dc24_schedule=tuple(dc24),
                dc48_schedule=tuple(dc48),
            )

    return tuple(dc24), tuple(dc48), traj


def plan(config: SystemConfig, inputs: PlanInputs) -> PlanResult:
    """One complete planning run — single consistent trajectory out (P2)."""
    threshold, base_traj = search_threshold(config, inputs)
    load_plans, extra_ac, traj = allocate_loads(config, inputs, threshold, base_traj)
    dc24, dc48, traj = support_escalation(config, inputs, threshold, extra_ac, traj)
    windows = appliance_windows(config, inputs, threshold, extra_ac, traj)

    if traj.flows:
        max_soc = traj.max_soc_percent
        hours_to_max = (
            next(i for i, f in enumerate(traj.flows) if f.soc_end_percent >= max_soc)
            + 1
        )
        inverter_on = traj.flows[0].inverter_on
    else:
        max_soc = inputs.start_soc_percent
        hours_to_max = 0
        inverter_on = False

    return PlanResult(
        threshold_percent=threshold,
        inverter_on=inverter_on,
        trajectory=traj,
        load_plans=tuple(load_plans),
        appliance_windows=windows,
        support_dc24_now=bool(dc24[0]) if dc24 else False,
        support_dc48_now=bool(dc48[0]) if dc48 else False,
        grid_import_kwh=traj.total_import_wh / 1000.0,
        grid_export_kwh=traj.total_export_wh / 1000.0,
        lost_surplus_kwh=traj.total_export_wh / 1000.0,
        min_soc_percent=traj.min_soc_percent
        if traj.flows
        else inputs.start_soc_percent,
        max_soc_percent=max_soc,
        hours_to_max_soc=hours_to_max,
    )
