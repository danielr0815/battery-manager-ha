# Implementierungsabschluss 0.63.0

Stand: 29.10.2026. **Implementierung, lokale Qualitätsgates und unabhängiges
fachliches Review abgeschlossen.** Veröffentlichung, Installation und
B1/B2-Live-Umstellung bleiben spätere Schritte. Bis zur Implementierungsabnahme wurde nichts live verändert, committed oder
veröffentlicht; Commit und Release sind anschließend ausdrücklich beauftragt. Die vorhandene Nutzeränderung an `AGENTS.md`
bleibt unangetastet.

## Implementierter Umfang

Die Planung ergänzt rückladegesicherte DCDC-/AC-Hausversorgung, allgemeine
Speicher-Mindestziele und kausale Recovery-/Top-up-Buchungen. Referenz,
Rückladetermine, Unsicherheitsvorhalt und Suchbudget werden gemeinsam geschützt.
Aktive manuelle Einspeisung verwendet konservativ das bisherige Verfahren ohne
zusätzliche Rückladekredite; die Diagnose zeigt diese Grenze an.

Die Eingangssteuerung trennt Ladefreigabe, tatsächliche Laderückmeldung,
Eingangs-Schalthoheit und Passthrough. Eigene Eingänge mit Ausgang ON oder
unbekanntem konfiguriertem Ausgang werden gehalten; nach bestätigtem OFF wird
die Abschaltung auch ohne erfolgreichen neuen Energieplan nachgeholt.
Manuelle Übernahme entzieht nur die Eingangs-Schalthoheit. Manifest,
Projektmetadaten und Lockfile sind konsistent auf 0.63.0 vorbereitet.

Die Karten und Diagnosen zeigen Speicherziele, Rückladezusagen, Eingangsaufnahme
während einer Ladepause, Haltegründe und fehlende Rückmeldungen getrennt.
Unbekannte Messwerte werden nicht als gemessene Nullwerte ausgegeben.

## Qualitätsnachweise

Alle vorgeschriebenen lokalen Prüfungen sind bestanden:

- Reiner Core: **1.022 Tests**, 5.387 Statements, keine fehlende Zeile,
  **100 % Coverage**.
- Kombinierte Core-/HA-Suite: **3.214 Tests**, 204,46 s,
  **98,108 % Zeilen-Coverage**. Alle **75 Module** erfüllen ihr Ziel:
  Core jeweils 100 %, HA jeweils mindestens 95 %.
- Separater CI-Branch-Lauf: **3.214 Tests**, 207,81 s; 6.117 von 6.454
  Branches abgedeckt (**94,778 %**). Dies ist der ergänzende Bericht ohne
  zusätzliches Prozentgate; der Line-Bericht bleibt getrennt erhalten.
- Ruff, Repository-Formatprüfung (351 Dateien), mypy (75 Quellen),
  `git diff --check` und Versionskonsistenz Manifest/Projekt/Lockfile bestehen.
  Keine `MUTANT`-Markierungen in Produktionscode oder Tests.
- Frontend: Build, Bundleprüfung, Lint, Format, Unit-Tests und **45 Browsertests**
  erfolgreich. Die letzte Rasterkorrektur hat keine Frontend-Datei verändert.
- **362 gezielte Tests** prüfen die zuletzt korrigierten Core-Module zusätzlich.

Astra / High hat Energie-, Speicher- und Eingangsschaltlogik unabhängig geprüft
und alle gemeldeten fachlichen Befunde korrekturgeprüft. Die abschließende
fachliche Gesamtfreigabe einschließlich Rasterfix ist erteilt; es bestehen
keine offenen Review-Befunde. Die Freigabe gilt für den unveränderten geprüften
Produktionsstand und umfasst keine Live-Inbetriebnahme.

Die Korrekturprüfungen betreffen unter anderem Stressschutz ohne neue
Zertifikate, Appliance-Eingaben, manuelle Einspeisung, globale Speicherpriorität,
Speicherstart-Minima, obere Zielstopps, bestätigte Dwell bei erschöpftem Budget
und Eingangs-Schalthoheit bei verspäteten Rückmeldungen. Bestätigte
Kaskaden-Dwell enthält die einmaligen Ausgangsverluste über die tatsächliche
Union der Downstream-Laufzeiten (Regressionen: 110 Wh und 136,25 Wh).

