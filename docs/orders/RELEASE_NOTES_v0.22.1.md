Stale watchdog: the 89 % plateau moves into the edge band — and the band bounds are configurable.

On a strong-PV afternoon the house battery sat at a real, correct 89.0 %
with the battery idle (Victron calibrating at the top plateau) — and the
stale watchdog latched after just ~107 Wh of expected flow, because its
edge band only covered values above 89 %. The plan was fine; the boundary
was one SOC point too high. The high bound is now 88 % and both bounds are
inclusive, so 88.0 and 89.0 get the loose 7 % drift allowance (~45–60
minutes of unexplained flow instead of ~10) — a genuinely frozen BMS is
still caught, just after much more evidence. Both SOC bounds are now
options in the battery section (`house_soc_stale_edge_low_soc` default
13 %, `house_soc_stale_edge_high_soc` default 88 %), with an order
validation.

592 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
