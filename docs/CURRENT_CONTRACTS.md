# Aktuelle Verträge ab 0.46.0

Dieses Dokument ist der Einstieg für das aktuelle Verhalten. Die `F-*.md` und
ältere Versionsanalysen dokumentieren die Entscheidungs- und Änderungshistorie.
Bei Widersprüchen zu früheren Beschreibungen gelten die hier genannten Verträge.
Die [Architektur](ARCHITECTURE.md) ordnet sie dem Code zu; die
[Testmatrix](TEST_MATRIX.md) benennt die ausführbaren Nachweise.

## Gemeinsame AC/DC-Marktverteilung ab 0.62.0

Der konservative DC-Vorrang erzeugt weiterhin eine sichere Referenz. Erst nach
Buchung der tatsächlichen Lasten verteilt der finale Plan deren bestehendes
Batterie-Entnahmebudget gemeinsam auf AC und DC. Höhere tatsächlich
veröffentlichte Preise in EUR/MWh haben Vorrang; der begrenzte Preisbonus der
Referenz bestimmt nicht die finale Rangfolge. Eine tägliche Vier- oder
Fünfstundengrenze gibt es dafür nicht. Unbekannte Preise bleiben unbekannt:
solche Schritte behalten ihre Referenzbelegung. Negative Preise sind gültig,
begründen aber weder zusätzliche Entnahme noch gezielte Netzladung.

Ein vollständiger Tausch enthält Empfänger und günstigere ausgleichende
Gelegenheiten. Seine sequenzielle physische Nachsimulation vergleicht ihn mit
derselben Referenz: kein zusätzlicher Gesamt-Netzbezug oder Export über den
vollständigen Horizont, keine höhere Netto-Batterieentnahme innerhalb ihrer
**festen ursprünglichen PV-Ladefenster**, kein niedrigerer Energieinhalt an einer
Fenstergrenze und keine schlechtere DC-Versorgung. Wirkungsgrade, Standby, Reserveuntergrenze, manuelle Quellen,
verfügbare Geräte und bestätigte Netzteilphasen bleiben verbindlich. Eine
Netzteil-/Inverter-Rückspeiseschleife und zusätzliche beiläufige Netzteilladung
sind unzulässig. Lokale Schätzwerte konstruieren nur Vorschläge.

Die finale Verteilung teilt insgesamt höchstens 32 zusätzliche vollständige
Simulationsläufe zwischen nominalen Kandidaten, Unsicherheitsreferenzen und
-kandidaten. Die vollständig gebuchte konservative Referenz behält ihre
bestehende Glättung; gemeinsame Kandidaten werden blockweise konstruiert und
nachsimuliert. Bei gleichem Preisnutzen zählen weniger Schaltflanken, dann
weniger Änderungen gegenüber der Referenz. Es folgt keine zusätzliche gemeinsame
Glättung. Die Suche hält höchstens vier Pläne einschließlich der Referenz.
Diese Grenze gilt für
den gebuchten finalen Plan, nicht erneut je Lastprobe, Kaskadenentwurf oder
Preisintervall.
`search_limit` heißt, dass die begrenzte Suche endet; es beweist keine
Unmöglichkeit. Auch `no_improvement` gilt nur für verglichene Alternativen.
Ohne bestandenen Vergleich bleibt die Referenz erhalten. Der Live-Pfad schafft
kein neues Budget und zieht eine finale gemeinsame Zuteilung nicht ohne neue
Planprüfung vor.

`reserve.market.joint` ergänzt Budget, unvermeidbare DC-Entnahme,
Referenz-/Ist-Vergleich, Status und Ablehnungsgründe. Wirtschaftliche Intervalle
und Tagesstunden bezeichnen die gesamte akzeptierte bekannte Preisbelegung;
Verschiebung und Netzbezugsänderung gegenüber der Referenz sind eigene Werte.
Fehlende ältere Diagnosen werden nicht als null erfunden. Details und
Feldbedeutungen: [Gemeinsame Marktverteilung](F-JOINT-MARKET.md).

## Stabile Netzteilphasen ab 0.61.0

Bestätigte eigene Netzteilflanken binden die wirtschaftliche Quellenverteilung
jeweils 30 reale Minuten. Die Restfrist gehört zum Planner-Eingang und bleibt
über Reloads erhalten. Wiederholte oder unbestätigte Befehle starten sie nicht neu.
Schutz, manuelle Vorgaben, Netzverlust, PV-Versorgung und notwendiger
PV-Speicherraum behalten Vorrang. Eine während der Berechnung neu bestätigte
Phase verlangt vor einem wirtschaftlichen Gegenbefehl einen frischen Plan.

