# PV-Vorrang und DC-Marktverschiebung ab 0.56.0

Betreiberauftrag vom 07.10.2026: Auch 24/48-V-Verbraucher sollen vorhandene
Batterieenergie bevorzugt während Marktspitzen nutzen. PV-Überschuss soll die
DC-Versorgung übernehmen. Die Rückfragen begrenzen dies ausdrücklich auf das
bestehende Entnahmebudget; Teilüberschuss rechtfertigt keine zusätzliche Entladung.
Manuelle Netzteilanforderungen und Batterieschutz bleiben vorrangig.

## R1 — Physische PV-Quellenwahl

Jeder Fünfminutenschritt betrachtet verfügbare Quellenkombinationen mit derselben
AC-/DC-Physik am gemeinsamen saldierenden Netzanschluss. Tatsächliche
Netzteil-AC-Aufnahme wird aus verbleibender PV gedeckt, bevor der Charger und
Export bilanziert werden. `psu_grid_import_wh` hält den wirklichen Netzteilbezug
fest; alte Aufzeichnungen ohne das Feld behalten ihre bisherige Interpretation.
Nach AC-Verbrauch und Zusatzlasten verbleibende PV wird mit
Ladeleistung, Standby, Wirkungsgraden und DC/DC-Grenzen geprüft. Vollständige
Deckung ohne Netto-Batterieentnahme schaltet automatische Netzteile aus, auch an
der Batterieobergrenze. Die interne Entnahme/Ladung innerhalb eines Schritts
wird netto betrachtet; sie ist kein zusätzliches Energieangebot.

Bei Teilüberschuss darf eine Kombination weder mehr Netto-Batterieenergie als
die Referenz verwenden noch SOC, DC-Versorgung oder Import/Export verschlechtern.
Der geringste Netzbezug gewinnt. Gleichstände behalten die bisherige Quelle;
bei vollständiger PV-Deckung hat der Rückfall auf beide ausgeschalteten
Netzteile Vorrang. Schutz- und manuelle Quellen werden nicht abgewählt.

## R2 — Budget und Marktzeitpunkt

Referenz ist die bisherige Reserveplanung mit R1 und den bestehenden AC-/DC-
Kostenkorrekturen. Deren Netto-DC-Entnahme bildet die Obergrenze. Ein sortierter,
begrenzter Kandidat verschiebt vollständige Fünfminutenschritte nach vermiedenem
DC-Netzbezug je Batterie-Wh multipliziert mit dem vorhandenen Marktgewicht.
Dominierte Kombinationen entfallen. Jede weitere Wahl verwendet den marginalen
Nutzen gegenüber der bereits gewählten Kombination; nichtpositive Einsparung
verbraucht kein Budget.
Gleichstände bevorzugen spätere Schritte. Unbepreiste Schritte haben Gewicht
eins; flache oder ganz fehlende Preise verschieben nichts. Negative Preise
bleiben gültig, ohne Netzladung zu begründen. Der vorhandene Marktschalter
entfernt die Preisintervalle und schaltet damit auch DC-Marktverschiebung ab.

Abschnitte enden an netto ladenden PV-Schritten. Entnahmebudget wird nicht über
diese Grenze vorgezogen. Bestehende AC-Schritte bleiben fest. Jeder Abschnitt
wird mit den vorgeschlagenen Quellen vollständig nachsimuliert. Voraussetzung:
keine zusätzliche Netto-Entnahme, kein niedrigerer Endenergieinhalt, kein höherer
Import oder Export, keine neue zeitliche DC-Unterversorgung, identische AC-Abgabe
und ein Vorteil beim gewichteten DC-Netzbezug. Zusätzliche frühere Entnahme
muss den zukünftigen DC-Mindestbedarf einschließlich Verbrauchspuffer einhalten.
Scheitert ein Abschnitt, behält er die Referenz; andere Abschnitte können dennoch
verbessert werden. Das ist eine konservative Heuristik, kein globales Tarifoptimum.
Der gemeinsame Simulationspfad verwendet diese Politik auch für Lastkandidaten,
Vergleichsrechnungen und Replay. DC-Verschiebung lockert keine Live-AC-Grenze.

## R3 — Gemessener PV-Vorrang

