# F-EXECUTOR-GUARDS — dwell-exempt target stop, stale-SOC guard, HA deprecation

Status: **binding spec**, part 2/2 of v0.9.0 (operator: "Setze alles um",
2026-07-10). Part 1 is docs/F-PLANNER-HONESTY.md. Coordinator/executor only —
no planner-core changes, goldens untouched by construction.

## 1. Problems

**P1 — target-SOC overshoot through the ON-dwell.** An energy-limited load's
plan-driven OFF (target SOC reached, rem → 0) is gated by the ON→OFF dwell
(`min_runtime`), so a 156 Wh top-up keeps charging for the full 30 min at
~505 W real (~250 Wh) and lands at ~95 % instead of 90 %. The dwell exists to
protect relays/compressors from short cycling — for a powerstation whose
charging is stopped by a charge-enable gate (input_boolean) that protection is
irrelevant to the STOP: the gate switches no load current path mechanically
worth protecting, and the plug (if switched at all) switches currentless
afterwards.

**P2 — stale load SOC is invisible.** The fossibot integration returns cached
SOC values with FRESH timestamps ("Poll timed out, returning cached data"), so
availability/age checks cannot catch it and the planner keeps booking against
a frozen `remaining`. v0.8.1's executor cap bounds a single run; nothing stops
the PLANNER from re-booking run after run against the frozen value.

**P3 — deprecated coordinator init.** `DataUpdateCoordinator.__init__` is
called without `config_entry=`; newer HA cores warn (`report_usage`) and will
eventually break (observed locally on the venv314 HA).

## 2. Requirements

### G1 — dwell-exempt target-SOC stop (P1)

- **R1** In `_apply_load_switching`, a pending switch **OFF** skips the
  ON→OFF dwell check iff ALL of: the load is energy-limited, a charge-enable
  entity is configured, the load's SOC reading is present and
  `soc >= target_soc_percent`. Everything else about the OFF action is
  unchanged (enable off first, then plug per policy — the OFF branch already
  orders it so the plug switches currentless).
- **R2** The dwell TIMESTAMP is still stamped on the confirmed switch, so the
  OFF→ON dwell (`min_off`) fully applies afterwards — a SOC hovering at the
  target cannot flap the gate: re-on additionally requires the planner to book
  again (rem > 0, i.e. SOC must first drop below target).
- **R3** Plug-only energy-limited loads (no charge-enable) keep the full dwell
  (conservative: the plug relay is exactly what `min_runtime` protects).
- **R4 (tests)** (a) energy-limited + enable gate + soc ≥ target: OFF executes
  before `min_runtime` has elapsed; (b) same load, soc < target: dwell still
  blocks; (c) plug-only load at target: dwell still blocks; (d) after a
  target-stop, an immediate re-on attempt is blocked by `min_off`.

### G2 — stale-SOC guard (P2)

- **R5** New per-load runtime tracking (in-memory, not persisted): while
  `_load_charging_active[id]` is true AND the power feedback's RAW reading
  passes the existing standby bar (`min_sample_w` — reuse it; do not invent a
  second threshold), track the SOC value; if it stays EXACTLY unchanged for
  `STALE_LOAD_SOC_MIN = 12` minutes (const.py, no config key; fossibot cadence
  is ~1 min, so 12 min of frozen SOC while drawing ~500 W is unambiguous),
  latch the load's SOC as **stale**.
- **R6** Effect while latched: the load's `SurplusLoadState` is built with
  `available=False` (existing semantics: never scheduled → plan-driven OFF via
  the normal executor path) and a change-gated WARNING is logged naming the
  load and the frozen value. Unlatch as soon as the SOC entity reports a
  DIFFERENT value (charging or not); unlatching logs INFO once.
- **R7** No latching without evidence: loads without a SOC entity or without a
  power-feedback entity never latch (the guard needs both signals). A taper
  below the standby bar pauses the evidence clock (no false positive at end of
  charge); the clock RESETS when charging stops or the sample bar is not met —
  it measures continuous charging against a frozen value, not wall time.
