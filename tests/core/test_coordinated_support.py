"""F-COORDINATED-DC-SUPPORT: physical balance and shared reserve decisions."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from core.model import HourSlot, PlanInputs, SupportParams, SystemConfig
from core.optimize import plan
from core.simulate import simulate, step_hour
from core.support import support_state

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def config(**kwargs):
    return SystemConfig(
        support=SupportParams(
            configured=True,
            coordinated=True,
            native48_base_w=35,
            dcdc_eta=0.93,
            psu24_eta=0.89,
            psu48_eta=0.89,
            psu48_max_power_w=49.56 * 1.15,
            **kwargs,
        )
    )


def inputs(soc=25, pv=0, ac=50, dc=59.7, duration=1):
    slot = HourSlot(0, NOW, duration, 0, pv, ac, dc)
    return PlanInputs(NOW, soc, (slot,))


def test_dc_loads_remain_after_inverter_cutoff():
    c = config()
    f = simulate(c, inputs(), 30).flows[0]
    assert not f.inverter_on
    assert not f.support_dc24 and not f.support_dc48
    assert f.battery_discharge_wh == pytest.approx((35 + 24.7 / 0.93) / 0.97)
    assert f.grid_import_wh == pytest.approx(50)


@pytest.mark.parametrize("v,power", [(48, 55.2), (49.56, 35), (52, 0), (None, 0)])
def test_current_limit_and_voltage_gate(v, power):
    c = config(psu48_bus_voltage_v=v)
    f = step_hour(
        c, 6, inputs(dc=100).slots[0], 20, dc24_from_grid=True, dc48_support=True
    )
    assert f.psu48_delivered_wh == pytest.approx(power)
    assert f.psu24_delivered_wh == pytest.approx(65)
    assert f.grid_import_wh == pytest.approx(50 + (65 + power) / 0.89)
    assert not f.inverter_on


def test_rail_transfer_removes_only_24v_load():
    f = simulate(config(dc24_forced_on=True), inputs(soc=60), 20).flows[0]
    assert not f.inverter_on
    assert f.battery_discharge_wh == pytest.approx(35 / 0.97)
    assert f.psu24_delivered_wh == pytest.approx(24.7)
    assert f.dcdc_input_wh == 0


def test_insufficient_support_reports_deficit_without_phantom_grid_charger():
    c = config(dc24_forced_on=True, dc48_forced_on=True, psu48_bus_voltage_v=48)
    c = replace(c, support=replace(c.support, native48_base_w=100))
    f = simulate(c, inputs(soc=5, dc=100), 20).flows[0]
    assert f.unserved_dc_wh == pytest.approx(44.8)
    assert f.grid_import_wh == pytest.approx(50 + 55.2 / 0.89)
    assert f.soc_end_percent == pytest.approx(5)


def test_pv_covers_floor_deficit_without_grid_import():
    f = step_hour(config(), 5, inputs(pv=200, ac=0, dc=100).slots[0], 20)
    assert f.unserved_dc_wh == 0
    assert f.grid_import_wh == 0
    assert f.battery_charge_wh > 0


def test_pv_insufficient_for_dc_reports_only_remaining_shortfall():
    f = step_hour(config(), 5, inputs(pv=10, ac=0, dc=100).slots[0], 20)
    assert f.grid_import_wh == 0
    assert f.unserved_dc_wh > 90


def test_low_soc_escalates_both_sources_and_blocks_inverter():
    f = simulate(config(psu48_bus_voltage_v=48), inputs(soc=5.4), 0).flows[0]
    assert f.support_dc24_start and f.support_dc48_start
    assert not f.inverter_start
    assert f.support_mode == "dc48"
    assert f.battery_charge_wh > 0


def test_grid_recovery_does_not_release_dc24_or_inverter():
    c = config(dc24_active=True, dc48_active=True, psu48_bus_voltage_v=48)
    f = simulate(c, inputs(soc=15), 10).flows[0]
    assert f.support_dc24_start and not f.support_dc48_start
    assert not f.inverter_on


def test_pv_recovery_releases_support():
    c = config(dc24_active=True, dc48_active=True)
    f = simulate(c, inputs(soc=50, pv=500), 20).flows[0]
    assert not f.support_dc24 and not f.support_dc48
    assert f.inverter_on


def test_missing_sources_are_not_simulated():
    f = simulate(
        config(dc24_available=False, dc48_available=False), inputs(soc=5), 20
    ).flows[0]
    assert not f.support_dc24 and not f.support_dc48
    assert f.unserved_dc_wh > 0


def test_activation_inside_hour_does_not_buy_full_hour_of_support():
    f = simulate(config(), inputs(soc=10.8), 20).flows[0]
    assert not f.support_dc24_start
    assert f.support_dc24
    assert 0 < f.psu24_delivered_wh < 24.7
    assert f.soc_end_percent > 9


def test_psu_power_is_not_guaranteed_in_stress_run():
    c = config(dc24_forced_on=True, dc48_forced_on=True, psu48_bus_voltage_v=48)
    expected = simulate(c, inputs(soc=6), 20).flows[0]
    stress = simulate(c, inputs(soc=6), 20, pv_scale=(0.8,)).flows[0]
    assert expected.psu48_delivered_wh > 0
    assert stress.psu48_delivered_wh == 0
    assert stress.soc_end_percent < expected.soc_end_percent


def test_plan_exposes_slot_start_support_not_future_activation():
    result = plan(config(), inputs(soc=10.8))
    assert result.support_dc24_now == result.trajectory.flows[0].support_dc24_start
    assert result.inverter_on == result.trajectory.flows[0].inverter_start


def test_empty_horizon_keeps_soc():
    trajectory = simulate(config(), PlanInputs(NOW, 20, ()), 25)
    assert trajectory.flows == () and trajectory.end_soc_percent == 20


def test_feed_in_and_additional_ac_are_conserved_on_partial_slot():
    f = simulate(
        config(),
        inputs(soc=50, pv=200, ac=10, dc=0, duration=0.25),
        20,
        extra_ac_wh=(5,),
        feedin_wh=(20,),
    ).flows[0]
    assert f.extra_ac_wh == pytest.approx(5)
    assert f.feedin_wh == pytest.approx(20)
    assert f.grid_export_wh >= 20 - 1e-9


def test_manual_request_obeys_source_availability_and_recovery():
    c = config(dc24_forced_on=True, dc48_forced_on=True)
    assert support_state(c, 80, False, False, True) == (True, True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("psu48_output_voltage_v", 0),
        ("psu48_bus_voltage_v", float("nan")),
        ("psu48_bus_voltage_v", 61),
    ],
)
def test_invalid_psu_voltage_is_rejected(field, value):
    with pytest.raises(ValueError):
        config(**{field: value})


def test_unknown_current_limit_never_becomes_unlimited_supply():
    c = replace(
        config(psu48_bus_voltage_v=48),
        support=replace(config(psu48_bus_voltage_v=48).support, psu48_max_power_w=None),
    )
    f = step_hour(c, 6, inputs().slots[0], 20, dc48_support=True)
    assert f.psu48_delivered_wh == 0


def test_undersized_24v_psu_is_not_selected_for_transfer():
    c = config(psu24_max_power_w=10)
    f = simulate(c, inputs(soc=9, dc=60), 20).flows[0]
    assert not f.support_dc24
    assert f.dcdc_input_wh > 0
    assert f.psu24_delivered_wh == 0
