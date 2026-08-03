"""Tests for the early grid feed-in executor (F-FEEDIN, docs/F-FEEDIN.md).

Covers the two-phase setpoint executor (_apply_feedin/_execute_feedin): the
plan anchor with the negative-export sign convention, the event-driven
battery-power trim (downward immediate/unthrottled, upward 60-s throttled and
rate-capped), the re-anchor deadband, the manual-override mode with midnight
reset (Store-persisted), the runtime on/off switch, the G4/D-A8 fail-safe
zeros, startup adoption and the entity gating on the config toggle.

The setpoint service is faked per service re-registration (pattern:
_register_switch_services in test_load_switching.py); executor passes are
driven directly with a SimpleNamespace plan result (pattern: the
_apply_support_switching tests in test_support_switching.py).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_FEEDIN_DEADLINE_HOUR,
    CONF_FEEDIN_ENABLED,
    CONF_FEEDIN_MAX_W,
    CONF_FEEDIN_MIN_SOC,
    CONF_FEEDIN_SETPOINT_ENTITY,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    ENTITY_FEEDIN_MODE,
    ENTITY_FEEDIN_SWITCH,
    FEEDIN_MANUAL_GRACE_S,
)

SETPOINT = "input_number.acpowersetpoint"
BATT_POWER = "sensor.victron_system_battery_power"

BASE_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}

FEEDIN_DATA = {
    CONF_FEEDIN_ENABLED: True,
    CONF_FEEDIN_SETPOINT_ENTITY: SETPOINT,
    CONF_FEEDIN_BATTERY_POWER_ENTITY: BATT_POWER,
    CONF_FEEDIN_MAX_W: 1000.0,
    CONF_FEEDIN_MIN_SOC: 30.0,
    CONF_FEEDIN_DEADLINE_HOUR: 9,
}

# Morning surplus (trim/anchor scenarios) and late evening (upward cap).
MORNING = dt_util.as_local(datetime(2026, 7, 19, 7, 30, tzinfo=UTC))
LATE = dt_util.as_local(datetime(2026, 7, 19, 23, 0, tzinfo=UTC))


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up (lingering-timer guard, same reason
    as in test_config_flow.py)."""
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _register_setpoint_service(hass, call_log):
    """Fake input_number.set_value: logs (entity_id, value) and applies the
    new state like a real input_number (the executor reads the value back)."""

    async def set_value(call):
        entity_id = call.data["entity_id"]
        value = float(call.data["value"])
        call_log.append((entity_id, value))
        hass.states.async_set(entity_id, str(value))

    hass.services.async_register("input_number", "set_value", set_value)


def _cancel_debounce(coordinator):
    """Drop a pending debounced refresh so every pass in a test is the
    explicit one (a stray refresh would stamp real-time state)."""
    if coordinator._debounce_task:
        coordinator._debounce_task.cancel()
        coordinator._debounce_task = None


def _set_battery_power(hass, coordinator, value):
    hass.states.async_set(BATT_POWER, value)
    _cancel_debounce(coordinator)


def _set_setpoint(hass, coordinator, value):
    hass.states.async_set(SETPOINT, value)
    _cancel_debounce(coordinator)


def _result(plan_w, by_day_wh=None):
    """Minimal PlanResult stand-in for the feed-in executor."""
    return SimpleNamespace(
        feedin_schedule_w=(plan_w,) if plan_w else (),
        feedin_by_day_wh=by_day_wh or {},
    )


async def _executor_pass(hass, coordinator, result, *, soc=55.0, moment=MORNING):
    """One executor pass pinned to `moment` (mirrors the _async_update_data
    call site; the background actuation completes inside the pinned window)."""
    config = coordinator.build_system_config()
    with patch.object(dt_util, "now", return_value=moment):
        await coordinator._apply_feedin(result, config, soc, moment)
        await hass.async_block_till_done()
    _cancel_debounce(coordinator)


async def _refresh_at(hass, coordinator, moment):
    """One full coordinator refresh pinned to `moment` — the feed-in mode
    judgement and the executor run inside _async_update_data."""
    with patch.object(dt_util, "now", return_value=moment):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    _cancel_debounce(coordinator)


