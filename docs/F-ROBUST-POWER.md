# F-ROBUST-POWER — robust per-load planning-power estimation (v0.14.0)

Status: **binding spec**, operator decision 2026-07-18. Implemented in
`core/power_learning.py` + the sample-buffer rework in `coordinator.py`.

## 1. Incident (root cause, empirically verified)

At the 2026-07-18 06:21 inverter cutoff (the G4 incident,
docs/LOAD_CONTROL.md §11) the dehumidifier compressor's restart transient —
**1711 W**, held ~60 s by the measuring plug's update cadence — was blended
into the then-current EMA: `0.3·1711 + 0.7·433 ≈ 818`. The run-max rule froze
that blend and persisted it, so a **426 W** device was planned at **818 W**
for the whole morning; the phantom surplus consumption pushed the fossibot
into 30-min duty-cycle scraps. The R2a seed rule only protected the FIRST
sample of a run — mid-run spikes fed the EMA unhindered, and one sample was
enough to lift it by up to 30 %.

## 2. Operator spec (binding, 2026-07-18)

Planning power = robust average over the recent run window (~30 min sketch).
SHORT dips and spikes must ALWAYS be ignored (defrost pauses where only a
small fan runs, compressor inrush, inverter-transfer transients). SUSTAINED
level changes must be adopted — a durably low draw is real. When the normal
level (~400 W) returns for a LONGER time (~10 min), it must be re-learned
quickly. The solution must generalise beyond the dehumidifier: fossibot
charge rates are operator-changeable and battery-side throttling produces
legitimate sustained tapers — levels ABOVE the configured nominal power are
legitimate too (505–701 W at 300 W nominal), so there is **no nominal
clamp**. Appliances (Waschmaschine) with declared run energy + duration keep
planning with the DECLARED values (see §6).

## 3. Estimator (core/power_learning.py)

`robust_power_estimate(samples, now) -> PowerEstimate(watts|None,
coverage_s, fast_adopted)` over ACCEPTED `(timestamp, watts)` samples (the
caller applies the v0.6.2 standby bar and the runs-at-BM's-request gate —
both unchanged).

- **Time weighting**: sample i covers `[t_i, min(t_{i+1}, t_i + GAP_CAP_S))`
  (last sample up to `now`, capped), clipped to the window — sparse and
  chatty sensors behave identically; a spike followed by silence is credited
  at most `GAP_CAP_S`.
- **Slow path**: time-weighted MEDIAN over `WINDOW_S` (1800 s). A level
  occupying < 50 % of covered time (60 s transfer transient, 8-min defrost
  dip) cannot move it; a sustained level is adopted once it is the majority
  (~15 min).
- **Fast-adopt**: when the last `FAST_WINDOW_S` (600 s) are internally
  stable (time-weighted P10/P90 within ±`STABLE_BAND` (20 %) of the
  sub-median) AND the sub-median deviates > `FAST_DEVIATION` (20 %) from the
  slow median, the sub-median wins immediately — "10 stable minutes
  re-learn quickly" (operator-changed charge rates included).
- **Warm-up**: below `WARMUP_COVERAGE_S` (300 s) of accepted coverage the
  estimate is `None` — start-up inrush can never be served or learned.
  **Supersedes F-PLANNER-HONESTY R2a** (seed rule) entirely.
- Module constants only, **no new config keys**: WINDOW 1800 s, FAST 600 s,
  bands 0.20, WARMUP 300 s, GAP_CAP 300 s, MAX_SAMPLES 720.

## 4. Coordinator wiring

- `_load_power_ema`, `_load_run_power_max`, `_POWER_EMA_ALPHA` are REMOVED.
  New: `_load_power_samples: dict[str, deque[(ts, watts)]]` with the run's
  lifecycle (dropped on run end / foreign use; NOT persisted — a stale
  window must not serve as fresh measurement after a restart).
- `measured_power_w` = the live estimate while running (None during
  warm-up → `planning_power_w` falls back to learned/nominal — the seam is
  unchanged, **no optimizer change, goldens bit-identical**).
- `learned_power_w` = WRITE-THROUGH of the estimate while running ("last
  stable estimate wins"; persistence key `load_learned_power` and the
  vanished-subentry pruning are unchanged). Old persisted values are kept on
  upgrade — the write-through self-heals within minutes of the first run.
- Mid-run feedback gap: the buffer estimate keeps serving (v0.5.1
  semantics); a long outage decays into warm-up rather than a stale value.
- **3×-nominal WARNING** (change-gated, once per run): the median cannot
  detect a PLAUSIBLE frozen sensor value (fossibot cached-data flakiness) —
  gross lies are observed, deliberately not clamped.

## 5. Behavior changes to know

- Runs shorter than ~5 min never learn and serve no measured value
  (previously the EMA served from sample 1, learned from sample 2).
- The learned value now FOLLOWS a sustained taper down (operator: "durably
  low is real") instead of freezing the run maximum. The saturation gate
  keeps its nominal floor (`max(power, nominal)`, unchanged), so a decayed
  value still cannot weaken it.

## 6. Appliances: declared values rule (clarification, no code change)

Verified: appliance planning uses ONLY the declared `run_energy_wh` /
`run_duration_h` (`series._apply_appliance_runs`, `insert_appliance_run`);
measured power feeds nothing but the on/off detection hysteresis. No learned
value ever influences appliance planning.

## 7. Tests

`tests/core/test_power_learning.py` (pure): 818 regression (1711 W/60 s →
estimate stays 420–470 at every step), 8-min dip ignored vs 20-min dip
adopted, sustained-low via median, fast-adopt stable 10 min (+ oscillating
counter-case), warm-up None, sparse-vs-chatty equivalence + GAP_CAP,
window pruning. `tests/ha/test_load_switching.py`: HA-level 818 twin,
warm-up/gap/run-end lifecycle, F-L6 manual-run gate (semantics verbatim),
standby bar, persistence round-trip, 3×-nominal warning (served, not
clamped).

## 8. Residual limitation (accepted)

A frozen-but-plausible sensor value (within 3× nominal) that persists longer
than the window is indistinguishable from a real level and will be adopted —
sensor-level lying cannot be fixed at this layer. G2 (stale SOC) covers the
SOC side; the 3× warning covers gross power lies.
