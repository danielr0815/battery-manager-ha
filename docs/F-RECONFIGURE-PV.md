# F-RECONFIGURE-PV — base-entry reconfigure for SOC + PV forecast sources

Status: **binding spec**, folded into v0.9.1. Operator need (2026-07-11):
repoint the three PV forecast entities to the Balcony Solar Forecast
integration without deleting the entry (which would destroy the load
subentries' priorities, learned power and runtime counters).

## 1. Problem

`BatteryManagerConfigFlow` has no `async_step_reconfigure`
(`supports_reconfigure` reads false live), and the four base entities set in
`async_step_user` — `CONF_SOC_ENTITY`, `CONF_PV_FORECAST_TODAY/TOMORROW/
DAY_AFTER` — live in `entry.data`, which the options flow does not touch. So
the PV source cannot be changed through any UI path today.

## 2. Requirements

- **R1** Add `async_step_reconfigure(self, user_input=None)` to
  `BatteryManagerConfigFlow`. It shows the SAME four-entity schema as
  `async_step_user` (SOC + three PV forecast sensors), each field PRE-FILLED
  from the current `entry.data` via `suggested_value` (not `default`, so the
  operator sees the current pick and can change it).
- **R2** On submit it merges the four values into a COPY of the existing
  `entry.data` (all other keys — battery, control, support, DC, learned-nothing
  — preserved untouched) and finishes with
  `self.async_update_reload_and_abort(self._get_reconfigure_entry(),
  data=merged)`. The reload re-runs setup with the new PV entities; subentries
  (loads) are unaffected (they live in `entry.subentries`, not `entry.data`).
- **R3** No validation beyond "entity ids are present" (the entity selector
  already constrains to `sensor`); the PV mode auto/hourly/daily is untouched
  (a source without `wh_period` buckets falls back to the daily model exactly
  as today — F-PREDRAIN F1).
- **R4** `MINOR_VERSION` unchanged (no data-schema migration — same keys, same
  shapes). The base `async_step_user`/`async_step_battery` chain is untouched.
- **R5 (tests)** `tests/ha/test_config_flow.py`: a reconfigure flow started on
  an existing entry pre-fills the current SOC + PV entities; submitting new PV
  entities updates `entry.data` (asserted) while leaving the battery/control
  keys and the load subentries intact; `supports_reconfigure` is true after.

## 3. Non-goals

No change to the entities themselves, the planner, or the options flow. No new
config keys. Goldens untouched (no `core/*` change).

## 4. Verify / version

Bundled into v0.9.1 (with docs/F-PERDAY-SURPLUS.md). Full suite green
(winshim), ruff check + format (0.15.21), goldens byte-identical. CHANGELOG
[0.9.1] Added: "Reconfigure flow — change the SOC sensor and the three PV
forecast sources without re-adding the integration."
