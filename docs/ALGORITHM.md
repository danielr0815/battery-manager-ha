# Battery Manager — Algorithm Design in Detail

> Status: reflects the shipped planner as of v0.7.10.
> In-depth companion to [STRATEGY.md](STRATEGY.md); decision points D-A1 … D-A9.

## 1. Flow of a Planning Run

Every ~5 minutes (or whenever an input entity changes):

```
1. Build the input series (hourly grid up to the end of the forecast data):
   PV(h)        from daily forecasts × distribution curve (later: hourly forecasts)
   AC load(h)   base profile + detected appliance remainders (washer/dishwasher)
   DC load(h)   base profile
2. Threshold search:
   for T in [max(batt_min, inv_min) … batt_max], in 1-% steps:
       trajectory = simulate(T)         # policy: inverter on ⇔ SOC > T
       cost(T) = Import(T) − f·SOC_end(T) + w_e·Export(T)
   T* = argmin cost      (tie → see D-A1)
3. Surplus allocation on the T* trajectory:
   identify export hours → assign loads by priority
   (in parallel when the surplus is sufficient); each assignment is checked
   by re-simulation against Z2 (no additional import) and Z3 (SOC limits, whole horizon)
4. Appliance advisor: "Can a run start now without additional import?" per appliance
5. Outputs: T*, inverter recommendation (with hysteresis), load plans/recommendations,
   min/max SOC, import/export forecast — all from ONE trajectory
```

## 2. Decision Points

### D-A1 Terminal Value & Tie-Breaking Rule (the most important tuning knob)

The battery energy remaining at the end of the horizon must be valued, otherwise
the optimizer "burns it off" down to the minimum for no reason.

- **Terminal value:** `f = η_discharge · η_inverter ≈ 0.92` — stored energy
  is worth as much as the import it later replaces. This makes
  "discharge now vs. keep it" cost-neutral when there is no PV surplus —
  mathematically correct, because that is exactly how it works in reality.
- **The tie-breaking rule then decides the behaviour:**
  - **(a) prefer the higher threshold ("Hold")**: robust against
    forecast errors (a full battery protects against import, export is worthless anyway).
    Consequence: on overcast days the house buys grid power even though the battery
    is at, say, 60 % — it is only used later (by DC load / the next gap).
    Looks "dumb" but is cost-neutral. → prototype scenario S2.
  - **(b) prefer the lower threshold ("Use")**: the house runs preferentially from
    the battery ("perceived self-consumption"), risk: if consumption is
    unexpected, the reserve is gone.
  - **Operator decision (2026-07-03): (b) Use.** Rationale:
    1. It should be avoided that the battery is not discharged far enough for the
       next strong sun (energy would be wasted).
    2. As a backstop there is an **emergency support** (§D-A9): PSUs
       can take over the DC levels and hard-prevent the battery
       from falling into the disallowed range.
    3. The buffer from D-A3 (+5 %) catches normal forecast errors.
    In addition, the export malus `w_e = 0.05` remains as a second tiebreaker.
  - **Extension stage (Phase 4):** risk assessment from season and
    past forecast quality (observed forecast error) → buffer
    dynamic instead of a fixed 5 %.
  - **Finding from the core implementation (2026-07-04):** On days without
    notable PV surplus, "Hold" is usually **strictly** cheaper, no
    tie: the battery on the direct DC path (η≈0.97) is worth more
    than via the inverter (η≈0.92 + standby), and an empty battery forces
    the DC rail into expensive grid draw via the charger (factor 1/0.92). The
    optimizer finds this itself; the "Use" tiebreak only takes effect on real
    ties (it then picks the lower edge of the cost-equal
    plateau). The desired "make room before strong sun" arises
    automatically as soon as surplus is forecast (scenario S1).

### D-A2 Hysteresis & Switching Stability

Problem: `SOC > T*` is re-evaluated every 5 min; near the threshold there is a risk of
flapping of the real inverter (and T* itself can jump between nearly equivalent
candidates).

