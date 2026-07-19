# F-STRICT-SURPLUS — loads never buy import, never run planned-grid-fed, and bets settle at the true refill

Status: implemented (v0.15.0).
Operator decision 2026-07-19 (binding, verbatim intent): *"Das oberste Ziel
ist es doch, den SOC immer maximal hoch zu halten, ohne dass eine Einspeisung
passiert. Lasten haben das Ziel überschüssigen Strom sinnvoll zu verbrauchen.
Aber das soll nur so spät wie möglich passieren, so dass eine Einspeisung
sicher verhindert wird."*

The objective hierarchy is LEXICOGRAPHIC, not scalarized:

1. surplus loads must never cause grid import (hard, restores REQUIREMENTS.md
   Z2 in its original form);
2. subject to 1, keep the house-battery SOC as high as possible;
3. subject to 1–2, surplus loads absorb energy that would otherwise be
   exported — as late as possible, just early enough that export is safely
   prevented. Lost surplus is the acceptable price of 1–2, never the other
   way round.

Operator clarification (2026-07-19, second statement): pre-conditioning
("Vorkonditionierung") — deliberately discharging the battery AHEAD of a
coming surplus so that no export happens — remains an explicit goal, NOT a
casualty of this feature. The refined rules: (a) the planned trajectory must
still reach soc_max (loads must never pre-empt the fill on the way up); (b)
dropping below the 20 % cutoff is to be prevented whenever possible; (c)
once today's max is reached with export prevented, today's load control owes
nothing to tomorrow — cross-day financing may shape post-peak/night
pre-conditioning, never the pre-max morning. R1–R3 bound HOW a bet may run
(import-free, above the cutoff, stress-safe to the true refill) and **R5**
guarantees the OUTCOME the operator cares about: the plan still reaches
soc_max on every day the no-loads base reaches it, so a bet may pre-condition
(pre-drain to make room) but may never rob the fill — the 2026-07-19 card's
77 % peak is exactly the case R5 refuses. Legitimate pre-conditioning is
untouched: night pre-drain before a clip still books (7 kWh night-rescue
regression, pre-dawn quanta at min SOC 25.6), because the target clip day
still reaches max. The same guarantee extends to the controllable
powerstations (energy-limited priority loads): they reach their target SOC
from surplus, in priority order, via the F-GATE-TOPUP final quantum — bounded
only by objective 1 (never grid-charged), exactly as the house battery
"reaches max only when the sun permits".

## 1. Incident (2026-07-19 card) and verified mechanism

Sunday card: T\* 20, loads 2.7/4.6 kWh (today/tomorrow), import 0.1/0.9,
lost 0.0/2.2, SOC never reaching soc_max today, forecast dipping to 17–18 %
(below the 20 % cutoff) Monday pre-dawn, Entfeuchter booked 04:00–08:00
Monday/Tuesday. Live plan diagnostics (`import_trade_used_wh = 1028.9`,
why-strings) plus a local repro with gate ablations
(scratchpad/repro_20260719_card.py, ablate_20260719.py) decomposed it:

- ~1.6 kWh of Sunday's runs were legitimate pass-1 absorption — WITHOUT the
  loads Sunday itself would have clipped (~1.7 kWh in the no-loads base). The
  card never shows this counterfactual, which is why the plan looked absurd.
- The Sunday **morning** runs were pass-2 `in-window insurance (beta/p90)`
  bets and pass-2 cross-day bets. They are what kept the SOC from ever
  touching soc_max (objective 2 violated).
- The pass-2 bets were paid for with **real planned grid import**
  (~0.45–1.0 kWh/day, concentrated in the pre-dawn cutoff hours) via the Z2'
  proportional trade budget `ratio × rescued_export + 1 Wh` (live ratio 0.10).
  F-PREDRAIN's sanctioning record contemplated ~10 Wh modelling artifacts;
  the mechanism as shipped mints hundreds of Wh whenever multi-kWh clipping
  is forecast (objective 1 violated). Ablation: at ratio 0 every pass-2 bet
  vanished and import returned to the no-loads base to the watt.
- The planner booked load slots its own simulation serves from the grid
  (inverter off at/below T\*): the Monday/Tuesday 04:00–08:00 blocks. G4
  ("Zusatzlasten nie netzgespeist", operator 2026-07-18) existed only in the
  executor; the planner deliberately planned what the executor is designed to
  refuse — phantom rescue energy corrupting the card's kWh figures.
