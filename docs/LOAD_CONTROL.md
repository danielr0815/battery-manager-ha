# Specification: Controlled charging paths for surplus loads (v0.3)

> Status: **implemented as of v0.7.10.**
> Extends REQUIREMENTS.md (L requirements) and ALGORITHM.md with direct
> actuation of powerstation charging paths by the integration.

## 1. Starting point (operator description, F2400-B as an example)

- The 230 V **input** of the Fossibot hangs on a switchable socket
  (`switch.shelly_01_switch_0`).
- If the input is **off**, the Fossibot sleeps: all of its sensors (incl.
  SOC) become `unavailable`. This is the **normal state** before charging —
  not "device gone".
- If the input is **on**, the Fossibot wakes up, **charges**, and at the same
  time supplies a load possibly connected to the **output** (e.g. a PC) via
  passthrough directly from the input.
- Via `input_boolean.charge_f2400_b`, **charging of the battery** can be
  **disabled** even though the input is on (the output is then still supplied
  from the input, the battery stays unchanged).
- The input is also activated by **external automations** — e.g. to wake the
  Fossibot or to supply the output from the grid instead of from the battery.
  This usage belongs to the operator, not to the Battery Manager.

**State model of the charging path:**

| Input (Shelly) | Enable (input_boolean) | Battery charges | Output supplied from |
|---|---|---|---|
| off | irrelevant | no | Fossibot battery (if output on) |
| on | on | **yes** | input (passthrough) |
| on | off | no | input (passthrough) |

## 2. New configuration fields per surplus load (optional)

| Field | Example F2400-B | Meaning |
|---|---|---|
| **Charging-input switch** (`control_switch_entity`) | `switch.shelly_01_switch_0` | Switches the 230 V supply of the charging input. If set, the integration switches the charging path **itself** (instead of only recommending). |
| **Charge enable** (`charge_enable_entity`) | `input_boolean.charge_f2400_b` | Gate "the battery may charge". Switched by the integration together with the input. |

Without these fields everything stays as before: a pure recommendation entity,
the operator switches via automation.

## 3. Switching semantics

**Charging active** ⇔ input ON **and** enable ON.

- **Charge start (planned hour begins):**
  1. Remember whether the input was already on (→ "foreign ownership",
     passthrough).
  2. Charge enable ON.
  3. Input ON (if off).
- **Charge end (no more planned hour / load saturated):**
  1. Charge enable OFF — always.
  2. Input OFF — **only if the integration switched it on itself**
     ("ownership rule"). If it was already on at charge start (e.g.
     passthrough supply of the PC by an operator automation), it stays on.
- **Minimum runtime** (`min_runtime_min`, default 30 min) acts as a minimum
  on/off dwell time of the real switching (no cycling in cloud gaps).
- The switching operations run — as with the support paths — in an
  abort-safe background task; real switch states are read back while idle
  (heals manual interventions).
- The load's recommendation entity remains (transparency + trigger option
  for additional operator automations).

**Conflict avoidance:** If the integration switches the charging path itself,
the previous charging automation ("F2400 Intelligent charge control") must be
disabled or restricted to other tasks — two controllers on the same switch
produce ping-pong.

## 4. SOC handling with a sleeping device (replaces previous behaviour)

Previously: an energy-limited load with an unreadable SOC ⇒ unavailable.
**New:**

- The SOC value is **cached** on every valid reading and **reused** on
  `unavailable`/`unknown` (last valid value, without an age limit —
  self-discharge is small, correction happens at the next wake-up).
- The cache is **persisted** (HA storage) so it survives an HA restart with
  a sleeping Fossibot.
- If no SOC is known at all (fresh install, empty storage): the load is
  treated as **needing a charge** (assumption SOC = 0). Self-healing: at the
  first scheduled charge the device wakes up, reports the real SOC, and the
  plan corrects itself within one cycle (≤ 5 min). A device that happens to
  be full ends charging anyway via its internal limit.
- No active "waking to measure": the SOC becomes live exactly when it is
  needed (during charging). The operator's external wake automations update
  the cache as a side effect.

## 5. Power measurement and passthrough

