# Battery Manager v0.31.0

Kaskaden reagieren auf Sicherheits- und Fehlerzustände jetzt konsequent über
ihren einzigen Actor-Owner. Der G4-Floor-Guard und der globale
Datenverlust-Shed schalten eine aktive Kaskade in der vollständigen sicheren
Reihenfolge ab, während der generische Load-Executor ihre Mitglieder nicht
mehr parallel ansteuert. Fault- und Hands-off-Kaskaden reservieren außerdem
keine Energie mehr in der wirksamen Planung; eine bewusst ausgeschaltete und
fehlerfreie Kaskade behält ihre Vorschau zur Inbetriebnahme.

Ein Aktorfehler nennt nun die betroffene Entity, den angeforderten und den
beobachteten Zustand sowie `service_failed` oder `confirmation_timeout`.
Fault- und Hands-off-Payloads zeigen unmittelbar 0 kWh und keinen ausführbaren
Zeitplan. Eine bereits laufende `proving`-Phase bleibt beim Rolling Replan als
begonnene Aux-Episode erhalten, sodass eine knappe zulässige Entladung nicht
durch eine erneut verlangte volle Mindestlaufzeit abgebrochen wird.

Der Hausakku-SOC-Watchdog prüft unveränderte Werte nur noch im inklusiven
Bereich von 21 bis 89 Prozent. Unter 21 Prozent und über 89 Prozent sind
Stillstände wegen BMS-Kalibrierung, Balancing oder Lade-/Entladegrenzen
plausibel; dort werden keine Watchdog-Evidenz aufgebaut und vorhandene Latches
sofort aufgehoben. Damit kann ein legitimes 99-Prozent-Plateau nicht mehr den
Coordinator auf `UpdateFailed` setzen und alle planungsabhängigen Entitäten
unverfügbar machen.

Die Testinfrastruktur verwendet für produktive Wartezeiten virtuelle Zeit und
parallelisiert die volle Suite modulweise. Core-, Home-Assistant-, Ruff- und
Mypy-Prüfungen decken die Kaskaden-Sicherheitsreihenfolge, Fault-Diagnose,
Rolling-Replan-Fortsetzung sowie die exakten SOC-Grenzen und die unmittelbare
Watchdog-Erholung ab. 761 Tests sind grün; die Gesamt-Coverage beträgt 95,26 %
und der reine Planner bleibt bei 100 %.
