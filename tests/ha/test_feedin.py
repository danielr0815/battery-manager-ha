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


async def _executor_pass(
    hass, coordinator, result, *, soc=55.0, moment=MORNING, seed_plan_trace=True
):
    """One executor pass pinned to `moment` (mirrors the _async_update_data
    call site; the background actuation completes inside the pinned window).

    `seed_plan_trace`: the R16 stability brake reads the plan value's spread
    over a sliding window; tests for the trim/re-anchor/fail-safe mechanics
    predate it and step the plan value deliberately — seeding the trace with
    the pass's own value keeps them isolated from the brake (spread 0). The
    brake's own tests pass False and drive the trace themselves."""
    config = coordinator.build_system_config()
    if seed_plan_trace:
        plan_w = result.feedin_schedule_w[0] if result.feedin_schedule_w else 0.0
        coordinator._feedin_plan_trace.clear()
        coordinator._feedin_plan_trace.append((moment, plan_w))
    with patch.object(dt_util, "now", return_value=moment):
        await coordinator._apply_feedin(result, config, soc, moment)
        await hass.async_block_till_done()
    _cancel_debounce(coordinator)


async def _refresh_at(hass, coordinator, moment):
    """One full coordinator refresh pinned to `moment` — the feed-in mode
    judgement and the executor run inside _async_update_data. The R16 plan
    trace is cleared first: these tests exercise other mechanics, so the
    planner's own value must not trip the stability brake mid-test."""
    coordinator._feedin_plan_trace.clear()
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
    coordinator._feedin_last_reanchor_at = None
    coordinator._feedin_prev_written_w = None
    coordinator._feedin_owned_entity = None
    coordinator._feedin_delivered = None
    coordinator._feedin_plan_trace.clear()
    coordinator._feedin_brake_engaged = False
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
    # The SOC sensor is nudged first: it has been frozen at 55 the whole test,
    # and a full day's plan drift against a frozen reading is exactly what the
    # stale watchdog (correctly) fails the cycle with — which would skip the
    # judgement and with it the reset under test.
    hass.states.async_set(
        "sensor.test_soc", "56", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    midnight = (MORNING + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0
    )
    await _refresh_at(hass, coordinator, midnight)
    assert coordinator.feedin_manual() is False
    assert coordinator._feedin_manual_until is None
    assert coordinator._persistent_payload()["feedin_manual_until"] is None
    assert hass.states.get(mode_eid).state == "auto"
    assert float(hass.states.get(SETPOINT).state) == 0.0


async def test_runtime_switch_rising_edge_exits_manual_mode(hass):
    """Operator rule 2026-08-08: manual mode ends on a rising edge of the
    runtime switch (off -> on) — not only at midnight. The operator's last
    value is adopted as the baseline (not re-judged as an override), and the
    resumed automation re-anchors the setpoint to the plan."""
    calls: list[tuple[str, float]] = []
    coordinator, entry = await _setup_feedin(hass, calls)
    registry = er.async_get(hass)
    switch_eid = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_{ENTITY_FEEDIN_SWITCH}"
    )

    _set_setpoint(hass, coordinator, "-900")
    later = MORNING + timedelta(seconds=FEEDIN_MANUAL_GRACE_S + 1)
    await _refresh_at(hass, coordinator, later)
    assert coordinator._feedin_manual_until is not None
    assert calls == []  # hands off while the operator owns the setpoint

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_eid}, blocking=True
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_eid}, blocking=True
    )
    await hass.async_block_till_done()
    _cancel_debounce(coordinator)
    assert coordinator._feedin_manual_until is None
    assert coordinator._persistent_payload()["feedin_manual_until"] is None
    # The operator's 900 W was adopted as the baseline on the rising edge (so
    # it is not re-judged as an override), and the resumed automation
    # re-anchored the setpoint to the plan's idle value in the same refresh.
    assert calls and calls[-1] == (SETPOINT, 0.0)
    assert float(hass.states.get(SETPOINT).state) == 0.0


