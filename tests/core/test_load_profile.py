"""Tests for the learned-consumption-profile math (docs/CONSUMPTION_FORECAST.md)."""

from datetime import date, datetime, timedelta

import pytest
from core.load_profile import (
    DAY_TYPE_ABSENCE,
    DAY_TYPE_WEEKDAY,
    DAY_TYPE_WEEKEND,
    aggregate_bins,
    balance_day,
    clean_day,
    day_type,
    local_hour_durations,
    on_fractions,
    profile_value,
    weighted_quantile,
)

MIN_SAMPLES = {DAY_TYPE_WEEKDAY: 3, DAY_TYPE_WEEKEND: 3, DAY_TYPE_ABSENCE: 2}


def _flat_day(value):
    return [value] * 24


# ----------------------------------------------------------------------
# balance_day (D-C1)
# ----------------------------------------------------------------------


def test_balance_subtracts_outflows():
    inflows = [_flat_day(500.0), _flat_day(100.0)]
    outflows = [_flat_day(200.0)]
    result = balance_day(inflows, outflows)
    assert result == _flat_day(400.0)


def test_balance_drops_negative_hours():
    """A negative balance hour (counter reset / sensor noise) is DROPPED, not
    learned as 0 W — a bogus 0 W sample would drag the bin median down, a
    dropped hour simply does not count (code review 2026-07)."""
    negative = balance_day([_flat_day(100.0)], [_flat_day(300.0)])
    assert negative == [None] * 24
    # Only the negative hours are dropped; valid hours keep their balance.
    inflow = _flat_day(100.0)
    inflow[5] = 500.0
    mixed = balance_day([inflow], [_flat_day(300.0)])
    assert mixed[5] == 200.0
    assert all(v is None for h, v in enumerate(mixed) if h != 5)


def test_balance_short_series_counts_missing_tail_as_no_data():
    """A series shorter than 24 h must not IndexError; the missing tail is
    no data (None), same guard as clean_day's load_wh (code review 2026-07)."""
    short_in = [500.0] * 12
    result = balance_day([short_in], [_flat_day(100.0)])
    assert result[:12] == [400.0] * 12
    assert result[12:] == [None] * 12
    short_out = [100.0] * 3
    result = balance_day([_flat_day(500.0)], [short_out])
    assert result[:3] == [400.0] * 3
    assert result[3:] == [None] * 21


def test_balance_requires_all_entities_per_hour():
    """A partial balance looks plausible but is wrong -> hour dropped."""
    gap = _flat_day(500.0)
    gap[7] = None
    result = balance_day([gap, _flat_day(100.0)], [_flat_day(50.0)])
    assert result[7] is None
    assert result[8] == 550.0

    out_gap = _flat_day(50.0)
    out_gap[3] = None
    result = balance_day([_flat_day(500.0)], [out_gap])
    assert result[3] is None


def test_balance_without_inflows_is_invalid():
    assert balance_day([], [_flat_day(100.0)]) == [None] * 24


# ----------------------------------------------------------------------
# clean_day (D-C2)
# ----------------------------------------------------------------------


def test_clean_subtracts_and_excludes():
    load = _flat_day(500.0)
    fossibot = _flat_day(300.0)
    cleaned, negatives = clean_day(load, [fossibot], {12, 13}, 3000.0, 10.0)
    assert cleaned[0] == 200.0
    assert cleaned[12] is None and cleaned[13] is None
    assert negatives == 0


def test_clean_counts_negative_residuals_and_clamps():
    load = _flat_day(100.0)
    oversubtract = _flat_day(200.0)
    cleaned, negatives = clean_day(load, [oversubtract], set(), 3000.0, 10.0)
    assert cleaned[0] == 0.0
    assert negatives == 24


