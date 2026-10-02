"""Optional EPEX series through HA configuration, planning and diagnostics."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import rig as rig
from test_reserve_policy import configure, update

from custom_components.battery_manager.market import (
    CONF_MARKET_ENABLED,
    CONF_MARKET_ENTITY,
    price_entity,
    read_prices,
)

ENTITY = "sensor.epex_spot_data_price"


def rows(now, field="price_eur_per_mwh", values=(100, 300, 100)):
    return [
        {
            "start_time": (now + timedelta(hours=i)).isoformat(),
            "end_time": (now + timedelta(hours=i + 1)).isoformat(),
            field: value,
        }
        for i, value in enumerate(values)
    ]


def publish(hass, data, state="100", entity=ENTITY):
    hass.states.async_set(
        entity, state, {"unit_of_measurement": "EUR/MWh", "data": data}
    )


@pytest.mark.parametrize(
    "field,values",
    [
        ("price_eur_per_mwh", (100, 300, -100)),
        ("price_ct_per_kwh", (10, 30, -10)),
        ("price_per_kwh", (0.1, 0.3, -0.1)),
    ],
)
async def test_units_negative_prices_and_automatic_source(hass, field, values):
    now = dt_util.now()
    publish(hass, rows(now, field, values))
    prices, diag = read_prices(hass, {}, now)
    assert [p.eur_per_mwh for p in prices] == [100, 300, -100]
    assert diag["entity_id"] == ENTITY
    assert diag["status"] == "available"
    assert len(diag["preferred_intervals"]) == 1
    assert diag["preferred_intervals"][0]["eur_per_mwh"] == 300


async def test_explicit_source_disable_and_ambiguous_autodiscovery(hass):
    now = dt_util.now()
    publish(hass, rows(now))
    publish(hass, rows(now), entity="sensor.epex_spot_other")
    assert price_entity(hass, {}) is None
    assert not read_prices(hass, {}, now)[0]
    assert read_prices(hass, {CONF_MARKET_ENTITY: ENTITY}, now)[0]
    assert not read_prices(
        hass, {CONF_MARKET_ENABLED: False, CONF_MARKET_ENTITY: ENTITY}, now
    )[0]


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        [],
        [{}],
        [None],
        list(range(401)),
        [{"start_time": "bad", "end_time": "bad"}],
        [
            {
                "start_time": "2026-10-02T10:00:00",
                "end_time": "2026-10-02T11:00:00",
                "price_per_kwh": 0.1,
            }
        ],
    ],
)
async def test_malformed_series_falls_back_without_failing_planning(hass, data):
    now = dt_util.now()
    publish(hass, data)
    prices, diag = read_prices(hass, {}, now)
    assert not prices
    assert diag["status"] in ("invalid", "unavailable")


@pytest.mark.parametrize(
    "variant",
    ["nan", "infinite", "overlap", "reversed", "long", "missing_end", "missing_price"],
)
async def test_reject_ambiguous_or_nonfinite_intervals(hass, variant):
    now = dt_util.now()
    data = rows(now)
    if variant in ("nan", "infinite"):
        data[0]["price_eur_per_mwh"] = float("nan" if variant == "nan" else "inf")
    elif variant == "overlap":
        data[1] = data[0].copy()
    elif variant == "reversed":
        data[0]["end_time"] = (now - timedelta(hours=1)).isoformat()
    elif variant == "long":
        data[0]["end_time"] = (now + timedelta(hours=2)).isoformat()
    elif variant == "missing_end":
        del data[0]["end_time"]
    else:
        del data[0]["price_eur_per_mwh"]
    publish(hass, data)
    prices, diag = read_prices(hass, {}, now)
    assert not prices
    assert diag["status"] == "invalid"


@pytest.mark.parametrize("offset", [-48, 96])
async def test_expired_or_unrelated_future_series_is_not_used(hass, offset):
    now = dt_util.now()
    publish(hass, rows(now + timedelta(hours=offset)))
    prices, diag = read_prices(hass, {}, now)
    assert not prices
    assert diag["status"] == "expired"


@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_unavailable_sensor_attributes_cannot_keep_stale_prices_alive(
    hass, state
):
    now = dt_util.now()
    publish(hass, rows(now), state)
    assert read_prices(hass, {}, now)[0] == ()


async def test_gaps_remain_unknown_and_out_of_order_series_is_sorted(hass):
    now = dt_util.now()
    data = rows(now)
    publish(hass, [data[2], data[0]])
    prices, diag = read_prices(hass, {}, now)
    assert diag["status"] == "available"
    assert prices[0].end < prices[1].start


async def test_coordinator_captures_prices_and_publishes_market_diagnostics(
    rig, hass, freezer
):
    freezer.move_to("2026-10-02T08:00:00Z")
    c, *_ = rig
    configure(c, hass, 80, sun=1)
    publish(hass, rows(dt_util.now()))
    assert ENTITY in c._tracked_entities()
    result = await update(c)
    assert result["reserve"]["market"]["status"] == "available"
    assert result["reserve"]["market"]["avoided_grid_import_wh"] >= 0
    assert c._last_planner_recording[1].market_prices[1].eur_per_mwh == 300
    # Disabling affects both the next immutable input snapshot and listeners.
    c.raw_config[CONF_MARKET_ENABLED] = False
    assert ENTITY not in c._tracked_entities()
    result = await update(c)
    assert not c._last_planner_recording[1].market_prices
    assert not result["reserve"]["market"]["enabled"]
