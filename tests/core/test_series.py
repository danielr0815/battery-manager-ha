"""Tests for hourly series construction."""

from dataclasses import replace
from datetime import datetime

from core.model import ApplianceRun, LoadProfile, PVParams, SystemConfig
from core.series import build_slots, insert_appliance_run, pv_hour_share, slot_starts


def test_horizon_runs_until_midnight_of_last_forecast_day():
    config = SystemConfig()
    now = datetime(2026, 7, 3, 21, 30)
    inputs = build_slots(config, now, 50.0, [5.0, 5.0, 5.0])
    last = inputs.slots[-1]
    assert last.start.date() == datetime(2026, 7, 5).date()
    assert last.hour_of_day == 23


def test_first_slot_partial_then_hour_aligned():
    config = SystemConfig()
    now = datetime(2026, 7, 3, 21, 30)
    inputs = build_slots(config, now, 50.0, [5.0, 5.0, 5.0])
    assert abs(inputs.slots[0].duration - 0.5) < 1e-9
    assert inputs.slots[1].start.minute == 0
    assert inputs.slots[1].hour_of_day == 22
    assert all(abs(s.duration - 1.0) < 1e-9 for s in inputs.slots[1:])


def test_pv_distribution_sums_to_daily_forecast():
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)  # full day from midnight
    daily_kwh = 8.0  # low enough that peak clipping never kicks in
    inputs = build_slots(config, now, 50.0, [daily_kwh])
    total_wh = sum(s.pv_wh for s in inputs.slots)
    assert abs(total_wh - daily_kwh * 1000.0) < 1.0


def test_misordered_pv_windows_still_distribute_full_forecast():
    """Review #5: a mis-ordered PV config (degenerate window) must NOT silently
    discard forecast energy — the shares renormalize to the full daily total."""
    # afternoon_end <= morning_end => the afternoon window is degenerate.
    pv = PVParams(
        peak_power_w=100_000.0,
        morning_start_hour=8,
        morning_end_hour=16,
        afternoon_end_hour=10,  # <= morning_end: degenerate
        morning_ratio=0.7,
    )
    config = replace(SystemConfig(), pv=pv)
    now = datetime(2026, 7, 4, 0, 0)
    daily_kwh = 8.0
    inputs = build_slots(config, now, 50.0, [daily_kwh])
    total_wh = sum(s.pv_wh for s in inputs.slots)
    assert abs(total_wh - daily_kwh * 1000.0) < 1.0  # full forecast, not short


def test_night_spanning_variable_load_wraps_midnight():
    """Review #6: a start > end variable-load window wraps around midnight
    instead of dropping the load for all 24 hours."""
    profile = LoadProfile(
        base_w=50.0, variable_w=25.0, variable_start_hour=20, variable_end_hour=6
    )
    # In-window hours 20..23 and 0..5 get base+variable; the rest just base.
    assert profile.power_w(22) == 75.0
    assert profile.power_w(3) == 75.0
    assert profile.power_w(6) == 50.0  # end is exclusive
    assert profile.power_w(12) == 50.0
    assert profile.power_w(20) == 75.0  # start inclusive


def test_pv_clipped_at_peak_power():
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)
    inputs = build_slots(config, now, 50.0, [100.0])  # absurd daily energy
    assert max(s.pv_wh / s.duration for s in inputs.slots) <= config.pv.peak_power_w


def test_appliance_run_adds_to_ac_load():
    config = SystemConfig()
    now = datetime(2026, 7, 4, 12, 0)
    run = ApplianceRun(
        appliance_id="washer", remaining_energy_wh=1000.0, remaining_hours=2.0
    )
    with_run = build_slots(config, now, 50.0, [0.0], appliance_runs=(run,))
    without = build_slots(config, now, 50.0, [0.0])
    delta = sum(
        w.ac_wh - b.ac_wh for w, b in zip(with_run.slots, without.slots, strict=True)
    )
    assert abs(delta - 1000.0) < 1.0


def test_insert_appliance_run_is_non_destructive():
    config = SystemConfig()
    now = datetime(2026, 7, 4, 12, 0)
    inputs = build_slots(config, now, 50.0, [5.0])
    modified = insert_appliance_run(inputs, 800.0, 2.0)
    assert sum(s.ac_wh for s in modified.slots) > sum(s.ac_wh for s in inputs.slots)
    # original untouched
    assert inputs.slots[0].ac_wh == build_slots(config, now, 50.0, [5.0]).slots[0].ac_wh


def test_slot_starts_matches_build_slots():
    """slot_starts is the single source of truth for the slot grid (D-C5)."""
    config = SystemConfig()
    for now in (
        datetime(2026, 7, 3, 21, 30),
        datetime(2026, 7, 4, 0, 0),
        datetime(2026, 7, 4, 23, 59),
    ):
        inputs = build_slots(config, now, 50.0, [5.0, 5.0, 5.0])
        starts = slot_starts(now, 3)
        assert len(starts) == len(inputs.slots)
        assert all(
            s.start == start for s, start in zip(inputs.slots, starts, strict=True)
        )


