"""Measured appliance cycles (F-APPLIANCE-TELEMETRY).

Only complete, continuously observed cycles teach future runs. Active samples
are deliberately not restored: a restart cannot account for missing power.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime
from statistics import median
from typing import Literal, NamedTuple, TypedDict, TypeGuard, cast

from homeassistant.util import dt as dt_util


def measurement(state, kind: str) -> float | None:
    """Normalize an explicitly configured meter to W or Wh."""
    if state is None:
        return None
    units = {"power": {"W": 1, "kW": 1000}, "energy": {"Wh": 1, "kWh": 1000}}
    factor = units[kind].get(state.attributes.get("unit_of_measurement"))
    try:
        value = float(state.state)
    except TypeError, ValueError:
        return None
    return value * factor if factor and math.isfinite(value) and value >= 0 else None


def duration_hours(state, now: datetime, *, remaining: bool = False) -> float | None:
    """Read durations in s/min/h or HH:MM[:SS]; bare appliance values are minutes.

    Remaining-time sensors may instead expose a timestamp for program end.
    Entity names are never interpreted as a unit or an activity signal.
    """
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    if remaining and state.attributes.get("device_class") == "timestamp":
        end = dt_util.parse_datetime(state.state)
        if end is None or end.tzinfo is None:
            return None
        return max(0.0, (end - now).total_seconds() / 3600)
    try:
        if ":" in state.state:
            parts = [float(v) for v in state.state.split(":")]
            if len(parts) not in (2, 3) or any(v < 0 for v in parts):
                return None
            value = parts[0] + parts[1] / 60
            if len(parts) == 3:
                value += parts[2] / 3600
        else:
            factor = {None: 1 / 60, "min": 1 / 60, "s": 1 / 3600, "h": 1}.get(
                state.attributes.get("unit_of_measurement")
            )
            if factor is None:
                return None
            value = float(state.state) * factor
    except TypeError, ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def program_name(state) -> str | None:
    """Use explicit program states only; unknown never becomes a learned label."""
    value = state.state.strip() if state is not None else ""
    return (
        value
        if value.lower() not in {"", "unknown", "unavailable", "none"}
        and len(value) <= 255
        else None
    )


# Bounds reject incomplete/misattributed runs and keep persisted learning small.
MAX_CYCLE_ENERGY_WH = 10_000
MAX_CYCLE_DURATION_H = 24
MAX_CYCLE_SAMPLES = 20
MAX_PROGRAMS = 32
MAX_MEASUREMENT_GAP_S = 600
# Observation history has the same bounded retention as the learning window.
MAX_CYCLE_HISTORY = 20
# Bounded diagnostic vocabulary; persisted corruption cannot grow one entry.
MAX_CYCLE_NOTES = 16
LEARNING_METADATA_VERSION = 1

MeasurementSource = Literal["energy_counter", "integrated_power"]
LearningState = Literal["no_data", "measuring", "learned", "invalid"]


class DeviceProfile(TypedDict):
    energy_wh: float
    energy_min_wh: float
    energy_max_wh: float
    count: int
    last_learned_at: str | None


class ProgramProfile(DeviceProfile):
    program: str
    duration_h: float
    duration_min_h: float
    duration_max_h: float


class CycleMeasurement(TypedDict):
    started_at: str
    observed_at: str
    energy_wh: float | None
    measurement_source: MeasurementSource | None
    complete: bool
    reasons: list[str]
    warnings: list[str]


class CycleHistory(TypedDict):
    program: str | None
    started_at: str
    ended_at: str
    duration_h: float
    energy_wh: float | None
    measurement_source: MeasurementSource | None
    complete: bool
    accepted: bool
    reasons: list[str]
    warnings: list[str]


class LearningSnapshot(TypedDict):
    profiles: list[ProgramProfile]
    device_profile: DeviceProfile | None
    history: list[CycleHistory]
    state: LearningState
    measurement: CycleMeasurement | None


class ProgramSample(NamedTuple):
    """Named units internally; JSON still stores the historical [Wh, hours] pair."""

    energy_wh: float
    duration_h: float


class ActiveCycle(TypedDict):
    at: datetime
    started: datetime
    program: str | None
    power: float | None
    energy: float | None
    first_energy: float | None
    wh: float
    power_ok: bool
    energy_ok: bool
    valid: bool
    reasons: list[str]
    warnings: list[str]


class ApplianceLearning:
    """Bounded median of measured cycle energies, keyed by appliance subentry."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}
        self.active: dict[str, ActiveCycle] = {}
        self.program_samples: dict[str, dict[str, list[ProgramSample]]] = {}
        self.programs: dict[str, str | None] = {}
        # Incomplete starts remain separate from teachable active cycles. They
        # explain partial consumption without silently becoming training data.
        self._partial: dict[str, ActiveCycle] = {}
        self._history: dict[str, list[CycleHistory]] = {}
        self._last_learned: dict[str, str] = {}
        self._program_learned: dict[str, dict[str, str]] = {}

    def restore(self, data) -> None:
        if not isinstance(data, dict):
            return
        for key, values in data.items():
            if isinstance(values, list):
                self.samples[key] = [
                    float(v)
                    for v in values
                    if isinstance(v, (int, float))
                    and math.isfinite(v)
                    and 0 < v <= MAX_CYCLE_ENERGY_WH
                ][-MAX_CYCLE_SAMPLES:]

    def restore_programs(self, data) -> None:
        """Restore bounded profiles without trusting persisted JSON types."""
        if not isinstance(data, dict):
            return
        for key, profiles in data.items():
            if not isinstance(profiles, dict):
                continue
            clean = {}
            for name, values in list(profiles.items())[-MAX_PROGRAMS:]:
                if (
                    not isinstance(name, str)
                    or not name
                    or len(name) > 255
                    or not isinstance(values, list)
                ):
                    continue
                samples = [
                    ProgramSample(float(pair[0]), float(pair[1]))
                    for pair in values
                    if isinstance(pair, list)
                    and len(pair) == 2
                    and all(
                        isinstance(v, (int, float))
                        and not isinstance(v, bool)
                        and math.isfinite(v)
                        and 0 < v <= limit
                        for v, limit in zip(
                            pair,
                            (MAX_CYCLE_ENERGY_WH, MAX_CYCLE_DURATION_H),
                            strict=True,
                        )
                    )
                ][-MAX_CYCLE_SAMPLES:]
                if samples:
                    clean[name] = samples
            self.program_samples[key] = clean

    def duration(self, key: str, fallback: float, program: str | None = None) -> float:
        values = self.program_samples.get(key, {}).get(program or "", [])
        return median(sample.duration_h for sample in values) if values else fallback

    def energy(self, key: str, fallback: float, program: str | None = None) -> float:
        profile = self.program_samples.get(key, {}).get(program or "", [])
        if profile:
            return median(sample.energy_wh for sample in profile)
        values = self.samples.get(key)
        return median(values) if values else fallback

    def observe(
        self,
        key: str,
        now: datetime,
        running: bool,
        power: float | None,
        energy: float | None,
        *,
        complete_start: bool,
        valid: bool = True,
        program: str | None = None,
        invalid_reason: str | None = None,
    ) -> None:
        """Integrate held power; prefer a continuous, non-resetting energy counter.

        Ten minutes is the maximum measurement gap. Missing samples invalidate
        that source for this cycle, rather than teaching an understated total.
        """
        cycle = self.active.get(key) or self._partial.get(key)
        if running:
            if key not in self.programs or self.programs[key] is None:
                self.programs[key] = program
            elif (
                program is not None
                and program != self.programs[key]
                and cycle is not None
            ):
                # Conflicting labels can mean a restart/aborted program. Never
                # mix two programs into a single profile (or aggregate sample).
                self._invalidate(cycle, "program_changed")
            if cycle is not None and cycle["program"] is None:
                cycle["program"] = self.programs[key]
        else:
            self.programs.pop(key, None)
        if cycle is None:
            if running:
                target = self.active if complete_start else self._partial
                target[key] = {
                    "at": now,
                    "started": now,
                    "program": self.programs.get(key),
                    "power": power,
                    "energy": energy,
                    "first_energy": energy,
                    "wh": 0.0,
                    "power_ok": power is not None,
                    "energy_ok": energy is not None,
                    "valid": valid and complete_start,
                    "reasons": ([] if complete_start else ["missing_start"])
                    + ([] if valid else [invalid_reason or "detection_unknown"]),
                    "warnings": [],
                }
            return
        seconds = (now - cycle["at"]).total_seconds()
        if seconds < 0:
            self._invalidate(cycle, "clock_changed")
        elif seconds > MAX_MEASUREMENT_GAP_S:
            self._invalidate(cycle, "measurement_gap")
        if not valid:
            self._invalidate(
                cycle, invalid_reason or ("detection_unknown" if running else "aborted")
            )
        if power is None:
            cycle["power_ok"] = False
        if cycle["power_ok"]:
            assert cycle["power"] is not None
            cycle["wh"] += cycle["power"] * max(0, seconds) / 3600
        if energy is None:
            cycle["energy_ok"] = False
        elif cycle["energy"] is not None and energy < cycle["energy"]:
            cycle["energy_ok"] = False
            if "counter_reset" not in cycle["warnings"]:
                cycle["warnings"].append("counter_reset")
        cycle.update({"at": now, "power": power, "energy": energy})
        if not running:
            self._finish(key, cycle, now)

    @staticmethod
    def _invalidate(cycle: ActiveCycle, reason: str) -> None:
        cycle["valid"] = False
        if reason not in cycle["reasons"]:
            cycle["reasons"].append(reason)

    @staticmethod
    def _measurement(cycle: ActiveCycle) -> CycleMeasurement:
        measured = None
        source: MeasurementSource | None = None
        if cycle["energy_ok"]:
            assert cycle["energy"] is not None and cycle["first_energy"] is not None
            measured = cycle["energy"] - cycle["first_energy"]
            source = "energy_counter"
        elif cycle["power_ok"]:
            measured = cycle["wh"]
            source = "integrated_power"
        return {
            "started_at": cycle["started"].isoformat(),
            "observed_at": cycle["at"].isoformat(),
            "energy_wh": measured,
            "measurement_source": source,
            "complete": cycle["valid"] and measured is not None,
            "reasons": list(cycle["reasons"]),
            "warnings": list(cycle["warnings"]),
        }

    def _finish(self, key: str, cycle: ActiveCycle, now: datetime) -> None:
        measurement = self._measurement(cycle)
        measured = measurement["energy_wh"]
        hours = max(0.0, (now - cycle["started"]).total_seconds() / 3600)
        if measured is None:
            self._invalidate(cycle, "missing_measurement")
        elif not 0 < measured <= MAX_CYCLE_ENERGY_WH:
            self._invalidate(cycle, "invalid_energy")
        accepted = cycle["valid"]
        if accepted:
            assert measured is not None
            self.samples[key] = (self.samples.get(key, []) + [measured])[
                -MAX_CYCLE_SAMPLES:
            ]
            self._last_learned[key] = now.isoformat()
            name = cycle["program"]
            if name is not None and 0 < hours <= MAX_CYCLE_DURATION_H:
                profiles = self.program_samples.setdefault(key, {})
                profiles[name] = (
                    profiles.pop(name, []) + [ProgramSample(measured, hours)]
                )[-MAX_CYCLE_SAMPLES:]
                self._program_learned.setdefault(key, {})[name] = now.isoformat()
                while len(profiles) > MAX_PROGRAMS:
                    removed = next(iter(profiles))
                    del profiles[removed]
                    self._program_learned[key].pop(removed, None)
            elif name is not None:
                # Aggregate energy still obeys its existing acceptance contract;
                # only a program's paired duration sample has this extra bound.
                cycle["warnings"].append("invalid_duration")
        self._append_history(
            key,
            {
                "program": cycle["program"],
                "started_at": cycle["started"].isoformat(),
                "ended_at": now.isoformat(),
                "duration_h": hours,
                "energy_wh": measured,
                "measurement_source": measurement["measurement_source"],
                "complete": accepted,
                "accepted": accepted,
                "reasons": list(cycle["reasons"]),
                "warnings": list(cycle["warnings"]),
            },
        )
        self.active.pop(key, None)
        self._partial.pop(key, None)

    def _append_history(self, key: str, item: CycleHistory) -> None:
        self._history[key] = (self._history.get(key, []) + [item])[-MAX_CYCLE_HISTORY:]

    def discard(
        self, key: str, now: datetime, reason: str = "detection_unknown"
    ) -> None:
        """End an observation explicitly, without converting it to training data."""
        cycle = self.active.get(key) or self._partial.get(key)
        if cycle is not None:
            self._invalidate(cycle, reason)
            self._finish(key, cycle, now)
        self.programs.pop(key, None)

    def snapshot(self, key: str) -> LearningSnapshot:
        """Return independent display data; reading it never observes or learns."""
        values = self.samples.get(key, [])
        device: DeviceProfile | None = (
            {
                "energy_wh": median(values),
                "energy_min_wh": min(values),
                "energy_max_wh": max(values),
                "count": len(values),
                "last_learned_at": self._last_learned.get(key),
            }
            if values
            else None
        )
        profiles: list[ProgramProfile] = []
        for name, samples in self.program_samples.get(key, {}).items():
            profiles.append(
                {
                    "program": name,
                    "energy_wh": median(s.energy_wh for s in samples),
                    "energy_min_wh": min(s.energy_wh for s in samples),
                    "energy_max_wh": max(s.energy_wh for s in samples),
                    "duration_h": median(s.duration_h for s in samples),
                    "duration_min_h": min(s.duration_h for s in samples),
                    "duration_max_h": max(s.duration_h for s in samples),
                    "count": len(samples),
                    "last_learned_at": self._program_learned.get(key, {}).get(name),
                }
            )
        cycle = self.active.get(key) or self._partial.get(key)
        history = deepcopy(self._history.get(key, []))
        state: LearningState = "learned" if device or profiles else "no_data"
        if cycle is not None:
            state = "measuring" if cycle["valid"] else "invalid"
        elif history and not history[-1]["accepted"]:
            state = "invalid"
        return {
            "profiles": profiles,
            "device_profile": device,
            "history": history,
            "state": state,
            "measurement": self._measurement(cycle) if cycle is not None else None,
        }

    def metadata_payload(self) -> dict[str, object]:
        """Keep optional display metadata apart from the legacy sample formats."""
        interrupted = {}
        for key, cycle in (self.active | self._partial).items():
            measurement = self._measurement(cycle)
            interrupted[key] = {
                "program": cycle["program"],
                "started_at": measurement["started_at"],
                "ended_at": measurement["observed_at"],
                "duration_h": max(
                    0.0, (cycle["at"] - cycle["started"]).total_seconds() / 3600
                ),
                "energy_wh": measurement["energy_wh"],
                "measurement_source": measurement["measurement_source"],
                "complete": False,
                "accepted": False,
                "reasons": list(cycle["reasons"]),
                "warnings": list(cycle["warnings"]),
            }
        return deepcopy(
            {
                "version": LEARNING_METADATA_VERSION,
                "history": self._history,
                "last_learned_at": self._last_learned,
                "program_learned_at": self._program_learned,
                "interrupted": interrupted,
            }
        )

    def restore_metadata(self, data: object, now: datetime | None = None) -> None:
        """Validate optional metadata; restart markers can never teach samples.

        The end is the last observation before shutdown, not the restore time:
        an appliance may have finished at any point during the missing interval.
        Legacy samples intentionally retain an unknown learning timestamp.
        """
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data.get("version") != LEARNING_METADATA_VERSION
        ):
            return
        history = data.get("history")
        if isinstance(history, dict):
            for key, items in history.items():
                if not isinstance(key, str) or not key or not isinstance(items, list):
                    continue
                self._history[key] = [
                    clean
                    for item in items[-MAX_CYCLE_HISTORY:]
                    if (clean := _clean_history(item)) is not None
                ]
        last = data.get("last_learned_at")
        if isinstance(last, dict):
            self._last_learned = {
                key: timestamp
                for key, value in last.items()
                if key in self.samples and (timestamp := _timestamp(value)) is not None
            }
        programs = data.get("program_learned_at")
        if isinstance(programs, dict):
            for key, values in programs.items():
                if key not in self.program_samples or not isinstance(values, dict):
                    continue
                self._program_learned[key] = {
                    name: timestamp
                    for name, value in values.items()
                    if name in self.program_samples[key]
                    and (timestamp := _timestamp(value)) is not None
                }
        interrupted = data.get("interrupted")
        if isinstance(interrupted, dict):
            for key, item in interrupted.items():
                if not isinstance(key, str) or not key:
                    continue
                clean = _clean_history(item)
                if clean is None:
                    continue
                clean["complete"] = clean["accepted"] = False
                if "restart" not in clean["reasons"]:
                    clean["reasons"].append("restart")
                self._append_history(key, clean)


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    return (
        parsed.isoformat() if parsed is not None and parsed.tzinfo is not None else None
    )


