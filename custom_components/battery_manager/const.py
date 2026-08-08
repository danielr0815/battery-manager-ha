"""Constants for the Battery Manager integration."""

# De-minimis floor for the gate-stop final top-up (F-GATE-TOPUP R3): a
# constant, not a config key. Its consumer is the PURE planner core, which the
# standalone core test setup imports without this package — the authoritative
# definition therefore lives in core/optimize.py; re-exported here per R3's
# placement so HA-layer code has the canonical constants module to read.
# MERGE_TERMINAL_RAMP_WH (F-MERGE-HYSTERESIS) rides along for the same reason:
# it is consumed by the pure planner core, so it is defined next to its consumer
# in core/optimize.py and re-exported here as the canonical constants module.
from .core.optimize import (  # noqa: F401
    GATE_TOPUP_MIN_WH,
    MERGE_TERMINAL_RAMP_WH,
)

DOMAIN = "battery_manager"

INTEGRATION_NAME = "Battery Manager"
# The version is NOT hard-coded here (it drifted from manifest.json for
# releases): the device sw_version is read from the manifest at runtime —
# see BatteryManagerCoordinator.integration_version.

# Update behaviour
UPDATE_INTERVAL_SECONDS = 300
INITIAL_UPDATE_INTERVAL_SECONDS = 30
DEBOUNCE_SECONDS = 5
STARTUP_RETRY_ATTEMPTS = 5
# V8 (2026-07-23 incident): grace window after the first refresh during which a
# not-yet-available SOC source (e.g. the Victron sensor still booting after an
# HA restart) does NOT escalate to an ERROR + UpdateFailed. Within it the
# coordinator waits quietly (DEBUG) and retries at the short startup interval,
# holding any prior valid state; only after it does the normal error path run.
# A SOC dropout AFTER the first successful cycle is the steady-state failure
# (the _get_soc cache + stale-SOC guard own it) and keeps the old behaviour.
STARTUP_SOC_GRACE_S = 120

# Data validity limits
MAX_HISTORICAL_SOC_AGE_HOURS = 6
MAX_HISTORICAL_FORECAST_AGE_HOURS = 72
# A load's last-known SOC (cached while the device sleeps) is trusted for at
# most this long; beyond it the load plans as "empty" and self-heals on wake.
LOAD_SOC_CACHE_MAX_AGE_HOURS = 168  # 7 days

# D-A8 stage 2 fail-safe: once the coordinator has had NO valid SOC/forecast
# data for this long CONTINUOUSLY (every refresh failing, entities
# unavailable), all controlled surplus loads are force-switched OFF once —
# blind operation must never leave loads running (potentially grid-fed or
# draining the battery below its floor). Deliberately a constant, not a
# config key (same class as the G2/F4 guards).
STALE_LOAD_SHED_HOURS = 2

# --- Input entity config keys (base entry) ---
CONF_SOC_ENTITY = "soc_entity"
CONF_PV_FORECAST_TODAY = "pv_forecast_today_entity"
CONF_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow_entity"
CONF_PV_FORECAST_DAY_AFTER = "pv_forecast_day_after_entity"

# --- Learned consumption profiles (docs/CONSUMPTION_FORECAST.md) ---
# Measurement sources per path: a direct load sensor OR a generic counter
# balance (inflow/outflow entity lists, D-C1). All optional; learning is
# active per path as soon as a source is configured.
CONF_AC_LOAD_ENTITY = "ac_load_entity"
CONF_AC_BALANCE_IN = "ac_balance_in_entities"
CONF_AC_BALANCE_OUT = "ac_balance_out_entities"
CONF_DC_LOAD_ENTITY = "dc_load_entity"
CONF_DC_BALANCE_IN = "dc_balance_in_entities"
CONF_DC_BALANCE_OUT = "dc_balance_out_entities"
CONF_LEARNING_WINDOW_DAYS = "learning_window_days"
CONF_LEARNING_MAX_AGE_DAYS = "learning_max_age_days"
# Stufe 2 (D-C7/D-C8): recency weighting, dynamic-buffer clamps, holidays
CONF_PROFILE_HALF_LIFE_DAYS = "profile_half_life_days"
CONF_BUFFER_MIN_PERCENT = "buffer_min_percent"
CONF_BUFFER_MAX_PERCENT = "buffer_max_percent"
CONF_WORKDAY_ENTITY = "workday_entity"

# Hardcoded learning constants (documented in the spec, §4.1)
LEARNING_RUN_HOUR = 3  # local time of the nightly learning run
LEARNING_MIN_SAMPLES = 10  # per bin; absence bins need fewer
LEARNING_MIN_SAMPLES_ABSENCE = 5
LEARNING_RATE_LIMIT = 0.2  # max relative bin change per nightly run
LEARNING_CLAMP_AC_W = 3000.0  # plausibility clamp per hourly mean
LEARNING_CLAMP_DC_W = 1000.0
LEARNING_NEGATIVE_RESIDUAL_WH = 10.0  # below -x Wh counts as suspicious
LEARNING_VACATION_MIN_HOURS = 12.0  # vacation-mode share tagging a day
LEARNING_HOLIDAY_MIN_HOURS = 12.0  # workday-sensor "off" share tagging a day
LEARNING_BIAS_ALERT_DAYS = 14  # one-sided P50 bias for this long -> repair
LEARNING_BIAS_ALERT_SHARE = 0.15  # ... when |bias| exceeds this load share
VALIDATION_HISTORY_DAYS = 30  # kept watchdog entries per path
# Hard timeout for every recorder executor call of the learner (LTS fetch
# and state-history chunks): a hung recorder DB (worn SD card, locked
# SQLite file) must not block the nightly learning run forever — the run
# holds the learner lock, so an unbounded wait would silently stall every
# future run. 300 s is generous for a weekly chunk yet bounded; a timeout
# raises a repair issue and the next run retries.
RECORDER_TIMEOUT_S = 300
# Store ENVELOPE major version — pinned at 1 forever: bumping the Store
# major would make HA's default _async_migrate_func raise on old files and
# crash the whole entry setup after an update. Schema changes are handled
# solely via the INNER data["version"] field below (mismatch = discard +
# fresh backfill; the source data is always re-fetchable).
LEARNED_STORE_MAJOR = 1
# v2: bins carry {p50, p80} quantiles (Stufe 2).
LEARNED_STORE_VERSION = 2
LEARNED_STORE_KEY = "learned_profiles"  # f"{DOMAIN}.{key}.{entry_id}"

