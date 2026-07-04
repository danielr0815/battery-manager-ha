# Battery Manager — Lösungsstrategie für die Überarbeitung

> Status: **Entwurf zur Abstimmung** (Stand 2026-07-03)
> Basiert auf [REQUIREMENTS.md](REQUIREMENTS.md).

## 1. Kernidee des neuen Algorithmus

Ohne Einspeisevergütung ist die Zielfunktion einfach und messbar:

> **Minimiere `Netzimport + Netzexport` über den Prognosehorizont.**

Daraus folgt die eigentliche Aufgabe der Steuerung:

- **Export entsteht**, wenn PV-Überschuss kommt und die Batterie voll ist
  → vorher gezielt Platz schaffen (Inverter laufen lassen, Haus aus Batterie
  versorgen) **oder** Überschuss in Zusatzlasten lenken.
- **Import entsteht**, wenn die Batterie leer ist, bevor der nächste
  PV-Überschuss kommt → nicht zu viel entladen.

Die SOC-Schwelle des Inverters balanciert genau diesen Zielkonflikt. Statt sie
wie bisher mit einer Heuristik-Formel zu schätzen, wird sie künftig **durch
Simulation gesucht**:

### Schwellwert per Politik-Simulation (statt Formel)

```
für jeden Kandidaten T in {min_soc … max_soc, Schrittweite 1 %}:
    simuliere den Horizont mit der ECHTEN Politik:
        Inverter an  ⇔  SOC > T
    bewerte: kosten(T) = w_i · import_kWh + w_e · export_kWh
wähle T mit minimalen Kosten (bei Gleichstand: höheres T = batterieschonender)
```

- **Politik-konsistent:** Die Simulation bildet exakt das Verhalten ab, das der
  ausgegebene Schwellwert real bewirkt (behebt Schwäche 2.2 aus REQUIREMENTS.md).
- **Rechenaufwand trivial:** ~90 Kandidaten × ~60 Stunden × einfache Bilanz —
  weit unter einer Sekunde, reine Python-Stdlib, keine Solver-Abhängigkeit.
- **Erklärbar & testbar:** Für jeden Kandidaten existiert eine nachvollziehbare
  Trajektorie; Tests können Kostenwerte direkt prüfen.

### Überschusslasten per Überschuss-Allokation

Nach der Schwellwert-Wahl liefert die Gewinner-Trajektorie pro Stunde den
**verlorenen Überschuss** (Energie, die exportiert würde, weil Batterie voll
bzw. Charger am Limit). Darauf arbeitet die Lastplanung:

```
für jede Stunde h mit Überschuss S(h) > 0:
    für jede verfügbare Last L in Prioritätsreihenfolge:
        wenn S(h) ≥ P(L) · (1 − Batterie-Toleranz):
            plane L in Stunde h ein; S(h) -= P(L)
re-simuliere mit eingeplanten Lasten (volle Horizont-Prüfung):
    – kein zusätzlicher Netzimport gegenüber Plan ohne Lasten (Z2)
    – min-SOC nie verletzt (Z3)
    verletzt → Stunde/Last streichen, wiederholen (konvergiert schnell)
```

- Aktivierung **nur bei realem Überschuss** (behebt Schwäche 2.1/1).
- Prüfung über den **gesamten Horizont**, nicht bis zum ersten Ziel-Erreichen
  (behebt Schwäche 2.1/2 und 2.1/3).
- Parallelbetrieb mehrerer Lasten ergibt sich natürlich aus der Allokation.
- Fossibot-Besonderheiten: verfügbare Restenergie aus SOC-Entität
  (voll → Last gesättigt, L8), Leistung aus Feedback-Entität geglättet (L7).

### Haushaltsgeräte

- **Laufender Betrieb erkannt** (LG-Washer-Status / Steckdosenleistung):
  hinterlegtes Restlaufprofil (kWh über n Stunden) wird der AC-Lastprognose
  aufaddiert (G2). Damit sinkt der berechnete Überschuss automatisch und
  Zusatzlasten weichen zurück — genau die vom Betreiber gewünschte Korrektur.
- **Startfenster-Empfehlung (G3):** Simulation „Was wäre, wenn das Gerät jetzt
  startet?" → binary_sensor `*_start_window` = an, wenn der komplette Lauf
  keinen zusätzlichen Netzimport erzeugt.

## 2. Verworfene Alternativen

