"""Planner: threshold search, surplus allocation, appliance advisor, support
escalation. Implements docs/ALGORITHM.md §1 with decisions D-A1..D-A9."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

from .allocation import (
    _allocate_recovery_after_continuous_loads as _allocate_recovery_after_continuous_loads,
)
from .allocation import (
    _continuous_loads_cover_to_max as _continuous_loads_cover_to_max,
)
from .allocation import (
    allocate_loads as allocate_loads,
)
from .cascade import augment_cascade_plans
from .dc_service import first_dc_service_regression, preserves_dc_service
from .model import (
    STORAGE_ACTION_MINUTES,
    STORAGE_TARGET_TOLERANCE_PERCENT,
    STORAGE_TARGET_TOLERANCE_WH,
    ApplianceAdvisory,
    ApplianceAdvisoryReason,
    CascadeSourceSegment,
    LoadPlan,
    PlanInputs,
    PlanResult,
    SurplusLoadState,
    SystemConfig,
    Trajectory,
)
from .planning_rules import (
    _EPS as _EPS,
)
from .planning_rules import (
    AT_MAX_TOPUP_BAND_PERCENT as AT_MAX_TOPUP_BAND_PERCENT,
)
from .planning_rules import (
    GATE_TOPUP_MIN_WH as GATE_TOPUP_MIN_WH,
)
from .planning_rules import (
    IMPORT_ARTIFACT_SLACK_WH as IMPORT_ARTIFACT_SLACK_WH,
)
from .planning_rules import (
    MERGE_TERMINAL_RAMP_WH as MERGE_TERMINAL_RAMP_WH,
)
from .planning_rules import (
    PREDRAIN_PEAK_TOLERANCE_PERCENT as PREDRAIN_PEAK_TOLERANCE_PERCENT,
)
from .planning_rules import (
    _add_path_overheads as _add_path_overheads,
)
from .planning_rules import (
    _committed_hours as _committed_hours,
)
from .planning_rules import (
    _crossday_daytime_bet as _crossday_daytime_bet,
)
from .planning_rules import (
    _cumulative_charge_caps as _cumulative_charge_caps,
)
from .planning_rules import (
    _degrades_min_soc as _degrades_min_soc,
)
from .planning_rules import (
    _effective_load_power_w as _effective_load_power_w,
)
from .planning_rules import (
    _final_note as _final_note,
)
from .planning_rules import (
    _quantised_hours as _quantised_hours,
)
from .planning_rules import (
    _ramped_stress_floors as _ramped_stress_floors,
)
from .planning_rules import (
    _refill_index as _refill_index,
)
from .planning_rules import (
    _respects_cumulative_energy_cap as _respects_cumulative_energy_cap,
)
from .planning_rules import (
    _respects_path_power_limits as _respects_path_power_limits,
)
from .planning_rules import (
    _saturation_power_w as _saturation_power_w,
)
from .planning_rules import (
    _seamless_spill as _seamless_spill,
)
from .planning_rules import (
    _slot_serviceable as _slot_serviceable,
)
from .planning_rules import (
    _spread_energy as _spread_energy,
)
from .planning_rules import (
    _windowed_min_soc as _windowed_min_soc,
)
from .planning_rules import (
    _z4_reject as _z4_reject,
)
from .planning_rules import (
    pv_windows as pv_windows,
)
from .policy import FULL_SOC_TOLERANCE_PERCENT
from .reserve import reserve_planning_scope
from .series import insert_appliance_run
from .simulate import simulate
from .uncertainty import (
    effective_uncertainty as _effective_uncertainty,
)
from .uncertainty import (
    quantile_band_slots as quantile_band_slots,
)


def _threshold_merge_bound(config: SystemConfig, inputs: PlanInputs) -> int | None:
    """Effective merge truncation bound — the slot the T* scan truncates to, or
    None when the scan keeps the full horizon (F-NIGHT-RESCUE R4/R5 as gated by
    F-MERGE-HYSTERESIS).

    The scan truncates only once the stressed clip is DECISIVE
    (``margin_wh >= MERGE_TERMINAL_RAMP_WH``, where the terminal credit has fully
    faded to 0); below that it keeps the full horizon and merely fades the credit.
    Kept as the public helper for the R7 diagnostic ``threshold_horizon_end`` so
    the surfaced horizon always matches the horizon the scan actually used.
    """
    end, margin_wh = _threshold_merge_probe(config, inputs)
    if end is not None and margin_wh >= MERGE_TERMINAL_RAMP_WH:
        return end
    return None


def _search_lo(config: SystemConfig) -> int:
    """Lower bound of the threshold scan (shared with the merge probe)."""
    return int(
        math.ceil(
            max(
                config.control.inverter_min_soc_percent,
                config.battery.soc_min_percent + config.control.soc_buffer_percent,
            )
        )
    )


def _threshold_merge_probe(
    config: SystemConfig, inputs: PlanInputs
) -> tuple[int | None, float]:
    """Merge bound of the threshold scan AND the stressed clip-episode margin.

    One pessimistic no-loads sim (threshold at the scan's lower bound, PV
    stressed with the SAME per-slot vector Z4 uses — P10 where banded, alpha
    elsewhere) finds the first slot where the battery is FULL and clipping
    even under stress. Beyond that slot the trajectory is independent of
    today's threshold (merge principle, D-A4): post-merge economics — e.g.
    hoarding for a weak final day — must not leak into the pre-merge choice
    (live 2026-07-12 04:13: T* jumped 20->58 because weak Tuesday entered the
    horizon, although Sunday's guaranteed clip decoupled the night).

    Returns ``(end, margin_wh)``:

    - ``end`` is the R5-floored truncation slot (never below 6 slots — a
      1-2 h window would make T* jumpy), or None when no stressed clip exists
      (full-horizon behaviour unchanged) or the merge sits at the horizon end
      anyway.
    - ``margin_wh`` is the stressed export accumulated over one contiguous
      full-battery episode, starting at its first clipping slot (0.0 when no
      episode exists).  Measuring only that first slot made the result depend on
      an hourly boundary: a 37 Wh first slot followed by four 42 Wh clipping
      slots looked weaker than the 250 Wh ramp even though the episode was
      ~205 Wh.  Worse, such a marginal first episode masked every later,
      decisive episode.  That recreated the 20 <-> 60 % T* flapping live on
      2026-09-03 while the forecast showed multi-kWh export.  Episode energy is
      the stable measure of the same merge evidence; the earliest decisive
      episode wins, otherwise the strongest marginal episode drives the
      continuous terminal-credit ramp.
    """
    n = len(inputs.slots)
    if n == 0:
        return None, 0.0
    control = config.control
    stress_vec, _optimism, _band = _effective_uncertainty(
        inputs, control.predrain_pv_confidence, control.upper_pv_reserve
    )
    base = simulate(config, inputs, float(_search_lo(config)), pv_scale=stress_vec)
    soc_full = config.battery.soc_max_percent - FULL_SOC_TOLERANCE_PERCENT
    best_merge: int | None = None
    best_margin_wh = 0.0
    episode_start: int | None = None
    episode_export_wh = 0.0

    def finish_episode() -> tuple[int, float] | None:
        nonlocal best_merge, best_margin_wh, episode_start, episode_export_wh
        if episode_start is None:
            return None
        merge = episode_start
        margin_wh = episode_export_wh
        episode_start = None
        episode_export_wh = 0.0
        end = max(merge, 5)
        if end >= n - 1:
            return None
        if margin_wh >= MERGE_TERMINAL_RAMP_WH:
            return end, margin_wh
        if margin_wh > best_margin_wh + _EPS:
            best_merge = merge
            best_margin_wh = margin_wh
        return None

    for j, flow in enumerate(base.flows):
        full = flow.soc_end_percent >= soc_full
        if full:
            if flow.grid_export_wh > _EPS:
                if episode_start is None:
                    episode_start = j
                episode_export_wh += flow.grid_export_wh
            continue
        decisive = finish_episode()
        if decisive is not None:
            return decisive
    decisive = finish_episode()
    if decisive is not None:
        return decisive
    if best_merge is None:
        return None, 0.0
    end = max(best_merge, 5)  # R5 floor: at least 6 slots (indices 0..5)
    return end, best_margin_wh


def _terminal_credit_factor(full_factor: float, margin_wh: float) -> float:
    """Terminal-value credit factor for the merge-bounded scan (F-MERGE-HYSTERESIS).

    Ramps from the full round-trip credit ``full_factor`` (= ``eta_discharge *
    eta_inverter``) at stressed clip margin 0 down to 0 at ``margin_wh >=
    MERGE_TERMINAL_RAMP_WH``. Continuous and monotone non-increasing in the
    margin, so a 1 Wh input tick can no longer flip T* between the hoard and
    drain regimes — it nudges the credit by at most
    ``full_factor / MERGE_TERMINAL_RAMP_WH``. At the endpoints it reproduces the
    pre-ramp binary: the full credit when no stressed clip decouples tonight,
    zero once the clip is guaranteed.
    """
    if margin_wh <= 0.0:
        return full_factor
    ramp = min(margin_wh / MERGE_TERMINAL_RAMP_WH, 1.0)
    return full_factor * (1.0 - ramp)


def search_threshold(
    config: SystemConfig, inputs: PlanInputs
) -> tuple[float, Trajectory]:
    """Find an economic threshold, then prefer reserve-safe house supply.

    First minimize import − terminal value + export tiebreak. Before returning,
    F-HOUSE-SUPPLY checks whether a lower threshold avoids both import and
    export while preserving the nominal and pessimistic SOC reserves.

    Ties prefer the LOWER threshold ("Nutzen", D-A1b): drain the battery ahead
    of the next surplus rather than hoarding charge.

    MERGE-BOUNDED (F-NIGHT-RESCUE R4-R6): when a pessimistic sim shows the
    battery full and clipping at some slot, the candidate costs are evaluated
    on the horizon truncated there — the scalar T* must not couple tonight to
    post-merge regimes it cannot influence. On the truncated window the
    terminal-value credit is DROPPED (F2 v2): the battery is full at the merge
    point by construction, so crediting its end SOC is meaningless and, with a
    DC load breaking the terminal/import cancellation, made the threshold an
    ill-conditioned knife-edge that hoarded at soc_max (live 2026-07-12).

    HYSTERESIS (F-MERGE-HYSTERESIS, live 2026-07-24): the credit-drop AND the
    truncation used to be BINARY at the first Wh of stressed clip, flipping T*
    between two ~1.8 kWh-apart regimes on a bare SOC tick. The terminal credit
    now RAMPS with the stressed clip margin (`_terminal_credit_factor` over
    [0, MERGE_TERMINAL_RAMP_WH]) and the horizon is truncated only once that
    credit has fully faded — one well-separated edge, away from the jittery clip
    onset, so the endpoints (no clip / decisive clip) match the old behaviour
    exactly while the crossover is continuous.

    The returned base trajectory is ALWAYS full-horizon at the chosen threshold
    (the allocation gates keep differencing complete horizons, R6).
    """
    if config.reserve.enabled and config.support.coordinated:
        threshold = float(_search_lo(config))
        return threshold, simulate(config, inputs, threshold)

    battery = config.battery
    control = config.control

    lo = _search_lo(config)
    hi = int(math.floor(battery.soc_max_percent))

    merge_end, margin_wh = _threshold_merge_probe(config, inputs)
    full_factor = battery.eta_discharge * config.inverter.eta
    # F-NIGHT-RESCUE F2 v2 (fix for the 2026-07-12 midday T*=95): on a
    # merge-truncated window the battery is FULL at the merge point by
    # construction, so the terminal-value credit for the truncated end SOC is
    # meaningless — it double-credits energy the imminent, stress-confirmed clip
    # is guaranteed to refill. Worse, a DC load breaks the exact terminal/import
    # cancellation (DC is served from the battery at eta_discharge WITHOUT the
    # inverter, but the terminal credits at eta_discharge*eta_inverter), so the
    # credit turned T* into an ILL-CONDITIONED knife-edge that pinned a full-day
    # hoard at soc_max live. Dropping it leaves cost = import + tiebreak*export,
    # which is MONOTONIC in the threshold, so the scan deterministically drains
    # to `lo` before the clip — the operator's principle exactly.
    #
    # F-MERGE-HYSTERESIS (2026-07-24): that drop is now RAMPED, not binary. Both
    # the credit AND the horizon truncation used to flip the instant the first
    # stressed clip crossed 0 Wh, so a bare SOC tick around the clip onset flapped
    # T* between the two ~1.8 kWh-apart regimes. The credit now fades linearly
    # over [0, MERGE_TERMINAL_RAMP_WH] of stressed clip margin, and the horizon is
    # truncated only once the credit has fully faded (margin >= the ramp) — a
    # single, well-separated edge that the clip-onset jitter never reaches.
    merge_active = merge_end is not None and margin_wh >= MERGE_TERMINAL_RAMP_WH
    if merge_end is not None:
        terminal_factor = _terminal_credit_factor(full_factor, margin_wh)
        scan_inputs = (
            replace(inputs, slots=inputs.slots[: merge_end + 1])
            if merge_active
            else inputs
        )
    else:
        scan_inputs = inputs
        terminal_factor = full_factor

    best_threshold = float(hi)
    best_cost = math.inf
    best_unserved = math.inf
    best_traj: Trajectory | None = None

    for candidate in range(lo, hi + 1):
        traj = simulate(config, scan_inputs, float(candidate))
        end_wh = battery.energy_wh(traj.end_soc_percent)
        cost = (
            traj.total_import_wh
            - terminal_factor * end_wh
            + control.export_tiebreak * traj.total_export_wh
        )
        unserved = (
            sum(f.unserved_dc_wh for f in traj.flows)
            if config.support.coordinated
            else 0.0
        )
        if unserved < best_unserved - _EPS or (
            abs(unserved - best_unserved) <= _EPS and cost < best_cost - _EPS
        ):
            # R3: an unsupplied DC rail cannot win by having a lower bill.
            best_unserved = unserved
            best_cost = cost
            best_threshold = float(candidate)
            best_traj = traj

    if merge_active or best_traj is None:
        # Truncated scan (or degenerate empty horizon): the caller needs the
        # FULL-horizon no-loads base at the chosen threshold. When the scan kept
        # the full horizon (no merge, or a faded-credit clip below the ramp)
        # best_traj is already full-horizon at best_threshold — no rebuild.
        best_traj = simulate(config, inputs, best_threshold)
    return _prefer_house_supply(config, inputs, best_threshold, best_traj)


def _prefer_house_supply(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    baseline: Trajectory,
) -> tuple[float, Trajectory]:
    """Prefer lower-import house supply only within existing reserve rules.

    F-HOUSE-SUPPLY R1/R2: this is a Pareto improvement of the economic
    decision, not a replacement threshold policy. Requiring a future 80%
    recharge here changed even no-export and merge-bounded decisions and
    broke the allocator's established reserve/peak contracts. Every newly
    accepted alternative instead preserves both floors over the FULL horizon,
    including nights after the first recharge.
    """
    lo = _search_lo(config)
    if not inputs.slots or threshold <= lo:
        return threshold, baseline
    forced = (True,) * len(inputs.slots)
    support = config.support
    dc24 = forced if support.configured and support.dc24_forced_on else None
    dc48 = forced if support.configured and support.dc48_forced_on else None
    reference = simulate(
        config, inputs, threshold, dc24_schedule=dc24, dc48_schedule=dc48
    )
    if reference.total_export_wh <= _EPS or reference.total_import_wh <= _EPS:
        return threshold, baseline
    stress, _, _ = _effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.control.upper_pv_reserve
    )
    battery_floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    floors = [
        max(battery_floor, floor)
        for floor in _ramped_stress_floors(config, inputs, stress)
    ]
    selected = threshold
    best_import = reference.total_import_wh
    for candidate in range(lo, math.ceil(threshold)):
        trial = simulate(
            config, inputs, float(candidate), dc24_schedule=dc24, dc48_schedule=dc48
        )
        # Less export due solely to conversion loss is not useful house supply.
        # Ascending candidates retain the lower threshold on equal import.
        if (
            trial.total_import_wh >= best_import - _EPS
            or trial.total_export_wh >= reference.total_export_wh - _EPS
        ):
            continue
        if any(
            flow.soc_end_percent < floor - _EPS
            for flow, floor in zip(trial.flows, floors, strict=True)
        ):
            continue
        stressed = simulate(
            config,
            inputs,
            float(candidate),
            dc24_schedule=dc24,
            dc48_schedule=dc48,
            pv_scale=stress,
        )
        if any(
            flow.soc_end_percent < floor - _EPS
            for flow, floor in zip(stressed.flows, floors, strict=True)
        ):
            continue
        selected = float(candidate)
        best_import = trial.total_import_wh
    # R3: comparisons include the same forced support, but public baseline
    # metrics and subsequent allocation retain their WITHOUT-support contract.
    if selected == threshold:
        return threshold, baseline
    return selected, simulate(config, inputs, selected)


def plan_feedin(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    extra_ac: tuple[float, ...],
    alloc_traj: Trajectory,
    *,
    load_plans: Sequence[LoadPlan] = (),
    decisions: list[tuple[int, str]] | None = None,
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
) -> tuple[tuple[float, ...], dict[str, float]]:
    """Pre-shift the UNAVOIDABLE export into the morning surplus (F-FEEDIN).

    The residual export of the post-allocation trajectory (`alloc_traj`,
    median forecast) is the candidate export target. R1 additionally requires
    continuous load service until full SOC: quantisation leftovers alone are
    not proof of exhausted consumption. This pass books eligible energy as early
    feed-in instead: PV surplus passed straight through to the grid while the
    battery idles (requirement 1: the battery is NEVER actively discharged
    for feed-in — step_hour has no mechanism for it and clamps feed-in to the
    slot surplus). Total export is invariant; only its timing moves off the
    midday peak.

    Rules (operator decisions, docs/F-FEEDIN.md):
    - Target per calendar day = the day's residual grid_export of alloc_traj
      (requirement 2: the MEDIAN amount, no stress scaling of the target).
    - Slots ascending from slot 0: book only while the day's remaining amount
      is open, the trial SOC at slot start is above `min_soc_percent` (the
      absolute floor, requirement 3) and below soc_max (a full battery
      exports naturally — setpoint 0), and the slot has PV surplus (same
      notion the simulation uses: PV - house AC - extra AC, before charging).
    - Rate per slot = remaining / max(hours until the day's `deadline_hour`,
      slot duration), capped at min(max_w, slot surplus) (requirement 5).
      The deadline is HARD (operator decision 2026-08-08): slots starting
      at/after the day's deadline get NO booking — leftover energy is not
      worked off afterwards, it exports naturally at midday. Every booking
      is re-simulated into the trial so the next slot's floor and soc_max
      checks see the reduced charge.
    - Manual mode (requirement 9): when `manual_w` is set, the operator owns
      the setpoint. Today's slots then book exactly that value (no daily
      target, no deadline — the plan and the chart must mirror reality), 0
      books nothing at all; the following days plan automatically again
      only if automatic_enabled (the runtime pause outlasts manual mode).
    - Z4 as a BRAKE only (requirement 3): the final WITH-feed-in series is
      re-simulated under the stressed PV vector (same P10/alpha machinery as
      the `_z4_reject` / `_ramped_stress_floors` gates); on a floor violation
      the LATEST feed-in slot at/before the violation is handed back,
      iterated (bounded: one slot per pass) until the floors hold.

    Manually forced support schedules are carried through every re-simulation
    so the target and all SOC/floor checks stay on the same trajectory as the
    surplus allocator. Returns the booked Wh per slot plus the per-day booked
    Wh. The caller re-simulates the published trajectory with the series; the
    `prevented_export_by_day_wh` counterfactual deliberately keeps comparing
    base vs. alloc WITHOUT feed-in.
    """
    if config.reserve.enabled and config.feedin.manual_w is None:
        if decisions is not None:
            decisions.extend(
                (i, "reserve_no_emergency_benefit") for i in range(len(inputs.slots))
            )
        return (0.0,) * len(inputs.slots), {}

    feedin = (
        replace(config.feedin, automatic_enabled=False)
        if config.reserve.enabled
        else config.feedin
    )
    battery = config.battery
    n = len(inputs.slots)
    booked = [0.0] * n
    manual_w = feedin.manual_w
    reasons = ["no_residual_export"] * n

    def explain():
        if decisions is not None:
            decisions.extend(enumerate(reasons))

    if n == 0 or (manual_w is None and alloc_traj.total_export_wh <= _EPS):
        explain()
        return tuple(booked), {}

    day_slots: dict = {}
    for i, slot in enumerate(inputs.slots):
        day_slots.setdefault(slot.start.date(), []).append(i)
    today = inputs.slots[0].start.date()

    trial = alloc_traj
    for day, idxs in day_slots.items():
        manual_today = manual_w is not None and day == today
        if manual_today:
            for j in idxs:
                reasons[j] = "manual_setpoint"
            # No daily target in manual mode — the operator's value books
            # into every servable slot left today.
            remaining = math.inf
            if manual_w is None or manual_w <= _EPS:
                continue  # operator set 0 W: no booking, no chart lane
        else:
            if not feedin.automatic_enabled:
                for j in idxs:
                    reasons[j] = "runtime_paused"
                continue  # R8: pause persists across the whole forecast horizon
            remaining = sum(alloc_traj.flows[j].grid_export_wh for j in idxs)
        if remaining <= _EPS:
            continue
        for i in idxs:
            if remaining <= _EPS:
                for j in idxs[idxs.index(i) :]:
                    reasons[j] = "export_budget_exhausted"
                break
            slot = inputs.slots[i]
            soc_start = trial.flows[i].soc_start_percent
            # Requirement 1 says the battery IDLES during feed-in — nothing in,
            # nothing out. The raw AC surplus is not what `step_hour` can
            # actually give away, so booking it broke that promise twice
            # (operator finding 2026-08-04, live 08-04 07:00):
            #   * the INVERTER STANDBY is part of the AC draw the simulation
            #     subtracts (`ac_total`), but not of this term — the booking
            #     then demands power that does not exist and is silently
            #     clamped (booked 439 Wh, exported 424 Wh, the 15 Wh standby).
            #   * the 48 V BUS LOAD is settled BEFORE the AC balance and comes
            #     out of the STORE. Unless the same slot charges it back, the
            #     battery pays for the bus while the entire surplus goes to the
            #     grid — the SOC sank 31.3 -> 29.8 %, through the feature's own
            #     min_soc floor (R5), which is only tested at slot START.
            # Both are reserved here, so the booked value is servable AND the
            # slot ends battery-neutral. The give-back is the AC energy the
            # charger needs to restore what the bus drew, plus its standby
            # (the charger compensates its own standby, see step_hour).
            standby_wh = (
                config.inverter.standby_power_w * slot.duration
                if soc_start > threshold
                else 0.0
            )
            bus_draw_wh = max(0.0, trial.flows[i].battery_discharge_wh)
            giveback_wh = (
                bus_draw_wh / (config.charger.eta * battery.eta_charge)
                + config.charger.standby_power_w * slot.duration
                if bus_draw_wh > _EPS
                else 0.0
            )
            surplus_wh = (
                slot.pv_wh - slot.ac_wh - extra_ac[i] - standby_wh - giveback_wh
            )
            if surplus_wh <= _EPS:
                reasons[i] = "no_power_surplus"
                continue
            if soc_start <= feedin.min_soc_percent + _EPS:
                reasons[i] = "feedin_soc_floor"
                continue
            if soc_start >= battery.soc_max_percent - _EPS:
                reasons[i] = "battery_already_full"
                continue
            deadline = slot.start.replace(
                hour=feedin.deadline_hour, minute=0, second=0, microsecond=0
            )
            if manual_today:
                rate_w = min(manual_w, surplus_wh / slot.duration)
            elif slot.start >= deadline:
                reasons[i] = "feedin_deadline"
                # HARD deadline (operator decision 2026-08-08): no deliberate
                # feed-in after the configured hour — the leftover exports
                # naturally at midday instead of being worked off fast.
                continue
            else:
                hours_left = (deadline - slot.start).total_seconds() / 3600.0
                denom = max(hours_left, slot.duration)
                rate_w = min(
                    remaining / denom, feedin.max_w, surplus_wh / slot.duration
                )
            if not manual_today and not _continuous_loads_cover_to_max(
                config, inputs, load_plans, trial, i
            ):
                reasons[i] = "continuous_load_or_peak_unproven"
                continue
            take_wh = min(rate_w * slot.duration, remaining)
            booked[i] = take_wh
            # Validate the delayed peak before accepting this booking.
            candidate = simulate(
                config,
                inputs,
                threshold,
                extra_ac_wh=extra_ac,
                dc24_schedule=dc24_schedule,
                dc48_schedule=dc48_schedule,
                feedin_wh=tuple(booked),
            )
            if not manual_today and not preserves_dc_service(
                candidate, baseline=alloc_traj, accepted=trial
            ):
                booked[i] = 0.0
                reasons[i] = "dc_service"
                continue
            if not manual_today and not _continuous_loads_cover_to_max(
                config, inputs, load_plans, candidate, i
            ):
                booked[i] = 0.0
                reasons[i] = "delayed_peak_uncovered"
                continue
            reasons[i] = (
                "manual_setpoint" if manual_today else "loads_exhausted_to_maximum"
            )
            trial = candidate
            remaining -= take_wh

    # Z4 brake: the stress must not push the reserve through the ramped floors
    # — same per-slot P10/alpha vector the allocation gates use. The relief
    # clause (`_z4_reject`) keeps dips the no-feed-in baseline already has.
    control = config.control
    stress_vec, _optimism, _band = _effective_uncertainty(
        inputs, control.predrain_pv_confidence, control.upper_pv_reserve
    )
    if any(s < 1.0 - _EPS for s in stress_vec) and any(b > _EPS for b in booked):
        floors = _ramped_stress_floors(config, inputs, stress_vec)
        base_stress = simulate(
            config,
            inputs,
            threshold,
            extra_ac_wh=extra_ac,
            dc24_schedule=dc24_schedule,
            dc48_schedule=dc48_schedule,
            pv_scale=stress_vec,
            # Manual setpoints are external intent, not removable automatic
            # preparation. The stress comparison only governs optional export.
            feedin_wh=tuple(
                b
                if manual_w is not None and inputs.slots[j].start.date() == today
                else 0.0
                for j, b in enumerate(booked)
            ),
        )
        for _ in range(sum(b > _EPS for b in booked) + 1):
            automatic = [
                j
                for j, energy in enumerate(booked)
                if energy > _EPS
                and not (manual_w is not None and inputs.slots[j].start.date() == today)
            ]
            if not automatic:
                break  # External manual intent has no removable booking.
            stressed = simulate(
                config,
                inputs,
                threshold,
                extra_ac_wh=extra_ac,
                dc24_schedule=dc24_schedule,
                dc48_schedule=dc48_schedule,
                feedin_wh=tuple(booked),
                pv_scale=stress_vec,
            )
            dc_bad = first_dc_service_regression(base_stress, stressed)
            bad = next(
                (
                    j
                    for j in range(n)
                    if _z4_reject(
                        stressed.flows[j].soc_end_percent,
                        floors[j],
                        base_stress.flows[j].soc_end_percent,
                    )
                    or j == dc_bad
                ),
                None,
            )
            if bad is None:
                break
            # Only feed-in at slots <= `bad` can deepen slot `bad`; hand back
            # the latest of them (requirement 3: latest-first).
            latest = max(
                (j for j in automatic if j <= bad),
                # The coordinated reserve can anticipate a later export and
                # change earlier support. Hand back an automatic booking even
                # then, rather than cancelling external manual intent.
                default=automatic[-1],
            )
            booked[latest] = 0.0
            reasons[latest] = (
                "dc_service"
                if not preserves_dc_service(stressed, baseline=base_stress)
                else "stress_reserve"
            )

    by_day: dict[str, float] = {}
    for i, wh in enumerate(booked):
        if wh > _EPS:
            day = inputs.slots[i].start.date().isoformat()
            by_day[day] = by_day.get(day, 0.0) + wh
    explain()
    return tuple(booked), by_day


def appliance_windows(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    extra_ac: tuple[float, ...],
    planned_trajectory: Trajectory,
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
    feedin_wh: tuple[float, ...] | None = None,
    *,
    advisories: dict[str, ApplianceAdvisory] | None = None,
) -> dict[str, bool]:
    """Advisor (G3): could a full appliance run start now without extra import?

    The hypothetical run is evaluated under the SAME support-PSU schedules as
    the planned trajectory it is compared against — otherwise the advisor
    simulates the run with the PSUs off (their default) while the baseline had
    them on, and gives false window advisories whenever support is active
    (e.g. winter operation with a forced 48 V PSU). The same holds for the
    booked feed-in series (F-FEEDIN): the trial must see the pass-through so
    the comparison stays apples-to-apples.

    ``advisories`` optionally receives the failed gates from these same trials.
    Disabled advisors have no entry; the HA layer explains configuration and
    live safety vetoes separately from this forecast decision.
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
            feedin_wh=feedin_wh,
        )
        # An empty horizon must NOT advise a start (code review 2026-07): with
        # no slots the trial imports 0 Wh and never degrades the min SOC, so
        # both gates below pass vacuously and the advisor used to green-light
        # a run on zero evidence.
        horizon_ok = (
            bool(traj.flows)
            and sum(slot.duration for slot in inputs.slots) + _EPS
            >= appliance.run_duration_h
        )
        import_ok = traj.total_import_wh <= planned_trajectory.total_import_wh + _EPS
        soc_ok = not _degrades_min_soc(traj, planned_trajectory, buffer_floor)
        dc_ok = preserves_dc_service(traj, baseline=planned_trajectory)
        allowed = horizon_ok and import_ok and soc_ok and dc_ok
        windows[appliance.appliance_id] = allowed
        if advisories is not None:
            reasons: list[ApplianceAdvisoryReason] = []
            if not horizon_ok:
                reasons.append("forecast_horizon_short")
            if not import_ok:
                reasons.append("extra_grid_import")
            if not soc_ok:
                reasons.append("soc_condition")
            if not dc_ok:
                reasons.append("dc_service")
            advisories[appliance.appliance_id] = ApplianceAdvisory(
                allowed, tuple(reasons)
            )
    return windows