# --- Support paths (docs/ALGORITHM.md D-A9) ---
CONF_SUPPORT_DC48_SWITCH = "support_dc48_switch_entity"
CONF_SUPPORT_DC48_POWER_W = "support_dc48_power_w"
CONF_SUPPORT_DC24_SWITCH = "support_dc24_switch_entity"
# Optional power sensor of the 24 V grid PSU: makes the DC->AC load shift
# during PSU operation exactly correctable while learning consumption
# profiles (docs/CONSUMPTION_FORECAST.md D-C2 step 3). Without it, hours
# with a PSU-fed 24 V rail cannot be learned.
CONF_SUPPORT_DC24_POWER_ENTITY = "support_dc24_power_entity"
CONF_DCDC_SWITCH = "dcdc_switch_entity"
CONF_SUPPORT_SWITCH_DELAY_S = "support_switch_delay_s"
# Escalation thresholds (D-A9): four ABSOLUTE battery-SOC % that drive the
# two grid-support hysteresis loops, independent of the planning buffer.
# Defaults 10 / 11 / 5.5 / 10 reproduce the legacy hard-coded thresholds at
# the default battery config. See ControlParams for the ordering rule.
CONF_SUPPORT_DC24_ACTIVATE_SOC = "support_dc24_activate_soc"
CONF_SUPPORT_DC24_RECOVERY_SOC = "support_dc24_recovery_soc"
CONF_SUPPORT_DC48_ACTIVATE_SOC = "support_dc48_activate_soc"
CONF_SUPPORT_DC48_RECOVERY_SOC = "support_dc48_recovery_soc"
# Support-actuator failure escalation (F7/U5): after this many CONSECUTIVE
# failed switch operations in the support path (failed service call OR an
# unconfirmed make-before-break sequence) a repair issue + push is raised.
# No retry counter lives here — the 5-min cycle already re-derives
# desired != state and retries implicitly; this only surfaces PERSISTENT
# actuator failure to the operator. A constant, not a config key.
SUPPORT_SWITCH_FAIL_ALERT = 3

# --- F-N3 two-bus device parameters (docs/DC_TOPOLOGY.md, phase 2) ---
# All NEUTRAL by default (share 100 %, efficiencies 1.0, 0 A = uncapped),
# so the upgrade changes nothing until the operator enters real values.
# Battery voltage sensor for the (later) voltage-gated 48 V controller.
CONF_BATTERY_VOLTAGE_ENTITY = "battery_voltage_entity"
# Fixed native-48 V base load (W) carved off before the rail split.
CONF_NATIVE48_BASE_W = "native48_base_w"
# Fraction of the remaining DC load on the 24 V rail (rest = native 48 V bus).
CONF_DC24_SHARE_PERCENT = "dc24_share_percent"
# DC/DC converter (battery 48 V -> 24 V rail).
CONF_DCDC_OUTPUT_VOLTAGE_V = "dcdc_output_voltage_v"
CONF_DCDC_EFFICIENCY = "dcdc_efficiency"
CONF_DCDC_MAX_CURRENT_A = "dcdc_max_current_a"
# Grid-fed 24 V support PSU (feeds the rail when the DC/DC is off).
CONF_PSU24_OUTPUT_VOLTAGE_V = "psu24_output_voltage_v"
CONF_PSU24_EFFICIENCY = "psu24_efficiency"
CONF_PSU24_MAX_CURRENT_A = "psu24_max_current_a"
# 48 V support PSU (nameplate; voltage gate lives from phase 3).
CONF_PSU48_OUTPUT_VOLTAGE_V = "psu48_output_voltage_v"
CONF_PSU48_EFFICIENCY = "psu48_efficiency"
CONF_PSU48_MAX_CURRENT_A = "psu48_max_current_a"
# 48 V PSU voltage gate as an SOC proxy (phase 3): the PSU only delivers
# while the battery SOC is below this value. 100 = always open (neutral).
# Calibrate from the observed voltage-crossing bracket (diagnostic).
CONF_GATE_SOC_PERCENT = "gate_soc_percent"
# Series cell count (informational: derives V/cell for the gate hint).
CONF_BATTERY_CELLS_SERIES = "battery_cells_series"

# --- R2 voltage controller for the manual 48 V mode (docs/DC_TOPOLOGY.md §6) ---
# While the 48 V PSU is in manual mode AND a battery-voltage sensor is set,
# the controller switches the PSU on below `on_voltage` and off above
# `off_voltage` (asymmetric hysteresis + dwell). Log-only by default: it
# logs its decisions without actuating until the operator arms it after a
# shakedown. Without a voltage sensor the manual mode stays F-N2 hands-off.
CONF_PSU48_ON_VOLTAGE_V = "psu48_on_voltage_v"
CONF_PSU48_OFF_VOLTAGE_V = "psu48_off_voltage_v"
CONF_PSU48_CTRL_LOG_ONLY = "psu48_controller_log_only"
# Dwell/plausibility constants (not per-install configurable in v1).
DC48_CTRL_DWELL_ON_S = 60
DC48_CTRL_DWELL_OFF_S = 300
DC48_CTRL_FAILSAFE_MIN = 10  # invalid voltage this long -> fail-safe PSU on
DC48_CTRL_VOLTAGE_MIN = 40.0  # plausibility window for the sensor
DC48_CTRL_VOLTAGE_MAX = 60.0

