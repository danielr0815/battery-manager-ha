"""Bad hourly PV buckets must leave adjacent forecasts and fallback usable."""

from datetime import UTC, datetime

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_coordinator import ENTRY_DATA

from custom_components.battery_manager.const import DOMAIN

TODAY = "sensor.pv_today"
NOVEMBER = datetime(2026, 11, 18, 5, tzinfo=UTC)
BAD_NOVEMBER = "2026-11-31T10:00:00+01:00"
NOVEMBER_BUCKET = "2026-11-18T10:15:00+01:00"


def _publish_today(hass, daily_kwh, **curves):
    hass.states.async_set(
        TODAY, str(daily_kwh), {"unit_of_measurement": "kWh", **curves}
    )


def _published_pv(coordinator, day):
    return {
        row["datetime"]: row["pv_production_wh"]
        for row in coordinator.data["hourly_details"]
        if row["datetime"].startswith(day)
    }


@pytest.fixture
async def start_entry(hass, freezer):
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)

    async def start(now, daily_kwh, **curves):
        freezer.move_to(now)
        await hass.config.async_set_time_zone("Europe/Berlin")
        _publish_today(hass, daily_kwh, **curves)
        hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
        for entity_id in ("sensor.pv_tomorrow", "sensor.pv_day_after"):
            hass.states.async_set(entity_id, "0", {"unit_of_measurement": "kWh"})
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return hass.data[DOMAIN][entry.entry_id]

    yield start
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("prior_good", [False, True])
async def test_impossible_date_preserves_adjacent_published_energy(
    hass, start_entry, prior_good
):
    if prior_good:
        coordinator = await start_entry(NOVEMBER, 0.2, wh_period={NOVEMBER_BUCKET: 80})
        assert (
            _published_pv(coordinator, "2026-11-18")["2026-11-18T10:00:00+01:00"] == 80
        )

    curve = {BAD_NOVEMBER: 500, NOVEMBER_BUCKET: 200}
    if prior_good:
        _publish_today(hass, 0.2, wh_period=curve)
        await coordinator.async_refresh()
    else:
        coordinator = await start_entry(NOVEMBER, 0.2, wh_period=curve)

    assert coordinator.last_update_success
    pv = _published_pv(coordinator, "2026-11-18")
    assert pv["2026-11-18T10:00:00+01:00"] == 200
    assert sum(pv.values()) == pytest.approx(200)
    assert coordinator.data["pv_source"]["2026-11-18"] == "hourly"
    assert len(coordinator.data["hourly_details"]) == 66


@pytest.mark.parametrize("prior_good", [False, True])
async def test_unusable_publication_uses_hourly_cache_or_daily_fallback(
    hass, start_entry, prior_good
):
    if prior_good:
        coordinator = await start_entry(
            NOVEMBER,
            0.9,
            wh_period={NOVEMBER_BUCKET: 900},
            wh_period_p10={NOVEMBER_BUCKET: 400},
            wh_period_p90={NOVEMBER_BUCKET: 1000},
        )

    curves = dict(
        wh_period={BAD_NOVEMBER: 500, "malformed": 900},
        wh_period_p10={NOVEMBER_BUCKET: 100},
        wh_period_p90={NOVEMBER_BUCKET: 2000},
    )
    if prior_good:
        _publish_today(hass, 0.9, **curves)
        await coordinator.async_refresh()
    else:
        coordinator = await start_entry(NOVEMBER, 0.9, **curves)

    assert coordinator.last_update_success
    pv = _published_pv(coordinator, "2026-11-18")
    assert sum(pv.values()) == pytest.approx(900)
    _, inputs, result = coordinator._last_planner_recording
    assert len(inputs.slots) == len(result.trajectory.flows) == 66
    slot = next(
        s for s in inputs.slots if s.start.isoformat() == "2026-11-18T10:00:00+01:00"
    )
    if prior_good:
        assert coordinator.data["pv_source"]["2026-11-18"] == "hourly"
        assert pv[slot.start.isoformat()] == 900
        assert (slot.pv_p10_wh, slot.pv_p90_wh) == (400, 1000)
    else:
        assert coordinator.data["pv_source"]["2026-11-18"] == "two_window"
        assert sum(energy > 0 for energy in pv.values()) > 1
        assert (slot.pv_p10_wh, slot.pv_p90_wh) == (None, None)


