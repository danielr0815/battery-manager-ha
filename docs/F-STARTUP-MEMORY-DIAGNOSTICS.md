# Startdiagnose für Speicherfehler

Ab 0.56.1. Anlass: Wiederholte globale OOM-Abbrüche beim HA-Neustart, zuletzt
2026-10-07 um 16:31 Uhr Europe/Berlin. HA Core wurde nach etwa 33 Sekunden mit
Signal 9 beendet; RAM und Swap waren erschöpft. Die verursachende Integration
ist bisher nicht festgestellt. Ein erfolgreicher Neustart widerlegt den Fehler
nicht; der isolierte Planner-Test deckt Archivladen und Verbrauchslernen nicht ab.

## Leichte Messung bei jedem Entry-Start

`startup_diagnostics.py` zeichnet vom Beginn der Battery-Manager-Einrichtung an
höchstens 120 Sekunden lang alle zwei Sekunden Prozess- und Hostspeicher auf.
Der Timer beginnt bei Einrichtung der Integration, nicht beim VM-Boot. Zusätzlich
gibt es Beginn-/Ende-/Abbruch-Marker für:

- Wiederherstellung gespeicherter Verbrauchsprofile und des Runtime-Stores;
- Laden und Validieren des Betriebsarchivs;
- Einrichtung der Entity-Plattformen;
- Planung einschließlich ihrer Phase;
- Verbrauchslernen und einzelne Recorder-Abfragen.

Alle Zahlen sind **Bytes**: `rss_bytes`, `peak_rss_bytes` (bisheriger Prozesspeak),
`swap_bytes`, `host_total_bytes`, `host_available_bytes`, `host_swap_total_bytes`
und `host_swap_free_bytes`. Nicht lesbare procfs-Felder fehlen; sie werden nicht
als null gemeldet. UTC-Zeitstempel und PID unterscheiden Starts und Prozesse.
`elapsed_seconds` verwendet eine monotone Uhr. `phases` zeigt gleichzeitig aktive
Abschnitte; Marker erlauben die Bestimmung ihrer Dauer.

Jeder Messpunkt wird sofort als INFO-Zeile `Startup memory [Entry-ID]: {JSON}`
im HA-Log ausgegeben. Das Host-Journal enthält damit auch vor einem OOM bereits
geschriebene Messpunkte. Im Diagnose-Download steht unter `startup_memory` eine
auf 96 Messpunkte begrenzte Momentaufnahme. Es gibt keine neuen Entity-Attribute,
Pflichtoptionen oder Runtime-Pakete. procfs und Tracing laufen im Executor.
Ein Messfehler unterbricht keine Planung; Entladen/Stoppen beendet den Timer und
wartet auf laufende Messarbeit. Fehlgeschlagene Einrichtung räumt ebenfalls auf.

**Interpretation:** RSS gehört dem gesamten HA-Prozess. Andere Integrationen
können während eines Battery-Manager-Abschnitts Speicher belegen. Der Unterschied
zweier Marker ist daher ein Hinweis auf den Zeitraum, keine exklusive Zuordnung.
Der Prozesspeak sinkt nicht, wenn Speicher freigegeben wird. Hostfelder stammen
aus dem für den Prozess sichtbaren Linux-procfs.

## Optionale Python-Aufzeichnung für genau einen Start

Für eine Zuordnung zu Python-Dateien im Terminal-/SSH-Add-on vorbereiten:

```sh
mkdir -p /config/battery_manager
touch /config/battery_manager/trace-next-start
```

Der erste Battery-Manager-Entry, der die reguläre Markerdatei entfernt, startet
`tracemalloc` mit drei Stackframes. Eine symbolische Verknüpfung wird ignoriert.
Die Datei wird **vor** Aktivierung verbraucht: Auch nach einem fehlgeschlagenen
Start ist die Aufzeichnung beim folgenden Recovery-Start aus.
Sollte bereits ein anderer Verbraucher Tracing aktiviert haben, benutzt die
Diagnose dessen vorhandene Tiefe und beendet dessen Tracer nicht.

Messpunkte ergänzen `traced_bytes`, `traced_peak_bytes` und
`tracing_overhead_bytes`. Höchstens acht Snapshots protokollieren jeweils die
20 größten Python-Allokationsgruppen samt gekürzten Dateipfaden und Zeilennummern.
Nach dem ersten Snapshot werden weitere frühestens nach zehn Sekunden erfasst,
wenn RSS um mindestens 64 MiB gestiegen ist oder die Beobachtung endet.
Snapshottabellen beschreiben aktuell noch vorhandene Allokationen; sie sind keine
Differenzmessung zu einem vollständigen HA-Boot. Roh-Snapshots werden nicht
gespeichert. Nach 120 Sekunden beziehungsweise beim Entladen stoppt ein von der
Diagnose gestarteter Tracer.

Tracing benötigt selbst CPU und Speicher und ist deshalb standardmäßig aus.
Es erfasst Python-Allokationen **ab** Einrichtung des Battery Managers, weder
frühere Allokationen noch jeden nativen Speicherverbrauch. Ein Anstieg des RSS
ohne entsprechend viele erfasste Python-Bytes erfordert eine andere Messung.
Profiler-Aktionen, die erst nach vollständigem HA-Start ausgeführt werden,
erfassen einen früheren Startabbruch nicht rückwirkend.

## Externe Aufzeichnung über einen Core-Neustart hinweg

