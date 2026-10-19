"""F-JOINT-MARKET: physical accepted schedules reach HA without a DC veto."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import LIMIT
from test_coordinated_actuation import rig as rig
from test_live_ac_runtime import fresh
from test_live_ac_runtime import live as live
from test_market_adapter import publish, rows
from test_reserve_policy import configure, update

from custom_components.battery_manager.core.model import (
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    MarketPrice,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from custom_components.battery_manager.core.optimize import plan
from custom_components.battery_manager.market import read_prices


def joint_case(now):
    config = SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        control=ControlParams(soc_buffer_percent=0, hysteresis_percent=0),
        support=SupportParams(configured=True, coordinated=True, dc48_available=False),
        reserve=ReserveParams(True, 1),
    )
    slots = tuple(
        HourSlot(i, now + timedelta(hours=i), 1, (now.hour + i) % 24, pv, ac, dc)
        for i, (pv, ac, dc) in enumerate([(0, 200, 0), (0, 0, 200), (200, 0, 0)])
    )
    inputs = PlanInputs(
        now,
        95,
        slots,
        market_prices=tuple(
            MarketPrice(slot.start, slot.start + timedelta(hours=1), price)
            for slot, price in zip(slots, (300, 0, 0), strict=True)
        ),
    )
    return config, inputs


async def test_accepted_joint_ac_budget_reaches_live_and_replan_revokes_it(live, hass):
    """Buying cheap DC instead of dear AC releases only the accepted energy."""
    c, calls, *_ = live
    c.raw_config["inverter_max_power_w"] = 1000
    hass.states.async_set("sensor.test_soc", "95")
    config, inputs = joint_case(dt_util.now())
    result = plan(config, inputs)
    decision = result.trajectory.reserve_decision
    assert decision.joint.joint_status == "accepted"
    assert decision.joint.joint_shifted_wh == pytest.approx(200)
    assert result.trajectory.flows[0].inverter_output_wh == pytest.approx(200)
    assert decision.joint.actual_grid_import_wh == pytest.approx(
        decision.joint.reference_grid_import_wh
    )
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert calls == [(LIMIT, 1000)]
    assert c.data["inverter_recommendation"]
    assert c.live_ac.diagnostics["available_wh"] == pytest.approx(
        round(decision.headroom_wh, 1)
    )
    assert config.battery.energy_wh(
        inputs.start_soc_percent - c.live_ac.envelope.floor_percent
    ) == pytest.approx(decision.headroom_wh)

    # A higher sensor reading cannot enlarge a physical allocation accepted at
    # planning time, even when it would leave more energy above the same floor.
    hass.states.async_set("sensor.test_soc", "100")
    await c.live_ac.run()
    assert c.live_ac.diagnostics["available_wh"] == pytest.approx(
        round(decision.headroom_wh, 1)
    )
    assert calls == [(LIMIT, 1000)]

    # A new physical SOC consumes this booked five-minute allowance. The
    # ten-minute consumer hold cannot prolong permission after budget depletion.
    hass.states.async_set(
        "sensor.test_soc", str(c.live_ac.envelope.floor_percent + 0.1)
    )
    await c.live_ac.run()
    assert calls == [(LIMIT, 1000), (LIMIT, 0)]
    assert c.live_ac.diagnostics["reason"] == "reserve_budget"

    # Replanning with a cheaper present opportunity removes its accepted AC
    # allocation, even though measured demand still exists and SOC recovers.
    hass.states.async_set("sensor.test_soc", "95")
    revised = replace(
        inputs,
        market_prices=tuple(
            replace(price, eur_per_mwh=value)
            for price, value in zip(inputs.market_prices, (0, 300, 0), strict=True)
        ),
    )
    successor = plan(config, revised)
    assert successor.trajectory.flows[0].inverter_output_wh == 0
    c.live_ac.set_plan(config, revised, successor)
    await c.live_ac.run()
    assert c.live_ac.limit_w == 0
    assert calls == [(LIMIT, 1000), (LIMIT, 0)]


@pytest.mark.parametrize("legacy,flat", [(False, False), (True, False), (False, True)])
async def test_coordinator_publishes_actual_joint_intervals_and_reads_legacy(
    rig, hass, freezer, monkeypatch, legacy, flat
):
    """The sensor's preferred intervals describe accepted physical allocation."""
    freezer.move_to("2026-10-19T08:00:00Z")
    c, *_ = rig
    configure(c, hass, 80, sun=1)
    publish(
        hass, rows(dt_util.now(), values=(100, 100, 100) if flat else (100, 300, 100))
    )
    obsolete = [{"start": "obsolete-price-ranking", "eur_per_mwh": 999}]

    def stale_rank(*args):
        prices, diagnostics = read_prices(*args)
        return prices, diagnostics | {"preferred_intervals": obsolete}

    monkeypatch.setattr(
        "custom_components.battery_manager.coordinator.read_prices", stale_rank
    )
    original = c._async_plan
    observed = []

    async def recorded_plan(*args):
        result = await original(*args)
        if args[0] == "reserve":
            decision = result.trajectory.reserve_decision
            if legacy:
                result = replace(
                    result,
                    trajectory=replace(
                        result.trajectory,
                        reserve_decision=replace(decision, joint=None),
                    ),
                )
            observed.append(result.trajectory.reserve_decision)
        return result

    monkeypatch.setattr(c, "_async_plan", recorded_plan)
    data = await update(c)
    market = data["reserve"]["market"]
    assert market["status"] == "available"
    assert observed
    if legacy:
        assert "joint" not in market
        assert market["preferred_intervals"] == obsolete
        assert data["reserve"]["decision_reason"] == observed[-1].reason
    else:
        summary = observed[-1].joint
        diagnostic = market["joint"]
        if flat:
            assert diagnostic["joint_status"] == "flat_prices"
            assert diagnostic["unavoidable_dc_draw_wh"] is None
        assert diagnostic["joint_status"] == summary.joint_status
        assert diagnostic["joint_budget_wh"] == summary.joint_budget_wh
        assert diagnostic["economic_daily_hours"] == dict(summary.economic_daily_hours)
        assert market["preferred_intervals"] == diagnostic["economic_intervals"]
        assert market["preferred_intervals"] != obsolete
        assert [row["start"] for row in diagnostic["economic_intervals"]] == [
            interval.start.isoformat() for interval in summary.economic_intervals
        ]
        # The complete nested output must remain valid sensor/diagnostics JSON.
        assert json.loads(json.dumps(market))["joint"] == diagnostic


