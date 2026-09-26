"""Measured appliance cycles (F-APPLIANCE-TELEMETRY).

Only complete, continuously observed cycles teach future runs. Active samples
are deliberately not restored: a restart cannot account for missing power.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import NamedTuple, TypedDict

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


class ApplianceLearning:
    """Bounded median of measured cycle energies, keyed by appliance subentry."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}
        self.active: dict[str, ActiveCycle] = {}
        self.program_samples: dict[str, dict[str, list[ProgramSample]]] = {}
        self.programs: dict[str, str | None] = {}

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
    ) -> None:
        """Integrate held power; prefer a continuous, non-resetting energy counter.

        Ten minutes is the maximum measurement gap. Missing samples invalidate
        that source for this cycle, rather than teaching an understated total.
        """
        cycle = self.active.get(key)
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
                cycle["valid"] = False
            if cycle is not None and cycle["program"] is None:
                cycle["program"] = self.programs[key]
        else:
            self.programs.pop(key, None)
        if cycle is None:
            if running and complete_start:
                self.active[key] = {
                    "at": now,
                    "started": now,
                    "program": self.programs.get(key),
                    "power": power,
                    "energy": energy,
                    "first_energy": energy,
                    "wh": 0.0,
                    "power_ok": power is not None,
                    "energy_ok": energy is not None,
                    "valid": valid,
                }
            return
        seconds = (now - cycle["at"]).total_seconds()
        if seconds < 0 or seconds > MAX_MEASUREMENT_GAP_S or not valid:
            cycle["valid"] = False
        if power is None:
            cycle["power_ok"] = False
        if cycle["power_ok"]:
            assert cycle["power"] is not None
            cycle["wh"] += cycle["power"] * max(0, seconds) / 3600
        if energy is None or (cycle["energy"] is not None and energy < cycle["energy"]):
            cycle["energy_ok"] = False
        cycle.update({"at": now, "power": power, "energy": energy})
        if not running:
            measured = None
            if cycle["energy_ok"]:
                assert energy is not None and cycle["first_energy"] is not None
                measured = energy - cycle["first_energy"]
            elif cycle["power_ok"]:
                measured = cycle["wh"]
            if (
                cycle["valid"]
                and measured is not None
                and 0 < measured <= MAX_CYCLE_ENERGY_WH
            ):
                self.samples[key] = (self.samples.get(key, []) + [measured])[
                    -MAX_CYCLE_SAMPLES:
                ]
                hours = (now - cycle["started"]).total_seconds() / 3600
                name = cycle["program"]
                if name is not None and 0 < hours <= MAX_CYCLE_DURATION_H:
                    profiles = self.program_samples.setdefault(key, {})
                    profiles[name] = (
                        profiles.pop(name, []) + [ProgramSample(measured, hours)]
                    )[-MAX_CYCLE_SAMPLES:]
                    while len(profiles) > MAX_PROGRAMS:
                        del profiles[next(iter(profiles))]
            del self.active[key]
