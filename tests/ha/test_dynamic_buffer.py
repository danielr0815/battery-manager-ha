"""Tests for the dynamic SOC buffer (D-C8, docs/CONSUMPTION_FORECAST.md).

`_dynamic_buffer` sizes the planning buffer from the learned P80−P50
consumption band over the critical window (now until the first forecast PV
surplus slot). The unit tests drive the method directly with hand-built
slots; two end-to-end tests pin the `soc_buffer_source` diagnostics of a real
refresh with and without learned quantiles.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_BUFFER_MAX_PERCENT,
    CONF_BUFFER_MIN_PERCENT,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)
from custom_components.battery_manager.core.model import HourSlot

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


# The expected values are derived from the BUILT config (not hardcoded), so a
# changed DEFAULT_CONFIG efficiency/capacity does not silently break the math.
def _etas(config):
    return config.battery.eta_discharge, config.inverter.eta


LEARNED_AC_BINS = {
    "weekday": {"p50": [100.0] * 24, "p80": [120.0] * 24},
    "weekend": {"p50": [80.0] * 24, "p80": [95.0] * 24},
    "absence": {"p50": [None] * 24, "p80": [None] * 24},
}


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up (lingering-timer guard, same reason
    as in test_config_flow.py)."""
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _setup_entry(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        options=options or {},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_soc", "55", {"unit_of_measurement": "%"})
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _slots(specs):
    """One full-hour slot per (pv_wh, ac_wh, dc_wh) spec, starting at midnight."""
    return [
        HourSlot(
            index=i,
            start=datetime(2026, 7, 3) + timedelta(hours=i),
            duration=1.0,
            hour_of_day=i,
            pv_wh=pv,
            ac_wh=ac,
            dc_wh=dc,
        )
        for i, (pv, ac, dc) in enumerate(specs)
    ]


async def test_window_stops_at_first_surplus_slot(hass):
    """The critical window ends at the FIRST slot with pv > ac + dc; later
    slots (and their band) contribute nothing to the uncertainty."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()
    slots = _slots(
        [
            (0.0, 100.0, 50.0),
            (0.0, 100.0, 50.0),
            (1000.0, 100.0, 50.0),
            (0.0, 100.0, 50.0),
        ]
    )
    band = {"ac": [100.0] * 4, "dc": [100.0] * 4}

    buffer_percent, diag = coordinator._dynamic_buffer(config, slots, band)

    eta_dis, eta_inv = _etas(config)
    expected_wh = 2 * (100.0 / (eta_dis * eta_inv) + 100.0 / eta_dis)
    assert diag["buffer_window_hours"] == 2
    assert diag["buffer_uncertainty_wh"] == round(expected_wh, 0)
    expected = round(
        min(max(expected_wh / config.battery.capacity_wh * 100.0, 3.0), 15.0), 1
    )
    assert buffer_percent == expected == 8.5
    assert diag["soc_buffer_source"] == "dynamic"
    assert diag["soc_buffer_effective"] == buffer_percent


async def test_eta_conversion_differs_between_ac_and_dc(hass):
    """AC uncertainty converts through discharge AND inverter efficiency,
    DC only through discharge efficiency — same band, different Wh."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()
    slots = _slots([(0.0, 100.0, 50.0)])

    _, diag_ac = coordinator._dynamic_buffer(
        config, slots, {"ac": [100.0], "dc": [0.0]}
    )
    _, diag_dc = coordinator._dynamic_buffer(
        config, slots, {"ac": [0.0], "dc": [100.0]}
    )

    eta_dis, eta_inv = _etas(config)
    assert diag_ac["buffer_uncertainty_wh"] == round(100.0 / (eta_dis * eta_inv), 0)
    assert diag_dc["buffer_uncertainty_wh"] == round(100.0 / eta_dis, 0)
    assert diag_ac["buffer_uncertainty_wh"] > diag_dc["buffer_uncertainty_wh"]


async def test_no_surplus_slot_counts_whole_horizon(hass):
    """No PV surplus anywhere -> the window spans the whole horizon."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()
    slots = _slots([(0.0, 100.0, 50.0)] * 3)

    _, diag = coordinator._dynamic_buffer(
        config, slots, {"ac": [10.0] * 3, "dc": [10.0] * 3}
    )

    assert diag["buffer_window_hours"] == 3
    assert diag["buffer_uncertainty_wh"] > 0.0


async def test_buffer_clamps_to_configured_minimum(hass):
    """A near-zero band must not shrink the buffer below buffer_min_percent."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()

    buffer_percent, diag = coordinator._dynamic_buffer(
        config, _slots([(0.0, 100.0, 50.0)]), {"ac": [1.0], "dc": [0.0]}
    )

    assert buffer_percent == 3.0
    assert diag["soc_buffer_effective"] == 3.0


async def test_buffer_clamps_to_configured_maximum(hass):
    """A huge band must not blow the buffer past buffer_max_percent."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()

    buffer_percent, diag = coordinator._dynamic_buffer(
        config, _slots([(0.0, 100.0, 50.0)]), {"ac": [10000.0], "dc": [0.0]}
    )

    assert buffer_percent == 15.0
    assert diag["soc_buffer_effective"] == 15.0


async def test_inverted_min_max_pair_does_not_pin_silently(hass):
    """Defensive: an inverted clamp pair (old/hand-edited options — the
    options flow validates min < max) collapses to the low bound instead of
    producing an always-min or always-max buffer."""
    entry = await _setup_entry(
        hass,
        options={CONF_BUFFER_MIN_PERCENT: 12.0, CONF_BUFFER_MAX_PERCENT: 5.0},
    )
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()

    buffer_percent, diag = coordinator._dynamic_buffer(
        config, _slots([(0.0, 100.0, 50.0)]), {"ac": [100.0], "dc": [100.0]}
    )

    # raw would be ~4.3 % — the inverted pair clamps to max(low, high) = low.
    assert buffer_percent == 12.0
    assert diag["soc_buffer_effective"] == 12.0


async def test_band_shorter_than_slots_contributes_zero(hass):
    """Slots beyond the band length (statically filled) add no uncertainty
    but still count into the window."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = coordinator.build_system_config()
    slots = _slots([(0.0, 100.0, 50.0)] * 3)

    _, diag = coordinator._dynamic_buffer(config, slots, {"ac": [100.0], "dc": []})

    eta_dis, eta_inv = _etas(config)
    assert diag["buffer_window_hours"] == 3
    assert diag["buffer_uncertainty_wh"] == round(100.0 / (eta_dis * eta_inv), 0)


async def test_refresh_reports_dynamic_source_with_learned_quantiles(hass):
    """End-to-end: learned P50/P80 bins activate the dynamic buffer in a real
    refresh — the published diagnostics read source=dynamic and the effective
    buffer matches the uncertainty/clamp arithmetic. The planner's `now` is
    PINNED to an early morning (pattern: test_coordinator.py) so the window
    contains at least one deficit slot regardless of the wall clock."""
    pinned = dt_util.as_local(datetime(2026, 7, 19, 5, 30, tzinfo=UTC))
    with patch.object(dt_util, "now", return_value=pinned):
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        learner = coordinator.learner
        learner.data["profiles"] = {"ac": LEARNED_AC_BINS, "dc": None}
        learner.data["source_entities"] = {"ac": [], "dc": []}
        learner.data["computed_at"] = dt_util.now().isoformat()

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    diag = coordinator.data["consumption_profile"]
    assert diag["soc_buffer_source"] == "dynamic"
    assert diag["buffer_window_hours"] >= 1
    assert diag["buffer_uncertainty_wh"] > 0.0
    capacity_wh = coordinator.build_system_config().battery.capacity_wh
    expected = round(
        min(max(diag["buffer_uncertainty_wh"] / capacity_wh * 100.0, 3.0), 15.0), 1
    )
    assert diag["soc_buffer_effective"] == expected


async def test_refresh_reports_fixed_source_without_quantiles(hass):
    """End-to-end fallback: no learned quantiles -> the fixed planning buffer
    stays in charge (scalar path) and is reported as source=fixed."""
    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    diag = coordinator.data["consumption_profile"]
    assert diag["soc_buffer_source"] == "fixed"
    assert diag["soc_buffer_effective"] == 5.0
