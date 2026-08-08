Three operator rules for early feed-in: a hard deadline, an honest manual
mode, and a quick way back to automatic.

**Hard deadline.** Until now the feed-in deadline was soft: whatever the
morning could not deliver was worked off *after* the configured hour, as
fast as the surplus allowed — deliberate export in the afternoon. No more:
slots starting at/after the deadline get no booking, and the leftover
simply exports naturally at midday (that energy was unavoidable export
anyway — only its timing is no longer pre-shifted).

**Manual mode is planned, not just tolerated.** When the operator grabs the
feed-in setpoint (manual mode), the plan so far kept showing the
*automatic* schedule nobody executed. Now today's remaining slots book
exactly the operator's current value — the SOC forecast and the card lane
reflect reality. A manual **0 W books nothing**, so no feed-in lane
appears. Tomorrow's slots plan automatically again: manual mode still ends
at midnight, and plan and chart already assume that. The executor stays
hands-off the whole time — this is forecast honesty, never actuation.

**Back to automatic with one toggle.** A rising edge on
`switch.…_early_feed_in` (off → on) ends manual mode immediately instead of
at midnight. The operator's last value is adopted as the baseline — not
re-judged as a fresh override — and the resumed automation re-anchors the
setpoint to the plan in the same refresh.

Under the hood: `FeedInParams.manual_w` (neutral default `None`, goldens
untouched), the manual verdict moved to the top of the update cycle so the
same cycle's plan already reflects it, and the options-flow help text now
says "hard deadline".

696 tests green (3 updated to the hard deadline, 4 new: manual today-only /
manual-0 in core, switch rising edge + manual planning in HA). Coverage
gates unchanged: planner core 100 %, integration total ≥ 95 %.