async def test_manual_mode_plans_the_operator_value_until_midnight(hass):
    """The plan mirrors the operator-owned setpoint for the rest of today —
    so the SOC forecast and the card lane reflect reality — while tomorrow
    plans automatically again. 0 W books nothing (no chart lane). The
    executor stays hands-off throughout (operator decision 2026-08-08)."""
    calls: list[tuple[str, float]] = []
    coordinator, _entry = await _setup_feedin(hass, calls)

    _set_setpoint(hass, coordinator, "-450")
    later = MORNING + timedelta(seconds=FEEDIN_MANUAL_GRACE_S + 1)
    await _refresh_at(hass, coordinator, later)
    today = later.date()
    feedins_today = [
        p.get("feedin", 0.0)
        for p in coordinator.data["soc_forecast"]
        if dt_util.parse_datetime(p["t"]).date() == today
    ]
    assert any(w == pytest.approx(450.0) for w in feedins_today)
    assert calls == []  # planned, never written

    # Operator value 0 W: nothing is planned for today — no feed-in lane.
    _set_setpoint(hass, coordinator, "0")
    await _refresh_at(hass, coordinator, later + timedelta(minutes=5))
    feedins_today = [
        p.get("feedin", 0.0)
        for p in coordinator.data["soc_forecast"]
        if dt_util.parse_datetime(p["t"]).date() == today
    ]
    assert not any(w > 0.0 for w in feedins_today)
    assert calls == []


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


# ---------------------------------------------------------------------------
# Fail-safes OUTSIDE the planning path, and the write-storm guards
# (review 2026-08-03 — every case below shipped broken in the first cut)
# ---------------------------------------------------------------------------


async def test_data_loss_forces_setpoint_to_zero_exactly_once(hass):
    """A SOC dropout raises UpdateFailed BEFORE `_apply_feedin`, so the export
    used to keep running unsupervised for the whole outage — draining the
    battery once PV fell below the setpoint. The zero is written from the
    failure path itself, and only once however long the outage lasts."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls[-1] == (SETPOINT, -500.0)

    # Outage that outlasted the input caches (SOC 6 h, forecasts 72 h) — that
    # is when `_get_soc`/`_get_forecasts` return None and the cycle fails.
    hass.states.async_set("sensor.test_soc", "unavailable")
    coordinator._last_valid_soc = None
    coordinator._last_valid_forecasts = None
    await _refresh_at(hass, coordinator, MORNING + timedelta(minutes=5))
    assert not coordinator.last_update_success
    assert calls[-1] == (SETPOINT, 0.0)

    writes = len(calls)
    await _refresh_at(hass, coordinator, MORNING + timedelta(minutes=10))
    await _refresh_at(hass, coordinator, MORNING + timedelta(minutes=15))
    assert len(calls) == writes  # idempotent: no write storm during the outage


async def test_runtime_switch_off_writes_zero_while_inputs_are_stale(hass):
    """The switch promised "the next `_apply_feedin` pass writes 0" — but that
    pass never comes while the inputs are stale, so the switch read `off` while
    the plant kept exporting. The zero is now written by the switch itself."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls[-1] == (SETPOINT, -500.0)

    hass.states.async_set("sensor.test_soc", "unavailable")
    coordinator._last_valid_soc = None
    coordinator._last_valid_forecasts = None
    with patch.object(dt_util, "now", return_value=MORNING + timedelta(minutes=5)):
        await coordinator.async_set_feedin_enabled(False)
        await hass.async_block_till_done()
    _cancel_debounce(coordinator)
    assert calls[-1] == (SETPOINT, 0.0)