def _clean_history(value: object) -> CycleHistory | None:
    """Discard a damaged observation independently from all valid learning."""
    if not isinstance(value, dict):
        return None
    start, end = _timestamp(value.get("started_at")), _timestamp(value.get("ended_at"))
    duration, energy = value.get("duration_h"), value.get("energy_wh")
    program = value.get("program")
    source = value.get("measurement_source")
    reasons, warnings = value.get("reasons"), value.get("warnings")
    accepted, complete = value.get("accepted"), value.get("complete")
    if (
        start is None
        or end is None
        or (
            program is not None
            and (not isinstance(program, str) or not 0 < len(program) <= 255)
        )
        or not _nonnegative(duration)
        or (energy is not None and not _nonnegative(energy))
        or source not in (None, "energy_counter", "integrated_power")
        or not isinstance(accepted, bool)
        or not isinstance(complete, bool)
        or not isinstance(reasons, list)
        or not isinstance(warnings, list)
        or len(reasons) + len(warnings) > MAX_CYCLE_NOTES
        or not all(
            isinstance(item, str) and 0 < len(item) <= 64 for item in reasons + warnings
        )
        or (
            accepted
            and (
                not complete
                or reasons
                or energy is None
                or not 0 < energy <= MAX_CYCLE_ENERGY_WH
                or source is None
                or datetime.fromisoformat(end) < datetime.fromisoformat(start)
            )
        )
    ):
        return None
    result: CycleHistory = {
        "program": program,
        "started_at": start,
        "ended_at": end,
        "duration_h": float(duration),
        "energy_wh": float(energy) if energy is not None else None,
        "measurement_source": cast(MeasurementSource | None, source),
        "complete": complete,
        "accepted": accepted,
        "reasons": list(reasons),
        "warnings": list(warnings),
    }
    return result


def _nonnegative(value: object) -> TypeGuard[int | float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False
