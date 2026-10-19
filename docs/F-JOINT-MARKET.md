# Gemeinsame AC/DC-Marktverteilung (0.62.1)

Betreiberauftrag vom 19.10.2026: Vorhandene Batterieenergie soll die teuersten
bekannten Gelegenheiten über AC und DC gemeinsam bedienen. Ein Tageslimit von
vier oder fünf Stunden soll diese Verteilung nicht beschränken. Höhere Preise
rechtfertigen weder zusätzliche Batterieentnahme noch zusätzliche Netz-kWh.

Dieser Vertrag ersetzt die **finale wirtschaftliche Rangfolge** aus
[F-RESERVE-LOAD-PRIORITY](F-RESERVE-LOAD-PRIORITY.md),
[F-MARKET-AC-PRIORITY](F-MARKET-AC-PRIORITY.md),
[F-DC-PV-MARKET](F-DC-PV-MARKET.md) und
[F-DC-SOURCE-BLOCKS](F-DC-SOURCE-BLOCKS.md). Die konservative Referenz und die
Schutz-/Versorgungsverträge bleiben erforderlich. Ein begrenztes Preisgewicht
oder DC-Vorrang der Referenz ist keine globale Rangfolge des finalen Plans.

## Korrektur 0.62.1: mehrere Empfänger und passende Spender

Der Befund vom 19.10.2026 war ein Suchfehler: Der lokale Empfänger mit dem
kleinsten Import konnte AC und DC zugleich bedienen und damit das Budget oder
Importlimit überschreiten. Seine Ablehnung ließ einen kleineren reinen
DC-Empfänger ungeprüft. Auch ein zu großer günstiger Spender kann einen genau
passenden kleineren Spender verdecken. Schließlich kann ein zulässiger Tausch
**zwei Empfänger gemeinsam** benötigen, obwohl keiner einzeln zu einem
zulässigen Spenderbetrag passt. Die abendliche Aufzeichnung unten belegt diesen
letzten Fall unter den unveränderten Schutzverträgen.

Die Suche prüft die verfügbaren Empfängerzustände getrennt. Preisfamilien
bekommen ihre Empfängervarianten nacheinander, bevor eine andere Familie diese
Variante verdrängen kann. Volle Phasen sowie kürzere Anfangs-, End- und innere
Halbstundenblöcke bekommen eigene Vorschläge. Vier deterministische Spenderpfade
prüfen unterschiedliche zusammenhängende Ausgleiche. Fünfminutenreste dürfen
keine neue kurze PSU-Phase erzeugen; eine Änderung kann sich jedoch an eine
bestehende Phase anschließen. Der vollständige Phasennachweis bleibt maßgeblich.

Zusätzlich gleicht eine begrenzte Maskensuche einen oder zwei reine
DC-Empfängerblöcke mit günstigeren Spenderphasen desselben ursprünglichen
PV-Fensters ab. Skalare Masken beschreiben Zustände, Energiemengen und
Importänderungen; sie sind keine simulierten Trajektorien. Höchstens 256
Empfängerbeschreibungen je Quelle/Fenster, 4.096 Energiesignaturen und insgesamt
65.536 gezählte Konstruktionsschritte begrenzen diesen Teil. Empfängerfamilien
teilen sich den Beschreibungsplatz. Fenster mit teuren erreichbaren Empfängern
kommen zuerst; Fenster ohne positive Spenderentnahme verbrauchen keine
Paarungsarbeit. Ein iterativer Durchlauf vermeidet Rekursionstiefe bei langen
Phasen. Die nachgeschalteten einzelnen Tausche haben außerdem insgesamt
höchstens 4.096 lokale Konstruktionen.