async def test_unwired_setpoint_entity_is_released_to_zero_after_reload(hass):
    """Clearing the setpoint entity in the options flow stranded the last
    exported value forever: `_apply_feedin` returns before it can write the
    documented one-shot 0. The owning entity is persisted, so the reloaded
    coordinator releases it."""
    calls: list[tuple[str, float]] = []
    coordinator, entry = await _setup_feedin(hass, calls)
    await _executor_pass(hass, coordinator, _result(500.0))
    assert calls[-1] == (SETPOINT, -500.0)
    assert coordinator._feedin_owned_entity == SETPOINT

    await coordinator.async_flush_persistent_state()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # The operator cleared the wiring: config_flow writes None for a cleared
    # selector field.
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_FEEDIN_SETPOINT_ENTITY: None},
    )
    calls.clear()
    with patch.object(dt_util, "now", return_value=MORNING + timedelta(minutes=5)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reloaded = hass.data[DOMAIN][entry.entry_id]
    reloaded._listeners_setup = False
    _cancel_debounce(reloaded)

    assert (SETPOINT, 0.0) in calls
    assert reloaded._feedin_owned_entity is None


async def test_downward_trim_does_not_rewrite_an_unchanged_value(hass):
    """Trim writes carry no deadband — but a sustained discharge clamps the
    trim at 0, and re-writing that same 0 on every pass flooded the log and
    hammered the state machine for the whole episode."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    await _executor_pass(hass, coordinator, _result(400.0))
    assert calls[-1] == (SETPOINT, -400.0)

    _set_battery_power(hass, coordinator, "-800")
    await _executor_pass(
        hass, coordinator, _result(400.0), moment=MORNING + timedelta(seconds=5)
    )
    assert calls[-1] == (SETPOINT, 0.0)  # clamped

    writes = len(calls)
    for offset in (10, 15, 20):
        await _executor_pass(
            hass,
            coordinator,
            _result(400.0),
            moment=MORNING + timedelta(seconds=offset),
        )
    assert len(calls) == writes  # same value, still discharging: nothing written


async def test_reanchor_is_throttled_to_the_planning_interval(hass):
    """`_apply_feedin` also runs on every debounced battery-power event, so an
    ungated re-anchor undid each throttled upward trim seconds later and the
    setpoint oscillated against the external controller. One re-anchor per
    planning interval."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    day = {MORNING.date().isoformat(): 20000.0}

    await _executor_pass(hass, coordinator, _result(500.0, day))
    _set_battery_power(hass, coordinator, "200")
    await _executor_pass(hass, coordinator, _result(500.0, day))
    assert calls[-1] == (SETPOINT, -700.0)

    # First re-anchor: allowed, pulls back to the plan value.
    _set_battery_power(hass, coordinator, "0")
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=5)
    )
    assert calls[-1] == (SETPOINT, -500.0)

    # Trim up again, then drift back inside the throttle window: the re-anchor
    # must NOT fire — that pair was the oscillation.
    _set_battery_power(hass, coordinator, "200")
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=70)
    )
    assert calls[-1] == (SETPOINT, -700.0)
    _set_battery_power(hass, coordinator, "0")
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=75)
    )
    assert calls[-1] == (SETPOINT, -700.0)  # unchanged: re-anchor suppressed

    # Past the interval it re-anchors again.
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=320)
    )
    assert calls[-1] == (SETPOINT, -500.0)


