# Stabile Netzteilphasen und gleichwertige Glättung (0.61.0)

## Befund

Am 18.10.2026 enthielt ein exakt reproduzierter Plan nach der Marktverteilung
20 statt vier 24-V-Wechsel. Physikalisch nachgerechnete Glättung entfernte vier
davon bei unverändertem stündlichem Netzbezug, Mindest-/End-SOC und ohne
DC-Unterversorgung. Die bisherigen Regeln aus
[F-DC-SOURCE-BLOCKS](F-DC-SOURCE-BLOCKS.md) verhindern kurze EIN-Inseln nicht.

Am selben Abend erzeugten neue Marktpläne 48-V-Einschaltphasen von ungefähr
2:19 und 2:22 Minuten. Die Spannung blieb unter der Einschaltgrenze; neue
wirtschaftliche Aufträge entzogen die Freigabe. Gegenproben widerlegten eine
alleinige Ursache durch den aktiven Zustand oder 0,01 V Unterschied.
Originaldaten bleiben in der privaten lokalen Untersuchungsablage.

## R1 — Bestätigte wirtschaftliche Phase

Eine bestätigte eigene EIN- oder AUS-Flanke beginnt je Netzteil eine Phase von
30 realen Minuten. Versuchte, unbestätigte und idempotente Befehle verlängern sie
nicht. Eine verspätete Rückmeldung beginnt die Phase erst bei ihrer Beobachtung.
Eine reine Spannungs- oder PV-Abschaltung bei fortbestehender wirtschaftlicher
Anforderung beendet die alte Bindung und startet keine wirtschaftliche AUS-Phase.
Die schnelle Spannungs-/Wolkenrückkehr bleibt dadurch möglich.
Beide Netzteile haben getrennte Phasen; das kurze Schalt-/Wiederholungsintervall
bleibt unabhängig. `SupportParams.dc24_phase_until` und `dc48_phase_until`
übergeben die Restfristen als UTC-Epochensekunden. Alte Aufzeichnungen verwenden
den neutralen Standard null. Ein Schritt mit Beginn vor der Frist bleibt gebunden.

Wirtschaftliche Neustarts warten innerhalb bestätigter AUS-Phasen, soweit das
vorhandene Referenz-Entnahmebudget und die physischen Quellen dies zulassen.
Ohne ausreichendes Budget gilt die Referenzversorgung. Die Bindung vergrößert
weder das Budget noch senkt sie dafür die Endenergie. Der Preisvorrang darf
innerhalb des betroffenen Ladefensters vorübergehend geringer
ausfallen, um eine bereits bestätigte Phase einzuhalten. Das ist eine ausdrücklich
begrenzte Stabilitätspriorität, keine zusätzliche Netzladung und kein erfundener
Geldwert für einen Relaiswechsel. Außerhalb gebundener Fenster bleibt der
Marktwertvergleich unverändert. Eine bestätigte EIN-Phase darf die
Marktverteilung nicht abschalten, solange die Referenz sie weiterhin zulässt.
Schutz, manuelle Anforderungen, Netzverlust, PV-Versorgung, notwendiger
PV-Speicherraum und physische Quellenverfügbarkeit behalten Vorrang. Die Referenz
darf unzulässige Haltung aufgeben. Eine Phase schafft kein zusätzliches Budget
und ist keine Schutz-Latch oder Garantie einer Mindestlaufzeit.

## R2 — Ganze Kombinationen glätten und vollständig prüfen

Nach vollständiger Last- und Einspeisebuchung betrachtet der Kern im finalen Plan
die vollständigen
24-/48-V-Kombinationen gemeinsam. Innerhalb desselben ursprünglichen Prognoseslots,
derselben tatsächlichen Preisveröffentlichung, desselben Gewichts und gleich
langer benachbarter Schritte bleibt die Dauer jeder Kombination unverändert.
Teilintervalle, Preisgrenzen einschließlich unbekannter Preise, Lücken und
laufende Phasen werden nicht übersprungen. Je Gruppe sind höchstens zwölf
Schritte und endlich viele Blockanordnungen und Rotationen zulässig.