Nach einer Marktverschiebung werden vollständige 24-/48-V-Kombinationen innerhalb
gleich langer Schritte desselben Prognose- und Preisintervalls zu weniger
Flanken zusammengelegt. Die Dauer je Kombination bleibt erhalten. Vollständige
Nachsimulation gegen Marktplan und Referenz schützt Energie, SOC, DC-Versorgung,
AC-Abgabe, Import und Export. Gleichwertige Pläne mit weniger Flanken sind
zulässig; ungültige Glättung behält den vorherigen Plan. Details:
[F-DC-SOURCE-STABILITY](F-DC-SOURCE-STABILITY.md).

## Adaptive 48-V-Zuschaltung ab 0.59.0

Bei koordinierter Versorgung mit konfigurierter Batteriespannung wird normale
wirtschaftliche 48-V-Bereitschaft durch frische Spannung gefiltert. Der Bedarf
bleibt erhalten, auch wenn der Schalter aus ist: Der Fünfsekundenpfad ruft bei
Spannungsänderung denselben Quellenbesitzer auf. Einschalten nach 60 Sekunden
unter/auf der Einschaltgrenze, Ausschalten nach 300 Sekunden oberhalb/auf der
Ausschaltgrenze; dazwischen wird der bestätigte Zustand gehalten. Bestehende
Mindestschaltzeiten gelten zusätzlich. Schutz und ausdrückliche manuelle
Anforderungen dürfen Bereitschaft erzwingen. Fehlende frische Spannung erlaubt
Bereitschaft, aber keine Leistungsgutschrift. Quellenbestätigung, Netzstatus,
Planablauf, Invertersperre und 24-V-Überlappung behalten Vorrang.

Ein begrenzter Lerner beobachtet SOC, Spannung und Batterieleistung nur bei
bestätigt ausgeschaltetem 48-V-Netzteil. Die vorhandene Zuordnung
`feedin_battery_power_entity` wird auch ohne aktivierte Einspeisung verwendet;
positiv bedeutet Laden, negativ Entladen. Ohne diese Messung bleibt Lernen
unverfügbar. Sensorpublikationen müssen höchstens 30 Sekunden alt sein. Stabile
Abschnitte werden nach 60 Sekunden höchstens alle fünf Minuten aufgenommen.
Lade- und Entladestrombereiche bleiben getrennt. Je Spannungsschwelle werden
Beobachtungen im Band ±0,04 V altersgewichtet ausgewertet (Halbwertszeit zwei Tage,
maximal 14 Tage). Mindestens drei Beobachtungen über mindestens 30 Minuten und
höchstens fünf SOC-Prozentpunkte P10–P90-Streuung sind erforderlich. Diese Werte
sind konservative Startparameter, keine empirisch garantierte Konfidenz.

Ein innerhalb von fünf Minuten nicht durch die gemessene Energie erklärbarer
SOC-Sprung verwirft alte Koordinaten als Rekalibrierungsverdacht. Das Modell
ändert weder den BMS-SOC noch die Batteriekapazität. Lernstand, Epoche und
Quellenbindung überleben Neustarts; Stabilitäts- und Schaltentscheidungstimer
beginnen neu. Konfigurationsänderungen an Quellen, Kapazität oder Grenzspannungen
entwerten das Modell. Unter Netzteil-Spannungshaltung findet kein Training statt.

Die nominale Prognose verwendet aktuelle Spannung für das erste kurze Zeitfenster,
danach eine zur Last passende gelernte SOC-Grenze. Gelernte Lieferung bedeutet
höchstens direkte Lastdeckung innerhalb der PSU-Leistungsgrenze, keine zusätzliche
Netzladung. Bei unzureichender Evidenz bestimmt die konfigurierte SOC-Grenze nur
eine gekennzeichnete Bereitschaftsprognose ohne gesicherte Lieferenergie.
Pessimistische Läufe erhalten keine ungemessene Stützenergie. Eingeschaltet,
angefordert und liefernd bleiben getrennte Diagnosen. Die Schaltspur zeigt den
geplanten Schalterzustand, der Quellenbesitzer prüft Livewerte erneut.

Dies ersetzt die pauschale wirtschaftliche Bereitschaft aus
`F-COORDINATED-DC-SUPPORT` R4 und die zukünftige Momentanspannungsprojektion aus R6.
Alte Planneraufzeichnungen ohne `adaptive_voltage_gate` bleiben im früheren
Modell; neue Aufzeichnungen enthalten den unveränderlichen Lern-Snapshot.
Die drei neuen Golden-Szenarien frieren Cold-Start, eine gelernte
75-%-Grenze und ihre Verschiebung auf 78 % ein. Bei identischer Dunkelprognose
steigen erwarteter Netzbezug und erhaltener SOC zusammen: 6,871 kWh / 41,03 %
(ohne gelernte Lieferung), 8,720 kWh / 74,95 % und 8,884 kWh / 77,96 %.
Das ist beabsichtigte direkte DC-Versorgung, keine Netzladung. Alle 16 bisherigen
Szenarien bleiben unverändert.
Nachweise: `test_voltage_gate.py`, `test_gate_learning.py`,
`test_voltage_gate_runtime.py` und die bestehenden Quellen-/Reserveprüfungen.

