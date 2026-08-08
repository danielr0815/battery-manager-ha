# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/0.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.25.0] - 2026-08-07

### Fixed
- **The planner no longer simulates a DC forecast curve as if it were AC.**
  Forecast sources whose `wh_period` hourly attributes are a DC model curve
  while the entity state reports the AC day energy (balcony_solar_forecast
  v0.25.x, verified live: Σwh_period 6.129 kWh vs. 5.649 kWh AC) fed the
  AC-side simulation ~8.5 % too much PV — about 1.1 kWh of phantom energy per
  normal day, which contributed to an avoidable 0.7 kWh grid import on
  2026-08-07 (SOC fell to 13 %). The hourly maps (median and p10/p90 bands)
  are now scaled by the ratio η = state / Σ(curve) derived from the same
  entity, clamped to [0.5, 1.0]; sources whose state and curve are consistent
  get η ≈ 1 (no-op), and explicit `wh_period_ac` attributes are preferred
  when a forecaster offers them. Found in the 7-day plant audit
  (2026-07-31 → 2026-08-07).
- **The house-SOC stale watchdog no longer flaps against a 1 %-quantized SOC
  source.** The mid-band drift allowance (2 % of capacity ≈ 100 Wh) sat only
  two display steps above the Victron source's 1 % quantization, while the
  planner's expected flow routinely overshoots the real flow by ~2× — the
  audit counted 37 false latches in 7 days, ~7 h of steering blackout, one
  episode 65 s short of the 2 h D-A8 load-shed tripwire. The default
  `house_soc_stale_mid_percent` is now 3.0 (2 % physical drift + 1 % display
  quantization). Installations with a persisted value keep it.
- **A failed plug-ON no longer orphans the charge-enable gate.** When the
  enable switch confirmed ON but the plug switch failed (2026-08-04, BLE RPC
  error), the gate stayed on forever — no later path ever switched it off
  (`desired == current`), voiding the firmware's protective threshold
  (LOAD_CONTROL §3 "Charge enable OFF — always"; on the plant it sat on for
  3.4 days). The ON sequence now rolls the enable back on plug failure, and a
  dwell-exempt sweep turns off any gate that is physically on while its load
  neither charges nor should charge.
- **The F4 telemetry-freeze and G2 stale-SOC latches survive a reload.** They
  were in-memory only: a coordinator reload on 2026-08-02 dropped an F4 latch
  and the recommendation duty-cycled 110 min for a verifiably unplugged load
  (~225 Wh misbooked). The latches now persist their reference values and
  accumulated evidence seconds — downtime itself still does not count as
  freeze evidence.
- **The F-PREDRAIN-BLOCK log line is back to "on change" instead of once a
  minute.** The booking signature ratcheted with the clock, producing 4,196
  INFO lines in 7 days (1,753 on 2026-08-04 alone). A booking now logs only
  when it materializes or materially changes (start shifted ≥ 30 min or
  energy ≥ 200 Wh), rate-limited to one line per block per hour; suppressed
  updates go to DEBUG.

## [0.24.1] - 2026-08-03

### Fixed
- **Early feed-in no longer lets the battery pay for the export.** The pass
  booked against the raw AC surplus (`pv − ac − extra_ac`), which is not what
  the simulation can actually give away: the inverter standby is part of the
  AC draw it subtracts, and the 48 V bus load is settled out of the STORE
  before the AC balance ever runs. The result on the plant was a slot that
  exported the entire surplus while the battery discharged to cover the bus —
  439 Wh booked against 424 Wh servable, SOC 31.3 → 29.8 %, i.e. straight
  through the feature's own 30 % floor, which is only tested at slot start.
  The booking now reserves the standby and the AC energy the charger needs to
  put the bus draw back, so every booked slot ends battery-neutral (net ≥ 0)
  and the setpoint is physically servable. The day's feed-in gets smaller by
  exactly that reserve; the SOC forecast and the floor now hold. Feed-in off
  ⇒ unchanged (goldens untouched).

### Added
- **The dashboard card answers "how much, and when?" without leaving it.**
  The legend used to give every surplus load its today/tomorrow energy but
  showed the early-feed-in and grid-support lanes as a bare dot and name; all
  three now carry the same `heute/morgen kWh` figure. The hover readout, which
  named the active lanes but never quantified them, now shows the energy of
  the hovered hour per lane — the feed-in derived from its planned power times
  the slot length (slot 0 is a partial hour and is handled as such), the loads
  from a new per-slot `wh` on each schedule entry. Feeding this, the `daily`
  breakdown gained `support_dc24_kwh` / `support_dc48_kwh` (delivered PSU
  energy per day). All additions are backward compatible: a card reading an
  older backend renders exactly as before.

## [0.24.0] - 2026-08-03

### Added
- **Realized surplus accounting (F-REALIZED-SURPLUS).** Everything the
  integration reported so far was forecast; the new measured ("Ist") side reads
  the plant's own energy counters and mixes both in the honest direction:
  **today = measured so far + forecast for the rest of the day**, tomorrow is
  pure forecast. The measurement also corrects a metering artefact of this
  plant — a load supplied outside the grid measuring point (the cellar
  dehumidifier on a circuit the EM540 never sees) shows up 1:1 as "export", so
  its measured consumption is subtracted from the export counter. The result is
  the **true export**, a monotone counter the energy dashboard can consume.
  Realized prevented export is the energy the surplus loads actually drew
  (measured where a counter exists, runtime × learned power where it does not),
  and the realized early feed-in is bridged from the F-FEEDIN executor's own
  delivered-energy integral — which now survives a restart. Counter deltas pass
  a jump filter (negatives and implausible steps dropped, the reading still
  advancing so one bad jump cannot poison every later delta) with the accrual
  window measured since the reading last *changed*, so a coarse counter
  publishing in 0.1 kWh chunks is not mistaken for a glitch. Day counters roll
  over at local midnight with the readings carried over as the new baseline;
  everything is persisted, so a reload no longer resets the day. Spec:
  `docs/F-REALIZED-SURPLUS.md`.
