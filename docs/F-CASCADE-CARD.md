# Kaskadenkachel: Energiefluss und Diagramme

Stand: v0.37.0. Die vorhandene `battery-manager-cascade-card` verwendet weiterhin
`entity`, `title` und `hours` (6–96, Standard 48). Kein neuer Kartentyp nötig.

## Verhalten

- Oben steht die konfigurierte lineare Kette. Pfeile im Ablauf geben die
  Energiequelle und den Empfänger entlang dieser Kette an, einschließlich
  Versorgung der Endlast durch einen bestimmten Speicher.
- Die Tagesauswahl gilt für Miniaturen, Gerätedaten, Details und Ablauf.
  Die Aktion „Prognose öffnen“ bei Root-Kennzahlen wählt automatisch heute
  bzw. morgen; bei der Speicher-Kennzahl den gesamten Planungshorizont.
  Der Wert selbst öffnet bei vorhandener Zuordnung die Messhistorie.
- Aufnahme ist `charge.energy_wh`, Speicherung ist `stored_energy_wh`,
  Akkuentnahme ist `discharge.energy_wh` inklusive modellierter Verluste.
  Die Kennzahl „Aus Speichern · an Endlast“ summiert nur Aux-Endlastenergie.
  Diese Größen dürfen nicht addiert oder gleichgesetzt werden.
- Der verbleibende Root-Anteil nach Ladeeingängen und Root-Endlastversorgung
  wird als aufklappbare „Weitere Energie / Bilanzrest“ ausgewiesen. Für einzelne AC-Ausgänge
  enthält der Sensor keine eigene Durchleitungsenergiemenge; dort wird nur der
  geplante Aktivzustand gezeigt. Es werden keine Messwerte erfunden.
- Diagramme sind ausdrücklich Planung (gestrichelt). SOC zwischen Stützstellen
  ist linear interpoliert. Seit v0.38.2 liegen Stützstellen an den geplanten
  Lade-, Entlade- und Übergangsgrenzen. Leistung ist Wh geteilt durch die
  tatsächliche Aktivitätsdauer, keine momentane Messung. Kumulierte Energie
  integriert diese Leistung ab dem Beginn des ausgewählten verfügbaren Plans.
- Heute umfasst den verbleibenden Plan, keine Tageshistorie. Der vorhandene
  Ist-Tageswert wird separat beschriftet. Ohne SOC-Prognose wird auch bei
  bekanntem Start-SOC keine künstliche konstante Kurve erzeugt.
- HA-lokale Zeitstempel, Tagesgrenzen und Sommerzeitwechsel werden in der
  konfigurierten HA-Zeitzone ausgewertet, unabhängig vom Browserstandort.
- Alle Diagramme teilen den Zeitcursor (Hover, Tippen/Ziehen; Pfeiltasten,
  Home/End). Die Prognose-/Detailschaltflächen öffnen die gemeinsame Detailansicht.
  Dort wechseln Speicher zwischen SOC/Aufnahme/Akkuentnahme, Energiekurven
  zwischen mittlerer Leistung und kumulierter Energie.
- Benachbarte Slots mit identischen Aktivitäten, Quellen und mittleren
  Leistungen werden im Ablauf zu einer Phase zusammengefasst. Die aufgelösten
  Aktivitätsintervalle bleiben Grundlage der Energie- und Cursorberechnung.
- Miniaturen sind maximal 600 px und Detaildiagramme maximal 900 px breit,
  damit Höhe, Linien und Achsentext auf breiten Dashboards nicht übergroß werden.
  Cursorwerte verwenden dieselbe Maximalbreite und bleiben links ausgerichtet.
- Namen, Kennzahlen, Quelle-Ziel-Zeilen und Cursorwerte stehen in umbrechendem
  HTML außerhalb des horizontal scrollbaren SVG-Bereichs. Die Kartenhöhe ist
  automatisch; Touch-Scrollen der Seite bleibt möglich.

## Verifikation

