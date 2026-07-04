"""Planner: threshold search, surplus allocation, appliance advisor, support
escalation. Implements docs/ALGORITHM.md §1 with decisions D-A1..D-A9."""

from __future__ import annotations

import math

from .model import (
    LoadPlan,
    PlanInputs,
    PlanResult,
    SurplusLoad,
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


def allocate_loads(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    base_trajectory: Trajectory,
) -> tuple[list[LoadPlan], tuple[float, ...], Trajectory]:
    """Assign surplus loads to hours in two passes.

    Pass 1 fills hours with direct surplus (per-slot battery share within the
    load's tolerance). Pass 2 ("zielbasiert", decision 2026-07-04) additionally
    allows hours WITHOUT direct surplus — e.g. pre-dawn charging to make room
    before a strong production peak — but only when the full-horizon
    re-simulation proves the energy is, time-shifted through the battery,
    covered by otherwise-lost surplus.

    Loads run in parallel when surplus suffices; config order = priority when
    it does not. Every assignment is validated by re-simulation over the FULL
    horizon: no additional grid import (Z2) and the SOC buffer floor holds (Z3).
    """
    n = len(inputs.slots)
    states = {s.load_id: s for s in inputs.load_states}
    schedules: dict[str, list[bool]] = {ld.load_id: [False] * n for ld in config.loads}
    planned_wh: dict[str, float] = dict.fromkeys(schedules, 0.0)
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
            if not state.available:
                continue
            power_wh = state.planning_power_w(load) * slot.duration
            if power_wh <= _EPS:
                continue
            rem = remaining[load.load_id]
            if rem is not None and rem < power_wh:
                continue  # saturated (or nearly): skip
            # Soft surplus condition (D-A4): battery may cover at most
            # `battery_tolerance` of the load's energy in this slot.
            battery_share = max(0.0, power_wh - surplus) / power_wh
            if battery_share > load.battery_tolerance + _EPS:
                continue
            # Hard conditions via full re-simulation (Z2/Z3).
            trial = list(extra)
            trial[i] += power_wh
            traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
            if traj.total_import_wh > base_import + _EPS:
                continue
            if _degrades_min_soc(traj, current, buffer_floor):
                continue
            extra = trial
            current = traj
            schedules[load.load_id][i] = True
            planned_wh[load.load_id] += power_wh
            if rem is not None:
                remaining[load.load_id] = rem - power_wh
            surplus = max(0.0, surplus - power_wh)

    # Pass 2: objective-based preemptive hours (docs/ALGORITHM.md D-A4 v2).
    # A load may run without direct surplus when the re-simulation proves:
    # (a) grid import does not increase, (b) the buffer floor holds, and
    # (c) lost surplus drops by >= (1 - tolerance) x the load's energy —
    # i.e. the battery drain is provably refilled from would-be-lost export.
    # Energy-limited loads only get here with budget left over from pass 1
    # (saturating in the sun window is always preferred).
    for i, slot in enumerate(inputs.slots):
        for load in config.loads:
            state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
            if not state.available or schedules[load.load_id][i]:
                continue
            power_wh = state.planning_power_w(load) * slot.duration
            if power_wh <= _EPS:
                continue
            rem = remaining[load.load_id]
            if rem is not None and rem < power_wh:
                continue
            trial = list(extra)
            trial[i] += power_wh
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
            schedules[load.load_id][i] = True
            planned_wh[load.load_id] += power_wh
            if rem is not None:
                remaining[load.load_id] = rem - power_wh

    plans = [
        LoadPlan(
            load_id=load.load_id,
            schedule=tuple(schedules[load.load_id]),
            planned_energy_wh=planned_wh[load.load_id],
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
    battery would otherwise fall through the buffer floor / hard minimum."""
    n = len(inputs.slots)
    dc24 = [False] * n
    dc48 = [False] * n
    if not config.support.configured or n == 0:
        return tuple(dc24), tuple(dc48), trajectory

    battery = config.battery
    buffer_floor = battery.soc_min_percent + config.control.soc_buffer_percent
    recovery = buffer_floor + 1.0

    # Stage 1: 24 V PSU replaces the DC/DC while SOC sits below the buffer floor.
    active = False
    for i, flow in enumerate(trajectory.flows):
        if flow.soc_end_percent < buffer_floor:
            active = True
        elif active and flow.soc_end_percent >= recovery:
            active = False
        dc24[i] = active
    if not any(dc24):
        return tuple(dc24), tuple(dc48), trajectory

    traj = simulate(
        config, inputs, threshold, extra_ac_wh=extra_ac, dc24_schedule=tuple(dc24)
    )

    # Stage 2: 48 V support PSU on top wherever the hard minimum is still hit.
    if traj.min_soc_percent < battery.soc_min_percent + 0.5:
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
        min_soc_percent=traj.min_soc_percent if traj.flows else inputs.start_soc_percent,
        max_soc_percent=max_soc,
        hours_to_max_soc=hours_to_max,
    )
