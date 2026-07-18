# Specification: Learned consumption forecast from measurement data (v0.5)

> Status: reflects the shipped consumption learner as of v0.7.10 (cleaning rules Rev. 4).
> Implementation notes: Store v2 discards v1 data (fresh backfill instead of
> migration — the source data can be re-fetched at any time);
> `negative_residuals` counts per run, not cumulatively; the watchdog's bias
> threshold is 15 % of the mean load over 14 days (hardcoded).
> The learning lives in `history_profile.py` (HA layer) with the pure math in
> `core/load_profile.py`; the concrete operator setup serves only as a
> **reference example in Appendix A**.
> Implements **P4** (REQUIREMENTS.md §4.4, "learn load profiles from HA
> history"), **N2** (dynamic SOC buffer from forecast quality) and the
> structural preparation for **P3** (hourly PV forecasts). Decision points
> D-C1 … D-C10.

## 1. Starting point & goal

Today the consumption forecast is purely static: per path (AC/DC) one
`LoadProfile` built from a base load plus an additional load in a time window
(2×4 scalars; no longer editable via the UI after setup — there is no
reconfigure flow for the base entry, and it is not in the options flow), plus
detected appliance runs with a configured energy, distributed linearly over
the remaining duration. No measured value flows in; the weaknesses are named
in REQUIREMENTS.md §2.2 (no HA history, no weekday/season profiles).

For every numeric sensor with a `state_class`, Home Assistant keeps
**long-term statistics** (hourly values, never deleted) — so on most
installations months to years of consumption data lie unused. The goal of
this specification:

1. **Stage 1:** Learn hourly base-load profiles (weekday/weekend/absence)
   nightly from the long-term statistics — cleaned of everything the
   integration itself switches.
2. **Stage 2:** Quantiles (P50/P80) instead of a point estimate; the
   uncertainty replaces the fixed config buffer as a **dynamic SOC buffer**
   (live immediately, operator decision 2026-07-04); daily target-vs-actual
   watchdog.
3. **Stage 3:** Learn appliance signatures (energy/duration/start times) from
   the history and factor **expected** appliance runs into the horizon.

The core principles are unchanged: exactly **one** simulation per update
(P1/P2), **no pessimism margin** on the load series (D-A3 — uncertainty acts
only through the buffer), fail-safe behaviour on data loss (D-A8) and the
HA-free, pure `core/` kernel (STRATEGY.md Q1). Without configured measurement
entities the integration behaves **exactly as it does today**. All data
sources are **freely configurable entities** — nothing is bound to a
manufacturer or a topology.

## 2. Consumption measurement points (generic)

Per path (AC, DC) the integration needs an hourly measurement series of the
consumer load. Two equivalent sources, selectable per path:

### 2.1 Direct load sensor

`ac_load_entity` / `dc_load_entity`: a sensor that measures the path's
consumer load directly — either as power (W, `state_class: measurement` →
hourly `mean`) or as an energy counter (kWh, `total_increasing` → hourly
`change`). Typical examples: the house-consumption sensor of an energy meter
(Shelly 3EM or similar), the "House Consumption" of a hybrid inverter, a DC
shunt.

**Measurement-point rule (the single most important prerequisite):** the
sensor should measure the consumer load that the planner models with
`ac_profile`/`dc_profile` — **not** the grid import and **not** a point that
includes the integration's own battery charging (otherwise the profile learns
the integration's own behaviour too, see D-C2). The config-flow help text
explains this.

### D-C1: Counter balance (when no direct sensor exists)

Many installations have no house-load sensor but do have energy counters at
the node boundaries. For that there are, per path, two **multi-entity lists**
(EntitySelector `multiple`):

- `ac_balance_in_entities` — counters for energy that flows **into** the
  consumption node (e.g. grid import, inverter output, PV feed-in into the
  house bus).
- `ac_balance_out_entities` — counters for energy that **leaves** the node
  again **without being consumed** (e.g. grid export, battery charging from
  the house bus).

```
Load(h) = Σ change(in_i, h) − Σ change(out_j, h),   clamped to ≥ 0
```

Each list entity may be a W sensor (`mean` × 1 h) or a kWh counter
(`change`); the kind is determined at runtime from the statistics metadata.
**Completeness rule:** an hour enters the balance only if **all** configured
balance entities supply a value for that hour — otherwise the hour is
discarded (counts into the coverage diagnostics), because a partial balance
looks plausible but is wrong. If a path has both a `*_load_entity` and balance
lists configured, the direct sensor takes precedence.

### 2.3 Loads outside the measurement point

Not every load controlled by the integration hangs behind the consumption
measurement point (example: a load in a different circuit that is supplied via
a feed-in setpoint — Appendix A). Each surplus load therefore carries the
subentry flag **`in_house_measurement`** (default **true**):

- **true** → the load is part of the measurement series and is subtracted
  during learning (D-C2);
- **false** → the load is not contained in the measured value and must **not**
  be subtracted (double subtraction).

### 2.4 DC path

Identical mechanism (`dc_load_entity` or `dc_balance_*_entities`). Without a
configured DC source the DC profile stays static — learning is active
independently per path. Hours in which a configured **support path** was
active (D-C2 step 3) are excluded, because grid feed-ins on the consumer side
distort the measurement picture.

## 3. Architecture overview

```
                     HA layer                                  core/ (pure)
┌─────────────────────────────────────────────┐   ┌──────────────────────────┐
│ history_profile.py (NEW)                    │   │ load_profile.py (NEW)    │
│  Nightly job 03:00: LTS queries (recorder-  │──▶│  cleaning arithmetic,    │
│  executor) → cleaning → aggregation         │   │  median/quantiles, bins  │
│  → Store (per config entry)                 │   │  (pure functions)        │
├─────────────────────────────────────────────┤   ├──────────────────────────┤
│ coordinator.py                              │   │ series.build_slots       │
│  reads the profile from the Store, checks   │──▶│  NEW: optional series    │
│  freshness, builds the ac_load_w/dc_load_w  │   │  ac_load_w/dc_load_w     │
│  series over the horizon (slot_starts       │   │  (slot-wise fallback to  │
│  helper), computes the dyn. buffer (Stage 2)│   │  the static profile)     │
└─────────────────────────────────────────────┘   └──────────────────────────┘
```

- **Learning lives in the HA layer** (recorder access); the computation
  kernels are pure functions (no HA import, fully unit-testable).
- The kernel consumes **finished series** — the same pattern intended for P3
  (hourly PV forecasts), and grid-neutral for the later 15-min extension
  (D-A7).
- The nightly job runs asynchronously alongside the 5-min planning cycle; the
  coordinator reads **only the Store** (no DB query in the planning path).
  Learning errors can never block the planner.

## 4. Stage 1 — Learned base-load hourly profiles

### 4.1 New configuration

All fields **optional**. The measurement entities appear in the base flow
(step "Base loads") **and** in the options flow; the tuning numbers only in
the options flow. The eight static profile values remain, are labelled
"fallback profile" in the UI and **also move into the options flow** (fixes:
not editable via the UI after setup today).

| Field | Type / default | Meaning |
|---|---|---|
| `ac_load_entity` | EntitySelector sensor, empty | Direct AC load sensor (§2.1); precedence over the balance |
| `ac_balance_in_entities` | EntitySelector sensor `multiple`, empty | Inflows to the AC consumption node (D-C1) |
| `ac_balance_out_entities` | EntitySelector sensor `multiple`, empty | Non-consumption outflows (D-C1) |
| `dc_load_entity` / `dc_balance_in_entities` / `dc_balance_out_entities` | analogous | DC path (§2.4) |
| `learning_window_days` | Number 14–120, **42** (options flow) | Rolling learning window |
| `learning_max_age_days` | Number 3–60, **14** (options flow) | Max. profile age, static fallback afterwards |
| per surplus-load subentry: `in_house_measurement` | bool, **true** | Load is in the measured value → subtract during learning (§2.3) |

**Activation logic:** learning is active per path as soon as `*_load_entity`
OR at least one `*_balance_in_entities` entity is set. No separate toggle
(implicit opt-in, no inconsistent states). A balance without any inflow
counter → validation error in the flow.

**Deliberately hardcoded** (documented constants, consistent with
the former `_POWER_EMA_ALPHA`, retired in v0.14.0 by F-ROBUST-POWER): learning time 03:00 local, `min_samples` = 10 per bin
(absence: 5), plausibility clamps (AC 3 000 W, DC 1 000 W per hourly mean),
change rate limit ±20 %/night, median as the aggregate (Stage 1), bin scheme.

### 4.2 Data acquisition (nightly job)

- Trigger: `async_track_time_change` (fires in local time) at 03:00;
  additionally once at setup when the Store is empty or the profile is > 24 h
  old.
- Query per entity:

  ```python
  await get_instance(hass).async_add_executor_job(
      lambda: statistics_during_period(
          hass, start, end, ids, "hour",
          units={"energy": "kWh", "power": "W"},   # mandatory parameter; also pins the units
          types={"mean", "change"},
      )
  )
  ```

  **All** recorder accesses (also `list_statistic_ids`,
  `state_changes_during_period`) go through the recorder executor
  (`get_instance(hass).async_add_executor_job`) — never
  `hass.async_add_executor_job`, never in the event loop.
- Check availability up front via `list_statistic_ids` (also returns
  `statistics_unit_of_measurement`; recorder excludes or a missing
  `state_class` → repair issue, path stays static). Units are enforced via the
  `units` parameter, not assumed.
- Missing hours (unavailable gaps) are skipped, never learned as 0; for the
  counter balance the completeness rule from D-C1 additionally applies (all
  balance entities, or discard the hour).
- First run: backfill over `learning_window_days` (42 days × 24 h ≈ 1 000
  rows/entity — uncritical). Afterwards incrementally only the previous day.
  If the window is **enlarged** (options change or Stage-2 migration to 120 d),
  the next nightly run triggers a **delta backfill** of the missing days; the
  `daily_hours` retention follows the configured maximum.
- **Cache invalidation:** the daily intermediate results were computed with
  the cleaning configuration of their fetch time. If a cleaning input changes
  (`in_house_measurement`, power/switch entities, nominal powers, appliances,
  support-path switches), a **cleaning fingerprint** in the Store becomes
  invalid → complete refetch of the window on the next (immediately triggered)
  run, instead of carrying contaminated days along for weeks.

### D-C2: Cleaning (mandatory component, not optional)

Only the **uninfluenced base load** is learned. Without cleaning the profile
learns the integration's own switching decisions (feedback: planned midday
loads → learned midday "base load" rises → less detected surplus →
oscillation over weeks) and double-counts appliance runs (ApplianceRuns stay
additive, `series._apply_appliance_runs`).