## Historische Evidenz und Rasterregression

Die Untersuchung verwendete eine private Diagnose mit Planneraufzeichnung und
komprimierten historischen Plänen unter
`/tmp/bm-live-investigation-2026-10-27/live-bm-diagnostics.json` (ca. 135 MB).
Sie wurde nicht ins Repository aufgenommen. Bei der späteren Fortsetzung am
29.10. waren diese temporären Dateien auch hostseitig nicht mehr vorhanden.
Die folgenden Ergebnisse wurden zuvor tatsächlich beobachtet; eine weitere
Wiederholung ohne Originaldateien wird nicht behauptet.

Frühere Replays reproduzierten beide historischen Aufzeichnungen exakt. Die
Nacht wurde vor dem letzten Rasterfix weiterhin exakt reproduziert. Der
11:03-Lauf nach dem Rasterfix ergab erneut `exact_match=True`.

| Planungseingang, lokale Zeit | Import (kWh) | Export (kWh) | Minimum-SOC (%) | End-SOC (%) |
|---|---:|---:|---:|---:|
| 27.10., 00:58:51 | 10,452014 | 0 | 55,238017 | 76,987827 |
| 27.10., 11:03:19 | 8,809506 | 0,014886 | 62,099730 | 76,994710 |

Die alten Eingaben enthalten keinen Verbrauchsunsicherheitssnapshot und erhalten
daher keine neue Rückladefreigabe. Die historische 11:03-Referenz enthält
275,916667 Wh B1-Ladung und 0 Wh B2-Ladung; diese werden nach dem Rasterfix
wieder exakt reproduziert. Die Aufzeichnung enthält die neuen allgemeinen
Speicherverträge noch nicht. Sie prüft deshalb den Altvertrag und beweist keine
neue Recovery oder vollständig stressgeprüfte historische Zusatzfreigabe.

Der historische Vergleich fand eine vermeidbare Kopplung zwischen neuer
Mindestaktionsdauer und Suchraster: Ein konfiguriertes 5-Minuten-Raster wurde
auf 15 Minuten vergröbert und verlor dadurch einen zulässigen 40-Minuten-Lauf.
Die Korrektur erhält das feine Raster und prüft 15 Minuten beziehungsweise
längere konfigurierte Mindestdauer sowie 50 Wh unabhängig an der tatsächlichen
neuen Aktion. Die öffentliche synthetische Regression reproduziert die
50,1667 Wh Exportdifferenz ohne private Haushaltsdaten; kurze echte Neustarts
bleiben verboten, laufende Ladungen dürfen exakt am Ziel stoppen.

## Laufzeitvergleich mit identischen synthetischen Eingaben

Der endgültige [Messbericht](2026-10-29-planner-benchmark.json) enthält
Messzeitpunkte, Quell-/Eingabehashes, alle drei Einzelwiederholungen,
Arbeitszähler und physische Ergebnisse. Sol / Low hat die Varianten sequenziell
ohne parallel gestartete Tests gemessen. Baseline ist der unveränderte Core aus
`35ed1db4538c7822062812617b81b609fde7c1fc`.

| Fall | Baseline-Median | Neuer Median | Einordnung |
|---|---:|---:|---|
| 72 Stunden, zwei unabhängige Kaskaden | 6,65782 s | 9,15622 s | +37,5 %, geänderte Speicherbuchungen und mehr Prüfungen |
| Aktiver 3-Stunden-AC-Zertifikatsfall | kein Altvertrag | 0,02389 s | Unsicherheitssnapshot vorhanden, tatsächliche Zusatzfreigabe |

Beim Mehrkaskadenfall sinkt Import von 1.091,667 auf 1.050 Wh; Export steigt
von 69,172 auf 201,665 Wh. DC-Dienst bleibt vollständig bei 3.600 Wh und
Endenergie jeweils 4.750 Wh. Vollständige Policy-Läufe steigen von 260 auf
354, physische Schritte von 54.136 auf 76.782. Der Vergleich verwendet dieselben
physischen Eingaben, plant aber andere Speicheraktionen; er ist kein Vergleich
identischer Optimierungsarbeit. Der höhere Export wird ausdrücklich ausgewiesen.