async def test_bad_median_and_band_buckets_keep_distinct_dst_folds(start_entry, caplog):
    curves = dict(
        wh_period={
            "2026-10-25T02:00:00+02:00": 300,
            "2026-10-25T02:15:00+02:00": 100,
            "2026-10-25T02:00:00+01:00": 500,
            "2026-10-25T03:15:00+01:00": -5,
            "2026-10-25T07:00:00+01:00": "NaN",
            "2026-10-25T08:00:00+01:00": "inf",
            "2026-10-25T09:00:00+01:00": "-inf",
            "2026-10-25T10:00:00+01:00": "bad Wh",
            "9999-12-31T23:59:59+00:00": 800,
            BAD_NOVEMBER: 800,
        },
        wh_period_p10={
            "2026-10-25T02:00:00+02:00": 160,
            "2026-10-25T02:15:00+02:00": 40,
            "2026-10-25T02:00:00+01:00": 250,
            BAD_NOVEMBER: 700,
        },
        wh_period_p90={
            "2026-10-25T02:00:00+02:00": 480,
            "2026-10-25T02:15:00+02:00": 120,
            "2026-10-25T02:00:00+01:00": 750,
            "2026-10-25T03:00:00+24:00": 650,
        },
    )
    with caplog.at_level(
        "DEBUG", logger="custom_components.battery_manager.coordinator"
    ):
        coordinator = await start_entry(
            datetime(2026, 10, 24, 22, tzinfo=UTC), 0.9, **curves
        )
        assert coordinator.last_update_success
        # Re-reading the same HA publication must not repeat the diagnostic.
        await coordinator.async_refresh()

    _, inputs, _ = coordinator._last_planner_recording
    day = [slot for slot in inputs.slots if slot.start.day == 25]
    assert len(day) == 25
    folds = [slot for slot in day if slot.start.hour == 2]
    assert [slot.start.isoformat() for slot in folds] == [
        "2026-10-25T02:00:00+02:00",
        "2026-10-25T02:00:00+01:00",
    ]
    assert [(s.pv_wh, s.pv_p10_wh, s.pv_p90_wh) for s in folds] == [
        (400, 200, 600),
        (500, 250, 750),
    ]
    now = inputs.now
    median, _, _ = coordinator._get_pv_hourly(now)
    assert len(median) == 3
    assert median[datetime.fromisoformat("2026-10-25T03:00:00+01:00")] == 0
    reports = [
        record.getMessage()
        for record in caplog.records
        if "Hourly PV bucket validation" in record.getMessage()
        and TODAY in record.getMessage()
    ]
    assert len(reports) == 1
    assert "invalid_timestamp=4" in reports[0]
    assert "invalid_value=1" in reports[0]
    assert "non_finite_value=3" in reports[0]
    assert "negative_value_clamped=1" in reports[0]


