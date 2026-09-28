# v0.48.0 — Schaltzeiten in der SOC-Prognose

Die SOC-Karte zeigt die geplanten Ein-/Aus-Phasen des Inverters unter der
Prognosekurve. Die 24-V- und 48-V-Netzteile lassen sich über die Checkbox
„24/48-V-Netzteile anzeigen“ zuschalten. Im Karteneditor oder per YAML mit
`show_power_supplies: true` können sie standardmäßig eingeblendet werden.
Farbig bedeutet ein, grau bedeutet aus.

„Geplante Schaltzeiten“ listet die genauen Zeiträume in der Home-Assistant-
Zeitzone auf. Maus und Tastatur erreichen auch Wechsel innerhalb einer Stunde.
Koordinierte Versorgung und aktive Reserve erhalten dazu die tatsächlichen
Fünf-Minuten-Planintervalle; der klassische Planner behält seine Slotauflösung.
Fehlende Zeitdaten bleiben unbekannt. Die Anzeige beschreibt den Plan,
nicht die bestätigte Geräteschaltung.

Das neue Sensorattribut `switching_schedule` wird nicht im Recorder aufgezeichnet.
Energieplanung, Schaltregeln und Golden-Snapshots bleiben unverändert. Neue
Planneraufzeichnungen prüfen die Schaltintervalle mit; alte bleiben lesbar.

Validierung: 2243 Python-Tests, 65 Frontendtests und 21 Browsertests erfolgreich.
Gesamt-Coverage 97,80 %, Kern 100 %, jedes HA-Modul mindestens 95 %. Ruff, mypy,
Frontend-Lint, Formatierung und Bundle-Abgleich bestanden.
