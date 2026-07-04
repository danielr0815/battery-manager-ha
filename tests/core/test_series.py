"""Tests for hourly series construction."""

from datetime import datetime

from core.model import ApplianceRun, SystemConfig
from core.series import build_slots, insert_appliance_run


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
        w.ac_wh - b.ac_wh
        for w, b in zip(with_run.slots, without.slots, strict=True)
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
