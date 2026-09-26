"""Load allocation and recovery, independent of threshold selection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from .allocation_candidates import AllocationCandidate, AllocationContext
from .model import (
    STORAGE_TARGET_TOLERANCE_WH,
    LoadPlan,
    PlanInputs,
    SurplusLoadState,
    SystemConfig,
    Trajectory,
)
from .planning_rules import (
    _EPS,
    AT_MAX_TOPUP_BAND_PERCENT,
    IMPORT_ARTIFACT_SLACK_WH,
    PREDRAIN_PEAK_TOLERANCE_PERCENT,
    _add_path_overheads,
    _crossday_daytime_bet,
    _degrades_min_soc,
    _effective_load_power_w,
    _final_note,
    _quantised_hours,
    _ramped_stress_floors,
    _refill_index,
    _respects_cumulative_energy_cap,
    _respects_path_power_limits,
    _saturation_power_w,
    _slot_serviceable,
    _spread_energy,
    _windowed_min_soc,
    _z4_reject,
    pv_windows,
)
from .policy import FULL_SOC_TOLERANCE_PERCENT
from .simulate import simulate
from .uncertainty import (
    effective_uncertainty as _effective_uncertainty,
)
from .uncertainty import (
    quantile_band_slots as quantile_band_slots,
)


def allocate_loads(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    base_trajectory: Trajectory,
    *,
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
    direct_surplus_only_load_ids: frozenset[str] = frozenset(),
    remaining_energy_overrides: dict[str, float] | None = None,
    power_caps_w: dict[str, float] | None = None,
    path_power_limits: tuple[tuple[tuple[tuple[str, float], ...], float], ...] = (),
    cumulative_energy_caps_wh: dict[str, tuple[float, ...]] | None = None,
    recovery_cumulative_caps_wh: dict[str, tuple[float, ...]] | None = None,
    path_overheads: tuple[tuple[tuple[str, ...], float], ...] = (),
) -> tuple[list[LoadPlan], tuple[float, ...], Trajectory]:
    """Assign surplus loads to hours in three passes.

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

    Manually forced support paths are known exogenous inputs, so the caller
    supplies their fixed schedules here.  Every candidate and gate then sees
    the same winter-support trajectory as the published forecast; automatic
    emergency escalation remains a later planning stage.

    ``direct_surplus_only_load_ids`` is an internal cascade boundary: those
    storage inputs may consume only residual export in every covered slot.
    They cannot use the normal battery-tolerance or pass-2 preconditioning
    paths, so charging a cascade member never drains the house battery.

    ``remaining_energy_overrides`` makes cascade storage demand include its
    member-specific charge efficiency. ``power_caps_w`` applies physical hard
    caps before energy allocation, saturation checks and downstream cascade
    accounting consume the resulting effective power.  The two cumulative-cap
    vectors make future Aux discharge time-causal: normal top-up and the 50 %
    recovery phase may only book charge energy after the corresponding physical
    headroom exists.

    Loads run in parallel when surplus suffices; config order = priority when
    it does not (order = the configured per-load priority since v0.8.2, default
    creation order, F-LOAD-PRIORITY). Every assignment is validated by
    re-simulation over the FULL horizon: no additional grid import (Z2) and the
    SOC buffer floor holds (Z3).

    GATE PARITY (F-GATE-PARITY, operator decision 2026-07-17): both load
    classes face the IDENTICAL gate set — one Z2' trade invariant, c1-rt/c2
    opportunity gates and the Z4 stress floor — so the priority order alone
    decides who gets contested energy.  The cascade wrapper deliberately
    presents its terminal before additional member charging, while ordinary
    loads retain their configured order. Since F-PREDRAIN-BLOCK
    (v0.19.0) pass 2 bets are ENERGY-LIMITED ONLY (incl. the daylight rule:
    they never book zero-PV night slots): continuous loads no longer bet
    slot-wise at all — pass 3 books their pre-drain as ONE contiguous block
    ending at today's SOC peak, which the executor only switches after
    PREDRAIN_BLOCK_STABLE_PLANS identical plans (the 2026-08-01 flicker run:
    a marginal slot-0 bet oscillated within minutes and produced a 19-min
    battery run nobody needed).
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
    rejected: dict[str, dict[int, str]] = {ld.load_id: {} for ld in config.loads}
    remaining: dict[str, float | None] = {}
    for load in config.loads:
        state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
        remaining[load.load_id] = (
            remaining_energy_overrides[load.load_id]
            if remaining_energy_overrides is not None
            and load.load_id in remaining_energy_overrides
            else state.remaining_energy_wh(load)
        )

    extra = [0.0] * n
    # Slots carrying ANY accepted booking (any load) — the set slots_serviceable
    # re-validates on every trial (F-STRICT-SURPLUS R2 ratchet closure).
    booked_any = [False] * n
    control = config.control
    alpha = control.predrain_pv_confidence
    beta = control.upper_pv_reserve
    # F-QUANTILE-BANDS R3/R4: per-slot stress/optimism vectors, composed ONCE.
    # Band-covered slots run on empirical P10/P90 evidence; everything else on
    # the scalar dials — the c2 machinery engages iff ANY slot is optimistic,
    # the Z4 stress iff ANY slot is pessimistic (replaces the beta!=1/alpha!=1
    # scalar guards; uniform fallback vectors keep both decisions identical).
    stress_vec, optimism_vec, band_slots = _effective_uncertainty(inputs, alpha, beta)
    c2_active = any(o > 1.0 + _EPS for o in optimism_vec)
    z4_active = any(s < 1.0 - _EPS for s in stress_vec)
    base_import = base_trajectory.total_import_wh
    buffer_floor = config.battery.soc_min_percent + control.soc_buffer_percent
    # "Full" sentinel for the refill-settled bet window (F-STRICT-SURPLUS R3)
    # and the reach-max invariant (R5), same tolerance as the merge probe.
    soc_full = config.battery.soc_max_percent - FULL_SOC_TOLERANCE_PERCENT
    # F-STRICT-SURPLUS R5 (operator 2026-07-19): pre-conditioning (a pass-2
    # pre-drain to make room before a coming surplus) stays welcome, but the
    # plan must STILL reach soc_max on every day the no-loads base reaches it —
    # a bet that stops the battery filling to max on a day it otherwise would
    # (the 2026-07-19 card: peak 77 % instead of 95 %) is not pre-conditioning,
    # it robs the fill. Pre-draining for a FUTURE clip stays legal: it lowers a
    # non-max day and the target clip day still reaches max. Grouped by
    # planner-local start day; a day reaches max iff any of its slots ends at
    # soc_full.
    day_slots: dict = {}
    for _idx, _slot in enumerate(inputs.slots):
        day_slots.setdefault(_slot.start.date(), []).append(_idx)
    base_max_days = {
        day
        for day, idxs in day_slots.items()
        if any(base_trajectory.flows[j].soc_end_percent >= soc_full for j in idxs)
    }
    # AC->battery->AC round-trip factor (F-NIGHT-RESCUE R1): the efficiency a
    # pure battery detour physically has in the simulator's own chain
    # (charger in, battery in/out, inverter out; live ~0.822). Clamped (0, 1].
    rt = min(
        1.0,
        max(
            _EPS,
            config.charger.eta
            * config.battery.eta_charge
            * config.battery.eta_discharge
            * config.inverter.eta,
        ),
    )
    # Z4 protects the INVERTER cutoff (L2), not the storage minimum, so its
    # floor differs from Z3's `soc_min + buffer` (F-PREDRAIN §3.3). Since
    # F-NIGHT-RESCUE R8 the BUFFER component ramps per candidate slot with the
    # remaining stressed deficit until the stressed PV crossover; Z3 stays
    # static (absolute battery protection).
    stress_floor_by_slot = _ramped_stress_floors(config, inputs, stress_vec)
    windows = pv_windows(inputs, control.strong_pv_cutoff_w, control.pv_window_end_hour)
    current = base_trajectory

    def import_ok(traj: Trajectory) -> bool:
        """Z2'' hard import gate — ONE cumulative invariant for ALL load
        classes (F-GATE-PARITY R1 base-anchoring kept; F-STRICT-SURPLUS R1
        semantics). The whole allocation may add at most
        IMPORT_ARTIFACT_SLACK_WH of simulated import over the no-loads base:
        enough that ~10 Wh charger-standby artifacts never veto a sensible
        booking (F-PREDRAIN L1), but never a budget scaling with rescued
        export — the retired Z2' trade (`ratio * rescued + 1`) financed
        ~0.45-1.0 kWh/day of REAL planned pre-dawn import on clip-eve days
        (live 2026-07-19), which the operator's objective hierarchy forbids:
        surplus loads must never cause grid import."""
        return traj.total_import_wh - base_import <= IMPORT_ARTIFACT_SLACK_WH + _EPS

    def slots_serviceable(traj: Trajectory, covered) -> bool:
        """Planner floor-guard parity (F-STRICT-SURPLUS R2, planner-G4): no
        booked slot — this candidate's covered slots OR any previously accepted
        booking — may, in the trial trajectory, be grid-fed or touch the cutoff
        (per-slot rule in `_slot_serviceable`). Re-checking ALL booked slots
        closes the latest-first re-drain ratchet: a later acceptance at an
        earlier hour must not silently degrade an accepted run into a grid-fed
        or cutoff-riding one."""
        # Same cutoff the simulator itself applies to the inverter
        # (simulate.py: inv_floor at max(soc_min, inverter_min_soc)) — the
        # planner guard must veto slots the simulation would ride at its own
        # floor. Bit-identical to the former bare `inverter_min_soc_percent`
        # for every config with soc_min <= inverter_min (the golden suite).
        inverter_floor = max(
            config.battery.soc_min_percent, control.inverter_min_soc_percent
        )
        return all(
            _slot_serviceable(traj.flows[j], inputs.slots[j], inverter_floor)
            for j, _take in covered
        ) and all(
            _slot_serviceable(traj.flows[j], inputs.slots[j], inverter_floor)
            for j in range(n)
            if booked_any[j]
        )

    # Accepted continuous blocks retain their absolute peak allowance when
    # later days/loads are tested; otherwise yesterday's accepted 94.5 % peak
    # would veto every subsequent candidate. The allowance never accumulates.
    peak_tolerance_days: set[date] = set()

    def preserves_daily_max(
        traj: Trajectory, peak_tolerance_day: date | None = None
    ) -> bool:
        """F-STRICT-SURPLUS R5: the trial must still reach soc_max on every day
        the no-loads base reached it. Vetoes a pre-drain bet that would stop the
        battery filling on such a day. Continuous pre-drain alone may use
        the fixed peak tolerance on its own day (operator 2026-09-07); every
        candidate is measured against soc_max, never against an already
        reduced peak. Other days and load passes keep the strict max gate."""
        return all(
            any(
                traj.flows[j].soc_end_percent
                >= (
                    config.battery.soc_max_percent - PREDRAIN_PEAK_TOLERANCE_PERCENT
                    if day == peak_tolerance_day or day in peak_tolerance_days
                    else soc_full
                )
                for j in day_slots[day]
            )
            for day in base_max_days
        )

    def in_window(i: int) -> bool:
        w = windows.get(inputs.slots[i].start.date())
        return w is not None and w[0] <= i <= w[1]

    def _spread_candidate(
        load, i: int, commit_h: float, power_w: float
    ) -> AllocationCandidate | None:
        return AllocationContext(
            inputs, extra, schedules, run_h, path_overheads
        ).candidate(load.load_id, i, commit_h, power_w)

    def _gate_trial(
        load_id: str,
        trial_ac: tuple[float, ...],
        covered: list[tuple[int, float]],
        *,
        peak_tolerance_day: date | None = None,
    ) -> Trajectory | None:
        """Full-horizon re-simulation + the hard conditions BOTH passes apply,
        in the same order (Z2'' import invariant, R2 planner-G4 slot
        serviceability, R5 reach-max, Z3 buffer floor) — the pre-refactor
        copies were verbatim duplicates; ONE implementation, so a gate change
        can never drift asymmetric between the passes. Returns None when the
        candidate is vetoed, else the trial trajectory for the caller's
        pass-specific gates / acceptance.
        """

        def reject(reason: str) -> None:
            rejected[load_id].setdefault(covered[0][0], reason)

        if not _respects_path_power_limits(load_id, covered, run_h, path_power_limits):
            reject("path_power_limit")
            return None
        traj = simulate(
            config,
            inputs,
            threshold,
            extra_ac_wh=trial_ac,
            dc24_schedule=dc24_schedule,
            dc48_schedule=dc48_schedule,
        )
        if not import_ok(traj):  # Z2''
            reject("additional_import")
            return None
        if not slots_serviceable(traj, covered):  # R2 planner-G4
            reject("slot_not_serviceable")
            return None
        if not preserves_daily_max(traj, peak_tolerance_day):  # R5 reach-max
            reject("daily_peak")
            return None
        if _degrades_min_soc(traj, current, buffer_floor):  # Z3
            reject("soc_reserve")
            return None
        return traj

    def _accept_candidate(
        load,
        pass_no: int,
        i: int,
        power_w: float,
        trial: list[float],
        covered: list[tuple[int, float]],
        traj: Trajectory,
        rem: float | None,
    ) -> tuple[float, float]:
        """Accept bookkeeping, shared by both passes (the pre-refactor copies
        were verbatim duplicates): commit the trial series, mark the covered
        slots booked, and account the energy. Book what actually landed in the
        horizon (a commitment may be truncated at the horizon end); the gates
        deliberately used the full committed energy. `pass_no` (1/2) is only
        recorded in the allocation entry. Returns ``(placed_h, placed_wh)`` for
        the caller's reason string.
        """
        nonlocal extra, current
        extra = trial
        current = traj
        for j, take in covered:
            schedules[load.load_id][j] = True
            run_h[load.load_id][j] = take
            booked_any[j] = True
        placed_h = sum(take for _, take in covered)
        placed_wh = power_w * placed_h
        planned_wh[load.load_id] += placed_wh
        allocations[load.load_id].append((i, len(covered), pass_no, placed_wh))
        if rem is not None:
            remaining[load.load_id] = rem - placed_wh
        return placed_h, placed_wh

    # F-EXECUTION-PROJECTION: an already confirmed run owns its remaining
    # physical dwell before optional new actions. Safety gates still veto it.
    for load in config.loads:
        state = states.get(load.load_id, SurplusLoadState(load.load_id))
        if not state.available or state.minimum_run_until is None:
            continue
        power_w = _effective_load_power_w(load, state, power_caps_w)
        covered = [
            (
                i,
                max(
                    0.0,
                    min(
                        slot.duration,
                        (state.minimum_run_until - slot.start).total_seconds() / 3600,
                    ),
                ),
            )
            for i, slot in enumerate(inputs.slots)
        ]
        covered = [(i, hours) for i, hours in covered if hours > _EPS]
        if not covered or power_w <= _EPS:
            continue
        # A controllable charge gate stops at its target even during physical
        # plug dwell; reserving the whole dwell would overcharge the forecast.
        rem = remaining[load.load_id]
        if load.gate_stop_capable and rem is not None:
            budget_h = max(0.0, rem / power_w)
            capped = []
            for i, hours in covered:
                take = min(hours, budget_h)
                if take > _EPS:
                    capped.append((i, take))
                budget_h -= take
            covered = capped
            if not covered:
                continue
        trial = list(extra)
        for i, hours in covered:
            trial[i] += power_w * hours
        _add_path_overheads(trial, load.load_id, covered, run_h, path_overheads)
        traj = _gate_trial(load.load_id, tuple(trial), covered)
        if traj is None:
            continue
        _accept_candidate(
            load,
            0,
            covered[0][0],
            power_w,
            trial,
            covered,
            traj,
            remaining[load.load_id],
        )
        reasons[load.load_id].append("confirmed minimum runtime")

    def allocate_direct_surplus() -> None:
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
            # Storage allocation follows the COMPLETE terminal plan, including
            # pass 3. No recovery reservation may fragment that primary service.
            if load.load_id in direct_surplus_only_load_ids:
                continue
            state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
            if not state.available:
                continue
            power_w = _effective_load_power_w(load, state, power_caps_w)
            saturation_power_w = _saturation_power_w(load, power_w, power_caps_w)
            for i in range(n):
                slot = inputs.slots[i]
                if not state.can_start_at(slot.start):
                    rejected[load.load_id][i] = "waiting for runtime release"
                    continue
                if schedules[load.load_id][i]:
                    continue
                total_rem = remaining[load.load_id]
                rem = total_rem
                # Try the largest quantised run first, falling back to shorter
                # min_runtime multiples so a small battery-buffered surplus can still
                # be captured (F-SUBHOUR R1-R3). The whole-slot candidate is first,
                # so a full-hour placement stays bit-identical to the old behaviour.
                # rem/power_w size the gate-stop final top-up (F-GATE-TOPUP R2).
                for commit_h in _quantised_hours(
                    load, slot, rem, power_w, saturation_power_w
                ):
                    power_wh = power_w * commit_h
                    if power_wh <= _EPS:
                        continue
                    if rem is not None and rem < saturation_power_w * commit_h:
                        continue  # saturated (or nearly): skip
                    spread = _spread_candidate(load, i, commit_h, power_w)
                    # Unreachable in pass 1: slots are tried ascending and bookings
                    # mark contiguously forward from the accepted slot, so no own
                    # slot ahead of i can be marked yet (unlike pass 2, which runs
                    # descending and relies on this guard). Kept as a defensive
                    # mirror of the pass-2 structure.
                    if spread is None:  # pragma: no cover
                        continue  # commitment overlaps an already-scheduled slot
                    trial, covered = spread.trial, spread.covered
                    commit_h, seamless = spread.commit_h, spread.seamless
                    power_wh = sum(trial[j] - extra[j] for j, _ in covered)
                    # Soft surplus condition (D-A4): battery may cover at most
                    # `battery_tolerance` of the committed energy. Spilled slots
                    # contribute their export prorated by the occupied share.
                    surplus_cov = current.flows[i].grid_export_wh + sum(
                        current.flows[j].grid_export_wh
                        * (take / inputs.slots[j].duration)
                        for j, take in covered[1:]
                    )
                    battery_share = max(0.0, power_wh - surplus_cov) / power_wh
                    # F-PEAK-FILL R2: an energy-limited load with budget left may
                    # pass the soft gate even over tolerance when the slot proves
                    # to be an at-max top-up (checked after the re-simulation).
                    at_max_topup = False
                    if battery_share > load.battery_tolerance + _EPS:
                        if not (load.energy_limited and rem is not None and rem > _EPS):
                            continue
                        at_max_topup = True
                    # Hard conditions via full re-simulation (Z2''/R2/R5/Z3).
                    traj = _gate_trial(load.load_id, tuple(trial), covered)
                    if traj is None:
                        continue
                    if at_max_topup and not (
                        slot.pv_wh > slot.ac_wh + slot.dc_wh
                        and traj.flows[i].soc_end_percent
                        >= config.battery.soc_max_percent
                        - AT_MAX_TOPUP_BAND_PERCENT
                        - _EPS
                    ):
                        continue  # no at-max top-up: dip leaves the hysteresis band
                    placed_h, placed_wh = _accept_candidate(
                        load, 1, i, power_w, trial, covered, traj, total_rem
                    )
                    final_note = _final_note(load, commit_h, seamless)
                    reasons[load.load_id].append(
                        f"pass 1 @ {slot.start.strftime('%m-%d %H:%M')}: "
                        + (
                            "at-max top-up (peak fill)"
                            if at_max_topup
                            else "direct surplus"
                        )
                        + f", {round(placed_h * 60)} min x "
                        f"{round(power_w)} W, battery share {round(battery_share * 100)}%"
                        f"{final_note}"
                    )
                    break  # placed the largest feasible quantum; done with this slot

    allocate_direct_surplus()

    def allocate_storage_preconditioning() -> None:
        # Pass 2: objective-based preemptive hours (docs/ALGORITHM.md D-A4 v2,
        # two-buffer pre-drain F-PREDRAIN §3) — ENERGY-LIMITED loads only since
        # F-PREDRAIN-BLOCK (v0.19.0): continuous loads no longer bet slot-wise
        # (their pre-drain is pass 3's single contiguous block). A load may run
        # without direct surplus
        # when the re-simulation proves it is safe AND worthwhile:
        #   Z2' import trade   — import stays within the trade invariant (F2),
        #   Z3  buffer floor   — nominal min SOC not degraded below soc_min+buffer,
        #   Z4  lower buffer   — even a pessimistic (alpha) PV run keeps the inverter
        #                        reserve above its floor across the bet's recovery
        #                        window [i, recovery] (F3 v2),
        #   (c) opportunity    — (c1) the nominal drain is refilled from lost export,
        #                        OR (c2) inside the day's PV window an optimistic
        #                        (beta) run would be (upper-buffer insurance, F4).
        # The DAYLIGHT restriction applies: energy-limited loads never book
        # zero-PV (night) slots (operator refinement 2, 2026-07-17). Iterated
        # latest-first (L4); slots after the last export can never satisfy the
        # gate, so they are skipped, as is the whole pass on an export-free
        # horizon.
        if current.total_export_wh > _EPS:
            # Optimistic opportunity baseline for the CURRENTLY accepted series —
            # whole-horizon, kept in step with `current` and refreshed only on
            # acceptance; skipped when the (c2) gate is neutral. Per-slot optimism
            # (F-QUANTILE-BANDS R4): P90 evidence where bands exist, beta elsewhere.
            current_beta = (
                simulate(
                    config,
                    inputs,
                    threshold,
                    extra_ac_wh=tuple(extra),
                    dc24_schedule=dc24_schedule,
                    dc48_schedule=dc48_schedule,
                    pv_scale=optimism_vec,
                )
                if c2_active
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
                # drain until the battery provably refills to soc_max — so a bet is
                # judged on its own recovery, not vetoed by an unrelated later dip,
                # and a sound pre-charge is not punished by a globally scaled-down
                # horizon (the v1 whole-horizon failure). Since F-STRICT-SURPLUS R3
                # the settlement point comes from the TRIAL trajectory (computed per
                # candidate below), not from the same-day PV window end.
                for load in config.loads:
                    state = states.get(
                        load.load_id, SurplusLoadState(load_id=load.load_id)
                    )
                    if (
                        load.load_id in direct_surplus_only_load_ids
                        or not state.available
                        or not state.can_start_at(slot.start)
                        or schedules[load.load_id][i]
                    ):
                        continue
                    # F-PREDRAIN-BLOCK: pass-2 bets are energy-limited only —
                    # continuous loads get their pre-drain as ONE block in
                    # pass 3 (no more slot-wise flicker bets).
                    if not load.energy_limited:
                        continue
                    # Daylight rule (F-GATE-PARITY refinement 2): energy-limited
                    # loads never open a bet in a zero-PV (night) slot. Deliberately
                    # `pv_wh > 0` and NOT `in_window` — pre-window daylight
                    # pre-charges before a short peak stay allowed (they were a
                    # pinned capability before parity, and forbidding them would
                    # re-introduce a class asymmetry in daylight).
                    if slot.pv_wh <= 0.0:
                        continue
                    power_w = _effective_load_power_w(load, state, power_caps_w)
                    saturation_power_w = _saturation_power_w(
                        load, power_w, power_caps_w
                    )
                    rem = remaining[load.load_id]
                    # Largest-first quantised search (F-SUBHOUR): a sub-hour
                    # preemptive run needs only export_drop >= (1-tol)*(k*q) energy,
                    # so a small afternoon dribble a whole hour cannot capture may
                    # still be soaked by a min_runtime chunk. rem/power_w size the
                    # gate-stop final top-up (F-GATE-TOPUP R2).
                    for commit_h in _quantised_hours(
                        load, slot, rem, power_w, saturation_power_w
                    ):
                        power_wh = power_w * commit_h
                        if power_wh <= _EPS:
                            continue
                        if rem is not None and rem < saturation_power_w * commit_h:
                            continue
                        spread = _spread_candidate(load, i, commit_h, power_w)
                        if spread is None:
                            continue
                        trial, covered = spread.trial, spread.covered
                        commit_h, seamless = spread.commit_h, spread.seamless
                        power_wh = sum(trial[j] - extra[j] for j, _ in covered)
                        if load.energy_limited and any(
                            inputs.slots[j].pv_wh <= 0.0 for j, _ in covered
                        ):
                            # Daylight rule, spill guard: a min-runtime commitment
                            # near the day's edge must not spill into night slots;
                            # shorter quantised candidates (incl. the gate-stop
                            # final quantum) still get their chance below.
                            continue
                        trial_tuple = tuple(trial)
                        traj = _gate_trial(load.load_id, trial_tuple, covered)
                        if traj is None:
                            continue
                        # Bet settlement (R3): where the trial actually refills.
                        recovery = _refill_index(traj, i, soc_full)
                        # R6 (operator 2026-07-19): no cross-day DAYTIME pre-drain.
                        # A DAYLIGHT bet (pv_wh > 0 — any production, NOT just the
                        # strong-PV window, else the afternoon taper leaks the bet
                        # one slot past the window edge) must refill soc_max the same
                        # calendar day it runs; only night/pre-dawn slots (pv_wh == 0)
                        # may pre-drain for a next-day clip (F-NIGHT-RESCUE keeps its
                        # carve-out). Stops a load draining the battery TODAY, in
                        # today's own daylight, to absorb TOMORROW's clip when today
                        # does not clip — the marginal cross-day daytime bet the
                        # operator rejected (Sunday 14:00 for Monday, live 2026-07-19).
                        if _crossday_daytime_bet(
                            slot.start.date(),
                            inputs.slots[recovery].start.date(),
                            slot.pv_wh > 0.0,
                        ):
                            continue
                        export_drop = current.total_export_wh - traj.total_export_wh
                        # F-NIGHT-RESCUE R2: the c1 need is judged at the physical
                        # AC->battery->AC round trip. A pure battery detour can only
                        # ever return `rt * energy` as rescued export (live ~0.82),
                        # so the old `(1-tol)*energy` demand was PHYSICALLY
                        # unsatisfiable for night runs — every 22:00-05:00 slot of
                        # the 2026-07-11 incident failed exactly there while
                        # ~3.3 kWh of next-day clipping was forecast. A direct-PV
                        # run drops export ~1:1 and passes even more easily; the
                        # factor only stops billing the detour's losses twice.
                        # Z2'/Z3/Z4 still bound how deep the drain may go (R3).
                        need = (1.0 - load.battery_tolerance) * power_wh * rt
                        need_c2 = (1.0 - load.battery_tolerance) * power_wh
                        trial_beta = None
                        via_beta = False  # which gate accepted -> reason string (R13)
                        # (c1) nominal refill OR (c2) optimistic in-window insurance
                        # — identical for BOTH load classes (F-GATE-PARITY R2; the
                        # former energy-limited c1-only path is superseded).
                        accept = export_drop + _EPS >= need
                        if not accept and c2_active and in_window(i):
                            # current_beta is simulated iff c2_active (see above).
                            assert current_beta is not None
                            trial_beta = simulate(
                                config,
                                inputs,
                                threshold,
                                extra_ac_wh=trial_tuple,
                                dc24_schedule=dc24_schedule,
                                dc48_schedule=dc48_schedule,
                                pv_scale=optimism_vec,
                            )
                            drop_beta = (
                                current_beta.total_export_wh
                                - trial_beta.total_export_wh
                            )
                            # c2 judges an optimistic direct-PV absorption, not
                            # a battery detour: the unscaled need applies.
                            accept = drop_beta + _EPS >= need_c2
                            via_beta = accept
                        if not accept:
                            continue
                        # Z4 windowed lower-buffer stress gate (§3.3 v2): stress
                        # PV only across the bet window [i, recovery] and take
                        # the windowed min. Reject iff that stressed reserve
                        # both breaks the inverter floor AND is worse than the same
                        # windowed min on the currently accepted series — a dip the
                        # baseline already contains does not veto the bet.
                        # Per-slot stress (F-QUANTILE-BANDS R4): empirical P10
                        # where a band exists, alpha elsewhere — Z4 protection
                        # now varies with the weather-class history. Since
                        # F-GATE-PARITY the stress gate also binds energy-limited
                        # bets: whoever takes bet energy respects the floors.
                        if z4_active:
                            # Extend the stress window past `recovery` when this
                            # candidate's run spills beyond it (a min-runtime
                            # commitment near the window end lands in later slots):
                            # the spill drains the reserve too, so it must be
                            # stressed and included in the windowed min (FIX-7).
                            hi = max(recovery, covered[-1][0])
                            scale_vec = [
                                stress_vec[j] if i <= j <= hi else 1.0 for j in range(n)
                            ]
                            trial_stress = simulate(
                                config,
                                inputs,
                                threshold,
                                extra_ac_wh=trial_tuple,
                                dc24_schedule=dc24_schedule,
                                dc48_schedule=dc48_schedule,
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
                                    dc24_schedule=dc24_schedule,
                                    dc48_schedule=dc48_schedule,
                                    pv_scale=scale_vec,
                                )
                                stress_base[key] = _windowed_min_soc(base_stress, i, hi)
                            if _z4_reject(
                                trial_wmin, stress_floor_by_slot[i], stress_base[key]
                            ):
                                continue
                        _accept_candidate(
                            load, 2, i, power_w, trial, covered, traj, rem
                        )
                        stress_base.clear()  # `extra` changed -> windowed baselines stale
                        if c2_active:
                            current_beta = (
                                trial_beta
                                if trial_beta is not None
                                else simulate(
                                    config,
                                    inputs,
                                    threshold,
                                    extra_ac_wh=tuple(extra),
                                    dc24_schedule=dc24_schedule,
                                    dc48_schedule=dc48_schedule,
                                    pv_scale=optimism_vec,
                                )
                            )
                        # "latest feasible slot" is structurally true: pass 2 walks
                        # descending and accepts the first slot that passes (R13).
                        final_note = _final_note(load, commit_h, seamless)
                        # F-QUANTILE-BANDS R6: name the evidence — "(p90)" when the
                        # accepted slot itself carried a band, "(beta)" otherwise.
                        insurance_src = "p90" if band_slots[i] else "beta"
                        reasons[load.load_id].append(
                            f"pass 2 @ {slot.start.strftime('%m-%d %H:%M')}: "
                            + (
                                f"in-window insurance ({insurance_src}), "
                                "latest feasible slot"
                                if via_beta
                                else (
                                    f"covered by otherwise-lost export "
                                    f"({round(export_drop)} Wh), latest feasible slot"
                                )
                            )
                            + final_note
                        )
                        break

    allocate_storage_preconditioning()

    def allocate_continuous_blocks() -> None:
        nonlocal extra, current
        # Pass 3 — pre-drain block for CONTINUOUS (non-energy-limited) loads
        # (docs/F-PREDRAIN-BLOCK.md, operator 2026-08-01). Replaces the slot-wise
        # pass-2 betting that flickered at the gate edge (the 19-min battery run
        # of 2026-08-01). Rules:
        #   R1 per day, within the day (2026-08-02): EVERY horizon day with a
        #      clip gets its own block — but each block stays inside its own
        #      day (start >= the day's midnight), so nothing ever drains one day
        #      for the next (the retired cross-day carve-out stays retired).
        #      Today's block actuates as before (slot 0 + stability gate);
        #      future-day blocks are plan/display until their day comes — the
        #      forecast then honestly shows tomorrow's pre-drain instead of a
        #      flat curve that cannot happen.
        #   R2 peak: first slot of the block's day whose ACCEPTED trajectory
        #      (`current`, without this load's pre-drain) exports. No export
        #      that day -> no block; peak at the day's first slot -> pass 1
        #      owns it.
        #   R3 target: today's remaining lost export from the peak on — the
        #      block's opportunity (c1) is justified by construction up to it.
        #   R4 latest start (L4): extend backwards from the peak only until the
        #      target is covered; the floors cap the block earlier.
        #   R5 floors: the WHOLE block is ONE candidate through the same gate
        #      stack pass 1/2 use (Z2''/R2/R5/Z3 via `_gate_trial`, the
        #      rt-weighted opportunity check, and the dynamic buffer — the
        #      crossover-ramped floor evaluated on the NOMINAL trial). The
        #      alpha/band STRESS is deliberately NOT applied to the block
        #      (operator decision 2026-08-02): a forecast miss is caught by the
        #      intra-day replan loop (the block is recomputed every refresh, a
        #      degraded forecast retracts the recommendation, and the G4 floor
        #      guard force-switches at the real-time cutoff) — the stress stays
        #      in force for the slot-wise pass-2 bets of energy-limited loads.
        #      Every gate worsens monotonically with block length, so the first
        #      veto ends the extension and the last accepted block is the
        #      longest feasible one; R5 (preserve daily max) additionally pins
        #      "the battery still fills today" — since 2026-09-07 within the
        #      fixed one-point peak tolerance, with all reserve gates unchanged.
        #   R6 min length: a block shorter than min_runtime is never booked
        #      (the executor dwell would deliver more than the plan accounts).
        #   R7 execution: the coordinator switches the block only after
        #      PREDRAIN_BLOCK_STABLE_PLANS identical plans (stable signature).
        today = inputs.slots[0].start.date() if n else None
        # Horizon days in ascending order: (date, first slot index).
        horizon_days: list[tuple] = []
        for _j, _slot in enumerate(inputs.slots):
            if not horizon_days or _slot.start.date() != horizon_days[-1][0]:
                horizon_days.append((_slot.start.date(), _j))
        for load in config.loads:
            if load.energy_limited or today is None:
                continue
            state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
            if not state.available:
                continue
            power_w = _effective_load_power_w(load, state, power_caps_w)
            if power_w <= _EPS:
                continue
            for day, day_start in horizon_days:
                peak = next(
                    (
                        j
                        for j in range(day_start, n)
                        if inputs.slots[j].start.date() == day
                        and current.flows[j].grid_export_wh > _EPS
                    ),
                    None,
                )
                if peak is None or peak == day_start:
                    continue  # no clip this day (or it opens the day: pass 1 owns it)
                # An own pass-1 booking directly BEFORE the peak is not a conflict
                # but the seamless continuation of the run (F-SEAMLESS-PLAN raster
                # edge): the block then ends at the first own-booked slot boundary
                # instead of dying wholesale (2026-08-08: the pass-1 quantum ate
                # the peak slot's export down to a hair above/below zero; the peak
                # jumped past the booked slot, the backward walk broke on the own
                # booking on its FIRST step, and the block vanished entirely 14x
                # in 45 min — starving the stability evidence and whipping the
                # feed-in target by the block's energy each time).
                end = peak
                while end - 1 >= day_start and schedules[load.load_id][end - 1]:
                    end -= 1
                if end == day_start:
                    continue  # no free slot before the own booking — no room
                target_wh = sum(
                    current.flows[j].grid_export_wh
                    for j in range(peak, n)
                    if inputs.slots[j].start.date() == day
                )
                # target_wh > _EPS holds by construction: the peak slot itself
                # exports more than _EPS and is part of the sum. The guard is
                # kept as a defensive mirror of the peak checks above.
                if target_wh <= _EPS:  # pragma: no cover
                    continue
                best: (
                    tuple[int, list[float], list[tuple[int, float]], Trajectory] | None
                ) = None
                block_wh = 0.0
                for s in range(end - 1, day_start - 1, -1):
                    if (
                        state.predrain_not_before is not None
                        and inputs.slots[s].start < state.predrain_not_before
                    ):
                        rejected[load.load_id][s] = "waiting for stable plan"
                        break
                    if not state.can_start_at(inputs.slots[s].start):
                        rejected[load.load_id][s] = "waiting for runtime release"
                        break
                    if schedules[load.load_id][s]:
                        break  # the block never overlaps own (scattered) bookings
                    block_wh += power_w * inputs.slots[s].duration
                    trial = list(extra)
                    covered = []  # already typed by the pass-1 unpack above
                    for j in range(s, end):
                        take = inputs.slots[j].duration
                        trial[j] += power_w * take
                        covered.append((j, take))
                    _add_path_overheads(
                        trial, load.load_id, covered, run_h, path_overheads
                    )
                    physical_block_wh = sum(trial[j] - extra[j] for j, _ in covered)
                    traj = _gate_trial(
                        load.load_id, tuple(trial), covered, peak_tolerance_day=day
                    )
                    if traj is None:
                        break  # Z2''/R2/R5/Z3 veto: longer blocks only get worse
                    export_drop = current.total_export_wh - traj.total_export_wh
                    if (
                        export_drop + _EPS
                        < (1.0 - load.battery_tolerance) * physical_block_wh * rt
                    ):
                        break  # the detour is not repaid (c1 at the physical rt)
                    # Dynamic-buffer floor for the block, evaluated on the NOMINAL
                    # trajectory (operator decision 2026-08-02): the alpha/band
                    # STRESS is deliberately NOT applied to pass 3 — a forecast
                    # miss is caught by the intra-day replan loop (the block is
                    # recomputed every refresh; a degraded forecast retracts the
                    # recommendation; the G4 floor guard force-switches at the
                    # real-time cutoff), and R5 already pins "the battery still
                    # fills today within the fixed peak tolerance". Stress stays for
                    # slot-wise pass-2 bets of energy-limited loads.
                    recovery = _refill_index(
                        traj,
                        s,
                        config.battery.soc_max_percent
                        - PREDRAIN_PEAK_TOLERANCE_PERCENT,
                    )
                    hi = max(recovery, covered[-1][0])
                    if _z4_reject(
                        _windowed_min_soc(traj, s, hi),
                        stress_floor_by_slot[s],
                        _windowed_min_soc(current, s, hi),
                    ):
                        break
                    best = (s, trial, covered, traj)
                    if block_wh >= target_wh:
                        break  # target covered: the latest feasible start is found
                if best is None:
                    continue
                s, trial, covered, traj = best
                # R6: the committed run must cover the executor dwell. An own
                # booking in the slot the block ends at (pass 1) continues the
                # block's run seamlessly, so it counts toward min_runtime —
                # otherwise a block ending inside a partial slot 0 would be
                # dropped although the dwell is delivered (the F-SEAMLESS-PLAN
                # raster-edge case).
                block_hours = sum(take for _, take in covered)
                run_after = run_h[load.load_id][end]
                if block_hours + run_after < load.min_runtime_min / 60.0 - _EPS:
                    continue
                _placed_h, placed_wh = _accept_candidate(
                    load, 3, s, power_w, trial, covered, traj, remaining[load.load_id]
                )
                peak_tolerance_days.add(day)
                reasons[load.load_id].append(
                    f"pass 3 @ {inputs.slots[s].start.strftime('%m-%d %H:%M')}: "
                    f"pre-drain block to the {day.strftime('%m-%d')} peak "
                    f"{inputs.slots[peak].start.strftime('%H:%M')} "
                    f"({round(placed_wh)} Wh against {round(target_wh)} Wh clip), "
                    "latest feasible start"
                    + (", ends at own pass-1 booking" if end < peak else "")
                )

        # Complete an existing continuous service interval before any cascade
        # storage is considered. Test the whole daily bridge once, so the fixed
        # energy tolerance cannot be spent separately on every little gap.
        for load in config.loads:
            if not direct_surplus_only_load_ids or load.energy_limited:
                continue
            power_w = _effective_load_power_w(
                load,
                states.get(load.load_id, SurplusLoadState(load.load_id)),
                power_caps_w,
            )
            for day, day_start in horizon_days:
                occupied = [
                    j
                    for j in range(day_start, n)
                    if inputs.slots[j].start.date() == day
                    and schedules[load.load_id][j]
                ]
                if not occupied:
                    continue
                first, last = occupied[0], occupied[-1]
                covered = [
                    (j, inputs.slots[j].duration - run_h[load.load_id][j])
                    for j in range(first, last + 1)
                ]
                if not any(take > _EPS for _, take in covered):
                    continue
                # Do not split an already committed cross-midnight dwell when
                # consolidating the allocation records into one continuous block.
                if any(
                    start <= last
                    and start + count > first
                    and (start < first or start + count > last + 1)
                    for start, count, _, _ in allocations[load.load_id]
                ):
                    continue
                trial = list(extra)
                for j, take in covered:
                    trial[j] += power_w * take
                _add_path_overheads(trial, load.load_id, covered, run_h, path_overheads)
                traj = _gate_trial(
                    load.load_id, tuple(trial), covered, peak_tolerance_day=day
                )
                if traj is None:
                    continue
                added_wh = sum(trial[j] - extra[j] for j, _ in covered)
                export_drop = current.total_export_wh - traj.total_export_wh
                if export_drop + STORAGE_TARGET_TOLERANCE_WH + _EPS < (
                    (1.0 - load.battery_tolerance) * added_wh * rt
                ):
                    continue
                recovery = _refill_index(
                    traj,
                    first,
                    config.battery.soc_max_percent - PREDRAIN_PEAK_TOLERANCE_PERCENT,
                )
                hi = max(last, recovery)
                if _z4_reject(
                    _windowed_min_soc(traj, first, hi),
                    stress_floor_by_slot[first],
                    _windowed_min_soc(current, first, hi),
                ):
                    continue
                extra, current = trial, traj
                planned_wh[load.load_id] += power_w * sum(take for _, take in covered)
                for j, _ in covered:
                    schedules[load.load_id][j] = booked_any[j] = True
                    run_h[load.load_id][j] = inputs.slots[j].duration
                # Allocation/reason are parallel records in the published plan.
                # Consolidate both, otherwise the new block inherits an obsolete
                # direct-surplus reason and later days shift to the wrong label.
                retained = [
                    (item, reason)
                    for item, reason in zip(
                        allocations[load.load_id], reasons[load.load_id], strict=True
                    )
                    if item[0] < first or item[0] > last
                ]
                allocations[load.load_id] = [item for item, _ in retained]
                reasons[load.load_id] = [reason for _, reason in retained]
                allocations[load.load_id].append(
                    (
                        first,
                        last - first + 1,
                        3,
                        power_w * sum(run_h[load.load_id][first : last + 1]),
                    )
                )
                peak_tolerance_days.add(day)
                reasons[load.load_id].append(
                    f"pass 3 @ {inputs.slots[first].start.strftime('%m-%d %H:%M')}: "
                    "continuous terminal block before storage, gaps closed"
                )

    allocate_continuous_blocks()

    plans = [
        LoadPlan(
            load_id=load.load_id,
            schedule=tuple(schedules[load.load_id]),
            planned_energy_wh=planned_wh[load.load_id],
            allocations=tuple(allocations[load.load_id]),
            run_hours=tuple(run_h[load.load_id]),
            reasons=tuple(reasons[load.load_id]),
            rejected_candidates=tuple(sorted(rejected[load.load_id].items())),
        )
        for load in config.loads
    ]
    return plans, tuple(extra), current


def _allocate_recovery_after_continuous_loads(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    load_plans: list[LoadPlan],
    extra_ac: tuple[float, ...],
    trajectory: Trajectory,
    recovery_targets_wh: dict[str, float],
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
    power_caps_w: dict[str, float] | None = None,
    path_power_limits: tuple[tuple[tuple[tuple[str, float], ...], float], ...] = (),
    cumulative_energy_caps_wh: dict[str, tuple[float, ...]] | None = None,
    *,
    path_overheads: tuple[tuple[tuple[str, ...], float], ...] = (),
    priority_terminals: dict[str, str] | None = None,
    allow_final_quantum_overshoot: bool = False,
    visible_export_only: bool = False,
    today_only: bool = True,
    repeat_final_rounding: bool = True,
    phase_name: str = "recovery",
) -> tuple[list[LoadPlan], tuple[float, ...], Trajectory]:
    """Retry cascade recovery after the terminal's pass-3 allocation.

    Continuous terminal pre-drain is intentionally constructed in pass 3,
    after the generic direct-surplus pass.  That higher-priority block can
    create same-day refill/export headroom which was therefore invisible to a
    member recovery phase in pass 1.  Without this fixed-point step the later
    feed-in pass could book exactly that PV while both Fossibots stayed idle.

    The retry is deliberately narrower than generic load allocation: direct
    PV only, no added import, same-day export repayment, no worsened protected
    SOC dip, and only the still-open part of each 50 % recovery target.  It
    runs before early feed-in, so feed-in sees the true residual after every
    higher-priority recovery opportunity.  After every member has had that
    strict attempt, a second internal pass may close a target with complete
    minimum-runtime quanta.  Such a quantum may cross 50 % slightly, but only
    when all of its energy replaces same-day export.  Keeping this rounding in
    a separate pass means rounding B1 up can never take energy with which B2
    could still make strict progress toward 50 %.
    """
    if not recovery_targets_wh or not inputs.slots:
        return load_plans, extra_ac, trajectory

    loads = {load.load_id: load for load in config.loads}
    plans = {load_plan.load_id: load_plan for load_plan in load_plans}
    extra = list(extra_ac)
    current = trajectory
    protected_floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    recovery_day = inputs.now.date()
    for load_id, target_wh in recovery_targets_wh.items():
        load = loads[load_id]
        plan = plans[load_id]
        state = next(
            (state for state in inputs.load_states if state.load_id == load_id),
            SurplusLoadState(load_id=load_id),
        )
        if not state.available:
            continue
        power_w = _effective_load_power_w(load, state, power_caps_w)
        saturation_power_w = _saturation_power_w(load, power_w, power_caps_w)
        # Recovery is a local-day obligation. A tomorrow booking used to make
        # this subtraction look complete and suppressed today's retry even
        # while today's feed-in stayed avoidable (Bad cascade incident).
        planned_period_wh = sum(
            power_w
            * (
                plan.run_hours[index]
                if plan.run_hours and index < len(plan.run_hours)
                else slot.duration
            )
            for index, slot in enumerate(inputs.slots)
            if (not today_only or slot.start.date() == recovery_day)
            and index < len(plan.schedule)
            and plan.schedule[index]
        )
        remaining = max(0.0, target_wh - planned_period_wh)
        if remaining <= _EPS:
            continue
        schedules = list(plan.schedule)
        run_hours = list(plan.run_hours or (0.0,) * len(inputs.slots))
        allocations = list(plan.allocations)
        reasons = list(plan.reasons)
        rejected = dict(plan.rejected_candidates)
        planned_wh = plan.planned_energy_wh
        final_quantum_h = load.min_runtime_min / 60.0

        for i, slot in enumerate(inputs.slots):
            if remaining <= _EPS:
                break
            if today_only and slot.start.date() != recovery_day:
                continue
            if not state.can_start_at(slot.start):
                rejected[i] = "waiting for runtime release"
                continue
            extending_h = run_hours[i] if schedules[i] else 0.0
            free_h = max(0.0, slot.duration - extending_h)
            if free_h <= _EPS:
                continue
            candidate_hours = (
                [
                    min(
                        free_h,
                        remaining / saturation_power_w
                        if load.energy_limited and saturation_power_w > _EPS
                        else free_h,
                    )
                ]
                if schedules[i]
                else _quantised_hours(
                    load, slot, remaining, power_w, saturation_power_w
                )
            )
            for commit_h in candidate_hours:
                final_quantum = False
                power_wh = power_w * commit_h
                if power_wh <= _EPS:
                    continue
                if remaining < saturation_power_w * commit_h:
                    final_quantum = (
                        allow_final_quantum_overshoot
                        and load.energy_limited
                        and commit_h <= final_quantum_h + _EPS
                    )
                    if not final_quantum:
                        continue
                trial_extra, covered = _spread_energy(
                    extra, inputs.slots, i, power_w, commit_h
                )
                if (
                    priority_terminals is not None
                    and not schedules[i]
                    and (
                        sum(take for _, take in covered) + _EPS < final_quantum_h
                        or power_w * sum(take for _, take in covered) + _EPS
                        < STORAGE_TARGET_TOLERANCE_WH
                    )
                ):
                    rejected.setdefault(i, "storage_action_too_small")
                    continue  # never start a truncated or negligible storage action
                if today_only and any(
                    inputs.slots[j].start.date() != recovery_day for j, _take in covered
                ):
                    continue
                if any(schedules[j] and j != i for j, _take in covered):
                    continue
                path_runs = {
                    key: (
                        item.run_hours
                        or tuple(
                            slot.duration if on else 0.0
                            for slot, on in zip(
                                inputs.slots, item.schedule, strict=True
                            )
                        )
                    )
                    for key, item in plans.items()
                }
                path_runs[load.load_id] = tuple(run_hours)
                terminal_id = (priority_terminals or {}).get(load.load_id)
                if terminal_id is not None and any(
                    path_runs[terminal_id][j] < inputs.slots[j].duration - _EPS
                    for j, _take in covered
                ):
                    rejected.setdefault(i, "terminal_priority")
                    continue  # keep the terminal's gaps free of storage cycling
                _add_path_overheads(
                    trial_extra, load.load_id, covered, path_runs, path_overheads
                )
                if not _respects_path_power_limits(
                    load.load_id, covered, path_runs, path_power_limits
                ):
                    continue
                if not _respects_cumulative_energy_cap(
                    load.load_id,
                    power_w,
                    run_hours,
                    covered,
                    cumulative_energy_caps_wh,
                ):
                    continue
                if visible_export_only and any(
                    current.flows[j].grid_export_wh + _EPS < trial_extra[j] - extra[j]
                    for j, take in covered
                ):
                    continue
                if any(
                    inputs.slots[j].pv_wh + _EPS
                    < inputs.slots[j].ac_wh
                    + trial_extra[j]
                    + (
                        config.inverter.standby_power_w * inputs.slots[j].duration
                        if current.flows[j].inverter_on
                        else 0.0
                    )
                    for j, take in covered
                ):
                    continue
                trial = simulate(
                    config,
                    inputs,
                    threshold,
                    extra_ac_wh=tuple(trial_extra),
                    dc24_schedule=dc24_schedule,
                    dc48_schedule=dc48_schedule,
                )
                if trial.total_import_wh > current.total_import_wh + _EPS:
                    rejected.setdefault(i, "additional_import")
                    continue
                if _degrades_min_soc(trial, current, protected_floor):
                    rejected.setdefault(i, "soc_reserve")
                    continue
                inverter_floor = max(
                    config.battery.soc_min_percent,
                    config.control.inverter_min_soc_percent,
                )
                if any(
                    not _slot_serviceable(
                        trial.flows[j], inputs.slots[j], inverter_floor
                    )
                    for j, _take in covered
                ):
                    rejected.setdefault(i, "slot_not_serviceable")
                    continue
                candidate_by_day: dict = {}
                for j, _take in covered:
                    day = inputs.slots[j].start.date()
                    candidate_by_day[day] = (
                        candidate_by_day.get(day, 0.0) + trial_extra[j] - extra[j]
                    )
                export_drop_by_day: dict = {}
                for j, slot_j in enumerate(inputs.slots):
                    day = slot_j.start.date()
                    export_drop_by_day[day] = export_drop_by_day.get(day, 0.0) + (
                        current.flows[j].grid_export_wh - trial.flows[j].grid_export_wh
                    )
                if any(
                    export_drop_by_day.get(day, 0.0) + _EPS < needed_wh
                    for day, needed_wh in candidate_by_day.items()
                ):
                    rejected.setdefault(i, "same_day_export")
                    continue

                extra = trial_extra
                current = trial
                placed_wh = 0.0
                for j, take in covered:
                    schedules[j] = True
                    run_hours[j] += take
                    placed_wh += power_w * take
                planned_wh += placed_wh
                remaining = max(0.0, remaining - placed_wh)
                allocations.append((i, len(covered), 1, placed_wh))
                reasons.append(
                    f"pass 1 @ {slot.start.strftime('%m-%d %H:%M')}: "
                    f"direct PV {phase_name} after terminal plan, "
                    f"{round(sum(take for _, take in covered) * 60)} min x "
                    f"{round(power_w)} W"
                    + (
                        ", final recovery dwell"
                        if final_quantum
                        else _final_note(load, commit_h, False)
                    )
                )
                break

        plans[load_id] = replace(
            plan,
            schedule=tuple(schedules),
            planned_energy_wh=planned_wh,
            allocations=tuple(allocations),
            run_hours=tuple(run_hours),
            reasons=tuple(reasons),
            rejected_candidates=tuple(sorted(rejected.items())),
        )

    updated_plans = [plans[plan.load_id] for plan in load_plans]
    if repeat_final_rounding and not allow_final_quantum_overshoot:
        return _allocate_recovery_after_continuous_loads(
            config,
            inputs,
            threshold,
            updated_plans,
            tuple(extra),
            current,
            recovery_targets_wh,
            dc24_schedule,
            dc48_schedule,
            power_caps_w,
            path_power_limits,
            cumulative_energy_caps_wh,
            path_overheads=path_overheads,
            priority_terminals=priority_terminals,
            allow_final_quantum_overshoot=True,
            visible_export_only=visible_export_only,
            today_only=today_only,
            repeat_final_rounding=repeat_final_rounding,
            phase_name=phase_name,
        )
    return updated_plans, tuple(extra), current


def _continuous_loads_cover_to_max(
    config: SystemConfig,
    inputs: PlanInputs,
    load_plans: Sequence[LoadPlan],
    trajectory: Trajectory,
    start: int,
) -> bool:
    """F-FEEDIN R1: deliberate export requires continuous service to full SOC.

    A residual from a failed/quantised load candidate is not proof of exhausted
    consumption. Even an unavailable load or a partial-hour gap cannot authorize
    feed-in. With no continuous consumers this additional prerequisite is empty.
    Check the WITH-feed-in trajectory too: delaying the peak must not move it
    past the end of the load's run. Slots model their SOC at the end, so the
    peak slot itself must be fully covered.
    """
    continuous = [load for load in config.loads if not load.energy_limited]
    if not continuous:
        return True
    day = inputs.slots[start].start.date()
    peak = next(
        (
            j
            for j in range(start, len(inputs.slots))
            if inputs.slots[j].start.date() == day
            and trajectory.flows[j].soc_end_percent
            >= config.battery.soc_max_percent - _EPS
        ),
        None,
    )
    if peak is None:
        return False
    plans = {load_plan.load_id: load_plan for load_plan in load_plans}
    states = {state.load_id: state for state in inputs.load_states}
    for load in continuous:
        state = states.get(load.load_id)
        if state is not None and (not state.available or not state.feedin_ready):
            return False
        load_plan = plans.get(load.load_id)
        if load_plan is None:
            return False
        if any(
            j >= len(load_plan.run_hours)
            or load_plan.run_hours[j] < inputs.slots[j].duration - _EPS
            for j in range(start, peak + 1)
        ):
            return False
    return True
