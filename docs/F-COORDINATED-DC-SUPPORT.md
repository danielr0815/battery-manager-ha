# Gemeinsame Strategie für Inverter und DC-Netzteile

> **Entscheidungs- und Änderungshistorie.** Der aktuelle Einstieg und
> etwaige ablösende Verträge stehen in [CURRENT_CONTRACTS](CURRENT_CONTRACTS.md).

Stand: 2026-09-14. **Implementiert für v0.44.0; Aktivierung durch Konfiguration des numerischen Inverter-Entladelimits.**
Auftrag: Inverter, 48-V-Netzteil und 24-V-Netzteil gemeinsam führen;
Spannungen, Stromgrenzen, Wirkungsgrade und Lasten als Parameter behandeln.

## Befund und Ziel

Live am 14.09.2026: Der 48-V-Support lief im spannungsgeregelten
Manual-Modus bei freigegebenem Inverter. Der BM-Regler meldete
`below_on_voltage` bei etwa 48,24 V und 28 % SOC. Die zusätzlich aktive
Automation „Energie: Steuerung 48V Netzteil“ verlangt dagegen bei
freigegebenem Inverter ein ausgeschaltetes Netzteil. Beide Regeln können
sich gegenseitig überschreiben. Die Prognose berücksichtigt diese
Netzunterstützung und entlädt die Batterie nachts deshalb kaum.

Zielpriorität: DC-Versorgung und Batteriegrenzen einhalten, danach Netzbezug
minimieren und verfügbare PV nutzen, danach unnötige Schaltwechsel vermeiden.
Netzstützung erhält eine kleine Schutzreserve; sie erzeugt keine Freigabe
zur erneuten AC-Entladung oder zum Betrieb von Überschusslasten.

Grundlagen: [DC_TOPOLOGY.md](DC_TOPOLOGY.md), insbesondere die Korrektur auf
15 Batteriezellen in §5. Die dortige unabhängige Manual-Spannungsregelung
wird durch diesen Entwurf abgelöst. Die Spannung am Netzteilanschluss ist
maßgeblich; eine feste SOC-Spannungs-Zuordnung wird nicht vorausgesetzt.

## R1 — Physik und Parameter

Topologie: Die Batterie und das 48-V-Netzteil speisen denselben 48-V-Bus.
Daran hängen native 48-V-Lasten, Inverter und DC/DC-Wandler. Der Wandler
oder das 24-V-Netzteil versorgt die 24-V-Schiene. Eine Unterstützung des
48-V-Busses durch das 24-V-Netzteil wird nicht angenommen.

Pro Netzteil: Sollspannung U_set, maximaler Ausgangsstrom I_max,
Wirkungsgrad eta, optional gemessene Ein-/Ausgangsleistung und Bereitschaftsverlust.
Für den DC/DC-Wandler ebenfalls Spannung, Stromgrenze und Wirkungsgrad.
Die aktuelle Anlage hat ein für alle 24-V-Lasten ausreichend großes Netzteil;
die allgemeine Prüfung lautet dennoch P24_Last <= U24 * I24_max.

U48_set * I48_max ist eine Nennleistungsobergrenze, keine konstante Einspeisung.
Für das vorgesehene spannungsbegrenzte, strombegrenzte Modell gilt:

- Oberhalb der Ausgangsspannung liefert das 48-V-Netzteil annähernd nichts.
- Unterhalb der Sollspannung kann es bis zum Stromlimit liefern;
  im Stromlimit ist die Leistung U_bus * I48_max.
- An der Sollspannung regelt sich der Strom nach Last und Batterieaufnahme
  zwischen annähernd null und I48_max ein. Eine angeschlossene Batterie
  kann einen Teil der Leistung aufnehmen.
- Reale Kennlinie und Rückspeiseschutz müssen zur angenommenen Topologie
  passen. Aus Sollspannung und Maximalstrom allein lässt sich die momentane
  Leistung nicht eindeutig ableiten.

Bei ausgeschaltetem Inverter:

```
P_bus = P48_Last + (P24_Last / eta_dcdc, falls DC/DC versorgt)
P_batt_bus = P_bus - P48_Netzteil - P_PV_Lader_DC
```

Positive P_batt_bus entlädt die Batterie; negative lädt sie. Batterieverluste
werden erst auf diesen Rest angewandt. Netzteilenergie deckt gleichzeitige
Last direkt, ohne fiktiven Batterie-Rundlauf. AC-Aufnahme ist die tatsächliche
DC-Abgabe geteilt durch eta, zuzüglich gegebenenfalls Bereitschaftsverlust.

## R2 — Gemeinsame Betriebszustände

