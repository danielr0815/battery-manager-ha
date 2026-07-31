House-SOC stale watchdog reworked — no more false alarms at the battery's SOC plateaus.

**False stale alarms at the SOC ends.** Shortly after the v0.17.0 rollout the
new house-battery SOC watchdog fired repeatedly on a healthy system (10
latches in 90 minutes): the battery's BMS legitimately pins its SOC reading
to one value for a long time near the ends of the range (around 90 % and
10 %, balancing/clamping), but the watchdog's adaptive window — "the time a
1 % SOC change takes at the expected power" — shrinks to a few minutes at
high power, so it latched the reading as frozen and escalated into
data-loss errors while everything was fine.

**Energy-based, banded, configurable.** The watchdog now accumulates the
*expected battery throughput* of the last valid plan while the SOC reading
stays exactly the same, and latches only once that expectation exceeds a
configurable drift allowance: `house_soc_stale_mid_percent` (default 2 % of
capacity) in the 13–89 % mid band, and `house_soc_stale_edge_percent`
(default 7 %) at the plateau-prone ends below 13 % or above 89 % — both
editable in the options flow (battery section). Below 300 W of expected
flow the accumulation pauses instead of resetting (standby is no evidence
either way), so duty-cycled flow still counts; any changed reading releases
the latch as before. A genuinely frozen BMS is still caught — it just has
to stay unexplained for much longer — and the fail-safe load shed after 2 h
of sustained data loss is unchanged.

563 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
