# Battery Manager — Solution Strategy for the Rework

> Status: historical design rationale; see [ARCHITECTURE.md](ARCHITECTURE.md) and the other docs for the current design.
> Based on [REQUIREMENTS.md](REQUIREMENTS.md).

## 1. Core Idea of the New Algorithm

Without a feed-in tariff, the objective function is simple and measurable:

> **Minimise `grid import + grid export` over the forecast horizon.**

From this follows the actual task of the controller:

- **Export arises** when a PV surplus comes in and the battery is full
  → deliberately make room beforehand (run the inverters, supply the house from
  the battery) **or** steer the surplus into additional loads.
- **Import arises** when the battery is empty before the next PV surplus
  arrives → do not discharge too much.

The inverter's SOC threshold balances exactly this trade-off. Instead of
estimating it with a heuristic formula as before, it is henceforth **found by
simulation**:

### Threshold via Policy Simulation (instead of a formula)

```
for each candidate T in {min_soc … max_soc, step size 1 %}:
    simulate the horizon with the REAL policy:
        inverter on  ⇔  SOC > T
    evaluate: cost(T) = w_i · import_kWh + w_e · export_kWh
choose T with minimal cost (on a tie: higher T = gentler on the battery)
```

- **Policy-consistent:** The simulation reflects exactly the behaviour that the
  emitted threshold actually produces in reality (fixes weakness 2.2 from
  REQUIREMENTS.md).
- **Trivial compute cost:** ~90 candidates × ~60 hours × simple balance — well
  under one second, pure Python stdlib, no solver dependency.
- **Explainable & testable:** For every candidate there is a traceable
  trajectory; tests can check cost values directly.

### Surplus Loads via Surplus Allocation

After the threshold choice, the winning trajectory provides, per hour, the
**lost surplus** (energy that would be exported because the battery is full or
the charger is at its limit). The load planning works on top of this:

```
for each hour h with surplus S(h) > 0:
    for each available load L in priority order:
        if S(h) ≥ P(L) · (1 − battery tolerance):
            schedule L in hour h; S(h) -= P(L)
re-simulate with the scheduled loads (full horizon check):
    – no additional grid import compared to the plan without loads (Z2)
    – min-SOC never violated (Z3)
    violated → drop hour/load, repeat (converges quickly)
```

- Activation **only on a real surplus** (fixes weakness 2.1/1).
- Check over the **entire horizon**, not just up to the first time the target
  is reached (fixes weakness 2.1/2 and 2.1/3).
- Parallel operation of multiple loads follows naturally from the allocation.
- Fossibot particularities: available remaining energy from the SOC entity
  (full → load saturated, L8), power from the feedback entity smoothed (L7).

### Household Appliances

- **Running operation detected** (LG washer status / socket power): a stored
  remaining-run profile (kWh over n hours) is added onto the AC load forecast
  (G2). This automatically lowers the computed surplus, and additional loads
  back off — exactly the correction the operator wants.
- **Start-window recommendation (G3):** simulation "What if the appliance
  started now?" → binary_sensor `*_start_window` = on when the complete run
  produces no additional grid import.

## 2. Rejected Alternatives

| Approach | Why not |
|---|---|
| **Patch the existing heuristic** | Fixes the acute bugs, but policy inconsistency and the missing objective function remain; multiple loads/appliances do not fit the model. |
| **LP/MILP optimisation (e.g. PuLP/HiGHS)** | Mathematically optimal, but external solver binaries are fragile on HAOS/Alpine, hard to debug, and the benefit over the simulation search is minimal for this problem size (1 threshold + a few loads). Can be retrofitted later as an option. |
| **ML / learned policy** | Data needs, explainability and maintainability are out of all proportion to the problem. |

## 3. Architecture

### 3.1 Simulation Core (rewrite, HA-free, `battery_manager/core/`)

```
core/
├── model.py       # Frozen dataclasses: BatteryParams, ChargerParams,
│                  # InverterParams, SurplusLoad, Appliance, SystemConfig
├── series.py      # Building the hourly series: PV distribution, load profiles,
│                  # appliance remaining-runs → HourlyInputs
├── simulate.py    # simulate(config, inputs, policy) -> Trajectory
│                  # PURE FUNCTION: no side effects, no shared state
└── optimize.py    # Threshold search + surplus allocation + appliance advisor
                   # -> PlanResult (threshold, load plans, forecasts, flows)
```

Principles (Q1): immutable configuration objects, inputs/outputs as data
classes, no objects with hidden state as before (`set_additional_load_active`,
SOC mutation on shared instances). The energy-flow balance of an hour is
adopted in substance and re-implemented as a pure function in `core/simulate.py`
(the `step_hour` function).

### 3.2 HA Layer

- **Coordinator:** fix the listener bug + missing unsubscribe (Q3); otherwise
  keep the structure (polling + debounce on entity changes).
- **Config Flow v2** (breaking change, D4):
  - Base configuration as before (battery, PV, charger, inverter, base loads).
  - **Config subentries** (HA ≥ 2025.3) per surplus load and per household
    appliance: arbitrarily many loads/appliances via an "Add" UI, individually
    editable.
- **Entities:**
  - existing sensors remain (threshold, inverter status, min/max SOC, …)
  - per surplus load: `binary_sensor.<load>_recommendation` (+ attributes:
    scheduled hours, expected energy)
  - per appliance: `binary_sensor.<appliance>_start_window`
  - `sensor.lost_surplus_kwh` (forecast export) as a transparency/debug value
- **Version 0.2.0**, `translations/de.json` + `en.json` new.

### 3.3 Tests & CI

- **Core:** scenario tests (sunny day/rainy day/transition, full/empty battery),
  invariant tests (energy conservation per hour, SOC limits, "an additional load
  never creates import"), regression tests with the failure patterns from
  section 2.1 of REQUIREMENTS.md.
- **HA layer:** pytest-homeassistant-custom-component: config flow, coordinator
  (incl. listener!), entity values.
- **CI:** add pytest + ruff to validate.yml.

## 4. Implementation Phases

| Phase | Content | Result |
|---|---|---|
| **0** | Dev environment, pytest harness, coordinator bugfix (listener) | Immediately mergeable, foundation for everything else |
| **1** | Core rewrite (`core/`), threshold search, ONE surplus load with correct surplus logic, test suite, CI | Fixes all known algorithm errors |
| **2** | Multiple loads (subentries, priorities, parallel), Fossibot feedback (power/SOC), load entities | Target picture for surplus utilisation |
| **3** | Household appliances: remaining-run forecast + start-window recommendation | Target picture for appliances |
| **4** *(optional)* | Hourly PV forecasts (Solcast/Forecast.Solar directly), learned load profiles from HA history, de/en polish | Accuracy |

Each phase ends with runnable tests and a testable state on the real HA instance
(http://hass:8123).
