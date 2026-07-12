# F-PREDRAIN — Hourly PV forecast & two-buffer pre-drain allocation (v0.8.0)

Status: approved by the operator 2026-07-10 ("Los gehts"). This document is the
implementation contract for WP1 (hourly forecast) and WP2 (allocation gates).
Architecture owner: Fable. Implementation: Opus agents. Review gates apply per
work package.

## 1. Problem (root cause, empirically verified)

The live plan of 2026-07-10 forecast **5.37 kWh lost surplus** over 62 h while
the dehumidifier (400 W, continuous, switchable) idled all night and the
battery sat at 46-64 %. Pass 2 of `allocate_loads` (core/optimize.py) is
designed to book preemptive "make room" hours (latest-first, operator decision
2026-07-05), and the export-refill gate (c) passes night candidates easily
(cross-day refill displaces export at ~1.216x the run energy). Every candidate
is instead killed by **Z2** (`optimize.py:295`,
`traj.total_import_wh > base_import + _EPS`, `_EPS = 1e-6` Wh): draining the
battery at night extends the next morning's charge into hours where the charger
was previously idle, and each such flipped hour books the charger's
`standby_power_w` (10 W default) as ~10 Wh of NEW grid import
(`simulate.py:169-173`). ~10 Wh of modeled standby import vetoes 250-520 Wh of
rescued export per candidate. Verified twice with the real planner: setting
charger standby to 0 makes the same code book night slots and drives lost
export 3.12 -> 1.05 kWh (independent probe: 3.93 -> 0.00 kWh).

A second limitation: the planner reads only the DAILY kWh state of the three
forecast entities (`coordinator.py:591-626`) and synthesizes the hourly shape
via the two-window model (`series.py:21-42`). The entities' hourly `wh_period`
attributes (Open-Meteo integration and the operator's balcony-solar-forecast
integration, 15-min buckets) are ignored.

## 2. Operator requirements (binding)

- **L1** Small modeling artifacts (10 Wh standby) must never veto sensible
  battery use.
- **L2** Always keep enough energy above the 20 % inverter-cutoff SOC.
- **L3** The LOWER buffer (inverter reserve) is time-of-day dependent: it may
  shrink toward the forecast solar onset.
- **L4** "As late as possible" stands: preemptive runs as late as the
  constraints allow, just early enough that nothing must be exported.
- **L5** Energy-limited loads (Fossibot powerstations) are NEVER night-charged
  from the house battery (operator decisions 2026-07-04/05). Pre-drain applies
  to continuous loads only.
- **L6** There are TWO buffers. The UPPER buffer (absorption headroom near max
  SOC) must be preserved while significant solar power is still expected for
  this specific installation; it may shrink from the moment the sun moves
  behind the house (low remaining forecast power). Site-specific: configurable
  AND derivable from the hourly forecast.

## 3. Design

> **Cross-note (v0.10.0, docs/F-QUANTILE-BANDS.md):** the scalar dials α
> (`predrain_pv_confidence`, §3.3) and β (`upper_pv_reserve`, §3.4) are now
> the per-slot FALLBACKS: where the balcony forecaster publishes empirical
> P10/P90 bands for a slot, the Z4 stress uses `p10/median` and the c2
> insurance uses `p90/median` there instead. Slots without a band — and a
> COLLAPSED band, the cold-start "no evidence" signature — keep the scalars,
> so an entry without any band data behaves exactly as specified below.

> **Cross-note (v0.11.0, docs/F-NIGHT-RESCUE.md):** the (c1) refill need is
> now EFFICIENCY-AWARE: `(1−tol)·energy·rt` with rt the AC→battery→AC
> round-trip factor of the configured chain (live ≈ 0.82). The old
> `(1−tol)·energy` demand was physically unsatisfiable for pure-battery
> night runs (a detour can only ever return rt of the energy as rescued
> export), which silently confined pre-drains to the morning shoulder. The
> Z4 stress floor's buffer component additionally ramps down toward the
> stressed PV crossover (F-NIGHT-RESCUE F3), and the T\* scan is
> merge-bounded at a stressed-clip slot (F2).

### 3.1 F1 — Hourly PV forecast (WP1)

- New pure-core module `core/forecast_hours.py` (no HA imports):
  - `aggregate_hours(entries: Iterable[tuple[datetime, float]]) -> dict[datetime, float]`
    Sums sub-hour buckets (15/30 min) into naive-local hour keys
    (`datetime` truncated to the hour). Values are Wh per bucket (Open-Meteo
    `wh_period` semantics). Multiple entities merge additively is NOT needed
    (the three entities are day-disjoint); merging maps for different days:
    last writer wins per hour, callers pass day-disjoint data.
  - `coverage_and_residual(day_hours, daily_total_wh) -> (covered_wh, residual_wh)`
    residual = max(0, daily_total - covered); used for fallback spread.
