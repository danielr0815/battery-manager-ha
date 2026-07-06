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
that the load draws while the input is active — it is still used (smoothed)
as the planning power. The energy progress of the charge is tracked over the
SOC (ground truth) anyway, not integrated over the power.

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
- Alongside this, the coordinator persists the switch dwell across restarts
  (the loss was a contributing cause). The power EMA is deliberately NOT
  persisted, and on feedback gaps is only served while the load is really
  charging — after charge end, the taper residual (10–40 W) would otherwise
  permanently weaken all gates as "measured" planning power. The log lines
  "Charging started/stopped" name the plain-text load name.
- **F-L7 (2026-07-05): power-deviation warning per load.** The dehumidifier
  periodically defrosts briefly (power drops for minutes) and stops entirely
  when the water tank is full (power near 0 W despite an active
  recommendation). Defrost cycles may enter the power average (samples between
  the standby threshold and the rated power average the EMA; below that the
  sample is discarded and the EMA is frozen — "totally ruining it" is
  precluded by the 25 % threshold). But if the real power deviates for **longer
  than 30 minutes** by more than the configured percentage (field "power-
  deviation warning", default 50 %, 0 = off) from the rated power while the
  load is running at the integration's instigation, the load's new warning
  sensor (`binary_sensor … power warning`, device class `problem`) goes on —
  as a trigger for user notifications (empty the tank, correct the rated power,
  foreign load). Short defrost pauses reset the timer and do not fire; manual
  operation never fires (F-L6 logic).
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
  Outside these windows measurements are ignored and any existing EMA is
  discarded (planning falls back to the rated power). Together with the standby
  filter (v0.6.2: samples only ≥ max(10 W, 25 % × rated power)) the learned
  power is thus shielded against hand and foreign use.
  Clarification for switched loads: there the **physical** charging state
  deliberately counts — the feedback ("IN Total") measures the device itself,
  so a manually started charge yields correct device data; the window is
  bounded by the minimum runtime. Entity outages (unavailable/unknown) never
  count as "off" here (otherwise the EMA would be discarded mid-charge).
