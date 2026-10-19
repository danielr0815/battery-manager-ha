"""Independent tiny-state enumeration verifies bounded joint search counterexamples."""

from dataclasses import replace
from itertools import product

import pytest
from core.joint_dispatch import dispatch, proof_failure
from core.model import ConverterParams, Trajectory
from core.reserve import _joint_choices
from core.reserve_sources import SourceStep, source_state
from core.simulate import simulate, step_hour
from test_joint_dispatch import forecast, plant


def tiny_dispatch(
    series,
    prices,
    *,
    inverter_eta=1,
    evaluator=None,
    grid_indices=(1,),
    subdivisions=1,
    custom_config=None,
):
    """Real hourly physics, with a deliberately conservative fixed DC reference.

    Unlike production's five-minute search this has only three indivisible
    quanta, so all combinations can be checked independently and exhaustively.
    """
    config = custom_config or plant()
    config = replace(config, inverter=ConverterParams(1000, inverter_eta, 0))
    inputs = forecast(series, prices)
    if subdivisions > 1:
        from datetime import timedelta

        inputs = replace(
            inputs,
            slots=tuple(
                replace(
                    slot,
                    index=n * subdivisions + quantum,
                    start=slot.start + timedelta(hours=quantum / subdivisions),
                    duration=1 / subdivisions,
                    pv_wh=slot.pv_wh / subdivisions,
                    ac_wh=slot.ac_wh / subdivisions,
                    dc_wh=slot.dc_wh / subdivisions,
                )
                for n, slot in enumerate(inputs.slots)
                for quantum in range(subdivisions)
            ),
        )
    decision = simulate(config, replace(inputs, market_prices=()), 20).reserve_decision

    def evaluate(proposal):
        soc = inputs.start_soc_percent
        steps = []
        for slot, desired in zip(inputs.slots, proposal, strict=True):
            flow = step_hour(
                config,
                soc,
                slot,
                config.control.inverter_min_soc_percent
                if desired.inverter_limit_w
                else 100,
                dc24_from_grid=desired.support_dc24,
                dc48_support=desired.support_dc48,
                inverter_limit_w=desired.inverter_limit_w,
            )
            steps.append(SourceStep(slot, flow, (), 0))
            soc = flow.soc_end_percent
        flows = tuple(s.flow for s in steps)
        return Trajectory(
            flows,
            sum(f.grid_import_wh for f in flows),
            sum(f.grid_export_wh for f in flows),
            soc,
            reserve_decision=decision,
        ), tuple(steps)

    # Spend the fixed reference budget in the cheaper first slot. This is an
    # admissible booked reference, regardless of how its planner produced it.
    desired = tuple(
        replace(
            step_hour(config, 95, slot, 100),
            support_dc24=n // subdivisions in grid_indices,
            inverter_limit_w=0,
        )
        for n, slot in enumerate(inputs.slots)
    )
    reference, before = evaluate(desired)
    options = tuple(
        _joint_choices(
            config, s.flow.soc_start_percent, s.slot, 0, 1, 0, (False, False), s.flow
        )
        for s in before
    )
    before = tuple(
        replace(s, joint_choice_factory=lambda opt=opt: opt)
        for s, opt in zip(before, options, strict=True)
    )
    actual = dispatch(
        config,
        reference,
        before,
        inputs.market_prices,
        (lambda proposal: evaluator(evaluate, proposal)) if evaluator else evaluate,
    )
    return config, inputs, reference, before, options, evaluate, actual


