# Marktorientierter Inverterbetrieb (0.53.0)

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