# --- Surplus load subentry keys ---
SUBENTRY_TYPE_LOAD = "surplus_load"
CONF_LOAD_NAME = "name"
# Explicit per-load priority (int >= 1, 1 = highest; F-LOAD-PRIORITY R1).
# Deliberately NOT in DEFAULT_LOAD_CONFIG: the effective order is resolved
# centrally (ordered_load_subentries, R3) with the insertion position as the
# legacy fallback, so a per-load default would shadow that fallback.
CONF_LOAD_PRIORITY = "priority"
CONF_LOAD_POWER_W = "power_w"
CONF_LOAD_BATTERY_TOLERANCE = "battery_tolerance_percent"
CONF_LOAD_MIN_RUNTIME_MIN = "min_runtime_min"
CONF_LOAD_MIN_OFF_MIN = "min_off_min"
CONF_LOAD_ENERGY_LIMITED = "energy_limited"
CONF_LOAD_CAPACITY_WH = "capacity_wh"
CONF_LOAD_TARGET_SOC = "target_soc_percent"
CONF_LOAD_SOC_ENTITY = "soc_entity"
CONF_LOAD_POWER_ENTITY = "power_entity"
# Optional kWh energy counter of the load itself (e.g. a Fritz!Powerline
# energy sensor). Feeds the realized surplus accounting (F-REALIZED-SURPLUS):
# measured consumption replaces the runtime x power estimate for "prevented
# export realized" and — for loads marked out-of-house (CONF_LOAD_IN_HOUSE
# False) — corrects the export meter their draw flows through. Optional key,
# no migration (absent = estimate as before).
CONF_LOAD_ENERGY_ENTITY = "energy_entity"
CONF_LOAD_AVAILABILITY_ENTITY = "availability_entity"
CONF_LOAD_CONTROL_SWITCH = "control_switch_entity"
CONF_LOAD_CHARGE_ENABLE = "charge_enable_entity"
CONF_LOAD_INPUT_OFF_POLICY = "input_off_policy"
# Load location relative to the grid-consumption measurement point (§2.3;
# V3 semantics, 2026-07-24). This flag governs ONLY the baseline (Grundlast)
# learning in history_profile.py — it decides whether the load's own draw is
# already contained in the measured house load and must be subtracted out:
#   True  (default): the load sits BEHIND the measuring node (EM540 / grid
#          meter), so its draw IS in the measured house load and the learner
#          subtracts it while learning the AC baseline.
#   False: the load is supplied OUTSIDE the measured node — via a feed-in
#          setpoint, OR simply on a circuit/socket the grid meter does not
#          see (e.g. the cellar dehumidifier whose supply shows up 1:1 as
#          "export" on an EM540). It is NEVER in the measured house load, so
#          the learner must NOT subtract it; subtracting it would double-count
#          the removal and bias the learned baseline low.
# The flag deliberately does NOT touch surplus planning: a surplus load is
# modelled as ADDITIONAL AC consumption (extra_ac_wh in optimize.allocate_loads)
# for BOTH values, so it is counted exactly once — subtracted in learning AND
# re-added in planning for an in-house load; neither subtracted nor in the
# baseline but added in planning for an out-of-house load. The G4 PV-coverage
# comparison and the learned/measured per-load power are topology-independent
# (raw watts of the load itself) and need no adjustment either.
CONF_LOAD_IN_HOUSE = "in_house_measurement"

# Power-feedback samples below this fraction of the load's nominal power
# are standby/idle draw (e.g. a 400 W dehumidifier reading ~20 W) and must
# not replace the planning power via the EMA: the planner would book hours
# at standby watts while the device really pulls its nominal power
# (2026-07-05 live incident: 11 h × 22 Wh planned vs. ~4.4 kWh real).
STANDBY_FRACTION = 0.25

# F11 latch-hold over-power protection: the F10 opportunistic hold is normally
# gated by a shadow plan (does the normal planner activate the latched load now,
# if it were not latched?). A latch can ALSO mean the outlet draws MORE than
# configured (a foreign consumer): the shadow plan then replans at the too-small
# learned/nominal power and may book the load although its REAL draw needs grid
# import. When the measured feedback clears the standby bar AND sits at least
# this factor above the planning power, the hold additionally requires slot-0 PV
# to cover the real draw, so it is never grid-fed. A constant, not a config key.
LATCH_HOLD_OVERPOWER_FACTOR = 1.5

# Stale-SOC guard (F-EXECUTOR-GUARDS G2): a load SOC that stays EXACTLY
# unchanged for this many minutes while the device charges above the standby
# bar is latched as stale (the fossibot integration serves cached values with
# FRESH timestamps, so age checks cannot catch it; its cadence is ~1 min, so
# 12 min of frozen SOC while drawing ~500 W is unambiguous). Deliberately a
# constant, not a config key.
STALE_LOAD_SOC_MIN = 12

# House-battery SOC stale watchdog (energy-based, banded sibling of the G2
# load guard): the same "cached values with fresh timestamps" failure exists
# for the HOUSE battery BMS, and `_get_soc` alone accepts any in-range value
# as fresh. While the SOC reading stays EXACTLY unchanged, the watchdog
# accumulates the EXPECTED energy throughput (the slot-0 battery flow of the
# last valid plan — there is no live house-battery power sensor); once that
# expectation exceeds the configured share of capacity, the SOC would have had
# to move, so the reading is latched as stale — which routes into the normal
# UpdateFailed path and hence the D-A8 stage-2 escalation. Proof of flow needs
# at least this much expected battery power (W); below it the accumulation
# pauses (standby/float: a constant SOC is physically correct there, no
# evidence either way). A constant, not a config key (like G2).
HOUSE_SOC_STALE_POWER_W = 300.0
# BMS plateaus (2026-07-31 incident: 10 false latches in 90 min at SOC
# 87-89 %): near the SOC ends the reading can sit on one value for a long time
# while power keeps flowing (balancing/clamping — Victron calibrates the SOC
# at the plateaus), so the edge bands tolerate a much larger unexplained drift
# than the mid band before the watchdog alarms. The high bound is 88 %, NOT
# 89 %, and the bounds are INCLUSIVE: the calibration plateau demonstrably
# covers 88.0 and 89.0 (2026-08-02: a real 89.0 reading with an idle battery
# latched after ~107 Wh in the mid band). Both bounds and both drift
# percentages are operator-tunable (defaults below / in DEFAULT_CONFIG).
HOUSE_SOC_STALE_EDGE_LOW_PERCENT = 13.0
HOUSE_SOC_STALE_EDGE_HIGH_PERCENT = 88.0
CONF_HOUSE_SOC_STALE_MID_PERCENT = "house_soc_stale_mid_percent"
CONF_HOUSE_SOC_STALE_EDGE_PERCENT = "house_soc_stale_edge_percent"
CONF_HOUSE_SOC_STALE_EDGE_LOW_SOC = "house_soc_stale_edge_low_soc"
CONF_HOUSE_SOC_STALE_EDGE_HIGH_SOC = "house_soc_stale_edge_high_soc"