| Ansatz | Warum nicht |
|---|---|
| **Bestehende Heuristik patchen** | Behebt die akuten Bugs, aber Politik-Inkonsistenz und fehlende Zielfunktion bleiben; Mehrlasten/Geräte passen nicht ins Modell. |
| **LP/MILP-Optimierung (z. B. PuLP/HiGHS)** | Mathematisch optimal, aber externe Solver-Binaries sind auf HAOS/Alpine fragil, schwer zu debuggen, und der Nutzen ggü. der Simulationssuche ist bei diesem Problemumfang (1 Schwelle + wenige Lasten) minimal. Kann später als Option nachgerüstet werden. |
| **ML/gelernte Politik** | Datenbedarf, Erklärbarkeit und Wartbarkeit stehen in keinem Verhältnis zum Problem. |

## 3. Architektur

### 3.1 Simulationskern (rewrite, HA-frei, `battery_manager/core/`)

```
core/
├── model.py       # Frozen dataclasses: BatteryParams, ChargerParams,
│                  # InverterParams, SurplusLoad, Appliance, SystemConfig
├── series.py      # Aufbau der Stundenreihen: PV-Verteilung, Lastprofile,
│                  # Geräte-Restläufe → HourlyInputs
├── simulate.py    # simulate(config, inputs, policy) -> Trajectory
│                  # PURE FUNCTION: keine Seiteneffekte, kein Shared State
└── optimize.py    # Schwellwertsuche + Überschuss-Allokation + Geräte-Advisor
                   # -> PlanResult (Schwelle, Lastpläne, Prognosen, Flüsse)
```

Prinzipien (Q1): unveränderliche Konfigurationsobjekte, Ein-/Ausgaben als
Datenklassen, keine Objekte mit verstecktem Zustand wie bisher
(`set_additional_load_active`, SOC-Mutation an geteilten Instanzen).
Die Energiefluss-Bilanz einer Stunde wird aus `energy_flow.py` fachlich
übernommen, aber als pure Funktion neu implementiert.

### 3.2 HA-Schicht

- **Coordinator:** Listener-Bug + fehlendes Unsubscribe beheben (Q3);
  ansonsten Struktur beibehalten (Polling + Debounce bei Entity-Änderungen).
- **Config Flow v2** (Breaking Change, D4):
  - Basiskonfiguration wie bisher (Batterie, PV, Charger, Inverter, Grundlasten).
  - **Config Subentries** (HA ≥ 2025.3) je Überschusslast und je Haushaltsgerät:
    beliebig viele Lasten/Geräte über „Hinzufügen"-UI, einzeln editierbar.
- **Entitäten:**
  - bestehende Sensoren bleiben (Schwelle, Inverter-Status, Min/Max-SOC, …)
  - je Überschusslast: `binary_sensor.<last>_empfehlung` (+ Attribute: geplante
    Stunden, erwartete Energie)
  - je Gerät: `binary_sensor.<gerät>_startfenster`
  - `sensor.verlorener_überschuss_kwh` (prognostizierter Export) als
    Transparenz-/Debug-Wert
- **Version 0.2.0**, `translations/de.json` + `en.json` neu.

### 3.3 Tests & CI

- **Kern:** Szenario-Tests (Sonnentag/Regentag/Übergang, volle/leere Batterie),
  Invarianten-Tests (Energieerhaltung pro Stunde, SOC-Grenzen, „Zusatzlast
  erzeugt nie Import"), Regressionstests mit den Fehlbildern aus Abschnitt 2.1
  der REQUIREMENTS.md.
- **HA-Schicht:** pytest-homeassistant-custom-component: Config Flow, Coordinator
  (inkl. Listener!), Entity-Werte.
- **CI:** pytest + ruff in validate.yml ergänzen.

## 4. Umsetzungsphasen

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **0** | Dev-Umgebung, pytest-Harness, Coordinator-Bugfix (Listener) | Sofort mergebar, Basis für alles Weitere |
| **1** | Kern-Rewrite (`core/`), Schwellwert-Suche, EINE Überschusslast mit korrekter Überschusslogik, Testsuite, CI | Behebt alle bekannten Algorithmus-Fehler |
| **2** | Mehrere Lasten (Subentries, Prioritäten, parallel), Fossibot-Feedback (Leistung/SOC), Last-Entitäten | Zielbild Überschussverwertung |
| **3** | Haushaltsgeräte: Restlauf-Prognose + Startfenster-Empfehlung | Zielbild Geräte |
| **4** *(optional)* | Stündliche PV-Prognosen (Solcast/Forecast.Solar direkt), gelernte Lastprofile aus HA-Historie, de/en-Feinschliff | Genauigkeit |

Jede Phase endet mit lauffähigen Tests und einem testbaren Stand auf der
realen HA-Instanz (http://hass:8123).
