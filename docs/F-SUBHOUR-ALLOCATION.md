# F‑SUBHOUR — Sub‑hour surplus‑load allocation (+ appliance‑run status)

Status: **design approved, implementation in progress** · Author: planner work,
2026‑07‑09 · Scope: `battery_manager` core planner + coordinator executor.

## 0. TL;DR

Today the surplus‑load planner books **whole hours** and the executor is a
per‑cycle on/off follower. A 400 W load can therefore never be scheduled to run
30 min to soak a ~200 Wh surplus, even though the battery buffers the intra‑hour
mismatch at **net‑zero cost**. This feature makes the planner commit a
**variable sub‑hour run** quantised to each load's `min_runtime_min`, and makes
the executor **actively switch the load off** after the planned energy is
delivered (robust "approach A").

**Feature 2 (detected household appliance folded into the plan) is already
implemented and live** — see §5. It needs no build, only (optionally) hardening.

## 1. Motivation (the operator's argument, verified)

Battery full; over one hour ~200 Wh of PV would be exported. Run a 400 W load
for the first 30 min:

| phase | PV surplus | load | battery | export |
|---|---|---|---|---|
| 0–30 min (on) | 200 W | 400 W | −200 W → −100 Wh | 0 |
| 30–60 min (off) | 200 W | 0 | +200 W → +100 Wh | 0 |
| **hour** | | 200 Wh | **±0** | **0** (was 200) |

The load absorbs 200 Wh entirely from would‑be‑lost export, battery net‑zero, no
import. The blocker is not physics; it is two code heuristics:

1. `optimize._committed_hours = max(slot.duration, min_runtime/60)` → a full hour
   for interior slots (`core/optimize.py:84`).
2. `LoadPlan.schedule` is a per‑slot **bool**; the executor follows `active_now`
   (`schedule[0]`) each cycle with a `min_runtime` dwell and no active‑off
   (`coordinator.py:1957`). Real run length is emergent, so a sub‑hour booking
   would still run the full hour → the "~250 Wh unaccounted" under‑count the
   whole‑hour floor was added to prevent (live 2026‑07‑05 04:59).

Pass 2 (preemptive) already implements exactly the buffered‑capture idea; its
gate `export_drop ≥ (1−tol)·power_wh` is unreachable in the afternoon only
because `power_wh` is a whole hour (340 Wh) instead of a 30‑min quantum (170 Wh).

## 2. Goals / non‑goals

**Goals.** Capture small, battery‑bufferable surpluses with non‑energy‑limited
loads (dehumidifier, heater) by running them for a sub‑hour multiple of
`min_runtime_min`; keep planned energy == actuated energy; change nothing when a
full hour is (still) the right answer.

**Non‑goals.** No new config (reuses `min_runtime_min`). No change to
energy‑limited powerstations (governed by target SOC). No change to the
simulator (already energy‑ and duration‑exact). No sub‑`min_runtime` runs
(compressor short‑cycling protection stays).

## 3. Requirements (testable)

### Planner (core, pure)
- **R1** For a **non‑energy‑limited** load, a per‑(load, slot) commitment MAY be
  `k · q` hours where `q = min_runtime_min/60` and `k ∈ {1,2,…}`, capped at the
  slot's remaining duration. Energy‑limited loads keep `max(slot.duration, q)`.
  *(Superseded by docs/F-RESIDUAL-TOPUP.md R1, v0.8.1: energy‑limited loads now
  get the same `k·q` candidate list as continuous loads.)*
- **R2** A commitment is **never shorter than `min_runtime_min`** (`k ≥ 1`).
  Guards the executor's minimum on‑time and the under‑count invariant.
- **R3** Among valid quanta the planner selects the **largest `k`** that passes
  the pass's pre‑filter (Pass 1 soft‑surplus `battery_share ≤ tolerance`; Pass 2
  `export_drop ≥ (1−tol)·power_wh`) **and** the full re‑simulation Z2
  (`total_import ≤ base_import`) and Z3 (buffer floor holds). Search largest→
  smallest, accept the first that clears both filters. Re‑sim runs only on the
  candidate that clears the cheap pre‑filter (≤ 1–2 re‑sims per placement).
- **R4** The booked energy `power_w · (k·q)` is placed via the existing
  `_spread_energy`; the simulator is **unchanged** and models the partial slot
  as a smaller `extra_ac_wh[j]`.
- **R5** `LoadPlan` gains a per‑slot planned run vector `run_hours: tuple[float,…]`
  (hours actually booked in each slot). `schedule[i]` becomes `run_hours[i] > ε`
  and `active_now` becomes `schedule[0]`, so the forecast card and diagnostics
  (which read bools) are unaffected. `planned_energy_wh` and `allocations`
  unchanged in meaning.
- **R6 (regression)** When the largest valid `k` fills the whole slot — i.e.
  whenever a full‑hour booking passes today — the chosen commitment is the full
  slot, so the produced plan is **bit‑identical** to today. Golden scenarios
  change only where a non‑energy‑limited load that full‑hour‑**failed** now
  sub‑hour‑**succeeds** (a new, intended capture); such deltas are documented.