- **R8** Observability: the per-load plan data dict exposes `soc_stale: bool`
  (drives dashboards/diagnostics; the existing diagnostics section gains it).
- **R9 (tests)** (a) frozen SOC + active charging ≥ threshold minutes →
  `available=False`, WARNING once (not every cycle); (b) SOC change unlatches
  and re-schedules; (c) taper/inactive periods do not accumulate evidence;
  (d) load without SOC or power entity never latches.

### G3 — `config_entry` deprecation (P3)

- **R10** Pass `config_entry=entry` to `DataUpdateCoordinator.__init__`
  (verify the kwarg exists in the installed HA before relying on it — it does
  in the venv314 core). Keep the existing `self.entry` attribute and all its
  usages untouched (alias, minimal diff).
- **R11 (tests)** Full HA suite green is the regression proof; add no test
  unless a setup fixture must change.

## 3. Non-goals

No persistence of stale latches (a restart re-detects within minutes). No new
config keys. No planner change (the guard acts through the existing
`available` flag). No handling of the fossibot integration's flakiness at its
source (separate project).

## 4. Test/verify

Full suite green on `.venv314` (winshim), ruff check AND format check (0.15.21).
Goldens MUST be byte-identical (coordinator-only change). docs/LOAD_CONTROL.md:
short sections for the target-stop dwell exemption and the stale-SOC guard.
CHANGELOG under [Unreleased]; release cut as v0.9.0 together with part 1.

## G4 — Floor guard: surplus loads never run grid-fed (v0.13.1)

Binding operator rule (2026-07-18, after the 06:20-06:30 incident — a booked
dehumidifier run stayed grid-fed for ~10 min at the 20 % inverter cutoff
because only the min_runtime dwell timed the OFF): **"Wenn der Inverter aus
ist oder der SOC 20 % erreicht, dürfen Zusatzlasten nicht mehr angesteuert
werden."**

- **R12** `_update_floor_guard(soc, config)` runs every cycle directly after
  `_apply_hysteresis`. Trip: `soc <= inverter_min_soc_percent` OR inverter
  recommendation off ("Inverter aus" is deliberately implemented as the
  RECOMMENDATION, not the physical inverter state — BM has no inverter state
  entity; the SOC branch catches the physical cutoff case, the recommendation
  branch the T*-driven one). The WHOLE guard latches: release requires the
  SOC strictly above the floor AND `>= floor + hysteresis_percent` (the
  strict-floor clause keeps trip/release disjoint even at hysteresis 0) AND
  the recommendation on. Restart inside the release band starts latched.
- **R13** Enforcement in `_apply_load_switching`: `desired = False` for every
  controlled load, ON->OFF dwell-exempt (G1 precedent; the confirmed OFF
  stamps the dwell so min_off gates the re-on). `_execute_load_switching`
  re-checks the guard per action and DROPS queued switch-ONs (in-flight race:
  a trip during a running switch task must not let a pre-trip ON fire), and
  requests a refresh after the task when the guard is active (the early
  in-flight return in the tripping cycle could not queue its forced OFFs).
- **R14** Published state follows: `_effective_load_active` returns False
  (operator automations and recommendation-only loads stop; note: for
  recommendation-only loads there is no BM-side min_off after a guard stop —
  the release hysteresis is the only flap brake, documented semantics), the
  appliance start-window advisory reads False, runtime accrual's plan-based
  fallback stops, and `_update_power_warnings` treats all loads as inactive
  (no 0 W "full tank" false positive during a guard episode). Surfaced as
  `floor_guard_active` (coordinator data + inverter-recommendation sensor
  attribute).
- **R15 (tests)** Force-off despite min_runtime; no ON under guard; latch
  trip/hold/release incl. hysteresis-0 disjointness; recommendation-off trip;
  restart-in-band latched; published active capped; end-to-end refresh at
  SOC below floor -> guard active + appliance advisory False; queued-ON drop.

Supersedes F-SUBHOUR R9's "never off before min_runtime" for the guard case.