Eine dynamische Programmierung bevorzugt weniger einzelne Netzteilflanken,
einschließlich des Übergangs vom bestätigten Anfangszustand. Bei Gleichstand
gewinnen weniger Änderungen, danach eine feste deterministische Reihenfolge.
EIN- und AUS-Abschnitte werden gemeinsam bewertet.

Der Kandidat wird einmal vollständig mit festen AC-Freigaben nachsimuliert und
gegen akzeptierten Marktplan und Referenz geprüft. Netto-Entnahme, Mindest-/End-SOC,
DC-Versorgung, Import, Export, AC-Abgabe und entfallende beiläufige 48-V-Ladung
behalten ihre bisherigen Schranken. Gleichwertigkeit mit weniger Flanken zählt
als Verbesserung. Ungültige Glättung behält den bereits validierten Marktplan.

Hypothetische Last-, Stress- und Geräteproben behalten die wirtschaftliche
Phasenbindung sowie sämtliche Energie- und Schutzprüfungen. Die zusätzliche reine
Schaltglättung und ihre Nachsimulation laufen erst für das gebuchte Ergebnis.
Der Probe-Cache trennt diese beiden Modi. So löst eine neue Glättungsregel nicht
hundertfach zusätzliche vollständige Horizontprüfungen aus. Preiszeiten werden
einmal vorbereitet; reine Blockgeometrie wird mit festen kleinen Grenzen
wiederverwendet. Der Kern bleibt deterministisch und ohne HA-Abhängigkeit.

Die Glättung verschiebt keine Laufzeit über eine Preisgrenze. Der bestehende
Marktverteiler kann weiterhin Energie in unbekannte Folgepreise verlegen;
daraus darf keine nachgewiesene Gesamtkostenersparnis abgeleitet werden.

## R3 — Wiederherstellung und überholte Aufträge

`source_phases` verwendet Payloadschema 1 im Runtime-Store: Stellung, Bestätigungszeit
und Quellenbindung (PSU sowie bei 24 V DC/DC). Alte Stores beginnen ohne Bindung.
Unbekannte Schemas, beschädigte Einträge, naive/zukünftige/abgelaufene Zeiten und
geänderte Aktoren werden ignoriert. Die Frist läuft über Neustarts weiter.
Unbekannte oder widersprechende aktuelle Stellungen binden keinen alten Zustand.

Eine während der CPU-Planung neu bestätigte Phase darf ein älterer Entwurf nicht
wirtschaftlich umkehren. Der Quellenbesitzer blockiert AC und fordert eine frische
Planung an; notwendige Schutzaktionen behalten Vorrang. Bestehende Prüfungen nach
Bestätigungen, Sperrwartezeiten und 24-V-Überlappung bleiben wirksam.

## R4 — Diagnose und Nachweise

Schaltbefehle enthalten Quellenkontext und bestätigte Phasen mit Zeit und Frist.
Die Ausführung erklärt einen überholten Entwurf mit `source_phase_changed`.

`tests/core/test_source_stability.py` prüft beide Quellen, Inseln, gleichwertige
Glättung, Preisgrenzen trotz gleicher Gewichte, unbekannte Preise, Teilintervalle,
Lücken, DST, rollende Planung, Fristablauf und Schutz/PV. Ein anonymes Gesamtszenario
reduziert neun auf fünf Flanken bei gleichem stündlichem Netzbezug und End-SOC.
`tests/ha/test_source_phases.py` prüft Bestätigungen, unabhängige Fristen,
Runtime-Wiederherstellung, beschädigte Evidenz, manuelle Freigabe und überholte
Aufträge. Private Originalpläne werden zusätzlich offline geprüft.

Lokaler Einzelvergleich derselben 51-Slot-Aufzeichnung: 728 → 732 interne
Policyläufe und 997.677 → 1.000.244 tatsächliche Physikschritte; gemessene Laufzeit
39,76 → 47,35 Sekunden. Das ist eine lokale Gegenprobe, keine garantierte
HA-Laufzeit. Die 19 Topologie-Goldens bleiben unverändert.

Die Regeln ersetzen keine Live-Abnahme nach Installation. Keine neue
Laufzeitabhängigkeit und keine automatische Installation.
