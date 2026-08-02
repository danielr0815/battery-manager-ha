Pre-drain blocks now drain to the dynamic buffer floor — deeper morning blocks, more export prevented.

**The problem with the stress.** The pass-3 pre-drain block was capped by
the alpha stress gate: it evaluated the whole block window under a 30 % PV
downgrade and only allowed the drain that still held the reserve floor even
then. On a strong day that kept the battery trough at ~35 % instead of the
intended ~25 % — so the block fired late and shallow (e.g. 04:00 instead of
03:00), and export was lost that the dehumidifier could have eaten.

**Why the stress is no longer needed.** The intra-day replan loop already
covers the forecast-miss case: the block is recomputed every refresh, a
degraded PV forecast retracts the recommendation (the dehumidifier simply
turns off from ~08:00), the reduced solar power still fills the battery to
max, and the G4 floor guard force-switches at the real-time cutoff. The
static whole-window stress duplicated that protection at the price of a
shallow block every day. So the stress now applies only to the slot-wise
pass-2 bets of the powerstations; the block's floor is the nominal dynamic
buffer (crossover-ramped, evaluated on the nominal trajectory).

**Effect.** On strong days the dehumidifier starts earlier and drains to
~25 % by the first surplus (e.g. block 03:00–08:00, 2.1 kWh instead of
04:00–08:00, 1.7 kWh), then runs continuously through the surplus window
until the battery is full — noticeably more export prevented. The dynamic
buffer check now also runs at alpha 1.0 (it was skipped there before, so
blocks can be shallower in that configuration). Everything else — today-only
rule, stability gate, floors against the inverter cutoff, the battery
still having to reach soc_max — is unchanged.

589 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
