# F-PREDRAIN-BLOCK — Pre-drain als zusammenhängender Dauerlauf bis zum heutigen SOC-Peak (v0.19.0)

Status: operator decision 2026-08-01. Supersedes for CONTINUOUS loads the
slot-wise pass-2 betting of F-PREDRAIN §3 / the F-NIGHT-RESCUE cross-day
carve-out (energy-limited loads keep both unchanged).

## 1. Befund (live, 2026-08-01, forensisch belegt)

06:55:23 bucht Pass 2 Slot 0 als marginale Pre-drain-Wette
(`F-PREDRAIN: preemptive night charging booked for Entfeuchter Keller @ …
2026-08-01 06:55, import traded 41.2 Wh`); 06:55:25 schaltet der Executor
(`Load Entfeuchter Keller -> ON (plan slot)`). Zwischen 07:00 und 07:06
flackert die Buchung dreimal rein/raus (Live-PV-Prognose-Updates bei
Sonnenaufgang schieben die Clip-Schätzung über/unter die Gate-Schwelle);
07:06:33 fällt die Empfehlung endgültig. Ergebnis: ein 19-min-Lauf aus der
Hausbatterie (~0,13 kWh, SOC 39 → 37 %), dessen Ziel-Clip nicht heute,
sondern auf morgen/übermorgen lag (Cross-day-Carve-out) — und den der
Operator um 07:14 manuell beendete.

Drei strukturelle Defekte dahinter:

1. **Slotweise Wetten flackern am Gate-Rand.** Pass 2 bewertet jede
   30-min-Atom-Buchung alle ~30 s neu; eine marginale Buchung kippt mit
   jeder Prognose-Aktualisierung. Statt eines verbindlichen Laufs entstehen
   Mikro-Läufe (Schaltverschleiß, nutzlose Batteriezyklen).
2. **„So spät wie möglich" (L4) war nur slot-lokal wahr.** Die späteste
   *Buchung* ist nicht der späteste *sinnvolle Lauf*: frühmorgens für einen
   Clip in zwei Tagen entladen ist genau das Ziehen auf Prognosenbasis, das
   L4 verbietet.
3. **Cross-day verwässert den Vertrag.** Das Nacht-Carve-out erlaubte
   Pre-drain für morgige Clips, obwohl die Batterie heute eh auf soc_max
   läuft — der entladene Strom wird mittags 1:1 nachgeladen (Round-trip-
   Verlust geschenkt), ohne dass der Morgen ihn bräuchte.

## 2. Regeln

- **R1 (nur heute).** Pre-drain bedient ausschließlich den HEUTIGEN
  Höhepunkt. Kein Cross-day-Pre-drain für morgige Clips mehr — die
  F-NIGHT-RESCUE-Carve-out endet für kontinuierliche Lasten (Operator
  2026-08-01).
- **R2 (Peak).** Der Höhepunkt ist der erste Slot des Slot-0-Tages, in dem
  die akzeptierte Trajektorie OHNE diese Last (`current` nach Pass 1/2)
  exportiert. Kein Export heute → kein Block. Peak in Slot 0 → Pass 1
  regelt (kein Block).
- **R3 (Ziel-Energie).** Der Block deckt höchstens den ab dem Peak heute
  noch verlorenen Export (`sum(grid_export_wh)` der heutigen Slots ≥ Peak)
  — die c1-Opportunity ist damit konstruktiv begründet; die Block-c1-Prüfung
  (`export_drop ≥ (1−tol) × block × rt`) bleibt als Netz-und-doppelt-Check.
- **R4 (spätester Start, L4).** Der Block ist EIN zusammenhängender Lauf
  `[s, peak)`; er wird nur so weit rückwärts verlängert, bis die
  Ziel-Energie gedeckt ist (bzw. die Floors ihn kappen). Keine verstreuten
  Einzelslots, nichts Früheres als nötig.
