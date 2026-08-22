# F-FIXED-SUPPORT-ALLOCATION — fixed PSU schedules participate in surplus allocation

Status: implemented (v0.25.8).
Operator decisions 2026-08-22.

## 1. Incident

In the live plan for 2026-08-23 both Fossibots were already filled to their
configured targets, while roughly 0.9 kWh remained as forecast export. The
house-battery SOC nevertheless rose before the PV clip because the manually
enabled 48 V PSU was inserted only by `support_escalation`, after
`allocate_loads` had finished. The published SOC forecast therefore contained
extra headroom/export that the dehumidifier's allocation trials had never
seen.

## 2. Rules

**R1 — fixed means exogenous.** A manually forced 24 V or 48 V support path
(`dc24_forced_on` / `dc48_forced_on`) is known for the complete horizon. Its
schedule is passed into the allocation baseline and every candidate,
stress/optimism and feed-in simulation.

**R2 — automatic remains protective.** SOC-triggered emergency support is not
anticipated by the allocator. It stays in the later `support_escalation`
stage because it is a protective response selected by the plan, not a fixed
operator-owned input.

**R3 — no imported load energy.** The F-STRICT-SURPLUS absolute import gate
compares allocation baseline and trial with the same fixed schedules. A load
may consume support-created clip headroom only when its added import remains
within `IMPORT_ARTIFACT_SLACK_WH` (50 Wh over the horizon).

**R4 — diagnostics keep their meaning.** `import_trade_used_wh` compares the
real baseline/allocation pair with identical fixed support.
`prevented_export_by_day_wh` remains the established without-support
counterfactual by re-simulating the accepted load series without PSUs.

**R5 — physical 48 V gate.** `gate_soc_percent` defaults to 40 %. Below the
threshold the PSU may deliver; at or above it delivery is 0 W, modelling the
battery voltage exceeding the PSU output voltage. An explicit 100 % disables
this SOC proxy (always open). Migration v2.5 changes the legacy auto-persisted
100 % default to 40 % and preserves calibrated values below 100 %.

## 3. Regression anchors

- A reduced live topology (499 W + 498 W Fossibots, 421 W dehumidifier,
  manually fixed 48 V support) proves the continuous load receives the
  remaining clip window after both Fossibot budgets are satisfied.
- The final plan adds no more than the absolute 50 Wh modelling slack versus
  the same supported no-load baseline.
- Default-gate tests prove delivery below 40 % and 0 W at 40 % without PV;
  config migration tests prove legacy 100 → 40 and preserve a calibrated 55 %.
