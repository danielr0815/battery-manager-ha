# Gemessener AC-Bedarf ab 0.50.0

## Anlass und Verhalten

Ein Wasserkocher oder die Heizphase eines Haushaltsgeräts ist im Stundenmittel
nicht zuverlässig sichtbar. Die Prognose kann den Inverter deshalb sperren,
obwohl eine höhere echte Restlast die sinnvollere Gelegenheit als eine spätere
schwache Last wäre. Die schnelle Regelung ergänzt die aktive Reservepolitik.

Der Inverter erhält ein **Leistungslimit**, keinen Befehl zu konstanter Entnahme
oder Netzeinspeisung. Die tatsächliche Lastregelung bleibt beim Wechselrichter.
Der neue Pfad startet keine Geräte und verändert keine Hausverbrauchsprognose.

- Prüfung alle **5 Sekunden**, ohne auf eine laufende Optimierung zu warten.
  Messintervall und Aktorbestätigung kommen zur Reaktionszeit hinzu; dies ist
  keine garantierte fünfsekündige physische Schaltzeit.
- Start bei **mindestens 100 W** Restlast. Im eingeschalteten Zustand halten
  mindestens **50 W** die Freigabe aufrecht.
- Bei weniger als 50 W bleibt die Freigabe **10 Minuten** bestehen. Wiederkehrender
  Bedarf setzt die Frist zurück. Das verhindert Umschalten in Heizpausen.
- Schutz, DC-Quellenbedarf, fehlende Messungen und erschöpftes bzw. veraltetes
  Budget beenden die zusätzliche Freigabe unabhängig von der Frist.

## Energievertrag

`ReserveDecision.live_ac_floor_percent` ist eine zusätzliche Grenze für **bereits
vorhandene** Energie. Sie nimmt das Maximum aus der DC-Obergrenze für nötigen
PV-Speicherraum, dem nominalen DC-Mindestvorrat und der Inverter-Untergrenze;
DC-Verbrauch des ersten Simulationsschritts wird zusätzlich reserviert. Anders als
bei der normalen Lastpriorität wird dabei kein zukünftiges AC-Fenster bevorzugt.
Noch nicht eingetroffene PV des aktuellen Schritts ist keine gespeicherte Energie.

Der Laufzeitpfad zieht außerdem den größeren Wert aus SOC-Puffer und SOC-Hysterese
ab. Ab 0.52.0 wird ausschließlich die konfigurierte Maximalleistung freigegeben,
wenn die verbleibenden Wh diese Leistung für 35 Sekunden tragen können
(30 Sekunden Messgültigkeit plus fünf Sekunden Prüfung), unter Beachtung von
Entlade- und Inverterwirkungsgrad. Andernfalls wird gesperrt; es gibt kein
Teilleistungslimit. Siehe [Binäre Inverterfreigabe](F-BINARY-INVERTER.md).
Eine neue SOC-Meldung verändert das Budget sofort bei der nächsten Prüfung.
Ein neues Planergebnis ersetzt die Grenze, nicht die laufende Ausschaltfrist.
Die eigentlichen Forecast-Flüsse und Golden-Szenarien bleiben unverändert.

Die Freigabe setzt aktive Reserve mit koordiniertem Inverter voraus. Geplante,
manuelle, physisch aktive oder unbekannte DC-Netzteile sperren den Zusatzpfad.
Der DC/DC-Pfad muss, sofern konfiguriert, eingeschaltet bestätigt sein. Der schnelle
Pfad besitzt keine Netzteilaktoren und verwendet dieselbe Aktorsperre wie die
geordnete Quellenumschaltung. Ohne frisches Netzsignal gibt es keine neue
Live-Freigabe. Ein unverfügbarer SOC darf nicht durch den historischen Cache
ersetzt werden.

Das Budget gilt ab Planeingabe höchstens fünf Minuten, begrenzt durch das Ende des
aktuellen Quellslots. Ein länger dauernder/fehlgeschlagener neuer Plan verlängert
es nicht. Bei zugeordneten Live-Quellen wird die nächste Planung bereits
eine Minute vor Ablauf angefordert, damit die Rechenzeit keine regelmäßige
Unterbrechung verursacht. Energie für allgemeine Reservehaltung wird nicht allein aufgrund
hohen Verbrauchs freigegeben; deshalb ist auch bei hohem SOC weiterhin eine
begründete Sperre möglich. Eine neue Vorhersage kann die Freigabe widerrufen.

## Messquellen und Regelstabilität

Für eine direkte AC-Bilanz können drei Quellen gemeinsam zugeordnet werden:

| Option | Vorzeichen |
|---|---|
| `live_ac_grid_power_entity` | Netzbezug positiv, Export negativ |
| `live_ac_input_power_entity` | AC-Netz zum Inverter positiv, Rückfluss negativ |
| `live_ac_output_power_entity` | Inverter zu AC-Verbrauchern positiv, AC-PV zum Inverter negativ |

