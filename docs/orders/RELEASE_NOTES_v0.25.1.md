The morning after the audit, the Battery Manager's entities disappeared —
this is the emergency compatibility release for Home Assistant 2026.8.

**What happened.** HA 2026.8.0 restricts every device to a single config
subentry (core PR #175785 — a breaking change with no compatibility shim).
The Battery Manager had attached one shared device to the config entry and
every load subentry. On startup each platform registered its entities,
silently moved the device to its own scope, and Home Assistant deleted the
entity registry rows of the scope it had just left — platform after
platform, until only the last one's entities survived. The coordinator kept
planning and switching the whole time; every sensor, forecast and button
simply vanished from the state machine.

**The fix.** Each load subentry now gets its own device, linked to the main
Battery Manager device via `via_device_id` — the pattern Home Assistant
prescribes. A config entry migration moves existing entity registry rows
onto the per-subentry devices, and the rows the startup ping-pong already
deleted are resurrected on setup with their original entity ids, names,
areas and options. Dashboards and history survive.

**For everyone already on HA 2026.8:** update, restart — the entities come
back exactly as they were. The test environment now runs against HA 2026.8.0
itself, with a regression test that reproduces the live failure before
proving the fix.

688 tests green. Coverage gates unchanged: planner core 100 %, integration
total ≥ 95 %.
