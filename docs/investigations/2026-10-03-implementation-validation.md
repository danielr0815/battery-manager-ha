# Validierung der Oktoberumsetzung

Stand: 03.10.2026, Arbeitsbaum für 0.54.0, Ausgangscommit `a211549`.
Manifest, Projektmetadaten und Lockfile sind versionsgleich. Keine Installation
auf der produktiven HA-Anlage und keine Änderung ihrer Konfiguration/Aktoren.

## Qualitätsprüfungen

| Prüfung | Ergebnis |
| --- | --- |
| Vollständige Python-/HA-Suite, vier Worker | 2.500 Tests, keine Fehler, Ausfälle oder übersprungenen Tests |
| Gesamte Line-Coverage | 97,83 % |
| Modul-Gates | Alle 60 Module bestanden; Core 100 %, jedes HA-Modul mindestens 95 % |
| Core-Suite ohne HA-Plugin | 100 % Coverage |
| Topology-Golden neu generiert | Alle 16 Szenarien unverändert; kein zusätzlicher Netzbezug |
| Ruff Check / Format einschließlich Markdown | Bestanden |
| mypy, gesamte Integration | Bestanden |
| Frontend Build / Bundle / ESLint / Prettier | Bestanden |
| Frontend-Unit-Tests | 76 bestanden |
| Chromium-Browsertests | 36 bestanden |
| Versionsprüfung und `uv sync --locked --group dev` | Bestanden, Version 0.54.0 |
| Offline-Reserve-CLI mit erhaltenem Diagnoseexport | 15 Varianten, drei PV-Szenarien; keine Live-Auswahl |

Die HA-Suite verwendet virtuelle Zeit für produktive Verzögerungen. Beobachtete
Worker-Veröffentlichungen werden über Ereignisse synchronisiert. Chunk-Dateien
liegen pro Test in einer isolierten temporären Ablage; wiederholte Läufe teilen
keine Archivdateien im Installationsverzeichnis der HA-Testhelfer.

## Exaktheit mit erhaltenen Betriebsdaten

Alle **6.938 Ereignisse** des erhaltenen Diagnosearchivs wurden in die neuen
Chunks geschrieben und zurückgelesen. Alle Ereignisse und ursprünglichen
Planhashes blieben erhalten; zusätzlich wurden rekonstruierte Pläne mit den
Originalen verglichen. An Chunk-Grenzen mehrfach benötigte Pläne ergeben
2.242 Planreferenzen bei 2.219 unterschiedlichen erhaltenen Planständen.

Die Chunks benötigen zusammen **4.447.485 Bytes**, der größte **268.605 Bytes**.
Der gesamte Encode-/Decode-/Originalvergleich benötigte lokal 48,52 Sekunden
während weiterer Prüfungen. Das ist kein HA-Loop-Delay und kein belastbarer
Geschwindigkeitsvergleich. Der parallele Heartbeat-Test belegt gesondert,
dass Restore und Dateiarbeit die Ereignisschleife freilassen.
Maschinenlesbares Ergebnis: [Codec-Prüfung](2026-10-03-archive-codec-check.json).

Dieser Datenbestand enthält weiterhin nur etwa 22,18 Stunden Einzelentscheidungen.
Die Kompression erzeugt keine verlorenen älteren Entscheidungen. Eine vollständige
Woche und der RAM-Bedarf auf der installierten Hardware müssen nach Ausrollung
beobachtet werden. Größen- und Ereignisgrenzen können vorher erreicht werden;
die Diagnose weist die tatsächlich erhaltene Spanne aus.

## Geltungsgrenzen

Der unabhängige Anlagenvergleich prüft synthetische Folgetage, AC-Spitzen,
Preisabdeckungslücken und beide DST-Tage durch Coordinator, Reserve, Live-AC
und Rückmeldung. Einzeltests ergänzen Fehlbestätigungen, Quellenausfälle und
Kaskaden. Ein Feldnachweis einer Wochenersparnis folgt daraus nicht.

Reserve-Alternativen bleiben offline. Unbekannte Quantilbänder werden von
gemessenen Nullbändern getrennt ausgewiesen, ohne den bisherigen Puffer zu
verändern. Nichtlineare Gerätephasen, alternative Unsicherheitspuffer und eine
andere PV/DC-Busbilanz benötigen valide zusätzliche Messreihen und den im
[Plan](../OPTIMIZATION-PLAN-2026-10.md) beschriebenen Vergleich.
