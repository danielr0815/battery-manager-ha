"""Fast AC permission: measured load, DC ownership, expiry and delayed commands."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_reserve_policy import configure

from custom_components.battery_manager.live_ac import LiveACEnvelope


@pytest.fixture
async def live(rig, hass, freezer):
    freezer.move_to("2026-09-29T09:00:00+00:00")
    c, calls, dead, failures = rig
    configure(c, hass, 80)
    c.raw_config.update(
        operation_house_power_entity="sensor.house",
        operation_pv_power_entity="sensor.pv",
        operation_import_power_entity="sensor.import",
    )
    c._reserve_inverter_limit_w = 0
    c.live_ac.envelope = LiveACEnvelope(
        c.build_system_config(), dt_util.utcnow() + timedelta(minutes=5), 50, False
    )
    for entity, value in (
        ("sensor.house", 2000),
        ("sensor.pv", 500),
        ("sensor.import", 1500),
    ):
        hass.states.async_set(entity, str(value), {"unit_of_measurement": "W"})
    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    c.async_request_refresh = AsyncMock()
    return c, calls, dead, failures


def fresh(hass):
    for entity in (
        "sensor.test_soc",
        "binary_sensor.grid",
        "sensor.house",
        "sensor.pv",
        "sensor.import",
    ):
        s = hass.states.get(entity)
        hass.states.async_set(entity, s.state, dict(s.attributes), force_update=True)


async def test_unplanned_load_releases_without_waiting_for_planner(live, hass):
    c, calls, *_ = live
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 2300
    assert c.data["inverter_recommendation"]
    assert c.data["live_ac"]["reason"] == "measured_ac_demand"
    assert calls == [(LIMIT, 2300)]
    c.async_request_refresh.assert_not_called()
    # Success reduces grid import to zero; the consumer still needs 1500 W.
    hass.states.async_set("sensor.import", "0", {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert c.live_ac.state.low_since is None
    assert c.live_ac.limit_w == 2300
    assert calls == [(LIMIT, 2300)]


async def test_pause_has_ten_minute_hysteresis_across_fresh_plans(live, hass, freezer):
    c, calls, *_ = live
    await c.live_ac.run()
    hass.states.async_set("sensor.house", "200", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.import", "0", {"unit_of_measurement": "W"})
    await c.live_ac.run()
    for seconds, expected in ((299, 2300), (300, 2300), (599, 2300), (600, 0)):
        freezer.move_to(
            dt_util.parse_datetime("2026-09-29T09:00:00Z") + timedelta(seconds=seconds)
        )
        fresh(hass)
        old = c.live_ac.envelope
        c.live_ac.envelope = LiveACEnvelope(
            old.config,
            dt_util.utcnow() + timedelta(minutes=5),
            old.floor_percent,
            False,
        )
        await c.live_ac.run()
        assert float(hass.states.get(LIMIT).state) == expected
    assert calls == [(LIMIT, 2300), (LIMIT, 0)]


@pytest.mark.parametrize(
    "fault",
    [
        "soc",
        "invalid_soc",
        "grid",
        "psu24",
        "psu48",
        "dcdc",
        "manual",
        "required",
        "budget",
        "house",
        "pv",
        "expired",
        "mode",
    ],
)
async def test_protection_bypasses_hold(live, hass, freezer, fault):
    c, calls, *_ = live
    await c.live_ac.run()
    if fault == "soc":
        hass.states.async_set("sensor.test_soc", "unknown")
    elif fault == "invalid_soc":
        hass.states.async_set("sensor.test_soc", "200")
    elif fault == "grid":
        hass.states.async_set("binary_sensor.grid", "off")
    elif fault in ("psu24", "psu48", "dcdc"):
        hass.states.async_set(
            {"psu24": PSU24, "psu48": PSU48, "dcdc": DCDC}[fault],
            "off" if fault == "dcdc" else "on",
        )
    elif fault == "manual":
        c._support_manual["dc24"] = True
    elif fault in ("required", "budget"):
        old = c.live_ac.envelope
        c.live_ac.envelope = LiveACEnvelope(
            old.config,
            old.expires,
            80 if fault == "budget" else 50,
            fault == "required",
        )
    elif fault in ("house", "pv"):
        hass.states.async_set("sensor." + fault, "unavailable")
    elif fault == "expired":
        freezer.tick(timedelta(minutes=5))
        fresh(hass)
    else:
        c.raw_config["reserve_mode"] = "shadow"
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 0
    assert not c.data["inverter_recommendation"]
    assert not c.live_ac.state.active
    assert calls[-1] == (LIMIT, 0)


async def test_no_signal_reports_means_timer_revokes_stale_permission(
    live, hass, freezer
):
    c, calls, *_ = live
    c.live_ac.start()
    c.live_ac.start()  # Starting twice must not install competing timers.
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(hass.states.get(LIMIT).state) == 2300
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(hass.states.get(LIMIT).state) == 0
    assert c.live_ac.diagnostics["reason"] == "soc_unavailable"
    c.live_ac.stop()
    fresh(hass)
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(LIMIT, 2300), (LIMIT, 0)]


async def test_kw_and_pending_confirmation_are_not_assumed_success(live, hass):
    c, calls, dead, _ = live
    hass.states.async_set("sensor.house", "2", {"unit_of_measurement": "kW"})
    dead.add(LIMIT)
    await c.live_ac.run()
    assert c.live_ac.limit_w == 2300
    assert not c.data["inverter_recommendation"]
    dead.clear()
    await c.live_ac.run()
    assert c.data["inverter_recommendation"]


async def test_revocation_retries_until_confirmed(live, hass):
    c, calls, dead, _ = live
    await c.live_ac.run()
    dead.add(LIMIT)
    hass.states.async_set("sensor.house", "unavailable")
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 2300
    assert not c.live_ac.diagnostics["confirmed"]
    dead.clear()
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 0


async def test_delayed_release_cannot_survive_new_psu_request(live, hass):
    c, calls, *_ = live
    original = c._set_number_value

    async def change_source(entity, value):
        result = await original(entity, value)
        if value:
            c._support_manual["dc48"] = True
        return result

    c._set_number_value = change_source
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 0
    assert not c.data["inverter_recommendation"]
    assert calls == [(LIMIT, 2300), (LIMIT, 0)]


async def test_fast_path_waits_for_source_owner_and_rechecks(live, hass):
    c, calls, *_ = live
    async with c._switch_lock:
        task = asyncio.create_task(c.live_ac.run())
        hass.states.async_set(PSU24, "on")
    await task
    assert calls == []
    assert c.live_ac.diagnostics["reason"] == "dc_supply"


async def test_plan_permission_survives_missing_optional_live_meter(live, hass):
    c, calls, *_ = live
    await c.live_ac.run()
    c._reserve_inverter_limit_w = 2300
    hass.states.async_set("sensor.house", "unavailable")
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 2300
    assert c.data["inverter_recommendation"]


async def test_shutdown_never_writes(live):
    c, calls, *_ = live
    c._actuation_shutdown = True
    c.live_ac._tick(dt_util.utcnow())
    await c.live_ac.run()
    assert calls == []


async def test_plan_executor_permission_is_revoked_even_before_first_live_tick(
    live, hass
):
    c, calls, *_ = live
    assert await c._confirm_inverter_limit(False, {})
    assert float(hass.states.get(LIMIT).state) == 2300
    hass.states.async_set("sensor.pv", "unavailable")
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 0


async def test_real_reserve_plan_keeps_forecast_but_releases_measured_load(live, hass):
    from dataclasses import replace

    from custom_components.battery_manager.core import plan
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs

    c, calls, *_ = live
    config = c.build_system_config()
    config = replace(
        config,
        battery=replace(
            config.battery, capacity_wh=1000, eta_charge=1, eta_discharge=1
        ),
        inverter=replace(config.inverter, max_power_w=1000, eta=1, standby_power_w=0),
        charger=replace(config.charger, max_power_w=2000, eta=1, standby_power_w=0),
        reserve=replace(config.reserve, upper_pv_factor=1),
        support=replace(config.support, native48_base_w=0),
    )
    now = dt_util.now()
    inputs = PlanInputs(
        now,
        80,
        tuple(
            HourSlot(i, now + timedelta(hours=i), 1, (now.hour + i) % 24, pv, ac, 0)
            for i, (pv, ac) in enumerate(((0, 100), (0, 600), (300, 0)))
        ),
    )
    result = plan(config, inputs)
    assert not result.inverter_on
    assert result.trajectory.flows[1].inverter_output_wh > 0
    c.raw_config["inverter_max_power_w"] = 1000
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 1000
    assert not result.inverter_on
    # Updating the same plan cannot accidentally restore its zero limit.
    c._coordinated_inverter_target = bool(c.live_ac.refresh())
    await c._apply_coordinated_support(result, config, now)
    await c._switch_task
    assert float(hass.states.get(LIMIT).state) == 1000
    assert calls == [(LIMIT, 1000)]


async def test_budget_refresh_is_requested_before_expiry(live, monkeypatch):
    from types import SimpleNamespace

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs

    c, *_ = live
    now = dt_util.now()
    inputs = PlanInputs(now, 80, (HourSlot(0, now, 1, now.hour, 0, 100, 0),))
    from unittest.mock import Mock

    timer = Mock(return_value=Mock())
    monkeypatch.setattr(
        "custom_components.battery_manager.coordinator.async_track_point_in_time", timer
    )
    BatteryManagerCoordinator._arm_plan_boundary(
        c, inputs, SimpleNamespace(load_plans=(), cascade_plans=())
    )
    assert timer.call_args.args[2] == dt_util.as_utc(now) + timedelta(minutes=4)


@pytest.mark.parametrize(
    "grid,ac_in,ac_out,expected",
    [
        (1500, -500, -500, 1500),
        (0, -1800, -300, 1500),
        (-300, -800, -1000, 0),
        (0, -50, 10, 60),
    ],
)
async def test_direct_ac_balance_excludes_dc_and_counts_solar_once(
    live, hass, grid, ac_in, ac_out, expected
):
    c, calls, *_ = live
    from custom_components.battery_manager.const import LIVE_AC_POWER_KEYS

    for key, entity, value in zip(
        LIVE_AC_POWER_KEYS,
        ("sensor.net", "sensor.ac_in", "sensor.ac_out"),
        (grid, ac_in, ac_out),
        strict=True,
    ):
        c.raw_config[key] = entity
        hass.states.async_set(entity, str(value / 1000), {"unit_of_measurement": "kW"})
    assert c.live_ac.sources_configured()
    assert c.live_ac.measured_demand() == expected
    await c.live_ac.run()
    assert (float(hass.states.get(LIMIT).state) > 0) is (expected >= 100)
    # An incomplete direct binding must not silently switch to another balance.
    c.raw_config["live_ac_output_power_entity"] = None
    await c.live_ac.run()
    assert c.live_ac.measured_demand() is None
    assert float(hass.states.get(LIMIT).state) == 0


async def test_direct_ac_sources_are_individually_fresh(live, hass, freezer):
    c, calls, *_ = live
    from custom_components.battery_manager.const import LIVE_AC_POWER_KEYS

    for key, value in zip(LIVE_AC_POWER_KEYS, (1500, 0, 0), strict=True):
        c.raw_config[key] = "sensor." + key
        hass.states.async_set("sensor." + key, str(value), {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 2300
    freezer.tick(timedelta(seconds=31))
    fresh(hass)
    for key in LIVE_AC_POWER_KEYS[:2]:
        s = hass.states.get(c.raw_config[key])
        hass.states.async_set(
            s.entity_id, s.state, dict(s.attributes), force_update=True
        )
    await c.live_ac.run()
    assert float(hass.states.get(LIMIT).state) == 0
    assert c.live_ac.diagnostics["reason"] == "measurement_unavailable"


async def test_small_remaining_budget_switches_off_instead_of_throttling(live, hass):
    c, calls, *_ = live
    await c.live_ac.run()
    config = c.live_ac.envelope.config
    floor = 50 + max(
        config.control.soc_buffer_percent, config.control.hysteresis_percent
    )
    # Ten remaining Wh cannot sustain full permission during the 35-s window.
    hass.states.async_set(
        "sensor.test_soc", str(floor + 10 / config.battery.capacity_wh * 100)
    )
    await c.live_ac.run()
    assert calls == [(LIMIT, 2300), (LIMIT, 0)]
    assert c.live_ac.diagnostics["reason"] == "reserve_budget"
    assert not c.data["inverter_recommendation"]


@pytest.mark.parametrize("fault", ["budget", "expired", "stale_soc", "grid", "source"])
async def test_planned_full_permission_is_guarded_without_optional_load_meters(
    live, hass, freezer, fault
):
    from dataclasses import replace

    c, calls, *_ = live
    c._reserve_inverter_limit_w = 2300
    c.live_ac.envelope = replace(
        c.live_ac.envelope, floor_percent=90, planned_floor_percent=50
    )
    hass.states.async_set("sensor.house", "unavailable")
    assert await c._confirm_inverter_limit(False, {})
    assert calls == [(LIMIT, 2300)]
    assert c.live_ac.limit_w == 0  # Only the forecast permission is active.
    c.live_ac.start()
    if fault == "budget":
        config = c.live_ac.envelope.config
        floor = 50 + config.control.hysteresis_percent
        hass.states.async_set(
            "sensor.test_soc", str(floor + 10 / config.battery.capacity_wh * 100)
        )
    elif fault == "expired":
        c.live_ac.envelope = replace(c.live_ac.envelope, expires=dt_util.utcnow())
    elif fault == "stale_soc":
        freezer.tick(timedelta(seconds=31))
    elif fault == "grid":
        hass.states.async_set("binary_sensor.grid", "off")
    else:
        hass.states.async_set(PSU48, "on")
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(LIMIT, 2300), (LIMIT, 0)]
    assert not c.data["inverter_recommendation"]
    assert c.live_ac.diagnostics["planned_limit_w"] == 0
    c.live_ac.stop()


async def test_delayed_planned_release_rechecks_its_budget(live, hass):
    from dataclasses import replace

    c, calls, *_ = live
    c._reserve_inverter_limit_w = 2300
    c.live_ac.envelope = replace(
        c.live_ac.envelope, floor_percent=90, planned_floor_percent=50
    )
    original = c._set_number_value

    async def consume_budget(entity, value):
        confirmed = await original(entity, value)
        if value:
            hass.states.async_set("sensor.test_soc", "50")
        return confirmed

    c._set_number_value = consume_budget
    assert not await c._confirm_inverter_limit(False, {})
    await c.live_ac.run()
    assert calls == [(LIMIT, 2300), (LIMIT, 0)]
    assert not c.data["inverter_recommendation"]


async def test_guard_does_not_take_ownership_of_legacy_inverter(live, hass):
    c, calls, *_ = live
    c.raw_config["reserve_mode"] = "off"
    c._reserve_inverter_limit_w = None
    assert await c._confirm_inverter_limit(False, {})
    await c.live_ac.run()
    assert calls == [(LIMIT, 2300)]
    assert float(hass.states.get(LIMIT).state) == 2300


async def test_timer_cannot_start_planned_permission_before_source_owner(live):
    from dataclasses import replace

    c, calls, *_ = live
    c._reserve_inverter_limit_w = 2300
    c.live_ac.envelope = replace(
        c.live_ac.envelope, floor_percent=90, planned_floor_percent=50
    )
    await c.live_ac.run()
    assert c.live_ac.diagnostics["planned_limit_w"] == 2300
    assert calls == []


async def test_real_forecast_headroom_funds_full_permission_without_live_meters(
    live, hass
):
    from dataclasses import replace

    from custom_components.battery_manager.core import plan
    from custom_components.battery_manager.core.model import HourSlot, PlanInputs

    c, calls, *_ = live
    config = c.build_system_config()
    config = replace(
        config,
        battery=replace(
            config.battery, capacity_wh=1000, eta_charge=1, eta_discharge=1
        ),
        inverter=replace(config.inverter, max_power_w=1000, eta=1, standby_power_w=0),
        charger=replace(config.charger, max_power_w=2000, eta=1, standby_power_w=0),
        control=replace(config.control, hysteresis_percent=0),
        reserve=replace(config.reserve, upper_pv_factor=1),
        support=replace(config.support, native48_base_w=0),
    )
    now = dt_util.now()
    inputs = PlanInputs(
        now,
        80,
        (
            HourSlot(0, now, 1, now.hour, 0, 600, 0),
            HourSlot(1, now + timedelta(hours=1), 1, (now.hour + 1) % 24, 900, 0, 0),
        ),
    )
    result = plan(config, inputs)
    assert result.inverter_on
    c.raw_config["inverter_max_power_w"] = 1000
    c._reserve_inverter_limit_w = result.trajectory.flows[0].inverter_limit_w
    hass.states.async_set("sensor.house", "unavailable")
    c.live_ac.set_plan(config, inputs, result)
    assert c.live_ac.limit_w == 0
    assert c.live_ac.planned_limit() == 1000
    assert await c._confirm_inverter_limit(False, {})
    assert calls == [(LIMIT, 1000)]
    # Spending the forecast headroom revokes the plan itself, not just live AC.
    hass.states.async_set(
        "sensor.test_soc", str(c.live_ac.envelope.planned_floor_percent)
    )
    await c.live_ac.run()
    assert calls == [(LIMIT, 1000), (LIMIT, 0)]


@pytest.mark.parametrize("current_price", [100, None])
async def test_real_market_plan_keeps_energy_for_later_peak_despite_measured_load(
    live, hass, current_price
):
    from dataclasses import replace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        MarketPrice,
        PlanInputs,
    )
    from custom_components.battery_manager.core.optimize import plan

    c, calls, *_ = live
    config = c.live_ac.envelope.config
    config = replace(
        config,
        battery=replace(
            config.battery, capacity_wh=1000, eta_charge=1, eta_discharge=1
        ),
        inverter=replace(config.inverter, max_power_w=2300, eta=1, standby_power_w=0),
        charger=replace(config.charger, max_power_w=2000, eta=1, standby_power_w=0),
        control=replace(config.control, hysteresis_percent=0),
        reserve=replace(config.reserve, upper_pv_factor=1),
        support=replace(config.support, native48_base_w=0),
    )
    now = dt_util.now()
    inputs = PlanInputs(
        now,
        80,
        tuple(
            HourSlot(i, now + timedelta(hours=i), 1, (now.hour + i) % 24, pv, ac, 0)
            for i, (pv, ac) in enumerate([(0, 600), (0, 500), (300, 0), (0, 550)])
        ),
        market_prices=tuple(
            MarketPrice(now + timedelta(hours=i), now + timedelta(hours=i + 1), p)
            for i, p in enumerate([current_price, 300, 100, None])
            if p is not None
        ),
    )
    result = plan(config, inputs)
    assert not result.inverter_on
    assert result.trajectory.flows[1].inverter_output_wh > 0
    assert (
        result.trajectory.reserve_decision.market_ranking_reason == "weighted_partial"
    )
    c.live_ac.set_plan(config, inputs, result)
    await c.live_ac.run()
    assert not calls
    assert c.live_ac.limit_w == 0
    assert c.live_ac.diagnostics["available_wh"] == 0

    # 1500 W is only equal to the future 500 W × 3 opportunity; 2000 W is
    # materially better and may consume the already scheduled allocation.
    hass.states.async_set("sensor.house", "2500", {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert calls == [(LIMIT, 2300)]
    assert c.live_ac.limit_w == 2300
    assert c.live_ac.envelope.override_floor_percent >= 67.5
    # The market budget overrides the ordinary ten-minute hold immediately.
    hass.states.async_set("sensor.house", "1200", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.import", "700", {"unit_of_measurement": "W"})
    await c.live_ac.run()
    assert calls[-1] == (LIMIT, 0)
    assert c.live_ac.limit_w == 0


async def test_price_boundary_expires_permission_and_schedules_replanning(
    live, hass, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from custom_components.battery_manager.core.model import (
        HourSlot,
        MarketPrice,
        PlanInputs,
    )
    from custom_components.battery_manager.core.optimize import plan

    c, *_ = live
    now = dt_util.now()
    boundary = now + timedelta(seconds=30)
    config = c.live_ac.envelope.config
    inputs = PlanInputs(
        now,
        80,
        (HourSlot(0, now, 1, now.hour, 0, 100, 0),),
        market_prices=(
            MarketPrice(now, boundary, 100),
            MarketPrice(boundary, now + timedelta(minutes=15), 300),
        ),
    )
    c.live_ac.set_plan(config, inputs, plan(config, inputs))
    assert c.live_ac.envelope.expires == dt_util.as_utc(boundary)
    timer = Mock(return_value=Mock())
    monkeypatch.setattr(
        "custom_components.battery_manager.coordinator.async_track_point_in_time", timer
    )
    BatteryManagerCoordinator._arm_plan_boundary(
        c, inputs, SimpleNamespace(load_plans=(), cascade_plans=())
    )
    assert timer.call_args.args[2] == dt_util.as_utc(boundary)
