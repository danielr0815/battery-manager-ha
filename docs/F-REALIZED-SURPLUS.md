# F-REALIZED-SURPLUS — Ist-Bilanz des Überschusses / realized surplus accounting (v0.24.0)

Status: operator decision 2026-08-03. Binding spec of the measured ("Ist")
surplus accounting: the coordinator pair `_counter_delta` /
`_update_realized_surplus` (HA only — `core/` is untouched), the options
section `surplus_accounting` with `export_meter_entity`, the optional per-load
`energy_entity`, the persisted store block `realized` and the eight sensors
`sensor.battery_manager_lost_surplus_realized_today` …
`sensor.battery_manager_true_export_energy`.

## 1. Ziel und Motivation

Everything the integration reported so far was **forecast**: `lost_surplus` and
`prevented_export` came out of the planner's counterfactual, not out of a
meter. That answers "what does the plan expect" but never "what actually
happened today" — and the two drift apart exactly on the days that matter (a
forecast miss, a load that did not run, a cloud band at noon).

This feature adds the **measured** side from the plant's own energy counters
and mixes it with the forecast in the honest direction: **today = measured so
far + forecast for the rest of the day**, **tomorrow = pure forecast**. The
measurement additionally **corrects** a metering artefact of this plant: loads
supplied outside the grid measuring point (the cellar dehumidifier on a circuit
the EM540 does not see) show up 1:1 as "export", so the raw export counter
over-reports lost surplus. Subtracting their real consumption yields the
**true export** — a monotone counter the HA energy dashboard can consume.

The feature is **opt-in through wiring**: no export meter configured ⇒ no
`realized` data block, no sensors, everything behaves exactly as before.

## 2. Regeln

- **R1 (Aktivierung/Gate).** The single switch is the option
  `export_meter_entity` (options-flow section `surplus_accounting`, energy
  device class). It is deliberately **absent from `DEFAULT_CONFIG`**: unset =
  feature off. Without it `_update_realized_surplus` returns `None`, the
  `realized` key stays **absent** from `coordinator.data` (older cards keep
  their old rendering) and the sensors 1–6 + 8 are not created; leftovers of a
  removed meter are dropped from the entity registry instead of lingering
  (pattern: `FeedInModeSensor`). Sensor 7 (realized early feed-in) rides on the
  **F-FEEDIN gate alone** and needs no export meter.
- **R2 (Zählerquellen).** Two kinds of monotone kWh counters feed the
  accounting: the **export meter** (`export_meter_entity`, e.g. the Victron
  reverse energy total) and the optional **per-load energy counter**
  (`energy_entity` on a `surplus_load` subentry, e.g. a Fritz!Powerline energy
  sensor — optional key, no migration, absent = estimate as before). Both are
  added to `_tracked_entities()`, so a counter update wakes the coordinator and
  the delta is booked promptly instead of only at the next 5-minute cycle.
- **R3 (Delta-Bildung & Baseline).** `_counter_delta` keeps the last reading
  per entity in the persisted `realized` block. The **first ever** reading is a
  baseline only (no delta). An **unchanged** value books nothing but keeps the
  accrual window open — this is what anchors the jump cap of the next change
  (R4). An `unavailable`/`unknown` state books nothing and marks the entity for
  **re-learn**: the next valid state becomes a fresh baseline, so an outage gap
  is never dumped as one delta.
- **R4 (Sprungfilter, decision 7).** Negative deltas (counter reset, telemetry
  backwards jump) are dropped — a monotone counter never legitimately
  decreases. A positive delta is dropped when it exceeds
  `REALIZED_JUMP_MAX_FACTOR (3.0) x cap power x elapsed hours`, where the cap
  power is the **learned** load power (fallback: the nominal `power_w`) and,
  for the export meter, the configured **PV peak** — the plant can never export
  more than it produces, since feed-in is passthrough and the battery is never
  discharged to the grid. The elapsed time is measured **since the reading's
  VALUE last changed**, not since our last read: a coarse counter (the
  Fritz!Powerline publishes in ~0.1 kWh chunks) sits unchanged for many cycles
  and then delivers the whole accrual window in one delta, so a per-read
  elapsed would false-positive on every legitimate chunk. The absolute
  `REALIZED_JUMP_MAX_WH = 2000` Wh covers only the one case without an elapsed
  time: the first delta of a reading restored from the store. It must stay a
  rare fallback — as a flat cap on the export meter it silently discarded a
  whole legitimate accrual after any cycle gap (planner failure, HA restart),
  and since the baseline advances anyway that energy was lost forever (review
  2026-08-03). **In every drop case the stored reading still advances** —
  otherwise a single bad jump would poison every later delta forever (the
  inflated baseline would keep producing huge deltas). The reading is scaled by
  the counter's **own `unit_of_measurement`** (Wh/kWh/MWh/GJ; unknown ⇒ kWh
  with a one-time warning), because the options-flow selector filters by device
  class only — assuming kWh mis-scaled a Wh counter by 1000x and the filter
  then swallowed every delta, leaving all sensors at 0 forever.
