# Battery Manager — Requirements Analysis and Rework

> Status: historical design record. It captures the original requirements and intent; some file/line references describe the pre-refactor layout (see docs/ARCHITECTURE.md for the current code map).

## 1. Current State (reconstructed from the code)

### 1.1 Modelled System

The simulation core models the following topology (derived from the energy-flow logic, now in `core/simulate.py`):

```
PV ──(AC side!)────┐
                   ├── AC loads (base + variable + additional load)
Grid ──────────────┤
                   │
             Charger (AC→DC, 92 %)          Inverter (DC→AC, 95 %, min. SOC)
                   │                               │
                   └────────── Battery ───────────┘
                                  │
                              DC loads
```

**Important:** In the code, PV production is balanced on the **AC side**
(`ac_balance = pv - ac_consumption`). The battery is charged exclusively through the
charger (AC→DC). The README, by contrast, claims "PV to DC Load: direct DC
consumption has highest priority" — **code and documentation contradict each other**.
→ To be clarified: the actual hardware topology (question F1).

### 1.2 Inputs and Outputs

**Inputs** (via HA entities): current SOC (%), daily PV forecasts for today/tomorrow/day-after (kWh).
**Internal models:** hourly PV distribution curve (morning/afternoon window, ratio),
static load profiles (base + variable time window) for AC and DC.

**Outputs** (HA entities): SOC threshold (%), inverter status (on/off),
min/max SOC forecast, hours until max SOC, discharge forecast, additional-load status,
grid import/export for the forecast period.

### 1.3 Core Algorithm ("Maximum-Based Controller")

1. Simulate the SOC trajectory hourly from now until **08:00 two days out**.
2. Determine `min_soc_forecast` — but only up to the point at which the SOC
   first reaches the target (`target_soc`, default 85 %).
3. Threshold: `threshold = current_soc − (min_soc_forecast − min_battery_soc) + forced_charger_soc`,
   clamped to `[max(battery_min, inverter_min) … min(target_soc, current_soc)]`.
4. `inverter_enabled = current_soc > threshold` → binary sensor for the real control.

### 1.4 Additional-Load Logic (`_calculate_additional_load_optimization`)

- Hour-by-hour iteration; while the load is inactive, a "safety check" verifies
  whether, with the additional load permanently active, the SOC never drops
  below the inverter minimum before the target SOC is reached. If so → **activate immediately**.
- Deactivation only when **both** hold: >50 % of the additional load comes from the
  battery **and** the target SOC is currently reached.

## 2. Identified Weaknesses of the Algorithm

### 2.1 Additional Load Activated Too Early / For Too Long (problem confirmed by the user)

1. **Activation does not check whether a surplus exists.** The safety check only
   evaluates "SOC stays above the minimum and eventually reaches the target". It
   therefore activates the load even when it is currently fed entirely from the
   battery/grid — the primary purpose "surplus utilisation" is not verified
   (historical: controller.py additional-load logic).
2. **The safety check ends at the first target-reach.** Everything after that is
   unchecked (historical: controller.py): if the SOC briefly reaches the target
   around midday tomorrow, the drop below the minimum tomorrow evening is invisible.
3. **Deactivation requires `current_soc >= target`.** If the SOC falls after
   activation (target never reached), the load stays active even when it is fed
   100 % from the battery — until the SOC reaches the inverter minimum and the
   normal consumption has to be bought from the grid (historical: controller.py).
   → **Exactly the misbehaviour observed by the user.**

### 2.2 Structural Problems

- **Simulated policy ≠ applied policy.** The simulation assumes the inverter
  runs as soon as SOC > inverter minimum (20 %). In reality, the inverter is
  switched via the computed threshold. The forecast therefore simulates a
  different system behaviour than the one the forecast itself brings about
  (feedback not modelled) → unreliable/oscillating thresholds.
- **Heuristic threshold formula** without a clearly defined objective. Nothing is
  optimised (no cost/benefit function); the formula is a snapshot heuristic.
- **Duplicate simulations with inconsistent assumptions:** `_calculate_total_grid_flows`
  simulates again without the additional-load schedule (historical: controller.py)
  and does not align the following hours to hour boundaries — the import/export
  sensors do not match the remaining outputs.
