# SOC-Nachführung und Netzteil-Evidenz

Eingeführt mit 0.65.0, Betriebsprognose aktualisiert mit 0.69.0. Ergänzt [adaptive 48-V-Stützung](F-ADAPTIVE-48V-GATE.md).
Der gemeldete BMS-SOC kann gegenüber dem tatsächlichen Energieinhalt driften.
Eine Nachkalibrierung im oberen Bereich darf deshalb schneller verlaufen, als
die gleichzeitig gemessene Ladeenergie im linearen Modell erwarten lässt.
Das ist zunächst eine Nachführung der Gerätekoordinate, kein Gerätefehler.

## Getrennte Aussagen

Der BMS-SOC bleibt der aktuelle Gerätewert. Gemessene Batterieleistung beschreibt
eine Energieänderung, keinen unabhängig bekannten absoluten SOC. Die konfigurierte
Kapazität wird durch diese Beobachtung nicht automatisch geändert. Insbesondere
erzeugt eine Nachführung keine zusätzliche gemessene Solarenergie und erfüllt
kein bereits ausgestelltes Rückladeversprechen.

`soc_consistency.py` führt einen begrenzten Vergleich mit getrennten originalen
Publikationszeiten. Das Fenster umfasst höchstens fünf Minuten; mindestens
60 Sekunden zusammenhängende Abdeckung und drei unterschiedliche frische
SOC-Publikationen sind erforderlich. Messungen und Halteintervalle sind auf
30 Sekunden begrenzt. Lücken, ungültige Werte und Bindungswechsel erzeugen keine
fiktive Energie. Eine großzügige Energiegrenze berücksichtigt die Quantisierung
einmal je Fenster statt erneut für jeden kleinen SOC-Schritt.

## Episode und Lernkoordinate

Einzel-Sprungprüfung und rollierender Vergleich gehören zu derselben Episode:
`tracking` → `reconciling` → `settling` → `tracking`. Die Nachführung wird als
Information diagnostiziert. Sie macht nicht allein alle Coordinator-Entities
unverfügbar und schaltet selbst keine Quelle.

Eine Episode beginnt genau eine neue Epoche. Alte natürliche Spannungs-/SOC-
Belege und während der Nachführung gewonnene Punkte können keine neue Lieferung
begründen. Die Stabilisierung erfordert zehn Minuten tatsächlich abgedeckte,
energetisch konsistente Beobachtung. Neustarts und Lücken zählen nicht als Ruhe.
Danach müssen neue natürliche OFF-Beobachtungen weiterhin mindestens drei Punkte
über 30 Minuten, einen passenden Strombereich, geringe Streuung und konsistente
Schwellen nachweisen. Vier Punkte über 920 Sekunden genügen ausdrücklich nicht.

Epoche und Phasengeneration gehören unveränderlich zu Planeingaben und Ergebnis.
Während Nachführung/Stabilisierung entstehen keine neuen optionalen
batteriegestützten Entnahmen aus dem gestiegenen Roh-SOC. Notwendige DC-Versorgung,
Schutz und manuelle Hoheit bleiben erhalten. Direkte PV-Nutzung benötigt weiterhin
einen unabhängigen vollständigen Nachweis; ein günstiger Quellenmix allein
beweist keine batterieunabhängige Deckung. Vor einer neuen Freigabe nach der
Stabilisierung ist ein frischer Plan erforderlich. Auch wartende Aufträge und
späte Bestätigungen müssen die aktuelle Koordinate prüfen.

Die Aktorautorität bindet Quellenziele, Inverterziel, Revision, Ablaufzeit und
Koordinate gemeinsam an denselben akzeptierten Plan, bevor dessen Aufträge
warten können. Eine neuere Revision zusammen mit einer älteren Planaufzeichnung
bildet keine gültige Übergabe. Die Rückkehr zu `tracking` allein erneuert keine
alte Erlaubnis.

Normale Lasten und Kaskaden teilen sich für direkte PV-Starts eine gemeinsame
Reservierung. Maßgeblich ist mindestens die höchste bekannte konfigurierte,
gelernte oder geplante Startleistung. Derselbe noch unveränderte Messwert kann
somit nicht mehrere Starts unabhängig finanzieren. Erst ein bestätigter Stopp
oder eine passende frische Bilanz nach dem belegten Leistungsbeginn kann die
Reservierung auflösen. Eine noch vor der maßgeblichen Eingangsleistung
veröffentlichte Hausbilanz genügt dafür nicht.
Eigene verspätete Einschaltbestätigungen verlieren ihre Berechtigung bei einem
Koordinatenwechsel und werden nach der Eingangs- und Besitzpolitik bereinigt;
eine manuelle Übernahme bleibt maßgeblich.