- **R5 (Korrigierter Export = verlorener Überschuss Ist, decisions 3/4).** Per
  cycle: `corr = export_delta − (Σ deltas of the EXTERNAL loads + carried
  debt)`, where external means a load subentry with `in_house_measurement =
  False` **and** a wired `energy_entity` — it is supplied outside the measuring
  node, so its draw sits falsely in the export counter. A negative result is
  **carried forward as `external_debt_wh`**, not clamped away: a coarse load
  counter delivers ~0.1 kWh in one chunk while the export meter trickles a few
  Wh per cycle, so a per-cycle clamp discarded almost the whole correction and
  the artefact this exists to remove survived (review 2026-08-03). A load delta
  arriving on a cycle where the export meter did not move at all is debt too.
  The debt is persisted and survives midnight — the metering artefact does too.
  An external load **without** an energy counter is not subtracted — the
  correction is measurement-based only, never an estimate. `corr` accumulates
  into both the day counter `lost_wh` and the monotone `true_export_total_wh`.
- **R6 (Verhinderter Export Ist, decision 5).** The realized prevented export
  is the energy the surplus loads actually consumed: the **measured delta of
  EVERY load with an energy counter** (in-house or not) plus, for loads without
  one, the **existing runtime counter's delta x planning power** — the same
  learned-power fallback as R4. Deliberately no second runtime integrator: the
  checkpoint (`_realized_runtime_base`) is in-memory, a restart re-arms it and
  the first delta after a restart is 0 — the same bounded loss the runtime
  cursor itself has.
- **R7 (Frühe Einspeisung Ist, decision 6).** The realized early feed-in is
  **not** re-measured: the F-FEEDIN executor already integrates the delivered
  energy from the setpoint it actually wrote (`_feedin_tick`). The accounting
  bridges that integral (same-day value only) into the persisted counter
  `feedin_wh`, which is what makes it survive a restart — since this feature
  the delivered integral lives in the persisted `realized` block, and the tick
  cursor re-arms at boot so downtime is never credited. Feed-in energy is grid
  export, so it is **also** contained in the corrected export of R5 (consistent
  with the planner, which counts pre-shifted export as lost surplus); the
  separate counter tells the operator how much of the loss was intentional.
- **R8 (Ist+Prognose-Mix, decision 1).** `_update_realized_surplus` runs with
  the fresh per-day forecast breakdown in scope. `*_today_kwh` = realized so far
  **+** the day's forecast value (which covers the remaining day),
  `*_tomorrow_kwh` = pure forecast, `*_realized_kwh` = measurement only. All
  values kWh rounded to three decimals; the forecast parts reuse the
  already-rounded `daily` values.
- **R9 (Tagesrollover, decision 8).** Rollover is by **local ISO date**: the
  three day counters (`lost_wh`, `prevented_wh`, `feedin_wh`) restart at 0, the
  **last readings carry over** as the new day's baseline (so midnight itself
  never shows up as a counter jump), and `true_export_total_wh` is monotone
  across days.
- **R10 (Persistenz).** Store block `realized` in the existing runtime Store
  (`battery_manager.<entry_id>`), shape `{date, lost_wh, prevented_wh,
  feedin_wh, true_export_total_wh, external_debt_wh,
  last_readings {entity_id: float}}`, written
  via `_save_persistent_state()` whenever something changed. Restored
  defensively (a missing or corrupt block starts neutral); the day counters are
  adopted only when the stored date is today, the readings always.
  `_feedin_delivered` is restored from `feedin_wh` on a same-day boot. The
  in-memory companions are deliberately volatile: `_realized_last_ts` (jump-cap
  window), `_realized_relearn` (dropout marks), `_realized_runtime_base` (R6
  checkpoints).
- **R11 (Zykluslage).** `_update_realized_surplus(now, daily_surplus)` runs at
  the **end** of `_async_update_data` — after `_update_load_runtime` (top of
  cycle, so the runtime counters of R6 are current), after `_apply_feedin` (so
  the delivered integral of R7 is current), and after the daily breakdown was
  hoisted out of the return dict (R8). It runs only on **successful** cycles: a
  cycle that fails (e.g. the house-SOC stale watchdog) books nothing, which is
  the safe direction — the readings simply carry to the next cycle.
- **R12 (Entities).** Eight sensors, all `device_class: energy`, unit kWh:
  1. `lost_surplus_realized_today`, 2. `lost_surplus_today`,
  3. `lost_surplus_tomorrow`, 4. `prevented_export_realized_today`,
  5. `prevented_export_today`, 6. `prevented_export_tomorrow`,
  7. `early_feed_in_realized_today`, 8. `true_export_energy`.
  1–7 are day values ⇒ `state_class TOTAL`; only 8 is `TOTAL_INCREASING` so the
  energy dashboard can consume it. 1–6 + 8 read the `realized` data block and
  are gated on the export meter (R1); 7 (`EarlyFeedInRealizedSensor`) reads
  `coordinator.feedin_delivered_today_kwh()` directly, is gated on
  `feedin_enabled` and is always available (a persisted counter).