Per historical hour, in order:

1. **Surplus loads with `in_house_measurement = true`:**
   - with `power_entity`: subtract the hourly `mean` of the LTS (most accurate
     source). **Statistics gaps count as 0 W** (operator decision 2026-07-04):
     devices like power stations report `unavailable` precisely when they are
     off — a missing hour means "no consumption", not "unknown"; discarding it
     would starve the weekend bins for weeks. The same applies to appliance
     power sensors.
   - without `power_entity`: subtract `nominal_power_w × on-fraction(h)` from
     the `control_switch_entity` history
     (`state_changes_during_period(…, no_attributes=True)`, one call per
     entity, weekly chunks, recorder executor).
   - loads with `in_house_measurement = false`: **no subtraction** (§2.3).
2. **Appliances:**
   - `detection_entity` is a power sensor with LTS → subtract the hourly
     `mean`;
   - status-only (no LTS for non-numeric entities): hours with a detected run
     are **excluded** from the bin sample (exclusion instead of subtraction —
     runs are sporadic, the median tolerates the data loss; for daily surplus
     loads, by contrast, exclusion would empty the midday bins, hence
     subtraction there). Run detection in the history: same rules as live
     (`APPLIANCE_RUNNING_STATES` or `power_threshold_w`).
3. **Support paths — correction instead of exclusion** (Rev. 3, operator note:
   in winter the PSUs may run for **months** — an exclusion would starve
   learning entirely). Active support paths **shift** power between the paths;
   the cleaning shifts it back arithmetically so that the profiles reflect the
   uninfluenced demand (exactly the semantics the simulation kernel expects —
   it models the support paths itself):
   - **48 V PSU ON:** draws `support_dc48_power_w` (config, e.g. 60 W) from the
     house grid and feeds it into the 48 V bus on the consumer side →
     **AC − P·on-fraction, DC + P·on-fraction** (converter losses neglected,
     as in the kernel).
   - **24 V PSU feeds the rail** (PSU ON **and** DC/DC OFF): the entire 24 V
     load moves from the DC to the AC measurement. Exact shift-back via the
     PSU's optional **power sensor** (`support_dc24_power_entity`):
     **AC − P24(h), DC + P24(h)** (statistics gaps = 0 W, the PSU is then off).
     **Without this sensor the shift is unknown → only then is the hour (both
     paths) excluded.** For winter operation the sensor is therefore strongly
     recommended (repair issue if it is configured but has no statistics).
   - **Dead rail** (DC/DC OFF without PSU): anomalous state → hour excluded
     (rare).
   The status-only appliance exclusion from step 2 applies only to the **AC**
   path. If no support paths are configured, this step is skipped.
   **Coverage rule:** hours before the first recorded state of a required
   switch/status entity are UNKNOWN, not "off": subtraction/correction sources
   supply no value there (the hour is discarded), and exclusion checks
   conservatively exclude the hour. Days outside the recorder retention thus
   stay unlearned rather than being learned uncleaned.
