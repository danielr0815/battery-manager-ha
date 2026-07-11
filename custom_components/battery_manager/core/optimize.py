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


def pv_windows(inputs: PlanInputs, cutoff_w: float, end_hour: int | None) -> dict:
    """Per calendar day, the [first, last] slot index of strong PV production.

    A slot is "strong" when its average power (`pv_wh / duration`) reaches
    `strong_pv_cutoff_w`. The window frames the hours during which the UPPER
    buffer (absorption headroom near max SOC) must be preserved (F-PREDRAIN F4,
    operator requirement L6): after the last strong slot the sun has moved
    behind the house, so the reserve may be spent. Derived from the slot PV
    series, so it works in both hourly and daily/two-window forecast modes.
    `pv_window_end_hour` (site override) caps the end at the last slot starting
    before that local hour. A day with no strong slot has no window — its
    night/cloudy slots can only ever book via the nominal opportunity gate (c1).
    """
    windows: dict = {}
    for i, slot in enumerate(inputs.slots):
        if slot.duration <= 0.0:
            continue
        if slot.pv_wh / slot.duration >= cutoff_w:
            day = slot.start.date()
            first, last = windows.get(day, (i, i))
            windows[day] = (min(first, i), max(last, i))
    if end_hour is None:
        return windows
    capped: dict = {}
    for day, (first, last) in windows.items():
        cap_idx = None
        for i, slot in enumerate(inputs.slots):
            if slot.start.date() == day and slot.hour_of_day < end_hour:
                cap_idx = i
        if cap_idx is None or cap_idx < first:
            continue  # the whole window sits at/after the override hour
        capped[day] = (first, min(last, cap_idx))
    return capped


def _recovery_index(windows: dict, i: int, n: int) -> int:
    """End slot of the pre-drain's "bet window" that starts at slot `i`.

    A pre-drain at slot `i` is a bet that the battery refills from the NEXT
    production before the reserve is exhausted (F-PREDRAIN §3.3 v2). The bet is
    settled at the end of the first PV window whose end is at or after `i` — the
    same-day window for an in-window slot, or the next morning's window for a
    night slot. `pv_windows()` is index-based and its per-day windows are ordered
    and non-overlapping, so the earliest such window is simply the smallest end
    index that is >= `i`. With no strong-PV window ahead (e.g. a cloudy tail) the
    bet only settles at the horizon end.
    """
    return min(
        (last for (_first, last) in windows.values() if last >= i), default=n - 1
    )


def _windowed_min_soc(traj: Trajectory, lo: int, hi: int) -> float:
    """Lowest end-of-slot SOC over the inclusive slot range [lo, hi]."""
    return min(traj.flows[j].soc_end_percent for j in range(lo, hi + 1))


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