async def test_external_change_during_an_active_trim_enters_manual_mode(hass):
    """The trim refreshes the write timestamp every few seconds, so a grace
    that blanket-excuses every difference masked operator overrides for as long
    as the trim ran. Only a state still showing the PREVIOUS value we wrote is
    propagation lag."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    await _executor_pass(hass, coordinator, _result(500.0))
    _set_battery_power(hass, coordinator, "-200")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=5)
    )
    assert calls[-1] == (SETPOINT, -300.0)

    # Operator caps the export by hand, well inside the grace window.
    moment = MORNING + timedelta(seconds=10)
    assert moment - MORNING < timedelta(seconds=FEEDIN_MANUAL_GRACE_S)
    _set_setpoint(hass, coordinator, "-200")
    with patch.object(dt_util, "now", return_value=moment):
        coordinator._update_feedin_mode(moment)
        # Read inside the pinned window: feedin_manual() compares against the
        # CURRENT date (pattern: the override test above).
        assert coordinator.feedin_manual() is True

    writes = len(calls)
    await _executor_pass(hass, coordinator, _result(500.0), moment=moment)
    assert len(calls) == writes  # the operator owns the setpoint now


async def test_lagging_setpoint_state_is_not_read_as_an_override(hass):
    """The other side of the same rule: while the entity still shows the value
    we wrote BEFORE the last write, that is propagation lag, not an override."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    await _executor_pass(hass, coordinator, _result(500.0))
    _set_battery_power(hass, coordinator, "-200")
    await _executor_pass(
        hass, coordinator, _result(500.0), moment=MORNING + timedelta(seconds=5)
    )
    assert calls[-1] == (SETPOINT, -300.0)

    _set_setpoint(hass, coordinator, "-500")  # still the previous value
    moment = MORNING + timedelta(seconds=10)
    with patch.object(dt_util, "now", return_value=moment):
        coordinator._update_feedin_mode(moment)
    assert not coordinator.feedin_manual()


async def test_upward_trim_cap_ignores_the_energy_delivered_earlier(hass):
    """`feedin_by_day_wh` is booked over the slots from NOW on — it is already
    the remaining amount. Subtracting the energy delivered earlier today drove
    the cap to 0 and silently disabled the upward trim for the rest of the
    day."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    # The plan still books 12 kWh from now on (rate cap ~727 W over the 16.5 h
    # to midnight — above the 700 W the trim wants), while MORE than that was
    # already delivered earlier today. The old `day - delivered` drove the cap
    # to 0; the plan value alone must be used.
    day = {MORNING.date().isoformat(): 12000.0}
    coordinator._feedin_delivered = (MORNING.date(), 12500.0, MORNING)

    await _executor_pass(hass, coordinator, _result(500.0, day))
    assert calls[-1] == (SETPOINT, -500.0)
    _set_battery_power(hass, coordinator, "200")
    await _executor_pass(
        hass, coordinator, _result(500.0, day), moment=MORNING + timedelta(seconds=70)
    )
    assert calls[-1] == (SETPOINT, -700.0)


async def test_fast_tracked_input_does_not_starve_the_debounce(hass):
    """The battery-power sensor publishes every 1-2 s. Cancelling and
    restarting the 5 s sleep on every event meant the debounced refresh — and
    with it the trim that must react in SECONDS — never ran at all."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    coordinator._listeners_setup = True
    _cancel_debounce(coordinator)

    coordinator._handle_entity_change(None)
    armed = coordinator._debounce_task
    assert armed is not None
    for _ in range(5):
        coordinator._handle_entity_change(None)
    # Same task, still pending: the burst was absorbed, not restarted.
    assert coordinator._debounce_task is armed
    assert not armed.cancelled()

    coordinator._listeners_setup = False
    _cancel_debounce(coordinator)


# ---------------------------------------------------------------------------
# R16 plan-value stability brake (2026-08-08 pass-3 flicker incident)
# ---------------------------------------------------------------------------