Die Maskensuche hält die drei nach echtem Preisnutzen besten Vorschläge über
Fenster und Quellen hinweg vor. Der Nutzen enthält die vermiedenen Importe
aller tatsächlichen Preisüberdeckungen **abzüglich der Spenderkosten je
Preisstufe**; negative Preise bleiben echte Preise. Wirtschaftliche Vergleiche
verwenden dieselbe Rundung auf sechs Dezimalstellen wie die finale Rangfolge,
damit bloße Summierungsabweichungen keine echte Verbesserung einer niedrigeren
Preisstufe verdrängen. Bei Gleichstand folgen weniger Schaltflanken, weniger
Änderungen und eine feste deterministische Ordnung. Gerundete Energie-/Import-
Signaturen suchen lediglich passende Nachbarn; exakte Flusswerte und der
vollständige physische Nachweis entscheiden über die Freigabe.

Es bleiben höchstens vier vollständige Pläne einschließlich Referenz im
Suchzustand. Die unveränderte Grenze von **32 vollständigen nominalen und
Stresssimulationen zusammen** gilt auch für die Maskenvorschläge. Eine lokale
Ablehnung betrifft genau diesen Vorschlag. `construction_limit` kennzeichnet
eine erreichte Konstruktionsgrenze; `unsearched_alternatives` kennzeichnet noch
nicht geprüfte Kombinationen. `no_improvement` ist keine globale
Unmöglichkeitsaussage. Weder die begrenzte Maskensuche noch die Einzelvarianten
garantieren ein globales Optimum oder die Prüfung jeder theoretischen
Kombination. Unbekannte Preisintervalle bleiben eingefroren.

## Referenz und endgültige Buchung

Die bestehende Reserve- und DC-Planung erzeugt eine konservative physische
Referenz. Lastproben und alternative Kaskaden-/Aux-Entwürfe erhalten keine neue
gemeinsame Suchrunde. Erst wenn Lasten,
Kaskaden und Einspeisung gebucht sind, prüft der finale gemeinsame Durchlauf
gekoppelte Änderungen an Inverterfreigabe und 24-/48-V-Quellen. Die Referenz
bleibt als sichere Alternative erhalten. Die finale Runde benutzt die effektiven
Eingaben und unveränderten Buchungen des tatsächlich akzeptierten Kaskadenplans,
auch wenn ein später berechneter Gegenentwurf verworfen wurde.

Das gemeinsame Budget ist die Summe der Netto-Batterieentnahme der Referenz
über die realen Kontrollschritte. Unvermeidbare DC-Entnahme wird separat
gekennzeichnet; ihre Differenz zum Gesamtbudget ist flexibel. Standby,
Wirkungsgrade, Lade-/Entladegrenzen und physisch verfügbare Quellen bestimmen,
welche Gelegenheit mit diesem Budget überhaupt erreichbar ist. Ein teurer
AC-Empfänger kann zusätzliche spätere DC-Netzversorgung nur dann ersetzen,
wenn der vollständige Tausch die folgenden Grenzen einhält.

## Tatsächlicher Preis und unbekannte Intervalle

Höhere veröffentlichte Preise in EUR/MWh haben strikt Vorrang vor niedrigeren
Preisen; der gedeckelte Bonus der Referenz wird nicht als finaler Tarif benutzt.
Verglichen werden echte Zeitintervalle und ihre Preisüberdeckung, einschließlich
Viertelstunden, negativer Preise und beider Herbststunden. Eine unbekannte
Preisabdeckung wird nicht mit null aufgefüllt oder durch einen künstlichen
Mittelpreis als bekannt ausgegeben. Ein teilweise unbekannter Kontrollschritt
bleibt in seiner Referenzbelegung und spendet oder empfängt kein Tauschbudget.

Die Fünfminutenschritte bleiben die physische Steuerungsauflösung. Preisgrenzen
innerhalb eines solchen Schritts begründen keine kürzeren Schaltbefehle.
Ausgewählte Tagesstunden ergeben sich aus den realen Zeitdauern der akzeptierten
wirtschaftlichen Belegung, einschließlich bereits passender Referenzbelegung.
Sie können über fünf Stunden liegen und sind keine garantierte Laufzeit.

## Vollständiger physischer Nachweis

