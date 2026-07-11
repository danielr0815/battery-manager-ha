# F-QUANTILE-BANDS — per-slot P10/P90 forecast bands replace scalar α/β

Status: **binding spec** for v0.10.0. Operator: "kümmere dich um v0.10.0"
(2026-07-11). Prereqs shipped: v0.8.0 per-slot `pv_scale` vectors, v0.9.x
gates, the balcony-solar-forecast cutover (2026-07-11), and that integration's
quantile module (empirical P10/P90 per (cloud-class × day-part) bin, cold-start
gated: a bin publishes a band only with ≥20 samples from ≥5 distinct days —
"no fake spread").

## 1. Problem

The planner's uncertainty handling is two ARBITRARY scalars:
- **α** (`predrain_pv_confidence`, live 0.5) — pessimism for the Z4
  lower-buffer stress on pre-drain bets,
- **β** (`upper_pv_reserve`, live 1.2) — optimism for the c2 in-window
  insurance bookings.

Both apply uniformly to every slot of every day regardless of how certain the
forecast actually is. Verified consequences: β=1.2 books afternoon insurance
runs on days whose real spread is small (operator complaint), while β=1.0
forfeits real yield on genuinely volatile days (162-combo sweep: lost surplus
up in 122/162). The balcony forecaster now publishes EMPIRICAL per-15-min
P10/P90 curves as `wh_period_p10` / `wh_period_p90` attributes **on the same
three sensors battery_manager already reads** — maturing bin-by-bin over the
coming days.

## 2. Design decisions

- **D1 (representation)** Quantiles enter the planner as per-slot RATIO
  vectors against the median series: `p10_ratio[j] = p10_wh[j] / pv_wh[j]`,
  `p90_ratio[j] = p90_wh[j] / pv_wh[j]`.
- **D2 (band presence, the cold-start trap)** A slot HAS a band iff all of:
  p10/p90 data covers the slot, `pv_wh[j] ≥ QUANTILE_RATIO_MIN_WH` (25.0,
  const — ratios against ~zero PV are noise), and `p90_wh[j] − p10_wh[j] >`
  a real spread (> max(1.0 Wh, 1 % of pv_wh[j])). A COLLAPSED band
  (p10==p50==p90, the balcony cold-start signature) counts as **no band** —
  it means "no evidence", NOT "no uncertainty"; treating it as certainty
  would make Z4 stress WEAKER than today's α=0.5 on cold bins.
- **D3 (per-slot fallback composition)** Two effective vectors, composed once
  per `allocate_loads` call:
  - `stress_vec[j] = clamp(p10_ratio[j], 0.1, 1.0)` where band present,
    else `alpha` (scalar, as today).
  - `optimism_vec[j] = clamp(p90_ratio[j], 1.0, 2.0)` where band present,
    else `beta` (scalar, as today).
  Result: with no bands anywhere the planner is BIT-IDENTICAL to v0.9.3 at
  the same scalars; as bins mature, slots switch to evidence one by one.
  The clamps also guard junk ratios; simulate()'s FIX-8 physical peak clamp
  additionally bounds ratios > 1 downstream.
- **D4 (no new config)** α/β stay as the FALLBACK dials (their config keys,
  defaults and options UI are untouched). Recommended operator posture after
  v0.10 settles: β=1.0 → insurance then fires ONLY where P90 evidence exists
  (band-present slots use p90_ratio regardless of the β scalar); α keeps the
  conservative posture for evidence-free days. This resolves the pending
  β decision without sacrificing either principle.
- **D5 (zero new entities/keys)** The p10/p90 curves are read from the SAME
  configured PV forecast entities (attributes on them) — structural guarantee
  that quantiles and median come from the same source (verification caveat).
- **D6 (placement policy unchanged — non-goal)** c2 still books latest-first
  in-window; Z4 windows unchanged. v0.10 changes WHAT the gates believe, not
  WHERE bookings land. With real evidence the insurance frequency drops
  sharply and a remaining afternoon run is data-justified; a
  placement-policy change would be a separate operator decision.

## 3. Requirements

### Ingestion (coordinator, hourly mode only)

- **R1** The hourly PV reader (F-PREDRAIN F1 path) additionally parses
  `wh_period_p10` and `wh_period_p90` attributes from each of the three PV
  entities WHEN PRESENT: same timestamp normalisation, same slot aggregation,
  same per-entity stale-cache pattern (FIX-4) — cached alongside the median
  buckets in the same cache entry. Absent/empty/garbage attributes → no p10/
  p90 series for that day (never an error). Daily/two-window mode → none.
- **R2** Per-slot results are threaded like pv itself: `Slot` gains optional
  `pv_p10_wh: float | None = None` and `pv_p90_wh: float | None = None`
  (frozen dataclass, neutral defaults → every legacy constructor, all goldens
  and the whole test corpus stay valid); `build_slots` accepts and fills the
  optional series. A slot only carries values where the p10/p90 buckets
  actually covered it (partial coverage is per-slot, not per-day).

