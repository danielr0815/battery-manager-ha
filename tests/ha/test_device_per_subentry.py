"""Regression test: HA 2026.8 'one device per config subentry' breaking change.

Spec: https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/
(core PR #175785). The integration used to share ONE device between the config
entry and all load/appliance subentries (BatteryManagerEntity set
``identifiers={(DOMAIN, entry_id)}`` for every entity and the platforms added
subentry entities with ``config_subentry_id``). Under HA 2026.8.0 the shared
device ping-ponged between the entry level and the subentries on every
``async_add_entities`` call; each move made the entity registry remove the
entities of the 'old' side — almost all BM entities vanished from the state
machine (production incident: only the last-added subentry switch survived).
Fix: one device per subentry (``identifiers={(DOMAIN, f"{entry_id}_{subentry_id}")}``,
``via_device_id`` on the main device) plus a registry migration (entry 2.4).
"""

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_POWER_WARNING_PCT,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    INPUT_OFF_POLICY_AUTO,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
)
from custom_components.battery_manager.entity import ensure_devices

LOAD_TITLE = "Fossibot Test"

BASE_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}

LOAD_DATA = {
    CONF_LOAD_POWER_W: 300.0,
    CONF_LOAD_ENERGY_LIMITED: True,
    CONF_LOAD_CAPACITY_WH: 2000.0,
    CONF_LOAD_TARGET_SOC: 90.0,
    CONF_LOAD_SOC_ENTITY: "sensor.fossibot_soc",
    CONF_LOAD_POWER_ENTITY: "sensor.fossibot_power",
    CONF_LOAD_POWER_WARNING_PCT: 50.0,
    CONF_LOAD_CONTROL_SWITCH: "switch.shelly_fossibot_input",
    CONF_LOAD_CHARGE_ENABLE: "input_boolean.charge_fossibot",
    CONF_LOAD_INPUT_OFF_POLICY: INPUT_OFF_POLICY_AUTO,
}


def _make_entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=BASE_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=dict(LOAD_DATA),
                subentry_type=SUBENTRY_TYPE_LOAD,
                title=LOAD_TITLE,
                unique_id=None,
            )
        ],
        **kwargs,
    )


async def _setup(hass, entry: MockConfigEntry) -> None:
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.fossibot_soc", "40", {"unit_of_measurement": "%"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_entities_survive_setup(hass, caplog):
    """Every registered BM entity must exist in the state machine (HA 2026.8).

    Pre-fix this reproduced the incident: the shared device moved between the
    entry level and the subentry on each add, and the entity registry removed
    the 'old' side's entities — only the subentry switch survived.
    """
    entry = _make_entry()
    await _setup(hass, entry)
    sub_id = next(iter(entry.subentries))

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    rows = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    # Entry-level (7 sensors, inverter binary sensor, vacation switch) plus
    # per-load rows (recommendation, power warning, runtime, planning power,
    # control, runtime reset and power calibration).
    assert len(rows) == 16
    missing = [row.entity_id for row in rows if hass.states.get(row.entity_id) is None]
    assert not missing, f"entities missing from the state machine: {missing}"

    # Exactly one main device plus one device per subentry.
    devices = dev_reg.devices.get_devices_for_config_entry_id(entry.entry_id)
    assert len(devices) == 2
    main = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert main is not None
    assert main.config_subentry_id is None
    sub_device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_{sub_id}")}
    )
    assert sub_device is not None
    assert sub_device.config_subentry_id == sub_id
    assert sub_device.name == LOAD_TITLE
    assert sub_device.via_device_id == main.id

    # Entry-level rows stay on the main device without a subentry scope;
    # subentry rows hang on their own device, scoped to their subentry.
    for row in rows:
        if row.unique_id.endswith(sub_id):
            assert row.device_id == sub_device.id, row.entity_id
            assert row.config_subentry_id == sub_id, row.entity_id
        else:
            assert row.device_id == main.id, row.entity_id
            assert row.config_subentry_id is None, row.entity_id

    # The forbidden shared-device pattern must not reappear.
    assert (
        "assigns an existing device to a different config subentry" not in caplog.text
    )


