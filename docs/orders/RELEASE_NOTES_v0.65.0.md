# v0.65.0 – SOC-Koordinaten nachführen und Netzteilbelege erklären

Der rollierende SOC-/Energieabgleich erkennt eine obere BMS-Nachkalibrierung als
Nachführung der SOC-Koordinate. Während dieser Nachführung und ihrer beobachteten
Stabilisierung bleiben neue optionale batteriegestützte Entnahmen gesperrt.
Versorgung, Schutz, manuelle Vorgaben und unabhängig nachgewiesene direkte
PV-Nutzung bleiben erhalten. Nach der Stabilisierung wird neu geplant.

Natürliche Spannungs-/SOC-Lernpunkte unterschiedlicher Koordinatenstände werden
getrennt. Neustarts überbrücken weder Energie- noch Stabilitätslücken. Begrenzte
private Diagnosebelege erklären den Abgleich und Ablehnungen im Lernband.

Optionale Netzteilmessungen liefern quellengebundene Momentanbelege für
bestätigten Schaltzustand, AC-Aufnahme und DC-Abgabe. Die Karten erklären
konservative SOC-Prognosen und fehlende Lieferbelege. Ein momentaner Messwert
begründet keine zusätzliche mehrtägige Lieferzusage. Der BMS-SOC und die
konfigurierte Kapazität werden nicht verändert.

Validiert mit 1.085 Core-Tests bei 100 % Coverage, 3.500 Gesamttests bei
97,98 % Zeilen-Coverage und mindestens 95 % je HA-Modul. Separater Branch-Lauf,
Ruff, Format und mypy sowie Frontend-Bundle, Lint, Format, 101 Node-Tests und
48 lokale Browsertests bestehen. Fünf historische Replays stimmen exakt überein.
Das unabhängige kritische Astra-/High-Review ist ohne offene P1-/P2-Befunde
abgeschlossen. Die Veröffentlichung führt keine Änderungen am Live-System aus.