4. **Clamping & diagnostics:** limit the residual to [0, clamp]; **count
   negative residuals** (indicator "measurement point does not contain the
   controlled loads / double subtraction / wrong `in_house_measurement` flag" →
   diagnostic attribute, repair issue on accumulation).

### D-C3: Aggregation & bin scheme

- Bins per path (AC, DC): **{weekday, weekend, absence} × 24 local hours**.
  Holidays → weekend bin, as soon as `workday_entity` is configured (Stage 2,
  §5.3).
- Stage-1 aggregate: **median** of the cleaned hourly values over the rolling
  window (robust against outlier days; season follows implicitly with a
  ~3–6 week lag — deliberately **no** explicit season model, §8).
- Validity per bin: ≥ `min_samples`, otherwise a bin-specific fallback (D-C6).
- Damping: change per bin and night limited to ±20 % (safety net against
  residual feedback and data errors), with an absolute minimum step of 10 W —
  otherwise a bin at 0 W would be a fixed point of the multiplicative clamp and
  could never grow again.
- **DST/UTC (learning side):** LTS rows are UTC-aligned; bin assignment uses
  the **local** hour of the row start. 23/25-h days are treated as normal
  samples; dedicated tests (§4.9).

### D-C4: Vacation mode (manual, operator wish 2026-07-04)

