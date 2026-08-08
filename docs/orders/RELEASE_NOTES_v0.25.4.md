"Spinning" is not "off" — appliance detection now survives the whole wash
cycle.

**What happened.** Hours after shipping the appliance card lanes, both
machines ran and neither showed up. Two different causes:

- **Waschmaschine (code):** its detection entity reports the run as phase
  strings — `running` → `rinsing` → `spinning` — and only `running` was in
  the integration's running-states set. The moment the machine moved to
  rinsing, the run was silently treated as finished: no planner reservation,
  empty card lane, although the drum was visibly still turning.
- **Geschirrspüler (configuration):** there was no appliance subentry for
  it at all — nothing to detect. Add one (Settings → Battery Manager →
  Waschmaschine-style appliance subentry) with
  `sensor.…_operation_state` as the detection entity.

**The fix.** `APPLIANCE_RUNNING_STATES` now also covers `rinsing`,
`spinning` and `drying`, so a run stays latched across its full cycle. The
same set feeds the consumption learner, which also stops mislearning those
hours as base load.

692 tests green (new: cycle-phase regression test replaying
running → rinsing → spinning → drying). Coverage gates unchanged: planner
core 100 %, integration total ≥ 95 %.