HACS liefert `custom_components/battery_manager/tools/capture_ha_memory.sh` mit.
Es wird außerhalb von HA
Core ausgeführt: in einem dauerhaft laufenden Terminal-/SSH-Add-on oder in der
HAOS-Host-Shell. Es benötigt `ha`, `timeout`, `date`, `sleep` und `sed`, aber weder
Python noch einen API-Token in der Befehlszeile. Das Skript benutzt ausschließlich
lesende Supervisor-CLI-Kommandos. Es führt selbst keinen Neustart aus.

Vor dem nächsten geplanten Core-Neustart starten; das Terminal offen lassen:

```sh
mkdir -p /config/battery_manager
sh /config/custom_components/battery_manager/tools/capture_ha_memory.sh \
  /config/battery_manager/memory-start-01.log 90 2
```

Damit entstehen 90 Messungen mit angestrebtem Zwei-Sekunden-Abstand, also unter
normalen Bedingungen ungefähr drei Minuten Beobachtung. Ein langsamer/fehlender
Supervisor kann den Abstand verlängern; jeder Abschnitt hat seinen tatsächlichen
UTC-Zeitstempel. Jeder CLI-Aufruf ist auf drei Sekunden begrenzt. Fehlgeschlagene
Core-Abfragen stehen als `request_failed` im Protokoll; die Aufzeichnung läuft
weiter und erfasst auch einen wieder erreichbaren Core.

Erfasst werden Core-Info, Core- und Supervisor-Statistik sowie ausgewählte
procfs-Zähler. Optional weitere Add-on-Slugs als zusätzliche Argumente angeben:

```sh
sh /config/custom_components/battery_manager/tools/capture_ha_memory.sh \
  /config/battery_manager/memory-start-02.log 90 2 core_example
```

`core_example` durch einen tatsächlich installierten Slug ersetzen. Add-ons
können mit `ha addons list` ermittelt werden. Zahlen in den API-JSON-Statistiken
sind Bytes, procfs-Zahlen im externen Log KiB. Der Core-Containerwert entspricht
nicht exakt dem RSS seines Python-Prozesses. Das externe procfs spiegelt den
Namensraum des aufzeichnenden Terminals wider. Ein VM-Neustart beendet auch diesen
Recorder; die bis dahin geschriebene Datei bleibt auf persistentem Speicher.

Ausgabe wird mit Dateirechten 0600 neu angelegt. Bestehende Dateien und
Symlink-Ziele werden nicht überschrieben. Ausgabe außerhalb von `/config/www/`
belassen. Das Log enthält technische Systemdaten, Add-on-Slugs, Dateipfade und
Zeitstempel; die übrigen HA-Logs können zusätzlich Verbrauchs-/Gerätedaten und
andere private Angaben enthalten. Vor öffentlichem Teilen prüfen.

## Auswertung und Nachweis einer Korrektur

1. Anhand von UTC-Zeit/PID externe Messung, `Startup memory` und Host-OOM-Zeilen
   zusammenführen. Prüfen, ob Speicher bereits vor dem neuen Core knapp war.
2. Den RSS-Anstieg einem Startabschnitt zuordnen; bei zugeschaltetem Tracing
   die größten Allokationsgruppen und deren Stackframes untersuchen.
3. Den verdächtigen Pfad mit repräsentativen gespeicherten Daten lokal messen.
   Ein Vergleich auf einer VM-Kopie erfolgt isoliert von produktiven Aktoren.
4. Vergleichsläufe mit gleichen Versionen, Daten, VM-Ressourcen und Add-ons
   durchführen. Ein einzelner erfolgreicher Lauf beweist keine Behebung.
5. Erst eine reproduzierbar kleinere Speicherspitze bei gleicher Fachfunktion
   begründet eine Korrektur. Zusätzlicher VM-Speicher allein identifiziert keinen
   verursachenden Codepfad.

Offizielle Schnittstellen:
[HAOS-CLI](https://www.home-assistant.io/common-tasks/os/),
[Supervisor-Statistiken und Boot-Logs](https://developers.home-assistant.io/docs/api/supervisor/endpoints/),
[Python tracemalloc](https://docs.python.org/3/library/tracemalloc.html),
[HA Profiler](https://www.home-assistant.io/integrations/profiler/).

Nachweise: `tests/ha/test_startup_diagnostics.py` prüft Ressourcen-/Snapshotgrenzen,
Trace-Besitz, Fehler, Abbruch, Draining und Fristen mit virtueller Zeit;
`tests/ha/test_memory_capture_script.py` simuliert Ausfall/Wiederkehr von Core,
Timeoutverträge, Dateischutz und vollständig gemockte Produktionswartezeiten.

Lokale Abnahme des Stands 0.56.1: 2740 Python-/HA-Tests, 97,94 % Gesamt-Coverage,
alle 66 Modul-Gates (HA mindestens 95 %, Core 100 %), Ruff und mypy sowie
80 Frontend- und 41 Browsertests bestanden; Bundle-Prüfung bestanden. Eine lokale
Messung von 300 passiven Samples ergab 0,14 ms Median und 0,20 ms P95 einschließlich
Executor-Dispatch/procfs/JSON, ohne Log-Datei-I/O. Die 96 gehaltenen Samples belegten
als JSON etwa 31 KiB. Ein separater Smoke-Test mit echtem `tracemalloc` ordnete
eine kontrollierte 2-MiB-Allokation ihrer Codezeile zu, verbrauchte den Marker und
beendete den eigenen Tracer. Diese Messung belegt die Funktion der Diagnose;
die Zuordnung des produktiven OOM benötigt einen aufgezeichneten HA-Start.