Ein Vorschlag umfasst Empfänger und alle günstigeren Ausgleichsschritte. Lokale
Flussschätzungen dienen nur der Konstruktion; akzeptiert wird erst nach
sequenzieller Simulation mit den vorhandenen physikalischen Regeln. Gegenüber
derselben gebuchten Referenz gelten:

- kein höherer gesamter Netzbezug und keine höhere Netzeinspeisung über den
  vollständigen Horizont;
- keine höhere Netto-Batterieentnahme in jedem **ursprünglichen festen
  PV-Ladefenster**;
- kein niedrigerer Batterieenergieinhalt an jeder solchen Fenstergrenze;
- keine schlechtere zeitliche DC-Versorgung.

Fenstergrenzen werden nicht aus dem Kandidaten neu erzeugt. Reserveuntergrenze,
Schutz, manuelle Vorgaben, verfügbare Quellen und bestätigte wirtschaftliche
Netzteilphasen bleiben bindend. Ein höherer Gesamtimport wegen schlechterer
AC-Wirkungsgrade wird auch bei extrem hohen Preisen verworfen. Zusätzliche
beiläufige Netzteilladung und eine PSU-/Inverter-Rückspeiseschleife sind verboten.
Die bestehende Glättung der vollständig gebuchten konservativen Referenz bleibt
erhalten. Gemeinsame Kandidaten werden bereits als zusammenhängende Blöcke
konstruiert und vollständig geprüft. Bei gleichem Preisnutzen bevorzugt die
Bewertung weniger Schaltflanken, anschließend weniger Änderungen gegenüber der
Referenz. Nach der gemeinsamen Suche läuft keine weitere Quellen-Glättung.

Die gemeinsame Zuteilung wird nicht durch einen unabhängigen Live-Marktvorgriff
umverteilt. Aktuelle Leistung bleibt auf den akzeptierten aktuellen Schritt und
dessen erlaubte Energie beschränkt. Schutz und gemessener PV-Vorrang behalten
ihre bestehenden Prüfungen. Haushaltsgeräte-Proben und die veröffentlichte
Stressreserve verwenden die akzeptierten zeitlichen Schaltzustände einschließlich
gebuchter Einspeisung; sie finanzieren ihre Diagnose nicht durch eine neue
wirtschaftliche Verteilung.

## Begrenzte Suche und ehrliche Statuswerte

Der gebuchte finale Plan hat insgesamt höchstens **32 zusätzliche vollständige
Simulationsläufe der gemeinsamen Suche**. Nominale Kandidaten, nötige
Unsicherheitsreferenzen und Unsicherheitskandidaten teilen sich diese eine
Grenze; es sind nicht 32 nominale plus 32 Unsicherheitsläufe. Kein Lauf wird für
eine nachgeschaltete gemeinsame Glättung reserviert. Es bleiben höchstens
**vier Pläne einschließlich Referenz** im Suchzustand. Die Grenze beginnt nicht
je Preisintervall, Lastprobe oder Kaskadenentwurf neu. Lokale Vorschläge
sind keine vollständigen Validierungsläufe. Abbruch und Wiederholbarkeit gelten
auch während der Suche.

Das Verfahren garantiert kein globales Tarifoptimum. `search_limit` bedeutet,
dass die Vergleichsgrenze erreicht wurde; eine bessere Verteilung kann weiter
existieren. `no_improvement` betrifft nur tatsächlich verglichene Alternativen.
Ablehnungen unterscheiden insbesondere zusätzlichen Gesamtimport,
fehlende Quelle, feste Phase, Budgetüberschreitung, geschützte Reserve,
verlorene Fensterendenergie und unerlaubte Netzteilladung. Eine nicht bewiesene
Alternative ersetzt die sichere Referenz nicht.

## Additive Diagnose

`ReserveDecision.joint` ist optional und wird als `reserve.market.joint`
veröffentlicht. Alte Eingänge und Aufzeichnungen ohne diese Zusammenfassung
bleiben lesbar; fehlende Werte werden in der Karte als „—“ dargestellt.

