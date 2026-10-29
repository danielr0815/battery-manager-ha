"""Additional solar recharge contracts, separate from legacy short windows."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from core.joint_dispatch import MAX_JOINT_VALIDATIONS, ValidationBudget
from core.model import (
    BatteryParams,
    ConsumptionUncertaintyInterval,
    ConsumptionUncertaintySnapshot,
    ConverterParams,
    HourFlows,
    HourSlot,
    PlanInputs,
    RechargeCertificate,
    ReserveParams,
    SupportParams,
    SystemConfig,
    Trajectory,
)
from core.recharge import (
    anchor_failure,
    certify,
    fixed_anchors,
    preserves_certificates,
    step_end,
    uncertainty_wh,
)
from core.reserve_sources import SourceStep

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def configuration():
    return SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
    )


def flow(
    soc=80,
    *,
    charge=0,
    discharge=0,
    imported=0,
    exported=0,
    source24=False,
    source48=False,
    limit=0,
    psu_charge=0,
    dcdc=0,
):
    return HourFlows(
        soc,
        soc,
        imported,
        exported,
        charge,
        discharge,
        bool(limit),
        0,
        0,
        source24,
        source48,
        psu48_battery_charge_wh=psu_charge,
        inverter_limit_w=limit,
        dcdc_input_wh=dcdc,
        dc_deficit_intervals=(),
    )


def step(index, value, *, pv=0, ac=0):
    return SourceStep(
        HourSlot(index, NOW + timedelta(hours=index / 2), 0.5, index // 2, pv, ac, 20),
        value,
        (),
        5,
    )


def trajectory(steps):
    return Trajectory(
        tuple(s.flow for s in steps),
        sum(s.flow.grid_import_wh for s in steps),
        sum(s.flow.grid_export_wh for s in steps),
        steps[-1].flow.soc_end_percent,
    )


def coverage(hours=48, ac=0, dc=0):
    return ConsumptionUncertaintySnapshot(
        (ConsumptionUncertaintyInterval(NOW, NOW + timedelta(hours=hours), ac, dc),)
    )


def test_anchors_use_last_solar_step_of_local_day_and_exclude_psu_charge():
    steps = (
        step(0, flow(charge=20)),
        step(1, flow(charge=30)),
        step(2, flow(charge=40, psu_charge=40)),
        step(48, flow(charge=10)),
    )
    assert fixed_anchors(steps) == (1, 3)
    assert step_end(steps[1]) == NOW + timedelta(hours=1)


def test_interval_uncertainty_is_immutable_loss_adjusted_and_requires_both_coverage():
    config = configuration()
    config = replace(
        config,
        inverter=replace(config.inverter, eta=0.5),
        support=replace(config.support, dcdc_eta=0.5),
    )
    snapshot = coverage(hours=2, ac=100, dc=50)
    assert uncertainty_wh(snapshot, NOW, NOW + timedelta(hours=1), config) == 150
    assert uncertainty_wh(snapshot, NOW, NOW + timedelta(hours=3), config) is None
    with pytest.raises(FrozenInstanceError):
        snapshot.intervals = ()
    with pytest.raises(ValueError, match="immutable"):
        ConsumptionUncertaintySnapshot([])
    with pytest.raises(ValueError, match="nonnegative"):
        ConsumptionUncertaintyInterval(NOW, NOW + timedelta(hours=1), -1, 0)


def test_small_first_recharge_cannot_replace_the_fixed_later_anchor():
    old = (
        step(0, flow(imported=10, source24=True)),
        step(1, flow(charge=5)),
        step(2, flow(charge=50)),
    )
    new = (
        step(0, flow(79, discharge=10, dcdc=10)),
        step(1, flow(79.5, charge=5)),
        step(2, flow(80, charge=50)),
    )
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(minutes=30), step_end(old[2]), "dc24", 10, ((NOW, 10),)
    )
    assert fixed_anchors(old) == (2,)
    assert (
        anchor_failure(
            configuration(),
            trajectory(old),
            trajectory(new),
            old,
            new,
            0,
            2,
            coverage(),
            (certificate,),
        )
        is None
    )
    short = replace(new[1], flow=replace(new[1].flow, soc_end_percent=75))
    assert (
        anchor_failure(
            configuration(),
            trajectory(old),
            trajectory(new),
            old,
            (new[0], short, new[2]),
            0,
            1,
            coverage(),
            (certificate,),
        )
        == "anchor_energy"
    )


def test_anchor_and_horizon_end_energy_import_export_and_psu_cannot_regress():
    old = (step(0, flow(imported=10)), step(1, flow(charge=50)))
    base = trajectory(old)
    for field, value, reason in (
        ("soc_end_percent", 79, "anchor_energy"),
        ("grid_import_wh", 11, "extra_import"),
        ("grid_export_wh", 1, "extra_export"),
        ("psu48_battery_charge_wh", 1, "extra_psu_charge"),
    ):
        new = (old[0], replace(old[1], flow=replace(old[1].flow, **{field: value})))
        assert (
            anchor_failure(
                configuration(), base, trajectory(new), old, new, 0, 1, coverage()
            )
            == reason
        )


def test_missing_uncertainty_at_block_start_prevents_release():
    steps = (step(0, flow()), step(1, flow(charge=20)))
    snapshot = ConsumptionUncertaintySnapshot(
        (
            ConsumptionUncertaintyInterval(
                NOW + timedelta(minutes=30), NOW + timedelta(hours=1), 0, 0
            ),
        )
    )
    result = trajectory(steps)
    assert (
        anchor_failure(configuration(), result, result, steps, steps, 0, 1, snapshot)
        == "consumption_coverage"
    )


def test_dc_certificate_cannot_be_spent_on_ac_or_another_psu():
    steps = (step(0, flow(dcdc=20)),)
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(minutes=30), NOW + timedelta(hours=6), "dc24", 20
    )
    assert preserves_certificates(steps, (certificate,))
    assert preserves_certificates((step(0, flow(limit=1000, dcdc=20)),), (certificate,))
    for value in (flow(source24=True), flow(source48=True)):
        assert not preserves_certificates((step(0, value),), (certificate,))


def test_shared_exhausted_budget_cannot_grant_unsearched_ac():
    steps = (step(0, flow(imported=10, source24=True)), step(1, flow(charge=10)))
    baseline = trajectory(steps)
    budget = ValidationBudget(MAX_JOINT_VALIDATIONS)

    def forbidden_evaluation(proposal, pessimistic):
        pytest.fail("No complete simulation beyond the shared budget")

    actual, actual_steps, summary = certify(
        configuration(),
        baseline,
        steps,
        baseline,
        steps,
        coverage(),
        forbidden_evaluation,
        budget,
        True,
    )
    assert actual is baseline
    assert actual_steps is steps
    assert summary.status == "search_limit"
    assert summary.certificates == ()
    assert summary.validations == MAX_JOINT_VALIDATIONS


def test_nominal_and_stress_must_pass_the_same_earliest_reference_anchor():
    steps = (
        step(0, flow(imported=10, source24=True)),
        step(1, flow(charge=20)),
        step(48, flow(charge=30)),
    )
    baseline = trajectory(steps)
    budget = ValidationBudget(2)
    calls = []

    def evaluate(proposal, pessimistic):
        budget.record()
        calls.append(pessimistic)
        converted = (
            replace(steps[0], flow=flow(79, discharge=10, dcdc=10)),
            replace(steps[1], flow=flow(79.5 if pessimistic else 80, charge=20)),
            steps[2],
        )
        return trajectory(converted), converted

    actual, actual_steps, summary = certify(
        configuration(),
        baseline,
        steps,
        baseline,
        steps,
        coverage(),
        evaluate,
        budget,
        True,
    )
    assert actual.total_import_wh == 0
    assert actual_steps[0].flow.dcdc_input_wh == 10
    assert calls == [False, True]
    assert len(summary.certificates) == 1
    certificate = summary.certificates[0]
    assert certificate.kind == "dc24"
    assert certificate.anchor == step_end(steps[2])
    assert summary.validations == 4


def test_first_step_consumption_error_is_not_discarded_after_its_p50_step():
    old = (step(0, flow(20, imported=10, source24=True)), step(1, flow(20, charge=20)))
    new = (step(0, flow(10, discharge=100, dcdc=100)), step(1, flow(20, charge=20)))
    new = (replace(new[0], flow=replace(new[0].flow, soc_start_percent=20)), new[1])
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(minutes=30), step_end(old[1]), "dc24", 100, ((NOW, 100),)
    )
    first_slot_error = ConsumptionUncertaintySnapshot(
        (
            ConsumptionUncertaintyInterval(NOW, NOW + timedelta(minutes=30), 100, 0),
            ConsumptionUncertaintyInterval(
                NOW + timedelta(minutes=30), NOW + timedelta(hours=1), 0, 0
            ),
        )
    )
    assert (
        anchor_failure(
            configuration(),
            trajectory(old),
            trajectory(new),
            old,
            new,
            0,
            1,
            coverage(),
            (certificate,),
        )
        is None
    )
    assert (
        anchor_failure(
            configuration(),
            trajectory(old),
            trajectory(new),
            old,
            new,
            0,
            1,
            first_slot_error,
            (certificate,),
        )
        == "consumption_reserve"
    )


def test_new_later_anchor_cannot_refinance_an_existing_earlier_promise():
    steps = (
        step(0, flow(imported=10, source24=True)),
        step(1, flow(imported=10, source24=True)),
        step(2, flow(charge=20)),
        step(48, flow(charge=30)),
    )
    baseline = trajectory(steps)
    budget = ValidationBudget(2)

    def evaluate(proposal, pessimistic):
        budget.record()
        both = not proposal[0].support_dc24 and not proposal[1].support_dc24
        converted = tuple(
            replace(
                item,
                flow=replace(
                    item.flow,
                    support_dc24=proposal[i].support_dc24,
                    dcdc_input_wh=10 if i < 2 and not proposal[i].support_dc24 else 0,
                    battery_discharge_wh=10
                    if i < 2 and not proposal[i].support_dc24
                    else 0,
                    grid_import_wh=10 if i < 2 and proposal[i].support_dc24 else 0,
                    soc_end_percent=79 if both and i == 2 else 80,
                ),
            )
            for i, item in enumerate(steps)
        )
        return trajectory(converted), converted

    actual, actual_steps, summary = certify(
        configuration(),
        baseline,
        steps,
        baseline,
        steps,
        coverage(),
        evaluate,
        budget,
        True,
    )
    assert len(summary.certificates) == 1
    assert summary.certificates[0].anchor == step_end(steps[2])
    assert actual.total_import_wh == 10
    assert actual_steps[1].flow.support_dc24
    assert "existing_certificate_anchor_energy" in summary.rejection_reasons


def public_forecast(values, *, uncertainty=True):
    slots = tuple(
        HourSlot(i, NOW + timedelta(hours=i), 1, i % 24, pv, ac, dc)
        for i, (pv, ac, dc) in enumerate(values)
    )
    return PlanInputs(
        NOW,
        80,
        slots,
        consumption_uncertainty=coverage(hours=len(values)) if uncertainty else None,
    )


def public_plant():
    return replace(
        configuration(),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
    )


def test_public_simulate_missing_consumption_coverage_keeps_legacy_ac_supply():
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 600, 0), (0, 100, 0), (300, 0, 0)])
    legacy = simulate(config, replace(inputs, consumption_uncertainty=None), 20)
    actual = simulate(
        config,
        replace(inputs, consumption_uncertainty=ConsumptionUncertaintySnapshot(())),
        20,
    )
    assert sum(flow.inverter_output_wh for flow in legacy.flows) > 0
    assert tuple(flow.inverter_output_wh for flow in actual.flows) == pytest.approx(
        tuple(flow.inverter_output_wh for flow in legacy.flows)
    )
    assert actual.total_import_wh == pytest.approx(legacy.total_import_wh)
    assert actual.end_soc_percent == pytest.approx(legacy.end_soc_percent)
    assert actual.reserve_decision.recharge.certificates == ()
    assert "consumption_coverage" in actual.reserve_decision.recharge.rejection_reasons


def test_public_plan_snapshot_preserves_legacy_reference_and_has_one_bounded_summary():
    from core.optimize import plan

    config = public_plant()
    inputs = public_forecast([(0, 600, 0), (0, 100, 0), (300, 0, 0)])
    actual = plan(
        config,
        replace(inputs, consumption_uncertainty=ConsumptionUncertaintySnapshot(())),
    )
    assert sum(flow.inverter_output_wh for flow in actual.trajectory.flows) > 0
    assert actual.trajectory.reserve_decision.recharge.certificates == ()
    assert (
        actual.trajectory.reserve_decision.recharge.validations <= MAX_JOINT_VALIDATIONS
    )
    assert (
        actual.trajectory.reserve_decision.joint.joint_validations
        <= MAX_JOINT_VALIDATIONS
    )


def test_horizon_and_exogenous_clones_do_not_mint_new_long_window_credit():
    from core.reserve import reserve_planning_scope, source_schedule_mode
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 100), (10, 0, 0), (1000, 0, 0)])
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        original = simulate(config, inputs, 20)
        short = simulate(config, replace(inputs, slots=inputs.slots[:2]), 20)
        changed = simulate(
            config,
            replace(
                inputs, slots=(replace(inputs.slots[0], ac_wh=200), *inputs.slots[1:])
            ),
            20,
        )
        repeated = simulate(config, replace(inputs), 20)
    assert len(short.flows) == 2
    assert short.reserve_decision.recharge is None
    assert changed.reserve_decision.recharge is None
    assert repeated.reserve_decision.recharge == original.reserve_decision.recharge
    assert repeated.total_import_wh == pytest.approx(original.total_import_wh)
    assert repeated.reserve_decision.recharge.validations <= MAX_JOINT_VALIDATIONS


@pytest.mark.parametrize("p10, accepted", [(325, True), (300, False)])
def test_public_house_recharge_uses_small_binary_ac_remainder_only_when_stress_repays(
    p10, accepted
):
    from core.optimize import plan
    from core.simulate import simulate

    config = public_plant()
    config = replace(config, control=replace(config.control, soc_buffer_percent=0))
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    inputs = replace(
        inputs,
        slots=tuple(
            replace(slot, pv_p10_wh=p10 if slot.pv_wh else 0, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
    )
    legacy = simulate(config, replace(inputs, consumption_uncertainty=None), 20)
    for actual in (simulate(config, inputs, 20), plan(config, inputs).trajectory):
        summary = actual.reserve_decision.recharge
        assert bool(summary.certificates) is accepted
        assert actual.end_soc_percent == pytest.approx(legacy.end_soc_percent)
        assert actual.total_import_wh <= legacy.total_import_wh + 1e-6
        assert actual.total_export_wh <= legacy.total_export_wh + 1e-6
        assert actual.flows[1].inverter_output_wh == pytest.approx(150)
        assert summary.validations <= MAX_JOINT_VALIDATIONS
        if accepted:
            assert actual.flows[0].inverter_output_wh == pytest.approx(25)
            assert actual.total_import_wh == pytest.approx(525)
            assert actual.total_export_wh == pytest.approx(0, abs=1e-6)
            assert all(
                cert.kind == "ac" and cert.anchor == NOW + timedelta(hours=3)
                for cert in summary.certificates
            )
        else:
            assert actual.flows[0].inverter_output_wh == 0
            assert actual.total_import_wh == pytest.approx(550)
            assert "anchor_energy" in summary.rejection_reasons


def test_a_named_recharge_debit_does_not_unlock_an_unrelated_short_window():
    from core.recharge import certified_windows_failure

    before = (
        step(0, flow()),
        step(1, flow(charge=20)),
        step(2, flow()),
        step(3, flow(charge=20)),
    )
    after = (
        step(0, flow(79, discharge=10)),
        step(1, flow(79.5, charge=20)),
        step(2, flow(79, discharge=5)),
        step(3, flow(80, charge=20)),
    )
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(minutes=30), step_end(before[3]), "dc24", 10, ((NOW, 10),)
    )
    assert (
        certified_windows_failure(configuration(), before, after, (certificate,))
        == "uncertified_window_draw"
    )
    approved_only = (after[0], after[1], before[2], before[3])
    assert (
        certified_windows_failure(
            configuration(), before, approved_only, (certificate,)
        )
        is None
    )


def test_a_repaid_certificate_cannot_keep_a_short_window_energy_exception():
    from core.recharge import certified_windows_failure

    before = (
        step(0, flow()),
        step(1, flow(charge=20)),
        step(2, flow()),
        step(3, flow(charge=20)),
    )
    after = (before[0], before[1], before[2], step(3, flow(79, charge=20)))
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(minutes=30), step_end(before[1]), "dc24", 10, ((NOW, 10),)
    )
    assert (
        certified_windows_failure(configuration(), before, after, (certificate,))
        == "uncertified_window_energy"
    )


def test_public_snapshot_rejects_borrowing_from_a_changed_physical_battery():
    from core.reserve import reserve_planning_scope, source_schedule_mode
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    inputs = replace(
        inputs,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
    )
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        changed = simulate(
            replace(config, battery=replace(config.battery, capacity_wh=2000)),
            inputs,
            20,
        )
    assert reference.reserve_decision.recharge.certificates
    assert changed.reserve_decision.recharge is None
    assert changed.total_import_wh > reference.total_import_wh


def test_public_optional_consumption_cannot_spend_an_already_booked_recharge():
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    inputs = replace(
        inputs,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
    )
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        assert reference.reserve_decision.recharge.certificates
        unchanged = simulate_fixed_dispatch(config, inputs, (0, 0, 0), reference)
        assert recharge_preserved(config, inputs, reference, unchanged)
        optional = simulate_fixed_dispatch(config, inputs, (0, 0, 10), reference)
        assert optional.end_soc_percent < reference.end_soc_percent
        assert not recharge_preserved(config, inputs, reference, optional)
        shortened = replace(inputs, slots=inputs.slots[:2])
        assert not recharge_preserved(config, shortened, reference, unchanged)
        other_battery = replace(
            config, battery=replace(config.battery, capacity_wh=2000)
        )
        assert not recharge_preserved(other_battery, inputs, reference, unchanged)
    # A certificate without its owning full-horizon proof context cannot fund a new load.
    assert not recharge_preserved(config, inputs, reference, unchanged)


def test_consumption_coverage_gap_between_known_intervals_cannot_be_zero_filled():
    snapshot = ConsumptionUncertaintySnapshot(
        (
            ConsumptionUncertaintyInterval(NOW, NOW + timedelta(minutes=15), 0, 0),
            ConsumptionUncertaintyInterval(
                NOW + timedelta(minutes=30), NOW + timedelta(hours=1), 0, 0
            ),
        )
    )
    assert (
        uncertainty_wh(snapshot, NOW, NOW + timedelta(hours=1), configuration()) is None
    )


def test_anchor_repayment_does_not_excuse_a_later_horizon_energy_loss():
    before = (step(0, flow()), step(1, flow(charge=20)), step(2, flow()))
    after = (*before[:2], step(2, flow(79)))
    assert (
        anchor_failure(
            configuration(),
            trajectory(before),
            trajectory(after),
            before,
            after,
            0,
            1,
            coverage(),
        )
        == "end_energy"
    )


def test_ac_certificate_requires_its_booked_inverter_permission():
    certificate = RechargeCertificate(
        NOW,
        NOW + timedelta(minutes=30),
        NOW + timedelta(hours=1),
        "ac",
        10,
        ((NOW, 10),),
    )
    assert preserves_certificates((step(0, flow(limit=1000)),), (certificate,))
    assert not preserves_certificates((step(0, flow()),), (certificate,))


def test_repeated_local_dst_hour_cannot_reuse_the_other_instant_reference():
    from zoneinfo import ZoneInfo

    from core.reserve import reserve_planning_scope, source_schedule_mode
    from core.simulate import simulate

    berlin = ZoneInfo("Europe/Berlin")
    first = datetime(2026, 10, 25, 2, tzinfo=berlin, fold=0)
    second = first.replace(fold=1)
    slot = HourSlot(0, first, 0.5, 2, 0, 0, 20)
    snapshot = ConsumptionUncertaintySnapshot(
        (ConsumptionUncertaintyInterval(first, second, 0, 0),)
    )
    inputs = PlanInputs(first, 80, (slot,), consumption_uncertainty=snapshot)
    config = public_plant()
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        original = simulate(config, inputs, 20)
        repeated = simulate(
            config, replace(inputs, slots=(replace(slot, start=second),)), 20
        )
    assert original.reserve_decision.recharge is not None
    assert repeated.reserve_decision.recharge is None


def test_naive_legacy_step_end_retains_elapsed_duration():
    original = step(0, flow())
    naive = replace(
        original, slot=replace(original.slot, start=NOW.replace(tzinfo=None))
    )
    assert step_end(naive) == NOW.replace(tzinfo=None) + timedelta(minutes=30)


@pytest.mark.parametrize("binding", [None, "manual48", "phase48"])
def test_joint_psu24_and_psu48_release_receives_real_sequential_physics(binding):
    from core.model import ControlParams
    from core.simulate import simulate

    config = replace(
        configuration(),
        control=ControlParams(soc_buffer_percent=0),
        support=SupportParams(
            configured=True,
            coordinated=False,
            dc24_active=True,
            dc48_active=True,
            psu48_bus_voltage_v=52,
            native48_base_w=20,
            dc48_power_w=60,
            dc48_forced_on=binding == "manual48",
            dc48_phase_until=(NOW + timedelta(minutes=30)).timestamp()
            if binding == "phase48"
            else 0,
        ),
    )
    slots = (
        HourSlot(0, NOW, 0.5, 0, 0, 0, 20),
        HourSlot(1, NOW + timedelta(minutes=30), 0.5, 0, 250, 0, 0),
    )
    inputs = PlanInputs(NOW, 80, slots)
    baseline = simulate(
        config, inputs, 100, dc24_schedule=(True, False), dc48_schedule=(True, False)
    )
    before = tuple(
        SourceStep(slot, value, (), 5)
        for slot, value in zip(slots, baseline.flows, strict=True)
    )
    budget = ValidationBudget(1)

    def evaluate(proposal, pessimistic):
        budget.record()
        actual = simulate(
            config,
            inputs,
            100,
            dc24_schedule=tuple(value.support_dc24 for value in proposal),
            dc48_schedule=tuple(value.support_dc48 for value in proposal),
        )
        return actual, tuple(
            SourceStep(slot, value, (), 5)
            for slot, value in zip(slots, actual.flows, strict=True)
        )

    actual, after, summary = certify(
        config, baseline, before, baseline, before, coverage(), evaluate, budget, False
    )
    assert actual.end_soc_percent == pytest.approx(baseline.end_soc_percent)
    assert actual.total_export_wh <= baseline.total_export_wh + 1e-6
    if binding is None:
        assert len(summary.certificates) == 1
        assert summary.certificates[0].kind == "dc24"
        assert not after[0].flow.support_dc24 and not after[0].flow.support_dc48
        assert after[0].flow.dcdc_input_wh > 0
        assert actual.total_import_wh < baseline.total_import_wh
    else:
        assert summary.certificates == ()
        assert actual is baseline
        assert budget.used == 1


def test_reference_identity_survives_recording_and_changes_with_physical_ledger():
    from core.replay import decode, encode
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    inputs = replace(
        inputs,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
    )
    original = simulate(config, inputs, 20).reserve_decision.recharge
    restored_config, restored_inputs = decode(encode((config, inputs)))
    restored = simulate(restored_config, restored_inputs, 20).reserve_decision.recharge
    assert original.reference_id is not None and len(original.reference_id) == 64
    assert restored.reference_id == original.reference_id
    assert all(
        item.reference_id == original.reference_id for item in original.certificates
    )
    changed = replace(
        inputs,
        slots=(
            *inputs.slots[:-1],
            replace(inputs.slots[-1], pv_wh=326, pv_p10_wh=326, pv_p90_wh=326),
        ),
    )
    changed_summary = simulate(config, changed, 20).reserve_decision.recharge
    assert changed_summary.reference_id != original.reference_id


def test_persisted_certificate_needs_its_own_active_full_horizon_proof_context():
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        source_schedule_mode,
    )
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    inputs = replace(
        inputs,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
    )
    original = simulate(config, inputs, 20)
    copied = replace(original)
    assert original.reserve_decision.recharge.certificates
    with reserve_planning_scope(), source_schedule_mode(False):
        assert not recharge_preserved(config, inputs, original, copied)
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        assert not recharge_preserved(config, inputs, original, copied)


def test_optional_probes_and_identical_input_clones_share_the_32_proof_limit():
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0), (0, 0, 0)])
    inputs = replace(
        inputs,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
    )
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        original = simulate(config, inputs, 20)
        assert original.reserve_decision.recharge.certificates
        outcomes = []
        # Distinct after-anchor counterfactuals leave the solar promise intact;
        # this proof gate is separate from the allocator's strict-import gate.
        for energy in range(MAX_JOINT_VALIDATIONS):
            trial = simulate_fixed_dispatch(config, inputs, (0, 0, 0, energy), original)
            outcomes.append(
                recharge_preserved(config, replace(inputs), original, trial)
            )
        assert any(outcomes)
        assert not outcomes[-1]
        with source_schedule_mode(True):
            final = simulate(config, replace(inputs), 20)
    assert (
        final.reserve_decision.recharge.reference_id
        == original.reserve_decision.recharge.reference_id
    )
    assert final.reserve_decision.recharge.validations == MAX_JOINT_VALIDATIONS
    assert final.reserve_decision.joint.joint_validations == MAX_JOINT_VALIDATIONS


def test_snapshot_without_extra_certificates_keeps_ordinary_joint_stress_proof():
    from core.model import MarketPrice
    from core.reserve import reserve_planning_scope
    from core.simulate import simulate

    config = public_plant()
    config = replace(config, control=replace(config.control, soc_buffer_percent=0))
    inputs = public_forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)])
    inputs = replace(
        inputs,
        start_soc_percent=95,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh / 2, pv_p90_wh=slot.pv_wh)
            for slot in inputs.slots
        ),
        market_prices=tuple(
            MarketPrice(slot.start, slot.start + timedelta(hours=1), price)
            for slot, price in zip(inputs.slots, (0, 300, 0), strict=True)
        ),
    )
    with reserve_planning_scope(config, inputs):
        actual = simulate(config, inputs, 20)
    assert actual.reserve_decision.recharge.certificates == ()
    assert actual.reserve_decision.joint.joint_validations <= MAX_JOINT_VALIDATIONS
    assert actual.total_import_wh <= 100 + 1e-6
    assert actual.end_soc_percent == pytest.approx(95)


def test_pv_covered_psu_intake_is_not_counted_as_avoided_grid_import():
    from core.model import ControlParams
    from core.simulate import simulate

    config = replace(
        configuration(),
        control=ControlParams(soc_buffer_percent=0),
        support=SupportParams(configured=True, coordinated=False, dc48_available=False),
    )
    slots = (
        HourSlot(0, NOW, 0.5, 0, 100, 0, 20),
        HourSlot(1, NOW + timedelta(minutes=30), 0.5, 0, 300, 0, 0),
    )
    inputs = PlanInputs(NOW, 80, slots)
    baseline = simulate(config, inputs, 100, dc24_schedule=(True, False))
    before = tuple(
        SourceStep(slot, value, (), 5)
        for slot, value in zip(slots, baseline.flows, strict=True)
    )
    budget = ValidationBudget(1)

    def evaluate(proposal, pessimistic):
        budget.record()
        actual = simulate(
            config,
            inputs,
            100,
            dc24_schedule=tuple(value.support_dc24 for value in proposal),
        )
        return actual, tuple(
            SourceStep(slot, value, (), 5)
            for slot, value in zip(slots, actual.flows, strict=True)
        )

    actual, _, summary = certify(
        config, baseline, before, baseline, before, coverage(), evaluate, budget, False
    )
    assert baseline.total_import_wh == 0
    assert actual.total_import_wh == 0
    assert summary.certificates == ()
    assert "no_improvement" in summary.rejection_reasons


@pytest.mark.parametrize(
    "demands, solar, expected",
    [((2, 2, 2, 2, 2, 2), 180, 10), ((0, 2, 0, 2, 0, 2), 175, 4)],
)
def test_short_dc_phase_extension_reclaims_only_the_physically_repayable_edge(
    demands, solar, expected
):
    from core.model import ControlParams
    from core.simulate import simulate

    config = replace(
        configuration(),
        control=ControlParams(soc_buffer_percent=0),
        support=SupportParams(configured=True, coordinated=False, dc48_available=False),
    )
    slots = (
        HourSlot(0, NOW, 0.5, 0, 0, 0, 20),
        *(
            HourSlot(i + 1, NOW + timedelta(minutes=30 + i * 5), 1 / 12, 0, 0, 0, dc)
            for i, dc in enumerate(demands)
        ),
        HourSlot(7, NOW + timedelta(hours=1), 0.5, 1, solar, 0, 0),
    )
    inputs = PlanInputs(NOW, 80, slots)
    baseline = simulate(
        config,
        inputs,
        100,
        dc24_schedule=(False, True, True, True, True, True, True, False),
    )
    before = tuple(
        SourceStep(slot, value, (), 5)
        for slot, value in zip(slots, baseline.flows, strict=True)
    )
    budget = ValidationBudget(1)

    def evaluate(proposal, pessimistic):
        budget.record()
        actual = simulate(
            config,
            inputs,
            100,
            dc24_schedule=tuple(value.support_dc24 for value in proposal),
        )
        return actual, tuple(
            SourceStep(slot, value, (), 5)
            for slot, value in zip(slots, actual.flows, strict=True)
        )

    actual, _, summary = certify(
        config, baseline, before, baseline, before, coverage(), evaluate, budget, False
    )
    assert summary.certificates
    assert baseline.total_import_wh - actual.total_import_wh == pytest.approx(expected)
    assert actual.end_soc_percent == pytest.approx(baseline.end_soc_percent)
    assert sum(item.battery_draw_wh for item in summary.certificates) == pytest.approx(
        expected
    )
    assert all(
        (item.end.timestamp() - item.start.timestamp()) < 1800
        for item in summary.certificates
    )
    assert actual.total_export_wh == pytest.approx(baseline.total_export_wh - expected)
    assert summary.validations <= MAX_JOINT_VALIDATIONS
    if demands[0] == 0:
        assert any(
            amount == 0
            for item in summary.certificates
            for _, amount in item.step_debits
        )


def test_uncertainty_before_the_requested_interval_does_not_spend_its_reserve():
    snapshot = ConsumptionUncertaintySnapshot(
        (
            ConsumptionUncertaintyInterval(NOW, NOW + timedelta(minutes=30), 100, 0),
            ConsumptionUncertaintyInterval(
                NOW + timedelta(minutes=30), NOW + timedelta(hours=1), 10, 0
            ),
        )
    )
    assert (
        uncertainty_wh(
            snapshot,
            NOW + timedelta(minutes=30),
            NOW + timedelta(hours=1),
            configuration(),
        )
        == 10
    )


def test_anchor_proof_rejects_an_unsafe_intermediate_physical_endpoint():
    """Fault injection: recovered final SOC cannot conceal a broken step solver."""
    before = (step(0, flow()), step(1, flow(charge=20)))
    after = (step(0, flow(4)), before[1])
    assert (
        anchor_failure(
            configuration(),
            trajectory(before),
            trajectory(after),
            before,
            after,
            0,
            1,
            coverage(),
        )
        == "soc_floor"
    )


def test_consumption_reserve_is_checked_before_a_recovering_step_too():
    before = (
        step(0, flow(20)),
        step(1, flow(30, charge=100)),
        step(2, flow(40, charge=100)),
    )
    after = (
        step(0, replace(flow(10, discharge=100), soc_start_percent=20)),
        step(1, replace(flow(30, charge=200), soc_start_percent=10)),
        before[2],
    )
    certificate = RechargeCertificate(
        NOW,
        NOW + timedelta(minutes=30),
        step_end(before[2]),
        "dc24",
        100,
        ((NOW, 100),),
    )
    assert (
        anchor_failure(
            configuration(),
            trajectory(before),
            trajectory(after),
            before,
            after,
            1,
            2,
            coverage(hours=1.5, ac=150),
            (certificate,),
        )
        == "consumption_reserve"
    )


def test_anchor_proof_keeps_the_unrelated_original_short_window_closed():
    before = (
        step(0, flow()),
        step(1, flow(charge=20)),
        step(2, flow()),
        step(3, flow(charge=20)),
    )
    after = (
        step(0, flow(79, discharge=10)),
        step(1, flow(79.5, charge=20)),
        step(2, flow(79, discharge=5)),
        step(3, flow(80, charge=20)),
    )
    certificate = RechargeCertificate(
        NOW, NOW + timedelta(minutes=30), step_end(before[3]), "dc24", 10, ((NOW, 10),)
    )
    assert (
        anchor_failure(
            configuration(),
            trajectory(before),
            trajectory(after),
            before,
            after,
            0,
            3,
            coverage(),
            (certificate,),
        )
        == "uncertified_window_draw"
    )


def test_forecast_gaps_cannot_be_concatenated_into_a_source_dwell():
    from core.simulate import simulate

    config = replace(
        configuration(),
        support=SupportParams(configured=True, coordinated=False, dc48_available=False),
    )
    slots = (
        HourSlot(0, NOW, 0.25, 0, 0, 0, 20),
        HourSlot(1, NOW + timedelta(hours=1), 0.25, 1, 0, 0, 20),
        HourSlot(2, NOW + timedelta(hours=2), 0.5, 2, 1000, 0, 0),
    )
    inputs = PlanInputs(NOW, 80, slots)
    reference = simulate(config, inputs, 100, dc24_schedule=(True, True, False))
    steps = tuple(
        SourceStep(slot, value, (), 5)
        for slot, value in zip(slots, reference.flows, strict=True)
    )

    def forbidden_evaluation(proposal, pessimistic):
        pytest.fail("An unobserved gap cannot establish a continuous OFF phase")

    actual, _, summary = certify(
        config,
        reference,
        steps,
        reference,
        steps,
        coverage(),
        forbidden_evaluation,
        ValidationBudget(1),
        False,
    )
    assert actual is reference
    assert summary.certificates == ()


def test_finishing_a_zero_load_off_phase_does_not_create_multiple_energy_credits():
    from core.model import ControlParams
    from core.simulate import simulate

    config = replace(
        configuration(),
        control=ControlParams(soc_buffer_percent=0),
        support=SupportParams(configured=True, coordinated=False, dc48_available=False),
    )
    slots = (
        HourSlot(0, NOW, 0.5, 0, 0, 0, 20),
        *(
            HourSlot(
                i + 1,
                NOW + timedelta(minutes=30 + i * 5),
                1 / 12,
                0,
                0,
                0,
                2 if i == 0 else 0,
            )
            for i in range(6)
        ),
        HourSlot(7, NOW + timedelta(hours=1), 0.5, 1, 180, 0, 0),
    )
    inputs = PlanInputs(NOW, 80, slots)
    baseline = simulate(
        config,
        inputs,
        100,
        dc24_schedule=(False, True, True, True, True, True, True, False),
    )
    before = tuple(
        SourceStep(slot, value, (), 5)
        for slot, value in zip(slots, baseline.flows, strict=True)
    )
    budget = ValidationBudget(1)

    def complete_off_phase(proposal, pessimistic):
        budget.record()
        actual = simulate(config, inputs, 100, dc24_schedule=(False,) * len(slots))
        return actual, tuple(
            SourceStep(slot, value, (), 5)
            for slot, value in zip(slots, actual.flows, strict=True)
        )

    actual, _, summary = certify(
        config,
        baseline,
        before,
        baseline,
        before,
        coverage(),
        complete_off_phase,
        budget,
        False,
    )
    assert baseline.total_import_wh - actual.total_import_wh == pytest.approx(2)
    assert len(summary.certificates) == 1
    assert summary.certificates[0].battery_draw_wh == pytest.approx(2)
    assert budget.used == 2


def test_a_rejected_nominal_probe_cannot_leave_an_unfunded_stress_permission():
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.simulate import simulate

    config = public_plant()
    config = replace(
        config,
        control=replace(
            config.control, soc_buffer_percent=0, predrain_pv_confidence=324 / 325
        ),
    )
    slots = tuple(
        HourSlot(
            i,
            NOW + timedelta(minutes=5 * i),
            1 / 12,
            i // 12,
            325 / 12 if 7 <= i < 19 else 0,
            100 / 12 if i < 3 else 50 if i < 7 else 0,
            0,
            pv_p10_wh=324 / 12 if 7 <= i < 19 else 0,
            pv_p90_wh=325 / 12 if 7 <= i < 19 else 0,
        )
        for i in range(20)
    )
    inputs = PlanInputs(NOW, 80, slots, consumption_uncertainty=coverage(hours=2))
    zeros = (0,) * len(slots)
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        assert reference.reserve_decision.recharge.certificates
        used = reference.reserve_decision.recharge.validations
        assert used < MAX_JOINT_VALIDATIONS - 2
        assert (MAX_JOINT_VALIDATIONS - used) % 2 == 0
        # Each distinct after-anchor load needs nominal AND stress proof.
        for energy in range((MAX_JOINT_VALIDATIONS - used - 2) // 2):
            trial = simulate_fixed_dispatch(
                config, inputs, (*zeros[:-1], energy), reference
            )
            assert recharge_preserved(config, inputs, reference, trial)
        first = reference.flows[0]
        removed = replace(
            first,
            inverter_on=False,
            inverter_limit_w=0,
            switching_schedule=tuple(
                replace(part, inverter_on=False) for part in first.switching_schedule
            ),
        )
        controls = replace(reference, flows=(removed, *reference.flows[1:]))
        displaced = simulate_fixed_dispatch(config, inputs, zeros, controls)
        assert displaced.flows[0].inverter_output_wh == 0
        assert not recharge_preserved(config, inputs, reference, displaced)
        # That rejection still used a real nominal validation. A subsequent
        # intact schedule must be refused when its stress proof no longer fits.
        intact = simulate_fixed_dispatch(config, inputs, zeros, reference)
        assert not recharge_preserved(config, inputs, reference, intact)
        with source_schedule_mode(True):
            final = simulate(config, inputs, 20)
    assert final.reserve_decision.recharge.validations == MAX_JOINT_VALIDATIONS
    assert final.reserve_decision.joint.joint_validations == MAX_JOINT_VALIDATIONS


@pytest.mark.parametrize(
    "response",
    [
        "inverter_refused",
        "stress_protection",
        "dc_for_ac_exchange",
        "old_certificate_displaced",
    ],
)
def test_proof_provider_responses_cannot_invent_an_actuator_permission(response):
    """Fault injection around the physical provider's allowed safety fallback."""
    from core.model import ControlParams
    from core.simulate import simulate

    config = replace(
        configuration(),
        control=ControlParams(soc_buffer_percent=0),
        support=SupportParams(configured=True, coordinated=False, dc48_available=False),
    )
    if response == "dc_for_ac_exchange":
        values, requested = ((0, 0, 50), (0, 50, 0), (50, 0, 0)), (False, False, False)
    elif response == "inverter_refused":
        values, requested = ((0, 50, 0), (250, 0, 0)), (False, False)
    elif response == "stress_protection":
        values, requested = ((0, 0, 20), (250, 0, 0)), (True, False)
    else:
        values, requested = ((0, 50, 0), (0, 0, 20), (250, 0, 0)), (False, True, False)
    slots = tuple(
        HourSlot(i, NOW + timedelta(minutes=30 * i), 0.5, i // 2, pv, ac, dc)
        for i, (pv, ac, dc) in enumerate(values)
    )
    inputs = PlanInputs(NOW, 95 if response == "dc_for_ac_exchange" else 80, slots)
    baseline = simulate(config, inputs, 100, dc24_schedule=requested)
    before = tuple(
        SourceStep(slot, value, (), 5)
        for slot, value in zip(slots, baseline.flows, strict=True)
    )
    budget = ValidationBudget(2 if response == "stress_protection" else 1)

    def evaluate(proposal, pessimistic):
        budget.record()
        ac = any(value.inverter_limit_w for value in proposal)
        sources = tuple(value.support_dc24 for value in proposal)
        threshold = 20 if ac else 100
        if response == "inverter_refused":
            threshold = 100
        elif response == "stress_protection" and pessimistic:
            sources = requested
        elif response == "dc_for_ac_exchange" and ac:
            sources = (True, False, False)
        elif response == "old_certificate_displaced" and ac:
            sources = requested
        actual = simulate(config, inputs, threshold, dc24_schedule=sources)
        # Generic simulator records actual output; reserve-specific permission
        # metadata comes from the accepted physical actuator state.
        annotated = tuple(
            replace(
                value,
                inverter_limit_w=config.inverter.max_power_w
                if value.inverter_on
                else 0.0,
            )
            for value in actual.flows
        )
        actual = replace(actual, flows=annotated)
        return actual, tuple(
            SourceStep(slot, value, (), 5)
            for slot, value in zip(slots, actual.flows, strict=True)
        )

    actual, _, summary = certify(
        config,
        baseline,
        before,
        baseline,
        before,
        coverage(),
        evaluate,
        budget,
        response == "stress_protection",
    )
    reason = {
        "inverter_refused": "ac_not_served",
        "stress_protection": "dc24_stress_protection",
        "dc_for_ac_exchange": "dc_priority",
        "old_certificate_displaced": "certificate_displaced",
    }[response]
    assert reason in summary.rejection_reasons
    if response == "old_certificate_displaced":
        assert summary.certificates and all(
            item.kind == "dc24" for item in summary.certificates
        )
        assert actual.total_import_wh < baseline.total_import_wh
    else:
        assert summary.certificates == ()
        assert actual is baseline


@pytest.mark.parametrize(
    "enabled, max_w, manual_w, blocked",
    [
        (True, 100, 100, True),
        (True, 100, 0, False),
        (False, 100, 100, False),
        (True, 0, 100, False),
    ],
)
def test_public_manual_export_cannot_spend_the_same_recharge_pv_twice(
    enabled, max_w, manual_w, blocked
):
    from core.optimize import plan
    from core.simulate import simulate

    config = public_plant()
    config = replace(
        config,
        control=replace(config.control, soc_buffer_percent=0),
        feedin=replace(config.feedin, enabled=enabled, max_w=max_w, manual_w=manual_w),
    )
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    legacy = plan(config, replace(inputs, consumption_uncertainty=None))
    actual = plan(config, inputs)
    summary = actual.trajectory.reserve_decision.recharge
    assert bool(summary.certificates) is not blocked
    if blocked:
        assert summary.status == "manual_feed_in"
        assert "manual_feed_in" in summary.rejection_reasons
        assert tuple(actual.feedin_schedule_w) == pytest.approx(
            tuple(legacy.feedin_schedule_w)
        )
        assert actual.trajectory.total_import_wh == pytest.approx(
            legacy.trajectory.total_import_wh
        )
        assert actual.trajectory.end_soc_percent == pytest.approx(
            legacy.trajectory.end_soc_percent
        )
        assert actual.trajectory.total_export_wh == pytest.approx(
            legacy.trajectory.total_export_wh
        )
        assert tuple(
            flow.inverter_output_wh for flow in actual.trajectory.flows
        ) == pytest.approx(
            tuple(flow.inverter_output_wh for flow in legacy.trajectory.flows)
        )
        assert tuple(
            (flow.support_dc24, flow.support_dc48, flow.switching_schedule)
            for flow in actual.trajectory.flows
        ) == tuple(
            (flow.support_dc24, flow.support_dc48, flow.switching_schedule)
            for flow in legacy.trajectory.flows
        )
        assert not simulate(config, inputs, 20).reserve_decision.recharge.certificates
    else:
        assert actual.trajectory.flows[0].inverter_output_wh == pytest.approx(25)


@pytest.mark.parametrize(
    "optional_wh, priced", [(0, False), (0.5, False), (2, False), (0, True)]
)
def test_complete_plan_preserves_a_physical_dc_edge_credit_after_optional_booking(
    monkeypatch, optional_wh, priced
):
    """Inject only the valid reference schedule, never a proof or certificate.

    This horizon's old PSU phase leaves ten Wh of repayable DC headroom.
    The provider fixes that phase; real source physics, certification,
    allocation, stress replay and final market arbitration still execute.
    """
    import core.reserve as reserve
    from core.model import ControlParams, MarketPrice, SurplusLoad, SurplusLoadState
    from core.optimize import plan
    from core.uncertainty import effective_uncertainty

    slots = tuple(
        HourSlot(
            index,
            NOW + timedelta(minutes=5 * index),
            1 / 12,
            index // 12,
            181 / 6 if index >= 12 else 0,
            10 if priced and index < 6 else 0,
            20 / 6 if index < 6 else 2 if index < 12 else 0,
        )
        for index in range(18)
    )
    config = replace(
        public_plant(),
        control=ControlParams(soc_buffer_percent=0, predrain_pv_confidence=180.5 / 181),
    )
    if optional_wh:
        load = SurplusLoad(
            "edge-surplus",
            "After house repayment",
            optional_wh * 12,
            min_runtime_min=5,
            min_off_min=0,
        )
        config = replace(config, loads=(load,))
    inputs = PlanInputs(
        NOW,
        80,
        slots,
        (SurplusLoadState("edge-surplus", soc_percent=0),) if optional_wh else (),
        consumption_uncertainty=coverage(hours=1.5),
        market_prices=tuple(
            MarketPrice(
                slot.start,
                slot.start + timedelta(minutes=5),
                300 if index < 6 else 100 if index < 12 else 0,
            )
            for index, slot in enumerate(slots)
        )
        if priced
        else (),
    )
    references = []

    def frozen_phase_reference(config, inputs, extra_ac, pv_scale, feedin, *, traces):
        source_schedule = tuple(
            (30 <= (slot.start.timestamp() - NOW.timestamp()) / 60 < 60, False)
            for slot in inputs.slots
        )
        result = reserve._simulate_reserve_policy(
            config,
            inputs,
            extra_ac,
            pv_scale,
            feedin,
            fixed_sources=source_schedule,
            fixed_limits=(0.0,) * len(source_schedule),
            traces=traces,
        )
        references.append(result)
        return result

    monkeypatch.setattr(reserve, "_simulate_reserve_reference", frozen_phase_reference)
    actual = plan(config, inputs)
    baseline = references[0]
    summary = actual.trajectory.reserve_decision.recharge
    assert summary.certificates
    assert all(item.kind == "dc24" for item in summary.certificates)
    assert all(
        item.reference_id == summary.reference_id for item in summary.certificates
    )
    certified_wh = sum(item.battery_draw_wh for item in summary.certificates)
    assert certified_wh > 0
    assert baseline.total_import_wh == pytest.approx(72 if priced else 12)
    assert actual.trajectory.total_import_wh < baseline.total_import_wh
    assert sum(flow.dcdc_input_wh for flow in actual.trajectory.flows) > sum(
        flow.dcdc_input_wh for flow in baseline.flows
    )
    assert actual.trajectory.end_soc_percent == pytest.approx(baseline.end_soc_percent)
    joint = actual.trajectory.reserve_decision.joint
    assert joint is not None
    assert joint.joint_validations <= MAX_JOINT_VALIDATIONS
    if priced:
        assert len({price.eur_per_mwh for price in inputs.market_prices}) > 1
        # A populated target with attempts proves the real final dispatcher
        # searched competing physical source placements under actual prices.
        assert any(target.attempted > 0 for target in joint.target_diagnostics)
        assert any(
            target.local_rejections
            or target.proof_rejections
            or target.proved_improvements
            for target in joint.target_diagnostics
        )
        assert "recharge_certificate" in joint.joint_rejection_reasons
    assert summary.validations <= MAX_JOINT_VALIDATIONS
    stress, _, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.control.upper_pv_reserve
    )
    replay = reserve.simulate_fixed_dispatch(
        config,
        inputs,
        tuple(flow.extra_ac_wh for flow in actual.trajectory.flows),
        actual.trajectory,
        pv_scale=stress,
    )
    assert replay.total_import_wh <= baseline.total_import_wh + 1e-6
    assert replay.end_soc_percent == pytest.approx(baseline.end_soc_percent)
    assert sum(flow.unserved_dc_wh for flow in replay.flows) == 0
    if optional_wh:
        booking = actual.load_plans[0].planned_energy_wh
        assert sum(
            flow.extra_ac_wh for flow in actual.trajectory.flows
        ) == pytest.approx(booking)
        if optional_wh == 0.5:
            assert booking == pytest.approx(optional_wh)
        else:
            # The stress repayment has only half a Wh left after the ten Wh
            # DC credit. A larger optional booking may not spend that promise.
            assert booking <= 0.5 + 1e-6


