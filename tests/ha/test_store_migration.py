"""Coordinator runtime-store migration robustness.

Mirrors the learner-store hardening (tests/ha/test_history_robustness.py):
an envelope the _RuntimeStore cannot migrate is discarded with a WARNING
instead of crashing the entry setup, and a file written by a NEWER code
version (downgrade scenario) never breaks setup either.
"""

import logging

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import coordinator as coordinator_mod
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

LOAD_SOC = {"load1": {"soc": 55.0, "entity": "sensor.load_soc"}}


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up (lingering-timer guard, same reason
    as in test_config_flow.py)."""
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _make_entry(hass):
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    return entry


async def test_store_newer_envelope_does_not_crash(hass, hass_storage, caplog):
    """Downgrade scenario: HA refuses a NEWER envelope major before the
    migrate callback runs — the coordinator catches it (re-derivable cache,
    must never break setup) and the entry loads."""
    entry = _make_entry(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 99,
        "minor_version": 1,
        "key": key,
        "data": {"load_soc": LOAD_SOC},
    }

    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert "written by a newer version" in caplog.text
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._load_soc_cache == {}


async def test_store_envelope_major_migration_discards(
    hass, hass_storage, monkeypatch, caplog
):
    """The code bumped the envelope major while an old file exists — HA's
    default migrate would raise NotImplementedError and kill the entry
    setup; ours discards the re-derivable cache with a warning instead."""
    entry = _make_entry(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"load_soc": LOAD_SOC},
    }
    # Simulate the code having moved to envelope major 2.
    monkeypatch.setattr(coordinator_mod, "STORAGE_VERSION", 2)

    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert "unsupported store envelope version 1.1" in caplog.text
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._load_soc_cache == {}


async def test_store_same_major_minor_diff_passes_through(hass, hass_storage):
    """A same-major minor diff is compatible: the payload is passed through
    and restored (every key is read defensively)."""
    entry = _make_entry(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 2,  # code runs 1.1
        "key": key,
        "data": {"load_soc": LOAD_SOC},
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._load_soc_cache == LOAD_SOC
