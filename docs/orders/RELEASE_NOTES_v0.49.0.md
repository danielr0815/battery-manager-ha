# v0.49.0 — DC-Vorrang und AC-Entladung bei höherer Last

Die aktive Reserve hält zuerst Energie für den erwarteten DC-Verbrauch zurück.
Zusätzliche AC-Entladung darf keine vermeidbare spätere DC-Netzversorgung
verursachen und schafft nur den zusätzlich nötigen Speicherplatz für PV.

Nötige AC-Entladung bevorzugt Zeiträume mit höherer nutzbarer AC-Restleistung
nach PV. Bei gleicher Leistung wird weiterhin möglichst spät entladen.
Teilstunden werden nach Leistung verglichen. Nachtbetrieb bleibt möglich,
wenn stärkere Lastfenster nicht rechtzeitig vor dem betreffenden PV-Überschuss
liegen oder nicht ausreichen. Es gibt keine starre Nachtsperre und keinen
zusätzlichen festen Nacht-SOC.

Die wirtschaftliche Netzteilhaltung übernimmt keinen SOC-Zielpfad mehr aus
einer Referenz mit maximaler AC-Abgabe. Damit entfällt die beobachtete unnötige
Netzteilphase am Dienstagnachmittag mit anschließender AC-Entladung derselben
zuvor erhaltenen Energie. Technische Schutzgrenzen, manuelle Quellenwahl und
geordnete Quellenübergaben bleiben erhalten.

Im vollständigen gespeicherten Liveplan sinkt der Netzbezug von 1466,98 auf
1350,73 Wh bei unverändertem End-SOC von 89,23 %. Dabei ändern sich auch die
geplanten optionalen Lasten; dies ist kein isolierter Effizienznachweis für
unveränderten Verbrauch. Die anonymisierte Regression prüft zusätzlich dieselben
fest gebuchten Lasten ohne erneute Allokation.

Validierung: 2254 Python-Tests, 65 Frontendtests und 21 Browsertests erfolgreich.
Gesamt-Coverage 97,79 %, Kern 100 %, jedes HA-Modul mindestens 95 %. Ruff, mypy,
Frontend-Lint, Formatierung und Bundle-Abgleich bestanden. Die 16 vorhandenen
Golden-Szenarien wurden neu erzeugt und bleiben unverändert.
