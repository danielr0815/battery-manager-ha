"""User-visible text stays bilingual, including server-originated messages."""

import json
from pathlib import Path
from string import Formatter

import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import _service_coordinator
from custom_components.battery_manager.const import CONF_WARNING_NOTIFY_TARGETS, DOMAIN
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.debug_utils import (
    format_hourly_details_table,
    format_learned_profiles_table,
)
from custom_components.battery_manager.localization import MESSAGES, message
from custom_components.battery_manager.sensor import CascadeModeSensor

TRANSLATIONS = (
    Path(__file__).parents[2] / "custom_components/battery_manager/translations"
)


def _leaves(data, path=""):
    if isinstance(data, dict):
        return {
            key: value
            for name, child in data.items()
            for key, value in _leaves(child, f"{path}.{name}").items()
        }
    return {path: data}


def _fields(text):
    return {field for _, field, _, _ in Formatter().parse(text) if field is not None}


def test_every_translation_and_placeholder_exists_in_both_languages():
    en = _leaves(json.loads((TRANSLATIONS / "en.json").read_text()))
    de = _leaves(json.loads((TRANSLATIONS / "de.json").read_text()))
    assert en.keys() == de.keys()
    for key in en:
        assert en[key] and de[key], key
        assert _fields(en[key]) == _fields(de[key]), key
    for key, (english, german) in MESSAGES.items():
        assert english and german, key
        assert _fields(english) == _fields(german), key


@pytest.mark.parametrize(
    "language,warning,resolved,tank",
    [
        ("de", "Leistungswarnung", "aufgehoben", "Tank fast voll"),
        ("en", "power warning", "cleared", "tank nearly full"),
    ],
)
async def test_actual_notifications_use_system_language(
    hass, language, warning, resolved, tank
):
    hass.config.language = language
    entry = MockConfigEntry(domain=DOMAIN, data={})
    coordinator = BatteryManagerCoordinator(hass, entry)
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = ["language_test"]
    captured = []

    async def capture(call):
        captured.append(call.data)

    hass.services.async_register("notify", "language_test", capture)
    await coordinator._notify_power_warning(
        "Fossibot", True, raw=2, nominal=400, dwell=30
    )
    await coordinator._notify_power_warning("Fossibot", False)
    await coordinator._notify_tank_full_soon("Fossibot", 12)
    await hass.async_block_till_done()
    assert warning in captured[0]["title"]
    assert resolved in captured[1]["title"]
    assert tank in captured[2]["title"]
    assert "400 W" in captured[0]["message"]
    assert "12 min" in captured[2]["message"]
    assert all("Fossibot" in item["message"] for item in captured)


@pytest.mark.parametrize(
    "language,expected",
    [
        ("de-DE", "Tank fast voll"),
        ("en-GB", "tank nearly full"),
        ("fr", "tank nearly full"),
        ("", "tank nearly full"),
    ],
)
def test_notification_language_variants_and_fallback(hass, language, expected):
    hass.config.language = language
    assert expected in message(hass, "tank_title", name="Test")


@pytest.mark.parametrize(
    "language,expected",
    [("de", "Wiederaufladung ausstehend"), ("en", "Recharge pending")],
)
async def test_ha_loads_translated_cascade_states_and_action_errors(
    hass, language, expected
):
    entity = await async_get_translations(hass, language, "entity", {DOMAIN})
    prefix = f"component.{DOMAIN}.entity.sensor.cascade_mode.state."
    assert entity[prefix + "recovering"] == expected
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "subentry_id": "cascade",
                "subentry_type": "cascade",
                "title": "Test",
                "data": {},
                "unique_id": None,
            }
        ],
    )
    coordinator = BatteryManagerCoordinator(hass, entry)
    sensor = CascadeModeSensor(coordinator, "cascade", "Test")
    for phase in sensor.options:
        assert entity[prefix + phase]
    errors = await async_get_translations(hass, language, "exceptions", {DOMAIN})
    with pytest.raises(ServiceValidationError) as raised:
        _service_coordinator(hass, ServiceCall(DOMAIN, "export_hourly_details", {}))
    error = raised.value
    assert error.translation_domain == DOMAIN
    assert errors[f"component.{DOMAIN}.exceptions.{error.translation_key}.message"] == (
        "Es ist kein Battery-Manager-Eintrag eingerichtet."
        if language == "de"
        else "No Battery Manager entry is set up"
    )


@pytest.mark.parametrize(
    "language,labels",
    [
        (
            "de",
            (
                "Zeit",
                "Laden Wh",
                "Werktag",
                "Gelernte Verbrauchsprofile",
                "Letzte Prüfung",
            ),
        ),
        (
            "en",
            (
                "Time",
                "Charge Wh",
                "Weekday",
                "Learned consumption profiles",
                "Last validation",
            ),
        ),
    ],
)
def test_human_readable_exports_are_bilingual(language, labels):
    hourly = format_hourly_details_table(
        [{"hour": 1, "inverter_enabled": True}], language
    )
    assert labels[0] in hourly and labels[1] in hourly
    table = format_learned_profiles_table(
        {
            "profiles": {"ac": {"weekday": {"p50": [42]}}},
            "diagnostics": {"missing_statistics": ["sensor.test"]},
            "validation": {"ac": [{"day": "2026-09-05", "bias_w": 2, "mae_w": 3}]},
        },
        language,
    )
    assert all(label in table for label in labels[2:])
    assert "42" in table and "sensor.test" in table
    assert (
        "Keine Stundendetails" if language == "de" else "No hourly details"
    ) in format_hourly_details_table([], language)
