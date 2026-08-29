Storage cascades now wait for real actor confirmation and no longer resend
commands to actors that already report the requested state.

**Delayed Fossibot feedback no longer faults cascade startup.** The configured
actor-confirmation timeout previously bounded only the Home Assistant service
call. The cascade recorded its claim as soon as that call returned, even when
the Fossibot entity still exposed its previous state. A following plan pass
could therefore misclassify the delayed publication as an exclusive external
override and disable the cascade. State-reporting actors now receive their
claim only after Home Assistant confirms the requested state; assumed-state
actors retain their documented logical confirmation.

**Repeated Safe-OFF is idempotent.** During the live incident on 2026-08-29,
both Fossibot AC outputs alternated synchronously between ON and OFF about every
12 seconds because every inactive refresh resent `turn_off`. A confirmed target
state is now adopted without another service call, preventing the fault path
from creating an output-command loop.

743 tests green. Total coverage is 95.02 % and the planner core remains at
100 %; Ruff formatting, Ruff linting, Mypy and the complete Home Assistant test
set are green.

**Full Changelog**:
https://github.com/danielr0815/battery-manager-ha/blob/main/CHANGELOG.md
