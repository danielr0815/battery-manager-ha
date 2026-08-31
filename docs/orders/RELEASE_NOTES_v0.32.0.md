# Battery Manager v0.32.0

Die Bad-Kaskade plant ihre Aux-Entladung jetzt als zusammenhängende Episode bis
zum konfigurierten 50-%-Ziel aller beteiligten Fossibot-Speicher. Bisher ging
nur die minimale Startlaufzeit des Entfeuchters in den Plan ein. Dadurch blieb
die danach verfügbare Ladekapazität nahezu unsichtbar und der Planner konnte
gleichzeitig vorzeitige Einspeisung vorsehen. Nun bewertet der anschließende
globale Replan die gesamte durch die Entladung entstehende Kapazität, bevor er
nicht anders nutzbaren Überschuss zur Einspeisung freigibt. Ausgeführt wird
weiterhin ausschließlich der aktuelle Rolling-Replan-Schritt.

Root/PV bleibt die höhere Priorität. Sobald der Replan einen späteren
Root-Slot für ein Kaskadenmitglied oder die Endlast bucht, endet die
vorausgeplante Aux-Episode davor. Damit zeigt die Prognose keine elektrisch
unmögliche gleichzeitige Root-Aufnahme und Speicherentladung. Reicht die Zeit
bis dahin nicht für das vollständige Ziel, wird nur die tatsächlich
konfliktfreie Teilentladung geplant.

Die Kaskadenkarte zeigt jetzt den SOC jedes Fossibot in einer eigenen,
übereinander angeordneten Kurve. Aktueller SOC und 50-%-Entladeziel stehen
direkt daneben. Phase und Energiemengen sind als kurze Bedieneraussagen
formuliert; redundante Root- und AC-Ausgangsinformationen wurden aus dem
Zeit-Hover entfernt, während Laden, Entladen, Endlastquelle und SOC erhalten
bleiben.

Die Browser-Regel für Live-Prüfungen verwendet verbindlich den lokalen
Playwright-MCP und startet keinen unverbundenen Ersatzbrowser mehr. 769 Tests
sind grün; der reine Planner erreicht 100 % Coverage, die Gesamt-Coverage
beträgt 95,24 %, Ruff und Mypy sind fehlerfrei.
