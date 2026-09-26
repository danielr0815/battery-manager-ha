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


_real_wait_for = asyncio.wait_for


async def _timeout_now(awaitable, timeout):
    # Check the real production contract; close the deliberately unrun coroutine.
    assert timeout == 300
    if getattr(awaitable, "cr_code", None) is asyncio.Event.wait.__code__:
        awaitable.close()
        raise TimeoutError
    return await _real_wait_for(awaitable, timeout)


class _HungRecorder:
    """Recorder stand-in whose executor jobs never finish (hung DB)."""

    def async_add_executor_job(self, job, *args):
        return asyncio.Event().wait()


async def test_recorder_timeout_creates_issue_and_frees_lock(hass, monkeypatch, caplog):
    """A hung recorder DB must time out: warning + repair issue, and the
    learner lock is free again so later runs are not blocked."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    hass.config.components.add("recorder")
    monkeypatch.setattr(history_profile.asyncio, "wait_for", _timeout_now)
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
    await learner.async_run_learning()
    assert not learner._lock.locked()


async def test_recorder_issues_resolve_on_next_success(hass, monkeypatch):
    """Self-resolving lifecycle: the next successful run deletes the timeout
    issue (and a stale no-recorder issue) again."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    hass.config.components.add("recorder")
    monkeypatch.setattr(history_profile.asyncio, "wait_for", _timeout_now)
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


async def test_failed_source_change_preserves_committed_profile(hass, monkeypatch):
    from copy import deepcopy
    from unittest.mock import AsyncMock

    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.new"})
    learner = ProfileLearner(hass, entry)
    learner.data["source_entities"] = {"ac": ["sensor.old"], "dc": []}
    learner.data["computed_at"] = dt_util.now().isoformat()
    before = deepcopy(learner.data)
    hass.config.components.add("recorder")
    monkeypatch.setattr(
        ProfileLearner,
        "_fetch_configuration_epochs",
        AsyncMock(side_effect=TimeoutError),
    )
    await learner.async_run_learning()
    assert learner.data == before
    assert learner.profiles_for_planning() is None


async def test_malformed_store_is_discarded(hass, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    learner = ProfileLearner(hass, _entry(hass))
    for data in (
        ["valid JSON, wrong schema"],
        {"version": LEARNED_STORE_VERSION, "profiles": []},
        {
            "version": LEARNED_STORE_VERSION,
            "configuration_epochs": [{"start": "broken timestamp", "config": {}}],
        },
        {
            "version": LEARNED_STORE_VERSION,
            "profiles": {"ac": {"weekday": {"p50": [float("nan")] * 24}}},
        },
    ):
        monkeypatch.setattr(learner._store, "async_load", AsyncMock(return_value=data))
        await learner.async_load()
        assert learner.profiles_for_planning() is None
    assert "malformed" in caplog.text


async def test_history_power_units_and_unknown_intervals(hass, monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from homeassistant.core import State

    from custom_components.battery_manager.history_profile import _running_predicate

    learner = ProfileLearner(hass, _entry(hass))
    start = datetime(2026, 9, 25, tzinfo=UTC)
    end = start + timedelta(hours=3)
    for value, unit in (("500", "W"), ("0.5", "kW")):
        rows = [
            State(
                "sensor.appliance",
                value,
                {"unit_of_measurement": unit},
                last_updated=start,
            ),
            State(
                "sensor.appliance",
                "unavailable",
                last_updated=start + timedelta(hours=1),
            ),
            State(
                "sensor.appliance",
                "0",
                {"unit_of_measurement": unit},
                last_updated=start + timedelta(hours=2),
            ),
        ]
        monkeypatch.setattr(
            learner, "_recorder_job", AsyncMock(return_value={"sensor.appliance": rows})
        )
        changes, _coverage = await learner._state_changes(
            "sensor.appliance", start, end, _running_predicate(100)
        )
        assert [active for _, active in changes] == [True, None, False]


async def test_complete_learned_store_roundtrip_and_corrupt_nested_sections(
    hass, monkeypatch
):
    from copy import deepcopy
    from unittest.mock import AsyncMock

    learner = ProfileLearner(hass, _entry(hass))
    learner._capture_configuration(learner._raw_config())
    learner.data["profiles"]["ac"] = {"weekday": {"p50": [125.0] * 24}}
    learner.data["samples"]["ac"] = {"weekday": [7] * 24}
    learner.data["day_log"] = {"2026-09-01": {"daytype": "weekday", "vacation": False}}
    learner.data["validation"]["ac"] = [
        {"day": "2026-09-01", "bias_w": -1, "mae_w": 2, "hours": 24}
    ]
    valid = deepcopy(learner.data)
    loaded = ProfileLearner(hass, learner.entry)
    monkeypatch.setattr(loaded._store, "async_load", AsyncMock(return_value=valid))
    await loaded.async_load()
    assert loaded.data == valid

    # Each field feeds a different consumer: schedule timestamps, cleaning
    # epochs, profile interpolation, watchdog arithmetic and diagnostics.
    for field, value in (
        ("computed_at", "2026-09-01T12:00:00"),
        ("source_entities", {"ac": "sensor.not_a_list"}),
        ("configuration_epochs", [None]),
        (
            "configuration_epochs",
            [{**valid["configuration_epochs"][0], "loads": [False]}],
        ),
        ("configuration_epochs", [{**valid["configuration_epochs"][0], "sources": []}]),
        ("configuration_epochs", [{**valid["configuration_epochs"][0], "sources": {}}]),
        ("day_log", {"not-a-day": {}}),
        ("day_log", {"2026-09-01": None}),
        ("daily_hours", {"2026-09-01": {"ac": [1] * 23}}),
        ("samples", {"ac": {"weekday": [2]}}),
        ("validation", {"ac": [None]}),
        ("validation", {"ac": [{"day": "2026-09-01", "bias_w": "bad"}]}),
        ("validation", {"ac": {}}),
        ("diagnostics", {"negative_residuals": "bad"}),
        ("profiles", {"ac": {"weekday": {"p50": [float("inf")] * 24}}}),
    ):
        candidate = {**deepcopy(valid), field: value}
        isolated = ProfileLearner(hass, learner.entry)
        monkeypatch.setattr(
            isolated._store, "async_load", AsyncMock(return_value=candidate)
        )
        await isolated.async_load()
        assert isolated.data["profiles"]["ac"] is None, field
        assert isolated.data["computed_at"] is None, field


async def test_cancelled_learning_keeps_the_committed_profile(hass, monkeypatch):
    from copy import deepcopy

    learner = ProfileLearner(hass, _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.new"}))
    learner.data["profiles"]["ac"] = {"weekday": {"p50": [100.0] * 24}}
    before = deepcopy(learner.data)
    entered = asyncio.Event()
    never = asyncio.Event()

    async def interrupted(worker):
        worker.data["profiles"]["ac"] = None
        entered.set()
        await never.wait()

    monkeypatch.setattr(ProfileLearner, "_run_learning_working_copy", interrupted)
    task = asyncio.create_task(learner.async_run_learning())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert learner.data == before
    assert not learner._lock.locked()
