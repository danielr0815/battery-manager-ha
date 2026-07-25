# Antwort des Repos `balcony-solar-forecast` an den Battery Manager (zur BM-Wochenforensik 17.–24.07.2026)

Bezug: `battery-manager-ha/docs/orders/balcony-solar-forecast-auftrag-2026-07-24.md`
(Aufträge F6/F7). Erstellt 2026-07-25, umgesetzt in `balcony-solar-forecast`
**v0.21.0** (Branch `fix/forensik-2026-07-24`, 9 Commits).

Beide BM-Kernbefunde sind bestätigt und ursächlich adressiert. Die Kernzahlen des
BM-Auftrags waren korrekt (04Z-Watt, Scalar 2,355 @ 08:28 lokal am 20.07.,
06:00-Vorabend-Bias −0,8 kWh / MAE 1,1 kWh, day-0-Bandkollaps, Headline-Jojo). Wir
weichen an zwei Stellen von der **vermuteten Ursache** bzw. der **vorgeschlagenen
Maßnahme** ab und korrigieren zwei Attributionsdetails — Begründung jeweils unten.

---

## Auftrag 1 (F6): Intraday-Overshoot durch Morgen-Unterschätzung

Gemeinsame Ursachenkette (bestätigt): Das Rohmodell unterschätzt den Direktstrahl
an klaren Morgen (Beam ×1,25–1,35, ~1,0–1,6 kWh/Tag) und der Intraday-Scalar
**samplte gegen die pure Roh-Kurve**, während serviert wird `roh × θ × scalar`.
θ (Day-ahead-Bias, morgens 1,36–1,49) und der Scalar (Peaks 1,33–2,36) korrigierten
denselben Fehler **doppelt** → served/Ist 07–09Z bis ×1,9, Headline-Jojo. Das ist
kein reiner Shape-, sondern ein **Doppelkorrektur**-Fehler.

### F6.1 — Morgen-Shape korrigieren → **ÜBERNOMMEN** (Prio 1), Ursache abweichend

Umgesetzt, aber die Ursache ist **nicht** „die Shademap deckt die Morgenstunden
nicht ab". Die Shademap ist morgens aktiv; das Defizit ist (a) unter­modellierter
**Direktstrahl** (bifacialer Rückseiten-Pickup + steile Ost-Geometrie) und (b) eine
zu harte Ost-Horizontkante (`tau: 0` unter el10, per Operator-Emergenzmessung
16.07.).

- **Neuer Site-Faktor `bifacial_beam_gain`** (Default 1,0 = identisch, Referenzsite
  ≈ 1,23), multipliziert **nur** den Beam+Zirkumsolar-POA-Anteil in der Engine
  **nach** dem IAM und **vor** der ungegateten Trainer-Referenz und dem τ-Gate —
  hebt den Direktstrahl in die **RAW- UND** korrigierte Physik, statt dass gedeckelte
  Lerner (τ≤1, θ-Zellen am 1,5-Clamp) das Defizit als >1-Korrektur ausdrücken. Damit
  konvergieren die Morgen-θ-Zellen Richtung 1,0–1,1 und der Clamp bekommt Headroom.
- **Config-Ost-Horizont**: die `tau:0`-Kante ist eine **Config-Empfehlung an den
  Operator** (τ-Rampe statt Stufe), kein Code-Fix — die semi-transparente Baumkrone
  ist per Site-YAML adressierbar. (Der Betreiber setzt Beam-Gain + τ-Rampe live; nach
  Umsetzung ist ein `reset_day_ahead_bias` empfohlen, da 8 Live-Lerntage 311 Tage
  Fremd-Config schlagen.)

### F6.2 — `INTRADAY_MIN_MODELED_WH` anheben / El-Gate → **MODIFIZIERT** (Sampling-Basis-Fix)

Der wirksame Hebel ist **nicht** das Anheben der Wh-Schwelle, sondern die
**Sampling-Basis**:

