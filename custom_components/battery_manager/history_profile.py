"""Nightly learning of consumption profiles from recorder history.

Implements the HA side of docs/CONSUMPTION_FORECAST.md Stufe 1: fetch
long-term statistics and switch histories (recorder executor), clean out
self-controlled loads (D-C2), aggregate day-type/hour medians (D-C3) and
persist them per config entry. The math lives in core/load_profile.py.

The learner never blocks the planning cycle: the coordinator only reads
the persisted result; a failing learning run leaves the previous profile
in place until it expires (D-C6).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    APPLIANCE_RUNNING_STATES,
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_AC_LOAD_ENTITY,
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_POWER_THRESHOLD_W,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
    CONF_DC_LOAD_ENTITY,
    CONF_DCDC_SWITCH,
    CONF_LEARNING_MAX_AGE_DAYS,
    CONF_LEARNING_WINDOW_DAYS,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_IN_HOUSE,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_SUPPORT_DC24_POWER_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_POWER_W,
    CONF_SUPPORT_DC48_SWITCH,
    DEFAULT_CONFIG,
    DOMAIN,
    ENTITY_VACATION_MODE,
    LEARNED_STORE_KEY,
    LEARNED_STORE_VERSION,
    LEARNING_CLAMP_AC_W,
    LEARNING_CLAMP_DC_W,
    LEARNING_MIN_SAMPLES,
    LEARNING_MIN_SAMPLES_ABSENCE,
    LEARNING_NEGATIVE_RESIDUAL_WH,
    LEARNING_RATE_LIMIT,
    LEARNING_RUN_HOUR,
    LEARNING_VACATION_MIN_HOURS,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
)
from .core import (
    DAY_TYPE_ABSENCE,
    DAY_TYPE_WEEKDAY,
    DAY_TYPE_WEEKEND,
    aggregate_bins,
    balance_day,
    clean_day,
    day_type,
    on_fractions,
)

_LOGGER = logging.getLogger(__name__)

_PATHS = ("ac", "dc")
_CLAMPS = {"ac": LEARNING_CLAMP_AC_W, "dc": LEARNING_CLAMP_DC_W}
_MIN_SAMPLES = {
    DAY_TYPE_WEEKDAY: LEARNING_MIN_SAMPLES,
    DAY_TYPE_WEEKEND: LEARNING_MIN_SAMPLES,
    DAY_TYPE_ABSENCE: LEARNING_MIN_SAMPLES_ABSENCE,
}

# {(date_iso, hour): wh} per entity
HourMap = dict[tuple[str, int], float]

# Part of the cleaning fingerprint: bump when the D-C2 cleaning SEMANTICS
# change, so cached days computed under the old rules are refetched.
# v2: statistic gaps of power-feedback sensors count as 0 W.
# v3: support paths corrected arithmetically instead of excluded.
_CLEANING_RULES_VERSION = 3


def _default_data() -> dict[str, Any]:
    return {
        "version": LEARNED_STORE_VERSION,
        "computed_at": None,
        "window_days": None,
        "cleaning_fingerprint": None,
        "source_entities": {"ac": [], "dc": []},
        "vacation_mode_active": False,
        "day_log": {},  # date -> {"daytype": ..., "vacation": bool}
        "daily_hours": {},  # date -> {"ac": [24 x Wh|None]|None, "dc": ...}
        "profiles": {"ac": None, "dc": None},
        "samples": {"ac": None, "dc": None},
        "diagnostics": {
            "negative_residuals": 0,
            "coverage": {"ac": 0.0, "dc": 0.0},
            "missing_statistics": [],
        },
    }


class ProfileLearner:
    """Owns the learned-profile store and the nightly learning run."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store = Store(
            hass,
            LEARNED_STORE_VERSION,
            f"{DOMAIN}.{LEARNED_STORE_KEY}.{entry.entry_id}",
        )
        self.data: dict[str, Any] = _default_data()
        self._lock = asyncio.Lock()
        self._unsub_nightly: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Persistence & lifecycle
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored and stored.get("version") == LEARNED_STORE_VERSION:
            merged = _default_data()
            merged.update(stored)
            self.data = merged

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 10)

    def async_schedule(self) -> None:
        """Register the nightly run; catch up when stale or reconfigured.

        A changed source binding must trigger a run even when learning is
        no longer configured at all — only a run reaches the branch that
        drops the stale learned state (opt-out).
        """
        if self._unsub_nightly is None:
            self._unsub_nightly = async_track_time_change(
                self.hass,
                self._handle_nightly,
                hour=LEARNING_RUN_HOUR,
                minute=0,
                second=0,
            )
        computed = self._computed_at()
        stale = computed is None or dt_util.now() - computed > timedelta(hours=24)
        cfg = self._raw_config()
        window_changed = self.data.get("window_days") != int(
            cfg[CONF_LEARNING_WINDOW_DAYS]
        )
        cleaning_changed = self.data.get(
            "cleaning_fingerprint"
        ) != self._cleaning_fingerprint(cfg)
        if self._binding_changed() or (
            self._learning_configured()
            and (stale or window_changed or cleaning_changed)
        ):
            self._start_run()

    def async_unschedule(self) -> None:
        if self._unsub_nightly is not None:
            self._unsub_nightly()
            self._unsub_nightly = None

    @callback
    def _handle_nightly(self, _now: datetime) -> None:
        # @callback: async_track_time_change would otherwise run a plain
        # function in an executor thread, where creating the background
        # task is not thread-safe.
        self._start_run()

    def _start_run(self) -> None:
        self.entry.async_create_background_task(
            self.hass, self.async_run_learning(), name="battery_manager_learning"
        )

    # ------------------------------------------------------------------
    # Vacation mode (D-C4)
    # ------------------------------------------------------------------

    @property
    def vacation_active(self) -> bool:
        return bool(self.data.get("vacation_mode_active"))

    async def async_set_vacation(self, active: bool) -> None:
        self.data["vacation_mode_active"] = bool(active)
        self._save()

    # ------------------------------------------------------------------
    # Read access for the coordinator
    # ------------------------------------------------------------------

    def _raw_config(self) -> dict[str, Any]:
        return {**DEFAULT_CONFIG, **self.entry.data, **self.entry.options}

    def _computed_at(self) -> datetime | None:
        raw = self.data.get("computed_at")
        return dt_util.parse_datetime(raw) if raw else None

    def profiles_for_planning(self) -> dict[str, Any] | None:
        """Return the learned bins per path, or None if absent/stale (D-C6).

        A profile learned from other source entities than the currently
        configured ones is never used — after a reconfiguration the planner
        falls back to the static profile until the next learning run.
        """
        computed = self._computed_at()
        if computed is None:
            return None
        max_age = timedelta(days=float(self._raw_config()[CONF_LEARNING_MAX_AGE_DAYS]))
        if dt_util.now() - computed > max_age:
            return None
        if self._binding_changed():
            return None
        profiles = self.data.get("profiles") or {}
        if not any(profiles.get(path) for path in _PATHS):
            return None
        return profiles

    def _binding_changed(self) -> bool:
        stored = self.data.get("source_entities") or {}
        sources = self._sources()
        return any((stored.get(path) or []) != sources[path]["all"] for path in _PATHS)

    def diagnostics(self) -> dict[str, Any]:
        diag = dict(self.data.get("diagnostics") or {})
        diag["computed_at"] = self.data.get("computed_at")
        diag["samples"] = self.data.get("samples")
        # The learned bins themselves (W per day type and hour): visible in
        # the developer tools and usable by dashboard cards/templates.
        diag["profiles"] = self.data.get("profiles")
        return diag

    def export_snapshot(self) -> dict[str, Any]:
        """Learned state for the export service (docs/CONSUMPTION_FORECAST.md)."""
        return {
            "computed_at": self.data.get("computed_at"),
            "window_days": self.data.get("window_days"),
            "profiles": self.data.get("profiles"),
            "samples": self.data.get("samples"),
            "diagnostics": self.data.get("diagnostics"),
            "day_log": self.data.get("day_log"),
        }

    # ------------------------------------------------------------------
    # Source configuration
    # ------------------------------------------------------------------

    def _sources(self) -> dict[str, dict[str, Any]]:
        cfg = self._raw_config()
        keys = {
            "ac": (CONF_AC_LOAD_ENTITY, CONF_AC_BALANCE_IN, CONF_AC_BALANCE_OUT),
            "dc": (CONF_DC_LOAD_ENTITY, CONF_DC_BALANCE_IN, CONF_DC_BALANCE_OUT),
        }
        sources: dict[str, dict[str, Any]] = {}
        for path, (direct_key, in_key, out_key) in keys.items():
            direct = cfg.get(direct_key) or None
            inflows = list(cfg.get(in_key) or [])
            outflows = list(cfg.get(out_key) or [])
            # The direct sensor takes precedence over the balance (§2.2).
            if direct:
                inflows, outflows = [], []
            sources[path] = {
                "direct": direct,
                "in": inflows,
                "out": outflows,
                "active": bool(direct or inflows),
                "all": ([direct] if direct else []) + inflows + outflows,
            }
        return sources

    def _learning_configured(self) -> bool:
        return any(src["active"] for src in self._sources().values())

    # ------------------------------------------------------------------
    # Learning run
    # ------------------------------------------------------------------

    async def async_run_learning(self) -> None:
        try:
            async with self._lock:
                await self._run_learning()
        except Exception:  # noqa: BLE001 - learning must never break setup
            _LOGGER.exception("Consumption-profile learning run failed")

    async def _run_learning(self) -> None:
        cfg = self._raw_config()
        sources = self._sources()
        if not any(src["active"] for src in _paths_of(sources)):
            # Nothing configured: drop learned state, planner stays static.
            self.data["profiles"] = {"ac": None, "dc": None}
            self.data["samples"] = {"ac": None, "dc": None}
            self.data["computed_at"] = None
            self.data["source_entities"] = {path: [] for path in _PATHS}
            self._save()
            return

        if "recorder" not in self.hass.config.components:
            _LOGGER.warning(
                "Consumption-profile learning requires the recorder"
                " integration; skipping run"
            )
            return

        window_days = int(cfg[CONF_LEARNING_WINDOW_DAYS])
        today = dt_util.now().date()
        window_start = today - timedelta(days=window_days)
        wanted_days = [
            (window_start + timedelta(days=offset)).isoformat()
            for offset in range((today - window_start).days)
        ]

        self._apply_source_binding(sources)
        cleaning_changed = self._apply_cleaning_fingerprint(cfg)

        daily_hours: dict[str, dict[str, Any]] = self.data["daily_hours"]
        missing: dict[str, list[str]] = {
            path: [
                day
                for day in wanted_days
                if sources[path]["active"]
                and (daily_hours.get(day) or {}).get(path) is None
            ]
            for path in _PATHS
        }
        all_missing = sorted(set(missing["ac"]) | set(missing["dc"]))
        if all_missing:
            await self._fetch_days(cfg, sources, all_missing, missing)

        # Prune outside the window, then aggregate (D-C3).
        self.data["daily_hours"] = {
            day: value
            for day, value in self.data["daily_hours"].items()
            if day in set(wanted_days)
        }
        self.data["day_log"] = {
            day: value
            for day, value in self.data["day_log"].items()
            if day in set(wanted_days)
        }

        day_types = {
            day: entry.get("daytype", DAY_TYPE_WEEKDAY)
            for day, entry in self.data["day_log"].items()
        }
        coverage: dict[str, float] = {}
        for path in _PATHS:
            if not sources[path]["active"]:
                self.data["profiles"][path] = None
                self.data["samples"][path] = None
                coverage[path] = 0.0
                continue
            per_day = {
                day: value[path]
                for day, value in self.data["daily_hours"].items()
                if value.get(path) is not None
            }
            bins, samples = aggregate_bins(
                per_day,
                day_types,
                _MIN_SAMPLES,
                # Fresh start after a cleaning change: the old bins were
                # computed under different rules and must not damp the
                # corrected values via the rate limit.
                None if cleaning_changed else self.data["profiles"].get(path),
                LEARNING_RATE_LIMIT,
                _CLAMPS[path],
            )
            self.data["profiles"][path] = bins
            self.data["samples"][path] = samples
            valid_hours = sum(
                1
                for series in per_day.values()
                for value in series
                if value is not None
            )
            coverage[path] = (
                round(valid_hours / (len(wanted_days) * 24), 3) if wanted_days else 0.0
            )

        self.data["diagnostics"]["coverage"] = coverage
        self.data["computed_at"] = dt_util.now().isoformat()
        self.data["window_days"] = window_days
        self._save()
        _LOGGER.info(
            "Consumption profiles updated (coverage ac=%.0f%% dc=%.0f%%)",
            coverage.get("ac", 0.0) * 100,
            coverage.get("dc", 0.0) * 100,
        )

    def _apply_source_binding(self, sources: dict[str, dict[str, Any]]) -> None:
        """Reset a path's learned state when its source entities changed."""
        stored = self.data.get("source_entities") or {}
        for path in _PATHS:
            current = sources[path]["all"]
            if stored.get(path) != current:
                for day_value in self.data["daily_hours"].values():
                    day_value[path] = None
                self.data["profiles"][path] = None
                self.data["samples"][path] = None
        self.data["source_entities"] = {path: sources[path]["all"] for path in _PATHS}

    def _cleaning_fingerprint(self, cfg: dict[str, Any]) -> str:
        """Everything the D-C2 cleaning depends on, as a comparable string.

        Cached daily_hours were cleaned with the configuration of their
        fetch time; if any cleaning input changes (in_house flags, power/
        switch entities, nominal powers, appliances, support switches), the
        cache is invalid and must be refetched — otherwise a reconfiguration
        would keep contaminated days in the window for weeks.
        """
        loads, appliances = self._subentries()
        parts: list[Any] = [
            _CLEANING_RULES_VERSION,
            sorted(loads, key=str),
            sorted(appliances, key=str),
        ]
        parts.extend(
            cfg.get(key)
            for key in (
                CONF_SUPPORT_DC48_SWITCH,
                CONF_SUPPORT_DC48_POWER_W,
                CONF_SUPPORT_DC24_SWITCH,
                CONF_SUPPORT_DC24_POWER_ENTITY,
                CONF_DCDC_SWITCH,
            )
        )
        return repr(parts)

    def _apply_cleaning_fingerprint(self, cfg: dict[str, Any]) -> bool:
        """Drop cached days when the cleaning config changed.

        The old profile stays in place until the refetch succeeds (a failing
        run must not lose it, D-C6); the caller disables the rate limit for
        the rebuild so stale bins cannot damp the correction.
        """
        fingerprint = self._cleaning_fingerprint(cfg)
        if self.data.get("cleaning_fingerprint") == fingerprint:
            return False
        if self.data.get("daily_hours"):
            _LOGGER.info("Cleaning configuration changed; relearning the full window")
        self.data["daily_hours"] = {}
        self.data["day_log"] = {}
        self.data["cleaning_fingerprint"] = fingerprint
        return True

    # ------------------------------------------------------------------
    # History fetching & cleaning (D-C1/D-C2)
    # ------------------------------------------------------------------

    async def _fetch_days(
        self,
        cfg: dict[str, Any],
        sources: dict[str, dict[str, Any]],
        days: list[str],
        missing: dict[str, list[str]],
    ) -> None:
        tz = dt_util.get_default_time_zone()
        start_local = datetime.combine(
            date.fromisoformat(days[0]), datetime.min.time(), tz
        )
        end_local = datetime.combine(
            date.fromisoformat(days[-1]) + timedelta(days=1),
            datetime.min.time(),
            tz,
        )

        # --- Long-term statistics (measurement sources + power feedback) ---
        stat_ids: set[str] = set()
        for path in _PATHS:
            if sources[path]["active"]:
                stat_ids.update(sources[path]["all"])
        loads, appliances = self._subentries()
        for load in loads:
            if load["in_house"] and load["power_entity"]:
                stat_ids.add(load["power_entity"])
        for appliance in appliances:
            stat_ids.add(appliance["detection_entity"])
        if cfg.get(CONF_SUPPORT_DC24_POWER_ENTITY):
            stat_ids.add(cfg[CONF_SUPPORT_DC24_POWER_ENTITY])

        recorder = get_instance(self.hass)
        metadata = await recorder.async_add_executor_job(
            lambda: list_statistic_ids(self.hass, stat_ids)
        )
        meta_by_id = {item["statistic_id"]: item for item in metadata}
        self._report_missing_statistics(sources, meta_by_id, loads)

        available = stat_ids & set(meta_by_id)
        stats: dict[str, list[dict[str, Any]]] = {}
        if available:
            stats = await recorder.async_add_executor_job(
                lambda: statistics_during_period(
                    self.hass,
                    dt_util.as_utc(start_local),
                    dt_util.as_utc(end_local),
                    available,
                    "hour",
                    {"energy": "kWh", "power": "W"},
                    {"mean", "change"},
                )
            )
        hour_maps = {
            entity_id: _rows_to_hour_map(rows, meta_by_id.get(entity_id))
            for entity_id, rows in stats.items()
        }

        # --- Switch/state histories (on-fractions) ---
        switch_specs: list[tuple[str, Callable[[str], bool]]] = []
        for load in loads:
            # Nominal-power fallback whenever there is no usable power
            # feedback (no power_entity, or one without statistics).
            if (
                load["in_house"]
                and load["control_switch"]
                and load["power_entity"] not in hour_maps
            ):
                switch_specs.append((load["control_switch"], _is_on))
        for key in (CONF_SUPPORT_DC48_SWITCH, CONF_SUPPORT_DC24_SWITCH):
            if cfg.get(key):
                switch_specs.append((cfg[key], _is_on))
        if cfg.get(CONF_DCDC_SWITCH):
            # Inverted: the DC/DC converter is ON in normal operation; its
            # OFF phase means the 24 V rail is fed past the measuring point.
            switch_specs.append((cfg[CONF_DCDC_SWITCH], _is_off))
        for appliance in appliances:
            if appliance["detection_entity"] not in hour_maps:
                threshold = appliance["power_threshold_w"]
                switch_specs.append(
                    (
                        appliance["detection_entity"],
                        _running_predicate(threshold),
                    )
                )
        vacation_entity = self._vacation_entity_id()
        if vacation_entity:
            switch_specs.append((vacation_entity, _is_on))

        # Coverage rule (D-C2): hours before an entity's first recorded
        # state are UNKNOWN, not "off" — they must never be cleaned with 0.
        fractions: dict[str, dict[tuple[str, int], float]] = {}
        coverage_start: dict[str, datetime | None] = {}
        for entity_id, predicate in switch_specs:
            changes, first_known = await self._state_changes(
                entity_id, start_local, end_local, predicate
            )
            fractions[entity_id] = on_fractions(changes, start_local, end_local)
            coverage_start[entity_id] = first_known

        # --- Day tagging (D-C4) ---
        vacation_fr = fractions.get(vacation_entity or "", {})
        for day in days:
            vacation_hours = sum(
                vacation_fr.get((day, hour), 0.0) for hour in range(24)
            )
            vacation = vacation_hours >= LEARNING_VACATION_MIN_HOURS
            self.data["day_log"][day] = {
                "daytype": day_type(date.fromisoformat(day), vacation),
                "vacation": vacation,
            }

        # --- Support-path corrections & exclusions (D-C2 step 3) ---
        # Active support paths SHIFT power between the paths instead of
        # invalidating them (winter can run on grid PSUs for months):
        #   48 V PSU: draws its configured power from the house AC net and
        #     injects it into the battery bus -> AC minus, DC plus.
        #   24 V PSU (DC/DC off): the whole 24 V rail moves from the DC to
        #     the AC measurement -> shifted back exactly via the PSU's
        #     power sensor; without that sensor the hour stays unlearnable.
        # Only unresolvable states (unmeasured PSU feed, dead rail,
        # uncovered switch history) still exclude the hour.
        p24_map = hour_maps.get(cfg.get(CONF_SUPPORT_DC24_POWER_ENTITY) or "")
        status_appliances = [
            appliance["detection_entity"]
            for appliance in appliances
            if appliance["detection_entity"] not in hour_maps
        ]

        negatives = 0
        for day in days:
            day_value = self.data["daily_hours"].setdefault(
                day, {"ac": None, "dc": None}
            )
            psu48_draw = self._psu48_series(day, cfg, fractions, coverage_start, tz)
            p24_series = (
                _day_series_zero_filled(p24_map, day) if p24_map is not None else None
            )
            support_excluded = self._unresolvable_support_hours(
                day, cfg, fractions, coverage_start, tz, p24_series is not None
            )
            appliance_excluded = _excluded_hours(
                day, status_appliances, fractions, coverage_start, tz
            )
            for path in _PATHS:
                if day not in missing[path]:
                    continue
                load_series = self._load_series(day, sources[path], hour_maps)
                subtract: list[list[float | None]] = []
                excluded = support_excluded
                if path == "ac":
                    subtract = self._subtractions(
                        day, loads, appliances, hour_maps, fractions, coverage_start, tz
                    )
                    if psu48_draw is not None:
                        subtract.append(psu48_draw)
                    if p24_series is not None:
                        subtract.append(p24_series)
                    excluded = support_excluded | appliance_excluded
                else:
                    # DC additions = negative subtractions (clean_day sums).
                    if psu48_draw is not None:
                        subtract.append([None if v is None else -v for v in psu48_draw])
                    if p24_series is not None:
                        subtract.append([-v for v in p24_series])
                cleaned, day_negatives = clean_day(
                    load_series,
                    subtract,
                    excluded,
                    _CLAMPS[path],
                    LEARNING_NEGATIVE_RESIDUAL_WH,
                )
                negatives += day_negatives
                day_value[path] = cleaned

        self.data["diagnostics"]["negative_residuals"] = (
            int(self.data["diagnostics"].get("negative_residuals", 0)) + negatives
        )

    def _psu48_series(
        self,
        day: str,
        cfg: dict[str, Any],
        fractions: dict[str, dict[tuple[str, int], float]],
        coverage_start: dict[str, datetime | None],
        tz: tzinfo,
    ) -> list[float | None] | None:
        """48 V PSU energy per hour: configured power x switch on-fraction.

        Subtracted from the AC measurement (PSU draw) and added to the DC
        measurement (injection into the battery bus) — the same fixed-power
        approximation the simulation core uses (conversion losses
        neglected). None = switch history not covered -> hour dropped.
        """
        switch = cfg.get(CONF_SUPPORT_DC48_SWITCH)
        if not switch:
            return None
        on_fr = fractions.get(switch, {})
        covered_from = coverage_start.get(switch)
        power = float(cfg.get(CONF_SUPPORT_DC48_POWER_W, 60.0))
        return [
            (
                power * on_fr.get((day, hour), 0.0)
                if _hour_covered(covered_from, day, hour, tz)
                else None
            )
            for hour in range(24)
        ]

    def _unresolvable_support_hours(
        self,
        day: str,
        cfg: dict[str, Any],
        fractions: dict[str, dict[tuple[str, int], float]],
        coverage_start: dict[str, datetime | None],
        tz: tzinfo,
        p24_measured: bool,
    ) -> set[int]:
        """Hours where the 24 V rail state cannot be resolved (D-C2 step 3).

        Unlearnable on both paths: PSU-fed rail without a PSU power sensor
        (the DC->AC shift is unknown), a dead rail (DC/DC off without PSU),
        or uncovered switch history. With a measured PSU the corrections
        handle everything and nothing is excluded here.
        """
        dc24_switch = cfg.get(CONF_SUPPORT_DC24_SWITCH)
        dcdc_switch = cfg.get(CONF_DCDC_SWITCH)
        if not dc24_switch and not dcdc_switch:
            return set()
        f24 = fractions.get(dc24_switch or "", {})
        f_off = fractions.get(dcdc_switch or "", {})
        excluded: set[int] = set()
        for hour in range(24):
            for entity in (dc24_switch, dcdc_switch):
                if entity and not _hour_covered(
                    coverage_start.get(entity), day, hour, tz
                ):
                    excluded.add(hour)
                    break
            if hour in excluded:
                continue
            off = f_off.get((day, hour), 0.0) if dcdc_switch else 0.0
            on24 = f24.get((day, hour), 0.0) if dc24_switch else 0.0
            if dcdc_switch and off > on24 + 1e-9:
                excluded.add(hour)  # rail (partly) dead: abnormal state
                continue
            psu_feeding = min(on24, off) if dcdc_switch else on24
            if psu_feeding > 0.0 and not p24_measured:
                excluded.add(hour)  # unmeasured DC->AC shift
        return excluded

    def _load_series(
        self,
        day: str,
        source: dict[str, Any],
        hour_maps: dict[str, HourMap],
    ) -> list[float | None]:
        if source["direct"]:
            return _day_series(hour_maps.get(source["direct"], {}), day)
        inflows = [
            _day_series(hour_maps.get(entity, {}), day) for entity in source["in"]
        ]
        outflows = [
            _day_series(hour_maps.get(entity, {}), day) for entity in source["out"]
        ]
        return balance_day(inflows, outflows)

    def _subtractions(
        self,
        day: str,
        loads: list[dict[str, Any]],
        appliances: list[dict[str, Any]],
        hour_maps: dict[str, HourMap],
        fractions: dict[str, dict[tuple[str, int], float]],
        coverage_start: dict[str, datetime | None],
        tz: tzinfo,
    ) -> list[list[float | None]]:
        """Per-source hourly energy to remove from the measured AC load.

        Surplus loads: power-feedback statistics where available, otherwise
        nominal power x switch on-fraction — but only for hours covered by
        the switch history (uncovered hours yield None and drop the hour).
        Appliances with a statistics-backed power sensor are subtracted the
        same way (D-C2 step 2); status-only appliances are handled via hour
        exclusion instead.

        Gaps in a power-feedback statistic count as 0 W (operator decision
        2026-07-04): these devices go unavailable exactly when they are off
        (sleeping powerstation, idle appliance), so a missing hour means
        "no consumption", not "unknown" — dropping it would starve the
        weekend bins for weeks.
        """
        result: list[list[float | None]] = []
        for load in loads:
            if not load["in_house"]:
                continue
            if load["power_entity"] in hour_maps:
                result.append(
                    _day_series_zero_filled(hour_maps[load["power_entity"]], day)
                )
            elif load["control_switch"]:
                switch = load["control_switch"]
                on_fr = fractions.get(switch, {})
                covered_from = coverage_start.get(switch)
                result.append(
                    [
                        (
                            on_fr.get((day, hour), 0.0) * load["nominal_power_w"]
                            if _hour_covered(covered_from, day, hour, tz)
                            else None
                        )
                        for hour in range(24)
                    ]
                )
            # Loads without any signal cannot be cleaned; they are planned
            # separately, so we deliberately do not drop the whole day.
        for appliance in appliances:
            entity_id = appliance["detection_entity"]
            if entity_id in hour_maps:
                result.append(_day_series_zero_filled(hour_maps[entity_id], day))
        return result

    def _subentries(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        loads: list[dict[str, Any]] = []
        appliances: list[dict[str, Any]] = []
        for subentry in self.entry.subentries.values():
            data = subentry.data
            if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
                loads.append(
                    {
                        "in_house": bool(data.get(CONF_LOAD_IN_HOUSE, True)),
                        "power_entity": data.get(CONF_LOAD_POWER_ENTITY),
                        "control_switch": data.get(CONF_LOAD_CONTROL_SWITCH),
                        "nominal_power_w": float(data.get(CONF_LOAD_POWER_W, 0.0)),
                    }
                )
            elif subentry.subentry_type == SUBENTRY_TYPE_APPLIANCE and data.get(
                CONF_APPLIANCE_DETECTION_ENTITY
            ):
                appliances.append(
                    {
                        "detection_entity": data[CONF_APPLIANCE_DETECTION_ENTITY],
                        "power_threshold_w": float(
                            data.get(CONF_APPLIANCE_POWER_THRESHOLD_W, 10.0)
                        ),
                    }
                )
        return loads, appliances

    def _vacation_entity_id(self) -> str | None:
        registry = er.async_get(self.hass)
        return registry.async_get_entity_id(
            "switch", DOMAIN, f"{self.entry.entry_id}_{ENTITY_VACATION_MODE}"
        )

    async def _state_changes(
        self,
        entity_id: str,
        start_local: datetime,
        end_local: datetime,
        predicate: Callable[[str], bool],
    ) -> tuple[list[tuple[datetime, bool]], datetime | None]:
        """Predicate changes in weekly chunks, plus the coverage start.

        The coverage start is the timestamp of the first known state row
        (the synthetic start-state row counts); None means the recorder has
        no history at all for this entity in the window — callers must not
        interpret that as "off".
        """
        recorder = get_instance(self.hass)
        changes: list[tuple[datetime, bool]] = []
        cursor = start_local
        while cursor < end_local:
            chunk_end = min(cursor + timedelta(days=7), end_local)
            states = await recorder.async_add_executor_job(
                lambda s=cursor, e=chunk_end: history.state_changes_during_period(
                    self.hass,
                    dt_util.as_utc(s),
                    dt_util.as_utc(e),
                    entity_id=entity_id,
                    no_attributes=True,
                )
            )
            changes.extend(
                (dt_util.as_local(state.last_updated), predicate(state.state))
                for state in states.get(entity_id, [])
            )
            cursor = chunk_end
        changes.sort(key=lambda item: item[0])
        return changes, (changes[0][0] if changes else None)

    def _report_missing_statistics(
        self,
        sources: dict[str, dict[str, Any]],
        meta_by_id: dict[str, Any],
        loads: list[dict[str, Any]],
    ) -> None:
        """Repair issue for configured entities without long-term statistics.

        Covers the measurement sources AND the power-feedback sensors of
        in-house loads: a power_entity without statistics would otherwise
        silently drop every hour of the AC learning (subtraction unknown).
        The 24 V PSU power sensor counts too — without its statistics every
        PSU-fed hour is unlearnable (D-C2 step 3).
        """
        psu24 = self._raw_config().get(CONF_SUPPORT_DC24_POWER_ENTITY)
        missing = sorted(
            {
                entity_id
                for path in _PATHS
                if sources[path]["active"]
                for entity_id in sources[path]["all"]
                if entity_id not in meta_by_id
            }
            | {
                load["power_entity"]
                for load in loads
                if load["in_house"]
                and load["power_entity"]
                and load["power_entity"] not in meta_by_id
            }
            | ({psu24} if psu24 and psu24 not in meta_by_id else set())
        )
        self.data["diagnostics"]["missing_statistics"] = missing
        issue_id = f"learning_no_statistics_{self.entry.entry_id}"
        if missing:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="learning_no_statistics",
                translation_placeholders={"entity_ids": ", ".join(missing)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)


# ----------------------------------------------------------------------
# Module helpers (no self-state)
# ----------------------------------------------------------------------


def _paths_of(sources: dict[str, dict[str, Any]]):
    return (sources[path] for path in _PATHS)


def _is_on(state: str) -> bool:
    return state == "on"


def _is_off(state: str) -> bool:
    # Explicit "off" only: unknown/unavailable must not read as "off".
    return state == "off"


def _hour_covered(
    coverage_start: datetime | None, day: str, hour: int, tz: tzinfo
) -> bool:
    if coverage_start is None:
        return False
    hour_start = datetime.combine(date.fromisoformat(day), time(hour=hour), tz)
    return hour_start >= coverage_start


def _running_predicate(threshold_w: float) -> Callable[[str], bool]:
    def _running(state: str) -> bool:
        try:
            return float(state) >= threshold_w
        except (TypeError, ValueError):
            return state.lower() in APPLIANCE_RUNNING_STATES

    return _running


def _rows_to_hour_map(
    rows: list[dict[str, Any]], meta: dict[str, Any] | None
) -> HourMap:
    """Map LTS rows to Wh per local (date, hour).

    Energy counters (has_sum) use the hourly `change` (kWh -> Wh); power
    sensors use the hourly `mean` (W over one hour = Wh numerically).
    On DST fall-back days two UTC hours map to the same local hour: energy
    is summed, power is averaged.
    """
    use_change = bool(meta and meta.get("has_sum"))
    sums: dict[tuple[str, int], float] = {}
    counts: dict[tuple[str, int], int] = {}
    for row in rows:
        raw = row.get("change") if use_change else row.get("mean")
        if raw is None:
            continue
        wh = float(raw) * 1000.0 if use_change else float(raw)
        start = row.get("start")
        local = dt_util.as_local(
            dt_util.utc_from_timestamp(start)
            if isinstance(start, (int, float))
            else start
        )
        key = (local.date().isoformat(), local.hour)
        sums[key] = sums.get(key, 0.0) + wh
        counts[key] = counts.get(key, 0) + 1
    if use_change:
        return dict(sums)
    return {key: sums[key] / counts[key] for key in sums}


def _day_series(hour_map: HourMap, day: str) -> list[float | None]:
    return [hour_map.get((day, hour)) for hour in range(24)]


def _day_series_zero_filled(hour_map: HourMap, day: str) -> list[float]:
    """Day series for subtraction sources: missing hours count as 0 W.

    Used for power-feedback sensors of devices that report unavailable
    while switched off — a statistics gap means "not consuming".
    """
    return [hour_map.get((day, hour), 0.0) for hour in range(24)]


def _excluded_hours(
    day: str,
    entities: list[str],
    fractions: dict[str, dict[tuple[str, int], float]],
    coverage_start: dict[str, datetime | None],
    tz: tzinfo,
) -> set[int]:
    """Hours with the exclusion signal active — or without state coverage.

    What cannot be verified as clean must not be learned (conservative:
    days outside the recorder retention stay unlearned when an exclusion
    entity is configured).
    """
    excluded: set[int] = set()
    for entity_id in entities:
        entity_fractions = fractions.get(entity_id, {})
        covered_from = coverage_start.get(entity_id)
        for hour in range(24):
            if (
                not _hour_covered(covered_from, day, hour, tz)
                or entity_fractions.get((day, hour), 0.0) > 0.0
            ):
                excluded.add(hour)
    return excluded
