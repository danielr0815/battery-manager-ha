"""Data update coordinator for the Battery Manager integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
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
    CONF_DC24_SHARE_PERCENT,
    CONF_DCDC_EFFICIENCY,
    CONF_DCDC_MAX_CURRENT_A,
    CONF_DCDC_OUTPUT_VOLTAGE_V,
    CONF_DCDC_SWITCH,
    CONF_GATE_SOC_PERCENT,
    CONF_IMPORT_TRADE_RATIO,
    CONF_LOAD_AVAILABILITY_ENTITY,
    CONF_LOAD_BATTERY_TOLERANCE,
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_MIN_OFF_MIN,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_POWER_WARNING_PCT,
    CONF_LOAD_PRIORITY,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
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
    DC48_CTRL_DWELL_OFF_S,
    DC48_CTRL_DWELL_ON_S,
    DC48_CTRL_FAILSAFE_MIN,
    DC48_CTRL_VOLTAGE_MAX,
    DC48_CTRL_VOLTAGE_MIN,
    DEBOUNCE_SECONDS,
    DEFAULT_CONFIG,
    DEFAULT_LOAD_CONFIG,
    DOMAIN,
    IMPORT_TRADE_RATIO_DEFAULT,
    INITIAL_UPDATE_INTERVAL_SECONDS,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_KEEP,
    LOAD_RUNTIME_MIN_W,
    LOAD_RUNTIME_TICK_MAX_S,
    LOAD_SOC_CACHE_MAX_AGE_HOURS,
    MAX_HISTORICAL_FORECAST_AGE_HOURS,
    MAX_HISTORICAL_SOC_AGE_HOURS,
    POWER_WARNING_DWELL_MIN,
    PREDRAIN_PV_CONFIDENCE_DEFAULT,
    PV_FORECAST_MODE_AUTO,
    PV_FORECAST_MODE_DAILY,
    PV_FORECAST_MODE_HOURLY,
    STALE_LOAD_SOC_MIN,
    STANDBY_FRACTION,
    STARTUP_RETRY_ATTEMPTS,
    STORAGE_VERSION,
    STRONG_PV_CUTOFF_W_DEFAULT,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
    UPDATE_INTERVAL_SECONDS,
    UPPER_PV_RESERVE_DEFAULT,
)
from .core import (
    DAY_TYPE_ABSENCE,
    Appliance,
    ApplianceRun,
    BatteryParams,
    ControlParams,
    ConverterParams,
    LoadProfile,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
    aggregate_hours,
    build_slots,
    plan,
    profile_value,
    slot_starts,
)
from .history_profile import ProfileLearner

_LOGGER = logging.getLogger(__name__)

_POWER_EMA_ALPHA = 0.3


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


def _gate_soc(percent: Any) -> float | None:
    """48 V PSU gate SOC; >= 100 % means always open (no gate)."""
    value = float(percent)
    return value if value < 100.0 else None


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
        # entity keeps its own last-good (map, timestamp); the merge reuses a
        # cached entity map within MAX_HISTORICAL_FORECAST_AGE_HOURS.
        self._pv_hourly_by_entity: dict[
            str, tuple[dict[datetime, float], datetime]
        ] = {}
        # FIX-10: state-change guard for the "hourly mode but no wh_period" warning.
        self._pv_hourly_empty_warned = False
        # Per-day PV source label ("hourly"/"two_window") for WP4 sensor exposure.
        self._pv_source_by_day: dict[str, str] = {}
        # FIX-11: last-logged set of night-booked (load, slot-start) pairs, so the
        # F-PREDRAIN night-charge line is emitted only when the booking changes.
        self._night_predrain_logged: frozenset[tuple[str, str]] = frozenset()

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

        # Appliance run tracking and load power smoothing
        self._appliance_started: dict[str, datetime] = {}
        # H1: keys restored from persistence get ONE restart-boundary staleness
        # check in _get_appliance_runs, so a run that finished during downtime
        # cannot pin a genuinely-new run at 0 remaining.
        self._appliance_started_restored: set[str] = set()
        self._load_power_ema: dict[str, float] = {}
        # Learned planning power (F-PLANNER-HONESTY R2): per load the run-max
        # of the accepted-sample EMA — run-max so an end-of-charge taper cannot
        # erode it, EMA so a spike cannot inflate it, and only samples past the
        # v0.6.2 standby bar feed it (the single gate; standby poisoning stays
        # impossible). `_load_run_power_max` tracks the CURRENT run (same
        # lifecycle as `_load_power_ema`); `_load_learned_power_w` is the
        # persisted last-run-wins store an OFF load plans with.
        self._load_run_power_max: dict[str, float] = {}
        self._load_learned_power_w: dict[str, float] = {}
        # Stale-SOC guard (F-EXECUTOR-GUARDS G2): evidence tuple (frozen value,
        # since) while actively charging above the standby bar, and the latch
        # (frozen value, for the log + unlatch compare). In-memory only — a
        # restart re-detects within STALE_LOAD_SOC_MIN minutes.
        self._load_soc_frozen: dict[str, tuple[float, datetime]] = {}
        self._load_soc_stale: dict[str, float] = {}

        # Charging-path control (docs/LOAD_CONTROL.md): SOC cache survives
        # sleeping devices and restarts; plug ownership implements the
        # configurable input-off policy.
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._load_soc_cache: dict[str, float] = {}
        self._load_plug_owned: dict[str, bool] = {}
        self._last_load_switch: dict[str, datetime] = {}
        self._load_charging_active: dict[str, bool] = {}
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
        # Real active-runtime counter per load (v0.7.18): accumulated seconds the
        # load really ran (persisted, so the count survives restarts), plus the
        # last-tick cursor of an in-progress run (NOT persisted — a restored
        # cursor would credit the restart gap; see _update_load_runtime).
        self._load_runtime_seconds: dict[str, float] = {}
        self._load_run_since: dict[str, datetime] = {}

        # Learned consumption profiles (docs/CONSUMPTION_FORECAST.md)
        self.learner = ProfileLearner(hass, entry)

        self._startup_complete = False
        self._successful_updates = 0

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
        data = await self._store.async_load()
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
            # night-charge incident). The power EMA is deliberately NOT
            # persisted — a taper-decayed value (last reading of a finished
            # charge) would otherwise become permanent planning power.
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
            # Only the accumulated total is persisted; the in-progress tick cursor
            # is deliberately NOT — restoring it would credit the whole restart
            # gap (up to the tick cap) as runtime the device may never have run.
            # After a restart the first tick just re-arms the cursor (losing at
            # most the last sub-cycle partial, which is bounded and unobservable).
            "load_runtime_seconds": dict(self._load_runtime_seconds),
            # F-PLANNER-HONESTY R3: the learned planning power (run-max of the
            # accepted-sample EMA) survives restarts — unlike the live EMA
            # (deliberately volatile, see async_load_persistent_state), the
            # learned value is taper-proof by construction.
            "load_learned_power": dict(self._load_learned_power_w),
            # F-SUBHOUR H1: persist the appliance run start so a restart mid-run
            # does not re-latch at `now` and re-inject the full run energy.
            "appliance_started": {
                k: v.isoformat() for k, v in self._appliance_started.items()
            },
            "support_manual": dict(self._support_manual),
            "support_state": dict(self._support_state),
            "dc48_ctrl_caused_off": self._dc48_ctrl_caused_off,
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
        for subentry in self.entry.subentries.values():
            data = subentry.data
            if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
                for key in (
                    CONF_LOAD_SOC_ENTITY,
                    CONF_LOAD_POWER_ENTITY,
                    CONF_LOAD_AVAILABILITY_ENTITY,
                ):
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
                import_trade_ratio=float(
                    cfg.get(CONF_IMPORT_TRADE_RATIO, IMPORT_TRADE_RATIO_DEFAULT)
                ),
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
            loads=tuple(loads),
            appliances=tuple(appliances),
        )

    # ------------------------------------------------------------------
    # Input reading
    # ------------------------------------------------------------------

    def _read_float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _get_soc(self, now: datetime) -> float | None:
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

    def _read_wh_period(self, entity_id: str) -> dict[datetime, float]:
        """Parse an entity's hourly ``wh_period`` attribute into a naive-local
        hour -> Wh map (docs/F-PREDRAIN.md F1).

        Keys are parsed with dt_util.parse_datetime: naive keys are treated as
        LOCAL, aware keys are converted to local and made naive. 15/30-min buckets
        are summed per hour by aggregate_hours. Malformed keys/values are skipped;
        non-finite (NaN/±inf) values are skipped and negative values clamped to 0
        (FIX-9) so a bad forecast attribute can never poison the hourly map.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return {}
        raw = state.attributes.get("wh_period")
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
            except (TypeError, ValueError):
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
                "battery_manager: %d skipped, %d clamped wh_period entries on %s",
                skipped,
                clamped,
                entity_id,
            )
        return aggregate_hours(entries)

    def _get_pv_hourly(self, now: datetime) -> dict[datetime, float] | None:
        """Merged naive-local hourly PV map from the three forecast entities.

        Stale-cache fallback is PER ENTITY (FIX-4): each cycle every entity uses
        its fresh map when non-empty, else its own last-good map within the
        max-age window — so a single unavailable entity no longer overwrites the
        cached full map with a partial read. The per-entity results merge in the
        existing (today, tomorrow, day-after) order; overlapping days are
        last-writer-wins (the Open-Meteo family entities carry identical
        overlapping data, so this is harmless). In "daily" mode the hourly
        attributes are ignored (None) so build_slots uses the two-window model.
        """
        if self._pv_forecast_mode == PV_FORECAST_MODE_DAILY:
            return None
        cfg = self.raw_config
        max_age = timedelta(hours=MAX_HISTORICAL_FORECAST_AGE_HOURS)
        merged: dict[datetime, float] = {}
        for key in (
            CONF_PV_FORECAST_TODAY,
            CONF_PV_FORECAST_TOMORROW,
            CONF_PV_FORECAST_DAY_AFTER,
        ):
            conf_key = cfg[key]
            fresh = self._read_wh_period(conf_key)
            if fresh:
                self._pv_hourly_by_entity[conf_key] = (fresh, now)
                merged.update(fresh)
                continue
            cached = self._pv_hourly_by_entity.get(conf_key)
            if cached is not None and now - cached[1] <= max_age:
                merged.update(cached[0])
            # An entity that never provides wh_period simply contributes nothing.
        self._maybe_warn_hourly_empty(bool(merged))
        return merged if merged else None

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

    def _log_night_predrain(self, result, inputs, config: SystemConfig) -> None:
        """One INFO line when the plan books preemptive night charging.

        A pre-drain "make room" run (pass 2) for a CONTINUOUS load that lands
        OUTSIDE every PV absorption window is the F-PREDRAIN feature's headline
        action (docs/F-PREDRAIN.md F4) — surface it with the load name, the booked
        slot times and the grid import the trade cost. Emitted only when the set of
        night-booked (load, slot-start) pairs CHANGES vs the previous log (FIX-11):
        the plan re-runs every 5 min, so an unchanged night booking would otherwise
        spam an identical line every cycle.
        """
        ends = result.pv_window_ends  # {iso date -> last strong-PV hour}
        if not result.load_plans:
            self._night_predrain_logged = frozenset()
            return
        cutoff = config.control.strong_pv_cutoff_w
        # First strong-PV slot index per day. The end-hour override only caps the
        # window END, so the start comes straight from the forecast shape; the
        # authoritative capped end is taken from the plan result.
        first_idx: dict[str, int] = {}
        for slot in inputs.slots:
            if slot.duration > 0.0 and slot.pv_wh / slot.duration >= cutoff:
                first_idx.setdefault(slot.start.date().isoformat(), slot.index)

        def _in_window(slot) -> bool:
            day = slot.start.date().isoformat()
            end_hour = ends.get(day)
            if end_hour is None or day not in first_idx:
                return False
            return slot.index >= first_idx[day] and slot.hour_of_day <= end_hour

        booked: list[str] = []
        pairs: set[tuple[str, str]] = set()
        for load_plan, load in zip(result.load_plans, config.loads, strict=True):
            if load.energy_limited:
                continue  # pre-drain is continuous-loads only (F-PREDRAIN L5)
            night = [
                inputs.slots[j]
                for start, count, pass_no, _wh in load_plan.allocations
                if pass_no == 2
                for j in range(start, start + count)
                if j < len(inputs.slots) and not _in_window(inputs.slots[j])
            ]
            if night:
                times = ", ".join(s.start.strftime("%Y-%m-%d %H:%M") for s in night)
                booked.append(f"{load.name} @ {times}")
                pairs.update((load.load_id, s.start.isoformat()) for s in night)
        current = frozenset(pairs)
        if not current:
            self._night_predrain_logged = frozenset()
            return
        if current == self._night_predrain_logged:
            return  # identical booking already logged — do not spam every cycle
        self._night_predrain_logged = current
        _LOGGER.info(
            "F-PREDRAIN: preemptive night charging booked for %s"
            " (import traded %.1f Wh)",
            "; ".join(booked),
            result.import_trade_used_wh,
        )

    def _update_power_warnings(self, result, now: datetime) -> None:
        """Per-load power-deviation warning (operator requirement F-L7).

        While a load runs at the integration's request but its real draw
        deviates from the CONFIGURED power by more than the per-load
        percentage for POWER_WARNING_DWELL_MIN sustained minutes, the
        load's warning binary sensor turns on — full water tank (draw near
        0 W), wrong nominal power or a foreign consumer on the measured
        outlet. Short defrost pauses reset the timer before the dwell
        elapses. A missing reading freezes the current state.
        """
        active_by_id = {lp.load_id: lp.active_now for lp in result.load_plans}
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            # Subentries created before v0.6.3 lack the key: default 50 %.
            pct = float(
                data.get(
                    CONF_LOAD_POWER_WARNING_PCT,
                    DEFAULT_LOAD_CONFIG[CONF_LOAD_POWER_WARNING_PCT],
                )
            )
            power_entity = data.get(CONF_LOAD_POWER_ENTITY)
            if pct <= 0 or not power_entity:
                continue  # disabled or nothing to measure
            if subentry_id in self._load_charging_active:
                active = self._load_charging_active[subentry_id]
            else:
                active = active_by_id.get(subentry_id, False)
            if not active:
                self._load_deviation_since.pop(subentry_id, None)
                self._set_power_warning(subentry_id, subentry.title, False)
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
                self._load_deviation_since.pop(subentry_id, None)
                self._set_power_warning(subentry_id, subentry.title, False)
                continue
            since = self._load_deviation_since.setdefault(subentry_id, now)
            self._load_warning_diag[subentry_id]["since"] = since
            if now - since >= timedelta(minutes=POWER_WARNING_DWELL_MIN):
                self._set_power_warning(
                    subentry_id, subentry.title, True, raw=raw, nominal=nominal
                )

    def _set_power_warning(
        self,
        subentry_id: str,
        title: str,
        on: bool,
        raw: float | None = None,
        nominal: float | None = None,
    ) -> None:
        if self._load_power_warning.get(subentry_id, False) == on:
            return
        self._load_power_warning[subentry_id] = on
        if on:
            _LOGGER.warning(
                "Load %s draws %.0f W while %.0f W are configured"
                " (sustained > %d min) — full tank, wrong configured power"
                " or a foreign consumer?",
                title,
                raw,
                nominal,
                POWER_WARNING_DWELL_MIN,
            )
        else:
            _LOGGER.info("Load %s: power warning cleared", title)

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
        return raw >= max(10.0, STANDBY_FRACTION * float(data[CONF_LOAD_POWER_W]))

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
                except (ValueError, TypeError):
                    pass
        if load_id in self._load_charging_active:
            return bool(self._load_charging_active[load_id])
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

    def reset_load_runtime(self, load_id: str) -> None:
        """Reset a load's runtime counter to zero (reset button, v0.7.18). A run
        in progress restarts its cursor from now so only post-reset time counts."""
        self._load_runtime_seconds[load_id] = 0.0
        if load_id in self._load_run_since:
            self._load_run_since[load_id] = dt_util.utcnow()
        self._save_persistent_state()
        self.async_update_listeners()

    def _update_soc_stale(
        self,
        subentry_id: str,
        title: str,
        live_soc: float | None,
        raw_power: float | None,
        sample_bar: float | None,
    ) -> bool:
        """Stale-SOC latch for one load (F-EXECUTOR-GUARDS G2, R5-R7).

        The fossibot integration serves cached SOC values with FRESH
        timestamps, so availability/age checks cannot catch a frozen reading
        and the planner keeps re-booking run after run against a frozen
        `remaining` (v0.8.1's executor cap only bounds a single run). While
        the device DEMONSTRABLY charges — `_load_charging_active` and the raw
        feedback above the v0.6.2 standby bar (the single threshold, reused) —
        a SOC that stays EXACTLY unchanged for STALE_LOAD_SOC_MIN minutes is
        latched as stale. The evidence clock measures continuous charging
        against a frozen value, not wall time: it RESETS when charging stops
        or the sample bar is not met (an end-of-charge taper never
        accumulates false evidence), and loads without a SOC or power entity
        never latch (no evidence, R7). Unlatch: any DIFFERENT live reading,
        charging or not. Latch logs WARNING once, unlatch INFO once
        (change-gated). Returns True while latched."""
        frozen_value = self._load_soc_stale.get(subentry_id)
        if frozen_value is not None:
            if live_soc is not None and live_soc != frozen_value:
                del self._load_soc_stale[subentry_id]
                self._load_soc_frozen.pop(subentry_id, None)
                _LOGGER.info(
                    "Load %s: SOC reports %.1f%% again (was frozen at %.1f%%)"
                    " — stale latch cleared",
                    title,
                    live_soc,
                    frozen_value,
                )
            else:
                return True  # still frozen (or no reading): stay latched
        charging = self._load_charging_active.get(subentry_id, False)
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

    def _get_load_states(self) -> tuple[SurplusLoadState, ...]:
        states = []
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
                min_sample_w = max(
                    10.0, STANDBY_FRACTION * float(data[CONF_LOAD_POWER_W])
                )
                bm_active = self._bm_load_active(subentry_id)
                if raw is not None and raw >= min_sample_w and bm_active:
                    # R2a: the EMA seeds VERBATIM from the run's first accepted
                    # sample (previous = raw), so only samples with a PRIOR
                    # EMA entry may feed the learned run-max — a single
                    # start-up spike must not be learned permanently.
                    ema_seeded = subentry_id in self._load_power_ema
                    previous = self._load_power_ema.get(subentry_id, raw)
                    measured = (
                        _POWER_EMA_ALPHA * raw + (1 - _POWER_EMA_ALPHA) * previous
                    )
                    self._load_power_ema[subentry_id] = measured
                    # F-PLANNER-HONESTY R2: learn the run-max of the accepted-
                    # sample EMA; the store always holds the current run's max
                    # once it has enough accepted samples (last run wins), so
                    # an OFF load later plans at its real power, not nominal.
                    if ema_seeded:
                        run_max = max(
                            self._load_run_power_max.get(subentry_id, measured),
                            measured,
                        )
                        self._load_run_power_max[subentry_id] = run_max
                        if self._load_learned_power_w.get(subentry_id) != run_max:
                            self._load_learned_power_w[subentry_id] = run_max
                            self._save_persistent_state()
                elif subentry_id in self._load_power_ema:
                    if bm_active:
                        # Mid-run feedback gap (v0.5.1): keep planning
                        # with the last smoothed value.
                        measured = self._load_power_ema[subentry_id]
                    else:
                        # Run over (or the device is being used outside the
                        # manager's plan): a taper/standby/foreign value
                        # must not stick as "measured" planning power. The
                        # run-max tracker ends with the run; the LEARNED value
                        # keeps serving (that is its point, R1/R2).
                        del self._load_power_ema[subentry_id]
                        self._load_run_power_max.pop(subentry_id, None)
            # F-EXECUTOR-GUARDS G2 (R6): a stale-latched load is held
            # unavailable — the planner stops re-booking against the frozen
            # `remaining`, and the plan-driven OFF runs through the normal
            # executor path.
            if self._update_soc_stale(
                subentry_id, subentry.title, live_soc, raw, min_sample_w
            ):
                available = False
            states.append(
                SurplusLoadState(
                    load_id=subentry_id,
                    available=available,
                    soc_percent=soc,
                    measured_power_w=measured,
                    learned_power_w=self._load_learned_power_w.get(subentry_id),
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
        energy. During an entity dropout the last (latched) state is held."""
        entity_id = data.get(CONF_APPLIANCE_DETECTION_ENTITY)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return latched  # hold last state during a dropout, do not reset
        try:
            power = float(state.state)
        except (ValueError, TypeError):
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
            if self._appliance_is_running(data, subentry_id in self._appliance_started):
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
        # Runtime counter: accumulate real active minutes every cycle (and on the
        # power-sensor state events that trigger a refresh), before any early-out
        # so it tracks continuously (v0.7.18).
        self._update_load_runtime(now)
        # Manual-override detection first: build_system_config feeds the
        # forced flags into the simulation (F-N2).
        self._update_support_modes()
        soc = self._get_soc(now)
        forecasts = self._get_forecasts(now)

        if soc is None or forecasts is None:
            self._successful_updates = 0
            missing = "SOC" if soc is None else "PV forecasts"
            raise UpdateFailed(f"No valid input data available ({missing})")

        config = self.build_system_config()
        load_states = self._get_load_states()
        appliance_runs = self._get_appliance_runs(now)
        ac_series, dc_series, band, quantiles_active, profile_diag = (
            self._learned_series(now, config, len(forecasts))
        )

        # Hourly PV forecast (docs/F-PREDRAIN.md F1): None/empty -> two-window.
        pv_hourly = self._get_pv_hourly(now)
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
        self._log_night_predrain(result, inputs, config)

        threshold = self._apply_threshold_inertia(result.threshold_percent, config)
        recommendation = self._apply_hysteresis(soc, threshold, config, now)
        await self._apply_support_switching(result, config, now)
        self._run_dc48_controller(now)
        await self._apply_load_switching(
            result, now, tuple(s.duration for s in inputs.slots)
        )
        self._update_power_warnings(result, now)
        self._update_gate_calibration(config, soc)

        self._successful_updates += 1
        if not self._startup_complete and (
            self._successful_updates >= 2
            or self._successful_updates >= STARTUP_RETRY_ATTEMPTS
        ):
            self._startup_complete = True
            self.update_interval = timedelta(seconds=UPDATE_INTERVAL_SECONDS)

        load_plans: dict[str, dict[str, Any]] = {}
        for load_plan, load in zip(result.load_plans, config.loads, strict=True):
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
                "planned_hours": sum(load_plan.schedule),
                "planned_energy_kwh": round(load_plan.planned_energy_wh / 1000.0, 3),
                "charging_active": self._load_charging_active.get(load_plan.load_id),
                "power_warning": self._load_power_warning.get(load_plan.load_id, False),
                "expected_power_w": diag.get("expected_w"),
                "measured_power_w": diag.get("measured_w"),
                # F-PLANNER-HONESTY R5: the run-max learned planning power an
                # OFF load is evaluated with (None until the first learned run).
                "learned_power_w": self._load_learned_power_w.get(load_plan.load_id),
                # F-EXECUTOR-GUARDS R8: True while the stale-SOC guard holds
                # the load unavailable (frozen reading during active charging).
                "soc_stale": load_plan.load_id in self._load_soc_stale,
                "deviating_since": since.isoformat() if since else None,
                "schedule": [
                    {
                        "start": slot.start.isoformat(),
                        "end": (
                            slot.start + timedelta(hours=slot.duration)
                        ).isoformat(),
                        # 1 = direct surplus, 2 = preemptive ("zielbasiert")
                        "pass": pass_by_slot.get(slot.index),
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
            }

        naive_now = now.replace(tzinfo=None)
        soc_forecast = [{"t": naive_now.isoformat(), "soc": round(soc, 1)}]
        for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):
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

        return {
            "valid": True,
            "last_update": now,
            "input_soc_percent": soc,
            "input_forecasts_kwh": forecasts,
            "soc_threshold_percent": threshold,
            "inverter_recommendation": recommendation,
            "min_soc_forecast_percent": result.min_soc_percent,
            "max_soc_forecast_percent": result.max_soc_percent,
            "hours_to_max_soc": result.hours_to_max_soc,
            "grid_import_kwh": round(result.grid_import_kwh, 3),
            "grid_export_kwh": round(result.grid_export_kwh, 3),
            "lost_surplus_kwh": round(result.lost_surplus_kwh, 3),
            # F-PREDRAIN diagnostics (docs/F-PREDRAIN.md §3.5) for the SOC-forecast
            # sensor (WP4): per-day PV source, the traded import, the stressed
            # reserve and the derived per-day PV-window end hours.
            "pv_source": dict(self._pv_source_by_day),
            "import_trade_used_wh": round(result.import_trade_used_wh, 1),
            "stressed_min_soc": (
                round(result.stressed_min_soc_percent, 2)
                if result.stressed_min_soc_percent is not None
                else None
            ),
            "pv_window_ends": dict(result.pv_window_ends),
            "load_plans": load_plans,
            "soc_forecast": soc_forecast,
            # Static planning context for the bundled forecast card
            "plan_params": {
                "battery_min_soc_percent": config.battery.soc_min_percent,
                "battery_max_soc_percent": config.battery.soc_max_percent,
                "inverter_min_soc_percent": config.control.inverter_min_soc_percent,
                "soc_buffer_percent": config.control.soc_buffer_percent,
            },
            "appliance_windows": dict(result.appliance_windows),
            "support_dc24": self._support_state["dc24"],
            "support_dc48": self._support_state["dc48"],
            "support_dc24_mode": "manual" if self._support_manual["dc24"] else "auto",
            "support_dc48_mode": "manual" if self._support_manual["dc48"] else "auto",
            "support_dc48_controller": dict(self._dc48_ctrl_diag),
            "consumption_profile": profile_diag,
            "gate_calibration": self._gate_calibration_diag(config),
            "hourly_details": hourly_details,
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

    def dc48_controller_diagnostic(self) -> dict[str, Any]:
        """Public snapshot of the R2 controller state (active/mode/decision/
        reason/voltage) for the 48 V support-mode sensor, so the log-only
        shakedown and the live regulation are observable in the UI."""
        return dict(self._dc48_ctrl_diag)

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

    async def _switch_entity(self, entity_id: str, turn_on: bool) -> bool:
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
                and await self._switch_entity(dc48_entity, desired["dc48"])
            ):
                # A successful service call is no device confirmation; the
                # idle re-sync corrects _support_state on the next cycle.
                self._support_state["dc48"] = desired["dc48"]
                _LOGGER.info(
                    "48 V support PSU switched %s (%s)",
                    "on" if desired["dc48"] else "off",
                    dc48_entity,
                )

            dc24_entity = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
            if (
                dc24_entity
                and not self._support_manual["dc24"]
                and desired["dc24"] != self._support_state["dc24"]
                and await self._sequence_dc24(desired["dc24"], dc24_entity)
            ):
                self._support_state["dc24"] = desired["dc24"]
                _LOGGER.info(
                    "24 V rail now fed by %s",
                    "grid PSU" if desired["dc24"] else "DC/DC converter",
                )

        # Persist the BM's own support state: after a restart it is the
        # evidence that an 'on' PSU is ours and not a manual override.
        self._save_persistent_state()
        # Reflect the new state in the entities without waiting for a replan.
        if self.data:
            self.data["support_dc24"] = self._support_state["dc24"]
            self.data["support_dc48"] = self._support_state["dc48"]
            self.async_update_listeners()

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

        FIX-5: while the plan stays CONTINUOUSLY active (e.g. a night pre-drain
        glued to the following day's block never lets it cycle inactive), the
        frozen deadline used to wedge the published `active` False from expiry
        until the whole block ended (hours). Once `now >= deadline + min_off`
        (min_off falls back to min_runtime) we RE-ANCHOR a fresh run window —
        mirroring a controlled load's force-off -> min_off dwell -> re-on cycle —
        so the sensor duty-cycles instead of staying wedged off."""
        active = bool(plan and plan.active_now)
        if not active:
            if load_id in self._load_run_deadline:
                self._load_run_deadline.pop(load_id, None)
                self._cancel_off_timer(load_id)
            return
        min_runtime = int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30))

        def _anchor() -> None:
            off_min = max(
                min_runtime, round(plan.active_run_hours(slot_durations) * 60.0)
            )
            off_at = now + timedelta(minutes=off_min)
            self._load_run_deadline[load_id] = off_at
            self._arm_off_timer(load_id, off_at)

        deadline = self._load_run_deadline.get(load_id)
        if deadline is None:
            _anchor()  # OFF->ON edge: freeze the first run window
            return
        # Still active past the deadline: re-anchor once the min_off dwell (since
        # the deadline) has elapsed, so a continuously-active block does not wedge
        # the published `active` off for the rest of the block.
        min_off = int(data.get(CONF_LOAD_MIN_OFF_MIN, min_runtime))
        if now >= deadline + timedelta(minutes=min_off):
            _anchor()

    def _effective_load_active(self, load_plan, now: datetime) -> bool:
        """Published `active` for the load binary sensor: the whole-slot
        `active_now` capped by the frozen sub-hour deadline (F-SUBHOUR)."""
        deadline = self._load_run_deadline.get(load_plan.load_id)
        return bool(load_plan.active_now) and (deadline is None or now < deadline)

    async def _apply_load_switching(
        self, result, now: datetime, slot_durations: tuple[float, ...] | None = None
    ) -> None:
        """Evaluate controlled loads and start switching where needed."""
        if self._load_switch_task is not None and not self._load_switch_task.done():
            return

        plans_by_id = {lp.load_id: lp for lp in result.load_plans}
        actions: list[tuple[str, dict[str, Any], bool, bool, float]] = []
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = subentry.data
            plan = plans_by_id.get(subentry_id)
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
            active_now = bool(plan and plan.active_now)
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
            if current and deadline is not None and now >= deadline:
                desired = False
            else:
                desired = active_now
            if desired == current:
                continue
            # Split dwell (R14): ON->OFF is gated by the minimum ON time
            # (min_runtime), OFF->ON by the minimum OFF time (min_off) — the
            # latter protects compressor loads from short-cycling.
            if current:  # pending switch OFF
                dwell_min = int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30))
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
            else:  # pending switch ON
                dwell_min = int(
                    data.get(
                        CONF_LOAD_MIN_OFF_MIN,
                        data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30),
                    )
                )
            last = self._last_load_switch.get(subentry_id)
            if last is not None and now - last < timedelta(minutes=dwell_min):
                continue
            # The dwell timestamp is stamped only on a CONFIRMED switch inside
            # the executor — not here — so a failed actuation does not consume
            # the dwell window and block an immediate retry (review #11).
            plug_was_on = self._entity_is_on(data[CONF_LOAD_CONTROL_SWITCH])
            # The planned contiguous run (h) lets the ON path freeze a deadline.
            run_h = plan.active_run_hours(slot_durations) if plan else 0.0
            actions.append((subentry_id, dict(data), desired, plug_was_on, run_h))

        if actions:
            self._load_switch_task = self.entry.async_create_background_task(
                self.hass,
                self._execute_load_switching(actions, now),
                name="battery_manager_load_switching",
            )

    async def _execute_load_switching(
        self,
        actions: list[tuple[str, dict[str, Any], bool, bool, float]],
        now: datetime | None = None,
    ) -> None:
        if now is None:
            now = dt_util.now()
        async with self._switch_lock:
            for action in actions:
                # Tolerate the legacy 4-tuple (no planned run) so any caller
                # that does not carry a sub-hour run just gets no deadline.
                subentry_id, data, activate, plug_was_on = action[:4]
                run_h = action[4] if len(action) > 4 else 0.0
                plug = data[CONF_LOAD_CONTROL_SWITCH]
                enable = data.get(CONF_LOAD_CHARGE_ENABLE)
                subentry = self.entry.subentries.get(subentry_id)
                label = subentry.title if subentry else subentry_id
                if activate:
                    if enable and not await self._switch_entity(enable, True):
                        continue
                    if not plug_was_on:
                        if not await self._switch_entity(plug, True):
                            continue
                        # We switched the plug on for charging: ownership
                        # allows the 'auto' policy to switch it off again.
                        self._load_plug_owned[subentry_id] = True
                    self._load_charging_active[subentry_id] = True
                    self._last_load_switch[subentry_id] = now
                    # F-SUBHOUR (approach A) + F-RESIDUAL-TOPUP R7: freeze the
                    # planned contiguous run and arm an active OFF at
                    # run_start + max(min_runtime, run_h) so a sub-hour booking is
                    # delivered exactly (no ~250 Wh over-run). Energy-limited loads
                    # are capped the same way — the deadline is an UPPER bound over
                    # their primary level-driven target-SOC stop, so a stale
                    # load-SOC sensor (R8) cannot stretch a ~150 Wh top-up into a
                    # full real_power × 1 h night charge.
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
                    _LOGGER.info("Charging started for load %s", label)
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
                    # F-SUBHOUR: run finished — clear the frozen deadline + timer.
                    self._load_run_deadline.pop(subentry_id, None)
                    self._cancel_off_timer(subentry_id)
                    _LOGGER.info(
                        "Charging stopped for load %s (input %s)",
                        label,
                        "off" if turn_plug_off else "stays on",
                    )
        self._save_persistent_state()
        if self.data:
            plans = self.data.get("load_plans") or {}
            for load_id, active in self._load_charging_active.items():
                if load_id in plans:
                    plans[load_id]["charging_active"] = active
            self.async_update_listeners()

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
        if self._debounce_task:
            self._debounce_task.cancel()
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
        # F-SUBHOUR: drop any pending force-OFF timers before flush/unload so a
        # detached point-in-time callback cannot fire after teardown (R13).
        for load_id in list(self._load_off_timer):
            self._cancel_off_timer(load_id)
        tasks = [
            self._switch_task,
            self._dc48_ctrl_task,
            self._load_switch_task,
            self._dcdc_restore_task,
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