def test_slot_starts_empty_without_forecast_days():
    assert slot_starts(datetime(2026, 7, 4, 12, 0), 0) == ()


def test_learned_series_overrides_static_profile():
    """A series value replaces the static profile Watt for that slot only."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 12, 30)  # partial first slot (0.5 h)
    n = len(slot_starts(now, 1))
    series = tuple([200.0] + [None] * (n - 1))
    inputs = build_slots(config, now, 50.0, [0.0], ac_load_w=series)
    baseline = build_slots(config, now, 50.0, [0.0])
    # Slot 0: 200 W x 0.5 h; remaining slots fall back to the static profile.
    assert abs(inputs.slots[0].ac_wh - 100.0) < 1e-9
    for got, expected in zip(inputs.slots[1:], baseline.slots[1:], strict=True):
        assert got.ac_wh == expected.ac_wh
    # DC untouched
    for got, expected in zip(inputs.slots, baseline.slots, strict=True):
        assert got.dc_wh == expected.dc_wh


def test_learned_series_shorter_than_horizon_falls_back():
    """Values beyond the series length count as None (contract D-C5)."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)
    inputs = build_slots(config, now, 50.0, [0.0], ac_load_w=(300.0,))
    baseline = build_slots(config, now, 50.0, [0.0])
    assert abs(inputs.slots[0].ac_wh - 300.0) < 1e-9
    for got, expected in zip(inputs.slots[1:], baseline.slots[1:], strict=True):
        assert got.ac_wh == expected.ac_wh


# ---------------------------------------------------------------------------
# F-PREDRAIN F1: hourly PV forecast mapping (build_slots pv_hourly)
# ---------------------------------------------------------------------------


def test_pv_hourly_none_and_empty_are_bit_identical_to_reference():
    """T7 identity anchor: no map / an empty map reproduce the two-window PV
    slot-for-slot (bit-identical float equality), including the partial slot 0."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 12, 30)  # partial first slot
    reference = build_slots(config, now, 50.0, [8.0, 8.0, 8.0])
    with_none = build_slots(config, now, 50.0, [8.0, 8.0, 8.0], pv_hourly=None)
    with_empty = build_slots(config, now, 50.0, [8.0, 8.0, 8.0], pv_hourly={})
    for r, n, e in zip(
        reference.slots, with_none.slots, with_empty.slots, strict=True
    ):
        assert r.pv_wh == n.pv_wh == e.pv_wh


def test_pv_hourly_covered_hours_take_bucket_value():
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)  # full day from midnight
    pv_hourly = {
        datetime(2026, 7, 4, 10, 0): 1000.0,
        datetime(2026, 7, 4, 11, 0): 1500.0,
        datetime(2026, 7, 4, 12, 0): 1200.0,
    }
    inputs = build_slots(config, now, 50.0, [8.0], pv_hourly=pv_hourly)
    by_hour = {s.hour_of_day: s.pv_wh for s in inputs.slots}
    assert by_hour[10] == 1000.0
    assert by_hour[11] == 1500.0
    assert by_hour[12] == 1200.0


def test_pv_hourly_slot0_partial_gets_prorated_share():
    """Slot 0 covers only part of its hour -> it gets that fraction of the hour's
    bucket (docs/F-PREDRAIN.md F1)."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 10, 30)  # slot 0 = 10:30..11:00 (0.5 h of hour 10)
    pv_hourly = {
        datetime(2026, 7, 4, 10, 0): 1000.0,
        datetime(2026, 7, 4, 11, 0): 800.0,
    }
    inputs = build_slots(config, now, 50.0, [8.0], pv_hourly=pv_hourly)
    assert abs(inputs.slots[0].duration - 0.5) < 1e-9
    assert abs(inputs.slots[0].pv_wh - 500.0) < 1e-9  # 1000 * 0.5
    assert inputs.slots[1].hour_of_day == 11
    assert abs(inputs.slots[1].pv_wh - 800.0) < 1e-9  # full hour, no proration


def test_pv_hourly_gap_spreads_residual_over_uncovered_hours():
    """Uncovered hours take their two-window SHARE of the day's residual (daily
    total minus covered buckets); night hours carry no share. There is NO
    renormalisation over the uncovered slots (FIX-3), so a partially covered day
    UNDER-fills — the covered hours' share of the residual is not redistributed."""
    config = SystemConfig()  # morning 7-13 ratio 0.8, afternoon 13-18, peak 3200
    now = datetime(2026, 7, 4, 0, 0)
    daily_kwh = 8.0
    pv_hourly = {
        datetime(2026, 7, 4, 7, 0): 1000.0,
        datetime(2026, 7, 4, 8, 0): 1000.0,
        datetime(2026, 7, 4, 9, 0): 1000.0,
    }
    inputs = build_slots(config, now, 50.0, [daily_kwh], pv_hourly=pv_hourly)
    by_hour = {s.hour_of_day: s.pv_wh for s in inputs.slots}
    # Covered hours keep their bucket value exactly.
    assert by_hour[7] == 1000.0 and by_hour[8] == 1000.0 and by_hour[9] == 1000.0
    residual = daily_kwh * 1000.0 - 3000.0  # 5000
    # Each uncovered slot = residual * its two-window share (no renormalisation).
    assert by_hour[10] == residual * pv_hour_share(config.pv, 10)
    assert by_hour[13] == residual * pv_hour_share(config.pv, 13)
    assert by_hour[10] > by_hour[13]  # morning share > afternoon share
    assert by_hour[2] == 0.0  # night hour has no two-window share
    total = sum(s.pv_wh for s in inputs.slots)
    # Conservative under-fill: never inflated above the daily forecast, and here
    # short by exactly the covered hours' share of the residual (0.4 * 5000).
    assert total < daily_kwh * 1000.0
    assert abs(total - (3000.0 + residual * 0.6)) < 1e-6


