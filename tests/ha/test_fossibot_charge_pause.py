"""Execute the Fossibot adapter in HA: renewable pause, wake and passthrough.

The number service is the hardware boundary. Blueprint expansion, automation
validation, state/event triggers and time-pattern scheduling are real HA code.
No productive timer period is waited in wall-clock time.
"""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_get_persistent_notifications,
)

from custom_components.battery_manager.const import DOMAIN

BLUEPRINT_PATH = (
    Path(__file__).parents[2]
    / "blueprints/automation"
    / DOMAIN
    / "fossibot_charge_pause.yaml"
)
ENABLE = "input_boolean.fossibot_charge"
PLUG = "switch.fossibot_input"
TIMER = "number.fossibot_delay_charge"
CHARGING = "binary_sensor.fossibot_grid_charging"
OUTPUT = "switch.fossibot_ac_output"
LIMIT = "number.fossibot_max_soc"
AUTOMATION = "automation.fossibot_charge_pause"


@pytest.fixture
def expanded_blueprint():
    """Use HA's own parser, blueprint schema and input substitution."""
    blueprint = Blueprint(
        load_yaml(str(BLUEPRINT_PATH)),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    assert blueprint.validate() is None
    inputs = BlueprintInputs(
        blueprint,
        {
            "alias": "Fossibot charge pause",
            "id": "fossibot_charge_pause",
            "use_blueprint": {
                "path": f"{DOMAIN}/fossibot_charge_pause.yaml",
                "input": {
                    "charge_enable": ENABLE,
                    "input_switch": PLUG,
                    "charge_delay": TIMER,
                    "grid_charging": CHARGING,
                },
            },
        },
    )
    inputs.validate()
    return inputs.async_substitute()


@pytest.fixture
async def adapter(hass, freezer, expanded_blueprint):
    """Start real automation and capture all hardware writes for assertions."""
    # Start after an even-minute boundary so the first renewal is in the
    # future, rather than an already-due callback at the frozen setup instant.
    freezer.move_to("2026-10-14T20:00:01Z")
    for entity_id, state in (
        (ENABLE, "off"),
        (PLUG, "on"),
        (TIMER, "0"),
        (CHARGING, "off"),
        (OUTPUT, "on"),
        (LIMIT, "90"),
    ):
        hass.states.async_set(entity_id, state)
    calls: list[ServiceCall] = []

    async def write(call):
        calls.append(call)
        if call.domain == "number" and call.data["entity_id"] == [TIMER]:
            hass.states.async_set(TIMER, str(call.data["value"]))

    for domain, services in (
        ("number", ("set_value",)),
        ("switch", ("turn_on", "turn_off", "toggle")),
        ("input_boolean", ("turn_on", "turn_off", "toggle")),
    ):
        for service in services:
            hass.services.async_register(domain, service, write)
    assert await async_setup_component(hass, "persistent_notification", {})
    assert await async_setup_component(
        hass, "automation", {"automation": [expanded_blueprint]}
    )
    await hass.async_start()
    await hass.async_block_till_done()
    assert hass.states.get(AUTOMATION).state == "on"
    assert _values(calls) == [10]
    calls.clear()
    yield calls
    await hass.services.async_call(
        "automation", "turn_off", {"entity_id": AUTOMATION}, blocking=True
    )
    await hass.async_block_till_done()


def _values(calls):
    """Every write must target only the timer; AC, socket and SOC are read-only."""
    for call in calls:
        assert (call.domain, call.service, call.data["entity_id"]) == (
            "number",
            "set_value",
            [TIMER],
        )
    return [call.data["value"] for call in calls]


async def _advance(hass, freezer, minutes):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def test_timer_contract_has_ten_minute_pause_and_two_minute_renewal(
    expanded_blueprint,
):
    """Bound the hardware pause and renewal period independently of virtual time."""
    renewal = next(
        trigger
        for trigger in expanded_blueprint["triggers"]
        if trigger["id"] == "renewal"
    )
    assert renewal == {
        "trigger": "time_pattern",
        "minutes": "/2",
        "id": "renewal",
    }
    assert expanded_blueprint["mode"] == "queued"
    failure = next(
        trigger
        for trigger in expanded_blueprint["triggers"]
        if trigger["id"] == "charge_pause_failed"
    )
    assert failure["for"] == {"minutes": 2}


@pytest.mark.parametrize("permission", ["off", "unknown", "unavailable"])
async def test_only_explicit_on_allows_charge_without_switching_passthrough(
    hass, adapter, permission
):
    """An unknown permission also pauses; only the timer is ever written."""
    hass.states.async_set(ENABLE, "on")
    await hass.async_block_till_done()
    hass.states.async_set(ENABLE, permission)
    await hass.async_block_till_done()
    assert _values(adapter) == [0, 10]
    assert hass.states.get(PLUG).state == "on"
    assert hass.states.get(OUTPUT).state == "on"
    assert hass.states.get(LIMIT).state == "90"


async def test_renewal_does_not_depend_on_countdown_telemetry(hass, freezer, adapter):
    """Even a cached, unchanged timer 10 is renewed well before expiry."""
    hass.states.async_set(TIMER, "10")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 1)
    assert _values(adapter) == []
    await _advance(hass, freezer, 1)
    assert _values(adapter) == [10]
    await _advance(hass, freezer, 2)
    assert _values(adapter) == [10, 10]


