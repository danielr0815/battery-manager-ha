# F-GATE-TOPUP — final partial quantum for gate-equipped energy-limited loads

Status: **binding spec** for v0.9.3. Trigger: 4-agent adversarial verification
2026-07-11 (workflow) CONFIRMED the "stall band": an energy-limited load can
never be re-booked once `rem < max(planning_power, nominal) × min_runtime/60`,
because `_quantised_hours` offers no candidate below one quantum (F-SUBHOUR R2)
and the saturation gate sits verbatim in both passes (optimize.py pass 1
~:337-341, pass 2 ~:440-444). Live consequence: F2400-B (2000 Wh, target 90 %,
learned ~600 W, min_runtime 30) is unbookable above **75 % SOC** (300 Wh
block) and chronically parks at ~85-89 %, never reaching its target — today's
run was cut by the dwell at ~88.9 % with `planned_energy=0`. Learned power
(v0.9.0) WIDENED the band (nominal-300 era: 82.5 %). Part of the reported
lost surplus is this forfeited headroom — a quantisation-policy limit, not
capacity.

## 1. Why the old rejection no longer holds (for one load class)

F-RESIDUAL-TOPUP §8 D2 rejected truncated-energy bookings because the executor
DWELL delivers ≥ min_runtime × real power once switched on — booking less than
that would recreate the v0.6.1 degenerate-slot-0 class in reverse. That
rationale is **dwell-based**. Since v0.9.0, F-EXECUTOR-GUARDS **G1** makes the
target-SOC stop **dwell-exempt** for energy-limited loads WITH a charge-enable
gate: the moment live SOC ≥ target, the replan empties the plan (rem→0),
desired=False, G1 zeroes the ON→OFF dwell, the enable gate stops charging
(confirmed-off ordering; plug switches currentless afterwards). Residual
overshoot = replan/actuation latency only (~10-50 Wh at 600 W; 5 s debounce,
worst case one 300 s poll) instead of the 300 Wh dwell overshoot. So for
**gate-equipped** loads the executor CAN deliver exactly `rem` — the planner
may book it. Plug-only loads keep the old rationale and the old behaviour
(G1 R3).

## 2. Requirements

- **R1 (core flag)** `SurplusLoad` gains `gate_stop_capable: bool = False`
  (frozen dataclass field, neutral default → all existing tests/goldens
  construct loads without it and stay bit-identical). The coordinator sets it
  iff `CONF_LOAD_CHARGE_ENABLE` is configured for the load subentry (the
  planner core cannot see subentry data — this is the single plumbing
  addition, in `build_system_config`).
- **R2 (final partial candidate)** In `_quantised_hours(load, slot)`: for a
  load with `energy_limited and gate_stop_capable`, the function needs the
  remaining energy to size the candidate — extend its signature to take the
  effective remaining (or compute the candidate at the call sites; choose the
  cleaner fit with the existing call shape, the two call sites already have
  `rem` and `power_w` in scope). Semantics: **after** the standard largest-
  first quantised list, append ONE extra candidate
  `commit_h_final = rem / max(power_w, nominal_power_w)` iff
  `0 < commit_h_final < q` (q = one min_runtime quantum) — i.e. exactly when
  every k·q candidate would fail the saturation gate. Being last/smallest
  preserves largest-first order and the whole-slot regression anchor.
- **R3 (de-minimis floor)** `GATE_TOPUP_MIN_WH = 50.0` in const.py (constant,
  no config key — G2 style): no final candidate below 50 Wh committed energy.
  Prevents relay-churn for negligible top-ups; with min_off 30 min the cycle
  frequency is bounded anyway.
- **R4 (gates unchanged)** The final candidate passes through the SAME gates
  as any candidate (soft-surplus/battery-tolerance in pass 1, Z2'/Z3 (+c1 for
  energy-limited) in pass 2, full re-simulation). The saturation gate check
  `rem < max(power_w, nominal) * commit_h` is naturally satisfied by
  construction (commit_h_final ≡ rem/max(...)); do NOT special-case the gate
  itself. Night-trickle protections hold: a slot without surplus/c1-refill
  still books nothing.
- **R5 (executor unchanged)** No coordinator/executor change. The ON-edge
  off-deadline stays `start + max(min_runtime, run_h·60)` — for a final
  quantum shorter than min_runtime the cap is LONGER than the booked run;
  that is CORRECT: the cap is the stale-SOC upper bound (F-RESIDUAL R7/R8),
  G1 is the primary stop at target. Document this in the spec-reference
  comment at the deadline arming site.
- **R6 (accounting)** `run_hours[i]` carries the final fraction; existing
  sub-hour-aware sensors/`active_run_hours` need no change. The explain-plan
  reason for a final-quantum booking appends `", final top-up to target"` so
  the operator can see why a shorter-than-min_runtime run was booked.
- **R7 (tests)**
  - Stall-band regression (the live scene): load 2000 Wh, target 90 %,
    learned 600 W, min_runtime 30, gate_stop_capable, SOC 84.9 %, exporting
    slot → books ONE candidate of `102/600 h ≈ 0.17 h` (~102 Wh), reason
    contains "final top-up"; WITHOUT gate_stop_capable (plug-only): books
    nothing (old behaviour).
  - De-minimis: SOC 89.9 % (rem 2 Wh... use rem < 50 Wh) → books nothing.
  - Largest-first preserved: at rem ≥ one quantum the candidate list is
    unchanged (no final candidate appended when a k·q candidate fits).
  - The F-RESIDUAL §5-derived assertion "any sub-hour energy-limited booking
    is a k·q multiple" is relaxed to "k·q multiple OR the gate-stop final
    quantum" — a DOCUMENTED semantic change (note it in the test docstring
    and the commit message), not a weakened test.
  - Goldens: expected UNCHANGED (golden loads have no charge_enable → flag
    False everywhere). Verify byte-identical; any delta is stop-the-line.
- **R8 (docs/version)** F-RESIDUAL-TOPUP §8 D2 gets a superseded-for-gate-
  equipped-loads note pointing here. ALGORITHM.md D-A4: v7 bullet (stall band
  + gate-stop top-up, 2026-07-11). CHANGELOG `[0.9.3]`; manifest + pyproject
  → 0.9.3.

## 3. Risks (accepted, document in code where relevant)

- **Stale-SOC worst case:** G1 never fires (frozen reading below target); the
  v0.8.1 cap delivers up to min_runtime × real power (~300 Wh) against a
  ~100 Wh booking → bounded ≤ ~+10 % SOC overshoot, same exposure class as
  today's gate-approved 0.5 h bookings; the G2 stale latch (12 min) stops
  planner re-booking on top. Note: runs shorter than 12 min never trip G2 —
  the cap is the only guard there; acceptable, bounded.
- **More gate/plug cycles near target:** bounded by min_off (30) + R3 floor.
- **Learned-power error** changes commit sizing but not delivered energy
  (G1 is level-driven at the SOC target) — robust by construction.

## 4. Non-goals

No change for plug-only energy-limited loads (dwell rationale intact). No
change to β/c2, α/Z4, pass ordering, or the balcony cutover. No config keys.
No executor changes.

## 5. Verify

Full suite green (winshim), ruff check + `format --check` (0.15.21), goldens
byte-identical (R7). Live observable after deploy: F2400-B reaches 90 %
(instead of parking at ~85-89 %) whenever export is available; its final
booking shows the "final top-up to target" reason.
