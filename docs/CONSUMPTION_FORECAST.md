# Spezifikation: Gelernte Verbrauchsprognose aus Messdaten (v0.5)

> Status: **Stufe 1 (v0.5.0) + Stufe 2 (v0.6.0) implementiert, 2026-07-04; Stufe 3 offen**
> Abweichungen der Umsetzung: Store v2 verwirft v1-Daten (frischer Backfill
> statt Migration — Quelldaten sind jederzeit neu abrufbar);
> `negative_residuals` zählt pro Lauf, nicht kumulativ; Bias-Schwelle des
> Wächters 15 % der mittleren Last über 14 Tage (hartkodiert).
> Rev. 2: verallgemeinert (nicht auf ein konkretes Setup zugeschnitten) und
> 19 Befunde eines adversarialen Reviews eingearbeitet.
> Setzt **P4** um (REQUIREMENTS.md §4.4, „Lastprofile aus HA-Historie lernen"),
> **N2** teilweise (dynamischer SOC-Puffer aus Prognosegüte) und bereitet **P3**
> (stündliche PV-Prognosen) strukturell vor. Entscheidungspunkte D-C1 … D-C10.
> Das konkrete Setup des Betreibers dient nur noch als **Referenzbeispiel in
> Anhang A**.

## 1. Ausgangslage & Ziel

Heute ist die Verbrauchsprognose rein statisch: je Pfad (AC/DC) ein
`LoadProfile` aus Grundlast + Zusatzlast in einem Zeitfenster (2×4 Skalare;
nach dem Setup per UI nicht mehr änderbar — kein Reconfigure-Flow für den
Basis-Entry, nicht im Options-Flow), plus erkannte Geräteläufe mit
konfigurierter Energie, linear über die Restdauer verteilt. Kein Messwert
fließt ein; die Schwächen sind in REQUIREMENTS.md §2.2 benannt (keine
HA-Historie, keine Wochentag-/Saisonprofile).

Home Assistant hält für jeden numerischen Sensor mit `state_class`
**Langzeitstatistiken** (Stundenwerte, werden nie gelöscht) — bei den meisten
Installationen liegen also monate- bis jahrelange Verbrauchsdaten brach.
Ziel dieser Spezifikation:

1. **Stufe 1:** Stündliche Grundlast-Profile (Werktag/Wochenende/Abwesenheit)
   nächtlich aus der Langzeitstatistik lernen — bereinigt um alles, was die
   Integration selbst schaltet.
2. **Stufe 2:** Quantile (P50/P80) statt Punktschätzer; die Unsicherheit
   ersetzt als **dynamischer SOC-Puffer** den fixen Konfig-Puffer (sofort
   scharf, Betreiber-Entscheid 2026-07-04); täglicher Soll-Ist-Wächter.
3. **Stufe 3:** Gerätesignaturen (Energie/Dauer/Startzeiten) aus der Historie
   lernen und **erwartete** Geräteläufe in den Horizont einrechnen.

Unverändert bleiben die Kernprinzipien: genau **eine** Simulation pro Update
(P1/P2), **kein Pessimismus-Aufschlag** auf die Lastreihe (D-A3 — Unsicherheit
wirkt nur über den Puffer), Fail-safe-Verhalten bei Datenausfall (D-A8) und
der HA-freie, pure `core/`-Kern (STRATEGY.md Q1). Ohne konfigurierte
Mess-Entitäten verhält sich die Integration **exakt wie heute**. Alle
Datenquellen sind **frei konfigurierbare Entitäten** — nichts ist an einen
Hersteller oder eine Topologie gebunden.

## 2. Verbrauchs-Messpunkte (generisch)

Die Integration braucht pro Pfad (AC, DC) eine stündliche Messreihe der
Verbraucherlast. Zwei gleichwertige Quellen, je Pfad wählbar:

### 2.1 Direkter Lastsensor

`ac_load_entity` / `dc_load_entity`: ein Sensor, der die Verbraucherlast des
Pfads direkt misst — als Leistung (W, `state_class: measurement` →
Stunden-`mean`) oder als Energiezähler (kWh, `total_increasing` →
Stunden-`change`). Typische Beispiele: Hausverbrauchssensor eines
Energiezählers (Shelly 3EM o. Ä.), „House Consumption" eines
Hybrid-Wechselrichters, DC-Shunt.

**Messpunkt-Regel (wichtigste Einzelvoraussetzung):** Der Sensor soll die
Verbraucherlast messen, die der Planer mit `ac_profile`/`dc_profile`
modelliert — **nicht** den Netzimport und **nicht** einen Punkt, der das
eigene Batterieladen enthält (sonst lernt das Profil das Verhalten der
Integration mit, siehe D-C2). Der Config-Flow-Hilfetext erklärt das.

### D-C1: Zählerbilanz (wenn kein direkter Sensor existiert)

Viele Anlagen haben keinen Hauslast-Sensor, aber Energiezähler an den
Knotengrenzen. Dafür gibt es je Pfad zwei **Mehrfach-Entitätslisten**
(EntitySelector `multiple`):

- `ac_balance_in_entities` — Zähler für Energie, die **in** den
  Verbrauchsknoten hineinfließt (z. B. Netzimport, Inverter-Abgabe,
  PV-Einspeisung in den Hausbus).
- `ac_balance_out_entities` — Zähler für Energie, die den Knoten wieder
  **verlässt, ohne verbraucht zu werden** (z. B. Netzexport,
  Batterieladen aus dem Hausbus).

```
Last(h) = Σ change(in_i, h) − Σ change(out_j, h),   geklemmt auf ≥ 0
```

Jede Listen-Entität darf W-Sensor (`mean` × 1 h) oder kWh-Zähler (`change`)
sein; die Art wird zur Laufzeit aus den Statistik-Metadaten bestimmt.
**Vollständigkeitsregel:** Eine Stunde geht nur in die Bilanz ein, wenn
**alle** konfigurierten Bilanz-Entitäten für diese Stunde einen Wert liefern
— sonst wird die Stunde verworfen (zählt in die Coverage-Diagnose), denn eine
Teilbilanz sieht plausibel aus, ist aber falsch. Hat der Pfad sowohl
`*_load_entity` als auch Bilanzlisten konfiguriert, hat der direkte Sensor
Vorrang.

### 2.3 Lasten außerhalb des Messpunkts

Nicht jede von der Integration gesteuerte Last hängt hinter dem
Verbrauchs-Messpunkt (Beispiel: eine Last in einem anderen Stromkreis, die
über einen Einspeise-Sollwert versorgt wird — Anhang A). Deshalb erhält
jede Überschusslast das Subentry-Flag **`in_house_measurement`**
(Default **true**):

- **true** → die Last steckt in der Messreihe und wird beim Lernen
  subtrahiert (D-C2);
- **false** → die Last ist im Messwert nicht enthalten und darf **nicht**
  subtrahiert werden (Doppelabzug).

### 2.4 DC-Pfad

Identischer Mechanismus (`dc_load_entity` bzw. `dc_balance_*_entities`).
Ohne konfigurierte DC-Quelle bleibt das DC-Profil statisch — das Lernen ist
je Pfad unabhängig aktiv. Stunden, in denen ein konfigurierter **Stützpfad**
aktiv war (D-C2 Schritt 3), werden ausgeschlossen, weil Netz-Einspeisungen
auf der Verbraucherseite das Messbild verfälschen.

## 3. Architektur-Überblick

```
                     HA-Schicht                                core/ (pur)
┌─────────────────────────────────────────────┐   ┌──────────────────────────┐
│ history_profile.py (NEU)                    │   │ load_profile.py (NEU)    │
│  Nachtjob 03:00: LTS-Queries (Recorder-     │──▶│  Bereinigungs-Arithmetik,│
│  Executor) → Bereinigung → Aggregation      │   │  Median/Quantile, Bins   │
│  → Store (je Config-Entry)                  │   │  (reine Funktionen)      │
├─────────────────────────────────────────────┤   ├──────────────────────────┤
│ coordinator.py                              │   │ series.build_slots       │
│  liest Profil aus Store, prüft Frische,     │──▶│  NEU: optionale Reihen   │
│  baut Reihen ac_load_w/dc_load_w über den   │   │  ac_load_w/dc_load_w     │
│  Horizont (slot_starts-Helfer), berechnet   │   │  (Slot-weiser Fallback   │
│  dyn. Puffer (Stufe 2)                      │   │  auf statisches Profil)  │
└─────────────────────────────────────────────┘   └──────────────────────────┘
```

- **Lernen lebt in der HA-Schicht** (Recorder-Zugriff), die Rechenkerne sind
  reine Funktionen (ohne HA-Import, voll unit-testbar).
- Der Kern konsumiert **fertige Reihen** — dasselbe Muster, das für P3
  (PV-Stundenprognosen) vorgesehen ist, und rasterneutral für den späteren
  15-min-Ausbau (D-A7).
- Der Nachtjob läuft asynchron neben dem 5-min-Planungstakt; der Coordinator
  liest **nur den Store** (keine DB-Query im Planungspfad). Lernfehler können
  den Planer nie blockieren.

## 4. Stufe 1 — Gelernte Grundlast-Stundenprofile

### 4.1 Neue Konfiguration

Alle Felder **optional**. Die Mess-Entitäten stehen im Basis-Flow (Schritt
„Grundlasten") **und** im Options-Flow; die Tuning-Zahlen nur im
Options-Flow. Die acht statischen Profilwerte bleiben bestehen, werden im
UI als „Fallback-Profil" beschriftet und wandern **ebenfalls in den
Options-Flow** (behebt: heute nach dem Setup per UI nicht änderbar).

| Feld | Typ / Default | Bedeutung |
|---|---|---|
| `ac_load_entity` | EntitySelector sensor, leer | Direkter AC-Lastsensor (§2.1); Vorrang vor der Bilanz |
| `ac_balance_in_entities` | EntitySelector sensor `multiple`, leer | Zuflüsse zum AC-Verbrauchsknoten (D-C1) |
| `ac_balance_out_entities` | EntitySelector sensor `multiple`, leer | Nicht-Verbrauchs-Abflüsse (D-C1) |
| `dc_load_entity` / `dc_balance_in_entities` / `dc_balance_out_entities` | analog | DC-Pfad (§2.4) |
| `learning_window_days` | Number 14–120, **42** (Options-Flow) | Rollierendes Lernfenster |
| `learning_max_age_days` | Number 3–60, **14** (Options-Flow) | Max. Profilalter, danach Fallback statisch |
| je Überschusslast-Subentry: `in_house_measurement` | bool, **true** | Last steckt im Messwert → beim Lernen subtrahieren (§2.3) |

**Aktivierungslogik:** Lernen je Pfad ist aktiv, sobald `*_load_entity` ODER
mindestens eine `*_balance_in_entities`-Entität gesetzt ist. Kein separater
Toggle (implizites Opt-in, keine inkonsistenten Zustände). Bilanz ohne
jeden Zufluss-Zähler → Validierungsfehler im Flow.

**Bewusst hartkodiert** (dokumentierte Konstanten, konsistent zu
`_POWER_EMA_ALPHA`): Lernzeitpunkt 03:00 lokal, `min_samples` = 10 je Bin
(Abwesenheit: 5), Plausibilitätsklemmen (AC 3 000 W, DC 1 000 W je
Stundenmittel), Änderungs-Rate-Limit ±20 %/Nacht, Median als Aggregat
(Stufe 1), Bin-Schema.

### 4.2 Datenerfassung (Nachtjob)

- Trigger: `async_track_time_change` (feuert in Lokalzeit) 03:00; zusätzlich
  einmalig beim Setup, wenn der Store leer oder das Profil > 24 h alt ist.
- Query je Entität:

  ```python
  await get_instance(hass).async_add_executor_job(
      lambda: statistics_during_period(
          hass, start, end, ids, "hour",
          units={"energy": "kWh", "power": "W"},   # Pflichtparameter; pinnt zugleich die Einheiten
          types={"mean", "change"},
      )
  )
  ```

  **Alle** Recorder-Zugriffe (auch `list_statistic_ids`,
  `state_changes_during_period`) laufen über den Recorder-Executor
  (`get_instance(hass).async_add_executor_job`) — nie
  `hass.async_add_executor_job`, nie im Event-Loop.
- Verfügbarkeit vorab per `list_statistic_ids` prüfen (liefert auch
  `statistics_unit_of_measurement`; Recorder-Excludes oder fehlende
  `state_class` → Repair-Issue, Pfad bleibt statisch). Einheiten werden über
  den `units`-Parameter erzwungen, nicht angenommen.
- Fehlende Stunden (unavailable-Lücken) werden übersprungen, nie mit 0
  gelernt; für die Zählerbilanz gilt zusätzlich die Vollständigkeitsregel
  aus D-C1 (alle Bilanz-Entitäten oder Stunde verwerfen).
- Erstlauf: Backfill über `learning_window_days` (42 Tage × 24 h ≈ 1 000
  Zeilen/Entität — unkritisch). Danach inkrementell nur der Vortag. Wird das
  Fenster **vergrößert** (Options-Änderung oder Stufe-2-Migration auf 120 d),
  stößt der nächste Nachtlauf einen **Delta-Backfill** der fehlenden Tage an;
  die `daily_hours`-Retention folgt dem konfigurierten Maximum.
- **Cache-Invalidierung:** Die Tages-Zwischenergebnisse wurden mit der
  Bereinigungs-Konfiguration ihres Abrufzeitpunkts gerechnet. Ändert sich
  ein Bereinigungs-Input (`in_house_measurement`, power-/Schalter-Entitäten,
  Nennleistungen, Geräte, Stützpfad-Schalter), wird ein
  **Bereinigungs-Fingerprint** im Store ungültig → kompletter Refetch des
  Fensters beim nächsten (sofort angestoßenen) Lauf, statt kontaminierte
  Tage wochenlang mitzuschleppen.

### D-C2: Bereinigung (Pflichtbestandteil, nicht optional)

Gelernt wird ausschließlich die **unbeeinflusste Grundlast**. Ohne
Bereinigung lernt das Profil die eigenen Schaltentscheidungen (Rückkopplung:
geplante Mittags-Lasten → gelernte Mittags-„Grundlast" steigt → weniger
erkannter Überschuss → Oszillation über Wochen) und zählt Geräteläufe
doppelt (ApplianceRuns bleiben additiv, `series._apply_appliance_runs`).

Pro historischer Stunde, Reihenfolge:

1. **Überschusslasten mit `in_house_measurement = true`:**
   - mit `power_entity`: Stunden-`mean` der LTS subtrahieren (genaueste
     Quelle). **Statistik-Lücken zählen als 0 W** (Betreiber-Entscheid
     2026-07-04): Geräte wie Powerstations melden `unavailable`, genau
     wenn sie aus sind — eine fehlende Stunde heißt „kein Verbrauch",
     nicht „unbekannt"; Verwerfen würde die Wochenend-Bins wochenlang
     aushungern. Gleiches gilt für Appliance-Leistungssensoren.
   - ohne `power_entity`: `nominal_power_w × Einschaltanteil(h)` aus der
     `control_switch_entity`-Historie subtrahieren
     (`state_changes_during_period(…, no_attributes=True)`, ein Aufruf je
     Entität, Wochen-Chunks, Recorder-Executor).
   - Lasten mit `in_house_measurement = false`: **kein Abzug** (§2.3).
2. **Appliances:**
   - `detection_entity` ist Leistungssensor mit LTS → Stunden-`mean`
     subtrahieren;
   - Status-only (keine LTS für nicht-numerische Entitäten): Stunden mit
     erkanntem Lauf werden aus der Bin-Stichprobe **ausgeschlossen**
     (Ausschluss statt Subtraktion — Läufe sind sporadisch, der Median
     verkraftet den Datenverlust; bei täglichen Überschusslasten würde
     Ausschluss dagegen die Mittags-Bins leeren, daher dort Subtraktion).
     Lauferkennung in der Historie: gleiche Regeln wie live
     (`APPLIANCE_RUNNING_STATES` bzw. `power_threshold_w`).
3. **Stützpfade — Korrektur statt Ausschluss** (Rev. 3, Betreiber-Hinweis:
   im Winter laufen die Netzteile ggf. **monatelang** — ein Ausschluss
   würde das Lernen komplett aushungern). Aktive Stützpfade
   **verschieben** Leistung zwischen den Pfaden; die Bereinigung schiebt
   sie rechnerisch zurück, sodass die Profile den unbeeinflussten Bedarf
   abbilden (genau die Semantik, die der Simulationskern erwartet — er
   modelliert die Stützpfade selbst):
   - **48-V-Netzteil AN:** zieht `support_dc48_power_w` (Konfig, z. B.
     60 W) aus dem Hausnetz und speist sie verbraucherseitig in den
     48-V-Bus → **AC − P·Einschaltanteil, DC + P·Einschaltanteil**
     (Wandlerverluste vernachlässigt, wie im Kern).
   - **24-V-Netzteil speist die Schiene** (Netzteil AN **und** DC/DC AUS):
     die komplette 24-V-Last wandert von der DC- in die AC-Messung.
     Exakte Rückverschiebung über den optionalen **Leistungssensor des
     Netzteils** (`support_dc24_power_entity`): **AC − P24(h),
     DC + P24(h)** (Statistik-Lücken = 0 W, das Netzteil ist dann aus).
     **Ohne diesen Sensor ist die Verschiebung unbekannt → nur dann wird
     die Stunde (beide Pfade) ausgeschlossen.** Für Winterbetrieb ist der
     Sensor daher dringend empfohlen (Repair-Issue, wenn er konfiguriert
     ist, aber keine Statistik hat).
   - **Tote Schiene** (DC/DC AUS ohne Netzteil): anomaler Zustand →
     Stunde ausgeschlossen (selten).
   Der Status-only-Appliance-Ausschluss aus Schritt 2 gilt nur für den
   **AC**-Pfad. Sind keine Stützpfade konfiguriert, entfällt dieser
   Schritt.
   **Coverage-Regel:** Stunden vor dem ersten aufgezeichneten Zustand
   einer benötigten Schalter-/Status-Entität sind UNBEKANNT, nicht „aus":
   Subtraktions-/Korrektur-Quellen liefern dort keinen Wert (Stunde wird
   verworfen), Ausschluss-Prüfungen schließen die Stunde konservativ aus.
   Tage außerhalb der Recorder-Retention bleiben damit ungelernt, statt
   unbereinigt gelernt zu werden.
4. **Klemmen & Diagnose:** Residuum auf [0, Klemme] begrenzen; **negative
   Residuen zählen** (Indikator „Messpunkt enthält gesteuerte Lasten nicht /
   Doppelabzug / falsches `in_house_measurement`-Flag" → Diagnose-Attribut,
   bei Häufung Repair-Issue).

### D-C3: Aggregation & Bin-Schema

- Bins je Pfad (AC, DC): **{Werktag, Wochenende, Abwesenheit} × 24 lokale
  Stunden**. Feiertage → Wochenend-Bin, sobald `workday_entity` konfiguriert
  ist (Stufe 2, §5.3).
- Aggregat Stufe 1: **Median** der bereinigten Stundenwerte über das
  rollierende Fenster (robust gegen Ausreißertage; Saison folgt implizit mit
  ~3–6 Wochen Verzögerung — bewusst **kein** explizites Saisonmodell, §8).
- Gültigkeit pro Bin: ≥ `min_samples`, sonst Bin-genauer Fallback (D-C6).
- Dämpfung: Änderung je Bin und Nacht auf ±20 % begrenzt (Sicherheitsnetz
  gegen Rest-Rückkopplung und Datenfehler), mit absoluter
  Mindestschrittweite 10 W — sonst wäre ein Bin bei 0 W ein Fixpunkt der
  multiplikativen Klemme und könnte nie wieder wachsen.
- **DST/UTC (Lernseite):** LTS-Zeilen sind UTC-ausgerichtet; Bin-Zuordnung
  über die **lokale** Stunde des Zeilenstarts. 23/25-h-Tage werden als
  normale Samples behandelt; dedizierte Tests (§4.9).

### D-C4: Urlaubsmodus (manuell, Betreiber-Wunsch 2026-07-04)

Kein Auto-Erkennen von Abwesenheit (verworfen, §8) — stattdessen ein
**manueller Schalter**:

- Neue Entität der Integration: `switch.<gerät>_urlaubsmodus`
  (Zustand persistiert über Neustarts, §4.6; Icon `mdi:beach`).
- **Wirkung auf die Prognose:** solange AN, verwendet der Reihen-Builder für
  den gesamten Horizont den **Abwesenheits-Binsatz**. Für Stunden, deren
  Abwesenheits-Bin noch < `min_samples` hat, schreibt der Coordinator
  **direkt `base_w` als Reihenwert** (niemand zu Hause ≈ reine Grundlast) —
  ausdrücklich **kein** `None`, denn der None-Fallback würde im Kern
  `power_w(hour)` inklusive `variable_w` liefern. Der Slot-weise
  None-Fallback (D-C5) gilt nur außerhalb des Urlaubsmodus.
- **Wirkung auf das Lernen:** Tage, an denen der Modus ≥ 12 h aktiv war,
  wandern in die Abwesenheits-Bins statt Werktag/Wochenende. Tagging: der
  Nachtjob vermerkt den Vortag im Store (`day_log`); für den Backfill
  zusätzlich die Schalter-Historie aus dem Recorder (Integration-eigene
  Entität, wird aufgezeichnet). Alt-Tage vor Einführung = normal.
- **Erwartete Geräteläufe (Stufe 3) sind im Urlaubsmodus 0.**
- Umbauten während der Abwesenheit (Beispiel Anhang A: Entfeuchter zieht in
  die Wohnung um) bleiben **manuelle Umkonfiguration** — ausdrücklich keine
  Automatik.

### 4.6 Persistenz (Store)

Store **je Config-Entry** (analog zum bestehenden SOC-Cache):
`Store(hass, 1, f"battery_manager.learned_profiles.{entry.entry_id}")`;
Löschung wird in `async_remove_entry` ergänzt (kein verwaister Zustand,
kein Key-Sharing zwischen zwei Entries).

```jsonc
{
  "version": 1,                       // Stufe 2 → 2 (Migration: profiles-Werte werden zu {p50, p80})
  "computed_at": "2026-07-04T03:00:00+02:00",
  "source_entities": {"ac": ["…"], "dc": ["…"]},   // Bindung: Wechsel ⇒ Profil verwerfen
  "vacation_mode_active": false,      // aktueller Schalterzustand (Restore nach Neustart)
  "day_log": {"2026-07-03": {"daytype": "weekday", "vacation": false}},
  "daily_hours": {"2026-07-03": {"ac": [ /* 24 × Wh|null */ ], "dc": [ … ]}},
  "profiles": {                       // Stufe 1: W-Wert je Bin; Stufe 2: {"p50": […], "p80": […]}
    "ac": {"weekday": [/*24*/], "weekend": [], "absence": []},
    "dc": { … }
  },
  "samples": { /* Zählstand je Bin */ },
  "appliance_signatures": {           // Stufe 3 (§6)
    "<appliance_id>": {
      "runs_observed": 12,
      "median_energy_wh": 950, "median_duration_h": 3.4,
      "rate_per_daytype": {"weekday": 0.4, "weekend": 0.7},   // Fallback-Modell
      "gap_histogram": [/* Lücken 0–14 Tage */],               // Intervall-Modell D-C10
      "last_run_date": "2026-07-02",
      "start_histogram": [/* 24 geglättete Gewichte */],
      "active_run_started_at": null   // persistierte Startzeit (Bugfix §6.2)
    }
  },
  "diagnostics": {"negative_residuals": 0, "coverage": 0.94}
}
```

`daily_hours` hält nur das (maximale konfigurierte) Lernfenster →
inkrementelles Lernen ohne Voll-Rescan; Re-Aggregation nach
Parameteränderung möglich, bei Fenstervergrößerung mit Delta-Backfill
(§4.2).

### D-C5: Kern-Andockung (Reihen-Kontrakt)

- `series.build_slots` erhält zwei neue **optionale** Parameter
  `ac_load_w: tuple[float | None, ...] | None` und `dc_load_w: …`.
- **Kontrakt:** Die Reihe wird per `slot.index` adressiert. Einheit ist
  **Watt** (Stundenmittel); `build_slots` multipliziert mit `slot.duration`
  (korrekt auch für die partielle erste Stunde). Werte jenseits der
  Reihenlänge gelten als `None`. `None` oder fehlende Reihe →
  `config.*_profile.power_w(hour)` wie bisher (**Slot-weiser Fallback**;
  Ausnahme Urlaubsmodus, D-C4).
- **Keine Duplikation der Slot-Logik:** Die Slot-Start-Enumeration (partielle
  erste Stunde, Stundenraster, Horizontende) wird als pure Helferfunktion
  `series.slot_starts(now, num_days) -> tuple[datetime, ...]` extrahiert;
  `build_slots` und der Reihen-Builder des Coordinators nutzen **dieselbe**
  Funktion (kein Divergenz-Risiko bei Slotanzahl/Indexierung). Test „Reihe
  kürzer als Horizont" ist Pflicht (§4.9).
- **Tagestyp/Bin-Lookup im Coordinator:** über die **tz-bewusste lokale**
  Slot-Startzeit (`dt_util.as_local`), nicht über das naive
  `slot.hour_of_day` — bei DST-Wechseln im Horizont nutzt die doppelte
  Stunde denselben Bin zweimal, die entfallene entfällt. Restunschärfe:
  `build_slots` selbst rechnet auf naiver Ortszeit weiter
  (`timedelta(hours=1)`); in den ≤ 2 Umstellungsnächten/Jahr kann
  `hour_of_day` nach dem Wechsel um ±1 h abweichen — akzeptiert und
  dokumentiert, da der Bin-Lookup davon unabhängig ist.
- `_apply_appliance_runs` bleibt unverändert additiv — keine Doppelzählung,
  weil die gelernte Grundlast appliance-bereinigt ist (D-C2).
- `simulate`/`optimize` bleiben unberührt (konsumieren nur `ac_wh`/`dc_wh`);
  P1/P2 gewahrt: die Reihe ist Eingangsgröße der EINEN Simulation.

### D-C6: Fallback-Kaskade & Diagnose (D-A8-analog)

| Situation | Verhalten |
|---|---|
| Bin ungültig (< `min_samples`), Normalbetrieb | statischer Profilwert **nur für diese Stunde** (`None` in der Reihe) |
| Bin ungültig, **Urlaubsmodus** | `base_w` als Reihenwert (ohne `variable_w`, D-C4) |
| Profil älter als `learning_max_age_days` / Store leer / Quell-Entität gewechselt | komplett statisches Profil, Warnung |
| Nachtlauf wirft Exception | Log-Warnung; altes Profil bleibt bis Altersgrenze gültig |
| Mess-Entität ohne LTS / Recorder-Exclude | Repair-Issue mit Klartext, Pfad statisch |

Diagnose als Attribute am bestehenden Status-Sensor: Profilquelle je Pfad
(„gelernt (2×24+A, 42 d, 94 % Abdeckung)" / „statisch"), Alter des letzten
Laufs, Sample-Abdeckung, mittlere Abweichung gelernt vs. statisch,
Negativ-Residuen-Zähler. (`_unrecorded_attributes` für die Profilmatrix.)

### 4.9 Tests & Akzeptanz

- Pure Funktionen (`core/load_profile.py`): Bereinigungs-Arithmetik,
  Median/Bins, Rate-Limit, Klemmen, Bilanz-Vollständigkeitsregel —
  synthetische Reihen inkl. DST-Tagen (23/25 h), Lücken, Negativ-Residuen.
- Kern: `build_slots` mit/ohne Reihen, Slot-weiser Fallback, **Reihe kürzer
  als Horizont**, partielle erste Stunde, `slot_starts`-Äquivalenz.
- HA-Schicht: Store-Versionierung/Entitäts-Bindung, Fallback-Kaskade,
  Nachtjob-Fehlerpfade (Mock-Recorder), **Reihen-Bau über eine DST-Grenze**,
  Urlaubsmodus-Wechsel untertags.
- **Akzeptanz:** Profile sind ab Deploy planungswirksam; 2 Wochen
  **begleitende Beobachtung** über den Diagnose-Vergleich gelernt vs.
  statisch; Negativ-Residuen ≈ 0; Ladehäufigkeit der Überschusslasten nicht
  systematisch gefallen (D-A3-Kontrolle).

## 5. Stufe 2 — Quantile, dynamischer SOC-Puffer, Wächter

### D-C7: Gewichtete Quantile mit Rezenz-Gewichtung

- Gleiche Bins und Bereinigung wie Stufe 1; der Median wird durch
  **gewichtete empirische Quantile** ersetzt: Gewicht
  `w = 0,5^(alter_tage / 30)` (Halbwertszeit 30 d, Options-Feld
  `profile_half_life_days` 7–120). P50 = gewichteter Median (ersetzt das
  Stufe-1-Aggregat nahtlos), zusätzlich **P80**; `P80 ≥ P50` wird erzwungen.
- Die Rezenz-Gewichtung ist zugleich das Drift-/Saisonmodell (folgt der
  Jahreszeit mit ~1 Monat Latenz). Bewusst **kein P90** (bei n_eff ≈ 20–80
  zu instabil).
- Das harte Fenster (`learning_window_days`) wird auf 120 d erweitert
  (Delta-Backfill, §4.2); Store-Version 2 (§4.6).

### D-C8: Dynamischer SOC-Puffer (setzt N2 um — **sofort scharf**)

Vor jedem `plan()`-Aufruf berechnet der Coordinator (pfadgetrennt wegen der
unterschiedlichen Entladeketten):

```
kritisches Fenster K = jetzt … erster Slot mit prognostiziertem PV-Überschuss
                       (pv_wh > ac_wh + dc_wh; kein solcher Slot → ganzer Horizont)

unsicherheit_wh = Σ_K [ (P80_ac − P50_ac) / (η_entladen × η_inverter)     # AC über Inverter
                      + (P80_dc − P50_dc) / η_entladen ]                   # DC direkt

puffer_% = clamp(unsicherheit_wh / capacity_wh × 100,
                 buffer_min_percent, buffer_max_percent)
```

- **Wirkorte im Kern — bewusste Trennung:** `soc_buffer_percent` wirkt heute
  an vier Stellen: (1) Untergrenze der Schwellwertsuche, (2) Puffer-Floor
  der Lastallokation, (3) Geräte-Advisor, (4) **Trigger der
  Netz-Stütz-Eskalation** (D-A9). Der dynamische Puffer ersetzt die Wirkorte
  1–3 (gewollt: mehr Nachtreserve, konservativeres Einschalt-Gate). Wirkort 4
  bleibt am **fixen** Konfig-Puffer: neuer Kern-Parameter
  `ControlParams.support_buffer_percent` (Default = konfigurierter
  `soc_buffer_percent`), sonst würden die Netz-Stützpfade nachts (breites
  Band → bis 15 %) systematisch früher/öfter schalten — eine ungewollte
  Verhaltensänderung.
- **Teilgelerntes Profil:** Der dynamische Puffer ist aktiv, sobald
  **mindestens ein Pfad** gültige Quantile hat; statisch gefüllte Slots und
  ungelernte Pfade tragen **0** zur Summe bei (Abdeckungsquote im
  Diagnose-Attribut). Voll-Fallback auf den fixen `soc_buffer_percent` nur,
  wenn **kein** Pfad Quantile liefert.
- Geplant wird mit der erwartungstreuen **P50-Reihe** (D-A3: kein
  Pessimismus in der Lastreihe; konservativ nur über den Puffer).
- **Betreiber-Entscheid 2026-07-04: sofort scharf** — kein Parallelbetrieb.
  Absicherung: Klemmen (`buffer_min_percent` Default 3, `buffer_max_percent`
  Default 15, Options-Flow), Diagnose-Attribut (aktueller Puffer,
  Fensterlänge, Abdeckung) und der fixe Puffer als automatischer Fallback.
- Intuition: nachts (bis zum Morgen-Überschuss) ist das Band breit → großer
  Puffer; mittags schmal → kleiner Puffer.

### 5.3 Feiertage

Optionales Feld `workday_entity` (binary_sensor der Core-Integration
**Workday**; an = Werktag). Wirkung: Feiertag = Wochenend-Bin.
**Folgetage im Horizont:** Der Sensor zeigt nur *einen* Tag (heute bzw.
konfigurierter Offset) — für Slots nach Mitternacht fragt der Coordinator
die Aktion `workday.check_date` (Response-Daten, beliebiges Datum gegen die
Sensor-Konfiguration) einmal täglich für die nächsten 3 Tage ab und cached
das Ergebnis; schlägt der Aufruf fehl, gilt für Folgetage die reine
Kalenderregel (Sa/So). Lern-Tagging ab Konfiguration; historische Feiertage
vor der Einrichtung werden nicht rückwirkend getaggt (akzeptierte Unschärfe,
~1 Tag/Monat).

### D-C9: Validierungs-Wächter & Export-Zerlegung

- Täglich nach dem Nachtlauf: Vergleich P50-Prognose vs. bereinigte Ist-Last
  des Vortags → **MAE** und **Bias** je Pfad, als Diagnose-Sensor exponiert
  (`_unrecorded_attributes` für Detailreihen). Läuft der Bias 14 Tage
  einseitig über eine Schwelle (hartkodiert 15 %), wird ein **Repair-Issue**
  erzeugt (`issue_registry.async_create_issue`) statt still weiterzulernen
  (deckt Messpunkt-/Bereinigungsfehler auf).
- **Export-Zerlegung (Diagnose, optional):** Wo ein Export-Zähler existiert,
  gilt `Export − Σ (bekannte Nicht-Verbrauchs-Exporte)` ≈ echte verlorene
  Einspeisung → Vergleichswert für die `lost_surplus`-Metrik des Planers
  (Beispiel Anhang A). Nur Diagnose, kein Regelpfad.

## 6. Stufe 3 — Gerätesignaturen & erwartete Läufe

### 6.1 Lauf-Segmentierung aus der Historie

- Quellen je `detection_entity`: Leistungssensor → 5-min-Statistik
  (`statistics_short_term`; Retention folgt `purge_keep_days` — zur Laufzeit
  prüfen, nicht annehmen) bzw. Roh-States; Status-Entität (**keine LTS** für
  nicht-numerische Entitäten) → `history.state_changes_during_period` in
  Tages-Chunks, ein Aufruf je Entität, Recorder-Executor.
- Lauf = zusammenhängende „läuft"-Phase (gleiche Erkennungsregeln wie live);
  Aus-Lücken < 15 min werden überbrückt. Je Lauf: Dauer, Startzeit, Energie
  (nur bei Leistungssensor integrierbar) und **Abstand zum vorherigen Lauf
  in Tagen** (Basis des Intervall-Modells, D-C10).

### 6.2 Gelernte Signaturen ersetzen Konfig-Werte

- Ab ≥ 5 beobachteten Läufen: `median_energy_wh` / `median_duration_h`
  ersetzen `run_energy_wh` / `run_duration_h` bei der **Live-Einplanung**
  (Konfig-Werte bleiben Fallback und UI-sichtbar; Diagnose zeigt gelernt vs.
  konfiguriert). Status-only-Geräte: nur Dauer lernbar, Energie bleibt
  Konfig. Ablage: `appliance_signatures` im Store (§4.6).
- **Bugfix im Zuge:** Startzeit erkannter Läufe wird im Store persistiert
  (`active_run_started_at`; heute nur in-memory → HA-Neustart setzt den Lauf
  auf „frisch gestartet" zurück).

### D-C10: Erwartete Läufe im Horizont — Intervall-Modell (per Appliance abschaltbar)

Beobachtung (Betreiber-Hinweis 2026-07-04): Waschmaschine, Geschirrspüler
u. Ä. werden vom Nutzer in **annähernd festen Intervallen** gestartet. Eine
pauschale Tagesrate verschenkt diese Information — die Wahrscheinlichkeit
eines Laufs hängt stark davon ab, **wie lange der letzte Lauf her ist**.

- **Gelernt wird je Gerät die Lücken-Verteilung** (Tage zwischen
  aufeinanderfolgenden Lauf-Starts, §6.1) und daraus die empirische
  **Hazard-Funktion**:

  ```
  h(g) = P(Lauf heute | letzter Lauf vor g Tagen)
       = (# Lücken der Länge g) / (# Lücken der Länge ≥ g)      für g = 0 … 14
  ```

  Beispiel Geschirrspüler „alle 2 Tage": h(0) ≈ 0, h(1) klein, h(2) hoch,
  h(3) ≈ 1 — gestern gelaufen → heute unwahrscheinlich, übermorgen fast
  sicher.
- **Tagestyp-Modulation:** die Hazard-Wahrscheinlichkeit wird mit dem
  gelernten Tagestyp-Faktor skaliert (relative Laufhäufigkeit
  Werktag/Wochenende, normiert) — bildet „Waschtag ist samstags" ab, ohne
  ein zweites Modell zu brauchen.
- **Prognose je Horizont-Tag d** (rekursiv, mit g = Tage seit letztem
  beobachtetem Lauf): `p_d = h(g_d) × daytype_factor(d)`;
  für Folgetage wird `g` mit `(1 − p)`-gewichteter Erwartung fortgeschrieben.
  Erwartungsenergie
  `E[h] = p_d × median_energy × start_histogram(h)`, über die Laufdauer
  gefaltet; einfließen als zusätzliche AC-Reihe (analog D-C5, additiv vor
  den Live-Läufen).
- **Fallback-Kaskade:** < 8 beobachtete Lücken → pauschale Tagesrate je
  Tagestyp (einfaches Modell); < 5 Läufe insgesamt → keine Erwartung
  (Verhalten wie heute). Hazard-Horizont hart bei 14 Tagen gekappt
  (`h(>14) = h(14)`).
- **Ersetzungsprinzip:** sobald ein Lauf des Geräts heute erkannt wurde oder
  abgeschlossen ist, wird die Resterwartung des Geräts **für heute** auf 0
  gesetzt und `g` zurückgesetzt (keine Doppelzählung mit
  `_apply_appliance_runs`). Urlaubsmodus → Erwartung 0 (D-C4); nach
  Urlaubsende zählt `g` ab dem letzten realen Lauf weiter, gedeckelt durch
  den Hazard-Cap.
- Neues Subentry-Feld `expected_runs` (bool, Default **an**). Risiko:
  Erwartungswerte wirken wie leichter Pessimismus auf die
  Überschuss-Allokation (D-A3-Gegenargument) → nach Rollout anhand der
  Ladehäufigkeit der Überschusslasten und des Bias-Wächters (D-C9)
  beobachten; bei negativem Befund bleibt §6.2 allein bestehen.
- Intra-Lauf-Leistungskurven (Aufheizspitze): erst mit 15-min-Raster
  (D-A7/Phase 4) wieder bewerten.

## 7. Konfigurationsübersicht (alle Stufen)

| Feld | Ort | Stufe | Default |
|---|---|---|---|
| `ac_load_entity` / `ac_balance_in_entities` / `ac_balance_out_entities` | Basis- + Options-Flow | 1 | leer (= Lernen aus) |
| `dc_load_entity` / `dc_balance_*_entities` | Basis- + Options-Flow | 1 | leer |
| `in_house_measurement` | Überschusslast-Subentry | 1 | true |
| `learning_window_days` | Options-Flow | 1 | 42 (Stufe 2: 120) |
| `learning_max_age_days` | Options-Flow | 1 | 14 |
| Urlaubsmodus | eigene Switch-Entität | 1 | aus |
| statische Profilfelder (8×) | Basis- + **neu:** Options-Flow | 1 | wie bisher |
| `profile_half_life_days` | Options-Flow | 2 | 30 |
| `buffer_min_percent` / `buffer_max_percent` | Options-Flow | 2 | 3 / 15 |
| `workday_entity` | Basis- + Options-Flow | 2 | leer |
| `expected_runs` | Appliance-Subentry | 3 | an |

## 8. Verworfene Alternativen

| Idee | Grund |
|---|---|
| Fest benannte Bilanz-Slots (Import/Export/Inverter/…) | An eine Topologie (ESS) gebunden; die generischen Zufluss-/Abfluss-Listen (D-C1) decken jede Verdrahtung ab und sind einfacher zu erklären. |
| Topologiespezifische Stützpfad-Rückrechnung (z. B. PSU-Leistung addieren) | Erfordert Wissen über die Einspeiseseite; der generische Stunden-Ausschluss (D-C2 Schritt 3) ist bei seltenen Eskalationsstunden praktisch verlustfrei. |
| Explizites Saisonmodell (Monats-Buckets) | Rezenz-Gewichtung/rollierendes Fenster deckt Saison mit ~1 Monat Latenz ab; Monats-Buckets zersplittern die Stichprobe (Sa im Februar = 4 Samples). |
| 7×24-Wochentagsmatrix | ~6–8 Samples/Bin → instabile Mediane oder träges Riesenfenster; feste Wochentagsmuster deckt die Geräte-Erkennung ab. Bin-Schema bleibt erweiterbar gekapselt. |
| Temperatur-/Wetterregression | Typische Batteriesysteme dieser Klasse tragen keine thermischen Großlasten; hoher Aufwand, Overfitting-Risiko. Ausbauoption. |
| Automatische Urlaubs-/Abwesenheitserkennung | Fragile Heuristik; Betreiber-Wunsch ist der manuelle Modus (D-C4). |
| P90-Quantil | Bei n_eff ≈ 20–80 zu instabil; P80 + Klemmen reicht. |
| Pessimismusfaktor auf die Lastreihe | Von D-A3 explizit verworfen (verzerrt Überschuss-Allokation) — Unsicherheit wirkt nur über den Puffer (D-C8). |
| Separater `learning_enabled`-Toggle | Implizites Opt-in über die Mess-Entitäten; weniger Konfigurationsfläche. |
| 15-min-Profilraster | Keine Wirkung im Stunden-Planer; `statistics_short_term`-Retention konfigurationsabhängig. Schnittstelle ist rasterneutral (D-C5). |
| Roh-States als Lernquelle für Profile | 365 Tage states = Millionen Zeilen, Minuten-Queries, Recorder-Executor-Blockade; Stunden-LTS reicht und ist konstant billig. Roh-States nur für Stufe-3-Segmentierung von Status-Entitäten. |

## 9. Risiken & Beobachtungsplan

1. **Bereinigungs-Restfehler** (Lasten ohne `power_entity`, Schaltflanken auf
   Stundengrenzen): begrenzt durch Rate-Limit + Median; sichtbar über
   Negativ-Residuen und Bias-Wächter. Empfehlung: allen geschalteten Lasten
   einen Leistungssensor hinterlegen.
2. **Puffer sofort scharf** (Betreiber-Entscheid): Fehlkalibrierung würde
   direkt wirken → enge Klemmen (3–15 %), Diagnose-Attribut, Fallback auf
   fixen Puffer ohne gültige Quantile, Eskalations-Trigger vom dynamischen
   Puffer entkoppelt (D-C8). Erste 2 Wochen beobachten: Pufferverlauf **und
   Schalthäufigkeit der Stützpfade**.
3. **Falsches `in_house_measurement`-Flag / falscher Messpunkt**: schwerste
   Fehlkonfigurationsquelle → Negativ-Residuen-Diagnose, Hilfetexte mit
   Beispielen (Anhang A/B), Repair-Issue bei Häufung.
4. **Strukturbrüche** (neues Gerät, Homeoffice-Wechsel): 2–4 Wochen Bias,
   bis die Gewichtung nachzieht; Wächter meldet, heilt nicht schneller.
5. **Was diese Spec nicht löst:** PV-Intraday bleibt das Zwei-Fenster-Modell
   (P3, gleicher Andockpunkt — nächster großer Hebel);
   Batterie-Effizienzen/Kapazität bleiben unkalibriert.

## 10. Umsetzungsreihenfolge & Aufwand

| Schritt | Inhalt | Aufwand |
|---|---|---|
| 1a | `core/load_profile.py` (pure Funktionen) + `slot_starts`-Helfer + `build_slots`-Reihen + Tests | ~2 PT |
| 1b | `history_profile.py` (Queries, Bereinigung, Store je Entry, Nachtjob) + Coordinator-Anbindung + Urlaubsmodus-Switch + Diagnose | ~3 PT |
| 1c | Config-/Options-Flow (inkl. statischer Profilfelder im Options-Flow), Subentry-Flag, de/en, Doku | ~1–2 PT |
| — | **Deploy; Profile planungswirksam + 2 Wochen begleitende Beobachtung (§4.9)** | — |
| 2 | Quantile (Store v2), dyn. Puffer (scharf, `support_buffer_percent`), Wächter, `workday_entity` | ~3–4 PT |
| 3a | Signaturen lernen + Startzeit-Persistenz-Fix | ~3 PT |
| 3b | Erwartete Läufe (Intervall-Modell D-C10, Ersetzungsprinzip, Flag) | ~2–3 PT |

Jede Stufe ist unabhängig ausrollbar; Stufe 1 ist ohne konfigurierte
Mess-Entitäten vollständig inert (Breaking-Change-frei).

---

## Anhang A: Referenzbeispiel — Victron-ESS-Setup des Betreibers

Verifiziert am 2026-07-04 auf der Live-Instanz (alle Zähler kWh
`total_increasing` mit Stunden-LTS ≥ 17 Monate; Roh-States ~350 Tage,
`purge_keep_days ≈ 365`). Victron MultiPlus „verkehrt" verdrahtet:
**AC-IN1 = 230-V-Wohnungsnetz**, **AC-Out1 = PV-Mikrowechselrichter**.

**AC-Zählerbilanz (D-C1):**

| Liste | Entität | Fluss |
|---|---|---|
| `ac_balance_in_entities` | `sensor.victron_grid_energy_forward_total_30` | Netz → Wohnung |
| | `sensor.victron_vebus_invertertoacin1_228` | Batterie → Wohnung |
| | `sensor.victron_vebus_acouttoacin1_228` | PV-Durchleitung → Wohnung |
| `ac_balance_out_entities` | `sensor.victron_grid_energy_reverse_total_30` | Wohnung → Netz (Export) |
| | `sensor.victron_vebus_acin1toinverter_228` | Wohnung → Charger (Batterieladen) |

**DC-Pfad:** `dc_load_entity = sensor.victron_system_system_power`
(W, `measurement`; misst Batterie → 48-V-Verbraucher inkl. DC/DC und
24-V-Schiene). Alternative/Plausibilisierung:
`victron_dcsystem_history_energyin_229` − `…out_229`.

**Nulleinspeisungs-Muster (Beispiel für `in_house_measurement = false`):**
Der Entfeuchter steht im Gemeinschaftskeller **vor** dem Grid-Zähler. Eine
Betreiber-Automation setzt seine gemessene Leistung
(`sensor.fritz_powerline_546e_power`) als ESS-Einspeise-Sollwert — die
Wohnung exportiert exakt den Kellerverbrauch (Nulleinspeisung am echten
Hauszähler). Der Entfeuchter steckt damit im `reverse`-Zähler und wird von
der Bilanz **automatisch ausgeschlossen** → Subentry-Flag
`in_house_measurement = false` (kein Doppelabzug!). Die Fossibot-Steckdosen
hängen dagegen im Wohnungsnetz → Flag `true`, Bereinigung über ihre
`total_input`-Leistungssensoren (LTS vorhanden).

**Export-Zerlegung (D-C9):** verlorene Einspeisung ≈
`reverse − fritz_powerline-Energie` (fritz-LTS ist lückenhaft — 9 von 14
Monaten — daher nur Diagnose).

**Empfohlene Einstellungen:** Bilanz wie oben; Entfeuchter-Subentry
zusätzlich `power_entity = sensor.fritz_powerline_546e_power` (bessere
Planleistung); `workday_entity` nach Installation der Workday-Integration.
Ignorierte Kleinflüsse: `invertertoacout`, `acin1toacout` (je ~4 kWh
Gesamtstand, Verluste/Randflüsse). Hinweis: verwaiste Alt-Statistiken mit
Suffix `_40` stammen von einer früheren Geräteanlage und werden ignoriert.

## Anhang B: Weitere typische Setups

**B1 — Direkter Hausverbrauchssensor (häufigster Fall):**
Energiezähler misst die Hauslast direkt (z. B. Shelly 3EM auf dem
Verbraucherabgang, „House Consumption" eines Hybrid-Wechselrichters) →
`ac_load_entity` setzen, fertig. Enthält der Messpunkt von der Integration
geschaltete Lasten, deren Subentries auf `in_house_measurement = true`
lassen (Default) — sie werden über D-C2 herausgerechnet.

**B2 — Nur Netzzähler + PV-Erzeugung (AC-gekoppelt, ohne Batteriezähler im
Hausbus):**
`ac_balance_in_entities = [Netzimport, PV-Erzeugung]`,
`ac_balance_out_entities = [Netzexport]`. Lädt die Batterie aus dem Hausbus,
zusätzlich den Charger-Zähler in die Abflüsse; entlädt sie in den Hausbus,
den Inverter-Zähler in die Zuflüsse.

**B3 — DC-gekoppelte Anlage:** Hybrid-WR liefert meist „Load"/„Consumption"
direkt → B1. Für einen separaten DC-Verbraucherzweig: `dc_load_entity`
(Shunt) oder DC-Bilanzlisten.
