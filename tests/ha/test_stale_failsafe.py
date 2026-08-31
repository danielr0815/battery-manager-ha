"""Tests for the fail-safe escalations on data loss / actuator failure.

- D-A8 stage 2: after STALE_LOAD_SHED_HOURS of continuous data loss (no valid
  SOC / PV forecasts) all controlled surplus loads are force-switched OFF once
  (repair issue + push), the latch blocks any re-ON, and recovery resumes
  normal operation. The data-loss clock and the latch survive restarts.
- House-SOC stale watchdog: within 21-89 % a frozen house-battery SOC is
  latched stale once the plan's expected battery throughput exceeds the
  band's drift allowance; values outside that window are exempt. The latch
  routes into UpdateFailed and thus into stage 2.
- F7/U5: three consecutive support-switch failures raise a repair issue +
  push; the first success resets the streak and resolves the issue.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_POWER_W,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    CONF_WARNING_NOTIFY_TARGETS,
    DOMAIN,
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_KEEP,
    STALE_LOAD_SHED_HOURS,
    SUBENTRY_TYPE_LOAD,
)

PLUG = "switch.load_one_input"
ENABLE = "input_boolean.load_one_enable"
PLUG2 = "switch.load_two_input"
ENABLE2 = "input_boolean.load_two_enable"

DC48 = "switch.psu_48v"
PSU = "switch.psu_24v"
DCDC = "switch.dcdc_converter"

BASE_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
    CONF_WARNING_NOTIFY_TARGETS: ["mobile_app_test"],
}

# Midday in the strong-PV morning window (the plan then expects a large
# battery charge flow); deep night for the standby case.
MIDDAY = dt_util.as_local(datetime(2026, 7, 19, 11, 0, tzinfo=UTC))
NIGHT = dt_util.as_local(datetime(2026, 7, 19, 1, 30, tzinfo=UTC))


def _at(moment):
    return patch.object(dt_util, "now", return_value=moment)


def _register_switch_services(hass, call_log, *, dead_entities=()):
    async def turn_on(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_on", entity_id))
        if entity_id not in dead_entities:
            hass.states.async_set(entity_id, "on")

    async def turn_off(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_off", entity_id))
        if entity_id not in dead_entities:
            hass.states.async_set(entity_id, "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)


def _register_failing_switch_services(hass):
    async def _boom(call):
        raise HomeAssistantError("switch unreachable")

    hass.services.async_register("homeassistant", "turn_on", _boom)
    hass.services.async_register("homeassistant", "turn_off", _boom)


def _register_notify(hass, notifications):
    async def _notify(call):
        notifications.append(
            {"title": call.data.get("title"), "message": call.data.get("message")}
        )

    hass.services.async_register("notify", "mobile_app_test", _notify)


def _cancel_debounce(coordinator):
    """Drop a pending debounced refresh so every refresh in a test is the
    explicit, time-pinned one (a stray refresh would stamp real-time state)."""
    if coordinator._debounce_task:
        coordinator._debounce_task.cancel()
        coordinator._debounce_task = None


def _set_soc(hass, coordinator, value):
    hass.states.async_set(
        "sensor.test_soc",
        value,
        {"unit_of_measurement": "%", "device_class": "battery"},
    )
    _cancel_debounce(coordinator)


async def _refresh_at(hass, coordinator, moment):
    """One explicit coordinator refresh pinned to `moment` (background tasks
    — shed executor included — complete inside the same pinned window)."""
    with _at(moment):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    _cancel_debounce(coordinator)


async def _setup(
    hass,
    call_log,
    notifications,
    *,
    loads=(("switch.load_one_input", "input_boolean.load_one_enable", "Load One"),),
    policy=INPUT_OFF_POLICY_AUTO,
    extra_data=None,
    pinned=MIDDAY,
):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    _register_switch_services(hass, call_log)
    _register_notify(hass, notifications)

    subentries = [
        ConfigSubentryData(
            data={
                CONF_LOAD_POWER_W: 300.0,
                CONF_LOAD_ENERGY_LIMITED: False,
                CONF_LOAD_CONTROL_SWITCH: plug,
                CONF_LOAD_CHARGE_ENABLE: enable,
                CONF_LOAD_INPUT_OFF_POLICY: policy,
            },
            subentry_type=SUBENTRY_TYPE_LOAD,
            title=title,
            unique_id=None,
        )
        for plug, enable, title in loads
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**BASE_DATA, **(extra_data or {})},
        title="Battery Manager",
        version=2,
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    with _at(pinned):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _cancel_debounce(coordinator)
    # Reset any setup-time switching so each test drives from a clean state.
    coordinator._load_plug_owned.clear()
    coordinator._load_charging_active.clear()
    coordinator._last_load_switch.clear()
    coordinator._load_plan_active.clear()
    call_log.clear()
    return entry, coordinator


def _stage_outage(hass, coordinator, t0):
    """Make the SOC source unavailable; the 6 h SOC cache then expires at
    t0 + 6 h 1 min, so the first UpdateFailed happens at that refresh."""
    hass.states.async_set(
        "sensor.test_soc",
        "unavailable",
        {"unit_of_measurement": "%", "device_class": "battery"},
    )
    _cancel_debounce(coordinator)
    return t0 + timedelta(hours=6, minutes=1)


def _issue_id(coordinator, key):
    return (DOMAIN, f"{key}_{coordinator.entry.entry_id}")


# ---------------------------------------------------------------------------
# D-A8 stage 2: fail-safe load shed after a sustained data loss
# ---------------------------------------------------------------------------


async def test_no_shed_before_two_hours(hass):
    """The fail-safe must not fire while the continuous data loss is younger
    than STALE_LOAD_SHED_HOURS — the plain UpdateFailed unavailability owns
    that phase (stage 1)."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    registry = ir.async_get(hass)

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    await _refresh_at(hass, coordinator, t1)

    assert not coordinator.last_update_success
    # The data-loss clock starts at the FIRST failed update.
    assert coordinator._data_stale_since == t1
    assert coordinator._stale_shed_active is False

    await _refresh_at(
        hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS, minutes=-1)
    )

    assert coordinator._stale_shed_active is False
    assert calls == []  # no fail-safe actuation yet
    assert notifications == []
    assert _issue_id(coordinator, "stale_data_load_shed") not in registry.issues


