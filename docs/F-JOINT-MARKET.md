# Gemeinsame AC/DC-Marktverteilung (0.62.0)

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

## Abschließende lokale Verifikation

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
