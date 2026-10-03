# Oktoberumsetzung ab 0.54.0

Diese Umsetzung folgt dem [Oktoberplan](OPTIMIZATION-PLAN-2026-10.md).
Die bestehende Zielhierarchie, binäre Inverterfreigabe, manuelle Aufträge und
nominaler Pass 3 bleiben maßgeblich. Schutzregressionen werden korrigiert;
Forschungsvarianten erteilen keine produktive Freigabe.

## Exakte Archive und kleine Runtimepersistenz

Der HA-Runtime-Store enthält Aktorpflichten und kleine Zustandsmetadaten.
Das Diagnosearchiv liegt unter `.storage/battery_manager.archive.<Entry-Hash>/`.
Ein Schema-3-Manifest referenziert nach UTC-Stunde und höchstens 512 Ereignissen
getrennte Chunks. Pläne verwenden exakte strukturelle Ersetzungen und spätestens
alle 64 Planstände einen vollständigen Checkpoint. Feldreihenfolge, Zahlentypen,
Vorzeichen von Null und ursprünglicher Planhash bleiben erhalten. Es gibt keine
Quantisierung oder fachliche Kompression. Export und Replay behalten Schema 2.

Ein einzelner Worker schreibt neue Dateien und veröffentlicht das Manifest
atomar mit `fsync` und Umbenennung. Bis dahin bleibt das vorherige Manifest
maßgeblich; ein zweites Manifest dient der Wiederherstellung. Dateinamen,
Dateigrößen, Dekompressionsgrenzen und Planhashes werden geprüft. Ein beschädigter
Chunk wird isoliert; andere Stunden und Tagesberichte bleiben lesbar. Die
Lücke unterbricht Replay-Integration, und der Wiederherstellungsfehler bleibt
sichtbar. Das ist eine Chunk-Transaktion, kein append-only WAL.

Neue Ereignisse werden über einen einzigen ausstehenden Snapshot gesammelt,
ohne zwischenliegende Kommandos zu verlieren. Die normale Schreibverzögerung
beträgt zehn Sekunden. Unload wartet auf den aktiven Worker und schreibt den
letzten Stand. Ein ungeordneter Prozessabbruch kann die noch nicht veröffentlichten
Ereignisse verlieren; ein erfolgreicher atomarer Commit bleibt maßgeblich.
Ein Entry-Remove entfernt auch seine Chunk-Dateien.

Eine Altarchivmigration hält das alte Archiv im Runtime-Store bis zum ersten
erfolgreichen Commit. Zusätzlich bleibt eine komprimierte Migrationskopie bis
zum nächsten verifizierten Reload erhalten. Beschädigte Archivdaten beeinflussen
die unabhängig geladenen Aktorpflichten nicht. Ein Downgrade liest die neue
separate Ablage nicht; dafür ist das vorherige HA-Backup erforderlich.

Die Grenzen sind sieben Tage, 100.000 Ereignisse und 64 MiB auf Disk inklusive
Transaktionsdateien. 48 MiB sind für aufbewahrte Chunks vorgesehen. Das Journal
im Speicher hat zusätzlich eine Grenze von 256 MiB für die logische Größe seiner
komprimierten Pläne und Ereignisse; das ist kein Grenzwert für den gesamten
Python-Prozess. Diese Grenzen sind vorläufige Betriebsbudgets. Die Diagnose
zeigt tatsächlich erhaltenen Beginn, Ende, Ereigniszahl und Verdrängungen.
Sie garantiert keine volle Woche bei beliebig hoher Ereignisrate.

Restore, Validierung, Dateiarbeit und große Exportkopien laufen außerhalb des
HA-Ereignisloops. Der aktive Zustand wird erst nach erfolgreicher Validierung
übernommen; ein entladener Coordinator übernimmt kein verspätetes Ergebnis.
Diagnosekontext und Archivsnapshot werden vor dem ersten Export-Await erfasst.

## Entscheidungsnachweis

Der Plan wird vor dem Live-Vergleich journalisiert. Geänderte Liveentscheidungen
tragen Grund, geplantes und angefordertes Limit sowie Aufnahme-/Aktivierungszeit.
Kommandos tragen diesen Kontext und ihren eigenen Journalbezug. Ein Service-ACK
ist weiterhin keine Gerätebestätigung. Angefragtes Limit, gemeldeter Wert,
Publikationszeit und Bestätigungsstatus bleiben getrennte Größen.
Wiederholte identische Fünf-Sekunden-Entscheidungen erzeugen keine neuen Zeilen.

## Forschung und unabhängige Anlagenvergleiche

`uv run python scripts/compare_reserve.py <planner-recording.json>` wertet
höchstens fünf Rückhaltswerte unter nominaler, pessimistischer und oberer PV aus.
Die akzeptierten Last-/Einspeisepläne bleiben feste Randbedingungen. Der Bericht
trennt Import, DC-Netzenergie einschließlich Wirkungsgrad, zeitliche
DC-Unterversorgung, Endspeicher und später nutzbare AC-Energie. Der terminale
Wert ist eine deklarierte Horizontannahme; ein Forecast-Vorteil ist kein gemessener
Wochengewinn. Keine Variante wird automatisch ausgewählt oder live aktiviert.

Die neuen HA-Anlagentests führen unabhängige physische Energiebilanzen durch
Reserveplanung, Live-AC und Aktorrückmeldung. Mehrere Tage, unerwartete AC-Spitzen,
Preisabdeckungslücken und beide Sommerzeitwechsel sind gekoppelt geprüft.
Sie ergänzen die vorhandenen Einzeltests für Netzteilfehler, eingefrorene
Messungen, nicht bestätigte Aktoren und Kaskaden. Sie beweisen synthetische
Versorgungssicherheit und Bilanzierung, keine Ersparnis der installierten Anlage.

Die Modellversuche O14 benötigen weitere valide Messreihen: nach ganzen Läufen
getrennte Gerätephasen, genügend Programme und eine belegte PV/DC-Messgrenze.
Die Diagnose kennzeichnet unbekannte Unsicherheit und Quellenqualität bereits.
Ersatzbänder, nichtlineare Restenergie und eine geänderte gemeinsame Busbilanz
werden erst nach deren Vergleich übernommen. Dafür enthält dieser Release
keine unbewiesene Änderung der produktiven Modelle.