`node tests/frontend/cascade-card.test.mjs` prüft Slotgrenzen, Teilstunden,
Energieintegration, Tages-Clipping, DST, HA-Zeitzone, Energiearten, fehlende
Daten, Escape-Verhalten und Phasengruppierung. Der CI-Job `frontend` führt die
Tests ohne npm-Abhängigkeiten aus. Browserprüfung zusätzlich mit lokalem
Playwright-MCP und synthetischen Sensordaten bei Desktop- und Mobilbreite.

### Desktop-Layout-Prüfung (v0.36.1)

Mit lokalem Playwright-MCP und aktuellen HA-Anlagendaten in einer temporären
Vorschau geprüft (2026-09-05): Bei 1920 und 2800 px Viewportbreite bleiben
Miniatur-SVGs rund 109 px, Detail-SVGs rund 313 px hoch. Bei 768, 390 und
280 px entsteht kein horizontaler Seitenüberlauf; bei 280 px scrollt nur der
Diagrammbereich. Der Node-Regressionstest prüft die Zeitauswahl bei skalierten
SVGs und horizontalem Scrollversatz einschließlich der Achsengrenzen.

Die gemeinsame Zeitauswahl steht vor den Speicher- und Endlastdiagrammen. Sie
steuert auch die Detailansicht und den geplanten Ablauf der jeweiligen Kaskade.
Die explizit mit heute, morgen oder Gesamtplan beschrifteten Kennzahlen bleiben
auf ihren angegebenen Zeitraum bezogen.

Status- und Sprachregeln: [Deutsch und Englisch](F-LANGUAGE-SUPPORT.md).
`recovering` bezeichnet eine noch ausstehende Wiederaufladung, keine aktuell
gemessene Ladung. Deutsche Karten verwenden „Eingang“ für die Root-Versorgung.


## Aktivitäts- und Historienansicht (v0.37.0)

- `activity_intervals` enthält Art, Last-ID, Beginn/Ende und `exact`. Ladezeiten
  stammen direkt aus `CascadeMemberFlow.charge_hours`; Aux- und Endlastzeiten
  aus den Quellsegmenten einschließlich ihrer Offsets. Das verändert weder
  Allokation noch Schaltverhalten. Unbekannte Ladezeiten sind schraffierte
  Zeitfenster, keine behaupteten exakten Schaltzeiten. Die Spuren beschreiben
  Modellaktivität, keine garantierten realen Schaltbefehle oder Wake-Zeiten.
- Alle Diagramme und Spuren verwenden denselben verfügbaren, ausgewählten
  Horizont. Balken sind per Fokus/Überfahren mit vollständigen Zeitangaben
  lesbar; Lücken im Ablauf heißen „Keine Aktivität im veröffentlichten Plan“.
- Unterstrichene Werte sowie Klick/Enter im Diagramm öffnen `hass-more-info`
  der explizit veröffentlichten und in HA vorhandenen Messentität. SOC und
  gespeicherte Energie verweisen auf den Ladestand, Aufnahme auf Eingangsleistung,
  Akkuentnahme auf AC-Ausgangsleistung, Endlastwerte auf deren Leistung.
  Diese Messhistorien sind keine Historie der berechneten Prognosekennzahl;
  insbesondere enthält AC-Ausgangsleistung auch Durchleitung. Der Zielname
  steht im Tooltip. Ohne geeignete Entität wird kein Historienlink erzeugt.
- Die separate Aktion „Prognose öffnen“ öffnet weiterhin die Kartendetails.
  Überblickskennzahlen haben feste Zeitbezüge; die Auswahl darunter steuert
  Speicher, Endlast, Details und Ablauf. Gerätedetails filtern Fremdgeräte aus.
- Die Phasengruppierung toleriert nur den aus der Energierundung resultierenden
  Leistungsfehler beider Intervalle (ursprünglich 0,1 Wh, seit v0.38.2 im
  aufgelösten Diagrammplan 0,000001 Wh). Echte Leistungswechsel und Quellenwechsel sowie
  Übergangsphasen und Tagesgrenzen werden nicht verschluckt.
- Bilanzreste sind aufklappbar; kleine positive Energiemengen unter 10 Wh
  werden in Wh angezeigt. Die gelbe Linie ist die Entladegrenze, kein Ladeziel.

