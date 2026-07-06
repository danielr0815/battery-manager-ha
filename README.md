# Battery Manager Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![CI](https://github.com/danielr0815/battery-manager-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/danielr0815/battery-manager-ha/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/danielr0815/battery-manager-ha.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.3.0+-blue.svg)](https://www.home-assistant.io/)

Simulation-based battery energy optimization for AC-coupled PV systems without
feed-in remuneration: the integration plans hourly energy flows over the full
forecast horizon and derives switching recommendations that minimize grid
import **and** wasted (exported) surplus.

New to the code base? Start with **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**
— the code map, the update cycle, and the glossary of the shorthand used in the
comments and design docs.

## How it works

Modelled topology (PV is AC-coupled; the battery charges through an AC→DC
charger and discharges through a DC→AC inverter; DC loads hang off the
battery):

```
PV ────────────────┐
                   ├── AC loads (house)          Grid
Grid ──────────────┤
                   │
             Charger (AC→DC)              Inverter (DC→AC, switchable)
                   │                               │
                   └────────── Battery ────────────┘
                                  │
                            DC loads (24 V rail)
```

Every ~5 minutes (and on input changes, debounced) the planner runs:

1. **Threshold search** — for every candidate SOC threshold the full horizon
   is simulated with the *actual* policy `inverter on ⇔ SOC > threshold`; the
   candidate with the lowest cost (grid import − terminal battery value +
   small export penalty) wins. "Make room before a sunny day, hold reserve
   before a cloudy one" emerges from the cost function on its own.
2. **Surplus allocation** — hours in which energy would be exported are
   assigned to configured surplus loads (powerstations, dehumidifier, …),
   in parallel when the surplus suffices, by priority when it does not.
   Every assignment is re-simulated over the *whole* horizon: a surplus load
   may never cause additional grid import and never violate the SOC buffer.
3. **Appliance handling** — a detected washer/dishwasher run adds its
   remaining consumption to the forecast; an optional advisor entity signals
   when a full run could start right now without any grid import.
4. **Emergency support** — if the battery would still fall through its
   minimum, the integration itself switches the configured grid PSUs
   (24 V DC/DC replacement, 48 V support PSU) as last-resort protection.

## Entities

| Entity | Meaning |
|---|---|
| `binary_sensor.…_inverter_recommendation` | Recommended state for the real discharge inverter (hysteresis + minimum switch interval applied) |
| `sensor.…_soc_threshold` | Optimal SOC threshold (%) from the search |
| `sensor.…_min/max_soc_forecast` | SOC range over the horizon |
| `sensor.…_hours_to_max_soc` | Hours until the maximum SOC is reached |
| `sensor.…_grid_import_forecast` | Expected grid import over the horizon (kWh) |
| `sensor.…_lost_surplus` | Surplus that will still be exported/wasted (kWh) |
| `sensor.…_soc_forecast` | Planned SOC curve (state: SOC in one hour; attributes: full plan for the bundled dashboard card) |
| `binary_sensor.<load>_recommendation` | Per surplus load: switch it on now (attributes: planned hours/energy) |
| `binary_sensor.<appliance>_start_window` | Per appliance: a full run fits into the surplus right now |
| `binary_sensor.…_24v/48v_grid_support` | State of the emergency support paths (switched directly by the integration) |

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → *Custom repositories* →
   `https://github.com/danielr0815/battery-manager-ha` (category *Integration*)
2. Download **Battery Manager**, restart Home Assistant.
3. Settings → Devices & Services → *Add Integration* → **Battery Manager**.

### Manual

Copy `custom_components/battery_manager/` into your `config/custom_components/`
directory and restart Home Assistant.

## Configuration

The base config flow asks for:

- **Input entities**: battery SOC sensor (%), three daily PV forecast sensors
  (kWh: today / tomorrow / day after tomorrow, e.g. from Solcast or
  Forecast.Solar).
- **System parameters**: battery (capacity, SOC limits, efficiencies), PV
  hourly distribution, AC/DC base load profiles, charger/inverter limits.
- **Planner tuning**: SOC safety buffer (default 5 %), hysteresis (±1 %),
  threshold inertia (2 %), minimum switch interval (60 s).
- **Emergency support** (optional): switch entities for the 48 V support PSU
  (fixed power, default 60 W) and the 24 V PSU replacing the DC/DC converter.

**Surplus loads** and **appliances** are added as sub-entries on the
integration card (*Add surplus load* / *Add appliance*):