- **R5 (Floors, ganzer Block = ein Kandidat).** Der gesamte Block läuft
  EINMAL durch den bestehenden Gate-Stack: Z2'' (Import-Invariante), R2
  (kein Slot netzgespeist/am Inverter-Cutoff), R5-preserve-daily-max (die
  Batterie muss heute trotzdem soc_max erreichen — genau die
  Operator-Bedingung „nur entladen, wenn sie eh vollläuft"), Z3
  (Buffer-Floor) und Z4 (fensterbasierte Stress-Reserve mit der
  Crossover-Rampe = die zeitabhängige Reserve Richtung Solarbeginn). Alle
  Gates verschlechtern sich monoton mit der Blocklänge → das erste Veto
  beendet die Verlängerung; der letzte akzeptierte Block ist der längste
  zulässige.
- **R6 (Mindestlänge).** Kürzer als `min_runtime` wird nicht gebucht; eine
  eigene Buchung im Peak-Slot (Pass 1) zählt als Fortsetzung mit
  (Rasterkanten-Fall F-SEAMLESS-PLAN).
- **R7 (Stabilitäts-Gate, Executor).** Geschaltet wird ein Block erst, wenn
  `PREDRAIN_BLOCK_STABLE_PLANS = 3` aufeinanderfolgende Pläne dieselbe
  `[start, end)`-Signatur liefern UND die erste mindestens
  `PREDRAIN_BLOCK_STABLE_MINUTES = 10` alt ist (die Zeit-Untergrenze deckt
  schnelle zustandsgetriebene Refresh-Kadenzen ~30 s, in denen drei Pläne
  in 90 s durchlaufen). Die Signatur „Block deckt Slot 0" zählt als
  konstantes „jetzt" (Slot-0-Start wandert sonst jeden Zyklus). Das Gate
  schaltet nur OFF→ON: ein LAUFENDER Block wird nicht geschnitten — fällt
  er aus dem Plan, greift die normale Dwell-Logik (min_runtime/min_off);
  G4 bleibt Backstop. Evidence ist in-memory (Neustart zählt neu =
  sichere Richtung).
- **R8 (Pass-Ordnung & Klassen).** Pass 1 (alle Lasten) → Pass 2 (nur
  `energy_limited=true`, unverändert mit c1-rt/c2/Z4/Tageslicht-Regel) →
  Pass 3 (nur `energy_limited=false`). Energie-limitierte bekommen keinen
  Block, kontinuierliche keine Slot-Wetten mehr. Konsequenz der Ordnung:
  bei umkämpfter Wett-Tiefe haben Energie-limitierte strukturell Vorrang
  (entspricht der stehenden Präferenz „lieber den Fossibot laden");
  Config-Order-Priorität gilt innerhalb Pass 3.

## 3. Abgrenzung (was bewusst NICHT getan wird)

- **Kein Cross-day-Nacht-Pre-drain** für kontinuierliche Lasten. Der
  Trade-off ist gewollt: in den Goldens steigt der verlorene Überschuss
  (z. B. s_night_predrain 3,99 → 6,32 kWh) und `min_soc` steigt (28 → 63 %)
  — lieber etwas Export verlieren als die Batterie planlos zu zyklen;
  Import sinkt überall (0,06 → 0,04 kWh).
- **Keine c2/β-Insurance** für kontinuierliche Lasten (nur noch
  energie-limitierte). Pass-1-Überschuss + R3-Block decken deren
  Bedarf.
- **Keine Interleaving-Priorität** zwischen Pass 2 und Pass 3 (wäre ein
  Redesign; R8 dokumentiert die Ordnungsfolge).
- **Block-c1 ist fast immer durch R5 dominiert** (ein re-maxter Trial zahlt
  die Tour ohnedies zurück); gehalten als expliziter Guard für
  Charger-saturierte Refill-Kanten.

## 4. Tests

- `tests/core/test_optimize.py` — T-Serie auf Same-day-Block-Geometrie
  umgeschrieben (T1/T2/T4/T5, windowed Z4, FIX-1); c2/p90-/p10-Evidence auf
  energie-limitierte umscope­t; neuer Abschnitt **F-PREDRAIN-BLOCK** (Peak-
  Erkennung, latest-start = ceil(target/power), kein Export heute → kein
  Block, Cross-day-Verbot, Peak in Slot 0, Zwei-Lasten-Priorität,
  min_runtime, c1-Veto, eigene Pass-1-Buchung begrenzt den Block).
- Goldens: `golden_topology.json` + `golden_night_predrain.json`
  regeneriert (Diff oben §3: kein Szenario importiert mehr).
- `tests/ha/test_predrain_block.py` — Stabilitäts-Gate: Aktuierung erst
  nach 3 Plänen + 10 min; Block-Wegfall → Dwell-Stopp; Pass 1 unberührt;
  Evidence zählt/resetet/löscht, Zeit-Untergrenze greift.
