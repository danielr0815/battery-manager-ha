# F-RESCUE-EXPORT — pass-1 energy-limited loads rescue present export first

Status: **binding spec** for v0.9.2. Operator decision 2026-07-11 (live
observation: Fossibot F2400-B at 73.9 % SOC idle while the house battery sat
at 99 % and ~1.7 kW was exported; the plan had deferred its 0.3 kWh charge to
13:00). Refines docs/F-PLANNER-HONESTY.md R7 v2 (day-bounded latest-first) for
**pass 1 only**.

## 1. Problem

v0.9.0 made pass-1 slot placement for **energy-limited** loads day-bounded
**latest-first** (days ascending, hours within a day descending). Live
consequence: with the house battery full and export happening NOW, the planner
deferred the Fossibot's charge to the latest export hour of the day — leaving a
device with room idle while surplus was actively lost. This violates the second
half of the operator's timing principle ("as late as possible, **but just early
enough to avoid export**", docs/operator-load-timing-goal / D-A4).

## 2. Key insight — pass 1 is post-saturation, so lateness has no benefit

Pass 1 only ever places a load on **direct surplus**: a candidate slot passes
the soft-surplus gate only when `grid_export_wh > 0` there, i.e. the battery is
already **full and exporting** in that slot. Latest-first is only justified when
deferring lets the battery **time-shift** the surplus (charge now, spend later)
— but once the battery is saturated and exporting, surplus not consumed in a
slot is **lost irrevocably**; deferring to a later export slot rescues the same
energy (an energy-limited load charges its fixed remaining capacity either way)
while (a) losing the *present, certain* export and (b) betting on a *later,
forecast* export slot. So among pass-1 (export) slots, the load must run **as
soon as export occurs**, not as late as possible.

Therefore "don't defer past a slot where surplus is already being lost" (the
operator's endorsed rule) resolves, for pass 1, to **earliest-export-first**.

Latest-first remains correct where the battery can still buffer — that is
**pass 2** (preemptive/insurance bookings BEFORE saturation), which is
unchanged. The two regimes together are the faithful reading of the principle:
buffer while you can (pass 2, defer the bet), rescue immediately once you
cannot (pass 1, run now).

## 3. Requirements

- **R1** In `allocate_loads` pass 1, energy-limited loads use **ascending**
  slot order (`range(n)`) — identical to continuous loads. Concretely: drop the
  `slot_order = _daywise_latest_first(...) if load.energy_limited else range(n)`
  branch so pass 1 iterates `range(n)` for every load, and **remove the now-dead
  `_daywise_latest_first` helper** (verify it has no other caller — pass 2 uses
  its own descending `range(last_export, -1, -1)` loop and does not use it).
- **R2** Pass 2 is **unchanged** (still latest-first, `range(last_export, -1,
  -1)`): preemptive bookings before saturation legitimately defer the bet.
- **R3** Everything else from F-PLANNER-HONESTY is **unchanged**: load-outer
  strict-priority structure (R7's outer loop), learned planning power (F1),
  explain-plan reasons (F3), exact re-simulated surplus reads (R8). Only the
  intra-pass-1 slot ORDER for energy-limited loads changes.
- **R4** The v0.8.1 residual/saturation protections are untouched: the
  saturation gate (`rem < max(power_w, nominal) * commit_h → skip`) still
  applies, so a night slot with no export is never booked (it has no surplus to
  pass the soft gate), and a residual still lands in the PV window — now at its
  **first** export slot instead of its last.
- **R5 (golden policy)** Energy-limited pass-1 placements move EARLIER (toward
  the first export slot of their day). Expected delta classes: (vi)
  energy-limited hours move earlier within the same day's export window (same
  planned energy — pure placement); (vii) minor continuous-load re-arrangement
  from the changed accepted-trajectory order under load-outer. Every golden
  delta inspected, classified, and listed in the commit; **import must not rise
  in any scenario** (assert per-scenario `import_kwh` unchanged-or-better).
  Regenerate via `scripts/gen_golden.py` + the night-predrain `__main__` only
  after classification.
- **R6 (tests)** Update the assertions that pinned energy-limited pass-1
  placement to the LATEST slot (they now assert EARLIEST):
  - `test_pass1_residual_capture_in_direct_surplus_hour` — assert the booking
    is at today's **first** exporting slot (was "last").
  - `test_r4_live_scene_residual_books_next_day_not_slot0` — the untouchable
    invariants stay (no slot-0 booking on the export-free first day, nothing
    before 06:00 next day, exactly one 0.5 h quantum ≈150 Wh); change the
    lateness assertion to the **first** export slot of the booked day.
  - `test_pass2_residual_books_latest_of_two_feasible_slots` — pass 2,
    **unchanged** (must still pass as-is; if it breaks, STOP and report).
  - Remove any `_daywise_latest_first` unit test.
  - **Add** a live-scene regression: house battery full and exporting in the
    current slot, an energy-limited load with headroom (e.g. 73.9 %→90 %) and
    a later export slot also feasible → the load books the **current/earliest**
    export slot, NOT the later one (assert the booked slot index is the first
    export slot, and `schedule[0]`/`active_now` reflects "run now" when slot 0
    exports).
- **R7 (docs)** docs/F-PLANNER-HONESTY.md R7: add a superseded-note that pass-1
  energy-limited order is earliest-first as of v0.9.2 (F-RESCUE-EXPORT); pass 2
  stays latest-first. docs/ALGORITHM.md D-A4: **v6** bullet (2026-07-11 operator
  decision, the pass-1-vs-pass-2 regime split above). CHANGELOG `[0.9.2]`;
  manifest.json + pyproject.toml → 0.9.2.

## 4. Non-goals

No pass-2 change. No new config. No change to Fossibot target SOC (operator:
max SOC stays 90 %). No change to continuous-load behaviour (already ascending).
No change to the saturation/residual/dwell machinery. The residual lost surplus
on a capacity-limited day (loads too small to absorb the whole midday peak) is
NOT addressed here — it is a sink-capacity limit, not a timing one.

## 5. Verify

Full suite green on `.venv314` (winshim), ruff check AND `ruff format --check`
(0.15.21). Goldens regenerated with per-scenario classification (R5). Release
cut as v0.9.2.