@pytest.mark.parametrize(
    "alteration", ["missing_interval", "removed_house_demand", "replaced_pv"]
)
def test_optional_actual_inputs_cannot_replace_the_frozen_house_ledger(alteration):
    """Only additional appliance AC belongs to the optional-input adapter."""
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        source_schedule_mode,
    )
    from core.simulate import simulate

    config = public_plant()
    config = replace(config, control=replace(config.control, soc_buffer_percent=0))
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    if alteration == "missing_interval":
        candidate = replace(inputs, slots=inputs.slots[:-1])
    elif alteration == "removed_house_demand":
        candidate = replace(
            inputs, slots=(replace(inputs.slots[0], ac_wh=99), *inputs.slots[1:])
        )
    else:
        candidate = replace(
            inputs, slots=(*inputs.slots[:-1], replace(inputs.slots[-1], pv_wh=326))
        )
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        assert reference.reserve_decision.recharge.certificates
        assert not recharge_preserved(
            config,
            inputs,
            reference,
            replace(reference),
            evaluation_inputs=candidate,
        )
        unchanged = simulate(config, inputs, 20)
    assert unchanged.reserve_decision.recharge == reference.reserve_decision.recharge
    assert unchanged.total_import_wh == reference.total_import_wh
    assert unchanged.end_soc_percent == reference.end_soc_percent