async def test_plan_value_flap_holds_upward_writes(hass, caplog):
    """R16 stability brake: while the plan's slot-0 value scatters by more
    than FEEDIN_STABLE_SPREAD_W over the sliding window, every UPWARD write
    is held — the 2026-08-08 pass-3 flicker otherwise swung the setpoint
    0<->~1 kW every 1-2 min. Downward writes are never braked, and once the
    plan settles the anchor write goes through."""
    import logging

    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    t0 = MORNING

    async def pass_at(k_s, plan_w):
        await _executor_pass(
            hass,
            coordinator,
            _result(plan_w),
            moment=t0 + timedelta(seconds=k_s),
            seed_plan_trace=False,
        )

    with caplog.at_level(logging.INFO):
        # Stable 300 W plan: the first pass seeds the trace (spread 0), the
        # anchor write fires immediately.
        for k in (0, 10, 20):
            await pass_at(k, 300.0)
        assert calls == [(SETPOINT, -300.0)]

        # The plan starts flapping 0 <-> 800 W: the drop to 0 writes at once
        # (downward is never braked); every re-raise is held.
        await pass_at(30, 0.0)
        assert calls[-1] == (SETPOINT, 0.0)
        for k in range(40, 170, 10):
            await pass_at(k, 800.0 if (k // 10) % 2 == 0 else 0.0)
        assert all(v > -800.0 for _, v in calls), "no upward burst may pass"
        assert calls == [(SETPOINT, -300.0), (SETPOINT, 0.0)]
        assert coordinator._feedin_brake_engaged

        # The plan settles at 800 W; once the flap has aged out of the
        # window, the anchor write goes through and the brake releases.
        for k in range(220, 400, 10):
            await pass_at(k, 800.0)
        assert calls[-1] == (SETPOINT, -800.0)
        assert not coordinator._feedin_brake_engaged

    texts = [r.getMessage() for r in caplog.records]
    assert sum("F-FEEDIN R16" in t and "held at" in t for t in texts) == 1
    assert sum("brake released" in t for t in texts) == 1


async def test_downward_trim_stays_immediate_during_plan_flap(hass):
    """R16 never brakes the safety direction: with the plan value flapping,
    a discharging battery still cuts the setpoint immediately and
    unthrottled."""
    calls: list[tuple[str, float]] = []
    coordinator, _ = await _setup_feedin(hass, calls)
    t0 = MORNING

    async def pass_at(k_s, plan_w):
        await _executor_pass(
            hass,
            coordinator,
            _result(plan_w),
            moment=t0 + timedelta(seconds=k_s),
            seed_plan_trace=False,
        )

    # Stable anchor 300 W first (write fires), then the plan starts flapping
    # (800 W pass held by the brake: trace spread 500 W).
    for k in (0, 10, 20):
        await pass_at(k, 300.0)
    assert calls == [(SETPOINT, -300.0)]
    await pass_at(30, 800.0)
    assert calls == [(SETPOINT, -300.0)]  # held

    # Battery discharging 800 W mid-flap: trim down by the discharge amount —
    # immediate, no brake, no deadband.
    _set_battery_power(hass, coordinator, "-800")
    await pass_at(40, 800.0)
    assert calls[-1] == (SETPOINT, 0.0)
    assert float(hass.states.get(SETPOINT).state) == 0.0


async def test_removing_battery_sensor_releases_owned_export_setpoint():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.battery_manager import const as c
    from custom_components.battery_manager.coordinator import (
        BatteryManagerCoordinator as Coordinator,
    )

    writes = AsyncMock(return_value=True)
    coordinator = SimpleNamespace(
        raw_config={
            c.CONF_FEEDIN_ENABLED: False,
            c.CONF_FEEDIN_SETPOINT_ENTITY: "input_number.export",
            c.CONF_FEEDIN_BATTERY_POWER_ENTITY: None,
        },
        _feedin_owned_entity="input_number.export",
        _feedin_last_written_w=500,
        feedin_manual=lambda: False,
        hass=SimpleNamespace(states=SimpleNamespace(get=lambda _: object())),
        _switch_lock=asyncio.Lock(),
        _set_number_value=writes,
        _save_persistent_state=lambda: None,
    )
    coordinator._feedin_entities = lambda: Coordinator._feedin_entities(coordinator)
    await Coordinator._feedin_release_orphan(coordinator)
    assert writes.await_count == 1
    assert writes.call_args.args[:2] == ("input_number.export", 0.0)
    assert coordinator._feedin_owned_entity is None
