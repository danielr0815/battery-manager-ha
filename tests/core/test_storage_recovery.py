"""Common storage targets share the useful-load priority and physical account."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta

import pytest
from core import HourSlot, PlanInputs, SurplusLoad, SurplusLoadState, SystemConfig, plan
from core.model import ConverterParams, LoadPlan, StorageDiagnostics
from core.storage import (
    effective_storage_config,
    required_dwell_energy,
    storage_diagnostics,
)

NOW = datetime(2026, 10, 27, 12)


def _config(**kwargs):
    # Exact export budgets in these fixtures exclude converter overheads.
    return SystemConfig(
        inverter=ConverterParams(eta=1, standby_power_w=0),
        charger=ConverterParams(eta=1, standby_power_w=0),
        **kwargs,
    )


def _inputs(*pv):
    return PlanInputs(
        NOW,
        95.0,
        tuple(
            HourSlot(
                index,
                NOW + timedelta(hours=index),
                1.0,
                (NOW.hour + index) % 24,
                energy,
                0.0,
                0.0,
            )
            for index, energy in enumerate(pv)
        ),
    )


def _storage(load_id="storage", **kwargs):
    values = {
        "energy_limited": True,
        "capacity_wh": 1000.0,
        "recovery_soc_percent": 50.0,
        **kwargs,
    }
    return SurplusLoad(load_id, load_id, 300.0, **values)


def test_disabled_minimum_is_a_neutral_constructor_default():
    load = SurplusLoad("old", "Old recording", 300, energy_limited=True)
    assert load.recovery_soc_percent is None
    assert load.eta_charge == 1.0
    assert load.max_charge_power_w is None
    config = _config(loads=(load,))
    assert effective_storage_config(config) == config
    result = plan(config, _inputs(0))
    assert result.load_plans[0].storage is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"eta_charge": 0},
        {"eta_charge": 1.1},
        {"max_charge_power_w": 0},
        {"recovery_soc_percent": 101},
        {"recovery_soc_percent": -1},
    ],
)
def test_storage_parameters_validate_physical_contract(kwargs):
    with pytest.raises(ValueError):
        _storage(**kwargs)


def test_all_storage_topups_follow_foreign_minimum_recovery():
    # Earlier storage without its own minimum cannot consume the last export
    # before a later storage has attempted its primary minimum task.
    optional = replace(_storage("optional"), recovery_soc_percent=None)
    minimum = _storage("minimum")
    config = _config(loads=(optional, minimum))
    inputs = replace(
        _inputs(300),
        load_states=(
            SurplusLoadState("optional", soc_percent=0),
            SurplusLoadState("minimum", soc_percent=0),
        ),
    )
    result = plan(config, inputs)
    by_id = {item.load_id: item for item in result.load_plans}
    assert by_id["minimum"].planned_energy_wh == pytest.approx(300)
    assert by_id["optional"].planned_energy_wh == 0
    assert by_id["minimum"].storage.recovery_planned_wh == pytest.approx(300)


@pytest.mark.parametrize("minimum_first", [False, True])
def test_primary_tasks_follow_configured_priority(minimum_first):
    minimum = _storage()
    useful = SurplusLoad("useful", "Useful", 300.0, battery_tolerance=0)
    config = _config(loads=(minimum, useful) if minimum_first else (useful, minimum))
    inputs = replace(
        _inputs(300), load_states=(SurplusLoadState("storage", soc_percent=0),)
    )
    result = plan(config, inputs)
    by_id = {item.load_id: item for item in result.load_plans}
    winner = "storage" if minimum_first else "useful"
    loser = "useful" if minimum_first else "storage"
    assert by_id[winner].planned_energy_wh == pytest.approx(300)
    assert by_id[loser].planned_energy_wh == 0


def test_charge_power_cap_and_efficiency_apply_to_standalone_storage():
    load = _storage(eta_charge=0.5, max_charge_power_w=100.0)
    inputs = replace(
        _inputs(1000), load_states=(SurplusLoadState("storage", soc_percent=0),)
    )
    result = plan(_config(loads=(load,)), inputs)
    item = result.load_plans[0]
    assert item.planned_energy_wh <= 100.0 + 1e-6
    assert item.storage.recovery_planned_wh == pytest.approx(item.planned_energy_wh)
    assert item.storage.day_end_energy_wh[-1][1] == pytest.approx(
        item.planned_energy_wh * 0.5
    )


def test_diagnostics_are_immutable_ac_totals_and_no_daily_energy_reset():
    load = _storage(eta_charge=0.5)
    config = _config(loads=(load,))
    inputs = replace(
        _inputs(*(0 for _ in range(25))),
        load_states=(SurplusLoadState("storage", soc_percent=40),),
    )
    base = plan(replace(config, loads=()), inputs)
    hours = (0.0,) * 11 + (1.0,) + (0.0,) * 12 + (1.0,)
    booked = LoadPlan("storage", tuple(bool(h) for h in hours), 600, run_hours=hours)
    result = storage_diagnostics(config, inputs, replace(base, load_plans=(booked,)))
    summary = result.load_plans[0].storage
    assert summary.initial_deficit_wh == 100
    assert summary.recovery_planned_wh == pytest.approx(200)
    assert summary.topup_planned_wh == pytest.approx(400)
    assert [energy for _, energy in summary.day_end_energy_wh] == [550, 700]
    assert summary.capacity_wh == 1000
    with pytest.raises(FrozenInstanceError):
        summary.capacity_wh = 1


def test_required_reference_contains_confirmed_dwell_only():
    load = _storage(eta_charge=0.5, max_charge_power_w=100)
    inputs = replace(
        _inputs(0, 0),
        load_states=(
            SurplusLoadState(
                "storage", soc_percent=0, minimum_run_until=NOW + timedelta(minutes=90)
            ),
        ),
    )
    projected = required_dwell_energy(_config(loads=(load,)), inputs)
    assert projected.recharge_required_ac_wh == (100, 50)
    assert required_dwell_energy(_config(loads=(load,)), projected) is projected
    optional = replace(
        inputs, load_states=(SurplusLoadState("storage", soc_percent=0),)
    )
    assert required_dwell_energy(_config(loads=(load,)), optional) is optional


def test_required_reference_caps_charge_gate_dwell_at_physical_target():
    load = _storage(gate_stop_capable=True)
    inputs = replace(
        _inputs(0, 0),
        load_states=(
            SurplusLoadState(
                "storage", soc_percent=95, minimum_run_until=NOW + timedelta(hours=2)
            ),
        ),
    )
    assert required_dwell_energy(
        _config(loads=(load,)), inputs
    ).recharge_required_ac_wh == (50, 0)


def test_storage_summary_empty_defaults_remain_replay_compatible():
    summary = StorageDiagnostics(50, 100, 0, 0)
    assert summary.capacity_wh == 0
    assert summary.day_end_energy_wh == ()
    assert summary.reasons == ()


def test_aux_above_minimum_does_not_create_phantom_recovery_headroom():
    from core.planning_rules import _cumulative_charge_caps

    # Initial 90 %, minimum 50 %: the first 400 stored Wh of discharge
    # merely spend existing surplus. The final 100 Wh become chargeable only
    # in the slot after the physical discharge, including across midnight.
    assert _cumulative_charge_caps(50, 200, 90, 1000, 0.5, [200, 200, 100, 0]) == (
        0,
        0,
        0,
        200,
    )


def test_earlier_minimum_retries_headroom_exposed_by_later_useful_block():
    """A later useful pass-3 task can reveal a prior minimum's PV opportunity."""
    now = NOW.replace(hour=6)
    storage = SurplusLoad(
        "storage",
        "Storage",
        500,
        energy_limited=True,
        capacity_wh=2000,
        recovery_soc_percent=50,
    )
    useful = SurplusLoad("useful", "Useful", 500, battery_tolerance=0)
    config = _config(loads=(storage, useful))
    inputs = PlanInputs(
        now,
        50,
        tuple(
            HourSlot(index, now + timedelta(hours=index), 1, 6 + index, pv, 0, 0)
            for index, pv in enumerate((1500, 1500, 1500, 700, 700, 700, 700))
        ),
        (SurplusLoadState("storage", soc_percent=20),),
    )
    result = plan(config, inputs)
    minimum, service = result.load_plans
    assert any(allocation[2] == 3 for allocation in service.allocations)
    assert minimum.planned_energy_wh > 0
    assert any("recovery" in reason for reason in minimum.reasons)
    assert result.grid_import_kwh == 0
    assert sum(minimum.run_hours) * 500 == pytest.approx(minimum.planned_energy_wh)


