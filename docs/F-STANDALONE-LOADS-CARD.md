# Dashboard-Karte für Lasten außerhalb von Kaskaden

## Regeln

1. `custom:battery-manager-loads-card` ist im vorhandenen Frontend-Modul und im
   Kartenauswahldialog registriert. Konfiguration: SOC-Prognosesensor (`entity`),
   optional `title` und `hours` (6–96). Automatische Sensorerkennung und GUI-Editor
   entsprechen der Kaskadenkarte; kein zusätzlicher Ressourcenpfad ist nötig.
2. Angezeigt werden Überschusslasten aus dem `loads`-Attribut des Prognosesensors.
   Kaskadenmitglieder und Endlasten sind ausgeschlossen. Stabile `load_id`-Werte
   erhalten die Zeitraumwahl auch bei neu sortierten Daten. Haushaltsgeräte
   bleiben Bestandteil der allgemeinen Prognosekarte.
3. Je Last: Empfehlung, Verfügbarkeit, geplante Energie heute/morgen/gesamt,
   Planungsleistung mit Herkunft, robuste Leistungsschätzung, zuletzt gelernte
   Leistung und bei Speicherlasten SOC und Ladeziel. Empfehlung und Messwerte
   bleiben sprachlich unterscheidbar. Fehlende Werte werden als „—“ dargestellt.
4. Leistungskurve und kumulierte Energiekurve verwenden die veröffentlichten
   Slotenergien und tatsächlichen geplanten Laufzeiten. Teilstunden, Pausen und
   Tagesgrenzen in der HA-Zeitzone erhalten die Energie. Zeitraumwahl, Hover,
   Tastaturnavigation und responsive Darstellung nutzen die Kaskadenfunktionen.
   Es wird keine zusätzliche SOC-Kurve ohne Backendprognose erfunden.
5. Laufzeitdetails, Ausführungsgründe, Mindestpause, Bestätigungsbedarf,
   abgelehnte Startkandidaten und Warnungen erklären die Planung. Die gemeinsamen
   Betriebs- und Einspeiseberichte bleiben als Anlagenkontext sichtbar.

## Prüfung

`tests/frontend/cascade-card.test.mjs` prüft die registrierte Karte, Ausschlüsse,
Teilslot-Energie, Zeitraumwahl, fehlende Werte und HTML-Escaping.
`tests/ha/test_coordinator.py` übergibt eine tatsächlich veröffentlichte
Sensor-Payload an `tests/frontend/backend-contract.mjs`; die Karte muss die
Backendenergie unverändert wiedergeben. Gemeinsame Diagrammtests sichern
Zeitzonen, Sommerzeit, Scrollposition und Tastaturbedienung ab.