def test_cascade_device_model_supports_filtered_action_picker(hass):
    """Only cascade subentries carry the model used by the action selector."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=BASE_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=dict(LOAD_DATA),
                subentry_type=SUBENTRY_TYPE_LOAD,
                title=LOAD_TITLE,
                unique_id=None,
            ),
            ConfigSubentryData(
                data={},
                subentry_type=SUBENTRY_TYPE_CASCADE,
                title="Bad",
                unique_id=None,
            ),
        ],
    )
    entry.add_to_hass(hass)
    ensure_devices(hass, entry, "0.35.1")
    registry = dr.async_get(hass)
    by_subentry = {
        device.config_subentry_id: device
        for device in registry.devices.get_devices_for_config_entry_id(entry.entry_id)
        if device.config_subentry_id is not None
    }
    for subentry_id, subentry in entry.subentries.items():
        expected = (
            "Storage Cascade"
            if subentry.subentry_type == SUBENTRY_TYPE_CASCADE
            else "Energy Optimizer"
        )
        assert by_subentry[subentry_id].model == expected


async def test_migration_moves_subentry_entities(hass):
    """Pre-2026.8 registry state (all rows on the shared main device, subentry
    rows with config_subentry_id=None or set, the main device itself left
    scoped to the subentry by the broken 2026.8.0 ping-pong) is migrated:
    subentry rows move to a new per-subentry device without being removed,
    entity ids stay stable (entry version 2.4).
    """
    entry = _make_entry(minor_version=3)
    entry.add_to_hass(hass)
    sub_id = next(iter(entry.subentries))

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    main = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        # The 2026.8.0 ping-pong's end state on the live system: the shared
        # device sits on the last-added subentry. The migration must re-point
        # the rows BEFORE clearing this scope, otherwise the entity registry
        # removes the rows still scoped to it (async_device_modified).
        config_subentry_id=sub_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Battery Manager",
    )
    # Old-install rows: everything on the shared main device. One subentry row
    # without subentry scope (pre-v0.7.19), one already scoped (v0.7.19+). The
    # suggested object ids reproduce the entity ids the pre-fix code generated
    # (device name "Battery Manager" + translated entity name).
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_soc_threshold",
        config_entry=entry,
        device_id=main.id,
        suggested_object_id=f"{DOMAIN}_soc_threshold",
    )
    ent_reg.async_get_or_create(
        "switch",
        DOMAIN,
        f"{entry.entry_id}_load_control_{sub_id}",
        config_entry=entry,
        device_id=main.id,
        suggested_object_id=f"{DOMAIN}_fossibot_test_bm_control",
    )
    ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_load_{sub_id}",
        config_entry=entry,
        config_subentry_id=sub_id,
        device_id=main.id,
        suggested_object_id=f"{DOMAIN}_fossibot_test_recommendation",
    )

    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.fossibot_soc", "40", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 5
    sub_device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_{sub_id}")}
    )
    assert sub_device is not None
    assert sub_device.config_subentry_id == sub_id
    assert sub_device.via_device_id == main.id
    main = dev_reg.async_get(main.id)
    assert main.config_subentry_id is None

    # The pre-seeded rows kept their entity ids and moved to the right device.
    control = ent_reg.async_get(f"switch.{DOMAIN}_fossibot_test_bm_control")
    assert control is not None
    assert control.device_id == sub_device.id
    assert control.config_subentry_id == sub_id
    reco = ent_reg.async_get(f"binary_sensor.{DOMAIN}_fossibot_test_recommendation")
    assert reco is not None
    assert reco.device_id == sub_device.id
    assert reco.config_subentry_id == sub_id
    threshold = ent_reg.async_get(f"sensor.{DOMAIN}_soc_threshold")
    assert threshold is not None
    assert threshold.device_id == main.id
    assert threshold.config_subentry_id is None


async def test_broken_2026_8_registry_state_heals_on_setup(hass):
    """Simulate the live system after one broken 2026.8.0 boot: the ping-pong
    removed the entry-level rows (they linger as deleted entities) and the
    main device ended up attached to the subentry. Setup with the fix must
    restore the original entity ids on the right devices.
    """
    entry = _make_entry(minor_version=3)
    entry.add_to_hass(hass)
    sub_id = next(iter(entry.subentries))

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    # The ping-pong's end state: the shared device sits on the subentry.
    main = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=sub_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Battery Manager",
    )
    row = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_soc_threshold",
        config_entry=entry,
        device_id=main.id,
        suggested_object_id=f"{DOMAIN}_soc_threshold",
    )
    ent_reg.async_remove(row.entity_id)  # removed by the ping-pong -> deleted
    assert ent_reg.async_get(row.entity_id) is None

    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.fossibot_soc", "40", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # The deleted row is resurrected with its original entity id, back on the
    # main device; the main device is detached from the subentry again.
    threshold = ent_reg.async_get(f"sensor.{DOMAIN}_soc_threshold")
    assert threshold is not None
    main = dev_reg.async_get(main.id)
    assert main.config_subentry_id is None
    assert threshold.device_id == main.id
    assert threshold.config_subentry_id is None
    assert hass.states.get(threshold.entity_id) is not None
    rows = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    missing = [row.entity_id for row in rows if hass.states.get(row.entity_id) is None]
    assert not missing, f"entities missing from the state machine: {missing}"