## Installation und Migration

Home Assistant **2026.8.0** oder neuer ist erforderlich. Manifest und
Projektmetadaten tragen gemeinsam **0.62.0**. Entity-IDs, Subentries, Services,
Konfiguration und die bestehende Karten-URL bleiben erhalten. Python benötigt
weiterhin keine zusätzlichen Laufzeitpakete. Node-Werkzeuge sind reine
Entwicklungsabhängigkeiten; ausgeliefert wird eine eingecheckte Bundle-Datei.

Vor dem Update gilt der normale HA-Backup-Workflow. Beim nächsten Laden wird ein
Betriebsarchiv in Schema 1 als einzelnes Segment übernommen. Schema 2 enthält
`schema_version: 2` und eine geordnete `segments`-Liste. Ein Segment enthält die
bisherigen Journalfelder sowie `segment_id` und seine eigene `timezone`.
Ein Downgrade kann das neue Archivformat nicht lesen; dafür das vorherige Backup
verwenden. Planneraufzeichnungen behalten ihr bisheriges JSON-Format.

## Telemetrie und wirtschaftliche Neuplanung ab 0.55.1

Batterie-SOC, Batterieleistung, Einspeisezähler und Leistungs-/Energiemessungen beratener
Haushaltsgeräte aktualisieren Schutz, Ist-Zähler und Gerätebeobachtung über
einen eigenen entprellten Pfad. Nur eine neue Batterieleistungsmeldung löst
zusätzlich den Einspeisetrim aus. Kleine Standby-Schwankungen und Bestätigungen
eigener Setpoint-Kommandos berechnen keinen neuen Mehrtagesplan.

Ein tatsächlicher Gerätezyklus oder manuell übernommener Einspeise-Setpoint
fordert eine Vollplanung an. PV-Prognosen, Preise, Versorgungszustände und
die Eingaben schaltbarer Lasten bleiben Planungsauslöser. Die regelmäßige
Fünf-Minuten-Aktualisierung und exakte Plangrenzen bleiben erhalten.

Normale SOC-Änderungen und die Abweichung von einer veröffentlichten
SOC-Prognose lösen keine zusätzliche Vollplanung aus. SOC-Ereignisse prüfen
den Schutz ohne auf den Telemetrie-Debounce zu warten. Überschrittene
Versorgungs- und Einspeise-SOC-Schwellen, unbekannter SOC und abgelaufene Slots
fordern weiterhin eine Neuplanung an. Die bestehende SOC-Driftprüfung während
und nach einer noch laufenden Berechnung bleibt unverändert: Ein während der
CPU-Arbeit überholter Entwurf darf keine neue Freigabe auslösen.

Der schnelle Pfad nutzt nur einen erfolgreichen, gültigen Plan im aktuellen
Slot, prüft SOC und Schutzschwellen und zieht seit dessen Eingangsaufnahme
eingespeiste Energie vom verbleibenden Tagesbudget ab. Ungültige Eingaben
rechtfertigen keine automatische Einspeisung. Die bestehende manuelle
Aktorhoheit und Quellen-Cache-Regeln gelten weiter. Schutz und Einspeisetrim
laufen auch während der CPU-Planung; Telemetrie verschiebt keine Planzeitstempel.

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

Ab **0.60.0** bleiben gültige Verbrauchsmessungen auch unterhalb der bisherigen
Mindestmenge als vorläufige P50-Schätzung nutzbar. Ohne Beobachtungen im
Lernfenster bleibt der vorherige Stundenwert erhalten. P80 setzt weiterhin
ausreichend Vergleichstage voraus; ohne verfügbare Bänder gilt der feste
SOC-Puffer. Karte und Diagnose unterscheiden etablierte, vorläufige,
beibehaltene und statische Werte. Ausschlüsse und explizite Reparatur bleiben
wirksam. Details und Upgrade-Vertrag: [F-PROFILE-CONTINUITY](F-PROFILE-CONTINUITY.md).

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

## Quellen, Messqualität und Rechenaufwand ab 0.57.0

