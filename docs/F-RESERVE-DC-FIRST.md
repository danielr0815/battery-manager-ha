# Reservebetrieb ab 0.47.0: DC zuerst, AC nur für benötigten Speicherraum

> Ab 0.47.1 ersetzt [SOC-Erhaltung](F-RESERVE-SOC-PRESERVATION.md) die hier
> beschriebene Entladung nativer DC-Lasten bis zur Schutzschwelle. Die folgenden
> Befunde und Abnahmewerte dokumentieren den historischen Stand 0.47.0.

Dieser Vertrag ersetzt die Halte- und Horizontregeln aus
[F-YEAR-ROUND-RESERVE](F-YEAR-ROUND-RESERVE.md). Er gilt im aktiven Reservebetrieb;
Schattenbetrieb berechnet dieselbe Entscheidung ohne sie auszuführen.

## Befund

Die Untersuchung vom 27.09.2026 zeigte bei 38 % SOC eine AC-Freigabe bis 2300 W.
Der zusätzliche Prognosetag übermorgen verursachte eine heutige Entladeanforderung,
obwohl heute und morgen auch mit 20 % PV-Aufschlag ohne zusätzliche AC-Entladung
in die Batterie passten. Die gelbe T*-Linie bei 20 % war im Reservebetrieb kein
optimiertes Halteziel. Ein historischer Haltewert von 0 konnte zudem eine
48-V-Schutzanforderung bei 6 % SOC überschreiben. Umgekehrt schaltete die alte
wirtschaftliche Halteregel Netzteile auch bei hohem SOC ein.

## Verbindliche Regeln

1. **DC zuerst:** Native 24-/48-V-Lasten verwenden die Batterie, solange die
   vorhandenen Schutzbedingungen es erlauben. Fehlender Vorbereitungsbedarf
   sperrt AC; er veranlasst keine vorsorgliche Netzübernahme.
2. **Heute und morgen:** Zusätzliche AC-Entladung bereitet nur PV innerhalb
   dieser beiden lokalen HA-Kalendertage vor. Das sind keine festen 48 Stunden;
   Teilstunden und 23-/25-Stunden-Tage bleiben erhalten. Spätere Prognosetage
   werden mit fortschreitendem Kalendertag dargestellt, ohne rückwirkenden
   Einfluss auf heutige Entladebudgets.
3. **Nur nötige und rechtzeitige AC-Abgabe:** Bestehende obere PV-Bänder bzw.
   der konfigurierte skalare Rückfall bestimmen den Speicherbedarf. Natürlicher
   DC-Verbrauch schafft zuerst Platz. Benötigte zusätzliche AC-Abgabe erfolgt
   möglichst spät; eine frühere einzige Gelegenheit muss genutzt werden.
   Eigenverbrauch des Inverters ist keine eigenständige Entlademöglichkeit.
4. **Physische Erreichbarkeit:** Referenz- und Rückwärtsrechnung berücksichtigen
   DC- und AC-Untergrenzen getrennt, Leistungen, Wirkungsgrade, Eigenbedarf und
   Batterieobergrenze. Unvermeidbarer Export, etwa wegen begrenzter Ladeleistung,
   ist keine Verpflichtung zu zusätzlicher Batterieentladung. Es wird keine
   neue feste Nacht- oder Reservegrenze eingeführt.
5. **Schutz hat Vorrang:** Bestehende Netzteil-Schwellen, Hysteresen, manuelle
   Vorgaben, Netzverfügbarkeit und Umschaltfreigaben bleiben verbindlich. Unbekannte
   Netzteilleistung wird nicht als gesicherte Versorgung angerechnet. Eine
   Schaltanforderung gilt erst nach Gerätemeldung als bestätigt. Aktuelle
   SOC-Schutzbedingungen werden auch vor tatsächlicher AC-Freigabe geprüft.
   Während der Planung geänderte manuelle Wünsche verwerfen alte Ziele;
   Anforderungsänderung und Aktuierung verwenden denselben Lock.
6. **Herkunft ist Diagnose:** `reserve_hold_soc_percent` bleibt für alte
   Aufzeichnungen lesbar, beeinflusst aber keine Schaltentscheidung. Fehlende
   Betriebs-PV-/Importmessungen bedeuten unbekannte Herkunft. Sie erzwingen
   weder einen Haltewert von 0 noch eine Beobachtungswartezeit.

## Energie-Berechnung und Grenzen

Die Planung bereitet kleine physische Energieübergänge vor, berechnet eine
Referenz mit maximal zulässiger AC-Nutzung und bestimmt rückwärts die noch
zulässige Eingangsenergie. Dabei werden die erreichbare Energie und der
unvermeidbare Referenzexport gemeinsam berücksichtigt. Der abschließende
Vorwärtslauf verwendet nur die jetzt erforderliche AC-Leistung.