No auto-detection of absence (rejected, §8) — instead a **manual switch**:

- New entity of the integration: `switch.<device>_urlaubsmodus`
  (state persists across restarts, §4.6; icon `mdi:beach`).
- **Effect on the forecast:** while ON, the series builder uses the
  **absence bin set** for the entire horizon. For hours whose absence bin
  still has < `min_samples`, the coordinator writes **`base_w` directly as the
  series value** (nobody home ≈ pure base load) — explicitly **not** `None`,
  because the None fallback would deliver `power_w(hour)` including
  `variable_w` in the kernel. The slot-wise None fallback (D-C5) applies only
  outside vacation mode.
- **Effect on learning:** days on which the mode was active ≥ 12 h go into the
  absence bins instead of weekday/weekend. Tagging: the nightly job records the
  previous day in the Store (`day_log`); for the backfill additionally the
  switch history from the recorder (the integration's own entity, which is
  recorded). Old days before its introduction = normal.
- **Expected appliance runs (Stage 3) are 0 in vacation mode.**
- Rearrangements during the absence (example Appendix A: the dehumidifier
  moves into the apartment) remain **manual reconfiguration** — explicitly no
  automation.

### 4.6 Persistence (Store)

Store **per config entry** (analogous to the existing SOC cache):
`Store(hass, 1, f"battery_manager.learned_profiles.{entry.entry_id}")`;
deletion is added to `async_remove_entry` (no orphaned state, no key sharing
between two entries).

```jsonc
{
  "version": 1,                       // Stage 2 → 2 (migration: profiles values become {p50, p80})
  "computed_at": "2026-07-04T03:00:00+02:00",
  "source_entities": {"ac": ["…"], "dc": ["…"]},   // binding: a change ⇒ discard the profile
  "vacation_mode_active": false,      // current switch state (restore after restart)
  "day_log": {"2026-07-03": {"daytype": "weekday", "vacation": false}},
  "daily_hours": {"2026-07-03": {"ac": [ /* 24 × Wh|null */ ], "dc": [ … ]}},
  "profiles": {                       // Stage 1: W value per bin; Stage 2: {"p50": […], "p80": […]}
    "ac": {"weekday": [/*24*/], "weekend": [], "absence": []},
    "dc": { … }
  },
  "samples": { /* sample count per bin */ },
  "appliance_signatures": {           // Stage 3 (§6)
    "<appliance_id>": {
      "runs_observed": 12,
      "median_energy_wh": 950, "median_duration_h": 3.4,
      "rate_per_daytype": {"weekday": 0.4, "weekend": 0.7},   // fallback model
      "gap_histogram": [/* gaps 0–14 days */],                // interval model D-C10
      "last_run_date": "2026-07-02",
      "start_histogram": [/* 24 smoothed weights */],
      "active_run_started_at": null   // persisted start time (bugfix §6.2)
    }
  },
  "diagnostics": {"negative_residuals": 0, "coverage": 0.94}
}
```

`daily_hours` holds only the (maximum configured) learning window →
incremental learning without a full rescan; re-aggregation after a parameter
change is possible, with a delta backfill on window enlargement (§4.2).

### D-C5: Kernel docking (series contract)

- `series.build_slots` gains two new **optional** parameters
  `ac_load_w: tuple[float | None, ...] | None` and `dc_load_w: …`.
- **Contract:** the series is addressed by `slot.index`. The unit is **watts**
  (hourly mean); `build_slots` multiplies by `slot.duration` (correct also for
  the partial first hour). Values beyond the series length count as `None`.
  `None` or a missing series → `config.*_profile.power_w(hour)` as before
  (**slot-wise fallback**; exception vacation mode, D-C4).
