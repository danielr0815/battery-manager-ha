# F-GATE-PARITY — one gate set for both load classes, priority decides

Status: **binding spec**, implemented in v0.13.0. Operator decision 2026-07-17:

> „Es ist ein Spezifikationsfehler, dass B Energie nicht verwenden darf aber
> der Luftentfeuchter schon. Es soll immer die Priorität eingehalten werden…
> lieber den Fossibot laden, als den Luftentfeuchter betreiben, wenn die Wahl
> besteht."

## 1. Diagnosis (live incident, 2026-07-17)

The Fossibot F2400-B (priority 1) was externally drained to its 20 % discharge
floor by midday while the dehumidifier (priority 3) ran 13:00–15:15 on
battery-share "make-room" bets. BM had charged B the moment direct surplus
existed (12:50, ~700 W — priority worked in pass 1), but once PV faded, the
CLASS rules — not priority — decided who could keep taking energy: continuous
loads had the Z2' import-trade slack, the c2 beta insurance and the Z4-stressed
battery-share machinery; energy-limited loads were confined to a strict
no-extra-import gate and a c1-only pass-2 path. A lower-priority load could
book bet energy a higher-priority load was forbidden. That class asymmetry is
the spec error this feature removes.

## 2. Operator refinements (AskUserQuestion, 2026-07-17)

1. **Full import-trade parity.** Energy-limited loads use the SAME Z2'
   cumulative trade invariant (`import − base_import ≤ ratio · rescued_export
   + 1 Wh slack iff ratio > 0`, anchored at the no-loads base) as continuous
   loads. The former strict current-anchored branch (F-PREDRAIN FIX-2) is
   superseded: its anchor only existed to shield energy-limited candidates
   from inheriting continuous trade deltas — under one shared budget there is
   no per-class anchor to inherit.
2. **Nights stay continuous-only.** Energy-limited loads never book zero-PV
   pass-2 slots. Chosen predicate: **daylight** (`slot.pv_wh > 0`), NOT
   `in_window` — the stricter predicate would also bar pre-window daylight
   pre-charges before a short peak (a capability pinned by tests and golden
   before parity), re-introducing a daylight class asymmetry. Revert point if
   ever wanted stricter: the two predicate checks in `allocate_loads` pass 2.

## 3. Requirements

- **GP-R1** One `import_ok` for all loads (core/optimize.py, Z2' trade
  invariant). No class branch.
- **GP-R2** Pass-2 opportunity gates (c1-rt OR c2-beta-in-window) and the Z4
  windowed stress gate apply identically to both classes. Energy-limited
  candidates are additionally skipped when the candidate slot has
  `pv_wh ≤ 0` or the commitment would spill into a `pv_wh ≤ 0` slot
  (daylight rule; shorter quanta still get their chance).
- **GP-R3** Priority semantics (unchanged code, now load-bearing): pass 1 is
  load-outer in config order (strict global priority); pass 2 is slot-outer
  latest-first with loads inner in config order — within a contested slot the
  higher-priority load consumes budget first.
- **GP-R4** Diagnostics: `stressed_min_soc_percent` covers the earliest
  pass-2 booking of ANY load (the `cont_ids` filter is gone). The
  night-pre-drain INFO log keeps its energy-limited skip as a redundant
  guard: the class cannot book night slots, and its legitimate pre-window
  daylight bookings must not be misread as night pre-drains by the log's
  strong-PV window test.

## 4. Test contract (tests/core/test_optimize.py)

- `test_gate_parity_contested_bet_goes_to_priority_one` — a depth-capped bet
  slot (room for exactly one booking above the inverter cutoff) goes to
  whichever load is first in config order; swapping the order swaps the
  winner.
- `test_gate_parity_z4_stress_binds_energy_limited_bets` — Z4 caps an
  energy-limited bet's depth (α=0.5 rejects the full quantum in the
  cutoff↔ramped-floor gap; α=1.0 books it).
- `test_gate_parity_daylight_rule_blocks_fb_night_predrain` — maximum
  temptation, T1-shaped horizon: the fossibot never books a zero-PV slot
  while the same horizon's night pre-drain IS booked by a continuous load.
- `test_gate_parity_c2_beta_books_energy_limited_in_window` — T12 parity:
  beta opens extra in-window slots for the fossibot, reason string
  "in-window insurance", trade invariant holds.
- `test_gate_parity_shared_trade_budget_across_classes` (rewrites FIX-2) —
  shared budget: fb books 2250 Wh (was 1800 under the strict gate); the b1
  cumulative invariant and b2 attribution bound keep holding.
- Strengthened: T3 (never night — now via the daylight rule; every ON slot
  additionally asserts `pv_wh > 0`). Adapted with dawn shoulder light
  (`_twilight`, 5 Wh) so their pre-window intent survives the daylight rule:
  `test_pass2_preemptive_charging_when_sun_window_too_short`,
  `test_pass2_places_preemptive_hours_latest_first`,
  `test_pass2_residual_books_latest_of_two_feasible_slots`.

## 5. Golden classification (v0.13.0 regeneration)

- All no-load scenarios, `s3_loads_night`, `s3_low_soc_5am`, `s4_midday_full`
  and the whole `golden_night_predrain.json`: **bit-identical**. (In the
  night-predrain scene the fossibot's small residual need is met by pass 1,
  so no budget contest arises and the dehumidifier's night pre-drain is
  untouched.)
- `short_peak_preempt`: **expected delta** — f1 loses its three pre-window
  pass-2 hours (1800 → 900 Wh, lost surplus 2.31 → 3.40 kWh, import
  unchanged 0.0, no slot outside 11–14). The synthetic two-window PV model
  has EXACTLY 0 Wh before 11:00, so the pre-window hours are night by the
  daylight rule. Real hourly forecasts carry morning light — live, the
  pre-window pre-charge capability persists (the adapted tests model this
  with `_twilight`).

## 6. Supersedes

- F-PREDRAIN.md L5 ("pre-drain applies to continuous loads only", "NEVER
  night-charged" as a class rule) and §3.2 FIX-2 (strict current-anchored
  energy-limited gate).
- F-NIGHT-RESCUE.md non-goal "the energy-limited L5 restrictions stay
  (strict import, no β/c2)" and the R8 "continuous-load pre-drain bets"
  scoping (Z4's ramped floor now binds energy-limited bets too).
- F-RESIDUAL-TOPUP.md non-goal restating the L5 carve-out ("no α-stress,
  no β-insurance, strict import gate").
- F-PREDRAIN.md §3.2 / §3.4 class-scoped gate definitions ("for candidates
  of continuous loads").
- The stale optimize.py L5 comments (removed with this feature).
- L5 survives ONLY as: energy-limited loads never book zero-PV (night)
  slots. Everything else is parity + priority.

## 7. Risks accepted by the operator

1. The dehumidifier's night pre-drain can shrink on days where a hungry
   fossibot consumes the trade/c2 budget first — intended (priority).
2. `stressed_min_soc_percent` may jump between releases (now covers
   energy-limited bets).
3. Fossibot charging may now cause small traded grid import (bounded by the
   shared `import_trade_ratio` budget, live 0.1). *Superseded (v0.15.0,
   docs/F-STRICT-SURPLUS.md R1): the trade budget is retired; the shared
   import gate is now the absolute `IMPORT_ARTIFACT_SLACK_WH` — the parity
   principle (ONE gate for all classes) is unchanged.*
4. Dawn-shoulder slots (minimal PV) count as daylight — deliberate.
