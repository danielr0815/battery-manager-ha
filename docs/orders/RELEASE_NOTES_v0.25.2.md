The washing machine that kept "running" all night — appliance detection now
treats a long entity dropout as what it usually is: the device is off.

**What happened.** The Battery Manager latches an appliance run (washing
machine, dishwasher, …) while its detection entity reports power above the
threshold, and — to survive soak phases between heater bursts — held that
latch for as long as the entity stayed `unavailable`/`unknown`. But a
switched-off appliance takes its whole integration offline. On 2026-08-08 the
washer finished at 22:00; its detection entity went offline until 08:43 the
next morning. The stale latch therefore "ran" all night and hid a real
08:17 run until ~08:45 — in that window the planner booked early feed-in
(re-anchor pulses down to −1010 W) against a load it could not see.

**The fix.** The coordinator now times every detection dropout per appliance
(`APPLIANCE_DETECTION_MAX_DROPOUT_MIN = 30`). Below the limit nothing changes:
the latch rides through soak phases as before. Past it the device is assumed
off — latch and persisted start are dropped and no run is planned. When the
entity comes back reporting "running", a genuinely new run is anchored fresh
at that moment, so late detection can no longer masquerade as one endless
run.

**For the operator:** no configuration change needed. Runs shorter than the
30-minute dropout limit behave exactly as before; longer outages simply plan
no appliance energy — and the plan stops feeding in early the moment a real
run becomes visible again.

690 tests green (two new dropout-rule tests). Coverage gates unchanged:
planner core 100 %, integration total ≥ 95 %.
