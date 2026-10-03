# Aktuelle Verträge ab 0.46.0

Dieses Dokument ist der Einstieg für das aktuelle Verhalten. Die `F-*.md` und
ältere Versionsanalysen dokumentieren die Entscheidungs- und Änderungshistorie.
Bei Widersprüchen zu früheren Beschreibungen gelten die hier genannten Verträge.
Die [Architektur](ARCHITECTURE.md) ordnet sie dem Code zu; die
[Testmatrix](TEST_MATRIX.md) benennt die ausführbaren Nachweise.

## Installation und Migration

Home Assistant **2026.8.0** oder neuer ist erforderlich. Manifest und
Projektmetadaten tragen gemeinsam **0.54.1**. Entity-IDs, Subentries, Services,
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

## DC-Vorrang und lastabhängige AC-Vorbereitung (0.49.0)

Die [Lastpriorisierung](F-RESERVE-LOAD-PRIORITY.md) ersetzt die rein späteste
AC-Vorbereitung. DC-Verbrauch hat zeitübergreifend Vorrang: Nominale PV darf
zukünftigen DC-Bedarf decken, die optimistische obere PV-Prognose begründet
hingegen nur Speicherplatzbedarf. AC nutzt nur den verbleibenden Energieraum.
Höhere nutzbare AC-Restlast wird vor niedriger Last gewählt; bei Gleichstand die
spätere Gelegenheit. Entscheidend sind W und die PV-Deadline, keine festen
Uhrzeiten. Dunkle Zeiträume können weiterhin AC-Abgabe benötigen, wenn die
stärkeren Lastfenster erst nach dem relevanten PV-Überschuss liegen.

Eine wirtschaftliche Netzteilhaltung darf keine spätere zusätzliche AC-Abgabe
finanzieren. Die DC-Obergrenze enthält deshalb keinen angehobenen Zielpfad aus
einer maximalen AC-Referenz. Schutz, manuelle Quellen, heutiger/morgiger lokaler
Horizont und physische Grenzen bleiben erhalten. Die Quellenwahl ist weiterhin
prognoseabhängig; ein späterer Forecastwechsel kann frühere Entscheidungen ändern.

## Schnelle AC-Freigabe bei gemessenem Bedarf (0.50.0)

Aktive Reserve mit koordiniertem Inverter nutzt alle fünf Sekunden die aktuellen
Leistungsmessungen. Eine bisher für später vorgesehene AC-Gelegenheit darf
vorgezogen werden, wenn gespeicherte Energie oberhalb der DC-/Reservegrenze und
des SOC-Puffers vorhanden ist. Der Core liefert diese Grenze; der schnelle Pfad
startet keine Optimierung und schaltet keine Netzteile um.

Ab 100 W gemessener AC-Restlast wird freigegeben; unter 50 W beginnt eine
zehnminütige Ausschaltverzögerung. Erneuter Bedarf ab 50 W setzt die Frist zurück.
Die direkte Bilanz aus saldierter Netzleistung und AC-Ein-/Ausgang des Inverters
bleibt auch bei erfolgreich auf null geregeltem Netzbezug aussagekräftig und
enthält keinen DC-Verbrauch. Alternativ werden frische Hauslast und PV verwendet,
optional ergänzt um Netzbezug. W/kW werden normalisiert.

SOC, die verwendeten Leistungsmessungen und Netzstatus dürfen höchstens 30 Sekunden alt sein. Planbudget
höchstens fünf Minuten und nicht über den aktuellen Quellslot hinaus. Schutz,
manuelle oder geplante DC-Versorgung, unbekannte Quellen und erschöpftes Budget
übergehen jede Haltefrist. Fehlende Live-Leistungsquellen lassen den normalen
Planner weiterarbeiten. Keine wiederhergestellte Freigabe nach Neustart; neue
Messungen und ein neuer Plan sind nötig. Details und Tests:
[Gemessener AC-Bedarf](F-LIVE-AC-DEMAND.md).


## Binäre Inverterfreigabe (0.52.0)

