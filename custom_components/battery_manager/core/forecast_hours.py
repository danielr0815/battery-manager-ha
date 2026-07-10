"""Hourly PV forecast aggregation (pure core, no Home Assistant imports).

The three PV forecast entities expose an hourly ``wh_period`` attribute
(Open-Meteo: hourly buckets; the balcony-solar-forecast integration: 15-min
buckets). This module reduces those raw buckets to a naive-local hour -> Wh map
and derives the per-day residual used to fill the hours the hourly forecast does
not cover (docs/F-PREDRAIN.md F1). Timezone parsing / normalisation happens in
the coordinator; everything here operates on naive-local datetimes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime


def aggregate_hours(
    entries: Iterable[tuple[datetime, float]],
) -> dict[datetime, float]:
    """Sum sub-hour buckets into naive-local hour-start keys.

    ``entries`` are (timestamp, Wh) pairs with naive-local timestamps (the caller
    has already converted any aware key to local and dropped tzinfo). Values are
    Wh produced during the bucket (Open-Meteo ``wh_period`` semantics), so 15- or
    30-minute buckets that fall in the same hour are ADDED. A whole-hour series
    passes through unchanged (one bucket per hour). Two buckets landing on the
    same local hour after a DST fall-back are likewise summed.
    """
    hours: dict[datetime, float] = {}
    for ts, wh in entries:
        key = ts.replace(minute=0, second=0, microsecond=0)
        hours[key] = hours.get(key, 0.0) + wh
    return hours


def coverage_and_residual(
    day_hours: Iterable[float], daily_total_wh: float
) -> tuple[float, float]:
    """Covered energy and the residual to spread over a day's uncovered hours.

    ``covered_wh`` is the sum of the day's hourly buckets; ``residual_wh`` is the
    part of the daily forecast total the buckets do not account for, clamped at 0
    so a bucket sum ABOVE the daily state (sensor mismatch) never yields negative
    fill. Callers detect that mismatch as ``covered_wh > daily_total_wh``.
    """
    covered_wh = sum(day_hours)
    residual_wh = daily_total_wh - covered_wh
    if residual_wh < 0.0:
        residual_wh = 0.0
    return covered_wh, residual_wh
