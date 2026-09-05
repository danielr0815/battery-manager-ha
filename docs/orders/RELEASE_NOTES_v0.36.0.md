# Battery Manager v0.36.0

Diese Version baut die Kaskadenkachel zu einer interaktiven Energiefluss- und
Prognoseansicht aus. Sie zeigt jetzt verständlich, woher Energie kommt, wohin
sie fließt und wie sich die Speicher dabei voraussichtlich entwickeln.

## Energiefluss und Ablauf

- Der geplante Ablauf fasst aufeinanderfolgende, identische Zeitfenster zu
  lesbaren Phasen zusammen und nennt für jede Phase Quelle, Empfänger und
  Energiemenge.
- Aufnahme, im Akku gespeicherte Energie und Akkuentnahme bleiben getrennte
  Größen. AC-Durchleitung wird nicht als Akkuladung ausgegeben; wo keine eigene
  Energiemenge verfügbar ist, erfindet die Karte keinen Messwert.
- Die konfigurierte Kette bleibt sichtbar. Root-Restenergie wird als
  AC-Eigenbedarf beziehungsweise Rundungsrest ausgewiesen, damit die Bilanz
  nachvollziehbar bleibt.

## Interaktive Diagramme

- Jeder Speicher erhält eine kompakte SOC-Prognose. Maus, Touch und Tastatur
  steuern einen gemeinsamen Zeitcursor, sodass SOC und Energieflüsse zum selben
  Zeitpunkt verglichen werden können.
- Ein Klick auf Speicher oder Kennzahlen öffnet eine gemeinsame Detailansicht
  für SOC, Ladeaufnahme, Akkuentnahme, mittlere Leistung je Planslot und
  kumulierte Energie.
- Die Endlast erhält denselben Diagrammmechanismus für geplante Leistung und
  Energie. Planung und Ist-Werte sind klar getrennt beschriftet.
- Heute, morgen und der gesamte Plan berücksichtigen die Home-Assistant-
  Zeitzone einschließlich Sommerzeitwechsel. Fehlende Prognosen bleiben leer,
  statt als künstliche konstante Kurve zu erscheinen.

## Darstellung und Bedienung

- Gerätenamen, Quelle-Ziel-Angaben und Cursorwerte umbrechen vollständig und
  bleiben außerhalb des nur bei Bedarf scrollenden Diagrammbereichs sichtbar.
- Die Kartenhöhe wächst automatisch mit dem Inhalt. Die Bedienung wurde bei
  Desktop- und Mobilbreite sowie mit langen Gerätenamen geprüft.

Validiert mit 884 grünen Python-Tests, 13 Frontend-Regressionstests, Ruff,
Formatter, mypy und Browserprüfungen für Maus, Touch und Tastatur.