### Executor (coordinator, live actuation — approach A)
- **R7** On the ON edge of a non‑energy‑limited controlled load, freeze the
  **contiguous planned run** `D = Σ run_hours[i]` over the contiguous scheduled
  slots from slot 0, and set `off_at = run_start + max(min_runtime, D)`.
- **R8** Force the load **OFF at `off_at`** via an explicit one‑shot timer
  (`async_track_point_in_time`); the 300 s poll is too coarse to stop mid‑hour.
  The force‑off uses the existing OFF path and stamps `_last_load_switch` (dwell).
- **R9** The load is also switched off earlier if a later plan makes it inactive
  once past the dwell (surplus vanished), and **never** off before `min_runtime`.
- **R10** After a forced off the `min_runtime` dwell blocks immediate re‑on; a
  later surplus window re‑activates the load (duty‑cycling across export windows,
  e.g. dehumidifier 16:00–16:30 and 17:00–17:30).
- **R11 (accounting)** Delivered ≈ booked: booked ≥ `power·min_runtime` (R2) and
  the executor caps the real run at `D` (R7/R8) → no "~250 Wh" gap. A regression
  test asserts `planned_energy_wh ≥ power·min_runtime` and simulated‑delivered ≈
  booked for a sub‑hour placement.
- **R12** **energy‑limited** loads: unchanged (level‑driven, target‑SOC stop; no
  quantum, no force‑off). *(Superseded by docs/F-RESIDUAL-TOPUP.md R7/R9, v0.8.1:
  energy‑limited loads now quantise like continuous loads, and the executor caps
  a sub‑hour run with the same frozen off‑deadline.)* **Recommendation‑only**
  loads (no control switch): publish `active=False` at `off_at` so the operator's
  automation stops them (deadline anchored on first‑seen‑active).
- **R13 (lifecycle)** The one‑shot timer is tracked and cancelled in
  `async_cancel_actuation_tasks` before flush/unload. Run state (`run_start`,
  `off_at`) is persisted or safely reconstructed post‑restart so a restart never
  converts a capped run into an uncapped one (mirror‑image of the 2026‑07‑05
  dwell‑wipe incident).

## 4. Design