- Z4's bet window `[i, recovery]` ended at the SAME-DAY strong-PV window end
  (`_recovery_index` premise: "the battery refills by this window's end") —
  false on a day that never refills. Daytime bets therefore escaped the
  stress test of the very overnight dip they deepen, while evening slots
  carried it: the veto pattern INVERTED the operator's lateness order (runs
  landed 08:00–13:00 instead of as late as possible).

## 2. Rules

**R1 — hard no-import gate (Z2'').** A booking is accepted only if
`trial.total_import_wh − base_import ≤ IMPORT_ARTIFACT_SLACK_WH` (absolute,
50 Wh over the whole horizon, a named constant like GATE_TOPUP_MIN_WH — not a
config key). This keeps F-PREDRAIN L1 satisfied (a ~10 Wh charger-standby
artifact can never veto a sensible pre-drain; 50 Wh covers several such
artifacts across multiple bookings) while making it impossible to finance
real import with rescued export. Supersedes F-PREDRAIN §3.2 F2 (Z2'
proportional trade) and §3.7, and F-GATE-PARITY §7 risk acceptance 3.
`import_trade_ratio` is retired: ignored by the planner, removed from the
options UI; stored config entries keep parsing. `import_trade_used_wh` stays
as a diagnostic and is now bounded by the slack.

**R2 — planner floor-guard parity (planner-G4).** No booking may cover a
slot that, in the TRIAL trajectory, is GRID-FED or touches the cutoff:
- **grid-fed** iff `inverter_on == False` AND `pv_wh < ac_wh + extra_ac_wh`
  (the inverter is off AND PV cannot cover the slot's AC load, so the deficit
  imports). *Correction to the first draft, gates review 2026-07-19:*
  `inverter_on == False` ALONE is NOT grid-fed — in the full-battery hoard
  regime `T* = soc_max` makes `inverter_on` False on every slot while PV
  serves the load with zero import; vetoing there disabled the whole allocator
  and re-exported multi-kWh. Only an inverter-off slot whose PV cannot cover
  the load is grid-fed.
- **cutoff** iff EITHER slot endpoint `≤ inverter_min_soc_percent`
  (`soc_start` OR `soc_end`): a slot entered below the cutoff is one the
  executor's real-time G4 (SOC ≤ 20 → no additional loads) would refuse, and
  running a load there slows the battery's recovery above 20 % (objective 2).

The check re-validates the candidate's covered slots AND ALL previously
booked slots on every trial, so a later (earlier-in-time) acceptance can never
silently degrade an accepted run into a grid-fed or cutoff-riding one (the
latest-first re-drain ratchet). Supersedes LOAD_CONTROL.md §11's scoping
sentence ("the planner is deliberately unchanged"); G4 remains as the runtime
backstop against forecast error.

**R3 — bets settle at the true refill.** The Z4 stress window for a pass-2
candidate at slot `i` ends at the first slot at/after `i` where the TRIAL
trajectory reaches `soc_max − 0.1` (the battery provably refilled /
clipping), horizon end if never (`_refill_index`, replacing
`_recovery_index`'s same-day-window-end premise for gating; `pv_windows`
stays for `in_window`/c2 and the pv_window_ends diagnostic). Consequences:
a daytime bet whose drain persists overnight is now stressed across that
night (the same overnight window an evening bet already faced), at the
candidate slot's own ramped floor (`stress_floor_by_slot[i]` stays anchored
at `i`, so a midday candidate near the stressed-PV crossover carries a
smaller ramped buffer than an evening one — a residual, buffer-bounded
lateness effect, not a full inversion). The latest-first walk then places
surviving bets at the latest feasible slot (operator objective 3), instead
of daytime slots winning by
stress-window escape. The `stressed_min_soc` diagnostic uses the same
function, so the sensor keeps reporting what the gate protected.

**R4 — counterfactual transparency.** `PlanResult.prevented_export_by_day_wh`
= max(0, base-day export − alloc-day export), BOTH taken PRE support-escalation
(base = no loads; alloc = the allocation trajectory before support_escalation,
so a winter support PSU cannot deflate it). The coordinator surfaces it as
`prevented_export_kwh` in the per-day `daily` breakdown and the card stats
line: the export the plan's load runs prevent that day. This answers "why is a
load running although SOC never reaches max?" directly on the dashboard —
2026-07-19 would have shown `prevented_export ≈ 1.6` for Sunday.

