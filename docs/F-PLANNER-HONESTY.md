# F-PLANNER-HONESTY — learned planning power, absolute lateness in pass 1, explain-plan

Status: **binding spec**, part 1/2 of v0.9.0 (operator: "Setze alles um",
2026-07-10). Part 2 is docs/F-EXECUTOR-GUARDS.md. Resolves the open decision
O1 of docs/F-RESIDUAL-TOPUP.md §8.

## 1. Problems

**P1 — planning power is dishonest when the load is off.** The power-feedback
EMA is deliberately discarded when a run ends (v0.5.1/v0.6.2 standby
protection, coordinator `_get_load_states`), so every booking of an OFF load
is evaluated at nominal power (F2400-B: 300 W configured vs ~505 W real). All
gates, the saturation check and the booked energy are ~40 % under real; the
executor then delivers substantially more than validated (bounded only by the
v0.8.1 cap and the target-SOC stop).

**P2 — pass 1 books the FIRST direct-surplus hour.** For a load that saturates
before taking every export hour (energy-limited residuals), ascending order
places the run hours earlier than necessary. Operator principle: as late as
possible, absolutely (D-A4 v4). This is O1 of F-RESIDUAL-TOPUP, now decided.

**P3 — the planner cannot explain itself.** "Why is this load on NOW?"
(operator, 2026-07-10) required code archaeology and live-data forensics. The
planner knows the answer at acceptance time and throws it away.

## 2. Requirements

### F1 — learned planning power (P1)

- **R1** New core field `SurplusLoadState.learned_power_w: float | None = None`.
  `planning_power_w()` precedence: `measured_power_w` (live, present only
  during/around an active run) > `learned_power_w` > `nominal_power_w`.