| Feld | Bedeutung |
|---|---|
| `joint_budget_wh` | Gesamte Referenz-Netto-Batterieentnahme, einschließlich unvermeidbarer DC-Entnahme |
| `unavoidable_dc_draw_wh` | Unvermeidbarer Anteil unter verfügbaren physischen Quellenalternativen; unbekannt, wenn bei flachen/fehlenden Preisen keine Alternativen geprüft werden |
| `joint_shifted_wh` | Gegenüber der Referenz an andere Schritte verlagerte Batterieenergie |
| `joint_status`, `joint_rejection_reasons` | Ergebnis und konkrete Gründe verworfener Alternativen |
| `joint_validations` | Gemeinsame Anzahl vollständiger Nominal- und Unsicherheitssimulationen innerhalb der Grenze 32 |
| `target_diagnostics` | Höchstens 48 Beobachtungen konkreter Empfängerspannen und Varianten, mit getrennten lokalen und vollständigen Prüfergebnissen |
| `target_diagnostics_truncated` | Weitere Preisfamilien oder Beobachtungen fehlen in der begrenzten Diagnose; die Liste ist nicht vollständig |
| `reference_*`, `actual_*` | Netzbezug, Export, Netto-Batterieentnahme und End-SOC vor/nach dem gemeinsamen Durchlauf |
| `economic_intervals` | Gesamte bekannte akzeptierte wirtschaftliche Belegung, nach tatsächlichem Preis absteigend |
| `economic_daily_hours` | Reale Stunden dieser Belegung je lokalem Tag, ohne feste Tagesobergrenze |

Jedes wirtschaftliche Intervall enthält `start`, `end`, `eur_per_mwh`,
`allocated_battery_wh` und `avoided_total_grid_import_wh` für die gesamte
Zuteilung gegenüber der lokal verfügbaren Alternative mit geringster
Batterieentnahme. `avoided_import_wh` ist davon getrennt die vorzeichenbehaftete
Netzbezugsminderung gegenüber derselben Referenz im Intervall. Eine bereits gut
platzierte Referenz kann wirtschaftliche Stunden mit null Änderungswert besitzen.
Es gibt keine Aussage über persönliche Euro-Ersparnis bei einem Festpreistarif.

Jede Zielbeobachtung nennt den exakten tatsächlich behandelten Anfang und das
Ende, den Preis und die Variante `dc_only`, `ac_dc` oder `paired_dc`. Die
Darstellung initialisiert die acht höchsten bekannten Preisfamilien als
ungeprüft; konkrete nachgewiesene Teilspannen haben beim festen Limit von 48
Einträgen Vorrang vor bloßen Versuchen und ungeprüften Bezeichnungen. Das ist
eine begrenzte Auswahl und keine Zusicherung, dass alle darunter liegenden
Varianten geprüft wurden.

`attempted` zählt beobachtete Vorschläge. `local_rejections` zählt Ablehnungen
bei der lokalen Konstruktion nach konkretem Grund; `proof_rejections` zählt
Ablehnungen des vollständigen nominalen/Stressnachweises. Diese Stufen sind
getrennt. `proved_improvements` zählt physisch nachgewiesene gegenüber der
Referenz bessere Alternativen; es bedeutet **nicht**, dass genau diese
Alternative im endgültigen Plan ausgewählt wurde. `not_searched` markiert eine
Bezeichnung ohne beobachteten Versuch. Eine konkrete lokale Ablehnung wird
nicht aus einem globalen Sammelgrund abgeleitet.

Bei der Paarung werden vollständige Ergebnisse nur den tatsächlichen
Empfängerspannen zugeordnet; lokale verworfene Maskenkombinationen werden nicht
als einzeln vollständig untersuchte Paarvarianten ausgegeben. Deswegen kann
ein ungeprüfter ganzstündiger Eintrag neben einer nachgewiesenen Teilspanne
stehen. Alte Aufzeichnungen ohne diese beiden additiven Felder bleiben
lesbar und werden ohne die damals nicht aufgezeichneten Beobachtungen
verglichen; vorhandene neue Felder und die physikalischen Ergebnisse bleiben
Teil des exakten Vergleichs.
Die Karte erklärt diese Daten auf Deutsch und Englisch, ohne Suchparameter als
Bedienanforderungen auszugeben.