# Pre-drain block stability gate (F-PREDRAIN-BLOCK R7, 2026-08-01 incident):
# a continuous load's pass-3 block is only ACTUATED after this many
# consecutive plans proposed the identical [start, end) window AND the first
# proposal is at least this old. The 2026-08-01 flicker run showed a marginal
# block flipping in and out within minutes (a pointless 19-min battery run);
# the time floor covers fast state-driven refresh cadences (~30 s), where
# three plans alone would pass in 90 s. Constants, not config keys (G2 style).
PREDRAIN_BLOCK_STABLE_PLANS = 3
PREDRAIN_BLOCK_STABLE_MINUTES = 10

# F-PREDRAIN-BLOCK log damping (7-day live audit 31.07.–07.08.2026): the
# change-gated INFO line compared raw (load, start, end) triples, but the
# block start ratchets with the clock (slot 0 covers "now") and the booked
# Wh with the forecast — every coordinator cycle looked like a new booking:
# 4,196 lines in 7 days (1,753 on 08-04 alone, ≈ 1/min), defeating the spec
# intent (project-knowledge 05 §5.6: one INFO line per CHANGE, no per-cycle
# spam). A booking change now logs only when MATERIAL vs the last LOGGED
# state: start shifted >= SHIFT_MIN or energy changed >= ENERGY_WH (a new
# load/day block always logs), and even material changes are rate-limited to
# one line per block per RATE_LIMIT_MIN (oscillating forecasts); suppressed
# changes go to DEBUG. Constants, not config keys (G2 style).
PREDRAIN_BLOCK_LOG_SHIFT_MIN = 30
PREDRAIN_BLOCK_LOG_ENERGY_WH = 200.0
PREDRAIN_BLOCK_LOG_RATE_LIMIT_MIN = 60

# Telemetry-freeze watchdog (F4, 2026-07-24 forensics): a redundant guard for
# ENERGY-LIMITED loads, independent of the switching path AND the G2 latch. The
# Fossibot B2 (a recommendation-only load) froze SOC AND input power for 95 h
# (SOC 87.5 %, input EXACTLY 144 W) yet the planner re-booked the same ~50 Wh
# top-up for 4 days, the recommendation duty-cycled in the 15-min raster, and it
# displaced ~0.4 kWh of dehumidifier slots — all without a single warning. When
# BOTH the SOC and the measured power stay EXACTLY unchanged for this many hours
# WHILE the recommendation was active at least once in the window, the telemetry
# is treated as frozen and the load is held unavailable (the same consequence as
# the G2 stale-SOC guard). A plain last_changed watchdog is deliberately avoided:
# cached values carry fresh timestamps, so a legitimately idle device would
# false-positive. The latch and the evidence clock are persisted (7-day live
# audit 2026-08-02: a coordinator reload dropped the latch and the
# recommendation duty-cycled an unplugged device for 110 min, ~225 Wh). A
# constant, not a config key.
FREEZE_STALE_HOURS = 6

# Dwell x replan flicker continuation (F8, 2026-07-24 forensics): a short plan
# flicker at a slot/quantum boundary (the recommendation drops for seconds to a
# few minutes and returns) would else be amplified by min_off into a 15+ min
# forced pause (23.07: ~0.3-0.4 kWh export at SOC 97-99 %; 24.07: two ~15-min
# pauses at PV 1.7-1.8 kW, lost_surplus 6.71->7.11 kWh). When a load's
# recommendation returns within this window after a RECOMMENDATION-driven stop
# (NOT a G4 floor-guard or G1 target-SOC stop), the restart is treated as a
# continuation of the same run and min_off is waived, so the device resumes as
# soon as the other gates allow. A constant, not a config key.
REC_FLICKER_CONTINUATION_MIN = 5
# Ping-pong guard: min_off exists to protect compressors from short cycling, so
# the continuation waiver is capped. After this many waived continuations within
# REC_FLICKER_PINGPONG_WINDOW_MIN minutes, the normal min_off applies again — a
# genuinely flapping plan can never short-cycle the device.
REC_FLICKER_MAX_CONTINUATIONS = 2
REC_FLICKER_PINGPONG_WINDOW_MIN = 30

# F10 latch opportunistic hold (F-TANK recovery, 2026-07-24 forensics): F5 plans
# a latched load (full tank -> ~2 W despite an active recommendation) at its
# measured ~0 W, so the plan never asks for it — but the F-L7 latch only clears
# while the load runs BM-commanded and back in band, a deadlock that keeps a
# refilled tank out of the plan indefinitely. Rather than probing on a timer,
# the executor HOLDS a still-latched SWITCHABLE load (control switch + power
# feedback) ON for as long as the surplus gates hold (no floor guard, inverter
# recommendation on, slot-0 PV power >= the load's effective power), so an
# emptied tank runs the instant the power is there (operator wish 2026-07-24).
# The hold ends only on a gate loss (normal min_runtime-dwelled stop; G4
# immediate) or the latch release (the normal plan then resumes). No interval
# constant is needed — the min_off dwell alone guards a re-hold after a
# gate-loss stop.

# Power-deviation warning (operator requirement F-L7, 2026-07-05): while a
# load runs at the integration's request but its real draw deviates from the
# configured power by more than this percentage (per-load setting, 0 =
# disabled, the default) for the per-load dwell in sustained minutes, a
# per-load warning binary sensor turns on (full water tank, wrong nominal
# power, foreign consumer). Short defrost pauses stay below the dwell. The
# warning latches once on and clears only when the load runs at its configured
# power again (coordinator._update_power_warnings).
CONF_LOAD_POWER_WARNING_PCT = "power_warning_percent"
CONF_LOAD_POWER_WARNING_DWELL_MIN = "power_warning_dwell_min"

