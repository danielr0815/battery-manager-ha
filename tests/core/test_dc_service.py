"""DC outages are safety evidence, independent of SOC floors and import slack."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.dc_service import (
    dc_service_regression_wh,
    first_dc_service_regression,
    preserves_dc_service,
)
from core.model import (
    Appliance,
    BatteryParams,
    ControlParams,
    ConverterParams,
    DCDeficitInterval,
    FeedInParams,
    HourSlot,
    PlanInputs,
    SupportParams,
    SurplusLoad,
    SystemConfig,
    Trajectory,
)
from core.optimize import plan
from core.simulate import simulate

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


def plant(**kwargs):
    return SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        support=SupportParams(
            configured=True,
            coordinated=True,
            dc24_available=False,
            dc48_available=False,
        ),
        **kwargs,
    )


def inputs(values, soc=30):
    return PlanInputs(
        NOW,
        soc,
        tuple(
            HourSlot(i, NOW + timedelta(hours=i), 1, 12 + i, *value)
            for i, value in enumerate(values)
        ),
    )


def evidence(values, *, fine=False):
    flows = simulate(plant(), inputs([(0, 0, 0)] * len(values)), 100).flows
    return Trajectory(
        tuple(
            replace(
                flow,
                unserved_dc_wh=value,
                dc_deficit_intervals=(
                    DCDeficitInterval(
                        NOW + timedelta(hours=i), NOW + timedelta(hours=i + 1), value
                    ),
                )
                if fine and value
                else ()
                if fine
                else None,
            )
            for i, (flow, value) in enumerate(zip(flows, values, strict=True))
        ),
        0,
        0,
        30,
    )


@pytest.mark.parametrize(
    "baseline,trial,accepted,allowed",
    [
        ((10, 5), (10, 5), None, True),
        ((10, 5), (5, 0), None, True),
        ((0, 0), (0, 1), None, False),
        ((0, 10), (10, 0), None, False),
        ((10, 0), (0, 10), None, False),
        ((5, 5), (4, 6), None, False),
        ((10, 5), (4, 0), (5, 0), True),
        ((10, 5), (6, 0), (5, 0), False),
        ((0, 0), (0.2e-6, 0.2e-6), None, True),
        ((0, 0), (0.75e-6, 0.75e-6), None, False),
    ],
)
@pytest.mark.parametrize("fine", [False, True])
def test_one_horizon_tolerance_preserves_existing_and_accepted_service(
    baseline, trial, accepted, allowed, fine
):
    base, candidate = evidence(baseline, fine=fine), evidence(trial, fine=fine)
    accepted = evidence(accepted, fine=fine) if accepted is not None else None
    assert preserves_dc_service(candidate, baseline=base, accepted=accepted) is allowed
    assert preserves_dc_service(
        candidate, baseline=base, accepted=base
    ) is preserves_dc_service(candidate, baseline=base)
    assert (first_dc_service_regression(base, candidate) is None) is (
        dc_service_regression_wh(base, candidate) <= 1e-6
    )


def test_five_minute_shift_is_visible_even_when_hourly_totals_and_prefixes_match():
    base = evidence((10,), fine=True)
    first = DCDeficitInterval(NOW, NOW + timedelta(minutes=5), 10)
    later = DCDeficitInterval(
        NOW + timedelta(minutes=5), NOW + timedelta(minutes=10), 10
    )
    base = replace(base, flows=(replace(base.flows[0], dc_deficit_intervals=(first,)),))
    trial = replace(
        base, flows=(replace(base.flows[0], dc_deficit_intervals=(later,)),)
    )
    assert base.flows[0].unserved_dc_wh == trial.flows[0].unserved_dc_wh
    assert dc_service_regression_wh(base, trial) == pytest.approx(10)
    assert not preserves_dc_service(trial, baseline=base)
    assert not preserves_dc_service(base, baseline=trial)
    with pytest.raises(ValueError):
        dc_service_regression_wh(base, replace(trial, flows=()))


def test_deficit_interval_validation_and_dst_fold_compare_real_time():
    zone = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 25, 0, 55, tzinfo=UTC).astimezone(zone)
    end = datetime(2026, 10, 25, 1, 5, tzinfo=UTC).astimezone(zone)
    interval = DCDeficitInterval(start, end, 5)
    base = evidence((5,), fine=True)
    trial = replace(
        base, flows=(replace(base.flows[0], dc_deficit_intervals=(interval,)),)
    )
    assert dc_service_regression_wh(trial, trial) == 0
    for a, b, wh in [
        (NOW, NOW, 1),
        (NOW, NOW + timedelta(hours=1), -1),
        (NOW, NOW + timedelta(hours=1), float("inf")),
        (NOW, NOW.replace(tzinfo=None) + timedelta(hours=1), 1),
    ]:
        with pytest.raises(ValueError):
            DCDeficitInterval(a, b, wh)


def test_full_plan_rejects_load_despite_zero_import_and_identical_floor_soc():
    config = plant(loads=(SurplusLoad("load", "Load", 1000),))
    config = replace(
        config, charger=ConverterParams(100, 1, 0), inverter=ConverterParams(2000, 1, 0)
    )
    source = inputs([(1000, 0, 0), (0, 0, 850)], 80)
    baseline = simulate(config, source, 20)
    unsafe = simulate(config, source, 20, extra_ac_wh=(1000, 0))
    assert baseline.min_soc_percent == pytest.approx(5)
    assert unsafe.min_soc_percent == pytest.approx(5)
    assert baseline.total_import_wh == unsafe.total_import_wh == 0
    assert dc_service_regression_wh(baseline, unsafe) == pytest.approx(100)
    result = plan(config, source)
    # The unsafe full hour is rejected; the allocator can still use 30 min
    # of genuine PV surplus without losing the capped 100 Wh battery recharge.
    assert result.load_plans[0].planned_energy_wh == 500
    assert result.load_plans[0].rejected_candidates == ((0, "dc_service"),)
    assert preserves_dc_service(result.trajectory, baseline=baseline)


def test_full_advisor_rejects_unserved_dc_when_import_and_soc_gates_pass():
    config = plant(appliances=(Appliance("washer", "Washer", 100, 1, True),))
    source = inputs([(100, 0, 50), (0, 0, 300)])
    result = plan(config, source)
    assert not result.appliance_windows["washer"]
    assert result.appliance_advisories["washer"].reasons == ("dc_service",)


@pytest.mark.parametrize("alpha", [1, 0.8])
def test_full_automatic_feedin_does_not_displace_next_dc_service(alpha):
    config = plant(
        control=ControlParams(predrain_pv_confidence=alpha),
        feedin=FeedInParams(
            enabled=True, max_w=1000, min_soc_percent=0, deadline_hour=14
        ),
    )
    config = replace(config, charger=ConverterParams(3000, 1, 0))
    source = inputs([(100, 0, 0), (0, 0, 350), (2000, 0, 0)])
    baseline = simulate(config, source, 20)
    result = plan(config, source)
    assert result.feedin_schedule_w == (0, 0, 0)
    assert (0, "dc_service") in result.feedin_decisions
    assert preserves_dc_service(result.trajectory, baseline=baseline)


@pytest.mark.parametrize("reserve", [False, True])
def test_coordinated_simulations_retain_atomic_deficit_wh_and_time(reserve):
    config = plant()
    config = replace(config, reserve=replace(config.reserve, enabled=reserve))
    result = simulate(config, inputs([(0, 0, 400)], 5), 100)
    intervals = result.flows[0].dc_deficit_intervals
    assert intervals and len(intervals) == 12
    assert all((part.end - part.start).total_seconds() == 300 for part in intervals)
    assert sum(part.unserved_dc_wh for part in intervals) == pytest.approx(
        result.flows[0].unserved_dc_wh
    )
    assert intervals[0].start == NOW and intervals[-1].end == NOW + timedelta(hours=1)


def _additional_outage(trajectory):
    return replace(
        trajectory,
        flows=(
            replace(
                trajectory.flows[0],
                unserved_dc_wh=trajectory.flows[0].unserved_dc_wh + 1,
                dc_deficit_intervals=None,
            ),
            *trajectory.flows[1:],
        ),
    )


def test_allocator_checks_dc_outages_in_stress_even_with_unchanged_soc(monkeypatch):
    from core import allocation
    from test_allocation_opportunity import scenario

    config, source = scenario([(20, 100), (20, 100), (2200, 100)], 90, beta=1.2)
    config = replace(
        config, control=replace(config.control, predrain_pv_confidence=0.8)
    )

    def measured_simulation(*args, **kwargs):
        trajectory = simulate(*args, **kwargs)
        return (
            _additional_outage(trajectory)
            if kwargs.get("pv_scale") is not None
            and sum(kwargs.get("extra_ac_wh") or ())
            else trajectory
        )

    monkeypatch.setattr(allocation, "simulate", measured_simulation)
    baseline = simulate(config, source, 20)
    plans, *_ = allocation.allocate_loads(config, source, 20, baseline)
    assert "dc_service" in dict(plans[0].rejected_candidates).values()


def test_recovery_must_not_spend_dc_service_to_use_export(monkeypatch):
    from core import allocation
    from core.model import LoadPlan

    config = plant(loads=(SurplusLoad("storage", "Storage", 100),))
    source = inputs([(200, 0, 0)], 95)
    baseline = simulate(config, source, 20)

    def measured_simulation(*args, **kwargs):
        trajectory = simulate(*args, **kwargs)
        return (
            _additional_outage(trajectory)
            if sum(kwargs.get("extra_ac_wh") or ())
            else trajectory
        )

    monkeypatch.setattr(allocation, "simulate", measured_simulation)
    plans, _, trajectory = allocation._allocate_recovery_after_continuous_loads(
        config,
        source,
        20,
        [LoadPlan("storage", (False,), 0)],
        (0,),
        baseline,
        {"storage": 100},
    )
    assert plans[0].planned_energy_wh == 0
    assert dict(plans[0].rejected_candidates)[0] == "dc_service"
    assert trajectory == baseline


def test_cascade_replan_cannot_create_a_dc_outage(monkeypatch):
    from core import cascade
    from test_cascade import _system

    config, source = _system()
    baseline = plan(replace(config, cascades=()), source)
    allocation = (
        (cascade.CascadeSourceSegment(0, 0, 0.5, "aux", "b1", False, 150),),
        {"b1": 82.5},
        False,
    )
    monkeypatch.setattr(cascade, "_allocate_aux_now", lambda *a, **kw: allocation)
    damaged = replace(baseline, trajectory=_additional_outage(baseline.trajectory))
    result = cascade.augment_cascade_plans(config, source, baseline, lambda *_: damaged)
    assert result.cascade_plans[0].planned_aux_energy_wh == 0
    assert result.trajectory == baseline.trajectory


def test_automatic_feedin_stress_rollback_preserves_dc_service(monkeypatch):
    from core import optimize

    config = plant(
        control=ControlParams(predrain_pv_confidence=0.8),
        feedin=FeedInParams(
            enabled=True, max_w=1000, min_soc_percent=0, deadline_hour=14
        ),
    )
    config = replace(config, charger=ConverterParams(3000, 1, 0))
    source = inputs([(2000, 0, 0), (0, 0, 0)], 80)

    def measured_simulation(*args, **kwargs):
        trajectory = simulate(*args, **kwargs)
        return (
            _additional_outage(trajectory)
            if kwargs.get("pv_scale") is not None and sum(kwargs.get("feedin_wh") or ())
            else trajectory
        )

    monkeypatch.setattr(optimize, "simulate", measured_simulation)
    result = optimize.plan(config, source)
    assert (0, "dc_service") in result.feedin_decisions
    assert result.feedin_schedule_w == (0, 0)


def test_stress_keeps_manual_export_and_rolls_back_anticipatory_automatic_export(
    monkeypatch,
):
    from core import optimize

    config = plant(
        control=ControlParams(predrain_pv_confidence=0.8),
        feedin=FeedInParams(
            enabled=True, max_w=1000, min_soc_percent=0, deadline_hour=14, manual_w=100
        ),
    )
    config = replace(config, charger=ConverterParams(3000, 1, 0))
    source = inputs([(2000, 0, 0), (0, 0, 300), (2000, 0, 0)], 80)
    source = replace(
        source,
        slots=(
            source.slots[0],
            replace(source.slots[1], start=NOW + timedelta(hours=6), hour_of_day=18),
            replace(source.slots[2], start=NOW + timedelta(days=1), hour_of_day=12),
        ),
    )

    def measured_simulation(*args, **kwargs):
        trajectory = simulate(*args, **kwargs)
        exports = kwargs.get("feedin_wh") or ()
        return (
            _additional_outage(trajectory)
            if kwargs.get("pv_scale") is not None
            and len(exports) == 3
            and exports[2] > 0
            else trajectory
        )

    monkeypatch.setattr(optimize, "simulate", measured_simulation)
    result = optimize.plan(config, source)
    assert result.feedin_schedule_w[0] == 100
    assert result.feedin_schedule_w[2] == 0
    assert (2, "dc_service") in result.feedin_decisions
