# Jahresbefund als Grundlage der Reservepolitik

Auswertung über den lokalen Playwright-MCP in Home Assistant. Zeitraum
26.09.2025 bis einschließlich 25.09.2026, Europe/Berlin; UTC-Grenzen
25.09.2025 22:00 bis 25.09.2026 22:00. 365 Tagesdatensätze, für SOC, Spannung,
Temperatur und wichtige Energiepfade 8.757 von 8.760 Stunden. Fehlende Stunden:
08.10.2025 20:00, 21:00 und 22:00 UTC.

| Zeitraum | Rekonstruierte PV | Wohnungszähler Bezug | Wohnungszähler Rückfluss |
|---|---:|---:|---:|
| 26.09.–31.10.2025 | 90,0 kWh | 153,8 kWh | 4,1 kWh |
| November–Februar | 179,5 kWh | 697,4 kWh | 8,5 kWh |
| März | 145,6 kWh | 78,4 kWh | 2,3 kWh |
| April–August | 1.356,0 kWh | 121,5 kWh | 292,3 kWh |
| 01.–25.09.2026 | 149,2 kWh | 31,8 kWh | 9,4 kWh |
| **Gesamt** | **1.920,3 kWh** | **1.082,9 kWh** | **316,6 kWh** |

PV ist die Summe der Zähleränderungen der beiden im Energiedashboard verwendeten
Pfade `sensor.victron_vebus_acouttoacin1_228` und
`sensor.victron_vebus_outtoinverter_228`. Rückfluss am Wohnungszähler enthält
zeitweise die Versorgung des vorgeschalteten Entfeuchters. Dessen Zähler liefert
nur 135 Tagesdatensätze; der korrigierte BM-Exportzähler beginnt erst am
03.08.2026. **Keine belastbare jährliche öffentliche Einspeisemenge oder exakte
Eigenverbrauchsquote aus diesen Zahlen ableiten.**

Dezember lieferte 23,6 kWh PV, Mai 305,6 kWh. Im Dezember liegen zweimal sieben
aufeinanderfolgende Tage jeweils unter 1 kWh; eine dieser Wochen summiert sich
auf 3,24 kWh. An 133 Tagen lag das Tagesminimum des SOC unter 20 %, während
993 Stunden auch im Mittel darunter lagen. Das sind keine gezählten Vollzyklen.
Die Batterietemperatur erreichte 39,3 °C.

Zum Auswertungszeitpunkt: 5 kWh, SOC-Grenzen 5–95 %, Inverteruntergrenze 20 %,
koordinierte Steuerung aktiv, separate 48-V-Automation ebenfalls eingeschaltet.
48-V-Netzteil maximal 49,56 V × 1,15 A ≈ 57 W; oberhalb Sollspannung keine
verfügbare Halteleistung. Native 48-V-Last bleibt nach Auslagerung von 24 V bestehen.

P10/P90-Bänder fehlen. Der bisherige obere Ersatzfaktor 1,05 und der gemeldete
mittlere absolute Tagesfehler von 0,809 kWh über 14 Tage beweisen keine gewünschte
Abdeckung. Die neue Politik startet daher mit 1,20 als ausdrücklich unkalibriertem
Wert und archiviert veröffentlichte Prognosen mit späteren Beobachtungen.

Die Zahlen plausibilisieren einen wetterabhängigen Ganzjahresansatz; sie sind
**kein rückwirkender Wirksamkeitsnachweis einer damals verfügbaren Prognose oder
der neuen Steuerung**. Anforderungen, Grenzen, Tests und Rollout stehen in
[F-YEAR-ROUND-RESERVE](F-YEAR-ROUND-RESERVE.md).
