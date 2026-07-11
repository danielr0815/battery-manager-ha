# F-PERDAY-SURPLUS — per-day lost-surplus and import breakdown

Status: **binding spec** for v0.9.1. Operator request (2026-07-11): *"Ich
möchte den Überschuss immer für heute und morgen separat angezeigt bekommen."*

## 1. Design

No core/planner change: the plan trajectory already carries per-slot
`grid_export_wh` / `grid_import_wh` and the slots carry `start` timestamps.
The COORDINATOR aggregates them per calendar day (grouped by
`slot.start.date()`, planner-local time; a slot belongs to the day it STARTS
in) and the sensors expose the breakdown:

- **R1** Coordinator: build `daily = [{"date": "YYYY-MM-DD",
  "lost_surplus_kwh": x, "grid_import_kwh": y}, ...]` from the FINAL planned
  trajectory (the same one the existing totals come from), one entry per
  calendar day present in the slot grid, chronological, kWh rounded to 3
  decimals like the existing totals. Invariant: the sums over `daily` equal
  the existing totals (rounding aside).
- **R2** `sensor.…lost_surplus_forecast` gains attributes `today_kwh`,
  `tomorrow_kwh`, `daily` (today = date of slot 0; tomorrow = today + 1 day;
  a missing day renders 0.0 for the scalar attrs). Same three attributes —
  with import values — on `sensor.…grid_import_forecast`.
- **R3** The SOC-forecast sensor's attributes gain the same `daily` list
  (single source for dashboard cards; totals stay untouched).
- **R4** No new config, no entity registry changes, goldens untouched by
  construction (no `core/*` change).

## 2. Tests

- Two-day horizon: split matches a hand-computed per-day sum; totals ==
  Σ daily (existing scenario fixtures in tests/ha/test_coordinator.py).
- Slot-start day attribution documented and asserted (a slot starting 23:00
  belongs to its start day even if it conceptually crosses midnight —
  hourly grid, D-A7).
- Sensors expose the attributes; `today_kwh`/`tomorrow_kwh` fall back to 0.0
  when the horizon lacks the day.
- Full suite green (winshim), ruff check + format check (0.15.21), goldens
  byte-identical.

## 3. Versioning

manifest.json + pyproject.toml → 0.9.1; CHANGELOG `[0.9.1]` Added. Release
cut after review (HACS is release-tracking).

## 4. Explicit non-goals

The 17:00 dehumidifier booking the operator questioned the same morning is
NOT a code item: analysis (to be validated by repro) attributes it to the
F-PREDRAIN F4 β-insurance gate (c2) — a config-philosophy question
(`upper_pv_reserve`, `pv_window_end_hour`), handled as operator consultation,
not as part of this change.

## 5. v2 (v0.10.1): per-day LOAD energy (operator request 2026-07-11)

The operator's tile shows today/tomorrow as `X/Y kWh`; besides lost surplus
and grid import it must also show the energy planned FOR THE LOADS per day.

- **R-V2-1** `_daily_surplus_breakdown` additionally sums each day's
  `flow.extra_ac_wh` (the surplus-load energy the final trajectory schedules
  in that slot — model.py SlotFlow) into a `loads_kwh` field (rounded 3
  decimals) on every `daily` entry. Same slot-start-day attribution.
- **R-V2-2** The SOC-forecast sensor additionally exposes convenience
  attributes `loads_today_kwh` / `loads_tomorrow_kwh` (today = slot-0 day,
  tomorrow = today+1, 0.0 fallback — exact same convention as the v0.9.1
  today/tomorrow attrs on the two dedicated sensors).
- **R-V2-3** Consistency invariant, asserted in a test: the sum of
  `loads_kwh` over all `daily` entries equals the horizon total of
  `extra_ac_wh` (rounding aside). Appliances are NOT included (they enter
  the AC forecast, not `extra_ac_wh`) — document in the attribute comment.
- **R-V2-4** No planner/core change; goldens byte-identical. CHANGELOG
  `[0.10.1]`; manifest + pyproject → 0.10.1.
