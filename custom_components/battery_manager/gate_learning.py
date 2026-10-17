"""Bounded recent voltage/SOC observations, independent of BMS SOC correction.

Only stable, naturally observed battery voltage is evidence. PSU regulation
clamps voltage while SOC changes, so it must never teach a fictitious SOC drift.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

from .core.model import VoltageGateBand

# One sample per five minutes, at most fourteen days. Two-day half-life lets
# repeated current observations replace a pre-recalibration SOC coordinate.
SAMPLE_SECONDS = 300
STABLE_SECONDS = 60
MAX_AGE_SECONDS = 14 * 86400
HALF_LIFE_SECONDS = 2 * 86400
MAX_SAMPLES = MAX_AGE_SECONDS // SAMPLE_SECONDS
VOLTAGE_WINDOW = 0.04
CURRENT_EDGES = (-100.0, -5.0, -2.0, -1.0, -0.25, 0.0, 0.25, 1.0, 2.0, 5.0, 100.0)


@dataclass(frozen=True)
class GateSample:
    at: float
    soc: float
    voltage: float
    current: float  # positive discharge, unlike the Victron battery-power sign


def _quantile(values: list[tuple[float, float]], fraction: float) -> float:
    ordered = sorted(values)
    target = sum(weight for _, weight in ordered) * fraction
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


class GateLearner:
    """Learn the BMS's current reported coordinate, not a universal OCV curve."""

    def __init__(self) -> None:
        self.samples: list[GateSample] = []
        self.fingerprint: str = ""
        self.epoch = 0
        self.reason = "warming_up"
        self._previous: GateSample | None = None
        self._stable_since: float | None = None

    def bind(self, fingerprint: str) -> None:
        if fingerprint != self.fingerprint:
            self.samples.clear()
            self._previous = None
            self._stable_since = None
            self.fingerprint = fingerprint
            self.epoch += 1
            self.reason = "configuration_changed"

    def observe(
        self, sample: GateSample | None, *, eligible: bool, capacity_wh: float
    ) -> bool:
        """Sample stable episodes; return whether persistent evidence changed."""
        if sample is None:
            self._previous = None
            self._stable_since = None
            self.reason = "measurement_unavailable"
            return False
        previous = self._previous
        if previous is not None and sample.at <= previous.at:
            return False  # Duplicate/out-of-order callbacks are not new evidence.
        self._previous = sample
        changed = False
        self.samples = [
            p for p in self.samples if 0 <= sample.at - p.at <= MAX_AGE_SECONDS
        ]
        if previous is not None:
            elapsed = sample.at - previous.at
            # An intentionally generous energy bound avoids interpreting a real
            # load change or 1%-quantized SOC as a BMS recalibration. Gaps are
            # not evidence: the unobserved current could have been very different.
            energy_bound = (
                max(
                    abs(previous.current * previous.voltage),
                    abs(sample.current * sample.voltage),
                )
                * max(0, elapsed)
                / 3600
                / capacity_wh
                * 100
                * 2
                + 2
            )
            if (
                0 < elapsed <= SAMPLE_SECONDS
                and abs(sample.soc - previous.soc) > energy_bound
            ):
                self.samples.clear()
                self.epoch += 1
                self._stable_since = None
                self.reason = "recalibration_suspected"
                return True
        if not eligible:
            self._stable_since = None
            self.reason = "source_clamped_or_unknown"
            return changed
        if (
            previous is None
            or not 0 < sample.at - previous.at <= SAMPLE_SECONDS
            or previous.current * sample.current < 0
            or abs(sample.current - previous.current)
            > max(0.15, abs(sample.current) * 0.2)
            or abs(sample.voltage - previous.voltage) > 0.05
        ):
            self._stable_since = sample.at
        if self._stable_since is None:
            self._stable_since = sample.at
        if sample.at - self._stable_since < STABLE_SECONDS:
            self.reason = "settling"
            return changed
        if self.samples and sample.at - self.samples[-1].at < SAMPLE_SECONDS:
            return changed
        self.samples.append(sample)
        self.samples = self.samples[-MAX_SAMPLES:]
        self.reason = "learning"
        return True

    def bands(
        self, now: float, thresholds: tuple[float, float, float]
    ) -> tuple[VoltageGateBand, ...]:
        result = []
        for low, high in zip(CURRENT_EDGES[:-1], CURRENT_EDGES[1:], strict=True):
            points = [
                p
                for p in self.samples
                if low <= p.current < high and 0 <= now - p.at <= MAX_AGE_SECONDS
            ]
            estimates: list[float | None] = []
            spreads = []
            expiries = []
            evidence: list[GateSample] = []
            for voltage in thresholds:
                near = [p for p in points if abs(p.voltage - voltage) <= VOLTAGE_WINDOW]
                # A single burst is not a learned crossing. Retain a minimum
                # half-hour observation span and reject broad contradictory SOCs.
                if (
                    len(near) < 3
                    or max(p.at for p in near) - min(p.at for p in near) < 1800
                ):
                    estimates.append(None)
                    continue
                values = [
                    (p.soc, 2 ** (-(now - p.at) / HALF_LIFE_SECONDS)) for p in near
                ]
                spread = _quantile(values, 0.9) - _quantile(values, 0.1)
                if spread > 5:
                    estimates.append(None)
                    continue
                estimates.append(_quantile(values, 0.5))
                spreads.append(spread)
                evidence.extend(near)
                expiries.append(max(p.at for p in near) + MAX_AGE_SECONDS)
            ordered = sorted(
                (v, estimate)
                for v, estimate in zip(thresholds, estimates, strict=True)
                if estimate is not None
            )
            if any(b[1] < a[1] for a, b in zip(ordered[:-1], ordered[1:], strict=True)):
                continue  # Partially shifted coordinates must not invert hysteresis.
            if evidence:
                observed_low = min(p.current for p in evidence)
                observed_high = max(p.current for p in evidence)
                low = max(low, observed_low - max(0.15, abs(observed_low) * 0.2))
                high = min(high, observed_high + max(0.15, abs(observed_high) * 0.2))
                result.append(
                    VoltageGateBand(
                        low,
                        high,
                        estimates[0],
                        estimates[1],
                        estimates[2],
                        max(spreads),
                        len(set(evidence)),
                        min(expiries),
                    )
                )
        return tuple(result)

    def export(self) -> dict[str, Any]:
        return {
            "version": 1,
            "fingerprint": self.fingerprint,
            "epoch": self.epoch,
            "samples": [asdict(p) for p in self.samples],
        }

    def restore(self, data: Any) -> None:
        """Corrupt derived evidence is disposable; never restore live timers."""
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        try:
            points = [GateSample(**p) for p in data["samples"][-MAX_SAMPLES:]]
            if any(
                not all(
                    isinstance(v, (int, float)) and isfinite(v)
                    for v in asdict(p).values()
                )
                or not (
                    0 <= p.soc <= 100
                    and 40 <= p.voltage <= 60
                    and -100 <= p.current < 100
                )
                for p in points
            ):
                return
            if not isinstance(data["fingerprint"], str) or not isinstance(
                data["epoch"], int
            ):
                return
            self.samples = sorted(points, key=lambda p: p.at)
            self.fingerprint = data["fingerprint"]
            self.epoch = data["epoch"]
        except KeyError, TypeError, ValueError:
            return
