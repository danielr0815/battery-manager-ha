Unavoidable midday export is pre-shifted into the morning — same kWh, better hour for the grid.

**The problem with noon.** On a good day the forecast already knows the
battery will be full by midday and the surplus loads cannot absorb what is
left: that energy is going to the grid no matter what. It leaves at exactly
the hour the grid needs it least, off a full battery, together with every
other roof in the neighbourhood.

**Shift the hour, not the amount.** The new `plan_feedin` planner pass takes
the day's residual export and books it into the MORNING instead: PV surplus is
passed straight through to the grid via the external controller's AC setpoint
while the battery idles. The battery is never discharged for feed-in — the
simulator serves booked feed-in from the slot surplus before charging, and
there is no path that feeds it from the pack in a deficit slot. Total export is
invariant; only its timing moves. The per-day target is the median residual,
spread towards a soft deadline (default 09:00, then worked off as fast as the
surplus allows, stopping when delivered or the battery is full), floored at a
configurable SOC and braked — never enlarged — by the P10 stress scenario,
trimmed latest-first.

**A setpoint executor that watches the battery.** The plan value is written to
your `input_number` (negative = export), and every battery-power update steers
it between planning cycles: downward immediately and unthrottled when the
battery starts discharging — a kettle the forecast never saw must not be paid
for out of the pack — upward throttled to one raise per minute and capped at
the remaining day target. If you set the setpoint by hand, the feature notices
and keeps its hands off until the next midnight; the mode is visible as its own
sensor. The floor guard and the stale-data shed force it to zero.

**Off by default.** The feature is opt-in: with the toggle off the plan is
bit-identical to before, which is exactly what the unchanged golden snapshots
verify.

626 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