- Coordinator (WP1 part): when reading each of the three PV entities, also read
  the `wh_period` attribute (dict[str, number]); parse keys with
  `homeassistant.util.dt.parse_datetime`; naive keys are LOCAL, aware keys are
  converted to local then made naive. Malformed keys/values are skipped; NaN/±inf
  values are skipped and negatives clamped to 0 (FIX-9) with a single debug log.
  The stale-state cache is PER ENTITY (FIX-4): each entity keeps its own last-good
  `(map, timestamp)`, and each cycle the merge uses the entity's fresh map when
  non-empty else its cached map within `MAX_HISTORICAL_FORECAST_AGE_HOURS`. A
  cycle where one entity is unavailable therefore contributes that entity's cached
  buckets rather than dropping them — a partial read never overwrites the cached
  full map (the earlier single merged-map cache treated a partial read as a good
  read and clobbered it).
- `series.build_slots` gains `pv_hourly: dict[datetime, float] | None = None`
  (keys = naive local hour starts). Per slot: if the slot's hour is covered,
  `pv_wh = hourly_value * slot.duration_fraction_of_that_hour`; an uncovered hour
  of a day takes its two-window SHARE of that day's RESIDUAL:
  `residual_wh * pv_hour_share(hour) * duration`. There is NO renormalization over
  the uncovered/remaining slots (FIX-3): the two-window shares already sum to 1
  over the FULL day, so a day with NO buckets reproduces the legacy two-window
  values bit-for-bit — even when `now` is mid-morning and the elapsed hours are
  absent from the horizon. (The earlier renormalization divided the FULL daily
  residual by only the remaining-slot weight sum, inflating a partially-elapsed
  day's afternoon PV 3-5x.) The trade-off is that a PARTIALLY covered day now
  UNDER-fills its uncovered slots — conservative by design. The existing
  `pv.peak_power_w` cap continues to apply per slot AFTER this mapping.
- Mode option `pv_forecast_mode` (WP3 wires config; core accepts the map or
  None): `auto` (default; hourly when a map is present) | `hourly` | `daily`
  (ignore attributes). With `daily` or no attributes the result must be
  BIT-IDENTICAL to v0.7.19 (golden anchor).
- Diagnostics: `build_slots` callers can report per-day source
  (`hourly`/`two_window`) — expose via coordinator data for WP4.

### 3.2 F2 — Import trade rule Z2' (WP2)

For candidates of **continuous** (non-energy-limited) loads, in BOTH passes,
replace the absolute Z2 check with the cumulative invariant against the
no-loads base trajectory:

```
(trial.total_import_wh - base_import_wh)
    <= import_trade_ratio * (base_export_wh - trial.total_export_wh)
       + (1.0 Wh if import_trade_ratio > 0 else 0.0)
```

- `ControlParams.import_trade_ratio: float = 0.0` (NEUTRAL dataclass default —
  0.0 reproduces v0.7.19 behavior EXACTLY; goldens unchanged).
- The +1 Wh slack (so a lone ~10 Wh standby artifact can be traded, L1) applies
  ONLY when a positive ratio is configured (FIX-6). At ratio 0 there is no slack,
  so the gate is `trial import <= base + EPS` — bit-for-bit v0.7.19. (Earlier
  drafts added the slack unconditionally, which contradicted the "0.0 reproduces
  today's behavior exactly" guarantee; resolved here.)
- Recommended live value 0.10 is applied as the coordinator absent-key fallback
  and config-flow default (WP3), NOT as the dataclass default.
- Energy-limited loads keep a STRICT no-extra-import comparison (L5), but anchored
  at the CURRENTLY ACCEPTED series' import, not the no-loads base (FIX-2):
  `trial.total_import_wh <= current.total_import_wh + EPS`. An energy-limited
  booking must never add import (it stays out of the pre-drain trade machinery),
  but once a continuous load has already traded a little import, anchoring at the
  base would starve every LATER energy-limited candidate on pure surplus (it
  inherits the delta it did not cause). Anchoring per-candidate at `current`
  preserves L5 (the fossibot adds no import) while letting it still fill on
  genuine surplus. `current`'s import is threaded into the gate in both passes.

### 3.3 F3 — Lower buffer: pessimistic stress gate Z4 (WP2; REVISED v2)

**v2 revision (2026-07-10, after acceptance testing).** The v1 whole-horizon
stress (alpha applied to every slot, min over the full horizon) failed
acceptance on the live scenario: three consecutive alpha-days push the
stressed baseline to ~9 %, which (a) vetoes bookings the OLD planner accepted
(recommended config lost MORE than v0.7.19) and (b) goes vacuous once the
baseline pins at the bottom. It also contradicts the operator's replan
philosophy (the plan re-runs every 5 min; catch-up on better information).

**v2 semantics — the stress is LOCAL to the candidate's bet window:**

- `simulate(..., pv_scale: float | Sequence[float] = 1.0)` — scalar as before;
  a sequence gives a PER-SLOT scale factor (same length as slots).
- **Bet window** of a candidate starting at slot `i`:
  `recovery = end index of the first PV window (per pv_windows()) whose end
  is >= i` (the next full recharge opportunity), capped at the last slot. If
  no window ends at/after `i`, use the last slot.
- **Gate:** build the scale vector `s[k] = alpha for i <= k <= recovery, else
  1.0`; run the stress sim on the TRIAL series with that vector; let
  `m_trial = min(soc_end over slots i..recovery)`. Reject iff
  `m_trial < stress_floor - EPS AND m_trial < m_baseline - EPS`, where
  `stress_floor = inverter_min_soc_percent + soc_buffer_percent` and
  `m_baseline` is the same windowed min computed on the CURRENT accepted
  `extra` series with the SAME scale vector (degrade-fallback: pre-existing
  stressed dips must not veto a run that does not worsen them).
- Baseline caching: one baseline sim per candidate slot `i` (not per
  candidate duration); invalidate on acceptance. Slots before `i` are
  identical between trial and baseline (scale 1.0, same extra), so the
  windowed comparison is well-posed.
- `ControlParams.predrain_pv_confidence: float = 1.0` (neutral; alpha 1.0
  skips the gate entirely). Recommended live value 0.5 via WP3 fallback.
- NOTE: existing Z3 (`_degrades_min_soc` vs `soc_min + buffer` on the nominal
  sim) stays as-is for all loads/passes.
- `PlanResult.stressed_min_soc_percent` diagnostic (3.5): report the windowed
  stressed min of the FIRST booked pre-drain bet window under the final
  series, or None when alpha == 1.0 or nothing pre-drain-booked (implementer:
  keep it simple and document the exact choice).

**Acceptance criterion (binding):** on the 2026-07-10 repro scenario
(scratchpad `repro_v080.py`), the recommended config (ratio 0.10 / alpha 0.5 /
beta 1.2) must (a) book night slots in the FIRST two nights, (b) achieve
lost_surplus strictly below the neutral config's value (3.12 kWh) minus 1.0
kWh, and (c) keep `import_trade_used_wh <= 0.10 x rescued export + 1 Wh`.

### 3.4 F4 — Upper buffer: optimistic opportunity gate (c2) + PV window (WP2)

- **PV window per day** (derived from the slot PV series, so it works in both
  hourly and daily/two-window mode):
  `window(day) = [first, last]` slot index of that calendar day with
  `pv_wh / duration >= strong_pv_cutoff_w`. If `pv_window_end_hour` is set
  (site override), `last` is capped at the last slot starting before that local
  hour. "Sun behind the house" = after `last`.
- **Gate (c) generalized** for pass-2 candidates of continuous loads. Accept if
  EITHER:
  - (c1) nominal `export_drop >= (1 - battery_tolerance) * power_wh` (existing), OR
  - (c2) the candidate slot lies INSIDE its day's PV window AND, in the
    optimistic sim (`pv_scale = upper_pv_reserve`, beta), the export drop
    relative to the optimistic sim of the current series satisfies
    `export_drop_beta >= (1 - battery_tolerance) * power_wh`.
  Night slots are (c1)-only by construction (outside every PV window).
- Safety gates (Z2' ratio invariant on the NOMINAL sim, Z4 alpha stress, Z3)
  apply unchanged to (c2) acceptances.
- `ControlParams.upper_pv_reserve: float = 1.0` (neutral = gate off),
  `strong_pv_cutoff_w: float = 200.0`, `pv_window_end_hour: int | None = None`.
  Recommended live values via WP3: beta 1.2, cutoff 200 W, end-hour unset.
- Known limitation (documented, not to fix now): pass 2 still only runs when
  the NOMINAL horizon has export (`optimize.py:263`); a nominally export-free
  but beta-exporting day gets no insurance booking.

### 3.5 Diagnostics (WP2 core part; WP4 exposes as sensor attributes)

`PlanResult` gains:
- `import_trade_used_wh: float` (final `total_import - base_import`, >= 0 clamp)
- `stressed_min_soc_percent: float | None` (min SOC of the final accepted
  series under alpha; None when alpha == 1.0)
- `pv_window_ends: dict[str, int] | tuple[...]` — per-day window-end hour
  (implementer's choice of a small stable structure; goldens will include it).

### 3.6 Explicitly unchanged

Threshold search, grid-support escalation, appliance windows, executor
(freeze-deadline / min_runtime / min_off), energy-limited level control,
`_quantised_hours`, pass ordering (pass 1 ascending, pass 2 latest-first).

### 3.7 Known / accepted semantics (by design, not bugs)

- **Cumulative trade budget.** The Z2' trade allowance is minted by ALL rescued
  export across the whole allocation, and the invariant bounds the CUMULATIVE
  `import - base`, not each pre-drain individually. A single pre-drain's own
  modeled import may therefore exceed the export IT alone rescues, as long as the
  running total stays within `import_trade_ratio * total_rescued + slack`. This is
  intended: the operator trades a small aggregate import for the aggregate rescued
  export, and the per-cycle replan keeps the total bounded.
- **Cross-entity `wh_period` merge.** The per-entity hourly maps merge in
  (today, tomorrow, day-after) order with last-writer-wins over any overlapping
  days (FIX-4). The three PV entities belong to the same Open-Meteo family and
  carry identical data for the days they overlap, so last-writer-wins is harmless
  (it never blends conflicting forecasts). Should genuinely disjoint providers be
  wired later, this becomes a documented precedence rule rather than a merge bug.

## 4. Work-package boundaries (file ownership)

- **WP1**: `core/forecast_hours.py` (new), `core/series.py`,
  `coordinator.py` (attribute reading + pv_hourly plumbing ONLY),
  `tests/core/test_forecast_hours.py` (new), `tests/core/test_series.py`,
  HA test for attribute parsing. MUST NOT touch `optimize.py`, `simulate.py`,
  `model.py`.
- **WP2**: `core/optimize.py`, `core/simulate.py`, `core/model.py`
  (ControlParams + PlanResult fields), `tests/core/test_optimize.py`,
  `tests/core/test_simulate.py`, new golden scenario. MUST NOT touch
  `series.py`, `coordinator.py`, `config_flow.py`.
- **WP3** (later): `config_flow.py`, `const.py` (config keys + recommended
  fallback constants), coordinator option mapping, translations.
- Shared file `const.py`: WP1/WP2 do NOT edit it; any needed constants live in
  the touched core modules until WP3 consolidates.

## 5. Test contract

Core (fast, `.venv/Scripts/python.exe -m pytest tests/core/`):
- T1 night slot booked with charger standby 10 W when ratio=0.1 (repro-style
  scenario); NOT booked with ratio=0.
- T2 cumulative invariant: over a multi-candidate scenario, final
  `import - base <= 0.1 * (base_export - export)`.
- T3 energy-limited load never gets a no-surplus slot even with ratio > 0.
- T4 alpha stress: deep 22:00 multi-hour run rejected, same-energy pre-dawn run
  accepted; alpha=1.0 disables; floor is inverter_min_soc + buffer.
- T5 latest-first tie: of two equally feasible night slots the later books.
- T12 beta=1.2 books morning hours before the nominal full-time; beta=1.0
  does not; (c2) never books night slots; ratio invariant holds.
- T13 PV window derivation from an east-heavy hourly profile (early end);
  `pv_window_end_hour` override wins; daily mode derives from the synthetic
  profile.
- T6 parser: Open-Meteo hourly keys, balcony 15-min UTC keys, naive local
  keys, DST boundary, gaps -> residual spread, sum-mismatch warning path.
- T7 identity anchor: without attributes / daily mode / neutral params, plans
  are bit-identical to v0.7.19 (existing goldens must NOT change).
- T8 new golden `s_night_predrain` (live 2026-07-10 shape: start 90 % @ 09:24,
  ~8.1 kWh/day e4 hourly profile, dehumidifier 400 W tol 0.15 min_runtime 30,
  Fossibot B 872 Wh remaining, B2 68 Wh, charger standby 10 W, recommended
  params ratio 0.1 / alpha 0.5 / beta 1.2): expect pre-dawn + in-window
  bookings, lost surplus < ~1.5 kWh, `import_trade_used_wh` <= 0.1 * rescued,
  stressed min SOC >= 23 %.

Fixtures: real attribute payloads are in the session dump
(`live-dump-2026-07-10.md` in the session scratchpad; hourly tables inline).

Conventions: English comments; match existing code style; ruff clean
(`.venv/Scripts/python.exe -m ruff check custom_components tests`); goldens via
`scratchpad/gen_golden.py` (indent=1, sort_keys=True) — but existing goldens
must remain untouched (neutral defaults).
