"""Invariant tests for the pure energy-flow simulation."""

from datetime import datetime

from core.model import PlanInputs, SupportParams, SystemConfig
from core.series import build_slots
from core.simulate import simulate

NOW_NIGHT = datetime(2026, 7, 3, 22, 0)
NOW_NOON = datetime(2026, 7, 3, 12, 0)


def make_inputs(config, now, soc, forecasts):
    return build_slots(config, now, soc, forecasts)


def test_soc_stays_within_bounds():
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NOON, 50.0, [15.0, 15.0, 0.0])
    traj = simulate(config, inputs, 40.0)
    for flow in traj.flows:
        assert 0.0 <= flow.soc_end_percent <= 100.0
        assert flow.soc_end_percent <= config.battery.soc_max_percent + 1e-6


def test_no_negative_flows():
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NIGHT, 60.0, [5.0, 5.0, 5.0])
    traj = simulate(config, inputs, 30.0)
    for flow in traj.flows:
        assert flow.grid_import_wh >= 0.0
        assert flow.grid_export_wh >= 0.0
        assert flow.battery_charge_wh >= 0.0
        assert flow.battery_discharge_wh >= 0.0


def test_policy_consistency():
    """The inverter must run exactly when SOC is above the threshold (P1)."""
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NIGHT, 70.0, [0.0, 10.0, 10.0])
    threshold = 40.0
    traj = simulate(config, inputs, threshold)
    for flow in traj.flows:
        assert flow.inverter_on == (flow.soc_start_percent > threshold)


def test_night_discharge_supplies_house():
    """With inverter on at night the house runs from battery, not grid."""
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NIGHT, 80.0, [0.0, 0.0, 0.0])
    traj = simulate(config, inputs, 20.0)
    first = traj.flows[0]
    assert first.inverter_on
    assert first.grid_import_wh < 1e-6
    assert first.battery_discharge_wh > 0.0


def test_inverter_off_means_grid_import():
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NIGHT, 80.0, [0.0, 0.0, 0.0])
    traj = simulate(config, inputs, 95.0)  # threshold above SOC -> inverter off
    first = traj.flows[0]
    assert not first.inverter_on
    assert first.grid_import_wh > 0.0
    assert first.inverter_output_wh == 0.0


def test_surplus_charges_battery_then_exports():
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NOON, 50.0, [20.0, 0.0, 0.0])
    traj = simulate(config, inputs, 20.0)
    noon = traj.flows[0]
    assert noon.battery_charge_wh > 0.0
    # Battery eventually full -> some export must appear during the day
    assert traj.total_export_wh > 0.0
    assert traj.max_soc_percent <= config.battery.soc_max_percent + 1e-6


def test_dc_load_forces_grid_supply_at_empty_battery():
    """Battery at hard minimum: DC rail must be kept alive from the grid."""
    config = SystemConfig()
    soc_min = config.battery.soc_min_percent
    inputs = make_inputs(config, NOW_NIGHT, soc_min, [0.0, 0.0, 0.0])
    traj = simulate(config, inputs, 95.0)
    first = traj.flows[0]
    assert first.grid_import_wh > 0.0
    assert first.soc_end_percent >= soc_min - 1e-6


def test_support_dc24_shifts_dc_load_to_grid():
    config = SystemConfig(support=SupportParams(configured=True))
    inputs = make_inputs(config, NOW_NIGHT, 30.0, [0.0, 0.0, 0.0])
    n = len(inputs.slots)
    base = simulate(config, inputs, 95.0)
    supported = simulate(config, inputs, 95.0, dc24_schedule=tuple([True] * n))
    # Battery is spared: SOC stays higher with the 24 V PSU active.
    assert supported.end_soc_percent > base.end_soc_percent
    assert supported.total_import_wh > base.total_import_wh


def test_support_dc48_charges_battery_from_grid():
    config = SystemConfig(support=SupportParams(configured=True, dc48_power_w=60.0))
    inputs = make_inputs(config, NOW_NIGHT, 20.0, [0.0, 0.0, 0.0])
    n = len(inputs.slots)
    plain = simulate(config, inputs, 95.0, dc24_schedule=tuple([True] * n))
    boosted = simulate(
        config,
        inputs,
        95.0,
        dc24_schedule=tuple([True] * n),
        dc48_schedule=tuple([True] * n),
    )
    assert boosted.end_soc_percent > plain.end_soc_percent


def test_partial_first_hour_scales_energy():
    config = SystemConfig()
    quarter = datetime(2026, 7, 3, 22, 45)
    inputs = build_slots(config, quarter, 80.0, [0.0, 0.0, 0.0])
    assert abs(inputs.slots[0].duration - 0.25) < 1e-9
    full_hour_ac = config.ac_profile.power_w(22)
    assert abs(inputs.slots[0].ac_wh - full_hour_ac * 0.25) < 1e-9


def test_empty_horizon_is_valid():
    config = SystemConfig()
    inputs = PlanInputs(now=NOW_NIGHT, start_soc_percent=50.0, slots=())
    traj = simulate(config, inputs, 50.0)
    assert traj.total_import_wh == 0.0
    assert traj.end_soc_percent == 50.0
