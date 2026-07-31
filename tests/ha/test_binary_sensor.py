"""Support-path binary sensors: created only for configured paths.

An unconfigured support path must not leave a permanently-off corpse
entity behind, and a stale registry row from a previously configured
path is dropped on setup (mirrors the mode-sensor/switch handling in
sensor.py/switch.py).
"""

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    DOMAIN,
    ENTITY_SUPPORT_DC24,
    ENTITY_SUPPORT_DC48,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up (lingering-timer guard, same reason
    as in test_config_flow.py)."""
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _set_states(hass):
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})


def _register_switch_services(hass):
    """The coordinator switches via homeassistant.turn_on/turn_off."""

    async def _toggle(call):
        entity_id = call.data["entity_id"]
        hass.states.async_set(entity_id, "on" if call.service == "turn_on" else "off")

    hass.services.async_register("homeassistant", "turn_on", _toggle)
    hass.services.async_register("homeassistant", "turn_off", _toggle)


def _support_entity_id(hass, entry_id, entity_key):
    return er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry_id}_{entity_key}"
    )


async def test_unconfigured_paths_create_no_support_sensors(hass):
    """Without configured support switches no SupportPathSensor may exist."""
    _set_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC24) is None
    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC48) is None


async def test_configured_paths_create_support_sensors(hass):
    """A configured support path keeps its sensor (no over-pruning)."""
    _set_states(hass)
    _register_switch_services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            CONF_SUPPORT_DC24_SWITCH: "switch.psu_24v",
            CONF_DCDC_SWITCH: "switch.dcdc_converter",
            CONF_SUPPORT_DC48_SWITCH: "switch.psu_48v",
            CONF_SUPPORT_SWITCH_DELAY_S: 1,
        },
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC24) is not None
    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC48) is not None


async def test_stale_support_sensor_is_removed(hass):
    """A registry row left over from a previously configured path is dropped
    on setup instead of lingering as a permanently-off corpse."""
    _set_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    for entity_key in (ENTITY_SUPPORT_DC24, ENTITY_SUPPORT_DC48):
        ent_reg.async_get_or_create(
            "binary_sensor", DOMAIN, f"{entry.entry_id}_{entity_key}"
        )
    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC24) is not None

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC24) is None
    assert _support_entity_id(hass, entry.entry_id, ENTITY_SUPPORT_DC48) is None
