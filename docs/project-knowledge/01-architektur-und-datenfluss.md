# Architektur & Datenfluss

Seit v0.26.0 ergänzt `core/cascade.py` den Root-Allokator um gemeinsame
Storage-SOC- und Recovery-Pläne. `cascade_manager.py` ist alleiniger
HA-Actor-Besitzer; interne Flüsse werden nie in die Root-Bilanz addiert. Siehe
[`../CASCADE-STORAGE-DESIGN.md`](../CASCADE-STORAGE-DESIGN.md).

**Stand: `main` @ v0.17.0 (Arbeitsstand 2026-07-31).** Dieses Dokument ist der Code-Wegweiser
für die Integration `battery_manager`: welches Modul ist wofür zuständig, in
welcher Reihenfolge läuft ein Update-Zyklus, woher kommt jeder Eingangswert und
was überlebt einen Neustart. Planer-Interna stehen in
`02-planner-und-optimizer.md`, die Schalt- und Latch-Logik in
`03-executor-und-lastregeln.md`.

Alle Aussagen sind gegen den Code geprüft. Belege werden als *Datei +
Funktions-/Konstantenname* genannt (Zeilennummern altern zu schnell).
**Im Zweifel gilt der Code.**

---

## 1. Was die Integration ist

| Feld (`manifest.json`) | Wert | Bedeutung |
|---|---|---|
| `domain` | `battery_manager` | Entity-Präfix, Service-Namespace, Storage-Key-Präfix |
| `version` | `0.16.2` | HACS-Release (HACS trackt **Releases**, nicht Commits) |
| `integration_type` | `device` | alle Entities hängen an EINEM Geräte-Eintrag je Config-Entry (`entity.BatteryManagerEntity`) |
| `iot_class` | `local_polling` | liest lokale HA-Entities, kein Cloud-Call |
| `config_flow` | `true` | Einrichtung nur über die UI; Lasten/Appliances als **Subentries** |
| `dependencies` | `http` | für den Static-Path der mitgelieferten Lovelace-Karte |
| `after_dependencies` | `frontend`, `lovelace`, `recorder` | Karte + Recorder-Historie fürs Profil-Lernen |
| `requirements` | **leer** | reine stdlib; kein numpy/pandas |

Die harte Architekturgrenze: **`custom_components/battery_manager/core/`
importiert nichts aus `homeassistant`** — verifiziert, kein einziger
`homeassistant`-Import in `core/*.py`. Der Kern ist reine Funktion über frozen
dataclasses: `plan(config, inputs) -> PlanResult`, ohne geteilten mutablen
Zustand, ohne IO, ohne Logging-Nebenwirkungen. Alles HA-Spezifische
(Entity-Lesen, Schalten, Latches, Persistenz, Lernen) liegt in
`coordinator.py`.

Diese Grenze ist praktisch wichtig: der Coordinator ruft `plan` **zweimal** pro
Zyklus, wenn ein F11-Latch-Kandidat existiert (der Schatten-Plan,
`coordinator._latch_shadow_active`), und darf das nur, weil `plan` garantiert
seiteneffektfrei ist.

---

## 2. Modul-Landkarte

### 2.1 Reiner Kern: `custom_components/battery_manager/core/`

| Modul | Verantwortung | Einstiegspunkte |
|---|---|---|
| `model.py` | **Alle Datenverträge** als frozen dataclasses: Systembeschreibung (`BatteryParams`, `ConverterParams`, `PVParams`, `LoadProfile`, `SurplusLoad`, `Appliance`, `SupportParams`, `ControlParams`, `SystemConfig`), Eingaben (`HourSlot`, `PlanInputs`, `SurplusLoadState`, `ApplianceRun`), Ergebnisse (`HourFlows`, `Trajectory`, `LoadPlan`, `PlanResult`). Enthält die einzige Logik, die Zustand in Planungsleistung übersetzt: `SurplusLoadState.planning_power_w`, `.remaining_energy_wh`, `LoadPlan.active_run_hours`. | `SystemConfig`, `PlanInputs`, `PlanResult` |
| `simulate.py` | Physik: ein Slot Energiefluss (`step_hour`) und die Horizont-Schleife (`simulate`). PV → AC-Seite, Batterie lädt **nur** über den AC→DC-Charger und entlädt **nur** über den DC→AC-Inverter; DC-Lasten hängen an der Batterie. Enthält das F-N3-Zwei-Bus-Modell (24 V-Schiene / natives 48 V) und die Support-PSU-Pfade. | `step_hour`, `simulate` |
| `optimize.py` | **Der Planer** (größte Kerndatei): T*-Suche, Merge-Probe + Terminal-Credit-Rampe, Zwei-Pass-Lastallokation mit allen Gates, Appliance-Advisor, Support-Eskalation, Diagnostik-Aufbau. Beherbergt auch die Kern-Konstanten `GATE_TOPUP_MIN_WH`, `QUANTILE_RATIO_MIN_WH`, `IMPORT_ARTIFACT_SLACK_WH`, `MERGE_TERMINAL_RAMP_WH`. | `plan`, `search_threshold`, `allocate_loads`, `quantile_band_slots` |
| `series.py` | Baut aus Tagesprognosen + optionaler Stundenprognose + Lastprofilen die Slot-Reihe. Definiert das **Slot-Raster** (`slot_starts`: erste Stunde partiell, dann volle Stunden bis Mitternacht nach dem letzten Prognosetag) — Single Source of Truth, damit Aufrufer nie über Slot-Anzahl/Index streiten. | `build_slots`, `slot_starts`, `insert_appliance_run` |
| `forecast_hours.py` | Reduziert rohe `wh_period`-Buckets (15-min oder stündlich) auf eine naive-lokale Stunde→Wh-Map und berechnet den Tages-Residual für unabgedeckte Stunden. | `aggregate_hours`, `coverage_and_residual` |
| `load_profile.py` | Reine Mathematik der Verbrauchsprofile: Tagestypen (`DAY_TYPE_WEEKDAY/WEEKEND/ABSENCE`), Quantile (`QUANTILE_KEYS` = p50/p80), gewichtete Quantile, Tages-Cleaning/Balancing, Bin-Aggregation. | `weighted_quantile`, `profile_value`, `aggregate_bins`, `day_type` |
| `power_learning.py` | **F-ROBUST-POWER**: zeitgewichteter Fenster-Median der Ist-Leistung einer Last mit Warm-up, Dominanz-Bar und Fast-Adopt. Ersetzt den früheren EMA/Run-Max. | `robust_power_estimate`, `PowerEstimate` |
| `__init__.py` | Re-Export-Fassade des Kerns (`plan`, `simulate`, `build_slots`, alle Dataclasses, die Profil-Helfer). | – |