def test_root_reference_cannot_start_after_the_shared_proof_allowance_is_spent():
    """Fault boundary: earlier nested work owns the same finite allowance."""
    import core.reserve as reserve
    from core.simulate import simulate

    config = public_plant()
    inputs = public_forecast([(0, 100, 0), (0, 600, 0), (325, 0, 0)])
    legacy = simulate(config, replace(inputs, consumption_uncertainty=None), 20)
    with reserve.reserve_planning_scope(config, inputs):
        budget = reserve._reserve_cache.get().validation_budget
        for _ in range(25):
            budget.record()
        actual = simulate(config, inputs, 20)
        assert budget.used <= MAX_JOINT_VALIDATIONS
    assert actual.reserve_decision.recharge is None
    assert actual.total_import_wh == pytest.approx(legacy.total_import_wh)
    assert actual.end_soc_percent == pytest.approx(legacy.end_soc_percent)
    assert tuple(flow.inverter_output_wh for flow in actual.flows) == pytest.approx(
        tuple(flow.inverter_output_wh for flow in legacy.flows)
    )


def test_dc_market_keeps_the_last_full_validation_for_the_stressed_reference():
    """No cheap price preference may consume the mandatory stress replay."""
    import core.reserve as reserve
    from test_dc_pv_market import dc_plant
    from test_market import priced

    config = dc_plant()
    inputs = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [300, 100, 100])
    traces = {}
    reference = reserve._simulate_reserve_reference(
        config,
        inputs,
        None,
        1,
        None,
        traces=traces,
    )
    normal = reserve._apply_dc_market(config, inputs, None, 1, None, reference, traces)
    assert normal.flows[0].psu24_delivered_wh < reference.flows[0].psu24_delivered_wh
    budget = ValidationBudget(31)
    token = reserve._counted_validation.set(budget)
    try:
        refused = reserve._apply_dc_market(
            config, inputs, None, 1, None, reference, traces
        )
    finally:
        reserve._counted_validation.reset(token)
    assert budget.used == 31
    assert refused.flows == reference.flows
    assert refused.total_import_wh == reference.total_import_wh
    assert refused.end_soc_percent == reference.end_soc_percent