# V6 (F-TANK, operator design 2026-07-24): opt-in per-load "consumable tank"
# model for a load with power feedback that stops internally when a tank fills
# (dehumidifier: full water tank -> ~2 W despite an active recommendation). The
# configured value (minutes) is the STARTING estimate of the full-tank run
# time; once at least one tank cycle is observed the learned median (last
# TANK_LEARN_SAMPLES samples) supersedes it. 0 = feature OFF (default) — the
# planner never caps the load's runtime and behaviour is exactly as before. The
# planner books at most (learned full runtime - runtime since last emptying)
# minutes; the reset-runtime button means "tank emptied" (auto-detected on the
# power-warning latch release). Only meaningful for a load with power feedback.
CONF_LOAD_TANK_FULL_RUNTIME_MIN = "tank_full_runtime_min"
# Number of most recent tank-full runtime samples the learned full-tank runtime
# is the median of (odd, small — the tank fills at a slowly drifting rate).
TANK_LEARN_SAMPLES = 5
# Push a "tank nearly full — please empty" notification once per tank cycle when
# the predicted remaining tank RUN time (learned full runtime minus runtime
# since the last emptying) drops below this many minutes AND the load is planned
# to keep running. Runtime minutes, not wall-clock: an idle device never warns.
TANK_WARN_LEAD_MIN = 60.0

# End-of-charge policies for the load's input plug (docs/LOAD_CONTROL.md §3)
INPUT_OFF_POLICY_AUTO = "auto"
INPUT_OFF_POLICY_ALWAYS = "always_off"
INPUT_OFF_POLICY_KEEP = "keep_on"
INPUT_OFF_POLICIES = [
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_KEEP,
]

# Power-warning push notifications (operator wish 2026-07-12): a single global
# list of `notify` service names (e.g. "mobile_app_pixel") the coordinator
# pushes to when ANY load's power warning trips (and, unless silenced, when it
# clears). Empty list = no push. Stored in the integration options.
CONF_WARNING_NOTIFY_TARGETS = "power_warning_notify_targets"
CONF_WARNING_NOTIFY_ON_RESOLVE = "power_warning_notify_on_resolve"

# Persistent state (SOC cache, plug ownership) survives HA restarts
STORAGE_VERSION = 1

# --- Appliance subentry keys ---
SUBENTRY_TYPE_APPLIANCE = "appliance"
CONF_APPLIANCE_NAME = "name"
CONF_APPLIANCE_DETECTION_ENTITY = "detection_entity"
CONF_APPLIANCE_POWER_THRESHOLD_W = "power_threshold_w"
CONF_APPLIANCE_OFF_THRESHOLD_W = "off_threshold_w"
CONF_APPLIANCE_RUN_ENERGY_WH = "run_energy_wh"
CONF_APPLIANCE_RUN_DURATION_H = "run_duration_h"
CONF_APPLIANCE_OPPORTUNISTIC = "opportunistic_start"

# States of a detection entity considered "running" (non-numeric entities).
# Includes the gorenje/frontlader cycle phases: live evidence 2026-08-08 —
# the washer reports washing as running -> rinsing -> spinning, and only
# "running" matched, so the run was dropped mid-cycle (and the just-shipped
# card lane went empty while the machine was visibly spinning).
APPLIANCE_RUNNING_STATES = {
    "on",
    "run",
    "running",
    "washing",
    "active",
    "wash",
    "rinsing",
    "spinning",
    "drying",
}

# Operator rule (incident 2026-08-08): a switched-off appliance takes its
# integration offline, so the detection entity goes unavailable for hours — the
# latch must NOT be held that long (a washer that finished at 22:00 was still
# "running" at 08:43 next morning and a real 08:17 run was missed until 08:45,
# letting the planner book early feed-in against an unknown load). Short
# dropouts (< this many minutes) are soak-phase gaps → keep the latched state;
# longer ones mean "device off" → drop the latch and plan no run.
APPLIANCE_DETECTION_MAX_DROPOUT_MIN = 30

# --- F-PREDRAIN two-buffer pre-drain (docs/F-PREDRAIN.md §3, WP3) ---
# System-level planner options. The core dataclass defaults are NEUTRAL (ratio
# 0.0, confidences 1.0, gate off) so the goldens stay frozen; these RECOMMENDED
# live values are the coordinator/config-flow fallbacks instead (via
# DEFAULT_CONFIG below), so an un-reconfigured install activates the feature
# after the update (F-PREDRAIN §3.2 note).
CONF_PV_FORECAST_MODE = "pv_forecast_mode"
# "import_trade_ratio" was retired by F-STRICT-SURPLUS R1 (2026-07-19): loads
# may never buy grid import, the planner uses a fixed artifact slack instead
# (core/optimize.py IMPORT_ARTIFACT_SLACK_WH). A stored key is ignored.
CONF_PREDRAIN_PV_CONFIDENCE = "predrain_pv_confidence"
CONF_UPPER_PV_RESERVE = "upper_pv_reserve"
CONF_STRONG_PV_CUTOFF_W = "strong_pv_cutoff_w"
# Optional site override with NO default (unset/empty = derive purely from the
# forecast shape), so it is deliberately absent from DEFAULT_CONFIG.
CONF_PV_WINDOW_END_HOUR = "pv_window_end_hour"

# PV forecast ingestion mode (docs/F-PREDRAIN.md F1): "auto" uses the hourly
# wh_period attributes when present and falls back to the two-window synthesis,
# "hourly" is the same today (hourly when present), "daily" ignores the
# attributes entirely (golden-anchor behaviour). Replaces WP1's internal
# PV_FORECAST_MODE_DEFAULT constant.
PV_FORECAST_MODE_AUTO = "auto"
PV_FORECAST_MODE_HOURLY = "hourly"
PV_FORECAST_MODE_DAILY = "daily"
PV_FORECAST_MODES = [
    PV_FORECAST_MODE_AUTO,
    PV_FORECAST_MODE_HOURLY,
    PV_FORECAST_MODE_DAILY,
]

# Lower clamp of the AC-efficiency factor η derived for the hourly wh_period
# curve (coordinator._pv_curve_ac_factor, upper clamp is 1.0). 2026-08-07 live
# audit: a forecast source may publish wh_period as a DC model curve while the
# entity state is the AC day energy (balcony_solar_forecast: Σ buckets ==
# `..._today_dc`, AC state = Σ × 0.9215 learned inverter η) — feeding the DC
# curve into the AC-side simulation over-planned PV by ~8.5 % and contributed
# to an avoidable 0.7 kWh grid import on 2026-08-07. η = state/Σ is a wiring
# sanity ratio, not a precise instrument: below 0.5 it is no longer a
# plausible inverter efficiency (more likely a mismatched state), so the
# scaling stops there instead of shredding the curve.
PV_CURVE_AC_ETA_MIN = 0.5