async def test_confirmed_restored_source_phase_binds_joint_ac_until_expiry(
    live, hass, freezer
):
    """Restart/replan cannot spend a joint budget during confirmed PSU holding."""
    from unittest.mock import AsyncMock

    from test_coordinated_actuation import DCDC, PSU24
    from test_source_phases import use_real_switch

    from custom_components.battery_manager.actor_ownership import SUPPLY_OWNER
    from custom_components.battery_manager.coordinated_supply import (
        reconcile_supply_feedback,
    )
    from custom_components.battery_manager.source_phases import SourcePhases

    c, calls, *_ = live
    c.raw_config["inverter_max_power_w"] = 1000
    hass.states.async_set("sensor.test_soc", "95")
    use_real_switch(c, hass)
    await c._switch_entity(PSU24, True, actor_owner=SUPPLY_OWNER)
    hass.states.async_set(DCDC, "off")
    reconcile_supply_feedback(c)
    deadline = c.source_phases.deadlines(dt_util.utcnow())[0]
    assert deadline == (dt_util.utcnow() + timedelta(minutes=30)).timestamp()
    payload = c._persistent_payload()
    c.source_phases = SourcePhases(c)
    c._store.async_load = AsyncMock(return_value=payload)
    await c.async_load_persistent_state()
    assert c.source_phases.deadlines(dt_util.utcnow())[0] == deadline

    now = dt_util.now()
    config, inputs = joint_case(now)
    bound_support = c.build_system_config().support
    assert bound_support.dc24_phase_until == deadline
    config = replace(
        config,
        support=replace(
            config.support,
            dc24_active=True,
            dc24_phase_until=bound_support.dc24_phase_until,
        ),
    )
    result = plan(config, inputs)
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert c.live_ac.limit_w == 0
    assert c.live_ac.diagnostics["reason"] == "dc_supply"
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)

    freezer.tick(timedelta(minutes=30))
    fresh(hass)
    assert c.source_phases.deadlines(dt_util.utcnow())[0] == 0
    # The native source is physically confirmed before applying a fresh plan.
    await c._switch_entity(PSU24, False, actor_owner=SUPPLY_OWNER)
    hass.states.async_set(DCDC, "on")
    reconcile_supply_feedback(c)
    config, inputs = joint_case(dt_util.now())
    successor = plan(config, inputs)
    assert successor.trajectory.reserve_decision.joint.joint_status == "accepted"
    c.live_ac.set_plan(config, inputs, successor)
    await c.live_ac.run()
    assert c.live_ac.limit_w == 1000
    assert float(hass.states.get(LIMIT).state) == 1000


