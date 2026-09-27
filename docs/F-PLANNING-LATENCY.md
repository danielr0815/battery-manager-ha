# Planungsdauer und Initialisierung ab 0.46.1

## Befund vom 27.09.2026

Nach Auswahl des Netzverfügbarkeitssensors blieb die HA-Integration ab
08:10:29 Europe/Berlin mehrere Minuten in `setup_in_progress`. Der SOC war
bekannt; weder die SOC-Anlaufkulanz noch eine Lern-Wartezeit verursachten die
Verzögerung. `async_setup_entry` wartete auf die gesamte erste Aktualisierung.
Der aktive Reservemodus berechnet dabei zunächst einen Vergleichsplan und
anschließend den Reserveplan. Jeder davon bewertet zahlreiche Lastkandidaten
über einen mehrtägigen Horizont mit Fünf-Minuten-Schritten.

Eine lokale Profiler-Stichprobe mit 64 gespeicherten Prognosestunden zeigte
bereits in einem Teil der Suche 170 Simulationen, rund 921.000 `replace`-Aufrufe
und 519.000 erneute numerische Modellvalidierungen. Die Dataclass-Kopien
beanspruchten rund 41 von 52 Sekunden kumulierter Profiler-Laufzeit.

Der Pfad bestand bereits in 0.45.1. Vergleichsläufe mit denselben Eingaben und
freigegebenem 48-V-Pfad benötigten lokal ungefähr 103 Sekunden unter 0.45.1 und
146 Sekunden unter 0.46.0. Die mit 0.46.0 erweiterten Eingangsvalidierungen
verstärkten die Kosten der wiederholten Konstruktionen. Diese Einzelmessungen
sind eine Ursachenanalyse, keine Laufzeitgarantie für andere Hardware.

Nach der Korrektur benötigte derselbe Reserveplan mit 64 Slots in drei lokalen
Läufen 13,00 / 12,86 / 12,89 Sekunden (Median 12,89 Sekunden). Der Netzbezug
betrug weiterhin 879,92 Wh. Historische Messungen waren Einzelmessungen;
CPU-Auslastung und Cachezustand können die absoluten Laufzeiten beeinflussen.

## Verbindliche Regeln

1. Das Laden der Persistenz und die Einrichtung der Entities bleiben geordnet.
   Die erste wirtschaftliche Planung läuft anschließend als Entry-gebundene
   Hintergrundaufgabe. Prognose-Entities bleiben bis zu einem gültigen Ergebnis
   unverfügbar. Ein geladener Entry bedeutet nicht, dass bereits ein Plan vorliegt.
2. Pro Coordinator läuft höchstens eine Aktualisierung gleichzeitig. Die
   Vergleichs-, Reserve- und gegebenenfalls Last-Schattenplanung bleiben seriell.
3. Die Eingangsvalidierung bleibt vollständig erhalten. Reservekonfigurationen
   werden je benötigter Betriebsvariante wiederverwendet; das zeitabhängige
   Inverterlimit wird separat als begrenzte Betriebsgröße übergeben.
4. Unveränderliche Teilintervalle werden in einem begrenzten Cache geteilt.
   Alle Slotdaten und der UTC-Offset gehören zum Schlüssel. Fortschreibung
   erfolgt bei zeitzonenbehafteten Werten in UTC, damit die doppelte Herbststunde
   weder verwechselt noch durch lokale Datumsarithmetik zurückgesetzt wird.
5. Ein Peak-Fill-Kandidat ohne den dafür notwendigen PV-Überschuss wird vor der
   Simulation verworfen. Die Diagnose lautet `no_peak_fill_surplus`. Das ändert
   die Erklärung ausgeschlossener Kandidaten, nicht deren Zulässigkeit,
   Energieflüsse oder Allokation. Golden-Energiebilanzen werden nicht angepasst.