@pytest.mark.parametrize("eta", [1, 0.8])
def test_dc_receiver_remains_searchable_when_full_ac_dc_exceeds_budget(eta):
    config, inputs, reference, before, options, evaluate, actual = tiny_dispatch(
        [(0, 0, 100), (0, 100, 100), (100, 0, 0)],
        [0, 300, 0],
        inverter_eta=eta,
    )
    assert reference.flows[0].battery_discharge_wh == pytest.approx(100)
    assert reference.flows[1].psu24_delivered_wh == pytest.approx(100)
    assert actual.flows[0].psu24_delivered_wh == pytest.approx(100)
    assert actual.flows[1].psu24_delivered_wh == 0
    assert actual.flows[1].inverter_output_wh == 0
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.end_soc_percent == pytest.approx(reference.end_soc_percent)
    assert actual.reserve_decision.joint.joint_status == "accepted"
    assert actual.reserve_decision.joint.joint_shifted_wh == pytest.approx(100)

    # Oracle does not use dispatch's targets, donor paths, rank or prefilters.
    # Enumerate every physically available state over this entire tiny horizon.
    feasible = []
    for proposal in product(*options):
        trial, after = evaluate(proposal)
        if proof_failure(config, reference, trial, before, after) is None:
            feasible.append(trial)

    def value(trajectory):
        return tuple(
            round(
                sum(
                    old.grid_import_wh - new.grid_import_wh
                    for p, old, new in zip(
                        (0, 300, 0), reference.flows, trajectory.flows, strict=True
                    )
                    if p == price
                ),
                6,
            )
            for price in (300, 0)
        )

    assert value(actual) == max(map(value, feasible)) == (100, -100)
    assert all(not (any(source_state(f)) and f.inverter_limit_w) for f in actual.flows)


def test_alternate_smaller_donor_avoids_greedy_import_overcompensation():
    config, inputs, reference, before, options, evaluate, actual = tiny_dispatch(
        [(0, 0, 100), (0, 100, 50), (0, 0, 50), (150, 0, 0)],
        [0, 300, 100, 0],
        inverter_eta=0.8,
    )
    assert actual.flows[0].battery_discharge_wh == pytest.approx(100)
    assert actual.flows[1].psu24_delivered_wh == 0
    assert actual.flows[2].psu24_delivered_wh == pytest.approx(50)
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    values = []
    for proposal in product(*options):
        trial, after = evaluate(proposal)
        if proof_failure(config, reference, trial, before, after) is None:
            values.append(
                tuple(
                    round(
                        sum(
                            old.grid_import_wh - new.grid_import_wh
                            for p, old, new in zip(
                                (0, 300, 100, 0),
                                reference.flows,
                                trial.flows,
                                strict=True,
                            )
                            if p == price
                        ),
                        6,
                    )
                    for price in (300, 100, 0)
                )
            )
    actual_value = tuple(
        round(
            sum(
                old.grid_import_wh - new.grid_import_wh
                for p, old, new in zip(
                    (0, 300, 100, 0), reference.flows, actual.flows, strict=True
                )
                if p == price
            ),
            6,
        )
        for price in (300, 100, 0)
    )
    assert actual_value == max(values) == (50, -50, 0)
    assert "extra_import" in actual.reserve_decision.joint.joint_rejection_reasons


def test_receiver_variants_get_adjacent_proofs_after_a_failed_full_receiver(
    monkeypatch,
):
    monkeypatch.setattr(
        "core.joint_dispatch.balanced_proposals", lambda *_: ((), False)
    )
    attempted = []

    def actuator(evaluate, proposal):
        trial, after = evaluate(proposal)
        attempted.append(bool(proposal[1].inverter_limit_w))
        if proposal[1].inverter_limit_w:
            # A full local estimate can fail its sequential proof. The next
            # receiver state at this price must still receive its own proof.
            trial = replace(trial, total_export_wh=trial.total_export_wh + 1)
        return trial, after

    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 100), (0, 100, 100), (0, 0, 100), (200, 0, 0)],
        [0, 300, 100, 0],
        evaluator=actuator,
    )
    assert attempted[:2] == [True, False]
    assert actual.flows[1].psu24_delivered_wh == 0
    assert actual.flows[1].inverter_output_wh == 0
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert "extra_export" in actual.reserve_decision.joint.joint_rejection_reasons


