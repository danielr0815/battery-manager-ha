"""Joint dispatch proves total-grid and fixed-window energy, not DC isolation."""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.joint_dispatch import actual_prices, price_slices, uncertainty_failure
from core.model import (
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    MarketPrice,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
    Trajectory,
)
from core.reserve import _simulate_reserve_reference, source_schedule_mode
from core.reserve_sources import SourceStep, grid_dc
from core.simulate import simulate, step_hour

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def plant():
    return SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        control=ControlParams(soc_buffer_percent=0),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
    )


def forecast(series, prices, start_soc=95):
    slots = tuple(
        HourSlot(i, NOW + timedelta(hours=i), 1, i % 24, pv, ac, dc)
        for i, (pv, ac, dc) in enumerate(series)
    )
    return PlanInputs(
        NOW,
        start_soc,
        slots,
        market_prices=tuple(
            MarketPrice(slot.start, slot.start + timedelta(hours=1), price)
            for slot, price in zip(slots, prices, strict=True)
        ),
    )


def test_joint_ac_replaces_cheap_dc_without_buying_total_grid_energy():
    c = plant()
    i = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 300, 0])
    with source_schedule_mode(False):
        reference = simulate(c, i, 20)
    actual = simulate(c, i, 20)
    assert reference.flows[1].inverter_output_wh == 0
    assert actual.flows[1].inverter_output_wh == pytest.approx(100)
    assert sum(grid_dc(c, f) for f in actual.flows) > sum(
        grid_dc(c, f) for f in reference.flows
    )
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.total_export_wh <= reference.total_export_wh + 1e-6
    assert actual.end_soc_percent == pytest.approx(reference.end_soc_percent)
    summary = actual.reserve_decision.joint
    assert summary.joint_status == "accepted"
    assert summary.joint_shifted_wh == pytest.approx(100)
    assert summary.economic_daily_hours == (("2026-10-19", pytest.approx(1)),)


def test_real_converter_losses_reject_extra_total_kwh_even_at_higher_price():
    c = plant()
    c = replace(
        c,
        battery=replace(c.battery, eta_discharge=0.97),
        inverter=ConverterParams(1000, 0.95, 15),
        support=replace(c.support, psu24_eta=0.89, dcdc_eta=0.93),
    )
    i = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 10000, 0])
    with source_schedule_mode(False):
        reference = simulate(c, i, 20)
    actual = simulate(c, i, 20)
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)
    assert actual.flows[1].inverter_output_wh == 0
    assert "extra_import" in actual.reserve_decision.joint.joint_rejection_reasons


def test_more_than_five_hours_and_negative_prices_have_no_daily_quota():
    c = plant()
    i = forecast(
        [(0, 0, 600)] + [(0, 100, 0)] * 6 + [(600, 0, 0)],
        [-200] + [-100] * 6 + [-200],
        95,
    )
    actual = simulate(c, i, 20)
    assert sum(f.inverter_output_wh for f in actual.flows) == pytest.approx(600)
    assert actual.reserve_decision.joint.economic_daily_hours == (
        ("2026-10-19", pytest.approx(6)),
    )
    assert actual.reserve_decision.joint.joint_validations <= 32
    assert actual == simulate(c, i, 20)


def test_unknown_cheap_interval_cannot_donate_energy():
    c = plant()
    i = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 300, 0])
    i = replace(i, market_prices=i.market_prices[1:])
    actual = simulate(c, i, 20)
    assert actual.flows[0].psu24_delivered_wh == 0
    assert actual.flows[1].inverter_output_wh == 0


def test_exact_price_overlap_keeps_real_quarterhour_values_at_partial_start():
    c = plant()
    start = NOW.replace(hour=7, minute=31, second=7)
    i = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 300, 0])
    slots = tuple(
        replace(slot, start=start + timedelta(hours=n))
        for n, slot in enumerate(i.slots)
    )
    left = start.replace(minute=30, second=0)
    prices = tuple(
        MarketPrice(
            left + timedelta(minutes=15 * n),
            left + timedelta(minutes=15 * (n + 1)),
            300 if n % 2 else -100,
        )
        for n in range(13)
    )
    i = replace(i, now=start, slots=slots, market_prices=prices)
    traces = {}
    reference = _simulate_reserve_reference(c, i, None, 1, None, traces=traces)
    steps = traces[id(reference.flows)]
    parts = price_slices(steps, prices)
    assert any(len(part) == 2 for part in parts)
    assert {part.price for quantum in parts for part in quantum} == {-100, 300}
    assert set(actual_prices(steps, prices)) == {-100, 300}
    crossing = next(part for part in parts if len(part) == 2)
    assert crossing[0].end == left + timedelta(minutes=15)
    assert sum(part.share for part in crossing) == pytest.approx(1)
    # Partial price coverage leaves the entire control quantum at reference.
    incomplete = price_slices(steps, prices[1:])
    assert incomplete[0] == ()


