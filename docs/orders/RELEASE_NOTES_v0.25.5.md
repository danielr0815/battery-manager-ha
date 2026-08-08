A second card joins the bundle: the Battery Manager now draws the planned
consumption, not just the planned SOC.

**The new card.** *Battery Manager Consumption* (same auto-registered
module — it simply appears in the card picker) renders the planned
consumption per hour as stacked bars, split by voltage level:

- **230 V AC** — the learned/static base profile plus detected appliance
  runs (the washing machine is finally visible *inside* the consumption),
- **48 V** and **24 V** — the DC total split exactly like the kernel does
  (fixed native-48 V base first, then `dc24_share` of the remainder),
- **planned loads** — the surplus loads the planner booked (Fossibot
  charging, dehumidifier, …) as their own, clearly separated top layer,

with a **total line** across the bar tops. The legend shows per-day kWh
(today/tomorrow) per level; hover or the arrow keys walk the slots with
per-level watts.

**Honesty cue.** Bars whose consumption profile fell back to the static
config values render dimmed — a learning gap is visible at a glance
instead of being silently trusted (the per-slot origin rides along as
`src` "L/S" per path).

**Caveat.** The 48 V / 24 V split is the configured approximation
(`native48_base_w`, `dc24_share`) — there is no separate 24 V measurement
on the plant. A learned 24 V series would be a follow-up (it fits the
consumption-profiles strategy, docs/F-CONSUMPTION-PROFILES.md).

**For custom dashboards:** the series is the new `consumption_forecast`
attribute on the SOC forecast sensor
(`{t, ac_w, dc48_w, dc24_w, loads_w, src}` per slot, W as hourly means).

693 tests green (attribute contract + default-split assertions). Coverage
gates unchanged: planner core 100 %, integration total ≥ 95 %.