def test_two_receivers_share_donors_when_neither_receiver_can_balance_alone():
    config, inputs, reference, before, options, evaluate, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 8), (0, 0, 11), (0, 0, 10), (18, 0, 0)],
        [0, 300, 1, 200, 0],
    )
    # The fixed reference also needs the second receiver on grid; the helper's
    # default uses only slot1, so build this reference explicitly and dispatch.
    reference, before = evaluate(
        tuple(
            replace(s.flow, support_dc24=n in (1, 3), inverter_limit_w=0)
            for n, s in enumerate(before)
        )
    )
    before = tuple(
        replace(s, joint_choice_factory=lambda opt=opt: opt)
        for s, opt in zip(before, options, strict=True)
    )
    actual = dispatch(config, reference, before, inputs.market_prices, evaluate)
    assert [round(f.psu24_delivered_wh) for f in actual.flows] == [7, 0, 11, 0, 0]
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.end_soc_percent == pytest.approx(reference.end_soc_percent)
    assert actual.reserve_decision.joint.joint_shifted_wh == pytest.approx(18)
    # All subsets of two donor quantities are {0,7,11,18}; neither8 nor10 is
    # financeable independently. A full independent oracle confirms the pair.
    feasible_values = []
    for proposal in product(*options):
        trial, after = evaluate(proposal)
        if proof_failure(config, reference, trial, before, after) is None:
            feasible_values.append(
                (
                    round(
                        reference.flows[1].grid_import_wh
                        - trial.flows[1].grid_import_wh,
                        6,
                    ),
                    round(
                        reference.flows[3].grid_import_wh
                        - trial.flows[3].grid_import_wh,
                        6,
                    ),
                )
            )
    assert max(feasible_values) == (8, 10)


def test_paired_exchange_in_later_pv_window_gets_first_proof_at_higher_prices():
    attempted = []

    def capture(evaluate, proposal):
        attempted.append(proposal)
        return evaluate(proposal)

    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 8), (0, 0, 11), (0, 0, 10), (18, 0, 0)] * 2,
        [0, 300, 1, 200, 0, 0, 900, 1, 800, 0],
        grid_indices=(1, 3, 6, 8),
        evaluator=capture,
    )
    assert not attempted[0][6].support_dc24
    assert not attempted[0][8].support_dc24
    assert attempted[0][1].support_dc24
    assert attempted[0][3].support_dc24
    assert actual.flows[6].psu24_delivered_wh == actual.flows[8].psu24_delivered_wh == 0
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.end_soc_percent == pytest.approx(reference.end_soc_percent)


@pytest.mark.parametrize("prices", [(0, 300, 100, 0), (-300, -100, -200, -300)])
def test_equivalent_donor_signatures_choose_lower_actual_opportunity_cost(prices):
    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 7), (0, 0, 7), (14, 0, 0)],
        prices,
    )
    assert actual.flows[0].psu24_delivered_wh == pytest.approx(7)
    assert actual.flows[1].psu24_delivered_wh == 0
    assert actual.flows[2].psu24_delivered_wh == 0
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)


def test_high_price_window_without_donor_budget_cannot_starve_earlier_pair():
    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 8), (0, 0, 11), (0, 0, 10), (18, 0, 0)] + [(0, 0, 1)] * 30,
        [0, 300, 1, 200, 0] + [1000] * 30,
        grid_indices=(1, 3, *range(5, 35)),
    )
    assert [round(f.psu24_delivered_wh) for f in actual.flows[:4]] == [7, 0, 11, 0]
    assert all(f.psu24_delivered_wh == pytest.approx(1) for f in actual.flows[5:])
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.reserve_decision.joint.joint_shifted_wh == pytest.approx(18)
    assert 0 < actual.reserve_decision.joint.joint_validations <= 32


