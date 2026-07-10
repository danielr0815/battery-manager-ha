"""Invariant tests for the pure energy-flow simulation."""

from dataclasses import replace
from datetime import datetime

from core.model import HourSlot, LoadProfile, PlanInputs, SupportParams, SystemConfig
from core.series import build_slots
from core.simulate import simulate, step_hour

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


def _slot(pv, ac, dc, dur=1.0):
    return HourSlot(
        index=0,
        start=NOW_NOON,
        duration=dur,
        hour_of_day=12,
        pv_wh=pv,
        ac_wh=ac,
        dc_wh=dc,
    )


# --- #1 energy conservation: no phantom import while PV is exported/stored ---


def test_pv_surplus_covers_dc_load_no_phantom_import():
    """Review #1: with the battery at its floor and a same-slot PV surplus, the
    DC bus load must be served from PV (via the charger), NOT imported from grid
    while the surplus is simultaneously exported/stored."""
    config = SystemConfig()
    # Zero charger standby so the assertion isolates the DC-load path (standby
    # would otherwise leak a few Wh to grid once the surplus is exactly used).
    config = replace(config, charger=replace(config.charger, standby_power_w=0.0))
    soc = config.battery.soc_min_percent  # battery empty -> DC can't drain it
    # 2 kWh PV surplus, 500 Wh DC bus load, inverter off (threshold above SOC).
    # Pre-fix this imported ~540 Wh for the DC load while storing/exporting PV.
    flow = step_hour(config, soc, _slot(pv=2000.0, ac=0.0, dc=500.0), 99.0)
    assert flow.grid_import_wh < _EPS  # PV covered the DC load, nothing imported
    # The (large) surplus still stores/exports after covering the DC load.
    assert flow.grid_export_wh > 0.0 or flow.battery_charge_wh > 0.0


def test_dc_load_at_floor_without_pv_still_imports():
    """Regression: with no PV surplus, a floor-battery DC load imports via the
    charger and never exports (the deficit path is unchanged)."""
    config = SystemConfig()
    soc = config.battery.soc_min_percent
    flow = step_hour(config, soc, _slot(pv=0.0, ac=0.0, dc=500.0), 99.0)
    assert flow.grid_import_wh > 0.0
    assert flow.grid_export_wh < _EPS


# --- #4 net-charging gate suppression ---


def test_gate_closed_during_net_charging():
    """Review #4: during a net-charging slot the charger lifts the 48 V bus over
    the PSU output, so the real PSU self-gates — the gate must be closed."""
    config = SystemConfig(support=SupportParams(configured=True))
    flow = step_hour(
        config, 50.0, _slot(pv=2000.0, ac=0.0, dc=200.0), 99.0, dc48_support=True
    )
    assert flow.gate_open is False
    assert flow.psu48_delivered_wh < _EPS


def test_gate_open_when_not_charging():
    """Contrast: with no PV surplus the gate stays open and the PSU delivers."""
    config = SystemConfig(support=SupportParams(configured=True))
    flow = step_hour(
        config, 50.0, _slot(pv=0.0, ac=0.0, dc=200.0), 99.0, dc48_support=True
    )
    assert flow.gate_open is True
    assert flow.psu48_delivered_wh > 0.0


# --- #9 gate-edge taper ---


def test_psu48_charge_tapers_at_gate_soc():
    """Review #9: the PSU must not charge the battery past gate_soc within a
    single slot (no overshoot)."""
    config = SystemConfig(
        support=SupportParams(
            configured=True, gate_soc_percent=60.0, dc48_power_w=5000.0
        )
    )
    # Start just below the gate, big PSU power, no PV (not net-charging), no DC.
    flow = step_hour(
        config, 59.0, _slot(pv=0.0, ac=0.0, dc=0.0), 99.0, dc48_support=True
    )
    assert flow.soc_end_percent <= 60.0 + 1e-6  # tapered at the gate, no overshoot


# --- native 48 V fixed base load (v0.7.12) ---


def test_native48_base_load_bypasses_the_rail():
    """A fixed native-48 V base load is carved off BEFORE the rail split, so it
    stays on the 48 V bus and does not flow through the DC/DC converter (a
    percentage share could not represent a constant absolute load)."""
    slot = _slot(pv=0.0, ac=0.0, dc=500.0)  # 500 Wh DC load, no PV
    f0 = step_hour(
        SystemConfig(support=SupportParams(configured=True)), 80.0, slot, 99.0
    )
    f1 = step_hour(
        SystemConfig(support=SupportParams(configured=True, native48_base_w=200.0)),
        80.0,
        slot,
        99.0,
    )
    # Neutral (base 0, share 1.0, eta 1.0): all 500 Wh go through the DC/DC.
    assert abs(f0.dcdc_input_wh - 500.0) < _EPS
    # With a 200 W native 48 V base: only the remaining 300 Wh use the DC/DC.
    assert abs(f1.dcdc_input_wh - 300.0) < _EPS
    # Total DC load served is unchanged (battery drain identical).
    assert abs(f0.battery_discharge_wh - f1.battery_discharge_wh) < _EPS