def test_clean_drops_hours_with_unknown_subtraction():
    load = _flat_day(500.0)
    partial = _flat_day(100.0)
    partial[5] = None
    cleaned, _ = clean_day(load, [partial], set(), 3000.0, 10.0)
    assert cleaned[5] is None
    assert cleaned[6] == 400.0


def test_clean_short_subtraction_series_counts_missing_tail_as_no_data():
    """A subtract series shorter than 24 h must not IndexError; hours beyond
    its length cannot be cleaned and drop out (code review 2026-07)."""
    load = _flat_day(500.0)
    short = [100.0] * 10
    cleaned, _ = clean_day(load, [short], set(), 3000.0, 10.0)
    assert cleaned[:10] == [400.0] * 10
    assert cleaned[10:] == [None] * 14


def test_clean_applies_plausibility_clamp():
    cleaned, _ = clean_day(_flat_day(9000.0), [], set(), 3000.0, 10.0)
    assert cleaned[0] == 3000.0


def test_clean_negative_subtraction_adds_energy():
    """Support-path corrections add energy as negative subtractions
    (D-C2 step 3: 48 V PSU injection / 24 V rail shifted back to DC)."""
    load = _flat_day(50.0)
    injection = _flat_day(-60.0)
    cleaned, negatives = clean_day(load, [injection], set(), 3000.0, 10.0)
    assert cleaned[0] == 110.0
    assert negatives == 0
    # None in the correction series still drops the hour (uncovered switch)
    partial = _flat_day(-60.0)
    partial[4] = None
    cleaned, _ = clean_day(load, [partial], set(), 3000.0, 10.0)
    assert cleaned[4] is None


# ----------------------------------------------------------------------
# aggregate_bins (D-C3)
# ----------------------------------------------------------------------


def test_aggregate_median_per_daytype_and_min_samples():
    daily = {
        "2026-06-29": _flat_day(100.0),  # Mo
        "2026-06-30": _flat_day(200.0),  # Di
        "2026-07-01": _flat_day(300.0),  # Mi
        "2026-07-04": _flat_day(500.0),  # Sa - only 1 weekend sample
    }
    day_types = {d: day_type(date.fromisoformat(d), False) for d in daily}
    bins, samples = aggregate_bins(daily, day_types, MIN_SAMPLES, None, 0.2, 3000.0)
    assert bins[DAY_TYPE_WEEKDAY]["p50"][10] == 200.0  # median of 100/200/300
    assert bins[DAY_TYPE_WEEKDAY]["p80"][10] >= 200.0
    assert samples[DAY_TYPE_WEEKDAY][10] == 3
    assert bins[DAY_TYPE_WEEKEND]["p50"][10] is None  # 1 < min_samples
    assert samples[DAY_TYPE_WEEKEND][10] == 1


