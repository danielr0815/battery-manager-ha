# Executor & Lastregeln

Kaskadenmitglieder tragen `managed_by_cascade` und umgehen den unabhängigen
Load-Executor. Wake, Leistungsnachweis, Handover, Floor, Ownership, Retry und
Safe-OFF liegen in `CascadeManager`; siehe
[`../F-CASCADE-STORAGE.md`](../F-CASCADE-STORAGE.md).
G4 und der globale Datenverlust-Shed werden deshalb ausdrücklich an diesen
alleinigen Actor-Besitzer delegiert und führen dort die vollständige
downstream→upstream Safe-OFF-Sequenz aus; der generische Lastpfad überspringt
auch bei diesen Zwangsstopps sämtliche Kaskadenmitglieder.

**Stand: `main` @ v0.16.2 (2026-07-25).** Dieses Dokument beschreibt die
Ausführungsschicht in `coordinator.py`: was die Empfehlungs-Entities wirklich
zeigen, in welcher Reihenfolge der Schaltpfad entscheidet, und welche Gates,
Dwells und Latches ein Gerät ein- oder ausschalten. Wer diagnostiziert, warum
ein Gerät nicht (oder unerwartet) schaltet, liest hier — der Planer selbst
steht in `02-planner-und-optimizer.md`.

**Im Zweifel gilt der Code.**

---

## 1. Die wichtigste Vorbemerkung: Empfehlung ≠ Gerätezustand