def test_minimum_target_never_resets_at_local_midnight():
    storage = _storage()
    inputs = replace(
        _inputs(*(1000 for _ in range(25))),
        load_states=(SurplusLoadState("storage", soc_percent=0),),
    )
    result = plan(_config(loads=(storage,)), inputs)
    item = result.load_plans[0]
    # The same capacity is charged once. Tomorrow cannot gain a fresh deficit.
    assert item.planned_energy_wh <= 1000 + 1e-6
    assert item.storage.recovery_planned_wh <= 500 + 1e-6
    assert item.storage.day_end_energy_wh[-1][1] <= 1000 + 1e-6


def test_final_quantum_cannot_cross_upper_charge_target():
    storage = SurplusLoad(
        "storage",
        "Storage",
        300,
        min_runtime_min=5,
        energy_limited=True,
        capacity_wh=2000,
        target_soc_percent=51,
        recovery_soc_percent=50,
        gate_stop_capable=True,
    )
    inputs = replace(
        _inputs(1000), load_states=(SurplusLoadState("storage", soc_percent=49.4),)
    )
    result = plan(_config(loads=(storage,)), inputs)
    # Only 32 stored Wh fit below the upper target, less than one new action.
    assert result.load_plans[0].planned_energy_wh == 0
    assert result.load_plans[0].storage.day_end_energy_wh[-1][1] == pytest.approx(988)


def test_storage_without_minimum_retains_original_source_rules_after_recovery():
    """An unrelated blocked minimum cannot remove existing preconditioning."""
    optional = replace(
        _storage("optional"),
        capacity_wh=4000,
        recovery_soc_percent=None,
        battery_tolerance=0.15,
        gate_stop_capable=False,
    )
    minimum = _storage("minimum")
    inputs = replace(
        _inputs(1500, 1500, 1500),
        start_soc_percent=50,
        load_states=(
            SurplusLoadState("optional", soc_percent=0),
            SurplusLoadState("minimum", available=False, soc_percent=0),
        ),
    )
    baseline = plan(_config(loads=(optional,)), inputs)
    combined = plan(_config(loads=(optional, minimum)), inputs)
    before = baseline.load_plans[0]
    after = combined.load_plans[0]
    assert before.planned_energy_wh > 0
    assert after.run_hours == before.run_hours
    assert after.planned_energy_wh == pytest.approx(before.planned_energy_wh)
    assert after.allocations == before.allocations


def test_future_recovery_is_pulled_into_today_after_later_pass3_opportunity():
    """A tomorrow booking is replaceable capacity, never today's satisfaction."""
    now = NOW.replace(hour=6)
    storage = SurplusLoad(
        "storage",
        "Storage",
        500,
        energy_limited=True,
        capacity_wh=2000,
        recovery_soc_percent=50,
    )
    useful = SurplusLoad("useful", "Useful", 500, battery_tolerance=0)
    config = _config(loads=(storage, useful))
    today = tuple(
        HourSlot(index, now + timedelta(hours=index), 1, 6 + index, pv, 0, 0)
        for index, pv in enumerate((1500, 1500, 1500, 700, 700, 700, 700))
    )
    tomorrow = tuple(
        HourSlot(
            len(today) + index,
            now + timedelta(days=1, hours=index),
            1,
            6 + index,
            1800,
            0,
            0,
        )
        for index in range(6)
    )
    inputs = PlanInputs(now, 50, today, (SurplusLoadState("storage", soc_percent=20),))
    short = plan(config, inputs).load_plans[0]
    long = plan(config, replace(inputs, slots=today + tomorrow)).load_plans[0]
    assert short.planned_energy_wh > 0
    assert sum(long.run_hours[: len(today)]) + 1e-6 >= sum(short.run_hours)
    assert long.planned_energy_wh <= 1600 + 1e-6


