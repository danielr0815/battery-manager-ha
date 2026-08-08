# HA-Integration: Entities, Attribute, Config-Flow

**Stand: `main` @ v0.17.0 (Arbeitsstand 2026-07-31).** Dieses Dokument ist die
**Außenschnittstelle** der Integration `battery_manager`: welche Entities je
Plattform entstehen, welchen Vertrag ihre Attribute haben (insbesondere der
große `soc_forecast`-Sensor), was der Config-Flow und die beiden Subentry-Typen
anbieten und was der Diagnostics-Download enthält. Interna des Planers stehen in
`02-planner-und-optimizer.md`, die Schaltlogik in
`03-executor-und-lastregeln.md`, die reale Anlage in
`05-anlage-und-betrieb-runbook.md`.

Alle Aussagen sind gegen den Code geprüft (Belege als *Datei::Symbol*).
**Im Zweifel gilt der Code.**

---

## 1. Grundmuster: Gerät, unique_id, Entity-IDs, Verfügbarkeit

- **Ein Config-Entry = ein Haupt-Gerät + ein Gerät je Subentry** (HA 2026.8,
  siehe Kasten unten). `entity.BatteryManagerEntity.__init__` setzt für
  Entry-Level-Entities unverändert
  `DeviceInfo(identifiers={(DOMAIN, entry_id)}, name=INTEGRATION_NAME
  ("Battery Manager"), manufacturer="Battery Manager", model="Energy
  Optimizer", sw_version=coordinator.integration_version)`; Subentry-Entities
  bekommen ein eigenes Gerät `identifiers={(DOMAIN,
  f"{entry_id}_{subentry_id}")}` mit dem Subentry-Titel als Name. Die
  `sw_version` kommt zur Laufzeit aus `manifest.json`
  (`__init__.async_setup_entry` → `async_get_integration`), **nicht** aus einer
  Konstante — die war früher weggedriftet. Alle Geräte legt
  `entity.ensure_devices` bereits in `async_setup_entry` an (idempotent), damit
  die Subentry-Geräte `via_device_id` auf das Haupt-Gerät tragen — zur
  Entity-Konstruktion existiert die Registry-ID des Haupt-Geräts bei einer
  Neuinstallation noch nicht.

> **HA 2026.8.0 Breaking Change (Produktionsvorfall):** „Devices can only
> have a single config subentry" (core PR #175785, ohne Compat-Shim;
> [Blog](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)).
> Das alte Shared-Device-Muster (Entry + alle Subentries auf **einem** Gerät)
> ließ das Gerät bei jedem `async_add_entities(config_subentry_id=…)` zwischen
> den Scopes hin- und herspringen; die Entity-Registry entfernte bei jedem
> Sprung die Entities der „alten" Seite — unter 2026.8.0 verschwanden so fast
> alle BM-Entities aus der State-Machine (nur der zuletzt angelegte
> Subentry-Switch überlebte). Fix: ein Gerät je Subentry + Migration 2.4
> (`__init__._migrate_to_subentry_devices` hängt bestehende Registry-Zeilen auf
> die neuen Geräte um, Entity-IDs bleiben stabil; vom Ping-Pong gelöschte Zeilen
> stellt HA beim Re-Add mit alter Entity-ID wieder her).
- **Plattformen** (`__init__.PLATFORMS`): `binary_sensor`, `button`, `sensor`,
  `switch`. Dazu der Diagnostics-Endpoint (keine Plattform) und zwei Services.
- **Stabiler Schlüssel ist die `unique_id`**, nicht die Entity-ID:
  `unique_id = f"{entry_id}_{key}"`. Per-Last-/Per-Appliance-Entities hängen die
  Subentry-ID an den Key (`f"load_{subentry_id}"`,
  `f"load_runtime_{subentry_id}"`, …).
- **Alle Entities tragen `_attr_has_entity_name = True`**, ihr Name ist also
  „Gerätename + Entity-Name". Die **Entity-ID** bildet HA einmalig bei der
  Anlage aus dem *englischen* Anzeigenamen (`translations/en.json`) plus
  Gerätename — sie weicht deshalb teils vom Key ab (`grid_import_forecast` →
  `…_grid_import_forecast`, aber `soc_forecast` → `…_soc_forecast`, `load_…`
  → `…_<lastname>_recommendation`).
- **Live-Eigenheit der Referenz-Anlage (Stand 25.07.2026):** die meisten
  Entities tragen dort das Präfix `abstellraum_battery_manager_*` (das Gerät
  wurde einem Bereich/Namen „Abstellraum" zugeordnet, bevor die Entities
  angelegt wurden), z. B.
  `sensor.abstellraum_battery_manager_soc_prognoseverlauf`,
  `binary_sensor.abstellraum_battery_manager_wechselrichter_empfehlung`.
  **Ausnahmen** sind früher angelegte Entities ohne Präfix, etwa
  `sensor.battery_manager_soc_threshold`. Verlass dich nie auf das Muster —
  suche über die `unique_id` bzw. den Gerätesatz in der Entity-Registry.
