# Battery Manager v0.33.0

## Kaskade nutzt vermeidbaren Export jetzt auch unterhalb von 50 %

Das Kaskaden-Entladeziel von 50 % bleibt die bedingungslos nutzbare Grenze:
Energie darüber darf weiterhin ohne Wiederaufladezusage verbraucht werden.
Wenn die Hausbatterie-Prognose danach noch Export ausweist, darf die Kaskade
zusätzlichen Speicherplatz bis zum konfigurierten Sicherheits-Floor schaffen.

Diese tiefere Entladung wird nur akzeptiert, wenn der gemeinsame Replan:

- den prognostizierten Export tatsächlich reduziert,
- keinen zusätzlichen Netzimport erzeugt und
- alle betroffenen Kaskadenspeicher bis zum Ende des ersten Exporttages wieder
  auf mindestens 50 % lädt.

Der Executor verwendet nun das SOC-Ziel des aktuellen Planslots. Die
Wiederaufladepflicht wird persistiert, über Mitternacht erhalten und blockiert
eine weitere Aux-Episode, bis sie erfüllt ist.

## Betriebshinweis für die Bad-Kaskade

Der interne Mindest-Entladestand des Fossibot begrenzt weiterhin die physisch
erreichbare Tiefe. Beim Live-Review stand F2400-B1 zuletzt auf 20 %, F2400-B2
auf 48 %. Der aktuelle kleine Restexport kann deshalb über B1 aufgenommen
werden; B2 kann unabhängig vom Battery-Manager-Plan nicht unter sein internes
48-%-Limit entladen.

Die Bad-Kaskadenautomation war beim Review ausgeschaltet und wurde nicht
automatisch aktiviert. Nach Installation und Home-Assistant-Neustart den Plan
prüfen und die Automation bewusst einschalten.
