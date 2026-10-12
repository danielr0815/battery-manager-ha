# Technische Reviewbefunde vom Oktober 2026

Historischer Befund vom 02.10.2026: v0.53.0, Code `a211549`.
Einordnung aktualisiert am 12.10.2026 für v0.57.0. Die reproduzierten Fehler
wurden anschließend im [Optimierungsplan](../OPTIMIZATION-PLAN-2026-10.md)
bearbeitet. Seine Umsetzung und die [aktuellen Verträge](../CURRENT_CONTRACTS.md)
sind für den heutigen Stand maßgeblich.

## Schutz und Korrektheit

Synthetische Gegenproben zeigten zusätzliche unversorgte DC-Energie bei
Lastbuchung, Geräte-Advisor und automatischer Einspeisung, obwohl Netzimport
und SOC-Grenzen scheinbar bestanden. Der Vergleich benötigt positive zusätzliche
Defizite je identischem physischem Intervall gegenüber Ausgangs- und zuletzt
akzeptiertem Plan. Gesamtsummen oder Präfixsummen können eine zeitliche
Verschiebung der Ausfälle verdecken. Diese Schutzverträge wurden mit O01 umgesetzt.

Der G4-Guard erfasste eine wegen Mindestlaufzeit tatsächlich weiter laufende
Last nicht zuverlässig, wenn ihre neue Buchung abgelehnt war. O02 schützt die
reale laufende Leistung. Die Gegenproben belegen Codefehler; sie beweisen keinen
entsprechenden historischen Versorgungsausfall auf der Anlage.

Weitere umgesetzte Pakete betreffen nebenläufigen Archivstart, Messfrische und
Einheiten, Geräteprovenienz, Quellenqualität, exakte Archivchunks, die Trennung
von Plan und Rückmeldung, Bedienung, physische Stundendauer und DST sowie
Marktpräferenz und wiederverwendbare Berechnungen.

Historische Zahlen: [Kerngegenproben](2026-10-02-core-review-proofs.json),
[vertiefte Gegenproben](2026-10-02-deep-review-proofs.json).
Aktuelle Regressionen sind in der [Testmatrix](../TEST_MATRIX.md) und im
[Umsetzungsnachweis](2026-10-03-implementation-validation.md) zugeordnet.

## Reservealternativen bleiben Forschung

Die damalige 500-Wh-Rückhalteprobe senkte nominal den prognostizierten Netzbezug
um 573,98 Wh, verbrauchte jedoch 671,71 Wh zusätzliche Endspeicherenergie.
Mit Entlade- und Inverterwirkungsgrad von 0,97 und 0,95 war sie
endenergiebereinigt 45 Wh schlechter. Bei pessimistischer PV entstanden
37,42 Wh zusätzlicher DC-Netzbezug. Das ist kein belegter Sparfix.

Ein Vergleich muss gleiche Eingaben, Endenergie, DC-Versorgung, schwächere PV
und die Fortsetzung nach dem Horizont berücksichtigen. Rückhalt wirkt nicht
monoton; eine einfache Bisektion ist kein nachgewiesener Optimierer.
O13 blieb ein Offline-Werkzeug ohne produktive Auswahl. O14 benötigt belastbare
Messdaten vor Änderungen an Unsicherheits-, Gerätephasen- oder Busmodellen.

## Grenzen der historischen Daten

Das feste Fenster umfasste 25.09. bis 02.10.2026, jeweils 17:07:42
Europe/Berlin. Erhalten waren nur etwa 22,18 Stunden Einzelentscheidungen:
6.938 Ereignisse und 2.219 Pläne. Fehlende ältere Entscheidungen lassen sich
nicht nachträglich erzeugen. Die Rohverläufe und ursprünglichen Langberichte
bleiben im lokalen komprimierten Archiv; die Gegenproben sind historische
Ergebnisprotokolle und kein Ersatz für ausführbare Regressionstests.