- **R2** The coordinator learns per load the **run-maximum of the accepted-
  sample EMA**: while a run is active and a sample passes the existing
  standby bar (`min_sample_w`, unchanged v0.6.2 semantics), after updating
  `_load_power_ema` set
  `learned = max(learned_of_this_run, ema)`; on each such update store it in
  `_load_learned_power_w[load_id]` (last completed/ongoing run wins, i.e. the
  store always holds the current run's max-EMA once the run has ≥ 1 accepted
  sample, else the previous run's). Run-max — not the final EMA — so an
  end-of-charge taper cannot erode the learned value; the EMA — not raw — so a
  spike cannot inflate it. Standby poisoning stays impossible because only
  accepted samples feed it (the v0.6.2 bar is the single gate).
  **R2a (hardening, added at mission-A review):** the run-max tracker starts
  at the SECOND accepted sample of a run — the EMA initialises on the first
  raw sample, so a single start-up spike would otherwise be learned verbatim
  and permanently (error direction conservative, but cheap to avoid).
- **R3** Persist `load_learned_power` in `_persistent_payload` and restore on
  setup (float per load id; drop entries whose subentry vanished). No new
  config keys.
- **R4** Wire `learned_power_w` into the `SurplusLoadState` built each cycle.
  The saturation gate formula `max(power_w, nominal)` is UNCHANGED — with a
  learned 505 W it now gates at 505 W for an off load, which is the honest
  committed energy.
- **R5** Observability: the per-load diagnostics dict that already exposes
  `measured_power_w` additionally exposes `learned_power_w`.
- **R6 (regression)** With no learned value present (fresh install, tests
  constructing states directly) behaviour is bit-identical to v0.8.2. Core
  tests: precedence order of `planning_power_w`; coordinator tests: run-max
  learning, taper does not erode, standby never learns, persistence
  round-trip, vanished-subentry pruning.

### F2 — pass-1 restructure: load-outer, per-class direction (P2, O1)

> **Superseded (v0.9.2, docs/F-RESCUE-EXPORT.md):** the energy-limited pass-1
> slot order below (day-bounded latest-first) is replaced by **earliest-
> export-first** — pass 1 walks `range(n)` ascending for ALL loads. A pass-1
> candidate only ever sits on a slot that is already exporting (battery full),
> so lateness rescues no extra energy but loses the present, certain surplus;
> the load must run as soon as export occurs. The load-outer strict-priority
> structure and everything else in R7 is unchanged. **Pass 2 (R2 there /
> latest-first) is unchanged** — there the battery can still buffer, so
> deferring the preemptive bet remains correct.

- **R7 (v2, amended after mission-A review)** Pass 1 becomes **load-outer**
  in config order (strict priority: a load books its complete pass-1
  allocation before the next load sees the horizon), slots inner: for
  **energy-limited loads DAY-BOUNDED latest-first** — calendar days in
  ASCENDING order, the hours WITHIN each day in DESCENDING order; ascending
  throughout for continuous loads (they book every feasible export hour
  anyway; ascending keeps deltas minimal).
  Rationale for the day bound (architect decision, 2026-07-10, supersedes the
  v1 whole-horizon descending order): the operator principle has two clauses —
  "as late as possible" AND "just early enough to avoid export". Deferring a
  saturating load from an exporting day to a LATER day strands the earlier
  day's real surplus (observed +0.4/+0.8 kWh export in the v1 goldens) and
  bets on the least certain forecast zone; within one day, lateness is free.
  So: first day whose export the load can rescue wins, latest hours of that
  day. (Matches the operator's own example: "tomorrow 06:00", not day 3.)
- **R8** The per-slot `surplus` decrement bookkeeping is dropped; each
  (load, slot) evaluation reads the CURRENT accepted trajectory's
  `flows[i].grid_export_wh` (+ spill proration as today). This replaces an
  intra-slot approximation with the exact re-simulated value — a deliberate
  refinement, called out for golden classification.
- **R9** Gates, candidate loop (`_quantised_hours`), Z2'/Z3, battery-share
  tolerance, booking/accounting: unchanged. Pass 2 unchanged (already
  latest-first).
- **R10 (golden policy)** Expected delta classes: (iii) energy-limited pass-1
  hours move LATER **within the same day's surplus window** (same or ±1
  quantum energy; a cross-DAY move is NOT class (iii) and fails review —
  R7 v2 day bound); (iv) scarce-surplus scenarios shift energy toward higher-priority
  loads (load-outer strictness); (v) marginal bookings flip due to exact
  (R8) surplus. Every delta inspected, classified (iii)/(iv)/(v), and listed
  in the commit message; imports must not rise in any golden (Z2' is
  scenario-level, verify `import_kwh` unchanged or better per scenario).
  Unexplained deltas = stop the line.
- **R11 (tests)** (a) an energy-limited residual with two feasible direct-
  surplus hours books the LATER one; (b) a continuous + an energy-limited
  load with scarce surplus: config-order priority holds under load-outer;
  (c) the R4 live-scene regression of F-RESIDUAL-TOPUP still passes
  (booking may move LATER, adjust only the "which slot" assertion if needed —
  the invariants "not slot 0, nothing before 06:00, one 0.5 h quantum" are
  untouchable).

### F3 — explain-plan (P3)

- **R12** `LoadPlan` gains `reasons: tuple[str, ...] = ()` — one terse English
  string per entry of `allocations`, same order. Empty default keeps every
  legacy constructor call valid; consumers must not assume it is populated.
- **R13** Reason strings are built at the three acceptance sites:
  - pass 1: `"pass 1 @ {start %m-%d %H:%M}: direct surplus, {minutes} min x {power} W, battery share {pct}%"`
  - pass 2 c1: `"pass 2 @ …: covered by otherwise-lost export ({export_drop} Wh), latest feasible slot"`
  - pass 2 c2 (continuous only): `"pass 2 @ …: in-window insurance (beta), latest feasible slot"`
  Numbers rounded to integers; "latest feasible slot" is structurally true in
  pass 2 (descending first-accept).
- **R14** The coordinator's load-plan data dict and the SOC-forecast sensor's
  per-load `schedule` entries carry the matching reason as `"why"` per
  allocation block. Legacy plans without reasons render without the key.
- **R15 (tests)** reasons align 1:1 with allocations (length, pass number
  mentioned matches the allocation's pass); sensor attribute carries `why`;
  golden files are NOT extended with reasons (they snapshot energy+schedule
  only — verify goldens unchanged by F3 alone).

## 3. Non-goals

No new config. No change to pass-2 logic, Z-gates, pre-drain machinery or the
executor (part 2 covers the executor). No localisation of reason strings
(technical attribute, English, documented in README/ALGORITHM note). No
15-minute grid.

## 4. Test/verify

Full suite green on `.venv314` (winshim), ruff check AND `ruff format --check`
(CI parity, ruff 0.15.21), goldens regenerated ONLY with per-scenario
classification per R10. Update docs/ALGORITHM.md D-A4: v5 note (load-outer
pass 1, per-class direction, learned power, explain-plan). CHANGELOG under
[Unreleased] (release cut as v0.9.0 after part 2).