`total_input` (IN Total) measures input = charging **+** passthrough output.
From the point of view of the house's AC balance this is correctly the power
that the load draws while the input is active — since v0.14.0 it feeds the
ROBUST windowed estimator (docs/F-ROBUST-POWER.md: time-weighted median,
5-min warm-up, spike/dip immunity) instead of the former EMA. The energy
progress of the charge is tracked over the SOC (ground truth) anyway, not
integrated over the power.

## 6. Further points from the operator's wish list

### 6.1 Visualize the SOC forecast trajectory

- New sensor `sensor.…_soc_forecast`: state = forecast SOC in 1 h; attribute
  `forecast` = list `[{t: ISO time, soc: %}, …]` over the whole horizon (from
  the final plan trajectory, incl. load effect).
- Display via the already-installed **ApexCharts card** with `data_generator`
  — ready-made card YAML is shipped in the README.

### 6.2 Icon

- **Shipped locally** (no brands PR needed): since HA 2026.3, custom
  integrations may ship their brand images directly. The files live under
  `custom_components/battery_manager/brand/` (`icon.png` 256×256, `icon@2x.png`
  512×512, plus `logo.png`/`logo@2x.png`). HA serves them via the local
  brands proxy API (`/api/brands/integration/battery_manager/icon.png`); local
  images take precedence over the CDN, no manifest configuration needed. Motif:
  battery with lightning bolt + sun.

## 7. Operator decisions (2026-07-04)

- **F-L1: The input-off policy is CONFIGURABLE PER LOAD** (field
  `input_off_policy`):
  - `auto` (default): ownership rule — input off only if the integration
    switched it on itself.
  - `always_off`: always switch the input off at charge end.
  - `keep_on`: never switch the input off (only toggle the enable). Note:
    without a configured charge enable, charging cannot be stopped in this
    mode — only meaningful with an enable entity.
- **F-L2: Yes** — an unknown SOC ⇒ assume it needs a charge (assumption 0 %).
- **F-L3: Yes** — the integration takes over the charge switching; the
  previous automation "F2400 Intelligent charge control" is disabled by the
  operator.
- **F-L4 (revised 2026-07-04):** the operator does **not** want to submit the
  icon officially. Instead the local brand/ mechanism is used (HA ≥ 2026.3,
  see §6.2) — the icon lives in `custom_components/battery_manager/brand/`, no
  PR needed.

## 8. Operator decision on charge timing (2026-07-05)

- **F-L5: Activate additional loads as late as possible** — but still early
  enough that no energy has to be exported. Catching up (at each replan with
  better information) beats pulling forward early on a forecast basis; pulling
  forward also costs the detour via the house battery (~18 % cycle losses with
  the default efficiencies). Implementation: Pass 2 latest-first + a
  minimum-runtime-honest evaluation (ALGORITHM.md D-A4 v3). The trigger was the
  night-charge incident of 05.07 (degenerate-slot-0 bug: three starts each at
  minute :59, really ~250 Wh per "5 Wh plan").