Die Sperre gilt auch für den älteren nicht koordinierten Betriebsmodus. Eine
allein daraus folgende Inverter-OFF-Empfehlung ist kein unterschrittener
SOC-Boden. Tatsächlich niedriger SOC, gemessener Netzbezug und notwendige
DC-Versorgung behalten ihre eigenen Schutzregeln.

Der Vorbehalt kann vorübergehend mehr Export oder Netzbezug verursachen. Diese
Kosten sind im jeweiligen Replay neben der erhaltenen Versorgung auszuweisen;
eine perfekte SOC-Prognose oder eine garantierte Einsparung folgt daraus nicht.

## Erklärte Bandqualifikation

Gültige Bänder und Ablehnungsdiagnosen verwenden dieselbe Auswertung. Anzahl,
Zeitspanne, gewichtete SOC-Quantile, Streuung, Lastbereich und Alter werden für
Einschalten, Lieferung und Ausschalten getrennt ausgewiesen. Viele gespeicherte
Punkte bedeuten nicht, dass der aktuelle Modellstrom qualifiziert ist.

Die kompakte Live-Diagnose nennt den Bezugsstrom der nativen 48-V-Last. Alle
Strombereiche, begrenzte Rohbelege und Ereignisse gehören in den privaten
Diagnose-Download. Sie enthalten Haushaltsdaten und werden nicht automatisch
veröffentlicht. Umfangreiche Diagnoseattribute bleiben vom Recorder ausgeschlossen.

## Gemessene Netzteilstützung

Die bereits vorhandenen optionalen AC-/DC-Messzuordnungen bleiben erhalten.
Ein Snapshot bindet Messwerte an Quelle, bestätigten Schaltzustand,
Wechselgeneration, Topologie, Erfassungs-/Publikationszeit und Gültigkeit.
Ein noch frischer Wert von vor einer Quellenflanke ist danach kein neuer Beleg.
Zeitlich versetzte AC-/DC-Werte sind nicht automatisch eine gemeinsame Bilanz.

Gemessene ON-Lieferung ist von der natürlichen OFF-Spannungs-/SOC-Kennlinie
getrennt. AC-Aufnahme ist ohne geeignete Verlustinformation keine DC-Lieferung.
Weder ON, Nennleistung noch eine einzelne Momentanmessung garantieren eine
mehrtägige Versorgung. Fehlende oder unpassende Messungen bleiben unbekannt.
Ein gesonderter langfristiger Liefervertrag benötigt eigene Mess- und
Reviewnachweise, bevor er zusätzliche Prognosegutschriften erlauben kann.

Für Schutz und Entnahmebudgets verwendet der Manager die konservative
SOC-Kurve. Die zusätzliche Erwartungskurve beschreibt die unten festgelegte
Annahme zur 48-V-Lieferung.

## Erwartungskurve bei gemessener 48-V-Lieferung ab 0.66.0

Die Erwartungskurve zeigt den SOC unter der Annahme, dass das 48-V-Netzteil
weiter mindestens so viel DC-Leistung abgibt wie im beobachteten Abschnitt.
Der Abschnitt gehört zur aktuellen, bestätigten Schaltfolge. Beide Netzteile
sind eingeschaltet, der DC/DC-Wandler ist bestätigt aus und das Inverterlimit
steht bestätigt auf null. Konfiguriertes Blockierungsfeedback wird ebenfalls
geprüft. Die Messung nutzt die bestehende optionale Zuordnung
`support_dc48_output_power_entity` und verarbeitet originale HA-Publikationen
einschließlich erneut gemeldeter unveränderter Werte. Eine konfigurierte
48-V-Stromgrenze liefert die Obergrenze für plausible Messwerte. Dafür ist eine
eindeutig als DC-Ausgang gebundene Messstelle erforderlich. Eine gleichzeitige
Zuordnung als Standortzähler, Sollwert oder unabhängiger Verbrauchsmesspunkt
verwirft den direkten Beleg. Als angenommen
oder wiederhergestellt markierte Zustände gelten als unbelegt.

