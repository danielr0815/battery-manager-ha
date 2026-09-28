# Aktuelle Verträge ab 0.46.0

Dieses Dokument ist der Einstieg für das aktuelle Verhalten. Die `F-*.md` und
ältere Versionsanalysen dokumentieren die Entscheidungs- und Änderungshistorie.
Bei Widersprüchen zu früheren Beschreibungen gelten die hier genannten Verträge.
Die [Architektur](ARCHITECTURE.md) ordnet sie dem Code zu; die
[Testmatrix](TEST_MATRIX.md) benennt die ausführbaren Nachweise.

## Installation und Migration

Home Assistant **2026.8.0** oder neuer ist erforderlich. Manifest und
Projektmetadaten tragen gemeinsam **0.48.0**. Entity-IDs, Subentries, Services,
Konfiguration und die bestehende Karten-URL bleiben erhalten. Python benötigt
weiterhin keine zusätzlichen Laufzeitpakete. Node-Werkzeuge sind reine
Entwicklungsabhängigkeiten; ausgeliefert wird eine eingecheckte Bundle-Datei.

Vor dem Update gilt der normale HA-Backup-Workflow. Beim nächsten Laden wird ein
Betriebsarchiv in Schema 1 als einzelnes Segment übernommen. Schema 2 enthält
`schema_version: 2` und eine geordnete `segments`-Liste. Ein Segment enthält die
bisherigen Journalfelder sowie `segment_id` und seine eigene `timezone`.
Ein Downgrade kann das neue Archivformat nicht lesen; dafür das vorherige Backup
verwenden. Planneraufzeichnungen behalten ihr bisheriges JSON-Format.

## Lasten pausieren und schalten

Der vorhandene BM-Kontrollschalter einer Last sperrt beim Ausschalten neue Starts
sofort. Ein laufender Verbraucher erhält seine verbleibende Mindestlaufzeit;
danach wird der Ladepfad geordnet abgeschaltet. Das gilt auch ohne gültige
PV-Prognose. Wiederaktivieren hebt einen noch wartenden manuellen Pausenauftrag
auf. Schutzabschaltungen dürfen die Mindestlaufzeit weiterhin übergehen.

Ein Serviceaufruf ist eine Anforderung. Erst der gemeldete Gerätezustand bestätigt
ON oder OFF. Normale Lasten warten dafür höchstens **30 Sekunden**. Dwell und
Laufzeitgrenze beginnen mit der Bestätigung. Vor ON und nach verspäteter
Bestätigung werden Freigabe und Schutzstatus erneut geprüft. Ein wartendes Gerät
hält die unabhängige Abschaltung anderer Lasten nicht auf.

Fehlende Bestätigungen stehen in der Ausführungsdiagnose. Unbestätigte Befehle und
der Sicherheitsmodus werden getrennt gespeichert. Wiederholungen erfolgen
frühestens nach **60 Sekunden** und bei bekannter Gegenstellung; `unknown` und
`unavailable` rechtfertigen keine blinden Wiederholungen. Ein bereits bestätigtes
OFF wird nicht erneut gesendet. Kaskaden bleiben alleinige Eigentümer ihrer
Aktorpfade; Gate-Reihenfolge und konfigurierte Eingang-Abschaltpolitik gelten
weiterhin. Die detaillierten Schutzregeln stehen in [LOAD_CONTROL](LOAD_CONTROL.md).

## Planung und Lernen

Der maximale Charger-Durchsatz umfasst DC-Versorgung, Eigenbedarf und
Batterieladung gemeinsam. Physisch nicht lieferbare DC-Energie bleibt als
unversorgt sichtbar und fließt in die Supportbewertung ein. Appliance-Starts
brauchen Prognosen für ihre vollständige Laufdauer. Hypothetische Läufe behalten
Reserve-, Support- und Kaskadenbedingungen des Ausgangsplans.

Alle Kerneingaben müssen endliche physikalische Werte, positive Slotdauer,
eindeutige Last-/Appliance-IDs und chronologisch geordnete Slots besitzen.
Absichtlich tolerierte Altparameter, etwa die dokumentierte Behandlung eines
invertierten SOC-Min/Max-Fensters, bleiben kompatibel. Veröffentlichte
Ergebnisabbildungen sind defensiv kopiert und unveränderlich.

Lernen arbeitet auf einer Kopie. Quellenbindung, Profile und Erfolgszeitpunkt
werden erst nach erfolgreichem Abschluss gemeinsam übernommen. Ein Recorderfehler
oder Abbruch beschädigt den letzten gültigen Stand nicht. Beschädigte,
rekonstruierbare Lerndaten werden verworfen und neu gelernt. Historische
Leistungen berücksichtigen W/kW wie Livewerte; unbekannte Zustandsintervalle
notwendiger Bereinigungsquellen liefern keine scheinbar gültigen Nulllasten.

## Archive, Zeit und Bedienung

Ein HA-Zeitzonenwechsel beginnt ein neues Archivsegment. Ältere Tagesberichte
behalten ihre ursprüngliche Zeitzone; Messintervalle überbrücken den Wechsel
nicht. Ereignis- und Größenbudgets gelten gemeinsam über die Segmente. Offline-
Replay prüft jedes Segment in seiner eigenen Zeitzone. Vergleiche verbinden
nur passende Segmentidentitäten und Zeitzonen. Recorder und Offline-Evaluator
verwenden dieselbe physische Intervallbilanz für Root-, Aux- und normale Lasten.