@pytest.mark.parametrize("unavailable", ["unknown", "unavailable"])
async def test_offline_device_is_reconciled_after_socket_wake(
    hass, freezer, adapter, unavailable
):
    """Helper-before-plug order survives unavailable telemetry and later return."""
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(TIMER, unavailable)
    hass.states.async_set(ENABLE, "on")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 2)
    assert _values(adapter) == []
    hass.states.async_set(PLUG, "on")
    await hass.async_block_till_done()
    assert _values(adapter) == []
    hass.states.async_set(TIMER, "9")
    await hass.async_block_till_done()
    assert _values(adapter) == [0]


@pytest.mark.parametrize("plug", ["off", "unknown", "unavailable"])
async def test_unpowered_or_unknown_socket_never_receives_device_commands(
    hass, freezer, adapter, plug
):
    """The adapter neither wakes the device nor trusts a cached numeric timer."""
    hass.states.async_set(PLUG, plug)
    hass.states.async_set(ENABLE, "on")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 2)
    hass.bus.async_fire("automation_reloaded")
    await hass.async_block_till_done()
    assert _values(adapter) == []
    assert hass.states.get(PLUG).state == plug


async def test_late_charging_feedback_reasserts_pause(hass, adapter):
    """Unexpected active AC charging causes another pause even at timer 10."""
    hass.states.async_set(TIMER, "10")
    hass.states.async_set(CHARGING, "on")
    await hass.async_block_till_done()
    assert _values(adapter) == [10]


async def test_reload_reconciles_without_helper_edge(hass, adapter):
    """Restored helper state is applied on automation reload, without a toggle."""
    hass.states.async_set(TIMER, "0")
    hass.bus.async_fire("automation_reloaded")
    await hass.async_block_till_done()
    assert _values(adapter) == [10]


async def test_failed_device_command_is_retried_by_renewal(hass, freezer, adapter):
    """A transport failure does not silently disable subsequent pause attempts."""
    attempts = []

    async def failing_write(call):
        attempts.append(call)
        raise HomeAssistantError("Device temporarily disconnected")

    hass.services.async_register("number", "set_value", failing_write)
    hass.bus.async_fire("automation_reloaded")
    await hass.async_block_till_done()
    assert _values(attempts) == [10]
    await _advance(hass, freezer, 2)
    assert _values(attempts) == [10, 10]
    assert hass.states.get(PLUG).state == "on"
    assert hass.states.get(OUTPUT).state == "on"


@pytest.mark.parametrize(
    ("entity_id", "state"), [(CHARGING, "off"), (ENABLE, "on"), (PLUG, "off")]
)
async def test_failed_pause_warning_survives_unavailable_timer_and_clears_on_recovery(
    hass, freezer, adapter, entity_id, state
):
    """Sustained charging is visible even when no command can reach the device."""
    hass.states.async_set(TIMER, "unavailable")
    hass.states.async_set(CHARGING, "on")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 1)
    assert async_get_persistent_notifications(hass) == {}
    await _advance(hass, freezer, 1)
    notifications = async_get_persistent_notifications(hass)
    assert len(notifications) == 1
    warning = next(iter(notifications.values()))
    assert warning["title"] == "Fossibot-Ladepause nicht bestätigt"
    assert _values(adapter) == []
    # A successful timer write is only a request, not charging-stop evidence.
    hass.states.async_set(TIMER, "10")
    await hass.async_block_till_done()
    assert len(async_get_persistent_notifications(hass)) == 1
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()
    assert async_get_persistent_notifications(hass) == {}


