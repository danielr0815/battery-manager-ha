"""Tests for hourly series construction."""

from datetime import datetime

from core.model import ApplianceRun, SystemConfig
from core.series import build_slots, insert_appliance_run, slot_starts


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