- **Umgesetzt (der eigentliche Fix, A2):** Die modellierte Seite des Intraday-Samples
  wird jetzt gegen die **θ-korrigierte** Kurve gestellt (`roh × nächtlich-eingefrorenes
  θ`, neuer `_day_factor`-Cache), nicht gegen pure Roh. θ ist nächtlich eingefroren →
  keine Zirkularität. Sim-validiert: Peaks 1,78→1,23, 1,45→1,12, 1,36→1,14; der ECHTE
  Unterschätzungstag 21.07. bleibt bei 1,49 — **das Wettersignal bleibt erhalten**.
- **Absolute Schwelle abgelehnt:** `INTRADAY_MIN_MODELED_WH` von 5 auf ~100–150 Wh
  (nötig für diesen 3,56-kWp-Standort) würde kleine 800-W-Anlagen anderer Nutzer fast
  ganztägig vom Sampling ausschließen — die Konstante skaliert mit der Anlagengröße,
  ein absolutes Anheben ist standort-unsicher. Ein Gates-Sim zeigt zudem: Schwelle/
  El-Gate allein bringt den Peak nur 1,78→1,36–1,46 (statt →1,2 via A2) und verzögert
  die Aktivierung um ~2 h. A2 ist strukturell überlegen.
- Optional als Ergänzung sinnvoll (nicht als Ersatz): energie-/elevationsgewichtetes
  Sampling. Der Docstring behauptete fälschlich „energy-weighted" — korrigiert.

### F6.3 — `INTRADAY_APPLY_HORIZON_MINUTES` 360→120–180 → **ABGELEHNT** (als Sofortmaßnahme)

Kappt **symmetrisch** auch echte Korrekturen (19./21.07. brauchten die volle
6-h-Reichweite). Nach A1+A2 ist der strukturelle Rest-Overshoot weg; falls nach einer
Messwoche noch messbarer Overshoot bleibt, käme allenfalls 240 in Frage — reines
Symptom-Symbol, nicht jetzt.

### F6 „kein harter Scalar-Clamp" → **ZUGESTIMMT**

Bestätigt: 21.07. brauchte legitim >1,6. Ein 1,3–1,5-Clamp hätte den echten
Unterschätzungstag bestraft. Nach A2 liegen die Peaks strukturell <1,3, ohne die
Weather-Extreme zu beschneiden. Kein Clamp eingebaut.

### Zusatz: Headline-Jojo behoben (A3), Blackout-Robustheit (A7)

- **A3:** Der Keep-Ceiling-Pfad der Tages-Headline behielt auf re-geclampten Slots die
  servierte (scalar-inflationierte) Ceiling → +3,27 kWh Balloon am 20.07. bei Scalar
  2,355. Jetzt `min(prereclamp/faktor, ceiling)` = exakter scalarfreier Wert, am
  physischen Deckel gekappt → Headline day-ahead-stabil by design. **Das ist der
  direkte Fix für das im BM beobachtete lost_surplus-Jojo 4,4→10,0 kWh.**
- **A7:** Der Intraday-Sample-Ring war rein in-memory → jeder Reload/Restart kostete
  ≥2 h neutrale Korrektur (19.07.: ~8 h blind). Ring wird jetzt beim ersten frischen
  Tick aus 5-min-Recorder-Statistiken rekonstruiert (modellierte Seite auf die
  gemeterten Ebenen beschränkt). Reload-Forensik ergab: alle ≥8 „Reloads" waren
  HA-Restarts nach HACS-Installs — bei dieser Release-Frequenz ist der restart-feste
  Ring der eigentliche Fix.

---

## Auftrag 2 (F7): day-0-Stundenbänder kollabieren

### F7.1 — Ursache day-0-Kollaps → **ERLEDIGT**, Ursache abweichend (Bin-Cold-Start)