Eine rein additive Rückwärtsformel genügt nicht: AC kann die Batterie nicht unter
seine technische Untergrenze entladen; DC kann darunter weiterhin Energie nutzen.
Die stückweise Energieberechnung wird deshalb gemeinsam mit der Simulation
verwendet. Schutz-Hysterese und manuelle Vorgaben bleiben diskrete Entscheidungen
außerhalb dieser Energieabbildung. Unsichere spätere Quellenwechsel begründen
keine globale Optimalitäts- oder Null-Export-Garantie. Jede reguläre Neuplanung
verwendet die dann gemessenen Zustände und aktuellen Prognosen.

Mehr AC-Netzbezug gegenüber der früheren weit vorausschauenden Entladung ist
beabsichtigt, wenn dadurch Batterieenergie für DC erhalten bleibt. Weniger frühe
DC-Netzübernahme ist ebenfalls beabsichtigt. Zusatzlasten behalten ihre bestehenden
Prüfungen gegen zusätzlichen Netzbezug. Der Modus führt keine gezielte Netzladung
der Batterie und keine Batterieeinspeisung ein.

## Diagnose und Oberfläche

Der Kern liefert einen unveränderlichen `ReserveDecision` in der Trajektorie.
Die HA-Ausgabe unter `reserve` übernimmt daraus:

| Feld | Bedeutung |
|---|---|
| `preparation_horizon_end` | Tatsächlich abgedecktes Ende des bindenden Horizonts als Zeitpunkt |
| `inverter_limit_w` | Aktuell erlaubte Leistung; kein gemessener Istwert |
| `headroom_wh` | Jetzt zusätzlich benötigter Speicherraum nach natürlichem DC-Verbrauch im oberen PV-Szenario |
| `unavoidable_export_wh` | Verbleibender Referenzexport im bindenden Horizont |
| `decision_reason` | Kein Bedarf, nötige Vorbereitung, Schutz, manuelle Unterstützung oder fehlende AC-Restlast |

Die Karte zeigt diese Gründe auf Deutsch/Englisch mit HA-Zeit. Im aktiven Modus
entfällt T* einschließlich Hover und zugänglicher Beschriftungen. Die technische
Inverter-Untergrenze wird separat bezeichnet. Alte Haltefelder sind ausschließlich
historische Diagnose; sie sind keine physischen Zielwerte. Aus/Schatten behalten
die für den tatsächlich ausgeführten alten Modus relevante T*-Anzeige.

## Upgrade und Nachweise

Alte Reserve-Persistenz bleibt lesbar. Automatische wirtschaftliche
Netzteil-Halteanforderungen oberhalb der vorhandenen Erholungsschwellen werden
beim Upgrade neu bewertet. Der bestätigte Gerätezustand bleibt erhalten, bis die
geordnete Rückkehr zur Batterie bestätigt wurde. Fehlgeschlagene Übergaben werden
auch nach Neustart erneut abgeglichen. Schutz und manuelle Vorgaben bleiben aktiv.

Regressionen: `tests/core/test_reserve.py`,
`tests/core/test_reserve_reachability.py`, `tests/core/test_reserve_energy.py`,
`tests/core/test_reserve_live_regression.py`, `tests/core/test_replay.py`,
`tests/ha/test_reserve_policy.py`, `tests/ha/test_reserve_runtime.py` und
`tests/browser/cards.spec.mjs`. Die Live-Fixture enthält ausschließlich anonymisierte
Anlagenparameter und Energiezeitreihen, keine Entity-IDs oder Kontodaten.


## Beabsichtigte Änderungen der Golden-Energiebilanzen

Nur die drei aktiven Reserve-Szenarien in `golden_topology.json` ändern sich.
Sämtliche Szenarien der bisherigen Politik bleiben identisch.

| Szenario | Netzbezug vorher → nachher (Wh) | Export vorher → nachher (Wh) | Begründung |
|---|---:|---:|---|
| `reserve_dark_winter` | 5871,348 → 4118,539 | 0 → 0 | DC nutzt zuerst Batterieenergie: Batterieentnahme 1948,454 → 3677,752 Wh, 24-V-Netzteilabgabe 1710 → 150 Wh. AC bleibt aus. |
| `reserve_sunny_winter` | 2952,247 → 1750 | 0 → 0 | Wirtschaftliche DC-Netzteil-Haltung entfällt; notwendige AC-Abgabe bleibt 1070 Wh. |
| `reserve_summer_preparation` | 663,483 → 450 | 14989,891 → 15318,641 | DC-Netzteil-Haltung entfällt. AC-Abgabe bleibt 2140 Wh; der Inverter läuft nicht mehr allein zum Verbrauch seiner 15 W Eigenbedarf in PV-Überschussstunden. Der dadurch höhere Export ist keine verlorene nutzbare AC-Versorgung. |

