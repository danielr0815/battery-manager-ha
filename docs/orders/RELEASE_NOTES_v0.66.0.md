# v0.66.0 – SOC-Erwartung bei gemessener 48-V-Netzteillieferung

Die Prognose zeigt eine zusätzliche SOC-Erwartungskurve, wenn mindestens
30 Minuten zusammenhängender, qualifizierter Messwerte zur 48-V-DC-Abgabe
vorliegen und der Plan den gemessenen Versorgungsmodus in mindestens einem
vollständigen Intervall fortsetzt. Die Kurve zeigt den erwarteten SOC bei
fortgesetzter Lieferung.
Die Vorschau ist auf die beobachtete Lieferdauer, höchstens 48 Stunden und
eine durchgehende passende Schaltfolge begrenzt.

Die Sicherheitskurve bleibt die Grundlage für die Planung und für
Schaltfreigaben.

Lokal bestanden 3.562 Python-Tests bei 98,00 % Zeilen-Coverage, darunter
1.105 Core-Tests bei 100 % Coverage. Alle HA-Module erreichten mindestens
95 % Zeilen-Coverage. Auch die statischen Prüfungen, 101 Node-Tests und
49 Browsertests bestanden. Das unabhängige Astra-/High-Review ist abgeschlossen.