`maxdischargepower` erhält nur 0 W oder `inverter_max_power_w`. Die
Reserveplanung budgetiert vollständige Fünf-Minuten-ON-Schritte anhand der
prognostizierten Last samt Standby; kleine Restbudgets bleiben ungenutzt.
Der Live-Pfad verlangt Energie für volle Leistung während seines
35-Sekunden-Sicherheitsfensters, sonst sperrt er sofort. Bereits kommandierte
Planfreigaben werden ebenfalls alle fünf Sekunden anhand des Headrooms,
SOC-Hysterese und desselben Sicherheitsfensters überwacht. Frischer SOC,
bestätigte Quellen und frischer verfügbarer Netzstatus sind dafür erforderlich;
optionale AC-Leistungsmesser bleiben für die Planfreigabe entbehrlich. Ein
abgelaufener Plan berechtigt nicht zur weiteren Freigabe. Volle Freigabe
bedeutet weiterhin bedarfsgerechte ESS-Abgabe, keine erzwungene Einspeisung.
Energiebilanzen, mögliche zusätzliche Import-/Export-Restmengen und Tests:
[Binäre Inverterfreigabe](F-BINARY-INVERTER.md).


## Marktorientierte AC-Priorität und gemeinsamer Horizont (0.53.0)

Die Reserveplanung betrachtet jetzt den gesamten verfügbaren Prognosehorizont;
frühere Heute/Morgen-Bindungen in diesem Dokument sind ersetzt. Optionale EPEX-
Intervalle bevorzugen bis zu vier teure Tagesstunden innerhalb des bestehenden
AC-Budgets. Fehlende Preise verwenden Lastpriorität. Ein Vergleich mit derselben
Prognose ohne AC-Abgabe begrenzt zusätzliche spätere DC-Netzversorgung; nötigenfalls
wird AC-Energie zurückgehalten oder die Freigabe verworfen. DC-Vorrang, physische
Simulation, binäre Freigabe und Quellenbesitz bleiben verbindlich.

Der Live-Pfad schützt spätere Marktfenster, darf aber bereits geplante AC-Energie
vorziehen, wenn eine unerwartete aktuelle Last unter Preisgewichtung deutlich
besser ist. Das Budget reicht nur bis zur nächsten Netto-PV-Ladung und bleibt
oberhalb der DC-/Schutzgrenze. Verhalten, Gewichtung, Datenformate, Diagnose und
Nachweise: [Marktorientierter Inverterbetrieb](F-MARKET-AC-PRIORITY.md).

## Ergänzungen aus dem Oktoberreview (0.54.0)

Automatische Mehrlast, Geräteberatung, Kaskaden-Recovery und Einspeisung dürfen
keinen zusätzlichen DC-Ausfall erzeugen. Der Vergleich verwendet physische
Zeitintervalle gegen Ausgangs- und zuletzt akzeptierten Plan. Bereits vorhandene
Defizite bleiben sichtbar; frühere Verbesserungen kompensieren keine späteren
Ausfälle. Der Import-Slack gewährt keine DC-Ausfalltoleranz. Nominaler Pass 3
behält seinen bestehenden Unsicherheitsvertrag.

G4 zählt auch tatsächlich eingeschaltete Ladepfade, deren neue Buchung verworfen
wurde. Bekannte Leistung wird in W normalisiert; bei fehlender Messung gilt für
einen gehaltenen Ladepfad die gelernte beziehungsweise konfigurierte Leistung.
Bei fehlender Inverterfreigabe und unzureichender PV überschreibt der Schutz die
Restmindestlaufzeit. Kaskaden und explizite Kalibrierung behalten ihren Besitz.

Power-Lernen nutzt echte HA-Publikationen mit höchstens 600 Sekunden Alter.
Gleiche neu publizierte Werte bleiben frisch. Zähler, Programme und Zustände
bekommen kein pauschales Power-Ablaufdatum. Quellenwechsel entwerten den
betroffenen Messkanal; ein anderer valider Kanal bleibt verwendbar. Ohne
verwertbare Messung wird kein Energieprofil gelernt.