def support_escalation(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold: float,
    extra_ac: tuple[float, ...],
    trajectory: Trajectory,
    feedin_wh: tuple[float, ...] | None = None,
) -> tuple[tuple[bool, ...], tuple[bool, ...], Trajectory]:
    """Last-resort protection (D-A9): shift DC loads to grid PSUs when the
    battery would otherwise fall through the buffer floor / hard minimum.

    Manually overridden PSUs (F-N2, `dc24_forced_on`/`dc48_forced_on`) are
    treated as permanently active: the trajectory must reflect the real
    winter operation even though the executor does not control them.

    `feedin_wh` (F-FEEDIN) is the booked early feed-in series the input
    trajectory was simulated with; every re-simulation below must keep it so
    the published trajectory stays the WITH-feed-in one (neutral None =
    pre-feature behaviour, bit-identical).
    """
    n = len(inputs.slots)
    dc24 = [False] * n
    dc48 = [False] * n
    if not config.support.configured or n == 0:
        return tuple(dc24), tuple(dc48), trajectory

    if config.support.coordinated:
        return (
            tuple(f.support_dc24_start for f in trajectory.flows),
            tuple(f.support_dc48_start for f in trajectory.flows),
            trajectory,
        )

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
            config,
            inputs,
            threshold,
            extra_ac_wh=extra_ac,
            dc48_schedule=tuple(dc48),
            feedin_wh=feedin_wh,
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
        feedin_wh=feedin_wh,
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
                feedin_wh=feedin_wh,
            )

    return tuple(dc24), tuple(dc48), traj


