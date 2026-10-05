"""Telemetry must preserve protection and accounting without CPU searches."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from test_appliance_views import _entry
from test_feedin import BATT_POWER, MORNING, SETPOINT, _executor_pass, _setup_feedin
from test_realized_surplus import EXPORT, KWH
from test_realized_surplus import _setup as setup_realized

from custom_components.battery_manager.const import DEBOUNCE_SECONDS, DOMAIN
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.fast_updates import async_fast_update


@pytest.fixture(autouse=True)
async def unload(hass):
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def exporting(hass, freezer, *, budget=12000):
    dt_util.set_default_time_zone(dt_util.UTC)
    freezer.move_to(MORNING)
    calls = []
    c, _ = await _setup_feedin(hass, calls)
    config, inputs, result = c._last_planner_recording
    result = replace(
        result,
        feedin_schedule_w=(500.0, *result.feedin_schedule_w[1:]),
        feedin_by_day_wh={MORNING.date().isoformat(): budget},
    )
    c._last_planner_recording = (config, inputs, result)
    await _executor_pass(hass, c, result)
    assert calls[-1] == (SETPOINT, -500)
    calls.clear()
    return c, calls


@pytest.mark.parametrize(("power", "expected"), [("-200", -300), ("200", -700)])
async def test_power_event_trims_in_both_directions_without_planning(
    hass, freezer, power, expected
):
    c, calls = await exporting(hass, freezer)
    metadata = dict(c.data["plan_metadata"])
    freezer.tick(timedelta(seconds=10))
    c._listeners_setup = True
    with patch.object(c, "_async_plan", side_effect=AssertionError("CPU search")):
        hass.states.async_set(BATT_POWER, power)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(SETPOINT, expected)]
    assert c.data["plan_metadata"] == metadata
    assert c._feedin_delivered[1] > 0
    assert c._debounce_task is None


async def test_small_power_burst_coalesces_without_replanning(hass, freezer):
    c, calls = await exporting(hass, freezer)
    c._listeners_setup = True
    with patch.object(c, "_async_plan", side_effect=AssertionError("CPU search")):
        for power in ("4", "9", "3", "8"):
            hass.states.async_set(BATT_POWER, power)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == []
    assert c._debounce_task is None


async def test_own_setpoint_confirmation_does_not_apply_the_same_trim_twice(
    hass, freezer
):
    c, calls = await exporting(hass, freezer)
    # The setpoint event may settle after the command task has finished.
    c._listeners_setup = True
    hass.states.async_set(BATT_POWER, "-200")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(SETPOINT, -300)]
    await async_fast_update(c, trim=False)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(SETPOINT, -300)]


async def test_held_plan_cannot_spend_its_export_budget_twice(hass, freezer):
    c, calls = await exporting(hass, freezer, budget=5)
    freezer.tick(timedelta(seconds=40))  # 500 W x 40 s > the remaining 5 Wh.
    await async_fast_update(c)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1] == (SETPOINT, 0)


async def test_only_delivery_since_capture_reduces_the_held_budget(hass, freezer):
    c, calls = await exporting(hass, freezer)
    c._planned_feedin_delivered_wh = 1000
    c._feedin_delivered = (MORNING.date(), 1001, MORNING)
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(BATT_POWER, "200")
    with patch.object(c, "_apply_feedin", wraps=c._apply_feedin) as apply:
        await async_fast_update(c)
    remaining = apply.call_args.args[0].feedin_by_day_wh[MORNING.date().isoformat()]
    assert remaining == pytest.approx(12000 - 1 - 500 * 10 / 3600)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1] == (SETPOINT, -700)


@pytest.mark.parametrize(
    "invalid", ["missing", "failed", "empty", "invalid", "expired", "soc"]
)
async def test_unusable_plan_stops_export_and_never_uses_a_stale_schedule(
    hass, freezer, invalid
):
    c, calls = await exporting(hass, freezer)
    if invalid == "missing":
        c._last_planner_recording = None
    elif invalid == "failed":
        c.last_update_success = False
    elif invalid == "empty":
        c.data = {}
    elif invalid == "invalid":
        c.data = {**c.data, "valid": False}
    elif invalid == "expired":
        _, inputs, _ = c._last_planner_recording
        freezer.move_to(
            inputs.slots[0].start + timedelta(hours=inputs.slots[0].duration)
        )
    else:
        hass.states.async_set("sensor.test_soc", "unavailable")
        c._last_valid_soc = None
    with patch.object(c, "_schedule_replan") as schedule:
        await async_fast_update(c)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1] == (SETPOINT, 0)
    assert schedule.call_count == int(invalid in {"expired", "soc"})


async def test_pv_loss_stops_export_without_waiting_for_a_new_plan(hass, freezer):
    c, calls = await exporting(hass, freezer)
    hass.states.async_set("sensor.pv_today", "unavailable")
    c._last_valid_forecasts = None
    await async_fast_update(c)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1] == (SETPOINT, 0)


async def test_manual_setpoint_is_preserved_and_requests_a_new_forecast(hass, freezer):
    c, calls = await exporting(hass, freezer)
    hass.states.async_set(SETPOINT, "-333")
    with patch.object(c, "_schedule_replan") as schedule:
        await async_fast_update(c, trim=False)
    assert c.feedin_manual()
    assert c.data["feedin_mode"] == "manual"
    assert hass.states.get(SETPOINT).state == "-333"
    assert calls == []
    schedule.assert_called_once()


async def test_fast_trim_runs_while_economic_planning_is_blocked(hass, freezer):
    c, calls = await exporting(hass, freezer)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()
        return c.data

    with patch.object(c, "_async_update_data_serial", side_effect=blocked):
        task = asyncio.create_task(c._async_update_data())
        await entered.wait()
        hass.states.async_set(BATT_POWER, "-200")
        await async_fast_update(c)
        await hass.async_block_till_done(wait_background_tasks=False)
        assert calls == [(SETPOINT, -300)]
        assert not task.done()
        release.set()
        await task


async def test_appliance_standby_power_does_not_plan_but_a_real_start_does(
    hass, freezer
):
    freezer.move_to(MORNING)
    entry, key = _entry(hass, detection_entity="sensor.power", power_threshold_w=100)
    hass.states.async_set("sensor.power", "0.7", {"unit_of_measurement": "W"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = hass.data[DOMAIN][entry.entry_id]
    with patch.object(c, "_async_plan", wraps=c._async_plan) as planner:
        hass.states.async_set("sensor.power", "1.3", {"unit_of_measurement": "W"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert planner.call_count == 0
        assert c.appliances.entity_snapshot(key)["observation"]["power_w"] == 1.3
        hass.states.async_set("sensor.power", "600", {"unit_of_measurement": "W"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert planner.call_count > 0
        assert c.appliances.entity_snapshot(key)["status"] == "running"


async def test_new_pv_forecast_still_triggers_full_planning(hass, freezer):
    c, _ = await exporting(hass, freezer)
    c._listeners_setup = True
    with patch.object(c, "_async_plan", wraps=c._async_plan) as planner:
        hass.states.async_set("sensor.pv_today", "9")
        await hass.async_block_till_done(wait_background_tasks=True)
    assert planner.call_count > 0


@pytest.mark.parametrize("soc", ["54", "52", "58"])
async def test_normal_soc_evolution_keeps_plan_and_export_until_periodic_refresh(
    hass, freezer, soc
):
    c, calls = await exporting(hass, freezer)
    metadata = dict(c.data["plan_metadata"])
    c._listeners_setup = True
    with patch.object(c, "_async_plan", side_effect=AssertionError("CPU search")):
        hass.states.async_set("sensor.test_soc", soc)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert c.data["plan_metadata"] == metadata
    assert c._debounce_task is None
    assert calls == []
    assert hass.states.get(SETPOINT).state == "-500.0"


@pytest.mark.parametrize("soc", ["30", "20", "23"])
async def test_soc_threshold_crossing_stops_export_and_requests_full_plan(
    hass, freezer, soc
):
    c, calls = await exporting(hass, freezer)
    c._listeners_setup = True
    with patch.object(c, "_schedule_replan") as schedule:
        hass.states.async_set("sensor.test_soc", soc)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1] == (SETPOINT, 0)
    schedule.assert_called_once()


async def test_soc_floor_protection_does_not_wait_for_fast_debounce(hass, freezer):
    c, calls = await exporting(hass, freezer)
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting():
        entered.set()
        await release.wait()
        await async_fast_update(c, trim=False)

    c._listeners_setup = True
    with (
        patch.object(c, "_debounced_fast_update", side_effect=waiting),
        patch.object(c, "_schedule_replan"),
    ):
        hass.states.async_set("sensor.test_soc", "20")
        await entered.wait()
        await c._planning_protection_task
        await c._feedin_task
        assert calls[-1] == (SETPOINT, 0)
        assert c.data["floor_guard_active"]
        assert not c._fast_update_task.done()
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_five_minute_poll_replans_from_current_soc(hass, freezer):
    c, _ = await exporting(hass, freezer)
    # The first two successful plans establish startup readiness; only then
    # does the existing 30-second startup cadence become the regular poll.
    await c.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    c._listeners_setup = True
    with patch.object(c, "_async_plan", wraps=c._async_plan) as planner:
        hass.states.async_set("sensor.test_soc", "52")
        await hass.async_block_till_done(wait_background_tasks=True)
        assert planner.call_count == 0
        assert c.update_interval == timedelta(minutes=5)
        freezer.tick(timedelta(minutes=5))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        assert planner.call_count > 0
        assert c._last_planner_recording[1].start_soc_percent == 52


async def test_fast_debounce_uses_the_production_delay():
    c = SimpleNamespace(_fast_trim_pending=True)
    with (
        patch(
            "custom_components.battery_manager.coordinator.DEBOUNCE_SECONDS",
            DEBOUNCE_SECONDS,
        ),
        patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
        patch(
            "custom_components.battery_manager.fast_updates.async_fast_update",
            new_callable=AsyncMock,
        ) as update,
    ):
        await BatteryManagerCoordinator._debounced_fast_update(c)
    sleep.assert_awaited_once_with(DEBOUNCE_SECONDS)
    update.assert_awaited_once_with(c, trim=True)


async def test_shutdown_ignores_telemetry(hass, freezer):
    c, calls = await exporting(hass, freezer)
    c._actuation_shutdown = True
    await async_fast_update(c)
    assert calls == []


async def test_completed_planning_cannot_rewind_the_live_export_integral(hass, freezer):
    c, _ = await exporting(hass, freezer)
    later = MORNING + timedelta(seconds=20)
    c._feedin_tick(later)
    before = c._feedin_delivered
    assert c._feedin_tick(MORNING + timedelta(seconds=10)) == before[1]
    assert c._feedin_delivered == before


async def test_previous_day_capture_cannot_reset_live_export_accounting(hass, freezer):
    c, _ = await exporting(hass, freezer)
    tomorrow = MORNING + timedelta(days=1)
    c._feedin_tick(tomorrow)
    c._feedin_tick(tomorrow + timedelta(seconds=20))
    before = c._feedin_delivered
    assert before[1] > 0
    c._feedin_tick(MORNING)
    assert c._feedin_delivered == before


async def test_soc_lost_after_validation_still_stops_export(hass, freezer):
    c, calls = await exporting(hass, freezer)
    with patch.object(c, "_get_soc", side_effect=[55, 55, None]):
        await async_fast_update(c)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1] == (SETPOINT, 0)


async def test_telemetry_does_not_duplicate_replan_while_worker_is_running(
    hass, freezer
):
    c, calls = await exporting(hass, freezer)
    freezer.tick(timedelta(hours=1))
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()
        return c.data

    with (
        patch.object(c, "_async_update_data_serial", side_effect=blocked),
        patch.object(c, "_schedule_replan") as schedule,
    ):
        task = asyncio.create_task(c._async_update_data())
        await entered.wait()
        await async_fast_update(c)
        await hass.async_block_till_done(wait_background_tasks=False)
        assert calls[-1] == (SETPOINT, 0)
        schedule.assert_not_called()
        release.set()
        await task


async def test_export_meter_updates_realized_energy_without_planning(hass, freezer):
    dt_util.set_default_time_zone(dt_util.UTC)
    freezer.move_to(MORNING)
    c, _ = await setup_realized(hass, at=MORNING)
    captured = c.data["plan_metadata"]
    c._listeners_setup = True
    freezer.tick(timedelta(seconds=30))
    with patch.object(c, "_async_plan", side_effect=AssertionError("CPU search")):
        hass.states.async_set(EXPORT, "5000.01", KWH)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert c.data["realized"]["true_export_total_kwh"] == 0.01
    assert c.data["plan_metadata"] == captured


async def test_finished_plan_accounts_meter_readings_at_publication(hass, freezer):
    dt_util.set_default_time_zone(dt_util.UTC)
    freezer.move_to(MORNING)
    c, _ = await setup_realized(hass, at=MORNING)
    entered, release = asyncio.Event(), asyncio.Event()
    plan = c._async_plan

    async def blocked(*args):
        entered.set()
        await release.wait()
        return await plan(*args)

    with patch.object(c, "_async_plan", side_effect=blocked):
        task = asyncio.create_task(c.async_refresh())
        await entered.wait()
        freezer.tick(timedelta(seconds=20))
        hass.states.async_set(EXPORT, "5000.01", KWH)
        await async_fast_update(c, trim=False)
        assert c.data["realized"]["true_export_total_kwh"] == 0.01
        freezer.tick(timedelta(seconds=20))
        hass.states.async_set(EXPORT, "5000.02", KWH)
        release.set()
        await task
    assert c.data["realized"]["true_export_total_kwh"] == 0.02
    assert c._realized_last_ts[EXPORT] == dt_util.now()


async def test_unload_cancels_a_waiting_fast_update(hass, freezer):
    c, calls = await exporting(hass, freezer)
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting():
        entered.set()
        await release.wait()
        await async_fast_update(c)

    c._fast_update_task = c.entry.async_create_background_task(
        hass, waiting(), name="test_waiting_telemetry"
    )
    task = c._fast_update_task
    await entered.wait()
    assert await hass.config_entries.async_unload(c.entry.entry_id)
    assert task.cancelled()
    assert calls == []