- **Verfügbarkeit:** Basis ist
  `BatteryManagerEntity.available = super().available and coordinator.data is
  not None and coordinator.data.get("valid", False)`, d. h. bei
  `UpdateFailed`/ungültigen Eingängen wird die Entity `unavailable`.
  **Bewusst immer verfügbar** (überschreiben `available` mit `True`, weil sie
  persistierten Zustand statt Planerausgabe zeigen — Review #15):
  `SupportModeSensor`, `SupportPathSensor`, `SurplusLoadRuntimeSensor`,
  `BatteryManagerVacationSwitch`, `SupportManualSwitch`,
  `SurplusLoadControlSwitch`, `SurplusLoadRuntimeResetButton`, (v0.23.0)
  `BatteryManagerFeedInSwitch` und `FeedInModeSensor` (persistierter
  Schaltzustand bzw. Modus statt Planerausgabe, Review-#15-Muster) sowie
  (v0.24.0) `EarlyFeedInRealizedSensor` (persistierter Zählerstand).
- **Aufräumen:** Per-Subentry-Entities werden über
  `entity.async_add_by_subentry` mit `config_subentry_id` angelegt, damit HA sie
  beim Löschen der Last/Appliance automatisch mitentfernt (v0.7.19). Entities,
  die durch eine **Options-Änderung** wegfallen (Support-PSU entfernt,
  Power-Warning auf 0 %, Appliance nicht mehr opportunistisch), werden aktiv aus
  der Registry gelöscht (`er.async_remove`) statt als Waisen liegenzubleiben.

---

## 2. Sensoren (`sensor.py`)

### 2.1 Die fünf einfachen Planwerte

Alle aus `SENSOR_DESCRIPTIONS`, Klasse `BatteryManagerSensor`,
`state_class = MEASUREMENT`, Wert = `round(coordinator.data[data_key], 2)`.

| Key | `data_key` | Einheit | Bedeutung |
|---|---|---|---|
| `soc_threshold` | `soc_threshold_percent` | % | **T\***, die gefundene Inverter-Abschaltschwelle nach Trägheitsfilter (`_apply_threshold_inertia`, Default 2 % Totband) |
| `min_soc_forecast` | `min_soc_forecast_percent` | % | tiefster SOC der finalen Trajektorie über den Horizont |
| `max_soc_forecast` | `max_soc_forecast_percent` | % | höchster SOC |
| `hours_to_max_soc` | `hours_to_max_soc` | h | Stunden bis zum Maximum |
| `grid_import_forecast` | `grid_import_kwh` | kWh | prognostizierter Netzbezug |
| `lost_surplus` | `lost_surplus_kwh` | kWh | prognostizierter ungenutzter Überschuss (= Export) |

**Die wichtigste Fehlerquelle:** `grid_import_kwh` und `lost_surplus_kwh` sind
**Horizontsummen über alle Prognosetage (2–3 Tage)**, keine Tageswerte. Der
Code sagt es explizit (Kommentar V10 in `sensor.BatteryManagerSensor.
extra_state_attributes`): 7,9 kWh um 05:00 spannen jeden Prognosetag, während
der eine Tag real 0,58 kWh realisierte. Tageswerte kommen aus `daily` /
`today_kwh` / `tomorrow_kwh`.

Attribute:

| Attribut | Auf welchem Sensor | Inhalt |
|---|---|---|
| `last_update` | allen fünf | `str(data["last_update"])` — tz-aware **Lokalzeit** (`dt_util.now()`) |
| `grid_export_kwh` | nur `grid_import_forecast` | Horizont-Exportsumme |
| `today_kwh`, `tomorrow_kwh`, `daily` | `lost_surplus` + `grid_import_forecast` | F-PERDAY-SURPLUS R2 via `sensor._per_day_attrs` |

`_per_day_attrs` definiert „heute" als **das Datum des ersten Eintrags in
`daily`**, also den Tag von Slot 0 — nicht die Systemuhr. „morgen" = dieses
Datum + 1. Ein Tag, den der Horizont nicht enthält, rendert `0.0`.

### 2.2 Support-Modus-Sensoren (`SupportModeSensor`)

Keys `support_dc24_mode` / `support_dc48_mode`, `device_class = ENUM`,
Optionen `auto` | `manual` (F-N2). Existieren **nur**, wenn der jeweilige
PSU-Switch konfiguriert ist; sonst wird eine Alt-Entity aus der Registry
entfernt. `manual` bedeutet: die PSU wurde extern eingeschaltet, die
Integration fasst sie nicht an, bis sie extern wieder aus ist.

Der **48-V-Sensor** trägt zusätzlich das Attribut `controller` =
`coordinator.dc48_controller_diagnostic()` — der Zustandsschnappschuss des
R2-Spannungsreglers (aktiv/Modus/Entscheidung/Grund/Spannung), damit der
Log-Only-Shakedown und die spätere scharfe Regelung beobachtbar sind.

### 2.2a Feed-in-Modus-Sensor (`FeedInModeSensor`, v0.23.0)

Key `feedin_mode`, `device_class = ENUM`, Optionen `auto` | `manual`
(F-FEEDIN R9, `docs/F-FEEDIN.md`). Existiert **nur**, solange der
Options-Toggle `feedin_enabled` an ist. `manual` bedeutet: der AC-Setpoint
wurde extern überschrieben — die Integration schreibt nichts mehr bis zum
nächsten Mitternacht, dann läuft die Automatik weiter. Quelle ist
`coordinator.feedin_manual()` bzw. `data["feedin_mode"]`.

### 2.2b Ist-Bilanz-Sensoren (F-REALIZED-SURPLUS, v0.24.0)

Acht Energie-Sensoren (`device_class = ENERGY`, kWh, `docs/F-REALIZED-SURPLUS.md`).
Sieben davon sind **Tageswerte** ⇒ `state_class = TOTAL`; nur der korrigierte
Exportzähler ist monoton ⇒ `TOTAL_INCREASING` (damit ihn das
Energie-Dashboard konsumieren kann).

| Key | Quelle (`realized`-Block) | Bedeutung |
|---|---|---|
| `lost_surplus_realized_today` | `lost_surplus_realized_kwh` | **gemessener** verlorener Überschuss heute (korrigierter Export, R5) |
| `lost_surplus_today` | `lost_surplus_today_kwh` | Ist bisher **+** Prognose für den Tagesrest |
| `lost_surplus_tomorrow` | `lost_surplus_tomorrow_kwh` | reine Prognose |
| `prevented_export_realized_today` | `prevented_export_realized_kwh` | **gemessen** verhinderter Export heute (Lastverbrauch, R6) |
| `prevented_export_today` | `prevented_export_today_kwh` | Ist **+** Prognose-Rest |
| `prevented_export_tomorrow` | `prevented_export_tomorrow_kwh` | reine Prognose |
| `early_feed_in_realized_today` | `coordinator.feedin_delivered_today_kwh()` | **gemessen** früh eingespeist heute (Executor-Integral, R7) |
| `true_export_energy` | `true_export_total_kwh` | monotoner **echter** Export (Zähler minus außerhäusige Lasten) |

**Zwei verschiedene Gates** (beide räumen Alt-Entities aktiv aus der Registry,
Muster `FeedInModeSensor`):

- Sensoren 1–6 + `true_export_energy` (Klasse `RealizedSurplusSensor`) hängen
  am Options-Feld `export_meter_entity`. Ohne Exportzähler fehlt der
  `realized`-Datenblock komplett — die Sensoren werden gar nicht erst angelegt.
- `early_feed_in_realized_today` (Klasse `EarlyFeedInRealizedSensor`) hängt
  **allein** an `feedin_enabled` und liest das persistierte Executor-Integral
  direkt, braucht also keinen Exportzähler; `available` ist immer `True`.

### 2.3 Laufzeitzähler je Last (`SurplusLoadRuntimeSensor`)

Key `load_runtime_<subentry_id>`, Einheit **Minuten**,
`device_class = DURATION`, `state_class = TOTAL_INCREASING` (ein Reset gilt der
Langzeitstatistik damit als neue Periode). Wert =
`round(coordinator.load_runtime_minutes(subentry_id), 1)`.

Gezählt wird **echte Laufzeit**: aus dem Leistungs-Feedback-Sensor, wenn
konfiguriert (`> LOAD_RUNTIME_MIN_W = 5 W` ⇒ „läuft", damit zählen auch manuelle
Läufe), sonst ersatzweise aus dem BM-Ladezustand. Ein einzelner Tick ist auf
`LOAD_RUNTIME_TICK_MAX_S = 900 s` gedeckelt.

**Attribute** (nur wenn das Tank-Modell für diese Last eingeschaltet ist, sonst
`None` ⇒ gar keine Attribute) — `coordinator.tank_diagnostics`:

| Attribut | Bedeutung |
|---|---|
| `tank_remaining_min` | prognostizierte **Rest-Laufzeit** bis Tank voll = gelernte Volltank-Laufzeit − Laufzeit seit letzter Leerung, ≥ 0 |
| `tank_learned_full_min` | Median der letzten `TANK_LEARN_SAMPLES = 5` beobachteten Volltank-Laufzeiten; solange keine Probe existiert der konfigurierte Startwert |
| `tank_samples` | Anzahl gelernter Proben |

Diese Werte steuern **nie** den Planer (Operator-Regel 24.07.2026: keine
vorsorgliche Drosselung) — sie speisen nur Diagnose und die einmalige
„Tank bald voll"-Push-Benachrichtigung (§7.2).

---

## 3. Der `soc_forecast`-Sensor — das Attribut-Vertragswerk

Klasse `sensor.BatteryManagerSocForecastSensor`, Key `soc_forecast`,
Einheit %, **kein `state_class`** (eine Prognose darf nie in die
Langzeitstatistik). `_unrecorded_attributes = {"forecast", "loads",
"appliances", "consumption_profile", "consumption_forecast"}` hält die dicken
Attribute aus dem Recorder.

**State** = SOC in *einer Stunde* (`curve[1]["soc"]`, ersatzweise `curve[0]`).
Der eigentliche Wert dieses Sensors sind die Attribute: die mitgelieferte
Lovelace-Karte rendert alles aus dieser einen Entity.

### 3.1 `forecast` — die Trajektorie

Liste von Punkten, gebaut in `coordinator._async_update_data`:

```
[{"t": "2026-07-25T14:07:31.482", "soc": 63.4},
 {"t": "2026-07-25T15:00:00", "soc": 68.1, "dc24": true},
 ...]
```

- Erster Punkt = **jetzt** (`now.replace(tzinfo=None)`, SOC = Eingangs-SOC).
- Danach je Slot **ein Punkt am Slot-ENDE** (`slot.start + duration`) mit
  `flow.soc_end_percent`, gerundet auf 0,1.
- `dc24` / `dc48` erscheinen **nur wenn `True`** (Grid-Support in diesem Slot
  aktiv), damit das Attribut kompakt bleibt. Die mitgelieferte Karte rendert
  daraus seit v0.25.3 **keine** Support-Lanes mehr (Operator-Entscheid);
  die Flags bleiben für Dritt-Karten im Attribut.
- `feedin` (W, gerundet auf 0,1) erscheint **nur auf Slots mit gebuchtem
  Feed-in** (v0.23.0, F-FEEDIN) — daraus rendert die Karte die Einspeise-Lane
  unter dem SOC-Chart.

**Zeitstempel-Falle:** die Strings sind **naiv, ohne Offset** — der Kern rechnet
durchgängig naiv-lokal (`build_slots(config, now.replace(tzinfo=None), …)`).
Ein Konsument, der sie als UTC interpretiert, verschiebt die Kurve um den
lokalen Offset (im Sommer 2 h). JavaScript parst einen ISO-String ohne Offset
als *lokal* — deshalb stimmt die Karte, während ein Python-Skript mit
`datetime.fromisoformat(...).replace(tzinfo=UTC)` daneben liegt. Dasselbe gilt
für `loads[].schedule[].start/end` und für die `hourly_details` des
Export-Services. **Ausnahme:** `last_update` ist tz-aware Lokalzeit.

### 3.2 `loads` — der Plan je Überschusslast

Liste (Reihenfolge = Prioritätsreihenfolge des Planers) mit je:

| Feld | Bedeutung |
|---|---|
| `name` | Name der Last (Subentry-Titel) |
| `active` | **effektive** Empfehlung jetzt (`_effective_load_active`: Slot-0-Aktivität gekappt durch die eingefrorene Sub-Stunden-Deadline, F-SUBHOUR R8/R12) |
| `planned_energy_kwh` | über den ganzen Horizont geplante Energie |
| `today_kwh`, `tomorrow_kwh` | Aufteilung über die Tage, in denen die Laufstunden **starten**; Anker = Slot-0-Tag (`_load_per_day_kwh`) |
| `schedule` | Liste der **belegten** Slots |

Ein `schedule`-Eintrag:

| Feld | Bedeutung |
|---|---|
| `start`, `end` | naiv-lokale ISO-Strings (§3.1) |
| `pass` | **1** = Direktüberschuss, **2** = vorausschauend/zielbasiert („covered by otherwise-lost export", Pre-Drain-Buchungen) |
| `why` | **Explain-Plan** (F-PLANNER-HONESTY R14): der Annahmegrund der Allokation, die diesen Slot belegt hat. Fehlt bei Alt-Plänen (der `zip` über `allocations`/`reasons` ist bewusst nicht `strict`) |
| `wh` | gebuchte Energie **dieses Slots**. Die Allokation trägt die Energie der ganzen Buchung; sie wird über ihre Slots nach den dort tatsächlich zugesagten `run_hours` aufgeteilt — eine Gleichverteilung würde die letzte Stunde eines Laufs falsch ausweisen (F-SUBHOUR). Quelle des Hover-Readouts der Karte |

`why` ist das schärfste Forensik-Werkzeug am Sensor: es beantwortet
„warum genau dieser Slot", ohne den Planer nachzurechnen.

### 3.2b `appliances` — erkannte Geräteläufe (v0.25.3)

Liste der **aktuell laufenden** Appliance-Runs (`data["appliance_plans"]`,
Quelle `_get_appliance_runs`), damit die Karte Waschmaschine & Co. als Lane
unter dem Chart zeigt — sie formten die AC-Prognose schon immer, waren aber
unsichtbar (Operator-Wunsch 2026-08-08). Je Eintrag:

| Feld | Bedeutung |
|---|---|
| `name` | Subentry-Titel des Geräts |
| `active` | immer `True` — nur laufende Runs werden publiziert |
| `schedule` | genau **ein** Block `{start: now, end: now + remaining_hours, wh: verbleibende Energie des Laufs}` (naiv-lokale ISO-Strings, §3.1) |

Leer, wenn nichts läuft. Die Hover-Chips der Karte zeigen für diese Lanes
bewusst kein Wh (kein Slot-Wert, nur Lauf-Summe).

### 3.2c `consumption_forecast` — geplanter Verbrauch je Slot (v0.25.5)

Speist die mitgelieferte **Verbrauchs-Card** (`battery-manager-consumption-card`,
dasselbe JS-Modul, zweite registrierte Card-Type). Liste je Slot:

| Feld | Bedeutung |
|---|---|
| `t` | Slot-**Start**, naiv-lokaler ISO-String (§3.1-Zeitstempel-Falle!) |
| `ac_w` | AC-Verbrauch (Basis-Profil + Appliance-Runs, `slot.ac_wh ÷ duration`) |
| `dc48_w`, `dc24_w` | DC-Aufteilung **exakt wie der Kern**: erst `native48_base_w` (fix), dann `dc24_share` vom Rest auf die 24-V-Schiene. Näherung ohne eigene 24-V-Messung — eine gelernte 24-V-Reihe wäre ein Folgeschritt (docs/F-CONSUMPTION-PROFILES.md) |
| `loads_w` | geplante Überschusslasten (`flow.extra_ac_wh ÷ duration`) — eigene Schicht |
| `src` | Quellen-Markierung je Pfad, `"L/S"`-Format (`L` = gelernt, `S` = statisches Fallback-Profil) — die Card dunkelt Fallback-Slots ab |

Werte sind **Watt** (Stundenmittel), nicht Wh — robust gegenüber der
Partialstunde von Slot 0. Tages-kWh summiert die Card selbst (W × Slotdauer,
Letztere aus dem Abstand zum nächsten Slot-Start).

### 3.3 Kennzahlen und Diagnosen

| Attribut | Quelle / Bedeutung |
|---|---|
| `soc_threshold_percent` | T\* nach Trägheit (wie der eigene Sensor) |
| `grid_import_kwh`, `lost_surplus_kwh` | **Horizontsummen** (siehe §2.1) |
| `daily` | Liste je Kalendertag: `{date, lost_surplus_kwh, grid_import_kwh, loads_kwh, prevented_export_kwh}` (`_daily_surplus_breakdown`). Gruppiert nach `slot.start.date()` in Planer-Lokalzeit — ein Slot zählt auf **seinen Starttag**. `prevented_export_kwh` (F-STRICT-SURPLUS R4) ist das Kontrafaktische „welchen Export haben die Lastläufe an diesem Tag verhindert" (Basis minus Allokation, beides **vor** Support-Eskalation) und beantwortet „warum läuft die Last, obwohl der SOC nie ans Maximum kommt". Bei aktivem Feed-in (v0.23.0) kommt `planned_feedin_kwh` je Tag hinzu (aus `PlanResult.feedin_by_day_wh`) — die Statistikzeile der Karte liest daraus „geplante Einspeisung heute/morgen". Seit v0.24.1 zusätzlich `support_dc24_kwh` / `support_dc48_kwh`: die je Tag **gelieferte** PSU-Energie (`flow.psu24/48_delivered_wh`, nicht die Nennleistung). Die mitgelieferte Karte zeigt die Stützpfade seit v0.25.3 nicht mehr (Operator-Entscheid); die Schlüssel bleiben für Dritt-Karten |
| `loads_today_kwh`, `loads_tomorrow_kwh` | Überschusslast-Energie heute/morgen (aus `daily[].loads_kwh`). **Appliances sind nicht enthalten** — sie gehen in die AC-Prognose, nie in `extra_ac_wh` |
| `consumption_profile` | Lern-Diagnostik, siehe §3.4 |
| `gate_calibration` | 48-V-Gate-Kalibrierbracket, siehe §3.5 |
| `pv_source` | je Tag `"hourly"` oder `"two_window"` (`_pv_day_sources`): hatte die gemergte Stundenkarte für diesen Kalendertag mindestens einen Bucket? |
| `quantile_coverage` | je Tag `{coverage: 0…1, source: "scalar" \| "mixed" \| "p10/p90"}` (F-QUANTILE-BANDS R7). Nenner sind die **Tageslicht-Slots** (`pv_wh > 0`), Zähler die Slots mit **wirksamem** Band nach genau demselben Prädikat, mit dem der Planer gatet (`optimize.quantile_band_slots`) — ein kollabiertes Band `p10 == p90` zählt **nicht**. So kann man die Bänder buchstäblich reifen sehen |
| `import_trade_used_wh` | der Import, den die Allokation über die Basis hinaus erzeugt hat. Seit F-STRICT-SURPLUS R1 **kein Trade mehr**, sondern durch den 50-Wh-Artefakt-Slack (`IMPORT_ARTIFACT_SLACK_WH`) begrenzt — ein Wert nahe 0 ist der Normalfall, ein größerer ein Alarmzeichen |
| `stressed_min_soc` | der tiefste SOC im *gestressten* Szenario (untere Reserve der Pre-Drain-Rechnung), `None` wenn nicht berechnet |
| `threshold_horizon_end` | Ende des merge-begrenzten T\*-Suchhorizonts als ISO-String; **`null` = Vollhorizont-Scan** (F-NIGHT-RESCUE R7). Macht Schwellensprünge wie den 04:13-Vorfall erklärbar |
| `pv_window_ends` | je Tag die abgeleitete Endstunde des PV-Fensters |
| `realized` | **v0.24.0, F-REALIZED-SURPLUS:** der Ist-Bilanz-Block (`date`, die drei `lost_surplus_*`- und drei `prevented_export_*`-Werte, `early_feed_in_realized_kwh`, `true_export_total_kwh`). **Der Schlüssel fehlt komplett**, wenn kein Exportzähler konfiguriert ist — nicht als leeres Dict, denn die Karte verzweigt auf seine Existenz (ein `{}` ist in JS truthy und ergäbe ein dauerhaftes „(Ist 0.0)"). Die Karte ersetzt damit ihre Prognose-Segmente für verlorenen/verhinderten Überschuss durch die Ist-annotierte Form |
| `battery_min_soc_percent`, `battery_max_soc_percent`, `inverter_min_soc_percent`, `soc_buffer_percent` | aus `plan_params` **flach** hineingemischt (`**data["plan_params"]`) — der statische Planungskontext für die Karte. `soc_buffer_percent` ist hier der **konfigurierte** Wert; den effektiven (ggf. dynamischen) zeigt `consumption_profile.soc_buffer_effective` |

### 3.4 `consumption_profile`

`profile_diag` aus `coordinator._learned_series` + `learner.diagnostics()` +
dem Buffer-Diag:

| Feld | Bedeutung |
|---|---|
| `vacation_mode` | Urlaubsschalter aktiv |
| `computed_at`, `samples`, `profiles`, `validation` | aus `history_profile.ProfileLearner.diagnostics`: Zeitpunkt des letzten Nachtlaufs, Sample-Zahlen, **die gelernten Bins selbst** (W je Pfad × Tagestyp × Stunde, p50/p80) und der jeweils letzte Watchdog-Eintrag je Pfad |
| `ac_source`, `dc_source` | `"learned"` \| `"vacation_base"` \| `"static"` — womit die Slot-Reihe dieses Pfades tatsächlich gefüllt wurde |
| `ac_slot_coverage`, `dc_slot_coverage` | Anteil der Slots mit gelerntem Wert (0…1) |
| `ac_mean_delta_w`, `dc_mean_delta_w` | mittlere Abweichung gelernt − statisch (D-C6-Diagnose), `None` ohne gelernte Slots |
| `soc_buffer_source` | `"dynamic"` (Quantile aktiv) oder `"fixed"` |
| `soc_buffer_effective` | der tatsächlich verwendete Buffer in % |
| `buffer_uncertainty_wh`, `buffer_window_hours` | nur im Dynamikfall: aufsummierte Unsicherheit über das kritische Fenster (jetzt bis zum ersten Slot mit PV-Überschuss) und dessen Länge in Slots |

### 3.5 `gate_calibration`

`_gate_calibration_diag`: `threshold_v` (Ausgangsspannung der 48-V-PSU),
`volt_per_cell` (÷ `battery_cells_series`), `delivering_below_soc_max` (höchster
SOC, bei dem die Batteriespannung *unter* der PSU-Spannung lag — dort würde die
PSU liefern), `gated_above_soc_min` (tiefster SOC oberhalb der Schwelle),
`suggested_gate_soc` (Mittelwert, nur wenn das Bracket sauber ist) und
`gate_soc_active` (der konfigurierte Wert). Weil die Busspannung unter Last
einbricht, dürfen sich die Kanten überlappen; beide werden roh gezeigt.

---

## 4. Binary Sensors (`binary_sensor.py`)

| Klasse / Key | device_class | `is_on` | Attribute |
|---|---|---|---|
| `InverterRecommendationSensor` / `inverter_status` | `POWER` | `data["inverter_recommendation"]` — Empfehlung für den echten Entlade-Inverter nach Hysterese | `soc_threshold_percent`, **`floor_guard_active`** (G4: True, solange Überschusslasten gesperrt/abgeschaltet sind, weil sie netzgespeist liefen — SOC am Inverter-Floor oder Empfehlung aus) |
| `SurplusLoadRecommendationSensor` / `load_<subentry_id>` | – | `load_plans[id]["active"]` | `planned_hours`, `planned_energy_kwh` |
| `LoadPowerWarningSensor` / `load_power_warning_<subentry_id>` | `PROBLEM` | `load_plans[id]["power_warning"]` (F-L7-Latch) | `expected_power_w`, `measured_power_w`, `deviating_since` (ISO oder `None`) |
| `ApplianceStartWindowSensor` / `appliance_<subentry_id>` | – | `appliance_windows[id]` — „ein voller Gerätelauf könnte jetzt ohne Netzbezug starten". **G4-gekappt**: bei aktivem Floor-Guard immer `False` | – |
| `SupportPathSensor` / `support_dc24`, `support_dc48` | – | `coordinator.support_active(key)` — der von der Integration geschaltete Zustand des Stützpfads; immer verfügbar | – |

**Die wichtigste Interpretationsregel (Operator-Verwirrung 24.07.2026):**
`SurplusLoadRecommendationSensor` zeigt den **Plan**, nicht den Gerätezustand.
„Empfehlung an, Gerät aus" ist regulär und heißt: ein Gate/Dwell im Executor
hält gerade dagegen (min_off, G4-Floor-Guard, Latch, Verfügbarkeit) — Details in
Dokument 03.

**Die Power-Warning-Entity existiert nur bedingt**: es braucht einen
konfigurierten `power_entity` **und** `power_warning_percent > 0`. Wird eines
davon zurückgenommen, löscht `async_setup_entry` die Entity aus der Registry.
Analog die Appliance-Startfenster-Entity, die `opportunistic_start` verlangt.

---

## 5. Switches (`switch.py`)

| Klasse / Key | Wirkung |
|---|---|
| `BatteryManagerVacationSwitch` / `vacation_mode` | Solange an, plant der Planer mit dem gelernten **Absenz-Profil**. Der Zustand liegt im Learner-Store; seine **Recorder-Historie** wird zusätzlich benutzt, um vergangene Tage rückwirkend als Absenztage zu taggen (D-C4). Schalten triggert sofort `async_request_refresh()` |
| `SupportManualSwitch` / `support_dc24_manual`, `support_dc48_manual` | Operator-Handbetrieb je Stütz-PSU (F-N2/R3). **An** = PSU erzwungen an *und* die Automatik für sie pausiert (Winterbetrieb); **Aus** = Automatik zurück. Existiert nur bei konfiguriertem PSU-Switch. Geht über den einen Einstiegspunkt `coordinator.async_set_support_manual` |
| `SurplusLoadControlSwitch` / `load_control_<subentry_id>` | Per-Last „BM-Steuerung aktiv" (v0.7.17). **Aus** = der BM hält die Last `available=False`, nimmt sie aus dem Plan und schaltet sie im nächsten Zyklus ab — eine Ein-Klick-Pause, **ohne** die Konfiguration anzufassen. Persistiert (`load_bm_enabled`) |
| `BatteryManagerFeedInSwitch` / `early_feed_in` (v0.23.0) | Laufzeit-Ein/Aus der vorzeitigen Netzeinspeisung (F-FEEDIN R8). **An** (Default) = der Executor treibt den AC-Setpoint per Plan inkl. Trim; **Aus** = Pause, der nächste Executor-Pass schreibt einmalig 0 (nur Auto-Modus). Store-persistiert, immer verfügbar; existiert nur bei aktivem Options-Toggle `feedin_enabled`. Geht über den einen Einstiegspunkt `coordinator.async_set_feedin_enabled` |

---

## 6. Buttons (`button.py`)

Genau einer je Überschusslast: `SurplusLoadRuntimeResetButton`, Key
`load_runtime_reset_<subentry_id>`. **Er tut drei Dinge auf einmal**
(`coordinator.reset_load_runtime`):

1. **Laufzeitzähler auf 0** und, falls gerade ein Lauf tickt, dessen Cursor auf
   jetzt — nur Zeit nach dem Reset zählt.
2. **„Tank geleert"** (V6/F-TANK): ein neuer Tankzyklus beginnt. Ein
   *anstehender* Volltank-Capture wird **verworfen** (ein manueller Reset geht
   dem automatisch erkannten Latch-Release voraus und würde sonst eine
   verfrühte Probe lernen), und die Ein-pro-Zyklus-Benachrichtigungssperre wird
   neu scharf gestellt.
3. **Löst einen gelatchten F-L7-Power-Warning** (F10). Das ist die
   Deadlock-Bremse: F5 plant eine gelatchte Last mit ihren gemessenen ~2 W, der
   BM fordert sie deshalb nie an — und der Latch löst sich nur, während die Last
   BM-kommandiert und wieder in Band läuft. Der Reset ist das explizite
   Operator-Signal „Ursache behoben". Selbstkorrigierend: ist der Tank doch noch
   voll, bricht die Leistung beim nächsten Lauf wieder ein und der Latch kehrt
   nach dem F-L7-Dwell zurück.

Der Button ist **immer** verfügbar (auch ohne Plandaten).

---

## 7. Diagnostics, Services, Benachrichtigungen

### 7.1 Diagnostics-Download (`diagnostics.py`)

Standard-Endpoint `async_get_config_entry_diagnostics`. Inhalt:

| Block | Inhalt |
|---|---|
| `entry` | `version`, `minor_version`, Titel, `data`, `options` (redigiert) |
| `subentries` | **jede** Subentry mit `subentry_id`, `subentry_type`, `title`, `unique_id`, `data` — der Teil, den eine Forensik früher von Hand rekonstruieren musste |
| `integration_version` | aus dem Manifest |
| `core_config` | die effektive `SystemConfig`, flach: `battery`, `charger`, `inverter`, `pv`, `ac_profile`, `dc_profile`, `control`, `loads[]`. Schlägt der Bau fehl, steht dort `{"error": …}` — Diagnostics dürfen nie scheitern |
| `learned_state` | `coordinator.learned_state_snapshot()`, siehe unten |
| `last_plan_metrics` | `{valid, last_update, soc_threshold_percent, inverter_recommendation, floor_guard_active, input_soc_percent, grid_import_kwh, grid_export_kwh, lost_surplus_kwh, prevented_export_kwh (Summe über `daily`), daily_surplus}` bzw. `{"valid": False}` |

**`learned_state_snapshot()`** ist der Forensik-Schatz — genau das, was sonst
nirgends sichtbar ist:

- `startup`: `startup_complete`, `successful_updates`, `startup_at`,
  `last_valid_soc_percent`, `last_soc_update`
- `threshold_inertia.displayed_threshold_percent` — der **angezeigte** T\*
  (erklärt, warum der Sensor nicht dem Rohplan folgt)
- `floor_guard.active`
- `learned_power_w` je Last — die persistierte gelernte Planungsleistung
- `load_runtime_seconds` je Last
- `power_warning_latched` je Last (F-L7)
- `soc_stale_frozen_soc` je Last (G2-Latch, mit dem eingefrorenen SOC)
- `telemetry_freeze_stale` (F4, sortierte Liste)
- `tank`: `diag` (wie am Runtime-Sensor), `samples` (die Rohproben),
  `pending_full_min` (Capture am Latch-Eintritt)
- `support`: `state`, `manual`, `dc48_controller`

`TO_REDACT` deckt nur generische HA-Schlüssel ab (`latitude`, `longitude`,
`api_key`, `token`, `password`, `access_token`); **Entity-IDs werden bewusst
NICHT redigiert** — ohne sie ist der Dump nicht interpretierbar, und sie sind
kein Geheimnis.

### 7.2 Services (`services.yaml`, registriert in `__init__.py`)

Beide sind **domainweit** (nicht je Entry) und werden beim Unload des letzten
Entrys wieder entfernt. Gemeinsames Schema: `entry_id` (optional, sonst der
erste Entry; UI-Selector `config_entry`), `file_path` (optional),
`download` (Default `false`), `as_table` (Default `true`). Beide tragen seit
v0.17.0 `name`/`description` für die Service-UI.

| Service | Wirkung |
|---|---|
| `battery_manager.export_hourly_details` | Schreibt die `hourly_details` des letzten Plans als Tabelle (oder JSON-Lines) in eine Datei. Je Slot: `hour`, `datetime`, `duration_minutes`, Start-/End-SOC, `pv_production_wh`, `ac_consumption_wh` (**inklusive** `extra_ac_wh`), `dc_consumption_wh`, `surplus_load_wh`, `grid_import_wh`, `grid_export_wh`, `battery_charge_wh`, `battery_discharge_wh`, `inverter_enabled`, `support_dc24/48`, die F-N3-Zwei-Bus-Werte (`psu48_delivered_wh`, `psu24_delivered_wh`, `dcdc_input_wh`, `dcdc_loss_wh`, `unserved_dc_wh`, `gate_open`) und `profile_sources` (`"learned/static"` o. ä. je Pfad) |
| `battery_manager.export_learned_profiles` | Schreibt `learner.export_snapshot()`: `computed_at`, `window_days`, `profiles`, `samples`, `diagnostics`, `validation`, `day_log` |

**Export-Pfade (v0.17.0, Breaking Change):** ohne `download` landet die Datei
unter `<config>/battery_manager/`, mit `download: true` unter
`<config>/www/battery_manager/` und eine persistente Benachrichtigung liefert
den `/local/`-Link samt Warnhinweis. Downloads laufen nach **1 h ab** (TTL
`_DOWNLOAD_TTL_S = 3600`, per Datei-Timer plus Sweep beim Start), weil
`/local/` unauthentifiziert ausgeliefert wird und der Export gelerntes
Haushaltsverhalten enthält. Erlaubt sind nur `.txt`/`.json` innerhalb des
Export-Verzeichnisses; `.storage` und Traversal sind gesperrt
(`_validate_file_path`, `Path.is_relative_to`, keine Punktdateien, keine
Nullbytes). Fehler (inkl. ungültigem Pfad) werden als **Service-Fehler**
gemeldet (`ServiceValidationError`/`HomeAssistantError`), nicht nur geloggt.
Alte Automatisierungen mit eigenem absolutem `file_path` außerhalb des
Export-Verzeichnisses schlagen damit fehl und müssen auf das neue
Verzeichnis zeigen.

### 7.3 Push-Benachrichtigungen

Global, nicht je Last: die Options-Liste `power_warning_notify_targets`
(Namen von `notify`-Services, z. B. `mobile_app_pixel`) plus
`power_warning_notify_on_resolve` (Default `True`). Genutzt von:

- **Power-Warning** (`_notify_power_warning`) beim Setzen und — sofern nicht
  stummgeschaltet — beim Lösen des Latches.
- **„Tank bald voll"** (`_notify_tank_full_soon`): Titel
  `🪣 <Name>: tank nearly full`. Feuert **einmal pro Tankzyklus** (Latch →
  Reset), wenn die prognostizierte Rest-**Laufzeit** unter
  `TANK_WARN_LEAD_MIN = 60 min` fällt **und** die Last im aktuellen Plan noch
  laufen soll. Laufzeitminuten, nicht Wanduhr — ein stehendes Gerät warnt nie.
  Eine bereits als voll gelatchte Last wird übersprungen (die F-L7-Warnung deckt
  sie ab).

Die Auswahlliste im Options-Flow (`_notify_services`) blendet `send_message`
(braucht eine `entity_id`) und `persistent_notification` aus; `custom_value` ist
erlaubt, damit ein gerade nicht registriertes Ziel gespeichert bleibt.

---

## 8. Config-Flow: der Haupt-Entry

`config_flow.BatteryManagerConfigFlow`, `VERSION = 2`, `MINOR_VERSION = 3`.
Einrichtung in **sechs Schritten**: `user` → `battery` → `pv` → `consumers` →
`power` → `control` (danach `async_create_entry`). Zusätzlich der
Reconfigure-Schritt und der Options-Flow.

**Single-Instance (v0.17.0):** es ist nur **ein** Config-Entry erlaubt —
`async_step_user` bricht mit `single_instance_allowed` ab, sobald bereits ein
Entry existiert.

Konvention im ganzen Flow: **Zahlenfelder** sind `NumberSelector` im BOX-Modus
mit Min/Max/Step; **optionale Entity-Felder** nutzen
`description={"suggested_value": …}` statt `default`, damit sie in der UI
**leerbar** bleiben. Defaults kommen über `_d(config, key)` aus
`const.DEFAULT_CONFIG` — dieselbe Quelle, aus der auch der Coordinator seinen
Fallback zieht, damit ein „Speichern ohne Änderung" nie das Verhalten
verschiebt.

### 8.1 Schritt `user` — Eingangs-Entities (Pflicht)

`soc_entity`, `pv_forecast_today_entity`, `pv_forecast_tomorrow_entity`,
`pv_forecast_day_after_entity` (alle `sensor`). Die drei PV-Entities liefern
kWh/Tag als State und tragen die Stundenattribute `wh_period` /
`wh_period_p10` / `wh_period_p90`.

### 8.2 Schritt `battery`

| Feld | Default | Bereich | Wirkung |
|---|---|---|---|
| `battery_capacity_wh` | 5000 | 100…1 000 000, Schritt 100 | nutzbare Kapazität; Bezugsgröße jeder Wh→SOC-Umrechnung |
| `battery_min_soc_percent` | 5 | 0…100 | unterer Rand des Modells |
| `battery_max_soc_percent` | 95 | 0…100 | oberer Rand; darüber ist Ladeenergie „verloren" |
| `battery_charge_efficiency` | 0,97 | 0,1…1,0 | η laden |
| `battery_discharge_efficiency` | 0,97 | 0,1…1,0 | η entladen |

Validierung: `min >= max` ⇒ `min_soc_above_max`.

### 8.3 Schritt `pv` (statisches Zwei-Fenster-Modell)

| Feld | Default | Wirkung |
|---|---|---|
| `pv_max_power_w` | 3200 | Peak-Cap je Slot (`pv.peak_power_w * duration`) — greift **auch** bei Stundenprognosen |
| `pv_morning_start_hour` | 7 | Beginn Vormittagsfenster |
| `pv_morning_end_hour` | 13 | Ende Vormittag / Beginn Nachmittag |
| `pv_afternoon_end_hour` | 18 | Ende Nachmittag |
| `pv_morning_ratio` | 0,8 | Anteil der Tagesenergie im Vormittagsfenster |

Validierung `_validate_pv_windows`: strikt `morning_start < morning_end <
afternoon_end`, sonst `pv_windows_out_of_order` — eine entartete Reihenfolge
würde stillschweigend einen festen Anteil jeder Tagesprognose verwerfen.

**Wichtig:** diese fünf Werte sind der **Fallback**. Sobald `wh_period`-Buckets
für einen Tag existieren (`pv_source == "hourly"`), speisen sie nur noch den
Residual-Anteil unabgedeckter Stunden.

### 8.4 Schritt `consumers` (zwei Sektionen)

**Sektion `consumption_profile`** — die acht statischen Fallback-Werte
(`_PROFILE_KEYS`): `ac_base_load_w` (50), `ac_variable_load_w` (75),
`ac_variable_start_hour` (6), `ac_variable_end_hour` (20), `dc_base_load_w`
(50), `dc_variable_load_w` (25), `dc_variable_start_hour` (6),
`dc_variable_end_hour` (22). Sie gelten slotweise überall dort, wo kein
gelernter Bin existiert (D-C6).

**Sektion `consumption_learning`** (eingeklappt) — die Messquellen:
`ac_load_entity` / `dc_load_entity` (Direktsensor) **oder** je Pfad eine
Zähler-Bilanz `ac_balance_in_entities` / `ac_balance_out_entities` bzw.
`dc_balance_in_entities` / `dc_balance_out_entities` (Mehrfachauswahl), dazu
`workday_entity` (`binary_sensor`, taggt Feiertage). Der Direktsensor hat
Vorrang vor der Bilanz. Validierung `_validate_learning_sources`: Outflow ohne
Inflow ⇒ `balance_out_without_in`.

### 8.5 Schritt `power` (Wandler)

| Feld | Default | Wirkung |
|---|---|---|
| `charger_max_power_w` | 2300 | Ladeleistungs-Deckel AC→DC |
| `charger_efficiency` | 0,92 | η Laden |
| `charger_standby_power_w` | 10 | Standby-Zehrung des Ladegeräts (der 10-W-Artefakt hinter dem Z2-Veto, Dokument 06) |
| `inverter_max_power_w` | 2300 | Entladeleistungs-Deckel DC→AC |
| `inverter_efficiency` | 0,95 | η Entladen |
| `inverter_standby_power_w` | 15 | Standby des Inverters |
| `inverter_min_soc_percent` | **20** | **Inverter-Floor** — darunter liefert der Inverter nichts; identisch der Trip-Schwelle des G4-Floor-Guards |

### 8.6 Schritt `control` (drei Sektionen)

**Sektion `planner_tuning`:**

| Feld | Default | Bereich | Wirkung |
|---|---|---|---|
| `soc_buffer_percent` | 5 | 0…30 % | statischer Sicherheitspuffer über dem Floor; wird bei aktiven Quantilen durch den dynamischen Buffer ersetzt |
| `hysteresis_percent` | 1 | 0…10 % | Totband der Inverter-Empfehlung um T\* |
| `threshold_inertia_percent` | 2 | 0…10 % | T\* wird erst übernommen, wenn er sich um mindestens so viel bewegt |
| `min_switch_interval_s` | 60 | 0…3600 s | Mindestabstand zweier Empfehlungswechsel |
| `pv_forecast_mode` | `auto` | auto/hourly/daily | `auto` = stündlich wenn vorhanden, sonst Zwei-Fenster; `hourly` warnt zusätzlich **einmalig**, wenn keine Buckets kommen; `daily` ignoriert die Stundenattribute komplett (Golden-Anker) |
| `predrain_pv_confidence` (**α**) | **0,5** | 0,2…1,0 | Skalar-Fallback für den Pre-Drain, wo **kein** p10/p90-Band greift. Höher = mehr Vertrauen in die Prognose = tieferer Pre-Drain |
| `upper_pv_reserve` (**β**) | **1,2** | 1,0…1,5 | Gegenstück nach oben (Reserve gegen Unterschätzung) |
| `strong_pv_cutoff_w` | 200 | 50…1000 W | ab welcher Leistung ein Slot als „starke PV" gilt |
| `pv_window_end_hour` | **kein Default** | 10…20 h | optionaler Standort-Override der PV-Fenster-Endstunde; **leer = aus der Prognoseform ableiten**. Bewusst nicht in `DEFAULT_CONFIG` |

Achtung auf die **Doppel-Defaults**: die Dataclass-Defaults in
`core/model.ControlParams` sind neutral (α=β=1,0, Pre-Drain-Ratio 0), damit die
Golden-Tests eingefroren bleiben. Die *empfohlenen Live-Werte* stehen in
`const.DEFAULT_CONFIG` und werden vom Coordinator explizit gesetzt. Der
retirete Schlüssel `import_trade_ratio` (F-STRICT-SURPLUS R1, 19.07.2026) wird
ignoriert, falls noch gespeichert.

**Sektion `support_paths`:** `support_dc48_switch_entity`,
`support_dc48_power_w` (60 W), `support_dc24_switch_entity`,
`support_dc24_power_entity` (optionaler Leistungssensor der 24-V-PSU — ohne ihn
können Stunden mit netzgespeister 24-V-Schiene nicht gelernt werden),
`dcdc_switch_entity`, `support_switch_delay_s` (3 s) und die **vier absoluten
SOC-Schwellen**: `support_dc24_activate_soc` (10), `support_dc24_recovery_soc`
(11), `support_dc48_activate_soc` (5,5), `support_dc48_recovery_soc` (10).

Validierungen: die drei Switches müssen **verschieden** sein
(`support_entities_not_distinct` — eine geteilte Entity würde die
Make-before-break-Sequenz die einzige Versorgung der Schiene abschalten lassen),
und die Schwellenleiter muss stimmen (`_validate_support_hysteresis`): je Stufe
`activate < recovery`, und die tiefere 48-V-Notstufe muss bei **beiden** Werten
≤ der 24-V-Stufe liegen. Fehlercodes:
`support_dc24_recovery_not_above_activate`,
`support_dc48_recovery_not_above_activate`, `support_dc48_activate_above_dc24`,
`support_dc48_recovery_above_dc24`.

**Sektion `dc_devices`** (F-N3, `_device_param_fields`) — **alle Defaults
neutral**, die Planung ändert sich erst mit echten Werten:
`battery_voltage_entity` (optional), `native48_base_w` (0),
`dc24_share_percent` (100), je Wandler ein Tripel Spannung/η/Max-Strom
(`dcdc_*` 24 V/1,0/0 A, `psu24_*` 24 V/1,0/0 A, `psu48_*` **49,56 V**/1,0/0 A;
**0 A = ungedeckelt**), `battery_cells_series` (16), `gate_soc_percent` (100 =
Gate immer offen) sowie der R2-Regler: `psu48_on_voltage_v` (49,56),
`psu48_off_voltage_v` (49,8), `psu48_controller_log_only` (**True** — erst nach
einem Shakedown scharf schalten). Validierung `_validate_controller_voltages`:
`off <= on` ⇒ `controller_off_below_on` (das Hystereseband würde kollabieren
und der Regler flattern).

### 8.7 Reconfigure (F-RECONFIGURE-PV)

`async_step_reconfigure` ändert **nur** die vier Basis-Entities
(SOC + drei PV-Quellen) und ruft `async_update_reload_and_abort`. Alles andere
— Batterie, Control, Support, DC — und **alle Last-Subentries** bleiben
erhalten (die liegen in `entry.subentries`, nicht in `entry.data`). Ein
PV-Quellenwechsel behält also Prioritäten, gelernte Leistung und
Laufzeitzähler.

### 8.8 Options-Flow

`BatteryManagerOptionsFlow` hat **einen** Schritt (`init`) mit **elf**
einklappbaren Sektionen: `battery`, `pv`, `power` (seit v0.17.0 die
**Basisdimensionen** — Kapazität/SOC-Grenzen, PV-Modell, Lade-/Inverter-
Leistung und Wirkungsgrade; nur die Batterie-Wirkungsgrade bleiben
Wizard-only), dann `planner_tuning`, `consumption_profile`,
`consumption_learning`, `support_paths`, `dc_devices`, `notifications`,
(seit v0.23.0) `early_feed_in` und (seit v0.24.0) `surplus_accounting`.
HA verschachtelt die Felder unter dem Sektionsschlüssel; `_flatten_sections`
macht daraus wieder ein flaches Dict. Die Basisdimensionen sind damit
**nachträglich änderbar, ohne Subentries oder Lernstände zu verlieren** —
die liegen in `entry.subentries` bzw. den Stores, die ein Options-Save nicht
anfasst (kein Löschen + Neuanlegen mehr).

Zusätzlich zum Einrichtungsflow bietet die Sektion `consumption_learning`:
`learning_window_days` (Default **120**, 14…120), `learning_max_age_days` (14,
3…60), `profile_half_life_days` (30, 7…120), `buffer_min_percent` (3,0, 0…10)
und `buffer_max_percent` (15,0, 5…30). Validierung `_validate_buffer_clamps`:
`min >= max` ⇒ `buffer_min_above_max`.

Sektion `notifications`: `power_warning_notify_targets` (Mehrfachauswahl,
Default leer) und `power_warning_notify_on_resolve` (Default `True`).

Sektion `early_feed_in` (v0.23.0, F-FEEDIN, `_feedin_schema_fields` — **nur
Options-Flow**):

| Feld | Default | Bereich | Wirkung |
|---|---|---|---|
| `feedin_enabled` | **`False`** | bool | Opt-in der vorzeitigen Netzeinspeisung; legt bei AN die Entities `switch…early_feed_in` und `sensor…feedin_mode` an |
| `feedin_setpoint_entity` | – | `input_number` | AC-Setpoint des externen Reglers (negativ = Export); Pflicht wenn enabled |
| `feedin_battery_power_entity` | – | `sensor` | Batterie-Leistung (positiv = ladend) für den ereignisgesteuerten Trim; Pflicht wenn enabled; wird in die getrackten Inputs aufgenommen |
| `feedin_max_w` | 1000 | 0…2000 W | Leistungsdeckel des Feed-in (0 = aus, Neutral-Default wirksam) |
| `feedin_min_soc_percent` | 30 | 0…100 % | absoluter SOC-Floor des Features; muss **über** `battery_min_soc_percent` liegen |
| `feedin_deadline_hour` | 9 | 0…12 h | weiche Deadline, bis zu der die Tages-Zielmenge abgegeben sein soll |

Validierung `_validate_feedin`: enabled ohne beide Entities ⇒
`feedin_entities_required`; `max_w` außerhalb 0…2000 ⇒
`feedin_max_w_out_of_range`; `min_soc` nicht über dem Batterieminimum ⇒
`feedin_min_soc_not_above_battery_min`. Geleerte Entity-Felder werden explizit
als `None` geschrieben (die Feinheit unten), was das Feature entdrahtet — der
Executor schreibt dann nichts mehr.

Sektion `surplus_accounting` (v0.24.0, F-REALIZED-SURPLUS,
`_surplus_accounting_schema_fields` — **nur Options-Flow**, genau ein Feld):

| Feld | Default | Bereich | Wirkung |
|---|---|---|---|
| `export_meter_entity` | – (**nicht in `DEFAULT_CONFIG`**) | `sensor`, `device_class: energy` | monotoner kWh-Exportzähler; **schaltet die Ist-Bilanz frei** — legt die sieben Sensoren aus §2.2b (ohne den Feed-in-Ist-Sensor) an und veröffentlicht den `realized`-Datenblock. Geleert ⇒ explizit `None` ⇒ wieder reiner Prognosebetrieb, die Sensoren werden aus der Registry entfernt |

Keine Validierung nötig (ein einzelnes optionales Feld); das Feld nutzt
`suggested_value` statt `default`, damit es leerbar bleibt.

**Feinheit beim Leeren von Feldern:** ein geleertes Selector-Feld fehlt in der
Eingabe komplett. Weil `coordinator.raw_config` `entry.data` **und**
`entry.options` mergt, würde der Altwert aus `data` weiterwirken. Der Flow
schreibt deshalb explizit `None` (Support-Switches,
`support_dc24_power_entity`, `battery_voltage_entity`, `pv_window_end_hour`,
`ac_load_entity`/`dc_load_entity`, `workday_entity`, `export_meter_entity`)
bzw. `[]`
(Bilanz-Entity-Listen, `power_warning_notify_targets`).

---

## 9. Subentry-Typ `surplus_load`

`SurplusLoadSubentryFlow`, Subentry-Typ-Key **`surplus_load`**. Zwei Schritte:
`user`/`reconfigure` (Basis) und — **nur wenn `energy_limited` an ist** —
`storage`. Für einen kontinuierlichen Verbraucher wie den Entfeuchter sind die
Speicherfelder bedeutungslos (Operator-Wunsch 05.07.2026).

### 9.1 Basis-Schritt

| Feld | Default | Bereich | Wirkung |
|---|---|---|---|
| `name` | – | Text | wird zum **Subentry-Titel** (aus `data` herausgepoppt) |
| `priority` | s. u. | 1…N | **1 = höchste** (F-LOAD-PRIORITY R1). Der Selektor-Bereich ist ehrlich: 1…N über alle Last-Subentries, beim Anlegen inklusive der neuen. Default beim Reconfigure = die **aktuelle effektive** Position (nicht der rohe gespeicherte Wert), beim Anlegen = N (neue Lasten hängen hinten an) |
| `power_w` | 300 | 1…10 000 W, Schritt 10 | **Nennleistung** — nur der letzte Fallback in der Präzedenzkette `saturated > measured > learned > nominal` |
| `battery_tolerance_percent` | 15 | 0…50 % | wie tief die Hausbatterie für diese Last herhalten darf |
| `min_runtime_min` | 30 | 0…240, Schritt 5 | Mindestlaufzeit (Dwell) nach dem Einschalten |
| `min_off_min` | 30 | 0…240 | Mindestpause. **Back-Compat:** fehlt der Schlüssel (Last vor 0.7.15), ist der Formulardefault `min_runtime_min` — genau der Coordinator-Fallback, damit ein Speichern ohne Änderung die OFF-Pause nie still verkürzt |
| `energy_limited` | `False` | bool | „hat einen eigenen Speicher mit Ziel-SOC" ⇒ blendet den `storage`-Schritt ein |
| `in_house_measurement` | **`True`** | bool | **betrifft ausschließlich das Grundlast-Lernen.** `True` = die Last hängt *hinter* dem Messknoten (Netzzähler), ihr Verbrauch steckt in der gemessenen Hauslast ⇒ der Lerner **subtrahiert** sie. `False` = sie wird *außerhalb* versorgt (Einspeise-Sollwert oder schlicht ein Stromkreis, den der Zähler nicht sieht) ⇒ **nicht** subtrahieren, sonst wird die Grundlast doppelt bereinigt und systematisch zu niedrig gelernt. Die **Planung** berührt das Flag nicht: eine Überschusslast ist in beiden Fällen zusätzliche AC-Last (`extra_ac_wh`), also genau einmal gezählt; auch G4 und die gelernte Ist-Leistung sind topologieunabhängig |
| `power_warning_percent` | **0 (= aus)** | 0…200 %, Schritt 5 | F-L7: ab welcher relativen Abweichung der Ist- von der Planungsleistung gewarnt wird. 0 legt auch die Warn-Entity nicht an |
| `power_warning_dwell_min` | 15 | 0…240 | wie lange die Abweichung anhalten muss (kurze Abtaupausen bleiben darunter) |
| `tank_full_runtime_min` | **0 (= aus)** | 0…6000, Schritt 15 | **V6/F-TANK**: Start-Schätzung der Volltank-Laufzeit in Minuten. Sobald ein Tankzyklus beobachtet wurde, gewinnt der gelernte Median. **0 = Feature aus** (Sicherheitsanker: exakt das Verhalten vor dem Feature). Nur sinnvoll mit Leistungs-Feedback |
| `power_entity` | – (optional) | `sensor` | Leistungs-Feedback. Voraussetzung für Median-Lernen, F-L7, G2/F4-Wächter und das Tank-Modell |
| `energy_entity` | – (optional) | `sensor`, `device_class: energy` | **v0.24.0, F-REALIZED-SURPLUS:** eigener kWh-Zähler der Last (z. B. Fritz!Powerline). Der gemessene Verbrauch ersetzt die Laufzeit-×-Leistung-Schätzung im „verhinderter Export Ist" und **korrigiert** — bei `in_house_measurement = False` — den Exportzähler um genau diese Energie. Unplausible Zählersprünge werden verworfen (R4). Fehlt der Schlüssel, bleibt alles beim Schätzwert (keine Migration) |
| `availability_entity` | – (optional) | beliebig | „Last ist überhaupt da" |
| `control_switch_entity` | – (optional) | `switch` | **der Schalter, den der BM betätigt.** Gilt für *jede* gesteuerte Last (auch den kontinuierlichen Entfeuchter), liegt deshalb auf dem Basis-Schritt |
| `input_off_policy` | `auto` | auto/always_off/keep_on | Was am Ende eines Laufs mit dem Eingangs-Stecker passiert: `auto` = nur ausschalten, wenn *wir* ihn eingeschaltet haben (persistiert als `plug_owned`); `always_off` = immer aus; `keep_on` = anlassen, nur die Lade-Freigabe schalten |

### 9.2 Speicher-Schritt (nur `energy_limited`)

| Feld | Default | Wirkung |
|---|---|---|
| `capacity_wh` | 2000 | Speicherkapazität der Last; Basis für `remaining_energy_wh` |
| `target_soc_percent` | 100 | **Ziel-SOC**; darüber wird nicht mehr geladen. Bei der Referenz-Fossibot live 90 |
| `soc_entity` | – (optional) | SOC-Sensor der Last; fehlt er, plant sie als „leer" |
| `charge_enable_entity` | – (optional) | `input_boolean` oder `switch` — das **Lade-Gate** (Firmware-Freigabe), getrennt vom Stecker |

Die Werte der beiden Speicherfelder werden **konserviert**, wenn man
`energy_limited` aus- und wieder einschaltet (`_STORAGE_KEYS`,
`_STORAGE_ENTITY_KEYS` in `_finish`).

### 9.3 Validierung des Ladepfads (`_validate_load_control`)

- `input_off_policy == keep_on` **ohne** `charge_enable_entity` ⇒
  `keep_on_requires_enable` (es gäbe nichts zu schalten).
- `control_switch_entity == charge_enable_entity` ⇒
  `control_entities_not_distinct`.

Bei einer kontinuierlichen Last wird direkt im Basis-Schritt geprüft, bei einer
energielimitierten erst im Speicher-Schritt über die **Vereinigung** beider
Eingaben.

### 9.4 Prioritäten-Renumbering (F-LOAD-PRIORITY R4)

`_renumber_siblings` setzt die bearbeitete Last an die gewünschte Position
*innerhalb der effektiven Reihenfolge ihrer Geschwister* und nummeriert alles
dicht 1…N. Geschrieben wird nur, wo sich die **effektive** Priorität wirklich
ändert (gespeicherter Wert, sonst die R3-Einfüge-Position) — denn **jeder
Geschwister-Write lädt den Entry neu**. Ein Anlegen an der Default-Position
(R5) und ein unverändertes Speichern eines dichten Zustands (R6) bleiben
schreibfrei.

---

## 10. Subentry-Typ `appliance`

`ApplianceSubentryFlow`, Typ-Key **`appliance`**. Ein Schritt. Haushaltsgeräte
(Wasch-/Spülmaschine) werden **nicht geschaltet**, nur erkannt und beraten.

| Feld | Default | Bereich | Wirkung |
|---|---|---|---|
| `name` | – | Text | Subentry-Titel |
| `detection_entity` | – (Pflicht) | beliebige Entity | Erkennungsquelle: numerisch = Leistung, sonst Zustandsvergleich gegen `APPLIANCE_RUNNING_STATES = {on, run, running, washing, active, wash, rinsing, spinning, drying}` (die drei Letzteren seit v0.25.4 — Frontlader-Phasen, sonst bricht die Erkennung mitten im Zyklus ab) |
| `power_threshold_w` | 10 | 1…3000 W | **Einschalt**schwelle der Lauferkennung |
| `off_threshold_w` | 5 | 0…3000 W | **Ausschalt**schwelle (Hysterese). **Back-Compat:** fehlt der Schlüssel, ist der Formulardefault die On-Schwelle — also *keine* Hysterese, exakt der Coordinator-Fallback |
| `run_energy_wh` | 1000 | 10…10 000 Wh | Energie eines vollen Laufs — speist `ApplianceRun.remaining_energy_wh` |
| `run_duration_h` | 2,5 | 0,25…12 h, Schritt 0,25 | Dauer eines vollen Laufs |
| `opportunistic_start` | `False` | bool | legt den Startfenster-Binary-Sensor an („ein voller Lauf ginge jetzt ohne Netzbezug"). Aus ⇒ die Entity wird aus der Registry entfernt |

Ein laufendes Gerät wird als **Restenergie mit konstanter Leistung auf die
kommenden Slots verteilt und auf `ac_wh` addiert** (`series._apply_appliance_runs`)
— Appliances sind damit Teil der AC-Prognose und tauchen **nie** in
`extra_ac_wh` auf. Genau deshalb enthält `daily[].loads_kwh` per Konstruktion
nur Überschusslasten.

---

## 11. Was löst einen Reload aus?

`__init__.async_setup_entry` registriert
`entry.add_update_listener(async_reload_entry)`. Damit lädt der Entry **bei
jeder Änderung an `entry.data`, `entry.options` oder einer Subentry** komplett
neu — also bei:

- jedem Speichern im **Options-Flow** (auch ohne inhaltliche Änderung),
- jedem **Reconfigure** des Haupt-Entrys (`async_update_reload_and_abort`),
- jedem **Anlegen/Ändern/Löschen** einer Last- oder Appliance-Subentry,
- **jedem einzelnen Geschwister-Write** des Prioritäten-Renumberings (§9.4) —
  deshalb die Sparsamkeitsregel dort.

Ein Reload verwirft allen **In-Memory**-Zustand (G2-/F4-/F8-/F10-Latches,
Floor-Guard, Sample-Puffer, Tick-Cursor) und stellt nur den persistierten Teil
wieder her. Weil ein Reload **kein** `EVENT_HOMEASSISTANT_FINAL_WRITE` feuert,
flusht `async_unload_entry` den Store explizit
(`async_flush_persistent_state`) — sonst ginge z. B. ein frisch gesetzter
Power-Warning-Latch verloren. Vorher werden laufende Schalt-Tasks abgebrochen
(`async_cancel_actuation_tasks`), damit nach dem Flush nichts mehr am Zustand
dreht.

`async_remove_entry` löscht **beide** Stores (Coordinator-Store und
Learner-Store).

**Entry-Migrationen** (`async_migrate_entry`): v1→v2 entfernt die alten
Controller-/Zusatzlast-Keys; v2.2 hebt ein `learning_window_days == 42`
(exakt der Alt-Default) auf 120; v2.3 backfillt die vier absoluten
Grid-Support-Schwellen aus dem alten `soc_min`/`buffer`, damit ein Upgrade die
Schaltpunkte nicht verschiebt (nur, wenn noch keine gesetzt sind).

---

## 12. Übersetzungen

`translations/de.json` und `translations/en.json` decken `config`,
`config_subentries`, `options`, `selector`, `entity` und `issues` ab. Die
Entity-Namen mit `{name}`-Platzhalter (`{name} Empfehlung`, `{name} aktive
Laufzeit`, `{name} Leistungswarnung`, `{name} BM-Steuerung`, `{name} Laufzeit
zurücksetzen`, `{name} Startfenster`) werden über
`_attr_translation_placeholders = {"name": subentry.title}` gefüllt.

Zwei `selector`-Blöcke tragen Klartext für Dropdowns:
`input_off_policy` (auto / always_off / keep_on) und `pv_forecast_mode`
(auto / hourly / daily).

**Merke:** die deutschen Namen erscheinen in der UI, die **englischen** haben
die Entity-IDs geprägt. Wer eine Entity-ID rät, rät falsch — Registry lesen.

---

**Verifikation: geprüft gegen Commit `a172d48` (v0.16.2); die v0.17.0-Einträge
gegen den Arbeitsstand vom 2026-07-31.**

Wichtigste Code-Anker dieses Dokuments:

1. `custom_components/battery_manager/sensor.py::BatteryManagerSocForecastSensor.extra_state_attributes`
   — der vollständige Attributvertrag des Prognosesensors.
2. `custom_components/battery_manager/coordinator.py::BatteryManagerCoordinator._async_update_data`
   (Rückgabe-Dict ab `return {"valid": True, …}`) — die Quelle **jedes** oben
   genannten Attributs, inkl. `soc_forecast`-Punktbau (naiv-lokale Zeitstempel).
3. `custom_components/battery_manager/const.py` — `DEFAULT_CONFIG`,
   `DEFAULT_LOAD_CONFIG`, `DEFAULT_APPLIANCE_CONFIG`, alle `CONF_*`-Keys und die
   Entity-Keys.
4. `custom_components/battery_manager/config_flow.py::SurplusLoadSubentryFlow._basic_schema`
   / `_storage_schema` / `_renumber_siblings` — die Lastoptionen samt
   Prioritätslogik.
5. `custom_components/battery_manager/coordinator.py::learned_state_snapshot`
   und `diagnostics.py::async_get_config_entry_diagnostics` — der
   Forensik-Dump.