„Inverter aus“ bedeutet hier: Batterieentladung nach AC gesperrt; PV-Ladung
und erforderliche Durchleitung bleiben möglich. Keine pauschale Abschaltung
des gesamten Victron-Geräts.

| Zustand | Inverter | 24-V-Netzteil | DC/DC | 48-V-Netzteil |
|---|---|---|---|---|
| A: PV-/Batteriebetrieb | nach Plan freigegeben | aus | an | aus |
| B: DC-Reserve nutzen | gesperrt | aus | an | aus |
| C: 24-V-Last auslagern | gesperrt | an | aus | aus |
| D: 48-V-Bus stützen | gesperrt | an | aus | bedarfsgerecht freigegeben |

Kein obligatorischer Durchlauf: Bei akutem Bedarf darf direkt in C oder D
gewechselt werden, jedoch mit bestätigten Umschaltsequenzen. Fehlt ein
Netzteil, wird der Zustand als nicht verfügbar behandelt.

A -> B: Die Planung reserviert genug Energie für beide DC-Lasten bis zur
nächsten belastbaren PV-Versorgung; spätestens am Inverter-Floor wird gesperrt.

B -> C: Die gemeinsame Prognose erwartet sonst eine Unterschreitung der
DC-Schutzreserve oder der reale SOC erreicht die 24-V-Stützschwelle.
Umschaltung möglichst spät, aber rechtzeitig für die verfügbare Stellleistung.
Ein schlechter PV-Tag allein schaltet das Netzteil nicht sofort ein.

C -> D: Die native 48-V-Last würde trotz ausgelagerter 24-V-Last die untere
Reserve verletzen, oder eine verlässliche Schutzmessung verlangt Unterstützung.
Spannung entscheidet hier über Lieferfähigkeit und akuten Schutzbedarf,
nicht allein über den Eintritt aus dem normalen Batteriebetrieb.

D -> C: Das Erreichen der 48-V-Rückkehrreserve beendet die 48-V-Stützung
auch nach Netzladung; die 24-V-Last bleibt ausgelagert und der Inverter gesperrt.
C -> B -> A: Rückkehr nach Reserve, prognostiziertem Bedarf und PV-Erholung. Rückkehr darf Stufen überspringen, wenn deren Bedingungen erfüllt
sind. Netzstützung allein darf keinen Zyklus D -> A -> D erzeugen.

Die direkte 24-V-Versorgung ist die bevorzugte erste Stufe: Sie entfernt
verlässlich die gesamte 24-V-Last vom knappen 48-V-Bus. Das ist eine robuste
Standardstrategie, kein universeller Wirkungsgradbeweis. Wenn Parameter oder
fehlende Geräte andere Pfade sinnvoll machen, darf die Planung erlaubte
Kombinationen anhand ihrer realen Verluste und Grenzen vergleichen. Während
automatischer Netzstützung bleibt die AC-Batterieentladung gesperrt.

## R3 — Reserve aus Energie und Reaktionsfähigkeit

Die Inverter-Abschaltschwelle wird aus dem geplanten DC-Energiebedarf bis zur
Erholung, Batterieeffizienz, Prognoseunsicherheit und einer Schutzreserve
bestimmt. Der bisherige T*-Optimierer kann diese Aufgabe weiter übernehmen,
muss aber alle Stützentscheidungen gemeinsam simulieren.

Grundordnung: Batterie-Untergrenze < 48-V-Aktivierung <= 24-V-Aktivierung
< Inverter-Untergrenze. Aktivierungs- und Rückkehrschwellen besitzen jeweils
Hysterese. Aktuelle 5/7/10/20 % sind nur bestehende Konfigurationswerte, keine
neu bestätigten Empfehlungen. Abstände müssen Messunsicherheit, Last,
Schalt-/Bestätigungszeit und die schwächste verfügbare Versorgung abdecken.

Für verbleibenden Leistungsfehlbetrag P_def gilt näherungsweise:

```
Zeit_bis_Untergrenze = nutzbare_Reserve_Wh / (P_def / eta_batt_discharge)
```

Die Planung darf diese Zeit nicht überschreiten. Ist P48_Last größer als die
verlässliche 48-V-Netzteilabgabe, stabilisiert auch Zustand D die Batterie
nicht. Frühere Unterstützung verschafft nur Zeit. Dann ist eine explizit
konfigurierte stärkere Versorgung oder Lastabwurf nötig; ohne diese meldet
der Plan ein Versorgungsdefizit. Keine virtuelle unbegrenzte Nachladung am
Batteriefloor und keine durch Clamping versteckte Unterversorgung.