async def test_shed_after_two_hours_switches_loads_off_and_raises_issue(hass):
    """At STALE_LOAD_SHED_HOURS of continuous data loss every controlled load
    is force-switched OFF once: charge-enable in any case, the plug per
    policy + ownership — plus repair issue and push. The latch blocks any
    re-fire while the outage lasts."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    loads = ((PLUG, ENABLE, "Load One"), (PLUG2, ENABLE2, "Load Two"))
    entry, coordinator = await _setup(hass, calls, notifications, loads=loads)
    registry = ir.async_get(hass)
    sub_ids = list(entry.subentries)

    # Both loads physically charging, owned by the integration (auto policy).
    for plug, enable in ((PLUG, ENABLE), (PLUG2, ENABLE2)):
        hass.states.async_set(plug, "on")
        hass.states.async_set(enable, "on")
    for sub_id in sub_ids:
        coordinator._load_plug_owned[sub_id] = True
        coordinator._load_charging_active[sub_id] = True
    calls.clear()

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    await _refresh_at(hass, coordinator, t1)
    await _refresh_at(hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS))

    assert coordinator._stale_shed_active is True
    for plug, enable in ((PLUG, ENABLE), (PLUG2, ENABLE2)):
        assert hass.states.get(enable).state == "off"
        assert hass.states.get(plug).state == "off"
    for sub_id in sub_ids:
        assert coordinator._load_charging_active[sub_id] is False
        assert coordinator._load_plug_owned[sub_id] is False
    assert _issue_id(coordinator, "stale_data_load_shed") in registry.issues
    assert any("data loss" in (n["title"] or "") for n in notifications)

    # The latch: further failed updates must not re-fire the shed.
    calls.clear()
    notifications.clear()
    hass.states.async_set(PLUG, "on")  # would be re-offed by a re-fired shed
    await _refresh_at(
        hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS, minutes=10)
    )
    assert calls == []
    assert notifications == []
    assert hass.states.get(PLUG).state == "on"


async def test_shed_delegates_cascade_owned_loads_to_their_actor_owner(
    hass, monkeypatch
):
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications)
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    calls.clear()
    delegated: list[tuple[str, datetime]] = []

    async def safety_off(reason: str, now: datetime) -> None:
        delegated.append((reason, now))

    monkeypatch.setattr(
        coordinator.cascade_manager, "async_safety_off_active", safety_off
    )
    monkeypatch.setattr(
        coordinator.cascade_manager, "managed_load_ids", lambda: {sub_id}
    )

    await coordinator._execute_stale_load_shed()

    assert delegated and delegated[0][0] == "stale-data load shed"
    assert calls == []
    assert hass.states.get(PLUG).state == "on"
    assert hass.states.get(ENABLE).state == "on"


async def test_shed_respects_keep_on_policy_for_the_plug(hass):
    """Fail-safe semantics: the charge-enable gate is switched off in ANY
    case (stopping the energy flow takes precedence), but the PLUG still
    follows the input_off_policy — keep_on keeps the device's mains."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(
        hass, calls, notifications, policy=INPUT_OFF_POLICY_KEEP
    )
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_charging_active[sub_id] = True
    calls.clear()

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    await _refresh_at(hass, coordinator, t1)  # first UpdateFailed stamps the clock
    await _refresh_at(hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS))

    assert coordinator._stale_shed_active is True
    assert hass.states.get(ENABLE).state == "off"
    assert hass.states.get(PLUG).state == "on"  # keep_on: the plug stays


