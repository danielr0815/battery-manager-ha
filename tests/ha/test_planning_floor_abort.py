"""Physical floor protection survives rejection of an economic SOC snapshot."""

from unittest.mock import AsyncMock

import pytest
from test_load_switching import ENABLE, PLUG, _detach_listener, _setup
from test_soc_coordinate_runtime import transition


@pytest.mark.parametrize("coordinate_changed", [False, True])
async def test_low_soc_during_calculation_sheds_real_load_before_plan_rejection(
    hass, monkeypatch, coordinate_changed
):
    calls = []
    c, load_id, _ = await _setup(hass, calls)
    _detach_listener(c)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    c._load_plug_owned[load_id] = True
    c._load_plug_owned_entities[load_id] = PLUG
    c._load_charging_active[load_id] = True
    calls.clear()
    previous_record = c._last_planner_recording
    economic_apply = AsyncMock(wraps=c.cascade_manager.async_apply)
    safety_off = AsyncMock(wraps=c.cascade_manager.async_safety_off_active)
    monkeypatch.setattr(c.cascade_manager, "async_apply", economic_apply)
    monkeypatch.setattr(c.cascade_manager, "async_safety_off_active", safety_off)
    reached_barrier = []

    async def changing_during_cpu(_hass, _plan, _phase, _config, inputs, protect):
        assert inputs.start_soc_percent == 55
        reached_barrier.append(True)
        hass.states.async_set("sensor.test_soc", "19", {"unit_of_measurement": "%"})
        if coordinate_changed:
            transition(c, "reconciling", new_epoch=True)
        # The production checkpoint protects physical supply before it rejects
        # stale numeric SOC or the independent coordinate identity.
        await protect()
        pytest.fail("Changed planning input must not publish an economic plan")

    monkeypatch.setattr(c._planning, "async_run", changing_during_cpu)
    await c.async_refresh()
    await hass.async_block_till_done()
    assert reached_barrier == [True]
    assert c._stale_shed_task is not None
    await c._stale_shed_task
    assert hass.states.is_state(ENABLE, "off")
    assert hass.states.is_state(PLUG, "off")
    assert c._floor_guard_active
    assert c.data["floor_guard_active"]
    assert c.data["inverter_recommendation"] is False
    assert c._last_planner_recording is previous_record
    assert not c._load_charging_active.get(load_id, False)
    safety_off.assert_awaited_once()
    assert safety_off.await_args.args[0] == "G4 floor guard"
    economic_apply.assert_not_awaited()
    assert ("turn_off", ENABLE) in calls
    assert ("turn_off", PLUG) in calls
    if coordinate_changed:
        assert c.data["planning_pending"] == "soc_coordinate_update"
    else:
        assert not c.last_update_success