## R4 — 48-V-Netzteil als Stütze

Die Freigabe erfolgt ausschließlich innerhalb eines gemeinsamen Stützzustands.
Das Netzteil darf zunächst liefern, ohne dass die volle Nennleistung als
verfügbar angenommen wird. Liegt U_bus über U_set, zeigt die Diagnose
„freigegeben, liefert nicht“.

Das Erreichen der Regelspannung ist kein Beweis einer geladenen Batterie.
Das 48-V-Netzteil darf nach Erreichen seiner kleinen SOC-Rückkehrreserve
ausgeschaltet werden und bei erneuter Unterschreitung der Aktivierungsschwelle
wieder stützen. Dadurch bleibt die Netzladung auf das Reserveband begrenzt;
Mindestschaltzeit und SOC-Hysterese verhindern schnelles Takten. Die 24-V-Stufe
bleibt bis zur PV-Erholung aktiv. Ein vom Netzteil erreichter kleiner
Reserve-SOC allein gibt den Inverter nicht wieder frei.

Keine Rückkehrregel, die ausschließlich U_bus > U_set verlangt: Das Netzteil
selbst kann diesen Wert prinzipbedingt nicht zuverlässig herstellen.
Kein periodisches Ein/Aus-Testen allein zur Leistungsschätzung.

## R5 — Schaltfolgen und Zuständigkeit

Ein gemeinsamer BM-Controller besitzt die drei Betriebsentscheidungen und
die 24-V-Quellenumschaltung. **Der Stellwert ist das numerische Entladelimit:**
0 W sperrt die AC-Batterieentladung, `inverter_max_power_w` gibt die konfigurierte
Leistung frei (2300 W ist nur der Standardwert). Der BM schreibt `number.set_value`
und liest den Wert zurück. Victron setzt den Sperrstatus selbst abhängig von
der Leistung; der BM liest diesen optional zur zusätzlichen Bestätigung und
schreibt niemals auf diesen Rückmeldeschalter. Bestehende unabhängige Netzteil-Automationen
werden bei Einführung gezielt abgelöst. Empfehlungen und reale Zustände
bleiben getrennt; HA-Serviceerfolg ist noch keine bestätigte Versorgung.

Bei automatischer Netzstützung sperrt und bestätigt der BM zuerst die
AC-Batterieentladung und schaltet danach die erforderlichen DC-Quellen um.
Bei fehlender Bestätigung greifen der Schutzpfad und die Diagnose. Akuter
Batterieschutz sperrt die AC-Entladung vorrangig.

24-V-Übernahme: Netzteil einschalten und bestätigen, Übergangszeit beachten,
danach DC/DC ausschalten und den erreichten Versorgungszustand prüfen.
Rückkehr: DC/DC einschalten/bestätigen, danach Netzteil ausschalten.
Bei fehlender Bestätigung bleibt die bisherige Quelle erhalten. Für die
bestehende überlappende Umschaltung gilt die dokumentierte Hardwareannahme;
bei anderer Hardware ist die Umschaltart ein eigener Parameter/Vertrag.
Mit 24,3 V am DC/DC und 24,05 V am Netzteil übernimmt das Netzteil nicht
schon deshalb, weil sein Schalter „an“ ist: Der DC/DC muss aus sein.

Vor Inverterfreigabe beendet der BM die automatische Netzstützung, bestätigt
die Schaltzustände der automatisch geführten Netzteile, stellt die
DC/DC-Versorgung her und prüft die Erholungsbedingungen. Ein automatisch
geführtes Netzteil, dessen Abschaltung unbestätigt ist, blockiert die
Inverterfreigabe und erscheint als Konflikt in der Diagnose.

### Manuelle Übergabe ab 0.70.0

„24-V-Stützung manuell“ und „48-V-Stützung manuell“ übergeben im koordinierten
Betrieb jeweils das zugehörige Netzteilrelais. Bei 24 V übernimmt der Bediener
zusätzlich den DC/DC-Wandler. Zum Übernehmen den betreffenden BM-Schalter
einschalten und die Bestätigung des manuellen Modus abwarten. Ausstehende
automatische Schaltaufträge für die betroffenen Relais müssen erst bestätigt
sein. Im Handbetrieb führt der Bediener die übernommenen Relais über die
vorhandenen Schalter und ist für die Umschaltfolge verantwortlich. Den
BM-Schalter ausschalten, um die betreffenden Relais an die Automatik
zurückzugeben.

Für die manuelle 24-V-Quellenumschaltung gilt diese Reihenfolge:

1. Zum Wechsel auf DC/DC den Wandler einschalten und seinen eingeschalteten
   Zustand bestätigen. Danach das 24-V-Netzteil ausschalten.
