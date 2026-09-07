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
    _core_config,
    _last_plan_metrics,
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


def test_diagnostics_degrade_to_explicit_errors_before_first_plan():
    """The diagnostics endpoint is a recovery tool: broken config building and
    a not-yet-produced plan must remain downloadable and self-describing."""

    def _broken_config():
        raise ValueError("invalid installation")

    coordinator = SimpleNamespace(build_system_config=_broken_config, data=None)

    assert _core_config(coordinator) == {
        "error": "could not build system config: invalid installation"
    }
    assert _last_plan_metrics(coordinator) == {"valid": False}


async def test_diagnostics_replays_captured_effective_config_not_current_options(hass):
    """A downloaded plan stays reproducible even after options have changed."""
    import json
    from dataclasses import replace
    from datetime import datetime

    from custom_components.battery_manager.core import (
        FeedInParams,
        HourSlot,
        PlanInputs,
        SystemConfig,
        plan,
    )
    from custom_components.battery_manager.core.replay import decode, replay

    now = datetime(2026, 9, 7, 9)
    config = SystemConfig(feedin=FeedInParams(enabled=True, automatic_enabled=False))
    inputs = PlanInputs(now, 90, (HourSlot(0, now, 1, 9, 1000, 100, 0),))
    result = plan(config, inputs)
    changed = replace(config, feedin=replace(config.feedin, automatic_enabled=True))
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=2)
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(
        _last_planner_recording=(config, inputs, result),
        integration_version="0.38.0",
        build_system_config=lambda: changed,
        learned_state_snapshot=lambda: {},
        data=None,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    dump = await async_get_config_entry_diagnostics(hass, entry)
    record = json.loads(json.dumps(dump["planner_recording"], allow_nan=False))
    assert replay(record) == (result, True)
    assert not decode(record["config"]).feedin.automatic_enabled
    assert dump["core_config"]["feedin"]["automatic_enabled"]
    assert "cascades" in dump["core_config"]
    assert "support" in dump["core_config"]
