# Anlage & Betriebs-Runbook

**Stand: `main` @ v0.17.0 (Arbeitsstand 2026-07-31).** Dieses Dokument beschreibt die
**konkrete Referenz-Anlage des Betreibers** (Batterie, PV, Messkreis, Lasten,
Stützpfade, Legacy-Automatisierungen) und die **Handgriffe im Betrieb**:
deployen, diagnostizieren, Logs und History korrekt lesen, Meldungstexte
einordnen. Die Entity-/Options-Referenz steht in
`04-ha-integration-entities-services.md`, die Schaltregeln in
`03-executor-und-lastregeln.md`, die Vorfallshistorie in
`06-forensik-juli-2026-und-offene-punkte.md`.

Alle Anlagenfakten sind **live verifiziert (Forensik 24./25.07.2026)** und als
solche gekennzeichnet. Wo eine Repo-Doku noch etwas anderes behauptet, steht es
dabei. **Im Zweifel gilt die Anlage bzw. der Code.**

---

## 0. Doku-Stand im Repo — was gilt, was nicht

| Datei | Stand | Nutzung |
|---|---|---|
| `docs/STRATEGY.md` | **veraltet** (v0.7.x, Eigenetikett „historical design rationale") | nur als Begründung des Grundgedankens: Zielfunktion = Import + Export minimieren, T\* durch Policy-Simulation statt Formel. Die dort skizzierte Allokationsschleife (Z2/Z3, „drop hour/load, repeat") ist **nicht mehr** die Implementierung — es gilt Dokument 02 |
| `docs/ARCHITECTURE.md` | **größtenteils aktuell** — Code-Map und Update-Zyklus werden gepflegt (ergänzt 2026-07, v0.17.0) | Einstieg in den Code; die Gate-Beschreibungen im Detail stehen in Dokument 02 |
| `docs/ALGORITHM.md`, `docs/REQUIREMENTS.md` | **historisch** (v0.7.x; Eigenetikett seit v0.17.0) | Referenzen für Regelnamen (D-A1…D-A9, D-C1…D-C8), nicht fürs Verhalten; verbindlich: Dokument 02 |
| `docs/DC_TOPOLOGY.md` | **teils stale**: Spezifikation der F-N3-Phasen 0–7 (implementiert seit v0.7.9/0.7.10). Die Spalte „Live value (operator)" ist ein **Planungsstand von 2026-07-05**, nicht der heutige Live-Zustand | Physik der Zwei-Bus-Modellierung und die R2-/R3-Regeln sind gültig; die konkreten Zahlen gegen die Live-Config prüfen |
| `docs/LOAD_CONTROL.md` | **aktuell**, wird fortgeschrieben (bis §14 `in_house_measurement`, V3 vom 24.07.2026; §15/§16 v0.17.0) | verbindliche Quelle für Schaltsemantik, Floor-Guard, Stale-SOC-Guard, Robust-Power, Seamless-Runs |
| `docs/F-*.md` | je F-Doc die **Spezifikation seines Features** | bei Konflikt mit den Zusammenfassungen dieser Wissensbasis gilt die Wissensbasis (Code-geprüft); Supersessions werden im F-Doc selbst annotiert |
| `docs/CONSUMPTION_FORECAST.md` | aktuell für die Lernschicht | D-C-Regeln |
| `docs/F-CONSUMPTION-PROFILES.md` | **Strategie (2026-08-08), nicht implementiert** — konfigurierbare Verbrauchsprofile (Homeoffice/…) + Jahreszeit-Gewichtung; enthält die Auseinandersetzung mit dem EMHASS-Alternativvorschlag | D-P-Regeln; baut auf D-C auf |

---

## 1. Die Referenz-Anlage (Stand 25.07.2026)

### 1.1 Zugang

- **Home Assistant:** `http://hass:8123` (extern `hass.rsn.ro-la.de`).
- Zugriff für Diagnosen über die **REST-API mit Long-Lived Token**
  (`hassTokens`) — Historie, Logbuch, Services, Supervisor-Logs.
- **Kein Samba, kein SSH vom Entwicklungsrechner.** Dateien landen
  ausschließlich über HACS auf dem HA-Host; ein „schnell mal die Datei
  patchen"-Weg existiert nicht (und soll nicht existieren).

### 1.2 Hausbatterie und Wechselrichter

| Punkt | Wert |
|---|---|
| Speicher | **Pylontech US5000** an einem **Victron**-System, ~**5 kWh nutzbar** |
| Arbeitsband | **5 … 95 %** (`battery_min_soc_percent` / `battery_max_soc_percent`) |
| **Inverter-Floor** | **20 %** (`inverter_min_soc_percent`) — hier schaltet der reale Inverter ab; identisch die Trip-Schwelle des G4-Floor-Guards |
| SOC-Quelle | `sensor.victron_system_battery_soc` |
| Batteriespannung | `sensor.victron_battery_voltage` (nur Diagnose/Gate; **bewusst nicht** im Listener-Set des Coordinators — analoges Rauschen würde permanent replanen) |