# Recommended live fallbacks (NOT the neutral core dataclass defaults). Used as
# the coordinator absent-key fallback AND the config-flow form default so a
# no-change reconfigure keeps the pre-drain behaviour unchanged.
PREDRAIN_PV_CONFIDENCE_DEFAULT = 0.5
UPPER_PV_RESERVE_DEFAULT = 1.2
STRONG_PV_CUTOFF_W_DEFAULT = 200.0

# --- F-FEEDIN early grid feed-in (docs/F-FEEDIN.md) ---
# Pre-shifts the UNAVOIDABLE midday export into the morning surplus via the
# external controller's AC setpoint: total export is invariant, only its
# timing moves off the midday peak. Disabled by default.
CONF_FEEDIN_ENABLED = "feedin_enabled"
CONF_FEEDIN_SETPOINT_ENTITY = "feedin_setpoint_entity"
CONF_FEEDIN_BATTERY_POWER_ENTITY = "feedin_battery_power_entity"
CONF_FEEDIN_MAX_W = "feedin_max_w"
CONF_FEEDIN_MIN_SOC = "feedin_min_soc_percent"
CONF_FEEDIN_DEADLINE_HOUR = "feedin_deadline_hour"
# Sign convention of the AC setpoint entity (operator decision, requirement 1):
# the external controller reads a NEGATIVE setpoint as EXPORT (e.g. a planned
# 500 W feed-in is written as -500). ALL coordinator bookkeeping keeps POSITIVE
# watts of feed-in; this single constant flips the sign at the entity boundary
# in both directions (write: power * SIGN; read: state * SIGN).
FEEDIN_SETPOINT_EXPORT_SIGN = -1.0
# Executor tuning (deliberately constants, not config keys — same class as the
# G2/F4 guards). Battery-power deadband of the event-driven trim (requirement
# 10): within ±50 W of zero the battery counts as idle and nothing is written.
FEEDIN_TRIM_DEADBAND_W = 50.0
# Upward trims (battery charging -> raise the setpoint) are throttled to one
# write per 60 s (anti-hunting against the external controller); downward
# trims (battery discharging) are immediate and unthrottled.
FEEDIN_UPWARD_MIN_INTERVAL_S = 60
# Deadband for the 5-min re-anchor to the plan slot value: drift below this is
# tolerated (the trim increments are estimates; the plan is the anchor). It
# applies ONLY to re-anchor writes — trim writes and 0<->>0 transitions always
# write.
FEEDIN_REANCHOR_DEADBAND_W = 25.0
# Minimum spacing between two re-anchor writes. The re-anchor is documented as
# the 5-MINUTE cycle's correction, but `_apply_feedin` also runs on every
# debounced battery-power event: without this throttle each throttled upward
# trim was undone seconds later by an unthrottled re-anchor, and the setpoint
# oscillated against the external controller for the whole feed-in window
# (review 2026-08-03). Matches the planning interval.
FEEDIN_REANCHOR_MIN_INTERVAL_S = 300.0
# Grace window after our own setpoint write during which a differing entity
# state reads as propagation lag, not a manual override (~1 cycle + slack;
# mirrors the F-N2 late-confirmation grace). The grace only excuses a state
# still showing the value we wrote BEFORE the last write — any third value is
# an operator action, else an active trim (which refreshes the write timestamp
# every few seconds) would mask every override forever (review 2026-08-03).
FEEDIN_MANUAL_GRACE_S = 360.0
# Stability brake for the plan anchor (F-FEEDIN R16): an UPWARD setpoint write
# (any source — plan slot, 0->>0 transition, upward trim/re-anchor) is held
# while the plan's slot-0 value scattered more than FEEDIN_STABLE_SPREAD_W
# over the sliding FEEDIN_STABLE_WINDOW_S window. Without it the 2026-08-08
# flap (the pass-3 block flickering in/out of the plan on the pass-1 remnant
# edge) swung the setpoint 0<->~1 kW every 1-2 min. Downward writes (incl.
# ->0 and every fail-safe) are never braked: in doubt the battery charges,
# not the grid. Time-based (not plan-count) because the debounced battery-
# power cadence (~10 s) and the 5-min poll mix freely.
FEEDIN_STABLE_WINDOW_S = 180
FEEDIN_STABLE_SPREAD_W = 50.0

# --- F-REALIZED-SURPLUS realized surplus accounting (docs/F-REALIZED-SURPLUS.md) ---
# Monotone kWh counter of grid export (e.g. the Victron reverse energy
# total). Enables the measured ("realized") day counters; without it the
# integration stays forecast-only. Deliberately NOT in DEFAULT_CONFIG:
# unset = feature off, and the `realized` data block stays absent so older
# backends/cards behave exactly as before.
CONF_EXPORT_METER_ENTITY = "export_meter_entity"
# Jump filter for the energy counters (operator decision 7): counter deltas
# above the plausibility cap are telemetry glitches (the Fritz!Powerline
# counter demonstrably jumps) and must never enter a counter. The cap is this
# factor x max(learned, nominal) power x elapsed hours — 3x covers defrost
# peaks and reading jitter while still catching a wild jump. A constant, not
# a config key (same class as the G2/F4 guards).
REALIZED_JUMP_MAX_FACTOR = 3.0
# Absolute fallback cap (Wh) for the ONE case without an elapsed time: the
# first delta of a reading restored from the store, which carries no
# timestamp. 2 kWh is far above any legitimate 5-min export/consumer step at
# this plant size, below a real counter jump. It must stay a rare fallback —
# a flat cap silently discarded real energy whenever a cycle gap (planner
# failure, HA restart) let a legitimate accrual grow past it, and since the
# baseline advances anyway that energy was lost forever, which is why the
# export meter now passes a cap power too (review 2026-08-03).
REALIZED_JUMP_MAX_WH = 2000.0
# Energy units the counters may report in, as the factor to Wh. HA's `energy`
# device class admits all of these and the options-flow selector filters by
# device class only, so a Wh- or MWh-unit counter must not be read as kWh (that
# mis-scaled every delta by 1000x, and the jump filter then swallowed all of
# them — the sensors sat at 0 forever with only a DEBUG line; review
# 2026-08-03). An unknown unit falls back to kWh with a one-time warning.
REALIZED_ENERGY_UNIT_FACTORS_WH = {
    "Wh": 1.0,
    "kWh": 1000.0,
    "MWh": 1000000.0,
    "GJ": 277777.7777777778,
}