Die 24-V-Übergabe bestätigt zuerst die neue Quelle, wartet danach die gesamte
konfigurierte Überlappung und entfernt erst anschließend die alte Quelle. Das
schließt den Rückfall bei ausgefallenem oder unbekanntem Netz ein. Kehrt das Netz
währenddessen zurück, werden die erforderlichen Quellen erneut geprüft.
Verspätete Bestätigungen eigener Befehle aktualisieren den tatsächlichen
Versorgungszustand über den schnellen Pfad. Der AC-SOC-Schutz erhält wirtschaftliche
DC-Vorgaben eines weiterhin gültigen Plans; abgelaufene Vorgaben werden verworfen.

In aktiver Reserve braucht ein neuer wirtschaftlicher Netzteilstart einen Abstand
von einem SOC-Prozentpunkt zur DC-Vorbereitungsgrenze. Bereits laufende Haltung
nutzt die ursprüngliche Grenze. Manuelle Vorgaben, physischer Schutz und die
PV-Rückkehr behalten Vorrang. Der reine Kern verwendet für alte Aufzeichnungen
weiterhin den neutralen Standardwert null.

Ab 0.58.0 gilt dieser Startabstand auch bei jedem späteren Neustart innerhalb
der Prognose, nicht nur im ersten Schritt.

Beide Netzteile können getrennte Messungen für AC-Aufnahme und DC-Abgabe erhalten.
Gemessene Aufnahme wird aus AC entfernt, gemessene Abgabe zu DC ergänzt. Eine
Messlücke oder negative Energie während eingeschaltetem Netzteil ist kein
Nullverbrauch. Nur ein nachweislich vollständig ausgeschaltetes Stundenintervall
kann ohne Netzteilmessung mit null bewertet werden. Ohne neue Messzuordnung bleibt
die bisherige Bereinigung erhalten; nicht auflösbare Netzteilstunden bleiben
ungelernt. Neue Zuordnungen gelten ab ihrer Konfigurationsepoche und erfinden
keine rückwirkenden Messwerte.

Vor Stundenbeginn werden die tatsächlich verwendeten Verbrauchsprognosen mit P50,
verfügbarem P80, Tagesart und Quelle gespeichert. Sieben vergangene und bis zu vier
zukünftige Kalendertage begrenzen den Speicher. Wiederhergestellte Recorderstunden
werden gegen diese ursprünglichen Prognosen nachbewertet. Eine P80-Abdeckung wird
nur für tatsächlich vorhandene P80-Prognosen ermittelt. Vor dem Update fehlende
Originalprognosen lassen sich damit nicht nachträglich rekonstruieren.

Die Diagnose und der Export zeigen den letzten verwertbaren Messzeitpunkt,
Stundenabdeckung und leere Lerntage je AC/DC-Pfad. Der Zeitpunkt eines erfolgreichen
Lernlaufs beweist keine frischen Messungen. Die Verbrauchskarte warnt bei leerem
Vortag oder mindestens 48 Stunden alten beziehungsweise fehlenden Messungen.

Reserveproben teilen die unveränderliche Horizontvorbereitung. Alternative
Quellen werden für PV-Auswahl und tatsächlich gewichtete Marktfenster berechnet;
physische Gegenprüfungen behalten ihre ursprünglichen Energieregeln. Die
Neuplanung beginnt abhängig von der zuletzt gemessenen Planungsdauer früher vor
dem Ablauf der weiterhin fünf Minuten gültigen Freigabe. Bei dauerhaft längerer
Berechnung bleibt die Freigabe abgelaufen; diese Änderung verlängert sie nicht.

## Archive, Zeit und Bedienung

Ab 0.56.2 darf das Betriebsarchiv erst nach abgeschlossener Wiederherstellung
gespeichert werden. Frühe Sensorereignisse und Shutdown-Flushes überschreiben
keine noch ungeladene Historie. Bei Abbruch oder ungültiger Wiederherstellung
bleibt das vorhandene Archiv geschützt; eine fehlende Datei beim Erststart
erlaubt normale Aufzeichnung. `operation_report.persistence_ready` zeigt die
Schreibfreigabe. Laufzeit- und Schutzsteuerung warten dafür nicht auf das Archiv.
Details: [Schreibschutz beim Start](F-ARCHIVE-STARTUP.md).

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
übernehmen, wenn diese Entnahme keinen benötigten PV-Speicherraum schafft. Seit 0.53.0 gilt der
vollständige verfügbare Prognosehorizont. Natürlicher DC-Verbrauch
schafft nötigen Platz vor zusätzlicher AC-Entladung; diese erfolgt möglichst spät.
Alle enthaltenen Tage können Vorbereitung begründen. Netzteilbetrieb darf keine
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
Intervalle bevorzugen in der konservativen Referenz bis zu vier teure
Tagesstunden innerhalb des bestehenden AC-Budgets. Ab 0.62.0 ersetzt die oben
beschriebene gemeinsame AC/DC-Verteilung diese Beschränkung im finalen Plan. Fehlende Preise verwenden Lastpriorität. Ein Vergleich mit derselben
Prognose ohne AC-Abgabe begrenzt zusätzliche spätere DC-Netzversorgung; nötigenfalls
wird in der Referenz AC-Energie zurückgehalten oder die Freigabe verworfen.
DC-Vorrang schützt die Referenz; im finalen Plan ist er keine zusätzliche
wirtschaftliche Rangfolge vor AC. Physische Simulation, binäre Freigabe und
Quellenbesitz bleiben verbindlich.

