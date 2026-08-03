# F-FEEDIN — Vorzeitige Netzeinspeisung / early grid feed-in (v0.23.0)

Status: operator decision 2026-08-03. Binding spec of the early-feed-in
feature: planner pass `plan_feedin` (core), executor pair `_apply_feedin` /
`_execute_feedin` (HA), config section `early_feed_in`, entities
`switch.battery_manager_early_feed_in` and `sensor.battery_manager_feedin_mode`.

## 1. Ziel und Motivation

When the forecast shows **unavoidable** grid export (residual export > 0 after
all surplus loads are allocated), that energy would clip at midday with a full
battery — exactly when the grid least needs it. The feature **pre-shifts the
timing** of this export: in the morning, PV surplus is passed straight through
to the grid while the battery idles (battery power ≈ 0, SOC stays ~flat),
instead of exporting at noon off a full battery. **Total export is invariant —
only its timing moves off the midday peak.** Morning export is worth more to
the grid than midday-peak export, so the same kWh relieves the system better.

Actuation goes through the external controller's AC setpoint entity
(`input_number`, e.g. `input_number.acpowersetpoint`): a **negative** setpoint
means export. The external controller decides about the energy source itself;
the Battery Manager shapes the setpoint so the battery is never discharged for
feed-in.

## 2. Regeln

- **R1 (Trigger).** The pass runs only when the post-allocation trajectory
  (`alloc_traj`, median forecast) still exports — energy the loads provably
  cannot absorb. No residual export ⇒ no schedule, no setpoint writes.
- **R2 (Passthrough-only physics).** The battery is NEVER actively discharged
  for feed-in: `step_hour` serves booked feed-in from the slot surplus BEFORE
  charging (`charge = min(headroom, max(0, surplus − feedin))`,
  `grid_export += feedin` 1:1, same conversion path as natural export), clamps
  it to the surplus (`feedin_eff = min(feedin, max(0, surplus))`), and has no
  mechanism to feed it from the battery in a deficit slot. The live criterion
  the executor steers against is the battery-power entity ≈ 0 W — Victron
  convention: **positive = charging**.
- **R3 (Zielmenge = Median).** The per-day target is the day's residual
  `grid_export` of `alloc_traj` — the MEDIAN amount. The stress vector scales
  only the Z4 brake (R6), never the target.
- **R4 (Rate & weiche Deadline).** Per slot, ascending from slot 0:
  `rate = remaining / max(hours until the day's deadline_hour, slot duration)`,
  capped at `min(feedin_max_w, slot surplus)`. After the deadline the
  denominator collapses to the slot duration: the rest is worked off **as fast
  as the surplus allows** — until the amount is delivered **or the battery is
  full** (a full battery exports naturally; feed-in stops, setpoint 0).
  Every booking is re-simulated into the trial so the next slot's floor and
  soc_max checks see the reduced charge.
- **R5 (min_soc-Floor).** No booking while the trial SOC at slot start is at or
  below `feedin_min_soc_percent` (default 30 %). This is the ABSOLUTE floor of
  the feature; the options flow enforces it above `battery_min_soc_percent`.
  The executor applies the same gate live (`soc > min_soc`, else setpoint 0).
- **R6 (Z4 nur als Bremse).** The final WITH-feed-in series is re-simulated
  under the stressed PV vector (same per-slot P10/alpha machinery as
  `_z4_reject` / `_ramped_stress_floors`, including the relief clause that
  keeps dips the no-feed-in baseline already has). On a floor violation the
  LATEST feed-in slot at/before the violation is handed back, iterated (one
  slot per pass) until the floors hold. Z4 never enlarges feed-in.
- **R7 (Setpoint-Vorzeichen & Schreibpfad).** Sign convention of the setpoint
  entity: **negative = export** (`FEEDIN_SETPOINT_EXPORT_SIGN = -1.0` — a
  planned 500 W feed-in is written as −500). ALL coordinator bookkeeping keeps
  positive watts; the single constant flips the sign at the entity boundary in
  both directions. Writes go through the HA service `input_number.set_value`
  (`_set_number_value`), actuated in `_execute_feedin` under the shared
  `_switch_lock` with the in-flight re-check (manual mode / fail-safe entered
  after queueing drops the write). Exactly one INFO line per confirmed write:
  `Feed-in setpoint -> N W (reason)`.