Die Leistung ist das Minimum aus mindestens 30 Minuten zusammenhängenden
DC-Messmeldungen. Jeder Abstand und das Alter der letzten Meldung dürfen
höchstens 30 Sekunden betragen. Ein gemeldeter Nullwert zählt zum Minimum.
Wechsel im bestätigten Versorgungsmodus, neue Messbindungen, Lücken und
ungültige Messwerte trennen die Abschnitte. Nach einem Neustart beginnt die
Beobachtung neu. Ist der Ausgangsmesser konfiguriert und fehlt der Messnachweis,
zeigt die Karte die Planungs- und Sicherheitskurve und erklärt den fehlenden
Nachweis.

### Betriebsprognose ab 0.69.0

Der akzeptierte Schaltplan wird ein zweites Mal energetisch durchgerechnet,
wenn bei Planbeginn eine qualifizierte Beobachtung vorliegt. Die Betriebsprognose
übernimmt die Schaltzeitpunkte, Leistungsgrenzen und Zusatzlasten dieses Plans.
Innerhalb des vergleichbaren Betriebs versorgt die belegte Stützung höchstens
die native 48-V-Last. Die dazu benötigte AC-Energie geht mit dem konfigurierten
Wirkungsgrad in die Netzbilanz ein. Ein Überschuss der gemessenen DC-Leistung
über die native Last berechtigt zu keiner prognostizierten Netzteilladung.

Das Fenster endet am ersten unpassenden Teilschritt oder an der zeitlichen
Gültigkeitsgrenze, auch mitten im Stundenintervall. Die übrigen Schritte
beginnen beim dann erreichten SOC. Spätere PV-Ladung, Speichergrenzen,
Batterieentnahme, Import und Export werden neu bilanziert. Der Sensor, seine
Stundenwerte, Tagesenergien und die durchgezogene Kurve zeigen diesen
Betriebsverlauf. Die gestrichelte Sicherheitskurve bleibt Grundlage für
Schaltungen, Reserve und Freigaben. Fehlt der Beleg bei Planung oder
Veröffentlichung, zeigt die Karte ausschließlich den konservativen Plan und
erläutert die Ursache.

Spannung und gelernte SOC-Schwellen beeinflussen die Bereitschaft des Netzteils.
Für die Lieferleistung zählt der belegte elektrische Betrieb. Bei einer
BMS-Nachführung pausiert die Veröffentlichung. Nach Stabilisierung kann ein
neuer Plan die lückenlos weiter beobachtete Versorgung am aktuellen gemeldeten
SOC verankern. Eine neue Kalibrierung liefert keine zusätzliche Energie.

### Diagnose in der Prognosekarte

Der Netzbezugsschutz bewertet die gemessene Leistung am Netzanschluss.
Eine gültige Meldung darf bei der Auswertung höchstens 30 Sekunden alt sein.
Erst ein Bezug über 50 W aktiviert die Sperre für Überschusslasten. Der
Forecast-Sensor gibt den Zustand der Sperre und die zugrunde liegende Messung
mit ihrem Alter bei der Auswertung aus. So lässt sich auch ein alter Messwert
von einem aktuellen Wert von 0 W unterscheiden. Hat der Schutz das Ausschalten
einer Last angefordert, bleibt der auslösende Messwert sichtbar, auch wenn
der Zähler inzwischen wieder 0 W meldet. Als letzte Anforderung zählt der
zuletzt an Home Assistant gesendete netzbedingte AUS-Auftrag. Bei einer
verspäteten Gerätebestätigung kann seine Entscheidungszeit früher liegen
als die angezeigte Anforderungszeit.

Für die 48-V-Erwartungskurve gilt der Messnachweis bei Veröffentlichung des
Plans. Fehlt er, zeigt die Karte den Grund an, etwa eine fehlende
DC-Ausgangsmessung, eine fehlende Stromgrenze zur Plausibilitätsprüfung oder
eine zu kurze Messreihe. Ein bestätigtes Einschalten allein belegt keine
Lieferung. Bei qualifizierter Messreihe nennt die Karte Mindestleistung und
Beobachtungsdauer. Sie unterscheidet, ob der geplante Versorgungsmodus abweicht
oder die Messreihe aufgrund geänderter Bedingungen endet. Sind beide Kurven im dargestellten Abschnitt optisch deckungsgleich,
weist die Karte darauf hin.

### Erwartung aus der gemessenen Batterieentladung