Bei Plänen ohne finale gemeinsame Zusammenfassung schützt der Live-Pfad spätere
Marktfenster, darf aber bereits geplante AC-Energie vorziehen, wenn eine
unerwartete aktuelle Last unter Preisgewichtung deutlich besser ist. Ein finaler
gemeinsamer Plan ab 0.62.0 erlaubt diesen eigenständigen Marktvorgriff nicht. Das Budget reicht nur bis zur nächsten Netto-PV-Ladung und bleibt
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

Die damalige horizonweite Lastpriorität bei relevanten Preislücken ist ab
0.54.2 durch den unten beschriebenen intervallweisen Rückfall ersetzt. Automatische
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
beiden Herbststunden bleiben getrennt. Die Preisbewertung wird je
Kandidat anhand seiner relevanten Lasten bestimmt. Summationsreihenfolge,
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

## Intervallweiser Preisrückfall ab 0.54.2

Vorhandene Marktpreise behalten ihre Gewichtung, auch wenn für andere relevante
AC-Gelegenheiten Preise fehlen. Nur unbedeckte Intervalle erhalten das neutrale
Gewicht eins und folgen damit der Lastpriorität. Der Vergleich verwendet immer
`nutzbare Restleistung × eigenes Gewicht`, einschließlich Live-Vorziehen; es gibt
keinen paarweisen Wechsel der Vergleichsregel. Teilweise bedeckte Schritte
mitteln die bekannten Gewichte und das neutrale Gewicht anhand der Zeitdauer.
Ein fehlender Preis am dritten Prognosetag entfernt keine heutigen Marktboni.
DC-Vorrang, vorhandenes Energiebudget, PV-Deadlines, Schutz und binäre Freigabe
bleiben verbindlich. Die aufgezeichnete Diagnose unterscheidet vollständige
Marktgewichtung (`weighted`), gemischte Gewichtung (`weighted_partial`), teilweise
vorhandene neutrale Preise (`load_priority_partial`), flache Preise und fehlende
Preise. Vertrag und Nachweise: [Marktpriorität](F-MARKET-AC-PRIORITY.md).

## Erwartete PV und Verbrauchsrückhalt ab 0.55.2

Betreiberauftrag vom 05.10.2026: Vorhandene Batterieenergie nur so weit entnehmen,
wie die normale PV-Prognose Speicherraum verlangt. Diese Regel ersetzt die
frühere Verwendung der vollständigen oberen PV-Prognose als verbindlichen
Vorbereitungspfad in der aktiven Reserve. Ein begrenzter Unsicherheitszuschlag
bleibt erhalten: 25 % der physisch begrenzten oberen Abweichung. Seit 0.55.2
gibt es keine pauschale Kapazitäts- oder Horizontgrenze für diesen Zuschlag;
jeder Prognosetag behält die aus seinem eigenen Band abgeleitete Reserve.
Ein schmales Band ergibt weniger Zuschlag. Fehlende Bänder verwenden
ein Viertel der Abweichung des konfigurierten oberen Ersatzfaktors; bei 1,20
sind das 5 % mehr PV. Diese Planungspolitik behauptet keine statistisch
kalibrierte Ausfallwahrscheinlichkeit. DC-Vorrang und Verbrauchsrückhalt
begrenzen weiterhin die tatsächlich erlaubte optionale Entladung.
Explizite Offline-Vergleiche simulieren vollständige untere/obere Szenarien mit
deren eigener Physik; ihre Ergebnisse erteilen keine Aktorfreigabe. Die
Unsicherheitsprüfungen außerhalb der Reserveplanung bleiben erhalten.

Optionales AC hält zusätzlich zum zukünftigen nominalen DC-Bedarf den
konfigurierten beziehungsweise gelernten Verbrauchspuffer in gespeicherten Wh
zurück. Das ist eine Grenze für optionale Entladung, kein festes Nacht-SOC-Ziel:
Natürlicher DC-Verbrauch, physische Schutzschwellen und vorhandene Netzteile
bleiben unabhängig. Der Puffer verschiebt keine PSU-Schwelle und fordert keine
Netzladung. Ist die Ausgangsenergie bereits knapp, darf DC sie weiter nutzen;
optionales AC erhält dadurch kein Budget. Die planmäßige Schnellüberwachung
erhält den Rückhalt über das begrenzte Headroom-Budget; der zusätzliche
Messwertpfad behält seine bestehende Puffer- und Frischeprüfung.