- **Configuration and entities.** New options section `surplus_accounting` with
  a single field, the export energy meter — wiring it is what switches the
  feature on. Optional per-load `energy_entity` (kWh counter of the load
  itself) on the surplus-load subentry. Seven sensors gated on the export meter
  (`lost_surplus_realized_today`, `lost_surplus_today`,
  `lost_surplus_tomorrow`, the three `prevented_export_*` counterparts and the
  monotone `true_export_energy`), plus `early_feed_in_realized_today` gated on
  early feed-in alone. The dashboard card's stats line shows the day totals
  with the measured share in parentheses ("verlorener Überschuss 2.3/1.1
  (Ist 0.8)") instead of double-reporting forecast and measurement. Neutral
  default: without an export meter the `realized` block stays absent and every
  sensor, attribute and card line behaves exactly as before; `core/` is
  untouched, so the plan is bit-identical.

### Fixed
- **Early feed-in: the fail-safes now work off the planning path.** The
  setpoint was only ever written on a SUCCESSFUL cycle, so the shutdowns that
  matter most were unreachable: an SOC/forecast outage raised `UpdateFailed`
  before the executor and left the plant exporting unsupervised for the whole
  outage — draining the battery once PV fell below the setpoint — and the
  runtime switch, which relied on that same refresh, read `off` while the
  export ran on. Both now write the 0 directly (idempotently). Clearing the
  setpoint entity in the options flow no longer strands the last exported
  value either: the owning entity is persisted and released to 0 by the
  reloaded coordinator.
- **Early feed-in: the control loop stops fighting itself.** The re-anchor is
  throttled to the planning interval (it also ran on every debounced
  battery-power event and undid each throttled upward trim seconds later, so
  the setpoint oscillated against the external controller); a downward trim no
  longer rewrites an unchanged value every few seconds; the upward rate cap no
  longer subtracts the energy delivered earlier today from a plan value that
  already covers only the remaining slots (which drove the cap to 0 and
  disabled the trim for the rest of the day); and the manual-mode grace only
  excuses a state still showing our previous value, so an operator override
  during an active trim is no longer silently overwritten.
- **Event-driven inputs no longer starve the debounce.** A tracked entity
  updating faster than the 5 s debounce (the Victron battery-power sensor
  publishes every 1-2 s) cancelled and restarted the window on every event, so
  the debounced refresh never ran between the 5-minute polls — and with it
  neither did the feed-in trim that has to react in seconds. A pending window
  now absorbs further events instead of restarting.
- **Realized surplus: measured energy is no longer silently discarded.** The
  export meter was capped by a flat 2 kWh jump filter, so a legitimate accrual
  across a cycle gap (planner failure, HA restart) was dropped whole while the
  baseline advanced anyway — the energy was lost forever; it is now capped
  against the PV peak scaled by elapsed time. The out-of-house load correction
  is carried forward as a debt instead of being clamped away per cycle, so a
  coarse counter publishing in 0.1 kWh chunks actually corrects the export
  meter it is meant to correct. And counter readings are scaled by their own
  `unit_of_measurement` — a Wh-unit counter was read as kWh, mis-scaled by
  1000x, and every delta then swallowed by the jump filter, leaving all
  realized sensors at 0 with nothing but a DEBUG line.

## [0.23.0] - 2026-08-03

### Added
- **Early grid feed-in (F-FEEDIN).** When the forecast shows unavoidable
  midday export after all surplus loads are allocated, the new `plan_feedin`
  planner pass pre-shifts that energy into the morning: PV surplus is passed
  straight through to the grid via the external controller's AC setpoint
  (negative setpoint = export) while the battery idles — it is never actively
  discharged for feed-in. Total export stays invariant; only its timing moves
  off the midday peak, when the grid needs it least. The per-day target is the
  median residual export, spread towards a soft deadline (default 9:00, runs
  over it as fast as the surplus allows, stops when delivered or the battery
  is full), floored at `feedin_min_soc_percent` (default 30 %) and braked —
  never enlarged — by the Z4 P10 stress, trimmed latest-first.
- **Setpoint executor with event-driven trim.** `_apply_feedin` /
  `_execute_feedin` write the plan slot value via `input_number.set_value`;
  every battery-power update (Victron convention: positive = charging) fires
  the trim between planning cycles — downward immediately and unthrottled
  (the battery discharging means the feed-in is eating into it), upward
  throttled to one raise per 60 s and capped at the remaining day target —
  with a 5-minute re-anchor to the plan value (25 W deadband, trim writes
  carry none). An external setpoint overwrite enters a persisted manual mode
  until the next midnight (no writes; `sensor.battery_manager_feedin_mode`
  shows auto/manual); the G4 floor guard and the stale-data shed force 0.
- **Configuration and entities.** New options section `early_feed_in`
  (toggle default OFF, setpoint `input_number` + battery-power `sensor`
  wiring, max power ≤ 2000 W, min SOC above the battery floor, deadline
  0–12 h), the persisted runtime switch `switch.battery_manager_early_feed_in`
  (default ON), `planned_feedin_kwh` in the `daily` breakdown, a `feedin` (W)
  value on scheduled SOC-forecast points, and a feed-in lane plus a
  today/tomorrow stats line on the dashboard card. Neutral default: with the
  toggle off the plan is bit-identical (golden snapshots unchanged). Spec:
  `docs/F-FEEDIN.md`.

## [0.22.1] - 2026-08-02

### Fixed
- **Stale watchdog: the 89 % plateau belongs in the edge band.** A real 89.0
  SOC (battery idle on a strong-PV afternoon, Victron calibrating at the top
  plateau) latched after just ~107 Wh of expected flow because the edge band
  only covered `> 89 %`. The high bound is now 88 % and both bounds are
  INCLUSIVE, so 88.0/89.0 get the loose 7 % allowance (~45–60 min tolerance
  instead of ~10 min) — a genuinely frozen BMS is still caught, just much
  later.

### Added
- **Configurable stale-band SOC bounds.** New options
  `house_soc_stale_edge_low_soc` (default 13 %) and
  `house_soc_stale_edge_high_soc` (default 88 %) in the battery section of
  the options flow, with an order validation (low must be below high).

## [0.22.0] - 2026-08-02

### Changed
- **Every horizon day gets its own pre-drain block (per-day).** The pass-3
  block was computed only for slot-0's day, so the forecast showed
  tomorrow's dehumidifier only from ~08:00 and a tomorrow SOC curve that
  could never happen. Now each horizon day with a clip gets its own block,
  each confined to its own day (start >= its midnight — nothing drains one
  day for the next; the cross-day carve-out stays retired). Today's block
  actuates as before (slot 0 + stability gate); future-day blocks are
  plan/display until their day comes — the SOC forecast honestly shows
  tomorrow's pre-drain now. On the clipping-eve golden the dehumidifier's
  own tomorrow block cuts lost export from 6.29 to 3.94 kWh (min SOC now
  reaches the ~25-28 % dynamic floor instead of ~63 %).

## [0.21.0] - 2026-08-02

### Changed
- **Pre-drain blocks may drain to the dynamic buffer floor.** The alpha/band
  Z4 stress is no longer applied to the pass-3 block (it stays in force for
  the slot-wise pass-2 bets of energy-limited loads): an intra-day forecast
  miss is caught by the replan loop — the block is recomputed every
  refresh, a degraded forecast retracts the recommendation, and the G4
  floor guard force-switches at the real-time cutoff — so the conservative
  whole-window stress was redundant. On strong days the block now drains to
  the nominal dynamic buffer (trough ~25 % instead of ~35 % at alpha 0.7)
  and prevents noticeably more export; the floor check now also runs at
  alpha 1.0 (previously skipped there — blocks can be shallower in that
  configuration than before).

## [0.20.0] - 2026-08-01

### Added
- **At-max top-up for energy-limited loads (F-PEAK-FILL).** A powerstation
  with charge budget left no longer stalls or pulses at the max line when
  the PV surplus is below its charge power: pass 1 may book the slot
  anyway, with the house battery buffering the difference — as long as PV
  exceeds consumption and the trial ends the slot inside the hysteresis
  band (`soc_max − 5 %`). R5/Z2'' prove the dip refills from otherwise-lost
  export, so afternoon export spikes become Fossibot charge instead of
  clipping. Reasons show `at-max top-up (peak fill)`; continuous loads are
  excluded (their buffer form is the pass-3 block).

### Fixed
- **Pre-drain blocks are no longer starved by phantom import.** The AC
  charger's 10 W standby was booked as grid import in every charging hour
  although the charger only runs on PV surplus; over the 3-day horizon
  these artifacts accumulated past the shared 50 Wh import slack (Z2'') and
  vetoed whole pre-drain blocks — on exactly the strong-sun days they exist
  for (found 2026-08-03: tomorrow's 07:00–10:00 block would have been
  refused). The standby now reduces the stored charge instead, with the
  charger ramping to cover its own standby (`needed_ac` compensation), so a
  nearly full battery still reaches soc_max exactly and surplus hours mint
  no import at all.

## [0.19.0] - 2026-08-01

### Changed
- **Pre-drain for continuous loads is now ONE block to today's SOC peak
  (F-PREDRAIN-BLOCK).** The slot-wise pass-2 betting is replaced by a single
  contiguous run ending at today's first export slot, sized to at most
  today's remaining lost surplus, started as late as the floors allow
  (docs/F-PREDRAIN-BLOCK.md). Consequences: no more cross-day night
  pre-drain for continuous loads (the F-NIGHT-RESCUE carve-out is retired
  for this class), and a block is only switched after
  `PREDRAIN_BLOCK_STABLE_PLANS = 3` identical plans plus a
  `PREDRAIN_BLOCK_STABLE_MINUTES = 10` wall-clock floor. Energy-limited
  loads (powerstations) keep the unchanged pass-2 machinery; a new
  `predrain_block` sensor attribute exposes the block window and stability
  progress.

### Fixed
- **No more flicker micro-runs at the pre-drain gate edge.** On 2026-08-01
  a marginal slot-0 bet flipped in and out of the plan within minutes and
  started a 19-minute battery run nobody needed (the target clip was two
  days out and today's battery refilled anyway). The block + stability gate
  makes the actuation match the operator rule: run as late as possible,
  only when the battery provably fills to max SOC today anyway, and never
  below the inverter reserve.

## [0.18.0] - 2026-07-31

### Added
- **Configurable house-SOC stale watchdog bands.** New options
  `house_soc_stale_mid_percent` (default 2 %) and
  `house_soc_stale_edge_percent` (default 7 %) in the battery section of the
  options flow: how much expected battery throughput (in % of capacity) may
  accumulate without any SOC change before the house-SOC reading is treated
  as frozen. The band bounds (13 % / 89 % SOC) are fixed.

### Fixed
- **No more false stale alarms at the BMS plateaus.** The house-SOC watchdog
  no longer uses the adaptive "time for a 1 % change" window (only a few
  minutes at high power), which false-latched healthy batteries whose SOC
  reading legitimately sticks near the ends (~10 % / ~90 %, BMS
  balancing/clamping). The watchdog is now energy-based and banded: it
  accumulates the plan's expected battery throughput while the reading is
  frozen and latches only past the configured band allowance — the edge
  bands tolerate much more drift. Below 300 W expected flow the accumulation
  pauses instead of resetting, so duty-cycled flow still counts.

## [0.17.0] - 2026-07-31

### Security
- **Export services hardened.** Exports are now confined to
  `<config>/battery_manager/` (plain) and `<config>/www/battery_manager/`
  (downloads) — previously any writable path under `/config` was accepted,
  including HA-internal state like `.storage/core.config_entries`. `.storage`
  is now refused explicitly and only `.txt`/`.json` suffixes are allowed.
  Download files are deleted automatically after 1 hour (the `/local/` tree
  is served without login and the learned-profile export contains household
  presence/consumption patterns); a startup sweep removes leftovers whose
  timer died with the previous run, and the notification now warns about the
  unauthenticated reachability.
- **Service failures are raised, not just logged.** Invalid `file_path` or
  unknown `entry_id` raise `ServiceValidationError`, missing export data
  raises `HomeAssistantError` — automations and the UI now see the failure.

### Changed
- **Single instance only.** A second config entry is aborted
  (`single_instance_allowed`): two planners would fight over the same
  physical switches.
- **Breaking: export paths moved** (see Security). Existing automations
  passing a custom `file_path` outside the new directories must be updated.
- **Base dimensions editable after setup.** Battery (capacity, SOC bounds),
  PV (peak power, forecast windows) and power (charger/inverter ratings,
  efficiencies) moved into the options flow — changing them no longer
  requires deleting the entry (subentries and learned data survive).
- **Gate pipeline consolidated** in the planner core (shared
  `_spread_candidate`/`_gate_trial`/`_accept_candidate` helpers replace the
  duplicated pass-1/pass-2 blocks; golden snapshots prove bit-identical
  behaviour). The serviceability gate now uses the same SOC cutoff as the
  simulator (`max(soc_min, inverter_min)`).
- **Docs consolidated.** Goal hierarchy now consistently references
  F-STRICT-SURPLUS (never import for optional loads → SOC high → absorb
  late), stale doc labels fixed, and the deliberate scope boundaries (no
  grid power limit, plan-side DST tolerance, counter-reset handling,
  privacy, BMS-owned battery protection) are written down.
- **Card: full accessibility + robustness overhaul.** All numeric/array
  attributes are validated defensively (a malformed entity can no longer
  crash the render; oversized series are capped), a render error shows an
  in-card message, and the chart is keyboard-navigable with an
  `aria-label`, a screen reader summary and a WCAG-AA-checked palette.
  `setConfig` now validates types strictly: a YAML `hours: "48"` string is
  rejected with a clear error instead of being silently coerced.

### Added
- **Fail-safe on sustained data loss (D-A8 stage 2).** Entities go
  `unavailable` immediately when the SOC (6 h) or forecast (72 h) caches
  expire; after 2 more hours of data loss all managed surplus loads are
  force-switched off once (charge-enable always, plug follows
  `input_off_policy`/ownership), with a repair issue and push notification
  until recovery.
- **Adaptive house-battery SOC watchdog.** A frozen SOC sensor (unchanged
  while the last valid plan expects > 300 W of battery flow for longer than
  the time a 1 % SOC change needs, capped at 60 min) is treated as stale
  and escalates through the normal data-loss path.
- **Support-switch failure escalation.** Three consecutive actuation
  failures (including unconfirmed make-before-break) raise the
  `support_switch_failed` repair issue plus push notification.
- **Recorder robustness.** Recorder queries time out after 5 minutes
  (`learning_recorder_timeout` issue, self-resolving), a missing recorder
  integration raises `learning_no_recorder` once instead of log-spamming,
  and both stores survive version mismatches with a warning instead of a
  crash.
- **Diagnostics include redacted subentry options** (plus a privacy note:
  the dump contains consumption data — share carefully).
- **Services metadata.** `services.yaml` now carries names/descriptions, a
  typed `config_entry` selector for `entry_id`, and de/en translations.
- **Coverage gates in CI.** The pure planner core is held at 100 % line
  coverage (dedicated step), the total integration at ≥ 95 %
  (`fail_under`), enforced in the `tests` and `devcontainer` jobs.

### Fixed
- **Slot-0 energy overestimation.** The first planning slot ignored the
  seconds fraction of `now`, overestimating every slot-0 energy by up to
  59 s; an empty horizon right before midnight is handled.
- **Corrupt PV forecasts are clamped.** Negative and NaN forecast values no
  longer propagate as phantom load or silent hoarding.
- **Counter-reset hours are discarded** instead of learned as 0 W
  consumption (negative balance hours in the profile learner).
- **Core input validation.** The planner dataclasses fail fast
  (`ValueError`) on zero capacity, out-of-range efficiencies and invalid
  battery tolerances instead of crashing later or inverting energy flows;
  `simulate()` checks input series lengths up front.
- **Appliance advisor no longer approves a start on an empty horizon.**
- **Stale `SupportPathSensor` entities** are only created for configured
  paths and leftovers are cleaned up.
- **Empty load/appliance names rejected** in the subentry flows.
- **Flaky time-dependent tests pinned** (startup-grace boundary).
- **Translations: dropped the deprecated `config_subentries.<type>.title`
  keys** (hassfest `[TRANSLATIONS]` warning). The title moved out of the flow
  sections back in HA 0.109; `entry_type` already carries the identical text,
  so nothing visible changes — this only silences the warning.

## [0.16.3] - 2026-07-30

### Changed
- **Dev setup 2026 (infrastructure only, no runtime change).** One-command
  onboarding via devcontainer (`.devcontainer/`, Python 3.14) or
  `uv sync --group dev` — the committed `uv.lock` is the single source of
  truth for every dev-tool version (minimum versions `>=` in `pyproject.toml`,
  exact ones in the lock; only `pytest-homeassistant-custom-component` stays
  exact-pinned because it pins homeassistant itself and thus leads the HA
  coupling). The devcontainer is not just a local option: a dedicated CI job
  (`devcontainers/ci`) builds it on every push/PR and runs the full test
  suite, ruff and mypy inside the container — verified locally the same way
  with the devcontainer CLI. CI installs through `astral-sh/setup-uv` + the
  dependency group instead of floating `pip install`, and its actions are
  pinned
  (`hacs/action@22.5.0`, hassfest master SHA); Dependabot now also tracks the
  uv ecosystem. New: `.vscode/` (ruff formatter, pytest tasks), pre-commit
  hooks (ruff check + format), a mypy baseline over `core/`, pytest-cov with
  the coverage XML as a build artifact, `.gitattributes` enforcing LF, and
  `AGENTS.md` as the entry point for coding agents.
- **Dev environment moved to Python 3.14 / homeassistant 2026.7.4** (phacc
  0.13.348; HA >= 2026.3 requires Python >= 3.14.2). Test-side fix: the
  config-flow suite now unloads its entries via an autouse fixture — under
  HA 2026.7's eager task scheduling the coordinator's refresh-interval timer
  was armed inside the test body and phacc's `verify_cleanup` flagged it as a
  lingering timer (flaky, order-dependent). ruff target is `py314` (PEP 758
  unparenthesized except tuples, syntax only).

## [0.16.2] - 2026-07-25

### Fixed
- **Latch hold follows the planner via a shadow plan (F11).** The F10 hold
  gate (slot-0 PV >= effective load power) was stricter than the normal
  planner, which also activates a load without direct surplus (early-morning
  battery-fed pass-2 runs "covered by otherwise-lost export" at PV ~ 0). A
  load under tank-full suspicion must not be held to a tighter rule than its
  normal state. The hold now fires exactly when a shadow plan (F5 saturated
  override cleared, computed only while a latched switchable load exists,
  never published) activates the load's slot 0. The PV comparison remains
  only as the over-power extra guard (measured >= 1.5x planning power).

## [0.16.1] - 2026-07-24

### Fixed
- **F5 latch deadlock (F10).** v0.16.0 planned a latched load at its measured
  ~0 W, but the F-L7 latch only clears while the load runs BM-commanded and
  in band — so the BM never commanded it again (live: dehumidifier planned
  0 kWh against 6.5 kWh forecast surplus even after the tank was emptied).
  Two recovery paths: the per-load reset button (= "tank emptied") now also
  clears a latched power warning (self-correcting if the tank is still
  full), and while latched the executor holds the load ON opportunistically
  whenever the surplus gates hold — a full tank draws ~2 W (free), an
  emptied tank instantly becomes the desired surplus run and releases the
  latch. Hold stops are flicker-ineligible; G4 aborts a hold immediately.

## [0.16.0] - 2026-07-24

Forensics-driven release (7-day decision audit 17.-24.07.2026).

### Fixed
- **T* bistability at the merge bound (F1, F-MERGE-HYSTERESIS).** The
  terminal credit now ramps continuously over the stressed clip margin
  (0-250 Wh) instead of flipping binary at the first Wh; horizon truncation
  moved to the ramp end. Executor G4 rec-off branch requires PV < running
  load power (planner parity); floor-guard warnings rate-limited.
- **Dwell x replan flicker (F8).** A recommendation returning within 5 min
  of a recommendation-driven stop restarts without min_off (ping-pong
  capped; G4/G1 stops unaffected).
- **Seamless plan continuation at raster edges (F9).** A min-runtime spill
  into a slot already booked for the same load is a continuation, not a
  conflict — root cause of the hourly :31 recommendation retractions.
- **Telemetry-freeze guard (F4).** Recommendation-only loads can reach the
  G2 stale latch; new 6 h freeze watchdog (SOC AND power exactly unchanged
  while recommended -> unavailable, one warning, auto-release).
- **Saturated-power feedback (F5).** A latched power warning plans the load
  at its measured draw — no more phantom full-power slots; the
  recommendation stays on so the latch can clear.

### Added
- **F-TANK (V6, opt-in).** Per-load `tank_full_runtime_min`: runtime counter
  = time since last emptying, auto-reset + median learning of the full-tank
  runtime, push notification when the tank is predicted full within 60 min
  of planned runtime. Notification/diagnostics ONLY — never preemptive
  curtailment (operator rule).
- Config-entry diagnostics (V7), 120 s startup grace for the SOC source
  (V8), one INFO line per confirmed switch action with reason (V9a).

### Documentation
- `in_house_measurement` semantics audited and documented (count-once
  accounting confirmed; option texts cover the outside-the-grid-meter
  case). README clarifies the forecast sensors are horizon sums. Work order
  for the balcony-solar-forecast repo (F6/F7) under `docs/orders/`.

## [0.15.1] - 2026-07-19

### Fixed
- **Cross-day daytime pre-drain (F-STRICT-SURPLUS R6).** A live v0.15.0 plan
  booked a single Sunday-14:00 dehumidifier run "covered by otherwise-lost
  export (215 Wh)" — a battery pre-drain in Sunday's own PV window to absorb
  MONDAY's clip, because Monday's absorption window was power-saturated
  (dehumidifier ~8 h + Fossibot, Monday still lost 1.09 kWh). It is import-free,
  floor-safe and reduces export by ~175 Wh, but it is a marginal "early bet on
  tomorrow's forecast" a full day ahead while the plan already rides the stress
  cutoff — which the operator rejected. R6 forbids it: a DAYLIGHT (`pv_wh > 0`)
  pass-2 bet must refill the same calendar day; only night/pre-dawn slots
  (`pv_wh == 0`) may pre-drain for a next-day clip (F-NIGHT-RESCUE keeps its
  carve-out). Keying on daylight rather than the strong-PV window is deliberate
  — an adversarial review showed a strong-window test leaked the bet one slot
  past the window edge (the afternoon taper, where the live 14:00 slot sits).
  Rare edge case — goldens and the F-NIGHT-RESCUE regressions are unchanged.

## [0.15.0] - 2026-07-19

### Fixed
- **Loads bought grid import and ran on optimism (F-STRICT-SURPLUS).** The
  2026-07-19 card made it visible: morning "in-window insurance" bets
  (scalar beta on empty forecast bands) kept the battery from ever reaching
  soc_max, cross-day bets financed today's runs from tomorrow's forecast
  clip, and the Z2' proportional trade budget (ratio 0.1 × rescued export,
  cumulative, no absolute cap) paid for ~1 kWh/day of REAL planned pre-dawn
  import — including load slots the planner's own simulation served from
  the grid below the 20 % cutoff (which the executor's G4 guard would have
  refused at runtime). Per the operator's lexicographic objective (1. loads
  never cause import, 2. SOC as high as possible, 3. absorb export as late
  as possible; lost surplus is the acceptable price):
  - **R1** the import gate is now the absolute `IMPORT_ARTIFACT_SLACK_WH`
    (50 Wh/horizon — ~10 Wh standby artifacts never veto a booking, rescued
    export never mints an import budget). `import_trade_ratio` is retired:
    ignored, removed from the options UI; stored entries keep parsing.
  - **R2** planner floor-guard parity: no booking covers a slot that, in the
    trial, is grid-fed (inverter off AND PV below the AC load) or touches the
    cutoff at either endpoint (soc_start or soc_end ≤ 20); every accepted
    booking is re-validated on each trial (closes the latest-first re-drain
    ratchet and the PV-eating below-cutoff case). A PV-served inverter-off
    slot (the full-battery hoard regime) is explicitly allowed — an
    inverter_on-only draft disabled the allocator on hoard days.
  - **R3** Z4 bets settle at the first slot where the TRIAL refills to
    soc_max, not at the same-day PV window end — daytime bets now face the
    same overnight stress as evening bets, so surviving bets land at the
    genuinely latest feasible slot.
  - **R5** the plan must still reach soc_max on every day the no-loads base
    reaches it (`preserves_daily_max`, both passes) — pre-conditioning
    (pre-drain to make room) stays welcome, but a bet that stops the battery
    filling to max (the card's 77 % peak) is refused. Objective 2 becomes a
    hard planner invariant, not an emergent stress-gate property. The
    priority powerstations reach their own target SOC as a consequence
    (house max first, then the priority load's target from the remaining
    export via the F-GATE-TOPUP final quantum, then the dehumidifier).
  Night pre-drain before stress-confirmed clip days keeps working (floors
  ramped at the crossover, artifact slack instead of trade); pass-1 direct
  surplus absorption is untouched. Expected price: more lost surplus on
  clip-eve days.

### Added
- Per-day `prevented_export_kwh` in the `daily` breakdown and card stats line
  (R4): the export the day's load runs prevent vs the no-loads base (both pre
  support-escalation) — the counterfactual that answers "why is a load
  running although SOC never reaches max?" directly on the card.