Die Restlast lautet `max(0, Netz + AC-Ausgang − AC-Eingang)`. Dadurch sind direkte
Solarversorgung, aktuelle AC-Abgabe und Batterieladung bereits bilanziert.
Batterie-DC-Leistung ist ausdrücklich kein Ersatz für AC-Ein-/Ausgang: Sie würde
DC-Verbrauch und Umwandlungsverluste als Hauslast zählen. Mehrphasige Anlagen
benötigen passende Summensensoren für denselben Messkreis.

Sobald eine dieser direkten Quellen zugeordnet ist, müssen alle drei gültig
sein. Eine fehlende Quelle verursacht keinen stillen Wechsel auf eine andere
Bilanz. Fehlen alle drei Zuordnungen, werden die bestehenden Optionen
`operation_house_power_entity` und `operation_pv_power_entity` verwendet,
optional ergänzt um `operation_import_power_entity`. Diese Alternative verwendet
`max(0, Hauslast − max(0, PV), frischer Netzbezug)`. Der Hauswert muss eine
Verbrauchsmessung einschließlich solar gedeckter Last sein, kein prognostizierter
Grundverbrauch. Import allein reicht nicht: Er fällt nach erfolgreicher
Freigabe auf null, obwohl das Gerät noch läuft.

Alle verwendeten Leistungswerte brauchen W/kW und dürfen höchstens 30 Sekunden
seit `last_reported` alt sein. Konstante Template-Ergebnisse können diese
Frischebedingung verletzen, auch wenn ihre Rohsensoren weiter melden. Deshalb
werden auf dieser Anlage die direkt meldenden Victron-Sensoren bevorzugt.
Fehlende AC-Leistungsmesswerte beenden nur den Zusatzpfad, nicht die normale
Planfreigabe. Ab 0.52.0 benötigen auch geplante Freigaben ein frisches Budget,
SOC und Netzsignal; ihr voller Leistungsdurchsatz wird unabhängig von den
optionalen AC-Leistungsmessern alle fünf Sekunden abgesichert.

### Zuordnung aus der Power Flow Card Plus dieser Anlage

Die Karte wurde am 29.09.2026 MESZ über den lokalen Playwright-MCP gelesen. Sie
verwendet `sensor.system_grid_total_power`, die invertierte AC-Out-Leistung
`sensor.victron_vebus_out_l1_power_228` als Solar und die invertierte
Batterie-DC-Leistung. `home: {}` ist berechnet, kein eigener Hauslastsensor.
Für die neue Regelung ist folgende Zuordnung vorbereitet:

| Option | Entity |
|---|---|
| `live_ac_grid_power_entity` | `sensor.system_grid_total_power` |
| `live_ac_input_power_entity` | `sensor.victron_vebus_activein_l1_power_228` |
| `live_ac_output_power_entity` | `sensor.victron_vebus_out_l1_power_228` |

Die zusätzliche AC-In-Messung derselben einphasigen Victron-Anlage ersetzt die
ungeeignete DC-Batterieleistung. Beobachtete Rohmeldungen waren rund 1–2 Sekunden
alt. Beispiel: Netz 0 W, AC-Out +10 W, AC-In −50 W ergibt 60 W AC-Restlast;
hohe zusätzliche DC-Batterieversorgung ist kein AC-Startsignal.

**Einführung:** Nach Installation von 0.50.0 die drei Felder in
*Konfigurieren → Verbrauchslernen* zuordnen. Das derzeit installierte 0.49.0
kennt diese Felder nicht und lehnt ihre Speicherung ab. Es wurden keine
bestehenden Live-Optionen verändert; kurzzeitig geprüfte Template-Helfer sind
wieder entfernt. Keine Änderung an Karte, Aktoren oder konkurrierenden Regeln.

## Diagnose, Lebenszyklus und Nachweise

Das additive unaufgezeichnete Sensorattribut `live_ac` enthält `reason`, `limit_w`,
`residual_demand_w`, `available_wh`, `low_since` und nach einem Ausführungsversuch
`confirmed`. Eine angeforderte Leistung gilt erst nach Aktorrückmeldung als
bestätigt. Fehlende Abschaltbestätigung bleibt im nächsten Fünf-Sekunden-Zyklus
nachzuprüfen. Normale Befehle erscheinen weiter im Betriebsjournal.

Die SOC-Zeitspur beschreibt den Forecast, nicht nachträglich erfundene Lastläufe.
Der aktuelle bestätigte Zustand kann davon abweichen. Timer und Aufgaben werden
beim Entladen beendet. Live-Freigaben sind nicht persistiert; nach Neustart braucht
es neue Messungen und ein gültiges Reservebudget.

`tests/core/test_live_ac.py` prüft Energiebudget, DC-Vorrang, Leistungslimit,
Schwellwerte und Zeithysterese. `tests/ha/test_live_ac_runtime.py` prüft reale
Aktorbefehle, Null-Netzbezug nach erfolgreicher Freigabe, Planintegration,
Quellenkonflikte, W/kW, ausbleibende Meldungen, fehlende Bestätigungen und
Schutzänderungen während verzögerter Befehle. Die Uhr läuft virtuell.
