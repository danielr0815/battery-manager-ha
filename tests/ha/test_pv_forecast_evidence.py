"""PV authority is bound to the publication's local calendar day."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_coordinator import ENTRY_DATA

from custom_components.battery_manager.const import (
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_POWER_W,
    DOMAIN,
    SUBENTRY_TYPE_LOAD,
)
from custom_components.battery_manager.daily_pv import DailyPVCache

MIDNIGHT = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
SOURCES = ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after")


def publish(hass, values):
    for entity, value in zip(SOURCES, values, strict=True):
        hass.states.async_set(entity, str(value), {"unit_of_measurement": "kWh"})


async def setup(hass, *, subentries=()):
    hass.states.async_set("sensor.test_soc", "65")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="PV",
        version=2,
        subentries_data=list(subentries),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_pv_totals_cross_midnight_independently(hass, freezer):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(MIDNIGHT - timedelta(minutes=5))
    publish(hass, (9, 0.2, 1.5))
    c = await setup(hass)
    assert c.data["input_forecasts_kwh"] == [9, 0.2, 1.5]
    freezer.move_to(MIDNIGHT)
    publish(hass, (0.2, 1.5, "unavailable"))
    await c.async_refresh()
    await hass.async_block_till_done()
    assert c.data["input_forecasts_kwh"] == [0.2, 1.5, None]
    assert c.data["pv_source"]["2026-11-20"] == "unknown"
    slots = c._last_planner_recording[1].slots
    totals = [
        sum(
            s.pv_wh
            for s in slots
            if s.start.date() == MIDNIGHT.date() + timedelta(days=i)
        )
        for i in range(3)
    ]
    assert totals == pytest.approx([200, 1500, 0])


async def test_standing_today_value_cannot_roll_forward(hass, freezer):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(MIDNIGHT - timedelta(minutes=5))
    publish(hass, (9, "unavailable", 1.5))
    c = await setup(hass)
    assert c.data["input_forecasts_kwh"] == [9, None, 1.5]
    freezer.move_to(MIDNIGHT)
    with patch.object(c, "_schedule_replan"):
        await c.async_refresh()
    assert not c.last_update_success


def register_forecast(hass, families=("roof", "roof", "roof")):
    provider = MockConfigEntry(domain="forecast_solar")
    provider.add_to_hass(hass)
    for entity, family, role in zip(
        SOURCES, families, ("today", "tomorrow", "day_after"), strict=True
    ):
        er.async_get(hass).async_get_or_create(
            "sensor",
            "forecast_solar",
            f"{family}_{role}",
            suggested_object_id=entity.removeprefix("sensor."),
            config_entry=provider,
        )


@pytest.mark.parametrize(
    ("publication", "hours_elapsed"),
    [("2026-03-28T23:30:00+01:00", 23), ("2026-10-24T23:30:00+02:00", 25)],
)
async def test_rolling_roles_use_provider_series_and_local_date(
    hass, freezer, publication, hours_elapsed
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    cache = DailyPVCache()
    register_forecast(hass, ("roof", "roof", "garden"))
    when = datetime.fromisoformat(publication)
    freezer.move_to(when)
    publish(hass, (9, 0.2, 1.5))
    assert cache.read(hass, SOURCES, when) == [9, 0.2, 1.5]
    after = when.astimezone(UTC) + timedelta(hours=hours_elapsed)
    freezer.move_to(after)
    assert dt_util.as_local(after).hour == 23
    publish(hass, ("unavailable", "unavailable", "unavailable"))
    assert cache.read(hass, SOURCES, after) == [0.2, None, None]
    assert DailyPVCache().read(hass, SOURCES, after) is None


async def test_cache_honours_72_absolute_hours_even_when_the_calendar_day_still_matches(
    hass, freezer
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    register_forecast(hass)
    cache = DailyPVCache()
    first = datetime.fromisoformat("2026-10-24T00:00:00+02:00")
    freezer.move_to(first)
    publish(hass, (0, 1, 2))
    assert cache.read(hass, SOURCES, first) == [0, 1, 2]
    limit = first.astimezone(UTC) + timedelta(hours=72)
    freezer.move_to(limit)
    assert dt_util.as_local(limit).isoformat() == "2026-10-26T23:00:00+01:00"
    assert cache.read(hass, SOURCES, limit) == [2, None, None]
    assert cache.read(hass, SOURCES, limit + timedelta(seconds=1)) is None


async def test_same_day_fallback_zero_unknown_rebind_and_new_publication(hass, freezer):
    await hass.config.async_set_time_zone("UTC")
    cache = DailyPVCache()
    freezer.move_to(MIDNIGHT)
    publish(hass, (0, "nan", -1))
    assert cache.read(hass, SOURCES, MIDNIGHT) == [0, None, 0]
    later = MIDNIGHT + timedelta(minutes=1)
    freezer.move_to(later)
    publish(hass, ("unavailable", 0.5, 0.3))
    assert cache.read(hass, SOURCES, later) == [0, 0.5, 0.3]
    replacement = ("sensor.replacement", *SOURCES[1:])
    hass.states.async_set("sensor.replacement", "10", {"restored": True})
    assert cache.read(hass, replacement, later) is None
    hass.states.async_set("sensor.replacement", "0.4")
    assert cache.read(hass, replacement, later) == [0.4, 0.5, 0.3]


async def test_hourly_evidence_fills_only_its_slots_on_unknown_middle_day(
    hass, freezer
):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(MIDNIGHT)
    publish(hass, (0, "unavailable", 2))
    key = "2026-11-19T12:00:00+00:00"
    hass.states.async_set(SOURCES[0], "0", {"wh_period_ac": {key: 250}})
    c = await setup(hass)
    inputs = c._last_planner_recording[1]
    assert c.data["input_forecasts_kwh"] == [0, None, 2]
    assert c.data["pv_source"]["2026-11-19"] == "hourly_only"
    assert [s.pv_wh for s in inputs.slots if s.start.day == 19 and s.pv_wh] == [250]
    assert inputs.pv_daily_credit == (((MIDNIGHT + timedelta(days=2)).date(), 2.0),)


async def test_missing_today_clock_survives_fast_updates_and_reload(
    hass, freezer, hass_storage
):
    from custom_components.battery_manager.fast_updates import async_fast_update

    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(MIDNIGHT - timedelta(minutes=5))
    publish(hass, (1, "unknown", 3))
    switch = "switch.flexible_load"
    hass.states.async_set(switch, "off")
    subentry = ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_LOAD,
        title="Load",
        unique_id=None,
        data={CONF_LOAD_CONTROL_SWITCH: switch, CONF_LOAD_POWER_W: 300},
    )
    c = await setup(hass, subentries=(subentry,))
    freezer.move_to(MIDNIGHT)
    with patch.object(c, "_schedule_replan"):
        await async_fast_update(c)
        await c.async_refresh()
        assert c._data_stale_since == MIDNIGHT
        freezer.move_to(MIDNIGHT + timedelta(minutes=45))
        await async_fast_update(c)
        await c.async_refresh()
        assert c._data_stale_since == MIDNIGHT
        assert not c._stale_shed_active
    await c.async_flush_persistent_state()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert (
        hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["stale_since"]
        == MIDNIGHT.isoformat()
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restored = hass.data[DOMAIN][entry.entry_id]
    assert restored is not c and restored._data_stale_since == MIDNIGHT
    freezer.move_to(MIDNIGHT + timedelta(hours=2))
    await restored.async_refresh()
    assert restored._stale_shed_active
    publish(hass, (0, "unknown", 3))
    await restored.async_refresh()
    assert restored.last_update_success
    assert restored._data_stale_since is None
    assert not restored._stale_shed_active


async def test_registered_single_entity_needs_new_report_to_reuse_unchanged_number(
    hass, freezer
):
    await hass.config.async_set_time_zone("UTC")
    provider = MockConfigEntry(domain="forecast_solar")
    provider.add_to_hass(hass)
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        "forecast_solar",
        "opaque-series-id",
        suggested_object_id="pv_today",
        config_entry=provider,
    )
    cache = DailyPVCache()
    previous = MIDNIGHT - timedelta(minutes=1)
    freezer.move_to(previous)
    publish(hass, (1, "unavailable", 0))
    assert cache.read(hass, SOURCES, previous) == [1, None, 0]
    freezer.move_to(MIDNIGHT)
    assert cache.read(hass, SOURCES, MIDNIGHT) is None
    hass.states.async_set(entity.entity_id, "1", {"unit_of_measurement": "kWh"})
    state = hass.states.get(entity.entity_id)
    assert state.last_updated == previous and state.last_reported == MIDNIGHT
    assert cache.read(hass, SOURCES, MIDNIGHT) == [1, None, None]
