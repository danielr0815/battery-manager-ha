# Betriebsarchiv während der Wiederherstellung schützen

Ab 0.56.2.

## Befund

Am 8. Oktober 2026 blieb die Wiederherstellung des Betriebsarchivs während
eines später durch den Kernel beendeten HA-Starts offen. Beim Wiederanlauf
enthielt das Archiv deutlich weniger Ereignisse. Der lokale Gegenversuch
reproduziert den Fehler mit echten Archivdateien und einem angehaltenen
Ladevorgang: Ein früher Persistenzaufruf veröffentlicht den noch leeren
Recorder und ersetzt damit die vorher gespeicherte Historie.

Sensorlistener und Schutzsteuerung existieren bereits während der asynchronen
Wiederherstellung. Ihre Speicherung darf deshalb nicht davon ausgehen, dass
der Recorder schon den vollständigen Archivstand enthält. Dieser Fehler ist
unabhängig von der noch nicht zugeordneten Speicherspitze des HA-Prozesses.

## Regeln

1. Ein neuer Recorder hat zunächst keine Schreibfreigabe. Sowohl verzögerte
   Speicherung als auch explizites Flushen erhalten das vorhandene Archiv.
2. Die Freigabe folgt erst auf Laden, Validieren und Übernahme des Archivs.
   Eine tatsächlich fehlende Historie ist ein gültiger Erststart.
3. Abbruch, fehlgeschlagene Validierung und ein inzwischen entladener
   Coordinator erteilen keine Freigabe. Ein nicht lesbares Archiv ohne
   wiederherstellbaren Ersatz bleibt für die Diagnose erhalten.
4. Die bestehende Wiederherstellung gültiger Altbestände sowie die isolierte
   Behandlung beschädigter Blöcke bleiben erhalten. Ein validierter Ersatz
   darf nach den bisherigen Migrationsregeln gespeichert werden.
5. Die Sperre betrifft die Archivdateien. Schutzsteuerung und Speicherung
   kleiner Laufzeitdaten bleiben unabhängig. Temporäre Beobachtungen während
   des Ladens ersetzen keine rekonstruierbare historische Beobachtung.
6. `operation_report.persistence_ready` macht die Schreibfreigabe sichtbar.
   Das Archivschema und vorhandene Diagnoseattribute bleiben kompatibel.

## Nachweise

`tests/ha/test_archive_startup.py` prüft den Inhalt echter Dateien vor und
während eines verzögerten Ladevorgangs, nach Abbruch, beim Entladen und nach
Lese-/Validierungsfehlern. Nach erfolgreicher Übernahme müssen alter und neuer
Eintrag gemeinsam auf Disk stehen. Der Erststart ohne Datei wird separat
geprüft. Async-Barrieren und gemockte Timer ersetzen sämtliche Wartezeiten.

Die Korrektur stellt bereits überschriebene historische Daten nicht automatisch
wieder her. Dafür sind gesicherte Archivkopien und eine gesonderte, geprüfte
Zusammenführung mit den seitdem neu aufgezeichneten Ereignissen erforderlich.