- **Operator decision (2026-07-03):** The inverter tolerates frequent
  switching; limit only **at most 1 switching operation per minute**.
  1. Output hysteresis: on at `SOC ≥ T*+1 %`, off at `SOC ≤ T*−1 %`,
     in between hold the last state.
  2. Threshold inertia: adopt a new T* only if it deviates ≥ 2 % from the old
     one or the cost is better by > ε (prevents jumping between
     equivalent candidates).
  3. Minimum switching interval for the inverter recommendation: **1 min**.

### D-A3 Handling Forecast Uncertainty

- Options: (a) PV pessimism factor (forecast × 0.9), (b) SOC buffer above the
  minimum (planning computes with `min+X %`), (c) nothing — frequent replanning
  corrects.
- **Operator decision (2026-07-03): (b) with X = 5 %** (configurable)
  plus (c). A pessimism factor (a) also distorts the load allocation
  (surplus is systematically underestimated → Fossibots charge too rarely).

### D-A4 Additional Loads: Tolerance & Cycling

- **Battery-share tolerance:** **Operator decision: allow some
  tolerance.** Default **15 %** battery share per load (configurable 0–50 %).
  The hard condition "no additional grid import over the horizon" (Z2)
  remains unaffected and is still checked by re-simulation — the
  tolerance only allows a load to run partly from the battery for a short time
  (e.g. a cloud gap), as long as the balance holds.
