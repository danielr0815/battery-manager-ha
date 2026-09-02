# Battery Manager v0.34.4

Automation-AN ist für eine bereits aktive Kaskade jetzt idempotent. Nur eine
echte Flanke von AUS nach AN eröffnet eine neue Besitzepisode und verwirft alte
Wake-, Proof- und Retry-Evidenz. Ein redundantes AN lässt dagegen Phase,
Claims und Wake-Deadline vollständig unverändert; ein tatsächlich abweichender
Shared Actor wird anschließend weiterhin geordnet als `hands_off` übernommen.

Der Fix bildet den Live-Vorfall der Bad-Kaskade vom 2. September nach. Während
eines Root-Wakes bei rund 20 Prozent SOC meldete Root kurz AUS. Ein erneuter
AN-Aufruf löschte daraufhin bisher `waking_members`, setzte die 60-Sekunden-
Deadline im ungefähr zehnsekündigen Coordinator-Takt immer neu und startete
Root wiederholt. Nach rund zehn Minuten endete das Takten in einem
`root_transition_failed`-Fault, obwohl B1 bereits nach etwa 47 Sekunden
Eingangsleistung meldete.

Der neue Regressionstest stellt genau das kritische Zeitfenster her: laufender
Root-Wake, bestätigter Claim, kurzzeitiges Shared-Root-AUS und redundantes
Automation-AN. Vor dem Fix sprang die Phase reproduzierbar auf `idle`; jetzt
bleibt die Wake-Evidenz erhalten und die Shared-Abweichung führt ohne weitere
Schaltanforderung kontrolliert zu `hands_off`.

804 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,15 Prozent.
