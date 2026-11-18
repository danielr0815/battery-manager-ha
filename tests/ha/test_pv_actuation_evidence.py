"""Automatic export holds only while the captured PV evidence remains valid."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import rig as rig
from test_fast_updates import unload as unload
from test_feedin import BATT_POWER, MORNING, SETPOINT, _reset_executor_baseline
from test_feedin import _setup as _setup_feedin
from test_reserve_policy import BLOCK, DCDC, LIMIT, PSU24, PSU48, configure, update

from custom_components.battery_manager.coordinator import PvPlanningEvidenceLost
from custom_components.battery_manager.fast_updates import async_fast_update

pytestmark = pytest.mark.freeze_time(real_asyncio=True)


def _lose_tomorrow(hass, freezer):
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set("sensor.pv_tomorrow", "0.01")


async def _export(
    hass,
    freezer,
    *,
    hourly=False,
    first_hour=3200,
    tomorrow_hourly=False,
    old_hourly=False,
):
    dt_util.set_default_time_zone(dt_util.UTC)
    freezer.move_to(MORNING)
    calls = []
    coordinator, _ = await _setup_feedin(hass, calls)
    _reset_executor_baseline(hass, coordinator, calls)
    today = MORNING.replace(minute=0, second=0)
    curve = {
        today.replace(hour=hour).isoformat(): 3200 if hour < 20 else 0
        for hour in range(24)
    }
    curve[MORNING.replace(minute=0).isoformat()] = first_hour
    attrs = (
        {
            "wh_period_ac": curve,
            "wh_period_ac_p10": {at: wh * 0.75 for at, wh in curve.items()},
            "wh_period_ac_p90": {at: wh * 1.1 for at, wh in curve.items()},
        }
        if hourly
        else {}
    )
    hass.states.async_set("sensor.pv_today", "64", {} if old_hourly else attrs)
    if old_hourly:
        published = MORNING - timedelta(hours=72)
        hass.states.async_set(
            "sensor.pv_day_after", "8", attrs, timestamp=published.timestamp()
        )
    tomorrow = today + timedelta(days=1)
    tomorrow_attrs = (
        {
            "wh_period_ac": {
                tomorrow.replace(hour=hour).isoformat(): 2000 if 8 <= hour < 12 else 0
                for hour in range(24)
            }
        }
        if tomorrow_hourly
        else {}
    )
    hass.states.async_set("sensor.pv_tomorrow", "8", tomorrow_attrs)
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    _, inputs, result = coordinator._last_planner_recording
    assert ((tomorrow.date(), 8.0) in inputs.pv_daily_credit) is not tomorrow_hourly
    assert result.feedin_schedule_w[0] > 200
    assert result.feedin_current_day_proven is hourly
    power = coordinator._feedin_read_power_w()
    assert power > 200
    calls.clear()
    return coordinator, calls, power


async def test_queued_export_uses_captured_pv_evidence(hass, freezer):
    coordinator, calls, _ = await _export(hass, freezer)
    _, inputs, _ = coordinator._last_planner_recording

    hass.states.async_set(BATT_POWER, "-200")
    async with coordinator._switch_lock:
        await async_fast_update(coordinator)
        assert coordinator._feedin_task is not None
        _lose_tomorrow(hass, freezer)
        assert coordinator._get_forecasts(inputs.now + timedelta(seconds=1))[0] > 0
        assert not coordinator.pv_evidence_valid(inputs.pv_daily_credit)
        assert calls == []

    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(SETPOINT, 0)]


async def test_late_export_confirmation_is_withdrawn_by_same_owner(hass, freezer):
    coordinator, calls, power = await _export(hass, freezer)
    entered, confirm = asyncio.Event(), asyncio.Event()

    async def set_value(call):
        value = float(call.data["value"])
        calls.append((call.data["entity_id"], value))
        if value:
            entered.set()
            await confirm.wait()
        hass.states.async_set(SETPOINT, str(value))

    hass.services.async_register("input_number", "set_value", set_value)
    hass.states.async_set(BATT_POWER, "-200")
    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator)
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        _lose_tomorrow(hass, freezer)
        with patch.object(coordinator, "_schedule_replan"):
            await async_fast_update(coordinator)
        assert calls == [(SETPOINT, -(power - 200))]
    finally:
        confirm.set()

    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [(SETPOINT, -(power - 200)), (SETPOINT, 0)]
    assert coordinator._feedin_owned_entity is None


async def test_lost_future_evidence_rejects_completed_slow_plan(hass, freezer):
    coordinator, calls, _ = await _export(hass, freezer)
    revision = coordinator.live_ac.plan_revision
    planner = coordinator._async_plan

    async def finishing_plan(phase, config, inputs):
        result = await planner(phase, config, inputs)
        assert coordinator.pv_evidence_valid(inputs.pv_daily_credit)
        _lose_tomorrow(hass, freezer)
        assert not coordinator.pv_evidence_valid(inputs.pv_daily_credit)
        return result

    with (
        patch.object(coordinator, "_async_plan", side_effect=finishing_plan),
        pytest.raises(PvPlanningEvidenceLost),
    ):
        await coordinator._async_update_data()

    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator._last_planner_recording is None
    assert (
        coordinator.live_ac.plan_revision == revision + 1
    )  # Revoke old; never publish new.
    assert coordinator._data_stale_since is None
    assert calls == [(SETPOINT, 0)]


@pytest.mark.parametrize("during_slow", [False, True])
async def test_hourly_covered_current_export_survives_loss_of_tomorrows_credit(
    hass,
    freezer,
    during_slow,
):
    coordinator, calls, power = await _export(hass, freezer, hourly=True)
    _, inputs, _ = coordinator._last_planner_recording
    assert inputs.now.date() not in dict(inputs.pv_daily_credit)
    if during_slow:
        planner = coordinator._async_plan

        async def finishing_plan(phase, config, inputs):
            result = await planner(phase, config, inputs)
            _lose_tomorrow(hass, freezer)
            return result

        with patch.object(coordinator, "_async_plan", side_effect=finishing_plan):
            await coordinator.async_refresh()
        assert isinstance(coordinator.last_exception, PvPlanningEvidenceLost)
        assert calls == []
    else:
        _lose_tomorrow(hass, freezer)
    assert not coordinator.pv_evidence_valid(inputs.pv_daily_credit)

    hass.states.async_set(BATT_POWER, "-200")
    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == [(SETPOINT, -(power - 200))]


async def test_loss_after_source_publication_revokes_live_envelope(rig, hass, freezer):
    coordinator, *_ = rig
    freezer.move_to(MORNING)
    configure(coordinator, hass, 80, sun=8)
    revision = coordinator.live_ac.plan_revision

    async def lose_evidence(_result, _now):
        assert coordinator.live_ac.envelope is not None
        assert coordinator.live_ac.plan_revision == revision + 1
        _lose_tomorrow(hass, freezer)

    with (
        patch.object(coordinator, "_update_tank_forecast", side_effect=lose_evidence),
        pytest.raises(PvPlanningEvidenceLost),
    ):
        await coordinator._async_update_data()

    assert coordinator.live_ac.envelope is None
    assert coordinator.live_ac.plan_revision == revision + 2
    assert coordinator._last_planner_recording is None
    assert not coordinator._inverter_recommendation


@pytest.mark.parametrize("key", ["wh_period_ac", "wh_period_ac_p10"])
async def test_revised_current_hourly_evidence_revokes_independent_export(
    hass,
    freezer,
    key,
):
    coordinator, calls, _ = await _export(hass, freezer, hourly=True)
    today = hass.states.get("sensor.pv_today")
    _lose_tomorrow(hass, freezer)
    attrs = {**today.attributes, key: dict.fromkeys(today.attributes[key], 0)}
    daily_total = "0" if key == "wh_period_ac" else today.state
    hass.states.async_set(today.entity_id, daily_total, attrs)

    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator, trim=False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == [(SETPOINT, 0)]


async def test_same_daily_sum_does_not_replace_lost_future_hourly_evidence(
    hass, freezer
):
    coordinator, calls, _ = await _export(hass, freezer, tomorrow_hourly=True)
    _, inputs, _ = coordinator._last_planner_recording
    tomorrow = hass.states.get("sensor.pv_tomorrow")
    freezer.tick(timedelta(seconds=1))
    previous = tomorrow.attributes["wh_period_ac"]
    # The same 8 kWh now arrives six hours later.
    shifted = {
        at: previous.get(
            (dt_util.parse_datetime(at) - timedelta(hours=6)).isoformat(), 0
        )
        for at in previous
    }
    hass.states.async_set(tomorrow.entity_id, tomorrow.state, {"wh_period_ac": shifted})
    assert sum(shifted.values()) == sum(previous.values()) == 8000
    assert coordinator.pv_evidence_valid(inputs.pv_daily_credit)

    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator, trim=False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == [(SETPOINT, 0)]


async def test_non_trim_telemetry_caps_already_applied_export_to_independent_basis(
    hass,
    freezer,
):
    coordinator, calls, power = await _export(
        hass, freezer, hourly=True, first_hour=800
    )
    config, inputs, result = coordinator._last_planner_recording
    assert power == pytest.approx(result.feedin_schedule_w[0], abs=0.05)
    assert power + 100 <= config.feedin.max_w
    await coordinator._execute_feedin(
        power + 100,
        "previous live adjustment",
        plan=(config, inputs),
        independent_limit_w=result.feedin_schedule_w[0],
    )
    assert calls == [(SETPOINT, -(power + 100))]
    _lose_tomorrow(hass, freezer)

    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator, trim=False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == [(SETPOINT, -(power + 100)), (SETPOINT, -power)]


async def test_unchanged_telemetry_does_not_project_hourly_forecasts_again(
    hass, freezer
):
    coordinator, calls, power = await _export(hass, freezer, hourly=True)
    hass.states.async_set(BATT_POWER, "-200")
    with (
        patch.object(
            coordinator,
            "_pv_slots_match",
            side_effect=AssertionError("unneeded projection"),
        ),
        patch.object(
            coordinator,
            "_get_pv_hourly",
            side_effect=AssertionError("unneeded parsing"),
        ),
    ):
        await async_fast_update(coordinator)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == [(SETPOINT, -(power - 200))]


async def test_hourly_evidence_memo_expires_even_without_a_sensor_event(hass, freezer):
    coordinator, calls, _ = await _export(
        hass,
        freezer,
        hourly=True,
        old_hourly=True,
    )
    config, inputs, _ = coordinator._last_planner_recording
    assert coordinator.pv_plan_evidence_valid(config, inputs, today_only=True)
    freezer.tick(timedelta(microseconds=1))

    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator, trim=False)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == [(SETPOINT, 0)]


def _erase_all_daily_pv(hass):
    for entity in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})


async def test_telemetry_revokes_published_source_plan_when_daily_pv_collapses(
    rig,
    hass,
    freezer,
):
    coordinator, *_ = rig
    freezer.move_to("2026-09-27T12:00:00+00:00")
    await hass.config.async_set_time_zone("Europe/Berlin")
    configure(coordinator, hass, 80, sun=10)
    coordinator._reserve_runtime.restore({"hold_soc": 0})
    await update(coordinator)
    assert coordinator.live_ac.envelope.dc_sources == (False, False)
    revision = coordinator.live_ac.plan_revision

    _erase_all_daily_pv(hass)
    with patch.object(coordinator, "_schedule_replan"):
        await async_fast_update(coordinator, trim=False)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert coordinator.live_ac.envelope is None
        assert coordinator.live_ac.plan_revision == revision + 1
        assert coordinator._last_planner_recording is None
        coordinator._last_support_switch = None
        await update(coordinator)

    assert coordinator.live_ac.envelope.dc_sources == (True, True)


async def test_source_removal_rechecks_evidence_after_native_rail_confirmation(
    rig,
    hass,
    freezer,
):
    coordinator, calls, *_ = rig
    freezer.move_to("2026-09-27T12:00:00+00:00")
    await hass.config.async_set_time_zone("Europe/Berlin")
    configure(coordinator, hass, 80, sun=10)
    coordinator._reserve_runtime.restore({"hold_soc": 0})
    coordinator._support_state = {"dc24": True, "dc48": True}
    for entity, on in ((PSU24, True), (PSU48, True), (DCDC, False), (BLOCK, True)):
        hass.states.async_set(entity, "on" if on else "off")
    hass.states.async_set(LIMIT, "0")
    switch = coordinator._switch_entity
    revoked_at = None

    async def intervene(entity, on, **kwargs):
        nonlocal revoked_at
        answer = await switch(entity, on, **kwargs)
        if (entity, on) == (DCDC, True) and revoked_at is None:
            _erase_all_daily_pv(hass)
            assert not coordinator.published_pv_authority_valid()
            revoked_at = len(calls)
        return answer

    with (
        patch.object(coordinator, "_switch_entity", side_effect=intervene),
        patch.object(coordinator, "_schedule_replan"),
    ):
        try:
            await update(coordinator)
        except PvPlanningEvidenceLost:
            if coordinator._switch_task:
                await coordinator._switch_task

    assert revoked_at is not None
    assert (PSU24, False) not in calls[revoked_at:]