async def test_shed_skipped_without_controlled_loads(hass):
    """No controlled loads -> the shed (and its issue/push) is pure noise;
    the plain UpdateFailed unavailability already covers the outage."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications, loads=())
    registry = ir.async_get(hass)

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    await _refresh_at(hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS))

    assert not coordinator.last_update_success
    assert coordinator._stale_shed_active is False
    assert _issue_id(coordinator, "stale_data_load_shed") not in registry.issues
    assert notifications == []


async def test_recovery_resolves_issue_latch_and_pushes(hass):
    """The first valid update after the shed ends the episode: latch and
    data-loss clock released, repair issue resolved, resolve push sent —
    the normal plan (not the recovery itself) re-enables loads."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications)
    registry = ir.async_get(hass)
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[sub_id] = True
    coordinator._load_charging_active[sub_id] = True

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    await _refresh_at(hass, coordinator, t1)  # first UpdateFailed stamps the clock
    await _refresh_at(hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS))
    assert coordinator._stale_shed_active is True

    _set_soc(hass, coordinator, "60")
    await _refresh_at(
        hass, coordinator, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS, minutes=5)
    )

    assert coordinator.last_update_success
    assert coordinator.data["valid"] is True
    assert coordinator._stale_shed_active is False
    assert coordinator._data_stale_since is None
    assert _issue_id(coordinator, "stale_data_load_shed") not in registry.issues
    assert any("data recovered" in (n["title"] or "") for n in notifications)


async def test_shed_latch_blocks_queued_switch_on(hass):
    """The latch works like the floor guard on the executor: a switch-ON
    queued before the shed must never fire into it (in-flight race)."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications)
    sub_id = next(iter(entry.subentries))
    data = dict(entry.subentries[sub_id].data)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    calls.clear()

    coordinator._stale_shed_active = True
    await coordinator._execute_load_switching(
        [(sub_id, data, True, False, 1.0, False, "plan slot")]
    )

    assert hass.states.get(PLUG).state == "off"
    assert calls == []


async def test_stale_clock_survives_reload_and_still_escalates(hass, hass_storage):
    """A restart must not reset the 2 h budget: the persisted data-loss
    clock restored by the new coordinator keeps running, so the shed fires
    on the stored timestamp, not on a post-restart one."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications)
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[sub_id] = True
    coordinator._load_charging_active[sub_id] = True

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    await _refresh_at(hass, coordinator, t1)
    assert coordinator._data_stale_since == t1
    await coordinator.async_flush_persistent_state()
    store_key = f"{DOMAIN}.{entry.entry_id}"
    assert hass_storage[store_key]["data"]["stale_since"] == t1.isoformat()

    # Restart (reload) half an hour into the outage.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with _at(t1 + timedelta(minutes=30)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator2 = hass.data[DOMAIN][entry.entry_id]
    _cancel_debounce(coordinator2)
    assert coordinator2 is not coordinator
    assert coordinator2._data_stale_since == t1
    assert coordinator2._stale_shed_active is False
    calls.clear()

    # 2 h after the ORIGINAL stamp the new coordinator sheds.
    await _refresh_at(hass, coordinator2, t1 + timedelta(hours=STALE_LOAD_SHED_HOURS))
    assert coordinator2._stale_shed_active is True
    assert hass.states.get(PLUG).state == "off"
    assert hass.states.get(ENABLE).state == "off"