- **No duplication of the slot logic:** the slot-start enumeration (partial
  first hour, hourly grid, horizon end) is extracted as a pure helper function
  `series.slot_starts(now, num_days) -> tuple[datetime, ...]`; `build_slots`
  and the coordinator's series builder use the **same** function (no divergence
  risk in slot count/indexing). A test "series shorter than horizon" is
  mandatory (§4.9).
- **Day-type/bin lookup in the coordinator:** via the **tz-aware local** slot
  start time (`dt_util.as_local`), not via the naive `slot.hour_of_day` — at
  DST transitions within the horizon the doubled hour uses the same bin twice,
  the dropped one drops out. Residual fuzziness: `build_slots` itself keeps
  computing on naive local time (`timedelta(hours=1)`); in the ≤ 2 changeover
  nights/year `hour_of_day` may differ by ±1 h after the change — accepted and
  documented, since the bin lookup is independent of it.
- `_apply_appliance_runs` stays unchanged additive — no double-counting,
  because the learned base load is appliance-cleaned (D-C2).
- `simulate`/`optimize` remain untouched (they consume only `ac_wh`/`dc_wh`);
  P1/P2 preserved: the series is an input to the ONE simulation.

### D-C6: Fallback cascade & diagnostics (D-A8-analogous)

| Situation | Behaviour |
|---|---|
| Bin invalid (< `min_samples`), normal operation | static profile value **only for this hour** (`None` in the series) |
| Bin invalid, **vacation mode** | `base_w` as the series value (without `variable_w`, D-C4) |
| Profile older than `learning_max_age_days` / Store empty / source entity changed | fully static profile, warning |
| Nightly run throws an exception | log warning; the old profile stays valid until the age limit |
| Measurement entity without LTS / recorder exclude | repair issue with plain text, path static |

Diagnostics as attributes on the existing status sensor: profile source per
path ("learned (2×24+A, 42 d, 94 % coverage)" / "static"), age of the last
run, sample coverage, mean deviation learned vs. static, negative-residual
counter. (`_unrecorded_attributes` for the profile matrix.)

### 4.9 Tests & acceptance

- Pure functions (`core/load_profile.py`): cleaning arithmetic, median/bins,
  rate limit, clamps, balance completeness rule — synthetic series incl. DST
  days (23/25 h), gaps, negative residuals.
- Kernel: `build_slots` with/without series, slot-wise fallback, **series
  shorter than horizon**, partial first hour, `slot_starts` equivalence.
- HA layer: Store versioning/entity binding, fallback cascade, nightly-job
  error paths (mock recorder), **series build across a DST boundary**,
  vacation-mode change during the day.
- **Acceptance:** profiles are planning-effective from deploy; 2 weeks of
  **accompanying observation** via the diagnostic comparison learned vs.
  static; negative residuals ≈ 0; the charge frequency of the surplus loads has
  not systematically fallen (D-A3 control).

## 5. Stage 2 — Quantiles, dynamic SOC buffer, watchdog

### D-C7: Weighted quantiles with recency weighting

- Same bins and cleaning as Stage 1; the median is replaced by **weighted
  empirical quantiles**: weight `w = 0.5^(age_days / 30)` (half-life 30 d,
  options field `profile_half_life_days` 7–120). P50 = weighted median
  (replaces the Stage-1 aggregate seamlessly), plus **P80**; `P80 ≥ P50` is
  enforced.
- The recency weighting is simultaneously the drift/season model (follows the
  season with ~1 month latency). Deliberately **no P90** (too unstable at
  n_eff ≈ 20–80).
- The hard window (`learning_window_days`) is extended to 120 d (delta
  backfill, §4.2); Store version 2 (§4.6).

### D-C8: Dynamic SOC buffer (implements N2 — **live immediately**)

Before each `plan()` call the coordinator computes (per path, because of the
different discharge chains):

```
critical window K = now … first slot with forecast PV surplus
                    (pv_wh > ac_wh + dc_wh; no such slot → the whole horizon)

uncertainty_wh = Σ_K [ (P80_ac − P50_ac) / (η_discharge × η_inverter)     # AC via inverter
                     + (P80_dc − P50_dc) / η_discharge ]                   # DC direct

buffer_% = clamp(uncertainty_wh / capacity_wh × 100,
                 buffer_min_percent, buffer_max_percent)
```

- **Effective sites in the kernel — deliberate separation:** `soc_buffer_percent`
  acts in three places: (1) lower bound of the threshold search, (2) buffer
  floor of the load allocation, (3) appliance advisor. The dynamic buffer
  replaces these (intended: more night reserve, more conservative switch-on
  gate). The **grid-support escalation** (D-A9) is deliberately *not* driven by
  the buffer at all: since v0.7.13 it uses its own four **absolute** SOC
  thresholds (`support_dc24_activate_soc` … `support_dc48_recovery_soc`), so a
  dynamically widened planning buffer never makes the grid support paths switch
  earlier/more often at night. (Before v0.7.13 this decoupling was achieved via
  a fixed `support_buffer_percent` kernel parameter, now removed.)