def test_only_terminal_moves_to_first_cascade_participant_not_member_recovery():
    from core import CascadeMember, LoadCascade

    b1, b2 = _storage("b1"), _storage("b2")
    external = _storage("external")
    terminal = SurplusLoad("terminal", "Terminal", 300)
    config = _config(
        loads=(b1, external, b2, terminal),
        cascades=(
            LoadCascade(
                "chain",
                (CascadeMember("b1", 20, 50), CascadeMember("b2", 20, 50)),
                "terminal",
            ),
        ),
    )
    inputs = replace(
        _inputs(300),
        load_states=(
            SurplusLoadState("b1", available=False, soc_percent=0),
            SurplusLoadState("external", soc_percent=0),
            SurplusLoadState("b2", soc_percent=0),
            SurplusLoadState("terminal", available=False),
        ),
    )
    result = plan(config, inputs)
    plans = {item.load_id: item for item in result.load_plans}
    assert plans["external"].planned_energy_wh == pytest.approx(300)
    assert plans["b2"].planned_energy_wh == 0
    assert tuple(item.load_id for item in result.load_plans) == (
        "b1",
        "external",
        "b2",
        "terminal",
    )


def test_plan_inputs_orders_aware_dst_fold_by_absolute_time():
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    first = datetime(2026, 10, 25, 2, tzinfo=berlin, fold=0)
    second = datetime(2026, 10, 25, 2, tzinfo=berlin, fold=1)
    slots = (HourSlot(0, first, 1, 2, 0, 0, 0), HourSlot(1, second, 1, 2, 0, 0, 0))
    assert PlanInputs(first, 50, slots).slots == slots
    with pytest.raises(ValueError, match="strictly ordered"):
        PlanInputs(second, 50, slots[::-1])


@pytest.mark.parametrize("recovery", [False, True])
def test_optional_allocation_cannot_bypass_failed_house_recharge_proof(
    monkeypatch, recovery
):
    """Nominally free PV is insufficient when the fixed house proof fails."""
    from core import allocation as allocation_core
    from core.simulate import simulate

    load = _storage() if recovery else SurplusLoad("useful", "Useful", 300)
    config = _config(loads=(load,))
    inputs = replace(
        _inputs(1000), load_states=(SurplusLoadState(load.load_id, soc_percent=0),)
    )
    baseline = simulate(config, inputs, 20)
    calls = []

    def reject_proof(actual_config, actual_inputs, reference, trial):
        calls.append((actual_config, actual_inputs, reference, trial))
        return False

    monkeypatch.setattr(allocation_core, "recharge_preserved", reject_proof)
    if recovery:
        plans, extra, result = (
            allocation_core._allocate_recovery_after_continuous_loads(
                config,
                inputs,
                20,
                [LoadPlan(load.load_id, (False,), 0, run_hours=(0,))],
                (0,),
                baseline,
                {load.load_id: 300},
                repeat_final_rounding=False,
            )
        )
    else:
        plans, extra, result = allocation_core.allocate_loads(
            config, inputs, 20, baseline
        )
    assert calls
    assert all(reference is baseline for _, _, reference, _ in calls)
    assert plans[0].planned_energy_wh == 0
    assert plans[0].rejected_candidates == ((0, "house_recharge"),)
    assert extra == (0,)
    assert result is baseline


def test_orphaned_confirmed_dwell_does_not_create_reference_consumption():
    """A state surviving config removal cannot invent power for a missing load."""
    inputs = replace(
        _inputs(0),
        load_states=(
            SurplusLoadState("removed", minimum_run_until=NOW + timedelta(hours=1)),
        ),
    )
    assert required_dwell_energy(_config(), inputs) is inputs
    assert inputs.recharge_required_ac_wh == ()


@pytest.mark.parametrize("required", [(1, 2), (-1,), (float("inf"),), (float("nan"),)])
def test_required_dwell_reference_rejects_unaligned_or_nonphysical_energy(required):
    with pytest.raises(ValueError, match="Required AC"):
        replace(_inputs(0), recharge_required_ac_wh=required)


def test_repacking_preserves_confirmed_dwell_and_removes_only_optional_recovery():
    """An earlier recovery opportunity cannot withdraw physical ON dwell."""
    from core.allocation import _withdraw_recovery_bookings

    config = _config(loads=(_storage(),))
    inputs = replace(
        _inputs(1000, 1000),
        load_states=(
            SurplusLoadState(
                "storage", soc_percent=0, minimum_run_until=NOW + timedelta(minutes=30)
            ),
        ),
    )
    confirmed = (0, 1, 0, 150.0)
    recovery = (1, 1, 1, 300.0)
    original = LoadPlan(
        "storage",
        (True, True),
        450,
        allocations=(confirmed, recovery),
        run_hours=(0.5, 1),
        reasons=(
            "confirmed minimum runtime",
            "pass 1: direct PV recovery after terminal plan",
        ),
    )
    updated, extra = _withdraw_recovery_bookings(
        config, inputs, [original], (150, 300), {"storage"}, None, ()
    )
    assert updated[0].run_hours == (0.5, 0)
    assert updated[0].schedule == (True, False)
    assert updated[0].allocations == (confirmed,)
    assert updated[0].reasons == ("confirmed minimum runtime",)
    assert updated[0].planned_energy_wh == 150
    assert extra == (150, 0)
    assert original.run_hours == (0.5, 1)  # Immutable accepted reference.