def _plan_legacy(
    config: SystemConfig,
    inputs: PlanInputs,
    *,
    direct_surplus_only_load_ids: frozenset[str] = frozenset(),
    remaining_energy_overrides: dict[str, float] | None = None,
    recovery_targets_wh: dict[str, float] | None = None,
    priority_terminals: dict[str, str] | None = None,
    power_caps_w: dict[str, float] | None = None,
    path_power_limits: tuple[tuple[tuple[tuple[str, float], ...], float], ...] = (),
    cumulative_energy_caps_wh: dict[str, tuple[float, ...]] | None = None,
    recovery_cumulative_caps_wh: dict[str, tuple[float, ...]] | None = None,
    path_overheads: tuple[tuple[tuple[str, ...], float], ...] = (),
) -> PlanResult:
    """One complete planning run — single consistent trajectory out (P2).

    The `stressed_min_soc_percent` diagnostic (§3.5, v2) reports the WINDOWED
    lower-buffer reserve that the Z4 gate actually protects: the earliest
    pass-2/pass-3 slot booked for ANY load is treated as the deepest bet
    (F-GATE-PARITY GP-R4 — the stress gate binds energy-limited bets too;
    F-PREDRAIN-BLOCK — the continuous load's block is a bet through the same
    gate stack), and the diagnostic is the stressed windowed min SOC over that
    bet's recovery window [i0, recovery] under the FINAL accepted series —
    stressed with the same per-slot vector as the gate (P10 bands where
    present, alpha elsewhere; F-QUANTILE-BANDS R5). It is None when no slot is
    stressed (alpha == 1.0 and no P10 evidence) or when no load at all has a
    pass-2/pass-3 booking (nothing was bet, so there is no reserve to report).
    """
    control = config.control
    threshold, base_traj = search_threshold(config, inputs)
    # F-N2 manual support is a KNOWN, horizon-wide operating condition, not an
    # automatic emergency decision.  Make those fixed schedules visible to the
    # allocator so surplus created by their higher SOC path can be absorbed by
    # loads instead of appearing only after allocation as stranded export.
    n = len(inputs.slots)
    forced_on = (True,) * n
    forced_dc24 = (
        forced_on
        if config.support.configured and config.support.dc24_forced_on
        else None
    )
    forced_dc48 = (
        forced_on
        if config.support.configured and config.support.dc48_forced_on
        else None
    )
    allocation_base = base_traj
    if forced_dc24 is not None or forced_dc48 is not None:
        allocation_base = simulate(
            config,
            inputs,
            threshold,
            dc24_schedule=forced_dc24,
            dc48_schedule=forced_dc48,
        )
    load_plans, extra_ac, traj = allocate_loads(
        config,
        inputs,
        threshold,
        allocation_base,
        dc24_schedule=forced_dc24,
        dc48_schedule=forced_dc48,
        direct_surplus_only_load_ids=direct_surplus_only_load_ids,
        remaining_energy_overrides=remaining_energy_overrides,
        power_caps_w=power_caps_w,
        path_power_limits=path_power_limits,
        path_overheads=path_overheads,
        cumulative_energy_caps_wh=cumulative_energy_caps_wh,
        recovery_cumulative_caps_wh=recovery_cumulative_caps_wh,
    )
    # `alloc_traj` is the real allocation trajectory, including any manually
    # forced support.  R4 deliberately retains its established WITHOUT-support
    # counterfactual, so reconstruct that one from the accepted load series only
    # when fixed support changed the gate baseline.  The import-trade diagnostic
    # below instead compares the real supported trajectories because those are
    # the trajectories protected by the allocator's absolute 50 Wh gate.
    alloc_traj = traj
    if recovery_targets_wh:
        load_plans, extra_ac, alloc_traj = _allocate_recovery_after_continuous_loads(
            config,
            inputs,
            threshold,
            load_plans,
            extra_ac,
            alloc_traj,
            recovery_targets_wh,
            dc24_schedule=forced_dc24,
            dc48_schedule=forced_dc48,
            power_caps_w=power_caps_w,
            path_power_limits=path_power_limits,
            path_overheads=path_overheads,
            cumulative_energy_caps_wh=recovery_cumulative_caps_wh,
            priority_terminals=priority_terminals,
        )
        # The terminal's continuous block can also expose residual export for
        # ordinary member top-up. Recovery for every member has already had
        # both strict and final-dwell passes, so this phase may only consume
        # export that is visibly still present in each covered slot.
        if remaining_energy_overrides:
            load_plans, extra_ac, alloc_traj = (
                _allocate_recovery_after_continuous_loads(
                    config,
                    inputs,
                    threshold,
                    load_plans,
                    extra_ac,
                    alloc_traj,
                    remaining_energy_overrides,
                    dc24_schedule=forced_dc24,
                    dc48_schedule=forced_dc48,
                    power_caps_w=power_caps_w,
                    path_power_limits=path_power_limits,
                    path_overheads=path_overheads,
                    cumulative_energy_caps_wh=cumulative_energy_caps_wh,
                    priority_terminals=priority_terminals,
                    visible_export_only=True,
                    today_only=False,
                    repeat_final_rounding=False,
                    phase_name="top-up",
                )
            )
        traj = alloc_traj
    metric_alloc_traj = (
        simulate(config, inputs, threshold, extra_ac_wh=extra_ac)
        if forced_dc24 is not None or forced_dc48 is not None
        else alloc_traj
    )
    # F-FEEDIN: pre-shift the unavoidable residual export into the morning
    # surplus. Runs AFTER allocate_loads (it needs the alloc residual = the
    # unavoidable amount, requirement 2) and BEFORE support escalation, so the
    # escalation's re-simulations keep the booked series. Neutral default
    # (disabled or 0 W cap) short-circuits: no extra simulation, no schedule,
    # and the trajectory chain stays bit-identical to the pre-feature plan.
    feedin_wh: tuple[float, ...] | None = None
    feedin_by_day_wh: dict[str, float] = {}
    feedin_decisions: list[tuple[int, str]] = []
    if config.feedin.enabled and config.feedin.max_w > _EPS:
        feedin_wh, feedin_by_day_wh = plan_feedin(
            config,
            inputs,
            threshold,
            extra_ac,
            alloc_traj,
            load_plans=load_plans,
            decisions=feedin_decisions,
            dc24_schedule=forced_dc24,
            dc48_schedule=forced_dc48,
        )
        # The published trajectory is the WITH-feed-in one (requirement: the
        # flat morning SOC must show in the forecast); the pass-through moves
        # export earlier 1:1, so the totals stay invariant.
        traj = simulate(
            config,
            inputs,
            threshold,
            extra_ac_wh=extra_ac,
            dc24_schedule=forced_dc24,
            dc48_schedule=forced_dc48,
            feedin_wh=feedin_wh,
        )
    dc24, dc48, traj = support_escalation(
        config, inputs, threshold, extra_ac, traj, feedin_wh=feedin_wh
    )
    advisories: dict[str, ApplianceAdvisory] = {}
    windows = appliance_windows(
        config,
        inputs,
        threshold,
        extra_ac,
        traj,
        dc24_schedule=dc24,
        dc48_schedule=dc48,
        feedin_wh=feedin_wh,
        advisories=advisories,
    )

    if traj.flows:
        max_soc = traj.max_soc_percent
        hours_to_max = (
            next(i for i, f in enumerate(traj.flows) if f.soc_end_percent >= max_soc)
            + 1
        )
        inverter_on = (
            traj.flows[0].inverter_start
            if config.support.coordinated
            else traj.flows[0].inverter_on
        )
    else:
        max_soc = inputs.start_soc_percent
        hours_to_max = 0
        inverter_on = False

    # F-PREDRAIN diagnostics (§3.5): the traded import, the stressed reserve, and
    # the derived PV absorption windows (WP4 exposes these as sensor attributes).
    import_trade_used_wh = max(
        0.0, alloc_traj.total_import_wh - allocation_base.total_import_wh
    )
    stressed_min_soc: float | None = None
    alpha = control.predrain_pv_confidence
    # F-QUANTILE-BANDS R5: the diagnostic stresses with the SAME per-slot
    # vector as the Z4 gate (empirical P10 where bands exist, alpha elsewhere),
    # so what the sensor reports is what the gate protected.
    stress_vec, _optimism_vec, _band_slots = _effective_uncertainty(
        inputs, alpha, control.upper_pv_reserve
    )
    if any(s < 1.0 - _EPS for s in stress_vec) and alloc_traj.flows:
        # Windowed stressed reserve of the deepest bet: the earliest slot booked
        # for ANY load via the bet passes — pass 2 (energy-limited) or pass 3
        # (the continuous load's pre-drain block, F-PREDRAIN-BLOCK) — evaluated
        # over its recovery window under the FINAL accepted series (§3.5 v2).
        # Since F-GATE-PARITY the Z4 stress gate binds energy-limited bets too,
        # so their bookings belong in the diagnostic — what the sensor reports
        # is what the gate protected. None when no bet booking exists at all.
        n = len(inputs.slots)
        booked = [
            alloc[0]
            for lp in load_plans
            for alloc in lp.allocations
            if alloc[2] in (2, 3)
        ]
        if booked:
            i0 = min(booked)
            # Same settlement rule as the Z4 gate (F-STRICT-SURPLUS R3): the
            # deepest bet's window ends where the FINAL accepted series refills
            # to soc_max — the sensor reports what the gate protected.
            recovery = _refill_index(
                alloc_traj,
                i0,
                config.battery.soc_max_percent - FULL_SOC_TOLERANCE_PERCENT,
            )
            scale_vec = [
                stress_vec[j] if i0 <= j <= recovery else 1.0 for j in range(n)
            ]
            stressed = simulate(
                config,
                inputs,
                threshold,
                extra_ac_wh=extra_ac,
                dc24_schedule=forced_dc24,
                dc48_schedule=forced_dc48,
                pv_scale=scale_vec,
            )
            stressed_min_soc = _windowed_min_soc(stressed, i0, recovery)
    window_ends = {
        day.isoformat(): inputs.slots[last].hour_of_day
        for day, (_first, last) in pv_windows(
            inputs, control.strong_pv_cutoff_w, control.pv_window_end_hour
        ).items()
    }
    # F-STRICT-SURPLUS R4: per-day export the loads PREVENTED — base (no loads)
    # minus alloc (with loads), BOTH without support.  `metric_alloc_traj`
    # removes even manually forced support from this established R4 diagnostic,
    # so a winter PSU never deflates the counterfactual. The dashboard shows
    # max(0, ...) per day.
    base_exp_day: dict[str, float] = {}
    alloc_exp_day: dict[str, float] = {}
    for slot, base_flow, alloc_flow in zip(
        inputs.slots, base_traj.flows, metric_alloc_traj.flows, strict=True
    ):
        day = slot.start.date().isoformat()
        base_exp_day[day] = base_exp_day.get(day, 0.0) + base_flow.grid_export_wh
        alloc_exp_day[day] = alloc_exp_day.get(day, 0.0) + alloc_flow.grid_export_wh
    prevented_export_by_day = {
        day: max(0.0, base_exp_day[day] - alloc_exp_day.get(day, 0.0))
        for day in base_exp_day
    }
    # F-NIGHT-RESCUE R7: surface the merge bound the T* scan used, so the
    # 04:13-class events ("why did the threshold jump?") are visible.
    merge_end, merge_margin_wh = (
        (None, 0.0)
        if config.reserve.enabled
        else _threshold_merge_probe(config, inputs)
    )
    if merge_margin_wh < MERGE_TERMINAL_RAMP_WH:
        merge_end = None
    threshold_horizon_end = None
    if merge_end is not None:
        merge_slot = inputs.slots[merge_end]
        threshold_horizon_end = merge_slot.start + timedelta(hours=merge_slot.duration)

    # F-FEEDIN: the executor-facing per-slot power (W); empty when disabled.
    feedin_schedule_w = (
        tuple(feedin_wh[i] / inputs.slots[i].duration for i in range(len(inputs.slots)))
        if feedin_wh is not None
        else ()
    )

    return PlanResult(
        threshold_percent=threshold,
        inverter_on=inverter_on,
        trajectory=traj,
        load_plans=tuple(load_plans),
        appliance_windows=windows,
        appliance_advisories=advisories,
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
        threshold_horizon_end=threshold_horizon_end,
        threshold_merge_margin_wh=merge_margin_wh,
        prevented_export_by_day_wh=prevented_export_by_day,
        feedin_schedule_w=feedin_schedule_w,
        feedin_by_day_wh=feedin_by_day_wh,
        feedin_decisions=tuple(feedin_decisions)
        if feedin_decisions
        else tuple(
            (
                i,
                "feature_disabled"
                if not config.feedin.enabled
                else "feedin_power_limit",
            )
            for i in range(n)
        ),
    )