# --- Default configuration (base entry) ---
DEFAULT_CONFIG = {
    # Battery
    "battery_capacity_wh": 5000.0,
    "battery_min_soc_percent": 5.0,
    "battery_max_soc_percent": 95.0,
    "battery_charge_efficiency": 0.97,
    "battery_discharge_efficiency": 0.97,
    # House-SOC stale watchdog (see HOUSE_SOC_STALE_* above): unexplained
    # expected SOC drift (in % of capacity) before the latch — mid band and
    # edge bands. Mid default 3.0 = 2 % physical drift + 1 % DISPLAY
    # QUANTIZATION: the Victron SOC source reports in 1 % steps (~50 Wh at
    # 5 kWh), and the plan-vs-real flow expectation deviates by up to ~2x —
    # the old 2 % band therefore flapped: 2026-08-07 audit (7 days live):
    # 37 false latches (~7 h of control blackout), one latch reached 1:58:55 —
    # only 65 s under the 2-h D-A8 load-shed threshold. Entries that already
    # PERSISTED this key (an options save auto-persists the vol.Required
    # default) keep their stored 2.0 — async_migrate_entry deliberately does
    # not rewrite it; only installs without a stored value pick up 3.0 here.
    CONF_HOUSE_SOC_STALE_MID_PERCENT: 3.0,
    CONF_HOUSE_SOC_STALE_EDGE_PERCENT: 7.0,
    # ... and the SOC bounds of those edge bands (Victron calibration
    # plateaus live there).
    CONF_HOUSE_SOC_STALE_EDGE_LOW_SOC: HOUSE_SOC_STALE_EDGE_LOW_PERCENT,
    CONF_HOUSE_SOC_STALE_EDGE_HIGH_SOC: HOUSE_SOC_STALE_EDGE_HIGH_PERCENT,
    # PV system
    "pv_max_power_w": 3200.0,
    "pv_morning_start_hour": 7,
    "pv_morning_end_hour": 13,
    "pv_afternoon_end_hour": 18,
    "pv_morning_ratio": 0.8,
    # AC consumer profile
    "ac_base_load_w": 50.0,
    "ac_variable_load_w": 75.0,
    "ac_variable_start_hour": 6,
    "ac_variable_end_hour": 20,
    # DC consumer profile
    "dc_base_load_w": 50.0,
    "dc_variable_load_w": 25.0,
    "dc_variable_start_hour": 6,
    "dc_variable_end_hour": 22,
    # Charger
    "charger_max_power_w": 2300.0,
    "charger_efficiency": 0.92,
    "charger_standby_power_w": 10.0,
    # Inverter
    "inverter_max_power_w": 2300.0,
    "inverter_efficiency": 0.95,
    "inverter_standby_power_w": 15.0,
    "inverter_min_soc_percent": 20.0,
    # Learned consumption profiles (docs/CONSUMPTION_FORECAST.md)
    # Stufe 2: recency weighting replaces the hard window edge, so the
    # window widens to 120 d (old days fade via the half-life instead).
    CONF_LEARNING_WINDOW_DAYS: 120,
    CONF_LEARNING_MAX_AGE_DAYS: 14,
    CONF_PROFILE_HALF_LIFE_DAYS: 30,
    CONF_BUFFER_MIN_PERCENT: 3.0,
    CONF_BUFFER_MAX_PERCENT: 15.0,
    # Planner tuning (docs/ALGORITHM.md D-A1..D-A4)
    "soc_buffer_percent": 5.0,
    "hysteresis_percent": 1.0,
    "threshold_inertia_percent": 2.0,
    "min_switch_interval_s": 60,
    # F-PREDRAIN pre-drain (docs/F-PREDRAIN.md §3) — RECOMMENDED live values, so
    # an un-reconfigured install runs with the feature active. pv_window_end_hour
    # is intentionally omitted (no default = unset/derive from the forecast).
    CONF_PV_FORECAST_MODE: PV_FORECAST_MODE_AUTO,
    CONF_PREDRAIN_PV_CONFIDENCE: PREDRAIN_PV_CONFIDENCE_DEFAULT,
    CONF_UPPER_PV_RESERVE: UPPER_PV_RESERVE_DEFAULT,
    CONF_STRONG_PV_CUTOFF_W: STRONG_PV_CUTOFF_W_DEFAULT,
    # Support paths
    CONF_SUPPORT_DC48_POWER_W: 60.0,
    CONF_SUPPORT_SWITCH_DELAY_S: 3,
    # Escalation thresholds (absolute SOC %) — neutral defaults = legacy.
    CONF_SUPPORT_DC24_ACTIVATE_SOC: 10.0,
    CONF_SUPPORT_DC24_RECOVERY_SOC: 11.0,
    CONF_SUPPORT_DC48_ACTIVATE_SOC: 5.5,
    CONF_SUPPORT_DC48_RECOVERY_SOC: 10.0,
    # F-N3 two-bus device parameters — neutral defaults (docs/DC_TOPOLOGY.md).
    CONF_NATIVE48_BASE_W: 0.0,
    CONF_DC24_SHARE_PERCENT: 100.0,
    CONF_DCDC_OUTPUT_VOLTAGE_V: 24.0,
    CONF_DCDC_EFFICIENCY: 1.0,
    CONF_DCDC_MAX_CURRENT_A: 0.0,  # 0 = uncapped
    CONF_PSU24_OUTPUT_VOLTAGE_V: 24.0,
    CONF_PSU24_EFFICIENCY: 1.0,
    CONF_PSU24_MAX_CURRENT_A: 0.0,
    CONF_PSU48_OUTPUT_VOLTAGE_V: 49.56,
    CONF_PSU48_EFFICIENCY: 1.0,
    CONF_PSU48_MAX_CURRENT_A: 0.0,
    CONF_GATE_SOC_PERCENT: 100.0,  # 100 = gate always open (neutral)
    CONF_BATTERY_CELLS_SERIES: 16,
    CONF_PSU48_ON_VOLTAGE_V: 49.56,
    CONF_PSU48_OFF_VOLTAGE_V: 49.8,
    CONF_PSU48_CTRL_LOG_ONLY: True,  # arm only after a shakedown
    # Power-warning push notifications (operator wish 2026-07-12)
    CONF_WARNING_NOTIFY_TARGETS: [],
    CONF_WARNING_NOTIFY_ON_RESOLVE: True,
    # F-FEEDIN early grid feed-in (docs/F-FEEDIN.md) — OFF by default: an
    # un-reconfigured install plans bit-identically (neutral core defaults).
    # The two entity keys have no default (unset = not wired).
    CONF_FEEDIN_ENABLED: False,
    CONF_FEEDIN_MAX_W: 1000.0,
    CONF_FEEDIN_MIN_SOC: 30.0,
    CONF_FEEDIN_DEADLINE_HOUR: 9,
}

