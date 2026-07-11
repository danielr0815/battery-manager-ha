# F-RESIDUAL-TOPUP — latest-feasible placement for energy-limited residual top-ups

Status: **binding spec** for v0.8.1. Author: planning session 2026-07-10 (evening).
Supersedes the energy-limited carve-outs of docs/F-SUBHOUR-ALLOCATION.md R1/R12
(see §7). Extends docs/ALGORITHM.md D-A4 (v4 note).

## 1. Problem / incident (live, 2026-07-10)

Fossibot F2400-B (energy-limited, nominal 300 W, `min_runtime_min` 30,
capacity 2000 Wh, target SOC 90 %) stood at ~82 % → remaining ≈ 156 Wh.
At 18:47 local, with the house battery at 72 % and no PV left for the day,
the planner booked a 0.5 h / ~150 Wh run **at slot 0 (= now)** and the
executor switched the charger on — a night charge from the house battery,
directly violating the operator's as-late-as-possible principle.

Root-cause chain (all in `core/optimize.py::allocate_loads`):

1. `_quantised_hours()` returns **only the whole-slot candidate** for
   energy-limited loads (F-SUBHOUR R1 carve-out): for every full hour the
   committed energy is `nominal × 1 h = 300 Wh`.
2. The saturation gate `rem < max(power_w, nominal_power_w) · commit_h → skip`
   therefore rejects **every future slot** once `rem < 300 Wh`.
3. The only slot whose commitment can be smaller than an hour is **slot 0**:
   its duration is the partial remainder of the current hour, dwell-floored to
   `min_runtime` (30 min → 150 Wh ≤ 156 Wh → feasible).
4. Pass 2 iterates latest-first (D-A4 v3.2), but with every later slot
   infeasible the "latest justifiable hour" degenerates to *now*. The c1 gate
   passes (next day's ~0.4–0.6 kWh lost surplus proves the refill), so the
   booking is accepted.
5. The device self-discharges (~130 W observed while powering a consumer), so
   `rem` re-grows past 150 Wh after each top-up → **repeating night
   trickle-charge loop** (observed gate cycles 16:54–17:24 and 18:47), each
   round-tripping energy through the house battery at ~80 % efficiency instead
   of one top-up in the next PV window.

Operator directive (2026-07-10, verbatim intent): *the as-late-as-possible
principle must not be violated — regardless of partial ("started") hours or any
other implementation reasons. The same top-up could just as well run tomorrow
at 06:00 for 30 minutes.* Note the example explicitly accepts a pre-window
(c1-refilled) morning placement; it does not demand a hard night ban, it
demands **lateness**.

## 2. Goals / non-goals

**Goals.** (a) A residual demand `E_min ≤ rem < nominal × 1 h` (with
`E_min = nominal × min_runtime/60`) must be bookable in **any** slot of the
horizon, so the pass-2 latest-first order — not slot-0 geometry — decides the
placement. (b) Slot 0 loses its accidental monopoly: a *now* booking may only
happen when no later slot admits any feasible candidate. (c) The executor
delivers a sub-hour energy-limited booking with a bounded overrun even when the
load's SOC sensor is stale (the fossibot integration is known to serve stale
cached values with fresh timestamps).

**Non-goals.** No new config. No change to pass-1's ascending slot order
(each direct-surplus hour is use-it-or-lose-it *in place*; see open decision
O1 in §8 for within-window micro-ordering). No change to Z2'/Z3/Z4 gate
formulas, to the c1/c2 opportunity gates, or to L5 (energy-limited loads stay
out of the two-buffer pre-drain machinery: no α-stress, no β-insurance, strict
import gate). No planner modelling of `min_off` spacing between two disjoint
sub-hour activations (pre-existing F-SUBHOUR limitation, unchanged — see §6
known limitations). No simulator changes.

## 3. Requirements (testable)

### Planner (`core/optimize.py`, pure)

- **R1** `_quantised_hours(load, slot)` offers energy-limited loads the same
  candidate list as non-energy-limited loads: `[whole, k·q … ]` largest-first,
  `whole = max(slot.duration, q)` first, `q = min_runtime_min/60`, no candidate
  below one quantum (F-SUBHOUR R2 unchanged). Implementation is the removal of
  the `energy_limited → [whole]` early-return; both passes already loop over
  the candidate list.
