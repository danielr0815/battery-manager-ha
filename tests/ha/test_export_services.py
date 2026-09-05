"""Export service hardening tests (security review 2026-07-30).

The export services write learned household behaviour to disk; the tests pin
the confinement to <config>/battery_manager/ (plain) and
<config>/www/battery_manager/ (download), the .txt/.json whitelist, the
1-hour TTL for files in the unauthenticated /local/ tree, and that service
failures raise instead of only logging.
"""

import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_get_persistent_notifications,
)

from custom_components.battery_manager import (
    _cascade_service_target,
    _export_coordinator,
    _schedule_download_cleanup,
    _validate_cascade_service_target,
    _validate_file_path,
    async_setup,
)
from custom_components.battery_manager.const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    SERVICE_EXPORT_HOURLY_DETAILS,
    SERVICE_EXPORT_LEARNED_PROFILES,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}

HOURLY_DETAILS = [{"datetime": "2026-07-30T10:00:00+00:00", "soc_percent": 55.0}]


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Isolate the exports in a per-test tmp dir — the phacc default is a
    shared testing_config dir inside the installed package."""
    return hass_tmp_config_dir


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up (lingering-timer guard, same reason
    as in test_config_flow.py)."""
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _coordinator(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]


async def test_export_writes_into_subdirectory(hass):
    """A valid export lands in <config>/battery_manager/, never in the
    config root (where e.g. .storage lives)."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}

    await hass.services.async_call(
        DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS, {}, blocking=True
    )

    config_dir = Path(hass.config.config_dir)
    target = (
        config_dir / "battery_manager" / f"battery_manager_hourly_{entry.entry_id}.txt"
    )
    assert target.is_file()
    assert target.read_text(encoding="utf-8")
    assert not (config_dir / f"battery_manager_hourly_{entry.entry_id}.txt").exists()


async def test_export_hourly_as_table_writes_ascii_table(hass):
    """as_table=True (the default): the file holds the formatted ASCII table
    (headers + the MM-DD HH:MM datetime rendering), not raw JSON."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}

    await hass.services.async_call(
        DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS, {"as_table": True}, blocking=True
    )

    target = (
        Path(hass.config.config_dir)
        / "battery_manager"
        / f"battery_manager_hourly_{entry.entry_id}.txt"
    )
    content = target.read_text(encoding="utf-8")
    assert "SOC in %" in content  # table header
    assert "07-30 10:00" in content  # datetime rendered as MM-DD HH:MM
    assert "+-" in content  # ASCII frame


async def test_export_hourly_as_json_lines(hass):
    """as_table=False: one JSON document per hourly row (machine-readable)."""
    import json

    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}

    await hass.services.async_call(
        DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS, {"as_table": False}, blocking=True
    )

    target = (
        Path(hass.config.config_dir)
        / "battery_manager"
        / f"battery_manager_hourly_{entry.entry_id}.txt"
    )
    content = target.read_text(encoding="utf-8")
    assert content == json.dumps(HOURLY_DETAILS[0])


async def test_export_accepts_uppercase_whitelisted_extension(hass):
    """The extension whitelist is case-insensitive (.JSON is allowed)."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}
    config_dir = Path(hass.config.config_dir)
    custom = config_dir / "battery_manager" / "custom_export.JSON"

    await hass.services.async_call(
        DOMAIN,
        SERVICE_EXPORT_HOURLY_DETAILS,
        {"file_path": str(custom)},
        blocking=True,
    )

    assert custom.is_file()


@pytest.mark.parametrize(
    "path_template",
    [
        "{config}/../evil.txt",  # classic traversal out of the config dir
        "{config}/.storage/core.config_entries",  # the review's attack path
        "{config}/battery_manager/.storage/evil.txt",  # nested .storage component
    ],
)
async def test_export_rejects_unsafe_paths(hass, path_template):
    """Traversal and .storage paths raise ServiceValidationError and create
    nothing (previously they were only logged and could overwrite HA state)."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}
    evil = Path(path_template.format(config=hass.config.config_dir))

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_HOURLY_DETAILS,
            {"file_path": str(evil)},
            blocking=True,
        )
    assert not evil.exists()


async def test_export_rejects_disallowed_extension(hass):
    """Only .txt/.json may be written — no .yaml/.storage-style files."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}
    target = Path(hass.config.config_dir) / "battery_manager" / "evil.yaml"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_HOURLY_DETAILS,
            {"file_path": str(target)},
            blocking=True,
        )
    assert not target.exists()


async def test_export_unknown_entry_id_raises(hass):
    """An unknown entry_id must fail the service call, not silently no-op."""
    await _setup_entry(hass)

    with pytest.raises(ServiceValidationError, match="Unknown entry_id"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_HOURLY_DETAILS,
            {"entry_id": "does_not_exist"},
            blocking=True,
        )


async def test_export_hourly_without_data_raises(hass):
    """No plan yet -> HomeAssistantError instead of a silent log line."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {}

    with pytest.raises(HomeAssistantError, match="No hourly details"):
        await hass.services.async_call(
            DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS, {}, blocking=True
        )