- **v2 — objective-based gate (operator decision, 2026-07-04):**
  In addition to the direct surplus hours (pass 1) there is a
  second allocation pass: a load may also run in an hour **without**
  direct surplus (e.g. pre-charging before a short, strong
  midday peak) if the re-simulation over the entire horizon proves:
  (a) grid import does not rise, (b) the SOC buffer holds, and (c) the
  lost surplus drops by at least `(1 − tolerance) × load energy` —
  so the energy demonstrably comes, time-shifted through the battery, from
  otherwise-lost surplus. Energy-limited loads (Fossibots) enter
  pass 2 only with budget left over from pass 1 (saturating within the sun window has
  priority, saving battery cycles). Among each other, loads compete in config
  order = priority (order = the configured per-load priority since v0.8.2,
  default creation order, F-LOAD-PRIORITY). The harmful scenario "5 a.m., 50 % SOC"
  is automatically discarded by (a) (the battery would breach the
  inverter minimum → import); the useful scenario "short
  sun window, high SOC" is automatically allowed.
  **v0.13.0 (F-GATE-PARITY):** both load classes face the IDENTICAL pass-2
  gate set (one Z2' trade invariant, c1-rt, c2-beta insurance, Z4 stress),
  so priority alone decides contested bet energy — the former
  energy-limited c1-only/strict-import carve-out silently overrode the
  priority order. Single remaining class rule: energy-limited loads never
  book zero-PV (night) slots.
  **v0.15.0 (F-STRICT-SURPLUS, operator decision 2026-07-19):** the Z2'
  proportional trade is retired — the import gate is the absolute
  `IMPORT_ARTIFACT_SLACK_WH` (Z2'', R1); no booking may cover a slot the
  trial serves inverter-off or at/below the 20 % cutoff, all accepted
  bookings re-validated per trial (planner-G4, R2); and the Z4 bet window
  ends at the first slot where the TRIAL refills to soc_max instead of the
  same-day PV window end (R3) — daytime and night bets face the same
  overnight stress, restoring the operator's lateness order.
  See docs/F-STRICT-SURPLUS.md.
- **Merge principle (operator insight, 2026-07-04):** All gate conditions
  are **difference comparisons** of two complete trajectories. As soon as
  both variants (with/without the load hour) reach the max SOC, they are
  identical from there on — everything after the "merge point" cancels out
  automatically. An explicit truncation of the simulation is therefore unnecessary.
  Consequence for the min-SOC condition (b): it checks **relatively** — a
  load hour is only discarded if it pushes the minimum SOC below the buffer
  AND worsens it relative to the plan without that hour. An
  SOC trough at an overcast horizon end that occurs identically in both variants
  can no longer block today's surplus hours.
- **v3 — minimum-runtime-honest evaluation + latest-first (operator
  decision, 2026-07-05):** Two corrections after the night-charging incident
  of 07-05 (04:59:50, ~250 Wh real for a 5 Wh plan):
  1. Every activation decision is evaluated AND simulated with the energy
     that the executor really forces: `power ×
     max(slot remaining duration, minimum runtime)`, distributed over time across slot
     boundaries ("spill", all affected slots are planned together).
     This means the degenerate slot 0 (a partial hour, at minute :59
     only 1/60 h) can no longer slip mini-energies through the gates that
     the real minimum runtime then exceeds by a factor of 50. The
     saturation gate is additionally floored at the nominal power, so that
     an empty/decayed feedback EMA cannot weaken it.
  2. Pass 2 runs **latest-first** (the latest justifiable hour first):
     additional loads are activated as late as possible, but still
     early enough that no surplus is lost. Catching up with
     better information beats the early bet on the forecast;
     bringing forward is only justified if the surplus window is
     power-limited. Slots after the last export slot can
     never satisfy condition (c) and are skipped; without export in the
     horizon pass 2 is dropped entirely.
- **v4 — lateness is absolute for energy-limited residual top-ups (operator
  decision, 2026-07-10):** After a night trickle-charge incident (a Fossibot
  156 Wh short of its target booked a 0.5 h / 150 Wh run at "now" and charged
  from the house battery), the as-late-as-possible principle is made
  **absolute** — it must not be broken by partial ("started") hours or any other
  implementation reason. Energy-limited loads therefore **quantise like
  continuous loads** (a `k · min_runtime` sub-hour commitment, not only whole
  slots), so a residual smaller than one nominal hour is bookable in any slot and
  pass 2's latest-first order — not slot-0 geometry — decides its placement. The
  executor bounds such a sub-hour run with a frozen off-deadline as an UPPER CAP
  over the primary level-driven target-SOC stop, so a stale load-SOC sensor
  cannot stretch a ~150 Wh top-up into a full-hour night charge
  (docs/F-RESIDUAL-TOPUP.md).
- **v5 — honest planning power, load-outer pass 1, explain-plan (operator:
  "Setze alles um", 2026-07-10):** Three refinements
  (docs/F-PLANNER-HONESTY.md, resolves the open decision O1 of
  F-RESIDUAL-TOPUP):
  1. **Learned planning power:** the coordinator learns per load a robust
     estimate of the real draw (since v0.14.0 the time-weighted windowed
     MEDIAN of accepted samples, docs/F-ROBUST-POWER.md — spikes/dips are
     majority-immune, 5-min warm-up; historically the run-max-of-EMA, which
     the 2026-07-18 818 W transient incident retired) and persists it, so an
     OFF load is planned at its real power (F2400-B: ~505 W) instead of the
     configured nominal (300 W). Precedence: live measured > learned >
     nominal (unchanged).
  2. **Pass 1 is load-outer** in config order (strict priority: a load books
     its complete pass-1 allocation before the next load sees the horizon)
     with per-class slot direction — **day-bounded latest-first for
     energy-limited loads** (calendar days ascending, hours within each day
     descending: lateness now also governs WHICH direct-surplus hours a
     saturating residual takes, but never at the price of stranding an
     earlier day's export — the principle's second clause "just early enough
     to avoid export" bounds the first), ascending for continuous loads.
     Each candidate reads the exactly re-simulated export of the current
     trajectory instead of an intra-slot decrement.
  3. **Explain-plan:** every accepted allocation records a terse reason
     string at acceptance time (`LoadPlan.reasons`, surfaced as `why` in the
     per-load schedule attribute), so "why is this load on NOW?" no longer
     needs code archaeology.
- **v6 — pass-1 rescues present export first; the regime split (operator
  decision, 2026-07-11):** the v5 day-bounded latest-first order for
  energy-limited loads in **pass 1** is replaced by **earliest-export-first**
  (ascending, like continuous loads; F-RESCUE-EXPORT). Live trigger: a
  Fossibot with room sat idle at 73.9 % while the house battery was at 99 %
  and ~1.7 kW was exported, because the plan had deferred its charge to the
  day's last export hour. The insight is that pass 1 is **post-saturation** —
  a candidate slot passes the soft-surplus gate only when the battery is
  already full and exporting there, so deferring rescues no extra energy (an
  energy-limited load charges its fixed remaining capacity either way) while
  losing the present, certain export to bet on a later forecast one. The two
  regimes are the faithful reading of the timing principle: **buffer while you
  can (pass 2, defer the preemptive bet), rescue immediately once you cannot
  (pass 1, run as soon as export occurs).** Pass 2 stays latest-first,
  unchanged.
- **v7 — gate-stop final top-up closes the stall band (2026-07-11,
  docs/F-GATE-TOPUP.md):** an energy-limited load could never be re-booked
  once `rem < max(planning_power, nominal) × min_runtime/60` — no candidate
  below one quantum exists (F-SUBHOUR R2) and the saturation gate rejects all
  of them — so the F2400-B (learned ~600 W) was unbookable above 75 % SOC and
  parked at ~85-89 % instead of its 90 % target; learned power (v5) WIDENED
  the band. For loads **with a charge-enable gate** the dwell-overshoot
  rationale that forbade smaller bookings (F-RESIDUAL-TOPUP D2) no longer
  holds: the G1 dwell-exempt target stop delivers exactly `rem`. Such loads
  now get ONE final candidate `rem / max(planning_power, nominal)` appended
  after the quantised list — offered exactly when every k·q candidate would
  fail the saturation gate, subject to a 50 Wh de-minimis floor
  (GATE_TOPUP_MIN_WH) and all the usual gates. Plug-only loads are unchanged
  (the dwell really would overshoot there). The executor's frozen off-deadline
  stays `max(min_runtime, run)` — for a final quantum it is deliberately
  LONGER than the run: the stale-SOC upper bound only, G1 remains the stop.
- **v8 — empirical P10/P90 bands replace the scalar α/β where evidence exists
  (2026-07-11, docs/F-QUANTILE-BANDS.md):** the balcony forecaster publishes
  per-15-min P10/P90 curves as attributes on the SAME three PV entities; the
  planner composes per-slot vectors from them — `stress = clamp(p10/median,
  0.1, 1.0)` for the Z4 lower-buffer gate, `optimism = clamp(p90/median, 1.0,
  2.0)` for the c2 insurance — with per-slot fallback to the α/β dials
  wherever no band exists. THE safety rule: a **collapsed** band (p10 == p90,
  the cold-start signature) counts as NO band — it means "no evidence", not
  "no uncertainty"; treating it as certainty would weaken Z4 below α on
  exactly the history-free bins. A slot has a band only with real data, a
  median ≥ 25 Wh and a real spread (> max(1 Wh, 1 %)). With no bands anywhere
  the plan is bit-identical to v0.9.3 at the same dials; a partially covered
  day mixes evidence and fallback in the same simulation vector. Consequences
  the operator should know: Z4 protection now varies with weather-class
  history (milder on provenly stable days — the yield intent), the c2 reasons
  read "(p90)" on band-backed slots, the SOC-forecast sensor's
  `quantile_coverage` shows the bands maturing per day, and with β later set
  to 1.0 insurance fires only where P90 evidence exists (the recommended
  posture once coverage settles — resolves the β=1.2 complaint without
  forfeiting volatile-day yield). Placement policy is unchanged: the bands
  change what the gates BELIEVE, not where bookings land.
- **v9 — night rescue: rt-honest c1, merge-bounded T\*, crossover buffer ramp
  (2026-07-12, docs/F-NIGHT-RESCUE.md; incident: no night pre-drain before a
  known ~3.3 kWh clipping day, then a 04:13 threshold jump 20→58):**
  1. **Round-trip-honest c1:** the refill gate demanded
     `export_drop ≥ (1−tol)·energy`, but a pure AC→battery→AC detour
     physically returns only `rt ≈ 0.82` of the energy — night bookings could
     NEVER pass, regardless of forecast clipping. The need is now
     `(1−tol)·energy·rt` (rt from the config's own efficiency chain, clamped
     (0, 1]); direct-PV runs drop export ~1:1 and are unaffected, and
     Z2'/Z3/Z4 still bound how deep the drain may go.
  2. **Merge-bounded threshold:** beyond a slot where the battery is full and
     clipping even under the stressed PV (the same per-slot vector Z4 uses),
     the trajectory is independent of today's T\* (merge principle) — so the
     candidate scan is truncated there (min 6 slots) and post-merge economics
     (hoarding for a weak final day) can no longer strangle tonight. No
     stressed clip → full horizon, unchanged. Exposed as
     `threshold_horizon_end` (PlanResult + SOC-forecast attribute).
  3. **Crossover buffer ramp:** the Z4 stress floor's BUFFER component ramps
     with the remaining stressed deficit until the stressed PV crossover —
     the closer the crossover, the less forecast-error buffer the inverter
     reserve needs (operator principle). Z3's absolute battery floor stays
     static.
- **Cycling:** planning on the hourly grid; the real recommendation with a
  minimum on/off duration (default 30 min) — spares appliances and relays
  and, since v3, enters the evaluation as committed energy.
- **Saturation:** Fossibot SOC ≥ configurable target (default 100 %) → the load
  is considered saturated and is skipped (L8). Remaining energy demand
  `= (target−SOC) × 2 kWh`, planned charge power from the feedback entity
  (EMA-smoothed), fallback 300 W (L7).

### D-A5 Appliance Profiles (Washer/Dishwasher)

- v1: configured per appliance: detection entity (status or power),
  program energy (kWh) and duration (h). While the appliance runs, the remaining energy is
  spread evenly over the remaining duration of the AC forecast and added on (G2).
- LG ThinQ (washer) provides remaining time → more accurate distribution possible.
- Start-window recommendation (G3): trial insertion of the complete profile from now;
  `on` when import delta = 0 (and the SOC limits hold).

### D-A6 Horizon

- So far: until 08:00 the day after tomorrow (arbitrary). With the terminal value the
  horizon end is uncritical. **Recommendation:** use the full available forecast
  (until midnight of day 3).

### D-A7 Time Grid

- **Recommendation:** hours in v1 (data situation: daily forecasts). Build the core so
  that the grid is a parameter → a 15-min grid becomes possible in Phase 4.

### D-A8 Behaviour on Data Loss

- As before: last valid values with age limits (SOC 1 h/6 h, forecast
  24 h/72 h). **Additional recommendation:** on exceedance the last plan is
  frozen, and after a further 2 h all recommendation entities become
  `unavailable` + the additional-load recommendation "off" (fail-safe: better to
  waste surplus than risk grid draw).

### D-A9 Emergency Support of the DC Levels (new, from the operator answer to D-A1)

The system has two DC levels: the **48 V battery** and a **24 V rail**,
which is normally supplied from the battery via a DC/DC converter. For the
emergency there are two support options:

1. **48 V support PSU:** feeds a fixed power (default **60 W**,
   parameter) into the battery level → compensates the base load, the battery does
   not fall into the disallowed range.
2. **24 V PSU instead of DC/DC:** takes over the 24 V loads entirely from
   the grid → relieves the battery of the DC load.

**Design:** The plugin gets one recommendation entity per support path
(binary_sensor) that turns "on" when the simulation, despite inverter-off,
expects the SOC to drop to the stage's **activate** threshold. The support
paths enter the simulation as switchable loads/sources (48 V support = −60 W on
the battery balance; 24 V PSU = DC load → 0, grid import instead).
Priority: support is the last escalation stage — normal planning should never
need it; it covers forecast errors and exceptional situations.

**Thresholds (v0.7.13):** each stage is a hysteresis loop driven by two
**absolute** battery-SOC values, configurable and independent of the planning
buffer (so a dynamically widened planning buffer, D-C8, never moves the PSUs):
`support_dc24_activate_soc`/`support_dc24_recovery_soc` (default 10 / 11 %) and
`support_dc48_activate_soc`/`support_dc48_recovery_soc` (default 5.5 / 10 %). A
stage switches on below its activate SOC and off again at/above its recovery
SOC; a wider gap latches the support on longer, so an SOC parked on a threshold
holds steadily on grid instead of chattering. The recommended ordering is
`soc_min < dc48_activate < dc48_recovery ≤ dc24_activate < dc24_recovery`.
Config validation enforces the parts that do not depend on the battery's
`soc_min` (which is set in a separate step): `activate < recovery` for each
stage, and the 48 V last-resort stage at or below the 24 V stage
(`dc48_activate ≤ dc24_activate` and `dc48_recovery ≤ dc24_recovery`). The
defaults reproduce the pre-0.7.13 hard-coded thresholds at the default battery
config, and a migration backfills the exact legacy values for existing entries.

**Decision F-N1 (2026-07-03):** Both support paths are switchable via HA entities
and the plugin shall **switch them directly** (entity IDs in the
configuration; plus status entities for transparency). Unlike with
inverter/additional loads (recommendation + user automation), the
integration performs the switching itself here — a protective function with priority.

**Addition make-before-break (2026-07-04, operator requirement):** The
24 V rail must never be without a source when switching over. Therefore the
**48 V→24 V DC/DC converter** is also configurable as a switch, and the switchover
runs sequenced with a configurable delay (default 3 s):

- **Activate grid operation:** 24 V PSU ON → delay → DC/DC OFF.
- **Return to battery operation:** DC/DC ON → delay → 24 V PSU OFF.
- **Fault case:** If the newly switched-on source does not report
  "on" after the delay, the switchover is aborted — the previous source stays on,
  and the next planning cycle tries again (the minimum switching interval applies).
- If no DC/DC switch is configured, only the PSU is switched
  (assumption: parallel feeding / diode decoupling is permissible).
- On startup the integration adopts the real actual state of the switches
  (important after an HA restart with active support).

**Decision F-N2 (2026-07-05): Manual override per PSU.** In
winter it can make sense to activate the PSUs permanently by hand.
If a support PSU is **switched on externally** (not by the
integration), the automatic control pauses for exactly this PSU
— including the make-before-break sequence on the 24 V path. Only when the
PSU is **switched off externally again** does the automation take over
again (on the 24 V path a switched-off DC/DC converter is then immediately
switched back on so that the rail is not dead). Details:

- The mode (automatic/manual) is **persisted per PSU** and
  survives HA restarts; in addition the integration's own switching state is
  persisted, so that after a restart "on, but not from
  us" stays distinguishable from "on, because switched by us".
- In manual mode the **simulation treats the path as permanently
  active** (24 V: DC load from the grid; 48 V: constant feed-in) —
  the SOC forecast thus matches real winter operation.
- Per PSU there is a **mode sensor** (`sensor. … support mode`,
  enum automatic/manual) for dashboards and notifications.
- Grace period: within `min_switch_interval_s` after our own
  switching action, an unexpectedly switched-on PSU is treated as a
  late-confirming device (the state is adopted), not as an override.
- Manually **switching off** an automatically activated PSU stays
  automatic: the protective function may switch it back on if needed.

## 3. Prototype Results (hourly grid, integration defaults)

Prototype: a script independent of the current HA layer (historical); battery 5 kWh (5–95 %),
inverter min 20 %, charger 92 %/inverter 95 %, loads: 2× Fossibot 300 W
(2 kWh demand each) + dehumidifier 400 W, priority in this order.

| Scenario | Result |
|---|---|
| **S1** 20:00, SOC 80 %, tomorrow 14 kWh | T\* = 57 % → the battery is discharged into the house overnight (make room), **import 0**, loads charge 8–14:00: export drops 17.1 → 9.9 kWh |
| **S2** 20:00, SOC 60 %, tomorrow 1.5 kWh | T\* = 95 % ("Hold", D-A1a): no inverter operation, battery as reserve, import 2.5 kWh (would be equal or worse at any other threshold) |
| **S3 = user error scenario** 21:00, SOC 84 %, tomorrow 13 kWh | **No load active overnight** — Fossibots/dehumidifier only run 8–14:00 in the real surplus; import stays 0. The old logic would have activated the load immediately. |
| **S4** 11:00, SOC 93 %, sunny | Loads run **immediately** (battery almost full, surplus present), export 10.8 → 4.8 kWh |

Observations:

- The threshold search produces exactly the expected behaviour "make room before
  sunny days, save before overcast days" — without this behaviour being
  programmed in anywhere; it follows from the objective function.
- The allocation prevents by construction the old misbehaviour
  (S3): loads run only in hours with real surplus.
- Remaining export in S1/S3/S4 is physically unavoidable (PV surplus >
  storage + load intake) — visible in the planned sensor
  `lost surplus`.

## 4. Open Points After This Discussion

- Confirmation/change of the recommendations D-A1 … D-A4 by the operator.
- Exact configuration fields per load/appliance → determined during the config-flow design
  (Phase 2/3).