- **R2 (regression anchor)** The whole-slot candidate stays FIRST: wherever a
  whole-slot booking passes every gate today, the produced plan is
  **bit-identical** to v0.8.0. Golden deltas may only be of the two intended
  classes: (i) a residual (`rem < nominal × commit`) that today books at
  slot 0 or nowhere now books a sub-hour quantum at a later slot; (ii) an
  energy-limited load that whole-hour-**failed** a gate now sub-hour-succeeds
  (new capture). Every golden delta is inspected and documented in the commit.
- **R3 (lateness invariant)** In pass 2, a booking at slot `i` implies that at
  that point of the search no slot `j > i` (within `[0, last_export]`) admitted
  any feasible candidate for that load. This is the existing latest-first loop —
  R1 restores its premise. Regression test: a nearly-full energy-limited load,
  an evening partial slot 0, night ahead, next-day surplus → asserts
  `schedule[0] is False` and the single booking sits in the next day.
- **R4 (live-scene regression)** Reproduce the 2026-07-10 18:47 geometry
  (battery ~72 %, fossibot rem ≈ 156 Wh, no PV left today, strong PV +
  clipping tomorrow): the plan books exactly one 0.5 h quantum (≈150 Wh),
  not at slot 0, and books nothing during the coming night
  (no covered slot may start before 06:00 next day).
- **R5 (saturation gate unchanged)** The gate formula
  `rem < max(power_w, nominal_power_w) · commit_h → skip candidate` is kept
  per candidate (it now falls through to the next-smaller quantum instead of
  killing the slot). No truncated-energy booking: booking `min(rem, …)` was
  considered and **rejected** because `rem/power < min_runtime` would book less
  energy than the executor dwell can deliver — the v0.6.1 degenerate-slot-0 bug
  class in reverse (see §8 D2).