- Surplus load: nominal power, allowed battery share (default 15 %),
  energy-limited flag with capacity + target SOC (powerstations), optional
  SOC / power-feedback / availability entities. Priority = creation order;
  loads run in parallel when the surplus suffices.
- Appliance: detection entity (power sensor or state), energy + duration per
  run, optional start-window advisor entity.

Automations then switch the real devices based on the recommendation
entities. Only the emergency support PSUs are switched by the integration
itself.

## Dashboard card (bundled)

The integration ships its own Lovelace card — no extra HACS frontend
download. It renders the planned SOC trajectory, the inverter threshold T*,
the reserve zone and the per-load surplus schedule, all from
`sensor.…_soc_forecast`.

The card registers itself automatically. To add it:

- **Easiest (HA 2026.6+):** edit a dashboard → *Add card* → pick the
  `…SOC forecast` sensor — **Battery Manager Forecast** appears as a
  suggestion with a live preview.
- Or search the card picker for *Battery Manager Forecast* (listed under
  *Community*).
- Or via YAML:

  ```yaml
  type: custom:battery-manager-forecast-card
  entity: sensor.battery_manager_soc_forecast
  # optional:
  # title: SOC-Prognose
  # hours: 48        # horizon shown (6–96)
  ```

If your dashboard **resources** are managed in YAML mode, the card module is
loaded globally instead; no manual resource entry is needed. After an
integration update, reload the browser page (or reset the frontend cache in
the companion app) if the card looks stale.

<details>
<summary>Alternative: ApexCharts card</summary>

The `forecast` attribute also works with the
[ApexCharts card](https://github.com/RomRider/apexcharts-card):

```yaml
type: custom:apexcharts-card
graph_span: 48h
span:
  start: minute
header:
  show: true
  title: SOC-Prognose
yaxis:
  - min: 0
    max: 100
series:
  - entity: sensor.battery_manager_soc_forecast
    name: SOC
    stroke_width: 2
    data_generator: |
      return entity.attributes.forecast.map(p => [new Date(p.t).getTime(), p.soc]);
```

</details>

## Documentation

| Document | What it covers |
|---|---|
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | **Start here.** Code map (core vs HA layer), the coordinator update cycle, and the decision-code glossary. |
| [docs/ALGORITHM.md](docs/ALGORITHM.md) | The planner: threshold search, surplus allocation, appliance advisor, emergency support. |
| [docs/DC_TOPOLOGY.md](docs/DC_TOPOLOGY.md) | The two-bus DC model, the 48 V voltage gate, and the R2/R3 support controllers (F-N3). |
| [docs/CONSUMPTION_FORECAST.md](docs/CONSUMPTION_FORECAST.md) | How the AC/DC consumption profile is learned from recorder history. |
| [docs/LOAD_CONTROL.md](docs/LOAD_CONTROL.md) | Direct charging-path control of surplus loads (powerstations). |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md), [docs/STRATEGY.md](docs/STRATEGY.md) | Historical design records (original requirements and rationale). |

## Development & testing

```bash
python -m venv .venv
# activate: `source .venv/bin/activate` (Linux/macOS/WSL) or
#           `.venv\Scripts\activate`     (Windows PowerShell)
python -m pip install homeassistant pytest pytest-homeassistant-custom-component ruff

# Core suite (pure Python, runs anywhere incl. Windows):
python -m pytest tests/core -p no:homeassistant

# Full suite incl. Home Assistant layer (needs Linux/WSL — the HA test
# helpers do not install on Windows):
python -m pytest tests

ruff check custom_components tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow (golden snapshots,
versioning) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

The simulation core (`custom_components/battery_manager/core/`) is free of
Home Assistant imports and side effects: frozen dataclasses in, frozen
dataclasses out. All planner behaviour is covered by scenario tests in
`tests/core/` — including a regression test for the historic bug where the
additional load was activated at night on the promise of tomorrow's sun.

### Charging-path control (powerstations)

A surplus load can optionally reference an input-plug switch and a
charge-enable entity; the integration then switches charging directly
(make-before-break with your passthrough-powered output loads, see
[docs/LOAD_CONTROL.md](docs/LOAD_CONTROL.md)). The last known SOC of a
sleeping powerstation is cached and survives restarts.

### Debug export

Service `battery_manager.export_hourly_details` writes the hourly plan of the
last run as an ASCII table (or JSON lines with `as_table: false`) to
`<config>/battery_manager_hourly_<entry_id>.txt`; `download: true` places it
under `/local/` with a notification link.

## License

MIT — see [LICENSE](LICENSE).