def test_real_elapsed_dst_and_midnight_price_slices_do_not_merge_days():
    c = plant()
    zone = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 25, 2, 55, tzinfo=zone)
    i = forecast([(0, 100, 100)], [300])
    slot = replace(i.slots[0], start=start, duration=1 / 6)
    end = (start.astimezone(UTC) + timedelta(minutes=10)).astimezone(zone)
    i = replace(
        i, now=start, slots=(slot,), market_prices=(MarketPrice(start, end, 300),)
    )
    traces = {}
    reference = _simulate_reserve_reference(c, i, None, 1, None, traces=traces)
    parts = price_slices(traces[id(reference.flows)], i.market_prices)
    assert (
        sum(
            (part.end.timestamp() - part.start.timestamp())
            for quantum in parts
            for part in quantum
        )
        == 600
    )
    assert end.fold == 1
    midnight = datetime(2026, 10, 19, 23, 58, tzinfo=UTC)
    slot = replace(slot, start=midnight)
    i = replace(
        i,
        now=midnight,
        slots=(slot,),
        market_prices=(MarketPrice(midnight, midnight + timedelta(minutes=10), 300),),
    )
    traces = {}
    reference = _simulate_reserve_reference(c, i, None, 1, None, traces=traces)
    parts = price_slices(traces[id(reference.flows)], i.market_prices)
    assert {part.start.date().isoformat() for quantum in parts for part in quantum} == {
        "2026-10-19",
        "2026-10-20",
    }


def test_search_limit_is_bounded_and_not_reported_as_physical_impossibility():
    c = plant()
    c = replace(
        c,
        battery=replace(c.battery, capacity_wh=10000),
        charger=ConverterParams(10000, 1, 0),
    )
    i = forecast(
        [(0, 0, 100)] * 40 + [(0, 100, 0)] * 40 + [(4000, 0, 0)],
        [0] * 40 + list(range(300, 340)) + [0],
        95,
    )
    actual = simulate(c, i, 20)
    summary = actual.reserve_decision.joint
    assert summary.joint_status == "search_limit"
    assert summary.joint_validations <= 32
    assert "search_limit" in summary.joint_rejection_reasons
    assert 0 < sum(f.inverter_output_wh for f in actual.flows) < 4000
    assert actual.total_import_wh <= summary.reference_grid_import_wh + 1e-6


def test_partial_expensive_receiver_uses_matching_fifty_minute_donor_block():
    c = plant()
    i = forecast([(0, 0, 100), (0, 500, 0), (100, 0, 0)], [0, 300, 0])
    actual = simulate(c, i, 20)
    assert actual.flows[1].inverter_output_wh == pytest.approx(500 / 6)
    assert actual.flows[0].psu24_delivered_wh == pytest.approx(500 / 6)
    assert actual.total_import_wh == pytest.approx(
        actual.reserve_decision.joint.reference_grid_import_wh
    )
    on = [part for part in actual.flows[0].switching_schedule if part.support_dc24]
    assert sum(
        (part.end.timestamp() - part.start.timestamp()) / 60 for part in on
    ) == pytest.approx(50)


def test_ac_only_donor_needs_no_psu_or_new_source_phase():
    c = plant()
    c = replace(c, support=replace(c.support, dc24_available=False))
    i = forecast([(0, 600, 0), (0, 100, 0), (100, 0, 0)], [0, 1, 0])
    with source_schedule_mode(False):
        reference = simulate(c, i, 20)
    actual = simulate(c, i, 20)
    assert reference.flows[0].inverter_output_wh == pytest.approx(100)
    assert actual.flows[1].inverter_output_wh == pytest.approx(100)
    assert not any(f.support_dc24 or f.support_dc48 for f in actual.flows)
    assert actual.total_import_wh == pytest.approx(reference.total_import_wh)


def test_original_pv_recharge_window_cannot_donate_to_following_window():
    c = plant()
    i = forecast([(0, 0, 100), (100, 0, 0), (0, 100, 0), (0, 0, 0)], [0, 0, 300, 0])
    actual = simulate(c, i, 20)
    assert actual.flows[0].battery_discharge_wh == pytest.approx(100)
    assert actual.flows[2].inverter_output_wh == 0
    assert (
        actual.reserve_decision.joint.actual_battery_draw_wh
        <= actual.reserve_decision.joint.reference_battery_draw_wh + 1e-6
    )


