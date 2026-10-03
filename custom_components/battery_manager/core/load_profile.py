"""Pure arithmetic for learned consumption profiles.

Implements the math of docs/CONSUMPTION_FORECAST.md (Stufe 1): counter
balancing (D-C1), cleaning of self-controlled loads (D-C2) and robust
median aggregation into day-type/hour bins (D-C3).

Everything here is HA-free: plain dicts/lists in (JSON-storable), plain
values out. The HA layer (history_profile.py) fetches recorder data and
persists results; this module only does the math.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from statistics import median

DAY_TYPE_WEEKDAY = "weekday"
DAY_TYPE_WEEKEND = "weekend"
DAY_TYPE_ABSENCE = "absence"
DAY_TYPES = (DAY_TYPE_WEEKDAY, DAY_TYPE_WEEKEND, DAY_TYPE_ABSENCE)

# A day series stores energy in Wh. An accompanying duration is essential on
# DST days: the folded local hour contains two real hours, the skipped hour none.
type DaySeries = list[float | None]

# Profiles contain day type -> quantile -> hourly Wh; sample counts are separate.
type Bins = dict[str, dict[str, DaySeries]]
type Samples = dict[str, list[int]]

# Absolute minimum change allowed per run: keeps the relative rate limit
# from freezing bins at (or near) 0 W forever.
_RATE_LIMIT_MIN_STEP_W = 10.0

# Learned quantiles per bin (D-C7): P50 = plan-truthful forecast, P80 =
# upper band for the dynamic SOC buffer. Deliberately no P90 (unstable at
# n_eff ~ 20-80).
QUANTILES = {"p50": 0.5, "p80": 0.8}
QUANTILE_KEYS = tuple(QUANTILES)


def day_type(day: date, vacation: bool) -> str:
    """Day-type key for learning and forecasting (D-C3/D-C4)."""
    if vacation:
        return DAY_TYPE_ABSENCE
    return DAY_TYPE_WEEKDAY if day.weekday() < 5 else DAY_TYPE_WEEKEND


def _hour_values(series_list: list[DaySeries], hour: int) -> list[float] | None:
    """Per-hour values across counter series; None if ANY source lacks the
    hour (explicit None or a series shorter than 24 h — the missing tail
    counts as no data, same guard as clean_day's `load_wh`)."""
    values: list[float] = []
    for series in series_list:
        value = series[hour] if hour < len(series) else None
        if value is None:
            return None
        values.append(value)
    return values


def balance_day(inflows: list[DaySeries], outflows: list[DaySeries]) -> DaySeries:
    """Combine counter series into one consumption series (D-C1).

    An hour is valid only if EVERY configured balance entity has a value
    for it — a partial balance looks plausible but is wrong. A NEGATIVE
    balance hour (counter reset / sensor noise) is DROPPED, not learned as
    0 W: learning it would drag the bin median down with garbage, while a
    dropped hour simply does not count as a sample (code review 2026-07).
    Series shorter than 24 h count the missing tail as no data (None), same
    as an explicit None.
    """
    result: DaySeries = []
    for hour in range(24):
        values_in = _hour_values(inflows, hour)
        values_out = _hour_values(outflows, hour)
        if not inflows or values_in is None or values_out is None:
            result.append(None)
            continue
        balance = sum(values_in) - sum(values_out)
        result.append(balance if balance >= 0.0 else None)
    return result


def clean_day(
    load_wh: DaySeries,
    subtract_wh: list[DaySeries],
    exclude_hours: set[int],
    clamp_wh: float,
    negative_threshold_wh: float,
    durations: list[float] | None = None,
) -> tuple[DaySeries, int]:
    """Remove self-controlled consumption from one day (D-C2).

    - `subtract_wh`: per-source hourly energy to subtract; a None value in
      any source means the hour cannot be cleaned and is dropped. A series
      shorter than 24 h counts the missing tail as None (same guard as
      `load_wh`).
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
        subtractions = _hour_values(subtract_wh, hour)
        if subtractions is None:
            cleaned.append(None)
            continue
        residual = value - sum(subtractions)
        if residual < -abs(negative_threshold_wh):
            negatives += 1
        duration = durations[hour] if durations is not None else 1.0
        cleaned.append(min(max(residual, 0.0), clamp_wh * duration))
    return cleaned, negatives


def weighted_quantile(
    values: list[float], weights: list[float], quantile: float
) -> float:
    """Weighted empirical quantile (D-C7).

    Smallest value whose cumulative weight reaches `quantile` x total
    weight. With equal weights and q=0.5 this is the (lower) median.
    """
    pairs = sorted(zip(values, weights, strict=True))
    total = sum(w for _v, w in pairs)
    if total <= 0:
        return float(median(values))
    threshold = quantile * total
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold - 1e-12:
            return float(value)
    return float(pairs[-1][0])


def aggregate_bins(
    daily_hours: dict[str, DaySeries],
    day_types: dict[str, str],
    min_samples: dict[str, int],
    previous: Bins | None,
    rate_limit: float,
    clamp_w: float,
    weights: dict[str, float] | None = None,
    durations: dict[str, list[float]] | None = None,
) -> tuple[Bins, Samples]:
    """Weighted P50/P80 per (day type, local hour) over the window (D-C3/D-C7).

    - `weights` (per ISO day, e.g. recency 0.5^(age/half_life)) turn the
      quantiles into the drift/season model; missing/None = equal weights
      (plain median behaviour of Stufe 1).
    - Bins with fewer than `min_samples[day type]` RAW values stay None
      (slot-wise fallback to the static profile, D-C6).
    - The change per bin, quantile and run is limited to ±`rate_limit`
      relative to the previous value (damping against residual feedback,
      D-C2), with an absolute minimum step so a bin at 0 W is no fixed
      point of the multiplicative clamp. P80 >= P50 is enforced last.
    """
    collected: dict[str, list[list[tuple[float, float]]]] = {
        dt: [[] for _ in range(24)] for dt in DAY_TYPES
    }
    for day, series in daily_hours.items():
        dt_key = day_types.get(day, DAY_TYPE_WEEKDAY)
        if dt_key not in collected:
            continue
        weight = (weights or {}).get(day, 1.0)
        for hour in range(24):
            value = series[hour] if hour < len(series) else None
            if value is not None:
                duration = (durations or {}).get(day, [1.0] * 24)[hour]
                if duration <= 0:
                    continue
                value /= duration
                collected[dt_key][hour].append((min(max(value, 0.0), clamp_w), weight))

    bins: Bins = {dt: {q: [None] * 24 for q in QUANTILE_KEYS} for dt in DAY_TYPES}
    samples: Samples = {dt: [0] * 24 for dt in DAY_TYPES}
    for dt_key in DAY_TYPES:
        needed = min_samples.get(dt_key, 10)
        prev_bins = (previous or {}).get(dt_key) or {}
        for hour in range(24):
            pairs = collected[dt_key][hour]
            samples[dt_key][hour] = len(pairs)
            if len(pairs) < needed:
                continue
            values = [v for v, _w in pairs]
            value_weights = [w for _v, w in pairs]
            results: dict[str, float] = {}
            for q_key, q in QUANTILES.items():
                new = weighted_quantile(values, value_weights, q)
                prev_list = (
                    prev_bins.get(q_key) if isinstance(prev_bins, dict) else None
                ) or []
                prev = prev_list[hour] if hour < len(prev_list) else None
                if prev is not None:
                    step = max(prev * rate_limit, _RATE_LIMIT_MIN_STEP_W)
                    new = min(max(new, prev - step), prev + step)
                results[q_key] = round(max(new, 0.0), 1)
            # Rate limiting may cross the quantiles: restore the order.
            if results["p80"] < results["p50"]:
                results["p80"] = results["p50"]
            for q_key, value in results.items():
                bins[dt_key][q_key][hour] = value
    return bins, samples


def profile_value(
    bins: Bins | None, dt_key: str, hour: int, quantile: str = "p50"
) -> float | None:
    """Bin lookup with bounds checking; None = invalid bin."""
    if not bins:
        return None
    by_quantile = bins.get(dt_key)
    if not isinstance(by_quantile, dict):
        return None
    values = by_quantile.get(quantile) or []
    if 0 <= hour < len(values):
        return values[hour]
    return None


def on_fractions(
    changes: list[tuple[datetime, bool]],
    start: datetime,
    end: datetime,
) -> dict[tuple[str, int], float]:
    """Real ON hours per (local ISO date, wall hour), including both folds.

    `changes` are (local timestamp, is_on) pairs sorted ascending; entries
    at or before `start` establish the initial state (default: off).
    Used for switch histories (nominal-power subtraction, support-path and
    appliance exclusion, vacation day tagging).
    """
    fractions: dict[tuple[str, int], float] = {}
    utc_start, utc_end = _absolute(start), _absolute(end)
    if utc_start >= utc_end:
        return fractions

    state = False
    idx = 0
    while idx < len(changes) and _absolute(changes[idx][0]) <= utc_start:
        state = changes[idx][1]
        idx += 1

    cursor = utc_start
    while cursor < utc_end:
        next_change = _absolute(changes[idx][0]) if idx < len(changes) else utc_end
        segment_end = min(next_change, utc_end)
        if state and segment_end > cursor:
            _add_on_time(fractions, cursor, segment_end, start.tzinfo)
        if idx < len(changes) and segment_end == next_change:
            state = changes[idx][1]
            idx += 1
        cursor = segment_end
    return fractions


def _add_on_time(
    fractions: dict[tuple[str, int], float],
    t0: datetime,
    t1: datetime,
    zone: tzinfo | None,
) -> None:
    cursor = t0
    while cursor < t1:
        local = (
            cursor.astimezone(zone) if zone is not None else cursor.replace(tzinfo=None)
        )
        hour_end = _next_hour(cursor, local)
        segment_end = min(hour_end, t1)
        key = (local.date().isoformat(), local.hour)
        fractions[key] = (
            fractions.get(key, 0.0) + (segment_end - cursor).total_seconds() / 3600.0
        )
        cursor = segment_end


def _absolute(value: datetime) -> datetime:
    return (
        value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    )


def _next_hour(absolute: datetime, local: datetime) -> datetime:
    # Elapsed time until the next wall-hour boundary remains correct across a
    # fold/gap and for zones with a non-integral UTC offset.
    wall = local.replace(tzinfo=None)
    next_wall = wall.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return absolute + (next_wall - wall)


def local_hour_durations(day: date, zone: tzinfo) -> list[float]:
    """Real hours represented by each local bin (23/24/25-hour days)."""
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    hours = on_fractions([(start, True)], start, end)
    return [hours.get((day.isoformat(), hour), 0.0) for hour in range(24)]