Seit 0.55.2 ergänzt die aktive HA-Reserve ein weiches Ziel von 85 % SOC am
nominalen Solarhöhepunkt. Eine zweite Hülle mit normaler PV wird mit der
Unsicherheitshülle über die jeweils kleinere Energieobergrenze kombiniert;
die beiden Puffer werden nicht addiert. Nominaler DC-Mindestbedarf plus
Verbrauchsrückhalt kann das Ziel bis zur physischen Ladeobergrenze lockern.
Ohne netto ladende PV entsteht durch das Ziel keine optionale AC-Entladung.
Eine unerreichbare Zielkurve verändert keine physische Lade- oder Exportgrenze.

Die begrenzte DC-Kostenkorrektur begrenzt bei diesem Ziel nach einer
AC-Abgabe das optionale DC-Halten: Statt zusätzlichen Netzbezug zum bloßen
Erhalt freigegebener Energie zu kaufen, wird derselbe Betrag durch natürlichen
DC-Verbrauch eingespart. Danach gilt wieder normales Halten. Physischer
Quellenschutz, Rückhalt vor AC und der abschließende Vergleich von DC-Kosten
und zeitlicher DC-Versorgung bleiben verbindlich. Falls Halten keinen echten
Quellen- oder Schutzengpass löst, wird zusätzlich einmalig die verbleibende
DC-Differenz vor AC zurückgehalten. Es gibt höchstens eine Halte- und eine
AC-Budgetkorrektur; scheitert der Vergleich,
gilt weiterhin die sichere DC-Referenz.

Speicherraum entsteht zuerst durch DC-Bedarf und verfügbare spätere AC-Fenster.
Innerhalb der tatsächlich nötigen AC-Menge gelten die bisherigen Preisgewichte,
Lastprioritäten, PV-Deadlines und binären Schritte. Ein später aktualisierter
normaler Forecast darf neue Vorbereitung begründen. Begrenzter Restexport bei
stärkerer tatsächlicher Sonne oder vollständigen Schaltquanten ist gegenüber
unbegründeter vorsorglicher Entladung zulässig; P90 ist keine Null-Export-Pflicht.