- **Mutating global state** (`set_additional_load_active` on the shared
  `ac_consumer`, SOC manipulation on `battery`) makes the logic error-prone and
  hard to test; a lot of dead code (`_test_additional_load_activation`,
  `_simulate_with_additional_load_schedule`, `_project_soc_*`).
- **Static load profiles** (base + one time window) capture real households only
  coarsely; no use of HA history, no weekday/seasonal profiles.
- **PV hourly curve** is a simple two-window model; hourly forecast data
  (e.g. Solcast/Forecast.Solar provide hourly values) is not used.

### 2.3 Known Bugs in the HA Layer (separate from the algorithm)

- The coordinator's `_listeners_setup` is never set to `True` → immediate updates
  on entity changes are ineffective, only 5-minute polling. Listeners are not
  removed on unload. (historical: coordinator.py)

## 3. Clarified Boundary Conditions (operator answers, 2026-07-03)

- **Topology confirmed:** PV is AC-side coupled. The code model
  (PV → AC balance, battery charged only via the AC→DC charger, DC loads on the
  battery, discharge inverter DC→AC) matches the real installation.
- **Optimisation objective:** Maximum self-consumption and minimum grid draw.
  Feed-in is **not remunerated** — exported energy is lost. No dynamic tariffs.
- **Control chain:** HA automations really switch both the discharge inverter
  (via inverter status / SOC threshold) and the additional load
  (via `additional_load_status`).
- **Additional loads (target picture, today only 1 flat load):**
  - **Load 1 + Load 2:** one Fossibot F2400 powerstation (2 kWh) each, as
    additional chargeable storage; both are integrated in Home Assistant
    (SOC/switchability available).
  - **Load 3:** dehumidifier in the shared cellar, optional (pure surplus
    utilisation, can be interrupted at any time).
- **Household appliances:** dishwasher and washing machine are to be taken into
  account:
  1. When a device is detected as started, its expected remaining consumption
     should immediately flow into the forecast.
  2. Expansion stage: The user can mark devices as "may start when surplus is
     available" → the plugin signals/starts when the run is possible without
     grid draw.

## 4. Requirements for the Reworked Solution (draft)

### 4.1 Objective Function

- **Z1:** The primary goal is to minimise grid draw AND feed-in over the forecast
  horizon (export is worthless, import costs). Since both goals follow from the
  same balance: minimise `grid_import + grid_export` (weightable).
- **Z2:** Hard constraint: normal household consumption always has priority over
  all optional loads. Optional loads must never cause grid power to be bought for
  the normal consumption.
- **Z3:** The battery operating limits (min/max SOC, inverter minimum) are hard
  constraints over the ENTIRE horizon (not only up to the first time a target SOC
  is reached).

### 4.2 Surplus-Load Management (new)

- **L1:** Support for several optional loads with a priority order (configurable),
  instead of one flat additional load.
- **L2:** Configurable per load: power (W), type (interruptible like a
  dehumidifier / energy amount until "done" like a powerstation charge),
  optional SOC entity (Fossibot) to determine the remaining energy.
- **L3:** An optional load is only activated when its consumption in the
  activation window is (almost entirely) covered by PV surplus that would
  otherwise be exported or lost due to a full battery — configurable tolerance
  (e.g. max. X % from the battery).
  *Extension v2 (2026-07-04):* Time-shifted coverage via the battery is also
  permitted ("precautionary space-making") if the simulation proves over the
  entire horizon that no additional grid import arises and the lost surplus
  drops by at least (1 − tolerance) × load energy
  (details: ALGORITHM.md D-A4 v2).
- **L4:** Deactivation as soon as the surplus condition is no longer met —
  regardless of whether a target SOC has been reached.
- **L5:** A dedicated HA entity per load (switch recommendation/status) so that
  automations can switch them individually.

### 4.3 Household Appliances (new)

- **G1:** Configurable devices (washing machine, dishwasher) with a detection
  entity (e.g. socket power or status sensor) and a stored remaining-run
  consumption profile (kWh, duration).
- **G2:** When a device is running, its expected remaining consumption is added
  to the AC load forecast.