- **R6** Pass-1 semantics unchanged (ascending, direct-surplus, battery-share
  tolerance per candidate). With R1 a residual can now also rescue a direct
  surplus hour with a 30-min run — intended (D-A4 v2: "saturating within the
  sun window has priority, saving battery cycles").

### Executor (`coordinator.py`, live actuation)

- **R7** The F-SUBHOUR approach-A run deadline is extended to **energy-limited
  controlled loads** as an UPPER CAP: on the ON edge freeze
  `off_at = run_start + max(min_runtime, D)` with `D =
  plan.active_run_hours(slot_durations)`, arm the one-shot timer, and force OFF
  at `off_at` even if the plan still shows active. The level-driven stop
  (plan drops the booking once `rem → 0` / target SOC reached) remains the
  primary, usually earlier stop; ON→OFF and OFF→ON dwells are unchanged.
  Concretely: drop the `not energy_limited` conditions at the force-off check
  and at the deadline-arming branch in
  `_apply_load_switching`/`_execute_load_switching`.
- **R8** Rationale for R7 (document in code): with sub-hour bookings a stale
  load-SOC sensor (fossibot flakiness: cached values with fresh timestamps)
  would otherwise keep `active_now` true for the whole slot hour and deliver up
  to `real_power × 1 h` against ~150 Wh of validated energy. The cap bounds the
  overrun at `real_power × max(min_runtime, D)`.
- **R9** `_maintain_recommendation_deadline` (recommendation-only loads) drops
  its `energy_limited` early-return for the same reason: the published `active`
  flag flips off at the deadline; the FIX-5 re-anchor cycle applies unchanged.
- **R10 (accepted consequence, document)** If a plan EXTENDS a running
  contiguous block, the frozen cap force-offs at the old `D`; after the
  `min_off` dwell the still-active plan re-ons with a fresh deadline
  (duty-cycle gap ≤ `min_off`, default 30 min). Harmless for charging
  powerstations; symmetric with existing non-energy-limited behaviour.

### Docs / housekeeping

- **R11** F-SUBHOUR-ALLOCATION.md: R1's "Energy-limited loads keep
  `max(slot.duration, q)`" and R12's "energy-limited: unchanged (level-driven,
  … no quantum, no force-off)" get a one-line superseded-by-F-RESIDUAL-TOPUP
  note (do not rewrite history, annotate). ALGORITHM.md D-A4 gets a **v4**
  bullet (operator decision 2026-07-10: lateness is absolute; energy-limited
  loads quantise like continuous ones; executor cap). CHANGELOG `[Unreleased]`
  → `[0.8.1]`; `manifest.json` version 0.8.1.
- **R12** The INFO night-booking log (`coordinator.py` ~line 839, F-PREDRAIN
  observability) currently skips energy-limited loads. Keep the skip for the
  "pre-drain" wording but note: with this fix an energy-limited night booking
  is only possible as last resort (no later feasible slot); no new logging
  required.

## 4. Design

### 4.1 Planner

One structural change: `_quantised_hours` loses the energy-limited
early-return; its docstring is rewritten to explain BOTH load classes (the
executor cap R7 replaces the old "no sub-hour cap" justification). Everything
else (candidate loop in both passes, `_spread_energy` spill, saturation gate,
Z2'/Z3(/Z4) gates, latest-first pass-2 order, booking/accounting) is already
candidate-generic and stays byte-identical.

Expected emergent behaviour for the incident geometry: pass 1 finds no surplus
tonight; pass 2 walks `last_export … 0` and now finds a feasible 0.5 h
candidate long before reaching slot 0 — inside or just before the next PV
window (the operator's "06:00 tomorrow" example is precisely the first
pre-window placement pass 2 falls back to when in-window slots fail a gate).
Slot 0 books only when it is genuinely the last feasible option (e.g. export
happening right now at the end of the day — which is pass-1 territory anyway).

### 4.2 Executor

`_apply_load_switching`: the force-off condition
`current and not energy_limited and deadline and now >= deadline` loses
`not energy_limited`. `_execute_load_switching`: the ON branch arms the
deadline for every controlled load with `run_h > 0` (remove the
`energy_limited` special case that popped the deadline). The OFF branch
(clear deadline + timer) is already load-agnostic.
`_maintain_recommendation_deadline`: drop the `or energy_limited` bail-out.

No persistence changes (`_load_run_deadline` is already in-memory with timers
cancelled on unload, F-SUBHOUR R13).

### 4.3 Sensor / display

None. `run_hours`, `schedule`, `planned_energy_kwh` and the per-load schedule
attribute are already sub-hour-aware and load-agnostic.

## 5. Test plan

Modify (semantics deliberately inverted by this feature):
- `test_quantised_hours_energy_limited_is_whole_slot_only` → replace with
  `test_quantised_hours_energy_limited_matches_continuous`: candidates for an
  energy-limited load equal the non-energy-limited list (whole first, k·q
  fallbacks, none below q).
- `test_energy_limited_load_books_only_whole_slots` → replace with an S3-based
  assertion that any sub-hour energy-limited booking is a `k·q` multiple
  (≥ one quantum) and `schedule[i] == (run_hours[i] > 0)` still holds.

Verify unchanged (must still pass; if not, STOP and report — do not weaken):
- `test_degenerate_slot0_never_triggers_min_runtime_charge`,
  `test_activation_books_at_least_min_runtime_energy`,
  `test_long_min_runtime_gates_interior_hours_consistently` (min_runtime 240:
  candidate list stays `[4.0]` — no sub-min_runtime run),
- `test_t3_energy_limited_never_night_charged_with_ratio` (T3): pass 1 should
  saturate the fossibot in-window before pass 2 ever looks at night slots. If
  T3 becomes flaky because in-window capacity < rem, report — that is a real
  semantic question for the operator, not a test to relax.
- FIX-2 / FIX-6 / F-PREDRAIN T1–T13, golden_night_predrain.

Add:
- **R4 live-scene regression** (see §3) in `tests/core/test_optimize.py`,
  clock 18:47-style partial slot 0 (e.g. `datetime(2026, 7, 10, 18, 47)`),
  fossibot `soc_percent=82.2`, `target_soc_percent=90`, capacity 2000; PV
  today ≈ 0 / tomorrow strong with clipping; assert: no slot-0 booking, no
  booking in slots starting before 06:00 next day, exactly one 0.5 h quantum,
  `planned_energy_wh ≈ 150`.
- **Latest-first tiebreak test**: two future slots both feasible for the same
  residual (construct via pass 2: no direct-surplus hour, two clip-refilled
  candidates) → the later slot hosts the booking.
- **Pass-1 residual capture**: a direct-surplus hour with ≥ 128 Wh export and
  a 156 Wh-residual fossibot books a 0.5 h pass-1 run in that hour (new
  capture class, documents R6).
- **Executor cap tests** (`tests/ha/test_load_switching.py`): (a) ON edge of an
  energy-limited controlled load with `run_h=0.5` arms `off_at = start +
  30 min` and the fired timer forces OFF even while the plan stays active
  (stale-SOC simulation); (b) plan-driven OFF before the deadline still works
  and clears the timer; (c) recommendation-only energy-limited load: published
  `active` flips off at the deadline (R9).

Goldens: regenerate via `scripts/gen_golden.py` ONLY after inspecting every
delta and classifying it as class (i) or (ii) of R2; list the classification in
the commit message. An unexplained delta is a stop-the-line finding.

Full suite must be green on `.venv314` (repo root, `python -m pytest tests`),
ruff clean.

## 6. Known limitations (accepted, pre-existing — do NOT fix here)

- The planner does not model `min_off` spacing between two disjoint sub-hour
  activations; with `min_runtime > 30` two adjacent-hour quanta can under-run
  (executor re-on blocked). Pre-existing F-SUBHOUR behaviour, unchanged by R1
  (site defaults 30/30 are safe: gap ≥ 30 min by construction).
- Planning power vs real power: an OFF load plans at nominal (300 W) while the
  real charger draws ~505 W; the dwell therefore delivers up to
  `real × min_runtime` (~250 Wh) against ~150 Wh booked, and a top-up can
  overshoot the target SOC by a few percent. Pre-existing (any activation is
  dwell-bound); the R7 cap bounds it, the target-SOC stop usually fires first.
- Sub-slot phase: commitments start at slot start (hourly grid, D-A7); "as
  late as possible *within* the hour" is below the model's granularity.

## 7. Cross-doc updates (part of this change)

- `docs/F-SUBHOUR-ALLOCATION.md`: superseded-notes on R1 and R12 pointing here.
- `docs/ALGORITHM.md` D-A4: new **v4** bullet (operator decision 2026-07-10).
- `CHANGELOG.md`: 0.8.1 entry under Fixed (night trickle-charge incident) and
  Changed (energy-limited sub-hour quantisation + executor cap).
- `custom_components/battery_manager/manifest.json`: 0.8.1.

## 8. Decision log

- **D1 (chosen)**: lift the quantisation carve-out (R1) + keep gates → the
  existing latest-first order does the right thing. Two-line core change.
- **D2 (rejected)**: truncated-energy booking `min(rem, power·commit)` at
  whole slots — books energy the dwell cannot match when `rem/power <
  min_runtime` (reverse v0.6.1 bug), and `run_hours` would overstate the run.
  *(Superseded for GATE-EQUIPPED loads by docs/F-GATE-TOPUP.md, v0.9.3: the
  rejection is dwell-based, and since F-EXECUTOR-GUARDS G1 the target stop is
  dwell-exempt behind a charge-enable gate — the executor delivers exactly
  `rem`, so one final sub-quantum candidate is booked to close the stall band.
  Plug-only loads keep this rejection verbatim.)*
- **D3 (chosen)**: executor deadline as upper cap for energy-limited loads
  (R7–R10) — protective against the site's known stale-SOC failure mode; the
  level-driven stop stays primary.
- **O1 (open, operator)**: within-window micro-ordering of pass 1 for
  saturating (energy-limited) loads. Today pass 1 books the FIRST direct-
  surplus hour (ascending); strict lateness would prefer the LAST export
  hours. Export-neutral either way (each surplus hour is use-it-or-lose-it in
  place); a per-class descending pass-1 sweep for energy-limited loads (before
  the continuous ascending sweep, preserving config-order priority) is the
  candidate design if the operator wants it. NOT part of v0.8.1.
