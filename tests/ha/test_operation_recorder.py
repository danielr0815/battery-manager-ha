"""HA observation boundary, configured sources, commands and persistence."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import DOMAIN
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.operation_recorder import OperationRecorder


@pytest.fixture
def coordinator(hass, request):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"soc_entity": "sensor.soc", "operation_pv_power_entity": "sensor.pv"},
        title="Recorder",
    )
    entry.add_to_hass(hass)
    entry.subentries = {
        "b1": ConfigSubentry(
            unique_id=None,
            subentry_type="surplus_load",
            title="Storage",
            data={
                "control_switch": "switch.root",
                "charge_enable_entity": "input_boolean.charge",
                "output_switch": "switch.output",
                "power_entity": "sensor.charge",
            },
            subentry_id="b1",
        ),
        "leaf": ConfigSubentry(
            unique_id=None,
            subentry_type="surplus_load",
            title="Terminal",
            data={"power_entity": "sensor.terminal"},
            subentry_id="leaf",
        ),
        "chain": ConfigSubentry(
            unique_id=None,
            subentry_type="cascade",
            title="Chain",
            data={"member_load_ids": ["b1"], "terminal_load_id": "leaf"},
            subentry_id="chain",
        ),
    }
    c = BatteryManagerCoordinator(hass, entry)
    c._save_persistent_state = Mock()
    request.addfinalizer(c.cleanup)
    return c


async def test_sources_capture_values_and_passive_edges_without_replan(
    hass, coordinator, monkeypatch
):
    from custom_components.battery_manager.const import (
        CONF_LOAD_CHARGE_ENABLE,
        CONF_LOAD_CONTROL_SWITCH,
        CONF_LOAD_OUTPUT_SWITCH,
        SUBENTRY_TYPE_CASCADE,
        SUBENTRY_TYPE_LOAD,
    )

    # Use the integration's actual keys; no assumptions about user-facing labels.
    coordinator.entry.subentries["b1"].data.update(
        {
            CONF_LOAD_CONTROL_SWITCH: "switch.root",
            CONF_LOAD_CHARGE_ENABLE: "input_boolean.charge",
            CONF_LOAD_OUTPUT_SWITCH: "switch.output",
        }
    )
    assert coordinator.entry.subentries["b1"].subentry_type == SUBENTRY_TYPE_LOAD
    assert coordinator.entry.subentries["chain"].subentry_type == SUBENTRY_TYPE_CASCADE
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    hass.states.async_set(
        "sensor.pv", "0.5", {"unit_of_measurement": "kW", "secret": "never archive"}
    )
    hass.states.async_set("switch.root", "on")
    hass.states.async_set("input_boolean.charge", "on")
    hass.states.async_set("switch.output", "off")
    rec = coordinator.operation_recorder
    rec.start()
    assert rec.history.events[-1]["data"]["measurements"]["pv"]["unit"] == "kW"
    assert rec.history.events[-1]["data"]["actors"]["load:leaf"] is False
    assert "secret" not in str(rec.export())
    hass.states.async_set("switch.output", "on")
    await hass.async_block_till_done()
    changes = [row for row in rec.history.events if row["kind"] == "state_changed"]
    assert changes[-1]["data"]["new"] == "on"
    assert rec.history.events[-1]["data"]["actors"]["load:leaf"] is True
    hass.states.async_remove("switch.output")
    await hass.async_block_till_done()
    assert rec.history.events[-1]["data"]["actors"]["load:leaf"] is None
    assert coordinator._save_persistent_state.call_count == 1
    now += timedelta(seconds=61)
    rec.sample()
    assert coordinator._save_persistent_state.call_count == 2
    rec.stop()
    count = len(rec.history.events)
    hass.states.async_set("switch.output", "off")
    await hass.async_block_till_done()
    assert len(rec.history.events) == count
    assert "days" in rec.summary()


async def test_corrupt_archive_is_isolated_and_persistent_payload_roundtrips(
    coordinator,
):
    rec = coordinator.operation_recorder
    rec.restore({"schema_version": 99})
    assert rec.last_error == "ValueError"
    rec.event("test", {"value": 1})
    payload = coordinator._persistent_payload()
    restored = OperationRecorder(coordinator)
    restored.restore(payload["operation_history"])
    assert restored.export() == rec.export()
    rec.restore(None)
    assert rec.export()["sequence"] > 0


async def test_switch_command_results_are_correlated_and_not_confirmation(
    hass, coordinator
):
    async def success(call):
        return None

    hass.services.async_register("homeassistant", "turn_on", success)
    assert await coordinator._switch_entity("switch.free", True)
    rows = coordinator.operation_recorder.history.events
    request, result = rows[-2:]
    assert request["kind"] == "command_requested"
    assert result["data"]["request"] == request["sequence"]
    assert result["data"]["success"] is True
    assert hass.states.get("switch.free") is None

    async def failure(call):
        raise HomeAssistantError("failure")

    hass.services.async_register("homeassistant", "turn_off", failure)
    assert not await coordinator._switch_entity("switch.free", False)
    assert rows[-1]["data"]["success"] is False
    hass.services.async_register("input_number", "set_value", success)
    assert await coordinator._set_number_value("input_number.limit", 100)
    assert rows[-2]["data"]["value"] == 100
    hass.services.async_register("input_number", "set_value", failure)
    assert not await coordinator._set_number_value("input_number.limit", 0)


async def test_listener_removal_creation_and_missing_roles(hass, coordinator):
    rec = coordinator.operation_recorder
    rec.start()
    hass.states.async_set("sensor.pv", "100", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert any(
        row["data"].get("old") is None
        for row in rec.history.events
        if row["kind"] == "state_changed"
    )
    rec.stop()
    coordinator.entry.subentries.clear()
    rec.sample()
    assert rec.history.events[-1]["data"]["actors"] == {}


async def test_successful_plan_opens_journal_and_uses_cascade_input_boundary(
    coordinator,
):
    from custom_components.battery_manager.const import CONF_LOAD_SOC_ENTITY
    from tests.ha.test_operation_history import planned

    coordinator.entry.subentries["b1"].data[CONF_LOAD_SOC_ENTITY] = "sensor.storage_soc"
    rec = coordinator.operation_recorder
    rec.plan(*planned())
    assert rec.history.events[-1]["kind"] == "plan"
    assert rec.history.events[-1]["data"]["version"] == coordinator.integration_version
    assert "cascade_input:b1" in rec.summary()["sources"]
    assert "load:b1" not in rec.summary()["sources"]
    assert rec.summary()["sources"]["soc:b1"] == "sensor.storage_soc"


@pytest.mark.parametrize("number", [False, True])
async def test_cancelled_services_leave_correlated_evidence(hass, coordinator, number):
    import asyncio

    async def cancel(call):
        raise asyncio.CancelledError

    domain, service = (
        ("input_number", "set_value") if number else ("homeassistant", "turn_on")
    )
    hass.services.async_register(domain, service, cancel)
    with pytest.raises(asyncio.CancelledError):
        if number:
            await coordinator._set_number_value("input_number.limit", 10)
        else:
            await coordinator._switch_entity("switch.free", True)
    request, cancelled = coordinator.operation_recorder.history.events[-2:]
    assert cancelled["kind"] == "command_cancelled"
    assert cancelled["data"]["request"] == request["sequence"]


async def test_recording_and_save_failures_do_not_block_switching(
    hass, coordinator, monkeypatch
):
    from tests.ha.test_operation_history import planned

    rec = coordinator.operation_recorder
    rec.start()
    rec.plan(*planned())
    monkeypatch.setattr(
        rec.history, "activate_plan", Mock(side_effect=ValueError("oversized"))
    )
    rec.plan(*planned())
    assert rec.history.events[-1]["kind"] == "observation_break"
    assert rec.history.events[-1]["data"]["reason"] == "plan_recording_failed"
    assert rec.history._record is None
    rec._saved_at = None
    coordinator._save_persistent_state.side_effect = OSError("store unavailable")

    async def switch(call):
        hass.states.async_set("switch.free", "on")

    hass.services.async_register("homeassistant", "turn_on", switch)
    assert await coordinator._switch_entity("switch.free", True)
    assert hass.states.get("switch.free").state == "on"
    assert rec.last_error == "OSError"


async def test_downstream_charge_needs_upstream_output_and_summary_is_defensive(
    hass, coordinator
):
    from dataclasses import replace

    from custom_components.battery_manager.const import CONF_LOAD_OUTPUT_SWITCH

    entries = coordinator.entry.subentries
    entries["b1"].data[CONF_LOAD_OUTPUT_SWITCH] = "switch.output"
    entries["b2"] = replace(
        entries["b1"],
        subentry_id="b2",
        title="B2",
        data={"charge_enable_entity": "switch.b2_gate"},
    )
    entries["chain"].data["member_load_ids"] = ["b1", "b2"]
    hass.states.async_set("switch.b2_gate", "on")
    hass.states.async_set("switch.output", "off")
    rec = coordinator.operation_recorder
    rec.sample()
    assert rec.history.events[-1]["data"]["actors"]["load:b2"] is False
    hass.states.async_set("switch.output", "on")
    rec.sample()
    assert rec.history.events[-1]["data"]["actors"]["load:b2"] is True
    summary = rec.summary()
    summary["days"][0]["switch_requests"] = 5000
    assert rec.summary()["days"][0]["switch_requests"] == 0
