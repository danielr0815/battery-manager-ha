"""Robustness tests for the consumption-profile learner.

Covers the recorder-timeout guard (RECORDER_TIMEOUT_S), the repair-issue
lifecycle, the store migration/mismatch handling and the no-recorder
reload-loop guard (docs/CONSUMPTION_FORECAST.md).
"""

import asyncio
import logging
from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import history_profile
from custom_components.battery_manager.const import (
    CONF_AC_LOAD_ENTITY,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    LEARNED_STORE_VERSION,
)
from custom_components.battery_manager.history_profile import ProfileLearner

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


def _entry(hass, **extra):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **extra},
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass, issue_id):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


class _HungRecorder:
    """Recorder stand-in whose executor jobs never finish (hung DB)."""

    def async_add_executor_job(self, job, *args):
        return asyncio.sleep(3600)


async def test_recorder_timeout_creates_issue_and_frees_lock(hass, monkeypatch, caplog):
    """A hung recorder DB must time out: warning + repair issue, and the
    learner lock is free again so later runs are not blocked."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    hass.config.components.add("recorder")
    monkeypatch.setattr(history_profile, "RECORDER_TIMEOUT_S", 0.05)
    monkeypatch.setattr(history_profile, "get_instance", lambda hass: _HungRecorder())

    with caplog.at_level(logging.WARNING):
        await learner.async_run_learning()  # must not raise

    assert "timed out" in caplog.text
    issue_id = f"learning_recorder_timeout_{entry.entry_id}"
    issue = _issue(hass, issue_id)
    assert issue is not None
    assert issue.translation_key == "learning_recorder_timeout"
    assert not learner._lock.locked()

    # A later run is not blocked by the timed-out one (watchdog on the test
    # itself: without the released lock this await would hang).
    await asyncio.wait_for(learner.async_run_learning(), timeout=5)
    assert not learner._lock.locked()


async def test_recorder_issues_resolve_on_next_success(hass, monkeypatch):
    """Self-resolving lifecycle: the next successful run deletes the timeout
    issue (and a stale no-recorder issue) again."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    hass.config.components.add("recorder")
    monkeypatch.setattr(history_profile, "RECORDER_TIMEOUT_S", 0.05)
    monkeypatch.setattr(history_profile, "get_instance", lambda hass: _HungRecorder())
    await learner.async_run_learning()

    timeout_issue = f"learning_recorder_timeout_{entry.entry_id}"
    no_recorder_issue = f"learning_no_recorder_{entry.entry_id}"
    assert _issue(hass, timeout_issue) is not None
    # Simulate a leftover issue from a previous no-recorder incident.
    ir.async_create_issue(
        hass,
        DOMAIN,
        no_recorder_issue,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="learning_no_recorder",
    )

    async def _fake_fetch(self, cfg, sources, days, missing):
        pass

    monkeypatch.setattr(ProfileLearner, "_fetch_days", _fake_fetch)
    await learner.async_run_learning()

    assert learner.data["computed_at"] is not None  # the run completed
    assert _issue(hass, timeout_issue) is None
    assert _issue(hass, no_recorder_issue) is None


async def test_store_inner_version_mismatch_logs_warning(hass, hass_storage, caplog):
    """A stored payload with an unexpected inner version is discarded with a
    WARNING (it was silently dropped before) — never a crash."""
    entry = _entry(hass)
    key = f"battery_manager.learned_profiles.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"version": 99, "profiles": {"ac": {"weekday": [1] * 24}}},
    }
    learner = ProfileLearner(hass, entry)

    with caplog.at_level(logging.WARNING):
        await learner.async_load()

    assert "unsupported inner store version 99" in caplog.text
    assert learner.data["version"] == LEARNED_STORE_VERSION
    assert learner.data["profiles"] == {"ac": None, "dc": None}


async def test_store_envelope_major_migration_discards(
    hass, hass_storage, monkeypatch, caplog
):
    """The incident scenario behind the LEARNED_STORE_MAJOR pin: the code
    bumped the envelope major while an old file exists — HA's default
    migrate raises NotImplementedError and kills the entry setup; ours
    discards the re-derivable data with a warning instead."""
    entry = _entry(hass)
    key = f"battery_manager.learned_profiles.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"version": 1, "profiles": {"ac": {"weekday": [1] * 24}}},
    }
    # Simulate the code having moved to envelope major 2.
    monkeypatch.setattr(history_profile, "LEARNED_STORE_MAJOR", 2)
    learner = ProfileLearner(hass, entry)

    with caplog.at_level(logging.WARNING):
        await learner.async_load()  # must not raise

    assert "unsupported store envelope version 1.1" in caplog.text
    assert learner.data["profiles"] == {"ac": None, "dc": None}


async def test_store_newer_envelope_does_not_crash(hass, hass_storage, caplog):
    """A file written by a NEWER envelope major (downgrade scenario) is
    refused by HA before the migrate callback; the learner still must not
    break the entry setup."""
    entry = _entry(hass)
    key = f"battery_manager.learned_profiles.{entry.entry_id}"
    hass_storage[key] = {
        "version": 99,
        "minor_version": 1,
        "key": key,
        "data": {"version": 99},
    }
    learner = ProfileLearner(hass, entry)

    with caplog.at_level(logging.ERROR):
        await learner.async_load()  # must not raise

    assert "could not be read" in caplog.text
    assert learner.data["version"] == LEARNED_STORE_VERSION
    assert learner.data["profiles"] == {"ac": None, "dc": None}


async def test_missing_recorder_reports_issue_once(hass, caplog):
    """No recorder integration: one WARNING + one idempotent repair issue
    per incident (no logspam on nightly retries); attempted_at is stamped,
    computed_at stays untouched (no profile was computed)."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)

    with caplog.at_level(logging.DEBUG):
        await learner.async_run_learning()
        await learner.async_run_learning()

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "requires the recorder" in record.message
    ]
    assert len(warnings) == 1
    issue_id = f"learning_no_recorder_{entry.entry_id}"
    issue = _issue(hass, issue_id)
    assert issue is not None
    assert issue.translation_key == "learning_no_recorder"
    assert learner.data["attempted_at"] is not None
    assert learner.data["computed_at"] is None


async def test_attempted_at_suppresses_reload_rerun(hass, monkeypatch):
    """Entry reload right after an aborted (no-recorder) attempt must not
    immediately re-run; once the recorder is back the catch-up runs."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    learner.data["attempted_at"] = dt_util.now().isoformat()
    start = MagicMock()
    monkeypatch.setattr(learner, "_start_run", start)

    learner.async_schedule()  # no recorder, recent abort -> suppressed
    start.assert_not_called()

    hass.config.components.add("recorder")
    learner.async_schedule()  # recorder back -> catch-up right away
    start.assert_called_once()
    learner.async_unschedule()


async def test_stale_attempt_does_not_suppress_catchup(hass, monkeypatch):
    """The suppression expires after 24 h: an old attempt still retries on
    reload even while the recorder is missing."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    learner.data["attempted_at"] = (dt_util.now() - timedelta(hours=25)).isoformat()
    start = MagicMock()
    monkeypatch.setattr(learner, "_start_run", start)

    learner.async_schedule()
    start.assert_called_once()
    learner.async_unschedule()
