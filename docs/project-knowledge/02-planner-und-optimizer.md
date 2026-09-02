# Planner & Optimizer

**Stand: `main` @ v0.17.0 (Arbeitsstand 2026-07-31).** Dieses Dokument beschreibt den
HA-freien Planungskern (`core/optimize.py`, `core/simulate.py`,
`core/series.py`) exakt so, wie er implementiert ist: T*-Suche mit Merge-Probe
und Terminal-Credit-Rampe, die Energiefluss-Simulation, die Zwei-Pass-
Lastallokation mit ihrer vollständigen Gate-Familie, und was die
Ergebniszahlen wirklich bedeuten. Wer eine konkrete Planentscheidung
nachrechnen oder den Optimierer ändern will, liest hier.

Die `F-*.md` im `docs/`-Ordner sind **je die Spezifikation ihres Features** —
bei einem Konflikt mit dieser Zusammenfassung gilt die Wissensbasis, weil sie
(anders als manche älteren F-Docs) laufend gegen den Code geprüft wird.
**Im Zweifel gilt der Code.**

---

## 1. Der Ablauf einer Planungsrunde

`optimize.plan(config, inputs) -> PlanResult` in genau dieser Reihenfolge:

```
1. threshold, base_traj = search_threshold(config, inputs)      # T*-Suche
2. fixed_support = horizonweite Schedules manuell erzwungener PSUs (F-N2)
   allocation_base = simulate(..., fixed_support)               # sonst base_traj
3. load_plans, extra_ac, traj = allocate_loads(                 # Pass 1–3
       ..., allocation_base, fixed_support)
4. Kaskaden: offene Recovery nach dem vollständigen Endlastplan erneut aus
   direkter PV mit Same-Day-Exportnachweis allokieren
5. alloc_traj = traj                                            # MIT festem Support
6. feedin_wh, feedin_by_day = plan_feedin(..., alloc_traj, fixed_support)
   traj = simulate(..., feedin_wh=feedin_wh)                    # publizierte Trajektorie MIT Feed-in
7. dc24, dc48, traj = support_escalation(...)                   # automatische Notfall-Netzteile
8. windows = appliance_windows(...)                             # Advisor (G3)
9. Diagnostik: import_trade_used_wh, stressed_min_soc,
   pv_window_ends, threshold_horizon_end, prevented_export_by_day_wh
```

Wichtig: Nur ein **manuell erzwungener**, also vorab bekannter Supportpfad
gehört schon in jede Allokationsprobe. Damit sieht der Planer dieselbe
SOC-/Clip-Trajektorie wie die veröffentlichte Prognose. Automatische
Notfall-Eskalation bleibt nachgelagert. `prevented_export_by_day_wh` wird als
separater Ohne-Support-Gegenvergleich rekonstruiert, damit ein Winter-PSU die
Kennzahl nicht entwertet; `import_trade_used_wh` vergleicht dagegen die zwei
realen Allokationstrajektorien mit demselben festen Support.

Ein Planlauf ist **zustandslos**: keine Hysterese, kein Gedächtnis über Läufe
hinweg. Alles Stabilisierende (Schwellen-Trägheit, Dwells, Latches) sitzt im
Coordinator. Deshalb musste die Merge-Hysterese als *stetige Rampe* im Kern
gelöst werden statt als gespeicherter Zustand (§2.3).

---

## 2. T*-Suche (`search_threshold`)

### 2.1 Was T* ist

T* ist die SOC-Abschaltschwelle der Politik „Inverter ist an ⇔ SOC >
Schwelle" (`simulate.step_hour`: `inverter_on = soc_percent >
threshold_percent`). Der Scan probiert **jeden ganzzahligen Prozentwert** von
`lo` bis `hi`:

- `lo = _search_lo(config) = ceil(max(inverter_min_soc_percent, soc_min_percent
  + soc_buffer_percent))`
- `hi = floor(battery.soc_max_percent)`

Kostenfunktion je Kandidat:

```
cost = total_import_wh
     - terminal_factor * energy_wh(end_soc)
     + export_tiebreak * total_export_wh
