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
  converted to local then made naive. Malformed keys/values are skipped with a
  single debug log. Result cached alongside the existing stale-state cache
  (same fallback semantics: on entity unavailable, reuse last good map).
- `series.build_slots` gains `pv_hourly: dict[datetime, float] | None = None`
  (keys = naive local hour starts). Per slot: if the slot's hour is covered,
  `pv_wh = hourly_value * slot.duration_fraction_of_that_hour`; uncovered hours
  of a day share the day's RESIDUAL via the existing two-window weights
  (renormalized over uncovered hours only, clamp >= 0). The existing
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
    <= import_trade_ratio * (base_export_wh - trial.total_export_wh) + 1.0 Wh
```

- `ControlParams.import_trade_ratio: float = 0.0` (NEUTRAL dataclass default —
  0.0 reproduces today's behavior exactly; goldens unchanged).
- Recommended live value 0.10 is applied as the coordinator absent-key fallback
  and config-flow default (WP3), NOT as the dataclass default.
- Energy-limited loads keep the strict Z2 comparison unchanged (L5).

### 3.3 F3 — Lower buffer: pessimistic stress gate Z4 (WP2)

- `simulate(config, inputs, threshold, extra_ac_wh=..., pv_scale: float = 1.0)`
  — multiplies each slot's `pv_wh` by `pv_scale` (1.0 = no change; keep the
  parameter orthogonal to the peak cap, which applies to the UNSCALED input at
  build time; scaling happens inside simulate's slot loop).
- For every **pass-2** candidate of a **continuous** load: additionally run the
  stress sim with `pv_scale = predrain_pv_confidence` (alpha) on the trial
  series and gate with `_degrades_min_soc(trial_stress, current_stress, floor)`
  where `floor = inverter_min_soc_percent + soc_buffer_percent` (NOT
  `soc_min + buffer`; the inverter cutoff is the operator's protected bound,
  L2) and `current_stress` is the stress sim of the currently accepted `extra`
  series (recompute on each acceptance; cache between candidates).
- `ControlParams.predrain_pv_confidence: float = 1.0` (neutral; alpha 1.0 makes
  the stress sim identical to the nominal sim). Recommended live value 0.5 via
  WP3 fallback.
- NOTE: existing Z3 (`_degrades_min_soc` vs `soc_min + buffer` on the nominal
  sim) stays as-is for all loads/passes.

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