async def test_unusable_band_leaves_fresh_median_and_fresh_other_band(
    hass, start_entry
):
    coordinator = await start_entry(
        NOVEMBER,
        0.2,
        wh_period={NOVEMBER_BUCKET: 200},
        wh_period_p10={NOVEMBER_BUCKET: 90},
        wh_period_p90={NOVEMBER_BUCKET: 350},
    )
    _publish_today(
        hass,
        0.2,
        wh_period={NOVEMBER_BUCKET: 200},
        wh_period_p10={BAD_NOVEMBER: 100},
        wh_period_p90={NOVEMBER_BUCKET: 360},
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    _, inputs, _ = coordinator._last_planner_recording
    slot = next(
        s for s in inputs.slots if s.start.isoformat() == "2026-11-18T10:00:00+01:00"
    )
    assert (slot.pv_wh, slot.pv_p10_wh, slot.pv_p90_wh) == (200, None, 360)


async def test_multiday_dc_uses_own_local_publication_day_only(start_entry):
    # 22:15 UTC is already the following day in Berlin, which has both folds.
    published = datetime(2026, 10, 24, 22, 15, tzinfo=UTC)
    first = "2026-10-25T02:00:00+02:00"
    second = "2026-10-25T02:00:00+01:00"
    next_day = "2026-10-26T10:00:00+01:00"
    coordinator = await start_entry(
        published,
        0.8,
        wh_period={first: 600, second: 400, next_day: 900},
        wh_period_p10={first: 500, second: 300, next_day: 700},
        wh_period_p90={first: 800, second: 600, next_day: 1000},
    )
    _, inputs, _ = coordinator._last_planner_recording
    slots = {s.start.isoformat(): s for s in inputs.slots}
    assert (slots[first].pv_wh, slots[first].pv_p10_wh, slots[first].pv_p90_wh) == (
        480,
        400,
        640,
    )
    assert (slots[second].pv_wh, slots[second].pv_p10_wh, slots[second].pv_p90_wh) == (
        320,
        240,
        480,
    )
    assert (slots[next_day].pv_wh, slots[next_day].pv_p10_wh) == (0, None)


@pytest.mark.parametrize(
    ("curves", "expected"),
    [
        (
            dict(
                wh_period={NOVEMBER_BUCKET: 1000},
                wh_period_ac={},
                wh_period_ac_p10={NOVEMBER_BUCKET: 450},
                wh_period_p90={NOVEMBER_BUCKET: 1200},
            ),
            (500, 450, 600),
        ),
        (
            dict(
                wh_period={NOVEMBER_BUCKET: 1000},
                wh_period_ac={BAD_NOVEMBER: 200},
                wh_period_ac_p10={NOVEMBER_BUCKET: 450},
            ),
            (500, 450, None),
        ),
        (
            dict(
                wh_period={NOVEMBER_BUCKET: 1000},
                wh_period_ac={NOVEMBER_BUCKET: 500},
                wh_period_p10={NOVEMBER_BUCKET: 700},
                wh_period_p90={NOVEMBER_BUCKET: 1200},
            ),
            (500, 350, 600),
        ),
        (
            dict(
                wh_period_ac={NOVEMBER_BUCKET: 500},
                wh_period_p10={NOVEMBER_BUCKET: 700},
                wh_period_ac_p90={NOVEMBER_BUCKET: 600},
            ),
            (500, None, 600),
        ),
        (
            dict(wh_period={NOVEMBER_BUCKET: 1000}, wh_period_ac={NOVEMBER_BUCKET: 0}),
            (0, None, None),
        ),
    ],
)
async def test_hourly_channels_keep_ac_provenance(start_entry, curves, expected):
    coordinator = await start_entry(NOVEMBER, 0.5, **curves)
    _, inputs, _ = coordinator._last_planner_recording
    slot = next(
        s for s in inputs.slots if s.start.isoformat() == "2026-11-18T10:00:00+01:00"
    )
    assert (slot.pv_wh, slot.pv_p10_wh, slot.pv_p90_wh) == expected


async def test_fresh_hour_wins_over_cached_median_and_bands_as_a_unit(
    hass, freezer, start_entry
):
    tomorrow = "2026-11-19T10:15:00+01:00"
    coordinator = await start_entry(NOVEMBER, 0.9, wh_period_ac={NOVEMBER_BUCKET: 900})
    hass.states.async_set(
        "sensor.pv_tomorrow",
        "0.9",
        {
            "unit_of_measurement": "kWh",
            "wh_period_ac": {NOVEMBER_BUCKET: 900, tomorrow: 900},
            "wh_period_ac_p10": {NOVEMBER_BUCKET: 800, tomorrow: 500},
            "wh_period_ac_p90": {NOVEMBER_BUCKET: 1000, tomorrow: 1000},
        },
    )
    await coordinator.async_refresh()
    _, captured, _ = coordinator._last_planner_recording
    assert coordinator.pv_plan_evidence_valid(
        coordinator.build_system_config(), captured
    )

    freezer.move_to(NOVEMBER.replace(minute=5))
    hass.states.async_set("sensor.pv_tomorrow", "unavailable")
    _publish_today(
        hass,
        0.2,
        wh_period_ac={NOVEMBER_BUCKET: 200},
        wh_period_ac_p90={NOVEMBER_BUCKET: 300},
    )
    assert not coordinator.pv_plan_evidence_valid(
        coordinator.build_system_config(), captured
    )
    await coordinator.async_refresh()
    _, inputs, _ = coordinator._last_planner_recording
    slots = {s.start.isoformat(): s for s in inputs.slots if s.start.hour == 10}
    today = slots["2026-11-18T10:00:00+01:00"]
    assert (today.pv_wh, today.pv_p10_wh, today.pv_p90_wh) == (200, None, 300)
    next_day = slots["2026-11-19T10:00:00+01:00"]
    assert (next_day.pv_wh, next_day.pv_p10_wh, next_day.pv_p90_wh) == (900, 500, 1000)


async def test_daily_role_breaks_only_equal_publication_timestamps(
    hass, freezer, start_entry
):
    coordinator = await start_entry(
        NOVEMBER,
        0.2,
        wh_period_ac={NOVEMBER_BUCKET: 200},
        wh_period_ac_p10={NOVEMBER_BUCKET: 100},
    )
    hour = datetime.fromisoformat("2026-11-18T10:00:00+01:00")

    def publish_overlap():
        hass.states.async_set(
            "sensor.pv_tomorrow",
            "0.5",
            {"unit_of_measurement": "kWh", "wh_period_ac": {NOVEMBER_BUCKET: 500}},
            force_update=True,
        )

    publish_overlap()
    median, low, high = coordinator._get_pv_hourly(NOVEMBER)
    assert (median[hour], low[hour], high) == (200, 100, None)

    newer = NOVEMBER.replace(minute=5)
    freezer.move_to(newer)
    publish_overlap()
    median, low, high = coordinator._get_pv_hourly(newer)
    assert (median[hour], low, high) == (500, None, None)


async def test_future_day_paired_curves_are_independent_from_other_daily_states(
    hass, start_entry
):
    tomorrow = "2026-11-19T10:15:00+01:00"
    no_pair = "2026-11-20T10:15:00+01:00"
    coordinator = await start_entry(
        NOVEMBER,
        0.8,
        wh_period={NOVEMBER_BUCKET: 1000, tomorrow: 1600, no_pair: 1000},
        wh_period_ac={tomorrow: 800},
        wh_period_p10={tomorrow: 1200, no_pair: 500},
        wh_period_p90={tomorrow: 1800, no_pair: 1400},
    )
    # A total from another publication cannot act as the AC reference for this
    # curve. Tomorrow has a matching pair of its own, the day after does not.
    hass.states.async_set("sensor.pv_day_after", "1", {"unit_of_measurement": "kWh"})
    await coordinator.async_refresh()
    median, low, high = coordinator._get_pv_hourly(NOVEMBER)
    paired = datetime.fromisoformat("2026-11-19T10:00:00+01:00")
    guess = datetime.fromisoformat("2026-11-20T10:00:00+01:00")
    assert (median[paired], low[paired], high[paired]) == (800, 600, 900)
    assert guess not in median and guess not in low and guess not in high


@pytest.mark.parametrize(
    ("ac_minute", "band_minute", "expected_p10"),
    [(45, 0, None), (0, 0, 192), (0, 45, None)],
)
async def test_dc_band_needs_matching_raw_periods_for_ac_conversion(
    start_entry, ac_minute, band_minute, expected_p10
):
    # The only daily state is for today. Tomorrow's conversion must come
    # from the raw timestamps of the paired medians and the converted band.
    dc = "2026-11-19T10:00:00+01:00"
    ac = f"2026-11-19T10:{ac_minute:02d}:00+01:00"
    band = f"2026-11-19T10:{band_minute:02d}:00+01:00"
    coordinator = await start_entry(
        NOVEMBER,
        0.9,
        wh_period={dc: 250},
        wh_period_ac={ac: 200},
        wh_period_p10={band: 240},
        wh_period_ac_p90={ac: 260},
    )
    _, inputs, _ = coordinator._last_planner_recording
    slot = next(s for s in inputs.slots if s.start.isoformat() == dc)
    assert (slot.pv_wh, slot.pv_p10_wh, slot.pv_p90_wh) == (200, expected_p10, 260)


@pytest.mark.parametrize(
    ("median_attr", "band_attr"),
    [
        ("wh_period_ac", "wh_period_ac_p10"),
        ("wh_period", "wh_period_p10"),
        ("wh_period", "wh_period_ac_p10"),
    ],
)
@pytest.mark.parametrize("both_periods", [False, True])
async def test_hourly_band_must_cover_all_periods_of_selected_median(
    start_entry, median_attr, band_attr, both_periods
):
    first = "2026-11-18T10:00:00+01:00"
    second = "2026-11-18T10:15:00+01:00"
    band = {second: 40}
    if both_periods:
        band[first] = 40
    coordinator = await start_entry(
        NOVEMBER, 0.1, **{median_attr: {first: 50, second: 50}, band_attr: band}
    )
    _, inputs, _ = coordinator._last_planner_recording
    slot = next(s for s in inputs.slots if s.start.isoformat() == first)
    assert (slot.pv_wh, slot.pv_p10_wh) == (100, 80 if both_periods else None)
