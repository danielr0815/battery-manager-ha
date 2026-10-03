"""Preserve DC service without spending an earlier improvement on a later outage."""

from datetime import UTC, datetime

from .model import DCDeficitInterval, HourFlows, Trajectory
from .reserve_energy import ENERGY_EPSILON_WH


def _time(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value


def _interval_regression(
    reference: tuple[DCDeficitInterval, ...], trial: tuple[DCDeficitInterval, ...]
) -> float:
    # Integrate positive additional deficit density over the union of physical
    # boundaries. A shifted outage cannot earn credit from its old interval.
    events: dict[datetime, float] = {}
    for intervals, sign in ((reference, -1), (trial, 1)):
        for interval in intervals:
            start, end = _time(interval.start), _time(interval.end)
            rate = sign * interval.unserved_dc_wh / (end - start).total_seconds()
            events[start] = events.get(start, 0.0) + rate
            events[end] = events.get(end, 0.0) - rate
    rate = result = 0.0
    previous: datetime | None = None
    for boundary, delta in sorted(events.items()):
        if previous is not None:
            result += max(0.0, rate) * (boundary - previous).total_seconds()
        rate += delta
        previous = boundary
    return result


def _flow_regression(before: HourFlows, after: HourFlows) -> float:
    if before.dc_deficit_intervals is None or after.dc_deficit_intervals is None:
        return max(0.0, after.unserved_dc_wh - before.unserved_dc_wh)
    return _interval_regression(before.dc_deficit_intervals, after.dc_deficit_intervals)


def first_dc_service_regression(reference: Trajectory, trial: Trajectory) -> int | None:
    """First slot where cumulative positive regressions exceed the ONE epsilon."""
    total = 0.0
    for index, (before, after) in enumerate(
        zip(reference.flows, trial.flows, strict=True)
    ):
        total += _flow_regression(before, after)
        if total > ENERGY_EPSILON_WH:
            return index
    return None


def dc_service_regression_wh(reference: Trajectory, trial: Trajectory) -> float:
    """Sum positive additional deficits; old evidence falls back to hourly sums.

    Callers must compare the same input grid, source availability and PV vector.
    Newly simulated flows always contain temporal evidence; None remains distinct
    from a known empty trace when reading legacy results or constructing fixtures.
    """
    return sum(
        _flow_regression(before, after)
        for before, after in zip(reference.flows, trial.flows, strict=True)
    )


def preserves_dc_service(
    trial: Trajectory, *, baseline: Trajectory, accepted: Trajectory | None = None
) -> bool:
    """One epsilon for the horizon, anchored to baseline and accepted service."""
    return dc_service_regression_wh(baseline, trial) <= ENERGY_EPSILON_WH and (
        accepted is None
        or accepted is baseline
        or dc_service_regression_wh(accepted, trial) <= ENERGY_EPSILON_WH
    )
