Two corrections make the 48 V winter-support forecast physical and let the
surplus allocator act on the SOC trajectory it actually publishes.

**Fixed support now participates in load allocation.** In the live plan for
2026-08-23 both Fossibots reached their configured targets, yet roughly
0.9 kWh remained as export and the dehumidifier was not brought forward. The
manually enabled 48 V PSU was the reason the house battery could rise before
the PV clip, but that support was injected only after load allocation. The
published forecast therefore contained export headroom the allocator had never
seen. Manually forced 24/48 V schedules now accompany the allocation baseline,
every candidate and stress simulation, and early feed-in planning. Automatic
emergency support remains the later protective stage. The strict-surplus rule
is unchanged: loads may use the newly visible headroom only without causing
more than the absolute 50 Wh modelling slack in added import.

**The 48 V PSU SOC gate now defaults to 40 %.** Below that threshold the PSU
may deliver; at or above it the simulation delivers 0 W, modelling the battery
voltage overtaking the PSU output voltage. Thus a battery already at 88 % can
no longer be lifted by the PSU merely because the old simulation kept its gate
open. Explicit 100 % still means “gate disabled / always open”. Config-entry
migration v2.5 moves the legacy auto-persisted 100 % default to 40 % while
preserving calibrated values below 100 %.

703 tests green. The planner core remains at 100 % coverage; Ruff formatting,
Ruff linting, Mypy, Golden snapshots and the complete Home Assistant test set
are green.

**Full Changelog**:
https://github.com/danielr0815/battery-manager-ha/blob/main/CHANGELOG.md