async def _setup_base(hass):
    """Config entry WITHOUT the feed-in section (feature off)."""
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(
        domain=DOMAIN, data=BASE_DATA, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id], entry


async def _setup(hass, call_log):
    """Config entry with the feed-in section enabled and the setpoint service
    faked (the setup-time refresh may already have driven the executor — the
    planner books feed-in on the sunny test day; _reset_executor_baseline
    brings the tests back to a deterministic baseline)."""
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SETPOINT, "0")
    hass.states.async_set(BATT_POWER, "0")
    _register_setpoint_service(hass, call_log)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**BASE_DATA, **FEEDIN_DATA},
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # The tests drive every refresh explicitly (pinned in time). The live
    # tracked-entity listener would otherwise schedule a 5-s-debounced full
    # replan on every setpoint/battery-power state change, stamping real-time
    # state and racing the pinned passes; the event WIRING is asserted via
    # _tracked_entities() instead (test_downward_trim_is_immediate_and_unthrottled).
    coordinator._listeners_setup = False
    _cancel_debounce(coordinator)
    return coordinator, entry


def _reset_executor_baseline(hass, coordinator, call_log):
    """Re-baseline after the setup-time refresh: adopted at a 0 setpoint,
    auto mode, no trim/tick history (pattern: the _load_* resets in
    test_load_switching._setup)."""
    coordinator._feedin_adopted = True
    coordinator._feedin_last_written_w = 0.0
    coordinator._feedin_last_write_at = None
    coordinator._feedin_manual_until = None
    coordinator._feedin_last_upward_at = None
    coordinator._feedin_delivered = None
    hass.states.async_set(SETPOINT, "0")
    hass.states.async_set(BATT_POWER, "0")
    _cancel_debounce(coordinator)
    call_log.clear()


async def _setup_feedin(hass, call_log):
    coordinator, entry = await _setup(hass, call_log)
    _reset_executor_baseline(hass, coordinator, call_log)
    return coordinator, entry


# ---------------------------------------------------------------------------
# Plan anchor: slot > 0 writes the negative setpoint, slot 0 re-anchors to 0
# ---------------------------------------------------------------------------


async def test_plan_slot_writes_negative_setpoint_and_reanchors_to_zero(hass):
    """Sign convention: export is written NEGATIVE (a 500 W feed-in -> -500);
    the write is capped at max_w; a plan slot of 0 re-anchors the setpoint
    directly to 0 (the trim never engages on a 0 booking)."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)

    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, -500.0)]
    assert float(hass.states.get(SETPOINT).state) == -500.0
    assert coordinator._feedin_last_written_w == 500.0

    # Plan above the max_w cap: the configured ceiling wins.
    await _executor_pass(hass, coordinator, _result(1500.0))
    assert calls[-1] == (SETPOINT, -1000.0)

    # Plan 0 -> direct re-anchor to 0 (0 <-> >0 transitions always write).
    await _executor_pass(hass, coordinator, _result(0.0))
    assert calls[-1] == (SETPOINT, 0.0)
    assert float(hass.states.get(SETPOINT).state) == 0.0
    assert coordinator._feedin_last_written_w == 0.0


# ---------------------------------------------------------------------------
# Event-driven battery-power trim (requirements 9/10)
# ---------------------------------------------------------------------------


async def test_downward_trim_is_immediate_and_unthrottled(hass):
    """Battery discharging (< -50 W deadband): the setpoint is cut by the
    discharge amount IMMEDIATELY, once per battery-power event, unthrottled —
    a sudden unmeasured consumer must not drain the battery for minutes."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    # The battery-power sensor (and the setpoint, for the override judgement)
    # drives the debounced refresh — the trim never waits for the 5-min poll.
    assert BATT_POWER in coordinator._tracked_entities()
    assert SETPOINT in coordinator._tracked_entities()

    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, -500.0)]

    _set_battery_power(hass, coordinator, "-200")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=5)
    )
    assert calls[-1] == (SETPOINT, -300.0)  # 500 booked - 200 discharging

    _set_battery_power(hass, coordinator, "-150")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=10)
    )
    assert calls[-1] == (SETPOINT, -150.0)  # second trim right behind, no throttle

    _set_battery_power(hass, coordinator, "-400")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=15)
    )
    assert calls[-1] == (SETPOINT, 0.0)  # clamped at 0 — never a battery drain


