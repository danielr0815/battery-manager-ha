# Strategy: Configurable consumption profiles & seasonal awareness

> Status: **strategy/design only — nothing implemented** (operator request
> 2026-08-08). Extends docs/CONSUMPTION_FORECAST.md (stages 1–2 live since
> v0.17.0). Evaluates and partially harvests an external LLM's EMHASS
> proposal (§3). Decision points D-P1 … D-P6.

## 1. Goal

The base consumption forecast should distinguish **user-defined, named
day profiles** — e.g. *Normal*, *Homeoffice*, *Wochenende*, *Urlaub* —
instead of only the three hard-wired day types, and each profile should be
allowed to **behave differently across the year** (season/month) without
fragmenting the sample base.

Non-goals: replacing the planner, replacing the learning architecture,
 appliance-run forecasting (that is the separately specified Stage 3,
CONSUMPTION_FORECAST.md §6).

## 2. What exists today (inventory)

- Nightly learning from long-term statistics into bins
  **{weekday, weekend, absence} × 24 h** per path (AC/DC), cleaned of
  everything the integration itself switches (D-C2: power-sensor
  subtraction, `in_house_measurement` flags, support-path back-correction).
- Weighted empirical quantiles P50/P80 per bin, recency weight
  `0.5^(age/30 d)` (`profile_half_life_days`), window 120 d (D-C7);
  dynamic SOC buffer from the P80−P50 band (D-C8); MAE/bias watchdog (D-C9).
- Day tagging in the Store's `day_log` (`history_profile.py`: date →
  `{daytype, vacation}`); future-day classification for the horizon via
  `workday.check_date` (`future_daytypes`, cached daily); manual vacation
  switch → absence bins for the whole horizon (D-C4).
- The planner docks via the D-C5 series contract (`ac_load_w`/`dc_load_w`
  with slot-wise `None` fallback to the static profile) — **unchanged by
  this strategy**.
- Rolling replan every 300 s (`UPDATE_INTERVAL_SECONDS`) — the system
  already *is* a model-predictive loop.

## 3. The external EMHASS proposal — evaluated honestly

The suggestion (EMHASS + `MLForecaster` + naive-MPC endpoint) was checked
point by point against this codebase:

| Proposal | Verdict |
|---|---|
| Subtract automated loads before training ("double counting") | **Already done** — that is exactly D-C2 cleaning, incl. power-sensor-accurate subtraction and the `in_house_measurement` flag. |
| Rolling MPC every 5–15 min | **Already the architecture** — the coordinator replans every 300 s with fresh SOC/forecasts. |
| Zero feed-in price = 0 to force self-consumption | **Already native** — export penalty, gate/threshold logic and the zero-feed-in pattern (Appendix A) encode this directly. |
| `MLForecaster` (sklearn, lag features, Bayesian hyperparameter tuning) | **Rejected.** The learning base here is ~120 days × 24 hourly samples per day class — a gradient-boosting model is not better than weighted empirical quantiles at that sample size, is opaque (this project debugs via `why`-reasons and golden snapshots), and pulls a heavy external service with a parallel configuration universe in. |
| Weather regression (temperature/humidity) for base load | **Rejected (again).** Already weighed in CONSUMPTION_FORECAST.md §8: no large thermal loads behind the measurement point. The dehumidifier argument does not apply either — it is a *controlled* load, subtracted from base-load learning; a humidity-driven dehumidifier *need* model would be a separate, smaller feature. |
| Bayesian occupancy sensor as input | **Harvested, reframed:** not as auto-detection (rejected in §8 there — fragile), but as one possible *user-wired* profile selector (D-P2 accepts any binary sensor; the operator's own automation/bayesian sensor → `input_boolean` keeps full control). |
| Replace the integration with EMHASS wholesale | **Rejected.** It would swap the pure, golden-tested core, the Victron-specific gate/support logic and the whole subentry model for an opaque external solver — architectural rupture with zero proven gain on this plant. |

**Conclusion:** evolve the existing learner; adopt the two genuinely useful
ideas (external day-class signals, explicit season handling).

## 4. Design

### D-P1: Named profiles as day classes (config subentries)

A **consumption profile** = a named day class the operator defines. The
three current day types become the built-in defaults:

- `normal` (was `weekday`), `wochenende` (was `weekend`), `urlaub` (was
  `absence`, still driven by the vacation switch, D-C4).

Custom profiles (e.g. *Homeoffice*) are a new config-subentry type
`consumption_profile` — same UI pattern as loads/appliances — with fields:

| Field | Default | Meaning |
|---|---|---|
| `name` | – | Display name (subentry title) |
| `selector_entity` | empty | **Calendar** or **binary sensor** that marks days of this class (D-P2) |
| `calendar_event_match` | = name | For calendar selectors: substring match on all-day event titles |
| `fallback_profile` | `normal` | Bin set used while this profile has too few samples (D-P3) |

Bins stay `{profile × 24 h}` per path — the scheme is deliberately not
widened to weekday×profile matrices (sample fragmentation, §8 there).

### D-P2: Day classification (deterministic resolver)

One pure function `classify_day(date, context) -> profile_id`, used
**identically** by the nightly tagging and the horizon lookup (no
divergence, tested against each other). Resolution order:

1. Vacation switch on → `urlaub` (whole horizon, as today).
2. Custom profiles in user-defined priority order — first match wins:
   - *calendar selector*: an all-day event matching `calendar_event_match`
     exists on that date — works for **past days (tagging), today and
     future horizon days** (calendars carry future events);
   - *binary-sensor selector*: sensor is on → matches **today only**; for
     future days the profile cannot be known → falls through to the next
     rule (honest limitation: a "Homeoffice tomorrow" is only plannable
     when it comes from a calendar; the coordinator's `future_daytypes`
     cache already demonstrates the pattern via `workday.check_date`).
3. Holiday/weekend rule (workday entity as today) → `wochenende`.
4. Else → `normal`.

**Learning side:** the nightly job tags yesterday by replaying the same
resolver against history (binary sensor: on ≥ 12 h of the day, reusing the
vacation-tagging rule; calendar: event present). `day_log` stores the
profile **subentry id**, not the name — renames stay stable; a deleted
profile's days are remapped to its `fallback_profile` at aggregation.

### D-P3: Fallback cascade (anti-fragmentation)

More profiles = fewer samples per bin. Per (profile, hour):

```
Σ weights ≥ min_eff_samples (5, like absence)  → profile's own quantiles
else → fallback_profile's bin for that hour (slot-wise, D-C6-style)
else → static profile value (None in the series)
```

A freshly created *Homeoffice* profile therefore plans exactly like
*Normal* from day one and fades in over ~2–4 weeks — no cold-start cliff,
no empty bins. Diagnostics expose per-profile effective sample counts so
the fade-in is watchable.

### D-P4: Season as a smooth day-of-year affinity (not month buckets)

The §8 rejection of explicit month buckets stands (a "Saturday in
February" bin has ~4 samples). But the operator's request — profiles may
differ by season — is answered by extending the existing weight, not the
bin scheme:

```
w(sample) = 0.5^(age_days / half_life)            # recency (today)
          × 0.5^(doy_distance / season_half_days) # seasonal affinity (new)
```

`doy_distance` = circular distance of the sample's day-of-year to the
aggregation date on the 365-day ring (0…182). With
`season_half_days ≈ 45`: a sample from the same season weighs 1, three
months away ≈ 0.25, half a year ≈ 0.06 — suppressed but never zero, so
bins never go empty. `season_half_days = 0` (default) reproduces today's
behaviour exactly (goldens stay frozen).

- **Data:** `daily_hours` retention extends 120 → 400 d (Store stays
  small: 400 × 48 floats; one-time delta backfill ~10 k LTS rows/entity —
  the reference plant has 17+ months). Only worthwhile with ≥ ~1 year of
  history; the diagnostics say so when there is less.
- **Resolution tradeoff (accepted):** the nightly aggregation references
  `doy(today)` and produces one profile set per profile id, as today — the
  season does not change materially across a 3–4-day horizon, and the
  Store schema/kernel contract stay untouched.
- Interaction with profiles is automatic: the weight multiplies into
  whichever (profile, hour) bin is aggregated — "Homeoffice in winter"
  emerges without a dedicated bin.

### D-P5: Planner docking — unchanged

The D-C5 series contract, the vacation semantics (D-C4: `base_w` without
`variable_w`), the DST rules and the slot-wise `None` fallback all stay.
The only change at the coordinator: the per-slot bin lookup asks
`classify_day(slot_date)` instead of the hard-wired day-type if/else.
P1/P2 (exactly one simulation, no pessimism in the series) are preserved.

### D-P6: Storage, migration, diagnostics

- Store **v3** of `battery_manager.learned_profiles.<entry_id>`:
  `day_log` values gain `profile_id` (migration maps weekday→normal,
  weekend→wochenende, absence/vacation→urlaub); `profiles`/`samples`
  re-keyed by profile id (the three built-ins carry their data over —
  no learning loss).
- Diagnostics: profile of today/tomorrow (sensor attributes), per-profile
  effective samples, season-affinity on/off; the bias watchdog (D-C9) validates
  the exact effective P50/fallback series used by the planner and labels each
  hour's origin. Sparse vacation bins that fall through to the static
  base-only profile are therefore still measurable instead of disappearing
  from MAE/bias.

## 5. Phasing & effort

| Phase | Content | Effort |
|---|---|---|
| 1 | `classify_day` resolver (pure core) + Store v3 migration + built-in profile renames + fallback cascade + per-profile diagnostics | ~3 PD |
| 2 | `consumption_profile` subentry type (config flow, de/en) + binary/calendar selectors + nightly tagging replay | ~2–3 PD |
| 3 | Calendar future-day classification (extend `future_daytypes`) | ~1–2 PD |
| 4 | Seasonal affinity weighting + retention 400 d + `season_half_days` option | ~2 PD |

Every phase ships with neutral defaults: an unconfigured installation
behaves **exactly** as today (phase 1–3) or with `season_half_days = 0`
(phase 4). Golden snapshots stay frozen; each phase gets its own
core/HA tests (resolver↔tagging equivalence, cascade, migration, DST).

## 6. Risks & observation

1. **Over-classification:** too many custom profiles starve every bin →
   the D-P3 cascade hides real learning. Mitigation: diagnostics show
   effective samples per profile; docs recommend ≤ 2–3 custom profiles.
2. **Selector misfires** (a calendar event typo silently reclassifies
   days): the day classification is surfaced as a sensor attribute and in
   `day_log`, so a wrong tag is visible and explainable.
3. **Season weight on short history:** with < 1 year of LTS the affinity
   suppresses the only samples there are → the diagnostics warn and the
   effective-sample floor keeps bins on their fallbacks instead of
   producing thin-air quantiles.
4. **Observation plan (as with stages 1–2):** 2 weeks watching the bias
   watchdog and per-profile coverage after each phase; the seasonal
   weight specifically gets a winter-vs-summer MAE comparison before
   `season_half_days` is recommended to all operators.
