# Battery Manager v0.34.1

Die Kaskadenautomation beginnt nach einem bewussten AUS→AN jetzt immer als
neue Besitzepisode. Alte Wake-Indizes, Deadlines, Claims und Retry-Evidenz
können dadurch nicht mehr einen früheren `waking_members`-Zustand fortsetzen
und Root-Aktoren in konkurrierende Schaltfolgen bringen. Aktivierung,
Deaktivierung, Fault-Reset und automatische Übergänge verwenden außerdem
denselben Kaskaden-Lock. Eine abgelehnte Aktivierung nennt ihren Grund und die
nicht sicher als AUS bestätigten Actors direkt in den Diagnoseattributen.

Ein HA-Neustart schaltet eine konsistente aktive Kaskade nicht mehr pauschal
aus. Der Manager verwirft nur flüchtige Wake- und Proof-Zeitfenster und baut
seine Claims aus den bestätigten Live-Schalterzuständen neu auf. Vollständige
Root- und Aux-Pfade bleiben unverändert; ein geordneter Wake wird mit frischer
Gerätetelemetrie an der vorhandenen Stufe fortgesetzt, und ein Aux-Pfad erhält
einen neuen 60-Sekunden-Leistungsnachweis. Beim HA-Start noch unbekannte Actors
bekommen 60 Sekunden Publikationszeit. Nur ein widersprüchlicher oder weiterhin
unbekannter Zustand fällt auf geordnetes Safe-OFF zurück. Bewusst deaktivierte
Kaskaden bleiben vollständig in manueller Hand.

Ein bereits vollständig aufgebauter Root-Pfad wird nun auch bei normalen
Rolling Refreshes idempotent übernommen. Aktive Charge-Gates werden deshalb
nicht mehr in jedem Planzyklus kurz AUS und wieder AN geschaltet. Die großen
Kaskadenattribute `member_details` und `schedule` bleiben gleichzeitig für die
Live-Anzeige verfügbar, werden aber nicht mehr alle zehn Sekunden in den
Recorder dupliziert und lösen dort keine 16-KiB-Warnungen mehr aus.

797 Tests sind grün. Der reine Planner-Core bleibt vollständig abgedeckt; die
Gesamt-Coverage beträgt 95,14 Prozent.