- **R8 (Aktivierung).** Two switches, both required: the **options-flow toggle**
  `feedin_enabled` (section `early_feed_in`, default OFF — opt-in, with the
  wiring entities `feedin_setpoint_entity` (`input_number`) and
  `feedin_battery_power_entity` (`sensor`)) **and** the **runtime switch**
  `switch.battery_manager_early_feed_in` (Store-persisted, default ON — the
  config toggle is the opt-in). Switching either off pauses the executor and
  writes 0 once (auto mode only).
- **R9 (Manuell-Modus / Ownership).** If the setpoint entity reads ≠ the value
  we last wrote, outside a `FEEDIN_MANUAL_GRACE_S = 360` s grace window after
  our own write (propagation lag, mirrors the F-N2 late-confirmation grace),
  the feature enters manual mode until the next midnight. The grace excuses
  **only a state still showing the value we wrote BEFORE the last write** —
  any third value is an operator action. A blanket grace was wrong: the trim
  refreshes the write timestamp every few seconds, so while it ran every
  override was judged as lag and silently overwritten (review 2026-08-03)
  (`_feedin_manual_until = tomorrow`, persisted in the Store): no further
  writes, the operator owns the setpoint. At startup the current value is
  adopted: a value differing from the persisted own value enters manual mode
  immediately. Manual reset happens automatically at midnight; the mode is
  visible as `sensor.battery_manager_feedin_mode` (auto/manual) and in
  `coordinator.data["feedin_mode"]`.
- **R10 (Ereignisgesteuerter Trim, beide Richtungen).** The battery-power
  entity is a tracked input: every state update (Victron: typically every few
  seconds) fires the trim path via the debounced refresh, without waiting for
  the 5-minute planning cycle. This only works because the debounce ABSORBS
  events while a window is armed instead of cancelling and restarting its
  sleep — a restart-on-every-event debounce is starved outright by an input
  that updates faster than `DEBOUNCE_SECONDS`, and the trim then never ran
  between the polls at all (review 2026-08-03). The trim engages **only while the plan slot
  books > 0 W** — a plan value of 0 is re-anchored to 0 directly (R11), never
  trimmed. Deadband `FEEDIN_TRIM_DEADBAND_W = 50` W around zero.
  - **Downward, immediate and unthrottled:** battery discharging
    (`battery_power < −50 W` ⇒ the feed-in is eating into the battery, e.g. a
    kettle the forecast never saw) ⇒ cut the setpoint by the discharge amount,
    floored at 0. Several writes per minute are fine (internal `input_number`,
    no physical actor) — seconds of unwanted discharge, not minutes.
  - **Upward, throttled and capped:** battery charging
    (`battery_power > +50 W` ⇒ surplus booked as export is charging the
    battery instead) ⇒ raise the setpoint by the charge amount (hold the
    battery at ~0 W, work the day's target off faster), but at most one raise
    per `FEEDIN_UPWARD_MIN_INTERVAL_S = 60` s (anti-hunting against the
    external controller) and capped at `min(feedin_max_w,
    feedin_by_day_wh[today] / hours_until_midnight)`. That plan value is
    booked over the slots **from now on** and is therefore ALREADY the
    remaining amount — subtracting the energy delivered earlier today
    double-counted it, drove the cap to 0 and silently disabled the upward
    trim for the rest of the day (review 2026-08-03).
  Trim writes carry no deadband — but an **unchanged** value is never
  rewritten (a sustained discharge clamps the trim at 0, and re-writing that 0
  every pass flooded the log and hammered the state machine); 0 ↔ >0
  transitions always write.
- **R11 (Re-Anchor).** The setpoint is pulled back to the plan slot value once
  the trim increments drifted beyond `FEEDIN_REANCHOR_DEADBAND_W = 25` W — the
  ONLY place this deadband applies (the trim increments are estimates; the
  plan is the anchor). Throttled to `FEEDIN_REANCHOR_MIN_INTERVAL_S = 300` s:
  this pass also runs on every debounced battery-power event, so an ungated
  re-anchor undid each throttled upward trim seconds later and the setpoint
  oscillated against the external controller for the whole window (review
  2026-08-03).
- **R12 (Fail-safes).** The G4 floor guard and the D-A8 stale-data shed force
  the setpoint to 0 dwell-exempt — an unsupervised export must not keep
  running (same argument as the load force-off); a queued write is dropped in
  `_execute_feedin` when a fail-safe tripped after queueing. Two of these
  shutdowns must work OUTSIDE the planning path, because `_apply_feedin` only
  runs on a successful cycle (review 2026-08-03):
  - **Data loss.** A SOC/forecast outage raises `UpdateFailed` before the
    executor, so `_feedin_force_zero` is called from the failure site itself
    (and by the runtime switch, which cannot rely on a refresh that raises).
    Idempotent — however long the outage lasts, the 0 is written once.
  - **Unwiring.** The setpoint entity that currently carries a non-zero value
    of ours is persisted (`feedin_owned_entity`). Clearing the wiring in the
    options flow would otherwise strand that value forever, since
    `_apply_feedin` returns before it can write the documented one-shot 0;
    `_feedin_release_orphan` runs at the top of every cycle and zeroes it.
- **R13 (Entities & Attribute).** Created only while the config toggle is on:
  `switch.battery_manager_early_feed_in` (R8) and
  `sensor.battery_manager_feedin_mode` (ENUM auto/manual, R9). SOC-forecast
  points gain `feedin` (W, rounded to 0.1) on slots with a scheduled value;
  the `daily` breakdown gains `planned_feedin_kwh` per day (from
  `PlanResult.feedin_by_day_wh`). The published trajectory is the WITH-feed-in
  one: the flat morning SOC shows in the forecast, and `lost_surplus` stays
  the export that is still lost AFTER feed-in.
- **R14 (Card).** The forecast card renders a feed-in lane under the SOC chart
  (pattern: the dc24/dc48 support lanes) with one block per feed-in period
  including the W figure, plus a stats line "geplante Einspeisung
  heute/morgen x/x kWh" from the `daily` breakdown (rendered only when the
  attribute exists — backend compatibility).
