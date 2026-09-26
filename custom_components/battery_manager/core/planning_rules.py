"""Shared pure candidate and feasibility rules; no allocation state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .model import (
    PlanInputs,
    SurplusLoadState,
    SystemConfig,
    Trajectory,
)
from .uncertainty import (
    quantile_band_slots as quantile_band_slots,
)

_EPS = 1e-6

# De-minimis floor for the gate-stop final top-up (F-GATE-TOPUP R3): no final
# candidate below this committed energy, so relay/gate cycles are never spent
# on negligible top-ups. A constant, not a config key (G2 style). R3 places it
# "in const.py", but its consumer is the pure planner core, which the
# standalone core test setup imports without the integration package — the
# authoritative definition therefore lives here and const.py re-exports it.
GATE_TOPUP_MIN_WH = 50.0

# Quantile-band presence gate (F-QUANTILE-BANDS D2): ratios against ~zero PV
# are noise, so a slot below this median PV never carries a band. A constant,
# not a config key.


# Hard import slack for load bookings (F-STRICT-SURPLUS R1): the whole
# allocation may add at most this much simulated grid import over the
# no-loads base — an ABSOLUTE artifact allowance, never a budget that
# scales with rescued export. Supersedes the Z2' proportional trade
# (`import_trade_ratio`, retired 2026-07-19: the ratio minted hundreds of Wh
# of REAL planned import on clip-eve days). A constant, not a config key.
# (The artifact class it tolerated — F-PREDRAIN L1's ~10 Wh charger-standby
# phantom import per flipped charging hour — was removed at the source in
# v0.20.0: the standby now reduces the stored charge, with `needed_ac`
# compensating it so the ceiling stays reachable; over a 3-day horizon the
# artifacts had accumulated past this slack and vetoed whole pre-drain
# blocks, live 2026-08-03.)
IMPORT_ARTIFACT_SLACK_WH = 50.0

# Operator 2026-09-07: forecast/consumption noise must not veto a useful
# continuous pre-drain over a fraction of one SOC tick (live 94.47 vs 95 %).
# This is a bounded SAME-DAY peak allowance, never extra import or floor slack.
PREDRAIN_PEAK_TOLERANCE_PERCENT = 1.0

# Terminal-credit ramp for the merge-bounded threshold search (F-MERGE-HYSTERESIS,
# forensics 2026-07-24). The merge decision used to be a knife-edge: a pessimistic
# no-loads sim either found a FULL+clipping slot (drop the terminal credit AND
# truncate the horizon -> drain to `lo`) or it did not (keep the full round-trip
# credit -> hoard-capable). One Wh of stressed clip flipped T* between two regimes
# ~1.8 kWh apart, so a bare SOC tick made the plan flap minute-to-minute (live
# 2026-07-24 07:28-08:02: 24 T* flanks 20<->61). The terminal credit now ramps
# LINEARLY with the stressed clip margin (Wh of export over the selected contiguous
# full-battery episode under stress): the full `eta_discharge * eta_inverter`
# credit at margin 0 (no stressed clip -> unchanged full-horizon regime) fades to 0 at margin >=
# MERGE_TERMINAL_RAMP_WH (a decisively guaranteed clip -> unchanged drain regime,
# horizon truncated). 250 Wh ~= a modest 250 W of clipping sustained one slot:
# small enough that a real guaranteed clip clears it in a single tick, wide enough
# that the clip-onset jitter (margin ~0 Wh) never crosses it. The hard horizon
# truncation reuses the SAME edge (margin >= ramp, i.e. once the credit has fully
# faded), so no independent knife-edge survives and the planner stays stateless
# (no cross-replan hysteresis state to plumb).
MERGE_TERMINAL_RAMP_WH = 250.0

# SOC band for the at-max top-up (F-PEAK-FILL R2, operator 2026-08-01): an
# energy-limited load with budget left may also book a slot whose PV surplus
# does NOT cover its power — the house battery buffers the difference — as
# long as the trial ends the slot at/above soc_max minus this band. The dip
# provably refills from otherwise-lost export (R5 forces re-reaching soc_max
# the same day, Z2'' forbids an unpaid dip = import), so export spikes become
# load charge instead of the load pulsing on/off at the max line. Sizing:
# one min-runtime quantum of a typical load (~750 W x 30 min ~ 7 % of a
# 5 kWh battery at zero surplus) must fit, so 2 % would suppress nearly all
# top-ups; 5 % admits moderate-surplus episodes and still bounds the ride to
# a shallow band — the per-slot band check IS the hysteresis: from the band
# floor every run dips below it and is rejected until PV refills. A constant,
# not a config key.
AT_MAX_TOPUP_BAND_PERCENT = 5.0


def _ramped_stress_floors(
    config: SystemConfig, inputs: PlanInputs, stress_vec: list[float]
) -> list[float]:
    """Per-slot Z4 stress floor with a crossover-ramped buffer (F-NIGHT-RESCUE
    R8): the closer the (stressed) PV crossover, the less buffer is needed.

    The buffer's purpose is to survive forecast error across the REMAINING
    dark deficit; for a candidate slot `i` it therefore ramps with
    `min(soc_buffer, 100 * stressed_deficit_wh(i) / capacity)`, where the
    deficit sums `max(0, consumption - stressed_pv)` from `i` up to the first
    slot whose stressed PV covers consumption. No crossover ahead (cloudy
    tail) -> the full static buffer. Stressed — not nominal — PV drives both
    the deficit and the crossover, using the SAME vector Z4 stresses with.
    Only the inverter-reserve floor ramps; Z3's `soc_min + buffer` (absolute
    battery protection) stays static.
    """
    control = config.control
    inverter_min = control.inverter_min_soc_percent
    full_buffer = control.soc_buffer_percent
    capacity = config.battery.capacity_wh
    n = len(inputs.slots)
    consumption = [slot.ac_wh + slot.dc_wh for slot in inputs.slots]
    stressed_pv = [slot.pv_wh * stress_vec[j] for j, slot in enumerate(inputs.slots)]
    floors: list[float] = []
    for i in range(n):
        deficit = 0.0
        crossed = False
        for j in range(i, n):
            if stressed_pv[j] >= consumption[j]:
                crossed = True
                break
            deficit += consumption[j] - stressed_pv[j]
        buffer_eff = (
            min(full_buffer, 100.0 * deficit / capacity) if crossed else full_buffer
        )
        floors.append(inverter_min + buffer_eff)
    return floors


def _crossday_daytime_bet(slot_date, refill_date, is_daylight: bool) -> bool:
    """R6 (F-STRICT-SURPLUS, operator 2026-07-19): is this pass-2 candidate a
    forbidden cross-day DAYTIME pre-drain?

    A DAYLIGHT bet (the slot produces PV, `pv_wh > 0`) whose battery only
    refills to soc_max on a LATER calendar day is draining today to absorb a
    NEXT-day clip. The operator rejected that marginal bet (the live 2026-07-19
    Sunday-14:00-for-Monday run): a daytime load belongs in its own day's
    surplus, not a day early. "Daylight" keys on `pv_wh > 0`, NOT on the
    strong-PV window — the afternoon taper below `strong_pv_cutoff_w`
    (e.g. 15:00-17:00) is still genuine daylight, and keying on the strong
    window let the identical bet escape one slot past the window edge (the
    live 14:00 slot sits right there). Night / pre-dawn slots (`pv_wh == 0`)
    keep the F-NIGHT-RESCUE cross-day carve-out — pre-draining overnight
    immediately before a clip day stays allowed — and a same-day refill is
    always fine, matching the energy-limited daylight rule's `pv_wh > 0` test.
    """
    return is_daylight and refill_date > slot_date


def _slot_serviceable(flow, slot, inverter_floor: float) -> bool:
    """One slot's R2 planner-G4 rule (F-STRICT-SURPLUS R2). A booked slot is
    serviceable iff it is neither cutoff-touching nor grid-fed:

    - **cutoff (G4 parity):** reject if EITHER endpoint sits at/below the
      inverter cutoff (`soc_start` OR `soc_end` <= inverter_floor). A slot
      ENTERED below the cutoff is one the executor's real-time G4 (SOC <= 20
      -> no additional loads) would refuse to actuate — booking it plans
      phantom rescue energy and slows the recovery above 20 %; a slot that
      ENDS at the cutoff rode it down (a battery-served discharge slot whose
      inverter is ON escapes the grid-fed test, so this endpoint is its sole
      guard — pinned by test_slot_serviceable).
    - **grid-fed:** reject if the inverter is off AND PV cannot cover the AC
      load (`pv_wh < ac_wh + extra_ac_wh`), so the deficit imports. When the
      inverter is off but PV covers the load — the full-battery hoard regime
      (T* = soc_max makes `inverter_on` False on every slot) or any export
      slot — the load is PV-served with zero import and is NOT grid-fed.
    """
    if (
        flow.soc_start_percent <= inverter_floor + _EPS
        or flow.soc_end_percent <= inverter_floor + _EPS
    ):
        return False
    grid_fed = not flow.inverter_on and (
        slot.pv_wh + _EPS < slot.ac_wh + flow.extra_ac_wh
    )
    return not grid_fed


def _z4_reject(trial_wmin: float, floor: float, base_wmin: float) -> bool:
    """Z4 windowed lower-buffer veto (F-PREDRAIN §3.3 v2 relief clause).

    Reject a pre-drain bet only if its stressed windowed reserve BOTH breaks
    the (ramped) inverter floor AND is worse than the same windowed min the
    currently accepted series already has. The second conjunct is the relief:
    a dip the baseline already contains — e.g. a cloudy tail, or a base
    consumption trough — must never veto a bet that does not DEEPEN it (mirrors
    the nominal `_degrades_min_soc` relief). Dropping it turns the gate
    floor-only and silently vetoes sound pre-drains, so it is exercised
    directly by test_z4_reject_relief_clause.
    """
    return trial_wmin < floor - _EPS and trial_wmin < base_wmin - _EPS


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


def _refill_index(traj: Trajectory, i: int, soc_full: float) -> int:
    """End slot of the pre-drain's "bet window" that starts at slot `i`.

    A pre-drain at slot `i` is a bet that the battery refills from coming
    production before the reserve is exhausted. The bet settles at the first
    slot at/after `i` where the TRIAL trajectory actually reaches soc_max
    (the drained energy is provably recovered / the battery clips) — not at
    the same-day strong-PV window end, whose premise "refilled by this
    window's end" is false on a day that never fills (F-STRICT-SURPLUS R3,
    2026-07-19: daytime bets escaped the stress test of the overnight dip
    they deepened, inverting the operator's lateness order). With no refill
    ahead (cloudy tail) the bet only settles at the horizon end.
    """
    return next(
        (
            j
            for j in range(i, len(traj.flows))
            if traj.flows[j].soc_end_percent >= soc_full
        ),
        len(traj.flows) - 1,
    )


def _windowed_min_soc(traj: Trajectory, lo: int, hi: int) -> float:
    """Lowest end-of-slot SOC over the inclusive slot range [lo, hi]."""
    return min(traj.flows[j].soc_end_percent for j in range(lo, hi + 1))


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


def _quantised_hours(
    load,
    slot,
    rem: float | None = None,
    power_w: float | None = None,
    saturation_power_w: float | None = None,
) -> list[float]:
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

    F-GATE-TOPUP R2: for an energy-limited load WITH a charge-enable gate
    (`gate_stop_capable`), `rem`/`power_w` size ONE extra final candidate
    `rem / max(power_w, nominal)` appended LAST — offered exactly when every
    k*q candidate would fail the saturation gate (the stall band: the load
    could otherwise never be re-booked once rem < one quantum's commitment
    and would park below its target forever). The G1 dwell-exempt target stop
    delivers exactly `rem` for this class, so F-RESIDUAL-TOPUP §8 D2's
    dwell-overshoot rejection does not apply; plug-only loads keep the old
    behaviour. No candidate below GATE_TOPUP_MIN_WH committed energy (R3).
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
    if (
        load.energy_limited
        and load.gate_stop_capable
        and rem is not None
        and power_w is not None
        and rem >= GATE_TOPUP_MIN_WH
    ):
        # The 1e-9 shave keeps max(power_w, nominal) * commit_final strictly
        # below `rem`, so the saturation gate's exact `<` comparison can never
        # trip on floating-point round-up of the by-construction equality.
        commit_final = (
            rem
            * (1.0 - 1e-9)
            / (
                saturation_power_w
                if saturation_power_w is not None
                else max(power_w, load.nominal_power_w)
            )
        )
        if _EPS < commit_final < q:
            candidates.append(commit_final)
    return candidates


def _effective_load_power_w(
    load,
    state: SurplusLoadState,
    power_caps_w: dict[str, float] | None,
) -> float:
    """Return the single physical planning power used by every layer."""
    power_w = state.planning_power_w(load)
    if power_caps_w is not None and load.load_id in power_caps_w:
        power_w = min(power_w, power_caps_w[load.load_id])
    return power_w


def _saturation_power_w(
    load,
    effective_power_w: float,
    power_caps_w: dict[str, float] | None,
) -> float:
    """Keep the saturation guard conservative without exceeding a hard cap."""
    nominal_w = load.nominal_power_w
    if power_caps_w is not None and load.load_id in power_caps_w:
        nominal_w = min(nominal_w, power_caps_w[load.load_id])
    return max(effective_power_w, nominal_w)


def _add_path_overheads(
    trial: list[float],
    load_id: str,
    covered: Sequence[tuple[int, float]],
    runs: Mapping[str, Sequence[float]],
    path_overheads: tuple[tuple[tuple[str, ...], float], ...],
) -> None:
    """Book each active output once, including overlapping downstream loads.

    Root supply pays the output standby losses; useful load energy and member
    charging budgets must stay unchanged. Aux accounts for its own losses in
    cascade.py and never enters this root-supply calculation.
    """
    for downstream, overhead_w in path_overheads:
        if load_id not in downstream:
            continue
        for index, take in covered:
            before = max(runs[key][index] for key in downstream)
            after = max(before, runs[load_id][index] + take)
            trial[index] += overhead_w * (after - before)


def _respects_path_power_limits(
    load_id: str,
    covered: list[tuple[int, float]],
    run_hours: Mapping[str, Sequence[float]],
    limits: tuple[tuple[tuple[tuple[str, float], ...], float], ...],
) -> bool:
    """All runs start at the slot boundary: their simultaneous W must fit.

    A shorter duty cycle cannot make a fixed-power device fit a smaller AC
    passthrough limit. Check the complete path on every allocation pass.
    """
    for participants, cap in limits:
        if not any(key == load_id for key, _power in participants):
            continue
        for index, take in covered:
            if (
                take > _EPS
                and sum(
                    power
                    for key, power in participants
                    if key == load_id or run_hours[key][index] > _EPS
                )
                > cap + _EPS
            ):
                return False
    return True


def _respects_cumulative_energy_cap(
    load_id: str,
    power_w: float,
    run_hours: list[float] | tuple[float, ...],
    covered: list[tuple[int, float]],
    cumulative_caps_wh: dict[str, tuple[float, ...]] | None,
) -> bool:
    """Prove that charge energy never precedes its physical storage headroom."""
    if cumulative_caps_wh is None or load_id not in cumulative_caps_wh:
        return True
    caps = cumulative_caps_wh[load_id]
    candidate_h = dict(covered)
    cumulative_wh = 0.0
    for index, existing_h in enumerate(run_hours):
        cumulative_wh += power_w * (existing_h + candidate_h.get(index, 0.0))
        if index < len(caps) and cumulative_wh > caps[index] + _EPS:
            return False
    return True


def _cumulative_charge_caps(
    target_soc: float,
    total_wh: float,
    initial_soc: float,
    capacity_wh: float,
    eta_charge: float,
    discharge_by_slot_wh: list[float],
) -> tuple[float, ...]:
    """Release a target's AC-input budget only after headroom exists."""
    available_wh = max(0.0, target_soc - initial_soc) / 100.0 * capacity_wh / eta_charge
    caps: list[float] = []
    for discharged_wh in discharge_by_slot_wh:
        caps.append(min(total_wh, available_wh))
        available_wh += discharged_wh / eta_charge
    return tuple(caps)


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


