"""Aggregate hourly PV energy without merging distinct DST-fold instants.

The coordinator parses timezone information. Naive inputs remain supported for
pure-core callers; aware inputs retain fixed local offsets and calendar dates.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone


def aggregate_hours(
    entries: Iterable[tuple[datetime, float]],
) -> dict[datetime, float]:
    """Sum sub-hour energy buckets, keeping repeated local hours separate.

    Fixed offsets avoid Python's same-ZoneInfo fold equality: 02:00+02:00
    and 02:00+01:00 are two independent hours, never one double-size bucket.
    """
    hours: dict[datetime, float] = {}
    for ts, wh in entries:
        key = ts.replace(minute=0, second=0, microsecond=0)
        if (offset := key.utcoffset()) is not None:
            key = key.replace(tzinfo=timezone(offset))
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
