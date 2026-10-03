"""Production-path regressions from the October review; physical state is independent."""

import asyncio
import threading
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util
from test_load_switching import ENABLE, PLUG, POWER_FEEDBACK, _detach_listener, _setup
from test_operation_recorder import coordinator as coordinator

from custom_components.battery_manager.core import plan
from custom_components.battery_manager.core.model import (
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SurplusLoadState,
    SystemConfig,
)
from custom_components.battery_manager.operation_archive import OperationArchive


@pytest.mark.parametrize("power", [".41", "unavailable"])
async def test_rejected_continuation_still_trips_g4_and_stops_minimum_runtime(
    hass, freezer, power
):
    freezer.move_to("2026-10-02T12:00:00Z")
    calls = []
    c, key, _ = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30, power_w=410
    )
    try:
        _detach_listener(c)
        hass.states.async_set(PLUG, "on")
        hass.states.async_set(ENABLE, "on")
        hass.states.async_set(POWER_FEEDBACK, power, {"unit_of_measurement": "kW"})
        c._load_charging_active[key] = True
        c._load_plug_owned[key] = True
        c._last_load_switch[key] = dt_util.utcnow() - timedelta(minutes=5)
        c._floor_guard_active = False
        c._inverter_recommendation = False
        now = dt_util.now()
        config = replace(
            c.build_system_config(),
            support=SupportParams(
                configured=True,
                coordinated=True,
                dc24_available=True,
                dc48_available=True,
                psu48_bus_voltage_v=50,
            ),
            reserve=ReserveParams(enabled=True),
        )
        inputs = PlanInputs(
            now,
            55,
            tuple(
                HourSlot(i, now + timedelta(hours=i), 1, (now.hour + i) % 24, 0, 50, 50)
                for i in range(2)
            ),
            load_states=(
                SurplusLoadState(key, minimum_run_until=now + timedelta(minutes=25)),
            ),
        )
        result = plan(config, inputs)
        assert not result.inverter_on
        assert not result.load_plans[0].active_now
        assert (
            "additional_import"
            in dict(result.load_plans[0].rejected_candidates).values()
        )
        actual_w = c._guard_running_load_power(result, config, now)
        assert actual_w == 410
        assert c._update_floor_guard(55, config, 0, actual_w, now)
        calls.clear()
        await c._apply_load_switching(result, now, (1, 1), 0)
        if c._switch_task:
            await c._switch_task
        assert not c._load_charging_active[key]
        assert hass.states.get(ENABLE).state == "off"
        assert ("turn_off", ENABLE) in calls
    finally:
        await hass.config_entries.async_unload(c.entry.entry_id)


@pytest.mark.parametrize("shutdown", [False, True])
async def test_archive_restore_worker_heartbeat_and_late_unload(
    hass, coordinator, monkeypatch, shutdown
):
    rec = coordinator.operation_recorder
    archive = OperationArchive(coordinator.hass.config.time_zone)
    for i in range(1000):
        archive.event(
            datetime(2026, 10, 2, tzinfo=UTC) + timedelta(seconds=i), "test", {"i": i}
        )
    data = archive.export()
    entered, release = threading.Event(), threading.Event()
    original = OperationArchive.restore

    def controlled(history, payload):
        entered.set()
        assert release.wait(5), "worker was not released"
        return original(history, payload)

    monkeypatch.setattr(OperationArchive, "restore", controlled)
    rec.storage.async_load = AsyncMock(return_value=None)
    old = rec.history
    task = asyncio.create_task(rec.async_restore(data))
    while not entered.is_set():
        await asyncio.sleep(0)
    heartbeat = asyncio.Event()
    hass.loop.call_soon(heartbeat.set)
    await heartbeat.wait()
    assert rec.history is old
    coordinator._actuation_shutdown = shutdown
    release.set()
    await task
    if shutdown:
        assert rec.history is old
    else:
        assert rec.export() == data


async def test_restore_failure_does_not_remove_small_runtime_obligations(coordinator):
    coordinator._load_plug_owned["b1"] = True
    await coordinator.operation_recorder.async_restore({"schema_version": -1})
    assert coordinator._load_plug_owned["b1"]
    assert coordinator.operation_recorder.last_error == "ValueError"
    assert coordinator.operation_recorder.summary()["last_error"] == "ValueError"


def test_known_zero_band_and_unknown_band_keep_separate_physical_coverage(
    coordinator, monkeypatch
):
    c = coordinator
    profiles = {
        "dc": {
            day: {"p50": [100.0] * 24, "p80": [100.0] * 24}
            for day in ("weekday", "weekend", "absence")
        }
    }
    monkeypatch.setattr(c.learner, "profiles_for_planning", lambda: profiles)
    c.learner.data["diagnostics"]["coverage_detail"] = {"dc": {"valid_hours": 24}}
    before = deepcopy(c.learner.data)
    ac, dc, band, active, diag = c._learned_series(
        datetime(2026, 10, 2, 23, 30, tzinfo=UTC), SystemConfig(), 1
    )
    assert ac is None and dc == (100.0,)
    assert active and band == {"ac": [0.0], "dc": [0.0]}
    assert diag["coverage_detail"]["ac"]["uncertainty_unknown_hours"] == 0.5
    assert diag["coverage_detail"]["dc"]["uncertainty_known_hours"] == 0.5
    assert diag["coverage_detail"]["dc"]["learned_duration_fraction"] == 1
    assert diag["coverage_detail"]["ac"]["learned_duration_fraction"] == 0
    assert c.learner.data == before