Fehlt die Zuordnung eines 48-V-Ausgangsleistungsmessers, kann die Karte die
am Akku beobachtete Entladerate fortschreiben. Dafür müssen beide Netzteile
bestätigt EIN, der DC/DC-Wandler AUS und das gemeldete Inverterlimit null
sein. Eine konfigurierte Netzrückmeldung muss während des gesamten Fensters
dieselbe verbundene Quelle bestätigen. Unterbrechungen und Quellenwechsel
starten die Beobachtung neu. Eine konfigurierte Invertersperre muss den
gesperrten Zustand bestätigen. Die Batteriespannung
darf die konfigurierte Ausgangsspannung des 48-V-Netzteils nicht übersteigen.
Ein unveränderter BMS-SOC allein genügt dafür nicht. Die Messung am Akku
fasst alle wirksamen Quellen und Verbraucher zusammen. Auch ein
ungemessener Lader kann zur beobachteten Entlastung beitragen.

SOC, Klemmenspannung und vorzeichenrichtige Batterieleistung müssen über
mindestens 30 Minuten mit frischen Originalmeldungen belegt sein. Das Fenster
endet am letzten gemeinsam abgedeckten Zeitpunkt. Nur Intervalle bis
30 Sekunden zählen. Pro Intervall wird die höhere der zwei benachbarten
Entladeleistungen über dessen tatsächliche Dauer angesetzt. Für die
Fortschreibung zählt mindestens die jüngste Entladeleistung. Bis zu 1 W
positive Ladeanzeige zählt als null Entladung. Größere Ladung, ein Wechsel
der Schaltung, Datenlücken oder eine Spannweite der
Entladewerte über 10 W erfordern einen neuen Messabschnitt.

Ein konfigurierter PV-Leistungsmesser muss für diesen Zeitraum null melden.
Fehlt die Zuordnung, verwendet der Manager die astronomische Nacht am
Anlagenstandort (Sonne mindestens 18° unter dem Horizont). In diesem Fall
endet die Annahme spätestens mit der astronomischen Morgendämmerung am
Standort. In hohen Breiten können einzelne Dämmerungsereignisse fehlen.
Der Manager prüft die Sonnenhöhe auch zwischen den Meldungen und kürzt die
Fortschreibung, sobald er durchgehende Dunkelheit nicht mehr belegen kann.
Die Batterieleistung braucht einen von Lastmessern unterscheidbaren
Messpunkt. Ein PV-Messer darf zugleich den PV-Zufluss der Verbrauchsbilanz
liefern. Ab 0.70.1 dienen die als Live-AC-Eingang und -Ausgang zugeordneten
Messwerte der AC-Restlastregelung. Unterschiede, Rückfluss und Meldelücken an
diesen Anschlüssen unterbrechen die Beobachtung der Batterieentladung nicht.
Wird derselbe physische Messpunkt ausdrücklich als saldierter Netzanschluss
für den Netzbezugsnachweis verwendet, gelten für ihn die unten beschriebenen
Anforderungen an Netzleistung und Aktualität. Ist für Live-AC ein anderer
Standortzähler zugeordnet, verwirft der Netzbezugsnachweis einen als
Wechselrichteranschluss konfigurierten Messpunkt. Der Wechselrichter kann
Leistung beziehen, während die Anlage insgesamt ins Netz einspeist.

Die Schätzung gilt höchstens für die belegte Dauer und maximal 48 Stunden.
Die belegte Versorgungsart muss bereits beim Start des Plans bestanden haben.
Die Fortschreibung gilt für Teilschritte mit derselben Versorgung und derselben
nativen 48-V-Grundlast. Ausgehend vom Start-SOC sinkt die
Erwartung je Intervall um die gemessene Entladeleistung mal Dauer, umgerechnet
mit Entladewirkungsgrad und Speicherkapazität. Bei 0 W bleibt sie flach. Diese
Nettobilanz bestimmt die Batterieentnahme im vergleichbaren Betrieb. Den
Rest der nativen Last bucht die Betriebsprognose als Stützung. Erreicht der
Speicher während einer positiven Restentladung die Untergrenze, endet das
Messfenster genau dort. Bei 0 W Restentladung kann die Versorgung auch am
Speicherboden fortbestehen.

Prognostizierte PV-Erzeugung, Ladung aus anderer Quelle, ein ungedeckter
DC-Bedarf, ein Wechsel des Versorgungsmodus oder eine erreichte Grenze des
Speichermodells begrenzen die Linie. Die beobachtete Entladung darf höchstens
der nativen Last entsprechen. Eine konfigurierte Netzteil-Leistungsgrenze
muss die dafür benötigte Stützung zulassen.

