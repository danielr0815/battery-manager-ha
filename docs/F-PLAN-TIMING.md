# Zeitliche Freigaben und Teilstunden (0.39.0)

## Vertrag

`SurplusLoadState.not_before` ist die früheste bekannte Freigabe für einen
Laststart. Die Zeit verwendet denselben festen lokalen UTC-Offset wie die
PlanInputs. `None` bedeutet keine bekannte zeitliche Sperre und ist keine
Bestätigung, dass Hardware schaltbereit ist.

Normale steuerbare Lasten verwenden ihre bestätigte letzte Schaltung und die
konfigurierte Mindestpause. Bereits laufende Lasten bekommen keine neue
Startpause. Zulässige F8-Fortsetzungen dürfen in der Prognose weiterhin sofort
anschließen; die lesende Prüfung verbraucht kein Fortsetzungs-Kontingent.

Kaskaden bestimmen die Frist aus den OFF-Zeitstempeln ihrer tatsächlichen
Versorgungsstrecke: Root, vorgelagerte Ausgänge, eigenes Lade-Gate bzw.
Endlast-Aktor. Die Liste der Fristen ist für Planner und Executor identisch.
Der späteste noch sperrende Aktor bestimmt die Freigabe. Bereits aktive Aktoren
sind kein neuer Start und verlängern die Pause nicht.

## Gemeinsame Zeitachse

`build_slots` teilt eine Stunde ausschließlich an bekannten Freigabezeitpunkten.
09:00–10:00 mit Freigabe 09:45 wird zu 09:00–09:45 und 09:45–10:00. PV, AC, DC
und verfügbare P10/P90-Bänder werden proportional verteilt. Laufende Appliance-
Profile werden anschließend anhand der echten Zeitdauern eingezeichnet.
Verbrauchsunsicherheit für den SOC-Puffer bleibt der ursprünglichen Stunde
zugeordnet; die Länge seines kritischen Fensters zählt tatsächliche Stunden.

Der erste Teil darf für die gesperrte Last keine Buchung enthalten. Der zweite
kann ohne Lücke an einen Lauf um 10:00 anschließen; Mindestlaufzeit und die
üblichen Energie-, Reserve- und Netzbezugsprüfungen gelten weiter. Andere
Lasten dürfen das vorherige Zeitfenster nutzen. Der versionierte Planexport
enthält sowohl Freigabe als auch die daraus entstandenen Slots, sodass Replay
und Anzeige dieselben Zeitgrenzen verwenden.

## Ausführung

Der Coordinator reserviert einen einzelnen Callback für die nächste zukünftige
Slot-, Laufende- oder Kaskaden-Segmentgrenze. Bei jeder Neuplanung wird der alte
Callback ersetzt. Der Callback fordert nur einen frischen Plan an; aktuelle
Sicherheits-, Ownership- und Bestätigungsprüfungen bleiben maßgeblich. Cleanup
und Shutdown entfernen den Callback. Der bestehende Timer für eingefrorene
Laufende-Fristen bleibt für den verbindlich begonnenen Lauf zuständig.

Bei Aux entscheidet nur ein Segment mit Startoffset null über einen sofortigen
Start. Ein späteres Segment wird einschließlich seiner modellierten
Übergangszeit erst an seiner Grenze geprüft. Das gilt auch nach Neustart und
für die Übernahme eines bereits ausgeschalteten Shared-Aktors.

## Grenzen und Abnahme

Die Änderung bildet bekannte Mindestpausen und bereits geplante Startoffsets
ab. Sie sucht nicht eigenständig alle denkbaren Startminuten durch. Diese
Alternativenprüfung gehört zum verbleibenden Punkt 5. Eine fehlende zukünftige
Gerätebestätigung kann keine garantierte Startzeit liefern: das konservative
Kaskaden-Startbudget und die vorhandenen Wake-/Stabilitätsdiagnosen bleiben
maßgeblich. Seit 0.41.0 ergänzt [F-EXECUTION-PROJECTION.md](F-EXECUTION-PROJECTION.md)
verbleibende normale Mindestlaufzeiten und Stabilitätsgrenzen. Wake-/Proof- und
Neustartphasen werden mit tatsächlichen Prüffristen getrennt ausgewiesen;
eine unbestätigte Hardware-Reaktion bleibt ausdrücklich bedingt.

Regressionen prüfen 09:00/09:15/09:30/09:45, Energieerhaltung, Stundenanschluss,
Vorentlade-/Recovery-Sperren, Aux-Startgrenzen, gemeinsame Pausenfristen,
Timer-Ersetzung und Shutdown. Produktive Wartezeiten werden nicht real
abgewartet. Live-Abnahme nach Installation steht aus.