### Changed
- Golden `s3_loads_night`, `s3_low_soc_5am`, `s4_midday_full` re-blessed:
  with the artifact slack, night pre-drains book at ratio 0 (the ~10 Wh
  standby veto is gone) — export drops 2.7-4.0 kWh per scenario, import
  rises only by the ≤50 Wh artifact slack, and drain depth is bounded by
  R2 exactly above the cutoff (min SOC 21.1) instead of accidentally by
  the standby artifact.

## [0.14.0] - 2026-07-18

### Fixed
- **818 W phantom planning power (F-ROBUST-POWER).** At the 06:21 inverter
  cutoff a 1711 W compressor-restart transient (held ~60 s by the measuring
  plug) was blended into the power EMA (0.3·1711 + 0.7·433 ≈ 818) and frozen
  by the run-max rule — a 426 W dehumidifier was planned at 818 W all
  morning, squeezing the fossibot into 30-min scraps. The EMA/run-max
  learner is replaced by a robust TIME-WEIGHTED MEDIAN over the run's last
  ~30 min: short spikes/dips (inrush, defrost, transfer transients) can
  never move it, sustained changes are adopted (~15 min, or ~10 min via the
  stable fast-adopt window — operator-changed charge rates included), and
  the first 5 min of a run never learn (inrush kill). No nominal clamp
  (levels above nominal are legitimate); estimates above 3× nominal log a
  change-gated warning. Old persisted values self-heal on the first run.
