# Battery Manager

Simulation-based battery energy optimization for AC-coupled PV systems **without
feed-in remuneration**. The integration plans hourly energy flows over the full
forecast horizon and derives switching recommendations that minimize grid import
**and** wasted (exported) surplus.

## What it does

- 🔎 **Threshold planning** — simulates the whole forecast horizon for every
  candidate discharge threshold and picks the one with the lowest cost
  (grid import − terminal battery value + a small export penalty).
- ☀️ **Surplus allocation** — schedules configured surplus loads (powerstations,
  dehumidifier, …) into hours that would otherwise be exported, re-simulating
  every assignment so it can never cause extra grid import.
- 🧺 **Appliance advisor** — signals when a full washer/dishwasher run fits into
  the surplus right now.
- 🔌 **Emergency grid support** — last-resort switching of the 24 V / 48 V
  support PSUs when the battery would fall through its minimum.
- 🧠 **Consumption learning** — learns the AC/DC load profile from recorder
  history, cleaning out its own controlled loads.
- 📈 **Bundled dashboard card** — a Lovelace card that renders the planned SOC
  curve, the threshold, the reserve zone, and the load/support schedule. No
  extra frontend download.

## Entities (overview)

Around nine entity families: the inverter recommendation, the SOC threshold,
min/max SOC and grid-import/lost-surplus forecasts, the planned SOC-forecast
curve, plus per-surplus-load and per-appliance recommendations and the state of
the 24 V / 48 V grid-support paths.

## Configuration

Configured entirely through the UI (**Settings → Devices & Services → Add
Integration → Battery Manager**). You provide a battery-SOC sensor and three
daily PV-forecast sensors, then the system parameters. Surplus loads and
appliances are added as sub-entries on the integration card.

## Requirements

- **Home Assistant** 2025.3.0 or newer
- **No** external Python dependencies (pure-Python planner core)

See the [README](https://github.com/danielr0815/battery-manager-ha) for the full
documentation, and [docs/ARCHITECTURE.md](https://github.com/danielr0815/battery-manager-ha/blob/main/docs/ARCHITECTURE.md)
to dive into the code.

## License

MIT — see the LICENSE file.
