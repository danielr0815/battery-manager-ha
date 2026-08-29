Shared actors in a storage cascade can now follow the Battery Manager's own
planned state transitions without disabling the cascade.

**A manager-owned claim change is no longer mistaken for an external
override.** After Safe-OFF, the cascade had recorded an OFF claim for the root
input. When the next Root-fed slot intentionally requested ON, the shared-mode
guard compared that new target with the previous claim instead of comparing
the physical actor state with the claim. It therefore entered Hands-off,
reported `root_transition_failed` and switched the automation back off. Shared
ownership is now evaluated only where external deviations are detected. The
manager may reverse its own claim as the plan changes, while a real external
change still relinquishes all claims and enters Hands-off without rollback.

740 tests green. Total coverage is 95.11 % and the planner core remains at
100 %; Ruff formatting, Ruff linting, Mypy and the complete Home Assistant test
set are green.

**Full Changelog**:
https://github.com/danielr0815/battery-manager-ha/blob/main/CHANGELOG.md
