"""Year-round reserve requirements, using energy outcomes rather than helper mocks."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from core.model import (
    BatteryParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.optimize import plan
from core.simulate import simulate

NOW = datetime(2026, 9, 26)


def config(**support):
    return SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        support=SupportParams(
            configured=True,
            coordinated=True,
            psu48_bus_voltage_v=52,
            native48_base_w=20,
            **support,
        ),
        reserve=ReserveParams(True, 1),
    )


def inputs(soc, series):
    slots = tuple(
        HourSlot(i, NOW + timedelta(hours=i), 1, i % 24, pv, ac, dc)
        for i, (pv, ac, dc) in enumerate(series)
    )
    return PlanInputs(NOW, soc, slots, reserve_hold_soc_percent=soc)


def test_dark_week_preserves_rail_energy_without_invented_48v_output():
    c = config()
    r = simulate(c, inputs(80, [(0, 100, 60)] * 72), 20)
    assert all(f.inverter_output_wh == 0 for f in r.flows)
    assert r.flows[0].psu24_delivered_wh == pytest.approx(40)
    assert r.flows[0].battery_discharge_wh == pytest.approx(20)
    assert any(f.psu24_delivered_wh == pytest.approx(40) for f in r.flows)
    assert all(f.psu48_delivered_wh == 0 for f in r.flows)
    assert r.end_soc_percent == pytest.approx(5)
    assert sum(f.unserved_dc_wh for f in r.flows) > 0
    assert sum(f.battery_charge_wh for f in r.flows) == 0


def test_dc_consumption_creates_all_headroom_without_ac_discharge():
    c = config()
    r = simulate(c, inputs(95, [(0, 100, 100), (0, 100, 100), (300, 100, 0)]), 20)
    assert r.total_export_wh == pytest.approx(0, abs=1e-6)
    assert sum(f.inverter_output_wh for f in r.flows) == pytest.approx(0, abs=1e-6)
    assert sum(f.psu24_delivered_wh for f in r.flows[:2]) == pytest.approx(0)
    assert r.end_soc_percent == pytest.approx(95)


def test_early_evening_ac_opportunity_used_when_morning_has_no_load():
    r = simulate(config(), inputs(95, [(0, 500, 100), (0, 0, 100), (700, 0, 0)]), 20)
    assert r.flows[0].inverter_output_wh == pytest.approx(500)
    assert r.total_export_wh == pytest.approx(0, abs=1e-6)
    assert r.end_soc_percent == pytest.approx(95)


def test_only_dc_remainder_is_allocated_to_ac():
    r = simulate(config(), inputs(95, [(0, 500, 100), (0, 500, 100), (500, 0, 0)]), 20)
    # Seven complete five-minute ON steps fit; an eighth would spend DC reserve.
    served = 7 * 500 / 12
    assert sum(f.inverter_output_wh for f in r.flows) == pytest.approx(served)
    assert sum(f.psu24_delivered_wh for f in r.flows) == 0
    assert r.total_export_wh == pytest.approx(300 - served)


def test_lower_pv_does_not_create_grid_recharge_after_preparation():
    c = config()
    r = simulate(c, inputs(95, [(0, 500, 100), (700, 0, 0)]), 20, pv_scale=0.1)
    # The lower scenario needs no headroom; do not prepare for the old upper
    # scenario while executing weaker sunshine.
    assert r.flows[0].inverter_output_wh == 0
    assert r.flows[-1].battery_charge_wh == pytest.approx(70)
    assert r.end_soc_percent > 90


def test_inverter_floor_and_charge_power_limit_remain_physical():
    c = config()
    c = replace(c, charger=replace(c.charger, max_power_w=100))
    r = simulate(c, inputs(25, [(0, 900, 0), (3000, 0, 0)]), 20)
    assert r.min_soc_percent >= 20
    assert r.total_export_wh == pytest.approx(2900)
    # Emptying farther cannot cure an input-power bottleneck.
    assert r.flows[0].inverter_output_wh == 0


def test_no_slots_and_neutral_default():
    c = config()
    assert simulate(c, inputs(80, []), 20).end_soc_percent == 80
    old = replace(c, reserve=ReserveParams())
    assert simulate(old, inputs(80, [(0, 100, 60)]), 20).flows[
        0
    ].inverter_output_wh == pytest.approx(100)


def test_planner_publishes_same_reserve_switching_as_simulation():
    c = config()
    i = inputs(80, [(0, 100, 60)])
    p = plan(c, i)
    assert p.support_dc24_now and p.support_dc48_now
    assert not p.inverter_on
    assert p.trajectory == simulate(c, i, p.threshold_percent)


@pytest.mark.parametrize("factor", [0, 1.6, float("nan")])
def test_invalid_upper_factor(factor):
    with pytest.raises(ValueError):
        ReserveParams(True, factor)


def test_undersized_psu_never_takes_over_working_dc_converter():
    r = simulate(config(psu24_max_power_w=10), inputs(80, [(0, 100, 60)]), 20)
    assert not r.flows[0].support_dc24
    assert r.flows[0].dcdc_input_wh == pytest.approx(40)


def test_emergency_export_cannot_merely_shift_the_same_export_earlier():
    from core.model import FeedInParams
    from core.optimize import plan_feedin

    c = replace(config(), feedin=FeedInParams(enabled=True, max_w=500))
    i = inputs(90, [(1000, 0, 0), (1000, 0, 0)])
    p = plan(c, i)
    assert p.feedin_schedule_w == (0, 0)
    assert all(
        reason == "reserve_no_emergency_benefit" for _, reason in p.feedin_decisions
    )
    assert p.grid_export_kwh > 0
    schedule, _ = plan_feedin(c, i, 20, (0, 0), p.trajectory)
    assert schedule == (0, 0)


def test_p90_and_scalar_add_only_bounded_uncertainty_to_expected_budget():
    c = config()
    c = replace(c, reserve=ReserveParams(True, 1.2), pv=replace(c.pv, peak_power_w=500))
    i = inputs(95, [(0, 500, 0), (400, 0, 0)])
    bands = replace(
        i, slots=(i.slots[0], replace(i.slots[1], pv_p10_wh=200, pv_p90_wh=800))
    )
    plain = simulate(c, i, 20)
    band = simulate(c, bands, 20)
    # Expected 400 Wh plus a small uncertainty margin: ten full steps fit.
    # Full P90 or the old +20% horizon would permit eleven/twelve steps.
    assert plain.flows[0].inverter_output_wh == pytest.approx(10 * 500 / 12)
    assert band.flows[0].inverter_output_wh == plain.flows[0].inverter_output_wh
    assert band.reserve_decision.headroom_wh <= 450  # 400 + 5% capacity cap
    stronger = replace(
        bands, slots=(bands.slots[0], replace(bands.slots[1], pv_wh=900))
    )
    updated = simulate(c, stronger, 20)
    # A revised expected forecast releases more AC, capped by physical PV peak.
    assert updated.flows[0].inverter_output_wh == pytest.approx(500)


def test_partial_slot_keeps_power_and_energy_units_distinct():
    c = config()
    i = inputs(95, [(0, 100, 0), (200, 0, 0)])
    i = replace(i, slots=(replace(i.slots[0], duration=0.25), i.slots[1]))
    r = simulate(c, i, 20)
    assert r.flows[0].inverter_limit_w <= c.inverter.max_power_w
    assert r.flows[0].inverter_output_wh == pytest.approx(100)
    assert r.total_export_wh == pytest.approx(100)


def test_recording_before_reserve_extension_remains_replayable():
    from core.replay import recording, replay

    c = replace(config(), reserve=ReserveParams())
    i = inputs(80, [(0, 100, 60)])
    recorded = recording(c, i, plan(c, i))
    recorded["config"]["fields"].pop("reserve")
    recorded["inputs"]["fields"].pop("reserve_hold_soc_percent")
    for flow in recorded["result"]["fields"]["trajectory"]["fields"]["flows"]["tuple"]:
        for key in (
            "reserve_ceiling_percent",
            "reserve_dc_ceiling_percent",
            "inverter_limit_w",
        ):
            flow["fields"].pop(key)
    assert replay(recorded)[1]


def test_latest_preparation_time_excludes_involuntary_native_discharge():
    c = config()
    r = simulate(c, inputs(95, [(0, 500, 0), (200, 0, 0)]), 20)
    assert r.flows[0].reserve_preparation_start == NOW + timedelta(minutes=40)
    assert not r.flows[0].inverter_start
    # Do not stretch the remaining 33.3 Wh through a partial-power ON step.
    assert r.flows[0].inverter_output_wh == pytest.approx(4 * 500 / 12)
    dark = simulate(c, inputs(80, [(0, 100, 60)]), 20)
    assert dark.flows[0].battery_discharge_wh > 0
    assert dark.flows[0].reserve_preparation_start is None


def test_measured_48v_support_preserves_soc_without_grid_recharging():
    c = config(psu48_max_power_w=57)
    c = replace(c, support=replace(c.support, psu48_bus_voltage_v=49.56))
    r = simulate(c, inputs(80, [(0, 100, 60)] * 12), 20)
    assert r.flows[0].soc_end_percent == pytest.approx(80)
    assert r.end_soc_percent == pytest.approx(80)
    assert all(f.psu48_delivered_wh == pytest.approx(20) for f in r.flows)
    assert r.reserve_decision.reason == "dc_reserve_holding"
    assert all(f.psu48_battery_charge_wh == 0 for f in r.flows)
    assert all(f.inverter_output_wh == 0 for f in r.flows)


def test_incidental_grid_charge_never_releases_ac_discharge_in_darkness():
    c = config(psu48_max_power_w=57, dc24_forced_on=True, dc48_forced_on=True)
    c = replace(c, support=replace(c.support, psu48_bus_voltage_v=49))
    r = simulate(c, inputs(80, [(0, 100, 60)] * 8), 20)
    assert sum(f.psu48_battery_charge_wh for f in r.flows) > 0
    assert sum(f.inverter_output_wh for f in r.flows) == 0
    assert r.end_soc_percent <= 80 + c.control.hysteresis_percent + 1


@pytest.mark.parametrize("sun", [0, 1500])
def test_optional_loads_do_not_purchase_additional_grid_energy(sun):
    from core.model import SurplusLoad, SurplusLoadState

    c = config()
    i = inputs(90, [(0, 100, 60), (sun, 100, 60), (sun, 100, 60), (0, 100, 60)])
    baseline = plan(c, i)
    loaded = plan(
        replace(c, loads=(SurplusLoad("useful", "Useful", 200),)),
        replace(i, load_states=(SurplusLoadState("useful"),)),
    )
    assert (
        loaded.trajectory.total_import_wh <= baseline.trajectory.total_import_wh + 1e-6
    )
    if sun:
        assert loaded.load_plans[0].planned_energy_wh > 0
        assert loaded.trajectory.total_export_wh < baseline.trajectory.total_export_wh
    else:
        assert loaded.load_plans[0].planned_energy_wh == 0


def test_reserve_requires_coordinated_source_ownership():
    with pytest.raises(ValueError, match="Reserve requires coordinated support"):
        SystemConfig(reserve=ReserveParams(True))


def test_withdrawn_pv_forecast_immediately_revokes_preparation_budget():
    c = config()
    i = inputs(95, [(0, 500, 100), (700, 0, 0)])
    before = plan(c, i)
    assert before.trajectory.flows[0].inverter_limit_w > 0
    after = plan(c, replace(i, slots=(i.slots[0], replace(i.slots[1], pv_wh=0))))
    assert after.trajectory.flows[0].inverter_limit_w == 0
    assert after.support_dc24_now and after.support_dc48_now
    # Unknown 48 V contribution stays conservative; the 80 Wh rail is held.
    assert after.trajectory.flows[0].battery_discharge_wh == pytest.approx(20)
    assert not after.inverter_on


def test_legacy_grid_charge_record_keeps_additive_diagnostics_neutral():
    from core.replay import recording, replay

    c = config(psu48_max_power_w=57, dc48_forced_on=True)
    c = replace(
        c, reserve=ReserveParams(), support=replace(c.support, psu48_bus_voltage_v=49)
    )
    i = inputs(10, [(0, 100, 0)])
    result = plan(c, i)
    assert result.trajectory.flows[0].battery_charge_wh > 0
    record = recording(c, i, result)
    for flow in record["result"]["fields"]["trajectory"]["fields"]["flows"]["tuple"]:
        flow["fields"].pop("psu48_battery_charge_wh")
    assert replay(record)[1]


@pytest.mark.parametrize("soc", [15, 38, 80])
def test_dark_reserve_starts_both_psus_above_protection_thresholds(soc):
    c = config(psu48_max_power_w=57)
    c = replace(c, support=replace(c.support, psu48_bus_voltage_v=49.56))
    r = simulate(c, inputs(soc, [(0, 100, 60)] * 12), 20)
    assert r.flows[0].support_dc24_start and r.flows[0].support_dc48_start
    assert r.min_soc_percent == pytest.approx(soc)
    assert r.end_soc_percent == pytest.approx(soc)
    assert r.reserve_decision.reason == "dc_reserve_holding"
    assert all(f.inverter_output_wh == 0 for f in r.flows)


def test_new_pv_forecast_releases_economic_support_without_waiting_for_recovery():
    c = config(psu48_max_power_w=57)
    c = replace(c, support=replace(c.support, psu48_bus_voltage_v=49.56))
    dark = inputs(80, [(0, 500, 100), (0, 0, 0)])
    held = plan(c, dark)
    assert held.support_dc24_now and held.support_dc48_now
    # Replan from confirmed ON states. They must not become permanent latches.
    c = replace(c, support=replace(c.support, dc24_active=True, dc48_active=True))
    sunny = replace(dark, slots=(dark.slots[0], replace(dark.slots[1], pv_wh=700)))
    released = plan(c, sunny)
    assert not released.support_dc24_now and not released.support_dc48_now
    assert released.trajectory.flows[0].inverter_output_wh > 0
    # Binary steps may leave less than one full load quantum of headroom unused.
    assert released.trajectory.total_export_wh == pytest.approx(500 / 15)


def test_dc_only_preparation_waits_until_needed_and_captures_future_pv():
    c = config()
    c = replace(c, support=replace(c.support, native48_base_w=0))
    r = simulate(c, inputs(80, [(0, 0, 100)] * 3 + [(250, 0, 0)]), 20)
    assert r.flows[0].support_dc24_start
    assert r.flows[0].soc_end_percent == pytest.approx(80)
    assert sum(f.battery_discharge_wh for f in r.flows) == pytest.approx(100)
    assert r.total_export_wh == pytest.approx(0, abs=1e-6)
    assert r.end_soc_percent == pytest.approx(95)
    assert all(f.inverter_output_wh == 0 for f in r.flows)


def test_pv_covering_dc_releases_economic_support_without_purchasing_solar_export():
    c = config(dc24_active=True, dc48_active=True)
    r = simulate(c, inputs(80, [(200, 100, 60)]), 20)
    assert not r.flows[0].support_dc24 and not r.flows[0].support_dc48
    assert r.flows[0].psu24_delivered_wh == 0
    assert r.flows[0].psu48_delivered_wh == 0
    assert r.end_soc_percent == pytest.approx(84)


def test_near_full_weak_pv_must_not_be_displaced_by_grid_support():
    c = config()
    c = replace(c, charger=replace(c.charger, eta=0.9, standby_power_w=10))
    r = simulate(c, inputs(95, [(170, 100, 60)] * 4), 20)
    # PV almost covers DC, so natural SOC drifts down. PSU24 would instead
    # fill the battery and export PV; a capped end SOC alone misses this.
    assert r.total_export_wh == pytest.approx(0, abs=1e-6)
    assert not r.flows[0].support_dc24_start
    # Later small transfers may offset conversion loss, provided all PV still
    # fits. Holding the SOC is the goal; prohibiting those transfers is not.
    assert r.end_soc_percent >= 94.5
    assert all(f.inverter_output_wh == 0 for f in r.flows)
