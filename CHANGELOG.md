# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/0.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.16] - 2026-07-09

### Changed
- **Any surplus load can now be switched directly by the integration, not only
  energy-limited storage.** The **control switch** and its **off policy** moved
  from the (energy-limited-only) storage page to the load's **basic settings**,
  so a continuous consumer — a dehumidifier, heater, … — can be assigned a switch
  and is driven directly by BM. This is what the F-SUBHOUR sub-hour executor
  needs to actively switch such a load; without it a continuous load could only
  be recommendation-only. The **charge-enable** gate stays on the storage page
  (meaningful only for a powerstation). No data migration — existing loads keep
  their control switch; the `keep_on`-needs-charge-enable rule is now validated
  across both steps.

## [0.7.15] - 2026-07-09

### Added
- **Sub-hour surplus-load allocation (F-SUBHOUR).** A non-energy-limited load
  (dehumidifier, heater) can now be scheduled for a **sub-hour run** quantised to
  its `min_runtime_min`, instead of only whole hours. This captures a small
  surplus the battery buffers within the hour at ~net-zero cost (e.g. a 400 W
  load 30 min to soak a ~200 Wh surplus). The planner searches quantised
  durations largest-first (the whole-slot booking stays the regression anchor,
  so full-hour plans are unchanged); **energy-limited powerstations keep their
  target-SOC behaviour**, untouched. The executor (approach A) freezes the
  planned contiguous run on the ON edge and **actively switches the load off**
  at `run_start + max(min_runtime, run)` via a one-shot timer, so the booked
  partial-hour energy is delivered exactly (no over-run). See
  docs/F-SUBHOUR-ALLOCATION.md.
- **Separate minimum OFF time (`min_off_min`) per load.** `min_runtime_min` is
  now the minimum ON time (ON→OFF dwell); the new `min_off_min` is the minimum
  OFF time (OFF→ON dwell), protecting compressor loads (dehumidifier, fridge)
  from short-cycling when a surplus keeps pulsing. Back-compatible: an existing
  load without the key keeps its symmetric dwell (`min_off = min_runtime`).

### Changed
- **Appliance detection hardened.** A detected run now uses **hysteresis**: it
  stays latched until the power drops below a new `off_threshold_w` (default
  below the running threshold), so a brief dip — e.g. a dishwasher soak between
  heater bursts — no longer resets the run clock and re-injects the full run
  energy; a sensor dropout holds the last state. The run start is now
  **persisted across restarts**, so a restart mid-run keeps the real elapsed
  time instead of re-injecting the full run energy.

## [0.7.14] - 2026-07-08

### Changed
- **Forecast card: the hover readout now names the active lanes.** Moving the
  crosshair still shows the slot's time and planned SOC, and now also lists which
  surplus loads and grid-support paths (24 V / 48 V) are switched on in that hour,
  each with its colour dot matching the lanes below the plot. A slot with nothing
  running shows just time and SOC as before.

## [0.7.13] - 2026-07-06

### Added
- **Configurable grid-support escalation thresholds (D-A9).** The SOC levels at
  which the emergency grid PSUs switch were hard-coded (24 V on below
  `soc_min + buffer`, off at `+1 %`; 48 V on below `soc_min + 0.5 %`, off at the
  buffer floor). They are now four **absolute** battery-SOC thresholds, one
  hysteresis loop per stage, fully independent of the planning buffer:
  - `support_dc24_activate_soc` (default 10 %) — SOC at/below which the 24 V PSU
    takes the DC rail from the DC/DC converter.
  - `support_dc24_recovery_soc` (default 11 %) — SOC at/above which the 24 V
    support switches back off.
  - `support_dc48_activate_soc` (default 5.5 %) — SOC at/below which the
    last-resort 48 V PSU also engages.
  - `support_dc48_recovery_soc` (default 10 %) — SOC at/above which the 48 V
    support switches back off.

  Widening the gap between a stage's activate and recovery SOC latches its
  grid support on longer, so a battery parked on a threshold overnight holds
  steadily on grid instead of chattering across it each cycle (the cause of the
  observed overnight 24 V PSU flapping). Cross-field validation enforces a sane
  ladder (`activate < recovery` per stage; the 48 V stage at/below the 24 V
  stage). The neutral defaults reproduce the previous hard-coded behaviour
  bit-for-bit at the default battery config (golden identical), and a config
  entry migration (minor version 2 → 3) backfills the exact legacy
  soc_min-derived thresholds for existing entries, so the upgrade never moves
  the switch points even when the battery minimum SOC is not the default 5 %.
  Making the thresholds absolute removes the previous coupling of the 24 V
  activation to `soc_buffer_percent` (the internal `support_buffer_percent`
  parameter is gone).

## [0.7.12] - 2026-07-06

### Added
- **Fixed native-48 V base load (`native48_base_w`).** A constant load wired
  directly to the 48 V bus (e.g. ~35 W) can now be given as absolute watts,
  carved off the DC load BEFORE the `dc24_share` rail split — a percentage
  share could not represent it (it would scale with the total DC load). The
  remainder is split by `dc24_share` as before. Default 0 W → behaviour
  unchanged (golden snapshots identical).
