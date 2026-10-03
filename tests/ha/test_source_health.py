"""O04/O05: explicit units, publication quality and read-only role diagnostics."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import State

from custom_components.battery_manager.source_health import (
    normalized_measurement,
    publication_age_s,
    source_health,
)

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "value,unit,signed,expected",
    [
        ("500", "W", False, 500),
        (".5", "kW", False, 500),
        ("500000", "mW", False, 500),
        (".0005", "MW", False, 500),
        (".0000005", "GW", False, 500),
        ("-500", "W", True, -500),
        ("-500", "W", False, None),
        ("inf", "W", False, None),
        ("nan", "W", False, None),
        ("bad", "W", False, None),
        ("100", "A", False, None),
        ("1e308", "GW", False, None),
    ],
)
def test_power_normalization(value, unit, signed, expected):
    assert (
        normalized_measurement(
            State("sensor.meter", value, {"unit_of_measurement": unit}),
            "power",
            signed=signed,
        )
        == expected
    )
    assert normalized_measurement(None, "power") is None


def test_publication_timestamp_is_required_only_when_age_is_requested():
    assert publication_age_s(None, NOW) is None
    assert publication_age_s(SimpleNamespace(last_reported="bad"), NOW) is None
    assert (
        publication_age_s(SimpleNamespace(last_reported=datetime(2026, 10, 2)), NOW)
        is None
    )
    assert (
        publication_age_s(
            SimpleNamespace(last_reported=NOW - timedelta(seconds=600)), NOW
        )
        == 600
    )


async def test_role_statuses_and_counters_without_generic_expiry(hass, freezer):
    freezer.move_to(NOW - timedelta(hours=2))
    hass.states.async_set("sensor.power", ".5", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.total", "1.2", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.program", "eco")
    hass.states.async_set("sensor.bad", "watts", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.unknown", "unknown")
    hass.states.async_set("sensor.unavailable", "unavailable")
    freezer.move_to(NOW)
    bindings = {
        "power": "sensor.power",
        "energy": "sensor.total",
        "program": "sensor.program",
        "bad": "sensor.bad",
        "unknown": "sensor.unknown",
        "unavailable": "sensor.unavailable",
        "missing": "sensor.missing",
        "empty": None,
    }
    rows = source_health(
        hass,
        bindings,
        NOW,
        kinds={"power": "power", "energy": "energy", "bad": "power"},
        fallbacks={"power": "nominal"},
        boundaries={"power": "appliance_input"},
    )
    by_role = {row["role"]: row for row in rows}
    assert by_role["power"]["value"] == 500
    assert by_role["power"]["unit"] == "W"
    assert by_role["power"]["native_unit"] == "kW"
    assert by_role["energy"]["unit"] == "Wh"
    assert by_role["energy"]["native_unit"] == "kWh"
    assert {role: row["status"] for role, row in by_role.items()} == {
        "power": "stale",
        "energy": "available",
        "program": "available",
        "bad": "invalid",
        "unknown": "unknown",
        "unavailable": "unavailable",
        "missing": "not_found",
        "empty": "not_configured",
    }
    assert by_role["power"]["value"] == 500
    assert by_role["energy"]["value"] == 1200
    assert by_role["power"]["publication_age_s"] == 7200
    assert by_role["power"]["fallback"] == "nominal"
    assert by_role["power"]["boundary"] == "appliance_input"
    assert (
        source_health(
            hass,
            {"power": "sensor.power"},
            NOW,
            kinds={"power": "power"},
            max_ages_s={"power": None},
        )[0]["status"]
        == "available"
    )
    assert (
        source_health(
            hass,
            {"power": "sensor.power"},
            NOW - timedelta(hours=3),
            kinds={"power": "power"},
        )[0]["status"]
        == "stale"
    )
    before = hass.states.get("sensor.power").last_reported
    assert (
        source_health(hass, {"power": "sensor.power"}, NOW)[0]["reported_at"]
        == before.isoformat()
    )
    assert hass.states.get("sensor.power").last_reported == before