Der aktive AC-Fall zertifiziert zusätzlich 25 Wh Hausversorgung bei idealen
Wirkungsgraden und vollständigem Null-Unsicherheitssnapshot: 1.000-Wh-Batterie,
Start-SOC 80 %, Obergrenze 95 %, drei Stunden mit (PV, AC, DC) = (0, 100, 0),
(0, 600, 0), (325, 0, 0) Wh. AC-Dienst beträgt 175 Wh, Import 525 Wh und
Endenergie 950 Wh; der Restexport entfällt.

Die ausschließlich synthetischen Originaleingaben wurden bytegleich aus ihren
ursprünglichen lokalen Generatoren rekonstruiert und dauerhaft gespeichert:

- [72 Stunden / zwei Kaskaden](fixtures/house-storage-benchmark-multicascade.json),
  SHA-256 `d9a9e2d1df00737580b2797056887bacc4258397b7b587e9c547c3112c8626a1`.
- [Aktiver AC-Fall](fixtures/house-storage-benchmark-active.json),
  SHA-256 `3237cd1ac24e8c1377754b5074cf1d3efbf86b5dfa90d78ef55f82db0befe595`.

Ein neuer historischer Laufzeitvergleich wird wegen der fehlenden privaten
Originaldateien nicht behauptet.

## DCDC-Nachweisgrenze

Der öffentliche Plan schützt DCDC-Hausversorgung vor optionaler Recovery und
Top-up. Zusätzliche DCDC-Zertifikate sind durch die vollständige nachgelagerte
physische Integration mit einer eng eingesetzten gültigen Referenz belegt:
Zertifizierung, optionale Buchung, aktive Marktumschichtung und PV-Stress des
veröffentlichten Plans. Ein natürlich erzeugter zusätzlicher öffentlicher
DCDC-Zertifikatsfall wurde bislang nicht beobachtet und wird nicht behauptet.
Die Schutzregeln wurden dafür nicht abgeschwächt. Ein Suchlimit beweist keine
Unmöglichkeit einer weiteren Freigabe.

## Modellrollen und Verbrauchsmessung

- Umsetzung, Analyse und Testentwicklung: GPT-6.1 Sol / Medium.
- Vorgegebene Prüf- und Messläufe: GPT-6.1 Sol / Low.
- Unabhängiges kritisches Review und Korrekturprüfungen: GPT-6 Astra / High.
- Begrenzte Eskalationsanalyse zur positiven DCDC-Suche: GPT-6.1 Sol / High,
  nach belegten erfolglosen Positivsuchen und zwei Suchraumerweiterungen.

Tatsächliche lokale Input-, Cache- und Output-Zähler stehen im abgegrenzten
[Messstand](2026-10-28-model-usage.json). Der Root-Zähler umfasst Untersuchung,
Planung und Implementierung gemeinsam; Agentenzähler gruppieren die zuvor
zugewiesenen Aufgaben. Cache-Input und Reasoning-Output werden nicht doppelt
gezählt. Aufwand nach dem jeweiligen Messstichtag ist nicht enthalten.
Der Messstand enthält den vor dem Commit gesetzten Stichtag und einen
normalisierten hypothetischen API-Vergleich zum am 29.10.2026 geprüften
[offiziellen Standard-/Short-Referenztarif](https://developers.openai.com/api/docs/pricing).
Dies rekonstruiert weder die tatsächliche Kontext-/Service-Tier-Mischung noch
die tatsächliche Abrechnung. Diese sowie Aufwand nach dem Stichtag bleiben
unbekannt und werden nicht als enthalten ausgegeben.

Der verbindliche Vertrag steht in
[F-HOUSE-RECHARGE-STORAGE](../F-HOUSE-RECHARGE-STORAGE.md). Vor aktiver Einführung
bleiben Shadow-/Live-Prüfungen und die Prüfung der realen Ausgangsrückmeldung,
getrennten Ladefreigabe und Schalthoheit erforderlich. B1/B2 werden dabei nicht
aus einem bereits eingeschalteten Eingang als eigene Aktoren rekonstruiert.