@pytest.mark.parametrize(
    "failure", ["candidate_budget_exhausted", "global_proof_failed"]
)
def test_failed_repacking_keeps_last_accepted_recovery_prefix_and_trajectory(
    monkeypatch, failure
):
    """Revalidation cannot erase accepted service or publish an unproved clone."""
    from core import allocation as allocation_core
    from core.simulate import simulate

    config = _config(loads=(_storage(),))
    inputs = replace(
        _inputs(1000), load_states=(SurplusLoadState("storage", soc_percent=0),)
    )
    baseline = simulate(config, inputs, 20)
    accepted_trials = []
    comparisons = []

    def proof(config, inputs, reference, trial):
        comparisons.append((reference, trial))
        if not accepted_trials:
            accepted_trials.append(trial)
            return True
        if failure == "candidate_budget_exhausted":
            return False
        # Each candidate is safe against its unbooked reference, but the
        # independent complete-plan proof rejects the replacement dispatch.
        return sum(flow.extra_ac_wh for flow in reference.flows) == 0

    monkeypatch.setattr(allocation_core, "recharge_preserved", proof)
    plans, extra, result = allocation_core.allocate_loads(
        config,
        inputs,
        20,
        baseline,
        remaining_energy_overrides={"storage": 300},
        recovery_targets_wh={"storage": 300},
    )
    assert len(comparisons) > 1
    assert result is accepted_trials[0]
    assert plans[0].schedule == (True,)
    assert plans[0].run_hours == (1,)
    assert plans[0].planned_energy_wh == 300
    assert extra == (300,)
    assert result.total_import_wh == 0
    if failure == "global_proof_failed":
        assert any(reference is accepted_trials[0] for reference, _ in comparisons)


def test_final_recovery_dwell_is_rejected_before_crossing_upper_storage_target():
    """A minimum-target rounding opportunity never licenses overcharging."""
    from core.allocation import _allocate_recovery_after_continuous_loads
    from core.simulate import simulate

    storage = SurplusLoad(
        "storage",
        "Storage",
        300,
        min_runtime_min=15,
        energy_limited=True,
        capacity_wh=2000,
        target_soc_percent=51,
        recovery_soc_percent=50,
    )
    config = _config(loads=(storage,))
    inputs = replace(
        _inputs(1000), load_states=(SurplusLoadState("storage", soc_percent=49.4),)
    )
    baseline = simulate(config, inputs, 20)
    original = LoadPlan("storage", (False,), 0, run_hours=(0,))
    # Recovery debt is 12 Wh, upper-target headroom 32 Wh. Even the smallest
    # permitted new action consumes 75 Wh AC and therefore must remain OFF.
    plans, extra, result = _allocate_recovery_after_continuous_loads(
        config,
        inputs,
        20,
        [original],
        (0,),
        baseline,
        {"storage": 12},
        allow_final_quantum_overshoot=True,
        repeat_final_rounding=False,
    )
    assert plans[0].schedule == original.schedule
    assert plans[0].planned_energy_wh == 0
    assert extra == (0,)
    assert result is baseline


@pytest.mark.parametrize(
    "rejection,house_priority",
    [
        ("house_recharge", True),
        ("house_recharge_search_limit", True),
        ("search_limit", False),
        ("same_day_export", False),
    ],
)
def test_unproved_recovery_is_unmet_without_claiming_physical_unreachability(
    rejection, house_priority
):
    """Missing proof, including a budget limit, cannot prove impossibility."""
    config = _config(loads=(_storage(),))
    inputs = replace(
        _inputs(0), load_states=(SurplusLoadState("storage", soc_percent=0),)
    )
    base = plan(_config(), inputs)
    uncharged = LoadPlan(
        "storage", (False,), 0, run_hours=(0,), rejected_candidates=((0, rejection),)
    )
    result = storage_diagnostics(config, inputs, replace(base, load_plans=(uncharged,)))
    item = result.load_plans[0]
    summary = item.storage
    assert summary.reasons == (
        (rejection, "recovery_target_unmet", "house_supply_priority")
        if house_priority
        else (rejection, "recovery_target_unmet")
    )
    assert "recovery_target_unreachable" not in summary.reasons
    assert item.schedule == uncharged.schedule
    assert item.allocations == uncharged.allocations
    assert item.planned_energy_wh == uncharged.planned_energy_wh
    assert summary.recovery_planned_wh == summary.topup_planned_wh == 0
    assert result.trajectory is base.trajectory


def test_satisfied_recovery_does_not_claim_an_unmet_target_from_an_earlier_veto():
    config = _config(loads=(_storage(),))
    inputs = replace(
        _inputs(0), load_states=(SurplusLoadState("storage", soc_percent=50),)
    )
    base = plan(_config(), inputs)
    completed = LoadPlan(
        "storage",
        (False,),
        0,
        run_hours=(0,),
        rejected_candidates=((0, "house_recharge"),),
    )
    result = storage_diagnostics(config, inputs, replace(base, load_plans=(completed,)))
    assert result.load_plans[0].storage.reasons == ("house_recharge",)