### 2.2 HA-Schicht: `custom_components/battery_manager/`

| Modul | Verantwortung | Einstiegspunkte |
|---|---|---|
| `coordinator.py` | **Das Herz** (~4730 Zeilen, Stand v0.17.0). Liest Eingänge, baut `SystemConfig`/`PlanInputs`, ruft den Kern, wendet Trägheit/Hysterese an, fährt Floor-Guard, Support-Schaltung, Lastschaltung, Power-Warnings, Tank-Prognose; hält sämtliche Latches und den persistierten Zustand. | `BatteryManagerCoordinator._async_update_data`, `build_system_config`, `_apply_load_switching` |
| `__init__.py` | Plumbing: Coordinator verdrahten, `sw_version` aus dem Manifest lesen, Persistenz laden, Plattformen forwarden, die zwei Export-Services registrieren, Entry-Migration (v1→v2, minor 2/3), sauberes Unload mit Flush. Export-Pfade (v0.17.0): `<config>/battery_manager/`, Downloads `<config>/www/battery_manager/` mit 1-h-TTL + Start-Sweep; nur `.txt`/`.json`, `.storage` gesperrt, Fehler als Service-Fehler. | `async_setup_entry`, `async_migrate_entry`, `_async_setup_card` |
| `const.py` | Single Source of Truth für Config-Keys, Defaults (`DEFAULT_CONFIG`, `DEFAULT_LOAD_CONFIG`) und alle HA-seitigen Tunables. Re-exportiert `GATE_TOPUP_MIN_WH` und `MERGE_TERMINAL_RAMP_WH` aus `core/optimize.py` (deren Konsument ist der pure Kern, deshalb wohnen sie dort). | (nur Konstanten) |
| `config_flow.py` | Config- und Options-Flow inkl. Subentry-Flows je Lastklasse und Appliance. Validiert Querbedingungen (soc_min < soc_max, PV-Fenster-Ordnung, buffer_min < buffer_max, `keep_on` ohne Charge-Enable). | `async_step_user`, Subentry-Flow-Handler |
| `history_profile.py` | **Verbrauchslernen** (`ProfileLearner`): nächtlicher Lauf um `LEARNING_RUN_HOUR = 3`, liest Recorder-Statistiken, bereinigt Tage, subtrahiert Zusatzlasten (`in_house`-Semantik!), bildet p50/p80-Bins je (Pfad × Tagestyp × Stunde), rate-limitiert Änderungen, betreibt einen Bias-Watchdog. Eigener Store. | `ProfileLearner.async_run_learning`, `profiles_for_planning`, `planning_daytype`, `diagnostics` |
| `sensor.py` | Sensor-Plattform: Schwelle, Min/Max-SOC-Prognose, Stunden bis Max, Import-/Lost-Surplus-Prognose, die zwei Support-Modus-Sensoren, der große `soc_forecast`-Kurvensensor (Attribut-Vertrag) und je Last ein Runtime-Sensor (mit Tank-Attributen). | `async_setup_entry`, `BatteryManagerSocForecastSensor`, `SurplusLoadRuntimeSensor` |
| `binary_sensor.py` | Inverter-Empfehlung (Attribute: Schwelle, `floor_guard_active`), je Last eine **Empfehlung** (`SurplusLoadRecommendationSensor`) und eine Power-Warnung (`LoadPowerWarningSensor`), je Appliance ein Startfenster, je Support-Pfad ein Aktiv-Sensor. | `async_setup_entry` |
| `switch.py` | Urlaubs-/Abwesenheitsmodus, je Support-PSU ein Manual-Override-Switch, je Last der „BM-Steuerung aktiv"-Switch (`SurplusLoadControlSwitch`). | `async_setup_entry` |
| `button.py` | Je Last ein Runtime-Reset-Button (`SurplusLoadRuntimeResetButton`) — der zugleich „Tank geleert" bedeutet und einen F-L7-Latch löst. | `async_press` |
| `entity.py` | Gemeinsame Basisklasse mit `DeviceInfo` (sw_version aus dem Manifest), `ensure_devices` (Haupt-Gerät + ein Gerät je Subentry mit `via_device_id` — HA 2026.8: ein Gerät je Config-Subentry, core PR #175785) und der Subentry-Zuordnungshilfe `async_add_by_subentry` (v0.7.19: Entities werden mit ihrer Subentry gelöscht). | `BatteryManagerEntity`, `ensure_devices`, `async_add_by_subentry` |
| `diagnostics.py` | Diagnose-Download: Entry-Konfiguration (redigiert), letzte Plandaten, Lernzustand (`coordinator.learned_state_snapshot`), Controller-Diagnose. | `async_get_config_entry_diagnostics` |
| `debug_utils.py` | Tabellenformatierung für die beiden Export-Services. | `format_hourly_details_table`, `format_learned_profiles_table` |
| `frontend/` | Die mitgelieferte Lovelace-Karte `battery-manager-forecast-card.js` (versionierte URL, Auto-Registrierung als Resource). | – |
| `services.yaml`, `translations/` | Service-Beschreibungen und Übersetzungen (de/en). | – |

Plattformen laut `__init__.PLATFORMS`: `binary_sensor`, `button`, `sensor`,
`switch` (+ Diagnostics). Details zu Entities und Attributen:
`04-ha-integration-entities-services.md`.

---

## 3. Der Update-Zyklus

### 3.1 Kadenz — was WIRKLICH gilt

| Konstante (`const.py`) | Wert | Wirkung |
|---|---|---|
| `INITIAL_UPDATE_INTERVAL_SECONDS` | **30 s** | Poll-Intervall beim Start |
| `UPDATE_INTERVAL_SECONDS` | **300 s** | Poll-Intervall im Normalbetrieb |
| `DEBOUNCE_SECONDS` | **5 s** | Entprellung für ereignisgetriebene Refreshes |
| `STARTUP_RETRY_ATTEMPTS` | 5 | Obergrenze der Startphase |
| `STARTUP_SOC_GRACE_S` | 120 s | Karenz für eine noch nicht bootende SOC-Quelle |

Der Coordinator startet mit 30 s und schaltet auf 300 s um, sobald
`_successful_updates >= 2` (oder `>= STARTUP_RETRY_ATTEMPTS`) — siehe
`_async_update_data`, Block `self._startup_complete`.

**Achtung, häufiges Missverständnis:** der Zyklus läuft im Normalbetrieb
**alle 5 Minuten**, nicht alle 10–30 Sekunden. Häufiger wird er nur durch
Zustandsänderungen der beobachteten Entities: `_setup_entity_listeners`
registriert `async_track_state_change_event` auf `_tracked_entities()` (SOC,
die drei PV-Prognose-Entities, Support-Switches + DC/DC, je Last
SOC-/Power-/Verfügbarkeits-Entity, je Appliance die Detection-Entity, seit
v0.23.0 die Feed-in-Batterieleistung und seit v0.24.0 der Exportzähler plus je
Last der optionale Energiezähler — damit die Ist-Bilanz-Deltas zeitnah gebucht
werden statt erst im 5-Minuten-Zyklus); jeder
Event löst über `_handle_entity_change` → `_debounced_update` nach 5 s einen
Refresh aus. Die Batteriespannung ist **bewusst nicht** getrackt (analoges
Rauschen würde permanent replanen; der R2-Regler läuft auf dem Poll).

### 3.2 Reihenfolge in `_async_update_data`

Exakt wie implementiert (jeder Schritt hängt am Ergebnis des vorigen):

1. **`now = dt_util.now()`** (lokal, tz-aware). Beim allerersten Refresh wird
   `_startup_at` gesetzt (Anker der V8-SOC-Karenz).
2. **`_update_load_runtime(now)`** — Laufzeitzähler je Last fortschreiben,
   *vor* jedem Early-Out, damit er lückenlos tickt.
3. **`_update_support_modes()`** — Manual-Override-Erkennung der Support-PSUs
   (F-N2). Muss vor `build_system_config` laufen, weil die `forced_on`-Flags in
   die Simulation eingehen.
4. **Eingänge lesen:** `_get_soc(now)`, `_get_forecasts(now)`. Fehlt eines →
   `UpdateFailed`, außer die V8-Karenz greift (siehe §4.1).
5. **`build_system_config()`** — Entry-Daten + Subentries → `SystemConfig`
   (Lasten in Prioritätsreihenfolge, `ordered_load_subentries`).
6. **`_get_load_states(now)`** — je Last Verfügbarkeit, SOC (+Cache),
   Median-Leistungsschätzung, G2-/F4-Stale-Latches, F5-`saturated_power_w`.
7. **`_get_appliance_runs(now)`** — laufende Haushaltsgeräte mit Restenergie.
8. **`_learned_series(...)`** — gelernte AC/DC-Wattreihen (p50), das p80−p50-Band
   und die Profil-Diagnostik.
9. **`_get_pv_hourly(now)`** — stündliche PV-Map + p10/p90-Maps;
   `_pv_day_sources` protokolliert je Tag `hourly` vs. `two_window`.
10. **`build_slots(...)`** → `PlanInputs`. Der Kern rechnet **naiv-lokal**:
    `now.replace(tzinfo=None)` wird übergeben.
11. **Dynamischer SOC-Buffer (D-C8):** nur wenn `quantiles_active`, ersetzt
    `_dynamic_buffer` den festen `soc_buffer_percent` per
    `dataclasses.replace`. Die Grid-Support-Schwellen sind davon **nicht**
    betroffen (absolute SOC-Werte).
12. **`plan(config, inputs)`** im Executor-Thread
    (`hass.async_add_executor_job`) → `PlanResult`.
13. **`_update_plan_active(result)`** — Lern-Erlaubnis je Last an der
    OFF→ON-Flanke schnappschussen (`_load_learn_ok`).
14. **`_update_predrain_block_evidence(...)`** — Stabilitäts-Gate
    (F-PREDRAIN-BLOCK R7): zählt je Last aufeinanderfolgende identische
    Pass-3-Block-Signaturen (≥ 3 Pläne + ≥ 10 min alt → aktuierbar).
15. **`_log_night_predrain(...)`** — eine INFO-Zeile, wenn sich die
    Pass-3-Block-Buchung ändert.
16. **`_apply_hysteresis(soc, threshold, ...)`** — Inverter-Empfehlung mit
    ±`hysteresis_percent` (Default 1 %) und `min_switch_interval_s` (60 s).
17. **`_update_floor_guard(soc, config, pv_power_w, running_load_power_w, now)`**
    — G4. Muss **nach** der Hysterese laufen (liest die frische Empfehlung) und
    **vor** dem Lastschalten (damit derselbe Zyklus schon greift).
18. **`_apply_support_switching(result, config, now)`** — 24 V/48 V-PSUs.
19. **`_run_dc48_controller(now)`** — R2-Spannungsregler im Manual-Modus.
20. **`_latch_shadow_active(config, inputs, load_states)`** — F11-Schatten-Plan,
    **nur** wenn ein Latch-Kandidat existiert.
21. **`_apply_load_switching(result, now, slot_durations, pv_power_w,
    shadow_active)`** — der eigentliche Schaltpfad (Dokument 03).
22. **`_update_power_warnings(result, now)`** — F-L7-Latch setzen/lösen.
23. **`_update_feedin_mode(now)`** + **`_apply_feedin(result, config, soc, now)`** —
    F-FEEDIN (v0.23.0): Manuell-Modus-Erkennung des Einspeise-Sollwerts und
    der zweiphasige Setpoint-Executor (Dokument 03 §11A).
24. **`_update_tank_forecast(result, now)`** — Tank-Diagnostik + „Tank bald
    voll"-Push (nach den Warnungen, deren Latch-Flanken die Tank-Events sind).
25. **`_update_gate_calibration(config, soc)`** — 48 V-Gate-Kalibrierbracket.
26. **Startphase abschließen**, dann das `data`-Dict bauen (Lastpläne,
    `soc_forecast`, `daily_surplus`, `realized`, Diagnostik, `hourly_details`).
27. **`_update_realized_surplus(now, daily_surplus)`** — F-REALIZED-SURPLUS
    (v0.24.0): die gemessenen Tageszähler aus Exportzähler und
    Last-Energiezählern, gebucht **innerhalb** des Dict-Baus, direkt nachdem
    `daily_surplus` steht. Läuft **zuletzt**, weil es die frischen
    Laufzeitzähler (Schritt 2), das Feed-in-Integral (Schritt 23) und eben die
    fertige Tages-Prognose (für den Ist+Prognose-Mix) braucht. Nur auf
    **erfolgreichen** Zyklen — ein `UpdateFailed` bucht nichts, die
    Zählerstände tragen in den nächsten Zyklus. Ohne Exportzähler liefert es
    `None` und der `realized`-Schlüssel fehlt im Dict.

**Wichtige Reihenfolge-Eigenheit:** die Latches, die der Planer im *nächsten*
Zyklus liest (F-L7-Power-Warning → F5 `saturated_power_w`, Tank-Zustand), werden
am **Ende** des Zyklus aktualisiert. `_get_load_states` liest sie also
absichtlich eine Runde alt — das ist im Code so dokumentiert und kein Bug.

---

## 4. Die Eingangsdaten

### 4.1 Batterie-SOC

`_get_soc` liest `CONF_SOC_ENTITY` und akzeptiert nur `0.0 <= v <= 100.0`.
Ein gültiger Wert aktualisiert `_last_valid_soc`/`_last_soc_update`. Fehlt er,
wird der letzte gültige Wert bis `MAX_HISTORICAL_SOC_AGE_HOURS = 6` (h)
weiterverwendet, danach `None`. (Die früheren, ungenutzten Alters-Konstanten
für den SOC-/Prognose-Cache wurden mit v0.17.0 entfernt; es gelten nur noch
die beiden `MAX_HISTORICAL_*`-Grenzen.)

Zwei Eskalationen hängen an diesem Pfad (v0.17.0): der **energiebasierte,
band-abhängige Haus-SOC-Stale-Wächter** (`_update_house_soc_watchdog`, seit
v0.18.0) erkennt einen mit frischen Zeitstempeln eingefrorenen SOC — erst,
wenn der akkumulierte erwartete Batteriedurchsatz die Drift-Toleranz des
SOC-Bands übersteigt (Mitte 13–89 %: `house_soc_stale_mid_percent`, Standard
3 % der Kapazität = 2 % Drift + 1 % Anzeige-Quantisierung, siehe §5.3 in
05-anlage-und-betrieb-runbook.md; Ränder < 13 % / > 89 %:
`house_soc_stale_edge_percent`,
Standard 7 %, weil BMS-Plateaus dort normal sind) — und lässt `_get_soc`
`None` liefern, und bei anhaltendem Datenverlust feuert die **D-A8-Stufe-2**
nach `STALE_LOAD_SHED_HOURS = 2` h einmalig das Fail-safe-Abschalten aller
gesteuerten Zusatzlasten (`_note_data_loss`/`_execute_stale_load_shed`,
Repair-Issue `stale_data_load_shed` + Push, Latch bis zur Recovery —
Details: `docs/ALGORITHM.md` D-A8 und `docs/LOAD_CONTROL.md` §15/§16).

`None` führt normalerweise zu `UpdateFailed("No valid input data available
(SOC)")`. **V8-Ausnahme:** innerhalb `STARTUP_SOC_GRACE_S = 120` s nach dem
ersten Refresh und solange noch **kein** erfolgreicher Zyklus lief
(`_first_success_done`), wartet der Coordinator still (DEBUG) und liefert
entweder den vorigen gültigen Zustand oder `{"valid": False,
"startup_pending": True}`. `_first_success_done` latcht dauerhaft — ein SOC-
Ausfall nach dem ersten Erfolg fällt nie in die Karenz zurück.

### 4.2 PV-Prognose

Drei Entities (heute/morgen/übermorgen), Zustand = **kWh/Tag**:
`_get_forecasts` verlangt alle drei; sonst greift der Cache bis
`MAX_HISTORICAL_FORECAST_AGE_HOURS = 72`.

Zusätzlich liest `_read_wh_period` je Entity drei Attribute:

| Attribut | Bedeutung |
|---|---|
| `wh_period` | Median-Stundenkurve (Buckets 15/30/60 min werden per `aggregate_hours` je Stunde summiert) |
| `wh_period_p10` | Pessimistisches Band (F-QUANTILE-BANDS) |
| `wh_period_p90` | Optimistisches Band |

Parse-Regeln: Keys über `dt_util.parse_datetime`; naive Keys gelten als
**lokal**, aware Keys werden nach lokal konvertiert und naiv gemacht. NaN/±inf
werden verworfen, negative Werte auf 0 geklemmt (FIX-9).

**AC-Skalierung (Live-Audit 31.07.–07.08.2026):** eine Quelle kann
`wh_period` als **DC-Modellkurve** liefern, während ihr State die
**AC**-Tagesenergie ist (balcony_solar_forecast: Σ Buckets == sein
`..._today_dc`-State; AC-State = Σ × 0,9215 gelerntes Wirkungsgrad-Skalar) —
unverändert übernommen plante die AC-seitige Simulation ~8,5 % zu viel PV
ein (07.08.: 0,7 kWh vermeidbarer Netzbezug). `_pv_curve_ac_factor` leitet
darum pro Entity η = State(Wh) / Σ(Kurve) ab und `_get_pv_hourly` skaliert
Median **und** beide Bänder mit demselben η, bevor der FIX-4-Cache die
bereits skalierten Maps speichert (kein doppeltes Skalieren beim Replay).
Konsistente Quellen erhalten η ≈ 1 (No-op); η wird auf [0,5; 1,0] geklemmt
(`PV_CURVE_AC_ETA_MIN`); nicht ableitbares η (State fehlt/ungültig/≤ 0 oder
Einheit unbekannt — bewusst kein kWh-Fallback wie bei `_energy_unit_factor`)
bleibt 1.0. Bietet eine Entity explizite AC-Attribute (`wh_period_ac` /
`wh_period_ac_p10` / `wh_period_ac_p90`), werden diese **stattdessen**
gelesen und nie skaliert.

Semantik-Nuance der BSF-Implementierung (Order-Antwort 2026-08-07): die
AC-Kurve ist DC × η je Wechselrichtergruppe **plus AC-Clamp am Gruppenlimit**
(geklampfte Mittags-Slots liegen also unter η × DC), und die Summen-Invariante
Σ == AC-State gilt nur für tomorrow/day_after — die **Heute**-Kurve behält
den Intraday-Skalar als Live-Nowcast, während ihre Headline day-ahead-stabil
bleibt. Für den BM folgt daraus: nach dem BSF-Update plant er die
Heute-Slots mit dem Nowcast statt mit der auf die Headline normalisierten
Kurve — gewollt und genauer, aber ein bewusster Semantikwechsel am Übergang.

`_get_pv_hourly` cached **pro Entity** `(median, p10, p90, timestamp)` in
`_pv_hourly_by_entity` — d.h. Median und Bänder stammen garantiert aus
demselben Lesevorgang, und eine einzelne unavailable Entity überschreibt die
Gesamtmap nicht mit einem Teilergebnis (FIX-4).

Modus `CONF_PV_FORECAST_MODE` (`PV_FORECAST_MODE_AUTO` ist der Default):

- `auto` — stündlich wenn vorhanden, sonst das Zwei-Fenster-Modell;
- `hourly` — heute identisch zu `auto`, warnt aber **einmalig**
  (`_maybe_warn_hourly_empty`), wenn keine `wh_period`-Daten da sind;
- `daily` — Stundenattribute werden komplett ignoriert (Golden-Anker), keine
  Bänder, `build_slots` nutzt das Zwei-Fenster-Modell.

**Slot-Befüllung** (`series._pv_wh_hourly`): eine Stunde mit Bucket bekommt
`bucket * duration`; eine unabgedeckte Stunde bekommt ihren
Zwei-Fenster-**Anteil am Tages-Residual** (`coverage_and_residual`). Es wird
**nicht** über die verbleibenden Slots renormiert (FIX-3) — ein teilweise
abgedeckter Tag unterfüllt bewusst, statt zu überfüllen. Danach greift der
Peak-Cap `pv.peak_power_w * duration`.

**Quantil-Bänder** (`series._quantile_wh`): rein per Slot, kein Residual-Fill,
kein Zwei-Fenster-Fallback — ein Band existiert nur auf Evidenz. Ob ein Slot
**wirksam** ein Band hat, entscheidet allein `optimize.quantile_band_slots`
(Details in Dokument 02, §3): p10 und p90 vorhanden **und** `pv_wh >=
QUANTILE_RATIO_MIN_WH = 25.0` **und** `p90 - p10 > max(1.0, 0.01 * pv_wh)`.
Ein **kollabiertes** Band (p10 == p90, die Cold-Start-Signatur des
Balkon-Forecasters) zählt als *kein* Band.

**α/β-Fallback:** wo kein Band greift, gelten die Skalare
`predrain_pv_confidence` (α, Live-Default `PREDRAIN_PV_CONFIDENCE_DEFAULT =
0.5`) und `upper_pv_reserve` (β, Default `UPPER_PV_RESERVE_DEFAULT = 1.2`).
Achtung auf die Doppel-Defaults: die **Dataclass**-Defaults in
`model.ControlParams` sind neutral (1.0/1.0, damit die Goldens eingefroren
bleiben), die **Live**-Defaults stehen in `const.DEFAULT_CONFIG`. Der
Coordinator setzt sie in `build_system_config` explizit.

Die Reifung der Bänder ist über `_quantile_coverage` je Tag beobachtbar:
Anteil der Tageslicht-Slots (`pv_wh > 0`) mit Band, plus Label
`scalar` / `mixed` / `p10/p90`.

### 4.3 Gelernte Verbrauchsprofile

`history_profile.ProfileLearner` lernt aus der Recorder-Historie
Stunden-Mittelwerte je Pfad (`ac`, `dc`) × Tagestyp × Stunde, als Quantile
**p50 und p80** (`LEARNED_STORE_VERSION = 2`). Nächtlicher Lauf um
`LEARNING_RUN_HOUR = 3`, Fenster `CONF_LEARNING_WINDOW_DAYS` (Default 120 Tage)
mit Recency-Gewichtung `CONF_PROFILE_HALF_LIFE_DAYS` (30 Tage), Mindest-Samples
`LEARNING_MIN_SAMPLES = 10` (Absenz-Bins: 5), Änderungsrate pro Lauf begrenzt
auf `LEARNING_RATE_LIMIT = 0.2`, Plausibilitätsklemmen
`LEARNING_CLAMP_AC_W = 3000` / `LEARNING_CLAMP_DC_W = 1000`.

`_learned_series` mappt die Bins per **absoluter verstrichener Zeit** auf das
Slot-Raster (DST-korrekt: die übersprungene Stunde entfällt, die doppelte nutzt
ihren Bin erneut) und liefert vier Dinge: die AC-Reihe, die DC-Reihe, das
p80−p50-Band je Slot und `quantiles_active`. Ein `None` in der Reihe fällt
slotweise auf das statische Profil zurück (`series._series_value`); im
Urlaubsmodus fällt ein ungültiger Absenz-Bin bewusst auf die **Basislast ohne
variablen Anteil** zurück (nicht auf `None`, das würde `variable_w` wieder
addieren).

Das Band speist `_dynamic_buffer`: über das kritische Fenster (jetzt bis zum
ersten Slot mit PV-Überschuss) wird die Unsicherheit in Wh umgerechnet — AC
über `1/(eta_discharge * eta_inverter)`, DC nur über `1/eta_discharge` — und
als SOC-Prozent auf `[CONF_BUFFER_MIN_PERCENT, CONF_BUFFER_MAX_PERCENT]`
(3 % … 15 %) geklemmt.

**`in_house_measurement` (CONF_LOAD_IN_HOUSE, Default `True`)** — die
meistmissverstandene Option. Sie betrifft **ausschließlich** das
Grundlast-Lernen in `history_profile.py` (`_subtractions`, `_fetch_days`):

- `True`: die Last hängt **hinter** dem Messknoten (EM540/Netzzähler), ihr
  Verbrauch steckt also in der gemessenen Hauslast → der Lerner **subtrahiert**
  ihn.
- `False`: die Last wird **außerhalb** des gemessenen Knotens versorgt (per
  Einspeise-Sollwert oder schlicht auf einem Stromkreis, den der Zähler nicht
  sieht — der Kellerentfeuchter, dessen Versorgung am EM540 1:1 als „Export"
  erscheint) → **nicht** subtrahieren, sonst wird die Grundlast doppelt
  bereinigt und systematisch zu niedrig gelernt.

Die Planung berührt das Flag **nicht**: eine Überschusslast ist in beiden
Fällen zusätzliche AC-Last (`extra_ac_wh` in `optimize.allocate_loads`), also
genau einmal gezählt. Auch der G4-PV-Vergleich und die gelernte
Ist-Leistung sind topologieunabhängig (rohe Watt des Geräts).

### 4.4 Appliance-Runs

`_appliance_is_running` erkennt einen Lauf mit Hysterese: Start bei
`power_threshold_w`, Halten bis unter `off_threshold_w` (Default = der
On-Schwelle, also ohne Hysterese, solange nichts Kleineres konfiguriert ist);
nichtnumerische Entities gegen `APPLIANCE_RUNNING_STATES`. Bei Entity-Dropout
wird der letzte gelatchte Zustand gehalten — aber nur bis
`APPLIANCE_DETECTION_MAX_DROPOUT_MIN` (30 min, Operator-Regel nach Vorfall
2026-08-08: ein ausgeschaltetes Gerät nimmt seine Integration für Stunden
offline und darf nicht ewig „running" bleiben). `_get_appliance_runs` zählt
die Dropout-Dauer je Subentry (`_appliance_dropout_since`); nach Überschreiten
gilt das Gerät als **aus**: Latch und persistierter Start werden verworfen,
kein Run geplant. Kommt die Entity später als „running" zurück, wird sauber
ein frischer Lauf verankert.

`_get_appliance_runs` bildet daraus `ApplianceRun(remaining_energy_wh,
remaining_hours)`. Der Startzeitpunkt wird persistiert; nach einem Neustart
bekommt ein wiederhergestellter Start **eine** Plausibilitätsprüfung (H1): ist
er schon vollständig verstrichen, wird er als frischer Lauf neu verankert.

`series._apply_appliance_runs` verteilt die Restenergie mit konstanter Leistung
auf die kommenden Slots und **addiert sie auf `ac_wh`** — Appliances sind also
Teil der AC-Prognose und tauchen nie in `extra_ac_wh` auf. Deshalb enthält
`daily_surplus[].loads_kwh` per Konstruktion nur Überschusslasten.

---

## 5. Die zentralen Datentypen

### 5.1 `HourSlot` (`core/model.py`)

| Feld | Bedeutung |
|---|---|
| `index` | Position im Horizont (0 = jetzt) |
| `start` | **naiv-lokale** Startzeit |
| `duration` | Bruchteil einer Stunde, `(0, 1]`; nur Slot 0 kann partiell sein |
| `hour_of_day` | Lokale Stunde (Profil-Lookup) |
| `pv_wh` | Median-PV-Energie des Slots |
| `ac_wh` | AC-Verbrauch **ohne** Überschusslasten, **inklusive** Appliance-Rest |
| `dc_wh` | DC-Verbrauch |
| `pv_p10_wh`, `pv_p90_wh` | Empirisches Band, `None` wo keine Bucket-Abdeckung |

### 5.2 `PlanInputs`

`now` (naiv-lokal), `start_soc_percent`, `slots`, `load_states`,
`appliance_runs`. Mehr nicht — alles Statische steckt in `SystemConfig`.

### 5.3 `SurplusLoadState` — die Leistungs-Präzedenz

| Feld | Bedeutung |
|---|---|
| `load_id` | = Subentry-ID |
| `available` | `False` ⇒ der Planer bucht die Last **nie**. Gesetzt durch: Verfügbarkeits-Entity aus, BM-Steuerung-Switch aus, G2-Stale-Latch, F4-Freeze-Latch |
| `soc_percent` | nur für energielimitierte Lasten (live oder Cache) |
| `measured_power_w` | robuster Fenster-Median **während** eines BM-Laufs; `None` im Warm-up und wenn aus |
| `learned_power_w` | persistierter Write-Through des letzten stabilen Schätzers |
| `saturated_power_w` | **F5**: gemessene Ist-Leistung (~2 W) während eines gelatchten F-L7-Power-Warnings |

`planning_power_w(load)` entscheidet strikt in dieser Reihenfolge:

```
saturated_power_w (falls gesetzt, geklemmt >= 0)   # F5, höchste Präzedenz
> measured_power_w (falls > 0)                     # laufende Messung
> learned_power_w  (falls > 0)                     # gelernt aus früheren Läufen
> load.nominal_power_w                             # Konfiguration
```

`remaining_energy_wh(load)` = `(target_soc - soc) / 100 * capacity_wh`, geklemmt
≥ 0, und `None` für nicht energielimitierte Lasten (= unbegrenzt aufnahmefähig).
Fehlt der SOC, wird `0.0` angenommen — die Last plant also als „leer" und heilt
sich beim Aufwachen selbst.

### 5.4 `LoadPlan` und `PlanResult`

`LoadPlan`: `schedule` (bool je Slot), `planned_energy_wh`, `allocations`
(je Buchung `(Start-Slot, Slot-Anzahl, Pass-Nr, Wh)`), `run_hours`
(tatsächlich gebuchte Laufstunden je Slot — `schedule[i] == run_hours[i] > 0`),
`reasons` (ein `why`-String je Allocation, gleiche Reihenfolge).
`active_run_hours(durations)` liefert die zusammenhängende Laufzeit ab Slot 0
und bricht am ersten Slot ab, der **nicht** bis zu seiner eigenen Dauer gefüllt
ist (dort endet der Lauf real).

`PlanResult` trägt neben Trajektorie und Lastplänen die Diagnostik:
`import_trade_used_wh`, `stressed_min_soc_percent`, `pv_window_ends`,
`threshold_horizon_end`, `prevented_export_by_day_wh`. Semantik in Dokument 02
§7 — insbesondere: **`lost_surplus_kwh` und `grid_import_kwh` sind
Horizontsummen über 2–3 Tage**, nicht Tageswerte. Tageswerte stehen im
Attribut `daily` bzw. im Coordinator-Key `daily_surplus`.

---

## 6. Persistenz

### 6.1 Coordinator-Store

Ein HA-`Store` je Config-Entry: `Store(hass, STORAGE_VERSION=1,
f"{DOMAIN}.{entry_id}")`. Geschrieben verzögert
(`_save_persistent_state` → `async_delay_save(..., 10)`), beim Unload sofort
geflusht (`async_flush_persistent_state`) — ein Entry-Reload feuert kein
`EVENT_HOMEASSISTANT_FINAL_WRITE`, ohne den Flush ginge z.B. ein frisch
gesetzter Power-Warning-Latch verloren.

Inhalt laut `_persistent_payload`:

| Key | Inhalt | Warum persistiert |
|---|---|---|
| `load_soc` | je Last `{entity_id, soc, ts}` | schlafende Powerstations planen weiter; max. `LOAD_SOC_CACHE_MAX_AGE_HOURS = 168` (7 d) alt |
| `plug_owned` | „wir haben den Stecker eingeschaltet" | für die `auto`-Input-Off-Policy |
| `last_load_switch` | Dwell-Zeitstempel je Last | ein gelöschter Stempel erlaubte Schalten direkt nach dem Boot (Mitverursacher des Nachtlade-Vorfalls 2026-07-05) |
| `load_run_deadline` | eingefrorene Sub-Stunden-Laufzeit-Deadline | ein Neustart darf einen Lauf nie „entdeckeln" |
| `load_bm_enabled` | Per-Last-Steuerungsswitch | – |
| `load_runtime_seconds` | akkumulierte Laufzeit | Zähler + Tank-Modell |
| `load_learned_power` | **gelernte Planungsleistung** je Last | der Schätzer selbst ist medianbasiert und warm-up-geschützt |
| `appliance_started` | Startzeit laufender Geräte | Neustart mitten im Lauf soll nicht die volle Energie neu injizieren |
| `support_manual`, `support_state` | Manual-Modus + eigener Schaltzustand je PSU | unterscheidet „an, weil wir" von „an, obwohl nicht wir" |
| `dc48_ctrl_caused_off` | R2-Regler hat abgeschaltet | verhindert Fehldeutung als Operator-Aus nach einem Reload |
| `load_power_warning` | **F-L7-Latch** je Last | ein Options-Save (= Reload) darf keine gesetzte Warnung verschlucken |
| `load_tank_samples` | letzte `TANK_LEARN_SAMPLES = 5` Voll-Tank-Laufzeiten | Median = gelernte Volltank-Laufzeit |
| `load_tank_full_min` | Laufzeit-Capture am Latch-Eintritt | Neustart mitten im Tankzyklus lernt trotzdem |
| `stale_since`, `stale_shed_active` | D-A8-Stufe-2-Datenverlustuhr + Shed-Latch | ein Neustart darf die Uhr nicht zurücksetzen |
| `feedin_switch_on`, `feedin_manual_until`, `feedin_last_written_w` | **F-FEEDIN** (v0.23.0): Laufzeitschalter, Manuell-Modus bis Mitternacht, zuletzt geschriebener Sollwert | der Manuell-Modus ist Operator-Eigentum und darf einen Reload nie verlieren; der letzte Schreibwert ist die Referenz der Fremdänderungs-Erkennung |
| `feedin_owned_entity` | **F-FEEDIN**: die Sollwert-Entity, die aktuell einen Wert **von uns ≠ 0** trägt | die einzige Möglichkeit, eine im Options-Flow **entdrahtete** Entity noch auf 0 zu ziehen — sonst bliebe der letzte Exportwert für immer stehen (`_feedin_release_orphan`, Review 03.08.2026) |
| `realized` | **F-REALIZED-SURPLUS** (v0.24.0): `{date, lost_wh, prevented_wh, feedin_wh, true_export_total_wh, external_debt_wh, last_readings}` | Tageszähler und der monotone Echt-Export sind Messwerte, kein Prognosezustand — ein Reload darf sie nicht auf 0 werfen. Die `last_readings` sind die Baselines der Delta-Bildung: ohne sie würde der erste Zählerstand nach dem Neustart als riesiges Delta gebucht (der Sprungfilter fängt es, aber die Energie fehlte). `external_debt_wh` ist der noch nicht verrechnete Rest der Außenlast-Korrektur (kein Tageszähler — er überlebt Mitternacht, weil das Messartefakt es auch tut). Die Tageszähler werden nur übernommen, wenn `date` heute ist; die Readings immer |

**Bewusst NICHT persistiert** (jeweils mit Begründung im Code):

- `_load_power_samples` — der Live-Sample-Puffer; ein alter Puffer wäre nach
  einem Neustart eine falsche „frische Messung". Der gelernte Wert trägt.
- `_load_run_since` — der Tick-Cursor des Laufzeitzählers; wiederhergestellt
  würde er die gesamte Ausfallzeit gutschreiben.
- `_load_deviation_since` — der F-L7-Dwell-Timer (eine noch nicht ausgelöste
  Abweichung wird nicht über die Downtime gerettet).
- `_load_last_off`/`_load_flicker_hist` (F8),
  `_load_latch_hold` (F10), `_floor_guard_active`, `_load_tank_notified` —
  alle rein in-memory, sie bauen sich nach einem Neustart neu auf.
  (G2 `_load_soc_frozen`/`_load_soc_stale` und F4 `_load_freeze_ref`/
  `_load_freeze_stale` standen bis zum Live-Audit 02.08.2026 ebenfalls in
  dieser Liste — seitdem sind Latch + Evidenzuhr persistiert, siehe
  Dokument 03 §3.2/3.3.)
- `_realized_last_ts`, `_realized_relearn`, `_realized_runtime_base`
  (F-REALIZED-SURPLUS) — das Zeitfenster des Sprungfilters, die
  Ausfall-Markierungen und die Laufzeit-Checkpoints. Ein wiederhergestelltes
  Fenster wäre nach der Downtime falsch weit (der Filter würde jeden Sprung
  durchlassen); der Laufzeit-Checkpoint macht das erste Delta nach dem
  Neustart 0 — derselbe begrenzte Verlust wie beim Laufzeit-Cursor selbst.

### 6.2 Learner-Store

Getrennter Store: `Store(hass, LEARNED_STORE_MAJOR=1,
f"{DOMAIN}.{LEARNED_STORE_KEY}.{entry_id}")`, also
`battery_manager.learned_profiles.<entry_id>`. Die **Envelope**-Major-Version
ist auf 1 eingefroren (ein Bump würde HAs Default-Migration werfen lassen und
das Entry-Setup crashen); Schemawechsel laufen über das **innere**
`data["version"]` (`LEARNED_STORE_VERSION = 2`, Bins mit `{p50, p80}`) — bei
Mismatch wird verworfen und frisch nachgelernt, die Quelldaten sind ja
re-fetchbar.

`async_remove_entry` löscht beide Stores.

---

## 7. Versionierung und CI

- Laufzeit-Quelle der Wahrheit ist **`manifest.json`**: `__init__.async_setup_entry`
  liest sie über `async_get_integration` und setzt
  `coordinator.integration_version` (davon lebt die `sw_version` des Geräts und
  die Cache-Busting-URL der Karte). In `const.py` steht bewusst **keine**
  Versionskonstante mehr — die war früher weggedriftet.
- **`pyproject.toml`** trägt dieselbe Version als Metadatum.
- **CI-Gate** (`.github/workflows/validate.yml`): ein Schritt vergleicht
  `manifest.json`-Version und `pyproject.toml`-Version und schlägt bei
  Abweichung fehl (`assert mf == pp, "version drift: ..."`).
- `.github/workflows/release.yml` synchronisiert beide Dateien auf das Tag und
  committet ggf. nach.

Es gibt keine Migration von Store-Inhalten über Releases hinweg; die
Entry-Migration (`async_migrate_entry`) deckt Config-Keys ab: v1→v2 (entfernte
Controller-Keys), v2.2 (Lernfenster 42→120 d) und v2.3 (Backfill der vier
absoluten Grid-Support-Schwellen aus dem alten soc_min/buffer, damit ein
Upgrade die Schaltpunkte nicht verschiebt).

---

## 8. Wo fange ich an? (Kurz-Rezepte)

| Frage | Erster Griff |
|---|---|
| Woher kommt ein Sensorwert? | `coordinator._async_update_data` (Rückgabe-Dict) → `sensor.py` / `binary_sensor.py` |
| Warum ist der Plan so? | `core/optimize.py::plan` → Dokument 02; die `why`-Strings am Slot lesen |
| Warum schaltet ein Gerät (nicht)? | `coordinator._apply_load_switching` → Dokument 03 |
| Warum ist die PV-Kurve komisch? | `_get_pv_hourly` → `series._pv_wh_hourly` (Residual-Regel!) → ggf. Schwester-Repo |
| Warum sind keine Bänder aktiv? | `_quantile_coverage` im `soc_forecast`-Attribut, dann `optimize.quantile_band_slots` (p10==p90 zählt nicht!) |
| Warum ist eine Last „unavailable"? | `_get_load_states`: Verfügbarkeits-Entity, BM-Switch, `_update_soc_stale` (G2), `_update_telemetry_freeze` (F4) |
| Warum plant der Planer 2 W statt 400 W? | F5: `_load_power_warning` gelatcht → `saturated_power_w` |
| Was überlebt einen Neustart? | `coordinator._persistent_payload` (§6.1) |
| Was ist der verbindliche Vertrag? | der Code; danach je `F-*.md` die Spezifikation seines Features. `ARCHITECTURE.md` ist größtenteils aktuell; `STRATEGY.md`/`REQUIREMENTS.md`/`ALGORITHM.md` sind **historisch** (v0.7.x-Stand) |

---

**Verifikation: geprüft gegen Commit `a172d48` (v0.16.2); die v0.17.0-Einträge
gegen den Arbeitsstand vom 2026-07-31.**

Wichtigste Code-Anker dieses Dokuments:

1. `custom_components/battery_manager/coordinator.py::BatteryManagerCoordinator._async_update_data`
   — die verbindliche Schrittreihenfolge des Zyklus.
2. `custom_components/battery_manager/const.py` — `UPDATE_INTERVAL_SECONDS = 300`,
   `INITIAL_UPDATE_INTERVAL_SECONDS = 30`, `DEBOUNCE_SECONDS = 5`,
   `STARTUP_SOC_GRACE_S = 120`, `DEFAULT_CONFIG`, `DEFAULT_LOAD_CONFIG`.
3. `custom_components/battery_manager/core/model.py` — `HourSlot`, `PlanInputs`,
   `SurplusLoadState.planning_power_w` (Präzedenzkette), `PlanResult`.
4. `custom_components/battery_manager/core/series.py::build_slots` /
   `_pv_wh_hourly` / `_quantile_wh` — Slot-Raster und PV-Befüllung.
5. `custom_components/battery_manager/coordinator.py::_persistent_payload` —
   die vollständige Liste dessen, was persistiert wird.
