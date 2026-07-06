# Specification: Two-Bus DC Model & Voltage-Guided Support Paths (F-N3)

> Status: **implemented — F-N3 phases 0-7 complete as of v0.7.9/0.7.10.**
> Extends ALGORITHM.md D-A9/F-N1/F-N2 with the physically correct
> representation of the DC levels. Synthesis of three independent design
> drafts (physics / HA-UX / migration) + two jury reviews; target versions
> v0.7.0 … v0.7.6, each phase independently deployable and roll-back-able
> via configuration.

## 1. Operator Requirements (2026-07-05)

- **R1 — Voltage gate for the 48 V PSU:** output voltage fixed at 49.56 V.
  The ~60 W flow only as long as the battery voltage is BELOW the
  threshold; above it the PSU delivers nothing, even when switched on.
  Threshold configurable.
- **R2 — Manual 48 V mode = voltage controller:** in manual mode the
  48 V PSU should be actively switched on as soon as the battery voltage
  drops below the threshold (and off again above it).
- **R3 — Manual mode via switch:** in addition to external activation
  (F-N2), one switch of the integration per PSU.
- **R4 — Device parameters:** the 48 V PSU, 24 V PSU and DC/DC converter
  get configurable parameters: max current, output voltage
  (→ power caps = U × I), efficiencies.
- **R5 — Combination-dependent load flows:** (48 V + DC/DC),
  (48 V + 24 V), (24 V only), (DC/DC only) yield different
  AC/DC distributions that the simulation must each represent correctly.

## 2. Current Model and Its Known Errors

The core ([simulate.py](../custom_components/battery_manager/core/simulate.py))
knows **one** undivided DC load series (`slot.dc_wh`) as battery load via
`eta_discharge` — no bus separation, no DC/DC efficiency, no caps:

| Error | Effect |
|---|---|
| 24 V PSU shifts the ENTIRE DC load onto the grid (1:1) | native 48 V loads wrongly move with it; no η, no cap |
| ~~48 V PSU feeds a flat 60 W, without a voltage gate~~ (fixed v0.7.2) | winter forecast credits energy that never really flows |
| ~~`grid_import += psu_wh` ALWAYS (even with a full battery)~~ (fixed v0.7.6) | over-calculation of the import |
| Learning (Rev. 3) assumes "switch on == 60 W delivered" | over-corrects AC and DC in gate-closed hours |

**Live finding (2026-07-05):** at ~41 % SOC and −38 A load,
`sensor.victron_battery_voltage` read **48.66 V** (< 49.56 V!), while the
cells showed 3.24 V (≈ 51.9 V) — line/shunt drops under load.
Consequences: (a) the gate is **load-coupled**, not just SOC-dependent —
the PSU already contributes under winter load at medium SOC; (b) it must
be clarified at which measurement point the PSU actually compares (bus vs.
BMS terminal); (c) during charge phases the charger lifts the bus above the
threshold → gate closed, regardless of SOC.

## 3. Target Data Model (Core)

- New frozen dataclasses: `Psu48(output_voltage_v, max_current_a, eta)`,
  `Psu24(output_voltage_v, max_current_a, eta)`,
  `DcDc(eta, max_current_a)`. **Single-parameter gate:** the threshold IS
  the output voltage (no second field).