def test_a_second_credit_cannot_reintroduce_dc_outage_already_removed_by_the_first():
    """Adversarial provider: original outage is not a reusable service credit."""
    config = configuration()
    before = (
        step(
            0,
            replace(
                flow(source24=True, imported=10),
                unserved_dc_wh=5,
                dc_deficit_intervals=None,
            ),
        ),
        step(1, flow(source24=True, imported=10)),
        step(2, flow(charge=100), pv=100),
    )
    reference = trajectory(before)
    budget = ValidationBudget(1)

    def provider(proposal, pessimistic):
        budget.record()
        first_off = not proposal[0].support_dc24
        second_off = not proposal[1].support_dc24
        first = replace(
            flow(
                79 if first_off else 80,
                source24=not first_off,
                imported=0 if first_off else 10,
                discharge=10 if first_off else 0,
                dcdc=10 if first_off else 0,
            ),
            unserved_dc_wh=5 if second_off or not first_off else 0,
            dc_deficit_intervals=None,
        )
        second = flow(
            78 if second_off else 79,
            source24=not second_off,
            imported=0 if second_off else 10,
            discharge=10 if second_off else 0,
            dcdc=10 if second_off else 0,
        )
        supplied = (
            replace(before[0], flow=first),
            replace(before[1], flow=second),
            replace(before[2], flow=flow(charge=120 if second_off else 110)),
        )
        return trajectory(supplied), supplied

    actual, _, summary = certify(
        config,
        reference,
        before,
        reference,
        before,
        coverage(),
        provider,
        budget,
        False,
    )
    assert summary.certificates
    assert "accepted_dc_service" in summary.rejection_reasons
    assert actual.total_import_wh == pytest.approx(10)
    assert actual.flows[0].unserved_dc_wh == 0
    assert not actual.flows[0].support_dc24
    assert actual.flows[1].support_dc24