def _seamless_spill(
    covered: list[tuple[int, float]],
    slots,
    i: int,
    power_w: float,
    extra: list[float],
    scheduled: list[bool],
) -> tuple[list[float], list[tuple[int, float]]] | None:
    """Re-place a min-runtime commitment that spills into a slot ALREADY booked
    for THIS load as a slot-local booking (F-SEAMLESS-PLAN, F9).

    A partial first slot shorter than one min_runtime quantum can only offer a
    full-quantum candidate (`_committed_hours` floors the commit at the dwell),
    which then spills past the slot boundary into slot i+1. When slot i+1 is
    ALREADY this load's booking, that spill is not a real conflict: the load
    runs contiguously across the boundary, so the min_runtime dwell is met by
    the booked continuation and slot i needs to commit only its OWN remaining
    duration. Without this, the spill collides with the booked next slot, the
    overlap guard drops slot i, and the recommendation of a genuinely RUNNING
    load is retracted at every raster edge (live: ~:31 each morning hour, the
    partial slot dips below the 30-min quantum) even though the surplus is
    unchanged — the plan-level analog of the executor's F-SEAMLESS-RUNS.

    Books exactly slot i's remaining duration (no phantom over-fill of the next
    slot, which would exceed one hour of the load in a one-hour slot). Returns
    the trimmed ``(trial, covered)``, or None when the overlap is a genuine
    double-booking — the next covered slot is NOT this load's, so trimming to
    slot i alone would strand the run below its dwell with no continuation; the
    caller then rejects the candidate as before.
    """
    if len(covered) >= 2 and scheduled[covered[1][0]]:
        return _spread_energy(extra, slots, i, power_w, slots[i].duration)
    return None


def _final_note(load, commit_h: float, seamless: bool) -> str:
    """Sub-quantum label for the explain-plan reason, shared by both passes
    (the pre-refactor copies were verbatim duplicates).

    Only the gate-stop final quantum sits below one min_runtime quantum —
    name it so a shorter-than-dwell booking is self-explaining
    (F-GATE-TOPUP R6). A seamless raster-edge continuation
    (F-SEAMLESS-PLAN) is also sub-quantum but is named for what it is,
    not a top-up.
    """
    return (
        ", seamless continuation"
        if seamless
        else ", final top-up to target"
        if commit_h < load.min_runtime_min / 60.0 - _EPS
        else ""
    )