@pytest.mark.parametrize("minimum_soc", [None, 50.0])
def test_public_plan_preserves_available_dcdc_before_optional_storage_charging(
    minimum_soc,
):
    """Preserve existing DC supply; this test claims no new DC certificate."""
    from datetime import UTC

    from core.joint_dispatch import MAX_JOINT_VALIDATIONS
    from core.model import (
        BatteryParams,
        ConsumptionUncertaintyInterval,
        ConsumptionUncertaintySnapshot,
        MarketPrice,
        ReserveParams,
        SupportParams,
    )
    from core.reserve import simulate_fixed_dispatch
    from core.uncertainty import effective_uncertainty

    now = datetime(2026, 10, 28, 8, tzinfo=UTC)
    storage = _storage(recovery_soc_percent=minimum_soc)
    config = _config(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
        loads=(storage,),
    )
    # The house has genuine AC and 24-V demand. Only the surplus left after
    # that service can fund the empty optional storage. Lower PV still leaves
    # the already available DCDC service physically safe.
    series = ((0, 100, 100), (200, 50, 100), (300, 0, 0))
    slots = tuple(
        HourSlot(
            index,
            now + timedelta(hours=index),
            1,
            now.hour + index,
            pv,
            ac,
            dc,
            pv_p10_wh=pv * 0.9,
            pv_p90_wh=pv,
        )
        for index, (pv, ac, dc) in enumerate(series)
    )
    inputs = PlanInputs(
        now,
        95,
        slots,
        (SurplusLoadState("storage", soc_percent=0),),
        market_prices=tuple(
            MarketPrice(slot.start, slot.start + timedelta(hours=1), price)
            for slot, price in zip(slots, (300, 100, 0), strict=True)
        ),
        consumption_uncertainty=ConsumptionUncertaintySnapshot(
            (ConsumptionUncertaintyInterval(now, now + timedelta(hours=3), 15, 15),)
        ),
    )
    house_config = replace(config, loads=())
    house = plan(house_config, inputs)
    actual = plan(config, inputs)
    booking = actual.load_plans[0]
    house_dcdc = sum(flow.dcdc_input_wh for flow in house.trajectory.flows)
    actual_dcdc = sum(flow.dcdc_input_wh for flow in actual.trajectory.flows)
    assert house_dcdc > 0
    assert actual_dcdc == pytest.approx(house_dcdc)
    assert sum(
        flow.psu24_delivered_wh for flow in actual.trajectory.flows
    ) == pytest.approx(sum(flow.psu24_delivered_wh for flow in house.trajectory.flows))
    assert 0 < booking.planned_energy_wh < storage.capacity_wh
    assert booking.planned_energy_wh <= house.trajectory.total_export_wh + 1e-6
    assert sum(flow.extra_ac_wh for flow in actual.trajectory.flows) == pytest.approx(
        booking.planned_energy_wh
    )
    if minimum_soc is not None:
        assert booking.storage.initial_deficit_wh > booking.planned_energy_wh
        assert "recovery_target_unmet" in booking.storage.reasons
    # Verify the final market result's published physics, rather than merely
    # inspecting a pre-allocation switch flag or the existence of a certificate.
    joint = actual.trajectory.reserve_decision.joint
    assert joint is not None
    assert joint.joint_validations <= MAX_JOINT_VALIDATIONS
    assert joint.actual_grid_import_wh == pytest.approx(
        actual.trajectory.total_import_wh
    )
    assert joint.actual_grid_import_wh <= joint.reference_grid_import_wh + 1e-6
    assert actual.trajectory.total_import_wh <= house.trajectory.total_import_wh + 1e-6
    assert actual.trajectory.end_soc_percent == pytest.approx(
        house.trajectory.end_soc_percent
    )
    extra = tuple(flow.extra_ac_wh for flow in actual.trajectory.flows)
    replay = simulate_fixed_dispatch(config, inputs, extra, actual.trajectory)
    assert replay.total_import_wh == pytest.approx(actual.trajectory.total_import_wh)
    assert replay.end_soc_percent == pytest.approx(actual.trajectory.end_soc_percent)
    stress, _, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.control.upper_pv_reserve
    )
    assert any(scale < 1 for scale in stress)
    stressed = simulate_fixed_dispatch(
        config, inputs, extra, actual.trajectory, pv_scale=stress
    )
    house_stressed = simulate_fixed_dispatch(
        house_config, inputs, (0, 0, 0), house.trajectory, pv_scale=stress
    )
    for reference_flow, actual_flow in zip(
        house_stressed.flows, stressed.flows, strict=True
    ):
        assert actual_flow.dcdc_input_wh == pytest.approx(reference_flow.dcdc_input_wh)
        assert (
            actual_flow.psu24_delivered_wh <= reference_flow.psu24_delivered_wh + 1e-6
        )
        assert actual_flow.unserved_dc_wh <= reference_flow.unserved_dc_wh + 1e-6
    # Remove only optional consumption while preserving the ACTUAL published
    # source/INV decisions as well: an independently optimized house plan must
    # not conceal which dispatch caused any additional stress import.
    same_dispatch_house = simulate_fixed_dispatch(
        config, inputs, (0, 0, 0), actual.trajectory, pv_scale=stress
    )
    physical_evidence = tuple(
        (
            slot.pv_wh * stress[index],
            slot.ac_wh,
            extra[index],
            flow.inverter_on,
            flow.inverter_output_wh,
            flow.dcdc_input_wh,
            flow.psu24_delivered_wh,
            flow.grid_import_wh,
            same_dispatch_house.flows[index].grid_import_wh,
        )
        for index, (slot, flow) in enumerate(zip(slots, stressed.flows, strict=True))
    )
    assert stressed.total_import_wh <= same_dispatch_house.total_import_wh + 1e-6, (
        physical_evidence
    )
    assert stressed.total_import_wh <= house_stressed.total_import_wh + 1e-6, (
        physical_evidence
    )
    assert stressed.min_soc_percent > config.control.inverter_min_soc_percent


