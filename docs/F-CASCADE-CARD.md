# Kaskadenkachel: Energiefluss und Diagramme

Stand: v0.36.1. Die vorhandene `battery-manager-cascade-card` verwendet weiterhin
`entity`, `title` und `hours` (6–96, Standard 48). Kein neuer Kartentyp nötig.

## Verhalten

- Oben steht die konfigurierte lineare Kette. Pfeile im Ablauf geben die
  Energiequelle und den Empfänger entlang dieser Kette an, einschließlich
  Versorgung der Endlast durch einen bestimmten Speicher.
- Die Tagesauswahl gilt für Miniaturen, Gerätedaten, Details und Ablauf.
  Root-Kennzahlen wählen beim Öffnen automatisch heute bzw. morgen; die
  Speicher-Kennzahl wählt den gesamten konfigurierten Planungshorizont.
- Aufnahme ist `charge.energy_wh`, Speicherung ist `stored_energy_wh`,
  Akkuentnahme ist `discharge.energy_wh` inklusive modellierter Verluste.
  Die Kennzahl „Aus Speichern · an Endlast“ summiert nur Aux-Endlastenergie.
  Diese Größen dürfen nicht addiert oder gleichgesetzt werden.
- Der verbleibende Root-Anteil nach Ladeeingängen und Root-Endlastversorgung
  wird als AC-Eigenbedarf / Rundungsrest ausgewiesen. Für einzelne AC-Ausgänge
  enthält der Sensor keine eigene Durchleitungsenergiemenge; dort wird nur der
  geplante Aktivzustand gezeigt. Es werden keine Messwerte erfunden.
- Diagramme sind ausdrücklich Planung (gestrichelt). SOC zwischen Stützstellen
  ist linear interpoliert. Leistung ist Wh geteilt durch die tatsächliche
  Slotdauer, keine momentane Messung. Kumulierte Energie integriert diese
  Slotmittelwerte ab dem Beginn des ausgewählten verfügbaren Plans.
- Heute umfasst den verbleibenden Plan, keine Tageshistorie. Der vorhandene
  Ist-Tageswert wird separat beschriftet. Ohne SOC-Prognose wird auch bei
  bekanntem Start-SOC keine künstliche konstante Kurve erzeugt.
- HA-lokale Zeitstempel, Tagesgrenzen und Sommerzeitwechsel werden in der
  konfigurierten HA-Zeitzone ausgewertet, unabhängig vom Browserstandort.
- Alle Diagramme teilen den Zeitcursor (Hover, Tippen/Ziehen; Pfeiltasten,
  Home/End). Ein Gerät bzw. eine Kennzahl öffnet die gemeinsame Detailansicht.
  Dort wechseln Speicher zwischen SOC/Aufnahme/Akkuentnahme, Energiekurven
  zwischen mittlerer Leistung und kumulierter Energie.
- Benachbarte Slots mit identischen Aktivitäten, Quellen und mittleren
  Leistungen werden im Ablauf zu einer Phase zusammengefasst. Die Originalslots
  bleiben Grundlage der Energie- und Cursorberechnung.
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
