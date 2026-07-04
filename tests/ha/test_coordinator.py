"""Tests for the Battery Manager coordinator wiring."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

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


def _set_input_states(hass):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})


async def _setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    _set_input_states(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_coordinator_with_active_listeners(hass):
    """Entry setup must arm the entity-change listeners (regression: was never set)."""
    entry = await _setup_entry(hass)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._listeners_setup is True
    assert coordinator._unsub_state_listener is not None
    assert coordinator.last_update_success


async def test_entity_change_schedules_debounced_update(hass):
    """A SOC state change must schedule a debounced refresh task."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator._debounce_task is None
    hass.states.async_set(
        "sensor.test_soc", "60", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    await hass.async_block_till_done(wait_background_tasks=False)

    assert coordinator._debounce_task is not None
    coordinator._debounce_task.cancel()


async def test_unload_releases_listeners(hass):
    """Unloading the entry must unsubscribe listeners and clear state."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert coordinator._listeners_setup is False
    assert coordinator._unsub_state_listener is None