- **R13 (Datenvertrag).** The `realized` block in `coordinator.data` has
  exactly these keys, present only under R1: `date`,
  `lost_surplus_realized_kwh`, `lost_surplus_today_kwh`,
  `lost_surplus_tomorrow_kwh`, `prevented_export_realized_kwh`,
  `prevented_export_today_kwh`, `prevented_export_tomorrow_kwh`,
  `early_feed_in_realized_kwh`, `true_export_total_kwh`. The same block is
  republished **as an attribute of the `soc_forecast` sensor** (that is where
  the card reads it), again with the key **omitted** rather than empty when the
  feature is off: the card branches on the attribute existing, and `{}` is
  truthy in JS — an empty dict would render a permanent "(Ist 0.0)".
- **R14 (Card).** With the `realized` block present the stats line **replaces**
  its lost/prevented segments (no double reporting) by the Ist-annotated form
  `verlorener Überschuss 2.3/1.1 (Ist 0.8)` — the established `today/tomorrow`
  slash pattern plus the measured share in parentheses, so the trailing legend
  `(kWh · heute/morgen)` keeps covering every segment. A realized early feed-in
  > 0 adds `frühe Einspeisung Ist 0.6`. `true_export_total_kwh` is deliberately
  not rendered (it belongs on the energy dashboard). Without the block the line
  renders **character-identical** to before; missing single fields degrade to
  `0.0` via the file's `num()` convention instead of crashing.
- **R15 (Neutralität).** The feature is HA-side only: `core/` (planner,
  simulation, goldens) is not touched, so the plan is bit-identical with and
  without it. Not configured ⇒ no data key, no entities, unchanged card.

## 3. Abgrenzung (was bewusst NICHT getan wird)

- **Kein Backfill.** The counters start at 0 when the meter is wired; no
  recorder/statistics history is reconstructed. The first day after wiring is
  therefore partial by design.
- **Keine zweite Laufzeit-Integration.** The runtime estimate of R6 reuses the
  existing per-load runtime counter; no parallel integrator, no cross-restart
  memory of its checkpoint.
- **Keine Schätzung in der Export-Korrektur.** Only loads with a real energy
  counter correct the export meter (R5) — an estimated correction would silently
  manufacture "true export" out of a guess.
- **Prognose-Semantik unverändert.** `prevented_export_by_day_wh` stays the
  planner's counterfactual (base vs. alloc). The realized counterpart measures
  what the loads consumed — the two are related but not the same quantity, and
  neither is redefined in terms of the other.
- **Kein Import-/Kostenteil.** Only the surplus side is measured; grid import,
  tariffs and costs stay out of scope.

## 4. Tests

- `tests/ha/test_realized_surplus.py` (22 tests). From the review 2026-08-03:
  the external-load correction carried forward as a debt (over several cycles,
  and when the export meter did not move at all), an export delta after a cycle
  gap booked instead of dropped, and a Wh-unit counter scaled by its own unit.
  Original set: baseline learning without
  deltas; corrected export subtracting an external load; negative corrected
  delta clamped at 0; jump filter (implausible + negative dropped, reading still
  advances, unavailable ⇒ relearn); prevented as energy deltas + runtime x power
  estimate; midnight rollover resetting the day counters while the monotone
  total survives; restart restore (same day, stale date, and a real
  flush + reload cycle); the `realized` block's exact shape and Ist+forecast
  composition; the block absent without a meter; the `soc_forecast` attribute
  carrying the block (and **omitting the key** without a meter — the card reads
  the attribute, not the data dict); the sensor set with state classes and
  values; sensors absent without a meter; sensor 7's F-FEEDIN gate and value;
  stale sensors removed when the gates close.
- `tests/ha/test_config_flow.py` (2 tests): the load subentry offers
  `energy_entity`; the options flow stores (and clears) `export_meter_entity`
  in the `surplus_accounting` section.
- **Time discipline in the HA tests** (hard-won, do not relax): the entry setup
  runs inside the pinned time window too — the setup refresh stamps the reading
  timestamps, and a later refresh pinned BEFORE real boot time measures
  negative elapsed hours and trips the jump filter on every delta. The SOC must
  vary slightly per refresh, otherwise the house-SOC stale watchdog fails the
  cycle and R11 books nothing. Deltas must stay plausible against the R4 cap
  (400 W load, 5-min window ⇒ 100 Wh).
- Goldens: **no** `gen_golden.py` run — `core/` is untouched (R15), the
  unchanged goldens are the neutrality verification.
