"""Recorder integration tests for the consumption-profile learner.

Unlike tests/ha/test_history_profile.py (which stubs ``_fetch_days``), these
tests exercise the full recorder path against a real recorder database
(in-memory SQLite via the phacc ``recorder_mock`` fixture): long-term
statistics are seeded with ``async_import_statistics`` and switch histories
are recorded as real states, so ``_fetch_days``, ``_state_changes`` and the
cleaning orchestration run unmodified.

Determinism: the learning window is pinned to fixed days via
``patch.object(dt_util, "now")`` (pattern: tests/ha/test_coordinator.py) and
the local timezone is fixed to UTC, so local days/hours map 1:1 onto the
seeded statistic rows. ``history_profile._MIN_SAMPLES`` is lowered to 2 so a
four-day window produces bins — the recorder path itself is untouched.
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import SupportsResponse
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.battery_manager import history_profile
from custom_components.battery_manager.const import (
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_AC_LOAD_ENTITY,
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_POWER_THRESHOLD_W,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_DC_LOAD_ENTITY,
    CONF_DCDC_SWITCH,
    CONF_LEARNING_WINDOW_DAYS,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_IN_HOUSE,
    CONF_LOAD_POWER_ENTITY,
    CONF_PSU48_OUTPUT_VOLTAGE_V,
    CONF_SUPPORT_DC24_POWER_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_POWER_W,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_WORKDAY_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_APPLIANCE,
    SUBENTRY_TYPE_LOAD,
)
from custom_components.battery_manager.core import (
    DAY_TYPE_ABSENCE,
    DAY_TYPE_WEEKDAY,
    DAY_TYPE_WEEKEND,
)
from custom_components.battery_manager.history_profile import (
    ProfileLearner,
    _is_off,
)

UTC = dt_util.UTC
# Pinned "now" (a Wednesday): the 4-day learning window then always covers
# Sat 2026-07-11 .. Tue 2026-07-14 — 2 weekend + 2 weekday samples.
PINNED_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
DAYS = ["2026-07-11", "2026-07-12", "2026-07-13", "2026-07-14"]
WINDOW_DAYS = len(DAYS)


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """Prepare the recorder DB before ``hass`` is created.

    phacc's ``hass`` fixture depends on this hook; without the override the
    autouse ``auto_enable_custom_integrations`` fixture creates ``hass``
    first and phacc's ``recorder_db_url`` assertion (``not
    hass_fixture_setup``) fails. This is the documented override point for
    tests that exercise the real recorder.
    """


@pytest.fixture(autouse=True)
async def _utc_timezone(hass, recorder_mock):
    """Real recorder per test; UTC so local days/hours map 1:1 onto rows.

    Depending on ``recorder_mock`` also enforces the setup order: the
    recorder database must exist before ``hass``-dependent fixtures run
    (phacc asserts this ordering in its recorder_db_url fixture).
    """
    await hass.config.async_set_time_zone("UTC")


@pytest.fixture
def _min_samples_2(monkeypatch):
    """Two samples per bin suffice (the window only has 2 days per type)."""
    monkeypatch.setattr(
        history_profile,
        "_MIN_SAMPLES",
        {DAY_TYPE_WEEKDAY: 2, DAY_TYPE_WEEKEND: 2, DAY_TYPE_ABSENCE: 2},
    )


def _at(day: str, hour: int, minute: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=UTC)


def _power_meta(statistic_id: str) -> dict:
    return {
        "mean_type": StatisticMeanType.ARITHMETIC,
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_class": "power",
        "unit_of_measurement": "W",
    }


def _energy_meta(statistic_id: str) -> dict:
    return {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_class": "energy",
        "unit_of_measurement": "kWh",
    }


def _power_rows(values: dict[tuple[str, int], float]) -> list[dict]:
    """Hourly mean-power rows for every (day, hour) in `values`."""
    return [
        {"start": _at(day, hour), "mean": mean, "min": mean, "max": mean}
        for (day, hour), mean in sorted(values.items())
    ]


def _counter_rows(
    increments: dict[tuple[str, int], float],
    skip: set[tuple[str, int]] | None = None,
) -> list[dict]:
    """Cumulative-sum rows (kWh) from per-hour increments.

    The first row's `change` equals its increment because no earlier sum
    exists (prev_sum defaults to 0), so increments must not rely on history
    before DAYS[0].
    """
    rows = []
    total = 0.0
    for key in sorted(increments):
        if skip and key in skip:
            continue
        total += increments[key]
        rows.append({"start": _at(*key), "sum": total, "state": total})
    return rows


def _all_hours(value: float) -> dict[tuple[str, int], float]:
    return {(day, hour): value for day in DAYS for hour in range(24)}


async def _import(hass, metadata: dict, rows: list[dict]) -> None:
    async_import_statistics(hass, metadata, rows)
    await async_wait_recording_done(hass)


def _learner(hass, subentries: list | None = None, **extra) -> ProfileLearner:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LEARNING_WINDOW_DAYS: WINDOW_DAYS, **extra},
        title="Battery Manager",
        version=2,
        subentries_data=subentries or [],
    )
    entry.add_to_hass(hass)
    return ProfileLearner(hass, entry)


async def _run_pinned(learner: ProfileLearner) -> None:
    """Learning run with the window pinned to DAYS (see PINNED_NOW)."""
    with patch.object(dt_util, "now", return_value=PINNED_NOW):
        await learner.async_run_learning()


def _assert_series(series: list, expected: list) -> None:
    assert len(series) == len(expected)
    for hour, want in enumerate(expected):
        if want is None:
            assert series[hour] is None, f"hour {hour}: expected None"
        else:
            assert series[hour] == pytest.approx(want, abs=1e-6), (
                f"hour {hour}: expected {want}, got {series[hour]}"
            )


async def test_fetch_days_learns_bins_from_real_statistics(hass, _min_samples_2):
    """Main path: hourly LTS power rows -> cleaned daily series -> bins.

    AC carries an hour-dependent load (100 + hour W) to prove the hour
    buckets; DC differs between the weekend (300 W) and weekdays (50 W) to
    prove the day-type attribution (D-C3).
    """
    learner = _learner(
        hass,
        **{
            CONF_AC_LOAD_ENTITY: "sensor.ac_load",
            CONF_DC_LOAD_ENTITY: "sensor.dc_load",
        },
    )
    await _import(
        hass,
        _power_meta("sensor.ac_load"),
        _power_rows({(day, hour): 100.0 + hour for day in DAYS for hour in range(24)}),
    )
    await _import(
        hass,
        _power_meta("sensor.dc_load"),
        _power_rows(
            {
                (day, hour): 300.0 if day < "2026-07-13" else 50.0
                for day in DAYS
                for hour in range(24)
            }
        ),
    )

    await _run_pinned(learner)

    daily = learner.data["daily_hours"]
    assert set(daily) == set(DAYS)
    _assert_series(daily["2026-07-13"]["ac"], [100.0 + hour for hour in range(24)])
    _assert_series(daily["2026-07-11"]["dc"], [300.0] * 24)
    _assert_series(daily["2026-07-14"]["dc"], [50.0] * 24)

    profiles = learner.data["profiles"]
    samples = learner.data["samples"]
    assert profiles["ac"]["weekday"]["p50"][13] == 113.0
    assert profiles["ac"]["weekend"]["p50"][13] == 113.0
    assert profiles["dc"]["weekday"]["p50"][5] == 50.0
    assert profiles["dc"]["weekend"]["p50"][5] == 300.0
    assert samples["dc"]["weekday"][5] == 2
    assert samples["dc"]["weekend"][5] == 2

    assert learner.data["day_log"]["2026-07-11"] == {
        "daytype": "weekend",
        "vacation": False,
    }
    assert learner.data["day_log"]["2026-07-13"]["daytype"] == "weekday"
    assert learner.data["diagnostics"]["coverage"] == {"ac": 1.0, "dc": 1.0}
    assert learner.data["diagnostics"]["missing_statistics"] == []
    assert learner.data["computed_at"] == PINNED_NOW.isoformat()
    assert learner.data["window_days"] == WINDOW_DAYS
    # profiles_for_planning applies the D-C6 freshness check against
    # dt_util.now() — evaluate it at the pinned computation time.
    with patch.object(dt_util, "now", return_value=PINNED_NOW):
        assert learner.profiles_for_planning() is not None

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"learning_no_statistics_{learner.entry.entry_id}"
    )
    assert issue is None


async def test_fetch_days_balance_cleaning_and_completeness(hass, _min_samples_2):
    """D-C1/D-C2: counter balance, self-load subtraction, completeness rule.

    - energy counters (has_sum) use the hourly `change` x 1000 (kWh -> Wh);
    - an hour is only valid when EVERY balance entity has a row (the last
      day misses the out-counter row at hour 23 -> dropped);
    - a negative balance hour (counter noise, hour 22) is dropped, not
      learned as 0 W;
    - the in-house load's power feedback is subtracted (zero-filled gaps);
    - a residual below -10 Wh is counted as suspicious and clamped to 0;
    - the measured 24 V PSU feed is subtracted from the AC path.
    """
    last = DAYS[-1]
    learner = _learner(
        hass,
        subentries=[
            ConfigSubentryData(
                data={
                    CONF_LOAD_IN_HOUSE: True,
                    CONF_LOAD_POWER_ENTITY: "sensor.heater_power",
                    CONF_LOAD_CONTROL_SWITCH: None,
                },
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="Heater",
                unique_id=None,
            )
        ],
        **{
            CONF_AC_BALANCE_IN: ["sensor.energy_in"],
            CONF_AC_BALANCE_OUT: ["sensor.energy_out"],
            CONF_SUPPORT_DC24_POWER_ENTITY: "sensor.psu24_power",
        },
    )
    increments_in = {(day, hour): 0.6 for day in DAYS for hour in range(24)}
    increments_in[(last, 22)] = 0.05  # below the out-counter -> negative balance
    increments_out = {(day, hour): 0.1 for day in DAYS for hour in range(24)}
    await _import(hass, _energy_meta("sensor.energy_in"), _counter_rows(increments_in))
    await _import(
        hass,
        _energy_meta("sensor.energy_out"),
        _counter_rows(increments_out, skip={(last, 23)}),
    )
    heater = {
        (day, hour): 700.0 if hour == 9 else 200.0
        for day in DAYS
        for hour in (8, 9, 10, 11, 12)
    }
    await _import(hass, _power_meta("sensor.heater_power"), _power_rows(heater))
    await _import(
        hass, _power_meta("sensor.psu24_power"), _power_rows(_all_hours(40.0))
    )

    await _run_pinned(learner)

    # balance 600-100 = 500; minus PSU feed 40 -> 460; heater hours minus 200.
    expected = [460.0] * 24
    for hour in (8, 10, 11, 12):
        expected[hour] = 260.0
    expected[9] = 0.0  # 500 - 700 - 40 < -10 Wh -> clamped, counted
    daily = learner.data["daily_hours"]
    for day in DAYS[:3]:
        _assert_series(daily[day]["ac"], expected)
    expected_last = list(expected)
    expected_last[22] = None  # negative balance dropped
    expected_last[23] = None  # out-counter row missing -> hour incomplete
    _assert_series(daily[last]["ac"], expected_last)

    assert learner.data["diagnostics"]["negative_residuals"] == 4
    assert learner.data["diagnostics"]["missing_statistics"] == []
    profiles = learner.data["profiles"]
    assert profiles["ac"]["weekday"]["p50"][0] == 460.0
    # Hour 22 has a single surviving weekday sample -> bin stays empty (D-C6).
    assert profiles["ac"]["weekday"]["p50"][22] is None
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"learning_no_statistics_{learner.entry.entry_id}"
    )
    assert issue is None


async def test_missing_statistics_creates_repair_issue(hass, _min_samples_2):
    """A configured source without LTS rows raises learning_no_statistics.

    The workday entity is configured but the (uninstalled) workday
    integration's check_date action fails -> calendar fallback (empty
    future day types), proving the fail-safe branch.
    """
    learner = _learner(
        hass,
        **{
            CONF_AC_LOAD_ENTITY: "sensor.ac_no_stats",
            CONF_WORKDAY_ENTITY: "binary_sensor.workday",
        },
    )

    await _run_pinned(learner)

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"learning_no_statistics_{learner.entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "learning_no_statistics"
    assert issue.translation_placeholders == {"entity_ids": "sensor.ac_no_stats"}
    assert learner.data["diagnostics"]["missing_statistics"] == ["sensor.ac_no_stats"]
    assert learner.data["profiles"]["ac"]["weekday"]["p50"][0] is None
    assert learner.data["diagnostics"]["coverage"]["ac"] == 0.0
    assert learner.data["computed_at"] == PINNED_NOW.isoformat()
    assert learner.data["future_daytypes"] == {}


async def test_state_changes_spans_weekly_chunks(hass, freezer):
    """_state_changes iterates 7-day chunks over the REAL state history.

    Changes on both sides of the first chunk boundary must be collected;
    the coverage start is the first known state row (the synthetic
    start-state row counts), never interpreted as "off".
    """
    learner = _learner(hass)
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    freezer.move_to(t0 - timedelta(days=2))
    hass.states.async_set("switch.dcdc", "off")
    await async_wait_recording_done(hass)
    freezer.move_to(t0 + timedelta(days=2))
    hass.states.async_set("switch.dcdc", "on")
    await async_wait_recording_done(hass)
    freezer.move_to(t0 + timedelta(days=9))
    hass.states.async_set("switch.dcdc", "off")
    await async_wait_recording_done(hass)

    start = t0 - timedelta(hours=1)
    end = start + timedelta(days=16)  # 3 weekly chunks
    changes, first_known = await learner._state_changes(
        "switch.dcdc", start, end, _is_off
    )

    # The synthetic start-state row of chunk 1 (state "off" predates the
    # window) carries the chunk-start timestamp and counts as coverage start.
    assert first_known == start
    # The two real changes straddle the first 7-day chunk boundary; each
    # later chunk prepends its own synthetic start-state row.
    assert set(changes) == {
        (start, True),
        (t0 + timedelta(days=2), False),
        (start + timedelta(days=7), False),
        (t0 + timedelta(days=9), True),
        (start + timedelta(days=14), True),
    }
    assert changes == sorted(changes, key=lambda item: item[0])


async def test_psu48_voltage_gate_end_to_end(hass, _min_samples_2):
    """Rev. 4 gate with real LTS min/max rows (docs/DC_TOPOLOGY.md §9).

    The 48 V PSU hour is fully attributed when max < U_thr (hour 10),
    dropped when min > U_thr (hour 14) and EXCLUDED in the clamp regime
    (hour 16) — on the AC path (minus) and the DC path (plus) alike. The
    measured 24 V PSU feed shifts energy between the paths as well.
    """
    learner = _learner(
        hass,
        **{
            CONF_AC_LOAD_ENTITY: "sensor.ac_load",
            CONF_DC_LOAD_ENTITY: "sensor.dc_load",
            CONF_SUPPORT_DC48_SWITCH: "switch.psu48",
            CONF_SUPPORT_DC48_POWER_W: 60.0,
            CONF_PSU48_OUTPUT_VOLTAGE_V: 49.56,
            CONF_BATTERY_VOLTAGE_ENTITY: "sensor.batt_v",
            CONF_SUPPORT_DC24_POWER_ENTITY: "sensor.psu24_power",
        },
    )
    await _import(hass, _power_meta("sensor.ac_load"), _power_rows(_all_hours(500.0)))
    await _import(hass, _power_meta("sensor.dc_load"), _power_rows(_all_hours(200.0)))
    await _import(
        hass, _power_meta("sensor.psu24_power"), _power_rows(_all_hours(40.0))
    )
    voltage_rows = [
        {
            "start": _at(day, hour),
            "mean": (lo + hi) / 2,
            "min": lo,
            "max": hi,
        }
        for day in DAYS
        for hour, lo, hi in ((10, 49.0, 49.4), (14, 49.7, 50.1), (16, 49.4, 49.8))
    ]
    await _import(
        hass,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": "sensor.batt_v",
            "unit_class": "voltage",
            "unit_of_measurement": "V",
        },
        voltage_rows,
    )
    with freeze_time("2026-07-01 12:00:00+00:00"):  # before the window
        hass.states.async_set("switch.psu48", "on")
        await async_wait_recording_done(hass)

    await _run_pinned(learner)

    expected_ac = [400.0] * 24  # 500 - 60 (PSU) - 40 (PSU24)
    expected_ac[14] = 460.0  # gated off: 500 - 0 - 40
    expected_ac[16] = None  # clamp regime -> hour excluded
    expected_dc = [300.0] * 24  # 200 + 60 + 40
    expected_dc[14] = 240.0
    expected_dc[16] = None
    for day in DAYS:
        _assert_series(learner.data["daily_hours"][day]["ac"], expected_ac)
        _assert_series(learner.data["daily_hours"][day]["dc"], expected_dc)

    profiles = learner.data["profiles"]
    assert profiles["ac"]["weekday"]["p50"][0] == 400.0
    assert profiles["ac"]["weekday"]["p50"][16] is None
    assert profiles["dc"]["weekday"]["p50"][0] == 300.0
    assert profiles["dc"]["weekday"]["p50"][14] == 240.0
    assert learner.data["diagnostics"]["missing_statistics"] == []


async def test_appliance_exclusion_and_coverage_rule(hass, _min_samples_2):
    """Status-only appliance (no LTS): running hours are excluded (D-C2).

    Hours before the entity's first recorded state are UNKNOWN, not
    "off": the first day (no washer history at all) stays completely
    unlearned, and day 2 loses hour 0 (history starts at 00:30).
    """
    learner = _learner(
        hass,
        subentries=[
            ConfigSubentryData(
                data={
                    CONF_APPLIANCE_DETECTION_ENTITY: "sensor.washer",
                    CONF_APPLIANCE_POWER_THRESHOLD_W: 100.0,
                },
                subentry_type=SUBENTRY_TYPE_APPLIANCE,
                title="Washer",
                unique_id=None,
            )
        ],
        **{CONF_AC_LOAD_ENTITY: "sensor.ac_load"},
    )
    await _import(hass, _power_meta("sensor.ac_load"), _power_rows(_all_hours(500.0)))
    # Non-numeric running state exercises the APPLIANCE_RUNNING_STATES fallback.
    for moment, state in (
        ("2026-07-12 00:30:00+00:00", "0"),
        ("2026-07-13 05:10:00+00:00", "running"),
        ("2026-07-13 07:20:00+00:00", "0"),
    ):
        with freeze_time(moment):
            hass.states.async_set("sensor.washer", state)
            await async_wait_recording_done(hass)

    await _run_pinned(learner)

    daily = learner.data["daily_hours"]
    _assert_series(daily["2026-07-11"]["ac"], [None] * 24)
    day2 = [500.0] * 24
    day2[0] = None  # uncovered: first state at 00:30
    _assert_series(daily["2026-07-12"]["ac"], day2)
    day3 = [500.0] * 24
    for hour in (5, 6, 7):  # running 05:10-07:20
        day3[hour] = None
    _assert_series(daily["2026-07-13"]["ac"], day3)
    _assert_series(daily["2026-07-14"]["ac"], [500.0] * 24)

    profiles = learner.data["profiles"]
    assert profiles["ac"]["weekday"]["p50"][8] == 500.0
    # Weekday hour 5 survives only on Tuesday -> below the sample floor.
    assert profiles["ac"]["weekday"]["p50"][5] is None
    assert learner.data["diagnostics"]["missing_statistics"] == []


async def test_support_path_exclusions_end_to_end(hass, _min_samples_2):
    """D-C2 step 3 with real switch histories: dead rail, unmeasured PSU
    feed and uncovered support-switch history all exclude their hours."""
    learner = _learner(
        hass,
        **{
            CONF_AC_LOAD_ENTITY: "sensor.ac_load",
            CONF_SUPPORT_DC24_SWITCH: "switch.psu24",
            CONF_DCDC_SWITCH: "switch.dcdc",
        },
    )
    await _import(hass, _power_meta("sensor.ac_load"), _power_rows(_all_hours(500.0)))
    for entity, moment, state in (
        ("switch.psu24", "2026-07-01 12:00:00+00:00", "off"),
        ("switch.psu24", "2026-07-12 08:00:00+00:00", "on"),
        ("switch.psu24", "2026-07-12 10:00:00+00:00", "off"),
        # The DC/DC history starts INSIDE the window -> day 1 uncovered.
        ("switch.dcdc", "2026-07-12 00:00:00+00:00", "on"),
        ("switch.dcdc", "2026-07-12 08:00:00+00:00", "off"),
        ("switch.dcdc", "2026-07-12 09:00:00+00:00", "on"),
        ("switch.dcdc", "2026-07-12 10:00:00+00:00", "off"),
        ("switch.dcdc", "2026-07-12 12:00:00+00:00", "on"),
    ):
        with freeze_time(moment):
            hass.states.async_set(entity, state)
            await async_wait_recording_done(hass)

    await _run_pinned(learner)

    daily = learner.data["daily_hours"]
    _assert_series(daily["2026-07-11"]["ac"], [None] * 24)  # uncovered history
    day2 = [500.0] * 24
    day2[8] = None  # PSU feeds while DC/DC off, unmeasured -> excluded
    day2[10] = None  # DC/DC off without PSU -> dead rail
    day2[11] = None
    _assert_series(daily["2026-07-12"]["ac"], day2)
    _assert_series(daily["2026-07-13"]["ac"], [500.0] * 24)
    _assert_series(daily["2026-07-14"]["ac"], [500.0] * 24)


async def test_bias_watchdog_and_future_daytypes(hass, _min_samples_2):
    """D-C9 watchdog on real data + holiday tagging + workday.check_date.

    A one-sided bias over LEARNING_BIAS_ALERT_DAYS days raises the
    learning_bias repair issue; a full "off" day of the workday sensor
    tags the Monday as weekend (holiday, §5.3); upcoming day types come
    from the check_date action.
    """
    learner = _learner(
        hass,
        **{
            CONF_AC_LOAD_ENTITY: "sensor.ac_load",
            CONF_WORKDAY_ENTITY: "binary_sensor.workday",
        },
    )
    await _import(hass, _power_meta("sensor.ac_load"), _power_rows(_all_hours(500.0)))
    for moment, state in (
        ("2026-07-01 12:00:00+00:00", "on"),
        ("2026-07-13 00:00:00+00:00", "off"),  # Monday fully off -> holiday
        ("2026-07-14 00:00:00+00:00", "on"),
    ):
        with freeze_time(moment):
            hass.states.async_set("binary_sensor.workday", state)
            await async_wait_recording_done(hass)

    def _check_date(call) -> dict:
        return {"workday": call.data["check_date"] != "2026-07-17"}

    hass.services.async_register(
        "workday", "check_date", _check_date, supports_response=SupportsResponse.ONLY
    )

    # Watchdog state: 13 days of one-sided bias; the run appends yesterday.
    seeded_bins = {
        day_type: {"p50": [100.0] * 24, "p80": [110.0] * 24}
        for day_type in ("weekday", "weekend", "absence")
    }
    learner.data["profiles"] = {"ac": seeded_bins, "dc": None}
    # Matching binding, otherwise _apply_source_binding discards the seeded
    # bins as "learned from other entities" before the watchdog sees them.
    learner.data["source_entities"] = {"ac": ["sensor.ac_load"], "dc": []}
    learner.data["validation"]["ac"] = [
        {
            "day": (date(2026, 6, 28) + timedelta(days=offset)).isoformat(),
            "bias_w": -100.0,
            "mae_w": 100.0,
            "hours": 24,
        }
        for offset in range(13)
    ]

    await _run_pinned(learner)

    # Yesterday (Tuesday): bins 100 W vs. cleaned actuals 500 W.
    last_entry = learner.data["validation"]["ac"][-1]
    assert last_entry == {
        "day": "2026-07-14",
        "bias_w": -400.0,
        "mae_w": 400.0,
        "hours": 24,
    }
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"learning_bias_{learner.entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "learning_bias"

    assert learner.data["day_log"]["2026-07-13"] == {
        "daytype": "weekend",  # holiday counts as weekend
        "vacation": False,
    }
    assert learner.data["day_log"]["2026-07-14"]["daytype"] == "weekday"
    assert learner.data["future_daytypes"] == {
        "2026-07-15": "weekday",
        "2026-07-16": "weekday",
        "2026-07-17": "weekend",
        "2026-07-18": "weekday",
    }
    assert learner.planning_daytype(date(2026, 7, 17)) == "weekend"
    assert learner.planning_daytype(date(2026, 7, 20)) == "weekday"