In keinem aktualisierten Golden-Szenario steigt der Netzbezug. Unvermeidbarer
Export wird sichtbar belassen, statt ihn durch nutzlosen Eigenverbrauch zu senken.


## Funktions- und Laufzeitnachweis am untersuchten Prognosestand

Die anonymisierte Fixture vom 27.09.2026 um 15:10:59 Uhr (UTC+02:00) enthält
38 % Start-SOC, 5 kWh Batterie und 57 Prognoseslots. Der unveränderte 0.46.1-Kern
gibt im vollen Horizont aktuell 2300 W frei und entlädt heute 414,15 Wh über AC.
Die korrigierte Politik erlaubt aktuell 0 W und verwendet heute 0 Wh für AC.
Bei auf heute/morgen begrenzter Prognose bleiben AC-Abgabe und Export auch mit
20 % PV-Aufschlag bei 0 Wh; die obere SOC-Spitze beträgt rund 91,74 %.
Eine veränderte Prognose übermorgen lässt die heutigen Entscheidungen unverändert.
Die spätere Vorschau darf sich mit ihrem vorgerückten Kalenderfenster verändern.

Für die vollständige 57-Slot-Aufzeichnung einschließlich der rollenden späteren
Vorschau steigt der geplante Netzbezug bewusst von 2041,28 auf 2546,33 Wh.
Gleichzeitig steigt der SOC am Horizontende von 67,97 auf 79,59 %: Rund 581 Wh
mehr verbleiben in der Batterie, statt früh über den Inverter abgegeben zu werden.
Der prognostizierte Mindest-SOC steigt von 5,38 auf 11,69 %. Diese Werte sind
Ergebnisse der Eingaben, keine neu eingeführten Reservegrenzen. Heute und morgen
bleiben ohne Export; die spätere Vorschau enthält 16,31 Wh Export. Die zusätzliche
Netzversorgung der AC-Lasten erfüllt hier ausdrücklich den gewünschten DC-/SOC-Vorrang.

Der vollständige Planner wurde zusätzlich mit der ursprünglichen Aufzeichnung
(57 Slots, drei Zusatzlasten, zwei Appliances) unter demselben Python 3.14.7
verglichen. Je Stand wurden drei unprofilierte Läufe bei freier CPU gemessen;
Zeiten sind Beobachtungen auf dieser Entwicklungsmaschine, kein HA-Hardwareversprechen.

| Stand | Einzelzeiten (s) | Median (s) |
|---|---|---:|
| 0.46.1 | 8,47549 / 8,16964 / 8,24478 | 8,24478 |
| Korrektur vor Wiederverwendung/Ausschlussprüfung | 19,49545 / 19,57841 / 19,68329 | 19,57841 |
| Fertige Korrektur | 9,04771 / 9,04090 / 8,64161 | 9,04090 |

Die zusätzliche Rechenarbeit entstand überwiegend durch mehr Kandidaten im
Speicher-Vorbereitungspass. Unveränderliche Reserveproben und Schrittbudgets werden
nun innerhalb eines einzigen Planungslaufs wiederverwendet. Eine sichere obere
Grenze für rettbaren Export schließt aussichtslose Allokationsproben früher aus;
Prüfreihenfolge und bereits veröffentlichte Ablehnungsgründe bleiben erhalten.
Das reduziert die gemessene Laufzeit um 53,82 %, ohne Energieergebnisse zu verändern.
Gegenüber 0.46.1 bleiben in dieser Messung rund 9,66 % zusätzliche Laufzeit für die
korrigierte Planung. Hintergrundinitialisierung und kooperativer Abbruch bleiben
unverändert; es werden keine produktiven Wartezeiten in Tests abgewartet.

Reproduktion mit einer eigenen Diagnose-Aufzeichnung:

```sh
uv run python scripts/benchmark_plan.py planner-recording.json --repeat 3
```

## Abnahme des Arbeitsstands

Der abschließende gemeinsame Lauf einschließlich der Haushaltsgeräte-Erweiterung
besteht aus 2227 erfolgreichen Python-Tests, 60 Frontendtests und 18 Browsertests.
Die Gesamt-Coverage beträgt 97,78 %; sämtliche 21 Kernmodule erreichen 100 %,
alle HA-Module mindestens 95 %. Ruff, Formatprüfung, mypy über 50 Quellmodule,
ESLint, Prettier, Bundle-Abgleich und Versionskonsistenz für 0.47.0 sind erfolgreich.
Die Prüfung erfolgte lokal; Veröffentlichung und Installation sind separate Schritte.