async def test_upward_trim_throttled_to_one_raise_per_minute(hass):
    """Battery charging (> +50 W): raise the setpoint by the charge amount to
    hold the battery at ~0 W — but max. one raise per 60 s (anti-hunting
    against the external controller)."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    day = {MORNING.date().isoformat(): 20000.0}  # cap never binds here

    await _executor_pass(hass, coordinator, _result(500.0, day))
    assert calls == [(SETPOINT, -500.0)]

    _set_battery_power(hass, coordinator, "200")
    await _executor_pass(hass, coordinator, _result(500.0, day))
    assert calls[-1] == (SETPOINT, -700.0)
    assert coordinator._feedin_last_upward_at == MORNING

    calls.clear()
    # A second raise inside the 60 s window is throttled. The plan is passed
    # at the trimmed value so ONLY the throttle is under test here (the
    # re-anchor pull on a drifted setpoint has its own test below).
    await _executor_pass(
        hass,
        coordinator,
        _result(700.0, day),
        moment=MORNING + timedelta(seconds=30),
    )
    assert calls == []

    # After the interval the next event raises again.
    await _executor_pass(
        hass,
        coordinator,
        _result(700.0, day),
        moment=MORNING + timedelta(seconds=61),
    )
    assert calls == [(SETPOINT, -900.0)]
    assert coordinator._feedin_last_upward_at == MORNING + timedelta(seconds=61)


async def test_upward_trim_capped_by_remaining_target_rate(hass):
    """The raise can never overshoot the booked energy: capped at
    min(max_w, remaining target Wh / hours until midnight)."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    day = {LATE.date().isoformat(): 700.0}  # 700 Wh left, 1 h to midnight

    await _executor_pass(hass, coordinator, _result(500.0, day), moment=LATE)
    assert calls == [(SETPOINT, -500.0)]

    _set_battery_power(hass, coordinator, "400")
    await _executor_pass(hass, coordinator, _result(500.0, day), moment=LATE)
    # cur + battery = 900, but the rate cap is min(1000, 700 Wh / 1 h) = 700.
    assert calls[-1] == (SETPOINT, -700.0)


async def test_battery_power_deadband_writes_nothing(hass):
    """Inside the ±50 W deadband the battery counts as idle: no trim write."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)

    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, -500.0)]
    calls.clear()

    _set_battery_power(hass, coordinator, "30")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=5)
    )
    assert calls == []

    _set_battery_power(hass, coordinator, "-49")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=10)
    )
    assert calls == []


async def test_reanchor_pulls_drifted_setpoint_back_to_plan(hass):
    """The plan is the anchor: a setpoint drifted beyond the 25 W re-anchor
    deadband is pulled back to the plan slot value; drift below it is
    tolerated (the trim increments are estimates)."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    day = {MORNING.date().isoformat(): 20000.0}

    await _executor_pass(hass, coordinator, _result(500.0, day))
    _set_battery_power(hass, coordinator, "200")
    await _executor_pass(hass, coordinator, _result(500.0, day))  # trim -> 700
    assert calls[-1] == (SETPOINT, -700.0)

    # Battery idle again: the trim increment drifted 200 W from the plan —
    # beyond the deadband, so the next pass re-anchors to the slot value.
    _set_battery_power(hass, coordinator, "0")
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=5)
    )
    assert calls[-1] == (SETPOINT, -500.0)

    # Sub-deadband drift (10 W) is tolerated: no write.
    _set_setpoint(hass, coordinator, "-490")
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=10)
    )
    assert calls[-1] == (SETPOINT, -500.0)


# ---------------------------------------------------------------------------
# Manual-override mode (R7) — external takeover, midnight reset, Store
# ---------------------------------------------------------------------------


