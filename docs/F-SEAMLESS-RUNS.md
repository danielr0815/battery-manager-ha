# F-SEAMLESS-RUNS — no artificial OFF at quantum boundaries (v0.14.0)

Status: **binding spec**, operator decision 2026-07-18. Implemented in
`coordinator.py` (`_seamless_extension_ok`, the deadline branch of
`_apply_load_switching`, `_maintain_recommendation_deadline`).

## 1. Operator model of min_off (binding)

> "Die Idee von min_off ist nicht, dass das Gerät mindestens die
> eingestellte Zeit aus ist jede Stunde, sondern dass es mindestens die
> konfigurierte Zeit aus bleibt, wenn es einmal ABSICHTLICH deaktiviert
> wurde."

min_off arms ONLY on deliberate deactivations. A booked-quantum boundary
where the plan wants to continue seamlessly is NOT a deactivation.

## 2. Incident (2026-07-18, observed live)

The plan booked the fossibot in 30-min quanta; the executor force-offed at
each frozen deadline (`run_start + max(min_runtime, run_h)`), the replan
re-booked SECONDS later (recommendation off 13:25 → on 13:25), and min_off
blocked the re-on until :55 — a 50 % duty cycle: pointless relay cycles,
compressor/charger restart inrushes (which fed the F-ROBUST-POWER 818 W
incident), and halved charging rates.

## 3. Mechanics

At an EXPIRED run deadline with the load still running, the executor now
checks THIS refresh's plan (recomputed with fresh remaining/SOC data before
switching runs):

- Plan re-booked a contiguous run starting now (`active_now` and
  `plan.active_run_hours(slot_durations) > 0`) AND the load is
  extension-eligible → **EXTEND**: re-freeze the deadline at `now + booked
  run` (min 1 min), re-arm the off-timer, no OFF call, **no dwell stamp**.
- Otherwise → force OFF as before (stamps min_off).

Same semantics for recommendation-only loads in
`_maintain_recommendation_deadline`: an eligible load re-anchors immediately
at the expired deadline (no min_runtime floor — it was already served), so
the published `active` never dips at a boundary. The FIX-5 duty-cycle
fallback remains for extension-INELIGIBLE loads.

### Eligibility (`_seamless_extension_ok`)

- Continuous loads: always eligible (the deadline is quantum bookkeeping).
- Energy-limited loads: only while the G2 stale-SOC guard CAN supervise them
  (control switch + SOC entity + power feedback all configured) AND they are
  not stale-latched. Anything G2 cannot supervise keeps the F-RESIDUAL-TOPUP
  R7/R8 duty-cycle cap — including recommendation-only energy-limited loads
  — because the cap is the only defence against a stale `remaining`
  stretching a small top-up into an unbounded charge.

### Decision log

- **No min_runtime floor on extensions**: the original deadline was
  `run_start + max(min_runtime, run)`, so min_runtime is always served
  before an extension is evaluated; flooring again would over-deliver.
- **No dwell stamp on extensions**: min_off arms only on real OFFs
  (plan-off, deadline without re-booking, G1 target stop, G4 floor guard) —
  exactly the operator's model. The cloud-flap OFF (surplus gone) keeps
  min_off: that IS deliberate.
- **Cap per extension = the newly booked contiguous run**, never
  indefinite: with a stale SOC, G2 latches within `STALE_LOAD_SOC_MIN`
  (12 min) of frozen-SOC charging evidence, the load goes unavailable, the
  planner drops it, and the next deadline expires without a re-booking.
- **No boundary-gap epsilon**: `_spread_energy` fills an earlier slot to its
  boundary before spilling, so a contiguous continuation is always visible
  as one unbroken run in the fresh plan; a hole is a deliberate planner
  pause → OFF + min_off is correct.
- **Mid-run extensions still do NOT move the deadline** (F-PREDRAIN §5 T9b
  kept): only AT the expired deadline is the fresh plan consulted.

## 4. Interaction table

| Mechanism | Interaction |
|---|---|
| G1 target stop | Unchanged; a real OFF, stamps min_off (anti-flap at target SOC). |
| G2 stale-SOC | Gates eligibility; a latch forces the deadline OFF path. |
| G4 floor guard | Checked BEFORE the deadline branch — always wins over an extension. |
| F-RESIDUAL-TOPUP R7/R8 | Preserved for G2-unsupervisable energy-limited loads (incl. rec-only, R9/FIX-5 duty cycle). |
| Runtime counter / `_load_is_running` | Read real power/charging state and the moving deadline consistently; no change. |
| Persistence | `load_run_deadline` already round-trips; an extended deadline survives restarts. |

## 4b. Accepted limitations (review 2026-07-18)

- An IN-FLIGHT switching task at the exact boundary makes the refresh
  return before the extension is evaluated — the published `active` may dip
  for one cycle (executor-side harmless; the next refresh extends).
- Recommendation-only loads UNDER-credit runtime by up to one refresh gap
  per seamless boundary (counter reads the deadline-capped `active`).
- The deadline extension is persisted in the extending cycle itself; a
  restart in the sub-second between set and save falls back to the previous
  deadline (conservative: earlier force-off path).

## 5. Tests

`tests/ha/test_load_switching.py`: seamless extension (no OFF call, fresh
deadline ≈ now + booked run, dwell stamp untouched), extension length =
booked run not min_runtime, deadline-off when NOT re-booked (+ min_off gates
the re-on), G2-latched energy-limited never extends, energy-limited without
a power sensor never extends, G4 wins over extension, rec-only seamless
published `active` never dips, rec-only ENERGY-LIMITED keeps the FIX-5 duty
cycle, night-block extend/off phases, mid-run extension never moves the
deadline.
