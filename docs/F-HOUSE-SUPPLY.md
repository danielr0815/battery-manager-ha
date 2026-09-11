# Hausversorgung vor Überschusslasten

## Befund

Am 09.09.2026 blieb der Inverter bei 45 % SOC ausgeschaltet (T* = 95 %),
obwohl für den Folgetag Kaskadenzufuhr geplant war. Die Kostenfunktion
bewertete die am Horizontende gespeicherte Energie höher als den vermiedenen
Netzbezug. Ohne pessimistisch bestätigtes Clipping blieb diese Gutschrift
voll erhalten; die spätere Lastallokation verbrauchte den so erzeugten
nominalen Überschuss.

## Regeln

- **R1 — Hausversorgung zuerst:** Nach der wirtschaftlichen Schwellenwahl,
  vor jeder Lastallokation, werden niedrigere ganzzahlige Schwellen geprüft.
  Eine Alternative muss sowohl den gesamten Netzbezug als auch den gesamten
  Export gegenüber der ursprünglichen Schwelle strikt verringern.
  Unter diesen Alternativen gewinnt der geringste Netzbezug; bei Gleichstand
  bleibt die niedrigere Schwelle. Bloßer Inverter-Eigenverbrauch ohne
  Importersparnis genügt nicht. Ohne geeigneten Kandidaten gilt die bisherige
  Entscheidung einschließlich Endenergie-Gutschrift weiter.
- **R2 — Reserve:** In jedem Slot über den vollständigen Horizont müssen
  nominaler und pessimistischer SOC mindestens dem größeren Wert aus
  `soc_min + soc_buffer` und der bestehenden gerampten Inverterreserve
  entsprechen. Die pessimistische PV nutzt P10 bei verwertbaren Bändern,
  sonst den konfigurierten Alpha-Faktor. Ein nominales Wiederauffüllen
  beendet diese Prüfung nicht; auch die folgende Nacht bleibt geschützt.
  Bereits bestehende Reserveverletzungen werden durch diese zusätzliche
  Optimierung nicht als Erlaubnis zum weiteren Entladen interpretiert.
- **R3 — Einheitliche Basis:** Bekannte manuelle DC-Unterstützung gilt in
  jedem Vergleich identisch. Der zurückgegebene Basisverlauf behält den
  bestehenden Ohne-Support-Vertrag für die Kennzahlen bei. Lasten, Kaskaden
  und vorgezogene Einspeisung planen erst auf der gewählten Schwelle und
  behalten sämtliche bisherigen Gates, Laufzeit- und Recovery-Regeln.

## Abgrenzung

Dies ist eine zusätzliche Prioritätsprüfung innerhalb der bestehenden
skalaren Schwellenpolitik, keine frei optimierte stündliche Schaltfolge.
Die bisherige Kostenfunktion und die Merge-Rampe bleiben bestehen. R2 gilt
für die neu akzeptierten Alternativen; sie ändert nicht rückwirkend sämtliche
bisherigen Schwellenentscheidungen. Die Ausführungshysterese bleibt erhalten.
Es gibt keinen neuen Konfigurationsschalter.

Ein Restüberschuss darf weiter entstehen, etwa wenn zusätzliche Entladung
an der Reserve scheitert. Eine Kaskade darf weiterhin aus bereits verfügbarer
Energie arbeiten oder erforderliche Recovery planen; ihre vollständige
Abschaltung ist kein Optimierungsziel.

## Tests und Nachrechnung

`tests/core/test_house_supply.py` prüft dasselbe kleine Dreitages-Szenario
mit Einzelverbraucher und echter Speicherkaskade: vorher T* 95 %, 1,2 kWh
Import und 450 Wh optionale Last; nachher T* 31 %, 0,2 kWh Import und keine
optionale Zufuhr. Beide SOC-Verläufe erfüllen die Reserveprüfung. Schlechte
P10-Prognose und schlechter Alpha-Fallback verhindern die Absenkung.
Ohne Überschuss bleibt die vorherige Entscheidung erhalten.

Der vollständige aufgezeichnete Live-Fall ergibt T* 35 % statt 95 %,
rund 0,237 statt 1,143 kWh Netzbezug, minimal 26,14 % nominalen SOC und
keine geplante Kaskadenzufuhr. Rund 67 Wh Restexport bleiben bestehen.
Dies ist eine lokale Modellnachrechnung, kein Nachweis realer Einsparung.

Die bestehenden Topologie- und Nacht-Golden-Snapshots bleiben unverändert.
Die Einspeisungs-Fristprobe nutzt jetzt 4,8 statt 4,5 kWh Tages-PV, damit
nach der Hausversorgung noch Export zum Verteilen vorhanden ist.


## Regressionsschutz

Die Prüfung bleibt auf niedrigere Schwellen mit gleichzeitig weniger Import
und Export begrenzt. Ein pauschales Wiederaufladeziel über 80 % hatte zuvor
auch bestehende Kostenentscheidungen verändert und zulässige Last-/Recovery-
Allokationen gesperrt. Das widersprach R1/R3 und den Merge- und Reserveverträgen.

Eine Wiederaufladung beendet die Reserveprüfung einer neuen Alternative nicht:
R2 schützt jeden Slot bis zum Horizontende. Es gibt keine zusätzliche globale
Wiederaufladesperre in den Lastallokatoren; ihre bestehenden Reserve-, Import-
und Tagespeak-Prüfungen gelten weiter. Die Tests in `test_house_supply.py`, die
Pre-Drain-/Z4-/Merge-Tests in `test_optimize.py` und die unveränderten Goldens
sichern diese Abgrenzung gemeinsam ab.