# --- #16 defensive inverted-bounds clamp ---


def test_inverted_soc_bounds_do_not_break_sim():
    """Review #16: a hand-edited min > max SOC must not produce NaN / negative
    flows (the config flow also rejects it)."""
    config = SystemConfig()
    bad = replace(config.battery, soc_min_percent=90.0, soc_max_percent=10.0)
    config = replace(config, battery=bad)
    flow = step_hour(config, 50.0, _slot(pv=1000.0, ac=0.0, dc=100.0), 99.0)
    assert flow.soc_end_percent == flow.soc_end_percent  # not NaN
    assert flow.grid_import_wh >= 0.0
    assert flow.grid_export_wh >= 0.0


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


# ---------------------------------------------------------------------------
# 48 V PSU direct-offset billing (docs/DC_TOPOLOGY.md §4). The PSU is a 48 V
# source: it covers concurrent bus load directly (no battery round-trip),
# charges only the remainder, and the grid pays for what is actually
# delivered (/eta), so a full battery or a closed gate bills ~0.
# ---------------------------------------------------------------------------


def test_psu48_offsets_bus_load_without_battery_roundtrip():
    """A concurrent 48 V bus load is covered by the PSU directly — it is not
    drained from and re-charged into the battery."""
    config = _dc_config(
        SupportParams(configured=True, dc48_power_w=100.0), dc_base_w=60.0
    )
    flow = _first_flow(config, soc=80.0, dc48=True)
    # 60 Wh bus load covered directly; the battery is not discharged for it.
    assert abs(flow.battery_discharge_wh) < _EPS
    # Remainder 40 Wh charges the battery (x eta_charge); delivered = 100.
    assert abs(flow.battery_charge_wh - 40.0 * config.battery.eta_charge) < 1e-3
    assert abs(flow.psu48_delivered_wh - 100.0) < 1e-3


def test_psu48_no_overbilling_when_battery_full():
    """Battery at the ceiling, no bus load: the PSU delivers nothing, so the
    grid is not billed (the old flat model billed the full rating)."""
    config = _dc_config(
        SupportParams(configured=True, dc48_power_w=60.0), dc_base_w=0.0
    )
    flow = _first_flow(config, soc=95.0, dc48=True)  # 95 % == soc_max
    assert abs(flow.psu48_delivered_wh) < _EPS
    assert abs(flow.grid_import_wh) < _EPS


def test_psu48_efficiency_and_power_cap():
    """Delivery is capped at V x I, and the grid pays delivered / eta."""
    config = _dc_config(
        SupportParams(
            configured=True,
            dc48_power_w=100.0,
            psu48_max_power_w=57.0,
            psu48_eta=0.9,
        ),
        dc_base_w=0.0,
    )
    flow = _first_flow(config, soc=30.0, dc48=True)
    # Capped at 57 W; all of it charges the battery (no bus load).
    assert abs(flow.psu48_delivered_wh - 57.0) < 1e-3
    assert abs(flow.grid_import_wh - 57.0 / 0.9) < 1e-2


# ---------------------------------------------------------------------------
# F-PREDRAIN F3: the pv_scale stress/optimism multiplier (docs/F-PREDRAIN.md
# §3.3). The planner re-runs the horizon pessimistically (alpha < 1) to protect
# the lower buffer and optimistically (beta > 1) to size the upper buffer.
# ---------------------------------------------------------------------------


def test_pv_scale_one_is_bit_identical():
    """The neutral default must reproduce the unscaled run exactly."""
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NOON, 50.0, [15.0, 15.0, 0.0])
    a = simulate(config, inputs, 40.0)
    b = simulate(config, inputs, 40.0, pv_scale=1.0)
    assert a.total_import_wh == b.total_import_wh
    assert a.total_export_wh == b.total_export_wh
    assert [f.soc_end_percent for f in a.flows] == [f.soc_end_percent for f in b.flows]


def test_pv_scale_below_one_reduces_pv():
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NOON, 50.0, [20.0, 0.0, 0.0])
    full = simulate(config, inputs, 20.0)
    stressed = simulate(config, inputs, 20.0, pv_scale=0.5)
    # Less PV -> less export, a lower end SOC, and import can only rise.
    assert stressed.total_export_wh < full.total_export_wh
    assert stressed.end_soc_percent <= full.end_soc_percent + 1e-9
    assert stressed.total_import_wh >= full.total_import_wh - 1e-9


def test_pv_scale_zero_removes_all_pv():
    config = SystemConfig()
    slot = _slot(pv=1000.0, ac=0.0, dc=0.0)
    # Battery full so the surplus must export rather than charge.
    with_pv = step_hour(config, 95.0, slot, 20.0)  # inverter on, PV exports
    without = step_hour(config, 95.0, slot, 20.0, pv_scale=0.0)
    assert with_pv.grid_export_wh > 0.0
    assert without.grid_export_wh < _EPS  # no PV left to export


