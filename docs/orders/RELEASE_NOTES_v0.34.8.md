# Battery Manager v0.34.8

Eine ausgeschaltete Kaskaden-Automation ist jetzt auch planerisch ausgeschaltet.
Bisher stoppte `switch.…_automation` zwar zuverlässig den Executor, der Planner
berechnete die Kaskade jedoch weiter als hypothetische Inbetriebnahme-Vorschau.
Dadurch konnte `binary_sensor.…_root_recommendation` eingeschaltet bleiben und
nicht ausführbare Root- oder Aux-Energie weiterhin die Haus-SOC-Trajektorie,
die Feed-in-Entscheidung und die Allokation konkurrierender Lasten verändern.

Bei ausgeschalteter Automation sowie im Fault- oder Hands-off-Zustand werden
Kaskadenmitglieder und Endlast nun aus dem wirksamen Plan entfernt. Root- und
Aux-Energie, Root-Empfehlung, Recovery-Frist und Timeline bleiben leer. Der
aktuelle Aggregat- und Mitglieder-SOC bleibt zur Diagnose sichtbar. Eine
künftige hypothetische Vorschau müsste als separater, ausdrücklich
nicht-operativer Rechenlauf umgesetzt werden und dürfte nicht in die globale
Planung zurückwirken.

Ein Regressionstest bildet die Bad-Kaskade mit zwei Fossibots und Endlast nach
und beweist, dass ihre deaktivierte Variante dieselbe Haus-SOC-Trajektorie wie
eine Konfiguration ohne Kaskade erzeugt. Ein zweiter Test hält die ausgeschaltete
Root-Empfehlung fest. 817 Tests sind grün. Der reine Planner-Core bleibt zu
100 Prozent abgedeckt; die Gesamt-Coverage beträgt 95,18 Prozent. Golden
Snapshots, Ruff, Formatierung und Mypy sind ebenfalls grün.
