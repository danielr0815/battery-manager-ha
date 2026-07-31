"""Diagnostics export tests.

Pins that the subentries section of the diagnostics dump carries the
(redacted) data AND options the module docstring promises — HA core's
ConfigSubentry has no options mapping yet, so the key is an empty dict
today but must stay present (stable dump schema, future-proof).
"""

from types import SimpleNamespace

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_LOAD_POWER_W,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_LOAD,
)
from custom_components.battery_manager.diagnostics import (
    _subentries,
    async_get_config_entry_diagnostics,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


async def test_diagnostics_subentries_carry_data_and_options(hass):
    """Every subentry in the dump has data AND options; no setup on purpose —
    the subentries section is built even without a running coordinator (the
    failed-setup forensic path)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data={CONF_LOAD_POWER_W: 800.0},
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="Boiler",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)

    dump = await async_get_config_entry_diagnostics(hass, entry)

    assert dump["coordinator"] is None
    assert len(dump["subentries"]) == 1
    sub = dump["subentries"][0]
    assert sub["subentry_type"] == SUBENTRY_TYPE_LOAD
    assert sub["title"] == "Boiler"
    assert sub["data"] == {CONF_LOAD_POWER_W: 800.0}
    assert sub["options"] == {}  # core has no subentry options yet


def test_subentry_options_are_redacted():
    """Once core grows subentry options the dump must redact them with the
    same TO_REDACT set as the data mapping."""
    fake_entry = SimpleNamespace(
        subentries={
            "sub1": SimpleNamespace(
                subentry_type="load",
                title="Boiler",
                unique_id=None,
                data={"api_key": "data-secret"},
                options={"api_key": "options-secret", "threshold": 42},
            )
        }
    )

    (sub,) = _subentries(fake_entry)

    assert sub["data"]["api_key"] == "**REDACTED**"
    assert sub["options"]["api_key"] == "**REDACTED**"
    assert sub["options"]["threshold"] == 42