async def test_shed_latch_survives_reload_and_does_not_refire(hass, hass_storage):
    """The shed is once per outage EPISODE, not once per session: a reload
    while latched must not re-fire it (no duplicate actuation/push)."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications)
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[sub_id] = True
    coordinator._load_charging_active[sub_id] = True

    t1 = _stage_outage(hass, coordinator, MIDDAY)
    t_shed = t1 + timedelta(hours=STALE_LOAD_SHED_HOURS)
    await _refresh_at(hass, coordinator, t1)  # first UpdateFailed stamps the clock
    await _refresh_at(hass, coordinator, t_shed)
    assert coordinator._stale_shed_active is True
    await coordinator.async_flush_persistent_state()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with _at(t_shed + timedelta(minutes=10)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator2 = hass.data[DOMAIN][entry.entry_id]
    _cancel_debounce(coordinator2)
    assert coordinator2._stale_shed_active is True  # latch restored
    assert coordinator2._data_stale_since == t1

    # A load turned on during the downtime is NOT re-shed (latched already).
    calls.clear()
    notifications.clear()
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    await _refresh_at(hass, coordinator2, t_shed + timedelta(minutes=20))
    assert calls == []
    assert notifications == []
    assert hass.states.get(PLUG).state == "on"


# ---------------------------------------------------------------------------
# House-SOC stale watchdog (adaptive sibling of the G2 load guard)
# ---------------------------------------------------------------------------


async def test_watchdog_no_false_alarm_at_standby(hass):
    """At night the plan expects well below HOUSE_SOC_STALE_POWER_W of
    battery flow: a constant SOC is physically correct there and must never
    latch, however long it stays put."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications, pinned=NIGHT)

    expected = coordinator._expected_battery_power_w
    assert expected is not None and abs(expected) < 300.0, (
        f"standby scenario must expect < 300 W battery flow, got {expected}"
    )
    for minutes in (30, 60, 120):
        await _refresh_at(hass, coordinator, NIGHT + timedelta(minutes=minutes))
        assert coordinator._house_soc_stale is None
        assert coordinator.last_update_success


async def test_watchdog_latches_frozen_soc_and_unlatches_on_change(hass):
    """Under expected load the SOC MUST move: once the accumulated expected
    throughput exceeds the mid-band drift allowance (capacity x 3 %) without
    a SOC change, the reading latches stale and routes into UpdateFailed; the
    first changed reading releases."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)

    expected = coordinator._expected_battery_power_w
    assert expected is not None and abs(expected) >= 300.0, (
        f"midday scenario must expect >= 300 W battery flow, got {expected}"
    )
    # Deterministic allowance: 5000 Wh x 3 % = 150 Wh; at 1000 W expected
    # flow that accumulates in 9 min. The plan-derived expectation is
    # re-seeded after each successful refresh (each plan recomputes it) so
    # the accumulation below is exactly what is asserted. The setup refresh
    # already seeded the evidence — restart it so t1 is the epoch.
    coordinator._house_soc_frozen = None
    coordinator._expected_battery_power_w = 1000.0

    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)  # seeds the frozen evidence
    assert coordinator._house_soc_stale is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=5))
    assert coordinator._house_soc_stale is None  # 83 Wh < 150 Wh allowance

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=10))
    assert coordinator._house_soc_stale == 55.0  # 167 Wh > 150 Wh: latched
    assert not coordinator.last_update_success  # routed into UpdateFailed

    _set_soc(hass, coordinator, "56")
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=11))
    assert coordinator._house_soc_stale is None  # unlatched on the change
    assert coordinator.last_update_success
    assert coordinator._data_stale_since is None  # short episode, no shed


async def test_watchdog_mid_band_covers_display_quantization(hass):
    """The mid-band default is 3 % of capacity (2 % physical drift + 1 %
    Victron display quantization, const.py CONF_HOUSE_SOC_STALE_MID_PERCENT):
    the OLD 2 % allowance (100 Wh at 5 kWh) must NOT latch anymore — a frozen
    reading only proves itself stale past the 150 Wh line."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)

    coordinator._house_soc_frozen = None
    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)  # seeds the frozen evidence
    assert coordinator._house_soc_stale is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=6))
    # 100 Wh == the pre-fix 2 % allowance: covered by the quantization headroom.
    assert coordinator._house_soc_stale is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=10))
    assert coordinator._house_soc_stale == 55.0  # 167 Wh > 150 Wh: latched


