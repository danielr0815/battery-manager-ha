"""Tests for the direct charging-path control of surplus loads.

Spec: docs/LOAD_CONTROL.md — charging active = input plug on AND charge-enable
on; ownership rule / configurable input-off policy; last-known-SOC caching.
"""

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_AUTO,
    SUBENTRY_TYPE_LOAD,
)

PLUG = "switch.shelly_fossibot_input"
ENABLE = "input_boolean.charge_fossibot"
FOSSI_SOC = "sensor.fossibot_soc"

BASE_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


def _register_switch_services(hass, call_log):
    async def turn_on(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_on", entity_id))
        hass.states.async_set(entity_id, "on")

    async def turn_off(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_off", entity_id))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)


async def _setup(hass, call_log, *, policy=INPUT_OFF_POLICY_AUTO):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(FOSSI_SOC, "40", {"unit_of_measurement": "%"})
    _register_switch_services(hass, call_log)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=BASE_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={
                    CONF_LOAD_POWER_W: 300.0,
                    CONF_LOAD_ENERGY_LIMITED: True,
                    CONF_LOAD_CAPACITY_WH: 2000.0,
                    CONF_LOAD_TARGET_SOC: 90.0,
                    CONF_LOAD_SOC_ENTITY: FOSSI_SOC,
                    CONF_LOAD_CONTROL_SWITCH: PLUG,
                    CONF_LOAD_CHARGE_ENABLE: ENABLE,
                    CONF_LOAD_INPUT_OFF_POLICY: policy,
                },
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="Fossibot Test",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # The setup-time refresh may already have started charging (sunny test
    # day); reset so each test drives _execute_load_switching from a clean,
    # deterministic state.
    await hass.async_block_till_done()
    coordinator._load_plug_owned.clear()
    coordinator._load_charging_active.clear()
    coordinator._last_load_switch.clear()
    sub_id = next(iter(entry.subentries))
    data = dict(entry.subentries[sub_id].data)
    return coordinator, sub_id, data


async def test_start_owns_plug_and_stop_releases_it(hass):
    """Plug was off: we own it — stop turns enable AND plug off (auto)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, True, False)])
    assert calls == [("turn_on", ENABLE), ("turn_on", PLUG)]
    assert coordinator._load_plug_owned[sub_id] is True

    calls.clear()
    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert calls == [("turn_off", ENABLE), ("turn_off", PLUG)]
    assert coordinator._load_plug_owned[sub_id] is False


async def test_passthrough_plug_stays_on(hass):
    """Plug was already on (output passthrough): only the enable gate toggles."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "on")  # user automation powers the output
    hass.states.async_set(ENABLE, "off")
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, True, True)])
    assert calls == [("turn_on", ENABLE)]
    assert coordinator._load_plug_owned.get(sub_id, False) is False

    calls.clear()
    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert calls == [("turn_off", ENABLE)]  # plug untouched
    assert hass.states.get(PLUG).state == "on"


async def test_policy_always_off_switches_foreign_plug_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls, policy=INPUT_OFF_POLICY_ALWAYS)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert ("turn_off", PLUG) in calls


async def test_soc_cache_survives_sleeping_device(hass):
    """SOC unavailable (device asleep): planning keeps the last known value."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)

    states = coordinator._get_load_states()
    assert states[0].soc_percent == 40.0

    hass.states.async_set(FOSSI_SOC, "unavailable")
    states = coordinator._get_load_states()
    assert states[0].soc_percent == 40.0  # cached, not None/unavailable
    assert states[0].available is True

    hass.states.async_set(FOSSI_SOC, "62.5")
    states = coordinator._get_load_states()
    assert states[0].soc_percent == 62.5
