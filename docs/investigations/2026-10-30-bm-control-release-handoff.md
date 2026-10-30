# v0.64.0 — BM control release handoff

BM control OFF immediately gives the operator input, charging gate and output.
Pending requests, timers and ownership are revoked without issuing a device
command. OFF and protected old bindings survive restart and reassignment.
Already-dispatched commands cannot be recalled and may still arrive. ON permits
fresh planning without claiming an input merely because it is already ON.
Cascade cleanup touches only previously claimed actors of other still-enabled
devices; no full physical isolation of a manually held chain is promised.

Independent Astra/High review and correction reviews are complete; all grouped
findings (3 P1 plus P2 terminal-state correction and queued-action follow-up) are
closed. Repeated correction reviews are not counted as new findings.

Frozen validation: 1,022 core tests / 100%; 3,248 full tests / 98.0262%; every HA
module >=95% (minimum sensor 95.1417%); branch 3,248 / 94.6161%; Ruff, format and
mypy; frontend bundle, lint and format, 89 Node and 45 browser tests passed.
Local evidence identifiers: `20261030T103105-03f1ac1841`,
`20261030T103811-85e2665712`, `20261030T103819-ac47afd766`.
The final documentation-only additions receive a separate static check before
publication; they do not claim a later source run has already finished.

Pilot 1/3 is accepted. The accompanying usage JSON records actual model/effort
per assigned agent, the declared scope, recovered reused-agent baselines and
cutoff. New precommit totals replace earlier checkpoint totals; they must not be
added together. Initial root exploration and prior separate analysis are
excluded, as is effort after the cutoff. Costs are explicit normalized
Standard/Short API comparisons; actual billing remains unknown. No historical
comparable basis supports an asserted saving or 50%/80% reduction.

Publication of v0.64.0 is authorized; Git operations are performed separately by
Sol/Low. Live deployment is not authorized or performed in this handoff.