- **F-GATE-PARITY (2026-07-17, v0.13.0): priority always wins over load
  class.** The former class asymmetry — continuous loads could take
  make-room/battery-share bet energy (Z2' trade slack, c2, Z4) that
  energy-limited powerstations were denied — let a lower-priority
  dehumidifier out-book a higher-priority Fossibot ("lieber den Fossibot
  laden, als den Luftentfeuchter betreiben, wenn die Wahl besteht"). Both
  classes now face the identical pass-2 gate set; the single remaining class
  rule is that energy-limited loads never book zero-PV (night) slots. See
  docs/F-GATE-PARITY.md.
- Alongside this, the coordinator persists the switch dwell across restarts
  (the loss was a contributing cause). The live power-sample window is deliberately NOT
  persisted, and on feedback gaps is only served while the load is really
  charging — after charge end, the taper residual (10–40 W) would otherwise
  permanently weaken all gates as "measured" planning power. The log lines
  "Charging started/stopped" name the plain-text load name.
- **F-L7 (2026-07-05, extended 2026-07-12): power-deviation warning per load.**
  The dehumidifier periodically defrosts briefly (power drops for minutes) and
  stops entirely when the water tank is full (power near 0 W despite an active
  recommendation). Defrost cycles may enter the power average (samples between
  the standby threshold and the rated power feed the windowed estimator
  (v0.14.0; the median ignores them as a minority anyway); below the bar the
  sample is discarded entirely — "totally ruining it" is
  precluded by the 25 % threshold). But if the real power deviates for **longer
  than the per-load dwell** (field "power warning dwell", default 15 min) by
  more than the configured percentage (field "power-deviation warning", default
  **0 % = off**; the operator opts a load in per device — an existing load keeps
  its stored value) from the rated power while the load is running at the
  integration's instigation, the load's warning sensor (`binary_sensor … power
  warning`, device class `problem`) goes on. Short defrost pauses reset the
  timer and do not fire; manual operation never fires (F-L6 logic).
  - **Latching (operator wish 2026-07-12).** Once on, the warning stays on
    until the load runs at its configured power **again at the integration's
    request** — it does NOT clear merely because BM stops requesting the load
    (a full tank is still full while the load is off). The latch is persisted,
    so an options save (which reloads the coordinator) or a restart does not
    silently drop it; only the bool is persisted, so a not-yet-tripped dwell
    re-arms after a restart.
  - **Push notifications (operator wish 2026-07-12).** A single global list of
    `notify` targets (integration Options → *Power-warning notifications*, e.g.
    each person's companion app) is pushed to when any load's warning trips,
    and — unless silenced — when it clears. Empty list = no push; the
    `binary_sensor` remains available for custom automations either way.
- **F-L6 (2026-07-05): manual activations do not influence planning.** The
  operator occasionally switches loads (or rather their metered socket) by
  hand — e.g. the dehumidifier, or a foreign load on the same socket. Feedback
  measurements therefore only train the planning power while the load is
  running **at the integration's instigation**: for switched loads the real
  charging state (plug AND enable on), for pure recommendation loads the active
  plan recommendation of the last cycle **with a clean start** — when the
  recommendation is switched on, the metered socket must not yet be drawing,
  otherwise an already-running hand/foreign load would be learned and could
  tip over the next plan via the learned value (flutter loop, review finding).
  Outside these windows measurements are ignored and the live sample window is
  discarded (planning falls back to the rated power). Together with the standby
  filter (v0.6.2: samples only ≥ max(10 W, 25 % × rated power)) the learned
  power is thus shielded against hand and foreign use.
  Clarification for switched loads: there the **physical** charging state
  deliberately counts — the feedback ("IN Total") measures the device itself,
  so a manually started charge yields correct device data; the window is
  bounded by the minimum runtime. Entity outages (unavailable/unknown) never
  count as "off" here (otherwise the sample window would be discarded mid-charge).

## 9. Target-SOC stop is dwell-exempt behind a charge-enable gate (v0.9.0)

F-EXECUTOR-GUARDS G1: the plan-driven OFF of an **energy-limited** load whose
SOC reading is at/above its target skips the ON→OFF minimum-runtime dwell —
**iff a charge-enable entity is configured**. The dwell protects relays and
compressors from short cycling, but the enable gate switches no load current
path mechanically worth protecting (the plug — if switched at all — switches
currentless afterwards, see the ordered OFF branch in §3), while every dwell
minute overshoots the target at real power (~250 Wh in 30 min at ~505 W,
landing at ~95 % for a 90 % target). Plug-only loads keep the full dwell (the
plug relay is exactly what `min_runtime` protects), as does a load whose SOC
reading is absent (conservative). The confirmed switch still stamps the dwell
timestamp, so the OFF→ON dwell (`min_off`) fully gates a re-on: a SOC hovering
at the target cannot flap the switch — re-on additionally requires the planner
to book again (SOC below target first).

## 10. Stale-SOC guard (v0.9.0)

F-EXECUTOR-GUARDS G2: the F2400-B's integration is known to serve **cached SOC
values with fresh timestamps** ("Poll timed out, returning cached data"), so
availability/age checks cannot catch it and the planner would re-book run
after run against a frozen `remaining` (the v0.8.1 executor cap only bounds a
single run). While the device **demonstrably charges** — BM's charging state
active AND the raw power feedback above the v0.6.2 standby bar (the single
threshold, reused) — a SOC that stays EXACTLY unchanged for
`STALE_LOAD_SOC_MIN` (12) minutes latches the reading as stale: the load is
held **unavailable** (the planner drops it; the plan-driven OFF runs through
the normal executor path) and a WARNING names the load and the frozen value
(logged once, change-gated). The latch clears as soon as the SOC entity
reports a **different** value (charging or not; INFO once). The evidence clock
measures continuous charging against a frozen value, not wall time: it resets
when charging stops or the sample bar is not met (an end-of-charge taper never
accumulates false evidence), and loads without a SOC or power-feedback entity
never latch. In-memory only — a restart re-detects within minutes. The
per-load diagnostics expose `soc_stale`.

## 11. Floor guard — surplus loads never run grid-fed (v0.13.1)

F-EXECUTOR-GUARDS G4, binding operator rule (2026-07-18): **"Wenn der
Inverter aus ist oder der SOC 20 % erreicht, dürfen Zusatzlasten nicht mehr
angesteuert werden."** Incident that forced it: on 2026-07-18 06:20 the plan
deactivated a booked dehumidifier run, but the min_runtime ON→OFF dwell held
the switch until 06:30 while the battery hit the 20 % inverter cutoff at
06:21 — the real inverter shut down and the 432 W load ran ~10 min on GRID.
No executor path checked SOC or inverter state; the recommendation itself
stayed ON at SOC 20.0 (its ±1 % hysteresis flips only at 19).

Mechanics (`_update_floor_guard`, computed each cycle right after
`_apply_hysteresis`): the guard trips when the battery SOC is at/below
`inverter_min_soc_percent` OR the inverter RECOMMENDATION is off ("Inverter
aus" is deliberately the recommendation, not the physical inverter state —
BM has no inverter state entity; the SOC branch catches the physical cutoff,
the recommendation branch the T*-driven shutdowns). While active,
`_apply_load_switching` forces every controlled load OFF **dwell-exempt**
(the G1 precedent: safety overrides min_runtime; the confirmed switch still
stamps the dwell so min_off fully gates the re-on), never switches one ON,
and the executor drops even already-QUEUED switch-ONs (in-flight race); the
published per-load `active` reads False (operators' own automations and
recommendation-only loads stop too — note that recommendation-only loads
have no BM-side min_off after a guard stop, the release hysteresis is their
only flap brake), the appliance start-window advisory reads False (a start
would import), runtime accrual's plan-based fallback pauses and the power
warning treats all loads as inactive (no 0 W false positive). The WHOLE
guard LATCHES: release requires the SOC strictly above the floor and at
`inverter_min + hysteresis_percent` (default +1 %; strictly-above keeps
trip/release disjoint even at hysteresis 0) with the recommendation on; a
restart inside the release band starts latched (conservative). Reaction
time is the SOC entity's debounced refresh (~5 s), not the dwell. In-memory
only; surfaced as `floor_guard_active` on the inverter-recommendation
binary sensor. The planner is deliberately unchanged — G4 is the hard
executor backstop against forecast error, not a planning rule.

## 12. Robust power learning (v0.14.0)

docs/F-ROBUST-POWER.md, operator spec 2026-07-18 after the 818 W incident
(a 60 s compressor-restart transient EMA-blended and frozen for a 426 W
dehumidifier): the planning power of a surplus load is now a TIME-WEIGHTED
MEDIAN over the run's last ~30 min — short spikes and dips (inrush,
defrost, transfer transients) can never move it; sustained level changes
are adopted (~15 min, or ~10 min via the stable fast-adopt window, e.g. an
operator-changed fossibot charge rate); the first 5 min of a run never
learn at all. No nominal clamp (levels above nominal are legitimate); a
3×-nominal estimate logs a change-gated WARNING instead. **Appliances are
untouched by all of this: their planning always uses the DECLARED run
energy/duration — measured power only feeds their on/off detection.**

## 13. Seamless quantum boundaries (v0.14.0)

docs/F-SEAMLESS-RUNS.md, operator min_off model 2026-07-18: min_off arms
ONLY on deliberate deactivations. When a booked quantum ends and THIS
refresh's plan re-booked a contiguous run, the executor extends the frozen
deadline seamlessly — no OFF/ON relay cycle, no compressor restart, no
min_off pause (the observed 50 % duty cycle is gone). Energy-limited loads
extend only while the G2 stale-SOC guard can supervise them; everything
G2-unsupervisable keeps the R7/R8 duty-cycle cap. G4 always wins.