async def test_external_override_enters_manual_mode_until_midnight(hass):
    """A setpoint changed externally (≠ our last write, beyond the ~1-cycle
    grace) pauses the feature until the next midnight — persisted in the
    Store, mirrored by the mode sensor, hands off including the trim."""
    calls: list[tuple[str, float]] = []
    coordinator, entry = await _setup_feedin(hass, calls)
    registry = er.async_get(hass)
    mode_eid = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_MODE}"
    )
    assert hass.states.get(mode_eid).state == "auto"

    # The operator takes over the setpoint (export 900 W ≠ adopted baseline 0).
    _set_setpoint(hass, coordinator, "-900")

    # Judged on the next cycle — beyond the grace after any own write this
    # reads as an override, not as propagation lag.
    later = MORNING + timedelta(seconds=FEEDIN_MANUAL_GRACE_S + 1)
    await _refresh_at(hass, coordinator, later)
    tomorrow = later.date() + timedelta(days=1)
    # feedin_manual() compares against the CURRENT date — the manual window
    # (frozen July day) lies in the past from the test's wall clock, so the
    # accessor is read inside the pinned window too.
    with patch.object(dt_util, "now", return_value=later):
        assert coordinator.feedin_manual() is True
    assert coordinator._feedin_manual_until == tomorrow
    # Store-persisted (survives a restart).
    assert coordinator._persistent_payload()["feedin_manual_until"] == (
        tomorrow.isoformat()
    )
    # No writes while the operator owns the setpoint — not even the anchor.
    assert calls == []
    assert coordinator.data["feedin_mode"] == "manual"
    assert hass.states.get(mode_eid).state == "manual"

    # Midnight reset: automatic control re-adopts the operator's value as the
    # new baseline and resumes (here: re-anchors the idle-night plan to 0).
    midnight = (MORNING + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0
    )
    await _refresh_at(hass, coordinator, midnight)
    assert coordinator.feedin_manual() is False
    assert coordinator._feedin_manual_until is None
    assert coordinator._persistent_payload()["feedin_manual_until"] is None
    assert hass.states.get(mode_eid).state == "auto"
    assert float(hass.states.get(SETPOINT).state) == 0.0


# ---------------------------------------------------------------------------
# Runtime on/off switch (R6)
# ---------------------------------------------------------------------------


async def test_runtime_switch_off_writes_zero_once(hass):
    """Switch off pauses the executor: one 0 write (auto mode), then hands
    off — the setpoint is never driven while paused."""
    calls: list[tuple[str, float]] = []
    coordinator, entry = await _setup_feedin(hass, calls)
    registry = er.async_get(hass)
    switch_eid = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_SWITCH}"
    )
    assert hass.states.get(switch_eid).state == "on"

    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, -500.0)]
    calls.clear()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_eid}, blocking=True
    )
    await hass.async_block_till_done()
    _cancel_debounce(coordinator)
    assert coordinator.feedin_enabled() is False
    assert hass.states.get(switch_eid).state == "off"
    # Persisted like the F-N2 manual flags, so a restart keeps the pause.
    assert coordinator._persistent_payload()["feedin_switch_on"] is False
    # The switch-off refresh already wrote the one-shot 0.
    assert calls == [(SETPOINT, 0.0)]

    # Further passes stay hands-off (no repeated writes).
    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, 0.0)]


async def test_runtime_switch_off_survives_restart(hass):
    """The paused state is Store-backed: an unload/reload cycle (the unload
    flushes the runtime store) keeps the runtime switch off."""
    calls: list[tuple[str, float]] = []
    coordinator, entry = await _setup_feedin(hass, calls)
    await coordinator.async_set_feedin_enabled(False)
    await hass.async_block_till_done()
    _cancel_debounce(coordinator)
    assert coordinator.feedin_enabled() is False

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    reloaded = hass.data[DOMAIN][entry.entry_id]
    assert reloaded is not coordinator
    assert reloaded.feedin_enabled() is False
    registry = er.async_get(hass)
    switch_eid = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_SWITCH}"
    )
    assert hass.states.get(switch_eid).state == "off"


# ---------------------------------------------------------------------------
# Fail-safes: G4 floor guard / D-A8 stale shed force the setpoint to 0
# ---------------------------------------------------------------------------


