"""Tests for the bundled forecast card: resource registration + attributes."""

from types import SimpleNamespace

from homeassistant.components.lovelace.resources import ResourceStorageCollection
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import (
    CARD_URL,
    _async_register_card_resource,
)
from custom_components.battery_manager.const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


async def _setup_entry(hass):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _find_forecast_state(hass):
    for state in hass.states.async_all("sensor"):
        if "forecast" in state.attributes and "soc_threshold_percent" in (
            state.attributes
        ):
            return state
    return None


async def test_setup_without_lovelace_does_not_break(hass):
    """Card registration is optional sugar; the planner must come up anyway."""
    entry = await _setup_entry(hass)
    assert hass.data[DOMAIN][entry.entry_id].last_update_success


async def test_card_resource_created_updated_never_duplicated(hass):
    """Storage mode: resource is created once and updated on version change."""

    async def _empty_legacy_config(_force):
        return {}

    resources = ResourceStorageCollection(
        hass, SimpleNamespace(async_load=_empty_legacy_config)
    )
    hass.data["lovelace"] = SimpleNamespace(resources=resources)

    await _async_register_card_resource(hass, f"{CARD_URL}?v=0.4.0")
    items = resources.async_items()
    assert [i["url"] for i in items] == [f"{CARD_URL}?v=0.4.0"]

    # Same version again: no duplicate
    await _async_register_card_resource(hass, f"{CARD_URL}?v=0.4.0")
    assert len(resources.async_items()) == 1

    # New version: existing entry is updated in place
    await _async_register_card_resource(hass, f"{CARD_URL}?v=9.9.9")
    items = resources.async_items()
    assert [i["url"] for i in items] == [f"{CARD_URL}?v=9.9.9"]


async def test_card_resource_yaml_mode_skips_registry(hass):
    """Without a storage collection nothing must crash (frontend absent)."""
    hass.data["lovelace"] = SimpleNamespace(resources=None)
    await _async_register_card_resource(hass, f"{CARD_URL}?v=0.4.0")


async def test_soc_forecast_sensor_carries_plan_context(hass):
    """The forecast sensor must expose the full plan for the bundled card."""
    await _setup_entry(hass)

    state = _find_forecast_state(hass)
    assert state is not None
    attrs = state.attributes

    forecast = attrs["forecast"]
    assert len(forecast) > 1
    assert {"t", "soc"} <= set(forecast[0])

    assert attrs["soc_threshold_percent"] is not None
    assert attrs["battery_min_soc_percent"] == 5.0
    assert attrs["battery_max_soc_percent"] == 95.0
    assert attrs["inverter_min_soc_percent"] == 20.0
    assert attrs["soc_buffer_percent"] == 5.0
    assert attrs["grid_import_kwh"] is not None
    assert attrs["lost_surplus_kwh"] is not None
    assert isinstance(attrs["loads"], list)