### Planner composition (core/optimize.py)

- **R3** Helper `_effective_uncertainty(inputs, alpha, beta)` →
  `(stress_vec, optimism_vec, band_slots)` per D2/D3, computed ONCE at the
  top of `allocate_loads`. `band_slots` is the per-slot bool of D2.
- **R4** Engagement guards generalise:
  - The c2 machinery (`current_beta` baseline, trial_beta, refresh) engages
    iff `any(optimism_vec[j] > 1 + _EPS)` (replaces every `beta != 1.0`);
    all three sites pass `pv_scale=optimism_vec` (list) instead of the
    scalar. The gate math (export-drop comparison) is unchanged — both sims
    use the same vector.
  - Z4 stress engages iff `any(stress_vec[j] < 1 − _EPS)` (replaces
    `alpha != 1.0`); the windowed scale vector becomes
    `[stress_vec[j] if i <= j <= hi else 1.0 …]` (replaces the scalar-alpha
    fill). The (i, hi) stress-base cache and FIX-7 spill extension are
    unchanged.
- **R5** The F-PREDRAIN diagnostics that simulate with alpha (stressed_min_soc
  path) use `stress_vec` the same way, so diagnostics and gate agree.
- **R6** Explain-plan: a c2 acceptance whose window-decisive slots were
  band-backed reads `"in-window insurance (p90)"`, else the existing
  `"(beta)"` wording. Decision rule (keep it simple and deterministic): p90
  wording iff `band_slots[i]` is true for the accepted slot i, else beta.

### Observability

- **R7** The SOC-forecast sensor gains `quantile_coverage`: per forecast day
  the fraction (0.0-1.0, 2 decimals) of DAYLIGHT slots (pv_wh > 0) with a
  band, plus `"source": "p10/p90" | "scalar" | "mixed"` per day. Cheap,
  computed from `band_slots`; lets the operator literally watch the bands
  mature day by day.

### Regression / fallback guarantees

- **R8 (bit-identity anchor)** With no p10/p90 data anywhere (all goldens,
  the entire existing test corpus, daily mode, cold balcony bins), plans are
  **bit-identical** to v0.9.3 at the same α/β values. Goldens must be
  byte-identical without regeneration — any delta is stop-the-line.
- **R9** Partial coverage mixes correctly: a day with bands only 10:00-14:00
  uses evidence there and scalars elsewhere IN THE SAME simulation vector.
- **R10** Collapsed bands (p10==p90) fall back to scalars (D2) — dedicated
  test, this is the safety-critical rule.

### Tests (core unless noted)

- **R11** `_effective_uncertainty`: band detection (presence, collapsed,
  low-PV, missing), clamps, fallback composition, R9 mixing.
- **R12** Gate behaviour: (a) a c2 insurance booking that exists at β=1.2
  scalar disappears when the slot's band is present with p90_ratio ≈ 1.0
  (evidence: no upside) even though β stays 1.2; (b) conversely with β=1.0 a
  booking APPEARS where p90_ratio = 1.3 band evidence exists (insurance from
  evidence, not from the dial); (c) Z4: a pre-drain rejected at α=0.5
  passes when p10_ratio = 0.85 band evidence exists (stable day), and one
  accepted at α=1.0 fails when p10_ratio = 0.4 evidence exists (volatile
  day). (d) R8 bit-identity: same scenario with/without empty band series.
- **R13** Coordinator (tests/ha): attribute parsing incl. stale-cache reuse,
  garbage tolerance, partial coverage; `quantile_coverage` attr wiring.
- **R14 (docs/version)** ALGORITHM.md D-A4 v8 note + F-PREDRAIN.md §3
  cross-note (α/β are now fallbacks); CHANGELOG `[0.10.0]`; manifest +
  pyproject → 0.10.0.

## 4. Risks (state in code/spec where relevant)

- **Evidence quality**: early bins have exactly 5 days × ≥20 samples — bands
  will be coarse at first. Bounded by the clamps + the balcony module's own
  ring/eviction; self-improves. The coverage attr makes it observable.
- **Milder-than-α stress on stable days** is the INTENT (yield), but document
  for the operator that Z4 protection now varies with weather-class history.
- **Median=0 slots** never carry bands (D2) — dawn/dusk slivers keep scalars.

## 5. Non-goals

No placement-policy change (D6). No new config keys. No change to c1, pass-1,
saturation/gate-topup, executor, or simulate() (pv_scale plumbing exists). No
consumption-side quantiles. No changes to the balcony integration.

## 6. Verify

Full suite green (winshim), ruff check + `format --check` (0.15.21), goldens
byte-identical (R8). Live after deploy: `quantile_coverage` climbs over the
week; on a band-covered day the c2 reasons read "(p90)"; with β later set to
1.0 insurance appears only on evidence days.