- **No HourSlot split:** the split of 24 V rail vs. native 48 V bus comes
  as a configured share `dc24_share` (default 100 % = today's behaviour)
  and is applied IN `step_hour` — the smallest blast radius (series.py,
  learning series, dynamic buffer remain untouched).
- **Fixed native-48 V base load** (`native48_base_w`, added v0.7.12): a
  constant load wired directly to the 48 V bus is carved off (in watts) BEFORE
  the `dc24_share` split, because a percentage cannot represent a fixed
  absolute load (it would scale with the total DC load). Default 0 W.
- `HourFlows` extended with: `psu48_delivered_wh`, `psu24_delivered_wh`,
  `dcdc_input_wh`, `dcdc_loss_wh`, `unserved_dc_wh`, `gate_open` —
  for hourly_details, debug export and card diagnostics.

## 4. Simulation Equations (one pass, two orthogonal dimensions)

Notation per slot: `dt`, `L24 = dc_wh × dc24_share`,
`L48 = dc_wh × (1 − dc24_share)`, caps `P48 = U48 × I48`,
`P24 = U24 × I24`, η24/η48/η_dcdc; battery η as before.

**Dimension 1 — rail source** (`dc24_from_grid`, semantics unchanged):

- *DC/DC (normal case):* `served24 = min(L24, P_dcdc·dt)`;
  `bus_draw24 = served24 / η_dcdc`; loss = difference;
  `unserved += L24 − served24` (rail brownout: must not occur in any
  accepted plan — warning path).
- *24 V PSU:* `served24 = min(L24, P24·dt)`;
  `grid_import += served24 / η24`; `bus_draw24 = 0`; a binding cap is
  made VISIBLE as `unserved`, not silently refilled from the battery
  (DC/DC is off in this combination).
- *Both on* (parallel operation, operator answer 8): **no decoupling,
  the source with the higher output voltage delivers** — the simulation
  compares `psu24_output_voltage_v` vs. `dcdc_output_voltage_v` and
  assigns the slot to the higher one (the other ~0 W). This also
  represents the 3 s make-before-break overlap brownout-free.

**Dimension 2 — 48 V bus:** `bus_load = L48 + bus_draw24`.
`gate_open = dc48_on AND soc_start < gate_soc AND no net charge slot`
(charger/PV charging lifts the bus above the threshold — Jury-Gap #1).

- PSU feed is netted **first directly** against the simultaneous bus load
  (without the battery detour — this fixes today's double-η error), the
  remainder charges the battery WITH `eta_charge`;
  **billing by DELIVERED energy**: `grid_import += delivered/η48`
  (full battery or closed gate ⇒ ~0 instead of today's 60 Wh).
- **Taper at the gate edge** (Jury-Gap #2): in the boundary slot
  `delivered ≤ energy up to gate_soc` — halves the worst-case slot error.
- Rest as before: battery draw via η_discharge down to floor,
  shortfall via the charger from the grid.

**Test net:** energy-conservation property per slot (inflows = outflows +
ΔStorage + losses) over EVERY trajectory of the suite; golden-plan
snapshots prove bit-equality under neutral defaults.

## 5. Voltage ↔ SOC (Gate Proxy)

- **Real-time (R2 controller):** direct voltage sensor `battery_voltage_entity`
  = `sensor.victron_battery_voltage` (BMS, 15s cell sum).
- **15s context (operator 2026-07-05):** battery = Pylontech US5000 =
  15 cells. 49.56 V / 15 = **3.304 V/cell** — just below the plateau
  rest voltage at medium SOC. Bus ≈ cell sum (live: 48.66–48.7 V ≈
  3.245 V × 15), so only a small line drop; the earlier "bus sags far
  below the cells" concern was a 16s calculation error.
- **Simulation (SOC grid):** configurable `gate_soc_percent` as a proxy.
  Calibration via a concurrent **14-day diagnostic** (SOC at observed
  threshold crossings, as an attribute of the mode sensor).
  **Explicitly calibrate in season** (Jury-Gap #3: LiFePO4 OCV and sag
  shift in winter, exactly when it matters).
- Limits documented: LiFePO4 flat curve, load sag (gate opens earlier
  under load than at rest — moderate at 15s), charging closes the gate.
  Sanity warning when `gate_soc ≤ soc_min + support_buffer` ("PSU can
  never help").

## 6. R2 Controller: voltage-guided manual 48 V mode

Small state machine in the coordinator (never in the core), active ONLY in
dc48 manual mode:

- **Hybrid trigger:** state listener on the voltage sensor + fallback per
  coordinator cycle (self-healing after restart/listener loss).
- **Asymmetric hysteresis + dwell** (one unnecessary ON is free — the
  device gates internally; a false OFF costs support): ON at
  `V ≤ 49.56 V` (output voltage), OFF at `V ≥ 49.8 V` (operator 9);
  both thresholds + dwell as **options fields** (not fixed). On dropping
  below, the controller mandatorily switches the PSU back on
  (operator 5).
- **Plausibility:** accept only 40–60 V; stale/unavailable ⇒ freeze;
  sensor invalid > 10 min ⇒ **fail-safe = ON** + warning.
- Actions run through `_switch_lock`, count against
  `min_switch_interval_s` (shared budget with the planner, D-A2) and are
  registered in `_last_support_cmd`/`_support_pending_confirm` — the
  F-N2 detector must never treat controller actions as "external". **A
  controller-caused PSU OFF therefore does NOT end manual mode** (resolves
  open question A; mode end only via the R3 switch, §7).
- **48 h log-only shakedown** before the first live activation
  (open question D).
- **Feature gate:** without a configured voltage sensor, dc48 manual
  stays exactly F-N2 hands-off — the existing tests remain valid.
- **24 V manual stays hands-off** (unchanged F-N2): there is no controller
  there, so "external OFF = mode end" still applies.

## 7. R3: Manual Switch

- Two new switch entities (`… Support 24 V manual` / `… 48 V manual`),
  always available (pattern: the vacation switch).
- **One** shared entry point `async_set_support_manual(key, on, source)`
  for the switch AND external detection; the source is persisted with it
  (display/diagnostics). External ON detection sets the switch too;
  external OFF ends the mode and resets it.
- Entry/exit of the 24 V mode via the make-before-break sequence.
- **Race rules** (Jury-Gap #5): never mutate the mode during a running
  N1a sequence (defer until sequence end); double toggle idempotent;
  display behaviour during the grace windows defined.

## 8. Configuration (base flow, support step)

New fields — ALL with behaviour-neutral defaults (η = 1.0, caps
unlimited, gate open, `dc24_share` = 100 %): the upgrade changes nothing
until the operator enters real values (rollback = clear the fields):

| Field | Default | Live value (operator) | Phase |
|---|---|---|---|
| `battery_voltage_entity` | — (feature gate) | `sensor.victron_battery_voltage` | 2 |
| `battery_cells_series` | 16 | **15** (Pylontech US5000) | 3 |
| `psu48_output_voltage_v` / `psu48_max_current_a` / `psu48_eta` | 49.56 / — / 1.0 | 49.56 / **1.15** / 0.89 | 2 |
| `psu24_output_voltage_v` / `psu24_max_current_a` / `psu24_eta` | — / — / 1.0 | **24.05** / **25** / 0.89 | 2 |
| `dcdc_output_voltage_v` / `dcdc_eta` / `dcdc_max_current_a` | 24 / 1.0 / — | **24.3** / **0.93** / **20** | 2 |
| `psu48_off_voltage_v` (controller OFF) / `psu48_on_voltage_v` (ON) | 49.8 / 49.56 | 49.8 / 49.56 | 5 |
| `gate_soc_percent` | 100 (= open) | calibrated (phase 3) | 3 |
| `dc24_share_percent` | 100 | estimate | 2 |

`rail24_voltage_entity` (optional): `sensor.victron_dcsystem_starter_voltage_229`
(×10 fix done) → dead-rail verification.

## 9. Learning (Cleaning Rules Rev. 4)

- `_psu48_series` is voltage-gated via **LTS hour min/max**
  (Jury-Gap #4): `max < U_thr` ⇒ full hour delivered;
  `min > U_thr` ⇒ nothing delivered; otherwise (clamp regime, PSU
  delivers exactly the bus load) ⇒ **exclude the hour instead of
  classifying it**.
- Optional AC-side measurement sensor for the 48 V PSU as a tier-1 source —
  with **deadband against standby poisoning** (lesson from v0.6.2).
- η-aware 24 V correction; `_CLEANING_RULES_VERSION = 4` +
  fingerprint ⇒ one-time full refetch.

## 10. Phase Plan (per phase: deployable, tests, live verification, rollback)

| Phase | Version | Content | Live verification |
|---|---|---|---|
| 0 ✓ | v0.6.5 | F-N2 committed (done) | 24 h soak of the override logic |
| 1 | v0.7.0 | Core: dataclasses, HourFlows, combination equations, gate wired but default-open; golden snapshots bit-exact | `export_hourly_details` before/after identical |
| 2 | v0.7.1 | Config flow + wiring + diagnostic columns | enter real nameplate values (leave gate open), sanity-check plan deltas |
| 3 ✓ | v0.7.2 | R1 gate live + calibration diagnostic | PSU manually on at high SOC ⇒ forecast credits NO 60 W; evening discharge against the Victron voltage graph |
| 4 ✓ | v0.7.3 | R3 switch + mode consolidation (one entry point) | toggle switch, mode sensors, rail never source-less, restart mid manual mode |
| 4b ✓ | v0.7.4/5 | Config dialogs grouped into collapsible sections (UX, no behaviour change) | check dialogs in the UI |
| 4c ✓ | v0.7.6 | **48 V direct netting live** (own golden diffs): PSU covers the bus load directly, the remainder charges the battery, billing = delivered/η, cap. Only `forced_dc48` changes, cost-neutral (import unchanged, SOC curve more physical) | winter evening: `psu48_delivered_wh` in hourly_details ~0 with a full battery, SOC gentler |
| 5 ✓ | v0.7.7 | **R2 voltage controller live** (asymmetric hysteresis ON ≤ 49.56 V / OFF ≥ 49.8 V, dwell 60 s/300 s, log-only flag default on, fail-safe = ON after > 10 min invalid, controller OFF NEVER ends manual mode [open question A]); off ≤ on validated in both flows + runtime guard; controller does NOT book onto `_last_support_switch` | 48 h log-only against Victron history, then live over one evening/morning cycle |
| 6 ✓ | v0.7.9 | **Learning Rev. 4 live** (LTS hour min/max gating of the 48 V attribution: `max < U_thr` ⇒ full, `min > U_thr` ⇒ nothing, otherwise clamp regime ⇒ hour excluded; `_CLEANING_RULES_VERSION = 4` + gate config in the fingerprint ⇒ one-time full relearn). An optional AC-side 48 V measurement sensor as a tier-1 source remains open (needs operator hardware). | relearn run, profile-export comparison, 14 d watchdog |
| 7 ✓ | v0.7.9 | **Card support lane** (24/48 V support as its own lane in the forecast card; flags compactly on the `soc_forecast` attribute) + documentation wrap-up | dashboard check |

**F-N3 complete** (v0.7.9): all phases 0–7 implemented, adversarially
reviewed, live-verifiable. Whole-plugin review (v0.7.8) + Rev. 4/card
(v0.7.9) are the current state.

## 11. Operator Decisions (2026-07-05) and Open Questions

**Answered:**

1. **Battery: Pylontech US5000 → 15s LiFePO4** (nominal 48 V, charge
   cut-off ~53.2 V, matches `victron_battery_info_maxchargevoltage`).
   Cell count/voltage window become **configurable** (default from the
   15s profile). This also resolves Q12 — see below.
2. **24 V rail voltage: fixed** — the ×10 scaling error was corrected
   locally, `sensor.victron_dcsystem_starter_voltage_229` now delivers
   the real ~24.3 V. Planned as the optional `rail24_voltage_entity`
   for dead-rail verification (plausibility ~20–29 V). Still only
   VOLTAGE measured (no current) → `dc24_share` stays a configured
   estimate.
3. **DC/DC converter:** max **20 A**, η **> 0.93** → `dcdc_max_current_a =
   20`, `dcdc_eta = 0.93` (cap ≈ 24 V × 20 A = 480 W rail-side).
4. **48 V PSU:** **Meanwell HGL-60H-54A**, max **1.15 A**, output set to
   49.56 V → `psu48_max_current_a = 1.15`,
   `psu48_output_voltage_v = 49.56` (cap ≈ 57 W). AC η ~0.89 (datasheet),
   configurable.
5. **R2 scope: yes** — external ON activates the voltage controller; it
   may switch off above the threshold AND **must switch back on
   automatically when dropping below it**. → The 48 V manual mode is a
   controlled standby, not hands-off (see §6, distinguishing it from the
   24 V logic). ⚠️ Interaction with (6) needs clarification — open
   question A.
6. **Exit: ok** — external OFF ends manual mode (back to automatic).
   ⚠️ Collides with (5) in the controlled 48 V mode — open question A.
8. **Parallel 24 V sources: no decoupling — the source with the HIGHER
   output voltage delivers** (the other ~0 W). Replaces the previous
   "PSU priority" assumption: when both are active the simulation
   compares `psu24_output_voltage_v` vs. `dcdc_output_voltage_v`; the
   higher one supplies the rail. Side effect: make-before-break is thereby
   physically brownout-free (the higher source carries during the
   overlap).
9. **Controller OFF threshold: 49.8 V** (instead of 50.06 V). ON
   threshold at ≤ 49.56 V (output voltage); both thresholds + dwell as
   options fields.
11. **PSU standby consumption: neglect** (documented).
12. **RESOLVED (calculation error on my part):** my 51.9 V was based on a
    16s assumption. With **15s**, 3.24 V/cell × 15 = 48.6 V ≈ the
    measured bus voltage 48.66–48.7 V — bus and cell sum thus practically
    agree, there is NO large measurement-point discrepancy. The concern
    "gate strongly load-coupled/bus sags far below the cells" largely
    falls away; the threshold 49.56 V simply lies just below the plateau
    rest voltage. Voltage entity: `victron_battery_voltage` (BMS)
    preferred.

**Open questions — all decided (2026-07-05):**

- **A — controller OFF vs. user OFF: OK (confirmed).** In the controlled
  48 V mode the **R3 switch "48 V manual" is the sole mode truth**; an
  external physical ON starts the mode and sets the switch too; it is
  ended only via the R3 switch. A controller-caused PSU OFF does not end
  the mode. The pure F-N2 hands-off logic (external OFF = mode end)
  remains only for the 24 V PSU.
- **B — 24 V support PSU: voltage 24.05 V, max current 25 A** →
  `psu24_output_voltage_v = 24.05`, `psu24_max_current_a = 25`
  (cap ≈ 601 W). **DC/DC output really 24.3 V** (confirmed) > PSU 24.05 V:
  by operator rule 8 the **DC/DC converter therefore has priority** in the
  parallel case. Consequence: the grid-fed 24 V PSU supplies the rail only
  when the DC/DC is OFF — matches exactly the existing make-before-break
  semantics (`dc24_from_grid` ⇔ DC/DC off, PSU on).
- **C — rail overload: only warn** (default adopted): if the 24 V load
  exceeds the cap of the active source, `unserved_dc_wh` is carried as a
  warning, no shortage is computed through (practically unreachable at
  480/601 W caps).
- **D — 48 h log-only shakedown: yes** (default adopted): the controller
  runs in phase 5 first for 48 h only logging, before it switches live.

## 12. Main Risks

- **Gate proxy error** (flat curve + sag + season): hence calibration diagnostic, in-season calibration, taper, and the learning classifies via real voltage LTS instead of via the proxy.
- **Controller chatter** at the threshold: asymmetric hysteresis + dwell + log-only shakedown.
- **Regressions** in freshly stabilized planner behaviour (v0.6.1–v0.6.5): golden snapshots + behaviour-neutral defaults + one phase per version with live soak.
