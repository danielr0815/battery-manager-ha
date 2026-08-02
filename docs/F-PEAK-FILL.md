# F-PEAK-FILL — At-max-Top-up für energie-limitierte Lasten (v0.20.0)

Status: operator decision 2026-08-01.

## 1. Befund

Live 01.08. 13:33: Hausbatterie am Maximum (96 %), PV 567–1049 W schwankend,
Fossibot B mit Restbudget (SOC 30 %) und ~736-900 W Ladeleistung. Bei
Überschuss < Ladeleistung ließ Pass 1 ihn aussetzen bzw. pulsieren, während
die Batterie am Max pinnte und Spitzen exportierte. Operator-Regel dazu
(R2): *„Haben die Fossibots noch freie Kapazität, die Ladeleistung ist >
überschüssige Leistung und der SOC ist auf dem Maximum, dann dürfen die
Fossibots trotzdem kurzzeitig aktiviert werden, sodass der SOC wieder unter
dem Max-Wert landet — mit vernünftiger Hysterese."*

## 2. Regeln

- **R1 (Peak-Füllung, bereits vorhanden — kein Code).** Fossibots sollen
  zum prognostizierten SOC-Peak geladen sein. Pass 1 bucht das Budget ab
  Export-Beginn (earliest-export-first); Pass-2-Ladephasen-Wetten (PV >
  Verbrauch) sind erlaubt und werden von den Gates genau dann verworfen,
  wenn die Wiederaufladung physikalisch nicht passt (Repro 01.08.: enge
  Mittagsspitze → verdrängte Ladung passt nicht unter die Charger-Kappe
  2300 W → abendlicher Import → Z2''-Veto; marginaler Tag → R5-Veto,
  Hausbatterie-Füllung hat Vorrang = R3). Für die Tagesbilanz ist der
  Aktivierungszeitpunkt innerhalb eines „Batterie wird eh voll"-Tages ohnehin
  irrelevant (Energieerhaltung).
- **R2 (At-max-Top-up, neu).** Eine energie-limitierte Last mit Restbudget
  darf auch Slots buchen, deren PV-Überschuss ihre Leistung nicht deckt —
  die Hausbatterie puffert die Differenz — solange:
  (a) `pv_wh > ac_wh + dc_wh` (positiver PV-Überschuss, keine
      Batterie-Nachtladung),
  (b) die Trial-Trajektorie den Slot **innerhalb der Hysterese-Bande**
      enden lässt: `soc_end >= soc_max − AT_MAX_TOPUP_BAND_PERCENT`
      (5 %, const.py-nahe Konstante in `core/optimize.py`), und
  (c) der volle Gate-Stack besteht (Z2''/R2/R5/Z3): R5 zwingt die
      Wiedererreichung von soc_max am selben Tag, Z2'' verbietet unbezahlte
      Dellen (Import) — die Delle ist also beweisbar aus sonst verlorenem
      Export refillt.
  Die Bandprüfung pro Slot IST die Hysterese: vom Bandboden dippt jeder
  Lauf darunter und wird verworfen, bis PV nachlädt; die Quantensuche
  (60→30 min) begrenzt tiefe Dellen automatisch.
- **R3 (Vorrang).** Hausbatterie-Füllung bleibt oberstes Ziel (R5 hart);
  kontinuierliche Lasten sind ausgenommen (ihre Puffer-Form ist der
  Pass-3-Block, F-PREDRAIN-BLOCK).

## 3. Abgrenzung / Entscheidungen

- **Band 5 % statt 2 %:** ein 30-min-Quantum einer ~750-W-Last dippt an
  einer 5-kWh-Batterie ~7 % bei Null-Überschuss — 2 % hätte fast jedes
  Top-up unterdrückt (Implementierungsbefund). 5 % lässt moderate
  Überschuss-Episoden zu und begrenzt den Ritt weiterhin flach.
- **Trade-off (Golden s4_midday_full):** der Top-up-Tag gewinnt (+67 Wh
  verhinderter Export), der Folgetag verliert etwas (−300 Wh, weil die
  Batterie ~3,6 % tiefer in die Nacht geht und die verlustfreien
  Morgen-Buchungen später/shrumpfen) — netto −0,2 kWh in DEM Szenario,
  akzeptiert: derselbe Round-trip-Preis wie beim (akzeptierten)
  Pre-drain-Block; der Operator-Fall (At-max-Pulsation) ist der
  Normalgewinn. Import +30 Wh = exakt 3×10 Wh Charger-Standby-Artefakte
  (die Klasse, für die `IMPORT_ARTIFACT_SLACK_WH = 50` existiert).
- **15-%-Toleranz bleibt** (Operator-Rückfrage 01.08.): Pass-1-Soft-Gate
  (max 15 % eines Laufs aus der Batterie) + Pass-2-c1-Honesty-Faktor —
  die Ehrlichkeitsbremse gegen Batterie-Round-trips auf Prognosebasis;
  R2 ist die einzige begründete Ausnahme, weil die Delle beweisbar aus
  verlorenem Export refillt.

## 4. Tests

- `tests/core/test_optimize.py`, Abschnitt F-PEAK-FILL: Buchung am Max mit
  Restbudget (Wortlaut `at-max top-up (peak fill)`); Band-Veto bei tiefer
  Delle + Quanten-Auto-Limitierung; kein Top-up ohne Restbudget / für
  kontinuierliche Lasten / bei PV ≤ Verbrauch; Z2''-Veto bei unbezahlter
  Delle; Hysterese-Form (Veto bis Refill über dem Bandboden).
- Re-Pins: gate_parity shared budget (2400 Wh), c2/p90-Szenarien auf
  Band-Exit umscoped (Top-up schattet c2 sonst physikalisch), Card-
  Invarianztest akzeptiert beide Pass-1-Wortlaute.
- Goldens regeneriert; s4-Diff oben §3 dokumentiert, kein Szenario
  importiert über die Artefakt-Slack hinaus.
