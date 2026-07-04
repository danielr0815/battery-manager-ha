"""Tests for make-before-break switching of the 24 V rail supplies.

Requirement N1a (docs/REQUIREMENTS.md): the 24 V rail must never be left
without a supply. Activation: PSU on -> delay -> DC/DC off. Deactivation:
DC/DC on -> delay -> PSU off. An unconfirmed new supply aborts the sequence.
"""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    DOMAIN,
)

PSU = "switch.psu_24v"
DCDC = "switch.dcdc_converter"

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
    CONF_SUPPORT_DC24_SWITCH: PSU,
    CONF_DCDC_SWITCH: DCDC,
    CONF_SUPPORT_SWITCH_DELAY_S: 1,
}


def _register_switch_services(hass, call_log, *, dead_entities=()):
    """Mock the cross-domain services that update states like real devices.

    The coordinator switches via `homeassistant.turn_on/turn_off` so that
    input_boolean entities work as well as switches.
    """

    async def turn_on(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_on", entity_id))
        if entity_id not in dead_entities:
            hass.states.async_set(entity_id, "on")

    async def turn_off(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_off", entity_id))
        if entity_id not in dead_entities:
            hass.states.async_set(entity_id, "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)


async def _setup(hass, call_log, *, dead_entities=()):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    _register_switch_services(hass, call_log, dead_entities=dead_entities)

    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_activation_turns_psu_on_before_dcdc_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        assert await coordinator._sequence_dc24(True, PSU) is True

    assert calls == [("turn_on", PSU), ("turn_off", DCDC)]


async def test_deactivation_turns_dcdc_on_before_psu_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        assert await coordinator._sequence_dc24(False, PSU) is True

    assert calls == [("turn_on", DCDC), ("turn_off", PSU)]


async def test_unconfirmed_new_supply_aborts_switchover(hass):
    """A dead PSU must never lead to the DC/DC being switched off."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, dead_entities=(PSU,))
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        assert await coordinator._sequence_dc24(True, PSU) is False

    assert calls == [("turn_on", PSU)]  # no turn_off — old supply stays on
    assert hass.states.get(DCDC).state == "on"


async def test_startup_adopts_real_switch_states(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")

    coordinator._sync_support_state_from_entities()
    assert coordinator._support_state["dc24"] is True

    # 'unavailable' (e.g. right after a restart) must never be read as 'off'.
    hass.states.async_set(PSU, "unavailable")
    coordinator._sync_support_state_from_entities()
    assert coordinator._support_state["dc24"] is True
