# Gebündelte Netzteilzeiten und Preisvorrang (0.58.0)

Ab 0.61.0 ergänzt [F-DC-SOURCE-STABILITY](F-DC-SOURCE-STABILITY.md) diese
Präferenz um bestätigte wirtschaftliche Phasen und gemeinsame EIN-/AUS-Glättung.

## Befund

Am 13.10.2026 zeigte der Betreiber eine SOC-Prognose mit zahlreichen kurzen
24-/48-V-Netzteilintervallen und bat um Bündelung und Abschaltung in teuren
Stromstunden. Der Screenshot belegt die Fragmentierung; er ist kein vollständiger
Replay-Eingang und erlaubt keine bezifferte Einsparung.

Die Marktverteilung wählte einzelne Fünf-Minuten-Schritte nach gewichteter
Importersparnis je Batterie-Wh. Kleine Wirkungsgradunterschiede konnten so
verstreute Schritte bevorzugen. Die Einschalthysterese der wirtschaftlichen
Haltung wurde außerdem nur im ersten Prognoseschritt angewendet.

## Regeln

### R1 — Einschalthysterese im gesamten Prognoseverlauf

Jeder neue wirtschaftliche Netzteilstart braucht den konfigurierten Abstand
zur DC-Vorbereitungsgrenze. Bereits laufende Haltung benutzt die ursprüngliche
Grenze. Schutz, manuelle Vorgaben und PV-Rückkehr behalten Vorrang.
Alte Aufzeichnungen mit neutralem Startabstand null bleiben kompatibel.

### R2 — Preisband zuerst, zusammenhängende Quellenwahl danach

Innerhalb jedes bestehenden PV-Ladefensters erhält das höhere vorhandene
Marktgewicht zuerst das DC-Entnahmebudget. Innerhalb eines Gewichts entscheidet
die marginale Importersparnis je Batterie-Wh, bei Gleichstand der spätere Schritt.
Die gemeinsame Preisgewichtung bleibt unverändert: Sie bevorzugt die vier
teuersten realen Stunden pro lokalem Tag und begrenzt den Preisbonus. Gleiche
Gewichte bedeuten daher nicht zwingend identische Preise.

Eine ausgewählte Quellenkombination wird zuerst rückwärts, dann vorwärts auf
direkte Nachbarschritte gleichen Preisgewichts ausgedehnt, bis insgesamt
30 reale Minuten erreicht sind. Jeder weitere Schritt muss dieselbe Kombination
als zulässige Alternative besitzen, zusätzliche Batterieenergie beanspruchen
und positiven Netzteil-Netzbezug vermeiden. Ein Block kann beide Netzteile
oder nur eines abschalten.

Eine Preisbandgrenze, Prognoselücke, fehlende Alternative, bereits ausgewählte Abschaltung
oder unzureichendes Restbudget beendet die Ausdehnung. Anschließend wird das
verbleibende Budget weiter verteilt. Die Nachbarsuche ist zeitlich begrenzt;
geänderte Schritte werden im bestehenden Heap neu bewertet. Es gibt keine
zusätzlichen vollständigen Simulationsversuche.

### R3 — Unveränderte physische Zulässigkeit

Die 30 Minuten sind eine Bündelungspräferenz, keine erzwungene Mindestlaufzeit.
Kurze Restbudgets können kürzere Fenster ergeben. Schutz und manuelle Quellen
dürfen nicht verschoben werden; PV-Rückkehr bleibt unabhängig.

Entfallende beiläufige 48-V-Ladung wird wie bisher zusätzlich budgetiert.
Die vollständige physische Nachsimulation prüft weiterhin DC-Mindest-SOC,
Versorgung, Gesamtentnahme, End-SOC, Import, Export und unveränderte AC-Abgabe.
Scheitert eine Fensterprüfung, gilt dessen Referenzplanung. Die Bündelung
rechtfertigt keinen zusätzlichen Netzbezug und keine zusätzliche Entnahme.

## Abgrenzung

Ohne wirksame Preisunterschiede gibt es weiterhin keine Marktverschiebung.
Die Hysterese gilt trotzdem. Es gibt keinen garantierten halbstündigen Betrieb,
keine zusätzliche Netzladung für spätere Preisspitzen und keine globale
Kostenoptimierung. Energiemangel kann auch in teuren Stunden Netzteilbetrieb
erfordern. Die Regel ist eine begrenzte Heuristik innerhalb der bestehenden
DC/PV/Markt-Verträge.

## Tests

`tests/core/test_dc_source_blocks.py` prüft zusammenhängende Ausschaltung bei
alternierenden Wirkungsgradvorteilen, Preisvorrang vor größerer Importersparnis
je Wh, verkürzte Restbudgets, reale Schrittzeiten, Grenzen durch Quellenzwang,
Preiswechsel und bereits ausgewählte Schritte sowie einen späteren
Prognoseneustart mit Einschalthysterese. Die bestehende
`tests/core/test_dc_pv_market.py` prüft weiterhin die physische Zulässigkeit
einschließlich entfallender 48-V-Ladung und abgewiesener Regressionen.
