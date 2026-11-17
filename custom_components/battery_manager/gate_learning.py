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


@dataclass(frozen=True)
class ArchivedGateSample:
    epoch: int
    fingerprint: str
    reason: str
    sample: GateSample


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
        self.history: list[ArchivedGateSample] = []
        self.fingerprint: str = ""
        self.epoch = 0
        self.reason = "warming_up"
        self._previous: GateSample | None = None
        self._stable_since: float | None = None

    def bind(self, fingerprint: str, *, epoch: int | None = None) -> None:
        if fingerprint != self.fingerprint:
            self.begin_epoch(
                self.epoch + 1 if epoch is None else epoch, "configuration_changed"
            )
            self.fingerprint = fingerprint
            self.reason = "configuration_changed"

    def begin_epoch(self, epoch: int, reason: str) -> None:
        if epoch == self.epoch:
            return
        self.quarantine_active(reason)
        self.epoch = epoch
        self.reason = reason

    def quarantine_active(self, reason: str) -> None:
        self.history.extend(
            ArchivedGateSample(self.epoch, self.fingerprint, reason, sample)
            for sample in self.samples
        )
        self.samples.clear()
        self.reset_stability()
        self.reason = reason
        if self.history:
            self._prune(max(item.sample.at for item in self.history))

    def reset_stability(self) -> None:
        self._previous = None
        self._stable_since = None

    def expire(self, now: float) -> bool:
        before = len(self.samples) + len(self.history)
        self._prune(now)
        return before != len(self.samples) + len(self.history)

    def _prune(self, now: float) -> None:
        active: list[tuple[float, bool, object]] = [
            (point.at, True, point) for point in self.samples
        ]
        archived: list[tuple[float, bool, object]] = [
            (item.sample.at, False, item) for item in self.history
        ]
        retained = sorted(
            (
                item
                for item in active + archived
                if 0 <= now - item[0] <= MAX_AGE_SECONDS
            ),
            key=lambda item: item[0],
        )[-MAX_SAMPLES:]
        active_ids = {id(item[2]) for item in retained if item[1]}
        archived_ids = {id(item[2]) for item in retained if not item[1]}
        self.samples = [point for point in self.samples if id(point) in active_ids]
        self.history = [item for item in self.history if id(item) in archived_ids]

    def quarantine(self, sample: GateSample, reason: str) -> bool:
        self.reset_stability()
        self._prune(sample.at)
        if self.history and sample.at - self.history[-1].sample.at < SAMPLE_SECONDS:
            return False
        self.history.append(
            ArchivedGateSample(self.epoch, self.fingerprint, reason, sample)
        )
        self._prune(sample.at)
        self.reason = "coordinate_quarantined"
        return True

    def observe(
        self,
        sample: GateSample | None,
        *,
        eligible: bool,
        capacity_wh: float,
        detect_recalibration: bool = True,
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
        self._prune(sample.at)
        if previous is not None and detect_recalibration:
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
                self.begin_epoch(self.epoch + 1, "recalibration_suspected")
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
        self._prune(sample.at)
        self.reason = "learning"
        return True

    def bands(
        self, now: float, thresholds: tuple[float, float, float]
    ) -> tuple[VoltageGateBand, ...]:
        return self.evaluate(now, thresholds)[0]

    def evaluate(
        self, now: float, thresholds: tuple[float, float, float]
    ) -> tuple[tuple[VoltageGateBand, ...], list[dict[str, Any]]]:
        """Project bands and explain qualification through the same filters."""
        result = []
        diagnostics = []
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
            roles = []
            for role, voltage in zip(
                ("on", "delivery", "off"), thresholds, strict=True
            ):
                near = [p for p in points if abs(p.voltage - voltage) <= VOLTAGE_WINDOW]
                values = [
                    (p.soc, 2 ** (-(now - p.at) / HALF_LIFE_SECONDS)) for p in near
                ]
                span = max(p.at for p in near) - min(p.at for p in near) if near else 0
                p10 = _quantile(values, 0.1) if values else None
                p90 = _quantile(values, 0.9) if values else None
                spread = p90 - p10 if p90 is not None and p10 is not None else None
                reason = (
                    "expired"
                    if not near
                    and any(
                        low <= p.current < high
                        and abs(p.voltage - voltage) <= VOLTAGE_WINDOW
                        and now - p.at > MAX_AGE_SECONDS
                        for p in self.samples
                    )
                    else "insufficient_samples"
                    if len(near) < 3
                    else "insufficient_span"
                    if span < 1800
                    else "conflicting_coordinates"
                    if spread is not None and spread > 5
                    else "qualified"
                )
                roles.append(
                    {
                        "role": role,
                        "voltage_v": voltage,
                        "near_count": len(near),
                        "span_seconds": span,
                        "p10_soc": p10,
                        "p50_soc": _quantile(values, 0.5) if values else None,
                        "p90_soc": p90,
                        "spread_percent": spread,
                        "reason": reason,
                        "valid_until": max(p.at for p in near) + MAX_AGE_SECONDS
                        if near
                        else None,
                    }
                )
                # A single burst is not a learned crossing. Retain a minimum
                # half-hour observation span and reject broad contradictory SOCs.
                if (
                    len(near) < 3
                    or max(p.at for p in near) - min(p.at for p in near) < 1800
                ):
                    estimates.append(None)
                    continue
                assert spread is not None
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
                for item in roles:
                    if item["reason"] == "qualified":
                        item["reason"] = "inconsistent_threshold_order"
                diagnostics.append(
                    {"current_min_a": low, "current_max_a": high, "roles": roles}
                )
                continue  # Partially shifted coordinates must not invert hysteresis.
            diagnostics.append(
                {"current_min_a": low, "current_max_a": high, "roles": roles}
            )
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
        return tuple(result), diagnostics

    def export(self) -> dict[str, Any]:
        return {
            "version": 2,
            "fingerprint": self.fingerprint,
            "epoch": self.epoch,
            "samples": [asdict(p) for p in self.samples],
            "history": [asdict(item) for item in self.history],
        }

    def restore(self, data: Any) -> None:
        """Corrupt derived evidence is disposable; never restore live timers."""
        if not isinstance(data, dict) or data.get("version") not in (1, 2):
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
            if (
                not isinstance(data["fingerprint"], str)
                or len(data["fingerprint"]) > 4096
                or type(data["epoch"]) is not int
                or data["epoch"] < 0
            ):
                return
            self.samples = sorted(points, key=lambda p: p.at)
            self.fingerprint = data["fingerprint"]
            self.epoch = data["epoch"]
            self.history = []
            if data["version"] == 2:
                for item in data.get("history", [])[-MAX_SAMPLES:]:
                    sample = GateSample(**item["sample"])
                    if (
                        type(item["epoch"]) is int
                        and item["epoch"] >= 0
                        and isinstance(item["fingerprint"], str)
                        and len(item["fingerprint"]) <= 4096
                        and isinstance(item["reason"], str)
                        and len(item["reason"]) <= 64
                        and all(isfinite(value) for value in asdict(sample).values())
                        and 0 <= sample.soc <= 100
                        and 40 <= sample.voltage <= 60
                        and -100 <= sample.current < 100
                    ):
                        self.history.append(
                            ArchivedGateSample(
                                item["epoch"],
                                item["fingerprint"],
                                item["reason"],
                                sample,
                            )
                        )
            if self.samples or self.history:
                self._prune(
                    max(
                        [p.at for p in self.samples]
                        + [item.sample.at for item in self.history]
                    )
                )
        except KeyError, TypeError, ValueError:
            return
