"""Tests for the consumption-profile learner (docs/CONSUMPTION_FORECAST.md)."""

from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_AC_LOAD_ENTITY,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)
from custom_components.battery_manager.history_profile import (
    ProfileLearner,
    _day_series_zero_filled,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}

AC_BINS = {
    "weekday": {"p50": [100.0] * 24, "p80": [120.0] * 24},
    "weekend": {"p50": [80.0] * 24, "p80": [95.0] * 24},
    "absence": {"p50": [None] * 24, "p80": [None] * 24},
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


def _prime(learner, source_entities, computed_at=None):
    learner.data["profiles"] = {"ac": AC_BINS, "dc": None}
    learner.data["source_entities"] = source_entities
    learner.data["computed_at"] = (computed_at or dt_util.now()).isoformat()


async def test_profiles_for_planning_fresh_and_bound(hass):
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    _prime(learner, {"ac": ["sensor.house_load"], "dc": []})

    profiles = learner.profiles_for_planning()
    assert profiles is not None
    assert profiles["ac"]["weekday"]["p50"][0] == 100.0


async def test_profiles_for_planning_stale_returns_none(hass):
    """Older than learning_max_age_days (default 14) -> static fallback."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    _prime(
        learner,
        {"ac": ["sensor.house_load"], "dc": []},
        computed_at=dt_util.now() - timedelta(days=15),
    )

    assert learner.profiles_for_planning() is None


async def test_profiles_for_planning_binding_mismatch_returns_none(hass):
    """A profile learned from other entities must never be used (D-C6)."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.new_load"})
    learner = ProfileLearner(hass, entry)
    _prime(learner, {"ac": ["sensor.old_load"], "dc": []})

    assert learner.profiles_for_planning() is None


async def test_run_learning_without_sources_clears_state(hass):
    """Opt-out: removing all measurement entities drops the learned state."""
    entry = _entry(hass)  # no learning entities configured
    learner = ProfileLearner(hass, entry)
    _prime(learner, {"ac": ["sensor.old_load"], "dc": []})

    await learner.async_run_learning()

    assert learner.data["profiles"] == {"ac": None, "dc": None}
    assert learner.data["computed_at"] is None
    assert learner.profiles_for_planning() is None


async def test_run_learning_failure_keeps_old_profile(hass, monkeypatch):
    """A failing nightly run must leave the previous profile valid (D-C6)."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    _prime(learner, {"ac": ["sensor.house_load"], "dc": []})
    hass.config.components.add("recorder")

    async def _boom(*args, **kwargs):
        raise RuntimeError("recorder unavailable")

    monkeypatch.setattr(ProfileLearner, "_fetch_days", _boom)
    await learner.async_run_learning()  # must not raise

    assert learner.profiles_for_planning() is not None


async def test_cleaning_config_change_invalidates_cached_days(hass, monkeypatch):
    """Changed cleaning inputs must drop cached daily_hours (fingerprint)."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    _prime(learner, {"ac": ["sensor.house_load"], "dc": []})
    learner.data["cleaning_fingerprint"] = "outdated"
    learner.data["daily_hours"] = {"2026-07-01": {"ac": [100.0] * 24, "dc": None}}
    hass.config.components.add("recorder")

    fetched: dict = {}

    async def _fake_fetch(self, cfg, sources, days, missing):
        fetched["days"] = days

    monkeypatch.setattr(ProfileLearner, "_fetch_days", _fake_fetch)
    await learner.async_run_learning()

    # The cached (contaminated) day was dropped and the full window refetched.
    assert len(fetched["days"]) >= 42
    assert learner.data["cleaning_fingerprint"] != "outdated"


async def test_run_learning_skips_without_recorder(hass):
    """No recorder integration -> warning + unchanged state, no crash."""
    entry = _entry(hass, **{CONF_AC_LOAD_ENTITY: "sensor.house_load"})
    learner = ProfileLearner(hass, entry)
    _prime(learner, {"ac": ["sensor.house_load"], "dc": []})

    await learner.async_run_learning()

    assert learner.profiles_for_planning() is not None


async def test_vacation_switch_toggles_learner(hass):
    """The vacation switch drives the learner's persisted state (D-C4)."""
    entry = _entry(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_vacation_mode"
    )
    assert entity_id is not None

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.learner.vacation_active is False

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.learner.vacation_active is True
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.learner.vacation_active is False


async def test_v1_store_file_does_not_crash_setup(hass, hass_storage):
    """Regression: bumping the Store MAJOR version made HA's default
    migration raise NotImplementedError and killed the whole entry setup.
    The envelope stays at major 1; the inner version field discards v1."""
    entry = _entry(hass)
    hass_storage[f"battery_manager.learned_profiles.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"battery_manager.learned_profiles.{entry.entry_id}",
        "data": {"version": 1, "profiles": {"ac": {"weekday": [1] * 24}}},
    }
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    # v1 payload discarded -> fresh defaults (inner version 2, no profiles)
    assert coordinator.learner.data["version"] == 2
    assert coordinator.learner.data["profiles"] == {"ac": None, "dc": None}


def test_power_feedback_gaps_count_as_zero():
    """Unavailable = off (operator decision): gaps subtract 0 W, hours stay."""
    hour_map = {("2026-07-01", 12): 300.0}
    series = _day_series_zero_filled(hour_map, "2026-07-01")
    assert series[12] == 300.0
    assert series[0] == 0.0
    assert None not in series


async def test_export_learned_profiles_service(hass):
    """The export service writes a readable table of the learned bins."""
    from pathlib import Path

    entry = _entry(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.learner.data["profiles"] = {"ac": AC_BINS, "dc": None}
    coordinator.learner.data["samples"] = {
        "ac": {"weekday": [12] * 24, "weekend": [5] * 24, "absence": [0] * 24},
        "dc": None,
    }
    coordinator.learner.data["computed_at"] = dt_util.now().isoformat()
    coordinator.learner.data["window_days"] = 42

    target = Path(hass.config.config_dir) / "bm_profiles_test.txt"
    await hass.services.async_call(
        DOMAIN,
        "export_learned_profiles",
        {"file_path": str(target)},
        blocking=True,
    )
    content = await hass.async_add_executor_job(target.read_text)
    assert "Werktag P50" in content
    assert "[AC-Pfad]" in content
    assert "100" in content  # learned weekday value
    assert "statisches Profil aktiv" in content  # DC has no learned profile


async def test_vacation_mode_uses_base_load_without_absence_bins(hass):
    """Vacation + empty absence bins -> base_w series, not the full profile."""
    entry = _entry(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.learner.data["vacation_mode_active"] = True
    config = coordinator.build_system_config()

    ac_series, dc_series, _band, _active, diag = coordinator._learned_series(
        dt_util.now(), config, 3
    )
    assert diag["ac_source"] == "vacation_base"
    # base_w only — never base_w + variable_w (D-C4).
    assert set(ac_series) == {50.0}
    assert set(dc_series) == {50.0}
