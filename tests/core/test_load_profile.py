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
    on_fractions,
    profile_value,
)

MIN_SAMPLES = {DAY_TYPE_WEEKDAY: 3, DAY_TYPE_WEEKEND: 3, DAY_TYPE_ABSENCE: 2}


def _flat_day(value):
    return [value] * 24


# ----------------------------------------------------------------------
# balance_day (D-C1)
# ----------------------------------------------------------------------


def test_balance_subtracts_outflows_and_clamps():
    inflows = [_flat_day(500.0), _flat_day(100.0)]
    outflows = [_flat_day(200.0)]
    result = balance_day(inflows, outflows)
    assert result == _flat_day(400.0)

    negative = balance_day([_flat_day(100.0)], [_flat_day(300.0)])
    assert negative == _flat_day(0.0)


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
    assert bins[DAY_TYPE_WEEKDAY][10] == 200.0  # median of 100/200/300
    assert samples[DAY_TYPE_WEEKDAY][10] == 3
    assert bins[DAY_TYPE_WEEKEND][10] is None  # 1 < min_samples
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
    assert bins[DAY_TYPE_WEEKDAY][8] is None  # 2 < 3
    assert bins[DAY_TYPE_WEEKDAY][9] == 200.0


def test_aggregate_rate_limit_damps_change():
    daily = {
        "2026-06-29": _flat_day(1000.0),
        "2026-06-30": _flat_day(1000.0),
        "2026-07-01": _flat_day(1000.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    previous = {DAY_TYPE_WEEKDAY: [100.0] * 24}
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, previous, 0.2, 3000.0)
    # 100 W before, raw median 1000 W -> limited to +20 %
    assert bins[DAY_TYPE_WEEKDAY][0] == 120.0


def test_aggregate_zero_bin_recovers():
    """A bin at 0 W must not be a fixed point of the rate limit (review)."""
    daily = {
        "2026-06-29": _flat_day(40.0),
        "2026-06-30": _flat_day(40.0),
        "2026-07-01": _flat_day(40.0),
    }
    day_types = {d: DAY_TYPE_WEEKDAY for d in daily}
    previous = {DAY_TYPE_WEEKDAY: [0.0] * 24}
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, previous, 0.2, 3000.0)
    # Minimum absolute step (10 W) instead of 0 * 1.2 = 0.
    assert bins[DAY_TYPE_WEEKDAY][0] == 10.0
    # And it keeps growing on subsequent runs.
    bins2, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, bins, 0.2, 3000.0)
    assert bins2[DAY_TYPE_WEEKDAY][0] == 20.0


def test_aggregate_absence_uses_lower_min_samples():
    daily = {
        "2026-07-06": _flat_day(40.0),
        "2026-07-07": _flat_day(60.0),
    }
    day_types = {d: DAY_TYPE_ABSENCE for d in daily}
    bins, _ = aggregate_bins(daily, day_types, MIN_SAMPLES, None, 0.2, 3000.0)
    assert bins[DAY_TYPE_ABSENCE][0] == 50.0


def test_profile_value_bounds():
    bins = {DAY_TYPE_WEEKDAY: [100.0] * 24}
    assert profile_value(bins, DAY_TYPE_WEEKDAY, 5) == 100.0
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


def test_on_fractions_dst_days_use_wall_clock_hours():
    """DST days are binned on wall-clock hours (spec D-C3: treated as
    normal samples; +-1 h blur in the two transition nights accepted).

    Python's same-tzinfo datetime arithmetic is wall-clock arithmetic, so
    both the 23-h spring day and the 25-h fall day yield 24 wall hours,
    each capped at fraction 1.0.
    """
    tz = _berlin()
    for day, month, dom in (("2026-03-29", 3, 29), ("2026-10-25", 10, 25)):
        start = datetime(2026, month, dom, 0, 0, tzinfo=tz)
        end = start + timedelta(days=1)
        fr = on_fractions([(start, True)], start, end)
        hours = {h for (d, h), v in fr.items() if d == day and v > 0}
        assert hours == set(range(24))
        assert all(v <= 1.0 for v in fr.values())
        day_total = sum(v for (d, _), v in fr.items() if d == day)
        assert abs(day_total - 24.0) < 1e-6