```

`export_tiebreak` ist standardmäßig 0.05. Die Annahme erfolgt nur bei
`cost < best_cost - _EPS` (`_EPS = 1e-6`) — da aufsteigend gescannt wird,
**gewinnt bei Gleichstand die NIEDRIGERE Schwelle**. Das ist die
Operator-Regel „Nutzen" (D-A1b): lieber vor dem nächsten Überschuss entladen
als horten.

`terminal_factor` ist im Normalfall `battery.eta_discharge * inverter.eta`
(„full_factor", live ~0,92) — der Endstand der Batterie wird also mit dem
Wirkungsgrad bewertet, mit dem er später wirklich AC-seitig ankommt.

### 2.2 Die Merge-Probe (`_threshold_merge_probe`)

Eine **einzige** pessimistische No-Loads-Simulation (Schwelle = `lo`,
PV skaliert mit *demselben* Per-Slot-Vektor, den auch Z4 benutzt: P10 wo
Bänder existieren, sonst α) sucht den ersten Slot, in dem die Batterie
**voll ist und exportiert**:

```
if flow.soc_end_percent >= soc_max - 0.1 and flow.grid_export_wh > _EPS
```

Rückgabe `(end, margin_wh)`:

- `end` = `max(merge, 5)` — der **R5-Boden**: nie weniger als 6 Slots
  (Indizes 0..5), sonst würde T* über einem 1–2-Stunden-Fenster hüpfen.
  `None`, wenn es keinen gestressten Clip gibt oder der Merge ohnehin am
  Horizontende liegt.
- `margin_wh` = der Export **an diesem ersten** Clip-Slot: das stetige Maß für
  die Stärke des garantierten Clippings.

Der Merge-Gedanke (D-A4): jenseits eines garantierten Clips ist die Trajektorie
unabhängig von der heutigen Schwelle. Post-Merge-Ökonomie — z.B. Horten für
einen schwachen letzten Tag — darf nicht in die Vor-Merge-Entscheidung
lecken (live 2026-07-12 04:13: T* sprang 20→58, weil ein schwacher Dienstag in
den Horizont rutschte, obwohl Sonntags garantierter Clip die Nacht entkoppelte).

### 2.3 Terminal-Credit-Rampe (F-MERGE-HYSTERESIS / „F1")

**Das war die Hauptkorrektur von v0.16.0.** Vorher war die Merge-Entscheidung
ein Messerschnitt: ein Wh gestresster Clip kippte sowohl den Terminal-Credit
als auch die Horizontkürzung — und damit T* zwischen zwei Regimen, die ~1,8 kWh
auseinanderliegen. Live 2026-07-24 07:28–08:02: **24 T*-Flanken zwischen 20 und
61**.

Jetzt gilt (`_terminal_credit_factor`):

```
if margin_wh <= 0.0: return full_factor
ramp = min(margin_wh / MERGE_TERMINAL_RAMP_WH, 1.0)
return full_factor * (1.0 - ramp)
```

mit **`MERGE_TERMINAL_RAMP_WH = 250.0`** (definiert in `core/optimize.py`,
re-exportiert in `const.py`). 250 Wh ≈ bescheidene 250 W Clipping über einen
Slot: klein genug, dass ein echter garantierter Clip sie in einem Tick
überschreitet, breit genug, dass das Jitter am Clip-Beginn (margin ≈ 0) die
Kante nie erreicht.

**Kanten-Semantik — genau lesen:**

| `margin_wh` | Terminal-Credit | Horizont |
|---|---|---|
| kein gestresster Clip (`merge_end is None`) | voll (`full_factor`) | voll |
| `0 < margin < 250` | linear verblassend | **voll** (nicht gekürzt!) |
| `margin >= 250` | 0 | **gekürzt** auf `merge_end` |

Die Horizontkürzung nutzt also **dieselbe** Kante wie das vollständige
Verblassen des Credits (`merge_active = merge_end is not None and margin_wh >=
MERGE_TERMINAL_RAMP_WH`). Es überlebt keine zweite, unabhängige Messerkante,
und der Planer bleibt zustandslos.

Warum der Credit auf dem gekürzten Fenster überhaupt weg muss (F-NIGHT-RESCUE
F2 v2): am Merge-Punkt ist die Batterie per Konstruktion voll, den Endstand zu
kreditieren doppelzählt Energie, die der bestätigte Clip ohnehin nachfüllt.
Schlimmer: eine DC-Last bricht die exakte Terminal/Import-Aufhebung (DC wird
mit `eta_discharge` **ohne** Inverter aus der Batterie bedient, der Credit
rechnet aber mit `eta_discharge * eta_inverter`) — der Credit machte T* zu
einer schlecht konditionierten Messerkante, die live einen Ganztags-Hort bei
soc_max festnagelte. Ohne Credit ist `cost = import + tiebreak*export`
monoton in der Schwelle, der Scan entleert deterministisch bis `lo`.

`_threshold_merge_bound` ist derselbe Test als öffentlicher Helfer und speist
nur die Diagnose `threshold_horizon_end` (F-NIGHT-RESCUE R7), damit der
angezeigte Horizont immer der ist, den der Scan tatsächlich benutzt hat.

### 2.4 Rückgabe

`search_threshold` liefert `(threshold, base_trajectory)`, wobei die
Basis-Trajektorie **immer über den vollen Horizont** simuliert ist
(F-NIGHT-RESCUE R6) — die Allokations-Gates differenzieren ausschließlich
vollständige Horizonte gegeneinander. Bei aktivem Merge (oder leerem Horizont)
wird sie dafür neu simuliert.

---

## 3. Simulation (`core/simulate.py`)

### 3.1 Topologie

PV speist die **AC-Seite**. (Der Coordinator liefert die Stundenkurve
bereits AC-skaliert — η-Ableitung aus State vs. Kurvensumme: siehe
01-architektur-und-datenfluss.md §4.2.) Die Batterie lädt ausschließlich
über den AC→DC-Charger und entlädt ausschließlich über den DC→AC-Inverter.
DC-Lasten hängen direkt an der Batterie. `step_hour` mutiert nichts und
gibt einen `HourFlows` zurück.

### 3.2 Reihenfolge in `step_hour`

1. **PV skalieren** (`pv_scale`): 1.0 überspringt die Multiplikation
   bit-identisch. Ein **optimistischer** Faktor (> 1.0) wird am physikalischen
   Peak `pv.peak_power_w * duration` nachgeklemmt (FIX-8) — β darf keine PV
   erfinden, die das Array nie liefern könnte. Faktoren ≤ 1.0 werden nie
   geklemmt.
2. **AC-Bilanz früh**: `ac_total = slot.ac_wh + extra_ac_wh` (+
   `inverter.standby_power_w * duration`, wenn der Inverter an ist),
   `balance = pv_wh - ac_total`, `net_charging = balance > 0`. Diese Bilanz
   wird früh gebraucht, weil das 48 V-PSU-Gate sich bei Netto-Ladung selbst
   schließt (der reale Meanwell sieht die angehobene Busspannung).
3. **DC-Last auf die zwei Busse aufteilen** (F-N3): erst die feste
   `native48_base_w`-Grundlast abzweigen, dann `dc24_share` des Rests auf die
   24 V-Schiene, Rest = nativer 48 V-Bus. Neutral (base 0, share 1.0) landet
   alles auf der Schiene — bit-identisch zum Ein-Bus-Modell.
4. **24 V-Quelle**: entweder das Netz-PSU (Import = `served / psu24_eta`) oder
   der DC/DC aus dem 48 V-Bus (`bus_draw = served / dcdc_eta`). Nachfrage über
   dem Cap wird als `unserved_dc_wh` ausgewiesen.
5. **48 V-Support-PSU** (Direct-Offset-Modell): Gate offen nur wenn
   `dc48_support ∧ configured ∧ (gate_soc is None ∨ soc < gate_soc) ∧ not
   net_charging`. Deckt erst 1:1 die gleichzeitige Buslast, der Rest lädt über
   `eta_charge` die Batterie (mit Edge-Taper, damit ein Slot `gate_soc` nie
   überschießt). Abgerechnet wird die **gelieferte** Energie / `psu48_eta`.
6. **Restliche Buslast entlädt die Batterie** bis zum Floor. Ein Fehlbetrag
   wird **nicht hier** importiert, sondern als `shortfall_dc` in die
   AC-Abrechnung getragen — sonst würde derselbe Slot Netz importieren, während
   PV exportiert (Energieerhaltung).
7. **AC-Abrechnung.** Bei `balance >= 0`: (a) DC-Fehlbetrag aus dem
   PV-Überschuss über den Charger decken, (b) Batterie über den Charger laden
   (`min(balance, charger.max_power_w * duration, headroom/(eta_charge *
   charger.eta))`), Charger-Standby abziehen, Rest exportieren; wird die Bilanz
   durch den Standby negativ, ist das Import. (c) Ein verbleibender
   DC-Fehlbetrag importiert. Bei `balance < 0`: DC-Fehlbetrag importiert, dann
   deckt der Inverter das Defizit bis zum Inverter-Floor
   (`max(soc_min, inverter_min_soc_percent)`) und maximal
   `inverter.max_power_w * duration`; der Rest ist Import.

**Es gibt kein Export-Limit.** Der Export ist unbegrenzt (`grid_export +=
max(0, balance)`); „Lost Surplus" ist genau dieser Export.

Effizienzen (Live-Defaults aus `DEFAULT_CONFIG`): `charger_efficiency` 0,92,
`inverter_efficiency` 0,95, `battery_charge/discharge_efficiency` je 0,97;
Standby: Charger 10 W, Inverter 15 W.

### 3.3 `simulate` über den Horizont

`pv_scale` ist entweder ein **Skalar** (ganzer Horizont pessimistisch/
optimistisch) oder eine **Sequenz** je Slot — Letzteres braucht Z4, um nur das
Erholungsfenster einer Wette zu stressen und den Rest bei Nominal-PV zu lassen.

---

## 4. Pre-Drain, α/β und die Unsicherheitsvektoren

### 4.1 Bänder statt Skalare (`_effective_uncertainty`)

Pro Slot wird ein **Stress-** und ein **Optimismus-Faktor** gebildet:

| Slot hat Band (D2) | `stress` | `optimism` |
|---|---|---|
| ja | `clamp(p10/pv_wh, 0.1, 1.0)` | `clamp(p90/pv_wh, 1.0, 2.0)` |
| nein | `alpha` (`predrain_pv_confidence`) | `beta` (`upper_pv_reserve`) |

Ein Slot **hat ein Band** genau dann, wenn (`quantile_band_slots`, die einzige
Wahrheit, auch von der Coordinator-Diagnostik benutzt):

```
p10 is not None ∧ p90 is not None
∧ pv_wh >= QUANTILE_RATIO_MIN_WH (= 25.0 Wh)
∧ (p90 - p10) > max(1.0 Wh, 0.01 * pv_wh)
```

Ein **kollabiertes** Band (p10 == p90) zählt als **kein** Band. Das ist
Absicht: es bedeutet „keine Evidenz", nicht „keine Unsicherheit" — sonst wäre
der Z4-Stress ausgerechnet auf den historienlosen Bins *schwächer* als der
Skalar α.

Zwei abgeleitete Schalter, einmal pro Lauf berechnet:

- `c2_active = any(optimism > 1)` — die c2-Versicherungsmaschinerie läuft nur
  dann (spart eine ganze Zusatzsimulation).
- `z4_active = any(stress < 1)` — die Z4-Stressprüfung ebenso.

### 4.2 Der gerampte Z4-Floor (`_ramped_stress_floors`, F-NIGHT-RESCUE R8)

Z4 schützt die **Inverter-Reserve**, nicht das Speicherminimum. Der Floor je
Kandidatenslot `i`:

```
floor(i) = inverter_min_soc_percent + buffer_eff(i)
buffer_eff(i) = min(soc_buffer, 100 * stressed_deficit(i) / capacity)   falls Crossover existiert
              = soc_buffer                                             sonst