async def test_export_learned_without_profiles_raises(hass):
    """A fresh learner (all profile bins None) has nothing worth exporting."""
    await _setup_entry(hass)

    with pytest.raises(HomeAssistantError, match="No learned consumption"):
        await hass.services.async_call(
            DOMAIN, SERVICE_EXPORT_LEARNED_PROFILES, {}, blocking=True
        )


async def test_export_learned_writes_json_snapshot(hass):
    """The learned-profiles export (as JSON) lands in the subdirectory."""
    entry = await _setup_entry(hass)
    coordinator = _coordinator(hass, entry)
    coordinator.learner.data["profiles"] = {
        "ac": {"workday": [1.0] * 24},
        "dc": None,
    }

    await hass.services.async_call(
        DOMAIN,
        SERVICE_EXPORT_LEARNED_PROFILES,
        {"as_table": False},
        blocking=True,
    )

    target = (
        Path(hass.config.config_dir)
        / "battery_manager"
        / f"battery_manager_profiles_{entry.entry_id}.txt"
    )
    assert '"workday"' in target.read_text(encoding="utf-8")


async def test_download_export_is_deleted_after_ttl(hass):
    """Download files are served WITHOUT login under /local/, so they expire
    after 1 h; the notification must say exactly that."""
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}

    await hass.services.async_call(
        DOMAIN, SERVICE_EXPORT_HOURLY_DETAILS, {"download": True}, blocking=True
    )

    target = (
        Path(hass.config.config_dir)
        / "www"
        / "battery_manager"
        / f"battery_manager_hourly_{entry.entry_id}.txt"
    )
    assert target.is_file()
    notifications = async_get_persistent_notifications(hass)
    assert any(
        "without login" in n["message"] and "/local/battery_manager/" in n["message"]
        for n in notifications.values()
    )

    # The per-file timer fires once the TTL has passed.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert not target.exists()


async def test_startup_sweep_removes_stale_downloads(hass):
    """The TTL timer dies with the HA process; the setup sweep removes files
    older than 1 h that survived a restart (fresh files stay)."""
    download_dir = Path(hass.config.config_dir) / "www" / "battery_manager"
    download_dir.mkdir(parents=True)
    stale = download_dir / "stale.txt"
    fresh = download_dir / "fresh.txt"
    stale.write_text("old", encoding="utf-8")
    fresh.write_text("new", encoding="utf-8")
    old_ts = (dt_util.utcnow() - timedelta(hours=2)).timestamp()
    os.utime(stale, (old_ts, old_ts))

    with patch("custom_components.battery_manager.async_call_later") as schedule:
        assert await async_setup(hass, {})
    assert schedule.call_count == 1
    assert 3590 < schedule.call_args.args[1] <= 3600
    assert not stale.exists()
    assert fresh.is_file()
    await schedule.call_args.args[2](dt_util.utcnow())
    assert not fresh.exists()


def test_export_path_rejects_null_hidden_and_resolution_failures(tmp_path, monkeypatch):
    """The export boundary rejects ambiguous names before writing; null bytes
    receive their own error instead of leaking a platform-dependent OSError."""
    base = tmp_path / "battery_manager"

    with pytest.raises(ValueError, match="null bytes"):
        _validate_file_path(str(base / "bad\0.txt"), base)
    with pytest.raises(ValueError, match="Invalid filename"):
        _validate_file_path(str(base / ".hidden.txt"), base)

    original_resolve = Path.resolve

    def fail_selected(path):
        if path.name == "unresolvable.txt":
            raise OSError("filesystem loop")
        return original_resolve(path)

    monkeypatch.setattr(Path, "resolve", fail_selected)
    with pytest.raises(ValueError, match="Invalid path: filesystem loop"):
        _validate_file_path(str(base / "unresolvable.txt"), base)


def test_export_requires_a_running_entry(hass):
    """Calling an export service without any configured coordinator is an
    explicit validation error, never a successful no-op."""
    with pytest.raises(ServiceValidationError, match="No Battery Manager entry"):
        _export_coordinator(hass, SimpleNamespace(data={}))


