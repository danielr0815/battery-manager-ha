# Battery Manager v0.34.5

Der Haus-SOC-Wächter stützt seine Freeze-Erkennung jetzt auf den realen
Batteriefluss, sobald `feedin_battery_power_entity` konfiguriert ist. Damit
kann ein Plan, der wegen eines Aktor- oder Kaskadenproblems nicht ausgeführt
wurde, nicht länger selbst die vermeintliche Evidenz für einen eingefrorenen
SOC erzeugen.

Ist der konfigurierte Batterieleistungssensor vorübergehend nicht lesbar,
pausiert die Energiebilanz des Wächters. Es erfolgt bewusst kein Rückfall auf
den Plan, denn dessen Energiefluss ist in genau diesem Fall nicht bewiesen.
Installationen ohne konfigurierte Leistungsmessung behalten das bisherige
Verhalten als Kompatibilitäts-Fallback.

Ein neuer Regressionstest bildet den Live-Vorfall vom 2. September nach: Der
Plan erwartet 1.000 W, während die Hausbatterie tatsächlich nur 344 W misst.
Nach sieben Minuten stehen deshalb korrekt 40,1 Wh statt 116,7 Wh Evidenz im
Wächter. Zusätzlich beweist der Test das Pausieren bei `unavailable` sowie das
spätere Auslösen nach tatsächlich ausreichendem gemessenem Durchsatz.

805 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,10 Prozent.