- **G3 (expansion stage):** "start-window recommendation": an entity per device
  that shows whether a complete run starting now (or in hour X) would be possible
  without grid draw.

### 4.4 Forecast & Model

- **P1:** Consistent policy simulation: the simulation must assume the same
  switching behaviour that the computed outputs actually cause
  (feedback threshold → inverter → SOC trajectory).
- **P2:** A single simulation per update delivers all outputs consistently
  (no parallel simulations with differing assumptions).
- **P3 (expansion stage):** Use hourly PV forecasts directly when available
  (e.g. Solcast/Forecast.Solar hourly values) instead of distributing daily
  values over a static curve.
- **P4 (expansion stage):** Learn load profiles from HA history instead of static
  base/time-window values.

### 4.5 Quality

- **Q1:** The simulation core stays HA-independent and pure (no side effects, no
  mutated shared objects) → deterministically testable.
- **Q2:** pytest test suite (core + HA layer), runnable in CI.
- **Q3:** Known bugs in the HA layer are fixed (entity listener, listener cleanup).

## 5. Clarified Detail Questions (2026-07-03)

- **D1 Priority:** Loads may run **in parallel** when the surplus is enough for
  several; the priority order only decides in case of scarcity.
  **Extended 2026-07-17 (F-GATE-PARITY):** priority decides ALL contested
  energy, including pass-2 make-room/battery-share bets — load-class rules
  must never override it ("lieber den Fossibot laden, als den
  Luftentfeuchter betreiben, wenn die Wahl besteht"). Energy-limited loads
  keep exactly one class rule: no zero-PV (night) slots.
- **D2 Fossibots:** Charge only, throttled to **300 W**. The energy supplies
  directly connected loads (e.g. PC) — no feed-back into the house grid. The
  **actual charging power should be read from the feedback of the HA entities**
  and used in the algorithm (measured power instead of a fixed value; the fixed
  value only as fallback/initial estimate).
- **D3 Devices:**
  - Dishwasher: detectable via socket power measurement; has Wi-Fi and can
    possibly be integrated directly.
  - Washing machine: already integrated via LG ThinQ
    (`F_V8_Y___W.B_2QEUK`, DEVICE_WASHER).
- **D4 Migration:** **Breaking change is accepted** — config flow and data model
  may be rebuilt; a one-time reconfiguration is fine.

### Additions from the Algorithm Discussion (see ALGORITHM.md)

- **Topology detail:** Two DC levels — 48 V battery and 24 V rail via a DC/DC
  converter. Emergency support paths: a 48 V PSU with fixed power
  (default 60 W, parameter) and a 24 V PSU as a DC/DC replacement.
- **N1:** Both support paths are **switched directly** by the plugin (configured
  switch entities) and are taken into account in the simulation as the last
  escalation stage to protect the battery (details: ALGORITHM.md D-A9).
- **N1a (make-before-break, 2026-07-04):** The 24 V rail must never be without a
  source: the 24 V PSU is activated first and, after a short delay, the DC/DC
  converter is switched off; conversely, the DC/DC converter is activated first
  and, after the delay, the PSU is switched off. The delay is configurable
  (default 3 s); if the new source is not confirmed, the switchover is aborted
  (the old source stays on).
- **N2 (expansion stage):** Risk assessment from season / past forecast quality →
  dynamic SOC buffer instead of a fixed 5 %.
- **Decided knobs:** Tie → "benefit" (draining the battery ahead of strong sun
  has priority; backstop N1); hysteresis ±1 % with max. 1 inverter switch/min;
  SOC buffer +5 %; additional-load battery share default 15 %
  (0–50 % configurable), minimum on/off duration 30 min.

### Requirements Added from D1–D3

- **L6:** Parallel operation of several surplus loads; priority only takes effect
  when the surplus is scarce.
- **L7:** Optionally per load a power-feedback entity (e.g. Fossibot charging
  power): when the load is active, the measured power (smoothed) is used as the
  load power; the configured nominal value serves as a fallback.
- **L8:** Optionally per load a SOC/done entity (Fossibot SOC) to detect "load is
  saturated" (fully charged → the load is no longer available and is skipped).