def test_quantized_pair_keeps_all_new_source_phases_at_least_thirty_minutes():
    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 8), (0, 0, 11), (0, 0, 10), (18, 0, 0)],
        [0, 300, 1, 200, 0],
        grid_indices=(1, 3),
        subdivisions=12,
    )
    assert sum(f.psu24_delivered_wh for f in actual.flows[12:24]) == pytest.approx(0)
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.reserve_decision.joint.joint_shifted_wh > 0
    states = [f.support_dc24 for f in actual.flows]
    runs = []
    for state in states:
        if runs and runs[-1][0] == state:
            runs[-1][1] += 1
        else:
            runs.append([state, 1])
    assert all(length >= 6 for _, length in runs[:-1])


@pytest.mark.parametrize(
    "limit",
    ["MAX_BALANCE_EXPANSIONS", "MAX_BALANCE_SIGNATURES", "MAX_RECEIVER_DESCRIPTORS"],
)
def test_scalar_search_limits_preserve_safe_reference_and_report_incompleteness(
    monkeypatch, limit
):
    import core.joint_balance as balance

    monkeypatch.setattr(balance, limit, 1)
    _, _, reference, before, options, _, _ = tiny_dispatch(
        [(0, 0, 7), (0, 0, 8), (0, 0, 11), (0, 0, 10), (18, 0, 0)],
        [0, 300, 1, 200, 0],
        grid_indices=(1, 3),
        subdivisions=12,
    )
    proposals, limited = balance.balanced_proposals(
        before, options, tuple([0] * 12 + [300] * 12 + [1] * 12 + [200] * 12 + [0] * 12)
    )
    assert limited
    assert len(proposals) <= 3
    assert reference.flows == tuple(s.flow for s in before)


def test_no_complete_proof_is_started_when_shared_budget_is_already_exhausted():
    from core.joint_dispatch import ValidationBudget

    config, inputs, reference, before, _, _, _ = tiny_dispatch(
        [(0, 0, 7), (0, 0, 7), (7, 0, 0)],
        [0, 300, 0],
    )

    def forbidden(_):
        raise AssertionError("No complete simulation may exceed the shared32budget")

    actual = dispatch(
        config,
        reference,
        before,
        inputs.market_prices,
        forbidden,
        validation_budget=ValidationBudget(32),
    )
    assert actual.flows == reference.flows
    assert actual.reserve_decision.joint.joint_status == "search_limit"
    assert actual.reserve_decision.joint.joint_validations == 32


def test_failed_balanced_physical_proof_never_publishes_an_unproved_allocation():
    def corrupt(evaluate, proposal):
        trial, after = evaluate(proposal)
        return replace(trial, total_export_wh=trial.total_export_wh + 1), after

    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 7), (7, 0, 0)],
        [0, 300, 0],
        evaluator=corrupt,
    )
    assert actual.flows == reference.flows
    assert "extra_export" in actual.reserve_decision.joint.joint_rejection_reasons


def test_long_donor_run_exhausts_work_budget_without_python_recursion(monkeypatch):
    from datetime import timedelta

    import core.joint_balance as balance
    from core.model import HourSlot
    from test_joint_dispatch import NOW

    monkeypatch.setattr(balance, "MAX_BALANCE_EXPANSIONS", 3000)
    config = plant()
    soc = 95.0
    steps = []
    options = []
    for n in range(1011):
        slot = HourSlot(
            n,
            NOW + timedelta(minutes=5 * n),
            1 / 12,
            0,
            0,
            0,
            0.001 if n < 1005 else 1.005 / 6,
        )
        flow = step_hour(
            config, soc, slot, 100, dc24_from_grid=n >= 1005, inverter_limit_w=0
        )
        choices = _joint_choices(config, soc, slot, 0, 1, 0, (False, False), flow)
        steps.append(SourceStep(slot, flow, (), 0))
        options.append(choices)
        soc = flow.soc_end_percent
    proposals, limited = balance.balanced_proposals(
        steps, options, [0] * 1005 + [300] * 6
    )
    assert limited
    assert len(proposals) <= 3
    assert all(f.psu24_delivered_wh == 0 for f in (s.flow for s in steps[:1005]))