- **Forecast card: off-window loads are labelled with their start time.** A
  surplus load scheduled beyond the shown horizon (e.g. a 3-day plan with the
  card's default `hours: 48`) previously showed planned energy in the legend
  but no lane block, which looked contradictory. The legend now appends when
  the run starts (e.g. "Wed 12:00").

## [0.7.11] - 2026-07-06

### Fixed
- **Device `sw_version` no longer drifts.** The version was hard-coded in
  `const.py` (stuck at 0.6.0) and in the dashboard card, so every device
  reported the wrong firmware. Both are now derived from `manifest.json` at
  runtime (the coordinator reads the integration version; the card reads its
  own `?v=` cache-bust param). A CI check now fails the build if
  `manifest.json` and `pyproject.toml` versions diverge.

### Documentation
- **All documentation is now in English**, and the design docs' stale
  "draft / awaiting feedback" headers were corrected to their shipped status.
  The `docs/*.md` design records were translated faithfully (technical content,
  decision codes, and tables preserved); references to deleted files
  (`controller.py`, `energy_flow.py`, `standalone_test/`) were removed or
  marked historical.
- **New [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the developer
  onboarding entry point: the core-vs-HA code map, the coordinator update
  cycle, the decision-code glossary, and a recommended reading order. Linked
  from the README (new documentation index) and CONTRIBUTING.
- **CONTRIBUTING.md rewritten** to the real workflow (ruff, the split
  core/HA test suites with the `-p no:homeassistant` flag, golden snapshots via
  the new `scripts/gen_golden.py`, versioning). **SECURITY.md** trimmed to what
  the project actually offers (GitHub Security Advisories; no placeholder email
  or fictional CVSS/scanning pipeline). **info.md** (HACS store page) and the
  issue/PR templates rewritten in English with real commands.

### Added / repo hygiene
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `.github/dependabot.yml`
  (GitHub Actions), `.editorconfig`, a `[project]` table + dev dependency group
  in `pyproject.toml`, `scripts/gen_golden.py`. CI now also runs
  `ruff format --check`. Removed stale root docs (`PROJECT_COMPLETE.md`,
  `STARTUP_OPTIMIZATION.md`, `HACS_INSTALLATION.md`) and the frozen
  `docs/IMPLEMENTATION_PLAN.md` (superseded by ARCHITECTURE.md).

## [0.7.10] - 2026-07-06

### Fixed
- **The R2 controller diagnostic is now actually exposed.** A live check of
  v0.7.9 found that `support_dc48_controller` (active/mode/decision/reason/
  voltage) was written to the coordinator data but surfaced on no entity —
  the earlier claim that it lived on the SOC-forecast sensor was wrong. It is
  now an attribute of the **48 V support-mode sensor**
  (`…_48_v_support_mode`), so the log-only shakedown and live regulation are
  observable in the UI.

### Verified live (v0.7.9 on the operator instance)
- v0.7.9 running; the Rev. 4 one-time full relearn fired on the upgrade; the
  LTS min/max fetch runs with no statistics errors; the forecast card
  resource registered/updated. No errors from the new code (only a transient
  SOC-unavailable at restart).

## [0.7.9] - 2026-07-06

### Changed — F-N3 phase 6: consumption learning Rev. 4 (docs/DC_TOPOLOGY.md §9)
- **The 48 V PSU's energy is now attributed to the learned AC/DC profile by
  the hour's LTS min/max battery voltage instead of a mean proxy.** For a
  PSU-on hour: `max < U_thr` → full nameplate delivered; `min > U_thr` →
  nothing delivered (the bus stayed above the PSU output all hour); the
  ambiguous clamp regime in between (the PSU delivered exactly the bus load)
  is **excluded from learning** rather than mis-classified. Hours where the
  switch was off, or with no voltage signal, keep the flat approximation.
  This replaces the v0.7.8 mean-voltage proxy with the physically correct
  min/max gate. `_CLEANING_RULES_VERSION` bumps to 4 and the gate config
  (voltage entity + threshold) joins the cleaning fingerprint, so the change
  triggers a **one-time full-window relearn**.

### Added — F-N3 phase 7: forecast-card grid-support lane
- The bundled forecast card now draws a **24 V / 48 V grid-support lane**
  below the SOC chart, marking the hours the plan engages each support PSU.
  The SOC-forecast sensor's `forecast` attribute carries compact per-hour
  `dc24`/`dc48` flags (emitted only when active) for the card to render.
- F-N3 is complete: all phases 0–7 implemented, reviewed, and deployable.

## [0.7.8] - 2026-07-06

### Fixed — whole-plugin review (16 findings across 10 subsystems)
A comprehensive multi-agent review of the entire plugin surfaced 16
confirmed defects (3 high, 5 medium, 8 low); all are fixed here, each
with a regression test. Grouped by shared root cause:

**Energy-balance core (`core/simulate.py`).** step_hour now computes the
AC/PV balance once and settles the residual DC-bus load against the
same-slot PV surplus, ending a **phantom grid import** where the DC load
was imported from grid while PV was simultaneously exported/stored
(energy non-conservation). The 48 V gate also closes during a
net-charging slot (the charger lifts the bus over the PSU output, so the
real PSU self-gates), the PSU charge is tapered so a slot cannot
overshoot `gate_soc`, and a defensive clamp keeps `ceil >= floor` for a
hand-edited inverted SOC band. Verified bit-identical for all
non-affected scenarios (golden: only `forced_dc48` shifts, import −0.2
kWh) and by a 200 000-case energy-conservation fuzz.

**Appliance advisor (`core/optimize.py`).** `appliance_windows` now
evaluates the hypothetical run under the same support-PSU schedules as
the planned trajectory, instead of with the PSUs off — no more false
"window open/closed" advisories whenever support is active.

**Actuation commits only on confirmation (`coordinator.py`).** A failed
plug turn-off no longer strands the plug ON while dropping ownership; the
min-runtime dwell is stamped only on a confirmed switch (a failed
actuation no longer blocks the retry for a whole window); and the OFF-lag
grace is now un-timed, so a slow switch is not misread as an external
override.

**Cross-field validation.** The setup wizard now rejects mis-ordered PV
windows (`morning_start < morning_end < afternoon_end`); the core
renormalizes PV shares defensively so a hand-edited config can't silently
discard forecast energy; and a night-spanning variable-load window
(`start > end`) now wraps around midnight instead of dropping the load
for all 24 hours.

**Lifecycle & learning.** In-flight actuation tasks are cancelled before
the unload flush, so none can overwrite the persisted state after it is
captured. The consumption learner's 48 V-PSU attribution is now
gate-aware (uses the battery-voltage proxy, so it no longer credits full
nameplate during self-gated high-voltage hours — closes the Learning
Rev. 4 gap), and a DST fall-back local hour now sums (not averages) a
power sensor's two folded clock hours.

**Robustness.** A load's cached SOC ages out after 7 days (a long-asleep
device plans as empty and self-heals on wake); the export-path guard uses
proper path containment (`is_relative_to`) instead of a string prefix;
and the support mode/state entities stay available on a planner failure
(reading persisted state), keeping them in sync with the always-available
manual switch.

## [0.7.7] - 2026-07-05

### Added — F-N3: R2 battery-voltage controller for the manual 48 V PSU (docs/DC_TOPOLOGY.md §6)
- **While the 48 V support PSU is in manual mode AND a battery-voltage
  sensor is configured, it is now regulated by battery voltage instead
  of held permanently on.** Asymmetric hysteresis: switch ON when the
  voltage stays at/below `psu48_on_voltage_v` (default 49.56 V) for
  60 s, OFF when it stays at/above `psu48_off_voltage_v` (default
  49.8 V) for 300 s, HOLD in the band between. This realises the
  operator's winter workflow: keep the PSU armed, but let it drop out
  once the pack recovers and re-engage when it sags again.
- **Log-only shakedown (`psu48_controller_log_only`, default on).** The
  controller computes and logs every decision but does not actuate the
  switch — a safe dry run before arming it for real.
- **The R3 switch is the sole mode truth for the regulated PSU
  (operator decision A).** A controller-caused OFF never exits manual
  mode; only toggling the manual switch returns the PSU to automatic
  control. During the log-only shakedown (controller not actuating) an
  external OFF still exits manual, exactly as before (F-N2 unchanged).
- **Fail-safe.** A missing or implausible voltage reading (outside
  40–60 V) for more than 10 minutes forces the PSU on; the hardware
  self-gates above its output voltage, so "on" can never overcharge.
  A single valid reading disarms the fail-safe.
- The current decision, mode, reason and voltage are exposed under the
  `support_dc48_controller` attribute of the SOC-forecast sensor. The
  options flow AND the setup wizard validate `off_voltage > on_voltage`,
  and the controller additionally refuses to regulate a collapsed band at
  runtime (defense against a hand-edited/legacy config).
- Three rounds of adversarial multi-agent review (a mid-run internet
  outage crashed round 1's F-N2 lens; it was re-run clean). Confirmed and
  fixed: (a) the band validation was missing from the options flow;
  (b) the controller consumed the planner's shared switch throttle
  (`_last_support_switch`), delaying unrelated 24 V switching; (c) flipping
  `log_only` back on after a controller-caused OFF silently dropped manual
  mode — closed with a persisted `dc48_ctrl_caused_off` flag so a
  controller OFF is never reinterpreted as an operator wall-off across a
  config reload; (d) that flag, once set, could trap the PSU off in manual
  if the voltage sensor was later removed — the exemption now requires the
  controller to still be engaged, and the flag is dropped when it is not;
  (e) the flag was persisted only via a 10 s delayed save that a reload
  could beat — the entry now flushes its state synchronously on unload.
  Also hardened: dwell timers freeze (not reset) on a brief invalid
  reading, and a queued controller command re-checks mode under the switch
  lock so it cannot fire after the operator has exited manual.
- Known follow-up: the consumption-learning correction
  (`history_profile._psu48_series`) still assumes "48 V PSU on == full
  rating delivered"; it will be made gate/controller-aware in the
  learning phase (Rev. 4).

## [0.7.6] - 2026-07-05

### Changed — F-N3: 48 V PSU direct-offset billing (docs/DC_TOPOLOGY.md §4)
- **The 48 V support PSU is now modelled as a 48 V source, not a
  battery charger.** It covers concurrent 48 V bus load directly (no
  battery round-trip), only the remainder charges the battery (via the
  charge efficiency), and the grid is billed for the energy actually
  delivered divided by the PSU efficiency (capped at V × I). This fixes
  three physical errors of the old flat model: over-billing (a full
  battery or closed gate no longer bills the full rating), the
  charge-then-discharge round-trip loss, and the missing efficiency/cap.
  Deliberate behaviour change (golden regenerated): only the
  `forced_dc48` scenario shifts, and cost-neutrally — grid import/export
  unchanged, the SOC trajectory just more physical (min SOC higher, no
  artificial over-storage). No scenario imports more than before.
  Adversarially reviewed (physics/energy-conservation, neutrality/
  escalation, integration) with no findings. Full suite 145 tests green.
- Known follow-up: the consumption-learning correction still assumes
  "48 V PSU on == full rating delivered"; it will be made gate/
  direct-offset aware in the learning phase (Rev. 4).

## [0.7.5] - 2026-07-05

### Changed
- **The setup wizard is grouped into sections too** (operator request):
  the "consumers" step splits into consumption profile + measurement
  sources, and the "control" step into control parameters, support paths
  and DC device parameters — matching the options dialog. Same flat
  storage (`_flatten_sections`), verified by an end-to-end wizard
  completion test; field labels moved into their sections in en/de with
  automated coverage checks. Full suite 142 tests green.

## [0.7.4] - 2026-07-05

### Changed
- **The options dialog ("Planer-Feineinstellung") is grouped into five
  collapsible sections** (operator UX request): control parameters
  (expanded), consumption profile, consumption learning & sources,
  support paths, and DC device parameters. The ~30 fields are no longer
  one long undivided list. Purely a UI change — the stored config stays
  flat (`_flatten_sections` merges the nested section data back) and
  planning behaviour is unchanged. Field labels/descriptions moved into
  their sections in both en/de. Adversarially reviewed (data flattening
  + translation completeness), no findings. Full suite 141 tests green.
  The one-time setup wizard's control step keeps its flat layout.

## [0.7.3] - 2026-07-05

### Added — F-N3 R3 manual-override switches (docs/DC_TOPOLOGY.md §7)
- **A manual switch per support PSU** ("24 V / 48 V support manual"),
  created when the PSU switch is configured. Turning it on forces the PSU
  on and pauses automatic control (winter operation); the 24 V switch
  uses the make-before-break sequence so the rail is never sourceless;
  turning it off restores automatic control. The simulation forces the
  path on while manual, so the SOC forecast matches reality. External
  hand-switching (F-N2) and the switch share one state.

### Fixed
- Adversarial review of the interaction with the F-N2 state machine
  (3 confirmed findings): an operator/auto OFF on a slow or assumed-state
  switch no longer bounces back to forced-on — a symmetric "pending off"
  confirmation distinguishes actuation lag (stay auto) from an operator
  ON right after our OFF (enter manual); a failed 24 V make-before-break
  restore keeps manual mode instead of desyncing the model; and the
  idempotence check moved inside the switch lock so a rapid double-toggle
  is honoured. Full suite 140 tests green.

## [0.7.2] - 2026-07-05

### Added — F-N3 phase 3: voltage gate live + calibration (docs/DC_TOPOLOGY.md)
- **48 V PSU voltage gate goes live.** A new `gate_soc_percent` option
  (SOC proxy for the PSU's output-voltage threshold) makes the simulation
  deliver the 48 V PSU only below that SOC. 100 % maps to "no gate"
  (`None`), so an existing entry stays behaviour-neutral until the
  operator sets a real value.
- **Gate calibration diagnostic.** The coordinator watches where the real
  battery-voltage sensor crosses the 48 V PSU output voltage and exposes
  the SOC bracket (highest SOC still delivering / lowest SOC already
  gated), a suggested gate SOC, and volts-per-cell (with the new
  informational `battery_cells_series` option) as the `gate_calibration`
  attribute on the SOC-forecast sensor — read it over a discharge evening
  to pick `gate_soc_percent`.
- Config-flow fields + en/de translations for the gate SOC and cell
  count. Adversarially reviewed (gate correctness / calibration /
  integration) with no findings. Full suite 133 tests green.

## [0.7.1] - 2026-07-05

### Added — F-N3 two-bus DC model, phase 2 (docs/DC_TOPOLOGY.md)
- **Device parameters are now configurable.** The base "control" step and
  the options flow gained the F-N3 fields — 24 V rail share, and per
  device (DC/DC converter, 24 V PSU, 48 V PSU) the output voltage,
  efficiency and max current (0 A = uncapped, rail-side power cap =
  V_out × I_max) — plus an optional battery-voltage sensor for the later
  voltage-gated controller. `build_system_config` maps them into the core
  `SupportParams`. All defaults are neutral (share 100 %, efficiency 1.0,
  uncapped, gate open), so an existing entry keeps its exact v0.7.0
  behaviour until real nameplate values are entered.
- `hourly_details` now carries the two-bus diagnostics (PSU delivered
  energy, DC/DC input/loss, unserved rail demand, gate state) for
  plausibility-checking the plan after entering real values.
- Adversarially reviewed (neutrality/back-compat, config-flow, edge).

## [0.7.0] - 2026-07-05

### Added — F-N3 two-bus DC model, phase 1 (docs/DC_TOPOLOGY.md)
- **Behaviour-neutral two-bus core.** `SupportParams` gained the device
  parameters for the real DC topology — 24 V rail share, DC/DC converter
  and both support PSUs (efficiency, power cap = V_out × I_max, output
  voltage) plus the 48 V PSU's voltage gate (`gate_soc_percent`). All
  defaults are neutral (share 100 %, unit efficiencies, uncapped, gate
  always open), so every plan stays byte-identical to pre-F-N3.
  `core/simulate.py` `step_hour` now splits the DC load into a 24 V rail
  part (served by the DC/DC from the battery, or by the grid 24 V PSU)
  and a native 48 V bus part, with per-device efficiency and caps, and
  `HourFlows` carries the delivered-energy / DC-DC-loss / unserved / gate
  diagnostics.
- A **golden-plan snapshot suite** (`tests/core/golden_topology.json` +
  `test_golden_topology.py`) freezes 11 representative plans and proves the
  refactor is bit-exact; six combination-equation tests exercise the new
  physics with non-neutral parameters. Reviewed adversarially (three
  lenses — neutrality, physics, integration — no findings).
- Not yet wired to the config flow (phase 2, v0.7.1) and the 48 V PSU
  still uses the flat-power formula (the physically correct direct-offset
  billing lands in a later phase with its own golden diffs).

## [0.6.5] - 2026-07-05

### Added
- **Manual override for the support PSUs** (operator decision F-N2,
  docs/ALGORITHM.md D-A9): switching a support PSU on externally (e.g.
  permanent winter operation) pauses the automatic control for exactly
  that PSU — including the 24 V make-before-break — until it is switched
  off externally again; then the automation takes over (and immediately
  restores an off DC/DC converter so the 24 V rail is never left dead).
  The mode survives restarts (persisted together with the integration's
  own switch state, so "on, but not ours" stays distinguishable from
  "on, because we switched it" across a reboot), and each PSU gets an
  enum mode sensor (automatic/manual) for dashboards and notifications.
  While a PSU is in manual mode the simulation treats that path as
  permanently active, so the SOC forecast matches real winter operation.
  Support switches are now tracked entities: manual toggles trigger a
  debounced replan instead of waiting for the next 5-min poll.
  Hardening from the adversarial review (9 confirmed findings): the
  late-confirmation grace is per PSU AND per direction (an operator ON
  right after a BM OFF enters manual mode instead of being reverted and
  oscillating), an own unconfirmed 24 V activation is remembered so a
  late device report is adopted as ours, the idle state sync never
  adopts a foreign OFF->ON (single owner: the mode detector), the 24 V
  rail guard is level-triggered (PSU off + DC/DC off is healed every
  cycle, surviving failed restores and boot races), adopted states are
  persisted, stale flags of a removed switch are dropped on restore,
  pre-0.6.5 stores adopt an already-on PSU once instead of flipping it
  to manual on the upgrade restart, and removed switches also drop
  their mode sensor from the registry.

## [0.6.4] - 2026-07-05

### Changed
- **Load dialog is now two-step**: capacity, target SOC, the SOC sensor
  and the whole charging-path block (input switch, charge enable,
  input-off policy — semantics defined for powerstation charging paths,
  docs/LOAD_CONTROL.md §2/§3) only appear when "energy limited" is
  enabled — for continuous consumers like a dehumidifier they were
  meaningless clutter (operator wish). Values are preserved when the
  toggle is switched off and back on; the keep_on-requires-enable
  validation moved to the storage step accordingly.
- **Power-deviation warning cleanly disableable per load** (0 % in the
  load dialog, e.g. for the Fossibots where it is not wanted): the
  warning binary sensor is then not created, and a previously created
  one is removed from the entity registry instead of lingering as an
  orphan.

## [0.6.3] - 2026-07-05

### Added
- **Per-load power-deviation warning** (operator requirement F-L7,
  docs/LOAD_CONTROL.md §8): a new binary sensor per load (device class
  `problem`) turns on when the load runs at the integration's request
  but its real draw deviates from the configured power by more than a
  per-load percentage (new subentry field, default 50 %, 0 = disabled)
  for more than 30 sustained minutes. Catches a full dehumidifier water
  tank (draw near 0 W), a wrong configured power and foreign consumers
  on the measured outlet; short defrost pauses reset the timer and stay
  silent. Attributes expose expected/measured watts and the deviation
  start for notification automations; state transitions are logged.

### Fixed
- **Manual runs no longer train the planning power** (operator decision
  F-L6, docs/LOAD_CONTROL.md §8): the operator sometimes runs a load —
  or a foreign consumer on the load's measured outlet — by hand (e.g.
  the dehumidifier while working in the basement). Power-feedback
  samples are now accepted only while the load runs at the integration's
  own request: for switched loads the physical charging state (plug AND
  enable on — the feedback meters the device itself, so even a manual
  charge yields correct device data, bounded by the switch dwell), for
  recommendation-only loads the last plan's active recommendation WITH a
  clean start (outlet idle at the activation edge — otherwise a
  pre-existing draw would be learned, flip the next plan and oscillate;
  adversarial-review finding). Outside these windows samples are ignored
  and a lingering EMA is discarded, so planning falls back to the
  nominal power instead of learning whatever happens to be plugged in.
  Entity dropouts (unavailable/unknown plug or enable) no longer read as
  "charge over" — that used to delete the learned EMA mid-charge.

## [0.6.2] - 2026-07-05

### Fixed
- **Standby draw no longer poisons the planning power** (live incident
  2026-07-05): a load's power-feedback sample was accepted whenever it
  read above a flat 10 W, so a 400 W dehumidifier idling at ~19.6 W
  (Fritz powerline plug) taught the EMA ~22 W as "measured" planning
  power. The plan then booked 11 hours at 22 Wh each (0.24 kWh) for a
  device that really pulls ~400 W (~4.4 kWh — 18× over plan), and the
  pass-2 export gate `export_drop ≥ (1−tol)×energy` became trivially
  satisfiable. The v0.6.1 nominal floor did not apply (it guards only
  the saturation gate of energy-limited loads). Samples are now accepted
  only at `raw ≥ max(10 W, STANDBY_FRACTION × nominal power)` with
  `STANDBY_FRACTION = 0.25`; below that, the v0.6.1 rule applies — the
  EMA keeps serving only during an active charge, otherwise it is
  discarded and the planner uses the nominal power.

## [0.6.1] - 2026-07-05

### Fixed
- **Degenerate-slot-0 night charging** (live incident 2026-07-05 04:59):
  the planner evaluated activation candidates with `power × slot
  duration`, and slot 0 (the partial current hour) shrinks to 1 minute
  just before each hour boundary. A nearly-full powerstation (6 Wh below
  target) then passed every gate with a ~5 Wh candidate, and the
  executor's 30-min minimum runtime charged ~250 Wh from the house
  battery — 50× the planned energy, never accounted for in the
  simulation. All three observed "Charging started" events sat in a :59
  minute. Candidates are now evaluated and simulated with the energy the
  executor really commits (`power × max(slot remainder, min runtime)`,
  spilled across slot boundaries and scheduled as one block), and the
  saturation gate is floored at the nominal power so a decayed/empty
  feedback EMA cannot weaken it.
- The per-load switch dwell timestamps are now persisted across restarts
  (a wiped dwell allowed switching right after boot — a co-factor of the
  incident). The power EMA is deliberately NOT persisted, and a feedback
  gap keeps serving the last smoothed value only WHILE the load is
  actually charging: after the charge, the taper-decayed reading (often
  10–40 W) is discarded so it can never stick as permanent "measured"
  planning power that would weaken every gate (adversarial-review
  finding on the first draft of this fix).
- Pass-1 slot-local surplus bookkeeping no longer double-counts the
  spilled share of a commitment (parallel loads in a partial slot 0 were
  starved conservatively), commitments truncated at the horizon end book
  only the energy actually placed, and the min-runtime commitment floor
  applies to interior hours too (min_runtime > 60 min configs no longer
  produce phantom 1-hour plans that can never execute).

### Changed
- **Pass 2 ("zielbasiert") now allocates latest-first** (operator
  decision F-L5, docs/LOAD_CONTROL.md §8): preemptive charging hours are
  placed as late as the constraints allow — just early enough that no
  surplus is lost — instead of at the earliest justifiable hour (e.g.
  22:00 the night before). Slots after the last export slot are skipped,
  and the whole pass is pruned on export-free horizons.
- Load allocations are transparent: `LoadPlan.allocations` records
  (slot, span, pass, energy) per decision, the coordinator debug-logs
  them, each `load_plans` schedule entry carries its `pass`, and the
  "Charging started/stopped" log lines name the load in plain text.

## [0.6.0] - 2026-07-04

### Added — Stufe 2 (docs/CONSUMPTION_FORECAST.md §5)
- **Weighted P50/P80 quantile profiles** (D-C7): the nightly aggregation
  now computes recency-weighted quantiles (half-life
  `profile_half_life_days`, default 30 d) instead of a plain median —
  the weighting doubles as the drift/season model, so the learning
  window default widens to 120 days. Learned store v2 (bins carry
  `{p50, p80}`); v1 stores are rebuilt from a fresh backfill.
- **Dynamic SOC buffer** (D-C8, active immediately per operator
  decision): the planning buffer is derived each run from the P80−P50
  uncertainty band over the critical window (now → first forecast PV
  surplus), clamped by `buffer_min_percent`/`buffer_max_percent`
  (3/15 %). The grid-PSU escalation trigger keeps the FIXED configured
  buffer via the new core parameter `support_buffer_percent` — a wide
  night band must not make the PSUs switch earlier. Effective value and
  window are exposed in the `consumption_profile` diagnostics and used
  by the forecast card's reserve zone.
- **Daily forecast watchdog** (D-C9): every learning run scores
  yesterday's P50 forecast against the cleaned actuals (bias + MAE per
  path, 30-day history in the export; latest entry in the
  diagnostics). A one-sided bias > 15 % of the mean load for 14 days
  raises a repair issue instead of learning on silently.
- **Holidays** (§5.3): optional `workday_entity` (Workday integration);
  holidays are learned and planned as weekends. Horizon days are
  resolved via the `workday.check_date` action (cached nightly),
  falling back to the plain calendar rule.

### Changed
- `negative_residuals` now counts per learning run instead of
  accumulating forever.
- `export_learned_profiles` shows P50 and P80 columns plus the latest
  watchdog entry per path.

## [0.5.2] - 2026-07-04

### Changed
- **Support paths correct the learning arithmetically instead of
  excluding hours** — in winter the grid PSUs can run for months and the
  old exclusion would have starved the learning completely:
  - 48 V PSU on: configured power × on-time is subtracted from the AC
    measurement (house-net draw) and added to the DC measurement
    (battery-bus injection) — same approximation as the simulation core.
  - 24 V PSU feeding the rail (DC/DC off): the DC→AC load shift is
    reversed exactly via the new optional **24 V PSU power sensor**
    (`support_dc24_power_entity`, base + options flow). Without that
    sensor only those hours remain unlearnable (recommendation: add a
    metering plug for winter operation); a repair issue is raised if the
    sensor is configured but has no statistics.
  - A dead rail (DC/DC off without PSU) still excludes the hour.
  Cached days from the old rule are refetched automatically.

## [0.5.1] - 2026-07-04

### Added
- Service **`battery_manager.export_learned_profiles`**: learned W bins
  and sample counts per (path, day type, hour) as ASCII tables, or the
  raw JSON snapshot with `as_table: false`.
- The `consumption_profile` attribute on the SOC forecast sensor now
  carries the learned bins themselves (for dashboard cards/templates).
- `export_hourly_details` gains a **Prof** column (`L/S` per AC/DC path):
  learned series vs. static fallback per hour.

### Changed
- **Statistic gaps of power-feedback sensors count as 0 W** while
  learning (operator decision): powerstations/appliances report
  `unavailable` exactly when they are off, so a missing hour means "no
  consumption" — previously those hours were dropped and starved the
  weekend bins. Cached learning days from the old rule are refetched
  automatically (cleaning-rules version in the fingerprint).
- Learning cache is invalidated when the cleaning configuration changes
  (`in_house_measurement`, power/switch entities, appliances, support
  switches) — with an immediate catch-up run and the rate limit
  suspended for the rebuild.

### Fixed
- Options flow failed to open with a bare "400: Bad Request": unit-less
  number selectors passed `unit_of_measurement: None`, which the
  selector config validation rejects (latent since v0.2, first hit by
  the options flow). Flow smoke tests added.
- Forecast card: wider right margin so the curve no longer runs into
  the card edge and the T* label has room.

## [0.5.0] - 2026-07-04

### Added
- **Learned consumption profiles** (docs/CONSUMPTION_FORECAST.md, Stufe 1):
  a nightly job learns hourly AC/DC baseline profiles
  (weekday/weekend/absence × 24 h, robust medians) from the recorder's
  long-term statistics and feeds them into the planner as per-slot series;
  the static two-step profile remains the slot-wise fallback.
  - Measurement sources per path: a direct load sensor
    (`ac_load_entity`/`dc_load_entity`) **or** a generic counter balance
    (inflow/outflow entity lists) — topology-independent, all entities
    configurable.
  - Mandatory cleaning of self-controlled consumption: surplus loads are
    subtracted via their power-feedback statistics (or nominal power ×
    switch-history on-time); status-only appliance hours and active
    support-path hours are excluded; negative residuals are counted as a
    misconfiguration diagnostic.
  - New per-load flag *Included in house-load measurement*
    (`in_house_measurement`) for loads fed outside the measured node
    (e.g. via a feed-in setpoint).
  - New **Vacation mode** switch: forecasts use the learned absence
    profile (or the base load until enough absence days are learned);
    vacation days are tagged for learning via the switch's history.
  - Diagnostics on the SOC forecast sensor (`consumption_profile`
    attribute: source per path, slot coverage, sample counts, negative
    residuals) and a repair issue when a measurement entity has no
    long-term statistics.
- Static fallback-profile fields and the new learning options are now
  editable in the **options flow** (previously only settable during setup).
- Core: `series.slot_starts()` as the single source of truth for the slot
  grid; `build_slots()` accepts optional per-slot consumption series.

## [0.4.0] - 2026-07-04

### Added
- Bundled Lovelace card **Battery Manager Forecast**
  (`custom:battery-manager-forecast-card`): planned SOC curve, inverter
  threshold T*, reserve/limit zones, "now" marker, per-load surplus schedule
  lanes with legend, hover readout. Ships inside the integration and
  registers itself as a dashboard resource — no HACS frontend download.
  Appears in the card picker under *Community* and is suggested
  automatically when picking the SOC forecast sensor (HA 2026.6+
  entity-first picker). YAML resource mode falls back to a global module.
- `sensor.…_soc_forecast` now carries the full plan context as attributes
  (`soc_threshold_percent`, SOC limits and buffer, `grid_import_kwh`,
  `lost_surplus_kwh`, per-load `schedule` blocks) so chart cards can render
  the whole plan from one entity.

### Fixed
- Stale `INTEGRATION_VERSION` constant (was still 0.2.0).

## [0.3.0] - 2026-07-04

### Added
- Direct charging-path control per surplus load (docs/LOAD_CONTROL.md):
  optional input-plug switch and charge-enable entity; the integration
  switches charging itself. Configurable end-of-charge policy for the input
  plug (auto/ownership, always off, keep on) — passthrough-powered output
  loads keep working.
- Last-known SOC caching for sleeping powerstations (sensors unavailable
  while the input is off), persisted across Home Assistant restarts; unknown
  SOC is treated as "needs charging" (self-healing on first wake).
- `sensor.…_soc_forecast`: forecasted SOC curve over the whole horizon as a
  `forecast` attribute for chart cards (ApexCharts example in README).
- Brand icon shipped locally under `custom_components/battery_manager/brand/`
  (HA 2026.3+ local brands proxy — no home-assistant/brands PR needed).
- `charging_active` attribute on load recommendation entities.

## [0.2.0] - 2026-07-04

**Breaking change:** complete algorithm and configuration rewrite. Existing
config entries are migrated best-effort, but reconfiguration is recommended.
Design documents: `docs/REQUIREMENTS.md`, `docs/STRATEGY.md`, `docs/ALGORITHM.md`.

### Changed
- Replaced the heuristic "Maximum-Based Controller" with a policy-consistent
  simulation search: every SOC-threshold candidate is simulated with the real
  switching policy over the full horizon; the cheapest (grid import − terminal
  battery value + export penalty) wins.
- Surplus loads are now scheduled by surplus allocation: only hours with
  otherwise-exported energy are assigned, validated by full-horizon
  re-simulation (a load can never cause grid import or violate the SOC
  buffer). This fixes the historic night-activation bug.
- New pure simulation core under `custom_components/battery_manager/core/`
  (no HA imports, no shared mutable state); the old
  `battery_manager/battery_manager/` library and `standalone_test/` scripts
  were removed in favour of a pytest suite (`tests/`).
- Forecast horizon now covers all provided forecast days (previously cut at
  08:00 of day 3); a terminal value makes the horizon end well-defined.
- CI now runs ruff and the pytest suite (replaces flake8/black/isort).

### Added
- Objective-based load scheduling (pass 2): loads may also run in hours
  without direct surplus (e.g. pre-charging before a short production peak)
  when the full-horizon re-simulation proves the energy comes from
  otherwise-lost surplus — grid import never increases.
- Make-before-break switching of the 24 V rail: the 48 V→24 V DC/DC converter
  is configurable as a switch; changeover runs sequenced (new supply on →
  delay → old supply off) with confirmation, abort-on-failure, background-task
  isolation (immune to replan cancellations) and idle re-sync of the real
  switch states. Distinct-entity validation and an `assumed_state` warning
  protect the guarantee.
- Multiple surplus loads as config sub-entries with priority order, parallel
  operation, per-load battery-share tolerance, energy limits (powerstation
  SOC/target), power feedback (measured W override) and availability entities;
  one recommendation entity per load.
- Household appliances as config sub-entries: detected runs add their
  remaining consumption to the forecast; optional start-window advisor entity
  ("a full run fits into the surplus right now").
- Emergency grid-support paths (24 V PSU replacing the DC/DC converter, 48 V
  fixed-power support PSU): simulated as last-resort escalation and switched
  directly by the integration; status entities included.
- Planner tuning options: SOC safety buffer, hysteresis, threshold inertia,
  minimum switch interval.
- New sensors: grid import forecast, lost surplus forecast.
- German translations (`de.json`).

### Fixed
- Entity-change listeners were never armed (`_listeners_setup` stayed False):
  input changes now trigger debounced replanning as intended, and listeners
  are properly released on unload.

## [0.1.0] - 2025-06-08

### Added
- Initial release of Battery Manager Home Assistant Integration
- Intelligent SOC threshold calculation based on PV forecasts
- Real-time energy flow simulation with battery, PV, and load modeling
- Home Assistant UI configuration via config flow
- Four main sensor entities:
  - Binary sensor for inverter status (on/off)
  - Sensor for calculated SOC threshold percentage
  - Sensor for minimum forecasted SOC
  - Sensor for maximum forecasted SOC
- Comprehensive standalone testing suite
- Robust error handling and input validation
- Flexible configuration options for different battery and PV system sizes
- Automatic entity monitoring with debounced updates
- Device grouping for all entities
- HACS custom repository support
- GitHub Actions workflows for validation
- Comprehensive documentation and installation guides

### Features
- **PV System Modeling**: Configurable PV system with hourly production distribution
- **Battery Simulation**: Charge/discharge efficiency modeling with SOC constraints
- **Load Profiles**: Separate AC and DC consumption modeling
- **Energy Flow Calculator**: Complex multi-component energy flow simulation
- **Maximum-Based Controller**: Intelligent threshold calculation algorithm
- **Multi-language Support**: English translations included
- **Performance Optimized**: Efficient algorithms suitable for real-time operation

### Technical Details
- Compatible with Home Assistant 2024.1.0+
- Python 3.11+ support
- No external dependencies required
- MIT License
- Comprehensive test coverage including edge cases and error scenarios