### 4.1 Planner (`core/optimize.py`, `core/model.py`)
- New helper `_quantised_hours(load, slot) -> list[float]`: for a non‑energy‑
  limited load returns `[k·q for k in kmax..1]` (largest first, ≤ slot.duration,
  q floored at one quantum); for energy‑limited returns `[max(slot.duration, q)]`
  (today's value) so those loads are untouched.
- In both passes, replace the single `commit_h` with a loop over
  `_quantised_hours`: compute `power_wh`, `_spread_energy`, run the pass's
  pre‑filter, and only then Z2/Z3; **accept the first (largest) that clears
  both**; record `run_hours` per covered slot from `covered`.
- `LoadPlan.run_hours` assembled from the per‑slot `take` values; `schedule`
  derived (`take > ε`). `active_now = schedule[0]`.
- Cost/perf: `kmax = floor(slot.duration/q)` ≈ 2 at the default 30‑min quantum;
  the pre‑filter prunes most candidates before any re‑sim.

### 4.2 Executor (`coordinator.py`)
- New per‑load state near `coordinator.py:231`: `_load_run_deadline: dict[str,
  datetime]`; a tracked one‑shot cancel handle `_load_off_timer: dict[str,
  CALLBACK]`.
- `_apply_load_switching`: for non‑energy‑limited controlled loads, read the
  slot‑0 contiguous `D` from the plan; on the ON action, compute `off_at` and
  arm the timer; `desired = active_now AND now < off_at`.
- Force‑off callback re‑uses `_execute_load_switching`'s OFF branch under the
  existing lock; stamps the dwell (so no re‑on for `min_runtime`).
- Recommendation‑only loads: compute an **effective active** (`active_now AND
  now < off_at`) into `load_plans[id]["active"]` (read by the binary sensor).
- Cancel/await the timer in `async_cancel_actuation_tasks`; persist `run_start`+
  `off_at` (or reconstruct from the first post‑restart plan) in the persistent
  payload.

### 4.3 Invariants preserved
- `min_runtime` stays the dual‑role quantum **and** anti‑chatter dwell; booked ≥
  one quantum; `off_at ≥ run_start + min_runtime` always.
- `HourSlot.ac_wh` still excludes surplus loads; surplus energy stays in
  `extra_ac_wh` only (no double count with the threshold search / dynamic buffer).
- `extra_ac_wh` stays index‑aligned to `inputs.slots`.

## 5. Feature 2 — detected appliance (ALREADY IMPLEMENTED)

Configure an **"Appliance"** subentry (`config_flow.py:972`) per device:
detection entity (power sensor or on/off state), power threshold (default 10 W),
expected run energy (Wh) and run duration (h). Then, every cycle,
`coordinator._appliance_is_running` + `_get_appliance_runs` (`coordinator.py:838,
851`) emit an `ApplianceRun(remaining_energy, remaining_hours)` — remaining =
`run_energy · remaining_h/duration` — which `build_slots → _apply_appliance_runs`
(`series.py:153`) folds into each slot's `ac_wh`, live in the plan. Tested
(`test_appliance_run_adds_to_ac_load`) and documented (CHANGELOG). **For the
dishwasher: add one Appliance subentry (its power sensor + ~1.0 kWh / ~2 h).**

### Optional hardening (real correctness gaps; recommend H1+H2)
- **H1** Persist `_appliance_started` (in‑memory today) → a restart mid‑run no
  longer re‑injects the full run energy. *(correctness)*
- **H2** Detection **off‑threshold / debounce** so a brief sub‑threshold dip
  (dishwasher soak between heater bursts) doesn't reset the start clock and
  re‑inject full energy — the appliance‑side analogue of the under‑count.
  *(correctness; small config addition `off_threshold_w`/`min_on_s`)*
- **H3** Optional: re‑anchor remaining energy to a **measured cumulative‑energy**
  entity instead of pure time proration.
- **H4** Optional: a diagnostic sensor exposing the active `ApplianceRun`.
- **H5** Optional: `in_house_measurement` for appliances (avoid double‑count with
  the learned AC series).

## 6. Test plan

- `tests/core/test_optimize.py`: (a) a non‑energy‑limited load with a small
  direct surplus books `k·q` not a whole hour; (b) largest‑k selection; (c)
  energy‑limited load unchanged; (d) `run_hours`/`schedule`/`active_now`
  derivation; (e) `planned_energy_wh ≥ power·min_runtime` (R11); (f) Pass‑2
  afternoon‑dribble capture that fails at whole‑hour and passes at a quantum.
- `tests/core/test_golden_topology.py`: regenerate/verify goldens; document any
  intended deltas (non‑energy‑limited full‑hour‑fail → sub‑hour‑pass).
- New coordinator test: ON arms `off_at = run_start + max(min_runtime, D)`; the
  one‑shot fires an OFF; OFF stamps the dwell; a persistent surplus keeps the
  load on; recommendation‑only `active` flips at `off_at`; unload cancels the
  timer; restart doesn't uncap an in‑flight run.
- Regression: full suite green in the HA venv (`~%LOCALAPPDATA%\Temp\claude\
  ha-venv`).

## 7. Rollout

- One `## [Unreleased]` CHANGELOG entry; bump `manifest.json` minor.
- Behaviour is **inactive by construction** for energy‑limited loads and for any
  load where a full hour still passes → safe default; only non‑energy‑limited
  loads on small surpluses change. Deploy after full‑suite green + a driven
  verification of one sub‑hour cycle.

## 8. Locked decisions (operator, 2026-07-09)

- **R14 — separate minimum OFF time.** Add a per-load `min_off_min` config
  (new `CONF_LOAD_MIN_OFF_MIN`, `SurplusLoad.min_off_min`, selector in the load
  subentry flow, mapped in `build_system_config`). `min_runtime_min` stays the
  minimum ON time (ON->OFF dwell); `min_off_min` is the minimum OFF time
  (OFF->ON dwell) to protect compressor loads from short-cycling. The
  `_last_load_switch` gate is split by direction. Back-compat: absent key falls
  back to `min_runtime_min` (today's symmetric dwell, bit-identical). Executor-
  only; the planner does not model it.
- **Feature 2 hardening H1 + H2 are IN SCOPE** (not optional): persist
  `_appliance_started` across restart (H1); add an off-threshold / debounce so a
  brief sub-threshold dip does not reset the run clock and re-inject full energy
  (H2). H3-H5 remain optional/deferred.

## 9. Post-review notes (adversarial review, 2026-07-09)

A 4-dimension adversarial review found 6 bugs the green suite missed; all fixed:
- **R12 recommendation-only cap** is implemented via a first-active deadline and
  an effective-`active` publish. **Limitation:** duty-cycling *within one
  continuous `active_now` window* is not implemented for recommendation-only
  loads — the deadline re-anchors only when the plan cycles inactive→active. This
  is safe (never over-delivers) but may under-capture a second contiguous export
  window for a recommendation-only load. Controlled loads duty-cycle fully.
- **`active_run_hours(durations)`** now stops the real-time block at the first
  slot not filled to its own duration (a sub-hour cap in slot i with slot i+1
  separately scheduled is a real-time gap, not a continuous run). The coordinator
  threads `tuple(s.duration for s in inputs.slots)`.
- **Appliance H1 restart edge**: a persisted start whose configured run already
  elapsed is re-anchored to `now` once, at the first post-restart evaluation, so a
  new run active at restart is not pinned at 0 remaining.
- **Config back-compat**: the `min_off_min` / `off_threshold_w` reconfigure
  defaults mirror the coordinator's absent-key fallback, so a no-change
  reconfigure never silently alters behaviour.