def test_every_full_physics_uncertainty_pass_shares_the_32_budget(monkeypatch):
    import core.reserve as reserve

    c = plant()
    c = replace(
        c,
        battery=replace(c.battery, capacity_wh=10000),
        charger=ConverterParams(10000, 1, 0),
    )
    i = forecast(
        [(0, 0, 100)] * 40 + [(0, 100, 0)] * 40 + [(4000, 0, 0)],
        [0] * 40 + list(range(300, 340)) + [0],
        95,
    )
    i = replace(
        i,
        slots=tuple(
            replace(slot, pv_p10_wh=slot.pv_wh / 2, pv_p90_wh=slot.pv_wh * 1.1)
            for slot in i.slots
        ),
    )
    original_dispatch, original_policy = (
        reserve.joint_dispatch,
        reserve._simulate_reserve_policy,
    )
    active = False
    passes = 0

    def counted_policy(*args, **kwargs):
        nonlocal passes
        if active:
            passes += 1
        return original_policy(*args, **kwargs)

    def counted_dispatch(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return original_dispatch(*args, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(reserve, "_simulate_reserve_policy", counted_policy)
    monkeypatch.setattr(reserve, "joint_dispatch", counted_dispatch)
    actual = simulate(c, i, 20)
    assert 3 <= passes <= 32
    assert actual.reserve_decision.joint.joint_validations == passes
    assert actual.reserve_decision.joint.joint_status == "search_limit"


def test_uncertainty_proof_sees_subhour_dip_even_when_pv_recovers_hour_end():
    c = plant()
    c = replace(
        c,
        battery=replace(c.battery, soc_max_percent=15),
        control=replace(c.control, soc_buffer_percent=5),
    )
    dark = HourSlot(0, NOW, 0.5, 0, 0, 0, 50)
    solar = HourSlot(0, NOW + timedelta(minutes=30), 0.5, 0, 100, 0, 0)
    before = (step_hour(c, 15, dark, 100), step_hour(c, 10, solar, 100))
    after = (step_hour(c, 14, dark, 100), step_hour(c, 9, solar, 100))

    def aggregate(parts):
        energies = {
            field.name: sum(float(getattr(part, field.name) or 0) for part in parts)
            for field in fields(parts[0])
            if field.name.endswith("_wh")
        }
        merged = replace(
            parts[0], **energies, soc_end_percent=parts[-1].soc_end_percent
        )
        return Trajectory(
            (merged,),
            sum(f.grid_import_wh for f in parts),
            sum(f.grid_export_wh for f in parts),
            parts[-1].soc_end_percent,
        )

    reference, trial = aggregate(before), aggregate(after)
    assert reference.end_soc_percent == trial.end_soc_percent == 15
    old = tuple(
        SourceStep(slot, flow, (), 0)
        for slot, flow in zip((dark, solar), before, strict=True)
    )
    new = tuple(
        SourceStep(slot, flow, (), 0)
        for slot, flow in zip((dark, solar), after, strict=True)
    )
    assert uncertainty_failure(c, reference, trial, old, new) == "uncertainty_reserve"
    assert uncertainty_failure(c, reference, reference, old, old) is None


def test_uncertainty_proof_preserves_dc_service_and_total_import():
    c = plant()
    c = replace(
        c,
        support=replace(
            c.support,
            dc48_available=True,
            native48_base_w=100,
            psu48_bus_voltage_v=49.56,
            psu48_max_power_w=100,
        ),
    )
    slot = HourSlot(0, NOW, 1, 0, 0, 0, 50)
    supplied = step_hour(c, 5, slot, 100, dc48_support=True)
    outage = step_hour(c, 5, slot, 100)
    reference = Trajectory(
        (supplied,), supplied.grid_import_wh, 0, supplied.soc_end_percent
    )
    trial = Trajectory((outage,), outage.grid_import_wh, 0, outage.soc_end_percent)
    assert supplied.unserved_dc_wh == 0
    assert outage.unserved_dc_wh > 0
    assert uncertainty_failure(c, reference, trial, (), ()) == "uncertainty_dc_service"
    ac_slot = replace(slot, dc_wh=0, ac_wh=100)
    served = step_hour(c, 95, ac_slot, 20, inverter_limit_w=1000)
    grid = step_hour(c, 95, ac_slot, 100, inverter_limit_w=0)
    reference = Trajectory((served,), served.grid_import_wh, 0, served.soc_end_percent)
    trial = Trajectory((grid,), grid.grid_import_wh, 0, grid.soc_end_percent)
    assert uncertainty_failure(c, reference, trial, (), ()) == "uncertainty_import"
