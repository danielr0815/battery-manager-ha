"""Robust per-load planning-power estimation (docs/F-ROBUST-POWER.md).

Operator spec (2026-07-18, binding): the planning power of a surplus load is
a robust average over the recent run window. SHORT dips and spikes — defrost
pauses, compressor inrush, inverter-transfer transients — must ALWAYS be
ignored; SUSTAINED level changes must be adopted (a durably low draw is
real), and when the normal level returns for a longer stretch (~10 min) it
must be re-learned quickly. The estimator must generalise across load types
(operator-changed powerstation charge rates, battery-side throttling), so
legitimate levels ABOVE the configured nominal power are allowed — there is
deliberately no nominal clamp here.

Incident that forced this design: at the 2026-07-18 06:21 inverter cutoff
the dehumidifier compressor's restart transient (1711 W, held ~60 s by the
measuring plug) was blended into the previous EMA (0.3·1711 + 0.7·433 ≈ 818)
and the run-max rule froze and persisted 818 W for a 426 W device.

Mechanics: samples are TIME-WEIGHTED — each accepted sample covers the span
until the next one (capped at GAP_CAP_S), clipped to the rolling window. The
estimate is the weighted median over WINDOW_S: a level occupying less than
half the covered time (a spike, a short defrost dip) cannot move it, while a
sustained level becomes the majority after ~WINDOW_S/2. A FAST-ADOPT path
covers the operator's "10 minutes of restored normal load" rule: when the
last FAST_WINDOW_S are internally stable (P10/P90 within STABLE_BAND of
their median) and that sub-median deviates by more than FAST_DEVIATION from
the slow median, the sub-median wins immediately. Below WARMUP_COVERAGE_S of
accepted coverage no estimate is produced at all — start-up inrush can never
be learned (supersedes the F-PLANNER-HONESTY R2a seed rule).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .load_profile import weighted_quantile

WINDOW_S = 1800.0  # robust window (operator sketch: ~30 min)
FAST_WINDOW_S = 600.0  # fast re-learn sub-window (operator: ~10 min)
STABLE_BAND = 0.20  # P10/P90 within ±20 % of the sub-median = "stable"
FAST_DEVIATION = 0.20  # sub-median must deviate >20 % from the slow median
WARMUP_COVERAGE_S = 300.0  # no estimate below 5 min of accepted coverage
GAP_CAP_S = 300.0  # one sample covers at most 5 min of silence
DOMINANCE_MAX = 0.8  # no single sample may carry >80 % of a window's weight
MAX_SAMPLES = 720  # deque bound for callers (1 h at 5 s event cadence)


@dataclass(frozen=True)
class PowerEstimate:
    """Result of `robust_power_estimate`.

    `watts` is None while the run has not yet accumulated
    WARMUP_COVERAGE_S of accepted samples (warm-up: nothing is served or
    learned). `coverage_s` is the time-weighted accepted coverage inside
    the window; `fast_adopted` marks an estimate taken from the stable
    FAST_WINDOW_S sub-window instead of the slow median.
    """

    watts: float | None
    coverage_s: float
    fast_adopted: bool = False


def _clipped_weights(
    samples: Sequence[tuple[datetime, float]],
    now: datetime,
    window_s: float,
) -> tuple[list[float], list[float]]:
    """Per-sample (value, covered-seconds) pairs clipped to the window.

    Sample i covers [t_i, min(t_{i+1}, t_i + GAP_CAP_S)); the last sample
    covers up to min(now, t_n + GAP_CAP_S). Each span is then clipped to
    [now - window_s, now]; zero-weight samples are dropped.
    """
    window_start = now.timestamp() - window_s
    now_ts = now.timestamp()
    values: list[float] = []
    weights: list[float] = []
    for i, (ts, watts) in enumerate(samples):
        start = ts.timestamp()
        if i + 1 < len(samples):
            end = min(samples[i + 1][0].timestamp(), start + GAP_CAP_S)
        else:
            end = min(now_ts, start + GAP_CAP_S)
        lo = max(start, window_start)
        hi = min(end, now_ts)
        weight = hi - lo
        if weight <= 0.0:
            continue
        values.append(watts)
        weights.append(weight)
    return values, weights


def robust_power_estimate(
    samples: Sequence[tuple[datetime, float]], now: datetime
) -> PowerEstimate:
    """Robust time-weighted planning-power estimate over the run window.

    `samples` are (timestamp, watts) pairs of ACCEPTED readings (the caller
    applies the standby bar and the runs-at-BM's-request gate), in
    chronological order.
    """
    values, weights = _clipped_weights(samples, now, WINDOW_S)
    coverage = sum(weights)
    # Warm-up needs enough covered time AND a window not dominated by one
    # sample: with GAP_CAP_S == WARMUP_COVERAGE_S a SINGLE sample followed
    # by silence reaches exactly the coverage bar, and at poll cadence the
    # newest sample carries ~zero weight — a lone start-up/transfer
    # transient could become the estimate and be learned (the incident
    # class this module exists to prevent; review finding 2026-07-18).
    # The dominance bar makes the median provably multi-sample-backed.
    if (
        coverage < WARMUP_COVERAGE_S
        or len(values) < 2
        or max(weights) > DOMINANCE_MAX * coverage
    ):
        return PowerEstimate(None, coverage)
    slow = weighted_quantile(values, weights, 0.5)

    fast_values, fast_weights = _clipped_weights(samples, now, FAST_WINDOW_S)
    fast_coverage = sum(fast_weights)
    # The stability test is vacuous when one sample dominates the window
    # (P10 == P90 == it), e.g. one accepted glitch held by GAP_CAP through
    # a sensor outage — apply the same dominance bar here.
    if (
        fast_coverage >= WARMUP_COVERAGE_S
        and len(fast_values) >= 2
        and max(fast_weights) <= DOMINANCE_MAX * fast_coverage
    ):
        fast = weighted_quantile(fast_values, fast_weights, 0.5)
        p10 = weighted_quantile(fast_values, fast_weights, 0.10)
        p90 = weighted_quantile(fast_values, fast_weights, 0.90)
        stable = (
            fast > 0.0
            and p10 >= (1.0 - STABLE_BAND) * fast
            and p90 <= (1.0 + STABLE_BAND) * fast
        )
        deviates = slow <= 0.0 or abs(fast - slow) > FAST_DEVIATION * slow
        if stable and deviates:
            return PowerEstimate(fast, coverage, fast_adopted=True)
    return PowerEstimate(slow, coverage)
