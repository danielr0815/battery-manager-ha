The early feed-in stops letting the battery pay for the export — and the card finally says how much, and when.

**The battery was never actually idling.** The feature promises the battery
sits at 0 A during early feed-in: nothing in, nothing out, only PV surplus
passing through to the grid. On the plant it did not. In the 07:00 hour of a
sunny Tuesday the plan booked 439 Wh of feed-in, exported 424 Wh of it, and
the battery *discharged* 76 Wh at the same time — its SOC fell from 31.3 % to
29.8 %, straight through the feature's own 30 % floor.

Two omissions in one line of arithmetic caused it. The planner booked against
the raw AC surplus, `pv − ac − extra_ac`, but that is not what the simulation
can give away. The inverter standby is part of the AC draw the simulation
subtracts, so 15 Wh of every booked hour was power that does not exist — the
setpoint asked for it and was silently clamped. And the 48 V bus load is
settled *before* the AC balance, out of the store: unless the same hour
charges it back, the battery covers the bus while the entire surplus goes to
the grid.

The booking now reserves both — the standby, and the AC energy the charger
needs to put the bus draw back, taken from the slot's own simulated discharge
so it stays correct when a grid PSU covers part of the load. Measured across
sunny and overcast scenarios, the worst net battery balance in any feed-in
hour is now exactly 0 Wh, where it used to go negative. The day's feed-in
shrinks by that reserve; in exchange the SOC forecast tells the truth, the
setpoint is physically deliverable, and the floor holds without the live trim
having to repair it after the fact — which it could only do outside its ±50 W
deadband anyway.

**The floor breach fixed itself.** The 30 % minimum is only tested at the
start of an hour, so a booking could push the SOC below it inside that hour.
That was the same root cause, and it no longer happens.

**And the card answers "how much, and when?" without leaving it.** The legend
gave every surplus load its today/tomorrow energy but showed the early-feed-in
and grid-support lanes as a bare dot and a name; all three now carry the same
figure. The hover readout named the active lanes but never quantified them —
it now shows the energy of the hovered hour per lane, with the feed-in derived
from its planned power times the actual slot length (slot 0 is a partial hour
and is treated as one), and the loads from a new per-slot value that splits a
booking by the run hours it really committed rather than dividing it evenly.

Feed-in switched off changes nothing: the golden snapshots are untouched.

662 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