Historische Wh werden durch die tatsächlich beobachteten Stunden geteilt.
Der wiederholte Herbstbin benötigt beide Stunden; der Frühlingsbin bleibt
unbekannt. Alte Herbstwerte ohne Dauerbeleg werden gezielt neu gelernt.
Fensterbelegung, Messabdeckung seit Quellenbindung, Ausschlüsse, Binreife und
zeitgewichteter gelernter Prognoseanteil haben getrennte Nenner. Ein unbekanntes
Quantilband wird diagnostisch von gemessener Streuung null unterschieden; die
bestehende Pufferpolitik bleibt erhalten.

Fehlt ein Preis für eine relevante AC-Gelegenheit, verwendet der gesamte
Vergleichshorizont einschließlich Live-Vorziehen Lastpriorität. Automatische
EPEX-Erkennung verlangt eine valide Intervallserie; explizite Quellenbindung
hat Vorrang. Diagnose und Bedienung: [Dashboard](F-DASHBOARD-DIAGNOSTICS.md).
Archivformat und Forschung: [Oktoberumsetzung](F-OCTOBER-OPTIMIZATIONS.md).


## Plananzeige und Berechnung ab 0.54.1

Bei `unknown` oder `unavailable` entfernt HA die benutzerdefinierten
Sensorattribute. Bereits geöffnete Prognose-, Verbrauchs-, Kaskaden- und
Lastkarten behalten den zuletzt empfangenen Plan deshalb lokal im Arbeitsspeicher.
Sie zeigen ihn als veraltet mit Aufnahmezeit an. Ein frischer Plan ersetzt ihn;
ein Entitätswechsel, eine entfernte Entität oder eine verfügbare leere Publikation
verwirft ihn. Ein neuer Browseraufruf ohne empfangenen Plan erfindet keine Historie.
Der Cache ist ausschließlich Darstellung und erteilt keine Aktorfreigabe.

Zeitliche Slotgrenzen, SOC-Toleranzen und unabhängige Schutzprüfungen gelten
weiter. Eine verworfene Berechnung kann den Sensor weiter auf `unavailable`
setzen; die erhaltene Darstellung hebt dessen Zustand nicht auf. Quellenalter
werden gegen die tatsächliche Ergebnisveröffentlichung gemessen, während
`plan_metadata.captured_at` weiterhin die Aufnahme der Planeingaben bezeichnet.

Innerhalb eines Planner-Aufrufs werden die unveränderten Fünf-Minuten-Slots und
Preisgewichte für Lastkandidaten wiederverwendet. Die Cache-Schlüssel halten die
unveränderlichen Originalobjekte fest; neue Slots, neue Preisintervalle und die
beiden Herbststunden bleiben getrennt. Die kohärente Preisabdeckung wird je
Kandidat anhand seiner relevanten Lasten geprüft. Summationsreihenfolge,
Optimierungsziele und DC-Schutz bleiben unverändert. Die Caches enden mit dem
Planner-Aufruf; Zeitmessungen sind Beobachtungen, kein Hardware-unabhängiges Gate.

AC-Off-Referenz und Rückhalt-Retry eines Lastvorschlags verwenden dieselbe
Vorbereitungshülle; AC-Freigabe und Rückhalt-Marge werden weiterhin getrennt in
der Vorwärtssimulation angewendet. Bis zu 128 Hüllen und 4.096 unveränderliche
Physikschritte werden je Planner-Aufruf gehalten. Physik-Schlüssel umfassen
Konfiguration, Slotidentität, SOC, Schwelle, Zusatzlast, beide Netzteilzustände,
PV-Skalierung, Feed-in und Inverterlimit. Ein Eintrag hält seine Identitäten
fest, bis er verdrängt wird. Reserve-Diagnosefelder werden je Kandidat ergänzt;
sie stammen nicht aus einem anderen Kandidaten. Jeder Physikschritt prüft den
Abbruch vor dem Cachezugriff. Gleiche angrenzende Schaltzustände werden erst als
fertige Intervalle materialisiert; Zeitlücken und reale DST-Grenzen bleiben
erhalten.