- **50 % duty-cycling at quantum boundaries (F-SEAMLESS-RUNS).** The
  executor force-offed at every booked-quantum end while the replan re-booked
  seconds later, and min_off then enforced a pointless 30-min pause (relay
  cycles, compressor restarts — which also fed the 818 W incident). Per the
  operator's min_off model ("mindestens die konfigurierte Zeit aus, wenn
  ABSICHTLICH deaktiviert"), an expired deadline with a freshly re-booked
  contiguous run now EXTENDS seamlessly: no OFF, no dwell stamp. min_off
  arms only on deliberate stops (surplus gone, target reached, floor guard,
  no re-booking). Energy-limited loads extend only while the G2 stale-SOC
  guard can supervise them — everything G2-unsupervisable keeps the R7/R8
  duty-cycle cap.

### Changed
- Runs shorter than ~5 min no longer produce or learn a measured power
  (warm-up). Appliance planning is explicitly unaffected by learning — it
  always uses the declared run energy/duration (documented clarification).

## [0.13.1] - 2026-07-18

### Fixed
- **Surplus loads could run grid-fed at the inverter floor (G4 floor
  guard).** Incident 2026-07-18 06:20-06:30: the plan deactivated a booked
  dehumidifier run, but the min_runtime ON→OFF dwell held the switch for
  another 10 minutes while the battery hit the 20 % inverter cutoff — the
  real inverter shut down and the 432 W load drew from the GRID. New binding
  operator rule: when the inverter is off or the SOC reaches the inverter
  floor, surplus loads must not be actuated. The executor now computes a
  floor guard every cycle (SOC at/below `inverter_min_soc_percent` OR
  inverter recommendation off): running controlled loads are forced off
  dwell-exempt (min_off still gates the re-on), nothing switches on, the
  published per-load `active` and the appliance start-window advisory read
  False (operator automations stop too). The SOC branch latches and releases
  only at floor + `hysteresis_percent` (no flapping at the floor); reaction
  time is the ~5 s debounced SOC refresh instead of up to a full dwell.
  Surfaced as `floor_guard_active` on the inverter-recommendation sensor.
  Planner unchanged — G4 is the hard executor backstop against forecast
  error (docs/LOAD_CONTROL.md §11, spec docs/F-EXECUTOR-GUARDS.md G4; named
  G4 — G1-G3 were taken). Review hardening: queued switch-ONs are dropped
  when the guard trips while a switch task is in flight (plus a catch-up
  refresh for the deferred forced OFF); trip/release stay disjoint even at
  `hysteresis_percent` 0; runtime accrual and the power warning treat loads
  as inactive during a guard episode (no false 0 W "full tank" warning).

