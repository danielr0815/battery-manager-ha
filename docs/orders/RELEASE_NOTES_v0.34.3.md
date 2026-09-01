# Battery Manager v0.34.3

Der physische Besitz aller Kaskadenaktoren wird jetzt direkt an der letzten
Service-Grenze durchgesetzt. Ein Schaltaufruf für Root, Charge-Gate,
Fossibot-Ausgang oder Endlast erreicht Home Assistant nur noch, wenn er die ID
des zuständigen `CascadeManager` mitführt. Generische Last-, Support-,
Kalibrierungs- und verspätete Hintergrundpfade bleiben dort fail-closed.

Der neue Regressionstest bildet den Live-Vorfall vom 1. September vollständig
nach: zwei Fossibots mit 89,4 und 89,1 Prozent SOC, B1 als alleiniger
Waschmaschinen-Root, transparenter B2-Eingang, ein Endverbraucher ohne eigenen
Schalter, rund 0,65 kWh sofort geplante Aux-Energie und fünf Rolling Refreshes
im Zehn-Sekunden-Abstand. Zusätzlich wird der beobachtete Restfall erzwungen,
in dem ein generischer AUS-Auftrag den früheren Load-ID-Filter passiert. Vor
der Korrektur sendete dieser Test `turn_off` an Gate und Root; jetzt erreicht
kein fremder Service-Aufruf die Kaskade.

802 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,13 Prozent.
