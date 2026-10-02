"""Reserve decisions must spend useful energy at the last necessary opportunity."""

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

NOW = datetime(2026, 9, 27, 20)


def plant():
    config = SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(20000, 1, 0),
        inverter=ConverterParams(20000, 1, 0),
        support=SupportParams(
            configured=True,
            coordinated=True,
            dc24_available=False,
            dc48_available=False,
        ),
        reserve=ReserveParams(True, 1),
    )
    return replace(config, pv=replace(config.pv, peak_power_w=20000))


def series(soc, values, duration=1):
    return PlanInputs(
        NOW,
        soc,
        tuple(
            HourSlot(
                index,
                NOW + timedelta(hours=index * duration),
                duration,
                (NOW.hour + index) % 24,
                pv,
                ac,
                dc,
            )
            for index, (pv, ac, dc) in enumerate(values)
        ),
    )


def test_unavoidable_export_does_not_spend_reserve_before_last_ac_window():
    result = simulate(plant(), series(80, [(0, 500, 0), (0, 600, 0), (2000, 0, 0)]), 20)
    assert result.flows[0].inverter_output_wh == pytest.approx(0)
    assert result.flows[0].soc_end_percent == pytest.approx(80)
    assert result.flows[1].inverter_output_wh == pytest.approx(600)
    assert result.total_import_wh == pytest.approx(500)
    assert result.total_export_wh == pytest.approx(1250)


def test_dc_below_ac_floor_requires_the_earlier_ac_opportunity():
    result = simulate(
        plant(), series(40, [(0, 200, 0), (0, 200, 150), (900, 0, 0)], 1 / 12), 20
    )
    assert result.flows[0].inverter_output_wh == pytest.approx(200)
    assert result.flows[1].inverter_output_wh == pytest.approx(0)
    assert result.flows[1].soc_end_percent == pytest.approx(5)
    assert result.total_export_wh == pytest.approx(0)
    assert sum(flow.unserved_dc_wh for flow in result.flows) == pytest.approx(0)


def test_third_day_deadline_can_use_the_preceding_evening():
    inputs = series(80, [(0, 200, 0), (0, 200, 0), (900, 0, 0)])
    inputs = replace(
        inputs,
        slots=(
            inputs.slots[0],
            replace(inputs.slots[1], start=NOW + timedelta(days=1)),
            replace(inputs.slots[2], start=NOW + timedelta(days=2)),
        ),
    )
    result = simulate(plant(), inputs, 20)
    assert result.flows[0].inverter_output_wh == pytest.approx(200)
    # The same PV deadline is visible before midnight, using both opportunities.
    assert result.flows[1].inverter_output_wh == pytest.approx(200)


@pytest.mark.parametrize("hold", [None, 0, 38])
def test_legacy_hold_cannot_override_low_soc_support(hold):
    config = plant()
    config = replace(
        config,
        support=replace(
            config.support,
            dc48_available=True,
            psu48_bus_voltage_v=49.35,
            psu48_max_power_w=56.994,
        ),
    )
    inputs = replace(
        series(5.4, [(0, 0, 40 / 12)], 1 / 12), reserve_hold_soc_percent=hold
    )
    flow = simulate(config, inputs, 20).flows[0]
    assert flow.support_dc48
    assert flow.psu48_delivered_wh == pytest.approx(4.729374999999999)
    assert flow.soc_end_percent > 5.4


def test_high_soc_holding_reports_incidental_charge_separately():
    config = plant()
    config = replace(
        config,
        support=replace(
            config.support,
            dc24_available=True,
            dc48_available=True,
            psu48_bus_voltage_v=49.35,
            psu48_max_power_w=57,
        ),
    )
    result = simulate(config, series(80, [(0, 100, 60)]), 20)
    flow = result.flows[0]
    assert flow.soc_end_percent > 80
    assert flow.support_dc24 and flow.support_dc48
    assert flow.battery_discharge_wh == 0
    assert flow.psu48_battery_charge_wh == pytest.approx(
        (flow.soc_end_percent - 80) * 10
    )
    assert result.flows[0].inverter_output_wh == 0


def test_current_dc_consumption_is_not_reported_as_additional_ac_headroom():
    result = simulate(plant(), series(95, [(0, 500, 100), (100, 0, 0)]), 20)
    assert result.reserve_decision is not None
    assert result.reserve_decision.headroom_wh == pytest.approx(0)
    assert result.reserve_decision.inverter_limit_w == 0
    assert sum(flow.inverter_output_wh for flow in result.flows) == 0
    assert result.total_export_wh == pytest.approx(0)


def test_source_protection_also_wins_when_custom_ac_floor_is_below_support():
    config = plant()
    config = replace(
        config,
        control=replace(config.control, inverter_min_soc_percent=5),
        support=replace(config.support, dc48_available=True),
    )
    result = simulate(config, series(6, [(0, 10, 0), (1000, 0, 0)], 1 / 12), 5)
    assert result.flows[0].support_dc48
    assert result.flows[0].inverter_output_wh == 0
    assert result.reserve_decision.reason == "dc_support_protection"


def test_no_sun_preserves_dc_energy_instead_of_spending_the_remaining_soc():
    config = plant()
    config = replace(config, support=replace(config.support, dc24_available=True))
    result = simulate(config, series(80, [(0, 600, 0), (0, 0, 650)]), 20)
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].support_dc24
    assert result.flows[1].soc_end_percent == pytest.approx(80)
    assert result.flows[1].psu24_delivered_wh == pytest.approx(650)


@pytest.mark.parametrize("month, day", [(3, 28), (10, 24)])
def test_full_horizon_preserves_elapsed_hours_across_dst(month, day):
    from datetime import UTC, timezone
    from zoneinfo import ZoneInfo

    local_zone = ZoneInfo("Europe/Berlin")
    start = datetime(2026, month, day, 12, tzinfo=local_zone)
    tomorrow_end = (start + timedelta(days=2)).replace(hour=0)
    slots = []
    for index in range(60):
        local = (start.astimezone(UTC) + timedelta(hours=index)).astimezone(local_zone)
        # This is the replay-stable representation emitted by series.build_slots.
        fixed = local.replace(tzinfo=timezone(local.utcoffset()))
        slots.append(
            HourSlot(
                index, fixed, 1, fixed.hour, 900 if local >= tomorrow_end else 0, 100, 0
            )
        )
    result = simulate(plant(), PlanInputs(start, 80, tuple(slots)), 20)
    decision = result.reserve_decision
    assert decision.preparation_horizon_end.astimezone(UTC) == start.astimezone(
        UTC
    ) + timedelta(hours=60)
    assert (
        decision.preparation_horizon_end.astimezone(UTC) - start.astimezone(UTC)
    ).total_seconds() == 60 * 3600
    todays_hours = sum(slot.start.date() == start.date() for slot in slots)
    assert all(flow.inverter_output_wh == 0 for flow in result.flows[:todays_hours])


def test_diagnostic_horizon_stops_at_the_available_partial_slot():
    inputs = series(80, [(0, 100, 0)], 0.25)
    result = simulate(plant(), inputs, 20)
    assert result.reserve_decision.preparation_horizon_end == NOW + timedelta(
        minutes=15
    )