`binary_sensor.SurplusLoadRecommendationSensor` (die „Empfehlung" je Last)
liest `coordinator.data["load_plans"][id]["active"]`. Dieser Wert kommt aus
`_effective_load_active(load_plan, now)`:

```
if self._floor_guard_active:      return False
deadline = self._load_run_deadline.get(load_plan.load_id)
return bool(load_plan.active_now) and (deadline is None or now < deadline)
```

Das ist der **Plan** (Slot-0-Aktivierung, gekappt an der eingefrorenen
Sub-Stunden-Deadline und am G4-Floor-Guard) — **nicht** der reale
Schalterzustand. Ein Gerät kann:

- Empfehlung **an**, Gerät **aus** — z.B. min_off-Dwell läuft noch, Schaltbefehl
  nicht bestätigt, oder es ist eine reine Empfehlungs-Last ohne Steuerschalter,
  deren Automatisierung nicht greift;
- Empfehlung **aus**, Gerät **an** — z.B. F10/F11-Latch-Hold (der Plan bucht
  wegen F5 mit ~0 W, der Executor hält die Last dennoch), oder min_runtime
  hält noch.

Der reale Zustand steht in `load_plans[id]["charging_active"]`
(`_charging_is_active`: Stecker an **und**, falls konfiguriert, Charge-Enable
an; `None` bei Entity-Dropout ⇒ letzter bekannter Zustand wird gehalten).
Diese Unterscheidung ist eine bekannte Betreiber-Verwirrung (siehe Dokument
06).

---

## 2. Der Schaltpfad `_apply_load_switching`

Aufruf im Zyklus **nach** Hysterese, Floor-Guard, Support-Schaltung und
Schatten-Plan (Dokument 01 §3.2). Signatur:
`(result, now, slot_durations, pv_power_w, shadow_active)`.

Vorab: läuft noch ein `_load_switch_task`, kehrt die Methode **sofort** zurück
(kein paralleles Schalten).

Dann je Last-Subentry:

### 2.1 Lasten ohne Steuerschalter

Kein `CONF_LOAD_CONTROL_SWITCH` ⇒ nichts zu schalten, aber
`_maintain_recommendation_deadline` hält die publizierte `active`-Flagge
sauber (§5.3) — die Automatisierung des Betreibers folgt ihr. Danach `continue`.

### 2.2 Entscheidungszweige, in Reihenfolge

```
hold = self._latch_hold_ok(subentry_id, data, pv_power_w, shadow_active)
if floor_guard:                                   desired=False, reason="G4 floor guard"
elif hold:                                        desired=True,  reason="latch hold"
elif current and deadline is not None and now >= deadline:
        └─ Seamless-Extension möglich?  ──ja──►   continue (kein OFF, kein Dwell-Stempel)
        └─ sonst                                  desired=False, reason="quantum end"
else:                                             desired=bool(plan.active_now)
                                                  reason = "plan slot" | "plan off"
```

**Präzedenz merken:** G4 > Latch-Hold > Deadline/Seamless > Plan. Der
Latch-Hold hat also Vorrang vor der Deadline-Logik (eine alte Sub-Stunden-
Deadline aus einem früheren Normallauf wird beim Hold **gelöscht**, damit der
gehaltene Lauf nicht zwangsabgeschaltet wird), aber **nie** vor G4.

Ist `desired == current`, passiert nichts (`continue`) — mit einer Ausnahme
(Audit 04.08.2026): steht das Charge-Enable-Gate physisch auf `on`, obwohl
die Last weder lädt noch laden soll (`desired == current == False`), reiht
der **Orphan-Sweep** ein OFF ein. Genau dieser Zustand war nach einem
fehlgeschlagenen Stecker-ON für jeden anderen Pfad unerreichbar — live stand
das Gate 3,4 Tage verwaist auf AN (LOAD_CONTROL.md §3: das Gate darf nur
während aktiver Ladung an sein). Der Sweep ist dwell-exempt (das Gate
schaltet keinen Laststrompfad, G1-Argument in §2.3) und rührt den Stecker
nicht (Ownership/Policy laufen über den normalen OFF-Zweig).

### 2.3 Dwell-Auswahl (Split-Dwell, F-SUBHOUR R14)

**ON → OFF** gilt `min_runtime_min` (Default 30), mit drei Ausnahmen:

| Bedingung | Effekt |
|---|---|
| `floor_guard` | `dwell_min = 0` (dwell-**exempt**), `flicker_eligible = False` — jede Dwell-Minute liefe netzgespeist (Vorfall 2026-07-18 06:21: ~10 min × 432 W) |
| Last ist im `_load_latch_hold` | `flicker_eligible = False` — ein Gate-Verlust, der einen Latch-Hold beendet, ist kein Plan-Flackern und darf keine F8-Fortsetzung säen |
| **G1**: `energy_limited ∧ charge_enable ∧ SOC >= target_soc` | `dwell_min = 0`, `flicker_eligible = False`, `reason = "target SOC reached"` (falls vorher „plan off") |

**OFF → ON** gilt `min_off_min` (Fallback: `min_runtime_min`),
`flicker_eligible = False`; Ausnahme ist die F8-Flicker-Continuation (§6).

Danach:

```
last = self._last_load_switch.get(subentry_id)
if last is not None and now - last < timedelta(minutes=dwell_min): continue
```

Der Dwell-Zeitstempel wird **nur bei bestätigtem Schalten** im Executor
gesetzt — eine fehlgeschlagene Aktuierung verbraucht das Dwell-Fenster nicht
und blockiert keinen sofortigen Retry.

Die Aktion wandert als 7-Tupel `(subentry_id, data, desired, plug_was_on,
run_h, flicker_eligible, reason)` in eine Liste, die als
`entry.async_create_background_task` ausgeführt wird.

### 2.4 `_execute_load_switching`

Unter `self._switch_lock`, je Aktion:

- **Race-Guard**: ist beim Abarbeiten `_floor_guard_active` und die Aktion ein
  ON, wird sie verworfen („dropping queued switch-ON") — der Guard kann
  getrippt sein, *nachdem* das ON eingereiht wurde.
- **ON**: erst Charge-Enable an (bei Fehlschlag Abbruch), dann — falls nicht
  schon an — den Stecker (Erfolg ⇒ `_load_plug_owned = True`). Schlägt der
  Stecker-ON fehl, nachdem das Enable bereits AN bestätigt wurde, rollt der
  Executor das Enable sofort wieder zurück (WARNING) — sonst bliebe das Gate
  für immer verwaist AN (Audit 04.08.2026: 3,4 Tage live; `charging_active`
  bleibt False, also erreicht kein späterer Pfad das Gate je wieder).
  Scheitert auch das Rollback-OFF (erneute WARNING), übernimmt der
  Orphan-Sweep aus §2.2; `charging_active = True`, Dwell-Stempel,
  `_load_last_off` löschen, Deadline einfrieren (§5), **eine** INFO-Zeile.
- **OFF**: Policy prüfen (`INPUT_OFF_POLICY_AUTO` / `ALWAYS` / `KEEP`), erst
  Charge-Enable aus (kein Bestätigung ⇒ Abbruch, Laden läuft ja weiter), dann
  ggf. Stecker aus; danach `plug_owned = False`, `charging_active = False`,
  Dwell-Stempel, `_load_latch_hold.discard(...)`,
  `_load_last_off[id] = (now, flicker_eligible)`, Deadline + Timer löschen,
  **eine** INFO-Zeile.
- Am Ende: `_save_persistent_state()`, Listener aktualisieren; ist der
  Floor-Guard aktiv, wird zusätzlich ein `async_request_refresh()` ausgelöst
  (Race-Teil 2: ein Refresh, der den Guard während dieses Tasks trippte, kam
  früh zurück und konnte seine Zwangs-OFFs nicht einreihen).

### 2.5 V9a: das INFO-Schaltprotokoll

Pro **bestätigtem** Schaltvorgang genau eine INFO-Zeile, mit dem am
Entscheidungspunkt abgeleiteten Anlass:

```
Load <Name> -> ON (<reason>)
Load <Name> -> OFF (<reason>; input off|stays on)
```

Mögliche `reason`-Werte: `plan slot`, `plan off`, `quantum end`,
`G4 floor guard`, `latch hold`, `target SOC reached`, jeweils ggf. mit dem
Suffix ` (flicker continuation)` beim ON. Das ist der schnellste Einstieg in
jede Live-Forensik: die Zeile sagt, welcher Zweig gewonnen hat.

---

## 3. Die Executor-Gates

### 3.1 G1 — dwell-freier Ziel-SOC-Stopp

Nur für energielimitierte Lasten **mit** Charge-Enable-Entity und nur wenn eine
SOC-Lesung vorliegt: `dwell_min = 0`. Begründung: `min_runtime` schützt Relais
und Kompressoren, aber das Enable-Gate schaltet keinen mechanisch
schützenswerten Laststrompfad (der Stecker wird, wenn überhaupt, stromlos
geschaltet), während jede Dwell-Minute mit echter Leistung über das Ziel
hinausschießt (~250 Wh in 30 min bei ~505 W ⇒ 95 % statt 90 %). Reine
Steckerlasten behalten den vollen Dwell, ein fehlender SOC ebenfalls
(konservativ). Der bestätigte Schaltvorgang stempelt den Dwell trotzdem, sodass
`min_off` ein Re-On voll gattert — ein am Ziel pendelnder SOC kann den Schalter
nicht flattern lassen.

G1 ist auch die Rechtfertigung des planerischen **Gate-Topups**: weil G1 exakt
`rem` liefert, darf der Planer ein finales Quantum unter `min_runtime` buchen
(Dokument 02 §5.2).

### 3.2 G2 — Stale-SOC-Latch (`_update_soc_stale`)

Die Fossibot-Integration liefert **gecachte SOC-Werte mit frischen
Zeitstempeln** — Verfügbarkeits- und Altersprüfungen greifen also nicht.

**Evidenz** (alle nötig): das Gerät lädt demonstrabel — `charging` (bei
Schaltlasten der reale Ladezustand, bei Empfehlungslasten
`_load_plan_active`) — **und** die rohe Leistungsmessung liegt über der
Standby-Bar. Bleibt der SOC dabei **exakt** unverändert für
**`STALE_LOAD_SOC_MIN = 12`** Minuten, wird er als stale gelatcht (WARNING
einmalig).

Die Uhr misst *kontinuierliche Ladeevidenz*, nicht Wandzeit: sie **resettet**,
sobald das Laden aufhört oder die Standby-Bar unterschritten wird (ein
End-of-Charge-Taper sammelt also nie falsche Evidenz). Lasten ohne SOC- oder
Power-Entity latchen nie (keine Evidenz, R7).

**Release**: irgendeine *andere* Live-Lesung, egal ob geladen wird oder nicht
(INFO einmalig).

**Persistenz** (Audit 02.08.2026): Latch und Evidenzuhr überleben einen
Coordinator-Reload — persistiert werden der eingefrorene Wert und die
**akkumulierten Sekunden**, nicht der Wandzeit-Start: Offline-Zeit ist keine
Ladeevidenz, also läuft die Uhr nach dem Reload mit der überlebten Evidenz
weiter statt bei null (und ohne die Downtime gutzuschreiben).

**Wirkung**: `available = False` in `_get_load_states` ⇒ der Planer bucht die
Last nicht mehr gegen ein eingefrorenes `remaining`, und das plan-getriebene
OFF läuft durch den normalen Executor-Pfad.

### 3.3 F4 — Telemetrie-Freeze-Watchdog (`_update_telemetry_freeze`)

Redundanter Wächter **nur für energielimitierte Lasten**, unabhängig vom
Schaltpfad und von G2. Anlass: der Fossibot B2 (Empfehlungslast) fror SOC
**und** Eingangsleistung 174,7 h lang bei 87,5 % / exakt 144 W ein (20.07.
13:30 → 27.07. 20:10 CEST; „95 h" war der Zwischenstand vom 24.07., Audit
07.08.2026), während der
Planer vier Tage lang denselben ~50-Wh-Topup neu buchte, die Empfehlung im
15-Minuten-Raster duty-cyclete und ~0,4 kWh Entfeuchter-Slots verdrängte —
ohne eine einzige Warnung.

Regel: bleiben **SOC UND gemessene Leistung exakt** unverändert für
**`FREEZE_STALE_HOURS = 6`** Stunden, **während** die Empfehlung im Fenster
mindestens einmal aktiv war ⇒ Latch (WARNING einmalig), `available = False` —
dieselbe Konsequenz wie G2. Release: irgendeine Änderung von SOC oder Leistung
(INFO einmalig).

Ein simpler `last_changed`-Watchdog ist bewusst vermieden: gecachte Werte
tragen frische Zeitstempel, ein legitim untätiges Gerät würde falsch
auslösen. Der Schlüssel ist **exakte Stasis trotz aktiver Empfehlung über
Stunden**. Latch und Evidenzuhr sind persistiert (Audit 02.08.2026: ein
Coordinator-Reload löschte den Latch, die Empfehlung duty-cyclete danach
110 min für ein nachweislich abgestecktes Gerät — ~225 Wh Fehlbuchung; der
Präzedenz-Freeze lief 174,7 h, ein Reload darf die 6-h-Evidenz nicht bei
null neu aufbauen). Semantik wie G2: akkumulierte Sekunden, Offline-Zeit
zählt nicht als Freeze-Evidenz.

Beide Latches werden nach außen als `load_plans[id]["soc_stale"]` gemeldet
(True, wenn G2 **oder** F4 greift).

### 3.4 G4 — Floor-Guard (`_update_floor_guard`)

Die harte Operator-Regel: *„Wenn der Inverter aus ist oder der SOC 20 %
erreicht, dürfen Zusatzlasten nicht mehr angesteuert werden."* Zusatzlasten
laufen **nie** netzgespeist.

Der Guard hat **zwei Zweige**:

**(a) SOC-Zweig — unconditional und latchend.**

```
prev = self._floor_guard_active
if prev is None or prev:  soc_low = soc <= floor or soc < floor + hyst   # Release-Band
else:                     soc_low = soc <= floor                          # Trip-Schwelle
```

Trip bei `soc <= inverter_min_soc_percent` (Default 20), Release erst
**oberhalb** `floor + hysteresis_percent` — und dank der Oder-Verknüpfung
strikt über dem Floor auch bei Hysterese 0 (sonst überlappten Trip und Release
genau am Floor und der Guard flatterte jeden Zyklus). `prev is None` (also
direkt nach einem Neustart) benutzt das Release-Band ⇒ **startet konservativ
gelatcht**. PV kann diesen Zweig **nicht** aufheben — ein abgeschalteter
Inverter bleibt abgeschaltet.

**(b) Rec-off-Zweig — PV-gegatet (Planner-G4-Parität).**

```
rec_off_grid_fed = (not self._inverter_recommendation) and (pv_power_w < running_load_power_w)
```

`pv_power_w` = `slot0.pv_wh / slot0.duration` (die einzige PV, die der
Coordinator kennt), `running_load_power_w` = Summe der gelernten Leistung
(Fallback: nominal) aller Lasten mit `lp.active_now`. Striktes `<` (PV genau
gleich zählt als gedeckt) spiegelt `_slot_serviceable`s `pv_wh + _EPS < ac_wh`.

Diese Gatung war die Korrektur vom 2026-07-24: der frühere unbedingte
Rec-off-Zweig zwang im PV-reichsten Morgenfenster (PV 1391–1680 W gegen einen
~410-W-Entfeuchter) das Gerät ~67 Minuten aus.

`guard = soc_low or rec_off_grid_fed`.

**WARN-Ratenlimit (Part C):** pro Grund (`"soc"` / `"rec"`) höchstens **eine
WARNING alle 15 Minuten**, weitere Trips loggen DEBUG
(`_floor_guard_warn_at`). Anlass: live 9 WARNINGs in 37 Minuten bei flatterndem
Guard. Der Release loggt immer INFO.

**Wirkung** (drei Stellen):

1. `_apply_load_switching`: alle gesteuerten Lasten dwell-exempt AUS, kein ON;
2. `_execute_load_switching`: eingereihte ONs werden verworfen;
3. `_effective_load_active` ⇒ die publizierte Empfehlung ist `False`, damit
   auch die Automatisierungen des Betreibers stoppen. Zusätzlich werden die
   Appliance-Startfenster mit `and not floor_guard` verundet, und
   `_load_is_running` (Laufzeitzähler, Plan-Fallback) zählt nicht weiter.

Nach außen: `data["floor_guard_active"]`, auch als Attribut am
Inverter-Empfehlungs-Sensor.

---

## 4. Inverter-Empfehlung: Trägheit und Hysterese

**Schwellen-Trägheit** (`_apply_threshold_inertia`): die angezeigte/benutzte
Schwelle wird nur übernommen, wenn sich T* um mindestens
`threshold_inertia_percent` (Default **2 %**) bewegt hat. Sonst bleibt der alte
Wert stehen.

**Hysterese** (`_apply_hysteresis`):

```
if soc >= threshold + hyst: desired = True
elif soc <= threshold - hyst: desired = False
```

(`hysteresis_percent` Default **1 %**; dazwischen bleibt es beim Status quo.)
Eine *Änderung* wird zusätzlich durch `min_switch_interval_s` (**60 s**)
gedrosselt; erst dann werden `_inverter_recommendation` und
`_last_inverter_switch` gesetzt.

Der BM **schaltet den Inverter nicht selbst** — er publiziert nur die
Empfehlung (`binary_sensor.inverter_status`); das Umsetzen ist Sache der
Betreiber-Automatisierung. Intern ist die Empfehlung aber ein echtes Gate: sie
geht in den G4-Rec-off-Zweig und in `_latch_hold_ok` ein.

---

## 5. Dwells, Deadlines und Seamless Runs

### 5.1 Die zwei Dwells

| Option | Default | Wirkt |
|---|---|---|
| `CONF_LOAD_MIN_RUNTIME_MIN` | 30 min | ON → OFF (Mindestlaufzeit) |
| `CONF_LOAD_MIN_OFF_MIN` | 30 min (Fallback = min_runtime) | OFF → ON (Anti-Short-Cycle, Kompressorschutz) |

### 5.2 Die eingefrorene Laufzeit-Deadline (F-SUBHOUR, Ansatz A)

Beim bestätigten ON wird — falls `run_h > 0` — eine Deadline eingefroren:

```
off_min = max(min_runtime_min, round(run_h * 60))
off_at  = now + timedelta(minutes=off_min)
```

und ein Punkt-in-der-Zeit-Timer armiert (`_arm_off_timer` →
`async_track_point_in_time`), der zur Deadline einen Refresh anfordert — der
300-s-Poll wäre zu grob, um einen Sub-Stunden-Lauf präzise zu beenden. Die
Deadline wird **persistiert**, damit ein Neustart einen Lauf nie entdeckelt.

Für energielimitierte Lasten ist sie eine **Obergrenze** über dem primären,
pegelgetriebenen Ziel-SOC-Stopp (R8): ein stale-gecachter Fossibot-SOC könnte
den Plan sonst die ganze Stunde aktiv halten und `real_power × 1 h` liefern,
wo ~150 Wh validiert waren. Bei einem Gate-Topup-Quantum **kürzer** als
`min_runtime` ist die Deckelung bewusst **länger** als die gebuchte Laufzeit —
sie bleibt nur Stale-SOC-Obergrenze, während G1 der eigentliche Stopp ist.

### 5.3 Deadline für Empfehlungs-Lasten (`_maintain_recommendation_deadline`)

Ohne Steuerschalter regelt die Deadline nur die publizierte `active`-Flagge.
Sie wird an der OFF→ON-Flanke verankert
(`off_min = max(min_runtime, round(active_run_hours*60))`) und gehalten, sodass
der Sensor vom Laufbeginn bis `run_start + max(min_runtime, run)` aktiv liest.
Ist der Plan an der abgelaufenen Deadline noch aktiv **und** die Last
verlängerungsfähig, wird **sofort** neu verankert (ohne min_runtime-Boden, der
war ja schon bedient) — die Empfehlung dippt an der Quantumsgrenze nicht.
Für verlängerungs-**un**fähige Lasten (FIX-5) wird nach
`deadline + min_off` neu verankert, damit der Sensor duty-cycled statt
stundenlang festzuhängen.

### 5.4 Seamless Runs (v0.14 / F-SEAMLESS-RUNS)

An einer abgelaufenen Deadline gilt: **dieser** Refresh hat den Plan bereits
mit frischen SOC-/`remaining`-Daten neu gerechnet, *bevor* geschaltet wird. Hat
er einen zusammenhängenden Lauf ab jetzt neu gebucht (`active_now ∧ run_h > 0`)
und ist die Last verlängerungsfähig, wird die Deadline verlängert, sofort
persistiert und mit `continue` weitergegangen: **kein OFF, kein Relaiszyklus,
kein Kompressor-Neustart, kein Dwell-Stempel**.

Damit gilt die entscheidende Semantik: **`min_off` wird nur von
ABSICHTLICHEN Deaktivierungen armiert** — Plan-Aus, Ziel-Stopp, Floor-Guard,
Deadline *ohne* Neubuchung. Eine Rasterkante allein armiert es nie.

**Verlängerungsfähigkeit** (`_seamless_extension_ok`):

| Lastklasse | Regel |
|---|---|
| nicht energielimitiert | **immer** verlängerbar (die Deadline ist für sie reine Quantums-Buchhaltung) |
| energielimitiert | nur wenn G2 die Last **jetzt** überwachen kann: Steuerschalter konfiguriert **und** SOC- und Power-Entity aktuell **lesbar** (nicht bloß konfiguriert — bei einem Sensorausfall sammelt G2 keine Evidenz) **und** nicht stale-gelatcht |

Alles, was G2 nicht überwachen kann (inkl. Empfehlungs-only-Lasten mit
Energielimit), behält die F-RESIDUAL-TOPUP-Duty-Cycle-Deckelung — für die ist
sie die einzige Verteidigung gegen ein stale `remaining`, das einen kleinen
Topup in eine unbegrenzte Ladung streckt.

---

## 6. F8 — Flicker-Continuation

**Problem:** ein kurzes Planflackern an einer Slot-/Quantumsgrenze (die
Empfehlung fällt für Sekunden bis Minuten weg und kommt zurück) wurde von
`min_off` zu einer 15+-Minuten-Zwangspause verstärkt. Live: 23.07. ~0,3–0,4 kWh
Export bei SOC 97–99 %; 24.07. zwei ~15-min-Pausen bei PV 1,7–1,8 kW,
`lost_surplus` 6,71 → 7,11 kWh.

**Konstanten** (`const.py`):

| Konstante | Wert | Bedeutung |
|---|---|---|
| `REC_FLICKER_CONTINUATION_MIN` | **5** min | Fenster nach einem empfehlungsgetriebenen Stopp, in dem ein ON als Fortsetzung gilt |
| `REC_FLICKER_MAX_CONTINUATIONS` | **2** | Ping-Pong-Deckel: so viele Waiver pro Fenster |
| `REC_FLICKER_PINGPONG_WINDOW_MIN` | **30** min | Länge des Ping-Pong-Fensters |

**Klassifikation `flicker_eligible`** — wird beim **OFF** bestimmt und in
`_load_last_off[id] = (timestamp, eligible)` festgehalten:

| Stopp-Grund | eligible? |
|---|---|
| Plan-Aus / Deadline ohne Neubuchung („quantum end") | **ja** |
| G4-Floor-Guard | nein |
| G1-Ziel-SOC-Stopp | nein |
| Ende eines Latch-Holds (Last war in `_load_latch_hold`) | nein |

Ein G4-Stopp, der innerhalb von 5 Minuten fortsetzt, würde die Last sonst
wieder netzgespeist starten — deshalb dürfen Sicherheitsstopps das Fenster
nicht säen.

**Gewährung** (`_flicker_continuation_ok`): innerhalb des 5-min-Fensters, und
nur solange das Ping-Pong-Budget reicht. Jede *Off-Episode* (gekeyt auf ihren
Zeitstempel) zählt einmal — ein im selben Zyklus wiederholtes ON (z.B. nach
fehlgeschlagener Aktuierung) wird nicht doppelt belastet. Ist das Budget
erschöpft, gilt wieder das normale `min_off`, ein wirklich flatternder Plan
kann also nie einen Kompressor kurztakten. Gewährt ⇒ `dwell_min = 0` und der
Reason bekommt ` (flicker continuation)`.

---

## 7. F10/F11 — Latch-Hold über einen Schatten-Plan

### 7.1 Das Deadlock

F5 plant eine Last mit **gelatchtem** F-L7-Power-Warning an ihrer *gemessenen*
Leistung (~2 W, `saturated_power_w`). Der ehrliche Plan fragt sie also nie an.
Der Latch selbst löst aber nur, **während die Last BM-kommandiert läuft und
wieder im Band ist**. Ohne Gegenmaßnahme bleibt ein wieder geleerter Tank
unbegrenzt aus dem Plan.

### 7.2 F10 — opportunistisches Halten

Statt periodisch zu probieren, **hält** der Executor eine noch gelatchte,
*schaltbare* Last (Steuerschalter + Leistungsrückmeldung) eingeschaltet,
solange die Überschuss-Gates halten — so läuft ein geleerter Tank in dem
Moment, in dem die Leistung da ist (Operator-Wunsch 2026-07-24: „der Entfeuchter
kann IMMER laufen, wenn die Leistung da wäre, nicht nur alle paar Stunden").
Kein Intervall, kein Auto-Stopp: der Hold endet nur bei Gate-Verlust (normaler
min_runtime-gedwellter Stopp; G4 sofort) oder bei Latch-Release (dann übernimmt
der normale Plan nahtlos). Die laufende Last steht in `_load_latch_hold` —
allein damit ihr Gate-Verlust-Stopp als flicker-**in**eligible klassifiziert
und beim Latch-Release verworfen wird.

### 7.3 F11 — der Schatten-Plan (v0.16.2)

F10 benutzte anfangs eine Heuristik „Slot-0-PV ≥ Lastleistung". Die war
**strenger als die normale Planung** und verbot Buchungen, die der echte Planer
ohnehin macht (z.B. einen frühmorgendlichen, batteriegespeisten
Pass-2-Lauf „covered by otherwise-lost export" bei PV ≈ 0 mit SOC über dem
Floor). Operator-Prinzip: *eine gelatchte Last darf nie strengeren Regeln
unterliegen als dieselbe Last im Normalzustand.*

`_latched_hold_candidates()` liefert die strukturelle Vorauswahl: Warnung
gelatcht **und** Steuerschalter **und** Power-Entity.

`_latch_shadow_active(config, inputs, load_states)` — **nur wenn Kandidaten
existieren** (kein permanenter Doppelplan):

```
shadow_states = tuple(replace(state, saturated_power_w=None) if state.load_id in candidates
                      else state for state in load_states)
shadow_result = await hass.async_add_executor_job(plan, config, replace(inputs, load_states=shadow_states))
return {lp.load_id: lp.active_now for lp in shadow_result.load_plans if lp.load_id in candidates}
```

Nur die F5-Übersteuerung wird geleert, jedes andere Zustandsfeld bleibt.
Der Schatten-Plan wird **nie publiziert** — Sensoren, Attribute und Prognosen
kommen weiter aus dem ehrlichen `result`. Das ist nur zulässig, weil `plan`
eine reine Kernfunktion ohne Coordinator-Nebenwirkungen ist (Dokument 01 §1).

### 7.4 `_latch_hold_ok` — die Gate-Kette

| # | Gate |
|---|---|
| 1 | Power-Warning ist gelatcht |
| 2 | Steuerschalter **und** Power-Entity konfiguriert |
| 3 | kein Floor-Guard aktiv |
| 4 | Inverter-Empfehlung ist an |
| 5 | **Hauptgate:** `shadow_active[id]` — der normale Planer würde die Last jetzt aktivieren |
| 6 | **Überleistungs-Zusatzschutz** (siehe unten) |

Gate 5 zieht implizit *alle* Planer-Gates mit (Floor, Strict-Surplus,
Pass-2-Lost-Export-Deckung, Dwell/Quantum). `min_off` prüft der ON-Pfad des
Aufrufers, nicht diese Methode.

**Gate 6, `LATCH_HOLD_OVERPOWER_FACTOR = 1.5`:** ein Latch kann auch bedeuten,
dass die Steckdose **mehr** zieht als konfiguriert (Fremdverbraucher). Der
Schatten-Plan replant dann mit der zu kleinen gelernten/nominalen Leistung und
bucht die Last womöglich, obwohl ihr realer Zug Netzbezug bräuchte. Also:

```
if raw is not None and raw >= standby_bar and raw > planning_power * 1.5:
    return pv_power_w >= raw
```

Das ist die **einzige** verbliebene Rolle von `pv_power_w` im Hold — nicht mehr
das Hauptgate.

### 7.5 Wie der Latch wieder loskommt

Drei Wege:

1. **Normal**: die Last läuft (per Hold oder Plan) und ihre Leistung ist wieder
   im Band ⇒ `_update_power_warnings` löst den Latch, `_set_power_warning(...,
   False)` verwirft auch die Hold-Markierung.
2. **Reset-Button** (`reset_load_runtime`): das explizite Operator-Signal, dass
   die Ursache behoben ist — löscht den Latch direkt (§9).
3. **Feature aus**: `power_warning_percent = 0` oder Power-Entity entfernt ⇒
   der Latch wird verworfen, damit er nicht unlösbar festhängt.

---

## 8. F-L7 — Power-Warning (der Latch, der alles auslöst)

`_update_power_warnings(result, now)`, ganz am Ende des Zyklus.

**Konfiguration je Last:** `CONF_LOAD_POWER_WARNING_PCT` (Default **0 %** =
aus) und `CONF_LOAD_POWER_WARNING_DWELL_MIN` (Default **15** min).
`pct <= 0` oder keine Power-Entity ⇒ Timer *und* Latch werden gelöscht.

**Aktiv-Begriff** (Präzedenz):

```
floor_guard aktiv                  -> active = False   (unter G4 läuft nichts auf BM-Wunsch)
sonst _load_charging_active[id]    -> realer Ladezustand (Schaltlasten)
sonst plan.active_now              -> Empfehlungslasten
```

Ist die Last nicht aktiv: Dwell-Timer zurücksetzen, **Latch bleibt** — ein
voller Tank ist auch dann voll, wenn das Gerät aus ist (Operator-Wunsch
2026-07-12).

**Auslösung**: `abs(raw - nominal) > pct/100 * nominal` startet den Timer
(`_load_deviation_since`); hält die Abweichung `dwell` Minuten an ⇒ Latch an,
WARNING + Push. Fehlende Lesung friert den aktuellen Zustand ein
(kein Reset). Kurze Abtaupausen setzen den Timer zurück, bevor der Dwell
abläuft.

**Release**: `abs(raw - nominal) <= pct/100 * nominal` **während die Last
aktiv ist** ⇒ Latch aus, INFO + (optional) Push.

**Persistenz**: der Latch-Bool wird gespeichert (ein Options-Save = Reload darf
keine gesetzte Warnung schlucken); der Dwell-Timer **nicht**.

**Push-Ziele**: `CONF_WARNING_NOTIFY_TARGETS` (Liste von `notify`-Servicenamen),
`CONF_WARNING_NOTIFY_ON_RESOLVE` (Default True). Jedes Ziel wird einzeln und
non-blocking gerufen, ein toter Service blockiert die anderen nicht.

### 8.1 F5 — der Rückfluss in die Planung

In `_get_load_states`:

```
saturated_power_w = None
if self._load_power_warning.get(subentry_id, False):
    saturated_power_w = max(0.0, raw if raw is not None else 0.0)
```

`SurplusLoadState.planning_power_w` gibt diesem Feld **höchste Präzedenz** —
der Planer bucht also die gemessenen ~2 W statt Phantom-Volllast-Slots
(24.07.: 5,4 kWh Phantomplan gegen 2 W Realität). Die gelernten und gemessenen
Werte bleiben unangetastet, der Release stellt die normale Planungsleistung im
selben Zyklus wieder her.

Der Latch ist beim Lesen **einen Zyklus alt** (`_update_power_warnings` läuft
am Ende) — dokumentiert und beabsichtigt, wie bei allen
Coordinator-Latches.

---

## 9. Tank-Modell V6 (F-TANK)

Opt-in je Last: `CONF_LOAD_TANK_FULL_RUNTIME_MIN` (Default **0 = aus**) und
eine Power-Entity müssen gesetzt sein (`_tank_enabled`). Der konfigurierte Wert
ist nur der **Startschätzwert**.

**Die Tank-Events sind die Latch-Flanken des Power-Warnings** — nicht der
Tankfüllstand selbst (den misst niemand):

| Flanke | Was passiert (`_set_power_warning`) |
|---|---|
| Latch **an** = Tank voll | `_load_tank_full_min[id] = load_runtime_minutes(id)` — die Laufzeit, die das Füllen gekostet hat. Der Zähler ist am Füllpunkt eingefroren (die ~2 W liegen unter `LOAD_RUNTIME_MIN_W = 5.0`), der Wert ist also die echte Volltank-Laufzeit. Noch **kein** Lernsample. |
| Latch **aus** = Tank geleert | Das gemerkte `full_min` wird als Sample committet (`_tank_record_sample`), `load_runtime_seconds = 0` (frischer Tankzyklus), Push-Gate re-armiert |

**Lernen**: `_tank_learned_full_min` = Median der letzten
**`TANK_LEARN_SAMPLES = 5`** Samples, solange keines existiert der konfigurierte
Startwert. Nicht-positive Samples werden ignoriert (ein Spurious-Latch direkt
nach einem Reset darf den Median nicht vergiften).

**Vorhersage**: `_tank_remaining_min` = `learned - Laufzeit seit letzter
Leerung`, geklemmt ≥ 0.

**Benachrichtigung** (`_update_tank_forecast`): genau **einmal pro Tankzyklus**,
wenn die verbleibende **Lauf**zeit unter **`TANK_WARN_LEAD_MIN = 60.0`**
Minuten fällt **und** die Last im aktuellen Plan noch laufen soll. Laufminuten,
nicht Wandzeit — ein untätiges Gerät warnt nie. Eine bereits als voll gelatchte
Last wird übersprungen (dafür ist F-L7 zuständig).

**Der eiserne Operator-Grundsatz (2026-07-24):** die Tankprognose fließt
**NIEMALS** in den Planer. Ein Gerät mit Verbrauchsbehälter wird nie
vorsorglich gedrosselt oder abgeschaltet, weil ein Zustand *vermutet* wird. Der
BM reagiert auf gemessene Realität: das Gerät stoppt selbst, wenn der Tank
wirklich voll ist, und **das** wird über den Leistungseinbruch → F-L7-Latch →
`saturated_power_w` erkannt. Der Kommentar steht wörtlich in
`_get_load_states`.

**Reset-Button** (`button.SurplusLoadRuntimeResetButton` →
`reset_load_runtime`) bedeutet vier Dinge auf einmal:

1. Laufzeitzähler auf 0 (laufender Lauf startet seinen Cursor neu);
2. **„Tank geleert"** — ein anstehendes Volltank-Capture wird **verworfen**
   (ein manueller Reset ist kein beobachteter Tankzyklus und darf kein
   verfrühtes Sample lernen), das Push-Gate wird re-armiert;
3. **F-L7-Latch-Clear** (F10): das explizite Operator-Signal, dass die
   Deadlock-Ursache behoben ist. Selbstkorrigierend — ist der Tank wirklich noch
   voll, bricht die Leistung beim nächsten Lauf erneut ein und der Latch kehrt
   nach dem F-L7-Dwell zurück;
4. Persistieren + Listener aktualisieren.

Diagnostik am Runtime-Sensor: `tank_remaining_min`, `tank_learned_full_min`,
`tank_samples`.

---

## 10. Median-Power-Learning (F-ROBUST-POWER)

Anlass: am 2026-07-18 wurde beim 06:21-Inverter-Cutoff der
Kompressor-Wiederanlauf-Transient des Entfeuchters (1711 W, vom Messstecker
~60 s gehalten) in die alte EMA gemischt (0,3·1711 + 0,7·433 ≈ 818) und von der
Run-Max-Regel als **818 W für ein 426-W-Gerät** eingefroren.

**Sample-Annahme** (`_get_load_states`): eine Lesung wird nur gepuffert, wenn
sie über der **Standby-Bar** liegt **und** die Last auf BM-Wunsch läuft:

- `_load_standby_bar(data) = max(10.0, STANDBY_FRACTION * nominal)` mit
  `STANDBY_FRACTION = 0.25` — die eine Schwelle, die auch G2 und die
  F4-Evidenzprüfung benutzen. Anlass: ein 400-W-Entfeuchter im Leerlauf bei
  ~20 W überschritt die alte flache 10-W-Bar und wurde mit 22 W eingeplant
  (2026-07-05).
- `_bm_load_active(id)`: bei Schaltlasten der reale Ladezustand; bei
  Empfehlungslasten `_load_plan_active` **und** `_load_learn_ok` — Letzteres
  wird von `_update_plan_active` an jeder OFF→ON-Flanke geschnappschusst
  („war die Steckdose vorher untätig?"). Ohne diesen Schnappschuss würde eine
  vorbestehende manuelle/fremde Last gelernt, der nächste Plan kippte auf
  inaktiv, der Wert würde gelöscht — eine Periode-2-Oszillation.

**Der Schätzer** (`core/power_learning.robust_power_estimate`), alle
Konstanten in `core/power_learning.py`:

| Konstante | Wert | Rolle |
|---|---|---|
| `WINDOW_S` | 1800 s (30 min) | Fenster des langsamen Medians |
| `FAST_WINDOW_S` | 600 s (10 min) | Fast-Adopt-Unterfenster |
| `STABLE_BAND` | 0,20 | P10/P90 innerhalb ±20 % des Sub-Medians = „stabil" |
| `FAST_DEVIATION` | 0,20 | Sub-Median muss > 20 % vom langsamen Median abweichen |
| `WARMUP_COVERAGE_S` | 300 s | **unter 5 min akzeptierter Abdeckung: gar keine Schätzung** |
| `GAP_CAP_S` | 300 s | ein Sample deckt höchstens 5 min Stille |
| `DOMINANCE_MAX` | 0,80 | kein Einzelsample darf > 80 % des Fenstergewichts tragen |
| `MAX_SAMPLES` | 720 | Deque-Grenze (1 h bei 5-s-Kadenz) |

Samples sind **zeitgewichtet**: Sample i deckt bis zum nächsten (gekappt bei
`GAP_CAP_S`), auf das Fenster geklemmt. Die Schätzung ist der gewichtete
Median — ein Pegel, der weniger als die halbe Fensterzeit belegt (Spike, kurze
Abtaupause), kann ihn nicht bewegen; ein anhaltender Pegel wird nach
~`WINDOW_S/2` zur Mehrheit.

Die **Dominanz-Bar** ist der eigentliche Anti-Transient-Schutz: weil
`GAP_CAP_S == WARMUP_COVERAGE_S`, erreicht ein **einzelnes** Sample gefolgt von
Stille exakt die Abdeckungsschwelle — ohne die Bar könnte ein einsamer
Anlauf-Transient zum Schätzwert werden. Sie gilt auch im Fast-Fenster (sonst
wäre der Stabilitätstest bei einem dominanten Sample trivial erfüllt).

**Fast-Adopt** setzt die Operator-Regel „10 Minuten wiederhergestellte
Normallast" um: ist das 10-min-Unterfenster in sich stabil und weicht sein
Median > 20 % vom langsamen Median ab, gewinnt er sofort.

**Bewusst kein Nominal-Clamp** — Pegel oberhalb der konfigurierten Leistung
können legitim sein (vom Operator erhöhte Laderaten, batterieseitige
Drosselung). Stattdessen beobachtet `_warn_estimate_overrun`: eine WARNING
einmal pro Lauf, wenn der Schätzer **> 3× nominal** liegt („Sensor eingefroren?
Nominalwert falsch?").

**Write-Through**: sobald ein Schätzwert existiert, wird
`_load_learned_power_w[id]` aktualisiert — aber nur bei ≥ 1 W Bewegung
persistiert (Sub-Watt-Jitter hätte sonst dutzende `.storage`-Schreibvorgänge
pro Laufstunde ausgelöst). Der Live-Puffer wird **nicht** persistiert
(ein alter Puffer wäre nach dem Neustart eine falsche „frische Messung"), der
gelernte Wert schon.

**Mid-Run-Lücke**: fehlt die Rückmeldung, während die Last noch auf BM-Wunsch
läuft, wird die Schätzung aus dem Puffer weiter bedient — dank `GAP_CAP_S`
verfällt ein langer Ausfall in den Warm-up-Zustand (`None`) statt zu einem
stale Wert. Endet der Lauf (oder wird das Gerät außerhalb des Plans benutzt),
wird der Puffer **verworfen**; der gelernte Write-Through-Wert trägt weiter.

### Manuelle Neubestimmung (v0.27.0; Actor-Wartezeit v0.27.1)

Für energielimitierte, direkt steuerbare Nicht-Kaskadenlasten mit
Leistungssensor startet der per-Last-Button einen höchstens vierminütigen,
bewusst netzstromfähigen Messlauf außerhalb des Plans. Die Messphase startet
erst nach bestätigtem Eingangsschalter und Lade-Gate; seit v0.27.1 wartet der
Coordinator dafür bis zu 30 s auf verzögert publizierte Shelly-Zustände statt
unmittelbar nach Rückkehr des Servicehandlers fälschlich abzubrechen. Danach
startet die Messphase beim Sprung von mindestens 20 % der konfigurierten
Leistung gegenüber der Idle-Baseline, spätestens nach 60 s. Nur neue
Sensorpublikationen im Abstand von mindestens 5 s gehen in den Buffer; vier
Samples oder 90 s Messzeit mit mindestens einem Sample genügen. Der Median wird
atomar gelernt. Bis dahin ist der normale Learner für diese Last gesperrt,
sodass Abbruch/Fehler den alten Wert nicht verändern. Release → sofortiger
Replan; Reload-Recovery stoppt einen persistiert unterbrochenen Lauf.
Vollvertrag: `docs/F-POWER-CALIBRATION.md`.

---

## 11. Grid-Support 24 V / 48 V (Kurzfassung)

Details: `docs/DC_TOPOLOGY.md`. Die Planungsseite steht in Dokument 02 §9.

**Modi je Pfad** (`dc24`, `dc48`): `auto` oder `manual`. Ein PSU, das
**ohne** BM-Kommando einschaltet, versetzt seinen Pfad in **manual**
(F-N2): die Automatik lässt dann die Finger davon — inklusive des
24-V-Make-before-break — bis es extern wieder ausgeschaltet wird.
`_update_support_modes` unterscheidet dafür pro Schlüssel und pro Richtung
ein spät bestätigendes Gerät (unser letztes Kommando war „on" und ist frisch,
oder die Bestätigung steht noch aus) von einem echten Operator-Eingriff. Modus
**und** eigener Schaltzustand werden persistiert — nach einem Neustart ist
„an, aber nicht von uns" nur so von „an, weil wir" unterscheidbar.
`_sync_support_state_from_entities` adoptiert im Leerlauf reale Zustände, aber
**nur** ON→OFF; ein OFF→ON zu beurteilen ist ausschließlich Sache von
`_update_support_modes`.

**Schwellen** (absolute SOC %, unabhängig vom Planungs-Buffer, Defaults):

```
support_dc48_activate_soc 5.5  <  support_dc48_recovery_soc 10
                               <= support_dc24_activate_soc 10  <  support_dc24_recovery_soc 11
```

Jede Stufe ist eine Hystereseschleife (an unterhalb *activate*, aus ab
*recovery*); ein breiterer Abstand latcht ein PSU länger an, damit ein an der
Schwelle parkender SOC nicht taktet.

**Ausführung**: `_apply_support_switching` vergleicht die Slot-0-Wünsche des
Plans mit `_support_state`, pinnt Manual-Pfade auf ihren Ist-Zustand,
respektiert `min_switch_interval_s` und startet
`_execute_support_switching` als Hintergrund-Task unter `_switch_lock`
(damit ein abgebrochener Debounce nie eine Make-before-break-Sequenz halbiert).

**`_sequence_dc24` (Make-before-break)**: die 24-V-Schiene wird entweder vom
DC/DC (aus der Batterie) oder vom Netz-PSU gespeist — **nie von keinem**.
Aktivierung: PSU an → `support_switch_delay_s` (Default 3 s) → DC/DC aus.
Deaktivierung umgekehrt. Bestätigt die neue Quelle nicht „on", wird die
Umschaltung **abgebrochen** und die alte Quelle bleibt an. Ohne konfigurierten
DC/DC-Schalter ist es ein einfacher PSU-Toggle (Parallelspeisung).

**`_ensure_dc24_rail_supplied`**: leveltriggert — lesen PSU **und** DC/DC hart
„off", wird der DC/DC wieder eingeschaltet (jeden Zyklus erneut versucht, bis
es hält).

**R2-Spannungsregler** (`_run_dc48_controller`), nur im Manual-Modus **und**
nur mit konfiguriertem Batteriespannungssensor **und** 48-V-Schalter:

| Bedingung | Aktion | Konstante |
|---|---|---|
| `V <= on_voltage` für 60 s | PSU **an** | `DC48_CTRL_DWELL_ON_S = 60` |
| `V >= off_voltage` für 300 s | PSU **aus** | `DC48_CTRL_DWELL_OFF_S = 300` |
| dazwischen | halten, beide Timer zurücksetzen | – |
| Lesung fehlt/unplausibel (außerhalb 40–60 V) | Timer **einfrieren**; nach 10 min Fail-Safe **an** | `DC48_CTRL_FAILSAFE_MIN = 10`, `DC48_CTRL_VOLTAGE_MIN/MAX = 40.0/60.0` |
| `off_voltage <= on_voltage` | Regelung deaktiviert + WARNING (kollabiertes Band) | – |

Defaults: `psu48_on_voltage_v` 49,56 V, `psu48_off_voltage_v` 49,8 V.
**`CONF_PSU48_CTRL_LOG_ONLY` ist standardmäßig `True`** — der Regler rechnet
und loggt („would switch on/off"), aktuiert aber erst, wenn der Betreiber ihn
nach einem Shakedown scharf schaltet. Er verlässt den Manual-Modus **nie**;
`_dc48_ctrl_caused_off` wird persistiert, damit ein Reload ein
reglerverursachtes Aus nicht als Operator-Aus fehldeutet.

Die Batteriespannung ist bewusst **nicht** in `_tracked_entities()` — ein
analoger Wert würde permanent Replans auslösen; der Regler läuft auf dem
5-min-Poll (die Hardware gattert sich oberhalb ihrer Ausgangsspannung ohnehin
selbst).

Diagnose: `data["support_dc48_controller"]` mit `active`, `mode`
(`off`/`log_only`/`regulating`), `decision` (`on`/`off`/`hold`), `reason`
(`inactive`, `on_dwell`, `off_dwell`, `hysteresis_band`, `below_on_voltage`,
`above_off_voltage`, `waiting_failsafe`, `failsafe_no_reading`,
`invalid_config_off_le_on`) und `voltage`.

---

## 11A. F-FEEDIN — der Feed-in-Setpoint-Executor (v0.23.0)

Spec: `docs/F-FEEDIN.md` (R7–R12); Planerseite: Dokument 02 §5A. Der Executor
treibt den AC-Setpoint des externen Reglers so, dass der prognostiziert
unvermeidliche Mittags-Export schon am Morgen durchgereicht wird — die
Batterie bleibt dabei ≈ 0 W (nie aktiv entladen, R2).

**Zweiphasig** wie die Lasten: `_apply_feedin` (Entscheidung) +
`_execute_feedin` (Aktuation unter `self._switch_lock` mit In-Flight-Recheck —
Manuell-Modus oder Fail-safe nach dem Einreihen ⇒ Schreibvorgang verworfen).
Geschrieben wird per Service `input_number.set_value`; Vorzeichenkonvention
**negativ = Export** (`FEEDIN_SETPOINT_EXPORT_SIGN = -1.0`, 500 W Feed-in →
−500), die interne Buchhaltung rechnet durchgängig positiv. Pro bestätigtem
Schreibvorgang eine INFO-Zeile `Feed-in setpoint -> N W (reason)` (V9a-Muster).

**`desired`-Basis**: Planwert des aktuellen Slots, wenn Config-Toggle ∧
Laufzeit-Switch ∧ Auto-Modus ∧ SOC > `feedin_min_soc_percent`, sonst 0. Der
G4-Floor-Guard und der D-A8-Stale-Data-Shed erzwingen 0 dwell-exempt (ein
unbeaufsichtigter Export darf nicht weiterlaufen — dasselbe Argument wie das
Last-Force-off).

**Ereignisgesteuerter Trim (R10)** — die Batterie-Leistungs-Entity ist
getrackt, jede Aktualisierung feuert den Trim-Pfad (Victron-Konvention:
positiv = ladend). Er greift **nur, solange der Plan-Slot > 0 W bucht** — ein
Plan-0 wird direkt auf 0 re-anchort, nie getrimmt. Totband
`FEEDIN_TRIM_DEADBAND_W = 50` W um null:

| Richtung | Bedingung | Aktion |
|---|---|---|
| Abwärts | `battery_power < −50 W` (Batterie entlädt — nicht erfasster Verbraucher) | Setpoint sofort um den Entladebetrag senken, Boden 0 — **ungedrosselt** (internes `input_number`, kein physischer Aktor) |
| Aufwärts | `battery_power > +50 W` (Überschuss lädt statt zu exportieren) | Setpoint um den Ladebetrag erhöhen — **gedrosselt** auf eine Erhöhung pro `FEEDIN_UPWARD_MIN_INTERVAL_S = 60` s und gedeckelt auf `min(max_w, verbleibende Tagesmenge / Stunden bis Mitternacht)` |

Trim-Schreibvorgänge tragen **kein** Deadband; 0↔>0-Wechsel schreiben immer
(aufwärts nur, sobald die R16-Stabilitätsbremse durchlässt). Der 5-min-Zyklus
zieht den Sollwert auf den Plan-Slot-Wert zurück, sobald der Drift >
`FEEDIN_REANCHOR_DEADBAND_W = 25` W liegt — die einzige Stelle mit
Deadband (R11). Die verbleibende Tagesmenge kommt aus `feedin_by_day_wh` minus
der in `_feedin_tick` in-memory integrierten gelieferten Energie (Reset um
Mitternacht).

**Stabilitätsbremse (R16, v0.25.7)**: Jede **Aufwärts**-Schreibung
(Plan-Slot, 0→>0, Aufwärts-Trim/Re-Anchor) wird gehalten, solange der
Plan-Slot-0-Wert über das gleitende Fenster `FEEDIN_STABLE_WINDOW_S = 180` s
um mehr als `FEEDIN_STABLE_SPREAD_W = 50` W streut; **abwärts wird nie
gebremst** (inkl. →0 und alle Fail-safes). Anlass (live 2026-08-08): das
Flackern des Pass-3-Blocks (Fix: F-PREDRAIN-BLOCK R9) schwang das Setpoint
0↔~1 kW im 1–2-min-Takt. Engage/Release loggen je einmal (change-gated);
in-memory, Neustart seedet neu.

**Manuell-Modus (`_update_feedin_mode`, R9, F-N2-Muster)**: liest die Entity
einen Wert ≠ dem zuletzt von uns geschriebenen — außerhalb der
`FEEDIN_MANUAL_GRACE_S = 360` s Grace nach eigenem Schreiben — geht das
Feature bis zum nächsten Mitternacht in den Manuell-Modus
(`_feedin_manual_until`, Store-persistiert): kein einziger Schreibzugriff
mehr, der Operator besitzt den Setpoint. Beim Start wird der Istwert
adoptiert; weicht er vom persistierten Eigenwert ab (offline geändert), gilt
sofort Manuell-Modus. Nach außen: `sensor.battery_manager_feedin_mode`
(auto/manual) und `data["feedin_mode"]`. Deaktivieren per Switch oder Config
schreibt einmalig 0 (nur im Auto-Modus).

---

## 12. Weitere Ausführungsdetails

### 12.1 Startup-SOC-Karenz (V8)

`STARTUP_SOC_GRACE_S = 120`. Innerhalb dieser Zeitspanne nach dem ersten
Refresh und **bevor** je ein Zyklus erfolgreich war, eskaliert eine noch nicht
verfügbare SOC-Quelle (Victron-Sensor bootet nach einem HA-Neustart) **nicht**
zu ERROR + `UpdateFailed`, sondern wartet still (DEBUG) und retryt im
30-s-Startintervall. `_first_success_done` latcht dauerhaft — ein echter
SOC-Ausfall nach dem ersten Erfolg fällt nie in die Karenz zurück. Nur SOC wird
begnadigt; eine fehlende Prognose nimmt den normalen Pfad.

### 12.2 Input-Off-Policy

| Wert | Verhalten beim Stopp |
|---|---|
| `auto` (Default) | Stecker nur aus, wenn **wir** ihn eingeschaltet haben (`_load_plug_owned`) |
| `always_off` | Stecker immer aus |
| `keep_on` | Stecker bleibt an (nur sinnvoll **mit** Charge-Enable; ohne wird eine WARNING geloggt, weil dann nichts das Laden stoppen kann) |

Ohne Charge-Enable-Entity ist das Ausschalten des Steckers die **einzige**
Möglichkeit, das Laden zu beenden — dann wird `turn_plug_off = True`
erzwungen (außer bei `keep_on`, das dafür oben abgefangen wird).

### 12.3 Laufzeitzähler

`_load_is_running` benutzt die Power-Entity, wenn vorhanden
(`> LOAD_RUNTIME_MIN_W = 5.0` W — so zählen auch manuelle Läufe), sonst den
BM-eigenen Zustand: bei Schaltlasten `charging_active`, bei Empfehlungslasten
die **deadline-gekappte** publizierte `active` (FIX-12; der rohe Plan-Flag
würde über einen Nacht-Pre-Drain-Block hinweg überzählen). Unter G4 zählt der
Plan-Fallback nicht.

`_update_load_runtime` addiert je Tick die verstrichene Zeit, gekappt bei
`LOAD_RUNTIME_TICK_MAX_S = 900.0` s (über dem 300-s-Intervall, damit ein
normaler Zyklus nie beschnitten wird, aber ein hängender Loop oder Uhrensprung
nichts aufbläht). Der Tick-Cursor `_load_run_since` wird **nicht** persistiert
— sonst würde die gesamte Ausfallzeit gutgeschrieben.

### 12.4 Per-Last-Steuerungsswitch

`switch.SurplusLoadControlSwitch` → `set_load_enabled(load_id, False)` hält die
Last **unavailable**: der Planer lässt sie fallen, der Executor schaltet sie im
nächsten Zyklus aus — **ohne** die Steuerschalter-Konfiguration anzufassen.
Persistiert.

### 12.5 Nebenläufigkeit

Ein einziger `asyncio.Lock` (`_switch_lock`) serialisiert **alle** Aktuierung
(Lasten, Support-Sequenzen, R2-Regler, DC/DC-Restore, Feed-in-Setpoint). Fünf
Hintergrund-Tasks werden verfolgt: `_switch_task`, `_load_switch_task`,
`_dc48_ctrl_task`, `_dcdc_restore_task`, `_feedin_task` (v0.23.0);
`async_cancel_actuation_tasks`
canceled sie beim Unload **vor** dem Persistenz-Flush (sonst könnte ein
detachter Task nach dem Flush noch Zustand mutieren) und räumt vorher alle
Force-OFF-Timer ab. Ein mitten in einer Make-before-break-Sequenz abgebrochener
Task ist sicher: schlimmstenfalls sind beide Quellen an, nie keine.

---

## 13. Diagnose-Kurzrezepte

| Symptom | Erster Griff |
|---|---|
| Empfehlung an, Gerät läuft nicht | Dwell (`_last_load_switch` + `min_off`), Charge-Enable bestätigt?, Log nach `-> ON (` durchsuchen |
| Gerät läuft, Empfehlung aus | Latch-Hold (`reason=latch hold`), min_runtime, oder eine Fremdautomatisierung |
| Alles aus, obwohl PV da | Floor-Guard: `data["floor_guard_active"]`, Log „Floor guard TRIPPED (…)" — Grund `soc` oder `rec` |
| Last verschwindet aus dem Plan | `load_plans[id]["soc_stale"]` (G2/F4), BM-Switch aus, Verfügbarkeits-Entity |
| Planer bucht plötzlich ~nichts für eine Last | F5: `power_warning` gelatcht ⇒ `saturated_power_w`; prüfen ob der Tank voll ist |
| 15-min-Pausen bei viel PV | F8-Ping-Pong-Budget erschöpft, oder der Stopp war G4/G1 (nicht flicker-eligible) |
| Gelernte Leistung wirkt falsch | Runtime-Sensor / `learned_power_w`; WARNING „exceeds 3x the configured" suchen; Standby-Bar prüfen |
| 24-V-Schiene tot | `_ensure_dc24_rail_supplied`-INFO, Manual-Modus des 24-V-Pfads, `assumed_state`-WARNING |

---

**Verifikation: geprüft gegen Commit `a172d48` (v0.16.2).**

Wichtigste Code-Anker dieses Dokuments:

1. `coordinator.py::_apply_load_switching` + `_execute_load_switching` — die
   Entscheidungsreihenfolge (G4 > Hold > Deadline/Seamless > Plan), die
   Split-Dwells und das V9a-INFO-Protokoll.
2. `coordinator.py::_update_floor_guard` — G4 mit unbedingtem SOC-Zweig,
   PV-gegatetem Rec-off-Zweig, Release-Band und 15-min-WARN-Ratenlimit.
3. `coordinator.py::_latch_hold_ok` + `_latch_shadow_active` +
   `_latched_hold_candidates` — F10/F11 inkl.
   `LATCH_HOLD_OVERPOWER_FACTOR = 1.5`.
4. `coordinator.py::_update_power_warnings` / `_set_power_warning` (F-L7 +
   Tank-Flanken) und `_get_load_states` (F5 `saturated_power_w`, G2, F4).
5. `core/power_learning.py::robust_power_estimate` — Warm-up, Dominanz-Bar,
   Fast-Adopt; Standby-Bar in `coordinator._load_standby_bar`
   (`STANDBY_FRACTION = 0.25`).
