Surplus-load planning can now expose and deliberately refresh the exact power
value on which its energy schedule is based.

**The planning power is visible.** Every surplus load now has a dedicated Watt
sensor, and the same value appears next to the load in the SOC forecast card.
Its attributes identify whether the planner used live power, a learned value,
the configured default or a saturation limit. This makes stale Fossibot power
assumptions visible instead of hiding them behind an apparently precise energy
schedule.

**Energy-limited loads can be calibrated on demand.** Eligible directly
controlled loads with a power sensor receive a “redetermine charging power”
button, which is also callable through `button.press`. The probe may draw grid
power intentionally, bypasses the normal plan for at most four minutes and
starts measuring after a 20 % power jump or a 60-second settling timeout. It
accepts only newly published sensor readings, samples no faster than every five
seconds and stores their median once four readings or 90 seconds of measurement
are available. The coordinator then replans immediately with the new value.

**Failure remains non-destructive.** Pressing the button again aborts the run.
Unavailable actors or sensors, timeouts, reloads and other failures preserve
the previous learned value and safely release or switch off the temporary
activation. A persisted marker also lets startup clean up an interrupted run.

738 tests green. Total coverage is 95.01 % and the planner core remains at
100 %; Ruff formatting, Ruff linting, Mypy and the complete Home Assistant test
set are green.

**Full Changelog**:
https://github.com/danielr0815/battery-manager-ha/blob/main/CHANGELOG.md
