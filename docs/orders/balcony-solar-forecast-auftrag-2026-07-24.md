# Arbeitsauftrag an das Repo `danielr0815/balcony-solar-forecast` (aus BM-Wochenforensik 17.–24.07.2026)

Kontext: 7-Tage-Entscheidungsforensik des Battery Managers (65-Agenten-Analyse, adversarial verifiziert). Zwei bestätigte Befunde liegen ursächlich im Balcony-Solar-Forecast und kosten den Battery Manager real Ertrag. Dieser Auftrag ist selbsttragend formuliert — er kann einer frischen Session im balcony-solar-forecast-Repo direkt übergeben werden.

## Auftrag 1 (F6): Intraday-Korrektur-Overshoot durch systematische Morgen-Unterschätzung

**Befund (CONFIRMED, Daten 17.–24.07.2026):**
- Das Rohmodell unterschätzt die frühen Morgenstunden (06–07 Uhr lokal) an 7 von 8 Tagen massiv: Ist 407–570 W vs. Modell 59–244 W (Faktor bis 7,3).
- Der Intraday-Korrektur-Scalar interpretiert diesen strukturellen Shape-Fehler täglich als Wettersignal: Scalar-Maxima 1,33–2,36, immer zwischen 08:00 und 09:30 lokal.
- Extremfall 20.07.: Scalar 2,355 um 08:28 → Stundenprognosen 09–11 Uhr +56–68 % über Ist, Tagesprognose-Jojo 9,7 → 13,0 → 9,3 kWh binnen 105 min. Folge im BM: lost_surplus-Prognose sprang 4,4 → 10,0 kWh, die Fossibot-B-Restladung wurde nach hinten geschoben, ~0,3–0,4 kWh vermeidbarer Export 11–13 Uhr, B endete bei 55 % statt 90 %.

**Gewünschte Maßnahmen (Reihenfolge = Priorität):**
1. Morgen-Shape des Rohmodells korrigieren (Horizont-/Verschattungsprofil der frühen Stunden — das Shademap-/Shade-Profil deckt die Morgenstunden offenbar nicht ab).
2. Niedrigsonnen-Slots aus den Intraday-Samples ausschließen oder stark untergewichten: `INTRADAY_MIN_MODELED_WH` (aktuell 5) deutlich anheben und/oder ein Sonnenstands-/Elevations-Gate für Samples einführen, damit Stunden mit winzigem Modellwert den Scalar nicht dominieren.
3. Optional: `INTRADAY_APPLY_HORIZON_MINUTES` (aktuell 360 in `const.py` ~402 ff., Anwendung in `bias.py::apply_intraday_scalar`) auf 120–180 senken und den Scalar-Anstieg früh morgens dämpfen.

**Ausdrücklich NICHT gewünscht:** ein harter Scalar-Clamp auf z. B. 1,3–1,5 — der würde echte Unterschätzungstage (z. B. 21.07., Vorabend −29 %) bestrafen.

## Auftrag 2 (F7): day-0-Stundenbänder kollabieren auf Skalar

**Befund (CONFIRMED):**
- Die Tages-p10/p90-Sensoren sind seit 19.07. (v0.20.5/v0.20.6) befüllt — gut.
- Die **stündlichen** Bänder (`wh_period_p10`/`wh_period_p90`) des **laufenden Tages** sind jedoch durchgehend leer/kollabiert: Der BM meldet `quantile_coverage` für day-0 konstant `0 / source=scalar` (p10 = p90 = Mean), z. B. am 24.07. bis mindestens 07:04 lokal; day-1 = 0,29, day-2 = 0,61.
- Folge im BM: Die Unsicherheits-Terme (Z4/c2-Pfade) fallen für den laufenden Tag auf den α-Skalar-Fallback zurück. Konkret: Nacht-Pre-Drain am 24.07. nur bis 32 % statt möglicher ~25 % **trotz korrekter** 12,6-kWh-Tagesprognose → mittags ~0,9 kWh/h Export bei SOC 99 %.

**Gewünschte Maßnahmen:**
1. Ursache finden, warum die per-Stunden-Quantile für day-0 kollabieren (Verdacht: Band-Berechnung läuft nur im Day-Ahead-Pfad; nach Mitternacht/Intraday-Refresh verlieren die day-0-Stunden ihre Quantile).
2. day-0-Stundenbänder durchgängig befüllen (auch nach Intraday-Korrekturen — Scalar auf p10/p90 mitanwenden statt Bänder zu verwerfen).
3. Abend-Bias prüfen: Vorabend-Prognose unterschätzt klare Folgetage systematisch (06:00-Bias der Woche −0,8 kWh, MAE 1,1 kWh; schlimmster Fall 21.07. 8,22 prognostiziert vs. 11,65 kWh Ist = −29 % → 4,0 kWh Export). Ein konservativer Abend-Bias-Term oder breitere Abend-p90 für „klar prognostizierte" Folgetage würde dem BM-Pre-Drain direkt helfen.

**Abnahme-Kriterium (vom BM aus messbar):** `sensor.abstellraum_battery_manager_soc_forecast` → Attribut `quantile_coverage[<heute>].coverage` > 0,5 mit `source != scalar` über den Tagesverlauf; keine Scalar-Spitzen > ~1,6 mehr an klaren Morgen ohne echte Wetterabweichung.
