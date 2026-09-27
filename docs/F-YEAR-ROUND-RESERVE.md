# Ganzjährige Reservepolitik

> **Abgelöst ab 0.47.0:** Für Vorbereitungshorizont, DC-Vorrang, Netzteil-Haltung
> und Haltewert-Diagnose gilt [F-RESERVE-DC-FIRST](F-RESERVE-DC-FIRST.md).
> Die folgende Beschreibung bewahrt die historische Entscheidung.

> **Entscheidungs- und Änderungshistorie.** Der aktuelle Einstieg und
> etwaige ablösende Verträge stehen in [CURRENT_CONTRACTS](CURRENT_CONTRACTS.md).

Stand 0.45.1, 26.09.2026. Ergänzt
[F-COORDINATED-DC-SUPPORT](F-COORDINATED-DC-SUPPORT.md).
Bei vorhandener koordinierter Invertersteuerung ist **Aktiv** die Voreinstellung:
Die Regeln greifen sofort anhand der Prognosen und des aktuellen SOC, ohne Lern-
oder Wartephase. Explizit gespeichertes **Aus** oder **Schattenbetrieb** bleibt
respektiert. Ohne numerischen Inverteraktor bleibt die bisherige Politik bestehen.
Diese Betreiberentscheidung ersetzt die 48-Stunden-Pflicht aus 0.45.0.

## Anforderungen und Priorität

1. Versorgung und technische Schutzgrenzen einhalten.
2. Vermeidbare PV-Einspeisung durch rechtzeitig geschaffenen Freiraum verhindern.
3. Dafür zuerst 48-/24-V-Verbrauch aus der Batterie bedienen, danach Hausverbrauch
   und tatsächlich geeignete Überschusslasten.
4. Übrige Energie möglichst als Reserve erhalten. Kein festes Winter-SOC und keine
   zugesagte Notstromdauer; Netzversorgung der Verbraucher ist dafür zulässig.
5. Zusätzlichen Netzbezug, Verluste und Schaltwechsel begrenzen.