## [0.13.0] - 2026-07-18

### Changed
- **Gate parity: priority always wins over load class (F-GATE-PARITY,
  operator decision 2026-07-17).** Energy-limited loads (Fossibot
  powerstations) now face the IDENTICAL pass-2 gate set as continuous
  loads — the shared Z2' import-trade invariant (anchored at the no-loads
  base), the rt-honest c1 refill gate, the optimistic c2 (beta) in-window
  insurance and the Z4 windowed stress floor. The former class carve-out
  (strict no-extra-import, c1-only) let a lower-priority dehumidifier take
  make-room/battery-share bet energy a higher-priority powerstation was
  forbidden, silently overriding the configured load priority. Now the
  priority (config order) alone decides contested energy. The single
  remaining class rule: energy-limited loads never book zero-PV (night)
  slots — nights stay reserved for continuous loads.
  Consequences to expect live: a hungry powerstation may now consume the
  trade/c2 budget ahead of the dehumidifier (its make-room and night
  pre-drain hours can shrink accordingly — intended), and powerstation
  charging may cause small traded grid import within the shared
  `import_trade_ratio` budget.
- **`stressed_min_soc` diagnostic covers all loads.** The SOC-forecast
  attribute now reports the stressed reserve of the earliest pass-2 booking
  of ANY load (previously continuous-only); the value may shift after the
  update — observability only.
