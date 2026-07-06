# Architecture & developer onboarding

> Start here if you want to work on the code. This is the map; the other docs in
> `docs/` are the detailed design records for individual subsystems.

Battery Manager plans hourly energy flows for an AC-coupled PV + battery system
and turns the plan into switching recommendations (and, for the grid-support
PSUs, direct switching). Everything runs locally on the ~5-minute Home Assistant
update cycle.

## The one big idea: a pure core behind a Home Assistant shell

The code is split into two layers, and keeping them separate is the single most
important design rule.

- **`custom_components/battery_manager/core/`** — the **pure planner**. It has
  **no Home Assistant imports and no side effects**: you pass frozen dataclasses
  in and get frozen dataclasses out. This is where the physics and the
  optimization live. It runs on any Python interpreter, which is why its tests
  run on Windows without Home Assistant installed.
- **Everything else in `custom_components/battery_manager/`** — the **Home
  Assistant layer**. It reads entity states, calls the core, actuates switches,
  learns the consumption profile, exposes entities, and serves the dashboard
  card. This is where async, I/O, and all the messy real-world state machines
  live.

If you find yourself importing `homeassistant` into `core/`, stop — that logic
belongs in the HA layer.

## Code map

### Pure core (`core/`)

| File | Role |
|---|---|
| `model.py` | All the frozen dataclasses: `SystemConfig`, `BatteryParams`, `ControlParams`, `SupportParams`, `LoadProfile`, `PVParams`, `SurplusLoad`, `Appliance`, and the per-hour `HourSlot` / `HourFlows` / `Trajectory`. The data contract between the layers. |
| `series.py` | Builds the per-hour input series (`build_slots`): the slot grid, PV distribution over the day, base AC/DC load profiles, and appliance-run insertion. |
| `simulate.py` | `step_hour` / `simulate`: the energy-flow simulation of one slot / the whole horizon. The battery charges via the AC→DC charger, discharges via the DC→AC inverter; DC loads and the two-bus support model are settled here. |
| `optimize.py` | `plan`: the planner. Threshold search, surplus-load allocation, the appliance-window advisor, and the last-resort grid-support escalation. |
| `load_profile.py` | The learning math: cleaning measured load into a residual profile, weighted quantiles for the uncertainty bands. |

### Home Assistant layer

| File | Role |
|---|---|
| `__init__.py` | Setup/unload/reload, the export services, and serving + registering the dashboard card. |
| `coordinator.py` | The heart. A `DataUpdateCoordinator` that runs the update cycle (below), reads inputs, calls `plan`, actuates the support PSUs and load switches, and holds the F-N2 manual-override and R2 controller state machines + persistence. |
| `config_flow.py` | The config + options flows (sectioned) and all cross-field validators; sub-entry flows for surplus loads and appliances. |
| `history_profile.py` | The consumption learner: fetches recorder LTS, cleans out self-controlled loads, and builds the AC/DC profile + uncertainty bands. |
| `sensor.py`, `binary_sensor.py`, `switch.py`, `entity.py` | The entity platforms + the shared base entity/device. |
| `const.py` | Config keys, defaults (`DEFAULT_CONFIG`), and tuning constants. Effectively the settings reference. |
| `frontend/battery-manager-forecast-card.js` | The bundled Lovelace card that renders the SOC forecast + schedule from the `soc_forecast` sensor. |

## A planning run (the core, at a glance)

`optimize.plan(config, inputs)` does, in order:

1. **Threshold search** — for each candidate discharge threshold, simulate the
   whole horizon under the real policy `inverter on ⇔ SOC > threshold`; pick the
   lowest-cost one (import − terminal battery value + a small export penalty).
2. **Surplus allocation** — assign would-be-exported hours to surplus loads,
   re-simulating each assignment so it can never add grid import or breach the
   SOC buffer (a committed-energy floor + a latest-first second pass).
3. **Appliance windows** — advise whether a full appliance run fits into the
   surplus right now.
4. **Support escalation** — if the battery would still fall through its floor,
   schedule the 24 V / 48 V grid PSUs as last-resort protection.

The output is a single consistent `PlanResult` (one trajectory). Details:
[ALGORITHM.md](ALGORITHM.md); the two-bus DC / support model:
[DC_TOPOLOGY.md](DC_TOPOLOGY.md).

## The coordinator update cycle

`BatteryManagerCoordinator._async_update_data` is the most important HA-layer
flow to understand. It runs every ~5 minutes (30 s during startup) and on
debounced input changes, in this order:

1. `_update_support_modes()` — reconcile the F-N2 manual-override state **first**
   (its result feeds the simulation's forced-on flags).
2. Read inputs: `_get_soc`, `_get_forecasts` (bail with `UpdateFailed` if
   missing), `build_system_config`, `_get_load_states`, `_get_appliance_runs`,
   `_learned_series` (the learned profile + uncertainty bands).
3. `build_slots(...)` → the per-hour inputs; compute the dynamic buffer.
4. `plan(config, inputs)` in an executor (the core is sync/CPU-bound).
5. Post-process: `_apply_threshold_inertia`, `_apply_hysteresis` (the inverter
   recommendation).
6. Actuate: `_apply_support_switching` → `_run_dc48_controller` (R2) →
   `_apply_load_switching`.
7. Diagnostics: `_update_power_warnings`, `_update_gate_calibration`.
8. Assemble the `data` dict the entities read (`soc_forecast`, `hourly_details`,
   plan params, support modes, …).

Actuation runs in **entry-scoped background tasks serialized by a single
`_switch_lock`**, deliberately detached so a cancelled refresh can't abort a
half-finished switch sequence. Persistent state (support mode, plug ownership,
the R2 caused-off flag, dwell timestamps) is written via an HA `Store` and
flushed on unload.

## Key state machines (read the code comments)

Two parts of `coordinator.py` are subtle and heavily commented with the *why*
(often the incident or review round that motivated them):

- **F-N2 manual override** (`_update_support_modes`, `async_set_support_manual`,
  the make-before-break `_sequence_dc24`): a support PSU switched on externally
  enters "manual" mode and the planner keeps hands off until it is switched off
  again. The tricky bit is telling device actuation lag apart from a real
  operator action.
- **R2 voltage controller** (`_run_dc48_controller`, `_dc48_actuate`): while the
  48 V PSU is in manual mode and a battery-voltage sensor is configured, it is
  cycled by voltage with asymmetric hysteresis. The R3 switch is the sole mode
  truth; a controller-caused OFF never exits manual mode.

Spec: [DC_TOPOLOGY.md](DC_TOPOLOGY.md) §6/§7. Load control: [LOAD_CONTROL.md](LOAD_CONTROL.md).

## Glossary of the shorthand

The comments and design docs use short decision codes. Each maps to a doc:

| Prefix | Meaning | Doc |
|---|---|---|
| `D-A1 … D-A9` | Algorithm design decisions | [ALGORITHM.md](ALGORITHM.md) |
| `D-C1 … D-C10` | Consumption-learning design decisions | [CONSUMPTION_FORECAST.md](CONSUMPTION_FORECAST.md) |
| `P1 … P4`, `N2` | Consumption-forecast requirements | [CONSUMPTION_FORECAST.md](CONSUMPTION_FORECAST.md) |
| `F-N1 / F-N2 / F-N3` | The grid-support / two-bus feature line | [DC_TOPOLOGY.md](DC_TOPOLOGY.md) |
| `R1 / R2 / R3` | Operator requirements for the support paths (voltage gate / voltage controller / manual switch) | [DC_TOPOLOGY.md](DC_TOPOLOGY.md) |
| `§N` | A section of DC_TOPOLOGY.md | [DC_TOPOLOGY.md](DC_TOPOLOGY.md) |
| `Jury-Gap #N` | A gap flagged during the F-N3 design jury review | [DC_TOPOLOGY.md](DC_TOPOLOGY.md) |
| `F-L1 … F-L7` | Load-control features | [LOAD_CONTROL.md](LOAD_CONTROL.md) |
| "review round N" | An adversarial code-review pass; the comment records the finding it fixed | (in code) |

## Recommended reading order

1. This file.
2. [ALGORITHM.md](ALGORITHM.md) — how the planner decides.
3. `core/model.py` then `core/simulate.py` then `core/optimize.py` — the code the
   algorithm doc describes.
4. `coordinator.py` — start at `_async_update_data`, then the two state machines.
5. The subsystem docs as needed: [DC_TOPOLOGY.md](DC_TOPOLOGY.md),
   [CONSUMPTION_FORECAST.md](CONSUMPTION_FORECAST.md), [LOAD_CONTROL.md](LOAD_CONTROL.md).

[REQUIREMENTS.md](REQUIREMENTS.md) and [STRATEGY.md](STRATEGY.md) are historical
design records — useful for intent, but the code and the docs above are the
current truth.

## Testing model

The test suite mirrors the layer split (see [CONTRIBUTING.md](../CONTRIBUTING.md)
for commands): `tests/core/` runs the pure core anywhere (`-p no:homeassistant`);
`tests/ha/` needs the Home Assistant test helpers (Linux/WSL/CI). Planner
behaviour is frozen by golden snapshots (`scripts/gen_golden.py`).
