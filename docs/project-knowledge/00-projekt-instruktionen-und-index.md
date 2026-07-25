# Projekt-Instruktionen & Index

**Stand: `main` @ v0.16.2 (2026-07-25).** Diese Datei ist der Einstieg in die
Wissensbasis zum Repository **`danielr0815/battery-manager-ha`**
(<https://github.com/danielr0815/battery-manager-ha>). Sie erklärt, was das
Projekt ist, welches Wissensdokument wofür zuständig ist, über welchen Pfad man
in eine konkrete Aufgabe einsteigt — und sie enthält einen kopierfertigen Block
mit den Projekt-Instruktionen für claude.ai.

---

## 1. Was ist battery-manager-ha

Eine **Home-Assistant-Custom-Integration** (HACS-installierbar, Domain
`battery_manager`), die eine Hausbatterie (Victron/Pylontech, ~5 kWh nutzbar)
und mehrere **Überschusslasten** (Entfeuchter, Fossibot-Powerstations) so
steuert, dass PV-Überschuss eines Balkonkraftwerks maximal genutzt statt
exportiert wird — ohne je Netzstrom für Lasten zu kaufen.

Kernideen, die das Projekt von simplen Überschuss-Automationen unterscheiden:

- **Planender Optimierer statt Schwellwert-Logik:** Ein HA-freier Kern
  (`custom_components/battery_manager/core/`) simuliert den Energiefluss über
  einen 2–3-Tage-Horizont (Stundenslots), sucht die Inverter-Abschaltschwelle
  T* und allokiert Lasten in zwei Pässen (Direktüberschuss; „covered by
  otherwise-lost export" mit Latest-First-Platzierung).
- **Strict-Surplus als hartes Prinzip** (Operator-Regel): Zusatzlasten kaufen
  nie Import und laufen nie netzgespeist — abgesichert doppelt im Planner
  (Gates) und im Executor (G4-Floor-Guard).
- **Nacht-Pre-Drain:** Vor sicheren Klipptagen wird die Batterie nachts
  kontrolliert in Lasten entladen, damit der Morgenüberschuss Platz hat —
  quantilbasiert (p10/p90-Bänder des PV-Forecasters), mit α-Skalar-Fallback.
- **Lern-Schichten:** Median-basierte Ist-Leistung je Last, Verbrauchsprofile
  (p50/p80, Wochentag/Wochenende) aus der Recorder-Historie, Tank-Laufzeit des
  Entfeuchters.
- **Ehrliche Selbstauskunft:** per-Slot-`why`-Strings am Plan, `daily`-Aufriss,
  prevented_export, Diagnostics-Endpoint.

Technische Eckdaten (Stand v0.16.2):

| Punkt | Wert |
|---|---|
| Aktueller Stand | `main` @ **v0.16.2** (Commit `a172d48`); manifest.json + pyproject.toml versionssynchron (CI-Gate) |
| Plattformen | `sensor`, `binary_sensor`, `switch`, `button`, Diagnostics; Konfiguration komplett über Config-Flow + **Subentries** (je Last / Appliance) |
| Architekturgrenze | `core/` (model/series/simulate/optimize) importiert nichts aus `homeassistant` — pur und einzeln testbar; `coordinator.py` (~3800 Zeilen) ist die Ausführungs- und Zustandsschicht |
| Prognose-Lieferant | Schwester-Repo `danielr0815/balcony-solar-forecast` (15-min-PV-Prognose + p10/p90-Bänder); Kopplung + Schriftwechsel siehe Dokument 06 §5 |
| Referenz-Anlage | Live-HA des Betreibers (Victron 5 kWh, 8-Modul-Balkonanlage, Entfeuchter + 2 Fossibots); Deploy ausschließlich über GitHub-Releases + HACS |
| Tests | volle Suite nur unter WSL lauffähig (fcntl); ruff unter Windows; Golden-Tests für Planentscheidungen |

**Wofür diese Wissensbasis da ist:** Sie soll einen Assistenten (oder
Menschen) ohne Vorwissen und ohne Chatverlauf arbeitsfähig machen: Code ändern,
eine Planentscheidung forensisch nachvollziehen, den Betreiber beraten, ein
Release bauen. Sie ist destillierte Analyse, kein Ersatz für den Code — jede
Verhaltensaussage wurde gegen `main` @ v0.16.2 geprüft, aber Code altert
schneller als Prosa. **Im Zweifel gilt der Code.**

---

## 2. Index der Wissensdokumente

Alle Dateien liegen in `docs/project-knowledge/`.

### `01-architektur-und-datenfluss.md`
Modul-Landkarte (jede Datei mit Verantwortung und Einstiegspunkt), der
Update-Zyklus des Coordinators (Kadenz, Reihenfolge der Schritte vom
Input-Einlesen bis zum Schalten), die Eingangsdaten (SOC, PV-Prognose +
Quantil-Bänder, gelernte Verbrauchsprofile, Appliance-Runs), die
Kern-Datentypen (`PlanInputs`, `HourSlot`, `SurplusLoadState`) und was
persistiert wird.
*Brauchst du:* zuerst, wenn du im Repo etwas suchst oder wissen musst, wo ein
Wert herkommt.

### `02-planner-und-optimizer.md`
Der HA-freie Kern exakt wie implementiert: T*-Suche mit Merge-Probe und
stetiger Terminal-Credit-Rampe (F1), Simulation, Pre-Drain (α, c1/c2,
Z-Regeln), Pass-1-/Pass-2-Lastallokation inkl. Quanten, Seamless-Spill (F9),
Gates und die R-/Z-Regelfamilie, energielimitierte Lasten (Ziel-SOC,
gate-topup), Prioritäten, exakte Semantik von lost_surplus / grid_import /
prevented_export (**Horizontsummen**, `daily`-Attribut für Tageswerte).
*Brauchst du:* wenn du eine Planentscheidung nachrechnest oder den Optimierer
änderst.

### `03-executor-und-lastregeln.md`
Die Ausführungsschicht: Empfehlungs-Binaries (zeigen den **Plan**, nicht den
Gerätezustand!), Schaltpfad mit allen Entscheidungszweigen, Gates G1/G2/G4,
Dwells (min_runtime/min_off) mit Seamless-Runs, Flicker-Continuation (F8),
Latch-Hold via Schatten-Plan (F10/F11), Power-Warning-Latch (F-L7) mit
F5-Planungs-Rückfluss, Tank-Modell (V6), Median-Power-Learning,
Grid-Support 24/48 V, Startup-Grace, INFO-Schaltprotokoll.
*Brauchst du:* wenn ein Gerät nicht (oder unerwartet) schaltet.

### `04-ha-integration-entities-services.md`
Die Außenschnittstelle: alle Entities mit Bedeutung und Live-Entity-IDs, das
Attribut-Vertragswerk des `soc_forecast`-Sensors (forecast, loads/why, daily,
quantile_coverage, gate_calibration, consumption_profile, …), Config-Flow +
Subentry-Optionen je Lastklasse (inkl. `tank_full_runtime_min`,
`in_house_measurement`, power-warning-Optionen), Buttons, Diagnostics,
Übersetzungen.
*Brauchst du:* für UI-/Konfig-Fragen und um Sensorwerte korrekt zu
interpretieren.

### `05-anlage-und-betrieb-runbook.md`
Die Referenz-Anlage und der Betrieb: Geräte- und Entity-Landkarte des
Live-Systems (Victron, EM540-Messkreis-Eigenheit!, Shelly, Fritz, Fossibots
inkl. deren Flakiness), Deploy-Rezept (GitHub-Release → HACS → Restart),
typische Diagnosewege (Floor-Guard-Trips, Latch-Zustände, stale Sensoren),
Legacy-Automatisierungen und ihr Zusammenspiel mit dem BM.
*Brauchst du:* für Betriebsfragen und Live-Forensik.

### `06-forensik-juli-2026-und-offene-punkte.md`
Das Gedächtnis: die 7-Tage-Forensik (Woche in Zahlen), die Fix-Serie F1–F11
mit Live-Heilungsnachweisen, **bewusst Verworfenes** (nicht wieder
vorschlagen!), offene Punkte/Watch-Items, die Kopplung zum
balcony-solar-forecast inkl. Abnahmekriterien, Methodik-Notizen für künftige
Forensiken.
*Brauchst du:* bevor du ein „komisches" Verhalten neu analysierst oder einen
Verbesserungsvorschlag machst.

### `07-entwicklung-tests-release.md`
Arbeitsumgebung und Konventionen: WSL-Testumgebung, ruff, Golden-Tests,
CI-Workflows (Validate mit Versions-Gate; Ruff formatiert auch
Markdown-Codeblöcke!), Release-Rezept, Feature-Doku-Konvention (F-*.md),
Review-Lektionen.
*Brauchst du:* bevor du Code änderst, testest oder released.

---

## 3. Einstiegspfade nach Aufgabentyp

| Aufgabe | Pfad |
|---|---|
| „Warum hat der BM gestern X entschieden?" | 06 §6 (Methodik) → Daten ziehen → 02 (Planner-Regeln) → 03 (Executor-Gates) |
| „Last schaltet nicht / schaltet komisch" | 03 (Schaltpfad + Gates + Latches) → 05 (Live-Entities, Log) → 06 (bekannt?) |
| Code-Änderung am Planner | 02 → 07 (Tests/Goldens) → 06 §3 (Verworfenes!) |
| Neue Last / Konfig-Frage des Betreibers | 04 (Subentry-Optionen) → 05 (Anlage) |
| Release bauen | 07 (Rezept, CI-Fallen) |
| Prognose scheint falsch | 06 §5 (Balcony-Kopplung, Abnahmekriterien) → ggf. Auftrag ans Schwester-Repo |

---

## 4. Projekt-Instruktionen für claude.ai (kopierfertig)

```
Du arbeitest am Repository danielr0815/battery-manager-ha — eine
Home-Assistant-Custom-Integration (Domain battery_manager), die eine
Victron-Hausbatterie und Überschusslasten (Entfeuchter, Fossibot-
Powerstations) mit einem planenden Optimierer steuert. Das Projektwissen
liegt in den Dokumenten 00–07 (docs/project-knowledge/); 00 enthält den
Index. Stand der Wissensbasis: main @ v0.16.2 (2026-07-25) — bei neueren
Versionen gilt der Code bzw. das CHANGELOG.

Grundregeln:
1. Im Zweifel gilt der Code, nicht die Doku. ARCHITECTURE.md/STRATEGY.md im
   Repo sind veraltet (v0.7.x); aktuell sind die F-*.md-Feature-Dokumente,
   LOAD_CONTROL.md und diese Wissensbasis.
2. Unverhandelbare Operator-Regeln: (a) Strict-Surplus — Zusatzlasten kaufen
   nie Import und laufen nie netzgespeist (G4). (b) Lasten so spät wie
   möglich, gerade früh genug um Export zu vermeiden (Latest-First).
   (c) Geräte mit Verbrauchsbehälter (Entfeuchter) werden NIE vorsorglich
   gedrosselt oder abgeschaltet, weil ein Zustand vermutet wird — der BM
   reagiert auf gemessene Realität; Prognosen dürfen nur benachrichtigen.
   (d) Eine gelatchte/verdächtige Last darf nie strengeren Regeln unterliegen
   als dieselbe Last im Normalzustand (F11-Prinzip: Schatten-Plan).
3. Bevor du ein beobachtetes Fehlverhalten neu analysierst oder einen Fix
   vorschlägst: Dokument 06 prüfen (Fix-Serie F1–F11, bewusst Verworfenes,
   Watch-Items). Schlage nichts aus der Verworfen-Liste erneut vor.
4. Code-Änderungen: Kern-Logik gehört nach core/ (HA-frei, pur), Zustands-/
   Schaltlogik in coordinator.py. Konstanten mit Begründungs-Kommentar nach
   const.py. Neue Verhaltensregeln bekommen ein F-*.md. Tests sind Pflicht;
   die HA-Suite läuft nur unter WSL (Dokument 07). Vor jedem Push:
   ruff format + ruff check; nach Agent-Reviews: grep MUTANT.
5. Release-Disziplin: Versionen in manifest.json UND pyproject.toml bumpen
   (CI-Gate), Push auf main, CI abwarten, GitHub-Release erstellen (HACS
   trackt Releases, nicht Commits). Der Betreiber deployt via HACS.
6. Der Betreiber (Daniel) kommuniziert deutsch, technisch versiert, direkt.
   Antworte deutsch, mit konkreten Zahlen/Zeitstempeln/Entity-IDs. Bei
   Live-Diagnosen: Zeitstempel der HA-History sind UTC, lokale Zeit ist
   CEST/CET; das HA-Log schreibt Lokalzeit.
7. Die PV-Prognose kommt aus dem Schwester-Repo balcony-solar-forecast.
   Prognose-Wurzelprobleme werden nicht im BM „weggefixt", sondern als
   Arbeitsauftrag (docs/orders/-Muster) ans Schwester-Repo formuliert;
   BM-seitig sind nur Robustheit/Fallbacks (α-Skalar, p10==p90-Heuristik)
   legitim.
```

---

## 5. Pflege dieser Wissensbasis

- Bei jedem Release mit Verhaltensänderung: betroffenes Dokument anfassen und
  die Stand-Zeile aktualisieren; 06 wächst um Forensik-Erkenntnisse und
  erledigte/neue offene Punkte.
- Die Dokumente sind **destilliert**, nicht generiert — keine
  Auto-Regeneration, sondern gezielte Edits mit Code-Verifikation.
- Quelle der Wahrheit für Historie: CHANGELOG.md + GitHub-Releases; für
  Live-Zustände: die Anlage selbst (Runbook in 05).
