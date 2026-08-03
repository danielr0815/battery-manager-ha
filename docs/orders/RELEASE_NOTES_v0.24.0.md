The surplus figures stop being pure forecast — the plant's own meters now tell you what actually happened today.

**The gap.** "Lost surplus" and "prevented export" were always planner
numbers: what the model expected, never what the meters saw. On exactly the
days that matter — a forecast miss, a load that never ran, a cloud band at
noon — the two drift apart, and nothing on the dashboard said which one you
were looking at. Worse, the raw export counter was lying about the plant:
the cellar dehumidifier hangs on a circuit the EM540 does not see, so every
watt it draws is booked as "export". The reported loss was systematically
too high, by exactly the amount the load actually used.

**Measured, and corrected.** Wire the export meter in the new options
section "Surplus accounting" and the integration starts keeping its own day
counters. Export minus the measured consumption of the loads fed past the
measuring point is the **true export** — a monotone counter the HA energy
dashboard can consume directly. The realized prevented export is what the
surplus loads really drew: measured where a load has its own kWh counter
(new optional field on the load), runtime × learned power where it does
not. Realized early feed-in is not re-measured at all — it is bridged from
the feed-in executor's own delivered-energy integral, which as of this
release survives a restart.

**Honest mixing.** Today's figures are measurement so far **plus** the
forecast for the rest of the day; tomorrow stays pure forecast; the
measured share is reported separately. The card's stats line shows both at
once — "verlorener Überschuss 2.3/1.1 (Ist 0.8)" — and replaces the old
forecast-only segments rather than showing the same day twice.

**Counters lie, so the deltas are guarded.** Negative steps and implausible
jumps are dropped (the Fritz!Powerline counter demonstrably jumps), but the
stored reading advances anyway — otherwise a single bad jump would poison
every later delta forever. The plausibility window is measured since the
reading last *changed*, not since the last read, so a coarse counter that
publishes in 0.1 kWh chunks is not mistaken for a glitch. A sensor dropout
re-learns its baseline instead of dumping the whole outage as one delta.
Day counters roll over at local midnight with the readings carried over,
and the whole block is persisted — a reload no longer resets your day.

Neutral by default: with no export meter configured, the data block, the
sensors and the card line are exactly what they were before. The planner
core is untouched, so the plan itself is bit-identical.

646 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
