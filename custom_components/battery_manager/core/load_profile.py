"""Pure arithmetic for learned consumption profiles.

Implements the math of docs/CONSUMPTION_FORECAST.md (Stufe 1): counter
balancing (D-C1), cleaning of self-controlled loads (D-C2) and robust
median aggregation into day-type/hour bins (D-C3).

Everything here is HA-free: plain dicts/lists in (JSON-storable), plain
values out. The HA layer (history_profile.py) fetches recorder data and
persists results; this module only does the math.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median

DAY_TYPE_WEEKDAY = "weekday"
DAY_TYPE_WEEKEND = "weekend"
DAY_TYPE_ABSENCE = "absence"
DAY_TYPES = (DAY_TYPE_WEEKDAY, DAY_TYPE_WEEKEND, DAY_TYPE_ABSENCE)

# A "day series" is a list of 24 hourly energy values in Wh (numerically
# equal to the mean W of that hour); None = no data for that hour.
DaySeries = list  # list[float | None]

# Bins: {day type: [24 x float | None]}; samples: {day type: [24 x int]}
Bins = dict
Samples = dict

# Absolute minimum change allowed per run: keeps the relative rate limit
# from freezing bins at (or near) 0 W forever.
_RATE_LIMIT_MIN_STEP_W = 10.0


def day_type(day: date, vacation: bool) -> str:
    """Day-type key for learning and forecasting (D-C3/D-C4)."""
    if vacation:
        return DAY_TYPE_ABSENCE
    return DAY_TYPE_WEEKDAY if day.weekday() < 5 else DAY_TYPE_WEEKEND


def balance_day(inflows: list[DaySeries], outflows: list[DaySeries]) -> DaySeries:
    """Combine counter series into one consumption series (D-C1).

    An hour is valid only if EVERY configured balance entity has a value
    for it — a partial balance looks plausible but is wrong. Negative
    results are clamped to 0.
    """
    result: DaySeries = []
    for hour in range(24):
        values_in = [series[hour] for series in inflows]
        values_out = [series[hour] for series in outflows]
        if not inflows or any(v is None for v in values_in + values_out):
            result.append(None)
            continue
        result.append(max(0.0, sum(values_in) - sum(values_out)))
    return result


def clean_day(
    load_wh: DaySeries,
    subtract_wh: list[DaySeries],
    exclude_hours: set[int],
    clamp_wh: float,
    negative_threshold_wh: float,
) -> tuple[DaySeries, int]:
    """Remove self-controlled consumption from one day (D-C2).

    - `subtract_wh`: per-source hourly energy to subtract; a None value in
      any source means the hour cannot be cleaned and is dropped.
    - `exclude_hours`: hours contaminated by sources that cannot be
      subtracted (status-only appliances, active support paths).
    - Residuals below -`negative_threshold_wh` are counted (diagnostic for
      a wrong measuring point / double subtraction) and clamped to 0.

    Returns the cleaned series and the negative-residual count.
    """
    cleaned: DaySeries = []
    negatives = 0
    for hour in range(24):
        value = load_wh[hour] if hour < len(load_wh) else None
        if value is None or hour in exclude_hours:
            cleaned.append(None)
            continue
        subtractions = [series[hour] for series in subtract_wh]
        if any(s is None for s in subtractions):
            cleaned.append(None)
            continue
        residual = value - sum(subtractions)
        if residual < -abs(negative_threshold_wh):
            negatives += 1
        cleaned.append(min(max(residual, 0.0), clamp_wh))
    return cleaned, negatives


def aggregate_bins(
    daily_hours: dict[str, DaySeries],
    day_types: dict[str, str],
    min_samples: dict[str, int],
    previous: Bins | None,
    rate_limit: float,
    clamp_w: float,
) -> tuple[Bins, Samples]:
    """Median per (day type, local hour) over the learning window (D-C3).

    - Bins with fewer than `min_samples[day type]` values stay None
      (slot-wise fallback to the static profile, D-C6).
    - The change per bin and run is limited to ±`rate_limit` relative to
      the previous value (damping against residual feedback, D-C2), with an
      absolute minimum step so a bin at 0 W is no fixed point of the
      multiplicative clamp and can grow out again.
    """
    collected: dict[str, list[list[float]]] = {
        dt: [[] for _ in range(24)] for dt in DAY_TYPES
    }
    for day, series in daily_hours.items():
        dt_key = day_types.get(day, DAY_TYPE_WEEKDAY)
        if dt_key not in collected:
            continue
        for hour in range(24):
            value = series[hour] if hour < len(series) else None
            if value is not None:
                collected[dt_key][hour].append(min(max(value, 0.0), clamp_w))

    bins: Bins = {dt: [None] * 24 for dt in DAY_TYPES}
    samples: Samples = {dt: [0] * 24 for dt in DAY_TYPES}
    for dt_key in DAY_TYPES:
        needed = min_samples.get(dt_key, 10)
        for hour in range(24):
            values = collected[dt_key][hour]
            samples[dt_key][hour] = len(values)
            if len(values) < needed:
                continue
            new = float(median(values))
            prev = None
            if previous:
                prev_list = previous.get(dt_key) or []
                if hour < len(prev_list):
                    prev = prev_list[hour]
            if prev is not None:
                step = max(prev * rate_limit, _RATE_LIMIT_MIN_STEP_W)
                new = min(max(new, prev - step), prev + step)
            bins[dt_key][hour] = round(max(new, 0.0), 1)
    return bins, samples


def profile_value(bins: Bins | None, dt_key: str, hour: int) -> float | None:
    """Bin lookup with bounds checking; None = invalid bin."""
    if not bins:
        return None
    values = bins.get(dt_key) or []
    if 0 <= hour < len(values):
        return values[hour]
    return None


def on_fractions(
    changes: list[tuple[datetime, bool]],
    start: datetime,
    end: datetime,
) -> dict[tuple[str, int], float]:
    """Per (local ISO date, hour) fraction of time a boolean signal was on.

    `changes` are (local timestamp, is_on) pairs sorted ascending; entries
    at or before `start` establish the initial state (default: off).
    Used for switch histories (nominal-power subtraction, support-path and
    appliance exclusion, vacation day tagging).
    """
    fractions: dict[tuple[str, int], float] = {}
    if start >= end:
        return fractions

    state = False
    idx = 0
    while idx < len(changes) and changes[idx][0] <= start:
        state = changes[idx][1]
        idx += 1

    cursor = start
    while cursor < end:
        next_change = changes[idx][0] if idx < len(changes) else end
        segment_end = min(next_change, end)
        if state and segment_end > cursor:
            _add_on_time(fractions, cursor, segment_end)
        if idx < len(changes) and segment_end == next_change:
            state = changes[idx][1]
            idx += 1
        cursor = segment_end
    return fractions


def _add_on_time(
    fractions: dict[tuple[str, int], float], t0: datetime, t1: datetime
) -> None:
    cursor = t0
    while cursor < t1:
        hour_end = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        segment_end = min(hour_end, t1)
        key = (cursor.date().isoformat(), cursor.hour)
        fractions[key] = min(
            1.0,
            fractions.get(key, 0.0) + (segment_end - cursor).total_seconds() / 3600.0,
        )
        cursor = segment_end
