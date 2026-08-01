Pre-drain for continuous loads reworked — one committed block to today's SOC peak, and no more flicker micro-runs.

**A pointless 19-minute battery run.** On 2026-08-01 at 06:55 the planner
booked a marginal pre-dawn bet for the cellar dehumidifier and switched it
on; over the next ten minutes the booking flipped in and out of the plan
three times before vanishing for good. The run drew ~0.13 kWh from the
house battery for a clip predicted two days out — energy that would have
been refilled 1:1 by the same day's PV (round-trip losses for nothing).
The root cause was structural: pass 2 re-evaluated every 30-minute bet atom
every refresh, so a forecast sitting exactly on a gate threshold turned
into relay chatter and battery cycles instead of one committed run. And the
cross-day carve-out let loads discharge the battery at night for tomorrow's
surplus — exactly the "pull forward on a forecast" the operator's
as-late-as-possible rule forbids.

**One block, today only, stable before switching.** Pre-drain for
continuous loads (like the dehumidifier) is now planned as a single
contiguous block ending at today's SOC peak — the first slot today where
the plan would export without it. It covers at most today's actually lost
surplus, starts as late as the safety floors allow, and is validated as one
candidate through the unchanged gate stack: the battery must still fill to
max SOC today, never ride the inverter cutoff, and keep the
time-of-day-dependent reserve toward solar onset. Cross-day night
pre-drain for these loads is gone entirely; energy-limited powerstations
keep the proven slot-wise pass-2 machinery unchanged. On top, the executor
only switches a block after it has survived 3 identical plans plus a
10-minute wall-clock floor — the 06:55 flicker would never have actuated.
If a block disappears, normal dwell rules stop the run; the floor guard
still backstops everything. The block window and its stability progress
show up per load as a new `predrain_block` sensor attribute.

The deliberate trade: where the battery cannot absorb the day anyway, more
surplus is now left to export instead of cycling the battery on shifting
forecasts (golden scenarios: import down everywhere, battery minimum SOC
markedly higher at night). If you rely on deep overnight pre-drain for a
known-big tomorrow, that no longer happens — say the word and we can talk
about a bounded same-day variant.

580 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
