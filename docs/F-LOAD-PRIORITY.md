# F-LOAD-PRIORITY — explicit, configurable load priority

Status: **binding spec** for v0.8.2. Author: planning session 2026-07-10.
Operator request (2026-07-10): *the priority should be configurable; initially
it may match the creation order, but every load must be assignable a different
priority, and the priorities of the other loads shift accordingly.*

## 1. Background

Load priority is positional today: the planner treats the order of
`SystemConfig.loads` as priority (docs/ALGORITHM.md D-A4, `model.py` SurplusLoad
docstring), and the coordinator builds that list by iterating
`entry.subentries.items()` in insertion order — i.e. priority == creation
order, changeable only by deleting and re-creating loads (which destroys
subentry ids, runtime counters, BM-control switch state).

## 2. Goals / non-goals

**Goals.** A per-load integer priority (1 = highest), editable in the load
subentry dialog, with **insert-shift semantics**: assigning load X priority P
moves X to position P and renumbers all loads densely (1..N) so every other
load shifts accordingly. Default/initial order = creation order. The planner
core stays untouched — priority materialises purely as the ORDER of
`SystemConfig.loads`.

**Non-goals.** No core/model change (no `priority` field on `SurplusLoad` —
order carries it). No golden changes (all-legacy state must sort exactly like
today). No priority for appliances or PSUs. No extra sensor exposure (the
`loads` attribute of the SOC-forecast sensor already lists loads in
`config.loads` order, which now IS priority order — document, don't add).

## 3. Requirements (testable)

- **R1 (storage)** New subentry-data key `CONF_LOAD_PRIORITY = "priority"`
  (int ≥ 1) in `const.py`. Not part of `DEFAULT_LOAD_CONFIG` fallbacks the
  coordinator reads per-load — ordering is resolved centrally (R3).
- **R2 (form)** The basic step of `SurplusLoadSubentryFlow` gets a required
  priority number field (`_number(1, N, 1)` style, step 1) where `N` = number
  of load subentries incl. a new one being created. Default: the load's
  CURRENT effective position (reconfigure) or `N` (create → lowest priority,
  matching today's "new loads append"). Position of the field: directly after
  the name, before the power fields.
- **R3 (effective order, single source of truth)** A helper (coordinator or a
  small shared function) computes the ordered load-subentry list:
  sort key `(stored_priority if present else insertion_position_among_loads + 1,
  insertion_position_among_loads)`. With NO stored priorities anywhere this is
  exactly today's insertion order (regression anchor). `_build_config` uses it
  to order `SystemConfig.loads`; nothing else changes.
- **R4 (insert-shift renumber)** On `_finish` of the load flow with priority P:
  build the effective order per R3 EXCLUDING the edited load, clamp P to
  [1, len+1], insert the edited load at position P, then renumber ALL loads
  densely 1..N. Sibling subentries whose EFFECTIVE priority (stored value, else
  the R3 insertion fallback) differs from their new dense value are updated via
  `hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, priority: new})`
  — data only, title untouched. The edited load's own priority is written
  through its normal `async_update_and_abort`/`async_create_entry` data.
  Only WRITE siblings that actually change (each update triggers an entry
  reload via the update listener — keep the churn minimal; the flow's own
  update/create triggers the final reload that picks everything up).
- **R5 (create path)** A newly created load with the default (P = N) must
  leave every sibling untouched (no writes, no extra reloads) and behave
  exactly like today's append semantics.
- **R6 (idempotence)** Reconfiguring a load without changing its priority
  field renumbers nothing (dense values already match → zero sibling writes).
- **R7 (legacy mix)** Loads created before this feature have no stored key and
  sort by insertion position (R3 pseudo-priority). The first ORDER-CHANGING
  save densifies all stored values (R4 renumber covers every load); a
  create-at-default or untouched re-save stays write-free by design (R5/R6)
  and leaves keyless siblings keyless. Until then, a mixed state (some stored,
  some not) must still order correctly per the R3 key: stored and fallback
  values compete equally, ties broken by insertion.
- **R8 (order-consumer audit)** Verify every consumer that pairs
  `config.loads` / `result.load_plans` with subentry iteration order is either
  id-keyed or derives from `config.loads` itself. Known pairing:
  `coordinator.py` `zip(result.load_plans, config.loads, strict=True)`
  (~line 841) — safe because `load_plans` is built from `config.loads` in
  order. `_read_load_states` & runtime counters & switch/binary_sensor
  platforms are keyed by `load_id`/subentry id — confirm, and fix any place
  that silently assumes insertion order == config.loads order.
- **R9 (translations)** `translations/de.json` + `en.json` (and any other
  languages present with the load step) get the field label + description:
  de "Priorität" / "1 = höchste. Andere Lasten rücken entsprechend auf."
  en "Priority" / "1 = highest. Other loads shift accordingly."
  Also update the subentry description sentence "Priority follows the order of
  creation." → "Initial priority follows the creation order; it can be changed
  per load (other loads shift)."
- **R10 (docs/version)** ALGORITHM.md D-A4 saturation/priority wording +
  model.py SurplusLoad docstring + optimize.py allocate_loads docstring:
  "config order = priority" stays true mechanically; add "(order = the
  configured per-load priority since v0.8.2, default creation order,
  F-LOAD-PRIORITY)". CHANGELOG `[0.8.2]` (Added). `manifest.json` 0.8.2.

## 4. Test plan

`tests/ha/test_config_flow.py`:
- create-with-default leaves siblings untouched (R5): 2 existing loads, add a
  third with default priority → third stored priority 3, siblings unwritten
  (assert their `.data` object unchanged / no priority key added).
- insert-shift on reconfigure (R4): loads A,B,C (legacy, no keys) → set C
  priority 1 → stored: C=1, A=2, B=3.
- clamp: priority input > N clamps to last position; idempotent re-save (R6)
  performs zero sibling updates (spy/count `async_update_subentry` calls or
  compare data identity).
- create at position 1 shifts all (R4 create path).

`tests/ha/test_coordinator.py`:
- R3 ordering: legacy-only → insertion order (regression); explicit priorities
  reorder `SystemConfig.loads`; mixed stored/absent (R7) orders stored-wins,
  insertion tiebreak.
- planner effect smoke test: two loads with inverted priority produce
  `config.loads` in inverted order (assert `[ld.load_id for ld in config.loads]`).

Core tests/goldens: MUST be untouched (no core change). Full suite green on
`.venv314` (winshim), ruff clean.

## 5. Decision log

- **D1** Order-as-priority (no core field): the planner already implements
  priority as iteration order; a core field would duplicate truth.
- **D2** Dense renumber-on-save (not sparse floats): matches the operator's
  shift semantics literally, keeps the selector range honest (1..N), and makes
  legacy migration a side effect of the first save.
- **D3** Sibling writes from within the subentry flow via
  `async_update_subentry` (available in the target HA version, verified
  2026-07-10): each write reloads the entry; writes are minimised (R4/R6) and
  the action is operator-triggered and rare.
