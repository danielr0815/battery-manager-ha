# Battery Manager v0.34.6

Die Bad-Kaskade kann nach einem Tageswechsel wieder zuverlässig aktiviert
werden. Eine vom Vortag erhaltene Episodenmarke wurde bisher im stabilen
Safe-OFF-Zustand nicht bereinigt. Der erste Root-Wake startete deshalb noch
normal; ab dem nächsten Coordinator-Refresh wurde er jedoch fälschlich als
über Mitternacht laufende alte Episode behandelt, geordnet ausgeschaltet und
sofort wieder gestartet. Root taktete dadurch ungefähr alle zehn Sekunden und
die Wake-Deadline wanderte immer weiter, sodass B1 seinen AC-Ausgang nicht
erreichen konnte.

Der Executor entfernt eine abgeschlossene Vortagsmarke nun vor dem ersten
neuen Tagesübergang. Läuft ein echter Wake über Mitternacht, wird dessen
Tageswechsel ebenfalls genau einmal konsumiert. Offene Recovery-Schulden
bleiben dabei unverändert erhalten; eine gleichlautende Marke des aktuellen
Tages schützt weiterhin vor einer zweiten unbewiesenen Aux-Episode durch
manuelles AUS→AN.

Der Regressionstest bildet die Live-Bedingungen vom 2. September nach: zwei
Fossibots bei 20 Prozent, transparente B2-Einspeisung, gemeinsamer Root-Aktor,
manuelle Aktivierung aus Safe-OFF, Episodenmarke des Vortags und zwei
Coordinator-Durchläufe im Abstand von zehn Sekunden. Zusätzlich ist ein
tatsächlich über Mitternacht laufender Wake abgedeckt.

807 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,12 Prozent. Ruff und Mypy sind ebenfalls grün.