def plan(config: SystemConfig, inputs: PlanInputs) -> PlanResult:
    """Plan with a call-scoped reserve cache, also shared by nested cascades."""
    if not config.reserve.enabled:
        return _plan_in_scope(config, inputs)
    with reserve_planning_scope():
        return _plan_in_scope(config, inputs)


def _plan_in_scope(config: SystemConfig, inputs: PlanInputs) -> PlanResult:
    """Retain the exact planning path and its existing cascade ordering."""
    if config.reserve.enabled:
        config = replace(
            config,
            control=replace(
                config.control, upper_pv_reserve=config.reserve.upper_pv_factor
            ),
        )
    if not config.cascades:
        return _plan_legacy(config, inputs)

    # F-CASCADE-STORAGE priority/day contract (operator decision 2026-09-02):
    # the complete cascade occupies its first participant's global position.
    # Its direct terminal is one lossless consumer and therefore comes first;
    # Root charging then runs in TWO explicit phases: every member to its
    # recovery target, only then every member toward its normal charge target.
    # A single load-outer pass cannot express that ordering: it used to fill
    # B1 to 80 % while B2 remained at 20 % in the live Bad cascade.
    loads_by_id = {load.load_id: load for load in config.loads}
    cascade_by_participant = {
        load_id: cascade
        for cascade in config.cascades
        for load_id in (
            *(member.load_id for member in cascade.members),
            cascade.terminal_load_id,
        )
    }
    member_by_id = {
        member.load_id: member
        for cascade in config.cascades
        for member in cascade.members
    }
    priority_loads = []
    emitted_cascades: set[str] = set()
    for load in config.loads:
        cascade = cascade_by_participant.get(load.load_id)
        if cascade is None:
            priority_loads.append(load)
            continue
        if cascade.cascade_id in emitted_cascades:
            continue
        emitted_cascades.add(cascade.cascade_id)
        member_ids = tuple(member.load_id for member in cascade.members)
        priority_loads.extend(
            (loads_by_id[cascade.terminal_load_id],)
            + tuple(loads_by_id[load_id] for load_id in member_ids)
        )
    # A configured five-minute device dwell is a physical minimum, not a
    # request to optimize every 20 Wh residual with another storage switch.
    legacy_config = replace(
        config,
        loads=tuple(
            replace(
                load,
                min_runtime_min=max(STORAGE_ACTION_MINUTES, load.min_runtime_min),
                gate_stop_capable=False,
            )
            if load.load_id in member_by_id
            else load
            for load in priority_loads
        ),
        cascades=(),
    )
    result_order = {load.load_id: index for index, load in enumerate(config.loads)}
    cascade_member_ids = frozenset(
        member.load_id for cascade in config.cascades for member in cascade.members
    )
    cascade_power_caps_w = {
        member.load_id: member.max_charge_power_w
        for cascade in config.cascades
        for member in cascade.members
        if member.max_charge_power_w is not None
    }
    original_states = {state.load_id: state for state in inputs.load_states}
    cascade_by_member = {
        member.load_id: cascade
        for cascade in config.cascades
        for member in cascade.members
    }

    def replan(
        trial_inputs: PlanInputs,
        aux_segments: tuple[CascadeSourceSegment, ...] = (),
    ) -> PlanResult:
        states = {state.load_id: state for state in trial_inputs.load_states}
        path_power_limits = tuple(
            (
                tuple(
                    (
                        load_id,
                        _effective_load_power_w(
                            loads_by_id[load_id],
                            states.get(load_id, SurplusLoadState(load_id=load_id)),
                            cascade_power_caps_w,
                        ),
                    )
                    for load_id in (
                        *(
                            downstream.load_id
                            for downstream in cascade.members[index + 1 :]
                        ),
                        cascade.terminal_load_id,
                    )
                ),
                member.max_passthrough_power_w,
            )
            for cascade in config.cascades
            for index, member in enumerate(cascade.members)
            if member.max_passthrough_power_w is not None
        )
        path_overheads = tuple(
            (
                tuple(m.load_id for m in cascade.members[index + 1 :])
                + (cascade.terminal_load_id,),
                member.output_overhead_w,
            )
            for cascade in config.cascades
            for index, member in enumerate(cascade.members)
            if member.output_overhead_w > 0
        )
        remaining_overrides: dict[str, float] = {}
        recovery_targets: dict[str, float] = {}
        for load_id, member in member_by_id.items():
            load = loads_by_id[load_id]
            state = states.get(load_id)
            soc = (
                state.soc_percent
                if state is not None and state.soc_percent is not None
                else 0.0
            )
            # SurplusLoad.remaining_energy_wh is stored-energy based.  A
            # cascade member's schedule is AC input, so both the intermediate
            # recovery budget and the final target must include eta_charge.
            remaining_overrides[load_id] = (
                max(0.0, load.target_soc_percent - soc)
                / 100.0
                * load.capacity_wh
                / member.eta_charge
            )
            recovery_target_wh = (
                max(0.0, member.recovery_soc_percent - soc)
                / 100.0
                * load.capacity_wh
                / member.eta_charge
            )
            tolerance_wh = max(
                STORAGE_TARGET_TOLERANCE_WH,
                load.capacity_wh * STORAGE_TARGET_TOLERANCE_PERCENT / 100,
            )
            if recovery_target_wh * member.eta_charge <= tolerance_wh + _EPS:
                recovery_target_wh = 0.0
            recovery_targets[load_id] = recovery_target_wh

        cumulative_caps: dict[str, tuple[float, ...]] = {}
        recovery_cumulative_caps: dict[str, tuple[float, ...]] = {}
        planning_inputs = trial_inputs
        if aux_segments:
            # `trial_inputs` carries the final post-Aux SOC so the total target
            # demand is known. The physical simulation must nevertheless start
            # at the observed SOC; per-slot cumulative caps release that demand
            # only after each Aux discharge has actually created headroom.
            source_ids = {
                segment.source_load_id
                for segment in aux_segments
                if segment.source == "aux" and segment.source_load_id is not None
            }
            planning_inputs = replace(
                trial_inputs,
                load_states=tuple(
                    original_states.get(state.load_id, state)
                    if state.load_id in source_ids
                    else state
                    for state in trial_inputs.load_states
                ),
            )
            discharge_by_slot = {
                load_id: [0.0] * len(trial_inputs.slots) for load_id in source_ids
            }
            for segment in aux_segments:
                source_id = segment.source_load_id
                if (
                    segment.source != "aux"
                    or source_id is None
                    or segment.terminal_energy_wh <= _EPS
                    or not 0 <= segment.slot_index < len(trial_inputs.slots)
                ):
                    continue
                cascade = cascade_by_member[source_id]
                member_index = next(
                    index
                    for index, member in enumerate(cascade.members)
                    if member.load_id == source_id
                )
                member = cascade.members[member_index]
                overhead_wh = (
                    sum(
                        item.output_overhead_w
                        for item in cascade.members[member_index:]
                    )
                    * segment.run_hours
                )
                discharge_by_slot[source_id][segment.slot_index] += (
                    segment.terminal_energy_wh + overhead_wh
                ) / member.eta_discharge

            for load_id in source_ids:
                load = loads_by_id[load_id]
                member = member_by_id[load_id]
                initial_state = original_states.get(load_id)
                initial_soc = (
                    initial_state.soc_percent
                    if initial_state is not None
                    and initial_state.soc_percent is not None
                    else 0.0
                )

                cumulative_caps[load_id] = _cumulative_charge_caps(
                    load.target_soc_percent,
                    remaining_overrides[load_id],
                    initial_soc,
                    load.capacity_wh,
                    member.eta_charge,
                    discharge_by_slot[load_id],
                )
                recovery_cumulative_caps[load_id] = _cumulative_charge_caps(
                    member.recovery_soc_percent,
                    recovery_targets[load_id],
                    initial_soc,
                    load.capacity_wh,
                    member.eta_charge,
                    discharge_by_slot[load_id],
                )
        result = _plan_legacy(
            legacy_config,
            planning_inputs,
            direct_surplus_only_load_ids=cascade_member_ids,
            remaining_energy_overrides=remaining_overrides,
            recovery_targets_wh=recovery_targets,
            priority_terminals={
                member.load_id: cascade.terminal_load_id
                for cascade in config.cascades
                for member in cascade.members
                if (terminal_state := states.get(cascade.terminal_load_id)) is None
                or (
                    terminal_state.available
                    and terminal_state.planning_power_w(
                        loads_by_id[cascade.terminal_load_id]
                    )
                    > _EPS
                )
            },
            power_caps_w=cascade_power_caps_w,
            path_power_limits=path_power_limits,
            path_overheads=path_overheads,
            cumulative_energy_caps_wh=cumulative_caps or None,
            recovery_cumulative_caps_wh=recovery_cumulative_caps or None,
        )
        return replace(
            result,
            load_plans=tuple(
                sorted(
                    result.load_plans,
                    key=lambda load_plan: result_order[load_plan.load_id],
                )
            ),
        )

    return augment_cascade_plans(config, inputs, replan(inputs), replan)
