# Battery Manager v0.31.1

Kaskaden wecken ihre Fossibot-Mitglieder jetzt in der tatsächlichen
elektrischen Reihenfolge auf. Nach Root-AN muss B1 zunächst eine neue
numerische SOC-Meldung veröffentlichen; erst danach wird B1s AC-Ausgang
aktiviert und B2 versorgt. B2 erhält seinen eigenen AC-Ausgangsbefehl wiederum
erst nach einer neuen SOC-Publikation von B2. Ein bereits vorhandener oder
gecachter SOC reicht nicht mehr als Aufwachnachweis. Auch ein unveränderter
Wert zählt korrekt über Home Assistants `last_reported`, und jede Stufe nutzt
ihr eigenes konfiguriertes Wake-Timeout.

Ein Hard-Fault führt weiterhin zuerst die vollständige geordnete
Safe-OFF-Sequenz aus und bleibt bis zum bewussten Reset sichtbar. Sobald
Safe-OFF erfolgreich abgeschlossen ist, wiederholt der Manager die
Abschaltung jedoch nicht mehr bei jedem Coordinator-Lauf. Dadurch können die
Root-/Waschmaschinensteckdose und die übrigen Kaskadenaktoren bei
ausgeschalteter Automation manuell diagnostiziert und bedient werden, ohne
wenige Sekunden später wieder ausgeschaltet zu werden. Automation-AN bleibt
bis zum Fault-Reset gesperrt.

Die Regressionstests bilden B1- und B2-Publikationen sowie den Wake-Timeout mit
virtueller Zeit ab und beweisen die manuelle Actor-Freigabe nach dem einmaligen
Fault-Safe-OFF. 764 Tests sind grün; die Gesamt-Coverage beträgt 95,15 %, Ruff
und Mypy sind fehlerfrei.