6. Während CPU-Arbeit prüfen Sensorereignisse und spätestens alle fünf Sekunden
   ein unabhängiger Schutzpfad SOC-Schutz, Quellenrückfall und nötige Abschaltungen.
   Bestehende Aktorbesitz-, Bestätigungs-, Kaskaden- und OFF-Regeln gelten weiter.
   Ein unbekanntes Netz bei ausgeschalteten Netzteilen widerruft keine bereits
   zulässige Batterieentladung. Schutzprüfungen geben keine AC-Entladung frei.
7. SOC-Abweichungen über einen Prozentpunkt (höchstens die konfigurierte
   Hysterese), jeder Wechsel über eine Versorgungsschutzschwelle, unbekannter SOC
   oder Ablauf des ersten Planungsslots verwirft den berechneten
   Stand. Diese Prüfung gilt während und nach einer Phase sowie vor den
   Aktoraufträgen nach einer Last-Schattenplanung. Ein neuer Refresh liest die
   aktuellen Eingaben; ein veralteter Plan darf keine neuen Starts rechtfertigen.
8. Beim Entladen werden die Hintergrundaufgaben abgebrochen. Zusätzlich erhält
   der CPU-Worker ein kooperatives Abbruchsignal, das spätestens vor der nächsten
   Simulation greift. Auf das Ende des Workers wird gewartet; ein abgebrochener
   asyncio-Waiter allein wäre kein Abbruch der CPU-Arbeit.

## Diagnose und reproduzierbare Messung

Der HA-Diagnosedownload enthält `planning` mit `phase`, `status`,
`elapsed_seconds` und `last_phase_seconds`. Phasen heißen `baseline`, `reserve`,
`standard` und bei Bedarf `load_shadow`. Laufzeiten stammen aus einer monotonen
Uhr; sie werden nicht als fachliche Planereingaben verwendet.

Ein vollständiger Diagnosedownload oder eine Planneraufzeichnung lässt sich
lokal ohne HA und ohne Aktorzugriff messen:

```bash
uv run python scripts/benchmark_plan.py recording.json --repeat 3
uv run python scripts/benchmark_plan.py recording.json --policy baseline --repeat 3
uv run python scripts/benchmark_plan.py recording.json --profile --repeat 1
```

`--policy` verändert ausschließlich den Reservemodus. Quellenfreigaben bleiben
wie aufgezeichnet. Für Versionsvergleiche dieselbe Aufzeichnung, Python-Version
und Hardware verwenden; Profiling und reine Laufzeitmessung getrennt betrachten.
Es gibt kein von der Rechnergeschwindigkeit abhängiges CI-Zeitlimit.

## Nachweise

- `tests/core/test_planning_performance.py`: äquivalente Inverterlimits,
  begrenzte Konfigurationskonstruktionen, unveränderte Energiemengen beim
  Wiederverwenden von Teilintervallen, DST-Unterscheidung und Abbruchkontext.
- `tests/ha/test_planning.py`: blockierter erster Plan bei fertig geladenem Entry,
  unverfügbare Prognosen bis zum Ergebnis, Entladen, Workerabbruch, kontrollierte
  Schutzkadenz, SOC-Abfall, Quellenfreigabe, überlappende Refreshes und alte Slots.
- Bestehende Kern-, Golden-, Quellenumschalt- und Lastabschaltungstests bleiben
  verbindlich. Testhilfen warten bei Bedarf ausdrücklich auf die erste
  Hintergrundplanung; Lifecycle-Tests beobachten den noch wartenden Zustand.

Abnahme im Entwicklungscontainer: 2.077 Python-Tests erfolgreich,
97,64 % Gesamt-Coverage, Kern 100 %, alle 46 Modul-Coverage-Gates erfüllt.
Zusätzlich bestanden 46 Frontendtests und sieben Chromium-Browsertests sowie
Ruff, Formatprüfung, mypy (46 Quelldateien), Frontend-Lint/Formatprüfung,
Bundle- und Versionskonsistenzprüfung. Golden-Dateien blieben unverändert.

Die Änderung wird regulär über ein Release/HACS installiert. Diese Untersuchung
ändert weder Live-Konfiguration noch laufende HA-Aktoren automatisch.
