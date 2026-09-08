"""Exact runtime releases and event-driven replanning, without real delays."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import coordinator as coordinator_module
from custom_components.battery_manager.const import (
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_MIN_OFF_MIN,
    DOMAIN,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.core import (
    CascadeSlotFlow,
    CascadeSourceSegment,
    HourSlot,
    PlanInputs,
)
from custom_components.battery_manager.core.model import LoadPlan


@pytest.fixture
def coordinator(hass, request):
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Timing")
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    request.addfinalizer(c.cleanup)
    return c


@pytest.mark.parametrize("minute", [15, 30, 45])
async def test_confirmed_minimum_off_projects_exact_release(hass, coordinator, minute):
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    hass.states.async_set("switch.load", "off")
    data = {CONF_LOAD_CONTROL_SWITCH: "switch.load", CONF_LOAD_MIN_OFF_MIN: minute}
    coordinator._last_load_switch["load"] = now
    assert coordinator._load_not_before("load", data, now) == now + timedelta(
        minutes=minute
    )
    assert (
        coordinator._load_not_before("load", data, now + timedelta(minutes=minute))
        is None
    )
    hass.states.async_set("switch.load", "on")
    assert coordinator._load_not_before("load", data, now) is None
    assert coordinator._load_not_before("missing", data, now) is None


async def test_planning_does_not_spend_flicker_continuation_budget(hass, coordinator):
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    hass.states.async_set("switch.load", "off")
    data = {CONF_LOAD_CONTROL_SWITCH: "switch.load", CONF_LOAD_MIN_OFF_MIN: 15}
    off = now - timedelta(seconds=5)
    coordinator._last_load_switch["load"] = off
    coordinator._load_last_off["load"] = (off, True)
    assert coordinator._load_not_before("load", data, now) is None
    assert coordinator._load_not_before("load", data, now) is None
    assert coordinator._load_flicker_hist == {}
    assert coordinator._flicker_continuation_ok("load", now, off)
    assert coordinator._load_flicker_hist == {"load": [off]}


@pytest.mark.parametrize("kind", ["release", "run_end", "aux_start", "aux_end"])
async def test_exact_plan_boundary_requests_fresh_plan(
    hass, coordinator, monkeypatch, kind
):
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    slot = HourSlot(0, now, 1, 9, 1000, 0, 0)
    slots = (
        (slot, HourSlot(1, now + timedelta(minutes=45), 0.25, 9, 250, 0, 0))
        if kind == "release"
        else (slot,)
    )
    inputs = PlanInputs(now, 80, slots)
    segment = CascadeSourceSegment(
        0, 0.25 if kind == "aux_start" else 0, 0.5, "aux", "b1", False, 150
    )
    result = SimpleNamespace(
        load_plans=(LoadPlan("load", (True,), 150, run_hours=(0.5,)),)
        if kind == "run_end"
        else (),
        cascade_plans=(SimpleNamespace(flows=(CascadeSlotFlow(segments=(segment,)),)),)
        if kind.startswith("aux")
        else (),
    )
    timer = Mock(return_value=Mock())
    monkeypatch.setattr(coordinator_module, "async_track_point_in_time", timer)
    coordinator.async_request_refresh = AsyncMock()
    coordinator._arm_plan_boundary(inputs, result)
    expected = {"release": 45, "run_end": 30, "aux_start": 15, "aux_end": 30}[kind]
    assert timer.call_args.args[2] == now + timedelta(minutes=expected)
    timer.call_args.args[1](now + timedelta(minutes=expected))
    await hass.async_block_till_done()
    coordinator.async_request_refresh.assert_awaited_once()
    assert coordinator._plan_boundary_cancel is None


async def test_replan_replaces_timer_and_shutdown_cancels_it(coordinator, monkeypatch):
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    inputs = PlanInputs(
        now,
        80,
        (
            HourSlot(0, now, 1, 9, 1000, 0, 0),
            HourSlot(1, now + timedelta(hours=1), 1, 10, 1000, 0, 0),
        ),
    )
    result = SimpleNamespace(load_plans=(), cascade_plans=())
    cancels = [Mock(), Mock()]
    timer = Mock(side_effect=cancels)
    monkeypatch.setattr(coordinator_module, "async_track_point_in_time", timer)
    coordinator._arm_plan_boundary(inputs, result)
    coordinator._arm_plan_boundary(inputs, result)
    cancels[0].assert_called_once()
    await coordinator.async_cancel_actuation_tasks()
    cancels[1].assert_called_once()
    coordinator._arm_plan_boundary(inputs, result)
    assert timer.call_count == 2
    timer.call_args.args[1](now)
    assert coordinator._plan_boundary_cancel is None


async def test_empty_horizon_has_no_timer(coordinator, monkeypatch):
    timer = Mock()
    monkeypatch.setattr(coordinator_module, "async_track_point_in_time", timer)
    coordinator._arm_plan_boundary(
        PlanInputs(datetime(2026, 9, 8, 9, tzinfo=UTC), 80, ()),
        SimpleNamespace(load_plans=(), cascade_plans=()),
    )
    timer.assert_not_called()


async def test_split_slots_keep_original_hour_uncertainty(coordinator):
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    config = coordinator.build_system_config()
    source = (now, now + timedelta(hours=1))
    slots = (
        HourSlot(0, now, 0.75, 9, 0, 100, 0),
        HourSlot(1, now + timedelta(minutes=45), 0.25, 9, 0, 100, 0),
        HourSlot(2, source[1], 1, 10, 0, 100, 0),
    )
    _, diag = coordinator._dynamic_buffer(
        config, slots, {"ac": [20, 100], "dc": [0, 0]}, source_starts=source
    )
    assert diag["buffer_uncertainty_wh"] == round(
        120 / (config.battery.eta_discharge * config.inverter.eta)
    )
    assert diag["buffer_window_hours"] == 2
