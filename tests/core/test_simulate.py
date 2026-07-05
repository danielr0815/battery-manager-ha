"""Invariant tests for the pure energy-flow simulation."""

from datetime import datetime

from core.model import LoadProfile, PlanInputs, SupportParams, SystemConfig
from core.series import build_slots
from core.simulate import simulate

NOW_NIGHT = datetime(2026, 7, 3, 22, 0)
NOW_NOON = datetime(2026, 7, 3, 12, 0)

_EPS = 1e-6


def make_inputs(config, now, soc, forecasts):
    return build_slots(config, now, soc, forecasts)


def _dc_config(support, dc_base_w=1000.0):
    """Config with a fixed DC load and no PV, to isolate the DC-bus path."""
    return SystemConfig(
        support=support,
        dc_profile=LoadProfile(base_w=dc_base_w, variable_w=0.0),
        ac_profile=LoadProfile(base_w=0.0, variable_w=0.0),
    )


def _first_flow(config, *, soc=80.0, dc24_from_grid=False, dc48=False):
    """Simulate one full night hour and return the first slot's flows.

    Threshold above SOC keeps the inverter OFF, so no AC-side standby drain
    pollutes the DC-path diagnostics we assert on."""
    inputs = build_slots(config, NOW_NIGHT, soc, [0.0])
    traj = simulate(
        config,
        inputs,
        threshold_percent=99.0,
        dc24_schedule=(dc24_from_grid,) * len(inputs.slots),
        dc48_schedule=(dc48,) * len(inputs.slots),
    )
    return traj.flows[0]


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


# ---------------------------------------------------------------------------
# F-N3 two-bus combination equations (docs/DC_TOPOLOGY.md §4). These exercise
# the new device physics directly, with non-neutral parameters that the
# behaviour-neutral golden suite deliberately does not reach.
# ---------------------------------------------------------------------------


def test_dc24_share_splits_rail_and_native_bus():
    """`dc24_share` puts part of the DC load on the 24 V rail (via DC/DC)
    and the rest as native 48 V bus load."""
    config = _dc_config(
        SupportParams(configured=True, dc24_share=0.6), dc_base_w=1000.0
    )
    flow = _first_flow(config)
    # 600 Wh on the rail through the DC/DC (eta 1 => bus draw 600),
    # 400 Wh native — both drain the battery (bus_load 1000).
    assert abs(flow.dcdc_input_wh - 600.0) < _EPS
    assert abs(flow.unserved_dc_wh) < _EPS
    assert abs(flow.battery_discharge_wh - 1000.0 / config.battery.eta_discharge) < 1e-3


def test_dcdc_efficiency_adds_bus_draw_and_loss():
    config = _dc_config(SupportParams(configured=True, dcdc_eta=0.9), dc_base_w=900.0)
    flow = _first_flow(config)
    # rail 900 Wh served; DC/DC draws 900/0.9 = 1000 from the bus, loss 100.
    assert abs(flow.dcdc_input_wh - 1000.0) < _EPS
    assert abs(flow.dcdc_loss_wh - 100.0) < _EPS


def test_dcdc_cap_creates_unserved_rail_demand():
    config = _dc_config(
        SupportParams(configured=True, dcdc_max_power_w=500.0), dc_base_w=1000.0
    )
    flow = _first_flow(config)
    # Cap 500 W over a 1 h slot: only 500 Wh served, 500 Wh unserved.
    assert abs(flow.dcdc_input_wh - 500.0) < _EPS
    assert abs(flow.unserved_dc_wh - 500.0) < _EPS


def test_psu24_from_grid_efficiency_and_cap():
    config = _dc_config(
        SupportParams(configured=True, psu24_eta=0.85, psu24_max_power_w=600.0),
        dc_base_w=1000.0,
    )
    flow = _first_flow(config, dc24_from_grid=True)
    # 24 V PSU feeds the rail from the grid, capped at 600 Wh, 400 unserved;
    # nothing drains the battery (DC/DC off), grid pays served/eta.
    assert abs(flow.psu24_delivered_wh - 600.0) < _EPS
    assert abs(flow.unserved_dc_wh - 400.0) < _EPS
    assert abs(flow.dcdc_input_wh) < _EPS
    assert abs(flow.battery_discharge_wh) < _EPS
    assert flow.grid_import_wh >= 600.0 / 0.85 - _EPS


def test_psu48_voltage_gate_opens_below_soc_proxy():
    config = _dc_config(
        SupportParams(configured=True, dc48_power_w=60.0, gate_soc_percent=50.0),
        dc_base_w=0.0,
    )
    # Above the gate SOC: PSU switched on but delivers nothing.
    high = _first_flow(config, soc=80.0, dc48=True)
    assert high.gate_open is False
    assert abs(high.psu48_delivered_wh) < _EPS
    # Below the gate SOC: PSU delivers its rated power onto the bus.
    low = _first_flow(config, soc=30.0, dc48=True)
    assert low.gate_open is True
    assert abs(low.psu48_delivered_wh - 60.0) < _EPS


def test_rail_node_energy_conserved_per_slot():
    """served + unserved == rail demand, and DC/DC loss == input - served,
    across every combination of share / efficiency / cap."""
    cases = [
        SupportParams(configured=True, dc24_share=0.7, dcdc_eta=0.92),
        SupportParams(configured=True, dcdc_max_power_w=400.0),
        SupportParams(configured=True, psu24_eta=0.8, psu24_max_power_w=700.0),
    ]
    for sp in cases:
        for grid in (False, True):
            config = _dc_config(sp, dc_base_w=1000.0)
            flow = _first_flow(config, dc24_from_grid=grid)
            rail = 1000.0 * sp.dc24_share
            served = (
                flow.psu24_delivered_wh if grid else flow.dcdc_input_wh * sp.dcdc_eta
            )
            assert abs(served + flow.unserved_dc_wh - rail) < 1e-3
            if not grid:
                assert abs(flow.dcdc_loss_wh - (flow.dcdc_input_wh - served)) < 1e-3