def test_pv_hourly_day0_without_buckets_matches_two_window_bit_for_bit():
    """FIX-3: a day with NO hourly buckets while OTHER days do (e.g. today's entity
    lacks wh_period) must reproduce the legacy two-window PV bit-for-bit — even
    when `now` is mid-morning and the elapsed hours are absent from the horizon.
    The old renormalisation over only the remaining slots inflated the afternoon
    3-5x; removing it restores exact parity for a fully-uncovered day."""
    config = SystemConfig()
    now = datetime(2026, 7, 10, 12, 30)  # partial first slot; morning elapsed
    daily = [8.0, 9.0]
    # Non-empty map, but only DAY 1 carries buckets -> day 0 is fully uncovered.
    pv_hourly = {datetime(2026, 7, 11, 11, 0): 700.0}
    inputs = build_slots(config, now, 50.0, daily, pv_hourly=pv_hourly)
    reference = build_slots(config, now, 50.0, daily)  # pure two-window path
    day0 = [
        (s.pv_wh, r.pv_wh)
        for s, r in zip(inputs.slots, reference.slots, strict=True)
        if s.start.date() == now.date()
    ]
    assert day0  # the afternoon-of-day-0 slots exist
    for got, expected in day0:
        assert got == expected  # bit-for-bit, no inflation


def test_pv_hourly_residual_dropped_when_only_night_uncovered():
    """When every PV-bearing hour is covered, the leftover residual has no
    uncovered daytime hour to land on and is dropped (not smeared into night)."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)
    pv_hourly = {datetime(2026, 7, 4, h, 0): 500.0 for h in range(7, 18)}  # 5500 Wh
    inputs = build_slots(config, now, 50.0, [8.0], pv_hourly=pv_hourly)
    total = sum(s.pv_wh for s in inputs.slots)
    assert abs(total - 5500.0) < 1e-6


def test_pv_hourly_peak_cap_applies_to_covered_hour():
    config = SystemConfig()  # peak 3200 W
    now = datetime(2026, 7, 4, 0, 0)
    pv_hourly = {datetime(2026, 7, 4, 12, 0): 5000.0}  # above the cap
    inputs = build_slots(config, now, 50.0, [8.0], pv_hourly=pv_hourly)
    by_hour = {s.hour_of_day: s.pv_wh for s in inputs.slots}
    assert by_hour[12] == config.pv.peak_power_w


def test_pv_hourly_peak_cap_on_partial_slot_is_power_based():
    """The cap limits POWER, so a partial slot is capped at peak * duration."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 12, 30)  # 0.5 h of hour 12
    pv_hourly = {datetime(2026, 7, 4, 12, 0): 5000.0}
    inputs = build_slots(config, now, 50.0, [8.0], pv_hourly=pv_hourly)
    assert abs(inputs.slots[0].duration - 0.5) < 1e-9
    assert abs(inputs.slots[0].pv_wh - config.pv.peak_power_w * 0.5) < 1e-9


def test_pv_hourly_is_per_day_disjoint():
    """A multi-day map maps each day from its own buckets; day 1 covered hours
    are untouched by day 0's residual."""
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)
    pv_hourly = {
        datetime(2026, 7, 4, 11, 0): 900.0,  # day 0
        datetime(2026, 7, 5, 11, 0): 700.0,  # day 1
    }
    inputs = build_slots(config, now, 50.0, [8.0, 8.0], pv_hourly=pv_hourly)
    day0 = {s.hour_of_day: s.pv_wh for s in inputs.slots if s.start.day == 4}
    day1 = {s.hour_of_day: s.pv_wh for s in inputs.slots if s.start.day == 5}
    assert day0[11] == 900.0
    assert day1[11] == 700.0


def test_pv_hourly_does_not_change_ac_or_dc_loads():
    config = SystemConfig()
    now = datetime(2026, 7, 4, 0, 0)
    pv_hourly = {datetime(2026, 7, 4, 12, 0): 1000.0}
    with_h = build_slots(config, now, 50.0, [8.0], pv_hourly=pv_hourly)
    without = build_slots(config, now, 50.0, [8.0])
    for a, b in zip(with_h.slots, without.slots, strict=True):
        assert a.ac_wh == b.ac_wh
        assert a.dc_wh == b.dc_wh
