"""Bounded SOC-coordinate observations, independent of HA and actuator control.

The BMS coordinate may normally reconcile near full charge. Terminal energy
does not independently measure absolute SOC or justify changing capacity.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Literal

WINDOW_SECONDS = 300
MIN_COVERED_SECONDS = 60
MIN_SOC_PUBLICATIONS = 3
MAX_MEASUREMENT_AGE_SECONDS = 30
SETTLING_SECONDS = 600
MAX_OBSERVATIONS = 4096
MAX_EVENTS = 8


@dataclass(frozen=True)
class SocObservation:
    at: float
    soc: float
    power_w: float  # positive charge, matching Victron terminal power
    voltage_v: float
    soc_published_at: float
    power_published_at: float
    voltage_published_at: float


@dataclass(frozen=True)
class _Point:
    observation: SocObservation
    charge_wh: float
    discharge_wh: float


class SocConsistencyMonitor:
    """One episode authority shared by rolling and single-jump evidence.

    This class reports evidence only. Its caller decides when an independently
    reviewed episode may affect learning/permission; it never sends commands.
    """

    def __init__(self) -> None:
        self.fingerprint = ""
        self.epoch = 0
        self.generation = 0
        self.phase: Literal["tracking", "reconciling", "settling"] = "tracking"
        self.has_reconciled = False
        self.reason = "warming_up"
        self.events: list[dict[str, Any]] = []
        self._points: deque[_Point] = deque(maxlen=MAX_OBSERVATIONS)
        self._charge_wh = 0.0
        self._discharge_wh = 0.0
        self._settled_seconds = 0.0
        self._last_residual: dict[str, Any] | None = None
        self._parameters: tuple[float, float, float] | None = None

    def bind(self, fingerprint: str, *, initial_epoch: int = 0) -> None:
        if fingerprint != self.fingerprint:
            if self.fingerprint:
                self.epoch += 1
                self.generation += 1
            else:
                self.epoch = max(self.epoch, initial_epoch)
            self.fingerprint = fingerprint
            self._gap("configuration_changed")

    def _phase(self, phase: Literal["tracking", "reconciling", "settling"]) -> None:
        if self.phase != phase:
            self.phase = phase
            self.generation += 1

    def _gap(self, reason: str) -> None:
        self._points.clear()
        self._charge_wh = self._discharge_wh = 0.0
        self._settled_seconds = 0.0
        self._last_residual = None
        if self.phase != "tracking":
            self._phase("reconciling")
        self.reason = reason

    @staticmethod
    def _valid(sample: SocObservation) -> bool:
        return (
            all(isfinite(value) for value in asdict(sample).values())
            and 0 <= sample.soc <= 100
            and 40 <= sample.voltage_v <= 60
            and abs(sample.power_w / sample.voltage_v) < 100
            and all(
                0 <= sample.at - stamp <= MAX_MEASUREMENT_AGE_SECONDS
                for stamp in (
                    sample.soc_published_at,
                    sample.power_published_at,
                    sample.voltage_published_at,
                )
            )
        )

    @staticmethod
    def _covered(previous: SocObservation, current: SocObservation) -> bool:
        if not 0 < current.at - previous.at <= MAX_MEASUREMENT_AGE_SECONDS:
            return False
        for before, after in (
            (previous.soc_published_at, current.soc_published_at),
            (previous.power_published_at, current.power_published_at),
            (previous.voltage_published_at, current.voltage_published_at),
        ):
            # Fresh endpoints alone cannot bridge an expired intermediate hold.
            if after < before or (
                after > before and after - before > MAX_MEASUREMENT_AGE_SECONDS
            ):
                return False
        return True

    @staticmethod
    def _energy(
        previous: SocObservation, current: SocObservation
    ) -> tuple[float, float]:
        change = min(current.at, max(previous.at, current.power_published_at))
        intervals = (
            (change - previous.at, previous.power_w),
            (current.at - change, current.power_w),
        )
        return (
            sum(seconds * max(power, 0.0) / 3600 for seconds, power in intervals),
            sum(seconds * max(-power, 0.0) / 3600 for seconds, power in intervals),
        )

    def observe(
        self,
        sample: SocObservation | None,
        *,
        capacity_wh: float,
        eta_charge: float = 1.0,
        eta_discharge: float = 1.0,
        power_trace_complete: bool = False,
    ) -> bool:
        """Return whether an episode/phase changed, never an actuation request."""
        identity = (self.epoch, self.generation, self.phase)
        if not power_trace_complete:
            # Fresh sampled endpoints cannot rule out unobserved power changes
            # between polling ticks. Only the original publication stream may
            # assert continuity (including unchanged-value publications).
            self._gap("power_trace_incomplete")
            return identity != (self.epoch, self.generation, self.phase)
        if (
            sample is None
            or not self._valid(sample)
            or not isfinite(capacity_wh)
            or capacity_wh <= 0
            or not 0 < eta_charge <= 1
            or not 0 < eta_discharge <= 1
        ):
            self._gap("measurement_unavailable")
            return identity != (self.epoch, self.generation, self.phase)
        parameters = capacity_wh, eta_charge, eta_discharge
        if self._parameters is not None and self._parameters != parameters:
            self._gap("model_parameters_changed")
        self._parameters = parameters
        previous = self._points[-1].observation if self._points else None
        # Replacing a same-time channel endpoint recomputes its energy, not
        # another elapsed interval. Settling must count real coverage once.
        covered_increment = max(0.0, sample.at - previous.at) if previous else 0.0
        if previous is not None and sample.at < previous.at:
            return False
        if previous is not None and sample.at == previous.at:
            # Separate channels can publish at one clock instant. Update that
            # endpoint (not its energy twice), so SOC-first and power-first
            # packet delivery have the same coordinate decision.
            if not any(
                after > before
                for before, after in (
                    (previous.soc_published_at, sample.soc_published_at),
                    (previous.power_published_at, sample.power_published_at),
                    (previous.voltage_published_at, sample.voltage_published_at),
                )
            ):
                return False
            if len(self._points) == 1:
                self._points[-1] = _Point(sample, self._charge_wh, self._discharge_wh)
                return False
            self._points.pop()
            point = self._points[-1]
            self._charge_wh, self._discharge_wh = point.charge_wh, point.discharge_wh
            previous = point.observation
        if previous is not None and not self._covered(previous, sample):
            self._gap("measurement_gap")
            previous = None
        elapsed = sample.at - previous.at if previous else 0.0
        if previous:
            charge, discharge = self._energy(previous, sample)
            self._charge_wh += charge
            self._discharge_wh += discharge
        self._points.append(_Point(sample, self._charge_wh, self._discharge_wh))
        while (
            self._points and sample.at - self._points[0].observation.at > WINDOW_SECONDS
        ):
            self._points.popleft()
        anchor = self._points[0]
        covered = sample.at - anchor.observation.at
        charge = self._charge_wh - anchor.charge_wh
        discharge = self._discharge_wh - anchor.discharge_wh
        gross_soc = (
            (charge * eta_charge + discharge / eta_discharge) / capacity_wh * 100
        )
        signed_soc = (
            (charge * eta_charge - discharge / eta_discharge) / capacity_wh * 100
        )
        delta = sample.soc - anchor.observation.soc
        publications = len(
            {point.observation.soc_published_at for point in self._points}
        )
        qualified = (
            covered >= MIN_COVERED_SECONDS and publications >= MIN_SOC_PUBLICATIONS
        )
        rolling = qualified and abs(delta) > 2 + 2 * gross_soc
        fast = False
        if previous:
            fast_bound = (
                max(abs(previous.power_w), abs(sample.power_w))
                * elapsed
                / 3600
                / capacity_wh
                * 100
                * 2
                * max(1.0, 1 / eta_discharge)
                + 2
            )
            fast = abs(sample.soc - previous.soc) > fast_bound
        self._last_residual = {
            "covered_seconds": covered,
            "soc_publications": publications,
            "charge_terminal_wh": charge,
            "discharge_terminal_wh": discharge,
            "observed_soc_delta_percent": delta,
            "energy_equivalent_soc_delta_percent": signed_soc,
            "residual_percent": delta - signed_soc,
            "generous_bound_percent": 2 + 2 * gross_soc,
            "qualified": qualified,
        }
        if rolling or fast:
            if self.phase == "tracking":
                self.epoch += 1
                self.has_reconciled = True
                self.events.append(
                    {
                        "epoch": self.epoch,
                        "at": sample.at,
                        "kind": "soc_coordinate_adjustment",
                        "level": "info",
                        "detector": "single_jump" if fast else "rolling_energy",
                        **self._last_residual,
                    }
                )
                self.events = self.events[-MAX_EVENTS:]
            self._phase("reconciling")
            self._settled_seconds = 0.0
            self._points.clear()
            self._points.append(_Point(sample, self._charge_wh, self._discharge_wh))
            self.reason = "soc_coordinate_adjustment"
        elif self.phase != "tracking":
            if qualified:
                if self.phase == "reconciling":
                    self._phase("settling")
                    self._settled_seconds = 0.0
                else:
                    self._settled_seconds += covered_increment
                if self._settled_seconds >= SETTLING_SECONDS:
                    self._phase("tracking")
                    self.reason = "coordinate_tracking_resumed"
                else:
                    self.reason = "covered_settling"
            else:
                self.reason = "warming_up"
        else:
            self.reason = "consistent" if qualified else "warming_up"
        return identity != (self.epoch, self.generation, self.phase)

    def diagnostic(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "generation": self.generation,
            "phase": self.phase,
            "reason": self.reason,
            "level": "info",
            "settled_covered_seconds": self._settled_seconds,
            "residual": self._last_residual,
            "last_event": self.events[-1] if self.events else None,
        }

    def export(self) -> dict[str, Any]:
        """Only evidence/provenance survives; no integration or settling cursor."""
        return {
            "version": 1,
            "fingerprint": self.fingerprint,
            "epoch": self.epoch,
            "generation": self.generation,
            "phase": self.phase,
            "has_reconciled": self.has_reconciled,
            "events": list(self.events),
        }

    def restore(
        self, data: Any, *, initial_epoch: int = 0, fingerprint: str = ""
    ) -> None:
        self._gap("restart_warming_up")
        self._parameters = None
        self.epoch = initial_epoch
        self.fingerprint = fingerprint
        if data is None:
            return  # Version1 learner migration: no invented SOC episode.
        try:
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or data.get("phase") not in ("tracking", "reconciling", "settling")
                or type(data.get("epoch")) is not int
                or data["epoch"] < 0
                or type(data.get("generation")) is not int
                or data["generation"] < 0
                or not isinstance(data.get("fingerprint"), str)
                or len(data["fingerprint"]) > 4096
                or type(data.get("has_reconciled")) is not bool
                or not isinstance(data.get("events"), list)
            ):
                raise ValueError("Invalid coordinate evidence")
            self.epoch = max(initial_epoch, data["epoch"])
            self.generation = data["generation"]
            self.fingerprint = data["fingerprint"]
            phase = data["phase"]
            self.phase = phase
            self.has_reconciled = data["has_reconciled"] or phase != "tracking"
            self.events = []
            for event in data["events"][-MAX_EVENTS:]:
                if (
                    not isinstance(event, dict)
                    or type(event.get("epoch")) is not int
                    or not 0 <= event["epoch"] <= self.epoch
                    or not isinstance(event.get("at"), (int, float))
                    or not isfinite(event["at"])
                    or event["at"] < 0
                    or event.get("kind") != "soc_coordinate_adjustment"
                ):
                    continue
                clean: dict[str, Any] = {
                    "epoch": event["epoch"],
                    "at": event["at"],
                    "kind": "soc_coordinate_adjustment",
                    "level": "info",
                    "detector": event.get("detector")
                    if event.get("detector") in ("single_jump", "rolling_energy")
                    else "unknown",
                    "qualified": event.get("qualified") is True,
                }
                for key in (
                    "covered_seconds",
                    "soc_publications",
                    "charge_terminal_wh",
                    "discharge_terminal_wh",
                    "observed_soc_delta_percent",
                    "energy_equivalent_soc_delta_percent",
                    "residual_percent",
                    "generous_bound_percent",
                ):
                    value = event.get(key)
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and isfinite(value)
                        and abs(value) <= 1e9
                    ):
                        clean[key] = value
                self.events.append(clean)
            self.has_reconciled |= bool(self.events)
            if self.phase == "settling":
                self._phase("reconciling")
        except TypeError, ValueError:
            # Lost derived provenance cannot certify a resumed economic grant.
            self.phase = "reconciling"
            self.generation += 1
            self.has_reconciled = True
