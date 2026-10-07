"""Initial setup and protection must not wait for a CPU-heavy economic plan."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_coordinated_actuation import LIMIT, PSU48
from test_coordinated_actuation import rig as rig
from test_coordinator import ENTRY_DATA, _set_input_states

from custom_components.battery_manager.const import DOMAIN
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.core.model import (
    HourSlot,
    PlanInputs,
    SystemConfig,
)
from custom_components.battery_manager.core.planning_control import PlanningCancelled
from custom_components.battery_manager.planning import (
    PLANNING_PROTECTION_INTERVAL_S,
    PlanningRunner,
)


def plan_inputs():
    now = datetime(2026, 9, 27)
    return PlanInputs(now, 50, (HourSlot(0, now, 1, 8, 0, 100, 10),))


async def test_runner_reports_phase_and_checks_protection_on_controlled_time():
    runner = PlanningRunner()
    assert runner.snapshot()["status"] == "idle"
    worker = asyncio.get_running_loop().create_future()
    hass = SimpleNamespace(async_add_executor_job=Mock(return_value=worker))
    result = object()
    protect = AsyncMock(side_effect=lambda: worker.set_result(result))
    real_wait = asyncio.wait
    waits = []

    async def controlled_wait(futures, *, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            assert runner.snapshot()["phase"] == "reserve"
            assert runner.snapshot()["elapsed_seconds"] >= 0
            return set(), futures
        return await real_wait(futures)

    with patch(
        "custom_components.battery_manager.planning.asyncio.wait", controlled_wait
    ):
        assert (
            await runner.async_run(
                hass, Mock(), "reserve", SystemConfig(), plan_inputs(), protect
            )
            is result
        )
    protect.assert_awaited_once()
    assert waits == [PLANNING_PROTECTION_INTERVAL_S] * 2
    assert runner.snapshot()["status"] == "complete"
    assert runner.snapshot()["phase"] is None
    assert runner.snapshot()["last_phase_seconds"]["reserve"] >= 0


async def test_runner_cancel_signals_cpu_work_and_drains_it():
    runner = PlanningRunner()
    worker = asyncio.get_running_loop().create_future()
    submitted = asyncio.Event()
    calls = []

    def submit(fn):
        calls.append(fn)
        submitted.set()
        return worker

    task = asyncio.create_task(
        runner.async_run(
            SimpleNamespace(async_add_executor_job=submit),
            Mock(),
            "reserve",
            SystemConfig(),
            plan_inputs(),
            AsyncMock(),
        )
    )
    await submitted.wait()
    task.cancel()
    # Wait until cancellation is handled using the worker await as a barrier.
    cancelled = asyncio.Event()
    real_shield = asyncio.shield

    def shield(future):
        cancelled.set()
        return real_shield(future)

    with patch("custom_components.battery_manager.planning.asyncio.shield", shield):
        await cancelled.wait()
        assert not worker.cancelled()
        with pytest.raises(PlanningCancelled) as error:
            calls[0]()
        worker.set_exception(error.value)
        with pytest.raises(asyncio.CancelledError):
            await task
    assert runner.snapshot()["status"] == "cancelled"
    assert worker.done()


async def test_runner_propagates_worker_failure_and_resets_phase():
    runner = PlanningRunner()
    worker = asyncio.get_running_loop().create_future()

    def submit(fn):
        try:
            fn()
        except ValueError as err:
            worker.set_exception(err)
        return worker

    with pytest.raises(ValueError, match="bad plan"):
        await runner.async_run(
            SimpleNamespace(async_add_executor_job=submit),
            Mock(side_effect=ValueError("bad plan")),
            "standard",
            SystemConfig(),
            plan_inputs(),
            AsyncMock(),
        )
    assert runner.snapshot()["status"] == "failed"
    assert runner.phase is None


@pytest.mark.parametrize("unload", [False, True])
async def test_setup_registers_entities_while_initial_plan_waits(hass, unload):
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real = BatteryManagerCoordinator._async_plan

    async def blocked(self, *args):
        entered.set()
        try:
            await release.wait()
            return await real(self, *args)
        finally:
            stopped.set()

    _set_input_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    with patch.object(BatteryManagerCoordinator, "_async_plan", blocked):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await entered.wait()
        assert entry.state is ConfigEntryState.LOADED
        assert (
            hass.states.get("sensor.battery_manager_soc_threshold").state
            == "unavailable"
        )
        coordinator = entry.runtime_data
        assert not coordinator.data["valid"]
        startup_events = {
            row["event"]
            for row in coordinator.startup_diagnostics.snapshot()["samples"]
        }
        assert {
            "archive_load:begin",
            "archive_load:end",
            "platform_setup:end",
        } <= startup_events
        assert coordinator.startup_diagnostics.active
        if unload:
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert stopped.is_set()
            assert coordinator._initial_refresh_task.done()
            assert not coordinator.startup_diagnostics.active
        else:
            release.set()
            await coordinator._initial_refresh_task
            await hass.async_block_till_done()
            assert coordinator.data["valid"]
            assert (
                hass.states.get("sensor.battery_manager_soc_threshold").state
                != "unavailable"
            )


async def test_failed_setup_stops_startup_sampler(hass):
    """A failed persistent restore cannot leave a tracing timer behind."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    captured = []

    async def fail(self):
        captured.append(self.startup_diagnostics)
        raise ValueError("damaged startup state")

    with patch.object(BatteryManagerCoordinator, "async_load_persistent_state", fail):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert not captured[0].active
    assert captured[0]._cancel is None
    assert captured[0].rows[-1]["event"] == "persistent_state_restore:aborted"