## Prüfnachweise

Die ausführbaren Szenarien liegen in `tests/core/test_joint_dispatch.py` und
`tests/core/test_joint_stress_diagnostic.py`; die einmalige Finalisierung nach
Kaskadenentwürfen prüft `tests/core/test_joint_cascade_finalization.py`.
HA-Veröffentlichung und Replay
prüfen die vorhandenen HA-Vertragstests. `tests/frontend/reports.test.mjs` und
`tests/browser/cards.spec.mjs` prüfen die sichtbare Diagnose. Erfolgreiche
synthetische Tauschfälle sind kein Nachweis für eine konkrete Live-Installation.
Eine historische Aufzeichnung muss mit identischen Eingaben, Wirkungsgraden,
Quellen- und Phasenbedingungen separat gegen die Referenz verglichen werden.

## Historischer Offlinevergleich vom 19.10.2026

Die Aufzeichnung mit Zeitstempel `2026-10-19T07:31:07.476713+02:00` wurde mit
identischen Eingaben gegen 0.61.0 verglichen. Der vollständige Horizont umfasst
65 Prognoseslots: zunächst 0,481256 Stunden, danach 64 volle Stunden, insgesamt
64,481256 Stunden. Die Wiederholung mit eingefrorenem 0.62.0-Code erhält alle
folgenden physischen Ergebnisse exakt unverändert; die Tabelle
enthält ausschließlich aggregierte Vergleichswerte.

| Kennzahl | 0.61.0 | 0.62.0, eingefrorener Vergleich |
|---|---|---|
| Gesamter Netzbezug | 8.305,331891 Wh | 8.305,331891 Wh |
| Gesamte Netzeinspeisung | 0 Wh | 0 Wh |
| Netto-Batterieentnahme | 2.526,410625 Wh | 2.526,410625 Wh |
| End-SOC | 61,166357 % | 61,166357 % |
| Mindest-SOC | 53,333927 % | 53,333927 % |
| AC-Abgabe der Hausbatterie | 0 Wh | 0 Wh |
| Geplante Schaltflanken | 10 | 10 |
| Lokale Laufzeit | 47,97 s | 41,16 s |

Die Laufzeiten stammen aus je einem lokalen Lauf (47,966473 s und 41,161784 s).
Sie sind keine allgemeine Leistungszusage und kein statistischer Benchmark.
Die Wiederholung bestätigte die physischen Vergleichswerte; die Laufzeit kann
mit Umgebung und Auslastung variieren.

Schaltflanken zählen jede Änderung der Inverter-, 24-V- und 48-V-Zustände
zwischen benachbarten physischen Schaltintervallen. Gleichzeitige Änderungen
zweier Zustände zählen zweimal.

Budget und Importwerte betreffen diesen vollständigen Horizont. Verfügbare
Preise decken dagegen nur den 19.10. ab; die neue Diagnose weist für diesen
bekannten Preistag insgesamt **9,16667 wirtschaftliche Stunden** aus. Das ist
die gesamte akzeptierte Batteriezuweisung einschließlich bereits
passender Referenzintervalle. Es sind keine zusätzlich gefundenen AC-Stunden
und kein Hinweis auf ein verstecktes Vier-/Fünfstundenlimit. Der Status lautet
`extra_import`; verworfene Vorschläge nennen `energy_budget` und `extra_import`.
Alle untersuchten Vorschläge scheiterten bereits an analytischen Budget- oder
Importgrenzen: `joint_validations` ist deshalb null. Die konservative
Referenzplanung lief weiterhin vollständig; die Null bezieht sich nur auf die
zusätzlichen gemeinsamen nominalen/Unsicherheitssimulationen.