Der vorhandene Fünfsekundentakt prüft frische, vorzeichenbehaftete AC-Messungen
und einen positiven PV-Messwert. Ohne direkte AC-Messer verwendet er PV minus
Hausverbrauch. Netzteilabgabe wird nie als PV-Überschuss hinzugerechnet. Unbekannte
Messungen sind keine Freigabe. Gemessene DC-Leistung hat Vorrang; sonst gilt
prognostizierter DC-Verbrauch plus bestehender Verbrauchspuffer verteilt über
eine feste Stunde (Prognose-Watt + Puffer-Wh / 1 h). Der Plan gilt höchstens fünf Minuten beziehungsweise bis
zur nächsten vorhandenen Plan-/Preisgrenze. Die bestehende 30-Sekunden-Frische
für SOC, Netz und Leistungsquellen bleibt verbindlich.

Start erfordert 10% zusätzliche PV-Leistung; Fortsetzung volle physische Deckung.
Nur automatische Haltung oberhalb der Erholungsschwellen wird aufgehoben.
Der koordinierte Quellenbesitzer bestätigt sämtliche Umschaltungen; der Timer
schaltet keine Netzteile direkt. AC erhält dadurch keine neue Freigabe.
Wolken, veraltete Daten oder entfallene Freigaben führen zurück zum gültigen Plan.
Schaltfristen oder fehlende Bestätigung lassen diese Rückkehr ausstehend und
werden erneut versucht. Nach jeder verzögerten Bestätigung und unmittelbar vor Quellenabschaltung,
auch innerhalb der 24-V-Überlappung, werden PV-, SOC-, Netz- und Planbedingungen
erneut geprüft. Planrevision und Ablaufzeit verhindern veraltete Aufträge nach
Refresh oder Sperrenwartezeit. Batterieschutz umgeht wirtschaftliche Schaltfristen. Bei abgelaufenem Plan erfolgt Schutzsteuerung und eine neue Plananforderung.

## R4 — Diagnose und Kompatibilität

`reserve.decision_reason` ergänzt `dc_pv_supply` und `dc_market_supply`;
`dc_reserve_holding` erklärt SOC-Erhaltung ohne aktuelle Freigabe.
`dc_budget_wh` bezeichnet die Referenz-Netto-DC-Entnahme im Prognosehorizont;
`dc_shifted_wh` die an anderen Schritten platzierte Batterieenergie.
`coordinated_support.pv_priority` zeigt eine gemessene Quellenfreigabe;
`pv_permission_expired` eine während der Übergabe entfallene Freigabe.
Alte Attribute und Replay-Eingänge bleiben lesbar. Es gibt keine Pflichtoption,
keine neue Runtime-Dependency und keine automatische Live-Installation.

## Nachweise

`tests/core/test_dc_pv_market.py` prüft PV-Voll-/Teilversorgung, manuelle und
physische Grenzen, positive/negative/fehlende Preise sowie Budget und Ladefenster.
`tests/ha/test_live_dc_pv.py` prüft echte Quellenreihenfolge, Wolken, Fristen,
veraltete Messwerte, Fehler, verzögerte Antworten und abgelaufene Pläne.
Frontend- und Browsertests prüfen beide Sprachen und die veröffentlichten Wh.

## Offline-Vergleich und Laufzeit

Im physikalischen Gegenbeispiel mit zwei Stunden je 100 Wh DC-Bedarf und einer
anschließenden PV-Ladung verschiebt der neue Plan 100 Wh aus der zweiten Stunde
in die erste Preisspitze. Netzbezug und End-SOC bleiben identisch. Ein gesonderter
48-V-Fall mit nachgewiesener Netzteilspannung verschiebt 10 Wh; unbekannte
48-V-Abgabe erhält kein solches Budget. Viertelstundenpreise und die beiden
Herbststunden werden nach realen Intervallen geprüft.

Die gespeicherte Oktober-Fixture enthält keinen zulässigen DC-Tausch innerhalb
ihrer Ladeabschnitte. Es wird keine Peak-Wirkung durch zusätzliche Entladung
erzwungen. Vollständige Planner-Replays, Golden-Änderungen und aktuelle Gates
stehen im [lokalen Releasebericht](RELEASE_CANDIDATE_0.56.0.md).

## Lokale Abnahme

Der aktuelle 0.56.0-Kandidat besteht mit 2.692 Python-/HA-Tests, 97,89 %
Gesamt-Coverage, Kern 100 % und allen 64 Modulgates. 80 Frontendtests und 41
Browsertests bestehen ebenso wie Ruff, mypy und Bundle-Abgleich. Vier beabsichtigte
Golden-Änderungen folgen aus der korrigierten Anschlussbilanz; einzelne Gründe
und die wiederholten vollständigen Laufzeit-/Energievergleiche stehen im
[Releasebericht](RELEASE_CANDIDATE_0.56.0.md). Installation und Live-Abnahme erfolgen
nach gesondertem Deployment über HACS.