async def test_protection_stops_ac_and_enables_low_soc_support_without_a_plan(
    rig, hass
):
    c, calls, _, _ = rig
    c.raw_config["soc_entity"] = "sensor.low_soc"
    hass.states.async_set("sensor.low_soc", "5")
    assert c._last_planner_recording is None
    await c._async_planning_protection()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (LIMIT, 0) in calls
    assert (PSU48, True) in calls
    assert c._floor_guard_active
    assert c._last_planner_recording is None


async def test_update_serializes_work_and_discards_soc_changed_during_planning(hass):
    _set_input_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c.cleanup()  # Explicit test drives refresh, no sensor-triggered refreshes.
    original = c._async_plan
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def blocked(*args):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return await original(*args)

    c._async_plan = blocked
    task = asyncio.create_task(c._async_update_data())
    await started.wait()
    assert c._update_lock.locked()
    hass.states.async_set("sensor.test_soc", "10")
    release.set()
    with pytest.raises(UpdateFailed, match="inputs changed"):
        await task
    assert c._last_planner_recording is None
    assert not c._update_lock.locked()
    assert calls == 1
    await c.async_cancel_actuation_tasks()


async def test_direct_refreshes_never_run_two_planners_at_once(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c.cleanup()
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = 0
    maximum = 0

    async def work():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.set()
        await release.wait()
        active -= 1
        return {"valid": False}

    async def second():
        attempted.set()
        return await c._async_update_data()

    c._async_update_data_serial = work
    first = asyncio.create_task(c._async_update_data())
    await entered.wait()
    another = asyncio.create_task(second())
    await attempted.wait()
    assert maximum == 1
    release.set()
    assert await asyncio.gather(first, another) == [{"valid": False}] * 2
    assert maximum == 1
    await c.async_cancel_actuation_tasks()


async def test_unknown_grid_with_psus_off_does_not_interrupt_existing_ac_permission(
    rig, hass
):
    from custom_components.battery_manager.const import CONF_RESERVE_MODE

    c, calls, _, _ = rig
    c.raw_config["soc_entity"] = "sensor.high_soc"
    c.raw_config[CONF_RESERVE_MODE] = "active"
    hass.states.async_set("sensor.high_soc", "80")
    c._inverter_recommendation = True
    await c._async_planning_protection()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == []
    assert c._inverter_recommendation


async def test_expired_slot_and_shutdown_cannot_authorize_old_plan(hass):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    _set_input_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c.cleanup()
    now = dt_util.now()
    expired = PlanInputs(
        now - timedelta(hours=1),
        55,
        (HourSlot(0, now - timedelta(hours=1), 0.5, 8, 0, 100, 10),),
    )
    with pytest.raises(UpdateFailed, match="inputs changed"):
        c._ensure_planning_inputs(expired)
    c._actuation_shutdown = True
    with pytest.raises(asyncio.CancelledError):
        c._ensure_planning_inputs(expired)
    await c._async_planning_protection()
    await c.async_cancel_actuation_tasks()


def test_protection_interval_contract():
    """A quiet sensor must not leave protection behind a minutes-long plan."""
    assert PLANNING_PROTECTION_INTERVAL_S == 5.0


@pytest.mark.parametrize(
    ("initial", "current", "accepted"),
    [
        (80.0, 80.1, True),
        (80.0, 82.0, False),
        (20.1, 19.9, False),
        (9.9, 10.0, False),
        (80.0, None, False),
    ],
)
async def test_plan_freshness_tolerates_noise_but_never_threshold_crossings(
    hass, initial, current, accepted
):
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    _set_input_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c.cleanup()
    now = dt_util.now()
    inputs = PlanInputs(now, initial, (HourSlot(0, now, 1, 8, 0, 100, 10),))
    with patch.object(c, "_get_soc", return_value=current):
        if accepted:
            c._ensure_planning_inputs(inputs)
        else:
            with pytest.raises(UpdateFailed, match="inputs changed"):
                c._ensure_planning_inputs(inputs)
    await c.async_cancel_actuation_tasks()


async def test_publications_during_calculation_use_current_diagnostic_age(
    hass, freezer
):
    from datetime import UTC, timedelta

    now = datetime(2026, 10, 3, 9, tzinfo=UTC)
    freezer.move_to(now)
    _set_input_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c.raw_config["operation_pv_power_entity"] = "sensor.fresh_power"
    original = c._async_plan

    async def publish_during_work(*args):
        result = await original(*args)
        freezer.move_to(now + timedelta(seconds=90))
        hass.states.async_set("sensor.fresh_power", "500", {"unit_of_measurement": "W"})
        return result

    c._async_plan = publish_during_work
    try:
        data = await c._async_update_data()
        pv = next(row for row in data["source_health"] if row["role"] == "pv")
        assert pv["status"] == "available"
        assert pv["publication_age_s"] == 0
        assert datetime.fromisoformat(data["plan_metadata"]["captured_at"]) == now
        assert datetime.fromisoformat(
            data["plan_metadata"]["activated_at"]
        ) == now + timedelta(seconds=90)
    finally:
        await c.async_cancel_actuation_tasks()
        c.cleanup()
