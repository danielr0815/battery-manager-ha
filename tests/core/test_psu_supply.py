"""The published operating plan uses qualified PSU supply without actuator credit."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from core.model import (
    ApplianceRun,
    HourSlot,
    PlanInputs,
    Psu48SupplyEvidence,
    ReserveParams,
    SocCoordinateSnapshot,
    SupportParams,
    SystemConfig,
    VoltageGateBand,
)
from core.optimize import plan
from core.psu_supply import operating_supply
from core.replay import decode, encode, recording, replay
from core.reserve_energy import FLOW_EPSILON_WH
from core.series import build_slots
from core.simulate import simulate, step_hour

NOW = datetime(2026, 11, 20, 0, 42, 38, 419902, UTC)
AT = NOW.timestamp()


def plant(*, reserve=True, forced=True, **kwargs):
    return SystemConfig(
        reserve=ReserveParams(enabled=reserve),
        support=replace(
            SupportParams(
                configured=True,
                coordinated=True,
                adaptive_voltage_gate=True,
                dc24_active=True,
                dc48_active=True,
                dc24_forced_on=forced,
                dc48_forced_on=forced,
                native48_base_w=35,
                psu48_bus_voltage_v=49.49,
                psu48_live_until=AT + 300,
                psu48_max_power_w=49.56 * 1.15,
                psu48_eta=0.89,
            ),
            **kwargs,
        ),
    )


def evidence(**changes):
    return replace(
        Psu48SupplyEvidence(AT, 6 * 3600, AT + 5 * 3600, "night", 0.04275), **changes
    )


def make_inputs(hours=3, *, soc=68, first_seconds=3600, supply=None):
    start = NOW
    slots = []
    for index in range(hours):
        elapsed = first_seconds if index == 0 else 3600
        duration = elapsed / 3600
        slots.append(
            HourSlot(
                index, start, duration, start.hour, 0.0, 200 * duration, 60 * duration
            )
        )
        start += timedelta(seconds=elapsed)
    return PlanInputs(NOW, soc, tuple(slots), psu48_supply=supply)


@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize("edge_offset", [-0.001, 0.0, 0.001])
def test_psu_milliseconds_at_five_minute_edge(reserve, edge_offset):
    seconds = 300 + edge_offset
    config = plant(reserve=reserve)
    before = make_inputs(first_seconds=1041.580098)
    boundary = (NOW + timedelta(seconds=seconds)).timestamp()
    after = replace(before, psu48_supply=evidence(valid_until=boundary))
    conservative = plan(config, before)
    result = plan(config, after)
    operating = result.operating_trajectory
    assert operating is not None
    assert result.trajectory == conservative.trajectory
    assert result.load_plans == conservative.load_plans
    assert result.inverter_on == conservative.inverter_on
    assert result.psu48_supply_stop_reason == "supply_evidence_ends"
    assert result.psu48_supply_until == boundary
    assert result.psu48_supply_end_soc == pytest.approx(
        68 - 0.04275 * seconds / 3600 / 0.97 / 50
    )
    assert operating.flows[0].psu48_delivered_wh == pytest.approx(
        (35 - 0.04275) * seconds / 3600, abs=1e-10
    )
    assert operating.flows[0].psu48_battery_charge_wh == 0
    assert all(flow.psu48_delivered_wh == 0 for flow in operating.flows[1:])
    assert (
        operating.total_import_wh - result.trajectory.total_import_wh
        == pytest.approx((35 - 0.04275) * seconds / 3600 / 0.89)
    )


def test_multi_hour_dark_horizon_and_soc_coordinate_are_independent_of_delivery():
    config = plant(forced=False)
    inputs = make_inputs(hours=5, first_seconds=1041.580098, supply=evidence())
    base = plan(config, inputs)
    assert base.operating_trajectory is not None
    assert base.psu48_supply_stop_reason == "forecast_ends"
    assert base.psu48_supply_until == inputs.slots[-1].start.timestamp() + 3600
    for slot, guard, nominal in zip(
        inputs.slots,
        base.trajectory.flows,
        base.operating_trajectory.flows,
        strict=True,
    ):
        assert guard.battery_discharge_wh == pytest.approx(35 * slot.duration / 0.97)
        assert nominal.battery_discharge_wh == pytest.approx(
            0.04275 * slot.duration / 0.97
        )
        assert nominal.battery_charge_wh == 0
        assert nominal.psu48_delivered_wh == pytest.approx(
            (35 - 0.04275) * slot.duration
        )
        assert nominal.grid_import_wh - guard.grid_import_wh == pytest.approx(
            nominal.psu48_delivered_wh / 0.89
        )
        assert nominal.switching_schedule == guard.switching_schedule
    shifted = plan(config, replace(inputs, start_soc_percent=72))
    assert shifted.operating_trajectory is not None
    assert shifted.operating_trajectory.flows[0].psu48_delivered_wh == pytest.approx(
        base.operating_trajectory.flows[0].psu48_delivered_wh
    )
    assert not shifted.inverter_on
    held = plan(
        plant(),
        replace(inputs, soc_coordinate=SocCoordinateSnapshot(phase="reconciling")),
    )
    assert held.operating_trajectory is not None
    assert held.operating_trajectory.flows[0].psu48_delivered_wh == pytest.approx(
        base.operating_trajectory.flows[0].psu48_delivered_wh
    )
    assert not held.inverter_on
    assert simulate(
        config, inputs, 100, pv_scale=(0.8,) * len(inputs.slots)
    ) == simulate(
        config,
        replace(inputs, psu48_supply=None),
        100,
        pv_scale=(0.8,) * len(inputs.slots),
    )


def test_evidence_cannot_resume_after_relay_cycle():
    config = plant(
        forced=False,
        psu48_gate_bands=(
            VoltageGateBand(0.25, 1, 69.5, 69.5, 69.8, valid_until=AT + 3600 * 4),
        ),
    )
    result = plan(config, make_inputs(soc=70, supply=evidence()))
    assert result.operating_trajectory is not None
    assert result.psu48_supply_until == AT + 300
    assert result.psu48_supply_stop_reason == "topology_changed"
    assert result.trajectory.flows[1].support_dc48
    assert result.operating_trajectory.flows[1].psu48_delivered_wh == 0


def test_direct_evidence_is_capped_at_native_load_and_exact_at_battery_floor():
    seconds = 299.999
    boundary = NOW + timedelta(seconds=seconds)
    result = plan(
        plant(),
        make_inputs(
            soc=5,
            supply=evidence(
                kind="direct", power_w=45, valid_until=boundary.timestamp()
            ),
        ),
    )
    assert result.operating_trajectory is not None
    flow = result.operating_trajectory.flows[0]
    assert flow.unserved_dc_wh == pytest.approx(35 * (3600 - seconds) / 3600)
    assert flow.dc_deficit_intervals[0].start == boundary
    assert flow.psu48_delivered_wh == pytest.approx(35 * seconds / 3600)
    assert flow.battery_charge_wh == flow.battery_discharge_wh == 0


@pytest.mark.parametrize("kind", ["night", "grid_import"])
def test_indirect_positive_residual_needs_stored_energy_at_plan_start(kind):
    supply = evidence(kind=kind, power_w=10, grid_import_w=200, grid_setpoint_w=0)
    result = plan(plant(), make_inputs(soc=5, supply=supply))
    assert result.operating_trajectory is None
    assert result.psu48_supply_until is None
    assert result.psu48_supply_stop_reason == "net_storage_limit"


def test_indirect_prefix_ends_at_floor_within_partial_slot_and_cannot_restart():
    first_seconds = 1980
    floor_seconds = 5 * 0.97 / 10 * 3600
    inputs = make_inputs(
        soc=5.1, first_seconds=first_seconds, supply=evidence(power_w=10)
    )
    first, second, third = inputs.slots
    result = plan(
        plant(), replace(inputs, slots=(first, replace(second, pv_wh=650), third))
    )
    assert result.operating_trajectory is not None
    boundary = NOW + timedelta(seconds=floor_seconds)
    assert result.psu48_supply_until == boundary.timestamp()
    assert result.psu48_supply_end_soc == pytest.approx(5)
    assert result.psu48_supply_stop_reason == "net_storage_limit"
    floor, recharge, after = result.operating_trajectory.flows
    assert floor.psu48_delivered_wh == pytest.approx(25 * floor_seconds / 3600)
    assert floor.battery_discharge_wh == pytest.approx(5)
    assert floor.unserved_dc_wh == pytest.approx(
        35 * (first_seconds - floor_seconds) / 3600
    )
    first_unserved = next(
        interval.start
        for interval in floor.dc_deficit_intervals
        if interval.unserved_dc_wh > FLOW_EPSILON_WH
    )
    assert first_unserved == boundary
    assert recharge.soc_end_percent > 5
    assert after.psu48_delivered_wh == 0


@pytest.mark.parametrize("kind,power,delivery", [("night", 0, 35), ("direct", 25, 25)])
def test_independent_dc_coverage_can_continue_at_floor(kind, power, delivery):
    result = plan(
        plant(), make_inputs(hours=1, soc=5, supply=evidence(kind=kind, power_w=power))
    )
    assert result.operating_trajectory is not None
    assert result.psu48_supply_until == AT + 3600
    assert result.psu48_supply_stop_reason == "forecast_ends"
    (flow,) = result.operating_trajectory.flows
    assert flow.psu48_delivered_wh == pytest.approx(delivery)
    assert flow.unserved_dc_wh == pytest.approx(35 - delivery)
    assert flow.battery_discharge_wh == flow.battery_charge_wh == 0


def test_grid_import_uses_upper_pv_and_is_bounded_by_observation_capture():
    supply = evidence(
        kind="grid_import", grid_import_w=230, grid_setpoint_w=40, captured_at=AT - 10
    )
    inputs = make_inputs(first_seconds=1800, supply=supply)
    first, second, *later = inputs.slots
    first = replace(first, pv_wh=20, pv_p90_wh=45)
    second = replace(second, pv_wh=20, pv_p90_wh=115)
    qualified = plan(plant(), replace(inputs, slots=(first, second, *later)))
    assert qualified.operating_trajectory is not None
    assert qualified.psu48_supply_until == AT + 1800
    assert qualified.psu48_supply_stop_reason == "net_grid_plan_margin_not_met"
    middle_clear = plan(plant(), inputs)
    assert middle_clear.operating_trajectory is not None
    assert middle_clear.psu48_supply_until == AT + 3590
    assert middle_clear.psu48_supply_stop_reason == "supply_evidence_ends"


@pytest.mark.parametrize("base_w", [0, 75])
def test_grid_import_forecast_margin_excludes_short_running_appliance(base_w):
    now = NOW.replace(hour=23, minute=0, second=0, microsecond=0)
    config = plant()
    inputs = build_slots(
        config,
        now,
        68,
        [0],
        ac_load_w=(base_w,),
        dc_load_w=(60,),
        appliance_runs=(ApplianceRun("washer", 100, 0.1),),
    )
    assert inputs.slots[0].ac_wh == base_w + 100
    supply = evidence(
        kind="grid_import",
        captured_at=now.timestamp(),
        valid_until=now.timestamp() + 3600,
        grid_import_w=200,
        grid_setpoint_w=0,
    )
    result = plan(config, replace(inputs, psu48_supply=supply))
    if not base_w:
        assert result.operating_trajectory is None
        assert result.psu48_supply_stop_reason == "net_grid_plan_margin_not_met"
    else:
        assert result.operating_trajectory is not None
        assert result.psu48_supply_until == now.timestamp() + 3600
        assert result.operating_trajectory.total_import_wh == pytest.approx(
            base_w + 100 + 25 + (35 - supply.power_w) / 0.89
        )


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"captured_at": AT - 31}, "supply_evidence_stale"),
        ({"captured_at": AT + 0.1}, "supply_evidence_stale"),
        ({"observed_seconds": 1799}, "supply_evidence_short"),
        ({"valid_until": AT}, "supply_evidence_ends"),
        ({"power_w": 35}, "no_positive_delivery"),
        ({"power_w": 36}, "supply_power_implausible"),
        ({"kind": "direct", "power_w": 70}, "supply_power_implausible"),
        ({"kind": "grid_import"}, "net_grid_plan_margin_not_met"),
        (
            {"kind": "grid_import", "grid_import_w": 50, "grid_setpoint_w": 0},
            "net_grid_plan_margin_not_met",
        ),
    ],
)
def test_evidence_ineligible_at_plan_start(updates, reason):
    result = plan(plant(), make_inputs(supply=evidence(**updates)))
    assert result.operating_trajectory is None
    assert result.psu48_supply_until is None
    assert result.psu48_supply_stop_reason == reason


def test_dwell_load_and_source_availability_prevent_start():
    inputs = make_inputs(supply=evidence())
    result = plan(plant(), replace(inputs, recharge_required_ac_wh=(15, 0, 0)))
    assert result.operating_trajectory is None
    assert result.psu48_supply_stop_reason == "additional_load"
    result = plan(plant(dc48_active=False), inputs)
    assert result.psu48_supply_stop_reason == "no_matching_plan_window"
    result = plan(plant(psu24_max_power_w=20), inputs)
    assert result.psu48_supply_stop_reason == "topology_changed"


def test_night_credit_stops_at_native_load_or_solar_change_and_settles_later_clip():
    inputs = make_inputs(soc=94, supply=evidence())
    first, second, third = inputs.slots
    config = plant()
    dropped = plan(
        config, replace(inputs, slots=(first, replace(second, dc_wh=30), third))
    )
    assert dropped.psu48_supply_stop_reason == "native_load_changed"
    sun = plan(
        config,
        replace(
            inputs, slots=(first, replace(second, pv_wh=700, pv_p90_wh=800), third)
        ),
    )
    assert sun.operating_trajectory is not None
    assert sun.psu48_supply_stop_reason == "pv_window"
    assert sun.psu48_supply_until == second.start.timestamp()
    assert sun.operating_trajectory.flows[2].psu48_delivered_wh == 0
    assert sun.operating_trajectory.end_soc_percent == pytest.approx(
        sun.trajectory.end_soc_percent
    )
    assert (
        sun.operating_trajectory.total_export_wh - sun.trajectory.total_export_wh
        == pytest.approx(
            (35 - 0.04275) / 0.97 / config.battery.eta_charge / config.charger.eta
        )
    )


def test_legacy_defaults_and_evidence_roundtrip():
    config, inputs = plant(), make_inputs(supply=evidence())
    encoded = recording(config, inputs, plan(config, inputs))
    recomputed, matches = replay(encoded)
    assert recomputed.operating_trajectory is not None
    assert matches
    payload = encode(inputs)
    del payload["fields"]["psu48_supply"]
    assert decode(payload).psu48_supply is None


@pytest.mark.parametrize(
    "change",
    ["inverter_limit", "inverter_floor", "additional_load", "gap", "pv_charge"],
)
def test_fixed_dispatch_after_exact_fragment_keeps_commanded_power(change):
    config = plant()
    floor_limited = change == "inverter_floor"
    inputs = make_inputs(hours=1, soc=20.1 if floor_limited else 68, supply=evidence())
    (slot,) = inputs.slots
    # A dispatch split outside the normal five-minute control grid retains
    # the actual requested power and bus states on each side of its edge.
    hours = 14 / 60
    edge = NOW + timedelta(minutes=14)
    first = replace(
        slot, duration=hours, ac_wh=slot.ac_wh * hours, dc_wh=slot.dc_wh * hours
    )
    before = step_hour(
        config,
        inputs.start_soc_percent,
        first,
        100,
        dc24_from_grid=True,
        dc48_support=True,
    )
    tail = replace(
        slot,
        start=edge,
        duration=1 - hours,
        ac_wh=slot.ac_wh * (1 - hours),
        dc_wh=slot.dc_wh * (1 - hours),
    )
    if change == "gap":
        tail = replace(tail, start=edge + timedelta(milliseconds=1))
    if change == "pv_charge":
        tail = replace(tail, pv_wh=400)
    on = change in ("inverter_limit", "inverter_floor")
    limit = 20.0 if on else 0.0
    after = step_hour(
        config,
        before.soc_end_percent,
        tail,
        0 if on else 100,
        extra_ac_wh=3.0 if change in ("inverter_limit", "additional_load") else 0.0,
        dc24_from_grid=not on,
        dc48_support=not on,
        feedin_wh=7.0 if change == "pv_charge" else 0.0,
        inverter_limit_w=limit,
    )
    plain = plan(config, replace(inputs, psu48_supply=None))
    trajectory = replace(
        plain.trajectory, _dispatch=(((first, before, 0.0), (tail, after, limit)),)
    )
    result = operating_supply(config, inputs, replace(plain, trajectory=trajectory))
    assert result.operating_trajectory is not None
    assert not result.trajectory._dispatch
    assert result.psu48_supply_until == edge.timestamp()
    assert (
        result.psu48_supply_stop_reason
        == {
            "inverter_limit": "topology_changed",
            "inverter_floor": "topology_changed",
            "additional_load": "additional_load",
            "gap": "no_matching_plan_window",
            "pv_charge": "pv_window",
        }[change]
    )
    (flow,) = result.operating_trajectory.flows
    assert flow.inverter_output_wh == pytest.approx(after.inverter_output_wh)
    if floor_limited:
        assert result.psu48_supply_end_soc > config.control.inverter_min_soc_percent
        assert flow.inverter_output_wh == 0
    elif on:
        assert flow.inverter_output_wh == pytest.approx(20 * (1 - hours))
    assert flow.extra_ac_wh == after.extra_ac_wh
    assert flow.feedin_wh == after.feedin_wh


@pytest.mark.parametrize(
    "change", [{"kind": "unknown"}, {"power_w": -1}, {"valid_until": AT - 1}]
)
def test_invalid_evidence_contract(change):
    with pytest.raises(ValueError):
        evidence(**change)
    with pytest.raises(ValueError):
        make_inputs(supply=True)
