# v0.64.1 – Recorder entlasten und SOC-Herkunft sichtbar machen

Die Prognose-Entity schreibt umfangreiche Reserve-, Support- und Kalibrierdiagnosen
nicht mehr in die Recorder-Attribute. Damit wird die beobachtete Überschreitung
der 16-KiB-Grenze verhindert; Live-Karten und Diagnose-Download behalten die Daten.

Die Lastenkarte zeigt neben dem SOC zu Planbeginn dessen Herkunft aus der HA-Entity,
dem Cache oder einer unbekannten Quelle. Meldezeitpunkt und Alter beziehen sich
auf den erfassten Planstand. Fehlende oder ungültige Zeitangaben bleiben unbekannt;
eine HA-Meldung bestätigt keine frische physische Gerätemessung. Die Hinweise sind
auf Deutsch und Englisch verfügbar.

Ladeplanung, siebentägige Cache-Gültigkeit, SOC-Schutzverriegelungen und
Schaltverhalten bleiben unverändert.

Validiert mit 1.022 Core-Tests bei 100 % Coverage, 3.258 Gesamttests bei
98,05218 % Zeilen-Coverage und mindestens 95 % je HA-Modul. Separater Branch-Lauf,
Ruff, Format und mypy sowie Frontend-Bundle, Lint, Format, 91 Node-Tests und
47 Browsertests bestehen. Unabhängiges Sol-/Medium-Review ohne Befunde.