**Live-Befund zur Spannung (DC_TOPOLOGY §2, weiterhin gültig):** bei ~41 % SOC
und −38 A las der Spannungssensor **48,66 V**, während die Zellen ≈ 51,9 V
zeigten — Leitungs-/Shunt-Abfall unter Last. Das 48-V-Gate ist damit
**lastgekoppelt**, nicht rein SOC-abhängig. Deshalb ist `gate_soc_percent` nur
ein Proxy und das Kalibrier-Bracket (`gate_calibration`, Dokument 04 §3.5)
überlappt zulässigerweise.

### 1.3 PV

- **8 Module, 2 Hoymiles-Wechselrichter** (Balkonanlage; die vollständige
  Modul-/Azimut-Tabelle steht im Schwester-Repo `balcony-solar-forecast`).
- **Ist-Leistung:** `sensor.balcony_solar_forecast_measured_ac_power`.
  **Wichtig:** die nackten `solar_*`-Sensoren haben **keine
  Langzeitstatistik** — für jede Forensik über mehr als die
  Recorder-Purge-Zeit ist der `measured_ac_power`-Sensor die einzige
  brauchbare Quelle.
- **Prognose:** die drei Tages-Entities des Schwester-Repos
  (heute/morgen/übermorgen), State in kWh/Tag, Attribute `wh_period`,
  `wh_period_p10`, `wh_period_p90` (15-min-Buckets).

### 1.4 Netzmessung — und die wichtigste Falle der ganzen Anlage

Zähler ist ein **Victron EM540**. Verwendbare Langzeitzähler:

- `sensor.victron_grid_energy_forward_total_30` — Bezug, kWh (monoton)
- `sensor.victron_grid_energy_reverse_total_30` — Einspeisung, kWh (monoton)