- **Partially learned profile:** the dynamic buffer is active as soon as **at
  least one path** has valid quantiles; statically filled slots and unlearned
  paths contribute **0** to the sum (coverage share in the diagnostic
  attribute). A full fallback to the fixed `soc_buffer_percent` only when
  **no** path supplies quantiles.
- Planning uses the unbiased **P50 series** (D-A3: no pessimism in the load
  series; conservative only via the buffer).
- **Operator decision 2026-07-04: live immediately** — no parallel operation.
  Safeguards: clamps (`buffer_min_percent` default 3, `buffer_max_percent`
  default 15, options flow), diagnostic attribute (current buffer, window
  length, coverage) and the fixed buffer as an automatic fallback.
- Intuition: at night (until the morning surplus) the band is wide → a large
  buffer; at midday it is narrow → a small buffer.

### 5.3 Holidays

Optional field `workday_entity` (binary_sensor of the core **Workday**
integration; on = workday). Effect: holiday = weekend bin. **Following days in
the horizon:** the sensor shows only *one* day (today or a configured offset) —
for slots after midnight the coordinator queries the `workday.check_date`
action (response data, any date against the sensor configuration) once daily
for the next 3 days and caches the result; if the call fails, the plain
calendar rule (Sat/Sun) applies for the following days. Learning tagging from
configuration onward; historical holidays before setup are not tagged
retroactively (accepted fuzziness, ~1 day/month).

### D-C9: Validation watchdog & export decomposition

- Daily after the nightly run: comparison of the P50 forecast vs. the cleaned
  actual load of the previous day → **MAE** and **bias** per path, exposed as a
  diagnostic sensor (`_unrecorded_attributes` for the detail series). If the
  bias runs one-sided over a threshold for 14 days (hardcoded 15 %), a **repair
  issue** is created (`issue_registry.async_create_issue`) instead of silently
  learning on (surfaces measurement-point/cleaning errors).
- **Export decomposition (diagnostic, optional):** where an export counter
  exists, `Export − Σ (known non-consumption exports)` ≈ the true lost feed-in
  → a comparison value for the planner's `lost_surplus` metric (example
  Appendix A). Diagnostic only, no control path.

## 6. Stage 3 — Appliance signatures & expected runs

### 6.1 Run segmentation from the history

- Sources per `detection_entity`: power sensor → 5-min statistics
  (`statistics_short_term`; retention follows `purge_keep_days` — check at
  runtime, do not assume) or raw states; status entity (**no LTS** for
  non-numeric entities) → `history.state_changes_during_period` in daily
  chunks, one call per entity, recorder executor.
- Run = a contiguous "running" phase (same detection rules as live); off gaps
  < 15 min are bridged. Per run: duration, start time, energy (integrable only
  with a power sensor) and the **gap to the previous run in days** (the basis
  of the interval model, D-C10).

### 6.2 Learned signatures replace config values

- From ≥ 5 observed runs onward: `median_energy_wh` / `median_duration_h`
  replace `run_energy_wh` / `run_duration_h` at the **live scheduling** (config
  values remain the fallback and stay UI-visible; the diagnostics show learned
  vs. configured). Status-only devices: only the duration is learnable, the
  energy stays config. Storage: `appliance_signatures` in the Store (§4.6).
- **Bugfix along the way:** the start time of detected runs is persisted in the
  Store (`active_run_started_at`; today only in-memory → an HA restart resets
  the run to "just started").

### D-C10: Expected runs in the horizon — interval model (disableable per appliance)

Observation (operator note 2026-07-04): a washing machine, dishwasher and the
like are started by the user at **approximately fixed intervals**. A flat daily
rate wastes this information — the probability of a run depends strongly on
**how long ago the last run was**.

- **Per device, the gap distribution is learned** (days between successive run
  starts, §6.1), and from it the empirical **hazard function**:

  ```
  h(g) = P(run today | last run g days ago)
       = (# gaps of length g) / (# gaps of length ≥ g)      for g = 0 … 14
  ```

  Example dishwasher "every 2 days": h(0) ≈ 0, h(1) small, h(2) high, h(3) ≈ 1
  — ran yesterday → unlikely today, almost certain the day after tomorrow.
- **Day-type modulation:** the hazard probability is scaled by the learned
  day-type factor (relative run frequency weekday/weekend, normalized) —
  captures "wash day is Saturday" without needing a second model.
- **Forecast per horizon day d** (recursive, with g = days since the last
  observed run): `p_d = h(g_d) × daytype_factor(d)`; for following days `g` is
  carried forward with a `(1 − p)`-weighted expectation. Expected energy
  `E[h] = p_d × median_energy × start_histogram(h)`, convolved over the run
  duration; flows in as an additional AC series (analogous to D-C5, additive
  before the live runs).