# A load counts as "really running" for the runtime counter when its measured
# power exceeds this (W) — above typical smart-plug/appliance standby, below any
# real draw. Falls back to the BM charging state when no power sensor is set.
LOAD_RUNTIME_MIN_W = 5.0
# Max time a single runtime tick may add (s). Above the 300 s update interval so
# a normal cycle is never clipped, but bounds any inflation from a stalled loop
# or a clock jump within a session — a longer gap adds nothing. (A restart gap is
# handled separately by not persisting the tick cursor.)
LOAD_RUNTIME_TICK_MAX_S = 900.0

DEFAULT_LOAD_CONFIG = {
    CONF_LOAD_POWER_W: 300.0,
    CONF_LOAD_BATTERY_TOLERANCE: 15.0,
    CONF_LOAD_MIN_RUNTIME_MIN: 30,
    CONF_LOAD_MIN_OFF_MIN: 30,
    CONF_LOAD_ENERGY_LIMITED: False,
    CONF_LOAD_CAPACITY_WH: 2000.0,
    CONF_LOAD_TARGET_SOC: 100.0,
    CONF_LOAD_INPUT_OFF_POLICY: INPUT_OFF_POLICY_AUTO,
    CONF_LOAD_IN_HOUSE: True,
    # Off by default (0 %); the operator opts a load in per device. Existing
    # loads keep their stored value, so a load already warning stays on.
    CONF_LOAD_POWER_WARNING_PCT: 0.0,
    CONF_LOAD_POWER_WARNING_DWELL_MIN: 15,
    # V6 (F-TANK): 0 = tank model off (default). A missing key on an existing
    # load therefore reads as off — the safety anchor: no behaviour change until
    # the operator enters a runtime for a device with power feedback.
    CONF_LOAD_TANK_FULL_RUNTIME_MIN: 0,
}

DEFAULT_APPLIANCE_CONFIG = {
    CONF_APPLIANCE_POWER_THRESHOLD_W: 10.0,
    CONF_APPLIANCE_OFF_THRESHOLD_W: 5.0,
    CONF_APPLIANCE_RUN_ENERGY_WH: 1000.0,
    CONF_APPLIANCE_RUN_DURATION_H: 2.5,
    CONF_APPLIANCE_OPPORTUNISTIC: False,
}

# --- Entity keys ---
ENTITY_INVERTER_STATUS = "inverter_status"
ENTITY_SOC_THRESHOLD = "soc_threshold"
ENTITY_MIN_SOC_FORECAST = "min_soc_forecast"
ENTITY_MAX_SOC_FORECAST = "max_soc_forecast"
ENTITY_HOURS_TO_MAX_SOC = "hours_to_max_soc"
ENTITY_GRID_IMPORT_FORECAST = "grid_import_forecast"
ENTITY_LOST_SURPLUS = "lost_surplus"
ENTITY_SOC_FORECAST_CURVE = "soc_forecast"
ENTITY_SUPPORT_DC24 = "support_dc24"
ENTITY_SUPPORT_DC48 = "support_dc48"
ENTITY_SUPPORT_DC24_MODE = "support_dc24_mode"
ENTITY_SUPPORT_DC48_MODE = "support_dc48_mode"
# Operator manual-override switches per support PSU (F-N2/R3).
ENTITY_SUPPORT_DC24_MANUAL = "support_dc24_manual"
ENTITY_SUPPORT_DC48_MANUAL = "support_dc48_manual"
SUPPORT_MODE_AUTO = "auto"
SUPPORT_MODE_MANUAL = "manual"
ENTITY_VACATION_MODE = "vacation_mode"
# F-FEEDIN: runtime on/off switch and auto/manual mode sensor.
ENTITY_FEEDIN_SWITCH = "early_feed_in"
ENTITY_FEEDIN_MODE = "feedin_mode"
# F-REALIZED-SURPLUS: measured (realized) surplus accounting sensors — the
# day counters, the Ist+forecast combined values and the corrected monotone
# export total (docs/F-REALIZED-SURPLUS.md).
ENTITY_LOST_SURPLUS_REALIZED_TODAY = "lost_surplus_realized_today"
ENTITY_LOST_SURPLUS_TODAY = "lost_surplus_today"
ENTITY_LOST_SURPLUS_TOMORROW = "lost_surplus_tomorrow"
ENTITY_PREVENTED_EXPORT_REALIZED_TODAY = "prevented_export_realized_today"
ENTITY_PREVENTED_EXPORT_TODAY = "prevented_export_today"
ENTITY_PREVENTED_EXPORT_TOMORROW = "prevented_export_tomorrow"
ENTITY_EARLY_FEED_IN_REALIZED_TODAY = "early_feed_in_realized_today"
ENTITY_TRUE_EXPORT_ENERGY = "true_export_energy"

# --- Attributes ---
ATTR_GRID_IMPORT_KWH = "grid_import_kwh"
ATTR_GRID_EXPORT_KWH = "grid_export_kwh"
ATTR_LAST_UPDATE = "last_update"
ATTR_DATA_VALIDITY = "data_validity"
ATTR_PLANNED_HOURS = "planned_hours"
ATTR_PLANNED_ENERGY_KWH = "planned_energy_kwh"
ATTR_THRESHOLD = "soc_threshold_percent"
ATTR_EXPECTED_POWER_W = "expected_power_w"
ATTR_MEASURED_POWER_W = "measured_power_w"
ATTR_DEVIATING_SINCE = "deviating_since"

# --- Services ---
SERVICE_EXPORT_HOURLY_DETAILS = "export_hourly_details"
SERVICE_EXPORT_LEARNED_PROFILES = "export_learned_profiles"
CONF_AS_TABLE = "as_table"