def test_cascade_action_resolves_selected_device_and_legacy_id(hass):
    """A named cascade device replaces the raw ID without breaking old YAML."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, entry_id="entry", title="Battery Manager"
    )
    entry.add_to_hass(hass)
    assert hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={},
            subentry_id="chain",
            subentry_type=SUBENTRY_TYPE_CASCADE,
            title="Bad",
            unique_id=None,
        ),
    )
    assert hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={},
            subentry_id="load",
            subentry_type=SUBENTRY_TYPE_LOAD,
            title="Load",
            unique_id=None,
        ),
    )
    coordinator = SimpleNamespace(entry=entry)
    hass.data[DOMAIN] = {"entry": coordinator}
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="entry",
        config_subentry_id="chain",
        identifiers={(DOMAIN, "entry_chain")},
        manufacturer="Battery Manager",
        model="Storage Cascade",
        name="Bad",
    )

    assert _cascade_service_target(
        hass, SimpleNamespace(data={"device_id": device.id})
    ) == (coordinator, "chain")
    assert _cascade_service_target(
        hass, SimpleNamespace(data={"entry_id": "entry", "cascade_id": "chain"})
    ) == (coordinator, "chain")


def test_cascade_action_rejects_missing_or_inconsistent_targets(hass):
    """The broad device selector schema cannot bypass runtime cascade checks."""
    with pytest.raises(vol.Invalid, match="device_id or cascade_id"):
        _validate_cascade_service_target({})

    entry = MockConfigEntry(
        domain=DOMAIN, data={}, entry_id="entry", title="Battery Manager"
    )
    entry.add_to_hass(hass)
    assert hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={},
            subentry_id="chain",
            subentry_type=SUBENTRY_TYPE_CASCADE,
            title="Bad",
            unique_id=None,
        ),
    )
    assert hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={},
            subentry_id="load",
            subentry_type=SUBENTRY_TYPE_LOAD,
            title="Load",
            unique_id=None,
        ),
    )
    coordinator = SimpleNamespace(entry=entry)
    hass.data[DOMAIN] = {"entry": coordinator}
    registry = dr.async_get(hass)
    cascade_device = registry.async_get_or_create(
        config_entry_id="entry",
        config_subentry_id="chain",
        identifiers={(DOMAIN, "entry_chain")},
        manufacturer="Battery Manager",
        model="Storage Cascade",
        name="Bad",
    )
    load_device = registry.async_get_or_create(
        config_entry_id="entry",
        config_subentry_id="load",
        identifiers={(DOMAIN, "entry_load")},
        manufacturer="Battery Manager",
        model="Energy Optimizer",
        name="Load",
    )

    with pytest.raises(ServiceValidationError, match="Unknown cascade device_id"):
        _cascade_service_target(
            hass, SimpleNamespace(data={"device_id": "does_not_exist"})
        )
    with pytest.raises(ServiceValidationError, match="does not belong"):
        _cascade_service_target(
            hass,
            SimpleNamespace(data={"device_id": cascade_device.id, "entry_id": "other"}),
        )
    with pytest.raises(ServiceValidationError, match="not a storage cascade"):
        _cascade_service_target(
            hass, SimpleNamespace(data={"device_id": load_device.id})
        )
    with pytest.raises(ServiceValidationError, match="does not match"):
        _cascade_service_target(
            hass,
            SimpleNamespace(
                data={"device_id": cascade_device.id, "cascade_id": "other"}
            ),
        )


async def test_download_cleanup_tolerates_external_delete_and_reports_io_error(
    hass, caplog
):
    """TTL cleanup races safely with an operator delete, but exposes genuine
    filesystem failures in the log for diagnosis."""
    missing = SimpleNamespace(unlink=Mock(side_effect=FileNotFoundError))
    broken = SimpleNamespace(unlink=Mock(side_effect=OSError("read-only")))

    _schedule_download_cleanup(hass, missing)
    _schedule_download_cleanup(hass, broken)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, seconds=1))
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    missing.unlink.assert_called_once_with()
    broken.unlink.assert_called_once_with()
    assert "Could not delete expired download export: read-only" in caplog.text


async def test_nested_download_link_preserves_and_quotes_relative_path(hass):
    entry = await _setup_entry(hass)
    _coordinator(hass, entry).data = {"hourly_details": HOURLY_DETAILS}
    target = (
        Path(hass.config.config_dir)
        / "www"
        / "battery_manager"
        / "reports"
        / "Bericht ä #1.json"
    )
    with patch("custom_components.battery_manager._schedule_download_cleanup"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_HOURLY_DETAILS,
            {"download": True, "file_path": str(target)},
            blocking=True,
        )
    assert target.is_file()
    notifications = async_get_persistent_notifications(hass)
    assert any(
        "/local/battery_manager/reports/Bericht%20%C3%A4%20%231.json" in item["message"]
        for item in notifications.values()
    )


async def test_restart_rearms_nested_exports_for_remaining_ttl(hass):
    from custom_components.battery_manager import _async_sweep_download_dir

    base = Path(hass.config.config_dir) / "www" / "battery_manager" / "nested"
    base.mkdir(parents=True)
    target = base / "recent.json"
    target.write_text("{}")
    timestamp = 1800000000
    os.utime(target, (timestamp - 1800, timestamp - 1800))
    with (
        patch("custom_components.battery_manager.time.time", return_value=timestamp),
        patch("custom_components.battery_manager.async_call_later") as timer,
    ):
        await _async_sweep_download_dir(hass)
    assert timer.call_args.args[1] == 1800
    await timer.call_args.args[2](dt_util.utcnow())
    assert not target.exists()