```

`stressed_deficit(i)` summiert `max(0, consumption - stressed_pv)` ab Slot `i`
bis zum ersten Slot, dessen **gestresste** PV den Verbrauch deckt. Sinn: der
Buffer soll das *verbleibende dunkle Defizit* überbrücken — steht der
PV-Crossover kurz bevor, braucht es weniger Puffer. Nur dieser Floor rampt;
Z3s `soc_min + buffer` (absoluter Batterieschutz) bleibt statisch.

### 4.3 Die PV-Fenster (`pv_windows`, F-PREDRAIN F4)

Pro Kalendertag `[erster, letzter]` Slot mit „starker" PV, d.h.
`pv_wh / duration >= strong_pv_cutoff_w` (Live-Default **200 W**). Das Fenster
rahmt die Stunden, in denen die **obere** Reserve (Absorptionsspielraum nahe
soc_max) geschützt bleiben muss; danach steht die Sonne hinter dem Haus und
die Reserve darf ausgegeben werden. `pv_window_end_hour` (Site-Override, kein
Default) kappt das **Ende** am letzten Slot, der vor dieser Lokalstunde
beginnt. Ein Tag ohne starken Slot hat kein Fenster — seine Slots können nur
über das nominale c1-Gate buchen.

### 4.4 `stressed_min_soc_percent` (Diagnose)

`None`, wenn kein Slot gestresst ist (α == 1.0 und keine P10-Evidenz) **oder**
wenn überhaupt keine Pass-2-Buchung existiert. Sonst: die **tiefste Wette** ist
der früheste Pass-2-Slot *irgendeiner* Last (`i0 = min(...)`); ihr
Erholungsfenster endet an `_refill_index(alloc_traj, i0, soc_max - 0.1)`; über
`[i0, recovery]` wird mit demselben Per-Slot-Vektor gestresst wie im Gate und
das Fenster-Minimum berichtet. Was der Sensor zeigt, ist also exakt das, was
das Gate geschützt hat.

---

## 5. Lastallokation (`allocate_loads`)

### 5.1 Grundstruktur

```
Pass 1: LOAD-OUTER in Prioritätsreihenfolge, Slots AUFSTEIGEND
Pass 2: SLOT-OUTER absteigend (latest-first), innen die Lasten in Prioritätsreihenfolge
```

**Pass 1 (Direktüberschuss)**, F-PLANNER-HONESTY R7: eine Last bucht ihre
komplette Pass-1-Allokation, bevor die nächste den Horizont überhaupt sieht —
strikte Priorität. Alle Lasten laufen die Slots **aufsteigend** ab
(F-RESCUE-EXPORT R1): ein Pass-1-Kandidat besteht das Soft-Surplus-Gate nur
dort, wo die Batterie bereits voll ist und **exportiert**; Warten rettet also
nichts, sondern verliert den gegenwärtigen, sicheren Überschuss an eine
spätere Prognosewette.

**Pass 2 („zielbasiert")**: erlaubt Stunden *ohne* Direktüberschuss — z.B.
Vorentladen, um vor einer Produktionsspitze Platz zu schaffen — aber nur, wenn
die Voll-Horizont-Resimulation beweist, dass die Energie, durch die Batterie
zeitversetzt, **von ohnehin verlorenem Überschuss gedeckt** ist. Läuft
**latest-first** (Operator-Entscheidung 2026-07-05): dort kann die Batterie
noch puffern, und Aufholen mit besserer Information schlägt eine frühe Wette.
Die Schleife startet am letzten Slot mit Export (`last_export`); danach kann
das Gate ohnehin nie erfüllt werden, und ein exportfreier Horizont überspringt
Pass 2 komplett.

**Priorität** = die Reihenfolge in `SystemConfig.loads`. Der Kern hat kein
Prioritätsfeld; der Coordinator materialisiert die gespeicherte Priorität in
`ordered_load_subentries` (F-LOAD-PRIORITY R3).

### 5.2 Quantisierung: wie viel wird pro Entscheidung gebucht

`_committed_hours(load, slot) = max(slot.duration, min_runtime_min / 60)`

Das ist die Laufzeit, zu der eine Einschaltentscheidung den Executor **wirklich**
verpflichtet (der Dwell hält den Schalter). Ohne diese Untergrenze ließ ein
1-Minuten-Slot 0 rund 5 Wh durch jedes Gate, während der Dwell danach ~250 Wh
unverbucht lud (degenerierter Slot-0-Artefakt, live 2026-07-05 04:59).

`_quantised_hours(load, slot, rem, power_w)` liefert Kandidaten **größte
zuerst**:

1. immer `_committed_hours` (der Regressionsanker: passt der ganze Slot durch
   alle Gates, ist der Plan bit-identisch zum Vor-F-SUBHOUR-Verhalten);
2. dann kürzere Vielfache `k * q` mit `q = min_runtime_min / 60`, absteigend,
   jeweils strikt kleiner als `whole` (F-SUBHOUR R2: **nie** unter ein Quantum);
3. **nur** für energielimitierte Lasten mit `gate_stop_capable`: ein einziger
   finaler Kandidat `rem * (1 - 1e-9) / max(power_w, nominal)` — angehängt als
   **letzter**, nur wenn `rem >= GATE_TOPUP_MIN_WH = 50.0` und das Ergebnis
   echt zwischen 0 und `q` liegt (F-GATE-TOPUP R2/R3, das „Stall-Band").

### 5.3 Platzierung und Seamless Spill (F9)

`_spread_energy(extra, slots, start, power_w, hours)` legt die Leistung in
Echtzeit ab Slot `start` ab und **läuft über Slotgrenzen** — eine
Min-Runtime-Verpflichtung kurz vor Stundenende landet teilweise im Folgeslot.
Zurück kommen die Trial-Reihe und die belegten `(Slot, Stunden)`-Paare.

Überlappt eine Verpflichtung einen Slot, der **für dieselbe Last** schon
gebucht ist, greift `_seamless_spill` (**F-SEAMLESS-PLAN / F9**, die
:31-Wurzelkorrektur):

```
if len(covered) >= 2 and scheduled[covered[1][0]]:
    return _spread_energy(extra, slots, i, power_w, slots[i].duration)
