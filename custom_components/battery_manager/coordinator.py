"""Data update coordinator for the Battery Manager integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, UnsupportedStorageVersionError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .cascade_manager import CascadeManager
from .const import (
    APPLIANCE_DETECTION_MAX_DROPOUT_MIN,
    APPLIANCE_RUNNING_STATES,
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_OFF_THRESHOLD_W,
    CONF_APPLIANCE_OPPORTUNISTIC,
    CONF_APPLIANCE_POWER_THRESHOLD_W,
    CONF_APPLIANCE_RUN_DURATION_H,
    CONF_APPLIANCE_RUN_ENERGY_WH,
    CONF_BATTERY_CELLS_SERIES,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_BUFFER_MAX_PERCENT,
    CONF_BUFFER_MIN_PERCENT,
    CONF_CASCADE_ACTOR_TIMEOUT_S,
    CONF_CASCADE_MEMBER_IDS,
    CONF_CASCADE_TERMINAL_LOAD_ID,
    CONF_DC24_SHARE_PERCENT,
    CONF_DCDC_EFFICIENCY,
    CONF_DCDC_MAX_CURRENT_A,
    CONF_DCDC_OUTPUT_VOLTAGE_V,
    CONF_DCDC_SWITCH,
    CONF_EXPORT_METER_ENTITY,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_FEEDIN_DEADLINE_HOUR,
    CONF_FEEDIN_ENABLED,
    CONF_FEEDIN_MAX_W,
    CONF_FEEDIN_MIN_SOC,
    CONF_FEEDIN_SETPOINT_ENTITY,
    CONF_GATE_SOC_PERCENT,
    CONF_HOUSE_SOC_STALE_EDGE_HIGH_SOC,
    CONF_HOUSE_SOC_STALE_EDGE_LOW_SOC,
    CONF_HOUSE_SOC_STALE_EDGE_PERCENT,
    CONF_HOUSE_SOC_STALE_MID_PERCENT,
    CONF_LOAD_AVAILABILITY_ENTITY,
    CONF_LOAD_BATTERY_TOLERANCE,
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_DISCHARGE_FLOOR_SOC,
    CONF_LOAD_ENERGY_ENTITY,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_ETA_CHARGE,
    CONF_LOAD_ETA_DISCHARGE,
    CONF_LOAD_HANDOVER_TIMEOUT_S,
    CONF_LOAD_IN_HOUSE,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_MAX_CHARGE_POWER_W,
    CONF_LOAD_MAX_OUTPUT_POWER_W,
    CONF_LOAD_MAX_PASSTHROUGH_POWER_W,
    CONF_LOAD_MIN_OFF_MIN,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_OUTPUT_OVERHEAD_W,
    CONF_LOAD_OUTPUT_POWER_ENTITY,
    CONF_LOAD_OUTPUT_SWITCH,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_POWER_WARNING_DWELL_MIN,
    CONF_LOAD_POWER_WARNING_PCT,
    CONF_LOAD_PRIORITY,
    CONF_LOAD_RECOVERY_SOC,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TANK_FULL_RUNTIME_MIN,
    CONF_LOAD_TARGET_SOC,
    CONF_LOAD_WAKE_TIMEOUT_S,
    CONF_NATIVE48_BASE_W,
    CONF_PREDRAIN_PV_CONFIDENCE,
    CONF_PSU24_EFFICIENCY,
    CONF_PSU24_MAX_CURRENT_A,
    CONF_PSU24_OUTPUT_VOLTAGE_V,
    CONF_PSU48_CTRL_LOG_ONLY,
    CONF_PSU48_EFFICIENCY,
    CONF_PSU48_MAX_CURRENT_A,
    CONF_PSU48_OFF_VOLTAGE_V,
    CONF_PSU48_ON_VOLTAGE_V,
    CONF_PSU48_OUTPUT_VOLTAGE_V,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_MODE,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_PV_WINDOW_END_HOUR,
    CONF_SOC_ENTITY,
    CONF_STRONG_PV_CUTOFF_W,
    CONF_SUPPORT_DC24_ACTIVATE_SOC,
    CONF_SUPPORT_DC24_RECOVERY_SOC,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_ACTIVATE_SOC,
    CONF_SUPPORT_DC48_POWER_W,
    CONF_SUPPORT_DC48_RECOVERY_SOC,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    CONF_UPPER_PV_RESERVE,
    CONF_WARNING_NOTIFY_ON_RESOLVE,
    CONF_WARNING_NOTIFY_TARGETS,
    DC48_CTRL_DWELL_OFF_S,
    DC48_CTRL_DWELL_ON_S,
    DC48_CTRL_FAILSAFE_MIN,
    DC48_CTRL_VOLTAGE_MAX,
    DC48_CTRL_VOLTAGE_MIN,
    DEBOUNCE_SECONDS,
    DEFAULT_CONFIG,
    DEFAULT_LOAD_CONFIG,
    DOMAIN,
    FEEDIN_MANUAL_GRACE_S,
    FEEDIN_REANCHOR_DEADBAND_W,
    FEEDIN_REANCHOR_MIN_INTERVAL_S,
    FEEDIN_SETPOINT_EXPORT_SIGN,
    FEEDIN_STABLE_SPREAD_W,
    FEEDIN_STABLE_WINDOW_S,
    FEEDIN_TRIM_DEADBAND_W,
    FEEDIN_UPWARD_MIN_INTERVAL_S,
    FREEZE_STALE_HOURS,
    HOUSE_SOC_STALE_CHECK_MAX_PERCENT,
    HOUSE_SOC_STALE_CHECK_MIN_PERCENT,
    HOUSE_SOC_STALE_POWER_W,
    INITIAL_UPDATE_INTERVAL_SECONDS,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_KEEP,
    LATCH_HOLD_OVERPOWER_FACTOR,
    LOAD_RUNTIME_MIN_W,
    LOAD_RUNTIME_TICK_MAX_S,
    LOAD_SOC_CACHE_MAX_AGE_HOURS,
    MAX_HISTORICAL_FORECAST_AGE_HOURS,
    MAX_HISTORICAL_SOC_AGE_HOURS,
    POWER_CALIBRATION_ACTOR_CONFIRM_TIMEOUT_S,
    POWER_CALIBRATION_JUMP_FRACTION,
    POWER_CALIBRATION_MAX_TOTAL_S,
    POWER_CALIBRATION_MIN_MEASURE_S,
    POWER_CALIBRATION_MIN_SAMPLE_INTERVAL_S,
    POWER_CALIBRATION_MIN_SAMPLES,
    POWER_CALIBRATION_POLL_S,
    POWER_CALIBRATION_START_WAIT_S,
    PREDRAIN_BLOCK_LOG_ENERGY_WH,
    PREDRAIN_BLOCK_LOG_RATE_LIMIT_MIN,
    PREDRAIN_BLOCK_LOG_SHIFT_MIN,
    PREDRAIN_BLOCK_STABLE_MINUTES,
    PREDRAIN_BLOCK_STABLE_PLANS,
    PREDRAIN_PV_CONFIDENCE_DEFAULT,
    PV_CURVE_AC_ETA_MIN,
    PV_FORECAST_MODE_AUTO,
    PV_FORECAST_MODE_DAILY,
    PV_FORECAST_MODE_HOURLY,
    REALIZED_ENERGY_UNIT_FACTORS_WH,
    REALIZED_JUMP_MAX_FACTOR,
    REALIZED_JUMP_MAX_WH,
    REC_FLICKER_CONTINUATION_MIN,
    REC_FLICKER_MAX_CONTINUATIONS,
    REC_FLICKER_PINGPONG_WINDOW_MIN,
    STALE_LOAD_SHED_HOURS,
    STALE_LOAD_SOC_MIN,
    STANDBY_FRACTION,
    STARTUP_RETRY_ATTEMPTS,
    STARTUP_SOC_GRACE_S,
    STORAGE_VERSION,
    STRONG_PV_CUTOFF_W_DEFAULT,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
    SUPPORT_MODE_AUTO,
    SUPPORT_MODE_MANUAL,
    SUPPORT_SWITCH_FAIL_ALERT,
    TANK_LEARN_SAMPLES,
    TANK_WARN_LEAD_MIN,
    UPDATE_INTERVAL_SECONDS,
    UPPER_PV_RESERVE_DEFAULT,
)
from .core import (
    DAY_TYPE_ABSENCE,
    Appliance,
    ApplianceRun,
    BatteryParams,
    CascadeMember,
    CascadeRuntimeState,
    ControlParams,
    ConverterParams,
    FeedInParams,
    LoadCascade,
    LoadProfile,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
    aggregate_hours,
    build_slots,
    plan,
    power_learning,
    profile_value,
    quantile_band_slots,
    slot_starts,
)
from .history_profile import ProfileLearner

_LOGGER = logging.getLogger(__name__)


class _RuntimeStore(Store):
    """Coordinator runtime store whose migration can never crash the entry
    setup.

    HA's default ``_async_migrate_func`` raises NotImplementedError for any
    envelope mismatch, which ``Store.async_load`` only swallows for a
    same-major minor diff — so bumping STORAGE_VERSION without a migration
    would kill the whole entry setup on the first load after the update
    (same incident class as the LEARNED_STORE_MAJOR pin, see
    history_profile._LearnedProfileStore). Everything in this store is a
    cache or a re-derivable latch (SOC cache, plug ownership, learned power,
    runtime counters), so any combination we do not explicitly migrate is
    discarded with a warning instead of raising.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> Any:
        if old_major_version in (1, STORAGE_VERSION):
            # Same envelope major: minor diffs are compatible — the payload
            # is a flat dict whose keys are each restored defensively
            # (.get + isinstance checks) in async_load_persistent_state.
            return old_data
        _LOGGER.warning(
            "Discarding persisted runtime state: unsupported store envelope "
            "version %s.%s (expected major %s); the cached values are "
            "re-derived at runtime",
            old_major_version,
            old_minor_version,
            STORAGE_VERSION,
        )
        return None


def _series_source(series: tuple[float | None, ...] | None, index: int) -> str:
    """Per-slot consumption source: L = learned series, S = static profile."""
    if series is not None and index < len(series) and series[index] is not None:
        return "L"
    return "S"


def _power_cap(voltage_v: Any, current_a: Any) -> float | None:
    """Rail-side power cap V_out x I_max; 0 A (or less) means uncapped."""
    current = float(current_a)
    if current <= 0:
        return None
    return float(voltage_v) * current


def _optional_positive(values: Any, key: str) -> float | None:
    """Translate an optional positive numeric config field into the core."""
    value = values.get(key)
    return float(value) if value not in (None, "") else None


def _gate_soc(percent: Any) -> float | None:
    """48 V PSU gate SOC; >= 100 % means always open (no gate)."""
    value = float(percent)
    return value if value < 100.0 else None


def _stored_wh(value: Any) -> float:
    """Defensive store restore for a Wh counter: numbers pass through,
    anything else (missing, None, corrupt) starts neutral at 0."""
    return float(value) if isinstance(value, int | float) else 0.0


# Explicit AC-side variants of the wh_period family: a forecast source that
# labels its buckets as already-AC (inverter efficiency applied) is trusted
# verbatim — the planner simulates AC-side, so no η derivation may touch them
# (see _pv_curve_ac_factor). Generic contract, not tied to one forecaster.
_WH_PERIOD_AC_ATTRS = {
    "wh_period": "wh_period_ac",
    "wh_period_p10": "wh_period_ac_p10",
    "wh_period_p90": "wh_period_ac_p90",
}


def ordered_load_subentries(entry: ConfigEntry) -> list[tuple[str, ConfigSubentry]]:
    """Load subentries in effective priority order (F-LOAD-PRIORITY R3).

    Single source of truth for load ordering: the planner core treats the
    ORDER of `SystemConfig.loads` as priority (docs/ALGORITHM.md D-A4), so this
    is where a stored per-load priority materialises. Sort key per load:
    `(stored priority if present else insertion position + 1, insertion
    position)` — an entry with NO stored priorities anywhere sorts exactly like
    the raw insertion order (the pre-v0.8.2 behaviour, regression anchor), and
    in a mixed legacy state stored values win positions with the insertion
    order breaking ties (R7).
    """
    loads = [
        (subentry_id, subentry)
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD
    ]

    def sort_key(indexed: tuple[int, tuple[str, ConfigSubentry]]) -> tuple[int, int]:
        position, (_subentry_id, subentry) = indexed
        return (int(subentry.data.get(CONF_LOAD_PRIORITY, position + 1)), position)

    return [pair for _, pair in sorted(enumerate(loads), key=sort_key)]


class BatteryManagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads inputs, runs the core planner, applies hysteresis and switching."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # F-EXECUTOR-GUARDS R10: pass the entry explicitly — newer HA cores
            # hard-error on the implicit ContextVar lookup (report_usage).
            config_entry=entry,
            update_interval=timedelta(seconds=INITIAL_UPDATE_INTERVAL_SECONDS),
        )
        # Alias kept deliberately (R10): the code base reads `self.entry`
        # everywhere; `self.config_entry` is the base-class attribute.
        self.entry = entry
        self.raw_config = {**DEFAULT_CONFIG, **entry.data, **entry.options}
        # Single source of truth for the version, set from manifest.json in
        # async_setup_entry (the device sw_version — avoids a hard-coded
        # constant drifting from the manifest).
        self.integration_version: str | None = None

        # Input caching for graceful degradation
        self._last_valid_soc: float | None = None
        self._last_soc_update: datetime | None = None
        self._last_valid_forecasts: list[float] | None = None
        self._last_forecast_update: datetime | None = None
        # D-A8 stage 2 fail-safe (data-loss load shed): start of the current
        # continuous data loss (persisted — the 2 h budget must survive a
        # restart) and the shed latch (also persisted: the shed fires ONCE per
        # outage episode, not again after every restart during it; recovery
        # clears both). The background task handle lets unload cancel it.
        self._data_stale_since: datetime | None = None
        self._stale_shed_active = False
        self._stale_shed_task: asyncio.Task | None = None
        # House-SOC stale watchdog (energy-based, banded sibling of the G2
        # load guard): evidence tuple (frozen value, last accumulation time,
        # accumulated battery throughput in Wh) while power flow is measured
        # or expected, and the latch (frozen value, for the unlatch compare +
        # log). A configured live house-battery power sensor is the preferred
        # flow proof; the expected slot-0 battery flow of the last VALID plan
        # remains the compatibility fallback — see _update_house_soc_watchdog.
        # In-memory only (like G2): a restart re-accumulates the evidence.
        self._house_soc_frozen: tuple[float, datetime, float] | None = None
        self._house_soc_stale: float | None = None
        self._expected_battery_power_w: float | None = None
        # Hourly PV forecast (docs/F-PREDRAIN.md F1): merged naive-local hour -> Wh
        # map, cached with the same stale-fallback semantics as the daily state.
        # The ingestion mode is a system option (WP3); absent = the recommended
        # "auto" (hourly when present, else two-window). Read once here — an
        # options change reloads the entry and rebuilds the coordinator.
        self._pv_forecast_mode = self.raw_config.get(
            CONF_PV_FORECAST_MODE, PV_FORECAST_MODE_AUTO
        )
        # PER-ENTITY stale-cache (FIX-4): a cycle where one forecast entity is
        # unavailable must not clobber the merged map with a partial read. Each
        # entity keeps its own last-good (median, p10, p90, timestamp) — the
        # quantile bands (F-QUANTILE-BANDS R1) ride in the SAME cache entry so
        # a stale reuse can never pair fresh medians with other-day bands. The
        # merge reuses a cached entry within MAX_HISTORICAL_FORECAST_AGE_HOURS.
        self._pv_hourly_by_entity: dict[
            str,
            tuple[
                dict[datetime, float],
                dict[datetime, float],
                dict[datetime, float],
                datetime,
            ],
        ] = {}
        # FIX-10: state-change guard for the "hourly mode but no wh_period" warning.
        self._pv_hourly_empty_warned = False
        # Per-day PV source label ("hourly"/"two_window") for WP4 sensor exposure.
        self._pv_source_by_day: dict[str, str] = {}
        # FIX-11 + 7-day live audit 31.07.–07.08.2026: per pre-drain block
        # (load + block day) the last LOGGED booking (start, end, Wh) with its
        # log time. The raw (load, start, end) change-gate ratcheted with the
        # clock and the forecast — 4,196 INFO lines in 7 days (1,753 on
        # 08-04 ≈ 1/min). Now only MATERIAL changes log (see
        # _log_night_predrain), rate-limited per block.
        self._night_predrain_logged: dict[
            str, tuple[datetime, datetime, float, datetime]
        ] = {}

        # Hysteresis / switching state (docs/ALGORITHM.md D-A2)
        self._displayed_threshold: float | None = None
        self._inverter_recommendation = False
        self._last_inverter_switch: datetime | None = None
        self._support_state = {"dc24": False, "dc48": False}
        # Manual override per PSU (F-N2): entered when the switch turns on
        # externally, left when it is switched off externally; persisted
        # across restarts together with _support_state (the latter is what
        # distinguishes "BM had it on before the restart" from "someone
        # switched it on while HA was down").
        self._support_manual = {"dc24": False, "dc48": False}
        self._last_support_switch: datetime | None = None
        # Last commanded direction per PSU (for the late-confirmation
        # grace), pending unconfirmed activations, the level-triggered
        # DC/DC restore task, and the one-shot pre-0.6.5 adoption flag.
        self._last_support_cmd: dict[str, tuple[bool, datetime]] = {}
        self._support_pending_confirm = {"dc24": False, "dc48": False}
        # True after we command a PSU OFF until we OBSERVE it reach 'off';
        # a lagging 'on' in that window is our own actuation catching up,
        # not an external override (distinguishes it from an operator ON
        # right after our OFF, where the device is seen off in between).
        self._support_pending_off = {"dc24": False, "dc48": False}
        # Support-actuator failure escalation (F7/U5): consecutive failed
        # switch operations in the support path; a repair issue + push is
        # raised at SUPPORT_SWITCH_FAIL_ALERT. In-memory only (like the G2/F4
        # guards): a restart re-accumulates within a few cycles.
        self._support_fail_count = 0
        self._support_fail_alerted = False
        self._dcdc_restore_task: asyncio.Task | None = None
        self._support_adopt_once = False
        # R2 voltage controller for the regulated manual 48 V PSU (v0.7.7):
        # while dc48 is in manual mode AND a battery-voltage sensor is
        # configured, the PSU is cycled by battery voltage with asymmetric
        # hysteresis instead of held permanently on. Continuous in-region
        # timers implement the ON/OFF dwell; a separate timer arms the
        # fail-safe when the reading is missing/implausible. The controller
        # never exits manual mode — only the R3 switch does (operator ans A).
        self._dc48_below_since: datetime | None = None
        self._dc48_above_since: datetime | None = None
        self._dc48_invalid_since: datetime | None = None
        self._dc48_ctrl_task: asyncio.Task | None = None
        # True while the PSU is off BECAUSE the R2 controller switched it off
        # (not the operator). Persisted so a config change that flips log_only
        # — which reloads the entry — cannot reinterpret a controller-caused
        # off as an operator wall-off and silently drop manual mode (answer A).
        self._dc48_ctrl_caused_off = False
        self._dc48_ctrl_diag: dict[str, Any] = {
            "active": False,
            "mode": "off",
            "decision": None,
            "reason": "idle",
            "voltage": None,
        }
        # 48 V gate calibration (F-N3 phase 3): SOC bracket where the real
        # battery voltage crosses the PSU output — helps pick gate_soc.
        self._gate_cal: dict[str, float | None] = {
            "below_max_soc": None,
            "above_min_soc": None,
        }
        self._switch_lock = asyncio.Lock()
        self._switch_task: asyncio.Task | None = None
        self._assumed_state_warned = False
        # Entity-boundary ownership violations indicate an internal stale or
        # misrouted actor path.  Remember each shape so a ten-second retry loop
        # stays visible once without flooding the HA log indefinitely.
        self._cascade_actor_block_warned: set[tuple[str, str | None, bool]] = set()

        # Appliance run tracking and load power smoothing
        self._appliance_started: dict[str, datetime] = {}
        # H1: keys restored from persistence get ONE restart-boundary staleness
        # check in _get_appliance_runs, so a run that finished during downtime
        # cannot pin a genuinely-new run at 0 remaining.
        self._appliance_started_restored: set[str] = set()
        # Dropout clock per appliance: a switched-off appliance takes its
        # integration offline for hours (2026-08-08 incident: the washer's
        # frontlader entities were unavailable 22:00 → 08:43 while a run
        # started unseen). After APPLIANCE_DETECTION_MAX_DROPOUT_MIN of
        # continuous dropout the run is treated as OFF (operator rule) —
        # shorter dropouts keep holding the latch (soak phases).
        self._appliance_dropout_since: dict[str, datetime] = {}
        # Robust planning-power estimation (docs/F-ROBUST-POWER.md): per load
        # a rolling buffer of ACCEPTED (timestamp, watts) samples — only
        # readings past the v0.6.2 standby bar while the load runs at BM's
        # request (the single gate; standby poisoning stays impossible). The
        # time-weighted windowed median in core/power_learning.py replaces
        # the former EMA/run-max pair after the 2026-07-18 818 W incident (a
        # 60 s compressor-restart transient was EMA-blended and frozen by the
        # run-max). Buffer lifecycle = the run; `_load_learned_power_w` is
        # the persisted write-through of the estimate an OFF load plans with.
        self._load_power_samples: dict[str, deque[tuple[datetime, float]]] = {}
        # Change-gated 3x-nominal estimate warning (sensor-lie observability;
        # deliberately no clamp — levels above nominal can be legitimate).
        self._load_power_overrun_warned: set[str] = set()
        self._load_learned_power_w: dict[str, float] = {}
        # F-POWER-CALIBRATION: one explicit operator probe may own a controlled
        # energy-limited load at a time. It deliberately bypasses the planner
        # and G4 (the operator authorised a bounded grid-powered measurement),
        # while the normal executor skips exactly that load. The active marker
        # is persisted so a clean restart can stop an interrupted probe before
        # the first plan. Samples/diagnostics are volatile; a failed or aborted
        # probe never changes the persisted learned value.
        self._load_power_calibration_id: str | None = None
        self._load_power_calibration_task: asyncio.Task | None = None
        self._load_power_calibration_restored: str | None = None
        self._load_power_calibration_release: set[str] = set()
        self._load_power_calibration_diag: dict[str, dict[str, Any]] = {}
        self._actuation_shutdown = False
        # Stale-SOC guard (F-EXECUTOR-GUARDS G2): evidence tuple (frozen value,
        # since) while actively charging above the standby bar, and the latch
        # (frozen value, for the log + unlatch compare). Persisted (7-day live
        # audit 2026-08-02: a coordinator reload dropped the F4 latch and the
        # recommendation duty-cycled a demonstrably unplugged device for
        # 110 min, ~225 Wh misbooked) — see _persistent_payload for the
        # downtime-safe clock semantics.
        self._load_soc_frozen: dict[str, tuple[float, datetime]] = {}
        self._load_soc_stale: dict[str, float] = {}
        # Telemetry-freeze watchdog (F4): per energy-limited load a reference
        # (soc, power, window_start, rec_seen) while SOC AND power sit EXACTLY
        # still, and the resulting stale latch. Persisted like G2 — the
        # precedence freeze ran 174.7 h; a reload must not rebuild the 6 h
        # evidence (FREEZE_STALE_HOURS) from zero.
        self._load_freeze_ref: dict[str, tuple[float, float, datetime, bool]] = {}
        self._load_freeze_stale: set[str] = set()

        # Charging-path control (docs/LOAD_CONTROL.md): SOC cache survives
        # sleeping devices and restarts; plug ownership implements the
        # configurable input-off policy.
        self._store: Store = _RuntimeStore(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._load_soc_cache: dict[str, float] = {}
        self._load_plug_owned: dict[str, bool] = {}
        self._last_load_switch: dict[str, datetime] = {}
        self._load_charging_active: dict[str, bool] = {}
        # Dwell x replan flicker continuation (F8): the last CONFIRMED OFF per
        # load as (timestamp, flicker_eligible) — eligible only for a
        # recommendation (plan/deadline) stop, NOT a G4/G1 safety stop — and the
        # off-episode timestamps of recently GRANTED continuations for the
        # ping-pong cap. In-memory only (a fresh flicker re-detects instantly).
        self._load_last_off: dict[str, tuple[datetime, bool]] = {}
        self._load_flicker_hist: dict[str, list[datetime]] = {}
        # F10 latch opportunistic hold (F-TANK recovery): the set of loads whose
        # current run is a latch hold (F5 books the load at ~0 W, so the plan
        # never asks for it, but while the surplus gates hold the executor keeps
        # it ON so an emptied tank runs immediately). Marking the run lets its
        # gate-loss STOP be classified flicker-ineligible (never seeds an F8
        # continuation) and be dropped on the latch release. In-memory only.
        self._load_latch_hold: set[str] = set()
        # G4 floor guard (operator rule 2026-07-18): True while surplus loads
        # are barred from running because they would be grid-fed. None until
        # the first update cycle computed it (treated as inactive by readers;
        # the first computation itself initialises conservatively).
        self._floor_guard_active: bool | None = None
        # Per-reason timestamp of the last emitted floor-guard TRIPPED WARNING
        # (keys: "soc", "rec"). Rate-limits the WARNING to one per 15 min per
        # reason (further trips log at DEBUG), so a flapping guard cannot spam
        # the log (live 2026-07-24: 9 WARN in 37 min). In-memory only — a
        # restart legitimately re-announces the current state once.
        self._floor_guard_warn_at: dict[str, datetime] = {}
        # Last plan's slot-0 activation per load: the learning gate for
        # recommendation-only loads (no control switch), see _bm_load_active.
        # _load_learn_ok snapshots at each activation edge whether the
        # outlet was idle — a pre-existing manual/foreign draw must not be
        # learned (it would flip the next plan: period-2 oscillation).
        self._load_plan_active: dict[str, bool] = {}
        self._load_learn_ok: dict[str, bool] = {}
        # Power-deviation warning (F-L7): sustained-deviation start per load
        # and the resulting warning flag + diagnostics for the entity.
        self._load_deviation_since: dict[str, datetime] = {}
        self._load_power_warning: dict[str, bool] = {}
        self._load_warning_diag: dict[str, dict[str, Any]] = {}
        self._load_switch_task: asyncio.Task | None = None
        # F-SUBHOUR (approach A): the frozen sub-hour run deadline per controlled
        # non-energy-limited load, and the one-shot timer that force-switches it
        # OFF at that deadline so the planned partial-hour energy is delivered
        # exactly (no ~250 Wh over-run). Persisted so a restart never uncaps a run.
        self._load_run_deadline: dict[str, datetime] = {}
        self._load_off_timer: dict[str, Any] = {}
        # Per-load "BM control active" switch (v0.7.17). Missing/True = BM plans
        # and actuates the load; False holds it UNAVAILABLE (planner drops it,
        # executor switches it off) without touching the control-switch config.
        self._load_bm_enabled: dict[str, bool] = {}
        # F-CASCADE-STORAGE: the manager owns every actor in configured
        # chains.  Its serialisable state is restored below; new cascades are
        # deliberately disabled until the operator explicitly enables them.
        self._cascade_state: dict[str, dict[str, Any]] = {}
        self.cascade_manager = CascadeManager(self)
        # F-PREDRAIN-BLOCK stability gate: per continuous load the current
        # block signature (start, end) with the count of consecutive identical
        # plans and its first-seen time; and the set of loads whose block may
        # be ACTUATED now (stable long enough). In-memory only — a restart
        # re-counts, which merely delays the block (the safe direction).
        self._predrain_block_evidence: dict[
            str, tuple[tuple[str | None, str], int, datetime]
        ] = {}
        self._predrain_block_stable: set[str] = set()
        # Real active-runtime counter per load (v0.7.18): accumulated seconds the
        # load really ran (persisted, so the count survives restarts), plus the
        # last-tick cursor of an in-progress run (NOT persisted — a restored
        # cursor would credit the restart gap; see _update_load_runtime).
        self._load_runtime_seconds: dict[str, float] = {}
        self._load_run_since: dict[str, datetime] = {}
        # V6 (F-TANK, operator design 2026-07-24): the consumable-tank model.
        # `_load_tank_samples` holds the last TANK_LEARN_SAMPLES observed
        # full-tank runtimes (minutes); their median is the learned full-tank
        # runtime, persisted like the power-warning latch. `_load_tank_full_min`
        # is the runtime captured at the power-warning latch ENTRY (the tank-full
        # event), committed as a sample and cleared at the latch RELEASE (the
        # tank-emptied event) — persisted so a restart mid-tank-cycle still
        # learns. `_load_tank_notified` (in-memory, one per tank cycle) gates the
        # single "tank nearly full" push. `_load_tank_diag` carries the sensor
        # attributes computed each cycle.
        self._load_tank_samples: dict[str, list[float]] = {}
        self._load_tank_full_min: dict[str, float] = {}
        self._load_tank_notified: set[str] = set()
        self._load_tank_diag: dict[str, dict[str, Any]] = {}

        # F-FEEDIN early grid feed-in executor state (docs/F-FEEDIN.md).
        # `_feedin_switch_on` is the runtime on/off switch (persisted, default
        # on — the config toggle is the opt-in). `_feedin_manual_until` is the
        # manual-mode deadline (persisted ISO date; the operator changed the
        # setpoint externally, so we keep hands off until the next midnight —
        # F-N2 pattern). `_feedin_last_written_w` is the last setpoint WE wrote
        # (persisted, positive W of feed-in), the ownership baseline the
        # manual-override detection compares against. `_feedin_last_write_at`
        # anchors the propagation grace (in-memory — after a restart the
        # adoption pass re-establishes the baseline instead of judging).
        self._feedin_switch_on = True
        self._feedin_manual_until: date | None = None
        self._feedin_last_written_w: float | None = None
        self._feedin_last_write_at: datetime | None = None
        # One-shot startup adoption flag: the first judgement pass adopts the
        # actual entity value (and flags manual when it drifted from the
        # persisted own value while HA was down).
        self._feedin_adopted = False
        self._feedin_task: asyncio.Task | None = None
        # Delivered-energy bookkeeping for the upward-trim cap: (day, Wh
        # delivered today, last tick). Restored from the persisted `realized`
        # block on startup (F-REALIZED-SURPLUS); the tick cursor re-arms at
        # boot so the downtime gap is never credited (see _feedin_tick).
        self._feedin_delivered: tuple[date, float, datetime] | None = None
        # Throttle anchor of the upward trim (one raise per 60 s, requirement
        # 10). In-memory — a restart merely delays the next raise.
        self._feedin_last_upward_at: datetime | None = None
        # Throttle cursor of the re-anchor and the value we wrote BEFORE the
        # last write (the manual-mode grace only excuses a state still showing
        # that previous value — see _update_feedin_mode). Both in-memory: a
        # restart re-arms them, which is the safe direction.
        self._feedin_last_reanchor_at: datetime | None = None
        self._feedin_prev_written_w: float | None = None
        # R16 stability brake (F-FEEDIN): the plan's slot-0 feed-in value over
        # a sliding FEEDIN_STABLE_WINDOW_S window. An upward setpoint write is
        # held while the spread exceeds FEEDIN_STABLE_SPREAD_W (the 2026-08-08
        # flap swung the setpoint 0<->~1 kW every 1-2 min). In-memory: a
        # restart re-seeds from the first pass — the safe direction, since the
        # brake only ever delays upward movement.
        self._feedin_plan_trace: deque[tuple[datetime, float]] = deque()
        self._feedin_brake_engaged = False
        # The setpoint entity that currently carries a non-zero value of ours.
        # Persisted: it is the only way to still zero an entity the operator
        # unwired in the options flow (_feedin_release_orphan).
        self._feedin_owned_entity: str | None = None

        # F-REALIZED-SURPLUS realized surplus accounting
        # (docs/F-REALIZED-SURPLUS.md): the measured day counters (lost /
        # prevented / feed-in Wh), the monotone corrected true-export total
        # and the last counter readings they were derived from — one dict in
        # exactly the persisted shape so _persistent_payload is a straight
        # copy. `date` is the LOCAL ISO date the day counters belong to
        # (None until the first tick). The companions stay in-memory:
        # `_realized_last_ts` holds the reading timestamps (the jump filter's
        # elapsed-hours input — a restored reading must not inherit a
        # downtime gap as elapsed time, so it falls back to the absolute
        # cap), `_realized_relearn` marks counters whose next valid state is
        # a fresh baseline after an unavailable/unknown dropout (the outage
        # gap is never dumped as one delta), and `_realized_runtime_base` is
        # the runtime-counter checkpoint of the prevented-export estimate for
        # loads without an energy counter.
        # `external_debt_wh` is the not-yet-applied remainder of the
        # external-load export correction: a coarse load counter delivers its
        # chunk in one cycle while the export meter trickles, so the surplus of
        # the correction waits here for the following cycles instead of being
        # clamped away. It is NOT a day counter — it survives midnight, because
        # the metering artefact it tracks does too.
        self._realized: dict[str, Any] = {
            "date": None,
            "lost_wh": 0.0,
            "prevented_wh": 0.0,
            "feedin_wh": 0.0,
            "true_export_total_wh": 0.0,
            "external_debt_wh": 0.0,
            "last_readings": {},
        }
        self._realized_last_ts: dict[str, datetime] = {}
        self._realized_relearn: set[str] = set()
        self._realized_runtime_base: dict[str, float] = {}
        self._realized_unit_warned: set[str] = set()

        # Learned consumption profiles (docs/CONSUMPTION_FORECAST.md)
        self.learner = ProfileLearner(hass, entry)

        self._startup_complete = False
        self._successful_updates = 0
        # V8: anchor of the SOC startup grace window, stamped on the first
        # refresh (None until then) — see _async_update_data.
        self._startup_at: datetime | None = None
        # V8: latches True on the FIRST successful cycle and never resets, so a
        # genuine SOC dropout after a good cycle (which zeroes _successful_updates)
        # cannot re-enter the startup grace even if it happens inside the window.
        self._first_success_done = False

        self._debounce_task: asyncio.Task | None = None
        self._listeners_setup: bool = False
        self._unsub_state_listener = None
        self._setup_entity_listeners()

    # ------------------------------------------------------------------
    # Persistent state (SOC cache, plug ownership)
    # ------------------------------------------------------------------

    async def async_load_persistent_state(self) -> None:
        """Restore the load-SOC cache and plug ownership after a restart."""
        await self.learner.async_load()
        try:
            data = await self._store.async_load()
        except UnsupportedStorageVersionError:
            # A store written by a NEWER envelope major (downgrade scenario)
            # is refused by HA before the migrate callback runs. The cached
            # state is re-derivable, so — like the learner store — this must
            # never break the entry setup.
            _LOGGER.warning(
                "Discarding persisted runtime state: the store was written "
                "by a newer version and cannot be read; the cached values "
                "are re-derived at runtime"
            )
            data = None
        if data:
            # Cache entries are keyed by subentry AND carry the source entity
            # id so a reconfigured load never reuses another device's SOC.
            self._load_soc_cache = {
                k: v
                for k, v in data.get("load_soc", {}).items()
                if isinstance(v, dict) and "soc" in v
            }
            self._load_plug_owned = {
                k: bool(v) for k, v in data.get("plug_owned", {}).items()
            }
            # The switch dwell survives restarts: a wiped timestamp allowed
            # switching right after boot (co-factor of the 2026-07-05
            # night-charge incident). The live power-sample buffer is
            # deliberately NOT persisted — a stale window (last readings of a
            # finished charge) would otherwise serve as fresh measurement;
            # the LEARNED write-through value below carries across restarts.
            for k, v in data.get("last_load_switch", {}).items():
                ts = dt_util.parse_datetime(v) if isinstance(v, str) else None
                if ts is not None:
                    self._last_load_switch[k] = ts
            # F-SUBHOUR: restore the frozen run deadline so a restart mid-run
            # still force-offs at the planned time (the poll enforces it; a past
            # deadline offs on the first refresh) — never uncapped (R13).
            for k, v in data.get("load_run_deadline", {}).items():
                ts = dt_util.parse_datetime(v) if isinstance(v, str) else None
                if ts is not None:
                    self._load_run_deadline[k] = ts
            self._load_bm_enabled = {
                k: bool(v) for k, v in data.get("load_bm_enabled", {}).items()
            }
            cascade_state = data.get("cascade_state")
            if isinstance(cascade_state, dict):
                self._cascade_state = {
                    str(k): dict(v)
                    for k, v in cascade_state.items()
                    if isinstance(v, dict)
                }
                # Wake indexes, deadlines and proof phases describe one live
                # process and are invalid after HA downtime.  The manager
                # releases a disabled chain or reclassifies an enabled chain's
                # confirmed live actor vector before execution resumes.
                self.cascade_manager.normalize_restored_state()
            self._load_runtime_seconds = {
                k: float(v) for k, v in data.get("load_runtime_seconds", {}).items()
            }
            # F-PLANNER-HONESTY R3: restore the learned planning power; entries
            # whose load subentry vanished are dropped (a re-created load must
            # not inherit another device's power).
            self._load_learned_power_w = {
                k: float(v)
                for k, v in data.get("load_learned_power", {}).items()
                if k in self.entry.subentries
            }
            restored_calibration = data.get("load_power_calibration_active")
            if isinstance(
                restored_calibration, str
            ) and self.power_calibration_supported(restored_calibration):
                self._load_power_calibration_restored = restored_calibration
            # F-L7 latched power warning: restore per-load, dropping entries
            # whose subentry vanished (a re-created load must not inherit a
            # stale warning). The dwell timer is intentionally NOT restored.
            self._load_power_warning = {
                k: bool(v)
                for k, v in data.get("load_power_warning", {}).items()
                if k in self.entry.subentries
            }
            # V6 (F-TANK): restore the learned tank samples and the pending
            # tank-full runtime capture, dropping entries whose subentry vanished
            # (a re-created load must not inherit another device's tank model).
            self._load_tank_samples = {
                k: [float(x) for x in v]
                for k, v in data.get("load_tank_samples", {}).items()
                if k in self.entry.subentries and isinstance(v, list)
            }
            self._load_tank_full_min = {
                k: float(v)
                for k, v in data.get("load_tank_full_min", {}).items()
                if k in self.entry.subentries
            }
            # F-EXECUTOR-GUARDS G2 + F4 (7-day live audit 2026-08-02): restore
            # latch AND evidence clock. The clock re-arms as now - elapsed_s,
            # so the reload/downtime gap itself is never credited as freeze
            # evidence (downtime is not evidence — the payload stores the
            # ACCUMULATED seconds, not the wall-clock start). Entries whose
            # load subentry vanished are dropped (a re-created load must not
            # inherit a stale latch); corrupt entries are skipped like every
            # other block here.
            restore_now = dt_util.utcnow()
            for k, v in data.get("load_soc_stale_guard", {}).items():
                if k not in self.entry.subentries or not isinstance(v, dict):
                    continue
                soc = v.get("soc")
                elapsed = v.get("elapsed_s")
                if not isinstance(soc, int | float) or not isinstance(
                    elapsed, int | float
                ):
                    continue
                self._load_soc_frozen[k] = (
                    float(soc),
                    restore_now - timedelta(seconds=max(0.0, float(elapsed))),
                )
                if v.get("latched"):
                    self._load_soc_stale[k] = float(soc)
            for k, v in data.get("load_freeze_guard", {}).items():
                if k not in self.entry.subentries or not isinstance(v, dict):
                    continue
                soc = v.get("soc")
                power = v.get("power")
                elapsed = v.get("elapsed_s")
                if (
                    not isinstance(soc, int | float)
                    or not isinstance(power, int | float)
                    or not isinstance(elapsed, int | float)
                ):
                    continue
                self._load_freeze_ref[k] = (
                    float(soc),
                    float(power),
                    restore_now - timedelta(seconds=max(0.0, float(elapsed))),
                    bool(v.get("rec_seen")),
                )
                if v.get("latched"):
                    self._load_freeze_stale.add(k)
            # The tick cursor (_load_run_since) is intentionally not restored —
            # see _persistent_payload; the first tick re-arms it so a restart gap
            # is never credited as runtime.
            # F-SUBHOUR H1: restore appliance run starts (a still-running
            # appliance keeps its real elapsed; a finished one is popped on the
            # next _get_appliance_runs when detection reads not-running).
            for k, v in data.get("appliance_started", {}).items():
                ts = dt_util.parse_datetime(v) if isinstance(v, str) else None
                if ts is not None:
                    self._appliance_started[k] = ts
                    self._appliance_started_restored.add(k)
            # Support-PSU manual override (F-N2) survives restarts, together
            # with the last known BM support state: after a restart, "PSU is
            # on but we never switched it on" must only read as manual when
            # the BM really did not have it on before. Flags of a PSU whose
            # switch was removed from the config are dropped — they could
            # never be cleared again and would poison the simulation.
            for key, conf_key in (
                ("dc24", CONF_SUPPORT_DC24_SWITCH),
                ("dc48", CONF_SUPPORT_DC48_SWITCH),
            ):
                if not self.raw_config.get(conf_key):
                    continue
                self._support_manual[key] = bool(
                    data.get("support_manual", {}).get(key, False)
                )
                self._support_state[key] = bool(
                    data.get("support_state", {}).get(key, False)
                )
            # R2 controller-caused-off flag survives the reload that a config
            # change (e.g. log_only) triggers — only meaningful while the 48 V
            # switch is configured and the path is in manual mode.
            if (
                self.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
                and self._support_manual["dc48"]
            ):
                self._dc48_ctrl_caused_off = bool(
                    data.get("dc48_ctrl_caused_off", False)
                )
            # Pre-0.6.5 store: no ownership record exists. The first mode
            # pass adopts an already-on PSU instead of flipping it to
            # manual (the old version may have switched it on itself).
            self._support_adopt_once = "support_state" not in data
            # D-A8 stage 2: the data-loss clock and the shed latch survive
            # restarts — a reboot must not reset the 2 h budget (an outage
            # spanning a restart would else never escalate) and must not
            # re-fire an already-executed shed (the latch is "once per
            # episode", not "once per session"). The latch is only honoured
            # together with a valid timestamp (inconsistent store = re-derive
            # from scratch rather than blocking loads forever).
            stale_since = data.get("stale_since")
            self._data_stale_since = (
                dt_util.parse_datetime(stale_since)
                if isinstance(stale_since, str)
                else None
            )
            self._stale_shed_active = (
                bool(data.get("stale_shed_active", False))
                and self._data_stale_since is not None
            )
            # F-FEEDIN: the runtime switch, the manual-mode deadline and our
            # last written setpoint survive restarts — after a reboot "setpoint
            # ≠ ours" must still be judgeable as an external override instead
            # of being silently adopted (F-N2 pattern). The in-flight grace
            # anchor and the delivered-Wh integral are deliberately volatile.
            self._feedin_switch_on = bool(data.get("feedin_switch_on", True))
            manual_until = data.get("feedin_manual_until")
            if isinstance(manual_until, str):
                with contextlib.suppress(ValueError):
                    self._feedin_manual_until = date.fromisoformat(manual_until)
            last_written = data.get("feedin_last_written_w")
            if isinstance(last_written, int | float):
                self._feedin_last_written_w = float(last_written)
            owned = data.get("feedin_owned_entity")
            if isinstance(owned, str) and owned:
                self._feedin_owned_entity = owned
            # F-REALIZED-SURPLUS: the measured day counters, the monotone
            # true-export total and the last counter readings survive
            # restarts (decision 8); a missing/corrupt block starts neutral.
            # The companions (reading timestamps, re-learn flags, runtime
            # checkpoints) are deliberately volatile — see __init__.
            realized = data.get("realized")
            if isinstance(realized, dict):
                stored_date = realized.get("date")
                last_readings = realized.get("last_readings")
                self._realized = {
                    "date": stored_date if isinstance(stored_date, str) else None,
                    "lost_wh": _stored_wh(realized.get("lost_wh")),
                    "prevented_wh": _stored_wh(realized.get("prevented_wh")),
                    "feedin_wh": _stored_wh(realized.get("feedin_wh")),
                    "true_export_total_wh": _stored_wh(
                        realized.get("true_export_total_wh")
                    ),
                    "external_debt_wh": _stored_wh(realized.get("external_debt_wh")),
                    "last_readings": (
                        {
                            k: float(v)
                            for k, v in last_readings.items()
                            if isinstance(v, int | float)
                        }
                        if isinstance(last_readings, dict)
                        else {}
                    ),
                }
            # F-FEEDIN/F-REALIZED-SURPLUS: the delivered-Wh integral lives in
            # the persisted realized block now — restore it so a restart no
            # longer re-integrates from 0. The tick cursor re-arms at now:
            # the downtime gap is never credited (same rule as the runtime
            # tick cursor).
            restored_at = dt_util.now()
            if (
                self._realized["date"] == restored_at.date().isoformat()
                and self._realized["feedin_wh"] > 0.0
            ):
                self._feedin_delivered = (
                    restored_at.date(),
                    self._realized["feedin_wh"],
                    restored_at,
                )

    def _persistent_payload(self) -> dict[str, Any]:
        return {
            "load_soc": self._load_soc_cache,
            "plug_owned": self._load_plug_owned,
            "last_load_switch": {
                k: v.isoformat() for k, v in self._last_load_switch.items()
            },
            "load_run_deadline": {
                k: v.isoformat() for k, v in self._load_run_deadline.items()
            },
            "load_bm_enabled": dict(self._load_bm_enabled),
            "cascade_state": self.cascade_manager.persistent_state_snapshot(),
            # Only the accumulated total is persisted; the in-progress tick cursor
            # is deliberately NOT — restoring it would credit the whole restart
            # gap (up to the tick cap) as runtime the device may never have run.
            # After a restart the first tick just re-arms the cursor (losing at
            # most the last sub-cycle partial, which is bounded and unobservable).
            "load_runtime_seconds": dict(self._load_runtime_seconds),
            # F-PLANNER-HONESTY R3 / F-ROBUST-POWER: the learned planning
            # power (write-through of the robust windowed estimate) survives
            # restarts — unlike the live sample buffer (deliberately
            # volatile, see async_load_persistent_state); spike-proof by the
            # estimator's median + warm-up construction.
            "load_learned_power": dict(self._load_learned_power_w),
            # The samples are intentionally volatile. Only the actor-ownership
            # marker survives so setup can force an interrupted grid-powered
            # probe OFF before the normal planner starts.
            "load_power_calibration_active": (
                self._load_power_calibration_id or self._load_power_calibration_restored
            ),
            # F-SUBHOUR H1: persist the appliance run start so a restart mid-run
            # does not re-latch at `now` and re-inject the full run energy.
            "appliance_started": {
                k: v.isoformat() for k, v in self._appliance_started.items()
            },
            "support_manual": dict(self._support_manual),
            "support_state": dict(self._support_state),
            "dc48_ctrl_caused_off": self._dc48_ctrl_caused_off,
            # F-L7: the latched power warning survives reloads/restarts so an
            # options save (which reloads the coordinator) does not silently
            # drop a raised warning. Only the bool is persisted; the dwell
            # timer re-arms after a restart (a not-yet-tripped deviation is
            # deliberately not credited across downtime).
            "load_power_warning": dict(self._load_power_warning),
            # V6 (F-TANK): the learned tank samples (median = learned full-tank
            # runtime) and the pending tank-full runtime capture survive
            # restarts, like the power-warning latch. The per-cycle notified set
            # is deliberately volatile (a rare duplicate push after a restart is
            # acceptable; persisting it would over-suppress after a real reset).
            "load_tank_samples": {
                k: list(v) for k, v in self._load_tank_samples.items()
            },
            "load_tank_full_min": dict(self._load_tank_full_min),
            # F-EXECUTOR-GUARDS G2 + F4 (7-day live audit 2026-08-02): both
            # watchdogs survive a coordinator reload — a reload that dropped
            # the F4 latch re-opened the exact hole it guarded against (the
            # recommendation duty-cycled a demonstrably unplugged Fossibot B2
            # for 110 min, ~225 Wh misbooked; the precedence freeze ran
            # 174.7 h, so re-building the 6 h evidence from zero is not a
            # shrug). Persisted per load is the reference value(s) the
            # release compares against PLUS the accumulated evidence in
            # SECONDS — not the wall-clock window start: downtime is NOT
            # freeze evidence (while HA is down nothing is observed), so the
            # restored clock resumes from the saved accumulation and the
            # restart gap is never credited (the same rule as the runtime
            # tick cursor). A clean reload flushes on unload
            # (async_flush_persistent_state); an unclean power loss forfeits
            # at most the delayed-save window.
            "load_soc_stale_guard": {
                k: {
                    "soc": soc,
                    "elapsed_s": max(0.0, (dt_util.utcnow() - since).total_seconds()),
                    "latched": k in self._load_soc_stale,
                }
                for k, (soc, since) in self._load_soc_frozen.items()
            },
            "load_freeze_guard": {
                k: {
                    "soc": ref[0],
                    "power": ref[1],
                    "elapsed_s": max(0.0, (dt_util.utcnow() - ref[2]).total_seconds()),
                    "rec_seen": ref[3],
                    "latched": k in self._load_freeze_stale,
                }
                for k, ref in self._load_freeze_ref.items()
            },
            # D-A8 stage 2: the data-loss clock and the shed latch survive
            # restarts (see async_load_persistent_state for the rationale).
            "stale_since": (
                self._data_stale_since.isoformat()
                if self._data_stale_since is not None
                else None
            ),
            "stale_shed_active": self._stale_shed_active,
            # F-FEEDIN: see async_load_persistent_state for the rationale.
            "feedin_switch_on": self._feedin_switch_on,
            "feedin_manual_until": (
                self._feedin_manual_until.isoformat()
                if self._feedin_manual_until is not None
                else None
            ),
            "feedin_last_written_w": self._feedin_last_written_w,
            "feedin_owned_entity": self._feedin_owned_entity,
            # F-REALIZED-SURPLUS: measured day counters + monotone true-export
            # total + last counter readings (decision 8; rationale in
            # async_load_persistent_state).
            "realized": {
                "date": self._realized["date"],
                "lost_wh": self._realized["lost_wh"],
                "prevented_wh": self._realized["prevented_wh"],
                "feedin_wh": self._realized["feedin_wh"],
                "true_export_total_wh": self._realized["true_export_total_wh"],
                "external_debt_wh": self._realized["external_debt_wh"],
                "last_readings": dict(self._realized["last_readings"]),
            },
        }

    def _save_persistent_state(self) -> None:
        self._store.async_delay_save(self._persistent_payload, 10)

    async def async_flush_persistent_state(self) -> None:
        """Write the persistent state immediately, cancelling any pending
        delayed save. Called on unload so a config-entry reload (which does not
        fire EVENT_HOMEASSISTANT_FINAL_WRITE) cannot beat the 10 s delayed write
        and read back a stale support-mode / caused-off record (review round 3)."""
        await self._store.async_save(self._persistent_payload())

    # ------------------------------------------------------------------
    # Configuration assembly
    # ------------------------------------------------------------------

    def _tracked_entities(self) -> list[str]:
        cfg = self.raw_config
        entities = [
            cfg[CONF_SOC_ENTITY],
            cfg[CONF_PV_FORECAST_TODAY],
            cfg[CONF_PV_FORECAST_TOMORROW],
            cfg[CONF_PV_FORECAST_DAY_AFTER],
        ]
        # Support switches are tracked so a manual toggle (F-N2) is picked
        # up by the debounced refresh instead of the next 5-min poll — and
        # a dead 24 V rail (PSU manually off, DC/DC still off) is healed
        # quickly.
        for key in (
            CONF_SUPPORT_DC24_SWITCH,
            CONF_SUPPORT_DC48_SWITCH,
            CONF_DCDC_SWITCH,
        ):
            if cfg.get(key):
                entities.append(cfg[key])
        # The R2 48 V controller (v0.7.7) reads battery_voltage_entity but it
        # is deliberately NOT tracked: an analog voltage sags/rises every few
        # seconds, so a debounced full replan per change would be constant and
        # wasteful. The controller runs on the 5-min poll instead — the PSU
        # hard-gates above its own output voltage, so sub-poll latency is
        # irrelevant (docs/DC_TOPOLOGY.md §6, fallback-only variant).
        # F-FEEDIN (requirements 9/10): the battery-power sensor drives the
        # event-driven trim — every Victron update (typ. every few seconds)
        # fires the trim path via the debounced refresh instead of waiting for
        # the 5-min planning cycle. The setpoint entity itself is tracked so an
        # external override is judged promptly (F-N2 pattern). Unlike the
        # voltage analog above this is deliberate: the trim MUST react in
        # seconds so a sudden unmeasured consumer cannot drain the battery.
        if cfg.get(CONF_FEEDIN_ENABLED):
            for key in (CONF_FEEDIN_SETPOINT_ENTITY, CONF_FEEDIN_BATTERY_POWER_ENTITY):
                if cfg.get(key):
                    entities.append(cfg[key])
        # F-REALIZED-SURPLUS: the export meter wakes the coordinator so the
        # realized deltas are booked promptly instead of only at the 5-min
        # planning cycle (same event-driven pattern as the feed-in trim).
        if cfg.get(CONF_EXPORT_METER_ENTITY):
            entities.append(cfg[CONF_EXPORT_METER_ENTITY])
        cascade_member_ids = {
            load_id
            for subentry in self.entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_CASCADE
            for load_id in subentry.data.get(CONF_CASCADE_MEMBER_IDS, [])
        }
        for subentry_id, subentry in self.entry.subentries.items():
            data = subentry.data
            if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
                keys = [
                    CONF_LOAD_SOC_ENTITY,
                    CONF_LOAD_POWER_ENTITY,
                    CONF_LOAD_AVAILABILITY_ENTITY,
                    # F-REALIZED-SURPLUS: the per-load kWh counter (when wired)
                    # feeds the realized accounting on every state change.
                    CONF_LOAD_ENERGY_ENTITY,
                ]
                if subentry_id in cascade_member_ids:
                    keys.extend(
                        (
                            CONF_LOAD_CONTROL_SWITCH,
                            CONF_LOAD_CHARGE_ENABLE,
                            CONF_LOAD_OUTPUT_SWITCH,
                            CONF_LOAD_OUTPUT_POWER_ENTITY,
                        )
                    )
                for key in keys:
                    if data.get(key):
                        entities.append(data[key])
            elif subentry.subentry_type == SUBENTRY_TYPE_APPLIANCE and data.get(
                CONF_APPLIANCE_DETECTION_ENTITY
            ):
                entities.append(data[CONF_APPLIANCE_DETECTION_ENTITY])
        return entities

    def build_system_config(self) -> SystemConfig:
        """Translate entry data + subentries into the core SystemConfig."""
        cfg = self.raw_config
        loads = []
        appliances = []
        cascades = []
        # F-LOAD-PRIORITY R3: the planner core reads priority from the ORDER of
        # SystemConfig.loads, so the loads are built in effective priority order
        # (stored per-load priority, legacy fallback: insertion position).
        for subentry_id, subentry in ordered_load_subentries(self.entry):
            data = subentry.data
            loads.append(
                SurplusLoad(
                    load_id=subentry_id,
                    name=subentry.title,
                    nominal_power_w=float(data[CONF_LOAD_POWER_W]),
                    battery_tolerance=float(data.get(CONF_LOAD_BATTERY_TOLERANCE, 15.0))
                    / 100.0,
                    min_runtime_min=int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)),
                    # Back-compat: an existing load without the key keeps its
                    # symmetric dwell (min_off == min_runtime), see F-SUBHOUR R14.
                    min_off_min=int(
                        data.get(
                            CONF_LOAD_MIN_OFF_MIN,
                            data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30),
                        )
                    ),
                    energy_limited=bool(data.get(CONF_LOAD_ENERGY_LIMITED, False)),
                    capacity_wh=float(data.get(CONF_LOAD_CAPACITY_WH, 0.0)),
                    target_soc_percent=float(data.get(CONF_LOAD_TARGET_SOC, 100.0)),
                    # F-GATE-TOPUP R1: a configured charge-enable gate means the
                    # G1 dwell-exempt target stop delivers exactly `rem`, so the
                    # planner may book the final sub-quantum top-up.
                    gate_stop_capable=bool(data.get(CONF_LOAD_CHARGE_ENABLE)),
                )
            )
        for subentry_id, subentry in self.entry.subentries.items():
            data = subentry.data
            if subentry.subentry_type == SUBENTRY_TYPE_APPLIANCE:
                appliances.append(
                    Appliance(
                        appliance_id=subentry_id,
                        name=subentry.title,
                        run_energy_wh=float(data[CONF_APPLIANCE_RUN_ENERGY_WH]),
                        run_duration_h=float(data[CONF_APPLIANCE_RUN_DURATION_H]),
                        opportunistic_start=bool(
                            data.get(CONF_APPLIANCE_OPPORTUNISTIC, False)
                        ),
                    )
                )

        load_subentries = self.entry.subentries
        for cascade_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_CASCADE:
                continue
            members = []
            for member_id in subentry.data.get(CONF_CASCADE_MEMBER_IDS, []):
                member_entry = load_subentries.get(member_id)
                if member_entry is None:
                    # Invalid references remain stored and fail closed in the
                    # executor.  Excluding them here keeps the pure core from
                    # accepting a topology it cannot model.
                    members = []
                    break
                values = member_entry.data

                members.append(
                    CascadeMember(
                        load_id=member_id,
                        discharge_floor_soc_percent=float(
                            values.get(CONF_LOAD_DISCHARGE_FLOOR_SOC, 20.0)
                        ),
                        recovery_soc_percent=float(
                            values.get(CONF_LOAD_RECOVERY_SOC, 50.0)
                        ),
                        eta_charge=float(values.get(CONF_LOAD_ETA_CHARGE, 1.0)),
                        eta_discharge=float(values.get(CONF_LOAD_ETA_DISCHARGE, 1.0)),
                        max_charge_power_w=_optional_positive(
                            values, CONF_LOAD_MAX_CHARGE_POWER_W
                        ),
                        max_output_power_w=_optional_positive(
                            values, CONF_LOAD_MAX_OUTPUT_POWER_W
                        ),
                        max_passthrough_power_w=_optional_positive(
                            values, CONF_LOAD_MAX_PASSTHROUGH_POWER_W
                        ),
                        output_overhead_w=float(
                            values.get(CONF_LOAD_OUTPUT_OVERHEAD_W, 0.0)
                        ),
                    )
                )
            terminal_id = subentry.data.get(CONF_CASCADE_TERMINAL_LOAD_ID)
            if members and terminal_id in load_subentries:
                cascades.append(
                    LoadCascade(
                        cascade_id=cascade_id,
                        members=tuple(members),
                        terminal_load_id=terminal_id,
                        # Worst-case time until useful Aux power is proven:
                        # every member may consume its wake timeout, every
                        # required actor edge its confirmation timeout, and
                        # the source proof uses the configured handover bound.
                        # The pure Core reserves this before useful energy.
                        startup_transition_s=(
                            sum(
                                float(
                                    load_subentries[member.load_id].data.get(
                                        CONF_LOAD_WAKE_TIMEOUT_S, 60
                                    )
                                )
                                for member in members
                            )
                            + max(
                                float(
                                    load_subentries[member.load_id].data.get(
                                        CONF_LOAD_HANDOVER_TIMEOUT_S, 180
                                    )
                                )
                                for member in members
                            )
                            + (3 * len(members) + 3)
                            * float(subentry.data.get(CONF_CASCADE_ACTOR_TIMEOUT_S, 30))
                        ),
                    )
                )

        support_configured = bool(
            cfg.get(CONF_SUPPORT_DC24_SWITCH) or cfg.get(CONF_SUPPORT_DC48_SWITCH)
        )

        return SystemConfig(
            battery=BatteryParams(
                capacity_wh=float(cfg["battery_capacity_wh"]),
                soc_min_percent=float(cfg["battery_min_soc_percent"]),
                soc_max_percent=float(cfg["battery_max_soc_percent"]),
                eta_charge=float(cfg["battery_charge_efficiency"]),
                eta_discharge=float(cfg["battery_discharge_efficiency"]),
            ),
            charger=ConverterParams(
                max_power_w=float(cfg["charger_max_power_w"]),
                eta=float(cfg["charger_efficiency"]),
                standby_power_w=float(cfg["charger_standby_power_w"]),
            ),
            inverter=ConverterParams(
                max_power_w=float(cfg["inverter_max_power_w"]),
                eta=float(cfg["inverter_efficiency"]),
                standby_power_w=float(cfg["inverter_standby_power_w"]),
            ),
            pv=PVParams(
                peak_power_w=float(cfg["pv_max_power_w"]),
                morning_start_hour=int(cfg["pv_morning_start_hour"]),
                morning_end_hour=int(cfg["pv_morning_end_hour"]),
                afternoon_end_hour=int(cfg["pv_afternoon_end_hour"]),
                morning_ratio=float(cfg["pv_morning_ratio"]),
            ),
            ac_profile=LoadProfile(
                base_w=float(cfg["ac_base_load_w"]),
                variable_w=float(cfg["ac_variable_load_w"]),
                variable_start_hour=int(cfg["ac_variable_start_hour"]),
                variable_end_hour=int(cfg["ac_variable_end_hour"]),
            ),
            dc_profile=LoadProfile(
                base_w=float(cfg["dc_base_load_w"]),
                variable_w=float(cfg["dc_variable_load_w"]),
                variable_start_hour=int(cfg["dc_variable_start_hour"]),
                variable_end_hour=int(cfg["dc_variable_end_hour"]),
            ),
            control=ControlParams(
                inverter_min_soc_percent=float(cfg["inverter_min_soc_percent"]),
                soc_buffer_percent=float(cfg["soc_buffer_percent"]),
                # Grid-support escalation thresholds (D-A9) as absolute SOC %,
                # independent of the planning buffer (D-C8). Neutral defaults
                # reproduce the historical hard-coded thresholds.
                support_dc24_activate_soc=float(cfg[CONF_SUPPORT_DC24_ACTIVATE_SOC]),
                support_dc24_recovery_soc=float(cfg[CONF_SUPPORT_DC24_RECOVERY_SOC]),
                support_dc48_activate_soc=float(cfg[CONF_SUPPORT_DC48_ACTIVATE_SOC]),
                support_dc48_recovery_soc=float(cfg[CONF_SUPPORT_DC48_RECOVERY_SOC]),
                hysteresis_percent=float(cfg["hysteresis_percent"]),
                threshold_inertia_percent=float(cfg["threshold_inertia_percent"]),
                min_switch_interval_s=int(cfg["min_switch_interval_s"]),
                # F-PREDRAIN two-buffer pre-drain (docs/F-PREDRAIN.md §3). The
                # absent-key fallbacks are the RECOMMENDED live values (NOT the
                # neutral core defaults), so an un-reconfigured install runs with
                # the feature active. pv_window_end_hour has no default: absent =
                # unset (None), deriving the window purely from the forecast.
                # A stored import_trade_ratio key is deliberately ignored —
                # retired by F-STRICT-SURPLUS R1 (loads never buy import).
                predrain_pv_confidence=float(
                    cfg.get(CONF_PREDRAIN_PV_CONFIDENCE, PREDRAIN_PV_CONFIDENCE_DEFAULT)
                ),
                upper_pv_reserve=float(
                    cfg.get(CONF_UPPER_PV_RESERVE, UPPER_PV_RESERVE_DEFAULT)
                ),
                strong_pv_cutoff_w=float(
                    cfg.get(CONF_STRONG_PV_CUTOFF_W, STRONG_PV_CUTOFF_W_DEFAULT)
                ),
                pv_window_end_hour=(
                    int(cfg[CONF_PV_WINDOW_END_HOUR])
                    if cfg.get(CONF_PV_WINDOW_END_HOUR) is not None
                    else None
                ),
            ),
            support=SupportParams(
                configured=support_configured,
                dc48_power_w=float(cfg.get(CONF_SUPPORT_DC48_POWER_W, 60.0)),
                # Manual override (F-N2): a manually activated PSU is
                # simulated as permanently on so the SOC forecast matches
                # the real winter operation.
                dc24_forced_on=self._support_manual["dc24"],
                dc48_forced_on=self._support_manual["dc48"],
                # F-N3 two-bus device parameters (docs/DC_TOPOLOGY.md). A
                # 0 A current means "uncapped" (None); the rail-side power
                # cap is V_out x I_max.
                native48_base_w=float(cfg[CONF_NATIVE48_BASE_W]),
                dc24_share=float(cfg[CONF_DC24_SHARE_PERCENT]) / 100.0,
                dcdc_eta=float(cfg[CONF_DCDC_EFFICIENCY]),
                dcdc_output_voltage_v=float(cfg[CONF_DCDC_OUTPUT_VOLTAGE_V]),
                dcdc_max_power_w=_power_cap(
                    cfg[CONF_DCDC_OUTPUT_VOLTAGE_V], cfg[CONF_DCDC_MAX_CURRENT_A]
                ),
                psu24_eta=float(cfg[CONF_PSU24_EFFICIENCY]),
                psu24_output_voltage_v=float(cfg[CONF_PSU24_OUTPUT_VOLTAGE_V]),
                psu24_max_power_w=_power_cap(
                    cfg[CONF_PSU24_OUTPUT_VOLTAGE_V], cfg[CONF_PSU24_MAX_CURRENT_A]
                ),
                psu48_eta=float(cfg[CONF_PSU48_EFFICIENCY]),
                psu48_output_voltage_v=float(cfg[CONF_PSU48_OUTPUT_VOLTAGE_V]),
                psu48_max_power_w=_power_cap(
                    cfg[CONF_PSU48_OUTPUT_VOLTAGE_V], cfg[CONF_PSU48_MAX_CURRENT_A]
                ),
                # Gate (phase 3): >= 100 % means always open (None).
                gate_soc_percent=_gate_soc(cfg[CONF_GATE_SOC_PERCENT]),
            ),
            # F-FEEDIN early grid feed-in (docs/F-FEEDIN.md): the absent-key
            # fallbacks read neutral (disabled), so a pre-feature install
            # plans bit-identically; the recommended live values come from
            # DEFAULT_CONFIG (merged into raw_config).
            feedin=FeedInParams(
                enabled=bool(cfg.get(CONF_FEEDIN_ENABLED, False)),
                max_w=float(cfg.get(CONF_FEEDIN_MAX_W, 1000.0)),
                min_soc_percent=float(cfg.get(CONF_FEEDIN_MIN_SOC, 30.0)),
                deadline_hour=int(cfg.get(CONF_FEEDIN_DEADLINE_HOUR, 9)),
                # Manual mode (R9, operator decision 2026-08-08): the plan and
                # the chart mirror the operator-owned setpoint for the rest of
                # today (0 W books nothing); tomorrow plans automatically
                # again. Unreadable entity -> None -> automatic schedule.
                manual_w=self._feedin_manual_plan_w(),
            ),
            loads=tuple(loads),
            appliances=tuple(appliances),
            cascades=tuple(cascades),
        )

    def _cascade_runtime_state(self, cascade_id: str) -> CascadeRuntimeState:
        """Expose the persisted executor episode to the stateless core."""
        return self.cascade_manager.runtime_state(cascade_id)

    def cascade_enabled(self, cascade_id: str) -> bool:
        return self.cascade_manager.enabled(cascade_id)

    async def async_set_cascade_enabled(self, cascade_id: str, enabled: bool) -> bool:
        changed = await self.cascade_manager.async_set_enabled(cascade_id, enabled)
        await self.async_request_refresh()
        return changed

    async def async_reset_cascade_fault(self, cascade_id: str) -> bool:
        changed = await self.cascade_manager.async_reset_fault(cascade_id)
        await self.async_request_refresh()
        return changed

    # ------------------------------------------------------------------
    # Input reading
    # ------------------------------------------------------------------

    def _read_float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError, TypeError:
            return None

    def _get_soc(self, now: datetime) -> float | None:
        # House-SOC stale watchdog latched: the source demonstrably serves a
        # FROZEN value, so neither the live read nor the cache (which holds
        # the same frozen number) may count as valid. Returning None routes
        # into the normal UpdateFailed path — and thereby into the D-A8
        # stage-2 escalation (STALE_LOAD_SHED_HOURS). The latch is released
        # by _update_house_soc_watchdog on the first CHANGED reading.
        if self._house_soc_stale is not None:
            return None
        value = self._read_float(self.raw_config[CONF_SOC_ENTITY])
        if value is not None and 0.0 <= value <= 100.0:
            self._last_valid_soc = value
            self._last_soc_update = now
            return value
        if (
            self._last_valid_soc is not None
            and self._last_soc_update is not None
            and now - self._last_soc_update
            <= timedelta(hours=MAX_HISTORICAL_SOC_AGE_HOURS)
        ):
            return self._last_valid_soc
        return None

    def _update_house_soc_watchdog(self, now: datetime) -> None:
        """Energy-based, banded stale watchdog for the HOUSE battery SOC
        (G2's sibling).

        `_get_soc` accepts any in-range value as fresh, so a BMS serving a
        cached, frozen SOC (with fresh timestamps — age checks cannot catch
        it) would keep the planner running on fiction forever. While the SOC
        reading stays EXACTLY unchanged, the watchdog accumulates battery
        energy throughput; once that exceeds the band's configured share of
        capacity, the SOC would have had to move, so the reading is latched as
        stale and `_get_soc` treats it as invalid.

        Flow proof: when a live house-battery power sensor is configured for
        feed-in control, its measured |power| is authoritative. A configured
        but unreadable sensor PAUSES the proof; falling back to the plan there
        would recreate the 2026-09-02 false latch after cascade execution had
        diverged from the plan. Installations without such a sensor retain the
        expected slot-0 battery flow of the last VALID plan as a compatibility
        fallback. Accumulation also PAUSES below HOUSE_SOC_STALE_POWER_W
        (standby / float charge: a constant SOC is physically correct there,
        no evidence either way). Only a CHANGED reading resets the evidence —
        a pause keeps it, so duty-cycled flow still accumulates. Values below
        21 % or above 89 %
        are exempt altogether: balancing and charge/discharge clamping make a
        constant displayed SOC physically plausible there regardless of the
        expected flow. Inside the inclusive 21-89 % plausibility window, the
        configurable edge bands still allow more unexplained drift
        (house_soc_stale_edge_percent) than the mid band
        (house_soc_stale_mid_percent).
        Unlatch: any DIFFERENT live reading (INFO once, G2 pattern).
        In-memory only — a restart re-accumulates the evidence energy.
        """
        raw = self._read_float(self.raw_config[CONF_SOC_ENTITY])
        if raw is not None and not 0.0 <= raw <= 100.0:
            raw = None  # implausible: the normal path rejects it the same way
        if raw is not None and (
            raw < HOUSE_SOC_STALE_CHECK_MIN_PERCENT
            or raw > HOUSE_SOC_STALE_CHECK_MAX_PERCENT
        ):
            # 2026-08-30 incident: the absolute plan-flow proxy repeatedly
            # latched valid Victron plateaus (14/17/18/99 %) and made every
            # coordinator-backed entity unavailable. Do not even retain
            # evidence outside the useful window. Clearing an existing latch
            # here also makes a deployed fix recover without waiting for the
            # displayed SOC to change first.
            if self._house_soc_stale is not None:
                _LOGGER.info(
                    "House battery SOC stale-watchdog latch cleared at %.1f%%"
                    " — outside the %.1f-%.1f%% plausibility window",
                    raw,
                    HOUSE_SOC_STALE_CHECK_MIN_PERCENT,
                    HOUSE_SOC_STALE_CHECK_MAX_PERCENT,
                )
            self._house_soc_stale = None
            self._house_soc_frozen = None
            return
        frozen_value = self._house_soc_stale
        if frozen_value is not None:
            if raw is not None and raw != frozen_value:
                self._house_soc_stale = None
                self._house_soc_frozen = None
                _LOGGER.info(
                    "House battery SOC reports %.1f%% again (was frozen at"
                    " %.1f%%) — stale watchdog latch cleared",
                    raw,
                    frozen_value,
                )
            else:
                return  # still frozen (or no reading): stay latched
        if raw is None:
            return  # no reading this cycle: keep the evidence untouched
        evidence = self._house_soc_frozen
        if evidence is None or evidence[0] != raw:
            # New (or changed) value: (re)seed — no elapsed time to add yet.
            self._house_soc_frozen = (raw, now, 0.0)
            return
        battery_power_entity = self.raw_config.get(CONF_FEEDIN_BATTERY_POWER_ENTITY)
        if battery_power_entity:
            power_w = self._read_float(battery_power_entity)
            flow_proof = "the battery-power sensor measured"
        else:
            power_w = self._expected_battery_power_w
            flow_proof = "the plan expected"
        _value, last, accumulated_wh = evidence
        if power_w is not None and abs(power_w) >= HOUSE_SOC_STALE_POWER_W:
            accumulated_wh += abs(power_w) * (now - last).total_seconds() / 3600.0
        # Advance the cursor on pause cycles too, so a later above-bar cycle
        # is only charged the time actually spent with significant flow.
        self._house_soc_frozen = (raw, now, accumulated_wh)
        cfg = self.raw_config
        # Edge bounds INCLUSIVE (2026-08-02): the Victron calibration plateau
        # demonstrably covers 88.0 itself, so `>= high` must put 88.0 into
        # the loose edge band, not the tight mid band (the operator's
        # "set to 88" intent). Symmetric `<= low` for the bottom plateau.
        if raw <= float(cfg[CONF_HOUSE_SOC_STALE_EDGE_LOW_SOC]) or raw >= float(
            cfg[CONF_HOUSE_SOC_STALE_EDGE_HIGH_SOC]
        ):
            drift_percent = float(cfg[CONF_HOUSE_SOC_STALE_EDGE_PERCENT])
        else:
            drift_percent = float(cfg[CONF_HOUSE_SOC_STALE_MID_PERCENT])
        threshold_wh = float(cfg["battery_capacity_wh"]) * drift_percent / 100.0
        if accumulated_wh > threshold_wh:
            self._house_soc_stale = raw
            _LOGGER.warning(
                "House battery SOC frozen at %.1f%% — %s %.0f Wh of battery"
                " flow without any SOC change (band drift"
                " allowance %.1f%% of capacity) — treating the reading as"
                " STALE (UpdateFailed path) until the sensor reports a"
                " different value",
                raw,
                flow_proof,
                accumulated_wh,
                drift_percent,
            )

    def _get_forecasts(self, now: datetime) -> list[float] | None:
        cfg = self.raw_config
        values = [
            self._read_float(cfg[CONF_PV_FORECAST_TODAY]),
            self._read_float(cfg[CONF_PV_FORECAST_TOMORROW]),
            self._read_float(cfg[CONF_PV_FORECAST_DAY_AFTER]),
        ]
        if all(v is not None for v in values):
            forecasts = [max(0.0, v) for v in values]  # type: ignore[arg-type]
            self._last_valid_forecasts = forecasts
            self._last_forecast_update = now
            return forecasts
        if (
            self._last_valid_forecasts is not None
            and self._last_forecast_update is not None
            and now - self._last_forecast_update
            <= timedelta(hours=MAX_HISTORICAL_FORECAST_AGE_HOURS)
        ):
            return self._last_valid_forecasts
        return None

    # ------------------------------------------------------------------
    # D-A8 stage 2: fail-safe load shed after a sustained data loss
    # ------------------------------------------------------------------

    async def _note_data_loss(self, now: datetime) -> None:
        """Stamp the data-loss clock; fire the fail-safe load shed once the
        outage persists >= STALE_LOAD_SHED_HOURS.

        This hook lives at the UpdateFailed raise site of
        `_async_update_data` — deliberately not in a coordinator error
        callback: it is the ONLY place that (a) knows the failure IS a data
        loss (SOC/forecasts invalid, as opposed to a planner exception) and
        (b) keeps being evaluated while the outage lasts (the coordinator
        keeps polling even while failing, so the 2 h mark is crossed on the
        first refresh past it). The V8 startup grace returns earlier, so the
        clock only starts on a steady-state failure. The clock and the latch
        are persisted: a restart must neither reset the budget nor re-fire
        an already-executed shed. The shed runs detached (pattern:
        `_execute_load_switching`) so a cancelled refresh can never abort it
        half-way, and exactly ONCE per outage episode — the latch is set
        before the task starts.
        """
        if self._data_stale_since is None:
            self._data_stale_since = now
            self._save_persistent_state()
        if self._stale_shed_active or now - self._data_stale_since < timedelta(
            hours=STALE_LOAD_SHED_HOURS
        ):
            return
        if not any(
            subentry.subentry_type == SUBENTRY_TYPE_LOAD
            and subentry.data.get(CONF_LOAD_CONTROL_SWITCH)
            for subentry in self.entry.subentries.values()
        ):
            # No controlled loads: a shed would be pure noise (no actuator,
            # nothing to protect) — the plain UpdateFailed unavailability
            # already covers the outage.
            return
        self._stale_shed_active = True
        self._save_persistent_state()
        _LOGGER.warning(
            "No valid input data for %d+ h — fail-safe (D-A8 stage 2):"
            " force-switching all controlled surplus loads OFF until data"
            " recovers",
            STALE_LOAD_SHED_HOURS,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"stale_data_load_shed_{self.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="stale_data_load_shed",
        )
        await self._push_notification(
            "⚠️ Battery Manager: data loss — loads switched off",
            f"No valid battery SOC or PV forecast data for"
            f" {STALE_LOAD_SHED_HOURS}+ hours. All controlled surplus loads"
            " were force-switched off (fail-safe); they resume automatically"
            " once data returns.",
        )
        self._stale_shed_task = self.entry.async_create_background_task(
            self.hass,
            self._execute_stale_load_shed(),
            name="battery_manager_stale_load_shed",
        )

    async def _note_data_recovered(self) -> None:
        """End a data-loss episode on the first valid update: reset the
        clock; if the stage-2 shed had fired, release the latch, resolve the
        repair issue and push the recovery (gated like the power-warning
        resolve). Recovery never switches anything ON by itself — the normal
        plan of the same cycle re-enables loads where appropriate."""
        if self._data_stale_since is None and not self._stale_shed_active:
            return
        had_shed = self._stale_shed_active
        self._data_stale_since = None
        self._stale_shed_active = False
        self._save_persistent_state()
        if not had_shed:
            return
        _LOGGER.info(
            "Input data recovered — stale-data load shed ended, normal planning resumes"
        )
        ir.async_delete_issue(
            self.hass, DOMAIN, f"stale_data_load_shed_{self.entry.entry_id}"
        )
        if self.raw_config.get(CONF_WARNING_NOTIFY_ON_RESOLVE, True):
            await self._push_notification(
                "✅ Battery Manager: data recovered",
                "Valid battery SOC and PV forecast data are back; the"
                " fail-safe load shed has ended and normal planning resumes.",
            )

    def _read_wh_period(
        self, entity_id: str, attr: str = "wh_period"
    ) -> dict[datetime, float]:
        """Parse an entity's hourly ``wh_period``-family attribute into a
        naive-local hour -> Wh map (docs/F-PREDRAIN.md F1; the quantile
        attributes ``wh_period_p10``/``wh_period_p90`` share the exact same
        format and tolerance rules, F-QUANTILE-BANDS R1).

        When the entity exposes the explicit AC variant of the requested
        attribute (``wh_period_ac`` etc., see _WH_PERIOD_AC_ATTRS) THAT is
        parsed instead: the source labels those buckets as already AC-side, so
        they are used verbatim and excluded from the η scaling in
        _pv_curve_ac_factor. A garbage/absent AC attribute falls back to the
        plain one.

        Keys are parsed with dt_util.parse_datetime: naive keys are treated as
        LOCAL, aware keys are converted to local and made naive. 15/30-min buckets
        are summed per hour by aggregate_hours. Malformed keys/values are skipped;
        non-finite (NaN/±inf) values are skipped and negative values clamped to 0
        (FIX-9) so a bad forecast attribute can never poison the hourly map.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return {}
        raw = state.attributes.get(attr)
        ac_attr = _WH_PERIOD_AC_ATTRS.get(attr)
        if ac_attr is not None:
            ac_raw = state.attributes.get(ac_attr)
            if isinstance(ac_raw, dict):
                raw = ac_raw
        if not isinstance(raw, dict):
            return {}
        entries: list[tuple[datetime, float]] = []
        skipped = 0
        clamped = 0
        for key, value in raw.items():
            ts = dt_util.parse_datetime(str(key))
            if ts is None:
                skipped += 1
                continue
            if ts.tzinfo is not None:
                ts = dt_util.as_local(ts).replace(tzinfo=None)
            try:
                wh = float(value)
            except TypeError, ValueError:
                skipped += 1
                continue
            if not math.isfinite(wh):  # NaN / ±inf
                skipped += 1
                continue
            if wh < 0.0:  # a negative forecast bucket is physically meaningless
                wh = 0.0
                clamped += 1
            entries.append((ts, wh))
        if skipped or clamped:
            _LOGGER.debug(
                "battery_manager: %d skipped, %d clamped %s entries on %s",
                skipped,
                clamped,
                attr,
                entity_id,
            )
        return aggregate_hours(entries)

    def _pv_curve_ac_factor(
        self, entity_id: str, curve: dict[datetime, float]
    ) -> float:
        """AC-efficiency factor η for ONE forecast entity's hourly curve.

        2026-08-07 live audit (7 days): a source may publish ``wh_period*`` as
        a DC model curve while the entity STATE is the AC day energy
        (balcony_solar_forecast: Σ buckets 6.129 kWh == its ``_today_dc``
        sibling; the AC state 5.649 kWh = Σ × 0.9215 learned inverter η).
        Feeding the DC curve into the AC-side simulation over-planned PV by
        ~8.5 % (~1.1 kWh on a normal day) and contributed to an avoidable
        0.7 kWh grid import on 2026-08-07. η = state(Wh) / Σ(curve), derived
        from the SAME entity, corrects that generically: sources whose state
        and curve are consistent get η ≈ 1 (a no-op), a DC-curve/AC-state
        source is pulled down to its own AC reality.

        The state is converted to Wh via its own unit_of_measurement
        (REALIZED_ENERGY_UNIT_FACTORS_WH table reused); unlike
        _energy_unit_factor an UNKNOWN unit does NOT fall back to kWh —
        guessing 1000x either way could halve a perfectly good curve, so an
        underivable η stays 1.0 (missing/invalid/non-positive state, unknown
        unit, empty curve). η is clamped to [PV_CURVE_AC_ETA_MIN, 1.0]: > 1
        means the curve sums BELOW the state (no efficiency effect — e.g. the
        attribute covers only part of the day), below 0.5 is no plausible
        inverter efficiency. An entity carrying the explicit AC curve
        (``wh_period_ac``, preferred by _read_wh_period) is already AC-side
        and is never rescaled.
        """
        state = self.hass.states.get(entity_id)
        if state is None or isinstance(
            state.attributes.get(_WH_PERIOD_AC_ATTRS["wh_period"]), dict
        ):
            return 1.0
        total = sum(curve.values())
        if total <= 0.0:
            return 1.0
        unit_factor = REALIZED_ENERGY_UNIT_FACTORS_WH.get(
            state.attributes.get("unit_of_measurement")
        )
        try:
            state_value = float(state.state)
        except TypeError, ValueError:
            return 1.0
        if unit_factor is None or not math.isfinite(state_value) or state_value <= 0.0:
            return 1.0
        eta = min(max(state_value * unit_factor / total, PV_CURVE_AC_ETA_MIN), 1.0)
        if eta != 1.0:
            _LOGGER.debug(
                "battery_manager: %s hourly PV curve scaled by AC factor %.3f"
                " (state %.1f Wh / curve sum %.1f Wh)",
                entity_id,
                eta,
                state_value * unit_factor,
                total,
            )
        return eta

    def _get_pv_hourly(
        self, now: datetime
    ) -> tuple[
        dict[datetime, float] | None,
        dict[datetime, float] | None,
        dict[datetime, float] | None,
    ]:
        """Merged naive-local hourly PV maps (median, p10, p90) from the three
        forecast entities.

        Stale-cache fallback is PER ENTITY (FIX-4): each cycle every entity uses
        its fresh maps when the MEDIAN map is non-empty, else its own last-good
        entry within the max-age window — so a single unavailable entity no
        longer overwrites the cached full map with a partial read. The quantile
        maps (F-QUANTILE-BANDS R1) are parsed from ``wh_period_p10``/
        ``wh_period_p90`` on the SAME entities and travel in the same cache
        entry, so median and bands always come from the same read. Each
        entity's fresh maps are scaled by its AC factor η
        (_pv_curve_ac_factor) BEFORE caching: median and bands get the SAME η
        (they describe the same physical day) and the FIX-4 stale-cache replay
        serves the already-scaled maps — never a second scaling.
        Absent/empty/garbage quantile attributes simply yield empty maps (never
        an error). The per-entity results merge in the existing (today,
        tomorrow, day-after) order; overlapping days are last-writer-wins (the
        Open-Meteo family entities carry identical overlapping data, so this is
        harmless). In "daily" mode the hourly attributes are ignored (None
        everywhere) so build_slots uses the two-window model and no slot ever
        carries a band.
        """
        if self._pv_forecast_mode == PV_FORECAST_MODE_DAILY:
            return None, None, None
        cfg = self.raw_config
        max_age = timedelta(hours=MAX_HISTORICAL_FORECAST_AGE_HOURS)
        merged: dict[datetime, float] = {}
        merged_p10: dict[datetime, float] = {}
        merged_p90: dict[datetime, float] = {}
        for key in (
            CONF_PV_FORECAST_TODAY,
            CONF_PV_FORECAST_TOMORROW,
            CONF_PV_FORECAST_DAY_AFTER,
        ):
            conf_key = cfg[key]
            fresh = self._read_wh_period(conf_key)
            if fresh:
                fresh_p10 = self._read_wh_period(conf_key, "wh_period_p10")
                fresh_p90 = self._read_wh_period(conf_key, "wh_period_p90")
                # One η per entity per read, applied to median AND bands
                # alike, then frozen into the cache entry (no re-scale on
                # replay — the cached maps are already AC-side).
                eta = self._pv_curve_ac_factor(conf_key, fresh)
                if eta != 1.0:
                    fresh = {hour: wh * eta for hour, wh in fresh.items()}
                    fresh_p10 = {hour: wh * eta for hour, wh in fresh_p10.items()}
                    fresh_p90 = {hour: wh * eta for hour, wh in fresh_p90.items()}
                self._pv_hourly_by_entity[conf_key] = (
                    fresh,
                    fresh_p10,
                    fresh_p90,
                    now,
                )
                merged.update(fresh)
                merged_p10.update(fresh_p10)
                merged_p90.update(fresh_p90)
                continue
            cached = self._pv_hourly_by_entity.get(conf_key)
            if cached is not None and now - cached[3] <= max_age:
                merged.update(cached[0])
                merged_p10.update(cached[1])
                merged_p90.update(cached[2])
            # An entity that never provides wh_period simply contributes nothing.
        self._maybe_warn_hourly_empty(bool(merged))
        return (
            merged if merged else None,
            merged_p10 if merged_p10 else None,
            merged_p90 if merged_p90 else None,
        )

    def _maybe_warn_hourly_empty(self, has_data: bool) -> None:
        """FIX-10: in explicit "hourly" mode, warn ONCE (state-change guarded, not
        per cycle) when the merged hourly map is empty/expired and the planner
        silently falls back to the two-window model. "auto"/"daily" stay quiet."""
        if self._pv_forecast_mode != PV_FORECAST_MODE_HOURLY:
            self._pv_hourly_empty_warned = False
            return
        if not has_data and not self._pv_hourly_empty_warned:
            _LOGGER.warning(
                "battery_manager: hourly PV mode active but no wh_period data —"
                " falling back to the two-window model"
            )
            self._pv_hourly_empty_warned = True
        elif has_data:
            self._pv_hourly_empty_warned = False

    def _pv_day_sources(
        self, now: datetime, num_days: int, pv_hourly: dict[datetime, float] | None
    ) -> dict[str, str]:
        """Per-day PV source label for later sensor exposure (docs/F-PREDRAIN.md
        F1 diagnostics). A day is "hourly" when the merged map carries at least
        one bucket for that calendar day, else "two_window"."""
        covered_days = {key.date() for key in pv_hourly} if pv_hourly else set()
        sources: dict[str, str] = {}
        for offset in range(num_days):
            day = now.date() + timedelta(days=offset)
            sources[day.isoformat()] = "hourly" if day in covered_days else "two_window"
        return sources

    def _quantile_coverage(self, inputs) -> dict[str, dict[str, Any]]:
        """Per forecast day: fraction of DAYLIGHT slots (pv_wh > 0) carrying a
        P10/P90 band, plus a source label (F-QUANTILE-BANDS R7). Computed from
        the same D2 band predicate the planner gates with, so the operator can
        literally watch the bands mature bin by bin."""
        band = quantile_band_slots(inputs.slots)
        daylight: dict[str, int] = {}
        banded: dict[str, int] = {}
        days: list[str] = []
        for slot, has_band in zip(inputs.slots, band, strict=True):
            day = slot.start.date().isoformat()
            if day not in daylight:
                days.append(day)
                daylight[day] = 0
                banded[day] = 0
            if slot.pv_wh <= 0.0:
                continue
            daylight[day] += 1
            if has_band:
                banded[day] += 1
        coverage: dict[str, dict[str, Any]] = {}
        for day in days:
            total, hits = daylight[day], banded[day]
            source = "scalar" if hits == 0 else "p10/p90" if hits == total else "mixed"
            coverage[day] = {
                "coverage": round(hits / total, 2) if total else 0.0,
                "source": source,
            }
        return coverage

    def _update_predrain_block_evidence(self, result, inputs, now: datetime) -> None:
        """Stability gate for pass-3 pre-drain blocks (F-PREDRAIN-BLOCK R7).

        A continuous load's block is only ACTUATED after
        PREDRAIN_BLOCK_STABLE_PLANS consecutive plans proposed the identical
        [start, end) window AND the first proposal is at least
        PREDRAIN_BLOCK_STABLE_MINUTES old — the 2026-08-01 flicker run showed
        a marginal block flipping in and out within minutes, producing a
        pointless 19-min battery run; the time floor covers fast state-driven
        refresh cadences where three plans pass in ~90 s. The count resets on
        any signature change; a RUNNING load is never cut by this gate (only
        the OFF->ON transition is gated — the plan itself retracts the
        recommendation when the block disappears). In-memory only: a restart
        re-counts, which merely delays the block.
        """
        signatures: dict[str, tuple[str | None, str]] = {}
        slot0_day = inputs.slots[0].start.date() if inputs.slots else None
        for load_plan in result.load_plans:
            for start, count, pass_no, _wh in load_plan.allocations:
                if pass_no != 3 or start + count > len(inputs.slots):
                    continue
                # Per-day blocks (R1, v0.22.0): only the block of slot 0's
                # own day is actuation-relevant — future-day blocks are
                # plan/display until their day comes and must not feed the
                # stability evidence (or switch anything now).
                if inputs.slots[start].start.date() != slot0_day:
                    continue
                last = inputs.slots[start + count - 1]
                signatures[load_plan.load_id] = (
                    # A block covering slot 0 starts "now" — slot 0's own start
                    # moves with every refresh, which would reset the evidence
                    # on every cycle instead of counting a stable running block.
                    None if start == 0 else inputs.slots[start].start.isoformat(),
                    (last.start + timedelta(hours=last.duration)).isoformat(),
                )
        stable: set[str] = set()
        for load_id, signature in signatures.items():
            prev = self._predrain_block_evidence.get(load_id)
            if prev is not None and prev[0] == signature:
                count, first_seen = prev[1] + 1, prev[2]
            else:
                count, first_seen = 1, now
            self._predrain_block_evidence[load_id] = (signature, count, first_seen)
            if count >= PREDRAIN_BLOCK_STABLE_PLANS and now - first_seen >= timedelta(
                minutes=PREDRAIN_BLOCK_STABLE_MINUTES
            ):
                stable.add(load_id)
        # Loads whose block vanished lose the evidence (and the stability).
        for load_id in list(self._predrain_block_evidence):
            if load_id not in signatures:
                del self._predrain_block_evidence[load_id]
        self._predrain_block_stable = stable

    def _predrain_block_attr(self, load_plan, inputs) -> dict[str, Any] | None:
        """The load's pass-3 pre-drain block for the sensor attributes: the
        [start, end) window plus the stability-gate progress (consecutive
        identical plans), or None when the plan books no block."""
        for start, count, pass_no, _wh in load_plan.allocations:
            if pass_no == 3 and start + count <= len(inputs.slots):
                last = inputs.slots[start + count - 1]
                evidence = self._predrain_block_evidence.get(load_plan.load_id)
                return {
                    "start": inputs.slots[start].start.isoformat(),
                    "end": (last.start + timedelta(hours=last.duration)).isoformat(),
                    "stable_plans": evidence[1] if evidence else 0,
                }
        return None

    def _log_night_predrain(self, result, inputs, config: SystemConfig) -> None:
        """One INFO line when the plan books a pre-drain block (pass 3).

        The block IS the F-PREDRAIN-BLOCK feature's headline action: a
        continuous load running ahead of today's SOC peak to make room —
        surface it with the load name, the block's [start, end) window and
        the booked energy. Only continuous loads can appear here (pass 3 is
        their pass; energy-limited loads bet slot-wise in pass 2, daylight
        only). Emitted only on MATERIAL change vs the last LOGGED booking
        (FIX-11 hardened by the 7-day live audit 31.07.–07.08.2026): the raw
        (load, start, end) change-gate ratcheted with the clock (slot 0
        covers "now") and the forecast (booked Wh), so every cycle looked
        like a new booking — 4,196 lines in 7 days (1,753 on 08-04 ≈ 1/min).
        Material means: a new block (load + block-day unseen, or re-booked
        after a gap), the start shifted >= PREDRAIN_BLOCK_LOG_SHIFT_MIN, or
        the energy changed >= PREDRAIN_BLOCK_LOG_ENERGY_WH; even those are
        rate-limited to one line per block per PREDRAIN_BLOCK_LOG_RATE_LIMIT_MIN
        (oscillating forecasts), with suppressed changes at DEBUG. Comparing
        against the last LOGGED — not last seen — booking means a slowly
        ratcheting block still surfaces once its cumulative drift crosses a
        threshold. The stability gate keeps the ACTUATION itself off a
        flickering block (F-PREDRAIN-BLOCK R7).
        """
        if not result.load_plans:
            self._night_predrain_logged.clear()
            return
        booked: list[str] = []
        seen: set[str] = set()
        now = dt_util.utcnow()
        for load_plan, load in zip(result.load_plans, config.loads, strict=True):
            if load.energy_limited:
                continue
            for start, count, pass_no, wh in load_plan.allocations:
                if pass_no != 3 or start + count > len(inputs.slots):
                    continue
                block_start = inputs.slots[start].start
                last = inputs.slots[start + count - 1]
                block_end = last.start + timedelta(hours=last.duration)
                # Per load AND block day (per-day blocks, R1): tomorrow's
                # block is a separate booking from today's.
                key = f"{load.load_id}:{block_start.date().isoformat()}"
                seen.add(key)
                prev = self._night_predrain_logged.get(key)
                material = (
                    prev is None
                    or abs((block_start - prev[0]).total_seconds())
                    >= PREDRAIN_BLOCK_LOG_SHIFT_MIN * 60
                    or abs(wh - prev[2]) >= PREDRAIN_BLOCK_LOG_ENERGY_WH
                )
                if not material:
                    continue
                line = (
                    f"{load.name} {block_start.strftime('%Y-%m-%d %H:%M')}"
                    f" -> {block_end.strftime('%H:%M')} ({wh:.0f} Wh)"
                )
                if prev is not None and now - prev[3] < timedelta(
                    minutes=PREDRAIN_BLOCK_LOG_RATE_LIMIT_MIN
                ):
                    _LOGGER.debug(
                        "F-PREDRAIN-BLOCK: rate-limited booking change for %s", line
                    )
                    continue
                booked.append(line)
                self._night_predrain_logged[key] = (block_start, block_end, wh, now)
        # Blocks that vanished lose their logged record: a re-booking after
        # a gap is a NEW booking and logs again.
        for key in list(self._night_predrain_logged):
            if key not in seen:
                del self._night_predrain_logged[key]
        if booked:
            _LOGGER.info("F-PREDRAIN-BLOCK: pre-drain booked for %s", "; ".join(booked))

    async def _update_power_warnings(self, result, now: datetime) -> None:
        """Per-load power-deviation warning (operator requirement F-L7).

        While a load runs at the integration's request but its real draw
        deviates from the CONFIGURED power by more than the per-load
        percentage for the per-load dwell in sustained minutes, the load's
        warning binary sensor turns on — full water tank (draw near 0 W),
        wrong nominal power or a foreign consumer on the measured outlet.
        Short defrost pauses reset the timer before the dwell elapses. A
        missing reading freezes the current state.

        The warning LATCHES: once on, it clears only when the load runs at
        its configured power again (active + measured back within band), not
        merely because BM stopped requesting it — a full tank is still full
        while the load is off (operator wish 2026-07-12). On the on/off edge
        it pushes a notification to the configured targets.
        """
        active_by_id = {lp.load_id: lp.active_now for lp in result.load_plans}
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            # Subentries created before the warning existed lack the key:
            # default 0 % (off) — the operator opts a load in per device.
            pct = float(
                data.get(
                    CONF_LOAD_POWER_WARNING_PCT,
                    DEFAULT_LOAD_CONFIG[CONF_LOAD_POWER_WARNING_PCT],
                )
            )
            power_entity = data.get(CONF_LOAD_POWER_ENTITY)
            if pct <= 0 or not power_entity:
                # Warning disabled (0 %) or no feedback sensor: drop any
                # lingering latch/timer so the feature turns fully off (and a
                # latch can never get stuck once recovery is unobservable).
                self._load_deviation_since.pop(subentry_id, None)
                if self._load_power_warning.pop(subentry_id, None):
                    self._save_persistent_state()
                continue
            dwell = float(
                data.get(
                    CONF_LOAD_POWER_WARNING_DWELL_MIN,
                    DEFAULT_LOAD_CONFIG[CONF_LOAD_POWER_WARNING_DWELL_MIN],
                )
            )
            if self._floor_guard_active:
                # G4: under the floor guard no load runs at BM's request —
                # a recommendation-only load stopped by the operator's
                # automation must not trip a 0 W "full tank" warning while
                # its plan flag is nominally still active.
                active = False
            elif subentry_id in self._load_charging_active:
                active = self._load_charging_active[subentry_id]
            else:
                active = active_by_id.get(subentry_id, False)
            if not active:
                # Reset the dwell timer, but keep a latched warning on: it
                # clears only when the load demonstrably runs again.
                self._load_deviation_since.pop(subentry_id, None)
                continue
            raw = self._read_float(power_entity)
            if raw is None:
                continue  # no reading: keep the current state
            nominal = float(data[CONF_LOAD_POWER_W])
            self._load_warning_diag[subentry_id] = {
                "expected_w": nominal,
                "measured_w": raw,
                "since": self._load_deviation_since.get(subentry_id),
            }
            if abs(raw - nominal) <= pct / 100.0 * nominal:
                # Demonstrably running at its configured power again -> clear.
                self._load_deviation_since.pop(subentry_id, None)
                await self._set_power_warning(subentry_id, subentry.title, False)
                continue
            since = self._load_deviation_since.setdefault(subentry_id, now)
            self._load_warning_diag[subentry_id]["since"] = since
            if now - since >= timedelta(minutes=dwell):
                await self._set_power_warning(
                    subentry_id,
                    subentry.title,
                    True,
                    raw=raw,
                    nominal=nominal,
                    dwell=dwell,
                )

    async def _set_power_warning(
        self,
        subentry_id: str,
        title: str,
        on: bool,
        raw: float | None = None,
        nominal: float | None = None,
        dwell: float | None = None,
    ) -> None:
        if self._load_power_warning.get(subentry_id, False) == on:
            return
        self._load_power_warning[subentry_id] = on
        # V6 (F-TANK): the power-warning latch edges ARE the tank events.
        subentry = self.entry.subentries.get(subentry_id)
        if subentry is not None and self._tank_enabled(subentry.data):
            if on:
                # Latch ENTRY = tank full: capture the runtime achieved this
                # cycle (the runtime it took to fill the tank). The counter has
                # frozen at the fill point — the ~2 W standby stays below the
                # runtime bar — so this is the true full-tank runtime. Committed
                # as a learning sample only at the release, once the emptying is
                # confirmed (a latch that never releases must not learn).
                self._load_tank_full_min[subentry_id] = self.load_runtime_minutes(
                    subentry_id
                )
            else:
                # Latch RELEASE = the device runs at full power again -> the tank
                # was emptied. Learn the captured full-tank runtime, then reset
                # the runtime counter (a fresh tank cycle) and re-arm the push.
                full_min = self._load_tank_full_min.pop(subentry_id, None)
                if full_min is not None:
                    self._tank_record_sample(subentry_id, full_min)
                self._load_runtime_seconds[subentry_id] = 0.0
                if subentry_id in self._load_run_since:
                    self._load_run_since[subentry_id] = dt_util.utcnow()
                self._load_tank_notified.discard(subentry_id)
        if not on:
            # F10: the latch released — if a hold was running it succeeded (tank
            # emptied / draw back in band), so the run is now a genuine plan run
            # and its later stop follows the normal flicker rules.
            self._load_latch_hold.discard(subentry_id)
        # Persist the latch so a coordinator reload (any options save) or a
        # restart does not silently drop a raised warning (operator wish
        # 2026-07-12); async_flush_persistent_state writes it before a reload.
        self._save_persistent_state()
        if on:
            _LOGGER.warning(
                "Load %s draws %.0f W while %.0f W are configured"
                " (sustained > %g min) — full tank, wrong configured power"
                " or a foreign consumer?",
                title,
                raw,
                nominal,
                dwell,
            )
            await self._notify_power_warning(
                title, on=True, raw=raw, nominal=nominal, dwell=dwell
            )
        else:
            _LOGGER.info("Load %s: power warning cleared", title)
            await self._notify_power_warning(title, on=False)

    async def _push_notification(self, msg_title: str, message: str) -> None:
        """Push to the configured global notify targets
        (CONF_WARNING_NOTIFY_TARGETS) — the same mechanism the power warning
        uses: each target is called independently so one stale/removed
        target (ServiceNotFound) neither stalls the update nor blocks the
        others. No targets -> no-op. Used by the fail-safe escalations
        (D-A8 stage 2, support-switch failures) whose messages are not
        per-load power warnings.
        """
        targets = self.raw_config.get(CONF_WARNING_NOTIFY_TARGETS) or []
        for service in targets:
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {"title": msg_title, "message": message},
                    blocking=False,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Notification to notify.%s failed: %s",
                    service,
                    err,
                )

    async def _notify_power_warning(
        self,
        title: str,
        on: bool,
        raw: float | None = None,
        nominal: float | None = None,
        dwell: float | None = None,
    ) -> None:
        """Push the power warning to the configured global notify targets.

        Targets are `notify` service names (e.g. "mobile_app_pixel"); each is
        called independently so one stale/removed target (ServiceNotFound)
        neither stalls the update nor blocks the others. No targets -> no-op.
        """
        targets = self.raw_config.get(CONF_WARNING_NOTIFY_TARGETS) or []
        if not targets:
            return
        if on:
            msg_title = f"⚠️ {title}: power warning"
            message = (
                f"{title} draws {raw:.0f} W but {nominal:.0f} W are configured"
                f" (sustained over {dwell:g} min). Check the device — full"
                " water tank, wrong configured power, or a foreign consumer."
            )
        else:
            if not self.raw_config.get(CONF_WARNING_NOTIFY_ON_RESOLVE, True):
                return
            msg_title = f"✅ {title}: power warning cleared"
            message = f"{title} draws its configured power again."
        for service in targets:
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {"title": msg_title, "message": message},
                    blocking=False,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Power-warning notification to notify.%s failed: %s",
                    service,
                    err,
                )

    async def _update_tank_forecast(self, result, now: datetime) -> None:
        """V6 (F-TANK): refresh per-tank-load runtime diagnostics and push the
        single "tank nearly full" notification per tank cycle.

        The push fires ONCE per tank cycle (latch -> reset) when the predicted
        remaining tank RUN time drops below TANK_WARN_LEAD_MIN AND the load is
        still planned to run in the current plan — so an idle device never warns
        and the lead time is grounded in planned runtime, not wall-clock time
        (operator design point 4). A load already latched full is skipped: the
        F-L7 power warning has already fired for it."""
        planned_upcoming = {
            lp.load_id: bool(lp.schedule) and any(lp.schedule)
            for lp in result.load_plans
        }
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            if not self._tank_enabled(data):
                self._load_tank_diag.pop(subentry_id, None)
                continue
            learned = self._tank_learned_full_min(subentry_id, data)
            remaining = self._tank_remaining_min(subentry_id, data) or 0.0
            self._load_tank_diag[subentry_id] = {
                "tank_remaining_min": round(remaining, 1),
                "tank_learned_full_min": round(learned, 1),
                "tank_samples": len(self._load_tank_samples.get(subentry_id, [])),
            }
            if self._load_power_warning.get(subentry_id, False):
                continue  # already latched full — F-L7 warning covers it
            if (
                planned_upcoming.get(subentry_id, False)
                and remaining < TANK_WARN_LEAD_MIN
                and subentry_id not in self._load_tank_notified
            ):
                self._load_tank_notified.add(subentry_id)
                await self._notify_tank_full_soon(subentry.title, remaining)

    async def _notify_tank_full_soon(self, title: str, remaining_min: float) -> None:
        """Push the "tank nearly full" warning to the global notify targets
        (V6), reusing the power-warning target list. No targets -> no-op; each
        target is called independently so one stale service cannot block."""
        targets = self.raw_config.get(CONF_WARNING_NOTIFY_TARGETS) or []
        if not targets:
            return
        msg_title = f"🪣 {title}: tank nearly full"
        message = (
            f"{title}: tank likely full within {max(0, round(remaining_min))} min"
            " of runtime — please empty it."
        )
        for service in targets:
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {"title": msg_title, "message": message},
                    blocking=False,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Tank notification to notify.%s failed: %s",
                    service,
                    err,
                )

    def _update_plan_active(self, result) -> None:
        """Track each load's plan activation and its learning permission.

        At the OFF->ON edge of a recommendation, `_load_learn_ok` snapshots
        whether the measured outlet was idle: only then did the draw start
        in response to the plan, so only then may it train the planning
        power. Without the snapshot, a pre-existing manual/foreign draw
        would be learned on the first active cycle, flip the next plan to
        inactive, get deleted again, and so on — a period-2 recommendation
        oscillation (adversarial-review finding, 2026-07-05).
        """
        for load_plan in result.load_plans:
            prev = self._load_plan_active.get(load_plan.load_id, False)
            if load_plan.active_now and not prev:
                self._load_learn_ok[load_plan.load_id] = not self._draw_above_standby(
                    load_plan.load_id
                )
            elif not load_plan.active_now:
                self._load_learn_ok.pop(load_plan.load_id, None)
            self._load_plan_active[load_plan.load_id] = load_plan.active_now

    def _load_standby_bar(self, data: dict[str, Any]) -> float:
        """The single standby threshold, reused by the median learner, the G2
        stale-SOC evidence path, and the F4 rec-only evidence check: readings
        below max(10 W, STANDBY_FRACTION * nominal) are standby/idle draw, not a
        charge sample (2026-07-05 live incident: a 400 W dehumidifier idling at
        ~20 W cleared the old flat 10 W bar and got planned at 22 W)."""
        return max(10.0, STANDBY_FRACTION * float(data[CONF_LOAD_POWER_W]))

    def _draw_above_standby(self, subentry_id: str) -> bool:
        """True when the load's feedback currently reads above the standby
        threshold — i.e. something is already drawing on the measured
        outlet."""
        subentry = self.entry.subentries.get(subentry_id)
        if subentry is None:
            return False
        data = subentry.data
        if not data.get(CONF_LOAD_POWER_ENTITY):
            return False
        raw = self._read_float(data[CONF_LOAD_POWER_ENTITY])
        if raw is None:
            return False
        return raw >= self._load_standby_bar(data)

    def _update_gate_calibration(self, config: SystemConfig, soc: float) -> None:
        """Track the SOC bracket where the real battery voltage crosses the
        48 V PSU output (F-N3 phase 3). The gate SOC proxy should sit near
        this crossing; the bracket is exposed as a hint for the operator.

        Because the bus voltage sags under load, the two edges can overlap;
        both are surfaced raw so the operator can judge in-season.
        """
        entity_id = self.raw_config.get(CONF_BATTERY_VOLTAGE_ENTITY)
        if not entity_id or not config.support.configured:
            return
        voltage = self._read_float(entity_id)
        if voltage is None or not (40.0 <= voltage <= 60.0):
            return  # missing or implausible reading
        threshold = config.support.psu48_output_voltage_v
        if voltage < threshold:
            # PSU would deliver here — remember the highest such SOC.
            prev = self._gate_cal["below_max_soc"]
            if prev is None or soc > prev:
                self._gate_cal["below_max_soc"] = soc
        else:
            # PSU gated off — remember the lowest such SOC.
            prev = self._gate_cal["above_min_soc"]
            if prev is None or soc < prev:
                self._gate_cal["above_min_soc"] = soc

    def _gate_calibration_diag(self, config: SystemConfig) -> dict[str, Any]:
        below = self._gate_cal["below_max_soc"]
        above = self._gate_cal["above_min_soc"]
        cells = int(self.raw_config.get(CONF_BATTERY_CELLS_SERIES, 16))
        threshold = config.support.psu48_output_voltage_v
        suggested = None
        if below is not None and above is not None and below <= above:
            suggested = round((below + above) / 2.0, 1)
        return {
            "threshold_v": threshold,
            "volt_per_cell": round(threshold / cells, 3) if cells else None,
            "delivering_below_soc_max": below,
            "gated_above_soc_min": above,
            "suggested_gate_soc": suggested,
            "gate_soc_active": config.support.gate_soc_percent,
        }

    def _bm_load_active(self, subentry_id: str) -> bool:
        """True while the load runs at the integration's own request.

        Switched loads: the real charging state (plug AND enable on, healed
        from entity states every cycle — the feedback meters the device
        itself, so even a manually started charge yields correct device
        data; contamination is bounded by the switch dwell). Recommendation-
        only loads: the last plan's slot-0 activation AND a clean start
        (see _update_plan_active). Feedback samples outside these windows
        come from manual runs or foreign consumers on the measured outlet
        and must not train the planning power (operator decision F-L6,
        2026-07-05: manual activations must not influence future planning).
        """
        if subentry_id in self._load_charging_active:
            return bool(self._load_charging_active[subentry_id])
        return bool(self._load_plan_active.get(subentry_id)) and bool(
            self._load_learn_ok.get(subentry_id, False)
        )

    def _load_soc_cache_stale(self, cached: dict[str, Any]) -> bool:
        """A cached load SOC older than LOAD_SOC_CACHE_MAX_AGE_HOURS is no longer
        trustworthy. A legacy entry without a timestamp is trusted once (it is
        re-stamped on the next real reading)."""
        ts = cached.get("ts")
        if ts is None:
            return False
        parsed = dt_util.parse_datetime(ts)
        if parsed is None:
            return False
        return dt_util.now() - parsed > timedelta(hours=LOAD_SOC_CACHE_MAX_AGE_HOURS)

    def load_bm_enabled(self, load_id: str) -> bool:
        """Whether BM may plan/actuate this load (the per-load control switch)."""
        return self._load_bm_enabled.get(load_id, True)

    def set_load_enabled(self, load_id: str, enabled: bool) -> None:
        """Enable/disable BM control of one load (v0.7.17 per-load switch).

        Disabling holds the load UNAVAILABLE — the planner drops it and the
        executor switches it off next cycle — WITHOUT touching the load's
        control-switch config, so the operator can pause a device with one tap
        and resume later. Persisted so it survives restarts."""
        self._load_bm_enabled[load_id] = bool(enabled)
        self._save_persistent_state()

    def power_calibration_supported(self, load_id: str) -> bool:
        """Whether a load has every actor/sensor needed for a manual probe."""
        subentry = self.entry.subentries.get(load_id)
        if subentry is None or subentry.subentry_type != SUBENTRY_TYPE_LOAD:
            return False
        data = subentry.data
        if not (
            data.get(CONF_LOAD_ENERGY_LIMITED)
            and data.get(CONF_LOAD_CONTROL_SWITCH)
            and data.get(CONF_LOAD_POWER_ENTITY)
        ):
            return False
        # A cascade has exactly one actor owner. A per-member calibration must
        # never bypass CascadeManager's wake/handover/safe-off state machine.
        return not any(
            load_id in cascade.data.get(CONF_CASCADE_MEMBER_IDS, [])
            for cascade in self.entry.subentries.values()
            if cascade.subentry_type == SUBENTRY_TYPE_CASCADE
        )

    def power_calibration_button_available(self, load_id: str) -> bool:
        """The active load's button remains available so a second press aborts."""
        return self.power_calibration_supported(load_id) and (
            self._load_power_calibration_id in (None, load_id)
        )

    def load_power_calibration_diagnostics(self, load_id: str) -> dict[str, Any]:
        """Volatile progress/result details exposed on the planning-power sensor."""
        return dict(self._load_power_calibration_diag.get(load_id, {"state": "idle"}))

    def load_planning_power_diagnostics(self, load_id: str) -> dict[str, Any]:
        """Exact scalar and source used by the latest published plan."""
        plan_data = ((self.data or {}).get("load_plans") or {}).get(load_id, {})
        subentry = self.entry.subentries.get(load_id)
        configured = (
            float(subentry.data.get(CONF_LOAD_POWER_W, 0.0)) if subentry else None
        )
        return {
            "planning_power_w": plan_data.get("planning_power_w"),
            "source": plan_data.get("planning_power_source"),
            "configured_power_w": configured,
            "learned_power_w": self._load_learned_power_w.get(load_id),
            "calibration": self.load_power_calibration_diagnostics(load_id),
        }

    def _set_power_calibration_diag(self, load_id: str, **values: Any) -> None:
        diag = dict(self._load_power_calibration_diag.get(load_id, {}))
        diag.update(values)
        self._load_power_calibration_diag[load_id] = diag
        self.async_update_listeners()

    async def async_toggle_load_power_calibration(self, load_id: str) -> None:
        """Start a bounded manual probe, or abort it on a second button press."""
        task = self._load_power_calibration_task
        if task is not None and not task.done():
            if self._load_power_calibration_id != load_id:
                raise HomeAssistantError(
                    "Another surplus-load power calibration is already running"
                )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return
        if not self.power_calibration_supported(load_id):
            raise HomeAssistantError(
                "Power calibration needs an energy-limited, directly controlled "
                "load with a power-feedback sensor and no cascade ownership"
            )
        data = self.entry.subentries[load_id].data
        if self._charging_is_active(data):
            raise HomeAssistantError(
                "Power calibration can only start while the load is not charging"
            )
        baseline = self._read_float(data[CONF_LOAD_POWER_ENTITY])
        if baseline is None:
            raise HomeAssistantError("The load power-feedback sensor is unavailable")
        if baseline >= self._load_standby_bar(data):
            raise HomeAssistantError(
                "The load already draws power; calibration needs an idle baseline"
            )

        self._load_power_calibration_id = load_id
        self._load_power_calibration_restored = None
        self._set_power_calibration_diag(
            load_id,
            state="starting",
            samples=0,
            baseline_w=round(baseline, 1),
            result_w=None,
            error=None,
        )
        # Persist actor ownership BEFORE applying grid power. A clean reload can
        # then stop a probe interrupted between any two awaits below.
        await self.async_flush_persistent_state()
        self._load_power_calibration_task = self.entry.async_create_background_task(
            self.hass,
            self._run_load_power_calibration(load_id, dict(data), baseline),
            name=f"battery_manager_power_calibration_{load_id}",
        )

    async def _run_load_power_calibration(
        self, load_id: str, data: dict[str, Any], baseline_w: float
    ) -> None:
        """Actuate, sample new sensor publications, learn their median, release."""
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        measurement_started_at: float | None = None
        samples: list[float] = []
        power_entity = data[CONF_LOAD_POWER_ENTITY]
        baseline_state = self.hass.states.get(power_entity)
        last_buffered_update = (
            baseline_state.last_reported if baseline_state is not None else None
        )
        last_buffered_at: float | None = None
        activation_attempted = False
        cancelled = False
        try:
            plug_was_on = self._entity_is_on(data[CONF_LOAD_CONTROL_SWITCH])
            activation_attempted = True
            await self._execute_load_switching(
                [
                    (
                        load_id,
                        data,
                        True,
                        plug_was_on,
                        0.0,
                        False,
                        "manual power calibration",
                        True,  # explicit operator-authorised G4/plan bypass
                    )
                ]
            )
            # A blocking HA service call only waits for the service handler; a
            # physical Shelly may publish its new state seconds later. The
            # v0.27.0 live Fossibot-B probe checked exactly once here, mistook
            # that propagation delay for an actor failure and immediately ran
            # the release OFF before the power station could wake. Keep the
            # overall four-minute cap, but give both plug and optional gate a
            # bounded confirmation window before sampling starts.
            self._set_power_calibration_diag(load_id, state="waiting_for_actor")
            actor_deadline = loop.time() + POWER_CALIBRATION_ACTOR_CONFIRM_TIMEOUT_S
            while self._charging_is_active(data) is not True:
                if loop.time() >= actor_deadline:
                    raise HomeAssistantError(
                        "The load did not confirm charging active within "
                        f"{POWER_CALIBRATION_ACTOR_CONFIRM_TIMEOUT_S:g} seconds"
                    )
                await asyncio.sleep(POWER_CALIBRATION_POLL_S)
            self._set_power_calibration_diag(load_id, state="waiting_for_power")

            while loop.time() - started_at < POWER_CALIBRATION_MAX_TOTAL_S:
                await asyncio.sleep(POWER_CALIBRATION_POLL_S)
                now_mono = loop.time()
                raw = self._read_float(power_entity)
                state = self.hass.states.get(power_entity)
                if raw is None or state is None:
                    continue

                jump_w = abs(raw - baseline_w)
                jump_threshold_w = POWER_CALIBRATION_JUMP_FRACTION * float(
                    data[CONF_LOAD_POWER_W]
                )
                if measurement_started_at is None and (
                    jump_w >= jump_threshold_w
                    or now_mono - started_at >= POWER_CALIBRATION_START_WAIT_S
                ):
                    measurement_started_at = now_mono
                    self._set_power_calibration_diag(load_id, state="measuring")

                if measurement_started_at is None:
                    continue
                # last_reported advances even when the sensor publishes the
                # same numeric value again; last_updated intentionally does
                # not. A stable 500 W report every 30 s is still four real
                # measurements and must therefore fill the buffer.
                is_new_publication = state.last_reported != last_buffered_update
                sample_interval_ok = (
                    last_buffered_at is None
                    or now_mono - last_buffered_at
                    >= POWER_CALIBRATION_MIN_SAMPLE_INTERVAL_S
                )
                if (
                    is_new_publication
                    and sample_interval_ok
                    and raw >= self._load_standby_bar(data)
                ):
                    samples.append(raw)
                    last_buffered_update = state.last_reported
                    last_buffered_at = now_mono
                    self._set_power_calibration_diag(
                        load_id, samples=len(samples), last_sample_w=round(raw, 1)
                    )

                measured_for = now_mono - measurement_started_at
                enough = len(samples) >= POWER_CALIBRATION_MIN_SAMPLES or (
                    measured_for >= POWER_CALIBRATION_MIN_MEASURE_S and bool(samples)
                )
                if enough:
                    learned = round(float(median(samples)), 1)
                    self._load_learned_power_w[load_id] = learned
                    self._load_power_samples.pop(load_id, None)
                    self._warn_estimate_overrun(
                        load_id,
                        self.entry.subentries[load_id].title,
                        learned,
                        data,
                    )
                    self._set_power_calibration_diag(
                        load_id,
                        state="successful",
                        samples=len(samples),
                        result_w=learned,
                        completed_at=dt_util.now().isoformat(),
                    )
                    break
            else:
                raise HomeAssistantError(
                    "No usable power samples arrived within the four-minute limit"
                )
        except asyncio.CancelledError:
            cancelled = True
            self._set_power_calibration_diag(load_id, state="aborted")
            raise
        except Exception as err:  # actuator/sensor failure must not poison learning
            _LOGGER.warning("Load power calibration for %s failed: %s", load_id, err)
            self._set_power_calibration_diag(
                load_id, state="failed", error=str(err), samples=len(samples)
            )
        finally:
            # Release ownership to a FRESH plan. While the marker is present the
            # executor skipped this load, so no normal plan could terminate the
            # grid-powered probe halfway through its measurement.
            self._load_power_calibration_id = None
            self._load_power_calibration_restored = (
                load_id if activation_attempted else None
            )
            if activation_attempted:
                self._load_power_calibration_release.add(load_id)
            await self.async_flush_persistent_state()
            if activation_attempted:
                if not self._actuation_shutdown:
                    with contextlib.suppress(Exception):
                        await self.async_request_refresh()
                    switch_task = self._load_switch_task
                    if switch_task is not None and not switch_task.done():
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await switch_task
                # A failed refresh never reached the release branch. Do not
                # leave an explicitly grid-powered probe running indefinitely.
                if load_id in self._load_power_calibration_release:
                    await self._execute_load_switching(
                        [
                            (
                                load_id,
                                data,
                                False,
                                self._entity_is_on(data[CONF_LOAD_CONTROL_SWITCH]),
                                0.0,
                                False,
                                "power calibration release fallback",
                                True,
                            )
                        ]
                    )
            else:
                self._load_power_calibration_release.discard(load_id)
                await self.async_flush_persistent_state()
            if self._load_power_calibration_task is asyncio.current_task():
                self._load_power_calibration_task = None
            if cancelled:
                _LOGGER.info("Load power calibration for %s aborted", load_id)
            self.async_update_listeners()

    def _complete_power_calibration_release(self, load_id: str) -> None:
        self._load_power_calibration_release.discard(load_id)
        if self._load_power_calibration_restored == load_id:
            self._load_power_calibration_restored = None
        self._save_persistent_state()

    async def async_recover_power_calibration(self) -> None:
        """Stop a probe that was persisted active across an integration reload."""
        load_id = self._load_power_calibration_restored
        if load_id is None or not self.power_calibration_supported(load_id):
            return
        data = dict(self.entry.subentries[load_id].data)
        await self._execute_load_switching(
            [
                (
                    load_id,
                    data,
                    False,
                    self._entity_is_on(data[CONF_LOAD_CONTROL_SWITCH]),
                    0.0,
                    False,
                    "interrupted power calibration recovery",
                    True,
                )
            ]
        )
        if self._charging_is_active(data) is False:
            self._complete_power_calibration_release(load_id)
            await self.async_flush_persistent_state()
        else:
            _LOGGER.error(
                "Interrupted power calibration for %s could not be stopped", load_id
            )

    def _load_is_running(
        self, load_id: str, data: dict[str, Any], now: datetime
    ) -> bool:
        """True while the load REALLY draws power (runtime counter, v0.7.18).

        Uses the configured power-feedback sensor when present (so any real run
        counts, including manual ones). Without usable power feedback it falls
        back to BM's own "load is on" signal: the real charging state for a
        switched load, else — for a recommendation-only load (no charging state) —
        the DEADLINE-CAPPED published `active` (FIX-12), mirroring
        `_effective_load_active`. The raw plan-active flag would over-credit
        runtime across a night pre-drain block glued to the following day, where
        the published `active` is capped off between the frozen deadline and its
        re-anchor."""
        power_entity = data.get(CONF_LOAD_POWER_ENTITY)
        if power_entity:
            st = self.hass.states.get(power_entity)
            if st is not None and st.state not in ("unknown", "unavailable"):
                try:
                    return float(st.state) > LOAD_RUNTIME_MIN_W
                except ValueError, TypeError:
                    pass
        if load_id in self._load_charging_active:
            return bool(self._load_charging_active[load_id])
        # G4: the plan-based fallback (recommendation-only loads without a
        # power reading) must not accrue runtime while the floor guard holds
        # everything off — the published `active` the operator follows is
        # False, so the device is not running at BM's request.
        if self._floor_guard_active:
            return False
        if not self._load_plan_active.get(load_id, False):
            return False
        deadline = self._load_run_deadline.get(load_id)
        return deadline is None or now < deadline

    def _update_load_runtime(self, now: datetime) -> None:
        """Accumulate real active runtime per load with a capped tick.

        `_load_run_since` holds the timestamp of the last tick while running (a
        live cursor, not the run start). Each cycle — and on the power-sensor
        events that trigger a refresh — the elapsed time since that cursor is
        added while the load runs, and the final partial is added on the off
        transition. Any single tick is capped at ``LOAD_RUNTIME_TICK_MAX_S``, so a
        normal 300 s cycle is never clipped while a stalled loop or a clock jump
        within a session can never inflate the counter (a gap longer than the cap
        adds nothing). A restart gap is handled separately by not persisting the
        cursor, so the first post-restart tick only re-arms it."""
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            running = self._load_is_running(subentry_id, subentry.data, now)
            last = self._load_run_since.get(subentry_id)
            if last is not None:
                delta = (now - last).total_seconds()
                if 0.0 < delta <= LOAD_RUNTIME_TICK_MAX_S:
                    self._load_runtime_seconds[subentry_id] = (
                        self._load_runtime_seconds.get(subentry_id, 0.0) + delta
                    )
            if running:
                self._load_run_since[subentry_id] = now
            else:
                self._load_run_since.pop(subentry_id, None)

    def load_runtime_minutes(self, load_id: str) -> float:
        """Accumulated real runtime in minutes.

        Current as of the last update cycle: ``_update_load_runtime`` runs at the
        top of every refresh before entities read this, so the in-progress run is
        already folded in."""
        return self._load_runtime_seconds.get(load_id, 0.0) / 60.0

    async def reset_load_runtime(self, load_id: str) -> None:
        """Reset a load's runtime counter to zero (reset button, v0.7.18). A run
        in progress restarts its cursor from now so only post-reset time counts.

        V6 (F-TANK): the reset button also means "tank emptied" — start a fresh
        tank cycle. Any pending tank-full capture is dropped (a manual reset
        precedes the auto-detected latch release and would otherwise learn a
        premature sample) and the one-per-cycle notification gate is re-armed.

        F10 (F-TANK recovery): the reset is also the explicit operator signal
        that the deadlock cause is fixed, so a latched F-L7 power warning is
        cleared here. F5 plans a latched load at ~0 W and the BM then never
        commands it, so the latch — which clears only while the load runs
        BM-commanded and back in band — can otherwise never release on its own.
        The pending tank-full capture is dropped BEFORE the release below so the
        release path learns no premature sample (a manual reset is not an
        observed full-tank cycle). Self-correcting: if the tank is really still
        full the power collapses again next run and the latch returns after the
        F-L7 dwell."""
        self._load_runtime_seconds[load_id] = 0.0
        if load_id in self._load_run_since:
            self._load_run_since[load_id] = dt_util.utcnow()
        self._load_tank_full_min.pop(load_id, None)
        self._load_tank_notified.discard(load_id)
        if self._load_power_warning.get(load_id, False):
            self._load_deviation_since.pop(load_id, None)
            subentry = self.entry.subentries.get(load_id)
            title = subentry.title if subentry is not None else load_id
            await self._set_power_warning(load_id, title, False)
        self._save_persistent_state()
        self.async_update_listeners()

    def _tank_enabled(self, data: dict[str, Any]) -> bool:
        """True iff the consumable-tank model is opted in for this load (V6).

        Requires a configured full-tank runtime > 0 AND a power-feedback sensor
        (the tank-full / tank-emptied events are detected via the power-warning
        latch, which needs feedback). 0 = off is the safety anchor: exactly the
        pre-feature behaviour, no runtime cap."""
        return float(data.get(CONF_LOAD_TANK_FULL_RUNTIME_MIN, 0) or 0) > 0.0 and bool(
            data.get(CONF_LOAD_POWER_ENTITY)
        )

    def _tank_learned_full_min(self, subentry_id: str, data: dict[str, Any]) -> float:
        """Learned full-tank runtime in minutes (V6): the median of the last
        observed tank-full samples, or the configured starting estimate while no
        sample exists yet."""
        samples = self._load_tank_samples.get(subentry_id)
        if samples:
            return float(median(samples))
        return float(data.get(CONF_LOAD_TANK_FULL_RUNTIME_MIN, 0) or 0)

    def _tank_remaining_min(
        self, subentry_id: str, data: dict[str, Any]
    ) -> float | None:
        """Remaining tank RUN time in minutes (V6), or None when the feature is
        off for this load. Learned full-tank runtime minus the runtime since the
        last emptying, clamped >= 0. Drives ONLY the emptying notification and
        diagnostics — never the planner (operator rule 2026-07-24: no preemptive
        curtailment; a really-full tank surfaces via the F5 power collapse)."""
        if not self._tank_enabled(data):
            return None
        learned = self._tank_learned_full_min(subentry_id, data)
        used = self.load_runtime_minutes(subentry_id)
        return max(0.0, learned - used)

    def _tank_record_sample(self, subentry_id: str, runtime_min: float) -> None:
        """Append a tank-full runtime sample (V6), keeping the last
        TANK_LEARN_SAMPLES. A non-positive sample is ignored (a spurious latch
        right after a reset must not poison the median)."""
        if runtime_min <= 0.0:
            return
        samples = self._load_tank_samples.setdefault(subentry_id, [])
        samples.append(round(runtime_min, 1))
        del samples[:-TANK_LEARN_SAMPLES]

    def tank_diagnostics(self, load_id: str) -> dict[str, Any] | None:
        """Tank model diagnostics for the runtime sensor (V6), or None when the
        feature is off for this load."""
        return self._load_tank_diag.get(load_id)

    def _update_soc_stale(
        self,
        subentry_id: str,
        title: str,
        live_soc: float | None,
        raw_power: float | None,
        sample_bar: float | None,
        charging: bool,
    ) -> bool:
        """Stale-SOC latch for one load (F-EXECUTOR-GUARDS G2, R5-R7).

        The fossibot integration serves cached SOC values with FRESH
        timestamps, so availability/age checks cannot catch a frozen reading
        and the planner keeps re-booking run after run against a frozen
        `remaining` (v0.8.1's executor cap only bounds a single run). While
        the device DEMONSTRABLY charges — `charging` (the real charging state
        for a switched load; the active recommendation for a rec-only load,
        F4) AND the raw feedback above the v0.6.2 standby bar (the single
        threshold, reused) — a SOC that stays EXACTLY unchanged for
        STALE_LOAD_SOC_MIN minutes is latched as stale. The evidence clock
        measures continuous charging against a frozen value, not wall time: it
        RESETS when charging stops or the sample bar is not met (an
        end-of-charge taper never accumulates false evidence), and loads
        without a SOC or power entity never latch (no evidence, R7). Unlatch:
        any DIFFERENT live reading, charging or not. Latch logs WARNING once,
        unlatch INFO once (change-gated). Returns True while latched."""
        frozen_value = self._load_soc_stale.get(subentry_id)
        if frozen_value is not None:
            if live_soc is not None and live_soc != frozen_value:
                del self._load_soc_stale[subentry_id]
                self._load_soc_frozen.pop(subentry_id, None)
                # Persist the release edge (the latch outlives reloads now).
                self._save_persistent_state()
                _LOGGER.info(
                    "Load %s: SOC reports %.1f%% again (was frozen at %.1f%%)"
                    " — stale latch cleared",
                    title,
                    live_soc,
                    frozen_value,
                )
            else:
                return True  # still frozen (or no reading): stay latched
        if (
            live_soc is None
            or raw_power is None
            or sample_bar is None
            or not charging
            or raw_power < sample_bar
        ):
            # No evidence this cycle: the clock resets (R7).
            self._load_soc_frozen.pop(subentry_id, None)
            return False
        evidence = self._load_soc_frozen.get(subentry_id)
        if evidence is None or evidence[0] != live_soc:
            self._load_soc_frozen[subentry_id] = (live_soc, dt_util.utcnow())
            return False
        if dt_util.utcnow() - evidence[1] >= timedelta(minutes=STALE_LOAD_SOC_MIN):
            self._load_soc_stale[subentry_id] = live_soc
            # Persist the latch edge immediately (the delayed save coalesces):
            # a reload must not drop it — audit 2026-08-02.
            self._save_persistent_state()
            _LOGGER.warning(
                "Load %s: SOC frozen at %.1f%% for %d+ minutes while actively"
                " charging — treating the reading as STALE and holding the"
                " load unavailable until the sensor reports a different value",
                title,
                live_soc,
                STALE_LOAD_SOC_MIN,
            )
            return True
        return False

    def _update_telemetry_freeze(
        self,
        subentry_id: str,
        title: str,
        live_soc: float | None,
        raw_power: float | None,
        rec_active: bool,
    ) -> bool:
        """Telemetry-freeze watchdog for energy-limited loads (F4, 2026-07-24
        forensics), independent of the switching path AND the G2 latch.

        The Fossibot B2 (recommendation-only) froze SOC AND input at 87.5 % /
        144 W for 95 h while the planner kept re-booking the same ~50 Wh top-up
        and the recommendation duty-cycled in the 15-min raster — displacing
        ~0.4 kWh of dehumidifier slots, with no warning. When BOTH the SOC and
        the measured power stay EXACTLY unchanged for FREEZE_STALE_HOURS while
        the recommendation was active at least once in the window, the
        telemetry is frozen: latch stale (WARNING once) and hold the load
        unavailable — the SAME consequence as the G2 stale-SOC guard. Recovery:
        any change in SOC or power releases the latch (INFO once). A plain
        last_changed watchdog is deliberately avoided (cached values carry
        fresh timestamps, so a legitimately idle device would false-positive);
        the key is EXACT stasis DESPITE an active recommendation over hours.
        Returns True while latched."""
        ref = self._load_freeze_ref.get(subentry_id)
        if subentry_id in self._load_freeze_stale:
            if (
                live_soc is not None
                and raw_power is not None
                and ref is not None
                and (live_soc != ref[0] or raw_power != ref[1])
            ):
                self._load_freeze_stale.discard(subentry_id)
                self._load_freeze_ref.pop(subentry_id, None)
                # Persist the release edge (the latch outlives reloads now).
                self._save_persistent_state()
                _LOGGER.info(
                    "Load %s: telemetry moving again (SOC %.1f%%, %.0f W) —"
                    " freeze latch cleared",
                    title,
                    live_soc,
                    raw_power,
                )
                return False
            return True  # still frozen (or no reading): stay latched
        if live_soc is None or raw_power is None:
            # No usable telemetry this cycle: reset the freeze window.
            self._load_freeze_ref.pop(subentry_id, None)
            return False
        if ref is None or ref[0] != live_soc or ref[1] != raw_power:
            # First sample, or telemetry moved: (re)start the window here.
            self._load_freeze_ref[subentry_id] = (
                live_soc,
                raw_power,
                dt_util.utcnow(),
                rec_active,
            )
            return False
        # SOC AND power both EXACTLY unchanged since the window start.
        rec_seen = ref[3] or rec_active
        if rec_seen != ref[3]:
            self._load_freeze_ref[subentry_id] = (ref[0], ref[1], ref[2], rec_seen)
        if rec_seen and dt_util.utcnow() - ref[2] >= timedelta(
            hours=FREEZE_STALE_HOURS
        ):
            self._load_freeze_stale.add(subentry_id)
            # Persist the latch edge immediately: a coordinator reload must
            # not drop it — audit 2026-08-02 (110 min duty-cycling, ~225 Wh).
            self._save_persistent_state()
            _LOGGER.warning(
                "Load %s: SOC %.1f%% and power %.0f W frozen for %d+ h while the"
                " recommendation was active — treating the telemetry as STALE"
                " and holding the load unavailable until either value changes",
                title,
                live_soc,
                raw_power,
                FREEZE_STALE_HOURS,
            )
            return True
        return False

    def _warn_estimate_overrun(
        self, subentry_id: str, title: str, learned: float, data
    ) -> None:
        """Sensor-lie observability (F-ROBUST-POWER): warn ONCE per run when
        the robust estimate exceeds 3x the configured nominal power. The
        median cannot detect a plausible frozen sensor value, and levels
        above nominal are legitimate (operator-raised charge rates), so this
        deliberately observes instead of clamping."""
        nominal = float(data[CONF_LOAD_POWER_W])
        if learned > 3.0 * nominal:
            if subentry_id not in self._load_power_overrun_warned:
                self._load_power_overrun_warned.add(subentry_id)
                _LOGGER.warning(
                    "Load %s: robust power estimate %.0f W exceeds 3x the"
                    " configured %.0f W — check the power sensor (frozen/"
                    "cached value?) or the configured nominal power",
                    title,
                    learned,
                    nominal,
                )
        else:
            self._load_power_overrun_warned.discard(subentry_id)

    def _get_load_states(
        self, now: datetime | None = None
    ) -> tuple[SurplusLoadState, ...]:
        if now is None:
            now = dt_util.utcnow()
        states = []
        # A disabled, hard-faulted or hands-off cascade cannot execute a
        # schedule. Remove its members and terminal from the effective plan so
        # the house-SOC trajectory, feed-in and competing load allocations do
        # not reserve energy for actors that are deliberately not owned.
        cascade_blocked = self.cascade_manager.planning_blocked_load_ids()
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            available = True
            if data.get(CONF_LOAD_AVAILABILITY_ENTITY):
                avail_state = self.hass.states.get(data[CONF_LOAD_AVAILABILITY_ENTITY])
                available = avail_state is not None and avail_state.state not in (
                    "unavailable",
                    "unknown",
                    "off",
                )
            if not self._load_bm_enabled.get(subentry_id, True):
                available = False  # per-load "BM control" switch off (v0.7.17)
            if subentry_id in cascade_blocked:
                available = False
            soc = None
            live_soc = None  # validated LIVE reading only (stale guard input)
            if data.get(CONF_LOAD_SOC_ENTITY):
                soc_entity = data[CONF_LOAD_SOC_ENTITY]
                raw_soc = self._read_float(soc_entity)
                if raw_soc is not None and 0.0 <= raw_soc <= 100.0:
                    cached = self._load_soc_cache.get(subentry_id)
                    changed = (
                        cached is None
                        or cached.get("soc") != raw_soc
                        or cached.get("entity_id") != soc_entity
                    )
                    # Always refresh the freshness timestamp on a real reading
                    # (an awake device reporting a stable SOC must not age out);
                    # only persist when the value/entity actually changes.
                    self._load_soc_cache[subentry_id] = {
                        "entity_id": soc_entity,
                        "soc": raw_soc,
                        "ts": dt_util.now().isoformat(),
                    }
                    if changed:
                        self._save_persistent_state()
                    soc = raw_soc
                    live_soc = raw_soc
                else:
                    # Sleeping device (e.g. powerstation with its input off):
                    # keep planning with the last known SOC — but only if it
                    # came from the SAME entity (reconfigured loads must not
                    # reuse another device's SOC) and it is not too old (a
                    # device asleep for LOAD_SOC_CACHE_MAX_AGE_HOURS reverts to
                    # "empty"). If none is usable, soc stays None and the core
                    # assumes an empty storage — self-healing once the device
                    # wakes while charging (docs/LOAD_CONTROL.md §4).
                    cached = self._load_soc_cache.get(subentry_id)
                    if (
                        cached is not None
                        and cached.get("entity_id") == soc_entity
                        and not self._load_soc_cache_stale(cached)
                    ):
                        soc = cached["soc"]
            measured = None
            raw = None
            min_sample_w = None
            if data.get(CONF_LOAD_POWER_ENTITY):
                raw = self._read_float(data[CONF_LOAD_POWER_ENTITY])
                # Readings below a fraction of the nominal power are
                # standby draw, not a charge sample (a 400 W dehumidifier
                # idling at ~20 W cleared the old flat 10 W bar and got
                # planned at 22 W — 2026-07-05 live incident).
                min_sample_w = self._load_standby_bar(data)
                bm_active = self._bm_load_active(subentry_id)
                buf = self._load_power_samples.get(subentry_id)
                calibrating = self._load_power_calibration_id == subentry_id
                if calibrating:
                    # The manual probe owns its own publication-aware buffer
                    # and commits atomically on success. Feeding the normal
                    # learner here could change the persisted value before an
                    # abort, violating the probe's all-or-nothing contract.
                    self._load_power_samples.pop(subentry_id, None)
                elif raw is not None and raw >= min_sample_w and bm_active:
                    # F-ROBUST-POWER: append the accepted sample and serve the
                    # time-weighted windowed median. During warm-up (< 5 min
                    # accepted coverage) the estimate is None — start-up
                    # inrush can never be served or learned (supersedes the
                    # F-PLANNER-HONESTY R2a seed rule); planning falls back
                    # to learned/nominal via planning_power_w.
                    if buf is None:
                        buf = self._load_power_samples[subentry_id] = deque(
                            maxlen=power_learning.MAX_SAMPLES
                        )
                    buf.append((now, raw))
                    est = power_learning.robust_power_estimate(buf, now)
                    measured = est.watts
                    if measured is not None:
                        # Write-through: the persisted learned value always
                        # holds the run's CURRENT robust estimate ("last
                        # stable estimate wins"), so an OFF load later plans
                        # at its real level — sustained changes included,
                        # spikes excluded by construction. Persist only on
                        # >= 1 W movement: sub-watt jitter would otherwise
                        # re-trigger the delayed save every cycle (~dozens
                        # of .storage writes per run-hour, review finding).
                        learned = round(measured, 1)
                        prev_learned = self._load_learned_power_w.get(subentry_id)
                        if prev_learned is None or abs(learned - prev_learned) >= 1.0:
                            self._load_learned_power_w[subentry_id] = learned
                            self._save_persistent_state()
                        self._warn_estimate_overrun(
                            subentry_id, subentry.title, learned, data
                        )
                elif buf is not None:
                    if bm_active:
                        # Mid-run feedback gap (v0.5.1 semantics): keep
                        # serving the buffer's estimate; the last sample
                        # covers at most GAP_CAP_S, so a long outage decays
                        # into warm-up (None) rather than a stale value.
                        measured = power_learning.robust_power_estimate(buf, now).watts
                    else:
                        # Run over (or the device is being used outside the
                        # manager's plan): a taper/standby/foreign window
                        # must not stick as "measured" planning power. The
                        # LEARNED write-through value keeps serving.
                        del self._load_power_samples[subentry_id]
                        self._load_power_overrun_warned.discard(subentry_id)
            # F-EXECUTOR-GUARDS G2 (R6): a stale-latched load is held
            # unavailable — the planner stops re-booking against the frozen
            # `remaining`, and the plan-driven OFF runs through the normal
            # executor path.
            #
            # G2 evidence signal. A switched load uses its real charging state
            # (plug+enable). A recommendation-only load has no charging state,
            # but the recommendation drives the operator's manual plug-in — so
            # while it is active AND the raw feedback clears the standby bar
            # (checked inside _update_soc_stale) the device is demonstrably
            # charging, and the SOC-frozen latch can supervise it too (F4: the
            # Fossibot B2 rec-only telemetry-freeze was previously invisible to
            # G2 because _load_charging_active is never set for a rec-only
            # load). `_load_plan_active` holds the last published recommendation
            # — the very signal the operator acted on — a one-cycle lag the
            # 12-min G2 clock absorbs, exactly like the switched-load path.
            charging = (
                self._load_charging_active.get(subentry_id, False)
                if data.get(CONF_LOAD_CONTROL_SWITCH)
                else self._load_plan_active.get(subentry_id, False)
            )
            if self._update_soc_stale(
                subentry_id, subentry.title, live_soc, raw, min_sample_w, charging
            ):
                available = False
            # F4 telemetry-freeze watchdog: a redundant guard for energy-limited
            # loads independent of the switching path — SOC AND power frozen
            # EXACTLY for FREEZE_STALE_HOURS while the recommendation was active.
            if data.get(CONF_LOAD_ENERGY_LIMITED) and self._update_telemetry_freeze(
                subentry_id,
                subentry.title,
                live_soc,
                raw,
                self._load_plan_active.get(subentry_id, False),
            ):
                available = False
            # F5 (2026-07-24): a LATCHED power warning (full tank — real draw
            # near 0 W despite an active recommendation) feeds the MEASURED
            # Ist-Leistung into planning so the planner stops booking phantom
            # full-power slots (24.07: 5.4 kWh phantom plan vs 2 W reality). The
            # latch is one cycle old here (_update_power_warnings runs at the end
            # of the cycle), exactly like the other coordinator-computed latches.
            # The learned/measured values stay untouched, so the release restores
            # normal planning power the same cycle. `raw` is the current feedback
            # reading (None -> 0 W: a full tank books nothing either way).
            saturated_power_w = None
            if self._load_power_warning.get(subentry_id, False):
                saturated_power_w = max(0.0, raw if raw is not None else 0.0)
            # V6 (F-TANK) is deliberately NOT fed into the planner: the tank
            # prediction only drives the emptying notification and diagnostics
            # (operator rule 2026-07-24: a dehumidifier is NEVER switched off
            # preemptively because the tank MIGHT be full — the device stops
            # itself when it really is, and THAT is detected via the power
            # collapse -> power-warning latch -> saturated_power_w above).
            states.append(
                SurplusLoadState(
                    load_id=subentry_id,
                    available=available,
                    soc_percent=soc,
                    measured_power_w=measured,
                    learned_power_w=self._load_learned_power_w.get(subentry_id),
                    saturated_power_w=saturated_power_w,
                    soc_observed_at=(
                        # Core planning uses naive local slot timestamps.  A
                        # restored cache timestamp is UTC-aware; passing it
                        # through unchanged caused `_usable_soc` to subtract an
                        # aware value from `PlanInputs.now` and abort the first
                        # post-restart plan.  Normalize both live and cached
                        # observations at this HA/core boundary.
                        dt_util.as_local(now).replace(tzinfo=None)
                        if live_soc is not None
                        else (
                            dt_util.as_local(cached_at).replace(tzinfo=None)
                            if (cached_at := dt_util.parse_datetime(cached.get("ts")))
                            is not None
                            else None
                        )
                        if soc is not None and cached is not None
                        else None
                    ),
                    soc_source=(
                        "live"
                        if live_soc is not None
                        else "cache"
                        if soc is not None
                        else "missing"
                    ),
                )
            )
        return tuple(states)

    def _appliance_is_running(self, data: dict[str, Any], latched: bool) -> bool:
        """Detection with hysteresis (F-SUBHOUR H2).

        A numeric detection entity starts a run at `power_threshold_w` and keeps
        it latched until the power drops below `off_threshold_w` (default = the
        on threshold, i.e. no hysteresis unless configured lower). A brief
        sub-threshold dip during a run (e.g. a dishwasher soak between heater
        bursts) therefore does not reset the run clock and re-inject the full
        energy. During an entity dropout the last (latched) state is held —
        but only up to APPLIANCE_DETECTION_MAX_DROPOUT_MIN, which the caller
        (_get_appliance_runs) enforces before delegating here."""
        entity_id = data.get(CONF_APPLIANCE_DETECTION_ENTITY)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return latched  # hold last state during a dropout, do not reset
        try:
            power = float(state.state)
        except ValueError, TypeError:
            return state.state.lower() in APPLIANCE_RUNNING_STATES
        on_th = float(data.get(CONF_APPLIANCE_POWER_THRESHOLD_W, 10.0))
        off_th = min(float(data.get(CONF_APPLIANCE_OFF_THRESHOLD_W, on_th)), on_th)
        return power >= (off_th if latched else on_th)

    def _get_appliance_runs(self, now: datetime) -> tuple[ApplianceRun, ...]:
        runs = []
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_APPLIANCE:
                continue
            data = subentry.data
            entity_id = data.get(CONF_APPLIANCE_DETECTION_ENTITY)
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is None or state.state in ("unknown", "unavailable"):
                # Detection dropout (operator rule, incident 2026-08-08 — see
                # APPLIANCE_DETECTION_MAX_DROPOUT_MIN): hold the latch across
                # short gaps (a soak phase looks identical), but once the
                # dropout lasts longer than the limit assume the device is
                # OFF — a switched-off appliance takes its integration offline
                # for hours and must not stay "running" (and must not hide a
                # fresh run once it comes back).
                since = self._appliance_dropout_since.setdefault(subentry_id, now)
                if now - since >= timedelta(
                    minutes=APPLIANCE_DETECTION_MAX_DROPOUT_MIN
                ):
                    self._appliance_started.pop(subentry_id, None)
                    self._appliance_started_restored.discard(subentry_id)
                    self._appliance_dropout_since.pop(subentry_id, None)
                    continue
                running = subentry_id in self._appliance_started
            else:
                self._appliance_dropout_since.pop(subentry_id, None)
                running = self._appliance_is_running(
                    data, subentry_id in self._appliance_started
                )
            if running:
                started = self._appliance_started.setdefault(subentry_id, now)
                duration = float(data[CONF_APPLIANCE_RUN_DURATION_H])
                elapsed_h = (now - started).total_seconds() / 3600.0
                # H1 restart-boundary re-anchor: a persisted start restored across
                # a restart may belong to a run that finished during downtime
                # while a NEW run is now active. Only at the first post-restart
                # evaluation, if it is already fully elapsed, treat it as a fresh
                # run so the active run is not silently omitted at 0 remaining.
                if subentry_id in self._appliance_started_restored:
                    self._appliance_started_restored.discard(subentry_id)
                    if duration > 0 and elapsed_h >= duration:
                        started = self._appliance_started[subentry_id] = now
                        elapsed_h = 0.0
                remaining_h = max(0.0, duration - elapsed_h)
                if remaining_h > 0 and duration > 0:
                    runs.append(
                        ApplianceRun(
                            appliance_id=subentry_id,
                            remaining_energy_wh=float(
                                data[CONF_APPLIANCE_RUN_ENERGY_WH]
                            )
                            * remaining_h
                            / duration,
                            remaining_hours=remaining_h,
                        )
                    )
            else:
                self._appliance_started.pop(subentry_id, None)
                self._appliance_started_restored.discard(subentry_id)
        return tuple(runs)

    # ------------------------------------------------------------------
    # Learned consumption series (docs/CONSUMPTION_FORECAST.md D-C5)
    # ------------------------------------------------------------------

    def async_setup_learning(self) -> None:
        """Start the nightly learning job (plus an initial catch-up run)."""
        self.learner.async_schedule()

    def _learned_series(
        self, now: datetime, config: SystemConfig, num_days: int
    ) -> tuple[
        tuple[float | None, ...] | None,
        tuple[float | None, ...] | None,
        dict[str, list[float]],
        bool,
        dict[str, Any],
    ]:
        """Build per-slot consumption overrides from the learned profiles.

        Bin lookup uses the tz-aware local slot start via absolute elapsed
        time (D-C5): across a DST change the skipped hour is skipped and
        the repeated hour reuses its bin, while the core keeps its naive
        raster. In vacation mode an invalid absence bin falls back to the
        static base load WITHOUT the variable share (D-C4) — deliberately
        not None, which would re-add variable_w via the core fallback.
        Also returns the P80−P50 band per slot (dynamic buffer, D-C8) and
        whether any quantiles are active.
        """
        vacation = self.learner.vacation_active
        profiles = self.learner.profiles_for_planning() or {}
        diag: dict[str, Any] = {
            "vacation_mode": vacation,
            **self.learner.diagnostics(),
        }
        naive_now = now.replace(tzinfo=None)
        utc_now = dt_util.as_utc(now)
        series: dict[str, list[float | None]] = {"ac": [], "dc": []}
        # P80−P50 per slot in W: the uncertainty band feeding the dynamic
        # SOC buffer (D-C8); 0 where no quantiles exist for the slot.
        band: dict[str, list[float]] = {"ac": [], "dc": []}
        static_profiles = {"ac": config.ac_profile, "dc": config.dc_profile}
        delta_sum = {"ac": 0.0, "dc": 0.0}
        delta_count = {"ac": 0, "dc": 0}
        for start in slot_starts(naive_now, num_days):
            # Absolute-time mapping: the naive slot delta is the intended
            # elapsed time; adding it in UTC yields the true local hour.
            local = dt_util.as_local(utc_now + (start - naive_now))
            dt_key = (
                DAY_TYPE_ABSENCE
                if vacation
                else self.learner.planning_daytype(local.date())
            )
            for path in ("ac", "dc"):
                value = profile_value(profiles.get(path), dt_key, local.hour, "p50")
                p80 = profile_value(profiles.get(path), dt_key, local.hour, "p80")
                band[path].append(
                    max(0.0, p80 - value)
                    if value is not None and p80 is not None
                    else 0.0
                )
                if value is not None:
                    # Diagnostic: learned vs. static for the same hour (D-C6)
                    delta_sum[path] += value - static_profiles[path].power_w(local.hour)
                    delta_count[path] += 1
                if value is None and vacation:
                    value = float(self.raw_config[f"{path}_base_load_w"])
                series[path].append(value)

        overrides: list[tuple[float | None, ...] | None] = []
        for path in ("ac", "dc"):
            values = series[path]
            filled = sum(1 for v in values if v is not None)
            if delta_count[path]:
                source = "learned"
            elif filled:
                source = "vacation_base"
            else:
                source = "static"
            diag[f"{path}_source"] = source
            diag[f"{path}_slot_coverage"] = (
                round(filled / len(values), 2) if values else 0.0
            )
            diag[f"{path}_mean_delta_w"] = (
                round(delta_sum[path] / delta_count[path], 1)
                if delta_count[path]
                else None
            )
            overrides.append(tuple(values) if filled else None)
        quantiles_active = any(delta_count[path] for path in ("ac", "dc"))
        return overrides[0], overrides[1], band, quantiles_active, diag

    def _dynamic_buffer(
        self,
        config: SystemConfig,
        slots,
        band: dict[str, list[float]],
    ) -> tuple[float, dict[str, Any]]:
        """Dynamic SOC buffer from the P80−P50 band (D-C8, active immediately).

        Critical window: now until the first slot with forecast PV surplus
        (none -> whole horizon). AC uncertainty converts through discharge
        AND inverter efficiency, DC only through discharge efficiency.
        Statically filled slots contribute 0 (their band is 0).
        """
        eta_dis = config.battery.eta_discharge
        eta_inv = config.inverter.eta
        uncertainty_wh = 0.0
        window_hours = 0
        for i, slot in enumerate(slots):
            if slot.pv_wh > slot.ac_wh + slot.dc_wh:
                break
            window_hours += 1
            ac_band = band["ac"][i] if i < len(band["ac"]) else 0.0
            dc_band = band["dc"][i] if i < len(band["dc"]) else 0.0
            uncertainty_wh += ac_band * slot.duration / (eta_dis * eta_inv)
            uncertainty_wh += dc_band * slot.duration / eta_dis
        raw = uncertainty_wh / config.battery.capacity_wh * 100.0
        low = float(self.raw_config[CONF_BUFFER_MIN_PERCENT])
        # Defensive: the options flow validates min < max, but an inverted
        # pair from old/hand-edited options must not pin the buffer silently.
        high = max(low, float(self.raw_config[CONF_BUFFER_MAX_PERCENT]))
        buffer_percent = round(min(max(raw, low), high), 1)
        return buffer_percent, {
            "soc_buffer_source": "dynamic",
            "soc_buffer_effective": buffer_percent,
            "buffer_uncertainty_wh": round(uncertainty_wh, 0),
            "buffer_window_hours": window_hours,
        }

    # ------------------------------------------------------------------
    # Update cycle
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        if self._startup_at is None:
            self._startup_at = now  # V8: anchor the SOC startup grace window
        # Runtime counter: accumulate real active minutes every cycle (and on the
        # power-sensor state events that trigger a refresh), before any early-out
        # so it tracks continuously (v0.7.18).
        self._update_load_runtime(now)
        # F-FEEDIN: release a setpoint entity the operator unwired in the
        # options flow. Runs BEFORE any early-out — the whole point is that it
        # must not depend on a successful cycle.
        await self._feedin_release_orphan()
        # Manual-override detection first: build_system_config feeds the
        # forced flags into the simulation (F-N2).
        self._update_support_modes()
        # The house-SOC stale watchdog runs BEFORE _get_soc so its latch is
        # current for this cycle's validity decision (it reads the raw SOC
        # entity itself and unlatches on the first changed reading).
        self._update_house_soc_watchdog(now)
        soc = self._get_soc(now)
        forecasts = self._get_forecasts(now)

        if soc is None or forecasts is None:
            missing = "SOC" if soc is None else "PV forecasts"
            # V8 startup grace: within STARTUP_SOC_GRACE_S of the first refresh
            # and before any successful cycle, a not-yet-available SOC source
            # (Victron sensor still booting after an HA restart) waits quietly
            # instead of escalating to an ERROR + UpdateFailed. Retry runs at
            # the short startup interval; a prior valid state (a re-setup that
            # already had a good cycle) is held, else a not-ready placeholder
            # keeps the entities unavailable WITHOUT a logged error. Only SOC is
            # graced — the Victron dropout is the observed startup case; a
            # missing forecast keeps the normal path. After the grace window,
            # or after the first success (steady state), the old path runs.
            if (
                soc is None
                and not self._first_success_done
                and now - self._startup_at < timedelta(seconds=STARTUP_SOC_GRACE_S)
            ):
                _LOGGER.debug(
                    "SOC source %s not ready yet; waiting out the startup grace"
                    " (%.0f s) before failing",
                    self.raw_config[CONF_SOC_ENTITY],
                    STARTUP_SOC_GRACE_S,
                )
                if self.data is not None and self.data.get("valid"):
                    return self.data
                return {"valid": False, "startup_pending": True, "last_update": now}
            self._successful_updates = 0
            # D-A8 stage 2: stamp the data-loss clock and fire the fail-safe
            # load shed once the outage persists. Must run BEFORE the raise —
            # the UpdateFailed path never reaches the switching code, so the
            # shed trigger cannot live in any plan/switching path.
            await self._note_data_loss(now)
            # F-FEEDIN R12: kill the export too. `_apply_feedin` — which holds
            # the G4/stale fail-safes and the SOC floor — is never reached on
            # this path, so without this the setpoint kept exporting for the
            # whole outage and drained the battery once PV fell below it
            # (review 2026-08-03). Idempotent, so the repeated failing cycles
            # write exactly once.
            await self._feedin_force_zero(f"no valid input data ({missing})")
            raise UpdateFailed(f"No valid input data available ({missing})")

        # Data is valid again: end any data-loss episode (release the shed
        # latch, resolve the repair issue, push the recovery) BEFORE the
        # normal switching paths run, so the recovered plan may re-enable
        # loads in this very cycle.
        await self._note_data_recovered()

        # The feed-in manual verdict must precede the config build: in manual
        # mode the planner books the operator-owned setpoint for the rest of
        # today (R9, operator decision 2026-08-08), so the plan would be one
        # cycle stale if the judgement ran after it.
        self._update_feedin_mode(now)

        config = self.build_system_config()
        load_states = self._get_load_states(now)
        appliance_runs = self._get_appliance_runs(now)
        ac_series, dc_series, band, quantiles_active, profile_diag = (
            self._learned_series(now, config, len(forecasts))
        )

        # Hourly PV forecast (docs/F-PREDRAIN.md F1): None/empty -> two-window.
        # The p10/p90 quantile maps (F-QUANTILE-BANDS R1) ride along; without
        # them no slot carries a band and the planner stays on the scalar dials.
        pv_hourly, pv_hourly_p10, pv_hourly_p90 = self._get_pv_hourly(now)
        self._pv_source_by_day = self._pv_day_sources(now, len(forecasts), pv_hourly)

        inputs = build_slots(
            config,
            now.replace(tzinfo=None),
            soc,
            forecasts,
            appliance_runs=appliance_runs,
            load_states=load_states,
            ac_load_w=ac_series,
            dc_load_w=dc_series,
            pv_hourly=pv_hourly,
            pv_hourly_p10=pv_hourly_p10,
            pv_hourly_p90=pv_hourly_p90,
        )
        inputs = replace(
            inputs,
            cascade_runtime_states=tuple(
                self._cascade_runtime_state(cascade.cascade_id)
                for cascade in config.cascades
            ),
        )
        # Dynamic SOC buffer (D-C8): replaces the fixed planning buffer as
        # soon as any learned quantiles exist. Only soc_buffer_percent is
        # overridden here; the grid-support escalation reads its own absolute
        # SOC thresholds, so a widened planning buffer never moves the PSUs.
        if quantiles_active:
            buffer_percent, buffer_diag = self._dynamic_buffer(
                config, inputs.slots, band
            )
            config = replace(
                config,
                control=replace(config.control, soc_buffer_percent=buffer_percent),
            )
        else:
            buffer_diag = {
                "soc_buffer_source": "fixed",
                "soc_buffer_effective": config.control.soc_buffer_percent,
            }
        profile_diag.update(buffer_diag)
        result = await self.hass.async_add_executor_job(plan, config, inputs)
        self._update_plan_active(result)
        self._update_predrain_block_evidence(result, inputs, now)
        self._log_night_predrain(result, inputs, config)
        # Flow proof for the house-SOC stale watchdog: the slot-0 battery
        # flow (charge + discharge power) this FRESH plan expects right now.
        # Kept across later failing cycles (the plan no longer runs then), so
        # the watchdog can still prove a freeze against the last expectation.
        flows = result.trajectory.flows
        slot0_slot = inputs.slots[0] if inputs.slots else None
        if flows and slot0_slot is not None and slot0_slot.duration > 0.0:
            self._expected_battery_power_w = (
                flows[0].battery_charge_wh + flows[0].battery_discharge_wh
            ) / slot0_slot.duration
        else:
            self._expected_battery_power_w = None

        threshold = self._apply_threshold_inertia(result.threshold_percent, config)
        recommendation = self._apply_hysteresis(soc, threshold, config, now)
        # G4 floor guard: needs the just-updated recommendation; must run
        # BEFORE load switching so this cycle already enforces it. The rec-off
        # branch is gated on PV coverage (planner-G4 parity): the current slot's
        # forecast PV power (the only PV the coordinator ingests) vs the running
        # surplus loads' learned power (nominal fallback), matching
        # `_slot_serviceable`'s `pv_wh` vs load-power test.
        slot0 = inputs.slots[0] if inputs.slots else None
        pv_power_w = (
            slot0.pv_wh / slot0.duration
            if slot0 is not None and slot0.duration > 0.0
            else 0.0
        )
        running_load_power_w = sum(
            self._load_learned_power_w.get(lp.load_id) or load.nominal_power_w
            for lp, load in zip(result.load_plans, config.loads, strict=True)
            if lp.active_now
        )
        floor_guard = self._update_floor_guard(
            soc, config, pv_power_w, running_load_power_w, now
        )
        await self._apply_support_switching(result, config, now)
        self._run_dc48_controller(now)
        # F11: only when a latched switchable load exists, replan with its F5
        # saturated override cleared (shadow plan, never published) so the F10
        # hold follows the normal planner's rules instead of a stricter PV gate.
        shadow_active = await self._latch_shadow_active(config, inputs, load_states)
        await self._apply_load_switching(
            result,
            now,
            tuple(s.duration for s in inputs.slots),
            pv_power_w,
            shadow_active,
        )
        cascade_safety_reason = (
            "G4 floor guard"
            if floor_guard
            else "stale-data load shed"
            if self._stale_shed_active
            else None
        )
        await self.cascade_manager.async_apply(
            config,
            result,
            load_states,
            now,
            safety_reason=cascade_safety_reason,
        )
        await self._update_power_warnings(result, now)
        # F-FEEDIN: the manual-override judgement already ran at the top of
        # the cycle (before build_system_config, so the plan books the
        # operator-owned setpoint in manual mode); the mode gates every write
        # of the two-phase setpoint executor incl. the event-driven trim.
        await self._apply_feedin(result, config, soc, now)
        # V6 (F-TANK): after the power warnings settle (their latch edges drive
        # the tank learning/auto-reset), refresh the tank diagnostics and fire
        # the one-per-cycle "tank nearly full" push.
        await self._update_tank_forecast(result, now)
        self._update_gate_calibration(config, soc)

        self._successful_updates += 1
        self._first_success_done = True  # V8: latch — grace never re-arms
        if not self._startup_complete and (
            self._successful_updates >= 2
            or self._successful_updates >= STARTUP_RETRY_ATTEMPTS
        ):
            self._startup_complete = True
            self.update_interval = timedelta(seconds=UPDATE_INTERVAL_SECONDS)

        load_plans: dict[str, dict[str, Any]] = {}
        load_states_by_id = {state.load_id: state for state in load_states}
        # Anchor for the per-load today/tomorrow split: the plan's slot-0 day
        # (same convention as the aggregate _daily_surplus_breakdown / the
        # sensor's _per_day_attrs) — NOT each load's own first booking day, so a
        # load that only runs tomorrow correctly reads today=0.0.
        day_anchor = inputs.slots[0].start.date() if inputs.slots else None
        for load_plan, load in zip(result.load_plans, config.loads, strict=True):
            load_state = load_states_by_id[load_plan.load_id]
            planning_power_w = load_state.planning_power_w(load)
            if load_state.saturated_power_w is not None:
                planning_power_source = "saturated"
            elif load_state.measured_power_w is not None and (
                load_state.measured_power_w > 0
            ):
                planning_power_source = "live"
            elif load_state.learned_power_w is not None and (
                load_state.learned_power_w > 0
            ):
                planning_power_source = "learned"
            else:
                planning_power_source = "configured"
            pass_by_slot: dict[int, int] = {}
            for start, count, pass_no, _wh in load_plan.allocations:
                for j in range(start, start + count):
                    pass_by_slot[j] = pass_no
            # Explain-plan (F-PLANNER-HONESTY R14): the acceptance reason per
            # covered slot. zip is deliberately non-strict — a legacy plan
            # without reasons renders its schedule without the `why` key.
            why_by_slot: dict[int, str] = {}
            for (start, count, _p, _wh2), why in zip(
                load_plan.allocations, load_plan.reasons, strict=False
            ):
                for j in range(start, start + count):
                    why_by_slot[j] = why
            # Booked energy per SLOT (operator ask 2026-08-03: the card's hover
            # readout shows the energy of the hovered hour per lane). The
            # allocation carries the energy of the whole booking, so it is
            # split across its slots by the run hours actually committed there
            # — F-SUBHOUR: a slot may run only a fraction of its duration, so
            # an equal split would misreport the last hour of a run.
            wh_by_slot: dict[int, float] = {}
            for start, count, _p3, booked_wh in load_plan.allocations:
                covered = range(start, start + count)
                hours = [
                    (
                        load_plan.run_hours[j]
                        if load_plan.run_hours and j < len(load_plan.run_hours)
                        else 1.0
                    )
                    for j in covered
                ]
                total_h = sum(hours)
                for j, h in zip(covered, hours, strict=True):
                    wh_by_slot[j] = (
                        booked_wh * h / total_h if total_h > 0 else booked_wh / count
                    )
            if load_plan.allocations:
                _LOGGER.debug(
                    "Load %s planned: %s",
                    load.name,
                    "; ".join(
                        f"pass {p} from slot {s} ({c} slot(s), {wh:.0f} Wh)"
                        for s, c, p, wh in load_plan.allocations
                    ),
                )
            diag = self._load_warning_diag.get(load_plan.load_id, {})
            since = diag.get("since")
            load_plans[load_plan.load_id] = {
                "name": load.name,
                # Effective active: whole-slot active_now capped by the frozen
                # sub-hour deadline (F-SUBHOUR R8/R12), so the binary sensor an
                # operator's automation follows flips off at the run's end.
                "active": self._effective_load_active(load_plan, now),
                # F-PREDRAIN-BLOCK: the load's pass-3 block window + the
                # stability-gate progress (None when the plan books none).
                "predrain_block": self._predrain_block_attr(load_plan, inputs),
                "planned_hours": sum(load_plan.schedule),
                "planned_energy_kwh": round(load_plan.planned_energy_wh / 1000.0, 3),
                # Per-load today/tomorrow planned energy (analogous to the
                # aggregate loads_today/tomorrow_kwh) so the card can show a
                # per-load heute/morgen split; `daily` sums to
                # planned_energy_kwh (rounding aside).
                **self._load_per_day_kwh(load_plan, inputs, day_anchor),
                "charging_active": self._load_charging_active.get(load_plan.load_id),
                "power_warning": self._load_power_warning.get(load_plan.load_id, False),
                "expected_power_w": diag.get("expected_w"),
                "measured_power_w": diag.get("measured_w"),
                # F-PLANNER-HONESTY R5: the run-max learned planning power an
                # OFF load is evaluated with (None until the first learned run).
                "learned_power_w": self._load_learned_power_w.get(load_plan.load_id),
                # The exact scalar consumed by this planning run, not merely
                # the persisted learned fallback. During a run the robust live
                # estimate wins; F5 may temporarily supply saturated (~0 W).
                "planning_power_w": round(planning_power_w, 1),
                "planning_power_source": planning_power_source,
                # F-EXECUTOR-GUARDS R8: True while a stale guard holds the load
                # unavailable — the G2 stale-SOC latch (frozen reading during
                # active charging) OR the F4 telemetry-freeze watchdog (SOC and
                # power frozen for hours despite an active recommendation).
                "soc_stale": (
                    load_plan.load_id in self._load_soc_stale
                    or load_plan.load_id in self._load_freeze_stale
                ),
                "deviating_since": since.isoformat() if since else None,
                "schedule": [
                    {
                        "start": slot.start.isoformat(),
                        "end": (
                            slot.start + timedelta(hours=slot.duration)
                        ).isoformat(),
                        # 1 = direct surplus, 2 = preemptive ("zielbasiert")
                        "pass": pass_by_slot.get(slot.index),
                        # Booked energy of THIS slot (Wh) — the card's hover
                        # readout renders it per lane.
                        "wh": round(wh_by_slot.get(slot.index, 0.0), 1),
                        # Acceptance reason (R14); absent for legacy plans.
                        **(
                            {"why": why_by_slot[slot.index]}
                            if slot.index in why_by_slot
                            else {}
                        ),
                    }
                    for slot, on in zip(inputs.slots, load_plan.schedule, strict=True)
                    if on
                ],
                "managed_by_cascade": (
                    next(
                        (
                            cascade.cascade_id
                            for cascade in config.cascades
                            if load_plan.load_id
                            in (
                                *(member.load_id for member in cascade.members),
                                cascade.terminal_load_id,
                            )
                        ),
                        None,
                    )
                    if load_plan.managed_by_cascade
                    else None
                ),
            }

        naive_now = now.replace(tzinfo=None)
        soc_forecast = [{"t": naive_now.isoformat(), "soc": round(soc, 1)}]
        # F-FEEDIN: the per-slot planned feed-in power rides on each forecast
        # point (only when the schedule is populated — the card renders its
        # lane purely on attribute presence, keeping old backends compatible).
        feedin_schedule = result.feedin_schedule_w
        for i, (slot, flow) in enumerate(
            zip(inputs.slots, result.trajectory.flows, strict=True)
        ):
            point = {
                "t": (slot.start + timedelta(hours=slot.duration)).isoformat(),
                "soc": round(flow.soc_end_percent, 1),
            }
            # Grid-support flags for the slot ending at this point, so the card
            # can render a 24 V / 48 V support lane. Only emitted when active,
            # to keep the forecast attribute compact.
            if flow.support_dc24:
                point["dc24"] = True
            if flow.support_dc48:
                point["dc48"] = True
            if feedin_schedule:
                point["feedin"] = round(feedin_schedule[i], 1)
            soc_forecast.append(point)

        hourly_details = [
            {
                "hour": slot.index,
                "datetime": slot.start.isoformat(),
                "duration_minutes": int(slot.duration * 60),
                "initial_soc_percent": flow.soc_start_percent,
                "final_soc_percent": flow.soc_end_percent,
                "pv_production_wh": slot.pv_wh,
                "ac_consumption_wh": slot.ac_wh + flow.extra_ac_wh,
                "dc_consumption_wh": slot.dc_wh,
                "surplus_load_wh": flow.extra_ac_wh,
                "grid_import_wh": flow.grid_import_wh,
                "grid_export_wh": flow.grid_export_wh,
                "battery_charge_wh": flow.battery_charge_wh,
                "battery_discharge_wh": flow.battery_discharge_wh,
                "inverter_enabled": flow.inverter_on,
                "support_dc24": flow.support_dc24,
                "support_dc48": flow.support_dc48,
                # F-N3 two-bus diagnostics (docs/DC_TOPOLOGY.md).
                "psu48_delivered_wh": flow.psu48_delivered_wh,
                "psu24_delivered_wh": flow.psu24_delivered_wh,
                "dcdc_input_wh": flow.dcdc_input_wh,
                "dcdc_loss_wh": flow.dcdc_loss_wh,
                "unserved_dc_wh": flow.unserved_dc_wh,
                "gate_open": flow.gate_open,
                "profile_sources": (
                    f"{_series_source(ac_series, slot.index)}"
                    f"/{_series_source(dc_series, slot.index)}"
                ),
            }
            for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True)
        ]

        # Consumption forecast for the bundled consumption card (operator
        # request 2026-08-08): per slot the planned consumption in W, split
        # by voltage level. ac_w = base profile + appliance runs (slot.ac_wh);
        # the planned surplus loads ride as their OWN layer (extra_ac_wh).
        # The DC total splits into 24 V rail / native 48 V exactly like the
        # kernel (fixed native48 base first, then dc24_share of the
        # remainder) — an approximation, there is no separate 24 V
        # measurement. src carries the L(earned)/S(tatic) origin per path so
        # the card can tint fallback slots.
        consumption_forecast = []
        for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):
            dc_w = slot.dc_wh / slot.duration
            native48_base_w = min(dc_w, config.support.native48_base_w)
            rail_w = (dc_w - native48_base_w) * config.support.dc24_share
            consumption_forecast.append(
                {
                    "t": slot.start.isoformat(),
                    "ac_w": round(slot.ac_wh / slot.duration, 1),
                    "loads_w": round(flow.extra_ac_wh / slot.duration, 1),
                    "dc24_w": round(rail_w, 1),
                    "dc48_w": round(dc_w - rail_w, 1),
                    "src": f"{_series_source(ac_series, slot.index)}"
                    f"/{_series_source(dc_series, slot.index)}",
                }
            )

        daily_surplus = self._daily_surplus_breakdown(inputs, result)
        # F-REALIZED-SURPLUS: advance the measured day counters (export meter,
        # per-load energy counters, feed-in integral) against the fresh
        # per-day forecast, so the `realized` block below can mix Ist +
        # Restprognose. Runs every successful cycle; None without an export
        # meter (the key then stays absent — backend-compat).
        realized = self._update_realized_surplus(now, daily_surplus)

        return {
            "valid": True,
            "last_update": now,
            "input_soc_percent": soc,
            "input_forecasts_kwh": forecasts,
            "soc_threshold_percent": threshold,
            "inverter_recommendation": recommendation,
            # G4 floor guard: surfaced for diagnostics and operator automations.
            "floor_guard_active": floor_guard,
            "min_soc_forecast_percent": result.min_soc_percent,
            "max_soc_forecast_percent": result.max_soc_percent,
            "hours_to_max_soc": result.hours_to_max_soc,
            "grid_import_kwh": round(result.grid_import_kwh, 3),
            "grid_export_kwh": round(result.grid_export_kwh, 3),
            "lost_surplus_kwh": round(result.lost_surplus_kwh, 3),
            # F-PERDAY-SURPLUS R1: per-calendar-day lost-surplus / import split
            # (single source for the today/tomorrow sensor attributes and the
            # dashboard cards); the sums equal the totals above (rounding aside).
            "daily_surplus": daily_surplus,
            # F-REALIZED-SURPLUS: measured day counters + Ist/forecast mixes
            # (only with an export meter configured — see above).
            **({"realized": realized} if realized is not None else {}),
            # F-PREDRAIN diagnostics (docs/F-PREDRAIN.md §3.5) for the SOC-forecast
            # sensor (WP4): per-day PV source, the traded import, the stressed
            # reserve and the derived per-day PV-window end hours.
            "pv_source": dict(self._pv_source_by_day),
            # F-QUANTILE-BANDS R7: per-day band coverage of daylight slots, so
            # the maturing P10/P90 evidence is observable from the sensor.
            "quantile_coverage": self._quantile_coverage(inputs),
            "import_trade_used_wh": round(result.import_trade_used_wh, 1),
            "stressed_min_soc": (
                round(result.stressed_min_soc_percent, 2)
                if result.stressed_min_soc_percent is not None
                else None
            ),
            # F-NIGHT-RESCUE R7: end of the merge-bounded T* horizon (null =
            # full-horizon scan) — makes 04:13-class threshold jumps explicable.
            "threshold_horizon_end": (
                result.threshold_horizon_end.isoformat()
                if result.threshold_horizon_end is not None
                else None
            ),
            # Full-episode stressed export behind the T* terminal-credit ramp.
            # Unlike the horizon end this remains informative below 250 Wh.
            "threshold_merge_margin_wh": round(result.threshold_merge_margin_wh, 1),
            "pv_window_ends": dict(result.pv_window_ends),
            "load_plans": load_plans,
            "cascade_plans": self.cascade_manager.payload(result, config, inputs.slots),
            # Detected appliance runs (washer, dishwasher, …) for the forecast
            # card: they already shape the AC consumption forecast but were
            # invisible on the card (operator request 2026-08-08). One block
            # per run: now -> now + remaining_hours, carrying the remaining
            # energy like a load slot's `wh`.
            "appliance_plans": [
                {
                    "name": self.entry.subentries[run.appliance_id].title,
                    "active": True,
                    "schedule": [
                        {
                            "start": naive_now.isoformat(),
                            "end": (
                                naive_now + timedelta(hours=run.remaining_hours)
                            ).isoformat(),
                            "wh": round(run.remaining_energy_wh, 1),
                        }
                    ],
                }
                for run in appliance_runs
                if run.appliance_id in self.entry.subentries
            ],
            "soc_forecast": soc_forecast,
            # Static planning context for the bundled forecast card
            "plan_params": {
                "battery_min_soc_percent": config.battery.soc_min_percent,
                "battery_max_soc_percent": config.battery.soc_max_percent,
                "inverter_min_soc_percent": config.control.inverter_min_soc_percent,
                "soc_buffer_percent": config.control.soc_buffer_percent,
            },
            # G4: while the floor guard is active an appliance start would run
            # grid-fed too — the advisory must not invite one.
            "appliance_windows": {
                k: (bool(v) and not floor_guard)
                for k, v in result.appliance_windows.items()
            },
            "support_dc24": self._support_state["dc24"],
            "support_dc48": self._support_state["dc48"],
            "support_dc24_mode": "manual" if self._support_manual["dc24"] else "auto",
            "support_dc48_mode": "manual" if self._support_manual["dc48"] else "auto",
            # F-FEEDIN R7: executor mode for the feed-in mode sensor (mirrors
            # the support mode keys above).
            "feedin_mode": (
                SUPPORT_MODE_MANUAL if self.feedin_manual() else SUPPORT_MODE_AUTO
            ),
            "support_dc48_controller": dict(self._dc48_ctrl_diag),
            "consumption_profile": profile_diag,
            "gate_calibration": self._gate_calibration_diag(config),
            "hourly_details": hourly_details,
            "consumption_forecast": consumption_forecast,
        }

    def _daily_surplus_breakdown(self, inputs, result) -> list[dict[str, Any]]:
        """Per-calendar-day lost-surplus / grid-import / load-energy split
        (F-PERDAY-SURPLUS R1 + §5 v2).

        Grouped by ``slot.start.date()`` in planner-local time: a slot belongs to
        the day it STARTS in (hourly grid, D-A7), so a 23:00 slot counts on its
        start day even where it conceptually crosses midnight. lost_surplus mirrors
        grid export (core: lost_surplus_kwh == grid_export_kwh), so the sums over
        the returned entries equal the existing totals (rounding aside).
        ``loads_kwh`` (v2, R-V2-1) sums the final trajectory's per-slot
        ``extra_ac_wh`` — the SURPLUS-LOAD energy scheduled that day; appliances
        are excluded by construction (they enter the AC forecast, never
        ``extra_ac_wh``).

        ``prevented_export_kwh`` (F-STRICT-SURPLUS R4) is the counterfactual:
        the export the load runs prevented that day, taken straight from
        ``result.prevented_export_by_day_wh`` (base minus alloc, both PRE
        support-escalation so support PSUs never deflate it). It answers "why
        is a load running although SOC never reaches max?" on the dashboard.
        """
        export_by_day: dict[str, float] = {}
        import_by_day: dict[str, float] = {}
        loads_by_day: dict[str, float] = {}
        # Grid-support energy per day, so the card's legend can show the
        # support lanes with the same heute/morgen figure as every other lane
        # (operator ask 2026-08-03). Delivered Wh, i.e. what the PSUs actually
        # put on their rail — not their nominal rating.
        dc24_by_day: dict[str, float] = {}
        dc48_by_day: dict[str, float] = {}
        order: list[str] = []
        for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):
            day = slot.start.date().isoformat()
            if day not in export_by_day:
                export_by_day[day] = 0.0
                import_by_day[day] = 0.0
                loads_by_day[day] = 0.0
                dc24_by_day[day] = 0.0
                dc48_by_day[day] = 0.0
                order.append(day)
            export_by_day[day] += flow.grid_export_wh
            import_by_day[day] += flow.grid_import_wh
            loads_by_day[day] += flow.extra_ac_wh
            # getattr: the coordinator tests build flows as SimpleNamespace
            # stubs, and a stub without the support fields must not break the
            # breakdown (same defensive read as `feedin_by_day_wh` below).
            dc24_by_day[day] += getattr(flow, "psu24_delivered_wh", 0.0) or 0.0
            dc48_by_day[day] += getattr(flow, "psu48_delivered_wh", 0.0) or 0.0
        prevented = result.prevented_export_by_day_wh
        # F-FEEDIN: planned feed-in per day (ISO date -> Wh), straight from the
        # PlanResult. Emitted ONLY when anything was booked — the card renders
        # its stats line purely on attribute presence (backend-compat, same
        # pattern as prevented_export_kwh != null on the frontend).
        feedin = getattr(result, "feedin_by_day_wh", None) or {}
        entries = []
        for day in order:
            entry = {
                "date": day,
                "lost_surplus_kwh": round(export_by_day[day] / 1000.0, 3),
                "grid_import_kwh": round(import_by_day[day] / 1000.0, 3),
                "loads_kwh": round(loads_by_day[day] / 1000.0, 3),
                "prevented_export_kwh": round(prevented.get(day, 0.0) / 1000.0, 3),
                "support_dc24_kwh": round(dc24_by_day[day] / 1000.0, 3),
                "support_dc48_kwh": round(dc48_by_day[day] / 1000.0, 3),
            }
            if feedin:
                entry["planned_feedin_kwh"] = round(feedin.get(day, 0.0) / 1000.0, 3)
            entries.append(entry)
        return entries

    # ------------------------------------------------------------------
    # F-REALIZED-SURPLUS realized surplus accounting
    # (docs/F-REALIZED-SURPLUS.md)
    # ------------------------------------------------------------------

    def _energy_unit_factor(self, entity_id: str) -> float:
        """Wh per unit of an energy counter, from its own
        `unit_of_measurement`. An unrecognised unit falls back to kWh and warns
        ONCE per entity — silently guessing 1000x either way corrupts the
        counters, so the operator has to be told (review 2026-08-03)."""
        state = self.hass.states.get(entity_id)
        unit = state.attributes.get("unit_of_measurement") if state else None
        factor = REALIZED_ENERGY_UNIT_FACTORS_WH.get(unit)
        if factor is not None:
            return factor
        if entity_id not in self._realized_unit_warned:
            self._realized_unit_warned.add(entity_id)
            _LOGGER.warning(
                "Realized surplus: energy counter %s reports an unknown unit"
                " %r — assuming kWh. Pick a counter with a Wh/kWh/MWh unit if"
                " the realized figures look wrong by a factor of 1000",
                entity_id,
                unit,
            )
        return REALIZED_ENERGY_UNIT_FACTORS_WH["kWh"]

    def _counter_delta(
        self, entity_id: str, power_w: float | None, now: datetime
    ) -> float | None:
        """Advance the persisted last reading of a kWh energy counter and
        return the plausible Wh delta since the previous reading, else None.

        Jump filter (operator decision 7): negative deltas (counter reset /
        telemetry glitch) and deltas above the plausibility cap are dropped —
        but the reading STILL advances either way, otherwise a single bad
        jump would poison every later delta (the inflated baseline would keep
        producing huge deltas forever). The cap is REALIZED_JUMP_MAX_FACTOR x
        planning power x elapsed hours, where the elapsed time is measured
        since the reading's VALUE last changed — NOT since our last read: a
        coarse counter (the Fritz!Powerline publishes in ~0.1 kWh chunks) sits
        unchanged for many cycles and then delivers the whole accrual window in
        one delta, so a per-read elapsed would false-positive on every
        legitimate chunk. EVERY caller passes a power — including the export
        meter, which used to fall back to the flat absolute cap and therefore
        discarded a whole legitimate accrual after any cycle gap (planner
        failure, HA restart); since the baseline advances anyway, that energy
        was lost forever (review 2026-08-03). The absolute REALIZED_JUMP_MAX_WH
        now only covers a reading restored from the store, which carries no
        timestamp and thus no elapsed time.

        The reading is scaled by the counter's OWN unit (`_energy_unit_factor`)
        instead of assuming kWh: the options-flow selector filters by device
        class only, so a Wh-unit counter is a valid pick and would otherwise be
        read 1000x too large — every delta swallowed by the filter, all realized
        sensors stuck at 0 with nothing but a DEBUG line (review 2026-08-03).

        While the sensor is unavailable/unknown nothing is booked and the
        next valid state RE-LEARNS the baseline instead of dumping the outage
        gap as one delta.
        """
        readings: dict[str, float] = self._realized["last_readings"]
        value = self._read_float(entity_id)
        if value is None:
            if entity_id in readings:
                self._realized_relearn.add(entity_id)
            return None
        last = readings.get(entity_id)
        if last is None or entity_id in self._realized_relearn:
            # First ever reading, or the first valid state after a dropout:
            # baseline only, no delta.
            readings[entity_id] = value
            self._realized_relearn.discard(entity_id)
            self._realized_last_ts[entity_id] = now
            return None
        if value == last:
            # No new energy: keep the accrual window open (see the docstring
            # — the window anchors the jump cap of the NEXT change).
            return None
        last_ts = self._realized_last_ts.get(entity_id)
        delta_wh = (value - last) * self._energy_unit_factor(entity_id)
        readings[entity_id] = value
        self._realized_last_ts[entity_id] = now
        if delta_wh < 0.0:
            # Counter reset or telemetry backwards jump: dropped (a monotone
            # counter never legitimately decreases).
            return None
        cap = REALIZED_JUMP_MAX_WH
        if power_w and last_ts is not None:
            elapsed_h = (now - last_ts).total_seconds() / 3600.0
            cap = REALIZED_JUMP_MAX_FACTOR * power_w * elapsed_h
        if delta_wh > cap:
            _LOGGER.debug(
                "Realized surplus: dropping implausible counter jump of %s"
                " (+%.0f Wh, cap %.0f Wh)",
                entity_id,
                delta_wh,
                cap,
            )
            return None
        return delta_wh

    def _update_realized_surplus(
        self, now: datetime, daily: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Advance the realized (measured) surplus accounting and return the
        `realized` data block, or None when no export meter is configured
        (backend-compat: the key stays absent and everything behaves as
        before).

        Runs near the daily-breakdown assembly so the fresh per-day FORECAST
        values (`daily`) are in scope: the today values mix realized
        (measurement so far) + forecast for the rest of the day, tomorrow is
        pure forecast (operator decisions 1/4/5). The counters themselves are
        always maintained and persisted (even without an export meter — the
        feed-in integral lives here since F-REALIZED-SURPLUS).
        """
        today = now.date()
        state = self._realized
        # Day rollover by LOCAL date (decision 8): the day counters restart
        # at 0; the last readings carry over as the new day's baseline, so
        # midnight itself never shows up as a counter jump.
        if state["date"] != today.isoformat():
            state["date"] = today.isoformat()
            state["lost_wh"] = 0.0
            state["prevented_wh"] = 0.0
            state["feedin_wh"] = 0.0

        changed = False
        external_delta_wh = 0.0
        prevented_delta_wh = 0.0
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            # Jump-filter cap power: the learned planning power wins (it is
            # what the load really draws), the nominal power is the fallback.
            power_w = self._load_learned_power_w.get(subentry_id) or float(
                subentry.data.get(CONF_LOAD_POWER_W, 0.0)
            )
            energy_entity = subentry.data.get(CONF_LOAD_ENERGY_ENTITY)
            if energy_entity:
                delta = self._counter_delta(energy_entity, power_w or None, now)
                if delta is None:
                    continue
                changed = True
                # Prevented realized (decision 5): the real energy of EVERY
                # load with an energy counter.
                prevented_delta_wh += delta
                if not bool(subentry.data.get(CONF_LOAD_IN_HOUSE, True)):
                    # External load (decision 3): fed THROUGH the export
                    # measuring node, so its consumption sits falsely in the
                    # export counter and is subtracted below.
                    external_delta_wh += delta
            else:
                # No energy counter: estimate from the EXISTING runtime
                # counter's delta x planning power instead of running a
                # second runtime integrator. The checkpoint is in-memory; a
                # restart re-arms it (the first delta after a restart is 0,
                # the same bounded loss as the runtime cursor itself).
                runtime_s = self._load_runtime_seconds.get(subentry_id, 0.0)
                base = self._realized_runtime_base.get(subentry_id)
                self._realized_runtime_base[subentry_id] = runtime_s
                if base is not None and runtime_s > base:
                    prevented_delta_wh += (runtime_s - base) / 3600.0 * power_w
                    changed = True
        state["prevented_wh"] += prevented_delta_wh

        export_entity = self.raw_config.get(CONF_EXPORT_METER_ENTITY)
        if export_entity:
            # Cap power of the export meter: the plant can never export more
            # than its PV peak (feed-in is passthrough, the battery is never
            # discharged to the grid), so the jump filter scales with elapsed
            # time like every load counter instead of falling back to the flat
            # absolute floor — which used to discard a whole legitimate
            # accrual after any cycle gap (review 2026-08-03).
            export_delta = self._counter_delta(
                export_entity, float(self.raw_config["pv_max_power_w"]), now
            )
            # Corrected export (decisions 3/4): export counter minus the real
            # consumption of the external loads fed through it. The correction
            # is CARRIED FORWARD as a debt instead of being clamped away: a
            # coarse load counter delivers ~0.1 kWh in one chunk while the
            # export meter advances a few Wh per cycle, so a per-cycle clamp
            # discarded almost the entire correction and the artefact this
            # exists to remove survived (review 2026-08-03). A load delta
            # arriving on a cycle where the export meter did not move is debt
            # too, not a loss.
            pending = external_delta_wh + state["external_debt_wh"]
            if export_delta is None:
                if pending != state["external_debt_wh"]:
                    state["external_debt_wh"] = pending
                    changed = True
            else:
                corr = export_delta - pending
                state["external_debt_wh"] = max(0.0, -corr)
                corr = max(0.0, corr)
                # Early feed-in (F-FEEDIN) counts as lost here — consistent
                # with the planner — and is additionally counted separately
                # below.
                state["lost_wh"] += corr
                state["true_export_total_wh"] += corr
                changed = True
        elif external_delta_wh:
            # No export meter: nothing to correct, so no debt may accumulate.
            state["external_debt_wh"] = 0.0

        # Realized early feed-in (decision 6): bridge the executor's
        # delivered-Wh integral (advanced in _apply_feedin via _feedin_tick,
        # just before this runs) into the persisted counters — the same-day
        # value only.
        delivered = self._feedin_delivered
        feedin_wh = (
            delivered[1] if delivered is not None and delivered[0] == today else 0.0
        )
        if feedin_wh != state["feedin_wh"]:
            state["feedin_wh"] = feedin_wh
            changed = True

        if changed:
            self._save_persistent_state()

        if not export_entity:
            return None
        by_date = {entry["date"]: entry for entry in daily}
        today_fc = by_date.get(today.isoformat(), {})
        tomorrow_fc = by_date.get((today + timedelta(days=1)).isoformat(), {})
        lost_realized = state["lost_wh"] / 1000.0
        prevented_realized = state["prevented_wh"] / 1000.0
        return {
            "date": today.isoformat(),
            "lost_surplus_realized_kwh": round(lost_realized, 3),
            "lost_surplus_today_kwh": round(
                lost_realized + today_fc.get("lost_surplus_kwh", 0.0), 3
            ),
            "lost_surplus_tomorrow_kwh": tomorrow_fc.get("lost_surplus_kwh", 0.0),
            "prevented_export_realized_kwh": round(prevented_realized, 3),
            "prevented_export_today_kwh": round(
                prevented_realized + today_fc.get("prevented_export_kwh", 0.0), 3
            ),
            "prevented_export_tomorrow_kwh": tomorrow_fc.get(
                "prevented_export_kwh", 0.0
            ),
            "early_feed_in_realized_kwh": round(state["feedin_wh"] / 1000.0, 3),
            "true_export_total_kwh": round(state["true_export_total_wh"] / 1000.0, 3),
        }

    def feedin_delivered_today_kwh(self) -> float:
        """Realized early feed-in energy today in kWh (F-REALIZED-SURPLUS
        sensor 7). Read straight from the executor integral — unlike the
        other realized sensors this one does not need the export meter, so it
        must not depend on the `realized` data block."""
        delivered = self._feedin_delivered
        if delivered is None or delivered[0] != dt_util.now().date():
            return 0.0
        return round(delivered[1] / 1000.0, 3)

    def _load_per_day_kwh(self, load_plan, inputs, anchor) -> dict[str, Any]:
        """Per-day planned energy for ONE surplus load — the per-load analogue
        of _daily_surplus_breakdown's loads_kwh. The load's planned energy is
        partitioned over the days its run hours START in (planner-local
        slot.start.date), so the entries sum to planned_energy_kwh (rounding
        aside); the planning power is constant per cycle, so
        `planned_energy_wh / Σ run_hours` recovers it exactly.

        Returns `{today_kwh, tomorrow_kwh, daily}` with `today` = the plan's
        slot-0 day (`anchor`) and `tomorrow` = anchor + 1 — a day the load does
        not run on renders 0.0, matching the aggregate convention.
        """
        run_hours = load_plan.run_hours
        total_h = sum(run_hours)
        by_day: dict[str, float] = {}
        order: list[str] = []
        if total_h > 0.0:
            power_w = load_plan.planned_energy_wh / total_h
            for i, hours in enumerate(run_hours):
                if hours <= 0.0 or i >= len(inputs.slots):
                    continue
                day = inputs.slots[i].start.date().isoformat()
                if day not in by_day:
                    by_day[day] = 0.0
                    order.append(day)
                by_day[day] += power_w * hours
        daily = [{"date": day, "kwh": round(by_day[day] / 1000.0, 3)} for day in order]
        by_date = {e["date"]: e["kwh"] for e in daily}
        today = anchor.isoformat() if anchor is not None else None
        tomorrow = (
            (anchor + timedelta(days=1)).isoformat() if anchor is not None else None
        )
        return {
            "today_kwh": by_date.get(today, 0.0),
            "tomorrow_kwh": by_date.get(tomorrow, 0.0),
            "daily": daily,
        }

    # ------------------------------------------------------------------
    # Output post-processing (D-A2, D-A9/F-N1)
    # ------------------------------------------------------------------

    def _apply_threshold_inertia(
        self, new_threshold: float, config: SystemConfig
    ) -> float:
        if self._displayed_threshold is None or (
            abs(new_threshold - self._displayed_threshold)
            >= config.control.threshold_inertia_percent
        ):
            self._displayed_threshold = new_threshold
        return self._displayed_threshold

    def _apply_hysteresis(
        self, soc: float, threshold: float, config: SystemConfig, now: datetime
    ) -> bool:
        hyst = config.control.hysteresis_percent
        desired = self._inverter_recommendation
        if soc >= threshold + hyst:
            desired = True
        elif soc <= threshold - hyst:
            desired = False

        if desired != self._inverter_recommendation:
            interval = timedelta(seconds=config.control.min_switch_interval_s)
            if (
                self._last_inverter_switch is None
                or now - self._last_inverter_switch >= interval
            ):
                self._inverter_recommendation = desired
                self._last_inverter_switch = now
        return self._inverter_recommendation

    def _update_support_modes(self) -> None:
        """Manual-override detection for the support PSUs (F-N2, 2026-07-05).

        A PSU that turns ON without the integration having switched it
        (e.g. permanent winter operation) puts that path into MANUAL mode:
        the automatic control keeps hands off it — including the 24 V
        make-before-break — until the PSU is switched OFF externally
        again. Both mode and the BM's own support state are persisted, so
        after a restart "on, but not ours" is still distinguishable from
        "on, because we switched it on".

        An unexpected ON is only adopted as a late-confirming device when
        the integration's LAST COMMAND for that specific PSU was 'on' and
        recent, or when that command's confirmation is still pending —
        per-key and per-direction, so an operator ON right after a BM OFF
        enters manual mode instead of being reverted (review finding).
        """
        if self._switch_task is not None and not self._switch_task.done():
            return  # our own sequence is in flight: no verdict possible
        changed = False
        for key, conf_key in (
            ("dc24", CONF_SUPPORT_DC24_SWITCH),
            ("dc48", CONF_SUPPORT_DC48_SWITCH),
        ):
            entity_id = self.raw_config.get(conf_key)
            if not entity_id:
                continue
            real = self._entity_tristate(entity_id)
            if real is None:
                continue  # unavailable/unknown: no verdict
            if self._support_manual[key]:
                if real:
                    self._support_state[key] = True
                    if key == "dc48":
                        # Back on: it is no longer "off because of us".
                        self._dc48_ctrl_caused_off = False
                elif key == "dc48" and (
                    self._dc48_controller_regulating()
                    or (self._dc48_ctrl_caused_off and self._dc48_controller_engaged())
                ):
                    # The R2 voltage controller cycles this PSU: a hard 'off'
                    # is its own doing (or an operator wall-flip the controller
                    # will correct), NOT an exit signal. The R3 switch is the
                    # sole mode truth for the regulated 48 V PSU (operator
                    # answer A) — so reflect the physical off but STAY manual.
                    # `_dc48_ctrl_caused_off` holds this even if log_only is
                    # later flipped back on, so a controller-caused off is never
                    # reinterpreted as an operator wall-off (review round 2). It
                    # is gated on _engaged() so that removing the voltage sensor
                    # (controller can no longer cycle the PSU) does NOT trap the
                    # PSU off in manual forever (review round 3).
                    self._support_state[key] = False
                else:
                    # Manually switched off: automatic control resumes.
                    self._support_manual[key] = False
                    self._support_state[key] = False
                    if key == "dc48":
                        self._dc48_ctrl_caused_off = False
                    changed = True
                    _LOGGER.info(
                        "%s support PSU manually switched off — automatic"
                        " control resumes",
                        "24 V" if key == "dc24" else "48 V",
                    )
            elif real and not self._support_state[key]:
                if self._support_pending_confirm[key]:
                    # Our own activation whose confirmation timed out —
                    # the device just reported late. Adopt, don't pause.
                    self._support_pending_confirm[key] = False
                    self._support_state[key] = True
                    changed = True
                    continue
                cmd = self._last_support_cmd.get(key)
                grace = timedelta(
                    seconds=int(self.raw_config.get("min_switch_interval_s", 60))
                )
                if cmd is not None and cmd[0] and dt_util.now() - cmd[1] < grace:
                    # We commanded THIS PSU on just now: late confirmation,
                    # not a manual override.
                    self._support_state[key] = True
                    changed = True
                    continue
                if self._support_pending_off[key] and cmd is not None and not cmd[0]:
                    # We commanded THIS PSU off and have NOT yet seen it reach
                    # 'off': a lagging 'on' is our own actuation catching up,
                    # not an external override. This escape is UN-TIMED
                    # (mirroring the ON-side _support_pending_confirm): a device
                    # slower than min_switch_interval_s that emits no state event
                    # must not be misread as a manual override (review #10). The
                    # flag is cleared only when the device is observed off (below)
                    # or a new command is issued — an operator ON *after* the
                    # device was seen off then still enters manual.
                    continue
                if self._support_adopt_once:
                    # Pre-0.6.5 store without an ownership record: an ON
                    # left over from the old version's own escalation must
                    # not flip to manual on the upgrade restart.
                    self._support_state[key] = True
                    changed = True
                    continue
                # On, but not switched by us: manual override starts.
                self._support_manual[key] = True
                self._support_state[key] = True
                if key == "dc48":
                    self._dc48_ctrl_caused_off = False  # it is on, not off-by-us
                changed = True
                _LOGGER.info(
                    "%s support PSU was switched on externally — automatic"
                    " control for this PSU is paused until it is switched"
                    " off manually (F-N2)",
                    "24 V" if key == "dc24" else "48 V",
                )
            elif not real:
                # Hard 'off' settles pending confirmations in both directions:
                # a later 'on' after this is a genuine external override.
                self._support_pending_confirm[key] = False
                self._support_pending_off[key] = False
        self._support_adopt_once = False
        # Level-triggered rail guard: PSU hard-off AND DC/DC hard-off is
        # always pathological (manual shutdown, failed restore, boot race).
        self._ensure_dc24_rail_supplied()
        if changed:
            self._save_persistent_state()

    def _ensure_dc24_rail_supplied(self) -> None:
        """The 24 V rail must never be left dead: whenever BOTH the 24 V
        PSU and the DC/DC converter read hard 'off', switch the DC/DC back
        on (level-triggered, retried every cycle until it sticks)."""
        dcdc_entity = self.raw_config.get(CONF_DCDC_SWITCH)
        psu_entity = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
        if not dcdc_entity or not psu_entity:
            return
        if self._dcdc_restore_task is not None and not self._dcdc_restore_task.done():
            return
        if (
            self._entity_tristate(psu_entity) is not False
            or self._entity_tristate(dcdc_entity) is not False
        ):
            return

        async def _restore() -> None:
            async with self._switch_lock:
                # Re-check under the lock: a sequence may have run meanwhile.
                if (
                    self._entity_tristate(psu_entity) is False
                    and self._entity_tristate(dcdc_entity) is False
                    and await self._switch_entity(dcdc_entity, True)
                ):
                    _LOGGER.info(
                        "24 V rail: DC/DC converter switched back on"
                        " (rail had no supply)",
                    )

        self._dcdc_restore_task = self.entry.async_create_background_task(
            self.hass, _restore(), name="battery_manager_dcdc_restore"
        )

    # ------------------------------------------------------------------
    # R2 voltage controller for the regulated manual 48 V PSU (F-N3, v0.7.7)
    # ------------------------------------------------------------------

    def _dc48_controller_engaged(self) -> bool:
        """The controller runs its timers and logs decisions whenever the
        48 V PSU is in manual mode AND both a battery-voltage sensor and the
        PSU switch are configured. In log-only mode it still runs (it only
        withholds actuation) so the shakedown can record what it *would* do.
        """
        return (
            self._support_manual.get("dc48", False)
            and bool(self.raw_config.get(CONF_BATTERY_VOLTAGE_ENTITY))
            and bool(self.raw_config.get(CONF_SUPPORT_DC48_SWITCH))
        )

    def _dc48_controller_regulating(self) -> bool:
        """True only when the controller may actually switch the PSU — i.e.
        engaged AND not in log-only shakedown. This is also the condition
        under which a hard 'off' must not exit manual mode (operator ans A)."""
        return self._dc48_controller_engaged() and not bool(
            self.raw_config.get(CONF_PSU48_CTRL_LOG_ONLY, True)
        )

    def _run_dc48_controller(self, now: datetime) -> None:
        """Battery-voltage hysteresis control of the manual 48 V PSU.

        ON  when V stays <= on_voltage for DC48_CTRL_DWELL_ON_S,
        OFF when V stays >= off_voltage for DC48_CTRL_DWELL_OFF_S,
        HOLD in the band between (off_voltage > on_voltage). A missing or
        implausible reading arms a fail-safe that forces the PSU ON after
        DC48_CTRL_FAILSAFE_MIN minutes (the hardware self-gates above its
        output voltage, so 'on' can never overcharge). Decisions are always
        logged; actuation is skipped in log-only mode.
        """
        diag = self._dc48_ctrl_diag
        if not self._dc48_controller_engaged():
            # Idle: reset the timers so a later activation debounces cleanly.
            self._dc48_below_since = None
            self._dc48_above_since = None
            self._dc48_invalid_since = None
            # The controller is no longer cycling the PSU, so a physical 'off'
            # can no longer be "its own doing": drop the caused-off record so it
            # can't keep the exemption alive (defense-in-depth, review round 3).
            self._dc48_ctrl_caused_off = False
            diag.update(
                active=False,
                mode="off",
                decision=None,
                reason="inactive",
                voltage=None,
            )
            return

        log_only = not self._dc48_controller_regulating()
        mode = "log_only" if log_only else "regulating"
        on_v = float(self.raw_config.get(CONF_PSU48_ON_VOLTAGE_V, 49.56))
        off_v = float(self.raw_config.get(CONF_PSU48_OFF_VOLTAGE_V, 49.8))

        if off_v <= on_v:
            # Defensive: both flows validate off > on, but a hand-edited or
            # legacy config with a collapsed/inverted band would chatter the
            # PSU (the else-branch HOLD region vanishes). Disable regulation
            # rather than actuate — mirrors the buffer clamp in the dynamic
            # buffer. The operator sees a warning and the diagnostic reason.
            self._dc48_below_since = None
            self._dc48_above_since = None
            diag.update(
                active=True,
                mode=mode,
                decision="hold",
                voltage=None,
                reason="invalid_config_off_le_on",
            )
            _LOGGER.warning(
                "48 V R2 controller disabled: off-voltage %.2f V <= on-voltage"
                " %.2f V (collapsed hysteresis band) — fix the configuration",
                off_v,
                on_v,
            )
            return

        entity_id = self.raw_config[CONF_BATTERY_VOLTAGE_ENTITY]
        voltage = self._read_float(entity_id)
        plausible = (
            voltage is not None
            and DC48_CTRL_VOLTAGE_MIN <= voltage <= DC48_CTRL_VOLTAGE_MAX
        )

        if not plausible:
            # Stale/implausible reading: FREEZE the dwell timers (do not reset)
            # so a brief sensor blip does not lose accumulated dwell (spec §6
            # "einfrieren") — this also denies a flapping sensor a way to stall
            # regulation forever. Arm the fail-safe; a sustained outage forces
            # the PSU on regardless.
            if self._dc48_invalid_since is None:
                self._dc48_invalid_since = now
            invalid_for = now - self._dc48_invalid_since
            if invalid_for >= timedelta(minutes=DC48_CTRL_FAILSAFE_MIN):
                diag.update(
                    active=True,
                    mode=mode,
                    decision="on",
                    voltage=voltage,
                    reason="failsafe_no_reading",
                )
                self._dc48_actuate(True, "fail-safe (no valid voltage)", log_only)
            else:
                diag.update(
                    active=True,
                    mode=mode,
                    decision="hold",
                    voltage=voltage,
                    reason="waiting_failsafe",
                )
            return

        # Valid reading: clear the fail-safe timer.
        self._dc48_invalid_since = None

        if voltage <= on_v:
            self._dc48_above_since = None
            if self._dc48_below_since is None:
                self._dc48_below_since = now
            held = now - self._dc48_below_since
            if held >= timedelta(seconds=DC48_CTRL_DWELL_ON_S):
                diag.update(
                    active=True,
                    mode=mode,
                    decision="on",
                    voltage=voltage,
                    reason="below_on_voltage",
                )
                self._dc48_actuate(True, f"V {voltage:.2f} <= on {on_v:.2f}", log_only)
            else:
                diag.update(
                    active=True,
                    mode=mode,
                    decision="hold",
                    voltage=voltage,
                    reason="on_dwell",
                )
        elif voltage >= off_v:
            self._dc48_below_since = None
            if self._dc48_above_since is None:
                self._dc48_above_since = now
            held = now - self._dc48_above_since
            if held >= timedelta(seconds=DC48_CTRL_DWELL_OFF_S):
                diag.update(
                    active=True,
                    mode=mode,
                    decision="off",
                    voltage=voltage,
                    reason="above_off_voltage",
                )
                self._dc48_actuate(
                    False, f"V {voltage:.2f} >= off {off_v:.2f}", log_only
                )
            else:
                diag.update(
                    active=True,
                    mode=mode,
                    decision="hold",
                    voltage=voltage,
                    reason="off_dwell",
                )
        else:
            # Hysteresis band: hold and reset both dwell timers.
            self._dc48_below_since = None
            self._dc48_above_since = None
            diag.update(
                active=True,
                mode=mode,
                decision="hold",
                voltage=voltage,
                reason="hysteresis_band",
            )

    def _dc48_actuate(self, target: bool, reason: str, log_only: bool) -> None:
        """Bring the 48 V PSU to ``target`` if it is not already there.

        Edge-triggered against the physical switch state (self-heals drift).
        In log-only mode the intended action is only logged. A real switch
        runs detached under the switch lock and records the F-N2 command
        bookkeeping so the manual-override detector never misreads the
        controller's own OFF as an external event.
        """
        entity_id = self.raw_config[CONF_SUPPORT_DC48_SWITCH]
        current = self._entity_tristate(entity_id)
        if current is target:
            return  # already in the desired state
        if current is None and target is False:
            return  # can't confirm an unavailable switch; only force-ON blind

        if log_only:
            _LOGGER.info(
                "48 V R2 controller (log-only) would switch %s: %s",
                "on" if target else "off",
                reason,
            )
            return

        if self._dc48_ctrl_task is not None and not self._dc48_ctrl_task.done():
            return  # our own actuation is still in flight
        if self._switch_task is not None and not self._switch_task.done():
            return  # a planner sequence holds the switch lock; retry next cycle

        async def _do() -> None:
            async with self._switch_lock:
                # Re-check under the lock: the operator may have exited manual
                # (async_set_support_manual, which also holds this lock) while
                # this actuation was queued. A stale controller command must not
                # fire in auto mode — the R3 switch owns the final state then.
                if not self._dc48_controller_regulating():
                    return
                if self._entity_tristate(entity_id) is target:
                    return
                if await self._switch_entity(entity_id, target):
                    self._support_state["dc48"] = target
                    # Remember whether the PSU is now off BECAUSE of us, so a
                    # later log_only flip can't reinterpret it as an operator
                    # wall-off and drop manual mode (review round 2).
                    self._dc48_ctrl_caused_off = target is False
                    # Record the F-N2 command bookkeeping only on a confirmed
                    # actuation (rolled forward, never on a failed switch) so
                    # the manual-override detector never misreads the
                    # controller's own OFF as an external operator action.
                    # NB: deliberately NOT touching _last_support_switch — that
                    # is the planner's shared dc24/dc48 throttle and the
                    # controller must not consume it (it has its own guards).
                    self._last_support_cmd["dc48"] = (target, dt_util.now())
                    self._support_pending_confirm["dc48"] = False
                    self._support_pending_off["dc48"] = not target
                    self._save_persistent_state()
                    _LOGGER.info(
                        "48 V R2 controller switched %s: %s",
                        "on" if target else "off",
                        reason,
                    )
                    if self.data:
                        self.data["support_dc48"] = target
                        self.async_update_listeners()

        self._dc48_ctrl_task = self.entry.async_create_background_task(
            self.hass, _do(), name="battery_manager_dc48_controller"
        )

    def support_manual(self, key: str) -> bool:
        """Public accessor for a PSU's manual-override state (R3 switch)."""
        return self._support_manual.get(key, False)

    def feedin_enabled(self) -> bool:
        """Public accessor for the feed-in runtime switch (F-FEEDIN R6)."""
        return self._feedin_switch_on

    def feedin_manual(self) -> bool:
        """Public accessor for the feed-in manual-override state (F-FEEDIN R7):
        True while the operator owns the setpoint — from the external change
        until the next midnight (the stored date is exclusive)."""
        return (
            self._feedin_manual_until is not None
            and dt_util.now().date() < self._feedin_manual_until
        )

    def _feedin_manual_plan_w(self) -> float | None:
        """The operator-owned setpoint for the planner (F-FEEDIN R9).

        Only while manual mode is active; a negative entity value means
        export (FEEDIN_SETPOINT_EXPORT_SIGN, already flipped by
        _feedin_read_power_w) — a positive setpoint is an IMPORT request and
        books no feed-in (clamped to 0). Unreadable entity -> None, so the
        plan falls back to the automatic schedule instead of inventing one.
        """
        if not self.feedin_manual():
            return None
        value = self._feedin_read_power_w()
        if value is None:
            return None
        return max(0.0, value)

    async def async_set_feedin_enabled(self, on: bool) -> None:
        """Runtime on/off switch for early feed-in (F-FEEDIN R6).

        Off pauses the executor; the next `_apply_feedin` pass then writes the
        setpoint 0 once (auto mode only — in manual mode the operator owns the
        value). On resumes plan-following. Persisted like the F-N2 manual
        flags so a restart keeps the pause.
        """
        if self._feedin_switch_on == on:
            return
        self._feedin_switch_on = on
        _LOGGER.info("Early feed-in runtime switch turned %s", "on" if on else "off")
        if on and self._feedin_manual_until is not None:
            # Operator rule 2026-08-08: a rising edge on the runtime switch
            # hands the setpoint back to the automation — manual mode ends
            # NOW instead of at midnight. The operator's last value is
            # adopted as the baseline so it is not re-judged as an override.
            self._feedin_manual_until = None
            self._feedin_last_written_w = self._feedin_read_power_w()
            self._feedin_last_write_at = None
            _LOGGER.info(
                "Feed-in manual mode ended (runtime switch on) — automatic"
                " control resumes"
            )
        self._save_persistent_state()
        self.async_update_listeners()
        if not on:
            # Write the 0 HERE, not via the refresh: while the inputs are stale
            # the refresh raises UpdateFailed before `_apply_feedin`, so the
            # switch read "off" while the plant kept exporting (review
            # 2026-08-03).
            await self._feedin_force_zero("runtime switch off")
        await self.async_request_refresh()

    def dc48_controller_diagnostic(self) -> dict[str, Any]:
        """Public snapshot of the R2 controller state (active/mode/decision/
        reason/voltage) for the 48 V support-mode sensor, so the log-only
        shakedown and the live regulation are observable in the UI."""
        return dict(self._dc48_ctrl_diag)

    def learned_state_snapshot(self) -> dict[str, Any]:
        """V7: a JSON-serialisable snapshot of the coordinator's learned and
        latched runtime state for the config-entry diagnostics endpoint.

        Everything a forensic pass previously had to guess at: the learned
        planning power and consumable-tank model per load, the power-warning /
        stale-SOC / telemetry-freeze latches, the T*-inertia value, the
        floor-guard state and the startup/SOC-cache bookkeeping. Sets are
        rendered as sorted lists and timestamps as ISO strings so the standard
        diagnostics JSON serialiser handles them."""

        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "startup": {
                "startup_complete": self._startup_complete,
                "successful_updates": self._successful_updates,
                "startup_at": _iso(self._startup_at),
                "last_valid_soc_percent": self._last_valid_soc,
                "last_soc_update": _iso(self._last_soc_update),
            },
            "threshold_inertia": {
                "displayed_threshold_percent": self._displayed_threshold
            },
            "floor_guard": {"active": self._floor_guard_active},
            "learned_power_w": dict(self._load_learned_power_w),
            "load_runtime_seconds": dict(self._load_runtime_seconds),
            "power_warning_latched": dict(self._load_power_warning),
            # F-PREDRAIN-BLOCK: consecutive-plan stability per proposed block.
            "predrain_block_stable_plans": {
                load_id: count
                for load_id, (
                    _sig,
                    count,
                    _first,
                ) in self._predrain_block_evidence.items()
            },
            "soc_stale_frozen_soc": dict(self._load_soc_stale),
            "telemetry_freeze_stale": sorted(self._load_freeze_stale),
            "tank": {
                "diag": {k: dict(v) for k, v in self._load_tank_diag.items()},
                "samples": {k: list(v) for k, v in self._load_tank_samples.items()},
                "pending_full_min": dict(self._load_tank_full_min),
            },
            "support": {
                "state": dict(self._support_state),
                "manual": dict(self._support_manual),
                "dc48_controller": dict(self._dc48_ctrl_diag),
            },
        }

    def support_active(self, key: str) -> bool:
        """Public accessor for a support PSU's current on/off state.

        Reflects the persisted BM support state, known independently of a plan —
        so the support entities stay available (and in sync with the always-
        available manual switch) even while an update is failing (review #15)."""
        return self._support_state.get(key, False)

    async def async_set_support_manual(self, key: str, on: bool) -> None:
        """Operator manual override for a support PSU (F-N2/R3, F-N3 §7).

        Turning it on enters manual mode and actuates the PSU (24 V via
        make-before-break so the rail is never sourceless); turning it off
        exits manual mode and restores automatic control. The single entry
        point for the manual-mode switches; serialised with the planner's
        own switching via the switch lock. The simulation then treats the
        path as permanently active (forced_on) so the SOC forecast matches
        the real winter operation.
        """
        conf_key = (
            CONF_SUPPORT_DC24_SWITCH if key == "dc24" else CONF_SUPPORT_DC48_SWITCH
        )
        psu_entity = self.raw_config.get(conf_key)
        if not psu_entity:
            return  # not configured
        label = "24 V" if key == "dc24" else "48 V"
        async with self._switch_lock:
            # Judge idempotence AFTER acquiring the lock, so a rapid second
            # toggle issued during a make-before-break window is honoured
            # against the settled state instead of the pre-lock snapshot.
            if self._support_manual.get(key, False) == on:
                return
            if on:
                actuated = (
                    await self._sequence_dc24(True, psu_entity)
                    if key == "dc24"
                    else await self._switch_entity(psu_entity, True)
                )
                if not actuated:
                    _LOGGER.warning(
                        "Manual %s support activation failed to actuate the PSU",
                        label,
                    )
                    return
                self._support_manual[key] = True
                self._support_state[key] = True
                _LOGGER.info("%s support PSU set to MANUAL (operator switch)", label)
            else:
                actuated = (
                    await self._sequence_dc24(False, psu_entity)
                    if key == "dc24"
                    else await self._switch_entity(psu_entity, False)
                )
                if not actuated:
                    # Restore aborted (e.g. DC/DC unconfirmed): the PSU is
                    # physically still on, so keep manual mode rather than
                    # desyncing the model. The next cycle / retry heals it.
                    _LOGGER.warning(
                        "Manual %s support deactivation failed to actuate —"
                        " staying in manual mode",
                        label,
                    )
                    return
                self._support_manual[key] = False
                self._support_state[key] = False
                _LOGGER.info(
                    "%s support PSU manual mode ended (operator switch) —"
                    " automatic control resumes",
                    label,
                )
            now = dt_util.now()
            self._last_support_switch = now
            self._last_support_cmd[key] = (on, now)
            self._support_pending_confirm[key] = False
            self._support_pending_off[key] = not on
            if key == "dc48":
                # Operator took over mode truth via the R3 switch: any prior
                # controller-caused-off record is void.
                self._dc48_ctrl_caused_off = False
        self._save_persistent_state()
        self.async_update_listeners()
        await self.async_request_refresh()

    def _sync_support_state_from_entities(self) -> None:
        """Adopt the real switch states while no sequence is running.

        Heals ON->OFF desyncs from restarts and aborted sequences. An
        OFF->ON transition is deliberately NOT adopted here: judging it
        (manual override vs late confirmation) is the exclusive job of
        _update_support_modes — adopting it would let the next plan revert
        an operator's ON without ever entering manual mode (review
        finding). Manual-mode PSUs are skipped entirely.
        'unavailable'/'unknown' states are ignored — never treated as 'off'.
        """
        changed = False
        for key, conf_key in (
            ("dc24", CONF_SUPPORT_DC24_SWITCH),
            ("dc48", CONF_SUPPORT_DC48_SWITCH),
        ):
            if self._support_manual[key]:
                continue
            entity_id = self.raw_config.get(conf_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state not in ("on", "off"):
                continue
            real = state.state == "on"
            if real and not self._support_state[key]:
                continue  # OFF->ON is judged by _update_support_modes only
            if self._support_state[key] != real:
                self._support_state[key] = real
                changed = True
        if changed:
            self._save_persistent_state()

    def _warn_unreliable_switches_once(self) -> None:
        """Warn if a support switch cannot confirm its real device state."""
        if self._assumed_state_warned:
            return
        self._assumed_state_warned = True
        for conf_key in (
            CONF_SUPPORT_DC24_SWITCH,
            CONF_SUPPORT_DC48_SWITCH,
            CONF_DCDC_SWITCH,
        ):
            entity_id = self.raw_config.get(conf_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is not None and state.attributes.get("assumed_state"):
                _LOGGER.warning(
                    "%s reports an assumed state only; the make-before-break"
                    " confirmation cannot detect an unresponsive device."
                    " Use state-reporting switches for the 24 V rail supplies.",
                    entity_id,
                )

    async def _switch_entity(
        self,
        entity_id: str,
        turn_on: bool,
        *,
        actor_owner: str | None = None,
    ) -> bool:
        # F-CASCADE-STORAGE final physical boundary (live 2026-09-01): the
        # normal load-ID guards correctly exclude cascade members, yet the
        # live Root still received a generic OFF every ~10 s while the cascade
        # remained in waking_members.  Entity ownership is the invariant that
        # matters physically.  Re-resolve it immediately before every service
        # call, and require CascadeManager to present the owning cascade ID.
        # This also fail-closes support/calibration/stale detached paths if a
        # future refactor accidentally points them at a cascade actor.
        reserved_by = self.cascade_manager.actor_owner(entity_id)
        if reserved_by is not None and actor_owner != reserved_by:
            warning_key = (entity_id, actor_owner, turn_on)
            if warning_key not in self._cascade_actor_block_warned:
                self._cascade_actor_block_warned.add(warning_key)
                _LOGGER.warning(
                    "Blocked non-owner switch request for cascade actor %s"
                    " (owner=%s, caller=%s, requested=%s)",
                    entity_id,
                    reserved_by,
                    actor_owner or "generic",
                    "ON" if turn_on else "OFF",
                )
            else:
                _LOGGER.debug(
                    "Repeated non-owner request remains blocked for cascade actor %s",
                    entity_id,
                )
            return False
        service = "turn_on" if turn_on else "turn_off"
        try:
            # homeassistant.* works across domains (switch, input_boolean, ...)
            await self.hass.services.async_call(
                "homeassistant", service, {"entity_id": entity_id}, blocking=True
            )
        except Exception as err:
            _LOGGER.error("%s failed for %s: %s", service, entity_id, err)
            return False
        return True

    async def _set_number_value(self, entity_id: str, value: float) -> bool:
        """Write an input_number (F-FEEDIN: the external controller's AC
        setpoint). Same error semantics as _switch_entity: log + False."""
        try:
            await self.hass.services.async_call(
                "input_number",
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
        except Exception as err:
            _LOGGER.error("input_number.set_value failed for %s: %s", entity_id, err)
            return False
        return True

    def _entity_is_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    async def _sequence_dc24(self, activate: bool, psu_entity: str) -> bool:
        """Switch the 24 V rail supply make-before-break (docs/ALGORITHM.md D-A9).

        The rail is fed either by the DC/DC converter (from battery) or the
        grid PSU — never by neither. Activation: PSU on -> delay -> DC/DC off.
        Deactivation: DC/DC on -> delay -> PSU off. If the newly activated
        source does not confirm 'on', the switchover is aborted and the
        previous source stays on.
        """
        dcdc_entity = self.raw_config.get(CONF_DCDC_SWITCH)
        delay_s = float(self.raw_config.get(CONF_SUPPORT_SWITCH_DELAY_S, 3))

        if not dcdc_entity:
            # No switchable DC/DC configured: plain PSU toggle (parallel feed).
            return await self._switch_entity(psu_entity, activate)

        first_on, then_off = (
            (psu_entity, dcdc_entity) if activate else (dcdc_entity, psu_entity)
        )
        if not await self._switch_entity(first_on, True):
            return False
        await asyncio.sleep(delay_s)
        if not self._entity_is_on(first_on):
            _LOGGER.error(
                "New 24 V supply %s did not report 'on'; aborting switchover,"
                " %s stays on",
                first_on,
                then_off,
            )
            if activate:
                # The ON service succeeded but the device has not confirmed
                # yet: remember it so a late 'on' report is adopted as ours
                # instead of being misread as a manual override (F-N2).
                self._support_pending_confirm["dc24"] = True
            return False
        return await self._switch_entity(then_off, False)

    async def _apply_support_switching(
        self, result, config: SystemConfig, now: datetime
    ) -> None:
        """Evaluate support paths and start switching if needed (decision F-N1).

        The actual switching runs in an entry-scoped background task so that
        a cancelled debounce/refresh task can never abort a make-before-break
        sequence halfway (review finding: cancellation propagation). While a
        sequence is in flight, evaluation is skipped; afterwards the idle
        re-sync adopts the real switch states.
        """
        if not config.support.configured:
            return
        if self._switch_task is not None and not self._switch_task.done():
            return
        self._warn_unreliable_switches_once()
        self._sync_support_state_from_entities()

        desired = {"dc24": result.support_dc24_now, "dc48": result.support_dc48_now}
        # Manual-mode PSUs (F-N2) are not ours to switch: pin desired to
        # the current state so no action is derived for them.
        for key, manual in self._support_manual.items():
            if manual:
                desired[key] = self._support_state[key]
        if desired == self._support_state:
            return
        interval = timedelta(seconds=config.control.min_switch_interval_s)
        if (
            self._last_support_switch is not None
            and now - self._last_support_switch < interval
        ):
            return

        self._last_support_switch = now
        # Per-key command record (direction + time): the basis for telling
        # a late-confirming device from a manual override (F-N2).
        for key in ("dc24", "dc48"):
            if (
                not self._support_manual[key]
                and desired[key] != self._support_state[key]
            ):
                self._last_support_cmd[key] = (desired[key], now)
                self._support_pending_confirm[key] = False
                self._support_pending_off[key] = not desired[key]
        self._switch_task = self.entry.async_create_background_task(
            self.hass,
            self._execute_support_switching(desired),
            name="battery_manager_support_switching",
        )

    async def _execute_support_switching(self, desired: dict[str, bool]) -> None:
        """Carry out the switching sequences; runs detached from the refresh."""
        async with self._switch_lock:
            dc48_entity = self.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
            if (
                dc48_entity
                and not self._support_manual["dc48"]
                and desired["dc48"] != self._support_state["dc48"]
            ):
                if await self._switch_entity(dc48_entity, desired["dc48"]):
                    # A successful service call is no device confirmation; the
                    # idle re-sync corrects _support_state on the next cycle.
                    self._support_state["dc48"] = desired["dc48"]
                    _LOGGER.info(
                        "48 V support PSU switched %s (%s)",
                        "on" if desired["dc48"] else "off",
                        dc48_entity,
                    )
                    await self._note_support_switch_result(ok=True)
                else:
                    await self._note_support_switch_result(ok=False)

            dc24_entity = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
            if (
                dc24_entity
                and not self._support_manual["dc24"]
                and desired["dc24"] != self._support_state["dc24"]
            ):
                # _sequence_dc24's False covers BOTH failure classes the F7/U5
                # escalation counts: a failed service call AND an unconfirmed
                # make-before-break switchover.
                if await self._sequence_dc24(desired["dc24"], dc24_entity):
                    self._support_state["dc24"] = desired["dc24"]
                    _LOGGER.info(
                        "24 V rail now fed by %s",
                        "grid PSU" if desired["dc24"] else "DC/DC converter",
                    )
                    await self._note_support_switch_result(ok=True)
                else:
                    await self._note_support_switch_result(ok=False)

        # Persist the BM's own support state: after a restart it is the
        # evidence that an 'on' PSU is ours and not a manual override.
        self._save_persistent_state()
        # Reflect the new state in the entities without waiting for a replan.
        if self.data:
            self.data["support_dc24"] = self._support_state["dc24"]
            self.data["support_dc48"] = self._support_state["dc48"]
            self.async_update_listeners()

    async def _note_support_switch_result(self, ok: bool) -> None:
        """F7/U5: escalate PERSISTENT support-actuator failure to the operator.

        No retry counter or backoff lives here: the 5-min cycle re-derives
        desired != state and retries implicitly, so a transient hiccup heals
        itself. What needs surfacing is an actuator that KEEPS failing —
        after SUPPORT_SWITCH_FAIL_ALERT consecutive failures (a failed
        service call OR an unconfirmed make-before-break sequence) a repair
        issue is raised and pushed ONCE; the first success resets the streak
        and resolves the issue. Scope is the automatic support path
        (`_execute_support_switching`); the operator-driven manual mode and
        the R2 voltage controller have their own diagnostics and are
        deliberately not counted.
        """
        if ok:
            self._support_fail_count = 0
            if self._support_fail_alerted:
                self._support_fail_alerted = False
                ir.async_delete_issue(
                    self.hass, DOMAIN, f"support_switch_failed_{self.entry.entry_id}"
                )
                _LOGGER.info(
                    "Support switching succeeded again — failure issue resolved"
                )
            return
        self._support_fail_count += 1
        if (
            self._support_fail_count < SUPPORT_SWITCH_FAIL_ALERT
            or self._support_fail_alerted
        ):
            return
        self._support_fail_alerted = True
        _LOGGER.warning(
            "Support switching failed %d times in a row — raising a repair"
            " issue (the 5-min cycle keeps retrying automatically)",
            self._support_fail_count,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"support_switch_failed_{self.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="support_switch_failed",
        )
        await self._push_notification(
            "⚠️ Battery Manager: support switching failing",
            f"The support actuators (48 V PSU / 24 V rail) failed"
            f" {self._support_fail_count} times in a row — service call"
            " failed or switchover not confirmed. Check the configured"
            " switches; the 5-minute cycle keeps retrying.",
        )

    # ------------------------------------------------------------------
    # Direct charging-path control per load (docs/LOAD_CONTROL.md §3)
    # ------------------------------------------------------------------

    def _entity_tristate(self, entity_id: str) -> bool | None:
        """on/off as bool; None while unavailable/unknown (not 'off')."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _charging_is_active(self, data: dict[str, Any]) -> bool | None:
        """Charging is active iff the input plug is on AND (if configured)
        the charge-enable gate is on. A plug that is on for passthrough
        purposes with the gate off does NOT count as charging. Returns
        None while an involved entity is unavailable/unknown — a dropout
        must not read as 'charge over' (it would delete the learned power
        EMA mid-charge; same principle as the support-switch re-sync)."""
        plug = self._entity_tristate(data[CONF_LOAD_CONTROL_SWITCH])
        if plug is not True:
            return plug  # False, or None while unavailable
        enable = data.get(CONF_LOAD_CHARGE_ENABLE)
        if not enable:
            return True
        return self._entity_tristate(enable)

    def _seamless_extension_ok(self, load_id: str, data) -> bool:
        """F-SEAMLESS-RUNS: may an expired run deadline be extended instead
        of force-switching OFF?

        Non-energy-limited loads: always — the deadline is pure quantum
        bookkeeping for them. Energy-limited loads: only while the G2
        stale-SOC guard CAN supervise the load RIGHT NOW — control switch
        configured AND the SOC and power-feedback entities currently
        READABLE (not merely configured: during a sensor outage G2 gathers
        no evidence, so an extension would reopen the unbounded-charge
        hole; review finding 2026-07-18) AND not stale-latched. Anything
        G2 cannot supervise keeps the F-RESIDUAL-TOPUP R7/R8 duty-cycle
        cap (recommendation-only energy-limited loads included), because
        for those the cap is the ONLY defence against a stale `remaining`
        stretching a small top-up into an unbounded charge."""
        if not data.get(CONF_LOAD_ENERGY_LIMITED):
            return True
        if not data.get(CONF_LOAD_CONTROL_SWITCH):
            return False
        if load_id in self._load_soc_stale:
            return False
        soc_entity = data.get(CONF_LOAD_SOC_ENTITY)
        power_entity = data.get(CONF_LOAD_POWER_ENTITY)
        if not soc_entity or not power_entity:
            return False
        return (
            self._read_float(soc_entity) is not None
            and self._read_float(power_entity) is not None
        )

    def _maintain_recommendation_deadline(
        self, load_id, data, plan, now: datetime, slot_durations
    ) -> None:
        """Sub-hour cap for a recommendation-only load (no control switch;
        F-SUBHOUR R12, extended to energy-limited loads by F-RESIDUAL-TOPUP R9).
        BM cannot switch the load, so the deadline only governs the published
        `active` flag the operator's automation follows: it is anchored on the
        OFF->ON edge and held (even once past) so the sensor reads active from the
        run's start to `run_start + max(min_runtime, run)` and off for the rest. A
        later export window (plan cycles inactive->active) re-anchors.

        F-SEAMLESS-RUNS: when the plan is STILL active at the expired
        deadline and the load is extension-eligible, the window re-anchors
        IMMEDIATELY (without the min_runtime floor — it was already served)
        so the published `active` never dips at a quantum boundary — the
        operator's automation keeps the device running seamlessly.

        FIX-5 (fallback for extension-INELIGIBLE loads, i.e. energy-limited
        recommendation-only loads keeping the F-RESIDUAL-TOPUP R9 cap):
        while the plan stays CONTINUOUSLY active, the frozen deadline used
        to wedge the published `active` False from expiry until the whole
        block ended (hours). Once `now >= deadline + min_off` (min_off
        falls back to min_runtime) we RE-ANCHOR a fresh run window —
        mirroring a controlled load's force-off -> min_off dwell -> re-on
        cycle — so the sensor duty-cycles instead of staying wedged off."""
        active = bool(plan and plan.active_now)
        if not active:
            if load_id in self._load_run_deadline:
                self._load_run_deadline.pop(load_id, None)
                self._cancel_off_timer(load_id)
            return
        min_runtime = int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30))

        def _anchor(floor_min: int) -> None:
            off_min = max(
                floor_min, round(plan.active_run_hours(slot_durations) * 60.0)
            )
            off_at = now + timedelta(minutes=max(1, off_min))
            self._load_run_deadline[load_id] = off_at
            self._arm_off_timer(load_id, off_at)

        deadline = self._load_run_deadline.get(load_id)
        if deadline is None:
            _anchor(min_runtime)  # OFF->ON edge: freeze the first run window
            return
        if now >= deadline and self._seamless_extension_ok(load_id, data):
            # F-SEAMLESS-RUNS: still booked at the boundary — re-anchor now,
            # no min_runtime floor (already served), `active` never dips.
            _anchor(1)
            return
        # Extension-ineligible fallback (FIX-5): re-anchor once the min_off
        # dwell (since the deadline) has elapsed.
        min_off = int(data.get(CONF_LOAD_MIN_OFF_MIN, min_runtime))
        if now >= deadline + timedelta(minutes=min_off):
            _anchor(min_runtime)

    def _update_floor_guard(
        self,
        soc: float,
        config: SystemConfig,
        pv_power_w: float = 0.0,
        running_load_power_w: float = 0.0,
        now: datetime | None = None,
    ) -> bool:
        """G4 floor guard (F-EXECUTOR-GUARDS, operator rule 2026-07-18):
        surplus loads must NEVER run grid-fed — "Wenn der Inverter aus ist
        oder der SOC 20 % erreicht, dürfen Zusatzlasten nicht mehr
        angesteuert werden."

        Trips when the battery SOC reaches the inverter-cutoff floor
        (`inverter_min_soc_percent`; the real inverter shuts down there, so
        any still-running load falls onto the grid — the 2026-07-18 06:21
        incident: 432 W dehumidifier grid-fed for ~10 min because only the
        min_runtime dwell timed the OFF). The SOC branch LATCHES: it releases
        only at `floor + hysteresis_percent`, so a SOC hovering at the floor
        cannot flap the loads, and it is UNCONDITIONAL (PV cannot rescue a
        cut-off inverter). A restart inside the release band starts latched
        (conservative).

        The inverter-recommendation-off branch is GATED on PV coverage to match
        the planner-G4 definition (F-STRICT-SURPLUS R2, `_slot_serviceable`):
        grid-fed = inverter off AND PV below the running surplus loads' power.
        A T*-driven inverter shutdown only routes the loads to the grid when PV
        cannot cover them; while PV >= the running loads' (learned) power the
        loads ran demonstrably PV-fed and must NOT be tripped off (live
        2026-07-24 flap window: PV 1391-1680 W vs a ~410 W dehumidifier, yet the
        old unconditional branch forced it off for ~67 min in the PV-richest
        morning). Must run AFTER `_apply_hysteresis` so the recommendation
        member is fresh for this cycle.
        """
        floor = config.control.inverter_min_soc_percent
        hyst = max(0.0, config.control.hysteresis_percent)
        prev = self._floor_guard_active
        if prev is None or prev:
            # Release threshold (and restart init): strictly ABOVE the floor
            # even at hysteresis 0 — otherwise trip (<= floor) and release
            # (< floor + 0) would overlap at exactly the floor SOC (a
            # persistent state: the inverter cuts off there) and the guard
            # would flap every cycle, re-enabling loads at the floor.
            soc_low = soc <= floor or soc < floor + hyst
        else:
            soc_low = soc <= floor  # trip threshold
        # Planner-G4 parity: rec-off only grid-feeds the loads when PV cannot
        # cover them. Strict `<` (PV exactly equal counts as covered) mirrors
        # `_slot_serviceable`'s `pv_wh + _EPS < ac_wh`.
        rec_off_grid_fed = (not self._inverter_recommendation) and (
            pv_power_w < running_load_power_w
        )
        guard = soc_low or rec_off_grid_fed
        if guard and not prev:
            reason_key = "soc" if soc_low else "rec"
            reason_text = (
                f"SOC {soc:.1f} % at the {floor:.0f} % inverter floor"
                if soc_low
                else (
                    "inverter recommendation off and PV"
                    f" {pv_power_w:.0f} W below the {running_load_power_w:.0f} W"
                    " running surplus loads"
                )
            )
            # Part C rate limit: one WARNING per reason per 15 min; further trips
            # of the same reason log at DEBUG so a flapping guard cannot spam.
            stamp = now or dt_util.utcnow()
            last = self._floor_guard_warn_at.get(reason_key)
            level = (
                logging.WARNING
                if last is None or stamp - last >= timedelta(minutes=15)
                else logging.DEBUG
            )
            if level == logging.WARNING:
                self._floor_guard_warn_at[reason_key] = stamp
            _LOGGER.log(
                level,
                "Floor guard TRIPPED (%s): surplus loads are forced off and"
                " blocked until SOC recovers above %.1f %% with the inverter"
                " recommendation on (G4)",
                reason_text,
                floor + hyst,
            )
        elif prev and not guard:
            _LOGGER.info(
                "Floor guard released at SOC %.1f %% — surplus loads may run again",
                soc,
            )
        self._floor_guard_active = guard
        return guard

    def _effective_load_active(self, load_plan, now: datetime) -> bool:
        """Published `active` for the load binary sensor: the whole-slot
        `active_now` capped by the frozen sub-hour deadline (F-SUBHOUR) and
        forced off entirely while the G4 floor guard is active — the
        operator's own automations follow this flag, and they must stop
        with the executor (a grid-fed run is forbidden either way)."""
        if self._floor_guard_active:
            return False
        deadline = self._load_run_deadline.get(load_plan.load_id)
        return bool(load_plan.active_now) and (deadline is None or now < deadline)

    def _flicker_continuation_ok(
        self, load_id: str, now: datetime, off_time: datetime
    ) -> bool:
        """F8: may an OFF->ON within REC_FLICKER_CONTINUATION_MIN of a
        recommendation-driven stop waive min_off as a run continuation?

        True only within the window AND while the load has not exhausted its
        ping-pong budget: after REC_FLICKER_MAX_CONTINUATIONS waived
        continuations within REC_FLICKER_PINGPONG_WINDOW_MIN minutes, the
        normal min_off applies again, so a genuinely flapping plan can never
        short-cycle a compressor. Each distinct off episode (keyed by its
        timestamp) counts once, so a retried ON in the same episode — e.g. a
        failed actuation re-attempted next cycle — is not double-charged."""
        if now - off_time > timedelta(minutes=REC_FLICKER_CONTINUATION_MIN):
            return False
        recent = [
            t
            for t in self._load_flicker_hist.get(load_id, [])
            if now - t < timedelta(minutes=REC_FLICKER_PINGPONG_WINDOW_MIN)
        ]
        if off_time in recent:
            # Same off episode retried within the window: already granted.
            self._load_flicker_hist[load_id] = recent
            return True
        if len(recent) >= REC_FLICKER_MAX_CONTINUATIONS:
            # Ping-pong budget spent: fall back to the normal min_off.
            self._load_flicker_hist[load_id] = recent
            return False
        recent.append(off_time)
        self._load_flicker_hist[load_id] = recent
        return True

    def _latched_hold_candidates(self) -> set[str]:
        """F11: the SWITCHABLE loads eligible for a latch hold this cycle — a
        power warning is latched AND the load has a control switch AND power
        feedback. This is exactly `_latch_hold_ok`'s structural pre-filter (it
        excludes the floor-guard/inverter/shadow-plan gates, which decide
        whether the hold may actually run). Used to (a) decide whether a shadow
        plan is even needed and (b) pick which loads' F5 saturated override to
        clear in it."""
        candidates: set[str] = set()
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            if (
                self._load_power_warning.get(subentry_id, False)
                and data.get(CONF_LOAD_CONTROL_SWITCH)
                and data.get(CONF_LOAD_POWER_ENTITY)
            ):
                candidates.add(subentry_id)
        return candidates

    async def _latch_shadow_active(
        self, config: SystemConfig, inputs, load_states
    ) -> dict[str, bool]:
        """F11: per latched-switchable load, whether the SHADOW plan activates
        its slot 0 right now — i.e. whether the normal planner WOULD run the
        load this instant if it were not latched.

        A load latched by F-L7 (full tank) is planned by F5 at its measured
        ~0 W (`saturated_power_w`), so the honest plan never books it and F10's
        opportunistic hold has no plan flag to lean on. Instead of a stricter-
        than-planning PV heuristic, replan with `saturated_power_w` cleared for
        exactly the latched switchable loads (every other state field
        unchanged) and read each one's slot-0 `active_now`. The hold then
        follows EXACTLY the planner's rules (floor, strict-surplus, pass-2
        lost-export coverage, dwell/quantum, ...).

        The shadow plan is NEVER published — sensors, attributes and forecasts
        keep coming from the honest `result`; it feeds `_latch_hold_ok` only.
        It is computed ONLY when a hold candidate exists (no permanent double
        plan). `plan` is a pure core function of (config, inputs) with no
        coordinator side effects (no learning/diagnostics/persistence writes),
        so the shadow run cannot leak into published state; the cleared states
        are fresh `replace` copies, and the untouched states are shared frozen
        objects the planner only reads.
        """
        candidates = self._latched_hold_candidates()
        if not candidates:
            return {}
        shadow_states = tuple(
            replace(state, saturated_power_w=None)
            if state.load_id in candidates
            else state
            for state in load_states
        )
        shadow_inputs = replace(inputs, load_states=shadow_states)
        shadow_result = await self.hass.async_add_executor_job(
            plan, config, shadow_inputs
        )
        return {
            lp.load_id: lp.active_now
            for lp in shadow_result.load_plans
            if lp.load_id in candidates
        }

    def _latch_hold_ok(
        self,
        subentry_id: str,
        data: dict[str, Any],
        pv_power_w: float,
        shadow_active: dict[str, bool],
    ) -> bool:
        """F10/F11: may a still-latched SWITCHABLE load be held ON right now?

        A load whose F-L7 warning is latched is planned by F5 at its measured
        ~0 W, so the plan never asks for it and the latch (which clears only
        while the load runs BM-commanded and back in band) can never release on
        its own. Instead of probing periodically the executor holds the load ON
        opportunistically — as long as the surplus gates hold it keeps running
        so an emptied tank is used the instant the power is there (operator wish
        2026-07-24: "the dehumidifier can ALWAYS run when the power would be
        there, not only every few hours"). No interval, no auto-stop: it runs
        until a gate drops or the latch releases (then the normal plan resumes
        seamlessly).

        F11 (2026-07-25): the hold fires EXACTLY when the normal planner would
        activate the load now, were it not latched — decided by the shadow plan
        (`_latch_shadow_active`: replan with the F5 saturated override cleared).
        This replaces F10's stricter "slot-0 PV >= load power" heuristic, which
        barred bookings the real planner makes anyway (e.g. an early-morning,
        battery-fed pass-2 run "covered by otherwise-lost export, latest
        feasible slot" at PV ~0 while the SOC stays above the floor). Gates:

        - a power warning is latched;
        - the load is switchable AND has power feedback (a latch implies
          feedback, but be explicit — the hold needs the in-band signal to
          eventually release the latch);
        - no floor guard (SOC above the inverter floor);
        - the inverter recommendation is on;
        - the shadow plan activates the load's slot 0 (the MAIN gate — all of
          the optimizer's own gates, incl. floor / strict-surplus / pass-2
          lost-export coverage, are thereby honoured implicitly);
        - over-power EXTRA protection: a latch can also mean the outlet draws
          MORE than configured (a foreign consumer). The shadow plan then
          replans at the too-small learned/nominal power and may book the load
          although its real draw would need grid import. So when the measured
          feedback clears the standby bar AND sits clearly above the planning
          power (>= LATCH_HOLD_OVERPOWER_FACTOR x), additionally require the
          slot-0 forecast PV to cover the real draw (`pv_power_w >= raw`) — the
          SOLE remaining role of `pv_power_w`, no longer the main gate.

        The min_off dwell is applied by the caller's ON path, so this method
        does not re-check it.
        """
        if not self._load_power_warning.get(subentry_id, False):
            return False
        if not data.get(CONF_LOAD_CONTROL_SWITCH) or not data.get(
            CONF_LOAD_POWER_ENTITY
        ):
            return False
        if self._floor_guard_active:
            return False
        if not self._inverter_recommendation:
            return False
        # Main gate: the normal planner would activate this load now (shadow
        # plan with the F5 override cleared), so the hold routes it exactly
        # where a legitimate surplus run would go — never grid-fed.
        if not shadow_active.get(subentry_id, False):
            return False
        # Over-power extra protection (foreign consumer): the shadow plan
        # replanned at the too-small learned/nominal power; if the real draw is
        # clearly above that, gate it on PV covering the real draw as well.
        planning_power = self._load_learned_power_w.get(subentry_id) or float(
            data.get(CONF_LOAD_POWER_W, 0.0)
        )
        raw = self._read_float(data[CONF_LOAD_POWER_ENTITY])
        if (
            raw is not None
            and raw >= self._load_standby_bar(data)
            and raw > planning_power * LATCH_HOLD_OVERPOWER_FACTOR
        ):
            return pv_power_w >= raw
        return True

    async def _apply_load_switching(
        self,
        result,
        now: datetime,
        slot_durations: tuple[float, ...] | None = None,
        pv_power_w: float = 0.0,
        shadow_active: dict[str, bool] | None = None,
    ) -> None:
        """Evaluate controlled loads and start switching where needed.

        While the G4 floor guard is active (SOC at the inverter floor or
        inverter recommendation off, `_update_floor_guard`), every controlled
        load is forced OFF dwell-exempt and nothing switches ON — a surplus
        load must never run grid-fed (operator rule 2026-07-18). The D-A8
        stage-2 stale-data shed latch (`_stale_shed_active`) forces the same
        OFF semantics: without valid input data no load may (re-)start.

        F10/F11 latch opportunistic hold: a still-latched switchable load (F5
        plans it at ~0 W, so the plan never asks for it) is held ON for as long
        as the surplus gates hold. `shadow_active` is the F11 shadow plan's
        slot-0 `active_now` per latched switchable load (`_latch_shadow_active`);
        `_latch_hold_ok` fires the hold exactly when that plan would run the
        load now, plus the `pv_power_w` over-power guard. It runs until a gate
        drops (normal min_runtime-dwelled stop; G4 immediate) or the latch
        releases (the normal plan then resumes seamlessly).
        """
        if shadow_active is None:
            shadow_active = {}
        if self._load_switch_task is not None and not self._load_switch_task.done():
            return

        floor_guard = bool(self._floor_guard_active)
        # D-A8 stage 2: while the stale-data shed latch is active, every
        # controlled load is forced OFF dwell-exempt and nothing switches ON
        # (the G4 semantics). Normally unreachable — a successful update
        # releases the latch BEFORE this runs — but a latch restored from
        # persistence or set after this cycle's data was read must never see
        # a re-ON without valid data.
        force_off = floor_guard or self._stale_shed_active
        plans_by_id = {lp.load_id: lp for lp in result.load_plans}
        cascade_managed = self.cascade_manager.managed_load_ids()
        actions: list[tuple[Any, ...]] = []
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            plan = plans_by_id.get(subentry_id)
            if subentry_id in cascade_managed or (
                plan is not None and getattr(plan, "managed_by_cascade", False)
            ):
                # F-CASCADE-STORAGE: the central manager is the sole actor
                # owner. Compatibility recommendation entities remain present
                # but publish OFF with a managed_by_cascade marker.
                self._load_charging_active[subentry_id] = False
                self._load_run_deadline.pop(subentry_id, None)
                self._cancel_off_timer(subentry_id)
                continue
            if not data.get(CONF_LOAD_CONTROL_SWITCH):
                # Recommendation-only load: no switch to drive, but F-SUBHOUR R12
                # still needs a sub-hour cap so the published `active` (which the
                # operator's own automation follows) flips off at the deadline.
                self._maintain_recommendation_deadline(
                    subentry_id, data, plan, now, slot_durations
                )
                continue
            current = self._charging_is_active(data)
            if current is None:
                # Entity dropout: keep the last known charging state.
                current = self._load_charging_active.get(subentry_id, False)
            self._load_charging_active[subentry_id] = current
            if self._load_power_calibration_id == subentry_id:
                # Explicit operator ownership: the bounded task below samples
                # this load outside the plan (and may knowingly buy grid power).
                # Every other load continues through the normal executor.
                self._load_run_deadline.pop(subentry_id, None)
                self._cancel_off_timer(subentry_id)
                continue
            calibration_release = subentry_id in self._load_power_calibration_release
            active_now = bool(plan and plan.active_now)
            # F-PREDRAIN-BLOCK stability gate (R7): a pass-3 slot-0 only
            # starts an OFF load after the block held stable (plans + time
            # floor, see _update_predrain_block_evidence); a RUNNING load
            # keeps its plan flag — the gate never cuts a run mid-block, the
            # plan itself retracts the recommendation when the block vanishes.
            slot0_pass = (
                next(
                    (
                        pass_no
                        for start, _count, pass_no, _wh in getattr(
                            plan, "allocations", ()
                        )
                        if start == 0
                    ),
                    None,
                )
                if plan
                else None
            )
            if active_now and not current and slot0_pass == 3:
                active_now = subentry_id in self._predrain_block_stable
            # F-SUBHOUR (approach A) + F-RESIDUAL-TOPUP R7: once a load's frozen
            # sub-hour run deadline passes, force it OFF even if the plan still
            # wants it on — the booked partial-hour energy has been delivered.
            # Energy-limited loads are included as an UPPER CAP over their primary
            # level-driven target-SOC stop (R8): the fossibot integration serves
            # stale cached SOC with fresh timestamps, which would else hold the
            # plan active for the whole slot hour and deliver real_power × 1 h
            # against ~150 Wh of validated energy. The min_off dwell then blocks
            # an immediate re-on (duty-cycling).
            deadline = self._load_run_deadline.get(subentry_id)
            # V9a: the switch-action reason, derived from the anlass at the
            # decision point (the one INFO line the executor logs per confirmed
            # switch). Refined below for target-SOC / flicker-continuation.
            # F10 latch opportunistic hold: a latched load (F5 books 0 W, so the
            # plan never asks for it) is held ON while the surplus gates hold —
            # precedence over the deadline/quantum logic (but never over the G4
            # floor guard). A stale sub-hour deadline from an earlier normal run
            # (that then latched) is cleared so the held run is not force-offed;
            # the run ends only on a gate loss or the latch release.
            hold = self._latch_hold_ok(subentry_id, data, pv_power_w, shadow_active)
            if force_off:
                # G4 / stale-data shed: unsupervised operation is forbidden
                # — force OFF, block ON.
                desired = False
                reason = "G4 floor guard" if floor_guard else "stale-data load shed"
            elif hold:
                desired = True
                reason = "latch hold"
                self._load_latch_hold.add(subentry_id)
                if subentry_id in self._load_run_deadline:
                    self._load_run_deadline.pop(subentry_id, None)
                    self._cancel_off_timer(subentry_id)
            elif current and deadline is not None and now >= deadline:
                # F-SEAMLESS-RUNS: THIS refresh's plan was recomputed with
                # fresh remaining/SOC data BEFORE switching runs. If it
                # re-booked a contiguous run starting now and the load is
                # extension-eligible, continue seamlessly — no OFF/ON relay
                # cycle, no compressor restart, no min_off pause. min_off is
                # thereby armed only by DELIBERATE deactivations (plan-off,
                # target stop, floor guard, deadline without re-booking) —
                # the operator's min_off semantics (2026-07-18).
                run_h = plan.active_run_hours(slot_durations) if plan else 0.0
                if (
                    active_now
                    and run_h > 0.0
                    and self._seamless_extension_ok(subentry_id, data)
                ):
                    off_at = now + timedelta(minutes=max(1, round(run_h * 60.0)))
                    self._load_run_deadline[subentry_id] = off_at
                    self._arm_off_timer(subentry_id, off_at)
                    # Persist the extended deadline in THIS cycle so a
                    # restart right after the boundary restores it.
                    self._save_persistent_state()
                    _LOGGER.info(
                        "Load %s: plan re-booked %.2f h at the run deadline —"
                        " extending seamlessly to %s",
                        subentry.title,
                        run_h,
                        off_at,
                    )
                    continue  # no OFF, no dwell stamp
                desired = False
                reason = "quantum end"
            else:
                desired = active_now
                reason = "plan slot" if active_now else "plan off"
            if desired == current:
                if calibration_release:
                    if desired and plan is not None:
                        # The fresh plan keeps the already-running probe load:
                        # take it over seamlessly and restore the usual frozen
                        # run deadline that the calibration ON deliberately did
                        # not create.
                        run_h = plan.active_run_hours(slot_durations)
                        if run_h > 0.0:
                            off_min = max(
                                int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)),
                                round(run_h * 60.0),
                            )
                            off_at = now + timedelta(minutes=off_min)
                            self._load_run_deadline[subentry_id] = off_at
                            self._arm_off_timer(subentry_id, off_at)
                    self._complete_power_calibration_release(subentry_id)
                # Orphan sweep (7-day live audit 2026-08-04): a gate left ON
                # while the load is NOT charging and shall not charge is
                # unreachable for every other path (desired == current here),
                # so without this sweep it would stay on forever — the live
                # gate sat orphaned for 3.4 days. The ON-path rollback in
                # _execute_load_switching prevents NEW orphans; this heals a
                # pre-existing one (and a failed rollback) within a cycle.
                # LOAD_CONTROL.md §3: the gate may only be on while charging,
                # so the OFF is always safe — and dwell-exempt like G1 (the
                # gate switches no load-current path worth protecting). A
                # failing OFF simply re-queues next cycle (bounded log via
                # _switch_entity's ERROR, and the state IS broken then).
                enable_entity = data.get(CONF_LOAD_CHARGE_ENABLE)
                if not desired and enable_entity and self._entity_is_on(enable_entity):
                    actions.append(
                        (
                            subentry_id,
                            dict(data),
                            False,
                            self._entity_is_on(data[CONF_LOAD_CONTROL_SWITCH]),
                            0.0,
                            False,  # a safety cleanup never seeds a flicker window
                            "orphaned enable cleanup",
                        )
                    )
                continue
            # Split dwell (R14): ON->OFF is gated by the minimum ON time
            # (min_runtime), OFF->ON by the minimum OFF time (min_off) — the
            # latter protects compressor loads from short-cycling.
            if current:  # pending switch OFF
                dwell_min = int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30))
                # F8: only a RECOMMENDATION-driven stop (the plan flickered /
                # the deadline expired without a re-booking) is a flicker
                # continuation candidate. A G4 floor-guard or G1 target-SOC
                # stop is deliberate — it keeps the full min_off, and the
                # continuation window must NOT undercut it (a G4 stop resuming
                # within 5 min would else re-run the load grid-fed).
                flicker_eligible = True
                if force_off:
                    # G4 / stale-data shed: the safety OFF is dwell-EXEMPT —
                    # every dwell minute runs the load grid-fed/unsupervised
                    # (the 06:21 incident lost ~10 min × 432 W exactly to
                    # this hold). The confirmed switch still stamps the dwell
                    # timestamp, so min_off fully gates the later re-on
                    # (compressor protection).
                    dwell_min = 0
                    flicker_eligible = False
                elif calibration_release:
                    # Releasing a bounded manual probe is not a plan flicker;
                    # its short duration must not be amplified to min_runtime.
                    dwell_min = 0
                    flicker_eligible = False
                    reason = "power calibration complete"
                elif subentry_id in self._load_latch_hold:
                    # F10: a gate-loss stop that ends a latch hold is NOT a
                    # recommendation-driven flap — it must not seed an F8
                    # flicker continuation (which would waive min_off for a
                    # following ON). Full min_runtime still gates this OFF, and
                    # a later re-hold is normally min_off-gated.
                    flicker_eligible = False
                # F-EXECUTOR-GUARDS G1 (R1-R3): a target-SOC stop of an
                # energy-limited load with a charge-enable gate is dwell-
                # EXEMPT. min_runtime protects relays/compressors from short
                # cycling, but the enable gate switches no load current path
                # mechanically worth protecting (the plug — if switched at
                # all — switches currentless in the ordered OFF branch), while
                # every dwell minute overshoots the target at real power
                # (~250 Wh in 30 min at ~505 W, landing at ~95 % for a 90 %
                # target). Plug-only loads keep the full dwell (the plug relay
                # is exactly what min_runtime protects); an absent SOC reading
                # keeps it too (conservative). The confirmed switch still
                # stamps the dwell timestamp, so min_off fully gates a re-on:
                # a SOC hovering at the target cannot flap the switch (R2).
                if data.get(CONF_LOAD_ENERGY_LIMITED) and data.get(
                    CONF_LOAD_CHARGE_ENABLE
                ):
                    soc = (
                        self._read_float(data[CONF_LOAD_SOC_ENTITY])
                        if data.get(CONF_LOAD_SOC_ENTITY)
                        else None
                    )
                    target = float(data.get(CONF_LOAD_TARGET_SOC, 100.0))
                    if soc is not None and soc >= target:
                        dwell_min = 0
                        flicker_eligible = False
                        if reason == "plan off":
                            reason = "target SOC reached"
            else:  # pending switch ON
                flicker_eligible = False
                dwell_min = int(
                    data.get(
                        CONF_LOAD_MIN_OFF_MIN,
                        data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30),
                    )
                )
                if calibration_release:
                    dwell_min = 0
                    reason = "power calibration plan takeover"
                # F8 flicker continuation: a short plan flicker at a slot/
                # quantum boundary (the recommendation drops for seconds-to-
                # minutes and returns) would else be amplified into a full
                # min_off pause. If the last stop was a recommendation-driven
                # off (flicker_eligible, tracked in _load_last_off) and the
                # recommendation returned within REC_FLICKER_CONTINUATION_MIN,
                # treat this ON as a continuation of the same run and waive
                # min_off — subject to a ping-pong cap so min_off still guards
                # a genuinely flapping plan from short-cycling a compressor.
                off_info = self._load_last_off.get(subentry_id)
                if (
                    off_info is not None
                    and off_info[1]
                    and self._flicker_continuation_ok(subentry_id, now, off_info[0])
                ):
                    dwell_min = 0
                    reason = f"{reason} (flicker continuation)"
            last = self._last_load_switch.get(subentry_id)
            if last is not None and now - last < timedelta(minutes=dwell_min):
                continue
            # The dwell timestamp is stamped only on a CONFIRMED switch inside
            # the executor — not here — so a failed actuation does not consume
            # the dwell window and block an immediate retry (review #11).
            plug_was_on = self._entity_is_on(data[CONF_LOAD_CONTROL_SWITCH])
            # The planned contiguous run (h) lets the ON path freeze a deadline.
            run_h = plan.active_run_hours(slot_durations) if plan else 0.0
            actions.append(
                (
                    subentry_id,
                    dict(data),
                    desired,
                    plug_was_on,
                    run_h,
                    flicker_eligible,
                    reason,
                )
            )

        if actions:
            self._load_switch_task = self.entry.async_create_background_task(
                self.hass,
                self._execute_load_switching(actions, now),
                name="battery_manager_load_switching",
            )

    async def _execute_load_switching(
        self,
        actions: list[tuple[Any, ...]],
        now: datetime | None = None,
    ) -> None:
        if now is None:
            now = dt_util.now()
        async with self._switch_lock:
            # F-CASCADE-STORAGE live incident 2026-09-01: a generic action
            # queued before the cascade pass repeatedly switched B1's Root
            # input OFF while CascadeManager switched it back ON.  Filtering
            # while the action list is built is insufficient because ownership
            # can change before this detached task acquires the actor lock.
            # Re-check at the final mutation boundary so a cascade always has
            # exactly one actor owner.
            cascade_managed = self.cascade_manager.managed_load_ids()
            for action in actions:
                # Tolerate the legacy 4-tuple (no planned run) so any caller
                # that does not carry a sub-hour run just gets no deadline. The
                # 6th element (F8 flicker_eligible, OFF actions only) defaults
                # False — an unmarked OFF is never a flicker continuation seed.
                subentry_id, data, activate, plug_was_on = action[:4]
                run_h = action[4] if len(action) > 4 else 0.0
                flicker_eligible = action[5] if len(action) > 5 else False
                # V9a: the switch reason (7th element). A legacy short tuple
                # (test/back-compat) falls back to a generic anlass string.
                reason = (
                    action[6]
                    if len(action) > 6
                    else ("plan slot" if activate else "plan off")
                )
                bypass_guards = bool(action[7]) if len(action) > 7 else False
                if subentry_id in cascade_managed:
                    _LOGGER.info(
                        "Dropping queued generic switch action for cascade-managed"
                        " load %s",
                        subentry_id,
                    )
                    continue
                plug = data[CONF_LOAD_CONTROL_SWITCH]
                enable = data.get(CONF_LOAD_CHARGE_ENABLE)
                subentry = self.entry.subentries.get(subentry_id)
                label = subentry.title if subentry else subentry_id
                if (
                    activate
                    and not bypass_guards
                    and (self._floor_guard_active or self._stale_shed_active)
                ):
                    # G4 floor guard / D-A8 stale shed, in-flight race: the
                    # guard may have tripped AFTER this ON was queued (a
                    # debounced SOC refresh returns early while this task is
                    # still running). A queued ON must never fire at/below
                    # the floor or into an active data-loss shed — that is
                    # exactly the unsupervised start both guards exist to
                    # prevent.
                    _LOGGER.info(
                        "Floor guard or stale shed active: dropping queued"
                        " switch-ON for %s",
                        label,
                    )
                    continue
                if activate:
                    if enable and not await self._switch_entity(enable, True):
                        continue
                    if not plug_was_on:
                        if not await self._switch_entity(plug, True):
                            # Rollback (7-day live audit 2026-08-04): the gate
                            # confirmed ON but the plug did not (BLE-RPC
                            # failure) — without this OFF the gate stays
                            # orphaned ON FOREVER: charging never becomes
                            # active, so every later cycle sees desired ==
                            # current and no path ever switches the gate off
                            # (live it sat ON for 3.4 days, bypassing the
                            # legacy automation's 20 % firmware floor). That
                            # violates LOAD_CONTROL.md §3: the gate may only
                            # be on while charging. The G4/shed guard drop
                            # above runs BEFORE the enable ON, so it never
                            # needs this rollback.
                            if enable:
                                if await self._switch_entity(enable, False):
                                    _LOGGER.warning(
                                        "Load %s: plug ON failed after the"
                                        " charge-enable gate confirmed ON —"
                                        " rolled the gate back OFF",
                                        label,
                                    )
                                else:
                                    # The gate stays physically on; the
                                    # orphan sweep in _apply_load_switching
                                    # retries every cycle until it clears.
                                    _LOGGER.warning(
                                        "Load %s: plug ON failed and the"
                                        " charge-enable rollback OFF failed"
                                        " too — the gate stays ON for now",
                                        label,
                                    )
                            continue
                        # We switched the plug on for charging: ownership
                        # allows the 'auto' policy to switch it off again.
                        self._load_plug_owned[subentry_id] = True
                    self._load_charging_active[subentry_id] = True
                    self._last_load_switch[subentry_id] = now
                    # F8: a fresh run started — close the previous off episode
                    # so a later stop opens a new flicker window from scratch.
                    self._load_last_off.pop(subentry_id, None)
                    # F-SUBHOUR (approach A) + F-RESIDUAL-TOPUP R7: freeze the
                    # planned contiguous run and arm an active OFF at
                    # run_start + max(min_runtime, run_h) so a sub-hour booking is
                    # delivered exactly (no ~250 Wh over-run). Energy-limited loads
                    # are capped the same way — the deadline is an UPPER bound over
                    # their primary level-driven target-SOC stop, so a stale
                    # load-SOC sensor (R8) cannot stretch a ~150 Wh top-up into a
                    # full real_power × 1 h night charge. For a gate-stop final
                    # quantum SHORTER than min_runtime (F-GATE-TOPUP R5) the cap
                    # is deliberately LONGER than the booked run: it stays the
                    # stale-SOC upper bound only, while G1's dwell-exempt target
                    # stop is the primary stop that ends the run at `rem`.
                    if run_h > 0.0:
                        off_min = max(
                            int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)),
                            round(run_h * 60.0),
                        )
                        off_at = now + timedelta(minutes=off_min)
                        self._load_run_deadline[subentry_id] = off_at
                        self._arm_off_timer(subentry_id, off_at)
                    else:
                        self._load_run_deadline.pop(subentry_id, None)
                        self._cancel_off_timer(subentry_id)
                    # V9a: exactly one INFO line per confirmed switch action,
                    # carrying the anlass derived at the decision point.
                    _LOGGER.info("Load %s -> ON (%s)", label, reason)
                    if subentry_id in self._load_power_calibration_release:
                        self._complete_power_calibration_release(subentry_id)
                else:
                    policy = data.get(CONF_LOAD_INPUT_OFF_POLICY, INPUT_OFF_POLICY_AUTO)
                    if not enable and policy == INPUT_OFF_POLICY_KEEP:
                        # Misconfiguration (blocked by the flow, but be safe):
                        # nothing can stop the charging in this combination.
                        _LOGGER.warning(
                            "Load %s: policy 'keep_on' without a charge-enable"
                            " entity cannot stop charging",
                            label,
                        )
                        continue
                    if enable and not await self._switch_entity(enable, False):
                        # Charge-enable did not confirm off: charging is not
                        # actually stopped — keep state and retry next cycle.
                        continue
                    owned = self._load_plug_owned.get(subentry_id, False)
                    turn_plug_off = policy == INPUT_OFF_POLICY_ALWAYS or (
                        policy == INPUT_OFF_POLICY_AUTO and owned
                    )
                    if not enable and policy != INPUT_OFF_POLICY_KEEP:
                        # Without a charge-enable gate, stopping charging is
                        # only possible by switching the input off.
                        turn_plug_off = True
                    if turn_plug_off and not await self._switch_entity(plug, False):
                        # Turn-off failed: keep ownership so the plug is never
                        # recorded as not-ours while physically ON. Without a
                        # charge-enable gate charging is still active, so the
                        # next cycle re-attempts the off; with a gate the gate
                        # already stopped charging and the next charge cycle's
                        # stop cleans the plug up (review #3).
                        continue
                    self._load_plug_owned[subentry_id] = False
                    self._load_charging_active[subentry_id] = False
                    self._last_load_switch[subentry_id] = now
                    # F10: the latch-hold run ended (a gate dropped) — clear its
                    # marker so a later hold re-arms the classification fresh.
                    self._load_latch_hold.discard(subentry_id)
                    # F8: record this confirmed OFF and whether it is a flicker
                    # continuation candidate (a recommendation stop, not a
                    # G4/G1 safety stop) so a re-on within the window can waive
                    # min_off.
                    self._load_last_off[subentry_id] = (now, flicker_eligible)
                    # F-SUBHOUR: run finished — clear the frozen deadline + timer.
                    self._load_run_deadline.pop(subentry_id, None)
                    self._cancel_off_timer(subentry_id)
                    # V9a: exactly one INFO line per confirmed switch action.
                    _LOGGER.info(
                        "Load %s -> OFF (%s; input %s)",
                        label,
                        reason,
                        "off" if turn_plug_off else "stays on",
                    )
                    if subentry_id in self._load_power_calibration_release:
                        self._complete_power_calibration_release(subentry_id)
        self._save_persistent_state()
        if self.data:
            plans = self.data.get("load_plans") or {}
            for load_id, active in self._load_charging_active.items():
                if load_id in plans:
                    plans[load_id]["charging_active"] = active
            self.async_update_listeners()
        if self._floor_guard_active:
            # G4, in-flight race (part 2): a refresh that tripped the guard
            # while this task was running returned early and could not queue
            # its forced OFFs. Re-run the cycle now so any load that this
            # task just switched on (or that is still running) is forced off
            # immediately instead of waiting for the next poll.
            await self.async_request_refresh()

    async def _execute_stale_load_shed(self) -> None:
        """D-A8 stage 2 fail-safe: force every controlled surplus load OFF
        after STALE_LOAD_SHED_HOURS of continuous data loss.

        Mirrors the normal executor's OFF branch (charge-enable first, then
        the plug per input_off_policy + ownership) with two deliberate
        deviations: NO dwell (a fail-safe must not wait out a min_runtime —
        the same argument as the G4 floor guard's dwell exemption: every
        dwell minute runs the load unsupervised) and the charge-enable gate
        is switched off even under 'keep_on' (that policy exists so the
        DEVICE keeps mains for passthrough; the gate switches no load
        current path, and stopping the energy flow takes precedence while
        flying blind — the PLUG still follows the policy, so a keep_on
        device keeps its mains). Runs ONCE per outage episode (the latch is
        set before this task starts); a switch that fails keeps its state
        and ownership and is only retried by a later shed (after a restart
        or a recovery + new outage), never on a timer.
        """
        now = dt_util.now()
        # Cascade actors have stricter ordering than independent loads: terminal
        # first, outputs leaf-to-root, gates, then Root.  Let the sole owner run
        # that complete sequence before the generic load loop, and exclude its
        # members below so two executors never race the same switches.
        await self.cascade_manager.async_safety_off_active("stale-data load shed", now)
        cascade_managed = self.cascade_manager.managed_load_ids()
        async with self._switch_lock:
            for subentry_id, subentry in self.entry.subentries.items():
                if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                    continue
                if subentry_id in cascade_managed:
                    continue
                data = subentry.data
                plug = data.get(CONF_LOAD_CONTROL_SWITCH)
                if not plug:
                    continue  # recommendation-only load: nothing to actuate
                enable = data.get(CONF_LOAD_CHARGE_ENABLE)
                policy = data.get(CONF_LOAD_INPUT_OFF_POLICY, INPUT_OFF_POLICY_AUTO)
                if not enable and policy == INPUT_OFF_POLICY_KEEP:
                    # Same misconfiguration guard as the normal OFF path:
                    # nothing here can stop the charging.
                    _LOGGER.warning(
                        "Load %s: policy 'keep_on' without a charge-enable"
                        " entity cannot stop charging (stale-data load shed)",
                        subentry.title,
                    )
                    continue
                if enable and not await self._switch_entity(enable, False):
                    # The gate did not confirm off: charging is not actually
                    # stopped — keep the state (and the plug ownership) for
                    # the next shed trigger.
                    continue
                owned = self._load_plug_owned.get(subentry_id, False)
                turn_plug_off = policy == INPUT_OFF_POLICY_ALWAYS or (
                    policy == INPUT_OFF_POLICY_AUTO and owned
                )
                if not enable and policy != INPUT_OFF_POLICY_KEEP:
                    # Without a charge-enable gate, stopping charging is
                    # only possible by switching the input off.
                    turn_plug_off = True
                if turn_plug_off and not await self._switch_entity(plug, False):
                    # Keep ownership (mirrors the normal OFF path): the plug
                    # is physically ON and was put there by us.
                    continue
                self._load_plug_owned[subentry_id] = False
                self._load_charging_active[subentry_id] = False
                # Stamp the dwell timestamp like every confirmed OFF, so a
                # post-recovery re-ON is still min_off-gated — and record the
                # stop as flicker-INELIGIBLE (a safety stop, like G4) so the
                # F8 continuation cannot waive that min_off.
                self._last_load_switch[subentry_id] = now
                self._load_last_off[subentry_id] = (now, False)
                self._load_latch_hold.discard(subentry_id)
                self._load_run_deadline.pop(subentry_id, None)
                self._cancel_off_timer(subentry_id)
                _LOGGER.info(
                    "Load %s -> OFF (stale-data load shed; input %s)",
                    subentry.title,
                    "off" if turn_plug_off else "stays on",
                )
        self._save_persistent_state()
        if self.data:
            # Keep the (stale) published charging flags honest until the
            # recovery update replaces the whole data dict.
            plans = self.data.get("load_plans") or {}
            for load_id, active in self._load_charging_active.items():
                if load_id in plans:
                    plans[load_id]["charging_active"] = active
            self.async_update_listeners()

    # ------------------------------------------------------------------
    # F-FEEDIN early grid feed-in executor (docs/F-FEEDIN.md)
    # ------------------------------------------------------------------

    def _feedin_entities(self) -> tuple[str, str] | None:
        """(setpoint, battery_power) entity ids when the feed-in wiring is
        configured — independent of the enable TOGGLE, so disabling the
        feature (or the runtime switch) can still write the one-shot 0 in
        auto mode (F-FEEDIN fail-safe)."""
        setpoint = self.raw_config.get(CONF_FEEDIN_SETPOINT_ENTITY)
        battery = self.raw_config.get(CONF_FEEDIN_BATTERY_POWER_ENTITY)
        if setpoint and battery:
            return setpoint, battery
        return None

    def _feedin_read_power_w(self) -> float | None:
        """Current feed-in power at the setpoint entity in POSITIVE watts of
        export (the entity holds the negative-export convention; the sign
        flips at this boundary — see FEEDIN_SETPOINT_EXPORT_SIGN). None when
        the entity is missing/unavailable/non-numeric: no verdict possible."""
        entities = self._feedin_entities()
        if entities is None:
            return None
        state = self.hass.states.get(entities[0])
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return FEEDIN_SETPOINT_EXPORT_SIGN * float(state.state)
        except TypeError, ValueError:
            return None

    def _feedin_tick(self, now: datetime) -> float:
        """Integrate the applied setpoint into today's delivered feed-in energy
        and return the Wh delivered so far today (drives the upward-trim cap).

        Persisted since F-REALIZED-SURPLUS: `_update_realized_surplus` bridges
        the day value into the `realized` store block and startup restores it,
        so a restart keeps the day's total (the tick cursor re-arms at boot —
        the downtime gap itself is still never credited). Before that a
        restart re-integrated from 0, which merely WIDENED the upward cap by
        the forgotten amount — benign either way, since the day's total export
        is invariant (docs/F-FEEDIN.md)."""
        applied_w = self._feedin_last_written_w or 0.0
        today = now.date()
        state = self._feedin_delivered
        if state is None or state[0] != today:
            self._feedin_delivered = (today, 0.0, now)
            return 0.0
        day, wh, tick = state
        wh += applied_w * (now - tick).total_seconds() / 3600.0
        self._feedin_delivered = (day, wh, now)
        return wh

    def _update_feedin_mode(self, now: datetime) -> None:
        """Manual-override detection for the feed-in setpoint (F-FEEDIN R7;
        F-N2 pattern, _update_support_modes).

        A setpoint that differs from the value WE last wrote — outside the
        ~1-cycle grace after our own write — means the operator took over the
        external controller's input_number: the feature enters MANUAL mode
        until the next midnight (persisted) and keeps hands off, including the
        trim. On startup the actual value is ADOPTED as the baseline; if it
        differs from the persisted own value, that change happened while HA
        was down and counts as an override too — after a reboot "changed, but
        not by us" must stay distinguishable from "unchanged since our write".
        """
        if self._feedin_entities() is None:
            return
        if self._feedin_task is not None and not self._feedin_task.done():
            return  # our own write is in flight: no verdict possible
        actual = self._feedin_read_power_w()
        if actual is None:
            return  # unavailable/unknown: no verdict
        if not self._feedin_adopted:
            self._feedin_adopted = True
            last = self._feedin_last_written_w
            if last is None:
                # Never wrote (fresh setup): adopt silently as the baseline.
                self._feedin_last_written_w = actual
                self._save_persistent_state()
            elif abs(actual - last) > 1.0:
                # 1 W tolerance: input_number round-trips our rounded writes.
                self._feedin_last_written_w = actual
                self._feedin_enter_manual(
                    now, "changed while Home Assistant was offline"
                )
            return
        if self._feedin_manual_until is not None:
            if now.date() < self._feedin_manual_until:
                return  # manual mode: hands off
            # Midnight reset: re-ADOPT whatever the operator left as the new
            # baseline — judging it as a fresh override would re-enter manual
            # immediately and the feature could never resume.
            self._feedin_manual_until = None
            self._feedin_last_written_w = actual
            self._feedin_last_write_at = None
            self._save_persistent_state()
            _LOGGER.info(
                "Feed-in manual mode ended (midnight reset) — automatic control resumes"
            )
            return
        if self._feedin_last_written_w is None:
            self._feedin_last_written_w = actual
            self._save_persistent_state()
            return
        if abs(actual - self._feedin_last_written_w) <= 1.0:
            return
        if (
            self._feedin_last_write_at is not None
            and now - self._feedin_last_write_at
            < timedelta(seconds=FEEDIN_MANUAL_GRACE_S)
            and self._feedin_prev_written_w is not None
            and abs(actual - self._feedin_prev_written_w) <= 1.0
        ):
            # Propagation lag: the entity still shows the value we wrote BEFORE
            # the last write. Any OTHER value is an operator action — the grace
            # must not blanket-excuse every difference, because the trim
            # refreshes the write timestamp every few seconds and would then
            # mask an override for as long as it runs (review 2026-08-03).
            return
        self._feedin_enter_manual(now, f"setpoint externally set to {actual:.0f} W")

    def _feedin_enter_manual(self, now: datetime, reason: str) -> None:
        """Enter manual mode until the next midnight (F-FEEDIN R7): the stored
        date is EXCLUSIVE, so automatic control resumes at 00:00 tomorrow."""
        self._feedin_manual_until = now.date() + timedelta(days=1)
        self._save_persistent_state()
        _LOGGER.info(
            "Feed-in setpoint was changed externally (%s) — manual mode until"
            " midnight, automatic control paused (F-FEEDIN)",
            reason,
        )

    async def _feedin_release_orphan(self) -> None:
        """Zero a setpoint entity we still own but no longer control.

        Clearing `feedin_setpoint_entity` (or the battery-power entity, which
        `_feedin_entities` also requires) in the options flow used to strand the
        last exported value: after the reload `_apply_feedin` returned before
        it could write the documented one-shot 0, so the external controller
        kept exporting forever — with the entity untracked and no entity of
        ours left to reveal it (review 2026-08-03). The entity we last wrote a
        non-zero setpoint to is therefore persisted, and released here as soon
        as it is no longer the configured one.
        """
        owned = self._feedin_owned_entity
        if owned is None or owned == (self.raw_config.get(CONF_FEEDIN_SETPOINT_ENTITY)):
            return
        if self.feedin_manual():
            # The operator took the setpoint over before the unwiring — it is
            # theirs, exactly as in the normal path.
            self._feedin_owned_entity = None
            self._save_persistent_state()
            return
        if self.hass.states.get(owned) is None:
            # Entity gone with the integration that provided it: nothing to
            # write, just drop the claim.
            self._feedin_owned_entity = None
            self._save_persistent_state()
            return
        async with self._switch_lock:
            if not await self._set_number_value(owned, 0.0):
                return
            _LOGGER.info(
                "Feed-in setpoint -> 0 W (%s no longer wired to the feed-in"
                " feature — releasing it)",
                owned,
            )
        self._feedin_owned_entity = None
        self._feedin_last_written_w = 0.0
        self._save_persistent_state()

    async def _feedin_force_zero(self, reason: str) -> None:
        """Force the setpoint to 0 outside the planning path (F-FEEDIN R12).

        `_apply_feedin` only runs on a SUCCESSFUL cycle, so the fail-safes it
        implements were unreachable exactly when they matter: a SOC/forecast
        dropout raises `UpdateFailed` before it, and the export kept running
        unsupervised for the whole outage — draining the battery once PV fell
        below the setpoint (review 2026-08-03). This is the same shutdown, but
        reachable from the failure path and from the runtime switch, which
        cannot rely on a refresh that raises.

        Idempotent: once 0 is written, `_feedin_last_written_w` keeps every
        later failing cycle from writing again.
        """
        entities = self._feedin_entities()
        if entities is None:
            return
        if self.feedin_manual():
            return  # R7: the operator owns the setpoint — never write
        if self._feedin_task is not None and not self._feedin_task.done():
            return
        current = self._feedin_read_power_w()
        if current is None:
            current = self._feedin_last_written_w
        if current is not None and abs(current) <= 0.5:
            return  # already at 0 — nothing to shut down
        self._feedin_task = self.entry.async_create_background_task(
            self.hass,
            self._execute_feedin(0.0, reason),
            name="battery_manager_feedin_failsafe",
        )

    async def _apply_feedin(
        self, result, config: SystemConfig, soc: float, now: datetime
    ) -> None:
        """Decision phase of the feed-in executor (F-FEEDIN R9/R10; two-phase
        like _apply_load_switching).

        desired base = the plan's slot-0 feed-in power, gated on: feature
        enabled, runtime switch on and live SOC above `min_soc` (manual mode
        returns earlier and never reaches a write). The G4 floor guard and the
        D-A8 stale shed force 0 dwell-exempt — an unsupervised export must not
        keep running (same argument as the load force-off).

        On top of the plan value the EVENT-DRIVEN trim runs: this pass fires
        on every tracked battery-power update (debounced refresh), not just on
        the 5-min poll. Battery-power convention: positive = charging
        (requirement 1).
        """
        entities = self._feedin_entities()
        if entities is None:
            return
        if self._feedin_task is not None and not self._feedin_task.done():
            return
        if self.feedin_manual():
            return  # R7: the operator owns the setpoint — never write
        params = config.feedin
        current = self._feedin_read_power_w()
        if current is None:
            # Entity dropout: steer against the last value we wrote.
            current = self._feedin_last_written_w
        # Advance the delivered-energy integral (the realized accounting and
        # sensor 7 read it). Its value is deliberately NOT used as a trim cap —
        # see the upward-trim branch.
        self._feedin_tick(now)
        fail = bool(self._floor_guard_active) or self._stale_shed_active
        enabled = bool(self.raw_config.get(CONF_FEEDIN_ENABLED))
        active = (
            enabled
            and self._feedin_switch_on
            and not fail
            and soc > params.min_soc_percent
        )
        plan_w = result.feedin_schedule_w[0] if result.feedin_schedule_w else 0.0
        # R16 stability trace: one sample of the plan's slot-0 value per pass,
        # pruned to the sliding window (cadence-free: debounced battery-power
        # events and the 5-min poll both just append).
        self._feedin_plan_trace.append((now, plan_w))
        trace_cutoff = now - timedelta(seconds=FEEDIN_STABLE_WINDOW_S)
        while self._feedin_plan_trace and self._feedin_plan_trace[0][0] < trace_cutoff:
            self._feedin_plan_trace.popleft()
        trace_values = [v for _, v in self._feedin_plan_trace]
        plan_spread = max(trace_values) - min(trace_values) if trace_values else 0.0
        if plan_spread <= FEEDIN_STABLE_SPREAD_W and self._feedin_brake_engaged:
            self._feedin_brake_engaged = False
            _LOGGER.info(
                "Feed-in plan value stable again (spread %.0f W over %d s) — "
                "setpoint brake released (F-FEEDIN R16)",
                plan_spread,
                FEEDIN_STABLE_WINDOW_S,
            )
        if active:
            base_w = min(plan_w, params.max_w)
            reason = "plan slot"
        else:
            base_w = 0.0
            if fail:
                reason = (
                    "G4 floor guard" if self._floor_guard_active else "stale-data shed"
                )
            elif not enabled:
                reason = "feature disabled"
            elif not self._feedin_switch_on:
                reason = "runtime switch off"
            else:
                reason = (
                    f"SOC {soc:.1f} % at/below the"
                    f" {params.min_soc_percent:.0f} % feed-in floor"
                )

        desired = base_w
        trimmed = False
        battery_power_w = self._read_float(entities[1])
        if active and base_w > 0.0 and battery_power_w is not None:
            cur = current if current is not None else 0.0
            if battery_power_w < -FEEDIN_TRIM_DEADBAND_W:
                # Downward trim (requirement 10): the battery is DISCHARGING —
                # the feed-in is eating into the battery (e.g. a kettle the
                # forecast never saw). Cut the setpoint by the discharge amount
                # IMMEDIATELY and unthrottled: seconds of unwanted discharge,
                # not minutes; an internal input_number tolerates the write
                # rate. Trim writes carry no deadband.
                desired = max(0.0, cur + battery_power_w)
                trimmed = True
                reason = f"trim down (battery {battery_power_w:.0f} W)"
            elif battery_power_w > FEEDIN_TRIM_DEADBAND_W:
                # Upward trim: surplus the plan booked as export is charging
                # the battery instead. Raise the setpoint by the charge amount
                # (hold the battery at ~0 W, work the day's target off faster)
                # — throttled to one raise per 60 s (anti-hunting against the
                # external controller) and capped at min(max_w, remaining
                # target as a rate) so the trim can never overshoot the booked
                # energy.
                last_up = self._feedin_last_upward_at
                if last_up is None or now - last_up >= timedelta(
                    seconds=FEEDIN_UPWARD_MIN_INTERVAL_S
                ):
                    today = now.date().isoformat()
                    # `feedin_by_day_wh` is booked over `inputs.slots`, which
                    # START at the current slot — it is ALREADY the remaining
                    # amount for today. Subtracting the energy delivered
                    # earlier today double-counted it and drove the cap to 0
                    # for the rest of the day, silently disabling the upward
                    # trim exactly when the plan still wanted export (review
                    # 2026-08-03).
                    remaining_wh = result.feedin_by_day_wh.get(today, 0.0)
                    midnight = (now + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    hours_left = max(
                        (midnight - now).total_seconds() / 3600.0, 1.0 / 60.0
                    )
                    rate_cap = min(params.max_w, remaining_wh / hours_left)
                    raised = min(cur + battery_power_w, rate_cap)
                    if raised > cur:
                        desired = raised
                        trimmed = True
                        self._feedin_last_upward_at = now
                        reason = f"trim up (battery +{battery_power_w:.0f} W)"

        reanchored = False
        if current is None:
            write = desired != (self._feedin_last_written_w or 0.0)
        elif trimmed:
            # Trim writes carry no deadband (requirement 10) — but an UNCHANGED
            # value must never be rewritten: a sustained discharge clamps the
            # trim at 0 and re-wrote that same 0 on every pass, one INFO line
            # and one service call per event for the whole episode (review
            # 2026-08-03).
            write = abs(desired - current) > 0.5
        elif (desired > 0.0) != (current > 0.0):
            write = True  # 0 <-> >0 transitions always write
        else:
            # Re-anchor: pull the setpoint back to the plan slot value once the
            # trim increments drifted beyond the re-anchor deadband — the ONLY
            # place the 25 W deadband applies. Throttled to the PLANNING
            # interval: this pass also runs on every debounced battery-power
            # event, so an ungated re-anchor undid each throttled upward trim
            # seconds later and the setpoint oscillated against the external
            # controller for the whole window (review 2026-08-03).
            last_anchor = self._feedin_last_reanchor_at
            write = abs(desired - current) > FEEDIN_REANCHOR_DEADBAND_W and (
                last_anchor is None
                or now - last_anchor
                >= timedelta(seconds=FEEDIN_REANCHOR_MIN_INTERVAL_S)
            )
            reanchored = write
        if not write:
            return
        # R16 stability brake: an UPWARD write (plan slot, 0->>0 transition,
        # upward trim or re-anchor — any source) follows the plan anchor only
        # once the plan held within FEEDIN_STABLE_SPREAD_W across the sliding
        # window; downward writes (incl. ->0 and every fail-safe) are never
        # braked — in doubt the battery charges, not the grid. A braked write
        # returns BEFORE the re-anchor stamp: the write never happened, so no
        # throttle cursor may be consumed by it.
        current_eff = (
            current if current is not None else (self._feedin_last_written_w or 0.0)
        )
        if desired > current_eff and plan_spread > FEEDIN_STABLE_SPREAD_W:
            if not self._feedin_brake_engaged:
                self._feedin_brake_engaged = True
                _LOGGER.info(
                    "Feed-in setpoint held at %.0f W — the plan value is "
                    "unstable (spread %.0f W over %d s, F-FEEDIN R16)",
                    current_eff,
                    plan_spread,
                    FEEDIN_STABLE_WINDOW_S,
                )
            return
        if reanchored:
            self._feedin_last_reanchor_at = now
        self._feedin_task = self.entry.async_create_background_task(
            self.hass,
            self._execute_feedin(desired, reason),
            name="battery_manager_feedin",
        )

    async def _execute_feedin(self, desired_w: float, reason: str) -> None:
        """Actuation phase: write the setpoint under the switch lock, with the
        in-flight guard re-check (mirrors _execute_load_switching)."""
        entities = self._feedin_entities()
        if entities is None:
            return
        async with self._switch_lock:
            if self.feedin_manual():
                # Manual mode entered after this write was queued (F-N2 race):
                # the operator owns the setpoint now — hands off.
                _LOGGER.info(
                    "Feed-in manual mode active: dropping queued setpoint write %.0f W",
                    desired_w,
                )
                return
            if desired_w > 0.0 and (
                self._floor_guard_active or self._stale_shed_active
            ):
                # G4 / D-A8 in-flight race (mirror of the load-switching drop):
                # a queued export must never fire into an active fail-safe.
                _LOGGER.info(
                    "Floor guard or stale shed active: dropping queued feed-in"
                    " write %.0f W",
                    desired_w,
                )
                return
            # Sign convention: negative setpoint = export (const.py
            # FEEDIN_SETPOINT_EXPORT_SIGN) — a 500 W feed-in is written as -500.
            value = round(FEEDIN_SETPOINT_EXPORT_SIGN * desired_w, 1)
            if not await self._set_number_value(entities[0], value):
                return
            self._feedin_prev_written_w = self._feedin_last_written_w
            self._feedin_last_written_w = desired_w
            self._feedin_last_write_at = dt_util.now()
            # Remember WHICH entity currently carries a non-zero setpoint of
            # ours, so a later unwiring can still zero it (see
            # _feedin_release_orphan). Cleared as soon as we wrote 0.
            self._feedin_owned_entity = entities[0] if desired_w > 0.0 else None
            # V9a pattern: exactly one INFO line per confirmed write, carrying
            # the reason derived at the decision point.
            _LOGGER.info("Feed-in setpoint -> %.0f W (%s)", desired_w, reason)
        self._save_persistent_state()

    # ------------------------------------------------------------------
    # Entity listeners
    # ------------------------------------------------------------------

    def _setup_entity_listeners(self) -> None:
        try:
            self._unsub_state_listener = async_track_state_change_event(
                self.hass, self._tracked_entities(), self._handle_entity_change
            )
            self._listeners_setup = True
        except Exception as err:
            _LOGGER.warning(
                "Failed to set up entity listeners: %s. Relying on polling.", err
            )

    @callback
    def _handle_entity_change(self, event) -> None:
        if not self._listeners_setup:
            return
        if self._debounce_task is not None and not self._debounce_task.done():
            # A window is already armed: absorb this event instead of
            # cancelling and restarting the sleep. Restarting starves the
            # debounce outright for any input that updates faster than
            # DEBOUNCE_SECONDS — the F-FEEDIN battery-power sensor publishes
            # every 1-2 s, so the refresh (and with it the event-driven trim
            # that must react in SECONDS) never ran between the 5-min polls
            # (review 2026-08-03). Absorbing keeps the coalescing but bounds
            # the latency at DEBOUNCE_SECONDS.
            return
        self._debounce_task = self.hass.async_create_task(self._debounced_update())

    async def _debounced_update(self) -> None:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await self.async_request_refresh()

    def _arm_off_timer(self, load_id: str, off_at: datetime) -> None:
        """Arm a one-shot timer to force a load OFF at its frozen run deadline.

        The 300 s poll is too coarse to stop a sub-hour run precisely, so a
        point-in-time timer requests a refresh at the deadline; the next
        `_apply_load_switching` pass then sees `now >= deadline` and switches the
        load off (F-SUBHOUR approach A, R8). Re-arming cancels any prior timer.
        """
        self._cancel_off_timer(load_id)

        @callback
        def _fire(_now: datetime) -> None:
            self._load_off_timer.pop(load_id, None)
            self.hass.async_create_task(
                self.async_request_refresh(), "battery_manager_subhour_off"
            )

        self._load_off_timer[load_id] = async_track_point_in_time(
            self.hass, _fire, off_at
        )

    def _cancel_off_timer(self, load_id: str) -> None:
        """Cancel a load's pending force-OFF timer, if any."""
        unsub = self._load_off_timer.pop(load_id, None)
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()

    async def async_cancel_actuation_tasks(self) -> None:
        """Cancel and await any in-flight background actuation task.

        Called on unload BEFORE async_flush_persistent_state so that no detached
        switch/controller task can take the switch lock and mutate the persisted
        support-mode / caused-off state AFTER the flush has captured the payload
        (the flush awaits an executor write, yielding the loop; review #7).
        Cancelling mid-sequence is safe: a make-before-break leaves at worst both
        sources on (never sourceless) and the reload re-reads and heals.
        """
        self._actuation_shutdown = True
        # F-SUBHOUR: drop any pending force-OFF timers before flush/unload so a
        # detached point-in-time callback cannot fire after teardown (R13).
        for load_id in list(self._load_off_timer):
            self._cancel_off_timer(load_id)
        tasks = [
            self._switch_task,
            self._dc48_ctrl_task,
            self._load_switch_task,
            self._dcdc_restore_task,
            self._stale_shed_task,
            self._feedin_task,
            self._load_power_calibration_task,
        ]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        for task in tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    def cleanup(self) -> None:
        """Release entity listeners and cancel pending debounce work."""
        self.cascade_manager.cleanup()
        self.learner.async_unschedule()
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None
        self._listeners_setup = False
        if self._debounce_task:
            self._debounce_task.cancel()
            self._debounce_task = None

    def get_last_hourly_details(self) -> list[dict[str, Any]]:
        if self.data and self.data.get("hourly_details"):
            return list(self.data["hourly_details"])
        return []
