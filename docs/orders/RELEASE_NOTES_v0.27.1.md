Manual Fossibot power calibration now survives the actor-state propagation
delay that follows a successful Home Assistant switch service call.

**Delayed Shelly confirmation no longer aborts the probe.** Version 0.27.0
issued the input and charge-gate ON commands, then inspected their entity
states exactly once. A blocking service call waits for its handler, but a
physical Shelly may publish the resulting state seconds later. The calibration
therefore reported “The load did not confirm charging active”, released the
input almost immediately and collected zero power samples. It now waits up to
30 seconds for both actors to publish ON before entering the separate Fossibot
wake-and-measure phase. A genuinely unresponsive actor still fails closed;
missing power telemetry continues to be tolerated within the existing
four-minute total cap, and failures preserve the previous learned value.

739 tests green. Total coverage remains at least 95 % and the planner core
remains at 100 %; Ruff formatting, Ruff linting, Mypy and the complete Home
Assistant test set are green.

**Full Changelog**:
https://github.com/danielr0815/battery-manager-ha/blob/main/CHANGELOG.md