def test_confirmed_dwell_survives_an_actually_exhausted_shared_optional_proof_budget():
    """Two exogenous commitments remain booked after optional proof is spent."""
    from datetime import UTC

    from core import reserve as reserve_core
    from core.joint_dispatch import MAX_JOINT_VALIDATIONS
    from core.model import (
        BatteryParams,
        ConsumptionUncertaintyInterval,
        ConsumptionUncertaintySnapshot,
        ReserveParams,
        SupportParams,
    )
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.simulate import simulate

    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    loads = (SurplusLoad("first", "First", 120), SurplusLoad("second", "Second", 180))
    config = _config(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
        loads=loads,
    )
    inputs = PlanInputs(
        now,
        95,
        (HourSlot(0, now, 1, 12, 1000, 50, 100, pv_p10_wh=900, pv_p90_wh=1000),),
        tuple(
            SurplusLoadState(
                load.load_id, minimum_run_until=now + timedelta(minutes=minutes)
            )
            for load, minutes in zip(loads, (10, 20), strict=True)
        ),
        consumption_uncertainty=ConsumptionUncertaintySnapshot(
            (ConsumptionUncertaintyInterval(now, now + timedelta(hours=1), 5, 5),)
        ),
    )
    inputs = required_dwell_energy(config, inputs)
    assert inputs.recharge_required_ac_wh == (80,)
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        assert reference.flows[0].extra_ac_wh == pytest.approx(80)
        outcomes = []
        for index in range(MAX_JOINT_VALIDATIONS):
            # Distinct, physically PV-covered optional probes consume the real
            # owning proof ledger; no counter, reference or proof is mocked.
            trial = simulate_fixed_dispatch(
                config, inputs, (80 + (index + 1) / 100,), reference
            )
            outcomes.append(recharge_preserved(config, inputs, reference, trial))
        cache = reserve_core._reserve_cache.get()
        assert cache is not None
        assert any(outcomes) and not outcomes[-1]
        assert cache.validation_budget.used == MAX_JOINT_VALIDATIONS
        actual = plan(config, inputs)
        # Confirmed first/second partial projections differ from the combined
        # reference. Neither may be discarded as a new optional proof request.
        assert cache.validation_budget.used == MAX_JOINT_VALIDATIONS
    assert [item.run_hours for item in actual.load_plans] == [(1 / 6,), (1 / 3,)]
    assert [item.planned_energy_wh for item in actual.load_plans] == pytest.approx(
        [20, 60]
    )
    assert all(item.allocations[0][2] == 0 for item in actual.load_plans)
    assert all(
        item.reasons == ("confirmed minimum runtime",) for item in actual.load_plans
    )
    assert actual.trajectory.flows[0].extra_ac_wh == pytest.approx(80)
    assert actual.trajectory.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.trajectory.end_soc_percent == pytest.approx(reference.end_soc_percent)


@pytest.mark.parametrize(
    "eta,max_charge,minutes,expected_ac,expected_hours",
    [
        (1.0, None, 60, 50.0, 0.25),
        (0.8, 100.0, 120, 62.5, 0.625),
    ],
)
def test_confirmed_storage_dwell_keeps_physical_gate_target_with_exhausted_budget(
    eta,
    max_charge,
    minutes,
    expected_ac,
    expected_hours,
):
    """Optional quantum policy cannot erase the actual upper-target stop."""
    from datetime import UTC

    from core import reserve as reserve_core
    from core.joint_dispatch import MAX_JOINT_VALIDATIONS
    from core.model import (
        BatteryParams,
        ConsumptionUncertaintyInterval,
        ConsumptionUncertaintySnapshot,
        ReserveParams,
        SupportParams,
    )
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.simulate import simulate

    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    storage = SurplusLoad(
        "storage",
        "Storage",
        200,
        energy_limited=True,
        capacity_wh=1000,
        target_soc_percent=100,
        recovery_soc_percent=50,
        gate_stop_capable=True,
        eta_charge=eta,
        max_charge_power_w=max_charge,
    )
    config = _config(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
        loads=(storage,),
    )
    inputs = PlanInputs(
        now,
        95,
        tuple(
            HourSlot(
                index,
                now + timedelta(hours=index),
                1,
                12 + index,
                1000,
                50,
                100,
                pv_p10_wh=900,
                pv_p90_wh=1000,
            )
            for index in range(2)
        ),
        (
            SurplusLoadState(
                "storage",
                soc_percent=95,
                minimum_run_until=now + timedelta(minutes=minutes),
            ),
        ),
        consumption_uncertainty=ConsumptionUncertaintySnapshot(
            (ConsumptionUncertaintyInterval(now, now + timedelta(hours=2), 5, 5),)
        ),
    )
    inputs = required_dwell_energy(config, inputs)
    assert inputs.recharge_required_ac_wh == (expected_ac, 0)
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        outcomes = []
        for index in range(MAX_JOINT_VALIDATIONS):
            trial = simulate_fixed_dispatch(
                config, inputs, (expected_ac + (index + 1) / 100, 0), reference
            )
            outcomes.append(recharge_preserved(config, inputs, reference, trial))
        cache = reserve_core._reserve_cache.get()
        assert cache is not None
        assert any(outcomes) and not outcomes[-1]
        assert cache.validation_budget.used == MAX_JOINT_VALIDATIONS
        actual = plan(config, inputs)
        assert cache.validation_budget.used == MAX_JOINT_VALIDATIONS
    booked = actual.load_plans[0]
    assert booked.run_hours == (expected_hours, 0)
    assert booked.planned_energy_wh == pytest.approx(expected_ac)
    assert len(booked.allocations) == 1
    assert booked.allocations[0][:3] == (0, 1, 0)
    assert booked.allocations[0][3] == pytest.approx(expected_ac)
    assert booked.reasons == ("confirmed minimum runtime",)
    assert tuple(flow.extra_ac_wh for flow in actual.trajectory.flows) == pytest.approx(
        inputs.recharge_required_ac_wh
    )
    assert booked.storage.day_end_energy_wh[-1][1] == pytest.approx(1000)
    assert all(energy <= 1000 + 1e-6 for _, energy in booked.storage.day_end_energy_wh)
    assert (
        booked.storage.recovery_planned_wh + booked.storage.topup_planned_wh
        == pytest.approx(expected_ac)
    )