2. Zum Wechsel auf das Netzteil zuerst das 24-V-Netzteil einschalten, seinen
   eingeschalteten Zustand bestätigen und die Umschaltzeit abwarten. Danach
   den DC/DC-Wandler ausschalten.

Die Relaisübergabe und die Inverterfreigabe sind voneinander unabhängig.
Für den Inverter gelten seine eigenen Freigabe- und Schutzbedingungen. Er
darf parallel zum manuell geschalteten Netzteil entladen, auch wenn die
Haus-AC-Versorgung dieses Netzteil und damit den DC-Kreis speist. Automatisch
geführte Netzteile unterliegen weiterhin der koordinierten Schutz- und
Schaltfolge.

Beim Upgrade auf 0.70.0 setzt der BM gespeicherte manuelle
Einschaltanforderungen koordinierter Einträge einmalig auf Automatik zurück.
Manuelle Übergaben nach dem Upgrade bleiben über Neustarts erhalten. Externe
Schaltabweichungen im Automatikbetrieb behandelt der koordinierte Regler
weiterhin als Abweichungen vom Sollzustand.

Bei Anlagen ohne direktes numerisches Entladelimit gilt der bisherige
Handbetrieb. Einschalten des BM-Schalters erzwingt das Einschalten des
Netzteils und pausiert dessen Automatik. Ausschalten stellt die Automatik
wieder her.

## R6 — Prognose und Messung

Core und Executor verwenden dieselben erlaubten Zustände, Prioritäten,
Hysteresen und Leistungsgrenzen. Der Executor ergänzt aktuelle Messungen,
Bestätigungen, Mindestlaufzeiten und Fehlerbehandlung. Die Prognose nutzt
Fünf-Minuten-Teilschritte und aktiviert Stützung vor einem sonst in diesem
Teilschritt erwarteten Unterschreiten. Stundenaggregate kennzeichnen zusätzlich
den Zustand am Slotanfang; eine erst später geplante Umschaltung löst jetzt
keine Schalthandlung aus. Der Executor bestätigt Quellen vor dem Weitergehen;
fehlende Bestätigung verschiebt die nächste Handlung auf eine folgende Aktualisierung.

Die DC-Verbrauchsprognose bleibt unabhängig von der gewählten Quelle.
Anzeige zusätzlich: Batterieanteil, Netzteilanteil, Verluste, begrenzte
48-V-Unterstützung sowie freigegeben/tatsächlich eingeschaltet/liefernd.