async def test_floor_guard_and_stale_shed_force_zero(hass):
    """An unsupervised export must not keep running: both fail-safes force
    the setpoint 0 (dwell-exempt) and block any positive write while latched."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)

    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, -500.0)]
    calls.clear()

    coordinator._floor_guard_active = True
    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == [(SETPOINT, 0.0)]
    assert coordinator._feedin_last_written_w == 0.0
    # No positive write while the guard holds, plan booking notwithstanding.
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=5)
    )
    assert calls == [(SETPOINT, 0.0)]

    coordinator._floor_guard_active = False
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=10)
    )
    assert calls[-1] == (SETPOINT, -500.0)  # recovered

    calls.clear()
    coordinator._stale_shed_active = True
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=15)
    )
    assert calls == [(SETPOINT, 0.0)]


async def test_soc_at_feedin_floor_blocks_feedin(hass):
    """The live SOC gate is exclusive: at/below feedin_min_soc the executor
    stays at 0; one percent above it the plan is followed."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)

    await _executor_pass(hass, coordinator, _result(500.0), soc=30.0)
    assert calls == []  # 30 % is NOT above the 30 % floor

    await _executor_pass(hass, coordinator, _result(500.0), soc=31.0)
    assert calls == [(SETPOINT, -500.0)]


# ---------------------------------------------------------------------------
# Startup adoption: "changed while HA was down" counts as an override
# ---------------------------------------------------------------------------


async def test_startup_adoption_offline_change_enters_manual(hass, hass_storage):
    """Persisted own value 300 W, actual 900 W at startup: the drift happened
    while HA was down and counts as an operator override — manual mode, the
    actual value adopted as the new baseline, hands off."""
    calls: list[tuple[str, float]] = []
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SETPOINT, "-900")  # operator changed it while down
    hass.states.async_set(BATT_POWER, "0")
    _register_setpoint_service(hass, calls)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**BASE_DATA, **FEEDIN_DATA},
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"feedin_last_written_w": 300.0},
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._listeners_setup = False  # explicit refreshes only (see _setup)
    _cancel_debounce(coordinator)

    assert coordinator._feedin_adopted is True
    assert coordinator.feedin_manual() is True
    assert coordinator._feedin_last_written_w == 900.0
    # The executor keeps hands off — the setup refresh wrote nothing either.
    assert calls == []
    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls == []


async def test_startup_adoption_matching_value_stays_auto(hass, hass_storage):
    """Persisted own value == actual value at startup: unchanged since our
    write, so the feature stays in auto mode (no false-positive override)."""
    calls: list[tuple[str, float]] = []
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    for pv in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(pv, "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SETPOINT, "-500")
    hass.states.async_set(BATT_POWER, "0")
    _register_setpoint_service(hass, calls)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**BASE_DATA, **FEEDIN_DATA},
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"feedin_last_written_w": 500.0},
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._listeners_setup = False  # explicit refreshes only (see _setup)
    _cancel_debounce(coordinator)

    assert coordinator._feedin_adopted is True
    assert coordinator.feedin_manual() is False


# ---------------------------------------------------------------------------
# Entity gating: switch + mode sensor only with the config toggle
# ---------------------------------------------------------------------------


async def test_entities_exist_with_config_toggle(hass):
    calls: list[tuple[str, float]] = []
    _, entry = await _setup_feedin(hass, calls)
    registry = er.async_get(hass)
    switch_eid = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_SWITCH}"
    )
    mode_eid = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_MODE}"
    )
    assert switch_eid is not None
    assert mode_eid is not None
    assert hass.states.get(switch_eid).state == "on"  # default: on
    assert hass.states.get(mode_eid).state == "auto"


async def test_entities_absent_without_config_toggle(hass):
    coordinator, entry = await _setup_base(hass)
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "switch", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_SWITCH}"
        )
        is None
    )
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_MODE}"
        )
        is None
    )
    # The feed-in entities are not tracked inputs either (feature off).
    assert SETPOINT not in coordinator._tracked_entities()
    assert BATT_POWER not in coordinator._tracked_entities()