def test_pv_scale_above_one_boosts_export():
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NOON, 80.0, [10.0, 0.0, 0.0])
    nominal = simulate(config, inputs, 20.0)
    optimistic = simulate(config, inputs, 20.0, pv_scale=1.2)
    assert optimistic.total_export_wh >= nominal.total_export_wh - 1e-9


def test_beta_scale_clamped_at_physical_peak():
    """FIX-8: an optimistic (beta > 1) PV scale must not conjure PV above the
    array's physical peak. A slot already at the peak-power cap stays there under
    scale 1.2 (peak * duration), not inflated 20 %."""
    config = SystemConfig()  # peak 3200 W
    peak = config.pv.peak_power_w
    # Battery full so all PV must export -> exported Wh mirrors the slot PV.
    at_cap = _slot(pv=peak, ac=0.0, dc=0.0)
    boosted = step_hour(config, 95.0, at_cap, 20.0, pv_scale=1.2)
    assert boosted.grid_export_wh <= peak + _EPS  # clamped, not 1.2 * peak

    # Below the cap, beta still boosts (scaled value stays under the cap).
    half = _slot(pv=peak / 2.0, ac=0.0, dc=0.0)
    boosted_half = step_hour(config, 95.0, half, 20.0, pv_scale=1.2)
    assert boosted_half.grid_export_wh > peak / 2.0
    assert boosted_half.grid_export_wh <= peak + _EPS


def test_beta_scale_clamp_respects_partial_slot_duration():
    """The FIX-8 clamp is power-based: a partial slot is capped at peak * duration."""
    config = SystemConfig()
    peak = config.pv.peak_power_w
    partial = _slot(pv=peak * 0.5, ac=0.0, dc=0.0, dur=0.5)  # already at the 0.5 h cap
    boosted = step_hour(config, 95.0, partial, 20.0, pv_scale=1.4)
    assert boosted.grid_export_wh <= peak * 0.5 + _EPS


def test_pv_scale_below_one_never_clamped_bit_identical():
    """Scales <= 1.0 keep the legacy path bit-identical (never clamped), even for
    a slot at the peak cap: scaling the peak slot equals running a pre-scaled slot
    unscaled (FIX-8 only touches the beta > 1 branch)."""
    config = SystemConfig()
    peak = config.pv.peak_power_w
    for scale in (0.5, 0.9):
        scaled = step_hour(
            config, 95.0, _slot(pv=peak, ac=0.0, dc=0.0), 20.0, pv_scale=scale
        )
        prescaled = step_hour(
            config, 95.0, _slot(pv=peak * scale, ac=0.0, dc=0.0), 20.0, pv_scale=1.0
        )
        assert scaled.grid_export_wh == prescaled.grid_export_wh
        assert scaled.soc_end_percent == prescaled.soc_end_percent


def test_pv_scale_per_slot_vector():
    """A SEQUENCE `pv_scale` applies a per-slot factor (F-PREDRAIN §3.3 v2): the
    windowed stress gate scales PV only inside the bet's recovery window and
    leaves the rest of the horizon at nominal PV."""
    config = SystemConfig()
    inputs = make_inputs(config, NOW_NOON, 50.0, [15.0, 15.0, 0.0])
    n = len(inputs.slots)

    # An all-ones vector is bit-identical to the scalar-1.0 (and default) run.
    scalar = simulate(config, inputs, 40.0)
    ones = simulate(config, inputs, 40.0, pv_scale=[1.0] * n)
    assert [f.soc_end_percent for f in ones.flows] == [
        f.soc_end_percent for f in scalar.flows
    ]
    assert ones.total_import_wh == scalar.total_import_wh
    assert ones.total_export_wh == scalar.total_export_wh

    # An all-alpha vector is bit-identical to the scalar-alpha run.
    scalar_half = simulate(config, inputs, 40.0, pv_scale=0.5)
    vec_half = simulate(config, inputs, 40.0, pv_scale=[0.5] * n)
    assert [f.soc_end_percent for f in vec_half.flows] == [
        f.soc_end_percent for f in scalar_half.flows
    ]

    # A vector that scales ONLY slot 0 leaves the other slots' PV untouched: the
    # per-slot flows match the all-ones run everywhere except where alpha applies.
    windowed = simulate(config, inputs, 40.0, pv_scale=[0.5] + [1.0] * (n - 1))
    assert windowed.flows[0].soc_end_percent < scalar.flows[0].soc_end_percent
    # Slot 0 sees exactly the alpha-scaled PV — same as a whole-horizon alpha run
    # would produce for slot 0 (the first slot has no earlier history).
    assert windowed.flows[0].soc_end_percent == scalar_half.flows[0].soc_end_percent