Die Busspannung bestimmt, ob das 48-V-Netzteil bei der gegebenen Last
liefern kann. Der konfigurierte Maximalstrom begrenzt seine Leistung.
Seit 0.69.0 bilanziert die koordinierte Betriebsprognose die übertragbare
DC-Abgabe aus einer qualifizierten Ausgangsmessung oder den aus einer
qualifizierten Batteriebilanz ableitbaren Beitrag zur nativen Last. Die
Sicherheitsrechnung nimmt für diesen Beitrag null an. Zeitgrenzen und
Energiefluss sind im [Vertrag zur Betriebsprognose](F-SOC-RECONCILIATION.md#betriebsprognose-ab-0690)
beschrieben.

Auch Verbrauchslernen muss diese Unterscheidung verwenden: Keine pauschale
Leistungskorrektur allein aufgrund eines eingeschalteten Netzteils.

## R7 — Zahlenbeispiel der vorhandenen Parameter

Bei U48_set = 49,56 V und I48_max = 1,15 A beträgt die nominelle Obergrenze
56,994 W; bei 48,0 V im Stromlimit nur 55,2 W. Bei 35 W nativer 48-V-Last
und 24,7 W 24-V-Last mit eta_dcdc = 0,93 benötigt der Bus 61,56 W.
Das 48-V-Netzteil allein reicht dann selbst bei maximalem Strom nicht aus.
Bei höheren 24-V-Abendlasten wächst das Defizit.

Nach Übernahme durch das 24-V-Netzteil verbleiben 35 W am 48-V-Bus.
Damit kann das 48-V-Netzteil im angenommenen Kennlinienmodell diese Last
tragen, sofern sein Spannungsgate offen ist. Im Stromlimit kann der Rest
zunächst die Batterie laden; an der Sollspannung regelt die Abgabe zurück.
Bei reiner Lastdeckung und eta48 = eta24 = 0,89 ergeben sich ungefähr
35 / 0,89 + 24,7 / 0,89 = 67,1 W Netzaufnahme. Ein zusätzlicher Ladestrom
würde die Aufnahme erhöhen. Diese Zahlen sind Rechnungen, keine Messwerte.

## Abnahmefälle für die spätere Implementierung

1. Inverter freigegeben, Spannung unter U48_set: keine eigenständige
   Netzteilaktivierung. Bei Schutzbedarf zuerst Inverter sperren.
2. Inverter aus, ausreichend Reserve: beide Netzteile bleiben aus;
   beide DC-Lasten entladen die Batterie korrekt.
3. 24-V-Übernahme entfernt nur die 24-V-Last samt DC/DC-Verlust vom Bus.
4. U_bus oberhalb, unterhalb und an U48_set: Kennlinie und Stromlimit,
   keine pauschale Nennleistungsbuchung; native Last kleiner/größer als Cap.
5. Beide Netzteile an: 24 V vollständig versorgt; Batterie-Restbilanz und
   Netzaufnahme einschließlich Verlusten stimmen.
6. Netzteil hebt Spannung/SOC: keine daraus allein folgende Inverterfreigabe.
7. PV-Erholung beendet Stützung mit bestätigten Übergängen und Hysterese.
8. Fehlerhafte/verspätete Schalter, Sensorverlust und Neustart während
   Umschaltung: keine unversorgte 24-V-Schiene, kein unbemerkter Parallelbetrieb.
9. Leistungsdefizit oder leere Batterie: explizites Defizit statt erfundener
   Ladeleistung. Unsichere PSU-Leistung macht Schutzreserven nicht optimistisch.
10. Externe Automation schaltet gegen den Plan: Zuständigkeitskonflikt wird
    sichtbar und startet keinen zweiten unabhängigen Regler.
11. Energieerhaltung über jeden Slot, Tests mit virtueller Zeit und
    dokumentierte Golden-Diffs einschließlich Änderungen am Netzbezug.

## Einrichtung und Einführung

Im Support-Abschnitt `inverter_discharge_limit_entity` konfigurieren. Für die
untersuchte Anlage ist dies `number.victron_settings_ess_maxdischargepower`.
Unter der maximalen Inverterleistung (`inverter_max_power_w`) wird der
gewünschte Freigabewert eingestellt; 2300 W ist ein änderbarer Standardwert.
Der BM setzt 0 W zum Sperren und diesen konfigurierten Wert zum Freigeben.

Optional `inverter_block_switch_entity` als **reine Rückmeldung** auswählen;
bei der untersuchten Anlage `switch.victron_vebus_disablefeedin_228`.
Victron setzt diesen Status automatisch aus dem Leistungslimit. Der BM liest
ihn, schaltet ihn aber niemals. Eine verzögerte Bestätigung hält die nächste
Umschaltung an, ohne bereits bestätigte Sollwerte ständig neu zu schreiben.

Bei Übernahme die Automation „Setze ESS Max-Discharge-Power nach Inverter-Status“
und konkurrierende Netzteil-Automationen deaktivieren. Der BM übernimmt dann
auch die bisherige 0/2300-W-Zuordnung vollständig; hierfür ist keine zusätzliche
Automation mehr erforderlich. Andere unabhängige Victron-Funktionen bleiben
von dieser Umstellung unberührt. Hardwareparameter und Schwellen prüfen; der
Config-Flow verlangt einen DC/DC-Schalter für die 24-V-Übernahme und eine
positive Stromgrenze für ein konfiguriertes 48-V-Netzteil.

Ohne konfiguriertes numerisches Entladelimit bleiben bestehende Installationen
und gespeicherte alte Planner-Replays im bisherigen Modus. Ein ausgewählter
Rückmeldeschalter allein aktiviert keine direkte Steuerung. Im koordinierten
Modus ist der frühere unabhängige Spannungsregler inaktiv.

Neue Diagnose: `coordinated_support` am SOC-Sensor mit Sollzustand,
Bestätigungs-/Fehlergrund, Leistungsschätzquelle, Migration und prognostizierter
Unterversorgung. Forecast-Punkte enthalten zusätzlich `support_mode`,
`psu24_wh`, `psu48_wh` und `unserved_dc_wh`. Die vorhandenen Support-Spuren und
Verbrauchslagen bleiben kompatibel. Eine neue Diagrammgestaltung ist nicht
Teil dieser Version.

Tests: `tests/core/test_coordinated_support.py` und
`tests/ha/test_coordinated_actuation.py`; zwei zusätzliche Golden-Szenarien
frieren Reservebetrieb und PV-Erholung ein. Bestehende Golden-Szenarien
bleiben unverändert, da die Übernahme eine neue Entladelimit-Konfiguration erfordert.
Es wurde kein Live-System umgestellt.