return None
```

Begründung: ein partieller erster Slot, der kürzer als ein
Min-Runtime-Quantum ist, kann nur den vollen Quantum-Kandidaten anbieten —
der überläuft dann in Slot i+1. Ist i+1 bereits diese Last, ist das kein
Konflikt: die Last läuft nahtlos über die Grenze, der Dwell wird von der
gebuchten Fortsetzung erfüllt, und Slot i muss nur seine **eigene**
Restdauer committen. Ohne das kollidierte der Spill, der Overlap-Guard warf
Slot i weg, und die Empfehlung einer *tatsächlich laufenden* Last wurde an
jeder Rasterkante zurückgezogen (live: jede Morgenstunde um :31). Gibt
`_seamless_spill` `None` zurück, ist es eine echte Doppelbuchung und der
Kandidat wird verworfen.

Nach einem Seamless-Trim gilt `commit_h = slots[i].duration` und die Buchung
wird im `why`-String als *„seamless continuation"* benannt (nicht als
Gate-Topup, obwohl sie ebenfalls unter einem Quantum liegt).

### 5.4 Pass 1 im Detail

Je Last (in Prioritätsreihenfolge), je Slot aufsteigend, je Kandidatendauer
größte zuerst:

1. **Sättigung**: `rem is not None ∧ rem < max(power_w, nominal) * commit_h`
   → überspringen. Der Floor auf `nominal` verhindert, dass ein verfallener
   Schätzer das Gate aufweicht.
2. `_spread_energy` → ggf. `_seamless_spill`.
3. **Soft-Surplus-Gate (D-A4)**: die Batterie darf höchstens
   `battery_tolerance` der committeten Energie tragen.
   `surplus_cov` = Export in Slot i **plus** anteiliger Export der
   Spill-Slots (`export * take/duration`). `battery_share = max(0, power_wh -
   surplus_cov) / power_wh` muss `<= battery_tolerance` sein. Gelesen wird der
   Export der **aktuell akzeptierten** Trajektorie `current` (R8) — frühere
   Buchungen sind darin schon einsimuliert, die alte Intra-Slot-Dekrementierung
   ist damit durch den exakten Wert ersetzt.
4. **Harte Gates per Voll-Resimulation**, in dieser Reihenfolge:
   `import_ok` → `slots_serviceable` → `preserves_daily_max` →
   `_degrades_min_soc`.
5. Annahme: `extra`/`current` übernehmen, Slots markieren, `booked_any` setzen,
   `planned_wh` und `remaining` fortschreiben, Allocation + Reason anhängen,
   **`break`** (der größte machbare Quantum ist platziert).

Gebucht wird, was **tatsächlich im Horizont landet** (`placed_h = Σ take`) —
eine am Horizontende abgeschnittene Verpflichtung zählt nur anteilig,
während die Gates bewusst mit der vollen committeten Energie geprüft wurden.

### 5.5 Pass 2 im Detail

Nur wenn `current.total_export_wh > _EPS`. Vorbereitet wird:

- `current_beta` — die optimistische Referenzsimulation der aktuell
  akzeptierten Reihe (nur wenn `c2_active`), bei jeder Annahme aufgefrischt;
- `stress_base` — Cache der gestressten Fenster-Minima, **gekeyt auf
  `(i, hi)`** (das Fensterende hängt vom Spill der Kandidatendauer ab, FIX-7)
  und bei jeder Annahme geleert.

Pro Slot `i` (absteigend ab `last_export`), pro Last (Priorität), pro
Kandidatendauer:

1. **Tageslicht-Regel** (F-GATE-PARITY Verfeinerung 2): eine energielimitierte
   Last eröffnet **nie** eine Wette in einem Null-PV-Slot (`pv_wh <= 0`).
   Nächte bleiben kontinuierlichen Lasten vorbehalten. Bewusst `pv_wh > 0` und
   **nicht** `in_window` — Vorfenster-Tageslicht-Vorladung bleibt erlaubt.
   Zusätzlich ein **Spill-Guard**: keine Verpflichtung, die in Nachtslots
   überläuft (kürzere Kandidaten bekommen ihre Chance).
2. Sättigungsprüfung, `_spread_energy`, ggf. `_seamless_spill` (wie Pass 1).
3. Harte Gates: `import_ok` (Z2''), `slots_serviceable` (R2/planner-G4),
   `preserves_daily_max` (R5), `_degrades_min_soc` (Z3).
4. **Wettabrechnung**: `recovery = _refill_index(traj, i, soc_full)` — der
   erste Slot ab `i`, an dem die **Trial**-Trajektorie soc_max erreicht; ohne
   Nachfüllung erst das Horizontende (F-STRICT-SURPLUS R3).
5. **R6 — kein tagesübergreifender Tageslicht-Pre-Drain**
   (`_crossday_daytime_bet`): eine Wette in einem Slot mit `pv_wh > 0`, deren
   Batterie erst an einem **späteren** Kalendertag auf soc_max kommt, wird
   abgelehnt. Nacht-/Vordämmerungsslots (`pv_wh == 0`) behalten die
   F-NIGHT-RESCUE-Ausnahme.
6. **Opportunitätsgate (c1 ODER c2):**

   ```
   export_drop = current.total_export_wh - traj.total_export_wh
   need    = (1 - battery_tolerance) * power_wh * rt      # c1
   need_c2 = (1 - battery_tolerance) * power_wh           # c2
   ```

   `rt` (F-NIGHT-RESCUE R1) ist der physikalische AC→Batterie→AC-Rundlauf
   `charger.eta * eta_charge * eta_discharge * inverter.eta` (live ~0,822),
   geklemmt auf `(0, 1]`. **c1** akzeptiert bei `export_drop + _EPS >= need`.
   Ohne den `rt`-Faktor war die alte Forderung für Nachtläufe *physikalisch
   unerfüllbar* — jeder 22:00–05:00-Slot des Vorfalls 2026-07-11 scheiterte
   genau dort, während ~3,3 kWh Clipping für den Folgetag prognostiziert waren.
   Ein Direkt-PV-Lauf senkt den Export ~1:1 und besteht erst recht; der Faktor
   verhindert nur, die Umwegverluste doppelt zu berechnen.

   **c2** (nur wenn `c2_active ∧ in_window(i)`): eine optimistische
   Zusatzsimulation (`pv_scale=optimism_vec`) muss
   `drop_beta >= need_c2` liefern — **ohne** `rt`, denn c2 beurteilt eine
   optimistische **Direkt-PV**-Absorption, keinen Batterieumweg.
7. **Z4** (nur wenn `z4_active`): Stressfenster `[i, hi]` mit
   `hi = max(recovery, covered[-1][0])` (der Spill drainiert die Reserve
   mit, FIX-7), Stressvektor nur innerhalb des Fensters, sonst 1.0.
   `_z4_reject(trial_wmin, floor, base_wmin)` lehnt ab **nur wenn beides**
   gilt: die gestresste Fenster-Reserve bricht den (gerampten) Floor **und**
   ist schlechter als dasselbe Fenster-Minimum der bereits akzeptierten
   Reihe. Die zweite Bedingung ist die **Entlastungsklausel**: ein Dip, den
   die Baseline ohnehin enthält, darf keine Wette vetoen, die ihn nicht
   vertieft.
8. Annahme: wie in Pass 1, plus `stress_base.clear()` und ggf. `current_beta`
   erneuern. Reason-String benennt c1 („covered by otherwise-lost export
   (N Wh), latest feasible slot") oder c2 („in-window insurance (p90|beta),
   latest feasible slot"); `insurance_src` ist `p90`, wenn der angenommene Slot
   selbst ein Band trug, sonst `beta` (F-QUANTILE-BANDS R6).

---

## 5A. Early Feed-In (`plan_feedin`, F-FEEDIN, v0.23.0)

Spec: `docs/F-FEEDIN.md`. Der Pass verlegt den **unvermeidlichen** Restexport
zeitlich in den Morgen: PV-Überschuss wird bei leerlaufender Batterie direkt
durchgereicht statt mittags bei voller Batterie zu exportieren. Gesamtexport
invariant, nur das Timing wandert vom Mittagspeak weg.

- **Position**: nach `allocate_loads` (braucht den `alloc_traj`-Restexport als
  unvermeidliche Menge) und vor `support_escalation`. Neutral-Default
  (`FeedInParams.enabled = False` oder `max_w = 0`) short-circuitet ohne
  Zusatzsimulation — bit-identischer Plan (§10).
- **Zielmenge pro Kalendertag** = Restexport (`grid_export`) des Tages aus der
  Median-Prognose (R3, keine Stress-Skalierung des Ziels).
- **Buchung** aufsteigend ab Slot 0, nur wenn `remaining > ε` ∧ Trial-SOC am
  Slotanfang > `feedin_min_soc_percent` (absoluter Floor, R5) ∧ < soc_max
  (volle Batterie exportiert natürlich) ∧ Slot-Überschuss > 0. Rate =
  `remaining / max(Stunden bis deadline_hour, Slotdauer)`, gedeckelt auf
  `min(max_w, Slot-Überschuss)`; nach der **weichen** Deadline kollabiert der
  Nenner auf die Slotdauer — Überzug so schnell, wie der Überschuss erlaubt
  (R4). Jede Buchung wird in die Trial re-simuliert.
- **Physik** (`step_hour`, R2): Feed-in wird **vor** der Batterieladung aus
  dem Überschuss bedient, `grid_export += feedin` 1:1, geklemmt auf den
  Überschuss, im Defizit-Zweig 0 — keine aktive Entladung.
  `HourFlows.feedin_wh` macht die Export-Zusammensetzung (natürlich vs.
  vorverlegt) nachvollziehbar.
- **Z4 nur als Bremse** (R6): die finale MIT-Feed-in-Reihe wird unter dem
  P10/α-Stressvektor gegen `_ramped_stress_floors` re-simuliert (mit der
  `_z4_reject`-Entlastungsklausel); bei Verletzung wird der **späteste**
  Feed-in-Slot an/vor der Verletzung zurückgegeben, iteriert, bis die Floors
  halten.
- **Ausgabe**: `PlanResult.feedin_schedule_w` (W je Slot) und
  `feedin_by_day_wh`. Die publizierte Trajektorie ist die MIT-Feed-in-
  Simulation — der flache Morgen-Verlauf ist in der SOC-Prognose sichtbar.
  `prevented_export_by_day_wh` vergleicht bewusst weiter base vs. alloc
  **ohne** Feed-in (R4 der Gegenrechnung unverändert).

Executor-Seite (Setpoint, Trim, Manuell-Modus): Dokument 03 §11A.

---

## 6. Die Gate-Familie im Überblick

| Regel | Wo im Code | Bedeutung in einem Satz |
|---|---|---|
| **Z2''** (F-STRICT-SURPLUS R1) | `import_ok` | Die gesamte Allokation darf über die No-Loads-Basis hinaus höchstens `IMPORT_ARTIFACT_SLACK_WH = 50.0` Wh simulierten Netzbezug hinzufügen — ein absoluter Artefakt-Freibetrag (Charger-Standby), **kein** mit gerettetem Export skalierendes Budget. |
| **Z2' (RETIRED)** | `ControlParams.import_trade_ratio` | Der proportionale Import-Trade wurde am 2026-07-19 stillgelegt; das Feld existiert nur noch für alte Aufrufer und wird vom Planer **ignoriert**. |
| **Z3** | `_degrades_min_soc(traj, current, buffer_floor)` | Ein Kandidat ist nur dann verboten, wenn er unter `soc_min + soc_buffer` fällt **und** dabei schlechter ist als die aktuell akzeptierte Reihe (Dips, die die Referenz ohnehin hat, vetoen nichts). |
| **Z4** | `_z4_reject` + `_ramped_stress_floors` | Gefenstertes Stress-Gate: die pessimistische Reserve über `[i, recovery]` muss den (gerampten) Inverter-Floor halten — mit derselben Entlastungsklausel wie Z3. |
| **R2 / planner-G4** (F-STRICT-SURPLUS) | `_slot_serviceable`, `slots_serviceable` | Kein gebuchter Slot darf netzgespeist sein oder den Cutoff berühren — geprüft an **beiden** SOC-Endpunkten und für **alle** je gebuchten Slots (Ratschen-Schluss). |
| **R3** (F-STRICT-SURPLUS) | `_refill_index` | Das Wettfenster endet dort, wo die **Trial**-Trajektorie wirklich soc_max erreicht — nicht am PV-Fensterende des Tages. |
| **R4** (F-STRICT-SURPLUS) | `prevented_export_by_day_wh` | Kontrafaktische Tageskennzahl: wie viel Export die Lastläufe verhindert haben. |
| **R5** (F-STRICT-SURPLUS) | `preserves_daily_max` | Der Plan muss an **jedem** Tag, an dem die No-Loads-Basis soc_max erreicht, weiterhin soc_max erreichen. |
| **R6** (F-STRICT-SURPLUS) | `_crossday_daytime_bet` | Kein tagesübergreifender **Tageslicht**-Pre-Drain; nur Nachtslots dürfen für den Folgetag vorentladen. |
| **R1/R2** (F-NIGHT-RESCUE) | `rt`, `need` | Das c1-Bedürfnis wird am physikalischen Rundlaufwirkungsgrad bemessen. |
| **R4/R5/R6** (F-NIGHT-RESCUE) | `_threshold_merge_probe`, `search_threshold` | Merge-begrenzter T*-Scan, R5-Boden von 6 Slots, Basis-Trajektorie immer voll-horizont. |
| **R7** (F-NIGHT-RESCUE) | `_threshold_merge_bound` → `threshold_horizon_end` | Der benutzte Merge-Horizont wird nach außen sichtbar gemacht. |
| **R8** (F-NIGHT-RESCUE) | `_ramped_stress_floors` | Der Z4-Buffer rampt mit dem verbleibenden gestressten Defizit bis zum PV-Crossover. |
| **c1** | `accept = export_drop >= need` | Der nominale Drain wird nachweislich aus verlorenem Export nachgefüllt. |
| **c2** (F-PREDRAIN F4) | `trial_beta`, `need_c2` | Nur **im PV-Fenster**: eine optimistische (P90/β) Simulation würde die Energie absorbieren — Versicherung der oberen Reserve. |
| **D2** (F-QUANTILE-BANDS) | `quantile_band_slots` | Bandpräsenz je Slot; `p10 == p90` = kein Band. |
| **Tageslicht-Regel** (F-GATE-PARITY) | `load.energy_limited and slot.pv_wh <= 0` | Die **einzige** verbliebene Klassenregel: energielimitierte Lasten buchen keine Nacht-Pass-2-Slots. |
| **Gate-Parität** (F-GATE-PARITY R1/R2) | gesamter Pass 2 | Beide Lastklassen sehen dieselbe Gate-Menge; die Priorität allein entscheidet über umkämpfte Energie. |
| **G3** | `appliance_windows` | Advisor: könnte ein voller Gerätelauf jetzt starten, ohne Zusatzimport und ohne den Buffer-Floor zu verschlechtern? |
| **D-A9** | `support_escalation` | Letzte Rettung: DC-Lasten auf Netz-PSUs verlagern, bevor die Batterie durch den Boden fällt. |

### 6.1 `_slot_serviceable` genau gelesen

```
if flow.soc_start_percent <= inverter_floor + _EPS
   or flow.soc_end_percent <= inverter_floor + _EPS:
    return False