async def test_watchdog_above_89_is_not_checked(hass):
    """A frozen SOC above 89 % is plausible while charging/balancing."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    _set_soc(hass, coordinator, "89.1")

    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)
    assert coordinator._house_soc_stale is None
    assert coordinator._house_soc_frozen is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(hours=12))
    assert coordinator._house_soc_stale is None
    assert coordinator._house_soc_frozen is None
    assert coordinator.last_update_success


async def test_watchdog_exact_upper_boundary_89_is_still_checked(hass):
    """The explicit exemption is > 89 %, so exactly 89 % remains checked."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    _set_soc(hass, coordinator, "89")

    coordinator._house_soc_frozen = None
    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)
    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=22))

    assert coordinator._house_soc_stale == 89.0


async def test_watchdog_band_boundary_87_is_mid(hass):
    """The high edge bound is 88 (the Victron calibration plateau includes
    89): a frozen 87.0 % reading still falls in the mid band and latches at
    the 3 % allowance."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    _set_soc(hass, coordinator, "87")

    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)  # seeds the frozen 87 % evidence
    assert coordinator._house_soc_stale is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=10))
    assert coordinator._house_soc_stale == 87.0  # 167 Wh > 150 Wh mid allowance


async def test_watchdog_band_boundary_88_is_edge(hass):
    """88.0 % itself is already the edge band (7 %): the Victron calibration
    plateau at the top end belongs there, so a frozen reading holds past
    the mid-band allowance."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    _set_soc(hass, coordinator, "88")

    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)  # seeds the frozen 88 % evidence
    assert coordinator._house_soc_stale is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=20))
    assert coordinator._house_soc_stale is None  # 333 Wh: past mid, under edge

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=22))
    assert coordinator._house_soc_stale == 88.0  # 367 Wh > 350 Wh edge allowance


async def test_watchdog_edge_high_bound_is_configurable(hass):
    """The edge bounds are options: raising the high bound above the reading
    moves it back into the mid band (3 %)."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(
        hass,
        calls,
        notifications,
        extra_data={"house_soc_stale_edge_high_soc": 95.0},
    )
    _set_soc(hass, coordinator, "89")

    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)  # seeds the frozen 89 % evidence
    assert coordinator._house_soc_stale is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=10))
    assert coordinator._house_soc_stale == 89.0  # mid band via the option


async def test_watchdog_below_21_is_not_checked(hass):
    """A frozen SOC below 21 % is exempt regardless of expected throughput."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    _set_soc(hass, coordinator, "20.9")

    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)
    assert coordinator._house_soc_stale is None
    assert coordinator._house_soc_frozen is None

    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(hours=12))
    assert coordinator._house_soc_stale is None
    assert coordinator._house_soc_frozen is None
    assert coordinator.last_update_success


async def test_watchdog_exact_lower_boundary_21_is_still_checked(hass):
    """The explicit exemption is < 21 %, so exactly 21 % remains checked."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications)
    _set_soc(hass, coordinator, "21")

    coordinator._house_soc_frozen = None
    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)
    coordinator._expected_battery_power_w = 1000.0
    await _refresh_at(hass, coordinator, t1 + timedelta(minutes=10))

    assert coordinator._house_soc_stale == 21.0


async def test_watchdog_exempt_soc_clears_an_existing_latch(hass):
    """Deploying the relaxed policy recovers a latched edge SOC immediately."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    _entry, coordinator = await _setup(hass, calls, notifications, pinned=NIGHT)
    _set_soc(hass, coordinator, "99")
    coordinator._house_soc_stale = 99.0
    coordinator._house_soc_frozen = (99.0, NIGHT, 1000.0)

    await _refresh_at(hass, coordinator, NIGHT + timedelta(minutes=1))

    assert coordinator._house_soc_stale is None
    assert coordinator._house_soc_frozen is None
    assert coordinator.last_update_success