- **Fallback cascade:** < 8 observed gaps → a flat daily rate per day type
  (simple model); < 5 runs in total → no expectation (behaviour as today). The
  hazard horizon is hard-capped at 14 days (`h(>14) = h(14)`).
- **Replacement principle:** as soon as a run of the device has been detected
  today or is complete, the device's remaining expectation **for today** is set
  to 0 and `g` is reset (no double-counting with `_apply_appliance_runs`).
  Vacation mode → expectation 0 (D-C4); after the vacation ends `g` continues
  from the last real run, capped by the hazard cap.
- New subentry field `expected_runs` (bool, default **on**). Risk: expectation
  values act like a slight pessimism on the surplus allocation (D-A3
  counter-argument) → after rollout, observe via the charge frequency of the
  surplus loads and the bias watchdog (D-C9); on a negative finding, §6.2
  remains on its own.
- Intra-run power curves (heat-up peak): re-evaluate only with the 15-min grid
  (D-A7/Phase 4).

## 7. Configuration overview (all stages)

| Field | Location | Stage | Default |
|---|---|---|---|
| `ac_load_entity` / `ac_balance_in_entities` / `ac_balance_out_entities` | base + options flow | 1 | empty (= learning off) |
| `dc_load_entity` / `dc_balance_*_entities` | base + options flow | 1 | empty |
| `in_house_measurement` | surplus-load subentry | 1 | true |
| `learning_window_days` | options flow | 1 | 42 (Stage 2: 120) |
| `learning_max_age_days` | options flow | 1 | 14 |
| Vacation mode | own switch entity | 1 | off |
| static profile fields (8×) | base + **new:** options flow | 1 | as before |
| `profile_half_life_days` | options flow | 2 | 30 |
| `buffer_min_percent` / `buffer_max_percent` | options flow | 2 | 3 / 15 |
| `workday_entity` | base + options flow | 2 | empty |
| `expected_runs` | appliance subentry | 3 | on |

## 8. Rejected alternatives

| Idea | Reason |
|---|---|
| Fixed-named balance slots (import/export/inverter/…) | Bound to a topology (ESS); the generic inflow/outflow lists (D-C1) cover any wiring and are simpler to explain. |
| Topology-specific support-path back-calculation (e.g. adding PSU power) | Requires knowledge of the feed-in side; the generic hour exclusion (D-C2 step 3) is practically lossless for rare escalation hours. |
| Explicit season model (monthly buckets) | Recency weighting / rolling window covers the season with ~1 month latency; monthly buckets fragment the sample (Saturday in February = 4 samples). |
| 7×24 weekday matrix | ~6–8 samples/bin → unstable medians or a sluggish huge window; fixed weekday patterns are covered by the appliance detection. The bin scheme stays extensibly encapsulated. |
| Temperature/weather regression | Typical battery systems of this class carry no large thermal loads; high effort, overfitting risk. An extension option. |
| Automatic vacation/absence detection | Fragile heuristic; the operator's wish is the manual mode (D-C4). |
| P90 quantile | Too unstable at n_eff ≈ 20–80; P80 + clamps is enough. |
| Pessimism factor on the load series | Explicitly rejected by D-A3 (distorts the surplus allocation) — uncertainty acts only via the buffer (D-C8). |
| Separate `learning_enabled` toggle | Implicit opt-in via the measurement entities; less configuration surface. |
| 15-min profile grid | No effect in the hourly planner; `statistics_short_term` retention is configuration-dependent. The interface is grid-neutral (D-C5). |
| Raw states as the learning source for profiles | 365 days of states = millions of rows, minute-long queries, recorder-executor blockade; the hourly LTS is enough and constantly cheap. Raw states only for the Stage-3 segmentation of status entities. |

## 9. Risks & observation plan

1. **Cleaning residual error** (loads without `power_entity`, switching edges
   on hour boundaries): bounded by rate limit + median; visible via negative
   residuals and the bias watchdog. Recommendation: give every switched load a
   power sensor.
2. **Buffer live immediately** (operator decision): a miscalibration would take
   effect directly → tight clamps (3–15 %), diagnostic attribute, fallback to
   the fixed buffer without valid quantiles, escalation trigger decoupled from
   the dynamic buffer (D-C8). Observe the first 2 weeks: the buffer trajectory
   **and the switch frequency of the support paths**.
3. **Wrong `in_house_measurement` flag / wrong measurement point**: the most
   severe misconfiguration source → negative-residual diagnostics, help texts
   with examples (Appendix A/B), repair issue on accumulation.