> **⚠ Der Entfeuchter hängt AUSSERHALB des Zählerkreises.**
> Seine Versorgung erscheint am EM540 **1:1 als „Export"**. Das ist ein
> Forensik-Fund vom 24.07.2026 und erklärt scheinbar paradoxe Bilanzen
> („Export, obwohl eine 400-W-Last läuft").
>
> **Konsequenz für die Konfiguration:** für den Entfeuchter gehört
> `in_house_measurement` auf **aus (False)** — er steckt nicht in der
> gemessenen Hauslast, der Lerner darf ihn also **nicht** subtrahieren.
> Täte er es, würde die Grundlast doppelt bereinigt und systematisch zu
> niedrig gelernt (Begründung im Code: `const.CONF_LOAD_IN_HOUSE`,
> Umsetzung in `history_profile._subtractions` / `_fetch_days`).
>
> **Konsequenz für die Forensik:** Netz-Deltas aus den EM540-Zählern
> beschreiben nicht die Hausbilanz, solange der Entfeuchter läuft. Immer
> gegen `sensor.fritz_powerline_546e_power` gegenrechnen.
>
> **Stand 25.07.2026:** die Umstellung auf `in_house = False` ist ein
> **offener Operator-Schritt** (siehe Dokument 06).

### 1.5 Die drei Überschusslasten

#### A) Entfeuchter Keller — kontinuierliche Last

| Punkt | Wert |
|---|---|
| Steuerschalter | `switch.fritz_powerline_546e` (`control_switch_entity`) |
| Leistungs-Feedback | `sensor.fritz_powerline_546e_power` |
| Leistung | **400 W nominal**, **~409 W gelernt** (Median-Schätzer) |
| Tank | Volltank-Laufzeit laut Operator **~800 min** (`tank_full_runtime_min`) |
| Besonderheiten | `energy_limited = False`; Ziel des Tank-Modells (V6/F-TANK), des F-L7-Power-Warnings und der F5/F10/F11-Latch-Kette |

Der Entfeuchter **stoppt intern**, wenn der Wassertank voll ist: die
Leistungsaufnahme fällt auf ~2 W, obwohl die Empfehlung an ist. Genau daraus
entsteht die Kette F-L7-Latch → F5 (Planung mit `saturated_power_w` ≈ 2 W) →
F10/F11 (opportunistischer Hold über einen Schatten-Plan). Wer den Tank leert,
drückt den **Runtime-Reset-Button** — das ist zugleich „Tank geleert" und
Latch-Reset (Dokument 04 §6).

**Historische Warnung (v0.13.1-Watch):** der Median-Schätzer hatte einmal
**818 W** statt ~432 W gelernt (doppelter Wert — Messartefakt). Der
`_warn_estimate_overrun`-Wächter warnt heute, wenn der Schätzer das Dreifache
der Nennleistung übersteigt.

#### B) Fossibot F2400-B — energielimitierte Last, vom BM geschaltet

| Punkt | Wert |
|---|---|
| Steckdose (Steuerschalter) | `switch.shelly_01_switch_0`, ~**505 W** Ladeleistung |
| Lade-Gate (`charge_enable_entity`) | `input_boolean.charge_f2400_b` |
| SOC | `sensor.fossibot_e80690c65522_state_of_charge` |
| Ziel-SOC | **90 %** |

#### C) Fossibot F2400-B2 — **nur Empfehlung**

Der Operator schließt sie **manuell** an; der BM schaltet nichts, er gibt nur
die Empfehlung aus.

> **⚠ Fossibot-Telemetrie friert ein.** Die Fossibot-Integration liefert
> **Cached-Values mit frischen Zeitstempeln** — sie wird **nie**
> `unavailable`, und ein Alters-Check (`last_changed`) greift deshalb nicht.
> Beobachtet: SOC 87,5 % und Eingangsleistung **exakt 144 W** über **174,7 h**
> (20.07. 13:30 → 27.07. 20:10 CEST; die früher hier notierten 95 h waren nur
> ein Zwischenstand vom 24.07., Audit 07.08.2026) eingefroren, während der
> Planer vier Tage lang denselben ~50-Wh-Top-up neu buchte und damit ~0,4 kWh
> Entfeuchter-Slots verdrängte.
>
> Dagegen laufen **zwei** Wächter (Dokument 03):
> **G2 Stale-SOC** (`STALE_LOAD_SOC_MIN = 12 min` exakt unveränderter SOC
> während aktiven Ladens über der Standby-Bar) und **F4 Telemetry-Freeze**
> (`FREEZE_STALE_HOURS = 6 h` SOC **und** Leistung exakt unverändert, während
> die Empfehlung im Fenster mindestens einmal aktiv war). Beide halten die Last
> `available = False`. Beide sind **persistiert** (Latch + Evidenzuhr als
> akkumulierte Sekunden — Audit 02.08.2026: ein Coordinator-Reload löschte den
> F4-Latch, die Empfehlung duty-cyclete danach 110 min für ein abgestecktes
> Gerät, ~225 Wh Fehlbuchung; Offline-Zeit zählt nicht als Evidenz).

### 1.6 Grid-Support 24 V / 48 V

Zwei netzgespeiste Stützpfade, die den DC-Kreis versorgen, wenn die Batterie zu
tief ist (D-A9 / F-N1 / F-N2 / F-N3):

| Punkt | Entity / Wert |
|---|---|
| 48-V-PSU | `switch.ab_netzteil_48v` |
| 24-V-PSU | `switch.ab_netzteil_24v` |
| DC/DC-Wandler (48 V → 24-V-Schiene) | `switch.ab_dcdc_wandler` |
| Schwellen **live** | 24 V: **10 / 12 %**, 48 V: **7 / 10 %** (jeweils activate / recovery, **absolute** SOC-Prozente) |
| Schwellen-Defaults im Code | 24 V: 10 / 11 %, 48 V: 5,5 / 10 % (`const.DEFAULT_CONFIG`) — die Live-Werte weichen bewusst ab |
| `native48_base_w` | **35 W** (live; Code-Default 0 = neutral) |
| 48-V-PSU-Ausgangsspannung | 49,56 V; R2-Regler ON ≤ 49,56 V, OFF ≥ 49,8 V |

Die Ordnungsregel prüft der Config-Flow: je Stufe `activate < recovery`, und die
tiefere 48-V-Notstufe muss bei **beiden** Werten ≤ der 24-V-Stufe liegen
(`_validate_support_hysteresis`). Die Live-Werte 7/10 vs. 10/12 erfüllen das.

**Die Schwellen sind absolut** und werden vom dynamischen SOC-Buffer (D-C8)
**nicht** verschoben — bewusst so (Dokument 01 §3.2, Schritt 11).

**Manual-Modus (F-N2/R3):** wird eine PSU extern eingeschaltet, erkennt der
Coordinator das und geht für sie in `manual` — die Integration fasst sie nicht
mehr an, bis sie extern wieder aus ist. Die zwei Switch-Entities
`…support_dc24_manual` / `…support_dc48_manual` tun dasselbe von innen. Der
**R2-Spannungsregler** läuft nur im 48-V-Manual-Modus **und** nur bei
konfiguriertem Spannungssensor; er ist per Default `log_only = True` — er
protokolliert seine Entscheidungen, ohne zu schalten, bis der Operator ihn nach
einem Shakedown scharf stellt. Ein regler-verursachtes PSU-OFF beendet den
Manual-Modus **nicht** (nur der R3-Switch bzw. externes AUS tut das).

Details der Zwei-Bus-Physik: `docs/DC_TOPOLOGY.md` §3–§5 (gültig), Live-Zahlen
dort jedoch als Planungsstand 05.07.2026 lesen.

---

## 2. Legacy-Automatisierungen — aktiv und gewollt

Diese HA-Automatisierungen laufen **parallel** zum Battery Manager. Sie sind
**kein Altlast-Kandidat**, sondern arbeitsteilig gewollt:

| Automatisierung | Was sie tut |
|---|---|
| `automation.f2400_b_ladesteuerung` | setzt das **Firmware-Ladelimit** `ac_charging_upper_limit` der Powerstation: **20**, wenn das Gate (`input_boolean.charge_f2400_b`) aus ist, das Gerät aber trotzdem lädt; **90**, wenn das Gate an ist oder die AC-Versorgung wegfällt |
| `automation.f2400_b2_ladesteuerung` | dasselbe für die zweite Powerstation |
| `automation.f2400_b_ac_out_off` | Housekeeping (AC-Ausgang abschalten) |

**Keine Doppelsteuerung:** der BM schaltet Steckdose und Lade-Gate; die
Automatisierungen fassen **nur** das Firmware-Limit an. Die beiden Ebenen
kollidieren nicht.

> **⚠ Kopplung, die man leicht übersieht:** die **hartkodierte 90** in den
> Automatisierungen muss zum **Ziel-SOC des BM-Subentries** passen. Ändert man
> `target_soc_percent` im BM auf z. B. 80, lädt die Firmware weiter bis 90 und
> der BM-Stopp bei 80 wird von der Powerstation unterlaufen (bzw. umgekehrt: bei
> BM-Ziel 95 stoppt die Firmware schon bei 90 und der BM sieht das Ziel nie
> erreicht → Endlos-Top-ups). **Beide Werte immer gemeinsam ändern.**

---

## 3. Deploy-Rezept (verifiziert)

HACS trackt bei diesem Repo **Releases, nicht Commits**. Ein Push allein ändert
an der Anlage nichts.

1. **Versionen bumpen** in `manifest.json` **und** `pyproject.toml` (CI-Gate
   vergleicht beide), CHANGELOG-Eintrag schreiben.
2. `ruff format` + `ruff check` lokal, Tests unter WSL (Dokument 07).
3. `git push origin main`.
4. **CI grün abwarten** (`.github/workflows/validate.yml`). Der HACS-Job flaked
   gelegentlich mit HTTP 429 beim Action-Download → schlicht neu starten.
5. **Release erzeugen:**
   `gh release create vX.Y.Z --target main --title "vX.Y.Z" --notes "…"`
6. **Operator** aktualisiert in **HACS** (Repository → Update).
7. **⚠ Home Assistant neu starten.** Ein HACS-Download **ohne Neustart lädt
   weiterhin den alten Code** — und das Tückische: `installed_version` in HACS
   zeigt **trotzdem schon die neue Version**. Wer nur auf die HACS-Anzeige
   schaut, glaubt deployt zu haben und debuggt danach den alten Code. Kontrolle
   nach dem Neustart: Gerät „Battery Manager" → **Firmware/Software-Version**
   (die kommt zur Laufzeit aus dem Manifest) oder Diagnostics →
   `integration_version`.

**Rollback:** in HACS die vorige Release-Version wählen und neu starten. Es gibt
**keine** Store-Migration über Releases hinweg; Config-Keys deckt
`async_migrate_entry` ab (v1→v2, v2.2, v2.3 — Dokument 04 §11).

---

## 4. Diagnosewege

### 4.1 Zeitzonen — die Dauerfalle

| Quelle | Zeitbasis |
|---|---|
| **HA-Log** (`home-assistant.log`, `/api/hassio/core/logs`) | **Lokalzeit** (CEST/CET) |
| **History-/Statistics-API** (`/api/history/period/...`) | **UTC** |
| `last_update`-Attribut der BM-Sensoren | tz-aware **Lokalzeit** |
| `soc_forecast[].t`, `loads[].schedule[].start/end`, `hourly_details[].datetime` | **naiv, ohne Offset** — Planer-Lokalzeit (Dokument 04 §3.1) |

Im Sommer sind das **2 Stunden** Unterschied. Jede Forensik, die Log-Zeilen mit
History-Punkten korreliert, muss die Umrechnung explizit machen.

### 4.2 Logs ziehen

- Supervisor-Endpoint `/api/hassio/core/logs` mit **Range-Header** liefert
  praktisch **2–3 Tage** zurück — mit großzügigem Range auch deutlich mehr
  (Audit 07.08.2026: `Range: entries=:-300000:` deckte 7+ Tage / 84 MB ab).
  Mehr gibt es nicht — für längere Zeiträume braucht es die Recorder-Historie
  bzw. Langzeitstatistiken.
- **HA 2026.7:** `/api/error_log` existiert **nicht mehr** (404); das Logbuch
  liegt unter `/api/logbook/<iso-datetime>` (ohne `period/`,
  `?end_time=` optional). Die Core-Logs bleiben der Weg für Volltext.
- Log-Level für die Integration bei Bedarf hochziehen:
  `logger: logs: custom_components.battery_manager: debug`. Auf DEBUG
  protokolliert der Planer je Last die Allokationen
  (`Load X planned: pass 2 from slot 7 (2 slot(s), 820 Wh)`).

### 4.3 Was zuerst greifen

| Frage | Erster Griff |
|---|---|
| Warum schaltet nichts? | Diagnostics-Download → `learned_state.floor_guard.active`, `power_warning_latched`, `telemetry_freeze_stale`, `soc_stale_frozen_soc` |
| Warum plant er so? | `soc_forecast`-Attribute: `loads[].schedule[].why`, `pass`, `daily`, `prevented_export_kwh` |
| Sind die Prognosebänder da? | `quantile_coverage` je Tag (`scalar` = keine Bänder ⇒ α/β-Fallback) |
| Kommt die PV stündlich? | `pv_source` je Tag (`hourly` vs. `two_window`) |
| Springt T\*? | `threshold_horizon_end` (`null` = Vollhorizont) + `learned_state.threshold_inertia` |
| Läuft die Last real? | Runtime-Sensor (zählt echte Minuten aus dem Leistungssensor) + `tank_remaining_min` |

---

## 5. Meldungskatalog — was der Log sagt und was er meint

Zitate aus `coordinator.py` (englische Originaltexte, damit sie greppbar sind).

### 5.1 Floor-Guard (G4)

```
Floor guard TRIPPED (SOC 20.0 % at the 20 % inverter floor): surplus loads
are forced off and blocked until SOC recovers above 21.0 % with the
inverter recommendation on (G4)
```

```
Floor guard TRIPPED (inverter recommendation off and PV 120 W below the
410 W running surplus loads): ...
```

```
Floor guard released at SOC 21.4 % — surplus loads may run again
```

```
Floor guard active: dropping queued switch-ON for <Last>
```

**Bedeutung:** Zusatzlasten dürfen nie netzgespeist laufen (Operator-Regel
18.07.2026). Zwei Auslöser:

- **SOC-Zweig** — SOC ≤ `inverter_min_soc_percent`. **Latcht**: Freigabe erst
  bei `floor + hysteresis_percent`, damit ein am Floor pendelnder SOC die Lasten
  nicht flattern lässt. Er ist **unbedingt** (PV kann einen abgeschalteten
  Inverter nicht retten). Ein Neustart innerhalb des Freigabebands startet
  **gelatcht** (konservativ).
- **Empfehlungs-Zweig** — Inverter-Empfehlung aus **und** PV unterhalb der
  Leistung der laufenden Überschusslasten. Der PV-Vergleich ist die
  Planner-G4-Parität (F-STRICT-SURPLUS R2): ein T\*-bedingtes Inverter-Aus
  schickt die Lasten nur dann ans Netz, wenn PV sie nicht deckt. Ohne diesen
  Zusatz zwang der alte, unbedingte Zweig am 24.07. einen ~410-W-Entfeuchter
  **67 Minuten** aus, bei PV 1391–1680 W.

**Rate-Limit:** eine WARNING je Grund je 15 min, weitere Trips desselben Grundes
auf DEBUG — ein flatternder Guard kann das Log nicht fluten.

### 5.2 Power-Warning (F-L7) und Tank

```
Load <Name> draws 2 W while 400 W are configured (sustained > 15 min)
— full tank, wrong configured power or a foreign consumer?
```

```
Load <Name>: power warning cleared
```

Der Latch bleibt bestehen, bis die Last **BM-kommandiert und wieder in Band**
läuft — oder bis der **Runtime-Reset-Button** ihn löst (F10). Er wird
persistiert, damit ein Options-Save (= Reload) ihn nicht verschluckt.

Zusätzlich der Push `🪣 <Name>: tank nearly full` (einmal pro Tankzyklus, siehe
Dokument 04 §7.3).

### 5.3 Stale-/Freeze-Wächter

```
Load <Name>: SOC frozen at 87.5% for 12+ minutes while actively charging
— treating the reading as STALE and holding the load unavailable until
the sensor reports a different value
```

```
Load <Name>: SOC reports 88.0% again (was frozen at 87.5%) — stale latch cleared
```

```
Load <Name>: SOC 87.5% and power 144 W frozen for 6+ h while the
recommendation was active — treating the telemetry as STALE and holding
the load unavailable until either value changes
```

```
Load <Name>: telemetry moving again (SOC 87.5%, 505 W) — freeze latch cleared
```

```
Load <Name>: robust power estimate 818 W exceeds 3x the configured 400 W
— check the power sensor (frozen/cached value?) or the configured nominal power
```

Seit v0.17.0 zusätzlich der **Haus-SOC-Wächter** (LOAD_CONTROL.md §15 —
er bewacht die SOC-Quelle der *Hausbatterie*, nicht die der Lasten; seit
v0.18.0 energiebasiert/band-abhängig):

```
House battery SOC frozen at 63.0% — the plan expected 420 Wh of battery
flow without any SOC change (band drift allowance 2.0% of capacity) —
treating the reading as STALE (UpdateFailed path) until the sensor
reports a different value
```

```
House battery SOC reports 63.4% again (was frozen at 63.0%) — stale
watchdog latch cleared
```

**Bedeutung:** der SOC blieb exakt unverändert, während der letzte gültige
Plan ≥ 300 W Batteriefluss erwartete — so lange, bis der akkumulierte
erwartete Durchsatz die Drift-Toleranz des SOC-Bands überstieg (Mitte
13–89 %: `house_soc_stale_mid_percent`, Standard 3 % der Kapazität = 2 %
physikalische Drift + 1 % Anzeige-Quantisierung der 1-%-Schritt-Victron-
Quelle — Live-Audit 07.08.2026: 37 Fehl-Latches in 7 Tagen bei 2 %, einer
nur 65 s unter der 2-h-Shed-Schwelle; Ränder
< 13 % / > 89 %: `house_soc_stale_edge_percent`, Standard 7 %, weil
BMS-Plateaus dort stundenlang normal sein können; unter 300 W pausiert die
Akkumulation). Danach gilt der SOC als Datenverlust — es greift derselbe
Pfad wie bei fehlenden Daten (§5.7 `stale_data_load_shed`).

### 5.4 Schaltprotokoll (V9a: genau eine INFO-Zeile je bestätigter Aktion)

```
Load <Name> -> ON (<reason>)
Load <Name> -> OFF (<reason>; input off|stays on)
```

Die `reason`-Werte kommen aus genau einem Entscheidungspunkt in
`_apply_load_switching`:

| `reason` | Bedeutung |
|---|---|
| `plan slot` | der Plan hat den aktuellen Slot belegt |
| `plan off` | der Plan hat ihn nicht (mehr) belegt |
| `quantum end` | die eingefrorene Sub-Stunden-Deadline ist erreicht und der Plan hat **nicht** nahtlos verlängert |
| `target SOC reached` | energielimitierte Last am Ziel-SOC hinter einem Lade-Gate — **dwell-frei** |
| `G4 floor guard` | Zwangs-Aus / ON blockiert (höchste Präzedenz) |
| `latch hold` | **F10/F11**: gelatchte Last wird opportunistisch gehalten (siehe unten) |
| `… (flicker continuation)` | **F8**: min_off wurde erlassen, weil die Empfehlung binnen `REC_FLICKER_CONTINUATION_MIN = 5 min` nach einem *empfehlungsbedingten* Stopp zurückkam. Gedeckelt: `REC_FLICKER_MAX_CONTINUATIONS = 2` je `REC_FLICKER_PINGPONG_WINDOW_MIN = 30 min` |

Dazu die Seamless-Meldung (F9/F-SEAMLESS-RUNS):

```
Load <Name>: plan re-booked 1.25 h at the run deadline — extending
seamlessly to 2026-07-25 14:32:00
```

Kein OFF, kein Relais-Zyklus, kein Kompressor-Neustart, **kein** min_off-Stempel
— min_off wird dadurch nur noch von *absichtlichen* Abschaltungen scharf gemacht
(Operator-Semantik 18.07.2026).

### 5.5 Latch-Hold (F10/F11) — die subtilste Meldung

`latch hold` heißt: eine Last mit **gelatchtem** Power-Warning wird gehalten,
obwohl der Plan sie nicht anfordert (F5 plant sie mit ~2 W). Die Gates
(`_latch_hold_ok`):

1. Power-Warning ist gelatcht;
2. die Last ist **schaltbar** und hat Leistungs-Feedback;
3. **kein** Floor-Guard;
4. Inverter-Empfehlung **an**;
5. **Hauptgate (F11):** der **Schatten-Plan** aktiviert Slot 0 — also: „würde
   der normale Planer die Last jetzt anfordern, wäre sie nicht gelatcht?".
   Damit gelten implizit **alle** Planer-Gates (Floor, Strict-Surplus,
   Pass-2-Deckung durch sonst verlorenen Export);
6. **Überleistungs-Zusatzschutz:** liegt die gemessene Ist-Leistung über der
   Standby-Bar **und** ≥ `LATCH_HOLD_OVERPOWER_FACTOR = 1,5` × Planungsleistung
   (Verdacht Fremdverbraucher), muss zusätzlich die Slot-0-PV die **reale**
   Aufnahme decken. Das ist die **einzig** verbliebene Rolle von `pv_power_w` —
   nicht mehr das Hauptgate.

**Das dahinterliegende Prinzip (F11, Operator):** eine gelatchte/verdächtige
Last darf **nie strengeren Regeln** unterliegen als dieselbe Last im
Normalzustand. F10s alte Heuristik „Slot-0-PV ≥ Lastleistung" verbot Buchungen,
die der echte Planer ohnehin macht (etwa einen batteriegespeisten Pass-2-Lauf
frühmorgens bei PV ≈ 0 mit „covered by otherwise-lost export").

Der Hold endet nur bei **Gate-Verlust** (normaler, min_runtime-gedwellter Stopp;
G4 sofort) oder beim **Latch-Release** — dann übernimmt der normale Plan
nahtlos.

### 5.6 Pre-Drain

```
F-PREDRAIN-BLOCK: pre-drain booked for <Last> 2026-08-02 05:00 -> 11:00 (1700 Wh)
```

Eine INFO-Zeile **nur bei materieller Änderung** der Buchung (kein Spam je
Zyklus). Der frühere rohe Triple-Vergleich (Last, Start, Ende) ratschte mit
Uhr (Slot 0 deckt „jetzt") und Prognose (gebuchte Wh) — jede Coordinator-
Runde sah eine „neue" Buchung: Live-Audit 31.07.–07.08.2026 zählte **4.196
Zeilen in 7 Tagen** (04.08.: 1.753 ≈ 1/min). Geloggt wird jetzt nur bei
neuem Block (andere Last/anderes Datum), Startverschiebung ≥ **30 min** oder
Energieänderung ≥ **200 Wh** — zusätzlich ratenlimitiert auf 1 Zeile je
Block pro Stunde; unterdrückte Änderungen landen auf DEBUG. Verglichen wird
gegen die zuletzt **geloggte** Buchung, sodass ein langsam driftender Block
beim Überschreiten der kumulativen Schwelle doch noch auftaucht. Seit
v0.19.0 (F-PREDRAIN-BLOCK) ist der Pre-drain endloser Lasten EIN
zusammenhängender Block bis zum heutigen SOC-Peak (kein Cross-day, keine
Slot-Einzelwetten mehr); geschaltet wird er erst nach 3 identischen Plänen
+ 10 min Stabilität (R7). Energie-limitierte Lasten wettern unverändert
slotweise in Pass 2.

### 5.7 Repair-Issues (Benachrichtigungsboard, Stand v0.17.0)

Die Integration meldet anhaltende Störungen als **Repair-Issue** (Einstellungen
→ System → Reparaturen), jeweils mit Push-Benachrichtigung. Alle vier lösen
sich bei Gesundung **selbst** wieder auf:

| Issue | Auslöser | Was zu tun ist |
|---|---|---|
| `stale_data_load_shed` | Datenverlust (kein gültiger SOC > 6 h bzw. keine Prognose > 72 h, inkl. Haus-SOC-Stale-Wächter §5.3) hält ≥ `STALE_LOAD_SHED_HOURS = 2` h an → das **Fail-safe** (D-A8 Stufe 2, LOAD_CONTROL.md §16) hat einmalig alle gesteuerten Zusatzlasten zwangsabgeschaltet (Charge-Enable immer, Stecker je `input_off_policy`/Ownership) und latcht gegen Wieder-Ein | SOC-/Prognose-Quelle prüfen; bei Recovery löst sich Latch und Issue automatisch, Recovery-Push geht raus |
| `support_switch_failed` | `SUPPORT_SWITCH_FAIL_ALERT = 3` **aufeinanderfolgende** Schaltfehler im Support-Pfad (fehlgeschlagener Service-Call **oder** unbestätigte Make-before-break-Sequenz); Retry läuft weiter implizit über den 5-min-Zyklus | die konfigurierten PSU-/DC/DC-Switches prüfen (erreichbar? richtige Entities?); der nächste erfolgreiche Schaltvorgang löst das Issue |
| `learning_recorder_timeout` | ein Lernlauf wartete `RECORDER_TIMEOUT_S = 300` s erfolglos auf den Recorder | Recorder-DB prüfen (Last, Korruption); der nächste erfolgreiche Lauf löst das Issue |
| `learning_no_recorder` | Lernquellen konfiguriert, aber die `recorder`-Integration fehlt | Recorder aktivieren oder Lernquellen leeren; idempotent (kein Logspam) |

Die Lern-Robustheit umfasst außerdem die **abgesicherte Store-Migration**
(`_LearnedProfileStore`): ein Envelope-Mismatch verwirft den Lernstand und
lernt frisch, statt das Entry-Setup zu crashen (die Quelldaten sind jederzeit
re-fetchbar).

---

## 6. Kochbuch: die häufigsten Symptome

### 6.1 „Empfehlung an, Gerät aus"

**Kein Bug.** Der Empfehlungs-Binary zeigt den **Plan**, nicht den
Gerätezustand. Reihenfolge der Verdächtigen:

1. **min_off-Dwell** — Standard 30 min. Der häufigste Grund. Ggf. senken.
2. **G4-Floor-Guard** aktiv (Attribut `floor_guard_active` am
   `inverter_status`-Binary).
3. **Latch** (F-L7 / G2 / F4) hält die Last `available = False` bzw. plant sie
   mit ~2 W.
4. **BM-Steuerungs-Switch** der Last steht auf aus.
5. **Verfügbarkeits-Entity** aus.

Diagnose in einem Schritt: Diagnostics-Download, `learned_state` lesen.

### 6.2 „Der Entfeuchter läuft tagelang nicht"

Klassische **F-L7-Deadlock**-Signatur (voller Tank): Latch gesetzt → F5 plant
2 W → Plan fordert nie an → Latch kann nicht releasen. Heilung:

- **Tank leeren** und den **Runtime-Reset-Button** drücken (löst den Latch,
  startet den Tankzyklus neu). Live bestätigt am **25.07.2026 05:33** — um
  **05:35** lief das Gerät wieder mit **418 W**.
- Seit v0.16.1/v0.16.2 heilt das System sich zusätzlich selbst über den
  **opportunistischen Latch-Hold** (§5.5).

### 6.3 „Batterie pre-draint nicht tief genug"

Prüfen: `quantile_coverage` — steht dort `scalar`, liefert der Forecaster keine
p10/p90-Bänder (oder kollabierte `p10 == p90`), und der Planer fällt auf den
α-Skalar zurück. **Das ist kein BM-Bug**; α ist über die Optionen erhöhbar
(live 0,7), die Wurzel liegt im Schwester-Repo.

### 6.4 „Netzbilanz passt nicht"

Erst §1.4 lesen: der Entfeuchter liegt außerhalb des Zählerkreises.

### 6.5 „Powerstation erreicht das Ziel nicht / lädt endlos"

Firmware-Limit der Legacy-Automatisierung (**90**) gegen den BM-Ziel-SOC prüfen
(§2). Danach die Freeze-Wächter (§5.3).

### 6.6 „Nach dem Update verhält sich nichts anders"

HA wurde nicht neu gestartet (§3, Schritt 7). `installed_version` in HACS lügt
in diesem Fall.

---

## 7. Offene Operator-Schritte (Stand 25.07.2026)

- **Entfeuchter:** `in_house_measurement` auf **False** setzen (§1.4) und
  `tank_full_runtime_min` auf **800** eintragen.
- **v0.16.2 deployen** (v0.16.1 ist live seit 24.07. 19:13).
- Kosmetik-Frage offen: der Empfehlungs-Binary zeigt den Plan, nicht den
  Gerätezustand — der Operator hat sich daran wiederholt gestoßen. Ein
  zusätzliches Attribut oder eine zweite Entity „läuft gerade" wäre denkbar,
  ist aber nicht beschlossen (Dokument 06).

---

**Verifikation: geprüft gegen Commit `a172d48` (v0.16.2); Anlagenfakten
live verifiziert am 24./25.07.2026; die v0.17.0-Einträge gegen den
Arbeitsstand vom 2026-07-31.**

Wichtigste Code-Anker dieses Dokuments:

1. `custom_components/battery_manager/coordinator.py::_update_floor_guard` —
   die beiden G4-Zweige, das Latch-/Freigabeband und die Meldungstexte.
2. `custom_components/battery_manager/coordinator.py::_latch_hold_ok` — die
   sechs F10/F11-Gates inkl. Schatten-Plan und Überleistungsschutz.
3. `custom_components/battery_manager/coordinator.py::_update_soc_stale` /
   `_update_telemetry_freeze` — G2 und F4 gegen eingefrorene Fossibot-Telemetrie.
4. `custom_components/battery_manager/const.py::CONF_LOAD_IN_HOUSE` — die
   ausformulierte Begründung der Messkreis-Semantik (EM540-Eigenheit).
5. `docs/LOAD_CONTROL.md` §11–§14 — Floor-Guard, Robust-Power, Seamless-Runs,
   `in_house_measurement`; die einzige noch aktuelle der großen Alt-Dokus.
