# Battery Manager v0.34.0

Neue Kaskaden-Entladeepisoden beginnen jetzt so spät wie sicher möglich.
Der Planner legt den Aux-Block an den letzten Forecast-Slot, von dem die
verfügbare Speicherenergie noch vollständig vor der Root-/PV-Übernahme oder dem
lokalen Tagesende genutzt werden kann. Erst wenn der Rolling-Horizont diesen
Start erreicht, wird die Episode ausgeführt; ein bereits laufender oder im
Leistungsnachweis befindlicher Lauf bleibt ohne Unterbrechung verankert.

Die Fossibot-Eingänge einer Kaskade laden außerdem nur noch aus gleichzeitig
verbleibendem Solarüberschuss. Die allgemeinen Batterie-Toleranz-, Topup- und
prognosegestützten Vorladepfade können damit nicht mehr unbemerkt Energie aus
der Hausbatterie in einen Kaskadenspeicher verschieben. Die direkte Endlast
behält ihre normalen Strategien und weiterhin Vorrang vor dem Speicherweg.

Mehrere Fehler aus dem Live-Wake am 1. September sind behoben. Eine frische
numerische SOC-, Eingangsleistungs- oder Ausgangsleistungs-Publikation beweist
nun, dass ein Fossibot nach dem Einschalten der vorgelagerten Versorgung wach
ist; die langsamere SOC-Kadenz blockiert seinen AC-Ausgang nicht mehr. Der
einmalige 15-Minuten-Retry bleibt über Rolling Refreshes erhalten, und der
Diagnose-Payload zeigt Wake-Modus, Mitglied, Baseline, Deadline und akzeptierte
Telemetrie-Evidenz.

Bereits eingereihte generische Lastaktionen prüfen den Kaskadenbesitz jetzt
noch einmal unmittelbar unter dem finalen Aktor-Lock. Sie können die
Root-Steckdose deshalb nicht mehr parallel zum Kaskadenmanager ausschalten.
Zusätzlich werden persistierte UTC-Zeitstempel des Speicher-SOC vor der
Core-Planung in naive lokale Planzeit umgewandelt, sodass der erste Plan nach
einem Neustart nicht mehr an gemischten Zeitzonen scheitert.

784 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage bleibt oberhalb des 95-Prozent-Gates.