4. **Structural breaks** (new device, home-office change): 2–4 weeks of bias
   until the weighting catches up; the watchdog reports, it does not heal
   faster.
5. **What this spec does not solve:** PV intraday stays the two-window model
   (P3, same docking point — the next big lever); battery
   efficiencies/capacity stay uncalibrated.

## 10. Implementation order & effort

| Step | Content | Effort |
|---|---|---|
| 1a | `core/load_profile.py` (pure functions) + `slot_starts` helper + `build_slots` series + tests | ~2 PD |
| 1b | `history_profile.py` (queries, cleaning, Store per entry, nightly job) + coordinator hookup + vacation-mode switch + diagnostics | ~3 PD |
| 1c | Config/options flow (incl. static profile fields in the options flow), subentry flag, de/en, docs | ~1–2 PD |
| — | **Deploy; profiles planning-effective + 2 weeks accompanying observation (§4.9)** | — |
| 2 | Quantiles (Store v2), dyn. buffer (live, `support_buffer_percent`), watchdog, `workday_entity` | ~3–4 PD |
| 3a | Learn signatures + start-time persistence fix | ~3 PD |
| 3b | Expected runs (interval model D-C10, replacement principle, flag) | ~2–3 PD |

Each stage is independently deployable; Stage 1 is fully inert without
configured measurement entities (breaking-change-free).

---

## Appendix A: Reference example — the operator's Victron ESS setup

Verified 2026-07-04 on the live instance (all counters kWh `total_increasing`
with hourly LTS ≥ 17 months; raw states ~350 days, `purge_keep_days ≈ 365`).
Victron MultiPlus wired "backwards": **AC-IN1 = 230 V apartment grid**,
**AC-Out1 = PV micro-inverter**.

**AC counter balance (D-C1):**

| List | Entity | Flow |
|---|---|---|
| `ac_balance_in_entities` | `sensor.victron_grid_energy_forward_total_30` | Grid → apartment |
| | `sensor.victron_vebus_invertertoacin1_228` | Battery → apartment |
| | `sensor.victron_vebus_acouttoacin1_228` | PV passthrough → apartment |
| `ac_balance_out_entities` | `sensor.victron_grid_energy_reverse_total_30` | Apartment → grid (export) |
| | `sensor.victron_vebus_acin1toinverter_228` | Apartment → charger (battery charging) |

**DC path:** `dc_load_entity = sensor.victron_system_system_power`
(W, `measurement`; measures battery → 48 V consumers incl. DC/DC and the 24 V
rail). Alternative/plausibility check:
`victron_dcsystem_history_energyin_229` − `…out_229`.

**Zero-feed-in pattern (example for `in_house_measurement = false`):**
The dehumidifier stands in the shared basement **upstream** of the grid meter.
An operator automation sets its measured power
(`sensor.fritz_powerline_546e_power`) as the ESS feed-in setpoint — the
apartment exports exactly the basement consumption (zero feed-in at the real
house meter). The dehumidifier is thus contained in the `reverse` counter and
is **automatically excluded** by the balance → subentry flag
`in_house_measurement = false` (no double subtraction!). The Fossibot outlets,
by contrast, hang in the apartment grid → flag `true`, cleaning via their
`total_input` power sensors (LTS present).

**Export decomposition (D-C9):** lost feed-in ≈
`reverse − fritz_powerline energy` (the fritz LTS is patchy — 9 of 14 months —
so diagnostic only).

**Recommended settings:** balance as above; the dehumidifier subentry
additionally `power_entity = sensor.fritz_powerline_546e_power` (better
planning power); `workday_entity` after installing the Workday integration.
Ignored small flows: `invertertoacout`, `acin1toacout` (each ~4 kWh total
reading, losses/edge flows). Note: orphaned old statistics with the suffix
`_40` stem from an earlier device installation and are ignored.

## Appendix B: Further typical setups

**B1 — Direct house-consumption sensor (the most common case):**
An energy counter measures the house load directly (e.g. a Shelly 3EM on the
consumer output, the "House Consumption" of a hybrid inverter) → set
`ac_load_entity`, done. If the measurement point contains loads switched by the
integration, leave their subentries at `in_house_measurement = true` (default)
— they are factored out via D-C2.

**B2 — Grid counter + PV generation only (AC-coupled, without a battery counter
in the house bus):**
`ac_balance_in_entities = [grid import, PV generation]`,
`ac_balance_out_entities = [grid export]`. If the battery is charged from the
house bus, add the charger counter to the outflows; if it discharges into the
house bus, add the inverter counter to the inflows.

**B3 — DC-coupled installation:** a hybrid inverter usually delivers
"Load"/"Consumption" directly → B1. For a separate DC consumer branch:
`dc_load_entity` (shunt) or DC balance lists.