Ein konkreter Verlustvergleich erklärt, weshalb zusätzliche AC-Abgabe hier
nicht automatisch eine zulässige Verbesserung ist. Bei 100 Wh gespeicherter
Batterieenergie, Batterie-Wirkungsgrad 0,97, DC/DC-Wirkungsgrad 0,93 und
24-V-Netzteil-Wirkungsgrad 0,89 vermeidet DC-Versorgung rund **101,36 Wh
Netzbezug**. Dieselben 100 gespeicherten Wh liefern bei Inverter-Wirkungsgrad
0,95 höchstens **92,15 Wh AC vor Standby**. Ein reiner DC-zu-AC-Tausch dieses
Quantums benötigt damit schon vor Inverter-Standby rund 9,21 Wh zusätzlichen
Gesamt-Netzbezug. Ein hoher Preis hebt diese physische Grenze nicht auf.

Die begrenzte Suche hat in diesem Vergleich unter den verbindlichen
Energie-, Import- und Versorgungsgrenzen keine zusätzliche AC-Zuteilung
gefunden. Dies beweist keine globale Unmöglichkeit anderer gemeinsamer Pläne.
Es ist ein Offlinevergleich einer gespeicherten Aufzeichnung, keine Abnahme
einer installierten Live-Version.

## Historische lokale Verifikation von 0.62.0

Der vorbereitete Stand 0.62.0 besteht die vollständige Python-/HA-Suite mit
**2.966 Tests in 260,05 s** und **97,97 % Gesamt-Coverage**. Der getrennte reine
Kernlauf besteht mit **861 Tests in 55,93 s** und **100 % Kern-Coverage**.
Alle 71 Modul-Gates bestehen: jedes HA-Modul erreicht mindestens 95 %, jedes
Core-Modul 100 %. Die Neuerzeugung der 19 Golden-Szenarien ergibt keinen Diff.

Die Frontendprüfung besteht mit **85 Unit-Tests** und **44 Browsertests**;
Build, Lint, Format und Bundle-Abgleich bestehen ebenfalls. Sieben fokussierte
HA-Übersetzungs-/Konfigurationsprüfungen bestehen. Ruff, die globale
Formatprüfung über 341 Dateien, mypy über 71 Dateien, der Offline-Lockfile-Check,
Versionsgleichheit 0.62.0 und Diff-Prüfung sind erfolgreich. Das abschließende
Astra-Review der Korrekturen enthält keine offenen P1-/P2-Befunde.

Damit ist die Version lokal vorbereitet und geprüft. Sie ist nicht installiert;
die Live-Abnahme folgt erst nach Installation. Der historische Vergleich oben
belegt unveränderte physische Ergebnisse bei 9,16667 bekannten wirtschaftlichen
Stunden am 19.10. und ohne zusätzliche AC-Zuteilung; er verspricht kein globales
Optimum und keine persönliche Tarifersparnis.

## Exakter Abendvergleich für 0.62.1

Die über den lokalen Playwright-MCP gelesene Aufzeichnung mit Eingangszeit
`2026-10-19T20:52:07.232503+02:00` wurde offline gegen den unveränderten
0.62.0-Code und den korrigierten Planer gerechnet. Eingänge, Wirkungsgrade,
Buchungen, Quellen und Phasen bleiben identisch. Die rohe Aufzeichnung bleibt
privat; hier stehen ausschließlich aggregierte Nachweise.

| Kennzahl | 0.62.0 | 0.62.1 |
|---|---|---|
| Gesamter Netzbezug | 7.055,795714399002 Wh | 7.055,795714399002 Wh |
| Gesamte Netzeinspeisung | 0 Wh | 0 Wh |
| Netto-Batterieentnahme | 1.779,293255861493 Wh | 1.779,293255861493 Wh |
| End-/Mindest-SOC | 61,31385283324013 % | 61,31385283324013 % |
| AC-Abgabe der Hausbatterie | 0 Wh | 0 Wh |
| Geplante Schaltflanken | 8 | 8 |
| Zusätzlich verlagerte Batterieenergie | 0 Wh | 61,292170121568184 Wh |
| Vollständige gemeinsame Prüfungen | 0 | 7 einschließlich Stressreferenz/-kandidaten |

