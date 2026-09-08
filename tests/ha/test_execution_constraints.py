"""Read-only executor projection and deadline semantics, all time virtual."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_MIN_RUNTIME_MIN,
    DOMAIN,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.execution import (
    execution_attributes,
    load_execution,
)

NOW = datetime(2026, 9, 8, 9, tzinfo=UTC)


@pytest.fixture
def c(hass, request):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"soc_entity": "sensor.soc"}, title="Projection"
    )
    entry.add_to_hass(hass)
    entry.subentries = {
        "load": ConfigSubentry(
            unique_id=None,
            subentry_type="surplus_load",
            title="Load",
            subentry_id="load",
            data={
                CONF_LOAD_CONTROL_SWITCH: "switch.load",
                CONF_LOAD_MIN_RUNTIME_MIN: 30,
            },
        )
    }
    coordinator = BatteryManagerCoordinator(hass, entry)
    coordinator._save_persistent_state = Mock()
    request.addfinalizer(coordinator.cleanup)
    return coordinator


def test_stability_deadline_does_not_slide_and_running_bypasses_wait(hass, c):
    data = c.entry.subentries["load"].data
    hass.states.async_set("switch.load", "off")
    initial = load_execution(c, "load", data, NOW)
    assert initial["predrain_not_before"] == NOW + timedelta(minutes=10)
    c._predrain_block_evidence["load"] = ((None, "end"), 2, NOW)
    later = load_execution(c, "load", data, NOW + timedelta(minutes=5))
    assert later["predrain_not_before"] == initial["predrain_not_before"]
    assert later["stable_plans"] == 2
    hass.states.async_set("switch.load", "on")
    c._last_load_switch["load"] = NOW
    running = load_execution(c, "load", data, NOW + timedelta(minutes=5))
    assert running["predrain_not_before"] is None
    assert running["minimum_run_until"] == NOW + timedelta(minutes=30)
    assert not running["confirmation_pending"]
    assert (
        execution_attributes(running)["minimum_run_until"]
        == running["minimum_run_until"].isoformat()
    )


@pytest.mark.parametrize("guard", ["floor", "stale", "calibration", "target"])
def test_safety_and_gate_target_override_remaining_dwell(hass, c, guard):
    data = c.entry.subentries["load"].data
    hass.states.async_set("switch.load", "on")
    c._last_load_switch["load"] = NOW
    if guard == "floor":
        c._floor_guard_active = True
    if guard == "stale":
        c._stale_shed_active = True
    if guard == "calibration":
        c._load_power_calibration_id = "load"
    if guard == "target":
        data.update(
            energy_limited=True,
            charge_enable_entity="switch.gate",
            soc_entity="sensor.load_soc",
            target_soc_percent=90,
        )
        hass.states.async_set("switch.gate", "on")
        hass.states.async_set("sensor.load_soc", "95")
    assert load_execution(c, "load", data, NOW)["minimum_run_until"] is None


def test_elapsed_start_boundary_preserves_stability_evidence(c):
    from types import SimpleNamespace

    from custom_components.battery_manager.core.model import HourSlot, LoadPlan

    start = NOW + timedelta(minutes=10)
    end = NOW + timedelta(hours=1)
    c._predrain_block_evidence["load"] = ((start.isoformat(), end.isoformat()), 2, NOW)
    inputs = SimpleNamespace(slots=(HourSlot(0, start, 50 / 60, 9, 500, 0, 0),))
    result = SimpleNamespace(
        load_plans=(LoadPlan("load", (True,), 250, allocations=((0, 1, 3, 250),)),)
    )
    c._update_predrain_block_evidence(result, inputs, start)
    assert "load" in c._predrain_block_stable
    assert c._predrain_block_evidence["load"][2] == NOW


def test_cascade_wake_deadline_is_not_a_start_promise(hass, c):
    entries = c.entry.subentries
    entries["load"].data.clear()
    entries["b1"] = ConfigSubentry(
        unique_id=None,
        subentry_type="surplus_load",
        title="Storage",
        subentry_id="b1",
        data={
            "output_switch_entity": "switch.output",
            "control_switch_entity": "switch.input",
            "charge_enable_entity": "switch.gate",
        },
    )
    entries["chain"] = ConfigSubentry(
        unique_id=None,
        subentry_type="cascade",
        title="Chain",
        subentry_id="chain",
        data={"member_load_ids": ["b1"], "terminal_load_id": "load"},
    )
    state = c.cascade_manager._state("chain")
    state.update(
        enabled=True,
        phase="waking_members",
        wake_deadline=(NOW + timedelta(seconds=60)).isoformat(),
    )
    hass.states.async_set("switch.output", "on")
    projection = load_execution(c, "load", {}, NOW)
    assert projection["not_before"] is None
    assert projection["check_at"] == NOW + timedelta(seconds=60)
    assert projection["confirmation_pending"]
    state.update(phase="root")
    assert not load_execution(c, "load", {}, NOW)["confirmation_pending"]
    hass.states.async_set("switch.output", "unavailable")
    assert load_execution(c, "load", {}, NOW)["confirmation_pending"]
    state.update(phase="proving", source="b1")
    c.cascade_manager._proof["chain"] = {"started": NOW}
    assert load_execution(c, "load", {}, NOW)["check_at"] == NOW + timedelta(
        seconds=180
    )
    state.update(restart_reconcile_pending=True)
    assert load_execution(c, "load", {}, NOW)["phase"] == "restart_reconciliation"
