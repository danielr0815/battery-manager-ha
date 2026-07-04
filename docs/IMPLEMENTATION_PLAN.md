# Battery Manager — Umsetzungsplan v0.2.0 (Phasen 0–3)

> Arbeitsdokument. Spec: [REQUIREMENTS.md](REQUIREMENTS.md),
> [STRATEGY.md](STRATEGY.md), [ALGORITHM.md](ALGORITHM.md).
> Alle Entscheidungen sind getroffen; Breaking Change ist freigegeben.

## Zielbild Dateien

```
custom_components/battery_manager/
├── __init__.py            # Setup, Plattformen, Service (angepasst)
├── config_flow.py         # NEU: Basis-Flow + Subentry-Flows (Lasten, Geräte)
├── const.py               # NEU: Konfig-Keys, Defaults
├── coordinator.py         # Überarbeitet: nutzt core/, Listener-Fix,
│                          #   schaltet Stützpfade direkt
├── sensor.py              # Sensoren aus PlanResult
├── binary_sensor.py       # NEU: Inverter-/Last-/Startfenster-/Stütz-Status
├── manifest.json          # version 0.2.0
├── translations/{en,de}.json
└── core/                  # NEU: HA-freier Kern (ersetzt battery_manager/)
    ├── __init__.py
    ├── model.py           # Frozen dataclasses (System, Lasten, Geräte, Stützpfade)
    ├── series.py          # Stundenreihen: PV-Verteilung, Lastprofile, Geräte-Restläufe
    ├── simulate.py        # step_hour() + simulate(): pure Funktionen
    └── optimize.py        # Schwellwertsuche, Überschuss-Allokation,
                           #   Geräte-Advisor, Stützpfad-Eskalation → PlanResult
tests/
├── conftest.py
├── core/test_simulate.py  # Bilanz-Invarianten (Energieerhaltung, SOC-Grenzen)
├── core/test_optimize.py  # Szenarien S1–S4 + Regression Fehlerszenario
├── core/test_series.py
└── ha/test_config_flow.py, test_coordinator.py, test_sensors.py
```

Alt-Code `custom_components/battery_manager/battery_manager/` und
`standalone_test/` werden ersatzlos entfernt (Logik geht in `core/` auf;
Szenarien wandern in `tests/`).

## Kern-Datenmodell (model.py)

- `BatteryParams(capacity_wh, soc_min, soc_max, eta_charge, eta_discharge)`
- `ConverterParams(max_w, eta, standby_w)` für Charger + Inverter
- `PVParams(peak_w, morning..., ratio)` (v1 wie Bestand)
- `LoadProfile(base_w, variable_w, start_hour, end_hour)` AC/DC
- `SurplusLoad(id, name, power_w, battery_tolerance=0.15, min_runtime_min=30,
   priority, energy_needed_wh|None, soc_entity?, power_entity?, target_soc)`
- `Appliance(id, name, energy_kwh, duration_h, may_start_opportunistically)`
- `SupportPaths(dc48_power_w=60, dc48_switch_entity, dc24_switch_entity)`
- `ControlParams(soc_buffer=5.0, hysteresis=1.0, threshold_inertia=2.0,
   export_tiebreak=0.05, min_switch_interval_s=60)`
- `PlanInputs(now, start_soc, pv_hourly, ac_hourly, dc_hourly,
   appliance_runs, load_states)` — von series.py gebaut
- `PlanResult(threshold, inverter_on, min_soc, max_soc, hours_to_max,
   import_kwh, export_kwh, lost_surplus_kwh, load_schedules{id: [bool]},
   appliance_windows{id: bool}, support_dc48, support_dc24, trajectory)`

## Algorithmus (optimize.py) — wie ALGORITHM.md §1

1. Schwellwertsuche: Kandidaten `[max(soc_min+buffer, inv_min) … soc_max]`,
   Kosten `import − 0.92·end_soc_wh + 0.05·export`; Gleichstand → NIEDRIGERE
   Schwelle („Nutzen", D-A1b).
2. Lastallokation auf Gewinner-Trajektorie: Export-Stunden, Priorität,
   parallel; Toleranz je Last (Batterieanteil der Laststunde ≤ tolerance);
   Re-Simulation prüft Z2/Z3 über ganzen Horizont; Sättigung via
   energy_needed_wh.
3. Geräte-Advisor: Testeinfügung Komplettlauf ab jetzt → Import-Delta 0?
4. Stützpfad-Eskalation: Fällt SOC in Trajektorie (mit Lasten) unter
   `soc_min + buffer` trotz Inverter aus → `support_dc24 = true` für
   betroffene Stunden; reicht das nicht → zusätzlich `support_dc48 = true`.
   Ausgabe für Stunde 0 → Coordinator schaltet real.
5. Hysterese-Zustand (letzter Schaltzustand + Zeitstempel, letzte Schwelle)
   lebt im Coordinator, nicht im Kern (Kern bleibt pur).

## HA-Schicht

- **Coordinator:** `_listeners_setup = True` nach Setup; Unsubscribe-Handles
  speichern und in `async_unload_entry` aufräumen. Zusätzlich abonnieren:
  Fossibot-SOC/-Leistung, Geräte-Erkennungs-Entitäten, Stütz-Switch-Zustände.
  Direkte Schaltung: `switch.turn_on/off` für Stützpfade (nur bei
  Zustandswechsel, min. Intervall).
- **Config Flow v2:** Basis-Steps (Entitäten, Batterie, PV, Profile,
  Charger/Inverter, Regelung inkl. Stützpfade); Subentries `surplus_load`
  und `appliance` (HA ≥ 2025.3 ConfigSubentryFlow).
- **Entitäten:** bestehende Sensoren behalten (IDs stabil wo möglich) +
  `binary_sensor` je Last (`empfehlung`), je Gerät (`startfenster`),
  2× Stützpfad-Status, `sensor.lost_surplus_kwh`.

## Reihenfolge

1. **P0** Coordinator-Listener-Fix (am Bestand), pytest-Setup, CI + ruff ✅
2. **P1** `core/` komplett + Tests (Szenarien aus Prototyp übernommen) ✅
3. **P1b** Coordinator/Sensoren auf core umgestellt, alte Kern-Lib gelöscht ✅
4. **P2** Subentries Lasten + Last-Entitäten + Feedback (Fossibot) ✅
5. **P3** Geräte (Erkennung, Restlauf, Startfenster) + Stützpfad-Schaltung ✅
6. Übersetzungen de/en, README-Update, Version 0.2.0, CHANGELOG ✅

## Status (2026-07-04)

- 30 Kern-Tests grün (`pytest tests/core -p no:homeassistant`), ruff sauber,
  alle HA-Module importieren gegen HA 2026.2.3.
- **Offen:** HA-Schicht-Tests (tests/ha/) laufen nur auf Linux (fcntl-Import
  in HA) → CI (validate.yml, Job „tests") oder lokal via WSL2. WSL-Installation
  auf dem Dev-Rechner erfordert Admin/Neustart (`wsl --install -d Ubuntu`).
- **Offen:** Deployment auf http://hass:8123 (Kopie nach
  /config/custom_components via Samba/SSH) und Praxis-Validierung über
  mehrere Tage.
- **Offen:** Weitere HA-Tests (Config Flow inkl. Subentries, Sensor-Werte,
  Stützpfad-Schaltung) ergänzen; PROJECT_COMPLETE.md/STARTUP_OPTIMIZATION.md
  sind v0.1-Altdokumente und könnten archiviert werden.
