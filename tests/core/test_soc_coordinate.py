"""A coordinate adjustment cannot create optional stored-energy entitlement."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from core.model import (
    HourSlot,
    PlanInputs,
    PsuDeliverySnapshot,
    PsuPowerObservation,
    ReserveParams,
    SocCoordinateSnapshot,
    SupportParams,
    SurplusLoad,
    SystemConfig,
)
from core.optimize import plan
from core.replay import decode, encode, recording, replay
from core.reserve import _same_recharge_inputs
from core.simulate import simulate
from core.voltage_gate import observed_psu_efficiency, present_delivery_w

NOW = datetime(2026, 2, 3, 12, tzinfo=UTC)
AT = NOW.timestamp()


def inputs(pv=0, dc=40, phase="reconciling", soc=90):
    return PlanInputs(
        NOW,
        soc,
        tuple(
            HourSlot(i, NOW + timedelta(hours=i), 1, 12 + i, pv, 100, dc)
            for i in range(3)
        ),
        soc_coordinate=SocCoordinateSnapshot(1, phase, 2, AT),
    )


def support_config(**kw):
    return SystemConfig(
        support=SupportParams(
            **(
                dict(
                    configured=True,
                    coordinated=True,
                    psu48_bus_voltage_v=49,
                    psu48_max_power_w=60,
                )
                | kw
            )
        ),
        reserve=ReserveParams(enabled=True),
    )


@pytest.mark.parametrize("phase", ["reconciling", "settling"])
def test_coordinate_hold_blocks_ac_without_cutting_essential_dc(phase):
    config = SystemConfig()
    held = simulate(config, inputs(phase=phase), 20)
    baseline = simulate(config, replace(inputs(), soc_coordinate=None), 20)
    assert baseline.flows[0].inverter_output_wh > 0
    assert all(
        f.inverter_output_wh == 0 and f.battery_discharge_wh > 0 for f in held.flows
    )
    assert all(f.unserved_dc_wh == 0 for f in held.flows)
    assert held.total_import_wh > baseline.total_import_wh


def test_coordinate_hold_preserves_protection_manual_and_direct_pv():
    config = support_config(dc24_forced_on=True)
    low = simulate(config, inputs(soc=5), 20)
    assert low.flows[0].support_dc24 and low.flows[0].support_dc48
    assert low.reserve_decision.reason == "manual_support"
    pv = simulate(config, inputs(pv=1000), 20)
    assert all(f.inverter_output_wh == 0 for f in pv.flows)
    assert all(f.battery_discharge_wh == 0 for f in pv.flows)
    assert pv.flows[0].support_dc24  # explicit manual owner survives PV


def test_reserve_hold_does_not_treat_partial_pv_as_battery_independent():
    config = support_config(dc24_available=True, dc48_available=False)
    partial = simulate(config, inputs(pv=110, dc=80), 20)
    # PV > AC alone cannot release the rail to 70 W of battery consumption.
    assert partial.flows[0].support_dc24
    assert partial.flows[0].psu24_delivered_wh == pytest.approx(80)
    assert partial.reserve_decision.inverter_limit_w == 0
    assert partial.reserve_decision.reason == "soc_coordinate_hold"
    independent = simulate(config, inputs(pv=400, dc=80), 20)
    assert not independent.flows[0].support_dc24
    assert (
        independent.flows[0].battery_discharge_wh
        <= independent.flows[0].battery_charge_wh
    )
    assert independent.flows[0].grid_import_wh == 0


def test_unknown_psu_delivery_keeps_native_dc_service_and_no_new_ac():
    config = support_config(
        dc24_available=False,
        dc48_available=True,
        adaptive_voltage_gate=True,
        native48_base_w=40,
        psu48_bus_voltage_v=None,
    )
    held = simulate(config, inputs(pv=110, dc=80), 20)
    assert held.flows[0].battery_discharge_wh > 0
    assert held.flows[0].psu48_delivered_wh == 0
    assert held.flows[0].unserved_dc_wh == 0
    assert held.flows[0].inverter_output_wh == 0


def test_plan_rejects_battery_load_but_allows_independent_direct_pv():
    load = SurplusLoad("neutral", "Consumer", 200, 0, 15, 15, False)
    config = SystemConfig(loads=(load,))
    held = plan(config, inputs(pv=0, dc=0))
    assert held.soc_coordinate == inputs().soc_coordinate
    assert held.load_plans[0].planned_energy_wh == 0
    solar = plan(config, inputs(pv=1500, dc=0))
    assert solar.load_plans[0].planned_energy_wh > 0
    assert all(f.battery_discharge_wh == 0 for f in solar.trajectory.flows)


def test_generation_prevents_old_permission_reviving_and_replay_is_exact():
    original = inputs(pv=300)
    settled = replace(
        original, soc_coordinate=SocCoordinateSnapshot(1, "tracking", 4, AT + 1000)
    )
    assert not original.soc_coordinate.same_coordinate(settled.soc_coordinate)
    assert original.soc_coordinate.same_coordinate(
        replace(original.soc_coordinate, captured_at=AT + 1)
    )
    assert not _same_recharge_inputs(original, settled)
    config = SystemConfig()
    result = plan(config, original)
    assert replay(recording(config, original, result)) == (result, True)
    assert decode(encode(original)) == original
    legacy = encode(original)
    legacy["fields"].pop("soc_coordinate")
    legacy["fields"].pop("psu_delivery")
    assert decode(legacy).soc_coordinate is None
    assert decode(legacy).psu_delivery == ()
    legacy_result = encode(result)
    legacy_result["fields"].pop("soc_coordinate")
    assert decode(legacy_result).soc_coordinate is None


@pytest.mark.parametrize(
    "kw",
    [
        {"epoch": -1},
        {"epoch": True},
        {"generation": -1},
        {"phase": "stable"},
        {"captured_at": float("nan")},
    ],
)
def test_coordinate_invalid_input_is_not_an_unknown_success(kw):
    with pytest.raises(ValueError):
        SocCoordinateSnapshot(**kw)


def test_frozen_coordinate_and_snapshot_input_types():
    with pytest.raises(FrozenInstanceError):
        SocCoordinateSnapshot().epoch = 3
    with pytest.raises(ValueError):
        replace(inputs(), soc_coordinate={})
    with pytest.raises(ValueError):
        replace(inputs(), psu_delivery=[])


def observation(role="dc_output", **kw):
    values = dict(
        source_id="neutral-meter",
        role=role,
        power_w=40 if role == "dc_output" else 50,
        published_at=AT,
        interval_start=AT - 10,
        interval_end=AT,
        source_generation=3,
        topology_fingerprint="topology",
    )
    return PsuPowerObservation(**(values | kw))


def delivery(**kw):
    values = dict(
        captured_at=AT,
        source_generation=3,
        topology_fingerprint="topology",
        confirmed_on=True,
        confirmed_on_since=AT - 20,
        dc_output=observation(),
        ac_input=observation("ac_input"),
        quality="measured",
    )
    return PsuDeliverySnapshot(**(values | kw))


def test_psu_present_evidence_is_source_bound_and_never_changes_horizon():
    snapshot = delivery()
    assert present_delivery_w(snapshot, AT + 30, 3, "topology") == 40
    assert observed_psu_efficiency(snapshot) == 0.8
    assert decode(encode(snapshot)) == snapshot
    assert (
        present_delivery_w(
            delivery(dc_output=observation(power_w=0)), AT, 3, "topology"
        )
        == 0
    )
    assert observed_psu_efficiency(delivery(dc_output=observation(power_w=0))) == 0
    original = inputs()
    measured = replace(original, psu_delivery=(snapshot,))
    assert simulate(support_config(), measured, 20) == simulate(
        support_config(), original, 20
    )
    assert not _same_recharge_inputs(original, measured)


@pytest.mark.parametrize(
    "change",
    [
        {"quality": "unknown"},
        {"confirmed_on": False},
        {"confirmed_on_since": None},
        {"confirmed_on_since": AT - 5},
        {"dc_output": None},
        {"dc_output": observation(source_generation=2)},
        {"dc_output": observation(topology_fingerprint="previous")},
        {"dc_output": observation(published_at=AT + 1)},
        {
            "dc_output": observation(
                published_at=AT - 31, interval_start=AT - 40, interval_end=AT - 31
            )
        },
    ],
)
def test_invalid_psu_evidence_is_unknown(change):
    assert present_delivery_w(delivery(**change), AT, 3, "topology") is None


@pytest.mark.parametrize(
    "at,generation,topology",
    [
        (AT + 31, 3, "topology"),
        (AT - 1, 3, "topology"),
        (AT, 4, "topology"),
        (AT, 3, "new"),
    ],
)
def test_off_on_or_topology_transition_does_not_reuse_timestamp_fresh_power(
    at, generation, topology
):
    assert present_delivery_w(delivery(), at, generation, topology) is None


@pytest.mark.parametrize(
    "ac",
    [
        None,
        observation("ac_input", source_generation=2),
        observation("ac_input", topology_fingerprint="old"),
        observation("ac_input", interval_start=AT - 30),
        observation(
            "ac_input",
            published_at=AT - 31,
            interval_start=AT - 40,
            interval_end=AT - 31,
        ),
        observation("ac_input", interval_start=AT - 20, interval_end=AT - 10),
        observation("ac_input", power_w=0),
        observation("ac_input", power_w=30),
    ],
)
def test_separate_nonoverlapping_or_incompatible_ac_channel_is_not_a_balance(ac):
    assert observed_psu_efficiency(delivery(ac_input=ac)) is None


@pytest.mark.parametrize(
    "kw",
    [
        {"power_w": -1},
        {"power_w": float("inf")},
        {"role": "counter_wh"},
        {"source_id": ""},
        {"interval_start": AT + 1},
        {"source_generation": -1},
    ],
)
def test_power_observation_validation(kw):
    with pytest.raises(ValueError):
        observation(**kw)


@pytest.mark.parametrize(
    "kw",
    [
        {"quality": "ready"},
        {"source_id": "other"},
        {"source_generation": -1},
        {"captured_at": -1},
        {"confirmed_on_since": AT + 1},
        {"dc_output": observation("ac_input")},
    ],
)
def test_psu_snapshot_validation(kw):
    with pytest.raises(ValueError):
        delivery(**kw)


def test_partial_direct_pv_cannot_borrow_native_dc_battery_budget_for_a_load():
    load = SurplusLoad("neutral", "Consumer", 200, 0.5, 15, 15, False)
    result = plan(SystemConfig(loads=(load,)), inputs(pv=300, dc=80))
    assert result.load_plans[0].planned_energy_wh == 0
    assert any(
        reason == "soc_coordinate_hold"
        for _, reason in result.load_plans[0].rejected_candidates
    )


def test_recovery_retry_cannot_spend_held_dc_budget():
    from core.allocation import _allocate_recovery_after_continuous_loads
    from core.model import LoadPlan, SurplusLoadState

    storage = SurplusLoad(
        "storage",
        "Storage",
        300,
        min_runtime_min=60,
        energy_limited=True,
        capacity_wh=1000,
        target_soc_percent=90,
        recovery_soc_percent=50,
    )
    config = SystemConfig(loads=(storage,))
    data = replace(
        inputs(pv=400, dc=80), load_states=(SurplusLoadState("storage", soc_percent=0),)
    )
    baseline = simulate(config, data, 20)
    original = LoadPlan("storage", (False,) * 3, 0, run_hours=(0,) * 3)
    plans, extra, trajectory = _allocate_recovery_after_continuous_loads(
        config,
        data,
        20,
        [original],
        (0,) * 3,
        baseline,
        {"storage": 300},
        repeat_final_rounding=False,
    )
    assert plans[0].planned_energy_wh == 0
    assert dict(plans[0].rejected_candidates)[0] == "soc_coordinate_hold"
    assert extra == (0,) * 3
    assert trajectory is baseline


def test_replay_exposes_export_cost_of_conservative_coordinate_hold():
    from core.series import build_slots

    config = SystemConfig(loads=(SurplusLoad("consumer", "Consumer", 400, 0.15, 30),))
    original = build_slots(config, datetime(2026, 2, 3, 21), 90, [0, 15])
    baseline = plan(config, original)
    held_inputs = replace(
        original, soc_coordinate=SocCoordinateSnapshot(1, "settling", 3)
    )
    held = plan(config, held_inputs)
    assert held.grid_export_kwh > baseline.grid_export_kwh
    assert held.grid_import_kwh > baseline.grid_import_kwh
    assert (
        held.load_plans[0].planned_energy_wh < baseline.load_plans[0].planned_energy_wh
    )
    assert replay(recording(config, held_inputs, held)) == (held, True)
    # Essential DC still draws energy; optional AC/pre-drain cannot spend SOC.
    assert all(f.inverter_output_wh == 0 for f in held.trajectory.flows)


@pytest.mark.parametrize("pv,allowed", [(400, False), (800, True)])
def test_appliance_hold_checks_its_covered_interval_before_later_recharge(pv, allowed):
    from core.model import Appliance

    config = SystemConfig(
        appliances=(Appliance("appliance", "Appliance", 300, 1, True),)
    )
    slots = tuple(
        HourSlot(i, NOW + timedelta(hours=i), 1, 12 + i, power, 100, 80)
        for i, power in enumerate((pv, 4000, *([0] * 9)))
    )
    data = PlanInputs(
        NOW, 90, slots, soc_coordinate=SocCoordinateSnapshot(1, "settling", 3)
    )
    result = plan(config, data)
    assert result.appliance_windows["appliance"] is allowed
    assert (
        "soc_coordinate_hold" in result.appliance_advisories["appliance"].reasons
    ) is not allowed


@pytest.mark.parametrize("role", ["dc_output", "ac_input"])
def test_fresh_publication_cannot_refresh_old_covered_electrical_interval(role):
    old = observation(role, interval_start=AT - 600, interval_end=AT - 500)
    snapshot = delivery(confirmed_on_since=AT - 1000, **{role: old})
    if role == "dc_output":
        assert present_delivery_w(snapshot, AT, 3, "topology") is None
    assert observed_psu_efficiency(snapshot) is None


def test_fixed_dispatch_hold_does_not_finance_candidate_with_a_new_psu_choice():
    from core.reserve import simulate_fixed_dispatch

    config = support_config(dc48_available=False)
    data = inputs(pv=400, dc=80)
    reference = simulate(config, data, 20)
    assert not reference.flows[0].support_dc24
    candidate = simulate_fixed_dispatch(config, data, (210,) * 3, reference)
    assert not candidate.flows[0].support_dc24
    assert (
        candidate.flows[0].battery_discharge_wh > candidate.flows[0].battery_charge_wh
    )
    # Ordinary planning can choose a different physical source for that demand.
    replanned = simulate(config, data, 20, extra_ac_wh=(210,) * 3)
    assert replanned.flows[0].support_dc24


@pytest.mark.parametrize("phase", ["reconciling", "settling", "tracking", None])
def test_full_planner_cannot_certify_new_dc_entitlements_during_coordinate_hold(phase):
    from core.model import (
        ConsumptionUncertaintyInterval,
        ConsumptionUncertaintySnapshot,
    )

    config = support_config(dc48_available=False)
    slots = tuple(
        HourSlot(i, NOW + timedelta(hours=i), 1, 12 + i, pv, 100, 80)
        for i, pv in enumerate((0, 0, 4000, 2000))
    )
    uncertainty = ConsumptionUncertaintySnapshot(
        (ConsumptionUncertaintyInterval(NOW, NOW + timedelta(hours=4), 0, 0),)
    )
    coordinate = SocCoordinateSnapshot(1, phase, 3) if phase else None
    data = PlanInputs(
        NOW, 80, slots, consumption_uncertainty=uncertainty, soc_coordinate=coordinate
    )
    result = plan(config, data)
    decision = result.trajectory.reserve_decision
    if phase in ("reconciling", "settling"):
        assert result.support_dc24_now
        assert result.trajectory.flows[0].battery_discharge_wh == 0
        assert decision.headroom_wh == 0
        assert decision.recharge is None or not decision.recharge.certificates
        assert decision.joint is None
        # The uncertainty snapshot cannot change source choices or create budget.
        neutral = plan(config, replace(data, consumption_uncertainty=None))
        assert result.trajectory == neutral.trajectory
    else:
        assert not result.support_dc24_now
        assert result.trajectory.flows[0].battery_discharge_wh > 0
        assert decision.recharge is not None
    assert replay(recording(config, data, result)) == (result, True)


@pytest.mark.parametrize("mode", ["no_alternative", "manual", "direct_pv"])
def test_certificate_hold_preserves_essential_manual_and_independent_pv_service(mode):
    from core.model import (
        ConsumptionUncertaintyInterval,
        ConsumptionUncertaintySnapshot,
    )

    config = support_config(
        dc48_available=False,
        dc24_available=mode != "no_alternative",
        dc24_forced_on=mode == "manual",
    )
    data = inputs(pv=1000 if mode == "direct_pv" else 0, dc=80)
    data = replace(
        data,
        consumption_uncertainty=ConsumptionUncertaintySnapshot(
            (ConsumptionUncertaintyInterval(NOW, NOW + timedelta(hours=3), 0, 0),)
        ),
    )
    result = plan(config, data)
    flow = result.trajectory.flows[0]
    assert flow.unserved_dc_wh == 0
    assert result.trajectory.reserve_decision.recharge is None
    if mode == "no_alternative":
        assert flow.battery_discharge_wh > 0
    elif mode == "manual":
        assert result.support_dc24_now
        assert flow.battery_discharge_wh == 0
    else:
        assert not result.support_dc24_now
        assert flow.battery_discharge_wh <= flow.battery_charge_wh
        assert flow.grid_import_wh == 0


def test_fixed_dispatch_does_not_republish_quarantined_reference_certificates():
    from core.model import RechargeCertificate, RechargeSummary
    from core.reserve import simulate_fixed_dispatch

    config = support_config(dc48_available=False)
    data = inputs(pv=1000)
    reference = simulate(config, data, 20)
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2), "dc24", 50
    )
    reference = replace(
        reference,
        reserve_decision=replace(
            reference.reserve_decision,
            recharge=RechargeSummary("released", certificates=(certificate,)),
        ),
    )
    candidate = simulate_fixed_dispatch(config, data, None, reference)
    assert candidate.reserve_decision.recharge is None
    assert candidate.reserve_decision.headroom_wh == 0