Diagnose: `soft_soc_ceiling_percent: 85`,
`preparation_pv_basis: expected_with_bounded_uncertainty`,
`pv_uncertainty_budget_wh`, `pv_uncertainty_budget_wh_by_day`,
`consumption_buffer_wh` und
`upper_pv_role: bounded_uncertainty_and_diagnostics`. `headroom_wh` und
`unavoidable_export_wh` gehören zum Vorbereitungspfad des tatsächlich
simulierten Forecasts mit begrenztem Zuschlag. Nachweise und Golden-Prüfung:
[Marktpriorität](F-MARKET-AC-PRIORITY.md#erwartete-pv-statt-verbindlicher-p90-vorbereitung-0550).

## PV-Vorrang und DC-Marktverschiebung ab 0.56.0

Automatische Quellenhaltung nutzt PV vorrangig, soweit diese DC physisch deckt.
Teilüberschuss vergrößert kein Entnahmebudget. Die konservative Referenz
verschiebt bereits freigegebene native DC-
Entnahme mit vorhandenen Preisgewichten zwischen PV-Ladungen; ab 0.62.0 folgt
anschließend die gemeinsame AC/DC-Verteilung nach tatsächlichen Preisen. Die
physische Nachsimulation verhindert zusätzliche Entladung, DC-Ausfälle,
Import-/Exportzuwachs; die ältere DC-Referenzverteilung schützt zusätzlich
bestehende AC-Abgabe. Manuelle Quellen, Schutz und
Bestätigungen bleiben vorrangig. Der bestehende Live-Takt kann vollständig
PV-gedeckte automatische Haltung aufheben und stellt bei Verlust der frischen
Freigabe den gültigen Plan wieder her. Vertrag: [DC/PV/Markt](F-DC-PV-MARKET.md).

### Gebündelte Netzteilzeiten ab 0.58.0

Die DC-Verteilung der konservativen Referenz priorisiert höhere vorhandene
Preisgewichte vor der
Importersparnis je Batterie-Wh. Innerhalb eines Preisbands erweitert sie eine
ausgewählte Quellenkombination auf direkt benachbarte Schritte bis zu
30 realen Minuten. Fehlende Alternativen, Restbudget und Preisbandgrenzen
verkürzen die Bündelung. Die vollständige physische Nachsimulation bleibt
verbindlich. Vertrag und Nachweise: [Netzteilblöcke](F-DC-SOURCE-BLOCKS.md).

### Entfallende Netzteilladung bei Marktverschiebung ab 0.57.2

Wenn eine Quellenalternative beiläufige 48-V-Netzteilladung der Referenz
entfallen lässt, belastet deren gespeicherte Energie zusätzlich das vorhandene
DC-Entnahmebudget. Dies gilt bei Quellenvergleich, erster Auswahl und jedem
weiteren marginalen Wechsel. Unveränderte Ladung benötigt keinen Rückhalt.
So kann bereits der einzige Markt-Kandidat eine kleinere zulässige Verschiebung
enthalten. Es entstehen weder zusätzliches Entnahmebudget noch weitere
Simulationsversuche; alle physischen Prüfungen einschließlich End-SOC bleiben
verbindlich. Diagnosewerte zählen weiterhin tatsächliche Netto-DC-Entnahme und
Verschiebung. Vertrag und Regression: [DC/PV/Markt](F-DC-PV-MARKET.md#r2a--entfallende-netzteilladung-0572).

## Quellenbesitz und korrigierte AC-Bilanz ab 0.56.0

PV, Hauslast, Zusatzlasten und beide Netzteile teilen den saldierenden Anschluss.
Nach Hauslast und gebuchten Zusatzlasten deckt PV die tatsächliche AC-Aufnahme
beider Netzteile, danach den Charger und Export. `HourFlows.psu_grid_import_wh`
erfasst nur den verbleibenden Netzteil-Netzbezug; fehlt das optionale Feld in
alten Aufzeichnungen, bleibt deren bisherige Netzteilrechnung lesbar.
Marktbewertung und Reservevergleich verwenden dieselbe Zuordnung.

Normale Planung, Lastkandidaten, Offline-Vergleich und Replay durchlaufen dieselbe
DC-Nachbearbeitung. Dominierte Quellen werden verworfen; weitere Entnahme braucht
positiven marginalen Nutzen. Hohe Preise erweitern weder DC-Budget noch AC-Freigabe.
Ohne frische DC-Leistung gilt Prognose-Watt plus Verbrauchspuffer-Wh / eine feste
Stunde, unabhängig von Slotrest und Stundengrenze. Startreserve bleibt 10 %.

Quellenaufträge tragen Planrevision und Ablaufzeit. Nach Refresh/Sperre werden
aktuelle Ziele erfasst; jede verzögerte Bestätigung und die 24-V-Überlappung können
Entnahmefreigaben widerrufen. Der Quellenbesitzer stellt notwendige Versorgung
wieder her; Batterieschutz wartet nicht auf wirtschaftliche Mindestschaltfristen.
Eine gemeinsame Aktorzuordnung prüft Lasten, Kaskaden und Versorgung in Config
Flow und unmittelbar vor Befehlen. Bestehende Konflikte erzeugen eine Reparatur:
Zusatzlasten bleiben gesperrt, reine Lastaktoren werden geordnet abgeschaltet,
Versorgungsaktoren bleiben ausschließlich beim Quellenbesitzer. Gültige Teile
bleiben nutzbar. Numerische Kern-Eingaben müssen endlich und physisch zulässig
sein; Geräteprogramme brauchen positive Dauer, beendete Restläufe dürfen null sein.

## Gemessener Netzbezug und offene Lastabschaltungen ab 0.56.0

Automatische Zusatzlasten starten nicht und werden ohne Mindestlaufzeit-Warten
gestoppt, wenn der konfigurierte Netzleistungssensor frischen Bezug über 50 W
meldet. Die Messrauschtoleranz ist kein Entnahme- oder Importbudget. Brutto-PV und
eine Inverterempfehlung widerlegen den saldierten Ist-Bezug nicht. Fehlende oder
über 30 Sekunden alte Messungen ersetzen keine Freigabe; die bisherigen
Plan-/SOC-/Stale-Regeln gelten weiter. Der vorhandene Fünfsekundentakt prüft
Lastabschaltungen ohne CPU-Neuplanung, Starts prüfen nach jeder Bestätigung erneut.
`load_grid_guard` diagnostiziert Zustand und gemessene Watt; Empfehlungen werden
bis zur nächsten freigebenden Planung unterdrückt. Kalibrierung, Quellen und
Kaskaden behalten ihren bestehenden Besitzer.

Ein ausgeschalteter Lade-Helfer bestätigt nicht die physische Umsetzung seiner
externen Automation. Ein noch eingeschalteter eigener Eingang wird nach
fehlgeschlagener Abschaltung weiterverfolgt, auch wenn der Helfer bereits aus ist.
Aktorretries bleiben auf 60 Sekunden begrenzt; Fremdeingänge für Passthrough
behalten ihre Besitzregel. Unterstützte Ladezielgrenzen der externen Geräte müssen
in der Automation eingehalten werden. Details: [Laststeuerung](LOAD_CONTROL.md).


## Fossibot-Stillstand und Freeze-Wächter (ab 0.59.1)

Unveränderter SOC und unveränderte Leistung unterhalb der bestehenden
lastspezifischen Standby-Schwelle sind kein Nachweis eingefrorener Ladetelemetrie.
Der F4-Wächter verwirft dafür seine Evidenz und hebt auch bereits persistierte
F4-Fehlsperren auf, sobald beide Messwerte lesbar sind. Bei bestätigt ausgeschalteter
externer Steckdose dürfen beide Fossibot-Sensoren `unavailable` sein: Dann reicht
die gespeicherte Standby-Leistung als Nachweis der alten F4-Fehlsperre. Ein
unerreichbarer Steckdosenschalter ist keine OFF-Bestätigung. Fehlende Messwerte
allein oder gespeicherte positive Ladeleistung geben keine Sperre frei.
G2-SOC-Sperren bleiben unabhängig davon
bestehen: Dort war unveränderter SOC trotz gemeldeter Ladeleistung nachgewiesen.

Die erste aktive Empfehlung beginnt das F4-Zeitfenster neu; vorherige Stunden
oder Tage ohne Empfehlung zählen nicht rückwirkend. Eingefrorene Werte oberhalb
der Standby-Schwelle bleiben einschließlich ihrer Neustart-Persistenz überwacht.
Eine bestätigte ausgeschaltete Steckdose sammelt keine neue F4-Evidenz, auch
wenn die Geräteintegration noch alte positive Leistungswerte anzeigt.
Der vorhandene Einschaltpfad startet geplante Ladung ohne vorherige Erreichbarkeit
der Fossibot. Der SOC-Cache gilt höchstens sieben Tage; anschließend greift die
bestehende Planung mit unbekanntem SOC. Ein noch gültiger SOC gleich Ladeziel
erzeugt keinen Ladebedarf. Ohne zurückkehrende Telemetrie wird die eingefrorene
Laufzeit nicht nahtlos verlängert. Überschuss-, Netzbezugs- und
Aktorbestätigungsregeln bleiben bestehen.

Nachweis: `tests/ha/test_load_switching.py` prüft Stillstand nach alter Empfehlung,
Wiederherstellung einer falschen 90-%-/0-W-Sperre sowie den ersten Ladestart nach
langer Pause zusätzlich zu den bestehenden G2-/F4- und Persistenztests.

## Fossibot-Ladepause mit Passthrough (ab 0.59.2)

Der separat installierbare [Ladepausen-Blueprint](F-FOSSIBOT-CHARGE-PAUSE.md)
übersetzt den bestehenden Lade-Helfer in einen Gerätebefehl: exakt `on` setzt
die Ladeverzögerung auf null, jeder andere Zustand auf zehn Minuten. Der
Adapter schreibt nur bei bestätigt eingeschaltetem Eingang und numerisch
verfügbarem Timer. Ein Zweiminutentakt erneuert die Pause unabhängig vom
angezeigten Countdown; Neustart und Geräte-Rückkehr gleichen den aktuellen
Helferzustand erneut ab. Der Battery Manager bleibt der Planer.

Für erhaltenen Netz-Passthrough wird je Last die bestehende Eingangsregel
`keep_on` verwendet. Sie erhält einen eingeschalteten Eingang beim Ladestopp,
erzwingt aber kein Einschalten eines ausgeschalteten Eingangs. Der Adapter
schaltet weder Eingangsstecker noch AC-Ausgang und verändert keine SOC-Grenze.
Ein ausgeschalteter Helfer oder positiver Timer bestätigt keine physische
Ladepause. Nach Ablauf des Timers während eines HA-/Verbindungsausfalls kann
das Gerät wieder laden; ein dauerhafter Hardware-Ladestopp wird nicht zugesagt.

Gemeldete Netzladung trotz gesperrter Ladefreigabe und eingeschaltetem Eingang
erzeugt nach zwei durchgehenden Minuten eine dauerhafte HA-Meldung, auch bei
nicht verfügbarem Timer. Ein erfolgreicher Timerbefehl löscht die Meldung nicht;
dafür ist eine neue Freigabe oder eine bestätigte OFF-Rückmeldung der Netzladung
beziehungsweise des Eingangs erforderlich.

Die begleitende Python-Korrektur unterbricht neue F4-Freeze-Evidenz, wenn das
konfigurierte Lade-Gate bestätigt ausgeschaltet ist: Konstante positive
Passthrough-Leistung allein belegt keine eingefrorene Batterieladung. Bereits
gesetzte positive F4-Sperren und G2-Sperren bleiben erhalten. Der Blueprint ist
separat installierbar; diese F4-Korrektur benötigt das Integrationsupdate.