grid_fed = not flow.inverter_on and (slot.pv_wh + _EPS < slot.ac_wh + flow.extra_ac_wh)
return not grid_fed
```

- **Cutoff, beide Endpunkte.** Ein Slot, der *unter* dem Cutoff **beginnt**,
  würde vom realtime-G4 des Executors ohnehin verweigert — ihn zu buchen plant
  Phantom-Rettungsenergie und bremst die Erholung über 20 %. Ein Slot, der am
  Cutoff **endet**, ist ihn heruntergeritten; da ein batteriegespeister
  Entladeslot mit eingeschaltetem Inverter dem Grid-Fed-Test entkommt, ist
  dieser Endpunkt seine einzige Absicherung.
- **Grid-fed** = Inverter aus **und** PV deckt die AC-Last nicht. Ist der
  Inverter aus, aber PV deckt (das Voll-Batterie-Hort-Regime mit T* = soc_max
  macht `inverter_on` auf jedem Slot False, ebenso jeder Exportslot), ist die
  Last PV-gespeist und **nicht** grid-fed.

`slots_serviceable` prüft die Slots dieses Kandidaten **und** alle bereits
gebuchten (`booked_any`) erneut — eine spätere Annahme an früherer Stunde darf
einen akzeptierten Lauf nicht heimlich in einen netzgespeisten verwandeln.

---

## 7. Energielimitierte Lasten

| Mechanismus | Wo | Wirkung |
|---|---|---|
| `remaining` | `SurplusLoadState.remaining_energy_wh` | `(target_soc - soc)/100 * capacity_wh`, ≥ 0; lokal in `allocate_loads` fortgeschrieben (`remaining[load_id] -= placed_wh`) |
| Ziel-SOC | `SurplusLoad.target_soc_percent` | Obergrenze der aufnehmbaren Energie |
| Sättigungs-Gate | Pass 1 + Pass 2 | Überspringen, wenn `rem < max(power_w, nominal) * commit_h` |
| **Gate-Topup** | `_quantised_hours`, `GATE_TOPUP_MIN_WH = 50.0` | Nur mit `gate_stop_capable` (= Charge-Enable-Entity konfiguriert): ein einziger finaler Kandidat unter Quantumsgröße, damit die Last nicht dauerhaft unter Ziel-SOC parkt (das „Stall-Band"). Legitim, weil G1s dwell-freier Ziel-Stopp exakt `rem` liefert. |
| Tageslicht-Regel | Pass 2 | Keine Null-PV-Slots, kein Spill in die Nacht |
| Priorität | Reihenfolge in `config.loads` | Außerhalb einer Kaskade entscheidet die konfigurierte Reihenfolge. Innerhalb einer Kaskade steht die direkte Endlast vor zusätzlichem Laden der Mitglieder, weil der Speicherweg zusätzliche Wandlungsverluste verursacht. |
| Kaskaden-PV-only | interne Recovery-Phasen + `direct_surplus_only_load_ids` | Recovery bis zum Entladeziel darf direkte PV statt gleichzeitiger Hausbatterieladung verwenden, wenn der vollständige lokale Tag denselben AC-Betrag aus späterem Export zurückholt; mehrere Mitglieder dürfen dabei parallel laden. Top-up oberhalb des Entladeziels bleibt je Laufabschnitt Restexport-gedeckt. Batterie-Toleranz, Pass 2 und At-Max-Topup bleiben gesperrt. Nach dem kontinuierlichen Endlastblock wird offene Recovery vor Feed-in erneut geprüft. Erst nachdem jedes Mitglied seine streng zielbegrenzte Chance hatte, darf ein vollständig exportgedecktes Mindestlaufzeit-Quantum das Entladeziel geringfügig überschreiten. |

Das `final top-up to target`-Label im `why`-String erscheint genau dann, wenn
`commit_h < min_runtime_min/60 - _EPS` **und** es kein Seamless-Trim war.

---

## 8. Was die Ergebniszahlen bedeuten

### 8.1 Horizontsummen vs. Tageswerte — die häufigste Fehlinterpretation

`PlanResult` liefert:

```
grid_import_kwh  = traj.total_import_wh / 1000.0
grid_export_kwh  = traj.total_export_wh / 1000.0
lost_surplus_kwh = traj.total_export_wh / 1000.0     # identisch zu grid_export!
```

`traj` ist hier die **finale** Trajektorie *nach* der Support-Eskalation, und
die Summen laufen über den **gesamten Horizont** — bei drei Prognosetagen also
über heute + morgen + übermorgen (bis Mitternacht nach dem letzten Tag). Ein
`lost_surplus` von 7 kWh heißt **nicht** „heute gehen 7 kWh verloren".

`lost_surplus_kwh == grid_export_kwh` per Definition: verlorener Überschuss
*ist* der nach der Lastallokation verbleibende Export.

**Tageswerte** liefert der Coordinator in `_daily_surplus_breakdown` (Key
`daily_surplus`, F-PERDAY-SURPLUS R1), gruppiert nach `slot.start.date()` in
Planer-Lokalzeit — ein Slot zählt auf dem Tag, an dem er **beginnt**. Je Tag:

| Feld | Herkunft |
|---|---|
| `lost_surplus_kwh` | Σ `flow.grid_export_wh` des Tages |
| `grid_import_kwh` | Σ `flow.grid_import_wh` des Tages |
| `loads_kwh` | Σ `flow.extra_ac_wh` — nur **Überschusslasten**; Appliances stecken per Konstruktion in `ac_wh` und tauchen hier nie auf |
| `prevented_export_kwh` | aus `result.prevented_export_by_day_wh` |

Die Tagessummen ergeben (Rundung beiseite) die Horizontsummen.

### 8.2 `prevented_export_by_day_wh` (F-STRICT-SURPLUS R4)

```
prevented[day] = max(0, base_export[day] - alloc_export[day])
```

Beide Seiten **ohne** Support: `base_traj` = ohne Lasten/PSUs; bei manuell
erzwungenem Support wird für die Metrik aus der akzeptierten Lastserie eigens
eine `metric_alloc_traj` ohne PSUs rekonstruiert. So kann ein Winter-PSU die
Zahl nicht deflationieren, obwohl seine reale Trajektorie bereits die
Lastallokation steuert. Diese Kennzahl beantwortet die Betreiberfrage „Warum
läuft eine Last, obwohl der SOC nie das Maximum erreicht?".

### 8.3 `import_trade_used_wh`

```
max(0, alloc_traj.total_import_wh - allocation_base.total_import_wh)
```

Seit F-STRICT-SURPLUS R1 ist das **kein Tausch** mehr gegen geretteten Export,
sondern durch `IMPORT_ARTIFACT_SLACK_WH` (50 Wh) begrenzt. Ein
Nacht-Pre-Drain mit Default-Parametern liest hier typischerweise **bis ~50 Wh**
(das Charger-Standby-Artefakt, das auf dem Slack reitet) — **nicht** 0.0.
Bei manuell festem Support enthalten beide Seiten exakt dasselbe
Support-Schedule; die Kennzahl misst weiterhin ausschließlich den Mehrimport
der Überschusslasten.

### 8.4 Weitere Felder

| Feld | Bedeutung |
|---|---|
| `threshold_percent` | T* (roh, ohne die Coordinator-Trägheit) |
| `inverter_on` | Rohpolitik für Slot 0; die Hysterese macht der Coordinator |
| `min_soc_percent` / `max_soc_percent` | Extrema der finalen Trajektorie |
| `hours_to_max_soc` | Index des ersten Slots mit `soc_end >= max_soc`, **+1** |
| `pv_window_ends` | ISO-Datum → letzte Starkstunde (`hour_of_day`) des Absorptionsfensters |
| `threshold_horizon_end` | Ende des merge-gekürzten T*-Horizonts; `None` = Voll-Horizont-Scan |
| `stressed_min_soc_percent` | siehe §4.4 |
| `support_dc24_now` / `support_dc48_now` | Slot-0-Zustand der Notfall-PSUs |
| `feedin_schedule_w` (v0.23.0) | geplante Feed-in-Leistung je Slot (W), leer wenn deaktiviert |
| `feedin_by_day_wh` (v0.23.0) | gebuchte Feed-in-Energie je Kalendertag (Wh) |

### 8.5 Die `why`-Strings je Slot

`LoadPlan.reasons` enthält **einen** Eintrag je `allocations`-Eintrag, in
derselben Reihenfolge; der Coordinator fächert sie in
`load_plans[id].schedule[].why` je gedecktem Slot auf. Formate:

```
pass 1 @ 07-25 09:00: direct surplus, 60 min x 410 W, battery share 3%
pass 1 @ 07-25 09:31: direct surplus, 29 min x 410 W, battery share 0%, seamless continuation
pass 2 @ 07-25 14:00: covered by otherwise-lost export (487 Wh), latest feasible slot
pass 2 @ 07-25 11:00: in-window insurance (p90), latest feasible slot
pass 1 @ 07-25 16:00: direct surplus, 12 min x 505 W, battery share 0%, final top-up to target
```

„latest feasible slot" ist bei Pass 2 strukturell wahr (die Schleife läuft
absteigend und nimmt den ersten passenden Slot). Der Zusatz
`, seamless continuation` bzw. `, final top-up to target` schließt sich
gegenseitig aus — Seamless gewinnt.

---

## 9. Appliance-Advisor und Support-Eskalation (Kurzfassung)

**`appliance_windows` (G3)**: je Gerät mit `opportunistic_start` wird ein
hypothetischer Lauf (`series.insert_appliance_run`) eingefügt und **unter
denselben Support-Schedules** wie die Vergleichs-Trajektorie simuliert (sonst
gäbe der Advisor bei aktivem Winter-PSU falsche Freigaben). Freigabe genau
dann, wenn der Import nicht steigt **und** `_degrades_min_soc` nicht greift.
Der Coordinator UND-verknüpft das Ergebnis zusätzlich mit `not floor_guard`.

**`support_escalation` (D-A9)**: zwei Hysterese-Stufen über **absoluten**
SOC-Schwellen (bewusst unabhängig vom Planungs-Buffer, damit ein dynamisch
geweiteter Buffer die Netzteile nie verschiebt):

```
soc_min < dc48_activate < dc48_recovery <= dc24_activate < dc24_recovery
Defaults: 5.5 / 10 / 10 / 11
```

Stufe 1 ersetzt den DC/DC durch das 24 V-Netzteil, Stufe 2 legt das
48 V-Netzteil obendrauf. Manuell übersteuerte PSUs (`dc24_forced_on`/
`dc48_forced_on`, F-N2) gelten als permanent aktiv und werden bereits in der
Lastallokation mitgeführt, damit Prognose, Clip-Erkennung und Lastplanung zum
realen Winterbetrieb passen. Die 48-V-Simulation liefert standardmäßig nur
unterhalb `gate_soc_percent = 40`; 100 % bedeutet bewusst „Gate deaktiviert".
Ausführung und der R2-Spannungsregler: Dokument 03 und
`docs/DC_TOPOLOGY.md`.

---

## 10. Neutralitäts- und Regressionsanker (beim Ändern beachten)

Der Kern ist voll von bewussten Bit-Identitäts-Ankern. Wer sie bricht, bricht
die Golden-Tests:

- **Neutrale Dataclass-Defaults**: `ControlParams` hat α = β = 1.0,
  `import_trade_ratio = 0.0`, `pv_window_end_hour = None`; die empfohlenen
  Live-Werte stehen ausschließlich in `const.DEFAULT_CONFIG`.
- **`pv_scale == 1.0`** überspringt die Multiplikation komplett.
- **Keine Bänder irgendwo** ⇒ die Vektoren sind uniform ⇒ der Plan ist
  identisch zum Skalar-Zeitalter bei denselben α/β (F-QUANTILE-BANDS R8).
- **Ganzer Slot als erster Kandidat** in `_quantised_hours` ⇒ eine
  Voll-Stunden-Platzierung ist bit-identisch zum Vor-F-SUBHOUR-Verhalten.
- **F-N3-Neutraldefaults** für Share (100 %), η (1.0) und Caps (0 A =
  uncapped) reproduzieren das Ein-Bus-Modell. Das 48-V-SOC-Gate ist seit
  v0.25.8 absichtlich nicht neutral: Default 40 %, explizit 100 % = offen.
- **Merge-Rampen-Endpunkte**: bei margin 0 und bei margin ≥ 250 Wh verhält sich
  der Scan exakt wie vor F-MERGE-HYSTERESIS.
- **F-FEEDIN-Neutraldefault** (`FeedInParams.enabled = False` / `max_w = 0`,
  v0.23.0): `plan` short-circuitet ohne Zusatzsimulation; eine `feedin_wh`-
  Nullserie in `simulate` ist bit-identisch zu keiner Serie.

---

**Verifikation: geprüft gegen Commit `a172d48` (v0.16.2); die v0.17.0-Einträge
gegen den Arbeitsstand vom 2026-07-31.**

Wichtigste Code-Anker dieses Dokuments:

1. `core/optimize.py::search_threshold` + `_threshold_merge_probe` +
   `_terminal_credit_factor` — T*-Suche und die 250-Wh-Rampe.
2. `core/optimize.py::allocate_loads` — die beiden Pässe samt aller inneren
   Gate-Funktionen (`import_ok`, `slots_serviceable`, `preserves_daily_max`,
   `in_window`).
3. `core/optimize.py::_slot_serviceable` / `_z4_reject` / `_degrades_min_soc` /
   `_crossday_daytime_bet` / `_refill_index` — die R-/Z-Regelfamilie.
4. `core/optimize.py::_quantised_hours` / `_committed_hours` /
   `_spread_energy` / `_seamless_spill` — Quantisierung und F9.
5. `core/simulate.py::step_hour` — Energieflüsse, Wirkungsgrade, kein
   Export-Limit; `core/optimize.py::plan` — die Diagnostikfelder und ihre
   exakte Definition.