def test_aggregate_ignores_none_hours():
    gappy = _flat_day(100.0)
    gappy[8] = None
    daily = {
        "2026-06-29": gappy,
        "2026-06-30": _flat_day(200.0),
        "2026-07-01": _flat_day(300.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    bins, samples = aggregate_bins(daily, day_types, MIN_SAMPLES, None, 0.2, 3000.0)
    assert samples[DAY_TYPE_WEEKDAY][8] == 2
    assert bins[DAY_TYPE_WEEKDAY]["p50"][8] is None  # 2 < 3
    assert bins[DAY_TYPE_WEEKDAY]["p50"][9] == 200.0


def test_aggregate_rate_limit_damps_change():
    daily = {
        "2026-06-29": _flat_day(1000.0),
        "2026-06-30": _flat_day(1000.0),
        "2026-07-01": _flat_day(1000.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    previous = {DAY_TYPE_WEEKDAY: {"p50": [100.0] * 24, "p80": [100.0] * 24}}
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, previous, 0.2, 3000.0)
    # 100 W before, raw median 1000 W -> limited to +20 %
    assert bins[DAY_TYPE_WEEKDAY]["p50"][0] == 120.0


def test_aggregate_zero_bin_recovers():
    """A bin at 0 W must not be a fixed point of the rate limit (review)."""
    daily = {
        "2026-06-29": _flat_day(40.0),
        "2026-06-30": _flat_day(40.0),
        "2026-07-01": _flat_day(40.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    previous = {DAY_TYPE_WEEKDAY: {"p50": [0.0] * 24, "p80": [0.0] * 24}}
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, previous, 0.2, 3000.0)
    # Minimum absolute step (10 W) instead of 0 * 1.2 = 0.
    assert bins[DAY_TYPE_WEEKDAY]["p50"][0] == 10.0
    # And it keeps growing on subsequent runs.
    bins2, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, bins, 0.2, 3000.0)
    assert bins2[DAY_TYPE_WEEKDAY]["p50"][0] == 20.0


def test_aggregate_absence_uses_lower_min_samples():
    daily = {
        "2026-07-06": _flat_day(40.0),
        "2026-07-07": _flat_day(60.0),
    }
    day_types = {d: DAY_TYPE_ABSENCE for d in daily}
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, None, 0.2, 3000.0)
    assert bins[DAY_TYPE_ABSENCE]["p50"][0] in (40.0, 50.0)  # lower weighted median
    assert bins[DAY_TYPE_ABSENCE]["p80"][0] == 60.0


def test_profile_value_bounds():
    bins = {DAY_TYPE_WEEKDAY: {"p50": [100.0] * 24, "p80": [130.0] * 24}}
    assert profile_value(bins, DAY_TYPE_WEEKDAY, 5) == 100.0
    assert profile_value(bins, DAY_TYPE_WEEKDAY, 5, "p80") == 130.0
    assert profile_value(bins, DAY_TYPE_WEEKEND, 5) is None
    assert profile_value(bins, DAY_TYPE_WEEKDAY, 24) is None
    assert profile_value(None, DAY_TYPE_WEEKDAY, 5) is None


def test_day_type_mapping():
    assert day_type(date(2026, 7, 3), False) == DAY_TYPE_WEEKDAY  # Fr
    assert day_type(date(2026, 7, 4), False) == DAY_TYPE_WEEKEND  # Sa
    assert day_type(date(2026, 7, 3), True) == DAY_TYPE_ABSENCE


# ----------------------------------------------------------------------
# on_fractions (switch histories)
# ----------------------------------------------------------------------


def test_on_fractions_spans_hours():
    start = datetime(2026, 7, 4, 0, 0)
    end = datetime(2026, 7, 5, 0, 0)
    changes = [
        (datetime(2026, 7, 4, 10, 30), True),
        (datetime(2026, 7, 4, 12, 15), False),
    ]
    fr = on_fractions(changes, start, end)
    assert abs(fr[("2026-07-04", 10)] - 0.5) < 1e-9
    assert abs(fr[("2026-07-04", 11)] - 1.0) < 1e-9
    assert abs(fr[("2026-07-04", 12)] - 0.25) < 1e-9
    assert ("2026-07-04", 13) not in fr


def test_on_fractions_initial_state_from_earlier_change():
    start = datetime(2026, 7, 4, 0, 0)
    end = datetime(2026, 7, 4, 2, 0)
    changes = [(datetime(2026, 7, 3, 22, 0), True)]  # on since yesterday
    fr = on_fractions(changes, start, end)
    assert abs(fr[("2026-07-04", 0)] - 1.0) < 1e-9
    assert abs(fr[("2026-07-04", 1)] - 1.0) < 1e-9


def test_on_fractions_default_off_without_history():
    fr = on_fractions([], datetime(2026, 7, 4, 0, 0), datetime(2026, 7, 5, 0, 0))
    assert fr == {}


def _berlin():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Berlin")
    except Exception:  # pragma: no cover - no tz database available
        pytest.skip("IANA timezone database not available")


def test_on_fractions_dst_days_use_real_elapsed_hours():
    """Switch cleaning accounts for the missing/repeated physical hour."""
    tz = _berlin()
    for day, month, dom in (("2026-03-29", 3, 29), ("2026-10-25", 10, 25)):
        start = datetime(2026, month, dom, 0, 0, tzinfo=tz)
        end = start + timedelta(days=1)
        fr = on_fractions([(start, True)], start, end)
        hours = {h for (d, h), v in fr.items() if d == day and v > 0}
        assert hours == (set(range(24)) - {2} if month == 3 else set(range(24)))
        assert fr.get((day, 2), 0) == (0 if month == 3 else 2)
        day_total = sum(v for (d, _), v in fr.items() if d == day)
        assert day_total == (23 if month == 3 else 25)


def test_switch_interval_across_fold_keeps_one_hundred_minutes():
    tz = _berlin()
    start = datetime(2026, 10, 25, 0, tzinfo=tz)
    end = start + timedelta(days=1)
    on = datetime(2026, 10, 25, 2, 10, tzinfo=tz, fold=0)
    off = datetime(2026, 10, 25, 2, 50, tzinfo=tz, fold=1)
    assert on_fractions([(on, True), (off, False)], start, end) == {
        ("2026-10-25", 2): pytest.approx(100 / 60)
    }


@pytest.mark.parametrize(
    "day,hours",
    [(date(2026, 3, 29), 23), (date(2026, 3, 30), 24), (date(2026, 10, 25), 25)],
)
def test_energy_duration_bins_forecast_constant_power(day, hours):
    durations = local_hour_durations(day, _berlin())
    energy = [100 * duration if duration else None for duration in durations]
    bins, samples = aggregate_bins(
        {day.isoformat(): energy},
        {day.isoformat(): DAY_TYPE_WEEKDAY},
        {DAY_TYPE_WEEKDAY: 1},
        None,
        1,
        4000,
        durations={day.isoformat(): durations},
    )
    values = bins[DAY_TYPE_WEEKDAY]["p50"]
    assert (
        sum(
            (value or 0) * duration
            for value, duration in zip(values, durations, strict=True)
        )
        == hours * 100
    )
    assert all(value == 100 for value in values if value is not None)
    assert sum(samples[DAY_TYPE_WEEKDAY]) == (23 if hours == 23 else 24)


def test_duration_normalization_precedes_regular_damping_and_clamp():
    days = {
        "2026-10-25": [200] * 24,
        "2026-10-26": [150] * 24,
        "2026-10-27": [200] * 24,
    }
    durations = {"2026-10-25": [2] * 24}
    previous = {DAY_TYPE_WEEKDAY: {"p50": [100] * 24, "p80": [100] * 24}}
    bins, _ = aggregate_bins(
        days, {}, MIN_SAMPLES, previous, 0.25, 4000, durations=durations
    )
    assert bins[DAY_TYPE_WEEKDAY]["p50"] == [125] * 24
    assert (
        clean_day([10_000] * 24, [], set(), 4000, 30, durations=[2] * 24)[0]
        == [8000] * 24
    )
    bins, samples = aggregate_bins(
        {"2026-03-29": [100] * 24},
        {},
        {DAY_TYPE_WEEKDAY: 1},
        None,
        1,
        4000,
        durations={"2026-03-29": [0] * 24},
    )
    assert bins[DAY_TYPE_WEEKDAY]["p50"] == [None] * 24
    assert samples[DAY_TYPE_WEEKDAY] == [0] * 24


# ----------------------------------------------------------------------
# weighted quantiles (D-C7)
# ----------------------------------------------------------------------


def test_weighted_quantile_equal_weights_is_median():
    values = [10.0, 20.0, 30.0, 40.0]
    weights = [1.0] * 4
    assert weighted_quantile(values, weights, 0.5) == 20.0
    assert weighted_quantile(values, weights, 0.8) == 40.0


def test_weighted_quantile_recency_shifts_result():
    """A heavily weighted recent day dominates old ones (season/drift)."""
    values = [10.0, 100.0]
    assert weighted_quantile(values, [1.0, 0.1], 0.5) == 10.0
    assert weighted_quantile(values, [0.1, 1.0], 0.5) == 100.0


def test_aggregate_weights_follow_recent_days():
    daily = {
        "2026-06-29": _flat_day(100.0),
        "2026-06-30": _flat_day(100.0),
        "2026-07-01": _flat_day(300.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    heavy_recent = {"2026-06-29": 0.1, "2026-06-30": 0.1, "2026-07-01": 1.0}
    bins, _ = aggregate_bins(
        daily, day_types, MIN_SAMPLES, None, 0.2, 3000.0, weights=heavy_recent
    )
    assert bins[DAY_TYPE_WEEKDAY]["p50"][0] == 300.0
    assert bins[DAY_TYPE_WEEKDAY]["p80"][0] >= bins[DAY_TYPE_WEEKDAY]["p50"][0]


def test_weighted_quantile_zero_total_weight_falls_back_to_median():
    """All-zero (or negative-sum) weights make the weighted rule degenerate:
    fall back to the plain median instead of dividing by a zero total."""
    assert weighted_quantile([1.0, 5.0, 9.0], [0.0, 0.0, 0.0], 0.8) == 5.0
    assert weighted_quantile([1.0, 5.0, 9.0], [-1.0, 0.0, 0.0], 0.8) == 5.0


def test_weighted_quantile_above_one_returns_largest_value():
    """A quantile beyond 1.0 overshoots the total weight — the loop never
    reaches the threshold and the largest value is returned."""
    assert weighted_quantile([1.0, 5.0, 9.0], [1.0, 1.0, 1.0], 1.5) == 9.0


def test_aggregate_bins_skips_unknown_day_type():
    """A day_types entry outside DAY_TYPES (stale/corrupt store content) is
    skipped instead of KeyError-ing or polluting a wrong bin."""
    daily = {
        "2026-06-29": _flat_day(100.0),
        "2026-06-30": _flat_day(100.0),
        "2026-07-01": _flat_day(100.0),
        "2026-07-02": _flat_day(999.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    day_types["2026-07-02"] = "not-a-day-type"
    bins, samples = aggregate_bins(daily, day_types, MIN_SAMPLES, None, 0.2, 3000.0)
    assert samples[DAY_TYPE_WEEKDAY][0] == 3  # only the three valid days count
    assert bins[DAY_TYPE_WEEKDAY]["p50"][0] == 100.0


def test_aggregate_bins_restores_quantile_order_after_rate_limit():
    """D-C2: the ±rate_limit clamp runs per quantile, so a fast-rising p50 can
    overtake a clamped-down p80 — the order restore must lift p80 to p50."""
    daily = {
        "2026-06-29": _flat_day(5000.0),
        "2026-06-30": _flat_day(5000.0),
        "2026-07-01": _flat_day(5000.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    previous = {
        DAY_TYPE_WEEKDAY: {"p50": [1000.0] * 24, "p80": [100.0] * 24},
    }
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, previous, 0.5, 10000.0)
    # p50 clamped to 1000+500 = 1500; p80 would clamp to 100+50 = 150 (< p50)
    # without the order restore.
    assert bins[DAY_TYPE_WEEKDAY]["p50"][0] == 1500.0
    assert bins[DAY_TYPE_WEEKDAY]["p80"][0] == 1500.0


def test_on_fractions_empty_window_is_empty():
    """start >= end (a zero-length or inverted query window) yields no
    fractions instead of a bogus full-hour entry."""
    from datetime import datetime

    start = datetime(2026, 7, 1, 10, 0)
    end = datetime(2026, 7, 1, 11, 0)
    assert on_fractions([(start, True)], end, start) == {}
    assert on_fractions([(start, True)], start, start) == {}
