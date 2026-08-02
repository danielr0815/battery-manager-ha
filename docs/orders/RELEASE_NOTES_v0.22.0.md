Every horizon day gets its own pre-drain block — the forecast now shows tomorrow's dehumidifier honestly.

**The surprise on the card.** Until now the pass-3 block was computed only
for slot-0's day: tomorrow's dehumidifier showed up on the SOC forecast
only from ~08:00, and the curve for tomorrow morning displayed a flat SOC
that could never happen — because the pre-drain that would drain it only
existed from midnight on. The plan was right; the forecast was not telling
the truth about it.

**Per-day blocks.** Now every horizon day with a clip gets its own block,
each confined to its own day (start >= its midnight — nothing ever drains
one day for the next; the cross-day night carve-out stays retired). Today's
block actuates exactly as before (slot 0 + the 3-plans/10-minute stability
gate); blocks for tomorrow and the day after are already in the plan as
display/plan, so the SOC curve shows tomorrow's drain toward the ~25 %
dynamic floor today already. On the clipping-eve golden the dehumidifier's
own tomorrow block cuts lost export from 6.29 to 3.94 kWh.

Everything else from v0.21.0 stands: the block's floor is the nominal
dynamic buffer (no alpha stress — the intra-day replan loop and the G4
floor guard cover forecast misses), powerstations keep their slot-wise
pass-2 bets, and the stability gate still decides every OFF->ON
actuation.

589 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
