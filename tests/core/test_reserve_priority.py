"""Operator 2026-09-28: preserve DC supply and favour useful high-load AC windows."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from core.model import (
    BatteryParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.simulate import simulate

NOW = datetime(2026, 9, 28, 16)


def plant():
    return SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(2000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        support=SupportParams(configured=True, coordinated=True, native48_base_w=0),
        reserve=ReserveParams(True, 1),
    )


def series(values, soc=80):
    return PlanInputs(
        NOW,
        soc,
        tuple(
            HourSlot(i, NOW + timedelta(hours=i), 1, (16 + i) % 24, pv, ac, dc)
            for i, (pv, ac, dc) in enumerate(values)
        ),
    )


@pytest.mark.parametrize(
    "demands, expected",
    [((600, 100), (150, 0)), ((100, 600), (0, 150)), ((300, 300), (0, 150))],
)
def test_only_required_ac_uses_highest_load_then_latest_window(demands, expected):
    result = simulate(
        plant(), series([(0, demands[0], 0), (0, demands[1], 0), (300, 0, 0)]), 20
    )
    assert [f.inverter_output_wh for f in result.flows[:2]] == pytest.approx(expected)
    assert result.total_export_wh == pytest.approx(0)
    assert result.end_soc_percent == pytest.approx(95)


def test_earlier_ac_cannot_force_later_dc_grid_support():
    result = simulate(plant(), series([(0, 600, 0), (0, 0, 600), (900, 0, 0)]), 20)
    assert result.flows[0].inverter_output_wh <= 100
    assert not result.flows[1].support_dc24
    assert result.flows[1].psu24_delivered_wh == 0
    assert result.flows[1].unserved_dc_wh == 0
    # A second 50-Wh ON step would consume the DC protection margin.
    assert result.flows[0].inverter_output_wh == pytest.approx(50)
    assert result.total_export_wh == pytest.approx(100, abs=1e-4)


def test_dc_alone_creates_space_so_no_ac_is_required():
    result = simulate(
        plant(), series([(0, 600, 100), (0, 100, 200), (300, 0, 0)], soc=95), 20
    )
    assert sum(f.inverter_output_wh for f in result.flows) == 0
    assert sum(f.psu24_delivered_wh for f in result.flows) == 0
    assert result.total_export_wh == pytest.approx(0)


def test_high_house_load_with_pv_is_not_a_battery_opportunity():
    result = simulate(plant(), series([(600, 600, 0), (0, 100, 0), (300, 0, 0)]), 20)
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh == pytest.approx(100)


def test_partial_slot_priority_compares_watts_not_watt_hours():
    inputs = series([(0, 150, 0), (0, 200, 0), (300, 0, 0)])
    inputs = replace(
        inputs, slots=(replace(inputs.slots[0], duration=0.25), *inputs.slots[1:])
    )
    result = simulate(plant(), inputs, 20)
    assert result.flows[0].inverter_output_wh == pytest.approx(150)
    assert result.flows[1].inverter_output_wh == 0


def test_upper_sunshine_does_not_guarantee_energy_for_future_dc():
    result = simulate(
        plant(),
        series([(0, 600, 0), (500, 0, 100), (0, 0, 650), (2000, 0, 0)], soc=85),
        20,
        pv_scale=0,
    )
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh == 0


def live_forecast():
    import json
    from pathlib import Path

    from core.model import ControlParams, PVParams

    fixture = json.loads(
        (
            Path(__file__).with_name("fixtures") / "reserve_load_priority.json"
        ).read_text()
    )
    values = fixture["config"]
    config = SystemConfig(
        battery=BatteryParams(**values["battery"]),
        inverter=ConverterParams(**values["inverter"]),
        charger=ConverterParams(**values["charger"]),
        pv=PVParams(**values["pv"]),
        control=ControlParams(**values["control"]),
        support=SupportParams(**values["support"]),
        reserve=ReserveParams(**values["reserve"]),
    )
    slots = tuple(
        HourSlot(**{**s, "start": datetime.fromisoformat(s["start"])})
        for s in fixture["slots"]
    )
    return config, PlanInputs(
        datetime.fromisoformat(fixture["now"]), fixture["start_soc_percent"], slots
    )


@pytest.mark.parametrize("scale", [1.0, 1.2])
def test_live_afternoon_preservation_cannot_purchase_later_ac_discharge(scale):
    config, inputs = live_forecast()
    result = simulate(config, inputs, 20, pv_scale=scale)
    afternoon = [
        f
        for s, f in zip(inputs.slots, result.flows, strict=True)
        if s.start.date().isoformat() == "2026-09-29" and 15 <= s.start.hour < 18
    ]
    assert not any(f.support_dc24 or f.support_dc48 for f in afternoon)
    assert not any(f.unserved_dc_wh for f in result.flows)
    assert (
        result.min_soc_percent >= 19.5
    )  # small DC draw continues below AC's 20% floor
    assert result.max_soc_percent <= 95 + 1e-6
    if scale == 1:
        by_time = {
            s.start.isoformat(): f
            for s, f in zip(inputs.slots, result.flows, strict=True)
        }
        assert by_time["2026-09-29T21:00:00+02:00"].inverter_output_wh == 0
        assert by_time["2026-09-29T18:00:00+02:00"].inverter_output_wh > 268.4
        assert result.total_import_wh < 1466.9
        # The single horizon preserves DC for the full forecast instead of
        # spending it before a midnight reset; binary steps leave < one step.
        assert result.total_export_wh <= config.inverter.max_power_w / 12


def test_high_load_after_the_pv_peak_cannot_replace_required_night_discharge():
    result = simulate(
        plant(), series([(0, 100, 0), (300, 0, 0), (0, 600, 0)], soc=85), 20
    )
    assert result.flows[0].inverter_output_wh == pytest.approx(100)
    assert result.flows[2].inverter_output_wh == 0
    assert result.total_export_wh == pytest.approx(100)


@pytest.mark.parametrize("standby", [0, 15])
def test_binary_preparation_uses_full_permission_and_budgets_standby(standby):
    c = plant()
    c = replace(c, inverter=replace(c.inverter, standby_power_w=standby))
    # 175 Wh of headroom: three full 5-minute steps fit, four do not.
    result = simulate(c, series([(0, 600, 0), (325, 0, 0)]), 20)
    first = result.flows[0]
    assert all(f.inverter_limit_w in (0, 1000) for f in result.flows)
    on_hours = sum(
        (interval.end - interval.start).total_seconds() / 3600
        for interval in first.switching_schedule
        if interval.inverter_on
    )
    assert on_hours == pytest.approx(0.25)
    assert first.inverter_output_wh == pytest.approx((600 + standby) * on_hours)
    assert first.soc_end_percent >= 62.5
    assert result.total_export_wh == pytest.approx(175 - first.inverter_output_wh)


def test_tiny_budget_does_not_run_inverter_at_partial_power():
    result = simulate(plant(), series([(0, 600, 0), (175, 0, 0)]), 20)
    assert result.flows[0].inverter_output_wh == 0
    assert not any(i.inverter_on for i in result.flows[0].switching_schedule)
    assert result.total_export_wh == pytest.approx(25)