async def test_already_optimal_current_ac_has_exact_cap_without_any_joint_shift(
    live, hass
):
    """Keeping the reference allocation still books only its current quantum."""
    c, calls, *_ = live
    c.raw_config["inverter_max_power_w"] = 1000
    hass.states.async_set("sensor.test_soc", "95")
    config, inputs = joint_case(dt_util.now())
    inputs = replace(
        inputs,
        slots=(
            inputs.slots[0],
            replace(inputs.slots[1], ac_wh=200, dc_wh=0),
            inputs.slots[2],
        ),
        market_prices=tuple(
            replace(price, eur_per_mwh=value)
            for price, value in zip(inputs.market_prices, (300, 0, 100), strict=True)
        ),
    )
    result = plan(config, inputs)
    decision = result.trajectory.reserve_decision
    assert decision.joint.joint_status == "no_improvement"
    assert decision.joint.joint_shifted_wh == 0
    assert result.trajectory.flows[0].inverter_output_wh == pytest.approx(200)
    assert result.trajectory.flows[1].inverter_output_wh == 0
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert calls == [(LIMIT, 1000)]
    booked_quantum_wh = 200 / 12
    assert c.live_ac.diagnostics["available_wh"] == round(booked_quantum_wh, 1)

    hass.states.async_set("sensor.test_soc", "100")
    await c.live_ac.run()
    assert c.live_ac.diagnostics["available_wh"] == round(booked_quantum_wh, 1)
    assert calls == [(LIMIT, 1000)]

    hass.states.async_set("sensor.test_soc", str(95 - booked_quantum_wh / 10 + 0.1))
    await c.live_ac.run()
    assert calls == [(LIMIT, 1000), (LIMIT, 0)]
    assert c.live_ac.diagnostics["reason"] == "reserve_budget"


@pytest.mark.parametrize("current_price", [0, None])
async def test_unchanged_future_ac_allocation_cannot_be_spent_by_measured_override(
    live, hass, current_price
):
    """A stronger live load requires a newly proved schedule to borrow future AC."""
    c, calls, *_ = live
    c.raw_config["inverter_max_power_w"] = 1000
    hass.states.async_set("sensor.test_soc", "95")
    config, inputs = joint_case(dt_util.now())
    inputs = replace(
        inputs,
        slots=(
            inputs.slots[0],
            replace(inputs.slots[1], ac_wh=200, dc_wh=0),
            inputs.slots[2],
        ),
        market_prices=tuple(
            replace(price, eur_per_mwh=value)
            for price, value in zip(
                inputs.market_prices, (current_price, 300, 100), strict=True
            )
            if value is not None
        ),
    )
    result = plan(config, inputs)
    decision = result.trajectory.reserve_decision
    assert decision.joint.joint_status == "no_improvement"
    assert decision.joint.joint_shifted_wh == 0
    assert result.trajectory.flows[0].inverter_output_wh == 0
    assert result.trajectory.flows[1].inverter_output_wh == pytest.approx(200)
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert c.live_ac.measured_demand() == 1500
    assert not calls
    assert c.live_ac.limit_w == 0
    assert c.live_ac.diagnostics["available_wh"] == 0
    assert c.live_ac.diagnostics["reason"] == "reserve_budget"

    # A fresh SOC increase cannot turn the unchanged, future-only allocation
    # into permission in an unselected cheap or unpriced current interval.
    hass.states.async_set("sensor.test_soc", "100")
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert c.live_ac.diagnostics["available_wh"] == 0
    assert not calls
