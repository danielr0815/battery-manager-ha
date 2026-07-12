# F-NIGHT-RESCUE — round-trip-honest c1, merge-bounded threshold, crossover buffer ramp

Status: **binding spec** for v0.11.0. Incident: night 2026-07-11→12 — ~3.3 kWh
of Sunday clipping was known in advance, yet the planner booked (almost) no
night pre-drain; the battery reached dawn at ~50 % (house load alone took it
to 35 % by 04:18), the dehumidifier ran only 06:03–06:33, and at 04:13 the
threshold jumped 20 → 58, shutting the inverter and all pre-drain off.
Operator principle to encode: *the closer the (stressed) PV crossover, the
less battery buffer is needed.*

Forensics (live history + repro `scratchpad/repro_night_predrain_20260712.py`):

- **D1 — c1 is round-trip-infeasible at night.** A 0.5 h night booking
  (213 Wh AC at 426 W) drains the battery; the next day's refill drops export
  by only ~168 Wh (AC→charger→battery→inverter→AC chain ≈ 0.79–0.82), but the
  c1 gate demands `export_drop ≥ (1−tol)·energy = 0.85·energy`. **Physics can
  never satisfy the gate for a pure-battery night run** — repro shows every
  night slot 22:00–05:00 failing exactly `c1 drop 168 < 181`. That is why
  bookings cluster at 05:00–08:00 (direct PV share fattens the drop) and never
  reach into the night, regardless of how much clipping is forecast.
- **D2 — one scalar T\* couples decoupled regimes.** `search_threshold`
  optimises import − terminal·end_energy + tiebreak·export over the FULL
  horizon. After the horizon's LAST clipping event, serving night AC from the
  battery is a knife-edge loss vs importing (1 Wh AC costs ≈1.085 Wh battery ≈
  0.998 terminal credit vs 1.0 import; inverter idle draw tips it), so with a
  WEAK final day (Tue 4.56 kWh vs Sun/Mon ~11) the optimiser hoards for the
  post-last-clip night by raising T\* — and the single scalar applies that
  hoarding to TONIGHT as well, although tomorrow's guaranteed clip makes
  tonight's drain free (merge principle: post-clip state is independent of
  tonight's threshold). Live timeline matches perfectly: T\*=20 all night;
  at ~04:00 the forecast day-rollover brought weak Tuesday into the horizon →
  04:13 T\* 20→58 → inverter off at SOC 35–54, pre-drain dead, night AC on
  grid. Repro with a STRONG day-2 keeps T\*=20 and monotonic costs — the flip
  needs the weak final day.