async def test_frozen_soc_escalates_into_stage2_shed(hass):
    """The watchdog latch makes _get_soc fail, so the stage-2 escalation
    inherits it: 2 h of frozen SOC -> shed; a SOC change -> full recovery."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications)
    registry = ir.async_get(hass)
    sub_id = next(iter(entry.subentries))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[sub_id] = True
    coordinator._load_charging_active[sub_id] = True

    # Restart the setup-seeded evidence so t1 is the accumulation epoch.
    coordinator._house_soc_frozen = None
    coordinator._expected_battery_power_w = 1000.0
    t1 = MIDDAY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t1)
    coordinator._expected_battery_power_w = 1000.0
    # 10 min x 1000 W = 167 Wh: past the 150 Wh mid-band allowance.
    t_latch = t1 + timedelta(minutes=10)
    await _refresh_at(hass, coordinator, t_latch)
    assert coordinator._house_soc_stale == 55.0
    assert coordinator._data_stale_since == t_latch  # clock starts at latch
    calls.clear()

    # The SOC stays frozen: the latch holds, every update fails, and the
    # stage-2 shed fires once the outage is 2 h old.
    await _refresh_at(hass, coordinator, t_latch + timedelta(hours=2))
    assert coordinator._stale_shed_active is True
    assert hass.states.get(PLUG).state == "off"
    assert hass.states.get(ENABLE).state == "off"
    assert _issue_id(coordinator, "stale_data_load_shed") in registry.issues

    # The BMS reporting a CHANGED value unlatches and recovers everything.
    _set_soc(hass, coordinator, "57")
    await _refresh_at(hass, coordinator, t_latch + timedelta(hours=2, minutes=5))
    assert coordinator._house_soc_stale is None
    assert coordinator._stale_shed_active is False
    assert coordinator.last_update_success
    assert _issue_id(coordinator, "stale_data_load_shed") not in registry.issues


# ---------------------------------------------------------------------------
# F7/U5: support-actuator failure escalation
# ---------------------------------------------------------------------------


async def _setup_support(hass, notifications, *, dead_entities=(), register=True):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    call_log: list[tuple[str, str]] = []
    if register:
        _register_switch_services(hass, call_log, dead_entities=dead_entities)
    _register_notify(hass, notifications)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **BASE_DATA,
            CONF_SUPPORT_DC48_SWITCH: DC48,
            CONF_SUPPORT_DC24_SWITCH: PSU,
            CONF_DCDC_SWITCH: DCDC,
            CONF_SUPPORT_SWITCH_DELAY_S: 1,
        },
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    with _at(MIDDAY):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _cancel_debounce(coordinator)
    return entry, coordinator, call_log


async def test_support_issue_after_three_consecutive_failures(hass):
    """One or two switch failures in a row are transient noise (the 5-min
    cycle retries anyway); the THIRD consecutive failure raises the repair
    issue + push, and the first success resets the streak and resolves it."""
    notifications: list[dict] = []
    entry, coordinator, _calls = await _setup_support(hass, notifications)
    registry = ir.async_get(hass)
    issue = _issue_id(coordinator, "support_switch_failed")

    async def attempt_dc48(turn_on: bool) -> None:
        await coordinator._execute_support_switching({"dc24": False, "dc48": turn_on})

    _register_failing_switch_services(hass)
    await attempt_dc48(True)
    await attempt_dc48(True)
    assert coordinator._support_fail_count == 2
    assert issue not in registry.issues
    assert notifications == []

    await attempt_dc48(True)
    assert coordinator._support_fail_count == 3
    assert issue in registry.issues
    assert any("support switching" in (n["title"] or "") for n in notifications)

    # A success in between resets the streak (consecutive, not cumulative).
    _register_switch_services(hass, [])
    hass.states.async_set(DC48, "off")
    await attempt_dc48(True)
    assert coordinator._support_state["dc48"] is True
    assert coordinator._support_fail_count == 0
    assert issue not in registry.issues

    # Two more failures stay below the threshold again.
    _register_failing_switch_services(hass)
    await attempt_dc48(False)
    await attempt_dc48(False)
    assert coordinator._support_fail_count == 2
    assert issue not in registry.issues


async def test_support_unconfirmed_make_before_break_counts(hass):
    """An unconfirmed make-before-break sequence is a failure too: the new
    24 V supply never reports 'on', so the aborted switchover increments
    the streak (and the existing abort semantics are untouched)."""
    notifications: list[dict] = []
    # The PSU service call succeeds but the device never confirms 'on'.
    entry, coordinator, _calls = await _setup_support(
        hass, notifications, dead_entities=(PSU,)
    )
    registry = ir.async_get(hass)
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")

    with patch("asyncio.sleep", return_value=None):
        await coordinator._execute_support_switching({"dc24": True, "dc48": False})

    assert coordinator._support_state["dc24"] is False  # switchover aborted
    assert coordinator._support_pending_confirm["dc24"] is True
    assert coordinator._support_fail_count == 1
    assert _issue_id(coordinator, "support_switch_failed") not in registry.issues
