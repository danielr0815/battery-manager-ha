# Programmspezifisches Lernen für Haushaltsgeräte

## Regeln

1. Optionale Eingänge `program_entity` (aktives Programm) und
   `selected_program_entity` (Auswahl für die Vorschau) akzeptieren Sensor,
   Select oder Input Select. Entfernen im Anpassungsdialog bleibt möglich.
   Es gibt keine automatische Ableitung aus Entity-Namen, Statusphasen,
   Restlaufzeit oder Leistungsform.
2. Nur das aktive Programm kennzeichnet einen gemessenen Durchlauf. Der erste
   gültige Wert während des Laufs wird festgehalten; spätere unbekannte Werte
   oder ein sofortiges Zurücksetzen am Ende verlieren die Zuordnung nicht.
   Ein erst später eintreffender Name wird übernommen. Unterschiedliche gültige
   Namen während desselben Laufs verwerfen dessen Lernmessung vollständig.
3. Die bestehenden Vollständigkeitsregeln bleiben erhalten: beobachteter Start,
   durchgängige Messquelle, höchstens zehn Minuten Messlücke, kein Fehler oder
   Abbruch. Pausen zählen zur Durchlaufdauer. Gemessene Energie und vergangene
   Dauer bilden ein Wertepaar; nur vollständige, gemessene Durchläufe lernen.
4. Je Gerät und Programm gilt der Median der letzten 20 Wertepaarungen.
   Höchstens 32 zuletzt gelernte Programme bleiben gespeichert. Energie muss
   positiv und höchstens 10 kWh sein, Dauer positiv und höchstens 24 Stunden.
   Geräteweite Energieproben werden weiterhin gepflegt; unbekannte Programme
   verwenden diese beziehungsweise die konfigurierte Energie und Dauer.
5. Programmprofile überleben Neustarts. Aktive Messungen und die Programmbindung
   werden nicht wiederhergestellt: Ein Neustart darf keinen unbeobachteten Lauf
   als vollständig lernen. Ein aktuell gemeldetes Programm kann dennoch ein
   vorhandenes Profil für die laufende Prognose auswählen.
6. Bei laufenden Geräten hat die gültige Gesamtlaufzeit Vorrang vor gelernter
   Programmdauer. Die gültige Restlaufzeit hat weiterhin Vorrang vor der
   berechneten Restzeit. Ohne Lauf dient die optionale Programmauswahl nur als
   Vorschau. Bei gültiger Auswahl wird die möglicherweise veraltete Gesamtdauer
   des vorigen Programms ignoriert. Die Auswahl kennzeichnet niemals Lernproben.

## Einrichtung und Grenzen

Beim Anpassen des Haushaltsgeräts das aktive Programm der Geräteintegration
zuordnen. Beim Geschirrspüler kann zusätzlich die Programmauswahl zur Vorschau
zugeordnet werden. Hat die Waschmaschinenintegration keine Programmkennung,
bleibt dieses Feld leer; Laufphasen wie „Waschen“ sind keine Programme.
Programmoptionen, Temperatur und Beladung bilden noch keine eigenen Profile.
Ein zukünftiger manueller Programmwechsel kann die Vorschau verändern.

## Prüfung

`tests/ha/test_appliance_learning.py` prüft Trennung, spätes Eintreffen,
Zurücksetzen am Ende, widersprüchliche Kennungen, Vorschau, Gerätefallback,
Speichergrenzen, beschädigte Persistenz und Neustartverhalten.
`tests/ha/test_config_flow.py` prüft Anlegen und Entfernen der optionalen Eingänge.
Die Fälle folgen den zuvor beobachteten Geräteverträgen; private Rohhistorien
werden nicht als öffentliche Testdaten abgelegt.
