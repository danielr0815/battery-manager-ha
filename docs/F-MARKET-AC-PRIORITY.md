# Marktorientierter Inverterbetrieb (0.53.0)

> Ab 0.55.0 ersetzt der Abschnitt [Erwartete PV](#erwartete-pv-statt-verbindlicher-p90-vorbereitung-0550)
> die nachfolgende historische Verwendung der oberen PV als Vorbereitungspfad.

Betreiberauftrag vom 02.10.2026: Bei Festpreis sollen deutliche EPEX-Spitzen
gegenüber einem geringfügig höheren AC-Verbrauch Vorrang bekommen. Das Ziel
ist die zeitliche Verlagerung des Netzbezugs nach Marktsignalen; es ist keine
Berechnung persönlicher Spotpreis-Ersparnisse und keine Zusage lokaler Netzentlastung.

## Gemeinsame Energieplanung

Der Reserveplanner betrachtet den gesamten verfügbaren PV-/Last-Horizont in
einem Durchgang. Der vorherige tägliche Neustart des Heute/Morgen-Fensters
entfällt: Eine PV-Deadline am dritten Tag ist bereits am ersten Abend sichtbar,
statt erst um simuliert Mitternacht wirksam zu werden. Nominaler DC-Bedarf wird
weiterhin vor optionalem AC reserviert; die obere PV-Prognose begründet den
benötigten Speicherraum. DC allein genügt: keine zusätzliche AC-Vorbereitung.
Netzteilhaltung folgt ausschließlich der DC-Grenze, unabhängig von Preisen;
Marktspitzen erlauben keine gezielte Netzladung. Eine zusätzliche spätere
DC-Netzteilphase darf AC-Vorbereitung nicht indirekt finanzieren.

Sobald eine AC-Trajektorie auch DC-Netzversorgung enthält, wird sie gegen dieselbe
Prognose ohne AC-Abgabe geprüft. Erhöht sich der DC-Netzbezug, erhält ein zweiter
Kandidat einen entsprechenden zusätzlichen Energierückhalt. Genügt diese einmalige
Korrektur nicht, gilt der DC-only-Plan bis zur nächsten Prognose. Eine Differenz
bis zu einem maximalen fünfminütigen DC-Verbrauchsquantum wird wegen der binären
Quellenübergaben toleriert; zusätzliche unversorgte DC-Energie nie. Die Prüfung
benutzt die Netzteilwirkungsgrade. Das ist eine begrenzte konservative Suche,
keine Garantie für die beste aller denkbaren AC-Teilmengen. Der Live-Pfad kann
ein auf diese Weise verworfenes Budget nicht wieder freigeben.

Alle Kandidaten laufen durch dieselbe physische Simulation mit Wirkungsgraden,
Standby, DC-Schutz und binären Fünf-Minuten-Freigaben. Preise schaffen kein neues
AC-Energiebudget. Der Inverter erhält weiterhin ausschließlich 0 oder seine
konfigurierte Maximalleistung; ESS liefert den tatsächlichen Bedarf.

## Preisgewichtung

Je lokalem Tag werden bis zu vier reale Stunden mit den höchsten veröffentlichten
Preisen bevorzugt. Viertelstunden zählen entsprechend ihrer Dauer. Der Bonus
ist proportional zum Abstand vom zeitgewichteten Tagesmedian: 50 EUR/MWh
Abstand erhöhen das Gewicht um eins; insgesamt wird es auf drei begrenzt.
Ohne nennenswerte Preisdifferenz bleibt die nutzbare AC-Restleistung entscheidend.
Verglichen wird `nutzbare Restleistung × Gewicht`, begrenzt durch die
Inverterleistung. PV-gedeckte Last ist keine Entladegelegenheit. Gleichstände
verwenden weiterhin die spätere Gelegenheit vor der zugehörigen PV-Deadline.

Diese begrenzte Heuristik erlaubt z. B. 500 W zur Preisspitze vor 600 W zu
normalen Preisen, gibt einer 50-W-Last aber auch bei extremem Preis keinen
Vorrang vor 600 W. Sie beansprucht kein mathematisch globales Marktoptimum.
Durch Standby und unterschiedliche vollständige ON-Schritte kann etwas mehr
Netzbezug oder Restexport entstehen; dies ist der ausdrücklich gewünschte
Kompromiss zwischen Marktzeitpunkt und reiner Effizienz.

Fehlende Preise sind unbekannt, niemals null. Ab 0.54.2 gilt der Betreiberauftrag
vom 04.10.2026: Vorhandene Preise bleiben für ihre Zeiträume wirksam; nur
unbedeckte Zeiträume verwenden die AC-Lastpriorität. Jeder Zeitraum besitzt einen
einheitlichen Vergleichswert `nutzbare Restleistung × eigenes Preisgewicht`.
Bei fehlenden Preisen beträgt das Gewicht eins. Damit bleibt die Reihenfolge
auch bei gemischter Abdeckung transitiv: 500 W mit Gewicht drei schlagen
600 W mit Gewicht eins, und diese wiederum unbepreiste 550 W. Fehlende Preise
am dritten Prognosetag löschen keine heutigen oder morgigen Preissignale.
Ein teilweise bedeckter Simulationsschritt mittelt bekannte Gewichte und das
neutrale Gewicht der unbedeckten Sekunden anhand ihrer realen Dauer.
Negative Preise bleiben gültig und rechtfertigen weder
zusätzliche Verbraucher noch Netzladung. Eine flache Preisreihe aktiviert keinen
besonderen Live-Vorbehalt. Vier Stunden sind bevorzugte Fenster, keine Pflichtlaufzeit.

## Gemeinsamer Vertrag mit dem schnellen Lastpfad

Alle fünf Sekunden prüft der bestehende Live-Pfad Messwerte, Quellen und SOC.
Bei aktiver Marktgewichtung schützt seine normale Energiegrenze auch die für
bessere spätere Gelegenheiten reservierte Energie.

Eine unerwartet große Last kann dennoch vorziehen:

1. Der Core betrachtet tatsächlich geplante AC-Abgabe vor der nächsten positiven
   Netto-PV-Ladung. Noch nicht erzeugte Energie gehört nicht zu diesem Budget.
2. Er veröffentlicht eine konservative Lastschwelle: die beste dieser späteren
   Gelegenheiten, umgerechnet mit dem aktuellen Preisgewicht. Jedes fehlende
   Preisgewicht wird einzeln durch eins ersetzt; bekannte Gewichte bleiben
   auch bei einer unbepreisten aktuellen oder zukünftigen Gelegenheit wirksam.
3. Ein Start verlangt mindestens 10 % mehr nutzbare aktuelle Leistung. Die
   nutzbare Leistung bleibt auf die Inverterleistung begrenzt. Ein bereits
   gestarteter Betrieb bleibt nur zulässig, solange er mindestens gleichwertig ist.
4. Die zusätzliche Freigabe kann höchstens die bereits geplante AC-Energie
   verbrauchen und niemals die DC-/Schutzgrenze unterschreiten. Nach Verbrauch
   dieses Budgets sperrt der Pfad wieder. Ein normaler Neuplan ersetzt beide
   Grenzen gemeinsam; Preise und Lastschwellen stammen aus demselben Snapshot.
5. Wegfall des Vorteils bei erschöpftem regulärem Budget, Quellenschutz oder
   veraltete Daten übergehen die normale zehnminütige Nachlaufzeit.

Das 35-Sekunden-Energiefenster für volle Inverterleistung, 30-Sekunden-
Messwertaktualität, fünf Minuten Planalter, Quellenbesitz und erneute Prüfung
nach verspäteten Geräteantworten bleiben verbindlich. Es gibt keinen zweiten
Netzteilcontroller. Diagnose: `live_ac.market_override_demand_w` sowie das
verbleibende `available_wh`.

## Home Assistant und Anzeige

In den Planner-Einstellungen:

- `market_price_enabled`: standardmäßig an, abschaltbar; wirksam nur innerhalb
  der Reserveplanung. Ihr vorhandener Schattenmodus dient weiter zum Vergleich.
- `market_price_entity`: optionaler Sensor. Leer wählt einen eindeutigen Sensor
  mit `epex_spot` in der Entity-ID und Einheit `EUR/MWh`. Mehrdeutige Quellen
  werden nicht willkürlich gewählt. Umbenannte Quellen können explizit gewählt werden.

Die Integration liest ausschließlich veröffentlichte `data`-Intervalle mit
`start_time` und `end_time`. Unterstützt sind `price_eur_per_mwh`, das ältere
`price_ct_per_kwh` und `price_per_kwh` in EUR/kWh. Zeitstempel müssen UTC-Offsets
enthalten; Dauer/Überlappungen/Nicht-Endlichkeit werden validiert. Eine ungültige,
abgelaufene oder ausgefallene Quelle fällt auf reine Energieplanung zurück.
Eine explizit ausgewählte Nettopreisquelle wird normalisiert, jedoch nicht als
persönlicher Tarif interpretiert. Empfohlen ist die Großhandelsreihe.

Die gewählte Quelle gehört zu den Eingangs-Listenern. Veröffentlichte Preisgrenzen
lösen eine Neuplanung aus und begrenzen die Gültigkeit der Live-Freigabe. Später erscheinende
Auto-Erkennungsquellen werden spätestens beim periodischen Neuplan gelesen.
Keine zusätzlichen HTTP-Abfragen oder Runtime-Abhängigkeiten. Der reine Core
bekommt unveränderliche Intervalle; Planneraufzeichnungen bleiben replayfähig.

Die Reserveanzeige zeigt Datenverfügbarkeit, bevorzugte Marktfenster und den
prognostizierten vermiedenen Netzbezug durch AC-Abgabe nach Standby. Das ist
keine gemessene Ersparnis und keine Differenz zu einer preisfreien Planung.

## Nachweise und beabsichtigte Änderungen

`tests/core/test_market.py` prüft Preis gegen 100 W Lastdifferenz, flache/kleine
Spreads, fehlende und negative Preise, DC-Vorrang, PV-Deadline, Standby und DST.
Die Regressionen für 0.54.2 prüfen einen unbepreisten Folgetag, die transitive
Reihenfolge bei gemischten Preisen und teilweise bedeckte Fünf-Minuten-Schritte.
`tests/ha/test_market.py` prüft Einheiten, Fehlerfälle, Autowahl, Konfiguration und
den unveränderten Snapshot bis zur Veröffentlichung. Ein echter Core-Plan in
`tests/ha/test_live_ac_runtime.py` belegt geschützte Marktreserve, frühe Freigabe
bei höherer Last und sofortige Rücknahme nach Lastabfall. Kartenprüfungen trennen
bevorzugte Fenster, Pflichtlaufzeiten und persönliche Ersparnisse.

Die früheren Tests zur absichtlichen Ausblendung des dritten Tages wurden auf
den neuen Gesamt-Horizont umgestellt. Im September-Lastprioritätsfall erhöht
sich der nominale Restexport von höchstens 8,257 auf rund 16,769 Wh: Der DC-Vorrat
umfasst jetzt den gesamten Horizont und verbleibende AC-Restbudgets werden nicht
mit Teilleistung verbraucht. Die bestehenden Schutz- und DC-Bilanzprüfungen gelten
weiterhin. Alte Heute/Morgen-Aussagen in historischen F-Dokumenten sind damit ersetzt.


### Vergleich mit dem Oktoberfall und Goldens

Die anonymisierte Fixture `reserve_october_forecast.json` bewahrt den untersuchten
Fall vom 02.10.2026, 06:41 MESZ. Die nominale Planung verzichtet nun auf AC-Abgabe;
Freitag und Samstag enthalten keine Netzteilversorgung. Samstag erreicht minimal
rund 21,04 % SOC, Sonntag maximal rund 85,02 %. Mit der stärkeren oberen PV-Prognose
ist AC-Abgabe wieder möglich. Die ursprüngliche Mitternachtsaktivierung entfällt.
Die bestehende wirtschaftliche Haltung nach dem letzten PV-Fenster bleibt zulässig.

Die 16 Topologie-Goldens wurden neu erzeugt: Nur `reserve_sunny_winter` ändert sich.
Der Import sinkt von 2,851124 auf 2,848221 kWh, der Export bleibt null; minimaler
SOC steigt von 40,301 auf 40,5361 %. Die einmalige Budgetkorrektur bewahrt nützliche
AC-Fenster und reduziert den anschließenden DC-Netzbezug. Kein Golden-Szenario
importiert mehr Energie. Die übrigen 15 Szenarien bleiben unverändert.

## Prognoseabhängiger Tagespuffer statt fester Horizontgrenze (0.55.2)

Betreiberauftrag vom 06.10.2026: Der Zuschlag soll von der jeweiligen
Tagesprognose abhängen, nicht von einer pauschalen 250-Wh-Grenze. Die Grenze
von 5 % Batteriekapazität über den gesamten Horizont entfällt. Unverändert
bleiben 25 % der physisch begrenzten oberen Abweichung, die skalare Rückfallregel
für fehlende Bänder und der Vorrang von DC-Bedarf und Verbrauchsrückhalt.
Ein zusätzlich angehängter Prognosetag verkleinert damit nicht mehr den Puffer
eines vorhandenen Tages. `pv_uncertainty_budget_wh_by_day` nennt den Zuschlag
je Datum in gespeicherten Wh; `pv_uncertainty_budget_wh` bleibt dessen Summe.

Im Offline-Replay des Plans vom 06.10., 07:54 MESZ entspricht die bisherige
Planung exakt der aufgenommenen Gerätesimulation. Ihr Zuschlag verteilt sich
mit rund 97/120/33 Wh auf heute/morgen/übermorgen. Ohne die pauschale Grenze
werden daraus rund 383/472/130 Wh entsprechend den jeweiligen Prognosebändern.
Das morgige nominale Maximum sinkt von 92,82 auf 86,30 % bei einer physischen
Ladeobergrenze von 95 %. Rund 335 Wh zusätzliche AC-Abgabe im Horizont
reduzieren den simulierten Gesamtimport um rund 307 Wh; DC-Netzversorgung und
nominaler Export bleiben gleich. Das ist ein Offline-Vergleich, keine gemessene
Ersparnis und keine Garantie für den tatsächlichen Solarertrag.

Der zusätzliche Betreiberauftrag verlangt 85 % als weiches SOC-Ziel, also
15 Prozentpunkte bis 100 %, bei unveränderter physischer Grenze von 95 %.
Eine zweite, nominale Vorbereitungshülle gibt dieses Ziel nur an netto
ladenden PV-Schritten vor. Der nominale DC-Mindestbedarf plus Verbrauchspuffer
darf es erhöhen. Die Grenze wird mit der bisherigen Unsicherheitshülle über
das Minimum kombiniert; die Puffer werden nicht addiert. Wo eine geeignete
AC-Möglichkeit fehlt, gilt die physische Simulation, kein erzwungener Export.

Beim selben aufgenommenen Fall ergibt die vollständige Politik für morgen
85,06 % statt 92,82 %. Gesamtimport sinkt um rund 503 Wh; DC-Netzversorgung
sinkt um rund 145 Wh. Nominaler Export bleibt null. Im einmaligen Rückhalt-Retry
wird nach einer AC-Abgabe genau das zusätzliche wirtschaftliche DC-Halten
vermieden, das sonst die DC-Kostenprüfung verletzt hätte. Physischer Schutz
und der abschließende DC-Vergleich bleiben aktiv. Ein verbleibender echter
Quellenengpass erhält höchstens eine weitere Rückhaltkorrektur vor AC.
Alle 16 Topologie-Goldens bleiben trotz aktivem 85-%-Ziel unverändert;
insbesondere bleibt nützliche Winter-Vorbereitung erhalten, ohne zusätzliche
Importe oder Exporte. Eine vollständige
AC-Freigabe kann das weiche Ziel geringfügig überschreiten; es ist keine
Regelung der Batterie-Ladeschlussspannung.

`tests/core/test_reserve_soft_ceiling.py` beweist die Kombination ohne doppelte
Puffer, stärkeres Prognoseband, tatsächlichen DC-Rückhalt mit gelockerter
Zielgrenze, fehlende AC-Möglichkeit, sonnenlosen Horizont und den anonymisierten
Morgenfall. Die Topologie-Goldens verwenden das aktive 85-%-Ziel wie die HA-Schicht.

Die folgenden Zahlen und Grenzen dokumentieren die historische Änderung
0.55.0; seit 0.55.2 gilt der prognoseabhängige Zuschlag ohne Kapazitätsgrenze.

## Erwartete PV statt verbindlicher P90-Vorbereitung (0.55.0)

Betreiberauftrag vom 05.10.2026: Nur so viel wie nötig entladen ist ein
verbindliches Ziel. Die normalen Reservebudgets verwenden deshalb dieselbe
erwartete PV-Serie wie die physische Vorwärtssimulation, ergänzt um eine kleine
Unsicherheitsreserve. Der Folgeauftrag desselben Tages verlangt ausdrücklich
einen von der Prognoseunsicherheit abhängigen Zuschlag. Dazu wird ein Viertel
der oberen Abweichung verwendet, nach physischer Peak-Begrenzung. Der gesamte
Zuschlag ist auf 5 % der Batteriekapazität begrenzt, in gespeicherten Wh und
einmal über den gesamten Horizont. Bei 5 kWh sind das höchstens 250 Wh;
schmale Bänder ergeben weniger. Ohne Band liefert der obere Ersatzfaktor die
Abweichung, ebenfalls nur zu einem Viertel und unter derselben Obergrenze.
Diese Werte sind eine begrenzte Planungspolitik, keine behauptete statistische
Kalibrierung. Vollständige untere/obere Werte bleiben für explizite
Offline-Szenarien verfügbar. Der gemeinsame Prognosehorizont und die
intervallweisen Preisgewichte bleiben erhalten.

Die AC-Energiegrenze ist der größere Wert aus Vorbereitungshülle,
zukünftiger DC-Mindestenergie plus Verbrauchspuffer und physischer
Inverteruntergrenze. Der Puffer liegt in gespeicherten Wh vor, bevor vollständige
ON-Schritte geprüft werden. Er bewegt keine Schutzschwelle und fordert keine
gezielte Netzladung. Der Headroom der aktuellen Planfreigabe enthält diesen
Rückhalt bereits; SOC-Hysterese und das volle 35-Sekunden-Fenster bleiben beim
schnellen Überwachen zusätzlich verbindlich. Der separate zusätzliche Live-Pfad
behält seine bestehende, konservative Pufferprüfung.

Der anonymisierte Fall vom 05.10., 18:42 MESZ hält die normale Prognose, die
P10/P90-Serie, den aktuellen Teilslot, die Anlagenphysik und die bekannten
Marktintervalle fest. Ohne optionale Geräte ergibt der alte Plan rund 10,08 %
minimalen SOC und 507 Wh AC am ersten Abend. Die neue Planung gibt am ersten
Abend 0 W frei, behält `weighted_partial` und erreicht mindestens 21,09 %.
Spätere nötige Vorbereitung bleibt aktiv; der nominale Export bleibt null.
Der Unsicherheitszuschlag ist hier auf 250 Wh begrenzt. Der Gesamtimport steigt
gegenüber dem alten Plan um rund 589 Wh, während am Horizontende rund 0,71 kWh mehr
gespeichert bleiben. Dieser Unterschied dokumentiert die beabsichtigte
Energieerhaltung; der Vergleich ist keine gemessene oder tarifliche Ersparnis.

`tests/core/test_reserve_expected_forecast.py` beweist den aufgenommenen Fall,
kleine bandabhängige Zuschläge, physische Peak-Begrenzung, die gemeinsame
Energieobergrenze einschließlich Wirkungsgraden und Rückhalt der
Verbrauchsunsicherheit. Ohne Überlauf trotz kleinem Zuschlag wird kein AC-Budget
erzeugt. Die aktualisierten Reserve- und Marktregressionen
prüfen weiterhin reale PV-Deadlines, stärkere erwartete Sonne, DC-Vorrang,
vollständige ON-Schritte und explizite obere Szenarien. Der HA-Test
`test_expected_forecast_withdraws_permission_despite_high_p90_and_missing_meters`
prüft die Abschaltung einer bestätigten Freigabe nach normalem Forecastwechsel.

Alle 16 Topologie-Goldens wurden neu erzeugt und bleiben unverändert.

## Erweiterung um native DC-Versorgung (0.56.0)

Die frühere Preisunabhängigkeit der wirtschaftlichen DC-Haltung wird durch
[F-DC-PV-MARKET](F-DC-PV-MARKET.md) ersetzt: Vorhandene DC-Entnahmebudgets dürfen
innerhalb ihrer PV-Ladeabschnitte Marktspitzen bevorzugen. Preise schaffen
weiterhin kein Budget. PV-Vorrang und die physische Quellenwahl gelten unabhängig
vom Marktschalter; AC-Kostenprüfung, Reserve und Quellenbesitz bleiben verbindlich.
