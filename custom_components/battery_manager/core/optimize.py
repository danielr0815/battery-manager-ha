"""Planner: threshold search, surplus allocation, appliance advisor, support
escalation. Implements docs/ALGORITHM.md §1 with decisions D-A1..D-A9."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta

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
QUANTILE_RATIO_MIN_WH = 25.0

# Hard import slack for load bookings (F-STRICT-SURPLUS R1): the whole
# allocation may add at most this much simulated grid import over the
# no-loads base — an ABSOLUTE artifact allowance (a few ~10 Wh charger-standby
# artifacts across multiple bookings, F-PREDRAIN L1), never a budget that
# scales with rescued export. Supersedes the Z2' proportional trade
# (`import_trade_ratio`, retired 2026-07-19: the ratio minted hundreds of Wh
# of REAL planned import on clip-eve days). A constant, not a config key.
IMPORT_ARTIFACT_SLACK_WH = 50.0

# Terminal-credit ramp for the merge-bounded threshold search (F-MERGE-HYSTERESIS,
# forensics 2026-07-24). The merge decision used to be a knife-edge: a pessimistic
# no-loads sim either found a FULL+clipping slot (drop the terminal credit AND
# truncate the horizon -> drain to `lo`) or it did not (keep the full round-trip
# credit -> hoard-capable). One Wh of stressed clip flipped T* between two regimes
# ~1.8 kWh apart, so a bare SOC tick made the plan flap minute-to-minute (live
# 2026-07-24 07:28-08:02: 24 T* flanks 20<->61). The terminal credit now ramps
# LINEARLY with the stressed clip margin (Wh of export at the first full slot under
# stress): the full `eta_discharge * eta_inverter` credit at margin 0 (no stressed
# clip -> unchanged full-horizon regime) fades to 0 at margin >=
# MERGE_TERMINAL_RAMP_WH (a decisively guaranteed clip -> unchanged drain regime,
# horizon truncated). 250 Wh ~= a modest 250 W of clipping sustained one slot:
# small enough that a real guaranteed clip clears it in a single tick, wide enough
# that the clip-onset jitter (margin ~0 Wh) never crosses it. The hard horizon
# truncation reuses the SAME edge (margin >= ramp, i.e. once the credit has fully
# faded), so no independent knife-edge survives and the planner stays stateless
# (no cross-replan hysteresis state to plumb).
MERGE_TERMINAL_RAMP_WH = 250.0


def quantile_band_slots(slots) -> list[bool]:
    """Per-slot band presence per F-QUANTILE-BANDS D2 — THE cold-start rule.

    A slot HAS a band iff p10/p90 data covers it, the median PV is at least
    QUANTILE_RATIO_MIN_WH, and the spread `p90 - p10` exceeds
    max(1.0 Wh, 1 % of pv_wh). A COLLAPSED band (p10 == p90, the balcony
    forecaster's cold-start signature) counts as NO band: it means "no
    evidence", NOT "no uncertainty" — treating it as certainty would make the
    Z4 stress WEAKER than the scalar alpha on exactly the bins that have no
    history yet. Single source of truth, shared by `_effective_uncertainty`
    and the coordinator's `quantile_coverage` diagnostics (R7).
    """
    band: list[bool] = []
    for slot in slots:
        band.append(
            slot.pv_p10_wh is not None
            and slot.pv_p90_wh is not None
            and slot.pv_wh >= QUANTILE_RATIO_MIN_WH
            and (slot.pv_p90_wh - slot.pv_p10_wh) > max(1.0, 0.01 * slot.pv_wh)
        )
    return band


def _effective_uncertainty(
    inputs: PlanInputs, alpha: float, beta: float
) -> tuple[list[float], list[float], list[bool]]:
    """Per-slot stress/optimism vectors from the empirical P10/P90 bands
    (F-QUANTILE-BANDS D1/D3), with per-slot scalar fallback.

    Where a slot carries a band (D2), the ratios against the median replace
    the scalar dials: `stress = clamp(p10/pv, 0.1, 1.0)` and
    `optimism = clamp(p90/pv, 1.0, 2.0)` — the clamps guard junk ratios, and
    simulate()'s FIX-8 physical peak clamp additionally bounds optimism
    downstream. Everywhere else the scalars apply unchanged, so with no bands
    anywhere the vectors are uniform and the plan is bit-identical to the
    scalar-era behaviour at the same alpha/beta (R8); a partially covered day
    mixes evidence and fallback IN THE SAME simulation vector (R9).
    """
    band = quantile_band_slots(inputs.slots)
    stress: list[float] = []
    optimism: list[float] = []
    for slot, has_band in zip(inputs.slots, band, strict=True):
        if has_band:
            stress.append(min(1.0, max(0.1, slot.pv_p10_wh / slot.pv_wh)))
            optimism.append(min(2.0, max(1.0, slot.pv_p90_wh / slot.pv_wh)))
        else:
            stress.append(alpha)
            optimism.append(beta)
    return stress, optimism, band


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
    """Merge bound of the threshold scan AND the stressed clip margin.

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
    - ``margin_wh`` is the stressed export at that first full+clipping slot: the
      CONTINUOUS strength of the guaranteed clip (0.0 when ``end`` is None).
      One Wh here used to flip the whole merge decision; it now moves the
      terminal-credit ramp (F-MERGE-HYSTERESIS) by 1/MERGE_TERMINAL_RAMP_WH of
      its full value instead. The FIRST clip is the right measure: the merge
      principle only decouples tonight once a clip is guaranteed, so a marginal
      first clip (export ~0) SHOULD stay in the cautious hoard-capable regime.
    """
    n = len(inputs.slots)
    if n == 0:
        return None, 0.0
    control = config.control
    stress_vec, _optimism, _band = _effective_uncertainty(
        inputs, control.predrain_pv_confidence, control.upper_pv_reserve
    )
    base = simulate(config, inputs, float(_search_lo(config)), pv_scale=stress_vec)
    soc_full = config.battery.soc_max_percent - 0.1
    merge: int | None = None
    margin_wh = 0.0
    for j, flow in enumerate(base.flows):
        if flow.soc_end_percent >= soc_full and flow.grid_export_wh > _EPS:
            merge = j
            margin_wh = flow.grid_export_wh
            break
    if merge is None:
        return None, 0.0
    end = max(merge, 5)  # R5 floor: at least 6 slots (indices 0..5)
    if end >= n - 1:
        return None, 0.0  # nothing to truncate
    return end, margin_wh


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
    """Find the SOC threshold minimizing import − terminal value + export tiebreak.

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
    best_traj: Trajectory | None = None

    for candidate in range(lo, hi + 1):
        traj = simulate(config, scan_inputs, float(candidate))
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

    if merge_active or best_traj is None:
        # Truncated scan (or degenerate empty horizon): the caller needs the
        # FULL-horizon no-loads base at the chosen threshold. When the scan kept
        # the full horizon (no merge, or a faded-credit clip below the ramp)
        # best_traj is already full-horizon at best_threshold — no rebuild.
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


def _quantised_hours(
    load, slot, rem: float | None = None, power_w: float | None = None
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
        commit_final = rem * (1.0 - 1e-9) / max(power_w, load.nominal_power_w)
        if _EPS < commit_final < q:
            candidates.append(commit_final)
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

    GATE PARITY (F-GATE-PARITY, operator decision 2026-07-17): both load
    classes face the IDENTICAL gate set — one Z2' trade invariant, c1-rt/c2
    opportunity gates and the Z4 stress floor — so the priority order alone
    decides who gets contested energy ("lieber den Fossibot laden, als den
    Luftentfeuchter betreiben, wenn die Wahl besteht"). The single remaining
    class rule: energy-limited loads never book zero-PV (night) slots in
    pass 2 — nights stay reserved for continuous loads.
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
        rem = state.remaining_energy_wh(load)
        # V6 (F-TANK): cap a tank-modelled load at its remaining tank RUN time,
        # converted to Wh with the exact planning power the allocator books at
        # (so the Wh budget corresponds to precisely tank_remaining_min minutes
        # of booked runtime). Reuses the energy-limited `remaining` machinery —
        # the saturation gate then skips a load whose tank is (nearly) full,
        # just as it skips a full powerstation. None = feature off (no cap).
        if state.tank_remaining_min is not None:
            tank_wh = (
                max(0.0, state.tank_remaining_min) / 60.0 * state.planning_power_w(load)
            )
            rem = tank_wh if rem is None else min(rem, tank_wh)
        remaining[load.load_id] = rem

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
    soc_full = config.battery.soc_max_percent - 0.1
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
        inverter_floor = control.inverter_min_soc_percent
        return all(
            _slot_serviceable(traj.flows[j], inputs.slots[j], inverter_floor)
            for j, _take in covered
        ) and all(
            _slot_serviceable(traj.flows[j], inputs.slots[j], inverter_floor)
            for j in range(n)
            if booked_any[j]
        )

    def preserves_daily_max(traj: Trajectory) -> bool:
        """F-STRICT-SURPLUS R5: the trial must still reach soc_max on every day
        the no-loads base reached it. Vetoes a pre-drain bet that would stop the
        battery filling to max on such a day (objective 2 outranks absorbing
        export); pre-conditioning for a FUTURE clip is untouched (that day is
        not in base_max_days, and its target clip day still fills)."""
        return all(
            any(traj.flows[j].soc_end_percent >= soc_full for j in day_slots[day])
            for day in base_max_days
        )

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
            # rem/power_w size the gate-stop final top-up (F-GATE-TOPUP R2).
            for commit_h in _quantised_hours(load, slot, rem, power_w):
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
                # Hard conditions via full re-simulation (Z2''/R2/R5/Z3).
                traj = simulate(config, inputs, threshold, extra_ac_wh=tuple(trial))
                if not import_ok(traj):
                    continue
                if not slots_serviceable(traj, covered):
                    continue
                if not preserves_daily_max(traj):
                    continue
                if _degrades_min_soc(traj, current, buffer_floor):
                    continue
                extra = trial
                current = traj
                for j, take in covered:
                    schedules[load.load_id][j] = True
                    run_h[load.load_id][j] = take
                    booked_any[j] = True
                # Book what actually landed in the horizon (a commitment may be
                # truncated at the horizon end); the gates above deliberately
                # used the full committed energy.
                placed_h = sum(take for _, take in covered)
                placed_wh = power_w * placed_h
                planned_wh[load.load_id] += placed_wh
                allocations[load.load_id].append((i, len(covered), 1, placed_wh))
                # Only the gate-stop final quantum sits below one min_runtime
                # quantum — name it so a shorter-than-dwell booking is
                # self-explaining (F-GATE-TOPUP R6).
                final_note = (
                    ", final top-up to target"
                    if commit_h < load.min_runtime_min / 60.0 - _EPS
                    else ""
                )
                reasons[load.load_id].append(
                    f"pass 1 @ {slot.start.strftime('%m-%d %H:%M')}: "
                    f"direct surplus, {round(placed_h * 60)} min x "
                    f"{round(power_w)} W, battery share {round(battery_share * 100)}%"
                    f"{final_note}"
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
    #                        window [i, recovery] (F3 v2),
    #   (c) opportunity    — (c1) the nominal drain is refilled from lost export,
    #                        OR (c2) inside the day's PV window an optimistic
    #                        (beta) run would be (upper-buffer insurance, F4).
    # All gates apply to ALL load classes (F-GATE-PARITY: the former
    # energy-limited c1-only carve-out let a lower-priority continuous load take
    # bet energy a higher-priority powerstation was forbidden, silently
    # overriding the operator's priority order). The one remaining class rule is
    # the DAYLIGHT restriction: energy-limited loads never book zero-PV (night)
    # slots — nights stay reserved for continuous loads (operator refinement 2,
    # 2026-07-17). Iterated latest-first (L4); slots after the last export can
    # never satisfy the gate, so they are skipped, as is the whole pass on an
    # export-free horizon.
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
                state = states.get(load.load_id, SurplusLoadState(load_id=load.load_id))
                if not state.available or schedules[load.load_id][i]:
                    continue
                # Daylight rule (F-GATE-PARITY refinement 2): energy-limited
                # loads never open a bet in a zero-PV (night) slot. Deliberately
                # `pv_wh > 0` and NOT `in_window` — pre-window daylight
                # pre-charges before a short peak stay allowed (they were a
                # pinned capability before parity, and forbidding them would
                # re-introduce a class asymmetry in daylight).
                if load.energy_limited and slot.pv_wh <= 0.0:
                    continue
                power_w = state.planning_power_w(load)
                rem = remaining[load.load_id]
                # Largest-first quantised search (F-SUBHOUR): a sub-hour
                # preemptive run needs only export_drop >= (1-tol)*(k*q) energy,
                # so a small afternoon dribble a whole hour cannot capture may
                # still be soaked by a min_runtime chunk. rem/power_w size the
                # gate-stop final top-up (F-GATE-TOPUP R2).
                for commit_h in _quantised_hours(load, slot, rem, power_w):
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
                    if load.energy_limited and any(
                        inputs.slots[j].pv_wh <= 0.0 for j, _ in covered
                    ):
                        # Daylight rule, spill guard: a min-runtime commitment
                        # near the day's edge must not spill into night slots;
                        # shorter quantised candidates (incl. the gate-stop
                        # final quantum) still get their chance below.
                        continue
                    trial_tuple = tuple(trial)
                    traj = simulate(config, inputs, threshold, extra_ac_wh=trial_tuple)
                    if not import_ok(traj):  # Z2''
                        continue
                    if not slots_serviceable(traj, covered):  # R2 planner-G4
                        continue
                    if not preserves_daily_max(traj):  # R5 reach-max
                        continue
                    if _degrades_min_soc(traj, current, buffer_floor):  # Z3
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
                        trial_beta = simulate(
                            config,
                            inputs,
                            threshold,
                            extra_ac_wh=trial_tuple,
                            pv_scale=optimism_vec,
                        )
                        drop_beta = (
                            current_beta.total_export_wh - trial_beta.total_export_wh
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
                        if _z4_reject(
                            trial_wmin, stress_floor_by_slot[i], stress_base[key]
                        ):
                            continue
                    extra = trial
                    current = traj
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
                                pv_scale=optimism_vec,
                            )
                        )
                    for j, take in covered:
                        schedules[load.load_id][j] = True
                        run_h[load.load_id][j] = take
                        booked_any[j] = True
                    placed_wh = power_w * sum(take for _, take in covered)
                    planned_wh[load.load_id] += placed_wh
                    allocations[load.load_id].append((i, len(covered), 2, placed_wh))
                    # "latest feasible slot" is structurally true: pass 2 walks
                    # descending and accepts the first slot that passes (R13).
                    # Only the gate-stop final quantum sits below one
                    # min_runtime quantum (F-GATE-TOPUP R6).
                    final_note = (
                        ", final top-up to target"
                        if commit_h < load.min_runtime_min / 60.0 - _EPS
                        else ""
                    )
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
    lower-buffer reserve that the Z4 gate actually protects: the earliest
    pass-2 slot booked for ANY load is treated as the deepest bet
    (F-GATE-PARITY GP-R4 — the stress gate binds energy-limited bets too),
    and the diagnostic is the stressed windowed min SOC over that bet's
    recovery window [i0, recovery] under the FINAL accepted series — stressed
    with the same per-slot vector as the gate (P10 bands where present, alpha
    elsewhere; F-QUANTILE-BANDS R5). It is None when no slot is stressed
    (alpha == 1.0 and no P10 evidence) or when no load at all has a pass-2
    booking (nothing was bet, so there is no reserve to report).
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
    # F-QUANTILE-BANDS R5: the diagnostic stresses with the SAME per-slot
    # vector as the Z4 gate (empirical P10 where bands exist, alpha elsewhere),
    # so what the sensor reports is what the gate protected.
    stress_vec, _optimism_vec, _band_slots = _effective_uncertainty(
        inputs, alpha, control.upper_pv_reserve
    )
    if any(s < 1.0 - _EPS for s in stress_vec) and alloc_traj.flows:
        # Windowed stressed reserve of the deepest bet: the earliest pass-2 slot
        # booked for ANY load, evaluated over its recovery window under the
        # final series (§3.5 v2). Since F-GATE-PARITY the Z4 stress gate binds
        # energy-limited bets too, so their bookings belong in the diagnostic —
        # what the sensor reports is what the gate protected. None when no
        # pass-2 booking exists at all.
        n = len(inputs.slots)
        booked = [
            alloc[0] for lp in load_plans for alloc in lp.allocations if alloc[2] == 2
        ]
        if booked:
            i0 = min(booked)
            # Same settlement rule as the Z4 gate (F-STRICT-SURPLUS R3): the
            # deepest bet's window ends where the FINAL accepted series refills
            # to soc_max — the sensor reports what the gate protected.
            recovery = _refill_index(
                alloc_traj, i0, config.battery.soc_max_percent - 0.1
            )
            scale_vec = [
                stress_vec[j] if i0 <= j <= recovery else 1.0 for j in range(n)
            ]
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
    # F-STRICT-SURPLUS R4: per-day export the loads PREVENTED — base (no loads)
    # minus alloc (with loads), BOTH pre support-escalation (alloc_traj is
    # captured before support_escalation), so a support PSU never deflates the
    # counterfactual. The dashboard shows max(0, ...) per day.
    base_exp_day: dict[str, float] = {}
    alloc_exp_day: dict[str, float] = {}
    for slot, base_flow, alloc_flow in zip(
        inputs.slots, base_traj.flows, alloc_traj.flows, strict=True
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
    merge_end = _threshold_merge_bound(config, inputs)
    threshold_horizon_end = None
    if merge_end is not None:
        merge_slot = inputs.slots[merge_end]
        threshold_horizon_end = merge_slot.start + timedelta(hours=merge_slot.duration)

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
        threshold_horizon_end=threshold_horizon_end,
        prevented_export_by_day_wh=prevented_export_by_day,
    )