def _quantised_hours(load, slot) -> list[float]:
    """Candidate commit durations for one (load, slot), LARGEST first.

    The FIRST candidate is always `_committed_hours` — the whole-slot / dwell
    floor — so a load that fits a full slot books exactly as before (the
    regression anchor: if the whole slot clears every gate it is chosen and the
    plan is bit-identical to the pre-F-SUBHOUR behaviour). Both load classes then
    offer SHORTER runs quantised to `min_runtime_min` (>= one quantum, never
    less — F-SUBHOUR R2), so a small surplus the battery buffers within the hour,
    or an energy-limited residual below one nominal hour, can still be captured
    at a later slot instead of defaulting to slot-0 geometry (F-RESIDUAL-TOPUP
    R1). Energy-limited loads share the same candidate list: their level-driven
    target-SOC stop stays primary, and the executor now caps a sub-hour booking
    with the same frozen off-deadline as a continuous load (F-RESIDUAL-TOPUP R7),
    so the removed "no sub-hour cap" carve-out no longer risks an over-run.
    """
    whole = _committed_hours(load, slot)
    q = load.min_runtime_min / 60.0
    if q <= _EPS:
        return [whole]
    candidates = [whole]
    k = int((whole - _EPS) / q)  # largest k with k*q < whole
    while k >= 1:
        d = k * q
        if d < whole - _EPS:
            candidates.append(d)
        k -= 1
    return candidates


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
    tolerance across the committed runtime), LOAD-OUTER in config order
    (F-PLANNER-HONESTY R7): a load books its complete pass-1 allocation before
    the next load sees the horizon, and ALL loads walk the slots ascending
    (earliest-export-first, F-RESCUE-EXPORT). A pass-1 candidate passes the
    soft-surplus gate only where the battery is already full and exporting, so
    lateness rescues no extra energy but loses the present, certain surplus to
    a later forecast bet: run as soon as export occurs. Pass 2 ("zielbasiert",
    decision 2026-07-04) additionally allows
    hours WITHOUT direct surplus — e.g. pre-charging to make room before a
    strong production peak — but only when the full-horizon re-simulation
    proves the energy is, time-shifted through the battery, covered by
    otherwise-lost surplus. Pass 2 runs LATEST-FIRST (operator decision
    2026-07-05): preemptive hours are placed as late as the constraints allow —
    there the battery can still buffer, so deferring the bet is legitimate —
    because catching up on better information beats an early bet on the
    forecast.

    Every candidate is evaluated with the energy the executor will really
    deliver (`_committed_hours`), and the saturation gate is floored at the
    nominal power so a decayed/empty feedback EMA can never weaken it.

    Loads run in parallel when surplus suffices; config order = priority when
    it does not (order = the configured per-load priority since v0.8.2, default
    creation order, F-LOAD-PRIORITY). Every assignment is validated by
    re-simulation over the FULL horizon: no additional grid import (Z2) and the
    SOC buffer floor holds (Z3).
    """
    n = len(inputs.slots)
    states = {s.load_id: s for s in inputs.load_states}
    schedules: dict[str, list[bool]] = {ld.load_id: [False] * n for ld in config.loads}
    run_h: dict[str, list[float]] = {ld.load_id: [0.0] * n for ld in config.loads}
    planned_wh: dict[str, float] = dict.fromkeys(schedules, 0.0)
    allocations: dict[str, list[tuple[int, int, int, float]]] = {
        ld.load_id: [] for ld in config.loads
    }
    # Explain-plan (F-PLANNER-HONESTY R12/R13): one reason string per
    # allocation entry, recorded at acceptance time — the only moment the
    # planner knows WHY a booking passed its gates.
    reasons: dict[str, list[str]] = {ld.load_id: [] for ld in config.loads}
    remaining: dict[str, float | None] = {}
    for load in config.loads:
        state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
        remaining[load.load_id] = state.remaining_energy_wh(load)

    extra = [0.0] * n
    control = config.control
    ratio = control.import_trade_ratio
    alpha = control.predrain_pv_confidence
    beta = control.upper_pv_reserve
    base_import = base_trajectory.total_import_wh
    base_export = base_trajectory.total_export_wh
    buffer_floor = config.battery.soc_min_percent + control.soc_buffer_percent
    # Z4 protects the INVERTER cutoff (L2), not the storage minimum, so its
    # floor differs from Z3's `soc_min + buffer` (F-PREDRAIN §3.3).
    stress_floor = control.inverter_min_soc_percent + control.soc_buffer_percent
    windows = pv_windows(inputs, control.strong_pv_cutoff_w, control.pv_window_end_hour)
    current = base_trajectory

    def import_ok(load, traj: Trajectory, current_import: float) -> bool:
        """Z2' import gate. Energy-limited loads keep the strict no-extra-import
        rule (L5 keeps them out of the pre-drain machinery), but anchored at the
        CURRENTLY ACCEPTED series rather than the no-loads base (FIX-2): a booking
        must not ADD import over what is already accepted, so once a continuous
        load has traded some import an energy-limited candidate on pure surplus is
        no longer starved by that inherited delta. Continuous loads use the
        cumulative trade invariant against the no-loads base: a small import is
        allowed only in exchange for rescued export at `import_trade_ratio`, plus
        1 Wh of slack ONLY when a positive ratio is configured (FIX-6) so a lone
        standby artifact never vetoes a traded run (L1). At ratio 0.0 there is no
        slack, so the continuous gate is `trial import <= base + _EPS` exactly as
        v0.7.19 — night pre-drains still need a positive ratio to book."""
        if load.energy_limited:
            return traj.total_import_wh <= current_import + _EPS
        allowed = ratio * (base_export - traj.total_export_wh) + (
            1.0 if ratio > 0.0 else 0.0
        )
        return traj.total_import_wh - base_import <= allowed + _EPS

    def in_window(i: int) -> bool:
        w = windows.get(inputs.slots[i].start.date())
        return w is not None and w[0] <= i <= w[1]

    # Pass 1 — direct-surplus hours, LOAD-OUTER in config order (F-PLANNER-
    # HONESTY R7): strict priority — a load books its complete pass-1
    # allocation before the next load sees the horizon. Slots are walked
    # ASCENDING (earliest-export-first) for ALL loads (F-RESCUE-EXPORT R1,
    # supersedes the v0.9.0 day-bounded latest-first for energy-limited loads):
    # a pass-1 candidate passes the soft-surplus gate only where the battery is
    # already full and EXPORTING, so lateness buys nothing — surplus not
    # consumed in a slot is lost irrevocably, and an energy-limited load
    # charges its fixed remaining capacity either way. Deferring past a slot
    # that already exports would lose that present, certain surplus to bet on a
    # later forecast one; so a load must run as soon as export occurs. (Pass 2
    # stays latest-first: there the battery can still buffer, so deferring the
    # preemptive bet is legitimate.) Each candidate reads the CURRENT accepted
    # trajectory's export (R8): earlier bookings — same load or a higher-
    # priority one — are already re-simulated into `current`, so the old
    # intra-slot decrement approximation is replaced by the exact value.
    for load in config.loads:
        state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
        if not state.available:
            continue
        power_w = state.planning_power_w(load)
        for i in range(n):
            slot = inputs.slots[i]
            if schedules[load.load_id][i]:
                continue
            rem = remaining[load.load_id]
            # Try the largest quantised run first, falling back to shorter
            # min_runtime multiples so a small battery-buffered surplus can still
            # be captured (F-SUBHOUR R1-R3). The whole-slot candidate is first,
            # so a full-hour placement stays bit-identical to the old behaviour.
            for commit_h in _quantised_hours(load, slot):
                power_wh = power_w * commit_h
                if power_wh <= _EPS:
                    continue
                if (
                    rem is not None
                    and rem < max(power_w, load.nominal_power_w) * commit_h
                ):
                    continue  # saturated (or nearly): skip
                trial, covered = _spread_energy(
                    extra, inputs.slots, i, power_w, commit_h
                )
                if any(schedules[load.load_id][j] for j, _ in covered):
                    continue  # commitment overlaps an already-scheduled slot
                # Soft surplus condition (D-A4): battery may cover at most
                # `battery_tolerance` of the committed energy. Spilled slots
                # contribute their export prorated by the occupied share.
                surplus_cov = current.flows[i].grid_export_wh + sum(
                    current.flows[j].grid_export_wh * (take / inputs.slots[j].duration)
                    for j, take in covered[1:]
                )
                battery_share = max(0.0, power_wh - surplus_cov) / power_wh
                if battery_share > load.battery_tolerance + _EPS:
                    continue
                # Hard conditions via full re-simulation (Z2'/Z3).
                traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
                if not import_ok(load, traj, current.total_import_wh):
                    continue
                if _degrades_min_soc(traj, current, buffer_floor):
                    continue
                extra = trial
                current = traj
                for j, take in covered:
                    schedules[load.load_id][j] = True
                    run_h[load.load_id][j] = take
                # Book what actually landed in the horizon (a commitment may be
                # truncated at the horizon end); the gates above deliberately
                # used the full committed energy.
                placed_h = sum(take for _, take in covered)
                placed_wh = power_w * placed_h
                planned_wh[load.load_id] += placed_wh
                allocations[load.load_id].append((i, len(covered), 1, placed_wh))
                reasons[load.load_id].append(
                    f"pass 1 @ {slot.start.strftime('%m-%d %H:%M')}: "
                    f"direct surplus, {round(placed_h * 60)} min x "
                    f"{round(power_w)} W, battery share {round(battery_share * 100)}%"
                )
                if rem is not None:
                    remaining[load.load_id] = rem - placed_wh
                break  # placed the largest feasible quantum; done with this slot

    # Pass 2: objective-based preemptive hours (docs/ALGORITHM.md D-A4 v2,
    # two-buffer pre-drain F-PREDRAIN §3). A load may run without direct surplus
    # when the re-simulation proves it is safe AND worthwhile:
    #   Z2' import trade   — import stays within the trade invariant (F2),
    #   Z3  buffer floor   — nominal min SOC not degraded below soc_min+buffer,
    #   Z4  lower buffer   — even a pessimistic (alpha) PV run keeps the inverter
    #                        reserve above its floor across the bet's recovery
    #                        window [i, recovery] (continuous loads only, F3 v2),
    #   (c) opportunity    — (c1) the nominal drain is refilled from lost export,
    #                        OR (c2) inside the day's PV window an optimistic
    #                        (beta) run would be (upper-buffer insurance, F4).
    # Energy-limited loads stay on the legacy nominal-only path (a1/c1) and never
    # night-charge from the house battery (L5). Iterated latest-first (L4); slots
    # after the last export can never satisfy the gate, so they are skipped, as
    # is the whole pass on an export-free horizon.
    if current.total_export_wh > _EPS:
        # Optimistic (beta) opportunity baseline for the CURRENTLY accepted
        # series — whole-horizon, kept in step with `current` and refreshed only
        # on acceptance; skipped when the (c2) gate is neutral.
        current_beta = (
            simulate(config, inputs, threshold, extra_ac_wh=tuple(extra), pv_scale=beta)
            if beta != 1.0
            else None
        )
        # Z4 (v2) is WINDOWED, so it needs no whole-horizon stress baseline. For
        # each bet window we cache the currently accepted series' windowed stressed
        # min over [i, hi]; the cache is invalidated whenever an acceptance changes
        # `extra`. Keyed by (i, hi) because the window end depends on the candidate
        # duration's spill past recovery (FIX-7), and each (i, hi) is rebuilt lazily.
        stress_base: dict[tuple[int, int], float] = {}
        last_export = max(
            (j for j, f in enumerate(current.flows) if f.grid_export_wh > _EPS),
            default=-1,
        )
        for i in range(last_export, -1, -1):
            slot = inputs.slots[i]
            # Bet window [i, recovery]: alpha stresses ONLY this stretch — the
            # drain until the battery refills from the next production — so a night
            # pre-drain is judged on its own recovery, not vetoed by an unrelated
            # later dip, and a sound in-window pre-charge is not punished by a
            # globally scaled-down horizon (the v1 whole-horizon failure).
            recovery = _recovery_index(windows, i, n)
            for load in config.loads:
                state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
                if not state.available or schedules[load.load_id][i]:
                    continue
                power_w = state.planning_power_w(load)
                rem = remaining[load.load_id]
                # Largest-first quantised search (F-SUBHOUR): a sub-hour
                # preemptive run needs only export_drop >= (1-tol)*(k*q) energy,
                # so a small afternoon dribble a whole hour cannot capture may
                # still be soaked by a min_runtime chunk.
                for commit_h in _quantised_hours(load, slot):
                    power_wh = power_w * commit_h
                    if power_wh <= _EPS:
                        continue
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
                    trial_tuple = tuple(trial)
                    traj = simulate(config, inputs, threshold, extra_ac_wh=trial_tuple)
                    if not import_ok(load, traj, current.total_import_wh):  # Z2'
                        continue
                    if _degrades_min_soc(traj, current, buffer_floor):  # Z3
                        continue
                    export_drop = current.total_export_wh - traj.total_export_wh
                    need = (1.0 - load.battery_tolerance) * power_wh
                    trial_beta = None
                    via_beta = False  # which gate accepted -> reason string (R13)
                    if load.energy_limited:
                        # Legacy nominal refill gate only (no two-buffer machinery).
                        if export_drop + _EPS < need:
                            continue
                    else:
                        # (c1) nominal refill OR (c2) optimistic in-window insurance.
                        accept = export_drop + _EPS >= need
                        if not accept and beta != 1.0 and in_window(i):
                            trial_beta = simulate(
                                config,
                                inputs,
                                threshold,
                                extra_ac_wh=trial_tuple,
                                pv_scale=beta,
                            )
                            drop_beta = (
                                current_beta.total_export_wh
                                - trial_beta.total_export_wh
                            )
                            accept = drop_beta + _EPS >= need
                            via_beta = accept
                        if not accept:
                            continue
                        # Z4 windowed lower-buffer stress gate (§3.3 v2): stress
                        # PV by alpha only across the bet window [i, recovery] and
                        # take the windowed min. Reject iff that stressed reserve
                        # both breaks the inverter floor AND is worse than the same
                        # windowed min on the currently accepted series — a dip the
                        # baseline already contains does not veto the bet.
                        if alpha != 1.0:
                            # Extend the stress window past `recovery` when this
                            # candidate's run spills beyond it (a min-runtime
                            # commitment near the window end lands in later slots):
                            # the spill drains the reserve too, so it must be
                            # stressed and included in the windowed min (FIX-7).
                            hi = max(recovery, covered[-1][0])
                            scale_vec = [
                                alpha if i <= j <= hi else 1.0 for j in range(n)
                            ]
                            trial_stress = simulate(
                                config,
                                inputs,
                                threshold,
                                extra_ac_wh=trial_tuple,
                                pv_scale=scale_vec,
                            )
                            trial_wmin = _windowed_min_soc(trial_stress, i, hi)
                            key = (i, hi)
                            if key not in stress_base:
                                base_stress = simulate(
                                    config,
                                    inputs,
                                    threshold,
                                    extra_ac_wh=tuple(extra),
                                    pv_scale=scale_vec,
                                )
                                stress_base[key] = _windowed_min_soc(base_stress, i, hi)
                            if (
                                trial_wmin < stress_floor - _EPS
                                and trial_wmin < stress_base[key] - _EPS
                            ):
                                continue
                    extra = trial
                    current = traj
                    stress_base.clear()  # `extra` changed -> windowed baselines stale
                    if beta != 1.0:
                        current_beta = (
                            trial_beta
                            if trial_beta is not None
                            else simulate(
                                config,
                                inputs,
                                threshold,
                                extra_ac_wh=tuple(extra),
                                pv_scale=beta,
                            )
                        )
                    for j, take in covered:
                        schedules[load.load_id][j] = True
                        run_h[load.load_id][j] = take
                    placed_wh = power_w * sum(take for _, take in covered)
                    planned_wh[load.load_id] += placed_wh
                    allocations[load.load_id].append((i, len(covered), 2, placed_wh))
                    # "latest feasible slot" is structurally true: pass 2 walks
                    # descending and accepts the first slot that passes (R13).
                    reasons[load.load_id].append(
                        f"pass 2 @ {slot.start.strftime('%m-%d %H:%M')}: "
                        + (
                            "in-window insurance (beta), latest feasible slot"
                            if via_beta
                            else (
                                f"covered by otherwise-lost export "
                                f"({round(export_drop)} Wh), latest feasible slot"
                            )
                        )
                    )
                    if rem is not None:
                        remaining[load.load_id] = rem - placed_wh
                    break

    plans = [
        LoadPlan(
            load_id=load.load_id,
            schedule=tuple(schedules[load.load_id]),
            planned_energy_wh=planned_wh[load.load_id],
            allocations=tuple(allocations[load.load_id]),
            run_hours=tuple(run_h[load.load_id]),
            reasons=tuple(reasons[load.load_id]),
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
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
) -> dict[str, bool]:
    """Advisor (G3): could a full appliance run start now without extra import?

    The hypothetical run is evaluated under the SAME support-PSU schedules as
    the planned trajectory it is compared against — otherwise the advisor
    simulates the run with the PSUs off (their default) while the baseline had
    them on, and gives false window advisories whenever support is active
    (e.g. winter operation with a forced 48 V PSU).
    """
    windows: dict[str, bool] = {}
    buffer_floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    for appliance in config.appliances:
        if not appliance.opportunistic_start:
            continue
        test_inputs = insert_appliance_run(
            inputs, appliance.run_energy_wh, appliance.run_duration_h
        )
        traj = simulate(
            config,
            test_inputs,
            threshold,
            extra_ac_wh=extra_ac,
            dc24_schedule=dc24_schedule,
            dc48_schedule=dc48_schedule,
        )
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

    control = config.control
    # Grid-support escalation thresholds are ABSOLUTE battery SOC % (D-A9),
    # deliberately independent of the planning buffer (D-C8): a dynamically
    # widened planning buffer must not make the grid PSUs switch earlier/more
    # often. Each stage is a hysteresis loop (ON below activate, OFF at/above
    # recovery); a wider activate->recovery gap latches a PSU on longer so an
    # SOC parked near a threshold holds steadily instead of chattering.
    dc24_activate = control.support_dc24_activate_soc
    dc24_recovery = control.support_dc24_recovery_soc
    dc48_activate = control.support_dc48_activate_soc
    dc48_recovery = control.support_dc48_recovery_soc

    # A forced 48 V injection changes the whole SOC path — stage 1 must
    # judge the already-supported trajectory.
    base = trajectory
    if config.support.dc48_forced_on:
        dc48 = [True] * n
        base = simulate(
            config, inputs, threshold, extra_ac_wh=extra_ac, dc48_schedule=tuple(dc48)
        )

    # Stage 1: 24 V PSU replaces the DC/DC while SOC sits below its activate SOC.
    if config.support.dc24_forced_on:
        dc24 = [True] * n
    else:
        active = False
        for i, flow in enumerate(base.flows):
            if flow.soc_end_percent < dc24_activate:
                active = True
            elif active and flow.soc_end_percent >= dc24_recovery:
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

    # Stage 2: 48 V support PSU on top wherever SOC sits below its activate SOC.
    if not config.support.dc48_forced_on and traj.min_soc_percent < dc48_activate:
        active = False
        for i, flow in enumerate(traj.flows):
            if flow.soc_end_percent < dc48_activate:
                active = True
            elif active and flow.soc_end_percent >= dc48_recovery:
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
    """One complete planning run — single consistent trajectory out (P2).

    The `stressed_min_soc_percent` diagnostic (§3.5, v2) reports the WINDOWED
    lower-buffer reserve that the Z4 gate actually protects: the earliest pass-2
    slot booked for a CONTINUOUS load is treated as the deepest bet, and the
    diagnostic is the stressed (alpha) windowed min SOC over that bet's recovery
    window [i0, recovery] under the FINAL accepted series. It is None when the
    stress gate is off (alpha == 1.0) or when no continuous load has a pass-2
    booking (nothing was pre-drained, so there is no reserve bet to report).
    """
    control = config.control
    threshold, base_traj = search_threshold(config, inputs)
    load_plans, extra_ac, traj = allocate_loads(config, inputs, threshold, base_traj)
    # Capture the allocation trajectory BEFORE support escalation: the import
    # trade is a property of the load allocation, not of the last-resort PSUs.
    alloc_traj = traj
    dc24, dc48, traj = support_escalation(config, inputs, threshold, extra_ac, traj)
    windows = appliance_windows(
        config,
        inputs,
        threshold,
        extra_ac,
        traj,
        dc24_schedule=dc24,
        dc48_schedule=dc48,
    )

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

    # F-PREDRAIN diagnostics (§3.5): the traded import, the stressed reserve, and
    # the derived PV absorption windows (WP4 exposes these as sensor attributes).
    import_trade_used_wh = max(
        0.0, alloc_traj.total_import_wh - base_traj.total_import_wh
    )
    stressed_min_soc: float | None = None
    alpha = control.predrain_pv_confidence
    if alpha != 1.0 and alloc_traj.flows:
        # Windowed stressed reserve of the deepest bet: the earliest pass-2 slot
        # booked for a continuous (non-energy-limited) load, evaluated over its
        # recovery window under the final series (§3.5 v2). None when nothing was
        # pre-drained for a continuous load.
        n = len(inputs.slots)
        cont_ids = {ld.load_id for ld in config.loads if not ld.energy_limited}
        booked = [
            alloc[0]
            for lp in load_plans
            if lp.load_id in cont_ids
            for alloc in lp.allocations
            if alloc[2] == 2
        ]
        if booked:
            i0 = min(booked)
            # Own local: reusing `windows` here would clobber the appliance
            # advisory dict computed above, corrupting PlanResult.appliance_windows
            # whenever a pre-drain books (FIX-1).
            pv_win = pv_windows(
                inputs, control.strong_pv_cutoff_w, control.pv_window_end_hour
            )
            recovery = _recovery_index(pv_win, i0, n)
            scale_vec = [alpha if i0 <= j <= recovery else 1.0 for j in range(n)]
            stressed = simulate(
                config, inputs, threshold, extra_ac_wh=extra_ac, pv_scale=scale_vec
            )
            stressed_min_soc = _windowed_min_soc(stressed, i0, recovery)
    window_ends = {
        day.isoformat(): inputs.slots[last].hour_of_day
        for day, (_first, last) in pv_windows(
            inputs, control.strong_pv_cutoff_w, control.pv_window_end_hour
        ).items()
    }

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
        import_trade_used_wh=import_trade_used_wh,
        stressed_min_soc_percent=stressed_min_soc,
        pv_window_ends=window_ends,
    )
