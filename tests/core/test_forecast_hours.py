"""Tests for hourly PV forecast aggregation (docs/F-PREDRAIN.md F1)."""

from datetime import datetime

from core.forecast_hours import aggregate_hours, coverage_and_residual


def test_aggregate_sums_15min_buckets_into_hours():
    """Balcony-forecast shape: four 15-min buckets collapse to one hour key."""
    base = datetime(2026, 7, 10, 9, 0)
    entries = [
        (base.replace(minute=0), 100.0),
        (base.replace(minute=15), 150.0),
        (base.replace(minute=30), 200.0),
        (base.replace(minute=45), 50.0),
    ]
    hours = aggregate_hours(entries)
    assert hours == {datetime(2026, 7, 10, 9, 0): 500.0}


def test_aggregate_sums_30min_buckets_into_hours():
    entries = [
        (datetime(2026, 7, 10, 9, 0), 120.0),
        (datetime(2026, 7, 10, 9, 30), 80.0),
        (datetime(2026, 7, 10, 10, 0), 40.0),
    ]
    hours = aggregate_hours(entries)
    assert hours == {
        datetime(2026, 7, 10, 9, 0): 200.0,
        datetime(2026, 7, 10, 10, 0): 40.0,
    }


def test_aggregate_hourly_series_passes_through():
    """Open-Meteo shape: one bucket per hour, keys already hour-aligned."""
    entries = [
        (datetime(2026, 7, 10, 5, 0), 40.0),
        (datetime(2026, 7, 10, 6, 0), 371.0),
        (datetime(2026, 7, 10, 7, 0), 814.0),
    ]
    hours = aggregate_hours(entries)
    assert hours == {
        datetime(2026, 7, 10, 5, 0): 40.0,
        datetime(2026, 7, 10, 6, 0): 371.0,
        datetime(2026, 7, 10, 7, 0): 814.0,
    }


def test_aggregate_empty():
    assert aggregate_hours([]) == {}


def test_aggregate_truncates_seconds_and_microseconds():
    entries = [
        (datetime(2026, 7, 10, 9, 12, 34, 567), 10.0),
        (datetime(2026, 7, 10, 9, 59, 59, 999), 20.0),
    ]
    assert aggregate_hours(entries) == {datetime(2026, 7, 10, 9, 0): 30.0}


def test_aggregate_dst_fallback_repeated_hour_sums():
    """A DST fall-back repeats a local wall-clock hour: after the coordinator has
    converted both aware buckets to naive-local they collide on the same hour key
    and must be summed, not overwritten."""
    # Both 15-min buckets land on local 02:xx (the repeated hour) after conversion.
    entries = [
        (datetime(2026, 10, 25, 2, 15), 90.0),
        (datetime(2026, 10, 25, 2, 45), 110.0),
    ]
    assert aggregate_hours(entries) == {datetime(2026, 10, 25, 2, 0): 200.0}


def test_coverage_and_residual_basic():
    covered, residual = coverage_and_residual([100.0, 200.0, 300.0], 1000.0)
    assert covered == 600.0
    assert residual == 400.0


def test_coverage_and_residual_full_coverage_zero_residual():
    covered, residual = coverage_and_residual([500.0, 500.0], 1000.0)
    assert covered == 1000.0
    assert residual == 0.0


def test_coverage_and_residual_clamps_negative_on_sum_mismatch():
    """Sum-mismatch path: hourly buckets exceed the daily state -> residual is
    clamped to 0 (never negative). The mismatch is visible as covered > total."""
    covered, residual = coverage_and_residual([700.0, 700.0], 1000.0)
    assert covered == 1400.0
    assert residual == 0.0
    assert covered > 1000.0  # caller's mismatch signal


def test_coverage_and_residual_empty_day():
    covered, residual = coverage_and_residual([], 8000.0)
    assert covered == 0.0
    assert residual == 8000.0
