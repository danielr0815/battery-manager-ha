"""Original forecast evidence survives retries; computation is not data freshness."""

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.util import dt as dt_util
from test_history_profile import _entry

from custom_components.battery_manager.const import (
    CONF_AC_LOAD_ENTITY,
    CONF_DC_LOAD_ENTITY,
)
from custom_components.battery_manager.history_profile import (
    ProfileLearner,
    _valid_learned_data,
)


@pytest.fixture
async def learner(hass, freezer):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-10-11T21:00:00Z")
    learner = ProfileLearner(
        hass,
        _entry(
            hass, **{CONF_AC_LOAD_ENTITY: "sensor.ac", CONF_DC_LOAD_ENTITY: "sensor.dc"}
        ),
    )
    learner._save = Mock()
    return learner


def prediction(hour=6, watts=100, day="2026-10-09"):
    return {
        "start": f"{day}T{hour:02d}:00:00+00:00",
        "daytype": "weekday",
        "ac": {"p50_w": watts, "p80_w": watts + 20, "source": "learned"},
        "dc": {"p50_w": 50, "p80_w": None, "source": "static"},
    }


def publish(learner, rows=None, at="2026-10-09T05:55:00+00:00"):
    learner.record_forecasts(
        rows or [prediction()], datetime.fromisoformat(at), "2026-10-09T03:00:00+00:00"
    )


async def test_last_forecast_before_hour_is_frozen_and_retains_original_quantiles(
    learner,
):
    publish(learner, at="2026-10-09T05:00:00+00:00")
    publish(learner, [prediction(watts=110)])
    saved = deepcopy(learner.data["forecast_hours"])
    publish(learner, [prediction(watts=999)], at="2026-10-09T06:01:00+00:00")
    assert learner.data["forecast_hours"] == saved
    actual = [None] * 24
    actual[6] = 200
    learner.data["daily_hours"]["2026-10-09"] = {"ac": actual, "dc": [50] * 24}
    learner.data["profiles"]["ac"] = {"weekday": {"p50": [999] * 24}}
    entry = learner._recorded_validation("2026-10-09", "ac")
    assert entry["bias_w"] == -90
    assert entry["mae_w"] == 90
    assert entry["p80_hours"] == 1
    assert entry["p80_covered_hours"] == 0
    assert entry["forecast_basis"] == "published_before_hour"
    assert (
        learner._recorded_validation("2026-10-09", "dc")["static_fallback_hours"] == 1
    )


async def test_recorder_retry_updates_old_day_without_reconstructing_forecasts(
    learner, hass, monkeypatch
):
    hass.config.components.add("recorder")
    monkeypatch.setattr(learner, "_fetch_configuration_epochs", AsyncMock())
    publish(learner, [prediction(6), prediction(7)])
    actual = [None] * 24
    actual[6] = 110
    learner.data["daily_hours"]["2026-10-09"] = {"ac": actual, "dc": None}
    await learner._run_learning()
    first = learner.data["validation"]["ac"][-1]
    assert first["day"] == "2026-10-09"
    assert first["hours"] == 1
    assert first["p80_covered_hours"] == 1
    learner.data["daily_hours"]["2026-10-09"]["ac"][7] = 200
    await learner._run_learning()
    entries = learner.data["validation"]["ac"]
    assert len(entries) == 1
    assert entries[0]["hours"] == 2
    assert entries[0]["mae_w"] == 55
    assert entries[0]["p80_covered_hours"] == 1


async def test_yesterday_uses_published_evidence_and_keeps_missing_actuals_unknown(
    learner,
):
    publish(learner, [prediction(day="2026-10-10")], at="2026-10-10T05:59:00+00:00")
    learner.data["daily_hours"]["2026-10-10"] = {"ac": [None] * 24, "dc": [None] * 24}
    learner._validate_yesterday(dt_util.now().date(), {}, learner._raw_config())
    assert learner.data["validation"]["ac"] == []
    learner.data["daily_hours"]["2026-10-10"]["ac"][6] = 125
    learner.data["daily_hours"]["2026-10-10"]["dc"][6] = 50
    learner._validate_yesterday(dt_util.now().date(), {}, learner._raw_config())
    assert learner.data["validation"]["ac"][-1]["bias_w"] == -25
    assert learner.data["validation"]["dc"][-1]["hours"] == 1