**R5 — the plan still reaches soc_max (operator clarification 2026-07-19).**
A booking is accepted only if the trial trajectory still reaches
`soc_max − 0.1` on EVERY calendar day the no-loads base reaches it
(`preserves_daily_max`, checked in both passes alongside R1/R2). This makes
"keep SOC as high as possible" (objective 2) a hard planner invariant, not an
emergent property of the stress gate: pre-conditioning (a pass-2 pre-drain to
make room) is welcome and untouched, but a bet that stops the battery filling
to max on a day it otherwise would — the card's 77 % peak — is refused. A
pre-drain for a FUTURE clip is unaffected: it lowers a non-max day, and its
target clip day still fills, so that day stays in `base_max_days` and passes.
Pass-1 clip absorption keeps the battery AT max (it eats only the overflow),
so it always passes. The energy-limited priority loads reach their own target
SOC as a consequence — the house battery fills to max first (R5), then the
priority powerstation charges from the remaining export to its target (via the
F-GATE-TOPUP final quantum), then the dehumidifier; all three are surplus-only
(objective 1), so a genuinely surplus-poor day charges them partially, never
from the grid.

**R6 — no cross-day DAYTIME pre-drain (operator decision 2026-07-19, v0.15.1).**
A pass-2 bet at a DAYTIME slot (`in_window(i)` — inside its day's strong-PV
window) is rejected if its refill (`_refill_index`) lands on a LATER calendar
day (`_crossday_daytime_bet`). A daytime load belongs in its own day's surplus,
not a day early: the live 2026-07-19 plan booked a single Sunday-14:00 run
"covered by otherwise-lost export (215 Wh)" — a battery pre-drain in Sunday's
own PV window to absorb MONDAY's clip, because Monday's absorption window was
power-saturated (dehumidifier 8 h + Fossibot, Monday still lost 1.09 kWh). It
is import-free and floor-safe and genuinely reduces export by ~175 Wh, but it
is the marginal "early bet on tomorrow's forecast" the operator rejected —
running while the plan already sits at the stress cutoff (`stressed_min = 20`).
NIGHT / pre-dawn slots (not in-window) keep the F-NIGHT-RESCUE cross-day
carve-out (pre-draining overnight immediately before a clip day), and a
same-day refill is always fine, so R6 removes only the day-early daytime bet.
The behaviour is rare (it needs an extreme same-day-saturated clip) — goldens
and the F-NIGHT-RESCUE regressions are unchanged. Price: a little more lost
surplus on such saturated days, the operator's chosen trade-off.

## 3. What deliberately does NOT change

- Pass-1 direct-surplus absorption (F-RESCUE-EXPORT regime split): runs as
  soon as export occurs. R1/R2 apply to it too, but its gates are otherwise
  untouched — suppressing pass 1 would recreate real export.
- The threshold search / merge bound (T\* = 20 before a stress-confirmed
  clip) and the sub-cutoff DC-load sag it implies: that drain is the
  sanctioned battery-usage policy (D-A1 "Nutzen") and house physics, not a
  load decision. The base import it causes stays out of the load gates by
  construction (base-anchored comparison).
- c1's physical round-trip factor `rt` (F-NIGHT-RESCUE R2) and the c2
  insurance gate: with R1–R3 bounding them, honest bets (genuinely
  power-limited surplus windows, floors held under stress, zero import)
  still book — night pre-drain before a clipping day keeps working, now
  bounded at the ramped floor instead of the trade budget.
- Executor guards G1–G4, F-ROBUST-POWER, F-SEAMLESS-RUNS, priority order
  (F-GATE-PARITY GP-R3), the energy-limited daylight rule.

## 4. Expected live behaviour after deploy

- No pre-dawn Entfeuchter blocks below/at the cutoff; no planned import from
  load slots. `import_trade_used_wh ≤ 50`.
- On a 2026-07-19-like day: morning insurance runs disappear, SOC rides to
  ~soc_max around midday, pass-1 absorbs the actual clip from there, evening
  SOC stays ~2 kWh higher, no sub-cutoff excursion beyond the base's own
  DC sag, Monday import ≈ base (~0.5 kWh instead of ~1.0).
- Lost surplus rises on clip-eve days (the repro scenario: +1.4 kWh). That
  is the operator's explicitly chosen price. Filling the balcony-forecast
  p10/p90 bands remains the lever to win some of it back safely (evidence-
  based c2 instead of scalar beta).