Es gibt keinen Ladeauftrag zum Wiederherstellen einer aus dem Netz gespeisten
Reserve. Hoher SOC ist ein Betreiberziel für Ausfallreserven, kein behauptetes
Alterungsoptimum. Die konfigurierte Obergrenze (hier 95 %) und BMS-Grenzen gelten.
Auch bei LFP beeinflussen Lager-SOC und Temperatur die kalendarische Alterung
([Originalstudie](https://mediatum.ub.tum.de/doc/1651485/document.pdf)).

## Planung und gemeinsame Energiegrenzen

`ReserveParams` aktiviert die Politik ausdrücklich. Die alte skalare Schwelle
bleibt aus Kompatibilitätsgründen im Ergebnis, steuert diese Politik aber nicht.
`core/reserve.py` simuliert den gesamten vorhandenen Horizont auf Fünf-Minuten-
Schritten; Teilstunden behalten ihre Energie und tatsächliche Dauer. Die feinere
Simulation erzeugt keine erfundenen zeitlichen Details innerhalb der ursprünglichen
PV-/Lastprognose: Stundenwerte werden gleichmäßig verteilt.

Zwei rückwärts berechnete Energieobergrenzen beantworten unterschiedliche Fragen:

- **DC-Obergrenze:** Welche gespeicherte Energie ist zulässig, wenn bis zum
  kommenden Überschuss zuerst der verfügbare DC-Verbrauch aus der Batterie kommt?
- **Gesamtobergrenze:** Welche Energie ist noch zulässig, wenn zusätzlich die
  verbleibenden nutzbaren AC-Entladefenster verwendet werden?

Für jeden Schritt wird vom nächsten Grenzwert die speicherbare PV-Energie
abgezogen und die mögliche Entladeenergie addiert; die Grenze ist höchstens die
Batteriekapazität bei maximalem SOC. DC/DC-, Lade- und Inverterleistungen sowie
Wirkungsgrade begrenzen diese Rechnung. Negative rechnerische Grenzen zeigen
unmögliche vollständige Aufnahme; die Vorwärtssimulation hält trotzdem die
technischen Entladegrenzen ein. PV oberhalb der Ladeleistung wird dabei nicht
als durch zusätzliche Entladung lösbarer Kapazitätsmangel behandelt.

Die Vorwärtsprüfung nutzt zuerst DC-Verbrauch, sobald er für den Freiraum nötig
ist. Zusätzliche AC-Nutzung beginnt spätestens im letzten ausreichenden Fenster;
Abend und Nacht sind Teil dieses Horizonts. Das unmittelbar erlaubte AC-Budget
wird in ein begrenztes Inverterlimit umgerechnet. Sind Netzteile aktiv, beträgt
es null. Bei ausreichendem Freiraum bleibt AC-Batterieversorgung gesperrt;
verfügbare Netzteile übernehmen den DC-Verbrauch. Neue PV darf wieder laden.

Alle Simulationen des Lastallokators, der Kaskaden und der Stressprüfungen laufen
durch dieselbe Reservepolitik. Optionale Lasten dürfen gegenüber dem zugehörigen
Reserve-Basisplan keinen zusätzlichen Import kaufen. Mehrbezug gegenüber der
bisherigen Politik wird separat als geschätzter Preis der Reservehaltung gezeigt.
Der pessimistische Verlauf zeigt Reserveverluste, verhindert aber nicht generell
die erforderliche Vorbereitung. Die Schutzregeln optionaler Lasten bleiben aktiv.

Verwertbare obere Prognosebänder werden verwendet. Bei fehlenden oder kollabierten
Bändern gilt `reserve_upper_pv_factor`, Standard **1,20** (zulässig 1–1,5), als
**unkalibrierter** Ersatz. Der Faktor ist unabhängig vom bisherigen Faktor 1,05
auf dieser Anlage. PV-Leistung und die zeitliche PV-Prognose begrenzen das Szenario.
Er ist keine garantierte statistische Obergrenze.

## Physische Lieferfähigkeit und gespeichertes Ziel

Das 48-V-Netzteil der untersuchten Anlage ist auf 49,56 V und 1,15 A begrenzt:
maximal etwa 57 W, oberhalb der Sollspannung kein verlässlicher Beitrag. Eine
frische Busspannung ersetzt im Reservebetrieb den alten festen SOC-Proxy. Für
zukünftige SOC-Werte außerhalb der aktuellen Hysteresebreite und im pessimistischen
Stresslauf wird daraus keine sichere Spannung bzw. Lieferfähigkeit extrapoliert.
Die Card bezeichnet dies als Schätzung, nicht als Leistungsmessung.

Ein zu schwaches 24-V-Netzteil verdrängt den funktionierenden DC/DC nicht. Auch
bei ausgelagertem 24-V-Verbrauch kann die native 48-V-Last weiter entladen.
Halteziel, tatsächlicher SOC, erwartetes Minimum und Restentladung werden getrennt
angezeigt. Das Ziel sinkt nur bei geplanter Vorbereitung, nicht bei unfreiwilligem
Verlust. Neustarts und Messlücken erzeugen keine rückwirkenden Energiegewinne.

Ein eingeschaltetes 48-V-Netzteil kann technisch bedingt geringe Ladeanteile
liefern. `psu48_battery_charge_wh` bilanziert diese getrennt. Solche Zuwächse
erhöhen das persistierte solare Halteziel nicht und lösen bei dunklem Horizont
keine AC-Freigabe aus. Das Netzteil wird oberhalb Ziel plus Hysterese wieder
freigegeben, soweit kein manueller Zwang vorliegt; es gibt keine Nachladeleistung
oder Spannungserhöhung zur Zielerreichung.

Ein höheres solares Ziel benötigt zwei aufeinanderfolgende frische Beobachtungen
mit positiver gemessener PV-Leistung, keinem gemessenen Import und ausgeschalteten
Netzteilen. Dafür die vorhandenen Optionen `operation_pv_power_entity` und
`operation_import_power_entity` korrekt zuordnen (W/kW, maximal 30 Sekunden alt).
Fehlende Quellen erlauben keine behauptete solare Gutschrift. Das ist eine
konservative Herkunftsprüfung, keine separate geeichte Batterie-Energiebilanz.

## Ausführung, Schattenbetrieb und Rückfall

Neue Optionen in den Planner-Einstellungen:

| Option | Bedeutung |
|---|---|
| `reserve_mode` | `off`, `shadow`, `active`; Standard `active` mit koordiniertem Inverteraktor, sonst `off` |
| `reserve_upper_pv_factor` | Unkalibrierter oberer Ersatzfaktor, Standard 1,20 |
| `reserve_grid_available_entity` | Frisches Netzsignal, binary_sensor oder sensor |
| `reserve_transfer_verified` | Betreiber bestätigt den unabhängig von HA geprüften 24-V-Rückfall |

Reservebetrieb erfordert die koordinierte Steuerung mit numerischem Inverterlimit.
Die Prognoseplanung beginnt ohne vorherige Messhistorie. Für Netzteilbeiträge
ist weiterhin ein frisches Netzsignal erforderlich; für die Übernahme der 24-V-
Schiene zusätzlich der verifizierte autonome Rückfall. Fehlende Nachweise sperren
nur die betreffenden Quellen, nicht den aktiven Reserveplanner. Anerkannte Netzwerte sind
`on`/`AC_INPUT_1`/`AC_INPUT_2` bzw. `off`/`DISCONNECTED`/`NOT_CONNECTED`.
Unbekannte oder über 30 Sekunden alte Werte gelten nicht als verfügbare Quelle.
Das Signal muss auch bei konstantem Zustand regelmäßig aktualisiert werden.

**Keine 48-Stunden-Wartezeit:** Ein frischer SOC und verwertbare PV-/Verbrauchs-
prognosen reichen für die sofortige Planung. Messungen für Quellenbestätigung,
Schutzgrenzen und Energiebilanz bleiben notwendig; sie trainieren keine neue
Freigabeschwelle. Halteziel und Diagnose-Beobachtungszeit werden persistiert.
Lücken über zehn Minuten und Ausfallzeiten erzeugen keine fiktive Beobachtung.
Konfigurationsänderungen aktualisieren das Ziel ohne erneute Wartephase.

**Schattenbetrieb bleibt optional:** Nur wenn ausdrücklich gewählt, wird der
bisherige Plan ausgeführt und der Reservekandidat diagnostiziert. Ein Wechsel
auf Aktiv wirkt beim nächsten gültigen Planungslauf, auch mit null vorherigen
Beobachtungsstunden. Das kompatible Diagnosefeld `shadow_ready` ist immer wahr;
`shadow_required_hours` beträgt null. Es gibt keine automatische Kalibrierung.

Bei Netzverlust stellt die aktive Steuerung zuerst den DC/DC-Pfad wieder her,
bevor sie auf einen eventuell nicht erreichbaren Inverter wartet. Erst bestätigte
Wiederherstellung erlaubt das Abschalten der bisherigen 24-V-Quelle. Aktor-
Bestätigungen, Mindestschaltzeiten und manuelle Vorgaben bleiben in der bestehenden
koordinierten Schaltfolge. Aktive Planung wird spätestens alle fünf Minuten neu
bewertet; die Freigabe erfolgt mit dem aktuellen begrenzten Inverterlimit.

**Software allein garantiert keinen Rückfall bei ausgefallenem HA.** Vor Setzen
von `reserve_transfer_verified` muss der reale 24-V-Pfad sowohl bei Netzausfall
als auch bei nicht verfügbarem HA funktionieren. Das Victron-ESS-Entladelimit
ist eine Netzbetriebsregel und keine gleichwertige physische Inselabschaltung
([ESS-Handbuch](https://www.victronenergy.com/media/pg/Energy_Storage_System/en/configuration.html)).

## Notfalleinspeisung

Die bestehende automatische F-FEEDIN-Logik leitet aktuelle PV vor der Speicherung
ins Netz. Sie entlädt die Batterie nicht und weist lediglich eine Verschiebung
späterer Einspeisung nach. Damit ist der geforderte Gesamtnutzen nicht bewiesen:
Im Reservebetrieb wird dieser Automatismus mit `reserve_no_emergency_benefit`
gesperrt. Eine aktive manuelle Vorgabe bleibt als Ist-Zustand sichtbar; sie erlaubt
keine automatische Fortsetzung am Folgetag.

Ein neuer automatischer Batterie-Exportauftrag ist deshalb **nicht implementiert**.
Er dürfte erst nach einem erweiterten physikalischen Wirkungsnachweis freigegeben
werden: frische Messungen, auch konservativ bestätigte Sättigung, ausgeschöpfte
DC-/Haus-/Nutzlastfenster, zusätzlich tatsächlich aufnehmbare PV und positiver
Gesamtnutzen. Energie, Leistung und Endzeit müssten dann gemeinsam begrenzt und
bei Prognoseänderung sofort widerrufen werden. Reine Zeitverschiebung oder ein
allein überschrittener SOC reicht ausdrücklich nicht.

## Nachvollziehbarkeit und Messdaten

Die Forecast-Card und das additive `reserve`-Attribut zeigen Halteziel, Ist-SOC,
erwartetes Minimum, Freiraumbedarf, geplanten Beginn auf Fünf-Minuten-Ebene,
Inverterlimit, verbleibende Entladung, physische Zielerreichbarkeit, PSU-Anteile,
unbeabsichtigte Netzteilladung, zusätzliche Importenergie und Schattenfortschritt.
Die Kurve enthält zeitabhängige Gesamt- und DC-Obergrenzen.

Das bestehende [Betriebsjournal](F-OPERATION-HISTORY.md) archiviert veröffentlichte
Prognosen samt Bändern und Konfiguration sowie zugeordnete spätere Istmessungen.
Ein verknüpftes `reserve_policy`-Ereignis hält die Entscheidung fest. Das Archiv
bleibt begrenzt: maximal sieben Tage/50.000 Ereignisse/32 MiB, Tagesberichte
30 Tage. Für langfristige Abdeckungsanalysen Diagnosearchive regelmäßig exportieren,
bevor diese Grenzen greifen. Noch erfolgt keine automatische statistische
Kalibrierung des Faktors. Jahresmesswerte ersetzen keine damaligen Prognosen.

## Abnahme und Einführung auf dieser Anlage

Automatische Nachweise: dunkler Horizont, DC-vorrangige Entladung, genutzte frühe
AC-Fenster, Teilstunden, P90/Peak-Begrenzung, zu schwache PSU, offene/geschlossene
Spannungsgates, getrennte Netzteilladung, Strict-Surplus einschließlich Kaskaden,
Neustart, Messlücke, sofortiger Start ohne Historie, optionale Schattenwahl, bestätigte begrenzte
Inverterleistung, Netzverlust und gescheiterte DC/DC-Bestätigung. Bestehende
Golden-Szenarien bleiben unverändert; drei Reserve-Szenarien kommen hinzu.

Der zusätzliche Bezug in diesen neuen Golden-Szenarien ist beabsichtigte
Reservehaltung gegenüber einer stärker entladenden Politik, keine für Nutzlasten
gekaufte Energie. Bei den dort angenommenen 52 V und nativen 35 W bleibt reale
Restentladung bestehen. Erwartete Importe: Winter 5,871348 kWh, sonniger Winter
2,952247 kWh, Sommer 0,663483 kWh. Im Sommer verbleibt ohne hinreichende nutzbare
Lasten trotz Vorbereitung Export; die Politik erfindet keine Entladesenke.

Einführung über den üblichen HACS-Weg:

1. Mit dem Update startet die neue Politik auf vorhandenen koordinierten
   Installationen sofort, sofern kein Modus ausdrücklich gespeichert ist.
   Ein zuvor gewähltes Aus/Schatten bei Bedarf auf **Aktiv** ändern.
2. Netzsignal, Leistungsquellen, Schutzgrenzen und Hardwareleistungen zuordnen.
   Ohne Netzsignal bleiben Netzteilbeiträge gesperrt; Inverter-Vorbereitung für
   prognostizierte PV-Spitzen ist trotzdem möglich. Bei nachgewiesenem Netzausfall
   gilt der Quellenrückfall mit Invertersperre im Netzbetrieb.
3. Für 24-V-Netzstützung den autonomen Rückfall bei Netzausfall und HA-Ausfall
   praktisch verifizieren. Ohne Bestätigung bleibt die Schiene am DC/DC.
4. Konkurrenz um Aktoren auflösen: insbesondere
   `automation.energie_steuerung_48v_netzteil` auf dieser Anlage ablösen, sobald
   die koordinierte Steuerung deren vollständige Verantwortung übernimmt.
   Regeln vorher sichern; die unabhängige Netzladesperre getrennt auf
   Zusammenspiel prüfen und nicht pauschal abschalten.
5. Importdifferenz, DC-Pfade, Freiraum, Restentladung und Schaltbestätigungen
   während des laufenden Betriebs beobachten. Bei Bedarf ist Schattenbetrieb
   freiwillig wählbar; es gibt keine Mindestdauer.
6. Rückkehr zur bisherigen Plannerpolitik über **Aus**. Keine externe
   Konkurrenzautomation unkoordiniert parallel aktivieren.

Code und virtuelle Tests ersetzen keine reale Ausfallprüfung. Das Update ändert
keine gespeicherten Live-Optionen und schaltet keine externe Automation ab;
bei fehlender Moduswahl ändert sich jedoch ausdrücklich der wirksame Standard.