async def test_learning_commit_preserves_forecasts_published_while_recorder_awaits(
    learner, monkeypatch
):
    async def working_copy(worker):
        worker.data["computed_at"] = dt_util.now().isoformat()
        publish(learner, [prediction(day="2026-10-12")], at="2026-10-11T21:00:00+00:00")

    monkeypatch.setattr(ProfileLearner, "_run_learning_working_copy", working_copy)
    await learner._run_learning()
    assert learner.data["computed_at"] is not None
    assert learner.data["forecast_hours"]["2026-10-12"]["ac"]["6"]["p50_w"] == 100


async def test_successful_learning_without_measurements_reports_stale_data(learner):
    learner.data["computed_at"] = dt_util.now().isoformat()
    learner.data["daily_hours"]["2026-10-09"] = {
        "ac": [100] * 2 + [None] * 22,
        "dc": None,
    }
    diag = learner.diagnostics()
    ac = diag["measurement_freshness"]["ac"]
    assert ac["last_usable_at"] == "2026-10-09T02:00:00+00:00"
    assert ac["age_hours"] == 67
    assert ac["stale"] and ac["yesterday_empty"]
    assert ac["recent_valid_hours"] == 2
    assert ac["recent_window_hours"] == 168
    assert learner.export_snapshot()["daily_hours"] == learner.data["daily_hours"]
    assert (
        learner.export_snapshot()["measurement_freshness"]
        == diag["measurement_freshness"]
    )
    learner.data["ac_valid_since"] = "2026-10-10T00:00:00+00:00"
    assert (
        learner.diagnostics()["measurement_freshness"]["ac"]["last_usable_at"] is None
    )


async def test_forecast_retention_and_missing_p80_do_not_invent_evidence(learner):
    publish(learner)
    publish(learner, [prediction(day="2026-10-18")], at="2026-10-11T21:00:00+00:00")
    assert "2026-10-18" not in learner.data["forecast_hours"]
    learner.record_forecasts([], datetime(2026, 10, 17, tzinfo=UTC), None)
    assert learner.data["forecast_hours"] == {}
    assert learner._recorded_validation("2026-10-09", "ac") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("p50_w", -1),
        ("p50_w", "bad"),
        ("p50_w", True),
        ("p80_w", 90),
        ("p80_w", "bad"),
        ("p80_w", True),
        ("source", "other"),
        ("daytype", "other"),
        ("published_at", None),
        ("published_at", "2026-10-09T06:01:00+00:00"),
        ("hour_start", "2026-10-09T06:00:00"),
        ("profile_computed_at", "bad"),
    ],
)
async def test_invalid_forecast_store_cannot_reach_error_arithmetic(
    learner, field, value
):
    publish(learner)
    assert _valid_learned_data(learner.data)
    learner.data["forecast_hours"]["2026-10-09"]["ac"]["6"][field] = value
    assert not _valid_learned_data(learner.data)


async def test_real_autumn_hour_duration_controls_measurement_freshness(
    learner, hass, freezer
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-10-26T02:00:00Z")
    actual = [None] * 24
    actual[2] = 200
    learner.data["daily_hours"]["2026-10-25"] = {"ac": actual}
    ac = learner.diagnostics()["measurement_freshness"]["ac"]
    assert ac["last_usable_at"] == "2026-10-25T02:00:00+00:00"
    assert ac["recent_valid_hours"] == 2
    assert ac["recent_window_hours"] == 169
    assert not ac["stale"]


async def test_autumn_forecast_error_uses_two_real_hours_and_timezone_identity(
    learner, hass
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    row = prediction(hour=2, day="2026-10-25")
    row["start"] = "2026-10-25T02:00:00+02:00"
    learner.record_forecasts(
        [row], datetime.fromisoformat("2026-10-25T01:55:00+02:00"), None
    )
    values = [None] * 24
    values[2] = 250
    learner.data["daily_hours"]["2026-10-25"] = {"ac": values}
    validation = learner._recorded_validation("2026-10-25", "ac")
    assert validation["hours"] == 2
    assert validation["bias_w"] == -25
    assert validation["mae_w"] == 25
    assert validation["p80_hours"] == 2
    assert validation["p80_covered_hours"] == 0
    await hass.config.async_set_time_zone("UTC")
    assert learner._recorded_validation("2026-10-25", "ac") is None


async def test_future_actuals_cannot_claim_fresh_measurements(learner):
    learner.data["daily_hours"]["2026-10-12"] = {"ac": [100] * 24}
    freshness = learner.diagnostics()["measurement_freshness"]["ac"]
    assert freshness["last_usable_at"] is None
    assert freshness["stale"]
