"""Tests for the bundled forecast card: resource registration + attributes."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import battery_manager
from custom_components.battery_manager import (
    CARD_URL,
    _async_register_card_resource,
)
from custom_components.battery_manager.const import (
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


def test_feedin_hover_uses_exact_slot_block_energy() -> None:
    """Slot-ending backend values must not be shifted at the hover boundary."""
    source = (
        Path(__file__).parents[2]
        / "custom_components/battery_manager/frontend/battery-manager-forecast-card.js"
    ).read_text(encoding="utf-8")

    assert "wh: w * durationH" in source
    assert "const booked = num(block?.wh);" in source
    assert "const w = num(nearest.feedin)" not in source


async def _setup_entry(hass):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _find_forecast_state(hass):
    for state in hass.states.async_all("sensor"):
        if "forecast" in state.attributes and "soc_threshold_percent" in (
            state.attributes
        ):
            return state
    return None


async def test_setup_without_lovelace_does_not_break(hass):
    """Card registration is optional sugar; the planner must come up anyway."""
    entry = await _setup_entry(hass)
    assert hass.data[DOMAIN][entry.entry_id].last_update_success


async def test_global_setup_isolates_optional_card_and_sweep_failures(
    hass, monkeypatch, caplog
):
    """Neither optional frontend registration nor stale-export maintenance may
    prevent Home Assistant from starting the integration."""
    monkeypatch.setattr(
        battery_manager,
        "_async_setup_card",
        AsyncMock(side_effect=RuntimeError("card")),
    )
    monkeypatch.setattr(
        battery_manager,
        "_async_sweep_download_dir",
        AsyncMock(side_effect=OSError("downloads")),
    )

    assert await battery_manager.async_setup(hass, {})
    assert "Could not register the bundled dashboard card" in caplog.text
    assert "Could not sweep stale download exports" in caplog.text


async def test_card_resource_registration_waits_for_ha_started(hass, monkeypatch):
    """Before HA is running, resource mutation is deferred until the official
    startup event so an unloaded Lovelace collection cannot be overwritten."""
    register = AsyncMock()
    monkeypatch.setattr(battery_manager, "_async_register_card_resource", register)
    monkeypatch.setattr(
        battery_manager,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="1.2.3")),
    )
    hass.set_state(CoreState.not_running)
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())

    await battery_manager._async_setup_card(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    register.assert_not_awaited()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    hass.set_state(CoreState.running)
    await hass.async_block_till_done()
    register.assert_awaited_once_with(hass, f"{CARD_URL}?v=1.2.3")


async def test_soc_forecast_sensor_single_point_and_empty_fallback(hass):
    """The forecast entity also has defined states before a full curve exists."""
    from custom_components.battery_manager.sensor import (
        BatteryManagerSocForecastSensor,
    )

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    sensor = BatteryManagerSocForecastSensor(coordinator)

    coordinator.data = {"soc_forecast": [{"soc": 42.0}]}
    assert sensor.native_value == 42.0
    coordinator.data = {"soc_forecast": []}
    assert sensor.native_value is None


async def test_soc_forecast_sensor_keeps_cascade_timeline(hass):
    """Cascade schedules survive as their own lane instead of normal loads."""
    from custom_components.battery_manager.sensor import (
        BatteryManagerSocForecastSensor,
    )

    entry = await _setup_entry(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    sensor = BatteryManagerSocForecastSensor(coordinator)
    block = {
        "start": "2026-08-30T10:00:00",
        "end": "2026-08-30T11:00:00",
        "sources": ["root"],
        "activities": [{"kind": "charge", "name": "B2", "energy_wh": 166.0}],
    }
    coordinator.data = {
        "load_plans": {
            "b2": {
                "name": "B2",
                "managed_by_cascade": "chain",
                "schedule": [block],
            }
        },
        "cascade_plans": {"chain": {"name": "Bad", "schedule": [block]}},
    }

    attrs = sensor.extra_state_attributes

    assert attrs["loads"] == []
    assert attrs["cascades"] == [{"name": "Bad", "schedule": [block]}]


async def test_card_resource_created_updated_never_duplicated(hass):
    """Storage mode: resource is created once and updated on version change."""

    async def _empty_legacy_config(_force):
        return {}

    resources = ResourceStorageCollection(
        hass, SimpleNamespace(async_load=_empty_legacy_config)
    )
    hass.data["lovelace"] = SimpleNamespace(resources=resources)

    await _async_register_card_resource(hass, f"{CARD_URL}?v=0.4.0")
    items = resources.async_items()
    assert [i["url"] for i in items] == [f"{CARD_URL}?v=0.4.0"]

    # Same version again: no duplicate
    await _async_register_card_resource(hass, f"{CARD_URL}?v=0.4.0")
    assert len(resources.async_items()) == 1

    # New version: existing entry is updated in place
    await _async_register_card_resource(hass, f"{CARD_URL}?v=9.9.9")
    items = resources.async_items()
    assert [i["url"] for i in items] == [f"{CARD_URL}?v=9.9.9"]


async def test_card_resource_yaml_mode_uses_global_frontend_url(hass, monkeypatch):
    """YAML mode cannot write a resource registry, so the documented global
    frontend fallback must receive the exact cache-busted module URL."""
    calls = []
    monkeypatch.setattr(
        "custom_components.battery_manager.add_extra_js_url",
        lambda _hass, url: calls.append(url),
    )
    hass.config.components.add("frontend")
    hass.data["lovelace"] = SimpleNamespace(resources=None)
    await _async_register_card_resource(hass, f"{CARD_URL}?v=0.4.0")

    assert calls == [f"{CARD_URL}?v=0.4.0"]


async def test_dc48_mode_sensor_exposes_controller_diagnostic(hass):
    """The 48 V support-mode sensor surfaces the R2 controller diagnostic
    (active/mode/decision/reason/voltage) so the log-only shakedown and live
    regulation are observable in the UI (live-verify finding)."""
    from custom_components.battery_manager.const import CONF_SUPPORT_DC48_SWITCH

    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_SUPPORT_DC48_SWITCH: "switch.psu48"},
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    modes = [
        s for s in hass.states.async_all("sensor") if s.state in ("auto", "manual")
    ]
    dc48 = next((s for s in modes if "controller" in s.attributes), None)
    assert dc48 is not None
    ctrl = dc48.attributes["controller"]
    assert {"active", "mode", "decision", "reason", "voltage"} <= set(ctrl)
    assert ctrl["active"] is False  # not manual + no voltage sensor -> inactive


async def test_soc_forecast_sensor_carries_plan_context(hass):
    """The forecast sensor must expose the full plan for the bundled card."""
    await _setup_entry(hass)

    state = _find_forecast_state(hass)
    assert state is not None
    attrs = state.attributes

    forecast = attrs["forecast"]
    assert len(forecast) > 1
    assert {"t", "soc"} <= set(forecast[0])

    assert attrs["soc_threshold_percent"] is not None
    assert attrs["battery_min_soc_percent"] == 5.0
    assert attrs["battery_max_soc_percent"] == 95.0
    assert attrs["inverter_min_soc_percent"] == 20.0
    assert attrs["soc_buffer_percent"] == 5.0
    assert attrs["grid_import_kwh"] is not None
    assert attrs["lost_surplus_kwh"] is not None
    assert isinstance(attrs["loads"], list)
    # Card lanes for detected appliance runs (operator request 2026-08-08).
    assert isinstance(attrs["appliances"], list)

    # Consumption card attribute (v0.25.5): per-slot planned W, split by
    # voltage level, plus the planned surplus-loads layer.
    cf = attrs["consumption_forecast"]
    assert isinstance(cf, list) and len(cf) > 1
    p0 = cf[0]
    assert {"t", "ac_w", "dc48_w", "dc24_w", "loads_w", "src"} <= set(p0)
    assert p0["src"] in {"L/L", "L/S", "S/L", "S/S"}
    assert any(p["ac_w"] > 0 for p in cf)  # static base load of the fixture
    # Default config: native48 base 0 W + 100 % of the DC load on the 24 V
    # rail -> the 48 V layer is exactly zero, the rail carries the DC total.
    assert all(p["dc48_w"] == 0 for p in cf)
    assert all(p["dc24_w"] >= 0 for p in cf)


async def test_soc_forecast_sensor_exposes_predrain_diagnostics(hass):
    """F-PREDRAIN WP4: the forecast sensor carries the pre-drain observability
    attributes (per-day PV source, traded import, stressed reserve, PV-window
    ends) with plausible values so the card and the operator can inspect them."""
    await _setup_entry(hass)

    state = _find_forecast_state(hass)
    assert state is not None
    attrs = state.attributes

    # Per-day PV source: one label per horizon day, each hourly/two_window. The
    # daily-only fixture entities carry no wh_period, so every day is two_window.
    pv_source = attrs["pv_source"]
    assert isinstance(pv_source, dict) and pv_source
    assert set(pv_source.values()) <= {"hourly", "two_window"}
    assert all(v == "two_window" for v in pv_source.values())

    # Traded import >= 0 and rounded to 0.1 Wh.
    trade = attrs["import_trade_used_wh"]
    assert trade is not None and trade >= 0.0
    assert round(trade, 1) == trade

    # Z4 v2: the stressed reserve reports the bet window of the earliest booked
    # pre-drain slot — None when this fixture's plan books no pass-2 pre-drain,
    # a rounded percentage when it does.
    assert "stressed_min_soc" in attrs
    stressed = attrs["stressed_min_soc"]
    if stressed is not None:
        assert 0.0 <= stressed <= 100.0
        assert round(stressed, 2) == stressed

    # PV-window ends: a dict keyed by ISO date -> local hour (may be empty when
    # no day reaches the strong-PV cutoff, e.g. a low synthetic profile).
    assert isinstance(attrs["pv_window_ends"], dict)