@pytest.mark.parametrize(
    "power,minimum,duration,soc,starts",
    [
        (6, 5, 1 / 12, 0, False),  # Original 0.5-Wh / five-minute regression.
        (100, 5, 1, 96, False),  # A 40-Wh gate-stop quantum is still a NEW start.
        (600, 5, 0.1, 0, False),  # 60 Wh cannot excuse only six available minutes.
        (600, 5, 1 / 6, 0, False),  # A 100-Wh ten-minute NEW start is forbidden.
        (200, 30, 0.25, 0, False),  # Configured dwell remains the stronger minimum.
        (200, 5, 0.25, 0, True),  # Both lower boundaries are met exactly.
        (100, 5, 0.5, 0, True),  # A physically available 50-Wh source is retained.
    ],
)
def test_new_standalone_storage_without_recovery_obeys_time_and_ac_energy_minima(
    power,
    minimum,
    duration,
    soc,
    starts,
):
    storage = SurplusLoad(
        "storage",
        "Storage",
        power,
        min_runtime_min=minimum,
        energy_limited=True,
        capacity_wh=1000,
        gate_stop_capable=True,
    )
    inputs = PlanInputs(
        NOW,
        95,
        (HourSlot(0, NOW, duration, NOW.hour, 1000 * duration, 0, 0),),
        (SurplusLoadState("storage", soc_percent=soc),),
    )
    result = plan(_config(loads=(storage,)), inputs)
    booking = result.load_plans[0]
    assert bool(booking.planned_energy_wh) is starts
    if starts:
        assert sum(booking.run_hours) + 1e-6 >= max(15, minimum) / 60
        assert booking.planned_energy_wh >= 50 - 1e-6
    else:
        assert booking.run_hours == (0,)
        assert result.trajectory.flows[0].extra_ac_wh == 0


@pytest.mark.parametrize("minimum_soc", [None, 50])
def test_observed_ongoing_charge_can_finish_at_gate_after_physical_dwell_expired(
    minimum_soc,
):
    storage = SurplusLoad(
        "storage",
        "Storage",
        200,
        min_runtime_min=5,
        energy_limited=True,
        capacity_wh=1000,
        gate_stop_capable=True,
        recovery_soc_percent=minimum_soc,
    )
    state = SurplusLoadState(
        "storage", soc_percent=98, on=True, minimum_run_until=NOW - timedelta(minutes=1)
    )
    inputs = replace(_inputs(1000), load_states=(state,))
    result = plan(_config(loads=(storage,)), inputs)
    booking = result.load_plans[0]
    assert booking.planned_energy_wh == pytest.approx(20)
    assert booking.run_hours == pytest.approx((0.1,))
    assert inputs.load_states[0].minimum_run_until == NOW - timedelta(minutes=1)
    assert not any(allocation[2] == 0 for allocation in booking.allocations)
    assert result.trajectory.flows[0].extra_ac_wh == pytest.approx(20)
    if minimum_soc is not None:
        assert booking.storage.day_end_energy_wh[-1][1] == pytest.approx(1000)


@pytest.mark.parametrize("minimum_soc", [None, 50])
def test_observed_ongoing_charge_does_not_exempt_a_later_restart_from_minima(
    minimum_soc,
):
    storage = SurplusLoad(
        "storage",
        "Storage",
        200,
        min_runtime_min=5,
        energy_limited=True,
        capacity_wh=1000,
        gate_stop_capable=True,
        recovery_soc_percent=minimum_soc,
    )
    inputs = replace(
        _inputs(0, 1000),
        load_states=(
            SurplusLoadState(
                "storage",
                soc_percent=98,
                on=True,
                minimum_run_until=NOW - timedelta(minutes=1),
            ),
        ),
    )
    result = plan(_config(loads=(storage,)), inputs)
    booking = result.load_plans[0]
    assert booking.planned_energy_wh == 0
    assert booking.run_hours == (0, 0)
    assert all(flow.extra_ac_wh == 0 for flow in result.trajectory.flows)


@pytest.mark.parametrize("untrusted_on", ["on", "false", 1, None])
def test_physical_charge_path_state_requires_boolean_evidence(untrusted_on):
    with pytest.raises(ValueError, match="on must be a bool"):
        SurplusLoadState("storage", on=untrusted_on)
    assert SurplusLoadState("old recording").on is False