- **D3 — static pre-dawn floors.** Z4's stress floor (`inverter_min +
  buffer`) is constant in time. Just before the stressed PV crossover the
  buffer's purpose (survive forecast error across the remaining deficit
  window) shrinks to ~zero — the operator's ramp principle. Secondary to
  D1/D2 (live dynamic buffer is only 3 %), but it is the correct semantics
  and cheap once the crossover is computed for D2.

Counterfactual check (repro Q3): the physics allowed ~1–1.5 h of night
running from the 00:41 47 % (down to the ~23–25 % floor); a naive 5 h forcing
breaks the floor and imports — so the CORRECT fix is gate repair, not gate
removal. All planner gates keep applying.

## Requirements

### F1 — round-trip-honest c1 (D1)

- **R1** In `allocate_loads`, compute once per call the AC→battery→AC
  round-trip factor from the config the simulator actually uses:
  `rt = charger.eta · battery.eta_charge · battery.eta_discharge ·
  inverter.eta`, clamped to (0, 1]. (Verify the exact attribute names against
  `SystemConfig`/`simulate`; live ≈ 0.92·0.97·0.97·0.95 ≈ 0.822.)
- **R2** The c1 need becomes `need = (1 − tol) · power_wh · rt` at BOTH c1
  sites (energy-limited legacy path and the continuous c1 branch). Semantics:
  "at least (1−tol) of the run's energy is recovered from otherwise-lost
  export, judged at the efficiency the battery detour physically has". A run
  fed by DIRECT PV drops export ~1:1 and passes even more easily — no change
  needed there; the factor only stops punishing the battery detour for its
  own losses twice.
- **R3** Z2'/Z3/Z4 unchanged. The import-trade rule and floors still bound
  how DEEP the night drain may go; c1 only decides whether the energy is
  surplus-covered.

### F2 — merge-bounded threshold search (D2)

- **R4** `search_threshold` gains a MERGE BOUND: before the candidate scan,
  run the base sim once (no loads, threshold = the lower search bound `lo`,
  conservative PV = the same per-slot stress vector Z4 uses — stress_vec from
  F-QUANTILE-BANDS, i.e. P10 where banded else α). Find the first slot where
  the battery is FULL and clipping under that pessimistic sim
  (`soc_end ≥ soc_max − ε` AND `grid_export_wh > 0`). If such a slot exists,
  evaluate the candidate thresholds on the TRUNCATED horizon `[0, merge]`
  (inclusive; reuse the slot prefix — `simulate` already accepts any slot
  list via inputs? if not, truncate `PlanInputs` by slicing its slots — pure
  dataclass copy). If no stressed clip exists, behaviour is UNCHANGED (full
  horizon).
  Rationale: beyond a guaranteed-even-under-stress clip, the trajectory is
  independent of today's threshold (merge principle, D-A4) — post-merge
  economics (e.g. hoarding for a weak final day) must not leak into the
  pre-merge choice. The receding replan re-decides the threshold when the
  post-merge night actually approaches.
- **R5** Floor: never truncate below 6 slots (a degenerate 1–2 h window would
  make the threshold jumpy); if merge < 6 slots away, use 6.
- **R6** `allocate_loads`/pass gates keep operating on the FULL horizon and
  full-horizon trajectories (they already difference two complete
  trajectories; no change). Only the T\* CHOICE is merge-bounded.
- **R7** Observability: `plan()` exposes the merge bound as a new PlanResult
  field (e.g. `threshold_horizon_end: datetime | None`), surfaced as a
  SOC-forecast attribute `threshold_horizon_end` — null when full-horizon.
  The explain of the 04:13 class of events becomes visible on the card.

### F3 — crossover buffer ramp (D3)

- **R8** For the Z4 stress floor ONLY (continuous-load pre-drain bets), the
  BUFFER component ramps with the remaining stressed deficit: for a candidate
  slot `i`, `buffer_eff(i) = min(soc_buffer_percent,
  100 · stressed_deficit_wh(i) / battery.capacity_wh)` where
  `stressed_deficit_wh(i)` = Σ over slots j ∈ [i, crossover) of
  `max(0, consumption_wh[j] − stressed_pv_wh[j])`, crossover = first slot ≥ i
  where stressed PV ≥ consumption (bounded by the horizon; no crossover ahead
  → full buffer). `stress_floor(i) = inverter_min + buffer_eff(i)`. The Z3
  buffer floor (absolute battery protection) stays STATIC — only the
  inverter-reserve floor ramps.
- **R9** Consequence to encode in a test: a pre-dawn slot 1–2 h before the
  stressed crossover admits a drain to ~inverter_min + ~1 %, while an
  early-evening slot (8 h of dark deficit ahead) keeps ~the full buffer.

### Tests

- **R10 (D1 regression, the incident)** Clipping-eve geometry (SOC ~57 at
  21:00, next day ≥ 3 kWh stressed clip, learned 426 W dehumidifier): the plan
  books ≥ 1 h in the 22:00–05:00 window (was: zero — repro-verified), import
  stays within the trade allowance, min SOC ≥ the (ramped) floors.
- **R11 (D2 regression, the 04:13 flip)** Horizon = strong day 1 (clips even
  at stress), weak day 3 (≈ 40 % of day 1): T\* must stay at the low-import
  choice (≈ lo), NOT jump toward hoarding; assert `threshold_horizon_end`
  falls inside day 1. Control: same geometry WITHOUT the day-1 clip (weak
  day 1) → full horizon, hoarding T\* allowed (existing behaviour) — both
  directions pinned.
- **R12 (F1 unit)** need formula: rt from config efficiencies; a pure
  round-trip booking with export_drop = rt·energy·(1−tol)+ε passes, −ε fails;
  direct-PV bookings unchanged.
- **R13 (F3 unit)** buffer ramp per R9; no crossover ahead → static floor;
  stressed (not nominal) PV drives both deficit and crossover.
- **R14 (goldens)** Deltas expected and classified: (viii) new night bookings
  from rt-honest c1 — import may only rise within the Z2' trade allowance;
  (ix) T\* drops in clipping-eve golden scenarios (import equal or lower);
  (x) knock-on placement shifts. Per-scenario classification table in the
  commit; unexplained delta = stop-the-line.

### Docs / version

- **R15** ALGORITHM.md D-A4 v9 note (three sub-decisions: rt-honest c1,
  merge-bounded T\*, crossover ramp — each with the one-line rationale);
  F-PREDRAIN.md §3 cross-note (c1 need now efficiency-aware). CHANGELOG
  `[0.11.0]`; manifest + pyproject → 0.11.0.

## Non-goals

No executor changes. No new config keys (rt derives from existing
efficiencies; ramp uses existing buffer/inverter_min; merge bound is
structural). No change to pass ordering, saturation/gate-topup, quantile
ingestion. The energy-limited L5 restrictions stay (strict import, no
β/c2) — but they GET the rt-honest c1 (R2 covers both branches) so fossibot
pre-dawn top-ups before a clipping day become feasible too.

## Verify

Full suite green (winshim), ruff check + format --check (0.15.21), goldens
regenerated ONLY with the R14 classification. Live observable after deploy:
on the next clipping-eve night the plan books night dehumidifier hours (card
shows them pre-dawn), T\* stays low overnight, `threshold_horizon_end` sits in
the clipping morning, and the battery reaches the ramped floor (~21–23 %)
by the PV crossover instead of 50 %.