- Golden `short_peak_preempt`: the powerstation's pre-window pass-2 hours
  sat in zero-PV (two-window model) morning hours and are now barred by the
  night rule (planned energy 1800 → 900 Wh in that synthetic scenario;
  import unchanged). With real hourly forecasts, morning hours carry light
  and pre-window pre-charges remain available — the adapted tests model
  this with a 5 Wh dawn-shoulder (`_twilight`).

## [0.12.0] - 2026-07-12

### Added
- **Per-load power-warning dwell + off by default (operator wish).** The 30-min
  dwell before a load's power-deviation warning fires is now a per-load setting
  ("power warning dwell", default **15 min**), so the delay is tunable per
  device. The warning itself now defaults to **off** for new loads (deviation %
  default `50 → 0`); the operator opts each device in. Existing loads keep their
  stored percentage, so a load that was already warning stays on — and now also
  gets the shorter default dwell.
- **Built-in push notifications for power warnings (operator wish).** A new
  *Power-warning notifications* section in the integration Options lets you pick
  a global list of `notify` targets (e.g. each person's companion app). When any
  load's power warning trips a push is sent to every target, and — unless the
  "Also notify when resolved" toggle is off — another when it clears. No targets
  configured = no push; the `binary_sensor` stays available for custom
  automations regardless.

### Changed
- **The power warning now latches (operator report).** Once on, it stays on
  until the load runs at its configured power again *at the integration's
  request* — it no longer self-clears the moment BM stops requesting the load (a
  full water tank is still full while the load is off). The latch is persisted,
  so saving the integration options (which reloads the coordinator) or a restart
  no longer silently drops a raised warning.

## [0.11.2] - 2026-07-12

### Fixed
- **Midday T\*=95 hoard on clipping days (F-NIGHT-RESCUE F2 v2).** With the
  v0.11.0 merge-bounded threshold search, a clipping day whose truncated scan
  window carried a substantial DC load could pin the discharge threshold at the
  maximum SOC (95 %), hoarding the battery across the whole forecast instead of
  draining it to make room for the clipping. On a merge-truncated window the
  battery is full at the merge point by construction, so the cost function's
  terminal-value credit is meaningless there — and a DC load breaks its exact
  cancellation with import (DC is served from the battery without the inverter,
  while the credit assumes the inverter's discharge efficiency), turning the
  threshold into an ill-conditioned knife-edge. The credit is now dropped on
  truncated windows, leaving a cost that is monotonic in the threshold, so the
  scan deterministically drains ahead of the clip. Full-horizon threshold
  choice, the night pre-drain, and all goldens are unchanged.

## [0.11.1] - 2026-07-12

### Added
- **Per-load today/tomorrow planned energy on the forecast card.** Each surplus
  load in the SOC-forecast sensor's `loads` attribute now also carries
  `today_kwh` / `tomorrow_kwh` (the load's planned energy partitioned over the
  days its run hours start in, anchored on the plan's slot-0 day exactly like
  the aggregate figures), and the card's legend shows each load as
  `heute/morgen` (e.g. `Entfeuchter Keller (2.5/4.0 kWh)`) — the slash format
  is already explained once by the subtitle's trailing legend. Falls back to
  the horizon total for a pre-0.11.1 backend or a load that only runs on day 3+.

## [0.11.0] - 2026-07-12

### Fixed
- **Night pre-drain physically possible again: round-trip-honest c1
  (F-NIGHT-RESCUE F1).** The refill gate demanded that a booking's export drop
  reach `(1-tol) x energy`, but a pure AC->battery->AC detour can only return
  ~82 % of the energy as rescued export — so every 22:00-05:00 booking failed
  by construction, and the planner left the battery full at dawn while a
  known ~3.3 kWh of next-day clipping was lost (incident 2026-07-11/12). The
  need is now `(1-tol) x energy x rt` with rt derived from the configured
  charger/battery/inverter efficiencies; direct-PV runs are unaffected and
  the import-trade/floor gates still bound how deep the drain may go.
- **The 04:13 threshold jump: merge-bounded T* search (F-NIGHT-RESCUE F2).**
  A weak final forecast day made the full-horizon optimiser hoard battery for
  the post-clip night and apply that hoarding to TONIGHT as well (live: T*
  20->58 at 04:13, inverter off, pre-drain dead). The candidate scan is now
  truncated at the first slot where the battery is provably full and clipping
  even under the stressed PV (merge principle: beyond that slot the
  trajectory is independent of today's threshold; minimum 6 slots, full
  horizon when no stressed clip exists). New `threshold_horizon_end`
  diagnostic on the SOC-forecast sensor shows when the bound is active.

### Changed
- **Crossover buffer ramp for the Z4 stress floor (F-NIGHT-RESCUE F3).** The
  buffer component of the pre-drain stress floor now ramps with the remaining
  stressed deficit until the stressed PV crossover — the closer the (stressed)
  sunrise, the less forecast-error buffer the inverter reserve needs
  (operator principle). The absolute battery floor (Z3) stays static.

## [0.10.2] - 2026-07-11

### Changed
- **Forecast card shows today/tomorrow per metric (bundled UI for
  F-PERDAY-SURPLUS).** The card's subtitle now renders grid import, lost
  surplus AND surplus-load energy as `today/tomorrow` (e.g.
  `Netzimport 0.3/0.1 · verlorener Überschuss 4.6/0.0 · Überschusslasten
  9.8/4.2`), reading the per-day `daily` breakdown, with a single trailing
  legend `(kWh · today/tomorrow)` so the slash format is explained once. Falls
  back to horizon totals on a pre-0.9.1 backend. The card ships with the
  integration, so it refreshes on update (cache-busted by the version).


## [0.10.1] - 2026-07-11

### Added
- **Per-day surplus-load energy in the daily breakdown (F-PERDAY-SURPLUS §5
  v2).** Every entry of the `daily` list now also carries `loads_kwh` — the
  surplus-load energy the final plan schedules that calendar day (summed from
  the trajectory's per-slot `extra_ac_wh`; appliances are not included, they
  enter the AC forecast instead). The SOC-forecast sensor exposes the matching
  `loads_today_kwh` / `loads_tomorrow_kwh` convenience attributes with the
  same today/tomorrow convention as the v0.9.1 per-day sensors (slot-0 day,
  +1 day, 0.0 when the horizon lacks the day) — so the operator's tile can
  show lost surplus, grid import AND planned load energy per day from one
  source.

## [0.10.0] - 2026-07-11

### Added
- **Per-slot P10/P90 forecast bands replace the scalar uncertainty dials where
  evidence exists (F-QUANTILE-BANDS).** The balcony forecaster's empirical
  quantile curves (`wh_period_p10`/`wh_period_p90` attributes on the same
  three PV entities) now drive the planner's uncertainty handling per slot:
  the Z4 pre-drain stress uses `p10/median` (clamped 0.1-1.0) and the c2
  in-window insurance uses `p90/median` (clamped 1.0-2.0) on every slot with a
  real band; slots without one keep the configured alpha/beta scalars — the
  dials become per-slot fallbacks, no config changes. A COLLAPSED band
  (p10 == p90, the cold-start "no evidence" signature), a median below 25 Wh
  or a spread below max(1 Wh, 1 %) never counts as a band, so cold bins can
  never weaken the Z4 stress below the scalar alpha. With no band data
  anywhere plans are bit-identical to v0.9.3 (goldens unchanged, verified).
  Insurance bookings backed by evidence read "in-window insurance (p90)" in
  the explain-plan reasons, and the SOC-forecast sensor's new
  `quantile_coverage` attribute shows the per-day daylight band coverage
  ("p10/p90" | "mixed" | "scalar") as the bins mature. Recommended posture
  once coverage settles: set the upper PV reserve (beta) to 1.0 — insurance
  then fires only where P90 evidence exists.

## [0.9.3] - 2026-07-11

### Fixed
- **Stall band: gate-equipped loads now reach their target SOC
  (F-GATE-TOPUP).** An energy-limited load could never be re-booked once its
  remaining energy fell below one min-runtime quantum's commitment
  (`max(planning power, nominal) x min_runtime`): no smaller candidate existed
  and the saturation gate rejected all others, so the F2400-B (learned ~600 W)
  was unbookable above 75 % SOC and chronically parked at ~85-89 % instead of
  90 % — learned power had widened the band. Loads WITH a charge-enable gate
  now get ONE final candidate `rem / max(planning power, nominal)` appended
  after the quantised list, offered exactly when every standard candidate
  would fail the saturation gate — safe because the dwell-exempt target stop
  (v0.9.0 G1) delivers exactly the remaining energy for this class. A 50 Wh
  de-minimis floor prevents relay churn; all planner gates apply unchanged;
  plug-only loads keep the old behaviour (their dwell really would overshoot).
  Such bookings carry the explain-plan marker ", final top-up to target".

## [0.9.2] - 2026-07-11

### Changed
- **Pass 1 rescues present export first (F-RESCUE-EXPORT).** Energy-limited
  loads now place their direct-surplus (pass-1) hours **earliest-export-first**
  (ascending), reverting the v0.9.0 day-bounded latest-first order for pass 1.
  A pass-1 candidate only ever sits on a slot that is already full and
  exporting, so deferring rescues no extra energy but loses the present,
  certain surplus to a later forecast bet — observed live as a Fossibot with
  room sitting idle at 73.9 % while the house battery was at 99 % and ~1.7 kW
  was exported. The load now runs as soon as export occurs. Pass 2 (preemptive
  bookings before saturation, where the battery can still buffer) stays
  latest-first, unchanged. Load-outer priority, learned power, explain-plan and
  the exact re-simulated surplus reads are unchanged.

## [0.9.1] - 2026-07-11

### Added
- **Per-day lost-surplus and grid-import breakdown (F-PERDAY-SURPLUS).** The
  coordinator aggregates the final planned trajectory per calendar day (grouped
  by each slot's planner-local start day) into a `daily` list of
  `{date, lost_surplus_kwh, grid_import_kwh}`; the sums equal the existing
  totals (rounding aside). The `lost_surplus_forecast` and `grid_import_forecast`
  sensors gain `today_kwh` / `tomorrow_kwh` / `daily` attributes (today = date of
  slot 0, tomorrow = today + 1, a missing day rendering 0.0), and the SOC-forecast
  sensor exposes the same `daily` list as a single source for dashboard cards. No
  planner or config change.
- **Reconfigure flow (F-RECONFIGURE-PV).** The battery SOC sensor and the three
  PV forecast sources can now be changed from the integration's Reconfigure
  entry without re-adding it, so a PV-source cutover (e.g. to a more accurate
  forecast) keeps every load subentry with its priority, learned power and
  runtime counters. Only those four entities change; all other settings and the
  loads are preserved, and the entry reloads onto the new sources.

## [0.9.0] - 2026-07-10

### Added
- **Learned planning power (F-PLANNER-HONESTY F1).** The coordinator learns per
  load the run-maximum of the accepted-sample power EMA (taper-proof,
  spike-proof — the run-max starts at a run's second accepted sample so a
  start-up spike is never learned verbatim — and standby samples never feed
  it) and persists it across restarts, so an OFF load is planned at its real
  power instead of the configured nominal (F2400-B: ~505 W real vs 300 W
  configured — all gates and the booked energy were ~40 % under). Precedence:
  live measured > learned > nominal; exposed as `learned_power_w` in the
  per-load diagnostics.
- **Explain-plan (F-PLANNER-HONESTY F3).** Every accepted booking records a
  terse reason string at acceptance time ("pass 1 @ 07-11 17:00: direct
  surplus, 30 min x 505 W, battery share 0%"), surfaced as `why` in the
  per-load `schedule` attribute of the SOC-forecast sensor.
- **Stale-SOC guard (F-EXECUTOR-GUARDS G2).** A load SOC that stays exactly
  unchanged for 12 minutes while the device demonstrably charges (feedback
  above the standby bar) is latched as stale: the load is held unavailable —
  the planner stops re-booking against the frozen value — until the sensor
  reports a different reading (the fossibot integration serves cached SOC with
  fresh timestamps, invisible to age checks). Change-gated WARNING/INFO logs;
  `soc_stale` in the per-load diagnostics.

### Changed
- **Pass 1 places energy-limited loads as late as possible within their day
  (F-PLANNER-HONESTY F2, R7 v2).** The direct-surplus pass is now load-outer in
  priority order (a load completes its pass-1 allocation before the next load
  sees the horizon) and walks the slots day-bounded latest-first for
  energy-limited loads — calendar days ascending, hours within each day
  descending — so a saturating residual takes the LATEST export hours of the
  FIRST day whose surplus it can rescue, never stranding an earlier day's
  export ("as late as possible, just early enough to avoid export"; resolves
  the open decision O1 of F-RESIDUAL-TOPUP). Continuous loads keep ascending
  order. Candidate evaluation now reads the exactly re-simulated export
  instead of an intra-slot approximation. Imports never rise; marginal
  placements can shift (see regenerated goldens).
- **Target-SOC stop is dwell-exempt behind a charge-enable gate
  (F-EXECUTOR-GUARDS G1).** The plan-driven OFF of an energy-limited load at or
  above its target SOC skips the ON->OFF minimum-runtime dwell when a
  charge-enable entity is configured — the gate switches no load current path
  worth protecting, while every dwell minute overshoots the target at real
  power (~250 Wh in 30 min at ~505 W). Plug-only loads and loads without a SOC
  reading keep the full dwell; `min_off` still gates a re-on, so a SOC at the
  target cannot flap the switch.

### Fixed
- **Deprecated coordinator init (F-EXECUTOR-GUARDS G4).** The
  `DataUpdateCoordinator` is now constructed with `config_entry=` — newer HA
  cores hard-error on the implicit ContextVar lookup.

## [0.8.2] - 2026-07-10

### Added
- **Configurable per-load priority (F-LOAD-PRIORITY).** The load dialog gets a
  required "Priority" field (1 = highest) directly after the name, with
  insert-shift semantics: assigning load X priority P moves it to position P
  and renumbers all loads densely 1..N, so the other loads shift accordingly.
  Initial/default priority follows the creation order (a new load appends,
  exactly the previous behaviour), and existing installs keep their order
  unchanged until a load is first saved. The planner core is untouched —
  priority materialises purely as the order of `SystemConfig.loads`; the
  SOC-forecast sensor's `loads` attribute therefore now lists loads in
  priority order.

## [0.8.1] - 2026-07-10

### Fixed
- **Night trickle-charge incident (F-RESIDUAL-TOPUP).** An energy-limited load a
  residual (< nominal x 1 h) short of its target SOC could only book its partial
  current hour (slot 0), so a ~150 Wh top-up was charged from the house battery
  at night instead of waiting for the next PV window. With the device's
  self-discharge this produced a repeating night trickle-charge loop,
  round-tripping energy through the battery at ~80 % efficiency.

### Changed
- **Energy-limited loads quantise like continuous loads (F-RESIDUAL-TOPUP R1).**
  The planner now offers energy-limited loads the same `k · min_runtime` sub-hour
  candidate list as continuous loads, so a residual top-up is bookable in any
  slot and pass 2's latest-first order — not slot-0 geometry — decides its
  placement (typically the next PV window, or just before it). No new config; the
  no-import guarantee, the pre-drain gates, and whole-slot bookings are unchanged.
- **Executor caps energy-limited sub-hour runs (F-RESIDUAL-TOPUP R7-R9).** On the
  ON edge a sub-hour energy-limited run now freezes an off-deadline
  (`run_start + max(min_runtime, planned_run)`) as an upper cap over the primary
  target-SOC stop, so a stale load-SOC sensor cannot stretch a ~150 Wh top-up
  into a full-hour charge. Recommendation-only energy-limited loads get the same
  published-`active` cap.

## [0.8.0] - 2026-07-10

### Added
- **Hourly PV forecast (F-PREDRAIN F1).** The planner now reads the hourly
  `wh_period` attributes of the three configured forecast entities (Open-Meteo
  hourly buckets and 15/30-minute buckets, timezone-normalized) and lays them
  onto the slot grid; hours the attributes do not cover share the day's residual
  via the legacy two-window model. New option **PV forecast mode**
  (auto/hourly/daily); `daily` (or absent attributes) reproduces the previous
  behavior bit-identically. The SOC-forecast sensor reports the per-day source
  (`pv_source`).
- **Night pre-drain with a two-buffer safety model (F-PREDRAIN F2-F4).**
  Continuous surplus loads (e.g. a dehumidifier) can now be scheduled at night
  or ahead of the battery filling up, deliberately spending stored energy that
  the next PV window provably replaces from otherwise-lost export:
  - **Import trade rule** replaces the absolute "no extra import" veto for
    continuous loads: total plan import may exceed the no-loads baseline by at
    most `import_trade_ratio` (default 10 %) of the export the plan rescues.
    This unblocks bookings that a ~10 Wh charger-standby modeling artifact used
    to veto while hundreds of Wh of surplus were lost. Energy-limited loads
    (powerstations) keep the strict rule and are never night-charged from the
    house battery.
  - **Lower buffer (inverter reserve):** every pre-drain booking must survive a
    pessimistic re-simulation of its bet window (PV x `predrain_pv_confidence`,
    default 0.5, from the booking until the end of the next strong-PV window)
    without dipping below the inverter cutoff + buffer. The reserve therefore
    shrinks automatically toward the forecast solar onset.
  - **Upper buffer (absorption headroom):** inside a day's strong-PV window,
    hours are additionally booked when an optimistic re-simulation
    (PV x `upper_pv_reserve`, default 1.2) shows they would prevent export —
    keeping charge headroom free while significant production is still
    expected. The window end ("sun behind the house") is derived from the
    forecast via `strong_pv_cutoff_w` (default 200 W) with an optional fixed
    `pv_window_end_hour` override for the installation.
  - Pass 2 keeps its latest-first order, so pre-drain lands as late as the
    constraints allow (pre-dawn before earlier night hours).
- **Observability:** SOC-forecast sensor attributes `import_trade_used_wh`,
  `stressed_min_soc`, `pv_window_ends`, `pv_source`; one INFO log line per
  cycle while a night pre-drain booking is active.
- All six new knobs live in the system options (planner tuning section, DE/EN),
  with the recommended values active by default after the update; setting
  `import_trade_ratio` to 0 (or mode `daily`, factors 1.0) restores the exact
  v0.7.19 behavior without a downgrade.

## [0.7.19] - 2026-07-10

### Fixed
- **Per-load entities are now scoped to their config subentry.** Previously every
  per-load/appliance entity (recommendation, power-warning, BM-control switch,
  runtime sensor, reset button, appliance start-window) was attached to the
  config entry rather than the subentry, so deleting a load or appliance left its
  entities behind as stale "unavailable" registry rows. They are now added with
  `config_subentry_id`, so Home Assistant removes them automatically when the
  subentry is deleted. Existing entities are re-homed in place on the next
  start/reload (no duplicates), so the fix migrates transparently — the shared
  Battery Manager device and all config-entry-level entities are untouched.
- The appliance **start-window** binary sensor is now also removed when an
  appliance's **opportunistic** option is turned off (subentry kept), instead of
  lingering as a stale entity — matching the existing power-warning cleanup.

## [0.7.18] - 2026-07-10

### Added
- **Per-load active-runtime counter + reset.** Every surplus load now exposes a
  **runtime sensor** (minutes) in the Battery Manager device that counts the time
  the load **really runs** — measured from its power-feedback sensor when one is
  configured (so manual runs count too), otherwise from BM's own charging state —
  and a **reset button** to zero it. The counter accumulates with a capped
  per-cycle tick, so a normal update is never clipped while an HA restart gap, a
  stalled loop or a clock jump can never inflate it; the value is persisted across
  restarts (`TOTAL_INCREASING`, so long-term statistics treat a reset as a new
  period).

## [0.7.17] - 2026-07-09

### Added
- **Per-load "BM control" switch.** Every surplus load now exposes a switch in
  the Battery Manager device (on by default). Turning it **off** holds that load
  **unavailable** — the planner drops it and the executor switches it off the
  next cycle — so a device can be paused with one tap **without removing its
  control switch**; turning it back **on** resumes. The state is persisted across
  restarts. This is the same effect as wiring an `availability_entity`, but
  built in and created automatically per load.

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
