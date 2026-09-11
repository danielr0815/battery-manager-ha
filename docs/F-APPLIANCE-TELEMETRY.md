# Optionale Messwerte für Haushaltsgeräte

## Befund

Geräteläufe verwendeten ausschließlich konfigurierte Energie und Dauer.
Unabhängige Verbrauchs- und Programmlaufzeitsensoren wurden nicht genutzt.

## Regeln

1. Jedes Haushaltsgerät kann unter „Haushaltsgerät anpassen“ einen Leistungssensor
   (W/kW), Energiezähler (Wh/kWh), Gesamtlaufzeit- und Restlaufzeitsensor erhalten.
   Alle vier Eingänge sind optional und wieder entfernbar.
2. Laufzeiten unterstützen s/min/h sowie HH:MM oder HH:MM:SS. Werte ohne Einheit
   werden als Minuten gelesen. Restlaufzeiten unterstützen außerdem einen
   HA-Timestamp-Sensor mit dem Programmende. Ungültige Werte fallen auf die
   konfigurierte Dauer und die bisherige Laufzeiterkennung zurück.
3. `sensor.frontlader_waschmaschine_total_time` gehört zur Gesamtlaufzeit;
   `sensor.frontlader_waschmaschine_remaining_time` beziehungsweise
   `sensor.kuche_geschirrspuhler_remaining_program_time` zur Restlaufzeit.
   Die tatsächlichen Entity-IDs werden im Dialog ausgewählt.
   Laufzeiten beweisen keinen laufenden Waschgang: Der Geschirrspüler meldet
   selbst im Zustand „ready“ noch 4,5 Stunden. Deshalb führt der Betriebsstatus
   die Erkennung. Zeit-Sensoren werden nicht als Leistungssignal interpretiert.
   Erkannte Pausen halten einen begonnenen Lauf; Fehler/Abbrüche werden nicht
   als vollständige Lernzyklen übernommen.
4. Eine gültige Gesamtlaufzeit ersetzt die manuelle Dauer auch für zukünftige
   Startfenster. Gemeldete Restzeit ersetzt die berechnete Restzeit. Der geschätzte Restverbrauch
   entspricht dem verbleibenden Anteil an der Gesamtenergie, maximal einem Lauf.
5. Vollständig beobachtete Läufe lernen den Median der letzten 20 Energieverbräuche
   pro Gerät. Zählerdifferenzen haben Vorrang; alternativ wird Leistung über die
   Zeit integriert (letzter Wert gilt bis zur nächsten Beobachtung). Der Median
   ersetzt die konfigurierte Energie sowohl für laufende Programme als auch für
   künftige Startfenster. Ohne Lernwerte gilt weiterhin die manuelle Energie.
6. Lernen beginnt erst nach beobachtetem Stillstand und anschließendem Start.
   Messlücken über zehn Minuten, Erkennungsausfälle und unvollständige Messreihen
   werden nicht gelernt. Ein Zählerrücksprung verwirft die Zählermessung dieses
   Laufs; eine vollständige Leistungsmessung kann sie ersetzen. Nur positive
   Verbräuche bis 10 kWh werden übernommen, passend zum Konfigurationsbereich.
7. Abgeschlossene Lernwerte werden gespeichert. Aktive Messungen werden nach
   Neustart verworfen, damit fehlende Messintervalle keinen zu kleinen Verbrauch
   lehren. Bereits laufende Geräte werden weiterhin prognostiziert.
8. Ein separater Leistungssensor wird auch zur Bereinigung des Hausverbrauchs-
   profils verwendet, damit Geräteläufe nicht zusätzlich als Grundlast gelten.

## Abgrenzung

Keine automatische Gerätesteuerung, keine rückwirkende Zyklenerkennung aus
Recorder-Daten und keine Unterscheidung verschiedener Waschprogramme. Die
Lernwerte repräsentieren den typischen vollständigen Lauf des jeweiligen Geräts.

## Tests

`tests/ha/test_appliance_learning.py`: Einheiten, Zeitformate, Zählerdifferenzen,
Leistungsintegration, fehlerhafte/unvollständige Läufe, Persistenzdaten und
Verwendung von Lernwerten und Laufzeiten in den Planner-Eingängen.
`tests/ha/test_config_flow.py`: optionale Eingänge beim Anlegen und Anpassen.