Die Karte kennzeichnet diese Linie und ihren Wattwert als Batterieentladung.
Bei konfiguriertem 48-V-Ausgangsmesser gilt weiterhin dessen eigener
Messvertrag, auch bei einem belegten Nullwert oder einer Messlücke.
Schaltaufträge, Verbrauchsfreigaben und Reserveplanung verwenden den
ursprünglichen Plan.

### Kurzfristige SOC-Erwartung bei anhaltendem Netzbezug

Bei AC-gekoppelter PV können die Verbraucher den Solarstrom vollständig nutzen,
während beide Netzteile die DC-Last decken. Für diesen Fall lassen sich unter
den Messquellen zwei Eingänge für die SOC-Erwartung zuordnen:

- **Saldierte Netzleistung** am Netzanschluss, positiv bei Bezug, in W oder kW.
- **Wirksamer Netzbezugs-Sollwert** einschließlich der Korrekturen der
  Hausregelung. Ein positiver Wert bedeutet gewünschten Bezug. Unterstützt sind
  Rückmeldungen in W oder kW und explizit als Watt zugeordnete
  `number`-Entitäten ohne Einheit.

Die Zuordnungen belegen den Tagesbetrieb. Ist bereits ein saldierter Zähler für
die Live-AC-Regelung zugeordnet, kann dieser mit dem explizit gebundenen
wirksamen Sollwert verwendet werden. Der Manager verlangt 30 Minuten
kontinuierlichen Netzbezug mehr als 50 W über dem größeren Wert aus null und
Sollwert. Im Ruhezustand liegt die Batterieleistung innerhalb von ±1 W. Einzelne Entladeimpulse
bis 4 W dürfen höchstens 30 Sekunden dauern. Die für solche Impulse angesetzten
Intervalle dürfen in einem 30-Minuten-Fenster zusammen höchstens 60 Sekunden
ausmachen. Ihr Energiebeitrag wird mitgerechnet. Während eines offenen
Impulses pausiert die Fortschreibung.
Ein Wechsel des Sollwerts startet die Beobachtung neu. Für den periodisch
gemeldeten Sollwert sind bis zu 65 Sekunden zwischen Originalmeldungen
zulässig, für die übrigen beteiligten Messkanäle bis zu 30 Sekunden.
Die Voraussetzungen für Topologie, Spannung und stabile SOC-Koordinate gelten
auch für dieses Szenario.

Die Fortschreibung mit Netzbezugsnachweis endet spätestens nach der belegten
Messdauer oder einer Stunde ab dem letzten gemeinsam belegten Zeitpunkt. Im
Plan muss die AC-Grundlast selbst bei der höheren verfügbaren PV-Schätzung
den nötigen Netzbezug plausibel machen. Über eine Stunde verteilte Restenergie
eines gerade laufenden Geräts darf diesen Nachweis nicht verlängern.
Geplante Überschussladung oder ein Wechsel der Versorgung beenden das Fenster.

## Kompatibilität und Nachweise

Alte Planner-Aufzeichnungen ohne Koordinaten- oder PSU-Snapshot behalten neutrale
Defaults. Die Migration des privaten Lernzustands erfindet weder frühere
Nachführungen noch Energie über Neustartlücken. Epoche und Episode überleben den
Neustart, Integrationscursor und Stabilitätsnachweis beginnen neu.

Widerrufene eigene Startaufträge haben getrennte versionierte Cleanup-Belege.
Nach einem Neustart berechtigen nur ausdrücklich gesendete, weiterhin passend
gebundene Aufträge unter derselben manuellen Kontrollgeneration zur Bereinigung.
Uneindeutige alte Auftragszeilen erzeugen weder neues Eigentum noch bestätigte
Laufzeit.

Verträge werden durch `tests/core/test_soc_coordinate.py`,
`tests/ha/test_soc_consistency.py`, `tests/ha/test_soc_coordinate_runtime.py`,
`tests/ha/test_psu_observation.py` und die Gate-/Aktor-Tests unter `tests/ha/`
sowie Frontend- und lokale Browserregressionen geprüft.
Private historische Replays ergänzen synthetische öffentliche
Gegenbeispiele; eine Live-Abnahme wird durch lokale Tests nicht behauptet.