- **R15 (Golden-Neutralität).** Neutral defaults: `FeedInParams.enabled =
  False`, and `plan` short-circuits when disabled or `max_w == 0` — no extra
  simulation, no schedule, and the trajectory chain stays bit-identical to the
  pre-feature plan. The golden snapshots were intentionally NOT regenerated;
  the unchanged goldens ARE the neutrality verification.

## 3. Abgrenzung (was bewusst NICHT getan wird)

- **Kein gestresstes Ziel.** The amount is the median residual (R3); pessimism
  acts only through the Z4 brake — a stressed target would systematically
  over-export energy the battery then misses at midday.
- **Kein Export-Limit-Regler.** The feature shapes a setpoint for the external
  controller; it does not limit, curtail, or meter the export itself.
- **`prevented_export_by_day_wh` unverändert.** The counterfactual keeps
  comparing base vs. alloc WITHOUT feed-in — it answers "what did the loads
  prevent", not "what was pre-shifted". Feed-in energy is reported separately
  (`feedin_by_day_wh` / `planned_feedin_kwh`).
- **Kein Cross-day-Trim-Gedächtnis.** The delivered-energy integrator is
  in-memory and resets at midnight; a restart re-anchors at the plan value and
  the startup-adoption judgement (R9), which is the safe direction.

## 4. Tests

- `tests/core/test_simulate.py` — F-FEEDIN passthrough: charge reduced, export
  +1:1, clamp to the surplus after DC cover, never discharged in a deficit
  slot, series length validated, zero series bit-identical.
- `tests/core/test_optimize.py` — F-FEEDIN section: disabled ⇒ empty and
  bit-identical; trigger only on residual export after loads; booking into the
  morning surplus; rate spread towards the deadline; overrun as fast as the
  surplus allows; `max_w` cap; `min_soc` floor; stop at soc_max; Z4 brake
  trims latest-first; empty horizon.
- Goldens: **no** `gen_golden.py` run — the suite passes without a diff
  (R15 neutrality verification).
- `tests/ha/test_feedin.py` (24 tests). Fail-safes and write-storm guards
  (review 2026-08-03): data loss forces the setpoint to 0 exactly once however
  long the outage lasts; the runtime switch writes its 0 even while the inputs
  are stale; an unwired setpoint entity is released to 0 by the reloaded
  coordinator; a downward trim never rewrites an unchanged value; the re-anchor
  is throttled to the planning interval; an external change DURING an active
  trim enters manual mode while a lagging state does not; the upward cap
  ignores the energy delivered earlier today; a fast tracked input does not
  starve the debounce. Original set: plan-slot write with negative setpoint
  and re-anchor to 0; downward trim immediate/unthrottled; upward trim 60-s
  throttle and remaining-target rate cap; deadband writes nothing; re-anchor
  pulls a drifted setpoint back; external override ⇒ manual mode until
  midnight; runtime switch off ⇒ 0 once, surviving restart; floor guard and
  stale shed ⇒ 0; SOC at the feed-in floor blocks; startup adoption (offline
  change ⇒ manual, matching value ⇒ auto); entities exist with the config
  toggle, absent without.
