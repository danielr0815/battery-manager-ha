# Untersuchungen und Aufbewahrung

Stand: 12.10.2026, v0.57.1. Hier bleiben technische Befunde, ausgewählte
Messaggregate und Umsetzungsnachweise erhalten. Historische Fehlerprotokolle
beschreiben den jeweils genannten Code, nicht automatisch den aktuellen Stand.
Maßgeblich sind [aktuelle Verträge](../CURRENT_CONTRACTS.md) und deren Tests.

## Weiterhin hilfreiche Befunde

| Untersuchung | Zweck und heutige Einordnung |
| --- | --- |
| [Review vom 02.10.](2026-10-02-review-summary.md) | Schutzfehler und Gegenproben hinter dem Oktoberplan; Endenergievergleich gegen unbewiesene Reserveersparnis. |
| [Umsetzung vom 03.10.](2026-10-03-implementation-validation.md) | Validierung von v0.54.0 und exakte Archivrekonstruktion. |
| [Speicherbefunde vom 07./08.10.](2026-10-07-startup-memory-summary.md) | OOM-Ursache bleibt offen; Archivverlust und Prozesspeak sind getrennte Befunde. |
| [Wochenbefunde vom 11.10.](2026-10-11-seven-day-review.txt) | Beobachtete Quellenwechsel, Lernlücken und CPU vor v0.57.0 mit heutigem Umsetzungsstatus. |
| [Umsetzung der Wochenbefunde](2026-10-11-improvements.txt) | Historischer Kandidatennachweis von v0.57.0 einschließlich instrumentiertem CPU-Replay. |

Die JSON-Gegenproben und Messaggregate sind Ergebnisprotokolle, keine eigenständig
ausführbaren Tests. Die [Testmatrix](../TEST_MATRIX.md) ordnet heutige Regressionen
zu. Live-Ersparnis oder erfolgreiche Installation lassen sich nicht aus lokalen
Testresultaten ableiten.

## Komprimierte lokale Aufbewahrung

Die Bereinigung hat zwei Archive unter
`.local_dev/archives/2026-10-12-document-cleanup/` angelegt. `.local_dev/` ist
bereits durch Git ignoriert. Jedes Archiv enthält ein `MANIFEST.json` mit
ursprünglichem Pfad, Dateigröße und SHA-256. Alle enthaltenen Originale wurden
nach dem Komprimieren bytegenau geprüft.

| Archiv | Inhalt | Originaldateien | Komprimierte Bytes |
| --- | --- | ---: | ---: |
| `operational-evidence-2026-09-07-to-2026-10-11.tar.gz` | Ursprüngliche Berichte, Recorder-/Planverläufe, Konfigurationsaudit und Start-/Speichernachweise. | 19 | 316105 |
| `historical-guide-v0.49.0.tar.gz` | Alter Leitfaden, sieben finale Diagramme, Portal, bearbeitbare Spezifikationen und zugehörige finale Prüfnachweise. | 62 | 3119946 |

SHA-256 des Betriebsarchivs:
`3f95ce6fb4ac73f29d7c9ab794d5e8ec7e6adabf59c1e721a5cfdea1fc3d49b6`.
SHA-256 des historischen Leitfadens:
`f7ea135effaf85f7e85987c4248fcaac9f13b4ec37308e50dcb11d2d125d91fc`.

Der lokale `inventory.json` dokumentiert die Archive und 29 entfernte redundante
Archify-Dateien. Diese Zwischenstände sind nicht für die finalen Prüfnachweise
erforderlich. Der historische Diagrammstand beschreibt v0.49.0 und wird nicht
als aktuelle Anleitung veröffentlicht. Der [deutsche Leitfaden](../BATTERY_MANAGER_GUIDE.md)
beschreibt stattdessen v0.57.1 ohne Abhängigkeit von den archivierten HTML-Dateien.

Das Betriebsarchiv enthält konkrete Geräte-, Haushalts- und Systemdaten und
bleibt lokal. Ein frischer Clone enthält diese Archive nicht. Die komprimierte
Ablage ist keine externe Sicherung und umfasst keine Browserprofile oder
weitere Rohdaten aus `.playwright-mcp/` oder temporären Verzeichnissen.