@pytest.mark.parametrize("allowance", [185, 200, 1000])
def test_work_exhaustion_at_different_search_depths_keeps_candidates_bounded(
    monkeypatch, allowance
):
    import core.joint_balance as balance

    monkeypatch.setattr(balance, "MAX_BALANCE_EXPANSIONS", allowance)
    _, _, reference, before, options, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 8), (0, 0, 11), (0, 0, 10), (18, 0, 0)],
        [0, 300, 1, 200, 0],
        grid_indices=(1, 3),
        subdivisions=12,
    )
    proposals, limited = balance.balanced_proposals(
        before, options, [0] * 12 + [300] * 12 + [1] * 12 + [200] * 12 + [0] * 12
    )
    assert limited
    assert len(proposals) <= 3
    assert (
        "unsearched_alternatives"
        in actual.reserve_decision.joint.joint_rejection_reasons
    )
    assert actual.total_import_wh <= reference.total_import_wh + 1e-6


def test_local_construction_limit_never_claims_a_physical_impossibility(monkeypatch):
    monkeypatch.setattr("core.joint_dispatch.MAX_JOINT_PROPOSALS", 0)
    monkeypatch.setattr(
        "core.joint_dispatch.balanced_proposals", lambda *_: ((), False)
    )
    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 7), (7, 0, 0)],
        [0, 300, 0],
    )
    assert actual.flows == reference.flows
    summary = actual.reserve_decision.joint
    assert summary.joint_status == "search_limit"
    assert summary.joint_validations == 0
    assert {"construction_limit", "unsearched_alternatives"} <= set(
        summary.joint_rejection_reasons
    )


def test_pv_receiver_conversion_cannot_be_financed_by_worse_grid_ratio():
    from core.reserve_sources import net_draw

    config = plant()
    config = replace(
        config,
        battery=replace(config.battery, eta_charge=0.97, eta_discharge=0.97),
        charger=ConverterParams(1000, 0.95, 0),
        support=replace(
            config.support,
            native48_base_w=35,
            dc24_share=0.4,
            dcdc_eta=0.93,
            psu24_eta=0.89,
        ),
    )
    _, _, reference, before, options, _, actual = tiny_dispatch(
        [(0, 0, 100), (20, 0, 100), (80, 0, 0)],
        [0, 300, 0],
        custom_config=config,
    )
    donor = next(f for f in options[0] if f.support_dc24 and not f.inverter_limit_w)
    receiver = next(
        f for f in options[1] if not f.support_dc24 and not f.inverter_limit_w
    )
    donor_ratio = (donor.grid_import_wh - before[0].flow.grid_import_wh) / (
        net_draw(before[0].flow) - net_draw(donor)
    )
    receiver_ratio = (before[1].flow.grid_import_wh - receiver.grid_import_wh) / (
        net_draw(receiver) - net_draw(before[1].flow)
    )
    assert receiver_ratio < donor_ratio
    assert actual.flows == reference.flows


def test_short_receiver_phase_does_not_create_a_subthirty_minute_pair():
    from core.joint_balance import balanced_proposals

    _, _, _, before, options, _, _ = tiny_dispatch(
        [(0, 0, 7), (0, 0, 7), (7, 0, 0)],
        [0, 300, 0],
        subdivisions=12,
    )
    proposals, limited = balanced_proposals(before[:13], options[:13], [0] * 12 + [300])
    assert proposals == ()
    assert not limited


def test_expensive_donor_does_not_finance_a_cheaper_dc_receiver():
    _, _, reference, _, _, _, actual = tiny_dispatch(
        [(0, 0, 7), (0, 0, 7), (7, 0, 0)],
        [300, 0, 300],
    )
    assert actual.flows == reference.flows
    assert actual.reserve_decision.joint.joint_shifted_wh == 0