async def test_brief_feedback_lag_does_not_raise_failure_warning(
    hass, freezer, adapter
):
    """Ordinary delayed telemetry may settle within the two-minute grace."""
    hass.states.async_set(CHARGING, "on")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 1)
    hass.states.async_set(CHARGING, "off")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 2)
    assert async_get_persistent_notifications(hass) == {}
    assert set(_values(adapter)) == {10}


async def test_existing_charging_mismatch_is_detected_after_trigger_reload(
    hass, freezer, adapter
):
    """An already-true template at activation must not suppress the warning."""
    await hass.services.async_call(
        "automation", "turn_off", {"entity_id": AUTOMATION}, blocking=True
    )
    hass.states.async_set(CHARGING, "on")
    await hass.services.async_call(
        "automation", "turn_on", {"entity_id": AUTOMATION}, blocking=True
    )
    await hass.async_block_till_done()
    await _advance(hass, freezer, 1)
    assert async_get_persistent_notifications(hass) == {}
    await _advance(hass, freezer, 1)
    assert len(async_get_persistent_notifications(hass)) == 1
    assert _values(adapter) == [10]


async def test_old_failure_trigger_cannot_skip_new_feedback_grace(
    hass, freezer, adapter
):
    """A queued failure's trigger ID cannot carry old evidence past recovery."""
    hass.states.async_set(CHARGING, "on")
    await hass.async_block_till_done()
    await _advance(hass, freezer, 2)
    assert len(async_get_persistent_notifications(hass)) == 1
    hass.states.async_set(CHARGING, "off")
    await hass.async_block_till_done()
    assert async_get_persistent_notifications(hass) == {}
    hass.states.async_set(CHARGING, "on")
    await hass.async_block_till_done()
    # Feed the original delayed trigger into the real automation actions after
    # recovery. Queue ordering itself is exercised separately below.
    await hass.services.async_call(
        "automation",
        "trigger",
        {
            "entity_id": AUTOMATION,
            "variables": {"trigger": {"id": "charge_pause_failed"}},
        },
        blocking=True,
    )
    assert async_get_persistent_notifications(hass) == {}
    assert _values(adapter)[-1] == 10


@pytest.mark.parametrize("latest", ["off", "on"])
async def test_queued_triggers_use_latest_permission_without_cancelling_write(
    hass, adapter, latest
):
    """A slow hardware call completes; queued old intent cannot override latest."""
    entered = asyncio.Event()
    release = asyncio.Event()
    queued = {2: asyncio.Event(), 3: asyncio.Event()}
    writes = []

    async def slow_write(call):
        writes.append(call)
        if len(writes) == 1:
            entered.set()
            await release.wait()

    def observe_queue(event):
        if event.data["entity_id"] != AUTOMATION:
            return
        state = event.data["new_state"]
        if state is not None and (current := state.attributes.get("current")) in queued:
            queued[current].set()

    unsubscribe = hass.bus.async_listen("state_changed", observe_queue)
    hass.services.async_register("number", "set_value", slow_write)

    async def published(event):
        # Synchronize on HA's observable publication while a hardware call is
        # deliberately blocked; draining all HA work would wait for that call.
        for _ in range(100):
            if event.is_set():
                return
            await asyncio.sleep(0)
        pytest.fail(f"Queue state not published: {hass.states.get(AUTOMATION)}")

    try:
        if latest == "off":
            hass.bus.async_fire("automation_reloaded")
        else:
            hass.states.async_set(ENABLE, "on")
        await published(entered)
        hass.states.async_set(ENABLE, "on" if latest == "off" else "off")
        await published(queued[2])
        hass.states.async_set(ENABLE, latest)
        await published(queued[3])
        release.set()
        await hass.async_block_till_done()
    finally:
        release.set()
        unsubscribe()
    assert _values(writes) == ([10, 10, 10] if latest == "off" else [0, 0, 0])