Der BM-Verdacht („Band-Berechnung nur im Day-Ahead-Pfad; nach Mitternacht/Intraday-
Refresh verlieren die day-0-Stunden ihre Quantile") ist **code-seitig widerlegt**: Die
Bänder werden **jeden Tick, für alle Slots** neu berechnet (`coordinator.py:898–935`),
day-0 verliert nichts nach Mitternacht. Echte Ursache: **Bin-Cold-Start** — der
311-Tage-Bootstrap fütterte den Quantile-Ring **nicht** (SPEC §6 versprach es), und die
alte Wolkenklassifikation (Random-Overlap) routete Sonnenstunden in overcast, sodass
nur overcast-Bins Samples bekamen. Alle anderen Bins kollabierten auf p10==p90 (= Mean,
`source=scalar`).

Fix (A6 + A5):
- **A6 Quantile-Bootstrap-Seeding:** `scripts/backfill.py` füttert jede Taglichtstunde
  als `relerr = gemessen/θ-korrigiert` über den **live** `train_quantiles` in die
  (Klasse × Tagesabschnitt)-Ringe; `build_bootstrap_json` emittiert `quantile_state`;
  Import ist additiv, Rollback-Snapshot trägt den Quantilzustand mit.
- **A5 Clear-Sky-Index-Klassifikation:** `classify_cloud` keyt jetzt auf
  `kc = ghi/haurwitz(elevation)` — sonnige Nachmittage landen nicht mehr in overcast.
  Betrifft Bias-Routing, Quantil-Bins UND Scoreboard-Strata konsistent.

Damit tragen day-0-Bänder ab dem Seeding-Release echte Spreizung, statt wochenlang zu
kollabieren.

### F7.2 — „Scalar auf p10/p90 mitanwenden statt Bänder zu verwerfen" → **ABGELEHNT** (No-Op)

Doppelt widerlegt am Code:
1. **Bänder werden nie verworfen.** `coordinator.py:898–935` baut die Bänder jeden
   Tick für alle Slots.
2. **Der Scalar steckt bereits in p10/p90.** `engine.py:859–878`
   (`band_curve_from_corrected` auf `total_watts`) wendet den Scalar auf die Bandkurve
   an. Die Maßnahme ist der Ist-Zustand — es gibt nichts umzusetzen. (Präzisierung: Das
   **Tages-P10-Aggregat** strippt den Transient jetzt asymmetrisch, s. u. B1 — das
   servierte Band behält den Scalar.)

### F7.3 — Abend-Bias / breitere Abend-p90 → **Mean-Shift ABGELEHNT; p90 via Seeding MODIFIZIERT**

- **Pauschaler konservativer Abend-Mean-Aufschlag: abgelehnt.** Er hätte die drei
  **Überprognose**-Tage der Woche (19./20./22.07.) verschlimmert — der Vorabend-Bias
  ist nicht monoton unterschätzend. Der systematische Anteil wird strukturell von A1
  (Beam-Gain) + A4 (Bias-Re-Bootstrap nach Config-Fingerprint) getragen, nicht von
  einem Hand-Aufschlag.
- **Breitere p90: via A6-Seeding + A4-Re-Bootstrap**, nicht per Hand. Nach dem Seeding
  tragen die klaren Folgetage echte, trainierte Bänder statt einer kollabierten p50.

---

## Korrektur zweier Attributionsdetails im BM-Auftrag

1. **„Bänder werden verworfen" (F7.2) ist faktisch falsch** — die Bänder werden jeden
   Tick, für alle Slots gebaut, und der Scalar steckt bereits in p10/p90 (Code-Belege
   oben). Der beobachtete Kollaps ist Bin-Cold-Start, nicht Band-Verwerfen.
2. **Die −29 % am 21.07.**: Die BM-Zahl 8,22 kWh ist der **AC-Headline**-Wert um 00:13
   lokal. In konsistenter AC-Sicht stimmt die Größenordnung (−28,8 % gegen Ist AC 11,65
   kWh) — der frühere „Attributionsfehler"-Einwand aus Welle 1 war selbst ein DC/AC-
   Artefakt. Hintergrund: `get_issued_forecast` lieferte DC-Kurven ohne Kennzeichnung,
   was alle Issued-Ratios um ~8 % schönte (jetzt behoben, s. u.).

---

## Neues Abnahmekriterium (ersetzt das BM-Kriterium)

Das BM-Kriterium war an zwei Stellen nicht tragfähig: Teil 1 (day-0-coverage) wäre ohne
A6-Seeding ~6 Wochen unerreichbar, Teil 2 (keine Scalar-Spitzen >1,6) ist zu lasch — der
strukturelle Overshoot passiert **unter** 1,6. Ersatz:

- **Teil 1 (Morgen-Overshoot):** Wochenmittel `served/Ist` über 07–10 Uhr lokal innerhalb
  **±20 %**, UND Scalar 08–09 Uhr lokal **≤ ~1,25** an Tagen mit `|Tagesfehler| < 10 %`
  (an echten Wetterabweichungstagen darf der Scalar frei laufen — das ist gewollt).
- **Teil 2 (day-0-Bänder):** `quantile_coverage[<heute>].coverage > 0,5` mit
  `source != scalar` über den Tagesverlauf — **gekoppelt an das A6-Seeding-Release**
  (nicht davor messbar).

Interimshinweis: Die BM-seitige **p10==p90-Heuristik als Kollaps-Detektor bleibt
gültig** und sinnvoll — sie erkennt genau den Cold-Start-Zustand, den A6 behebt; nach
dem Seeding-Release sollte sie für day-0 an klaren Tagen nicht mehr auslösen.

---

## Neue Observability-Felder für den Battery Manager (v0.21.0)

Zur besseren Auswertbarkeit BM-seitig neu ausgeliefert:

- **`get_issued_forecast`:** `hourly_wh_ac` (= DC × η zum Ausgabezeitpunkt, in den
  Snapshot eingefroren; Alt-Snapshots fallen auf aktuelles gelerntes η zurück und weisen
  das per `eta_source` aus). `hourly_wh`/`raw_hourly_wh` sind jetzt **explizit als DC**
  dokumentiert. Zusätzlich `cloud_class_by_hour` (Day-ahead-Wetterklasse je Stunde) und
  `applied_factor_by_hour` (`hourly_wh/raw_hourly_wh`) — die angewandte Korrektur wird
  sichtbar. **Empfehlung an den BM:** issued-Vergleiche künftig gegen `hourly_wh_ac`
  führen (die DC-Semantik hatte die BM-Forensik um ~8 % verzerrt).
- **`get_forecast` / P10/P90-Sensoren:** `band_source` und `band_source_by_day` (pro
  lokalem Tag Slot-Zählung je Herkunft `bin`/`envelope`/`ensemble`/`neutral`) — direkt
  ablesbar, welche Prognosetage ein trainiertes Band tragen. `band_source` ist an das
  Vorhandensein eines Band-Blocks gekoppelt (kein Herkunfts-Claim ohne Band).
- **`day_ahead_bias_status`:** je Zelle `clamped: true`, wenn θ am Band-Rand klebt
  (clear|morning klebte seit Bootstrap am 1,5-Deckel — wichtiges Diagnosesignal für den
  BM, um systematische Morgenunterschätzung früh zu erkennen).
- **Diagnostics:** echte Füllstände (`store_stats`, `learner_state_summary`), Quantil-
  `trained` gated auf `n` UND `effective_days`, Forecast-Split `daily_kwh_dc` vs
  `daily_kwh_ac`.

---

## Zusammenfassung der Urteile

| BM-Maßnahme | Urteil | Wirksamer Hebel |
|---|---|---|
| F6.1 Morgen-Shape | Übernommen (Prio 1) | Beam-Gain (RAW-Physik) + Config-Ost-Horizont, nicht Shademap |
| F6.2 MIN_MODELED_WH ↑ / El-Gate | Modifiziert | Sampling gegen θ-korrigierte Kurve (A2); absolute Schwelle bricht kleine Anlagen |
| F6.3 APPLY_HORIZON 360→120–180 | Abgelehnt (Sofort) | kappt echte Korrekturen; ggf. später 240 |
| F6 kein harter Clamp | Zugestimmt | nach A2 Peaks strukturell <1,3 |
| F7.1 Ursache day-0-Kollaps | Erledigt | Bin-Cold-Start + Bootstrap-Lücke (A6/A5), nicht Refresh-Verlust |
| F7.2 Scalar auf Bänder | Abgelehnt (No-Op) | Ist-Zustand; Code-Beleg coordinator:898–935 / engine:859–878 |
| F7.3 Abend-Mean / p90 | Mean-Shift abgelehnt; p90 via Seeding | A6+A4; Mean-Shift verschlimmert die 3 Überprognose-Tage |
| Abnahmekriterium | Ersetzt | Wochenmittel served/Ist 07–10 ±20 % UND Scalar 08–09 ≤1,25 an |Fehler|<10 %-Tagen; day-0-coverage an A6-Release gekoppelt |

Umgesetzt in v0.21.0; day-0-coverage wird nach dem Seeding-Release (Backfill neu
ausführen) messbar. Fragen gerne zurück an das balcony-solar-forecast-Repo.

---

## Update 2026-07-25: Deployment + Re-Bootstrap erfolgt — Messbasis für BM ab sofort

Alles oben Angekündigte ist seit heute früh live und verifiziert; der BM misst ab
sofort gegen die neue Physik.

### Was heute passiert ist

- **v0.21.0 live seit ~06:00 lokal.** Config-Kampagne Teil 1 umgesetzt: Ost-Horizont
  als τ-Rampe (az 63–78) in allen 8 Planes, `site.bifacial_beam_gain: 1.25`,
  Albedo 0,15.
- **`reset_day_ahead_bias` (alle 12 Zellen) + 320-Tage-Re-Bootstrap unter der neuen
  Physik** um ~06:30 importiert — erstmals **inklusive Quantile-Seeding (A6)**:
  10 Bins, 3.796 Samples.

### Verifizierter Live-Zustand

- **day-0-Stundenbänder:** `wh_period_p10/p90` tragen jetzt in **56/56**
  Tageslicht-Slots ein echtes Band (vorher 4/62); day-1 55/55. 9/10 Quantil-Bins
  `trained` (einzige Ausnahme: `fog|morning`, n=13). Tages-p10/p50/p90 heute
  9,7/11,2/14,7 kWh.
- **Bias-Zellen nach Re-Bootstrap:** `clear|morning` θ=1,094 (vorher 1,50 am Clamp —
  der F6-Rohphysik-Fix trägt), `clear|midday` 0,908, `clear|afternoon` 0,812. Einzige
  geclampte Zelle: `overcast|morning` 1,5 — bekannter, dokumentiert offener
  Diffus-Floor, Fix in 0.22 unterwegs.

### Konsequenz für den BM

- **Abnahmekriterium Teil 2 ist ab heute messbar**, nicht erst in Wochen:
  `quantile_coverage` day-0 mit `source != scalar` sollte ab sofort deutlich > 0,5
  liegen.
- Die BM-seitige **p10==p90-Fallback-Heuristik** bitte beobachten: sie wird jetzt nur
  noch selten anschlagen (z. B. fog-Morgen) — häufiges Auslösen wäre ab heute ein
  Regressionssignal, kein Normalzustand mehr.
- **Neue Forensik-Felder (v0.21.0) stehen bereit:** `get_issued_forecast` liefert
  `hourly_wh_ac` (DC/AC-Verwechslung damit vom Tisch), `cloud_class_by_hour` und
  `applied_factor_by_hour`; die Kurven-Sensoren tragen `band_source`;
  `day_ahead_bias_status` weist je Zelle ein `clamped`-Flag aus.
- **Scalar-Peaks > 1,3 an klaren Morgen sollten ab heute strukturell Geschichte
  sein.** Sieht der BM sie weiterhin, bitte melden — dann greift ein Fix nicht wie
  erwartet.

### Ausblick (damit der BM nicht überrascht wird)

- **0.22 in Arbeit:** elevationsabhängiges Horizont-τ (`tau_points`) + `diffuse_tau`
  für Wand-Reflexion — hebt den Diffus-Floor (`overcast|morning`-Clamp) und ändert
  die RAW-Kurve erneut. **0.23:** `run_bootstrap` als HA-Action.
- Nach deren Config-Kampagne folgt **ein** weiterer Bias-Reset + Re-Bootstrap. In
  dieser Übergangswoche (~Anfang August) Tagesabweichungen bitte nicht
  überinterpretieren; danach ist die Messbasis stabil.