@pytest.mark.parametrize("overlapping", [False, True])
def test_confirmed_cascade_dwell_reference_includes_union_of_capped_path_losses(
    overlapping,
):
    """Each physical output is funded once before optional proof is exhausted."""
    from datetime import UTC

    from core import reserve as reserve_core
    from core.joint_dispatch import MAX_JOINT_VALIDATIONS
    from core.model import (
        BatteryParams,
        CascadeMember,
        ConsumptionUncertaintyInterval,
        ConsumptionUncertaintySnapshot,
        LoadCascade,
        ReserveParams,
        SupportParams,
    )
    from core.reserve import (
        recharge_preserved,
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.simulate import simulate

    now = datetime(2026, 10, 29, 12, tzinfo=UTC)
    root = SurplusLoad(
        "root",
        "Root",
        200,
        energy_limited=True,
        capacity_wh=1000,
        recovery_soc_percent=50,
        gate_stop_capable=True,
    )
    terminal = SurplusLoad("terminal", "Terminal", 100)
    loads = [root]
    members = [CascadeMember("root", 20, 50, output_overhead_w=10)]
    states = [SurplusLoadState("root", soc_percent=20, soc_observed_at=now)]
    if overlapping:
        leaf = SurplusLoad(
            "leaf",
            "Leaf",
            200,
            energy_limited=True,
            capacity_wh=1000,
            recovery_soc_percent=50,
            gate_stop_capable=True,
            eta_charge=0.8,
            max_charge_power_w=100,
        )
        loads.append(leaf)
        members.append(
            CascadeMember(
                "leaf",
                20,
                50,
                eta_charge=0.8,
                max_charge_power_w=100,
                output_overhead_w=5,
            )
        )
        states.append(
            SurplusLoadState(
                "leaf",
                soc_percent=96,
                soc_observed_at=now,
                minimum_run_until=now + timedelta(hours=1),
            )
        )
    loads.append(terminal)
    states.append(
        SurplusLoadState(
            "terminal",
            minimum_run_until=now + timedelta(minutes=45 if overlapping else 60),
        )
    )
    config = _config(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
        loads=tuple(loads),
        cascades=(LoadCascade("chain", tuple(members), "terminal"),),
    )
    inputs = PlanInputs(
        now,
        95,
        (HourSlot(0, now, 1, 12, 1000, 50, 100, pv_p10_wh=900, pv_p90_wh=1000),),
        tuple(states),
        consumption_uncertainty=ConsumptionUncertaintySnapshot(
            (ConsumptionUncertaintyInterval(now, now + timedelta(hours=1), 5, 5),)
        ),
    )
    inputs = required_dwell_energy(config, inputs)
    # Single path: terminal100Wh + root-output10Wh.
    # Overlap: terminal75Wh + leaf50WhAC (40Whstored / .8), root-output
    # max(.75h,.5h)*10W=7.5Wh, leaf-output .75h*5W=3.75Wh.
    expected = 136.25 if overlapping else 110.0
    assert inputs.recharge_required_ac_wh == pytest.approx((expected,))
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        reference = simulate(config, inputs, 20)
        assert reference.flows[0].extra_ac_wh == pytest.approx(expected)
        outcomes = []
        for index in range(MAX_JOINT_VALIDATIONS):
            trial = simulate_fixed_dispatch(
                config, inputs, (expected + (index + 1) / 100,), reference
            )
            outcomes.append(recharge_preserved(config, inputs, reference, trial))
        cache = reserve_core._reserve_cache.get()
        assert cache is not None
        assert any(outcomes) and not outcomes[-1]
        assert cache.validation_budget.used == MAX_JOINT_VALIDATIONS
        result = plan(config, inputs)
        assert cache.validation_budget.used == MAX_JOINT_VALIDATIONS
    by_id = {item.load_id: item for item in result.load_plans}
    assert by_id["terminal"].run_hours == (0.75 if overlapping else 1.0,)
    if overlapping:
        assert by_id["leaf"].run_hours == (0.5,)
        assert by_id["leaf"].planned_energy_wh == pytest.approx(50)
        assert by_id["leaf"].storage.day_end_energy_wh[-1][1] == pytest.approx(1000)
    assert result.trajectory.flows[0].extra_ac_wh == pytest.approx(expected)
    assert result.cascade_plans[0].planned_root_energy_wh == pytest.approx(expected)
    assert result.trajectory.total_import_wh == pytest.approx(reference.total_import_wh)
    assert result.trajectory.end_soc_percent == pytest.approx(reference.end_soc_percent)


def test_public_plan_keeps_legal_forty_minute_charge_on_five_minute_search_raster():
    """Public synthetic reproduction of the 11:03 / 12:00 raster regression.

    The first action ends well before noon. A second 40-minute / 200.67-Wh
    action is a legal new start; the 15-minute lower bound cannot remove that
    candidate merely because 40 is not divisible by 15.
    """
    from core.model import BatteryParams

    now = NOW.replace(hour=11, minute=3, second=20)
    noon = now.replace(hour=12, minute=0, second=0)
    power = 301.0
    storage = SurplusLoad(
        "storage",
        "Storage",
        300,
        battery_tolerance=0.05,
        min_runtime_min=5,
        min_off_min=5,
        energy_limited=True,
        capacity_wh=2000,
        target_soc_percent=90,
        gate_stop_capable=True,
    )
    config = _config(battery=BatteryParams(100, 5, 95, 1, 1), loads=(storage,))
    first_hours = (noon - now).total_seconds() / 3600
    inputs = PlanInputs(
        now,
        90,
        (
            HourSlot(0, now, first_hours, 11, 5 + power * 0.25, 0, 0),
            HourSlot(1, noon, 1, 12, power * 2 / 3, 0, 0),
        ),
        (SurplusLoadState("storage", soc_percent=60.2, learned_power_w=power),),
    )
    fine = plan(config, inputs)
    deliberately_coarse = plan(
        replace(config, loads=(replace(storage, min_runtime_min=15),)), inputs
    )
    assert fine.load_plans[0].run_hours == pytest.approx((0.25, 2 / 3))
    assert deliberately_coarse.load_plans[0].run_hours == pytest.approx((0.25, 0.5))
    assert fine.load_plans[0].planned_energy_wh == pytest.approx(power * (0.25 + 2 / 3))
    assert fine.trajectory.total_import_wh == pytest.approx(
        deliberately_coarse.trajectory.total_import_wh
    )
    assert fine.trajectory.min_soc_percent == pytest.approx(
        deliberately_coarse.trajectory.min_soc_percent
    )
    assert fine.trajectory.end_soc_percent == pytest.approx(
        deliberately_coarse.trajectory.end_soc_percent
    )
    assert (
        deliberately_coarse.trajectory.total_export_wh - fine.trajectory.total_export_wh
        == pytest.approx(power / 6)
    )
    assert all(hours >= 0.25 for hours in fine.load_plans[0].run_hours)
    assert all(power * hours >= 50 for hours in fine.load_plans[0].run_hours)
    assert now + timedelta(minutes=15) < noon  # Genuine second start, not continuation.


def test_new_storage_action_can_span_three_five_minute_forecast_slots():
    """Keep a fine raster while constructing one valid 15-minute action."""
    storage = SurplusLoad(
        "storage",
        "Storage",
        200,
        min_runtime_min=5,
        energy_limited=True,
        capacity_wh=1000,
    )
    inputs = PlanInputs(
        NOW,
        95,
        tuple(
            HourSlot(
                index,
                NOW + timedelta(minutes=5 * index),
                1 / 12,
                NOW.hour,
                1000 / 12,
                0,
                0,
            )
            for index in range(3)
        ),
        (SurplusLoadState("storage", soc_percent=0),),
    )
    result = plan(_config(loads=(storage,)), inputs)
    booking = result.load_plans[0]
    assert booking.run_hours == pytest.approx((1 / 12, 1 / 12, 1 / 12))
    assert booking.planned_energy_wh == pytest.approx(50)
    assert len(booking.allocations) == 1
    assert booking.allocations[0][:3] == (0, 3, 1)
    assert result.trajectory.total_import_wh == 0