- Das Raster richtet sich nach der tatsächlichen Kachelbreite (Container Queries):
  eine Spalte unter 740 px, zwei ab 740 px, drei ab 1180 px, vier ab 1600 px.
  Speicher und Endlast belegen je eine Spalte. Details belegen im dreispaltigen Raster
  zwei Spalten, ansonsten eine. Im vierspaltigen Raster passen damit zwei
  Speicher, Endlast und Details nebeneinander. Die Lesereihenfolge bleibt auch mit Tastatur erhalten.


### Abnahme v0.37.0

Am 2026-09-06 mit lokalem Playwright-MCP in einer temporären, ausdrücklich
als Beispieldaten markierten Kachel geprüft: 2800, 1920, 1280, 800, 390 und
280 px Viewportbreite ohne horizontalen Seitenüberlauf. Bei 1920/2800 px
stehen zwei Speicher, Endlast und Details in einer Reihe. Bei schmaleren
Containern wird umgebrochen; Diagramme können bei 280 px intern scrollen.
Ein Historienklick öffnete für den konfigurierten Fossibot-SOC-Sensor den
HA-Dialog mit `ha-more-info-history` und `state-history-chart-line`.


## Gemeinsame Zeitauflösung (v0.38.2)

- `chart_schedule` teilt die unveränderten Planner-Slots ausschließlich für die
  Darstellung an Ladeenden und Quellsegmentgrenzen auf. `schedule` bleibt als
  ursprünglicher Plan erhalten. Aufnahme, gespeicherte Energie und Akkuentnahme
  werden aus ungerundeten Wh über ihre jeweiligen aktiven Zeiträume verteilt.
- `soc_forecast` erhält an diesen Grenzen zusätzliche Stützstellen. Die
  Änderung ergibt sich aus gespeicherten bzw. entnommenen Wh und der
  konfigurierten Kapazität. Originale Slot-Endwerte bleiben erhalten. Auch
  Laden und Entladen innerhalb desselben Slots mit netto gleicher Energie
  ergeben eine steigende und fallende Kurve; Pausen bleiben waagerecht.
- Die Eingangskurve berücksichtigt gleichzeitige Ladeeingänge, direkte
  Endlastversorgung und nur die dafür benötigten Ausgangsverluste. Die
  Root-Energiesumme des ursprünglichen Slots bleibt verbindlich. Endlastkurven
  folgen den einzelnen Quellsegmenten; Übergänge transportieren keine Energie.
- `chart_resolution: activity` kennzeichnet diese Auflösung. Fehlen älteren
  Plänen Ladezeiten oder Quellsegmente, bleibt `slot` als sichtbar erklärter
  Fallback erhalten. Es werden keine genauen Schaltzeiten aus gerundeten Wh
  erfunden. Alle Kurven bleiben Prognosen, keine Messhistorie.
- Der gemeinsame Cursor lässt sich in Diagrammen und Aktivitätsachsen per
  Maus, Touch und Tastatur bewegen. Jede Spur zeigt Uhrzeit und geplanten
  Zustand; bei nur geschätzten Intervallen bleibt der Zustand unbekannt.
- Regressionen: `test_chart_slots_follow_activity_and_conserve_energy_and_soc`
  prüft Energiesummen, SOC, Pausen und Übergänge. Der Backend-/Frontend-Vertrag
  in `backend-contract.mjs` prüft Kurvenpunkte, Cursorwerte und Energieintegration
  einschließlich eines innerhalb des Slots beschnittenen Zeitraums.


Sichtprüfung am 07.09.2026 mit lokalem Playwright-MCP in einer temporären
Home-Assistant-Vorschau und synthetischem Backend-Payload: Bei 1280, 390 und
280 px kein horizontaler Seitenüberlauf. Bei 23:37 zeigt der gemeinsame Cursor
56 % SOC sowie Laden aus, Entladen ein und AC-Ausgang ein. Die Tastaturwahl
wechselt von 0 W um 23:00 zu 320 W am Entladestart um 23:30. SOC-Kurve und
Balken beginnen ihre Entladung am selben Zeitpunkt; die Dashboard-Konfiguration
wurde für diese Prüfung nicht gespeichert oder verändert.
