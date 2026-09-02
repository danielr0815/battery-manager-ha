# Battery Manager v0.34.7

Die Bad-Kaskade lädt vorhandene Recovery-Defizite jetzt vor jedem normalen
Fossibot-Top-up und vor jeder vorzeitigen Einspeisung. Bisher lief der
Direktüberschuss-Pass lastweise: B1 konnte dadurch bereits über das
50-Prozent-Ziel laden, während B2 nahe 20 Prozent blieb. Außerdem entstand der
kontinuierliche Endlast-Block erst in einem späteren Planner-Pass. Die dadurch
frei gewordene Aufnahmefähigkeit war für die Recovery nicht mehr sichtbar,
wohl aber für die anschließend berechnete frühe Einspeisung.

Der Planner behandelt die Kaskade nun als gemeinsame Prioritätsfolge:
Endlast, Recovery aller Mitglieder, danach erst Top-up. Nach dem vollständigen
Endlastplan wird offene Recovery erneut gegen den gesamten lokalen Tagesplan
geprüft. Sie darf direkte PV verwenden, die sonst zunächst die Hausbatterie
laden würde, wenn derselbe AC-Betrag später nachweislich nicht mehr exportiert
wird, kein Netzimport entsteht und die geschützten Haus-SOC-Grenzen erhalten
bleiben. Reicht die PV-Leistung, können beide Fossibots parallel laden. Erst
nach den streng zielbegrenzten Recovery-Chancen darf ein vollständig
exportgedeckter Mindestlaufzeit-Block das 50-Prozent-Ziel geringfügig
überschreiten; so verdrängt die Raster-Rundung keine Recovery des anderen
Speichers.

Der Replay mit den am 2. September aufgezeichneten Live-Eingaben reduziert die
frühe Einspeisung von 0,693 auf 0,020 kWh. B1 erreicht 50,4 Prozent, B2 erhält
291 Wh und lädt in zwei Zeitfenstern parallel zu B1. Die verbleibenden 19,7 Wh
sind über zwei Kalendertage verteilt und liegen an jedem Tag unter dem jeweils
kleinsten zulässigen Fünf-Minuten-Ladeblock; sie sind unter den konfigurierten
Laufzeitbedingungen nicht mehr absorbierbar.

815 Tests sind grün. Der reine Planner-Core bleibt zu 100 Prozent abgedeckt;
die Gesamt-Coverage beträgt 95,18 Prozent. Golden Snapshots, Ruff, Formatierung
und Mypy sind ebenfalls grün.
