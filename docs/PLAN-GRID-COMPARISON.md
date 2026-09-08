# Begrenzter Offline-Rastervergleich

## Bedienung

```bash
uv run python scripts/compare_plan_grids.py diagnostics.json --timeout 60 > vergleich.json
```

Eingabe ist ein HA-Diagnoseexport mit `data.planner_recording` oder ein einzelner
Planstand aus dem Replay-Format. Das Werkzeug schreibt nur den Bericht nach
stdout, schaltet keine Entitäten und verändert keine Aufzeichnung. Der
Unterprozess wird nach standardmäßig 60 Sekunden beendet (wählbar 1–120).
Ein Timeout liefert Exit-Code 2, ohne ein Ergebnis oder Optimum vorzutäuschen.
Ungültige Eingaben liefern ebenfalls einen Fehler. Die HA-Integration benötigt
dieses Skript nicht und erhält keine zusätzliche Runtime-Abhängigkeit.

## Vergleichsvertrag

Exakt drei Neuplanungen mit derselben Konfiguration und Laufzeitzuständen:
Originalintervalle, zusätzlich halbstündliche Grenzen, zusätzlich
viertelstündliche Grenzen. Bestehende Freigabegrenzen bleiben erhalten.
PV, AC, DC und vorhandene P10-/P90-Energien werden proportional aufgeteilt;
fehlende Prognosebänder bleiben unbekannt. Es wird konstante Leistung innerhalb
eines ursprünglichen Intervalls angenommen, keine zusätzliche Wetterinformation.
Mehr als 512 resultierende Intervalle oder zwölf Lasten werden vor Planung
abgelehnt. Originaleingaben müssen zusammenhängende Intervalle enthalten.

Der Bericht zeigt Netzbezug, Export, Hausbatterie-Minimum, Tagesmaxima sowie je
kontinuierlicher Last Nutzenergie und exakte zusammengefasste Laufintervalle.
Kaskaden-Endlasten verwenden ihre tatsächlichen geplanten Quellensegmente;
Speicherladung wird nicht als Endlastnutzen addiert. `planned_blocks` zählt
geplante zusammenhängende Läufe, keine physischen Schaltbefehle. Quellenwechsel
innerhalb eines durchgehenden Laufs erhöhen diese Zahl nicht.
`recorded_result_matches` prüft die exakte Reproduktion des Originalplans.
Neue Softwarestände können wegen zusätzlicher Diagnosefelder abweichen.

Ein feineres Raster verändert auch die Simulation von Grenzübertritten, nicht
nur den Startzeitpunkt. Mehr Nutzenergie bei niedrigerem Export ist deshalb
allein kein Zulassungskriterium. Tagesmaxima, Reserve und Laufunterbrechungen
müssen ebenfalls verglichen werden. Der Bericht wählt keine Alternative aus.
Insbesondere ist dies **keine vollständige Suche über alle zulässigen Pläne**.
Elektrische Kaskadenreihenfolge, Quellenpolitik und Toleranzen bleiben gleich.

## Erster Vergleich am 08.09.2026

Aktueller Plan aus installiertem 0.41.0, gegen 17:14 Uhr lokal ausgelesen;
Original offline exakt reproduziert. Alle Werte sind Prognosen desselben
verbleibenden Mehrtageshorizonts, keine gemessenen Einsparungen.

| Raster | Intervalle | Endlastenergie | Laufblöcke | Netzbezug | Restexport |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original | 55 | 2260,13 Wh | 4 | 0 Wh | 106,91 Wh |
| 30 Minuten | 110 | 2372,88 Wh | 3 | 0 Wh | 108,91 Wh |
| 15 Minuten | 220 | 2485,63 Wh | 2 | 0 Wh | 8,67 Wh |

Das Hausbatterie-Minimum bleibt rund 38,916 %. Die Tagesmaxima am
08./09./10.09. betragen im Original 95,058/78,799/95,000 %, bei 30 Minuten
95,675/80,035/95,000 % und bei 15 Minuten 95,983/80,653/94,598 %.
Schon diese Unterschiede verbieten, den Vergleich nur als Verschiebung eines
Startzeitpunkts zu interpretieren. Der Bericht zeigt sie deshalb ausdrücklich.

Ein unabhängiges kleines Gegenbeispiel (90 % Anfangs-SOC, vier Stunden mit
600/1800/1800/0 Wh PV, jeweils 100 Wh AC und 50 Wh DC, 400-W-Endlast) liefert
beim feineren Raster mehr Energie, aber zwei statt einem Laufblock.
Diese Regression begründet, warum keine pauschale Live-Umstellung erfolgt.
Vor automatischer Auswahl sind ein vollständiger kleiner Vergleichsmaßstab,
Erhalt angenommener Direktläufe und ein gemeinsamer Schutzvergleich nötig.