Die akzeptierte Änderung schaltet die 24-V-Quelle am 19.10. von **21:25 bis
22:00** und am 20.10. von **07:20 bis 08:00** aus. Der Ausgleich schaltet sie
am 20.10. von **01:30 bis 02:30**, **04:55 bis 05:40** und **06:10 bis 06:30**
ein. Die verbleibende Ausschaltphase 05:40–06:10 dauert 30 Minuten. Der
physische Nachweis besteht nominal und unter der niedrigeren PV-Prognose.

Bei **218,45 EUR/MWh** sinkt der Import um 32,434456928839 Wh, bei
**191,99 EUR/MWh** um 29,691011235955 Wh. Die Ausgleichsimporte liegen sämtlich
bei höchstens 190,92 EUR/MWh. Das sind veröffentlichte Marktpreise und
verschobene Netz-Wh, keine persönliche Euro-Ersparnis bei einem Festpreistarif.
Die frühere Ablehnung eines AC/DC-Quantums war somit keine Aussage über alle
zulässigen reinen DC-Tausche.

Das erste ursprüngliche PV-Fenster endet am 20.10. gegen 10:05. Seine
Referenzentnahme beträgt 595,702183185946 Wh, der lokal unvermeidbare Anteil
473,810670788946 Wh: **121,891512397 Wh** sind innerhalb dieses Fensters
verschiebbar. Die akzeptierte Änderung bleibt vollständig darin. Die größte
numerische Entnahmedifferenz eines ursprünglichen Fensters beträgt nur
2,22e-15 Wh; die größte End-SOC-Abweichung liegt bei 5,69e-14 Prozentpunkten.
Diese Rundungsreste liegen innerhalb der unveränderten physikalischen Toleranz.

Ab dem ursprünglichen Fenster um 15:00 am 20.10. sind dagegen sämtliche
1.183,591072675547 Wh Referenzentnahme unvermeidbar. Dort gibt es kein
zusätzliches lokales Tauschbudget für die teurere Abendphase. Das frühere
Fenster darf dieses spätere Fenster nicht finanzieren. Eine eingeschaltete
24-V-Quelle bedeutet außerdem nicht automatisch denselben Netzbezug: Im
09-Uhr-Slot deckt PV bereits einen großen Teil der PSU-Leistung; der gesamte
Referenzimport dieses Slots beträgt nur rund 7,82 Wh. Die Korrektur verspricht
keine unzulässige spätere Abschaltung und keine zusätzliche AC-Abgabe.

Ein lokaler Ausgangslauf dauerte 8,07 s; ein korrigierter Lauf unter parallelen
Prüfungen 19,09 s. Einzelne Läufe und unterschiedliche Auslastung sind kein
statistischer Benchmark und keine allgemeine Laufzeitzusage. Die harte
Begrenzung der lokalen Konstruktion und der vollständigen Simulationen bleibt
prüfbar.

Auch die historische Aufzeichnung **07:31:07 am 19.10.** mit 65 Slots bleibt
gegenüber isoliertem 0.61.0 physisch unverändert: 8.305,331890585434 Wh Import,
0 Wh Export, 2.526,41062477836 Wh Nettoentnahme, End-SOC 61,16635718313719 %,
Mindest-SOC 53,33392680663723 %, zehn Flanken und keine AC-Abgabe. Die lokale
Wiederholung benötigte 42,21 s für 0.61.0 und 37,64 s für den korrigierten
Planer. Dieser unveränderte Morgenfall wird getrennt vom nachgewiesenen
verbesserten Abendfall geführt.

Diese Vergleiche sind Offline-Nachweise mit identischen aufgezeichneten
Eingängen. Eine Installation oder Live-Schaltänderung erfolgte dabei nicht.
