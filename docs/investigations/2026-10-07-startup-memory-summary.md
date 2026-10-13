# Offene Ursache der Speicherüberläufe beim HA Start

Befunde vom 07. und 08.10.2026, eingeordnet am 12.10.2026 für v0.57.0.
Die Speicherüberläufe sind belegt; die verursachende Allokation ist weiterhin
nicht eindeutig zugeordnet. Der separat reproduzierte Archivverlust wurde
in v0.56.2 behoben. Das ist kein Nachweis einer OOM-Behebung.

## Prozesspeak und Archivgegenproben

Am 07.10. lief HA 2026.9.4 mit Python 3.14.6 und Battery Manager v0.56.1.
Während des Archivladens stieg der Prozesshöchststand von etwa 538 auf
2.510 MiB. Die zusätzliche Spitze gegenüber dem RSS vor dem Laden betrug
etwa 1.977 MiB. Der Höchststand gilt für den gesamten HA-Prozess, nicht
für den Besitzer der gleichzeitig protokollierten Phase. Allokationstracing
war in dieser Beobachtung ausgeschaltet.

Isolierte Gegenproben mit den später kopierten Originalarchiven benötigten
etwa 43–69 MiB zusätzlichen RSS-Höchststand. Verglichen wurden Pythonversion,
Allocator und Worker-Ausführung. 48 Archivchunks mit 2.219.348 Bytes und
4.454 Ereignissen wurden geprüft; Dateihashes und gelesener Produktcode passten.
Die Kopie entstand nach dem Start und ist kein exakter Snapshot seiner Dateien.

[Bereinigte Messwerte](2026-10-07-startup-memory-measurements.json) erhalten
die Prozessphasen und isolierten Laufresultate. Eine fremde Integration,
native Bibliothek oder parallele Initialisierung ist damit weder bewiesen
noch ausgeschlossen. Der Zeitüberlappung allein lässt sich keine Ursache entnehmen.

## Weitere Beobachtungen am Folgetag

Kernelmeldungen belegten einen globalen OOM mit getötetem HA-Core-Prozess und
nahezu ausgeschöpftem Swap. In zwei Fehlstarts war Archivladen und Validierung
vor dem starken Speicheranstieg bereits beendet. Safe Mode lief erfolgreich,
schließt jedoch mehrere Integrationen gleichzeitig aus und identifiziert
keinen einzelnen Verursacher.

Ein regulärer Neustart mit zwei vorübergehend gestoppten Zusatzdiensten lief
anschließend erfolgreich; beide Dienste wurden wiederhergestellt. Der gemessene
Startpeak betrug etwa 709 MiB. Das belegt die erfolgreiche Erholung unter
diesen Bedingungen, erklärt aber die ursprüngliche Core-Allokation nicht.
Prozess-IDs, Containerkennungen und operative Kommandoprotokolle bleiben lokal.

## Archivverlust getrennt behandeln

Speichern vor Abschluss der asynchronen Wiederherstellung konnte das Journal
mit dem leeren Startzustand überschreiben. v0.56.2 hält Persistenz bis nach
Laden, Validieren und Übernahme zurück; Abbruch und Fehler schützen die Datei.
Vertrag und Regressionen: [Archivschutz beim Start](../F-ARCHIVE-STARTUP.md),
[Tests](../../tests/ha/test_archive_startup.py).

## Nächster belastbarer Nachweis

Zeitlich begrenzte externe Prozessmessung mit Stack- oder Allokationszuordnung
während des Peaks, möglichst in einer isolierten Kopie, bleibt sinnvoll.
RSS, Python-Allokationen, Tracing-Overhead und parallel laufende HA-Arbeit
müssen getrennt bleiben. Zusätzlicher Tracing-Speicher gehört in das Messbudget.
Der [historische Startdiagnosevertrag](../F-STARTUP-MEMORY-DIAGNOSTICS.md)
beschreibt die damalige begrenzte Erfassung; längeres Beobachten allein ist kein
Beweis der Ursache.