Die fünf Karten verwenden die HA-Zeitzone für sichtbare Zeiten, Hover und
zugängliche Labels. Englisch ist die Rückfallsprache. Bei Aktualisierung bleiben
aufgeklappte Berichte, Fokus und Scrollposition erhalten. Diagramme unterstützen
Tastaturbedienung; Sommerzeitwechsel werden im echten Browser geprüft.

## Konfiguration und Services

Das Entfernen des optionalen Reserve-Netzsensors speichert ausdrücklich `None`;
ein Reload übernimmt dadurch keinen alten Optionswert. Services werden einmal in
`async_setup` registriert. Ein Aufruf braucht eine gültige geladene Entry-ID;
fehlende oder entladene Entries liefern einen verständlichen Servicefehler.
`ConfigEntry.runtime_data` enthält den typisierten Coordinator. Parameter und
Beispiele der bestehenden Services stehen in README und `services.yaml`.

## Initialisierung und Planungsdauer ab 0.46.1

Die erste Planung läuft nach Einrichtung der Entities im Hintergrund. Bis zum
validen Ergebnis bleiben Prognose-Entities unverfügbar. Aktualisierungen laufen
pro Coordinator seriell; Entladen stoppt auch die CPU-Berechnung kooperativ.
SOC-Schutz und Quellenrückfall werden während einer Rechnung unabhängig geprüft.
Veraltete SOC- oder Zeitabschnitt-Ergebnisse dürfen keine Aktoren freigeben.

Die Diagnose enthält laufende Phase und gemessene Phasenlaufzeiten unter
`planning`. Wiederverwendung validierter Konfigurationen und unveränderlicher
Teilintervalle reduziert Rechenarbeit ohne Lockerung fachlicher Gates. Details,
Messwerkzeug und Nachweise: [Planungsdauer](F-PLANNING-LATENCY.md).

## Haushaltsgeräte ab 0.47.0

Gerätebeobachtung und Profile bleiben ohne wirtschaftlichen Plan verfügbar.
Acht Geräte-Sensoren und die Haushaltsgerätekarte trennen Messung, Schätzung,
Konfiguration und gelernte Werte. Begrenzte Zusatzmetadaten erklären die letzten
20 angenommenen/verworfenen Beobachtungen; bestehende Profile behalten ihre
Werte und fehlende historische Zeitstempel bleiben unbekannt. Kein automatischer
Gerätestart und keine zusätzlichen Optimizer-Simulationen. Der vollständige
aktuelle Vertrag steht in [APPLIANCE_VISIBILITY](APPLIANCE_VISIBILITY.md).

## Reservebetrieb ab 0.47.1

Aktive Reserve erhält vorhandene Batterieenergie auch für DC: Verfügbare Netzteile
übernehmen, wenn diese Entnahme keinen benötigten PV-Speicherraum schafft. Der
bindende Horizont bleibt heute und morgen in HA-Ortszeit. Natürlicher DC-Verbrauch
schafft nötigen Platz vor zusätzlicher AC-Entladung; diese erfolgt möglichst spät.
Übermorgen löst keine heutige Vorbereitung aus. Netzteilbetrieb darf keine
nutzbare PV verdrängen, unbekannte 48-V-Leistung wird nicht als gesichert gerechnet.
Es gibt kein festes Nachtziel und keine gezielte Netzladung.

Schutzschwellen, manuelle Anforderungen, Netzverfügbarkeit und bestätigter
24-V-Rückfall bleiben verbindlich. Wirtschaftliches Halten ist keine Schutz-Latch;
eine geänderte Prognose kann wieder freigeben. Historische SOC-Haltewerte bleiben
reine Diagnose. Reserveanzeige und Aktordiagnose erklären wirtschaftliche Haltung
und eine fehlende 24-V-Freigabe. Aktueller Vertrag und Golden-Diffs:
[SOC-Erhaltung](F-RESERVE-SOC-PRESERVATION.md). Die physische Energieabbildung und
Kalenderhorizonte aus [DC bevorzugen](F-RESERVE-DC-FIRST.md) bleiben erhalten.

## Geplante Schaltzeiten in der SOC-Karte (0.48.0)

`switching_schedule` am SOC-Prognosesensor enthält zusammenhängende Intervalle
`[start, end)` mit `inverter_on`, `dc24_on`, `dc48_on`. Koordinierte Versorgung
und aktive Reserve erhalten die Entscheidungen der kleinen Simulationsschritte;
Stundenflags (`all`/`any`) oder Energiefluss ersetzen diese Zeitdaten nicht.
Unveränderte Zustände werden über Stundengrenzen zusammengefasst. Der klassische
Planner liefert seine vorhandene Slotauflösung. Das Attribut ist unaufgezeichnet.

Inverter standardmäßig sichtbar, Netzteile per Checkbox oder Kartenoption
`show_power_supplies` zuschaltbar. Die aufgeklappte Liste und der Diagrammcursor
zeigen geplante Ein-/Aus-Zeiten in HA-Ortszeit; die Liste benennt den UTC-Offset
bzw. die Zeitzone auch für doppelte Herbststunden. Fehlende Intervalle sind
unbekannt. Zwischen SOC-Messpunkten wird kein zusätzlicher SOC erfunden.
Geräterückmeldungen, manuelle Eingriffe und Freigabesperren können vom Plan
abweichen; die Zeitspur ist kein Betriebsnachweis. Nachweise:
`tests/core/test_switching_schedule.py`, `tests/ha/test_coordinator.py`,
`tests/frontend/switching.test.mjs`, `tests/browser/cards.spec.mjs`.
