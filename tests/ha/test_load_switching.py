"""Tests for the direct charging-path control of surplus loads.

Spec: docs/LOAD_CONTROL.md — charging active = input plug on AND charge-enable
on; ownership rule / configurable input-off policy; last-known-SOC caching.
"""

from datetime import timedelta

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_LOAD_CAPACITY_WH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_ENERGY_LIMITED,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_MIN_OFF_MIN,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_POWER_WARNING_DWELL_MIN,
    CONF_LOAD_POWER_WARNING_PCT,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_WARNING_NOTIFY_ON_RESOLVE,
    CONF_WARNING_NOTIFY_TARGETS,
    DOMAIN,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_AUTO,
    SUBENTRY_TYPE_LOAD,
)

PLUG = "switch.shelly_fossibot_input"
ENABLE = "input_boolean.charge_fossibot"
FOSSI_SOC = "sensor.fossibot_soc"
POWER_FEEDBACK = "sensor.fossibot_in_total"

BASE_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}


def _register_switch_services(hass, call_log):
    async def turn_on(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_on", entity_id))
        hass.states.async_set(entity_id, "on")

    async def turn_off(call):
        entity_id = call.data["entity_id"]
        call_log.append(("turn_off", entity_id))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)


async def _setup(
    hass,
    call_log,
    *,
    policy=INPUT_OFF_POLICY_AUTO,
    power_w=300.0,
    with_control_switch=True,
    energy_limited=True,
    min_runtime_min=None,
    min_off_min=None,
    power_entity=POWER_FEEDBACK,
    charge_enable=ENABLE,
    power_warning_pct=50.0,
    power_warning_dwell_min=30,
):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(FOSSI_SOC, "40", {"unit_of_measurement": "%"})
    _register_switch_services(hass, call_log)

    load_data = {
        CONF_LOAD_POWER_W: power_w,
        CONF_LOAD_ENERGY_LIMITED: energy_limited,
        CONF_LOAD_CAPACITY_WH: 2000.0,
        CONF_LOAD_TARGET_SOC: 90.0,
        CONF_LOAD_SOC_ENTITY: FOSSI_SOC,
        CONF_LOAD_POWER_WARNING_PCT: power_warning_pct,
        CONF_LOAD_POWER_WARNING_DWELL_MIN: power_warning_dwell_min,
    }
    if power_entity is not None:
        load_data[CONF_LOAD_POWER_ENTITY] = power_entity
    if min_runtime_min is not None:
        load_data[CONF_LOAD_MIN_RUNTIME_MIN] = min_runtime_min
    if min_off_min is not None:
        load_data[CONF_LOAD_MIN_OFF_MIN] = min_off_min
    if with_control_switch:
        load_data |= {
            CONF_LOAD_CONTROL_SWITCH: PLUG,
            CONF_LOAD_INPUT_OFF_POLICY: policy,
        }
        if charge_enable is not None:  # None -> plug-only load (G1 R3)
            load_data[CONF_LOAD_CHARGE_ENABLE] = charge_enable
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=BASE_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=load_data,
                subentry_type=SUBENTRY_TYPE_LOAD,
                title="Fossibot Test",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # The setup-time refresh may already have started charging (sunny test
    # day); reset so each test drives _execute_load_switching from a clean,
    # deterministic state.
    await hass.async_block_till_done()
    coordinator._load_plug_owned.clear()
    coordinator._load_charging_active.clear()
    coordinator._last_load_switch.clear()
    coordinator._load_plan_active.clear()
    coordinator._load_learn_ok.clear()
    sub_id = next(iter(entry.subentries))
    data = dict(entry.subentries[sub_id].data)
    return coordinator, sub_id, data


async def test_start_owns_plug_and_stop_releases_it(hass):
    """Plug was off: we own it — stop turns enable AND plug off (auto)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, True, False)])
    assert calls == [("turn_on", ENABLE), ("turn_on", PLUG)]
    assert coordinator._load_plug_owned[sub_id] is True

    calls.clear()
    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert calls == [("turn_off", ENABLE), ("turn_off", PLUG)]
    assert coordinator._load_plug_owned[sub_id] is False


async def test_passthrough_plug_stays_on(hass):
    """Plug was already on (output passthrough): only the enable gate toggles."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "on")  # user automation powers the output
    hass.states.async_set(ENABLE, "off")
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, True, True)])
    assert calls == [("turn_on", ENABLE)]
    assert coordinator._load_plug_owned.get(sub_id, False) is False

    calls.clear()
    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert calls == [("turn_off", ENABLE)]  # plug untouched
    assert hass.states.get(PLUG).state == "on"


async def test_policy_always_off_switches_foreign_plug_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, policy=INPUT_OFF_POLICY_ALWAYS
    )
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert ("turn_off", PLUG) in calls


async def test_failed_plug_off_keeps_ownership_for_later_cleanup(hass):
    """Review #3: if the plug turn-off fails, ownership/charging state must NOT
    be cleared — otherwise the plug is stranded ON while BM records it as
    not-ours and never cleans it up. Keeping ownership bounds the stranding to
    the next charge cycle instead of forever."""
    from homeassistant.exceptions import HomeAssistantError

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)  # AUTO + charge-enable
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_plug_owned[sub_id] = True
    coordinator._load_charging_active[sub_id] = True

    async def turn_off(call):
        eid = call.data["entity_id"]
        calls.append(("turn_off", eid))
        if eid == PLUG:
            raise HomeAssistantError("plug offline")  # actuation fails
        hass.states.async_set(eid, "off")

    hass.services.async_register("homeassistant", "turn_off", turn_off)
    calls.clear()

    await coordinator._execute_load_switching([(sub_id, data, False, True)])
    assert ("turn_off", PLUG) in calls  # attempted
    assert hass.states.get(PLUG).state == "on"  # failed -> still on
    assert coordinator._load_plug_owned[sub_id] is True  # KEPT, not dropped


async def test_failed_activation_does_not_consume_dwell(hass):
    """Review #11: a failed actuation must not stamp the min-runtime dwell, so
    the retry next cycle is not blocked for the whole window."""
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")

    async def turn_on(call):
        eid = call.data["entity_id"]
        calls.append(("turn_on", eid))
        if eid == ENABLE:
            raise HomeAssistantError("enable offline")  # activation fails
        hass.states.async_set(eid, "on")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    calls.clear()

    await coordinator._execute_load_switching(
        [(sub_id, data, True, False)], now=dt_util.now()
    )
    assert sub_id not in coordinator._last_load_switch  # dwell NOT consumed


async def test_successful_switch_stamps_dwell(hass):
    """The dwell IS stamped on a confirmed switch (throttle still works)."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    now = dt_util.now()

    await coordinator._execute_load_switching([(sub_id, data, True, False)], now=now)
    assert coordinator._last_load_switch[sub_id] == now


async def test_soc_cache_survives_sleeping_device(hass):
    """SOC unavailable (device asleep): planning keeps the last known value."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)

    states = coordinator._get_load_states()
    assert states[0].soc_percent == 40.0

    hass.states.async_set(FOSSI_SOC, "unavailable")
    states = coordinator._get_load_states()
    assert states[0].soc_percent == 40.0  # cached, not None/unavailable
    assert states[0].available is True

    hass.states.async_set(FOSSI_SOC, "62.5")
    states = coordinator._get_load_states()
    assert states[0].soc_percent == 62.5


async def test_switch_dwell_survives_restart(hass):
    """The per-load switch dwell must not reset on restart: a wiped
    timestamp allowed switching right after boot (co-factor of the
    2026-07-05 degenerate-slot-0 night charge). The power EMA is
    deliberately NOT persisted (a taper-decayed value must not become
    permanent planning power)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)

    from homeassistant.util import dt as dt_util

    ts = dt_util.utcnow().replace(microsecond=0)
    coordinator._load_power_ema[sub_id] = 505.4
    coordinator._last_load_switch[sub_id] = ts

    captured: dict = {}
    coordinator._store.async_delay_save = lambda data_func, _delay: captured.update(
        data_func()
    )
    coordinator._save_persistent_state()
    assert captured["last_load_switch"] == {sub_id: ts.isoformat()}
    assert "power_ema" not in captured

    # Round-trip: a restarted coordinator restores the dwell verbatim. A
    # fresh Store instance simulates the restart (the old one caches its
    # first async_load result for its lifetime).
    from homeassistant.helpers.storage import Store

    await coordinator._store.async_save(captured)
    coordinator._load_power_ema.clear()
    coordinator._last_load_switch.clear()
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert coordinator._last_load_switch == {sub_id: ts}
    assert coordinator._load_power_ema == {}


async def test_power_ema_serves_only_while_charging(hass):
    """A feedback gap keeps the EMA only DURING an active charge (v0.5.1
    rule); after the charge the taper-decayed value is discarded so the
    planner falls back to the nominal power."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_charging_active[sub_id] = True  # BM-initiated charge
    hass.states.async_set(POWER_FEEDBACK, "505")

    states = coordinator._get_load_states()
    assert states[0].measured_power_w == 505.0

    # Feedback drops out mid-charge: last smoothed value keeps serving.
    hass.states.async_set(POWER_FEEDBACK, "0")
    states = coordinator._get_load_states()
    assert states[0].measured_power_w == 505.0

    # Charge over: the EMA is dropped, planning returns to nominal power.
    coordinator._load_charging_active[sub_id] = False
    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None
    assert sub_id not in coordinator._load_power_ema


async def test_standby_power_never_seeds_ema(hass):
    """A standby reading (dehumidifier idling at ~20 W of 400 W nominal)
    clears the old flat 10 W bar but sits far below STANDBY_FRACTION of
    the nominal power — it must not become the planning power. The live
    plan otherwise booked 11 h × 22 Wh for a device that really pulls
    ~400 W (2026-07-05 incident)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator._load_charging_active[sub_id] = True  # even mid-charge
    hass.states.async_set(POWER_FEEDBACK, "19.6")

    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None  # planner uses nominal 400 W
    assert sub_id not in coordinator._load_power_ema


async def test_operating_power_above_standby_threshold_is_learned(hass):
    """A real operating value (350 W of 400 W nominal) is above the
    standby threshold and seeds the EMA; when the device drops back to
    standby without an active charge, the EMA is discarded again."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator._load_charging_active[sub_id] = True  # BM-initiated charge
    hass.states.async_set(POWER_FEEDBACK, "350")

    states = coordinator._get_load_states()
    assert states[0].measured_power_w == 350.0

    # Back to standby draw, run over: the learned value must not linger.
    coordinator._load_charging_active[sub_id] = False
    hass.states.async_set(POWER_FEEDBACK, "19.6")
    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None
    assert sub_id not in coordinator._load_power_ema


async def test_manual_run_never_trains_planning_power(hass):
    """Operator decision F-L6 (2026-07-05): a manual activation (or a
    foreign consumer on the measured outlet) must not influence future
    planning. For a recommendation-only load, samples train the EMA only
    during an activation that started with an idle outlet — the draw then
    provably happened in response to the plan."""
    from types import SimpleNamespace

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, power_w=400.0, with_control_switch=False
    )
    on = SimpleNamespace(load_plans=[SimpleNamespace(load_id=sub_id, active_now=True)])
    off = SimpleNamespace(
        load_plans=[SimpleNamespace(load_id=sub_id, active_now=False)]
    )

    # Manual run at full power, no active recommendation: not learned.
    hass.states.async_set(POWER_FEEDBACK, "400")
    coordinator._update_plan_active(off)
    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None
    assert sub_id not in coordinator._load_power_ema

    # Plan activates WHILE the manual draw is ongoing (dirty edge): the
    # pre-existing draw still must not train — repeatedly (stability!).
    coordinator._update_plan_active(on)
    for _ in range(3):
        states = coordinator._get_load_states()
        assert states[0].measured_power_w is None
        assert sub_id not in coordinator._load_power_ema

    # Clean cycle: recommendation off, outlet idle, then a fresh edge —
    # now the draw follows the plan and is a legitimate sample.
    coordinator._update_plan_active(off)
    hass.states.async_set(POWER_FEEDBACK, "5")
    coordinator._update_plan_active(on)
    hass.states.async_set(POWER_FEEDBACK, "400")
    states = coordinator._get_load_states()
    assert states[0].measured_power_w == 400.0

    # Recommendation ends, device back to standby: EMA is discarded.
    coordinator._update_plan_active(off)
    hass.states.async_set(POWER_FEEDBACK, "19.6")
    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None
    assert sub_id not in coordinator._load_power_ema


async def test_charging_state_survives_entity_dropout(hass):
    """A plug/enable entity dropout (unavailable) must not read as 'charge
    over' — that would delete the learned EMA mid-charge (review finding
    on v0.6.3)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    assert coordinator._charging_is_active(data) is True

    hass.states.async_set(PLUG, "unavailable")
    assert coordinator._charging_is_active(data) is None  # unknown, not off

    hass.states.async_set(PLUG, "off")
    assert coordinator._charging_is_active(data) is False


async def test_power_warning_after_sustained_deviation(hass):
    """F-L7: a full water tank (draw near 0 W) while the load runs at the
    integration's request trips the warning after the 30-min dwell and
    clears as soon as the real draw is back within the band."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    result = SimpleNamespace(load_plans=[])
    coordinator._load_charging_active[sub_id] = True
    t0 = dt_util.utcnow()

    hass.states.async_set(POWER_FEEDBACK, "2")  # tank full
    await coordinator._update_power_warnings(result, t0)
    assert coordinator._load_power_warning.get(sub_id, False) is False

    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=31))
    assert coordinator._load_power_warning[sub_id] is True

    hass.states.async_set(POWER_FEEDBACK, "395")  # back to normal
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=40))
    assert coordinator._load_power_warning[sub_id] is False


async def test_power_warning_defrost_dip_resets_timer(hass):
    """Short defrost pauses (deviating minutes, then normal draw again)
    keep resetting the dwell timer and never trip the warning."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    result = SimpleNamespace(load_plans=[])
    coordinator._load_charging_active[sub_id] = True
    t0 = dt_util.utcnow()

    hass.states.async_set(POWER_FEEDBACK, "150")  # defrost: fan + heater
    await coordinator._update_power_warnings(result, t0)
    hass.states.async_set(POWER_FEEDBACK, "400")  # compressor back on
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=10))
    hass.states.async_set(POWER_FEEDBACK, "150")  # next defrost
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=45))
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=60))
    # Second dip lasted only 15 min since its own start: no warning.
    assert coordinator._load_power_warning.get(sub_id, False) is False


async def test_power_warning_ignores_manual_runs(hass):
    """Manual/foreign consumption on the measured outlet (load NOT running
    at the integration's request) never trips the warning (F-L6/F-L7)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, power_w=400.0, with_control_switch=False
    )
    result = SimpleNamespace(load_plans=[])
    t0 = dt_util.utcnow()

    hass.states.async_set(POWER_FEEDBACK, "800")  # foreign consumer
    await coordinator._update_power_warnings(result, t0)
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=45))
    assert coordinator._load_power_warning.get(sub_id, False) is False

    # With an active recommendation the same deviation IS a problem.
    active_plan = SimpleNamespace(load_id=sub_id, active_now=True)
    result = SimpleNamespace(load_plans=[active_plan])
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=50))
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=81))
    assert coordinator._load_power_warning[sub_id] is True


async def test_power_warning_dwell_is_per_load_configurable(hass):
    """The dwell is a per-load setting: a 15-min dwell trips after 15 min,
    not after the old fixed 30 (operator wish 2026-07-12)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, power_w=400.0, power_warning_dwell_min=15
    )
    result = SimpleNamespace(load_plans=[])
    coordinator._load_charging_active[sub_id] = True
    t0 = dt_util.utcnow()

    hass.states.async_set(POWER_FEEDBACK, "2")  # tank full
    await coordinator._update_power_warnings(result, t0)
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=14))
    assert coordinator._load_power_warning.get(sub_id, False) is False  # < 15
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=16))
    assert coordinator._load_power_warning[sub_id] is True  # >= 15


async def test_power_warning_disabled_at_zero_percent(hass):
    """0 % = off: a sustained deviation never trips (the new default for a
    freshly added load)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, power_w=400.0, power_warning_pct=0.0
    )
    result = SimpleNamespace(load_plans=[])
    coordinator._load_charging_active[sub_id] = True
    t0 = dt_util.utcnow()

    hass.states.async_set(POWER_FEEDBACK, "2")  # tank full
    await coordinator._update_power_warnings(result, t0)
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=60))
    assert coordinator._load_power_warning.get(sub_id, False) is False


async def test_power_warning_latches_when_deactivated(hass):
    """Regression (operator report 2026-07-12): once tripped, the warning
    stays on when the load is deactivated (BM stops requesting it) — a full
    tank is still full while the load is off — and clears only when the load
    runs at its configured power again."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    result = SimpleNamespace(load_plans=[])
    coordinator._load_charging_active[sub_id] = True
    t0 = dt_util.utcnow()

    # Trip the warning while active.
    hass.states.async_set(POWER_FEEDBACK, "2")  # tank full
    await coordinator._update_power_warnings(result, t0)
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=31))
    assert coordinator._load_power_warning[sub_id] is True

    # Deactivate the load (BM no longer requests it): the OLD behaviour cleared
    # the warning here — it must now LATCH on.
    coordinator._load_charging_active[sub_id] = False
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=40))
    assert coordinator._load_power_warning[sub_id] is True

    # Even a normal reading while inactive must NOT clear it (not BM-driven).
    hass.states.async_set(POWER_FEEDBACK, "400")
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=50))
    assert coordinator._load_power_warning[sub_id] is True

    # Only running at configured power AT BM'S REQUEST clears it.
    coordinator._load_charging_active[sub_id] = True
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=60))
    assert coordinator._load_power_warning[sub_id] is False


async def test_power_warning_latch_survives_reload(hass):
    """The latch is persisted so an options save (coordinator reload) or a
    restart does not silently drop a raised warning; a vanished subentry is
    dropped on restore."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)

    coordinator._load_power_warning = {sub_id: True, "vanished_sub": True}
    payload = coordinator._persistent_payload()
    assert payload["load_power_warning"] == {sub_id: True, "vanished_sub": True}

    await coordinator._store.async_save(payload)
    coordinator._load_power_warning = {}
    await coordinator.async_load_persistent_state()
    # Restored for the live subentry, dropped for the vanished one.
    assert coordinator._load_power_warning == {sub_id: True}


async def test_power_warning_latch_cleared_when_disabled(hass):
    """Turning the warning off (0 %) drops a lingering latch and dwell timer,
    so it can never get stuck invisibly once the feature is disabled."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, power_w=400.0, power_warning_pct=0.0
    )
    coordinator._load_power_warning[sub_id] = True
    coordinator._load_deviation_since[sub_id] = dt_util.utcnow()
    result = SimpleNamespace(load_plans=[])
    coordinator._load_charging_active[sub_id] = True

    await coordinator._update_power_warnings(result, dt_util.utcnow())
    assert coordinator._load_power_warning.get(sub_id, False) is False
    assert sub_id not in coordinator._load_deviation_since


async def test_power_warning_pushes_notifications(hass):
    """The trip edge pushes a 'problem' notification to every configured
    target (with load name + measured/expected W); the clear edge pushes a
    'resolved' notification. Driven through _set_power_warning to keep the
    edge->notify wiring deterministic (the coordinator's refresh machinery
    resets manual _load_charging_active across async_block_till_done)."""
    captured: list[dict] = []

    async def _fake_notify(call):
        captured.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_test", _fake_notify)

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = ["mobile_app_test"]
    coordinator.raw_config[CONF_WARNING_NOTIFY_ON_RESOLVE] = True

    # Trip edge.
    await coordinator._set_power_warning(
        sub_id, "Fossibot Test", True, raw=2, nominal=400, dwell=30
    )
    await hass.async_block_till_done()
    assert len(captured) == 1
    assert "power warning" in captured[0]["title"].lower()
    assert "Fossibot Test" in captured[0]["message"]
    assert "400 W" in captured[0]["message"]

    # Clear edge.
    await coordinator._set_power_warning(sub_id, "Fossibot Test", False)
    await hass.async_block_till_done()
    assert len(captured) == 2
    assert "cleared" in captured[1]["title"].lower()


async def test_power_warning_notifies_all_targets(hass):
    """A global list with several targets pushes to each (arbitrary users)."""
    captured: list[tuple[str, dict]] = []

    def _make(name):
        async def _fake_notify(call):
            captured.append((name, dict(call.data)))

        return _fake_notify

    for name in ("mobile_app_a", "mobile_app_b"):
        hass.services.async_register("notify", name, _make(name))

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = [
        "mobile_app_a",
        "mobile_app_b",
    ]

    await coordinator._set_power_warning(
        sub_id, "Fossibot Test", True, raw=2, nominal=400, dwell=30
    )
    await hass.async_block_till_done()
    assert {name for name, _ in captured} == {"mobile_app_a", "mobile_app_b"}


async def test_power_warning_resolve_notification_can_be_silenced(hass):
    """With the resolve toggle off, the clear edge sends no push (the trip
    still does)."""
    captured: list[dict] = []

    async def _fake_notify(call):
        captured.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_test", _fake_notify)

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = ["mobile_app_test"]
    coordinator.raw_config[CONF_WARNING_NOTIFY_ON_RESOLVE] = False

    await coordinator._set_power_warning(
        sub_id, "Fossibot Test", True, raw=2, nominal=400, dwell=30
    )
    await coordinator._set_power_warning(sub_id, "Fossibot Test", False)
    await hass.async_block_till_done()
    assert len(captured) == 1  # trip only, no resolve push


async def test_power_warning_no_targets_no_push(hass):
    """No configured targets -> no service call attempted (no-op), even though
    a notify service exists."""
    seen: list[dict] = []

    async def _spy(call):
        seen.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_spy", _spy)

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = []
    await coordinator._set_power_warning(
        sub_id, "Fossibot Test", True, raw=2, nominal=400, dwell=30
    )
    await hass.async_block_till_done()
    assert coordinator._load_power_warning[sub_id] is True
    assert seen == []  # the registered service was NOT invoked


async def test_power_warning_notify_isolates_bad_target(hass):
    """A stale/removed target (ServiceNotFound is raised synchronously even
    with blocking=False) must neither escape into the update cycle nor block
    the remaining good targets — the per-target try/except is load-bearing."""
    captured: list[dict] = []

    async def _ok(call):
        captured.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_good", _ok)

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    # 'mobile_app_deleted' is not registered (e.g. a since-removed phone) and
    # is listed FIRST — it must not stop the good target that follows.
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = [
        "mobile_app_deleted",
        "mobile_app_good",
    ]
    # Awaited directly: an un-caught ServiceNotFound would raise HERE (which is
    # exactly how it would break _async_update_data in production).
    await coordinator._set_power_warning(
        sub_id, "Fossibot Test", True, raw=2, nominal=400, dwell=30
    )
    await hass.async_block_till_done()
    assert len(captured) == 1  # the good target still received the push


async def test_power_warning_notifies_through_update_cycle(hass):
    """The trip push also fires when reached through the production method
    _update_power_warnings (not just _set_power_warning directly), and the
    real raw/nominal/dwell values flow into the message. No block_till_done
    between the two updates, so the coordinator machinery cannot reset the
    manually-driven active state mid-test."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    captured: list[dict] = []

    async def _fake_notify(call):
        captured.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_test", _fake_notify)

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, power_w=400.0, power_warning_dwell_min=15
    )
    coordinator.raw_config[CONF_WARNING_NOTIFY_TARGETS] = ["mobile_app_test"]
    result = SimpleNamespace(load_plans=[])
    t0 = dt_util.utcnow()

    hass.states.async_set(POWER_FEEDBACK, "2")  # tank full
    coordinator._load_charging_active[sub_id] = True
    await coordinator._update_power_warnings(result, t0)
    coordinator._load_charging_active[sub_id] = True
    await coordinator._update_power_warnings(result, t0 + timedelta(minutes=16))
    await hass.async_block_till_done()
    assert len(captured) == 1
    assert "Fossibot Test" in captured[0]["message"]
    assert "400 W" in captured[0]["message"]
    assert "15 min" in captured[0]["message"]  # per-load dwell in the text


# ---------------------------------------------------------------------------
# F-SUBHOUR: sub-hour executor (approach A) + split dwell (docs/F-SUBHOUR-ALLOCATION.md)
# ---------------------------------------------------------------------------


async def test_subhour_on_arms_deadline_and_timer(hass):
    """A non-energy-limited load booked for a sub-hour run gets a frozen
    off-deadline and a one-shot timer on the ON edge (F-SUBHOUR R7/R8)."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30, min_off_min=30
    )
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    before = dt_util.utcnow()
    await coordinator._execute_load_switching([(sub_id, data, True, False, 0.5)])
    assert sub_id in coordinator._load_run_deadline
    # off_at = run_start + max(min_runtime 30, round(0.5 h) = 30 min) = +30 min
    delta = (coordinator._load_run_deadline[sub_id] - before).total_seconds() / 60.0
    assert 29.0 <= delta <= 31.0
    assert sub_id in coordinator._load_off_timer  # one-shot timer armed
    coordinator._cancel_off_timer(sub_id)  # avoid a lingering test timer


async def test_subhour_run_longer_than_min_runtime_sets_that_deadline(hass):
    """A 90-min planned run deadlines at run_start + 90 min, not min_runtime."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30
    )
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    before = dt_util.utcnow()
    await coordinator._execute_load_switching([(sub_id, data, True, False, 1.5)])
    delta = (coordinator._load_run_deadline[sub_id] - before).total_seconds() / 60.0
    assert 89.0 <= delta <= 91.0
    coordinator._cancel_off_timer(sub_id)


async def test_energy_limited_on_arms_deadline_and_forces_off_when_stale(hass):
    """F-RESIDUAL-TOPUP R7: an energy-limited controlled load booked for a
    sub-hour run now gets the SAME frozen off-deadline as a continuous load, and
    is force-switched OFF at the deadline even while the plan (fed a STALE load
    SOC that never reaches target) still wants it on. This upper-caps the run at
    real_power x max(min_runtime, D) instead of a full-hour night charge (R8)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, energy_limited=True, min_runtime_min=30, min_off_min=30
    )
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    t0 = dt_util.utcnow()
    # ON edge: a 0.5 h booking arms off_at = run_start + max(30, 30) = +30 min.
    await coordinator._execute_load_switching(
        [(sub_id, data, True, False, 0.5)], now=t0
    )
    assert sub_id in coordinator._load_run_deadline
    off_at = coordinator._load_run_deadline[sub_id]
    assert 29.0 <= (off_at - t0).total_seconds() / 60.0 <= 31.0
    assert sub_id in coordinator._load_off_timer  # one-shot timer armed

    # Stale SOC: the plan still shows the load active past the deadline. The cap
    # must force it OFF regardless (the level-driven stop never fired).
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_charging_active[sub_id] = True
    calls.clear()
    result = SimpleNamespace(
        load_plans=[
            LoadPlan(
                load_id=sub_id,
                schedule=(True,),
                planned_energy_wh=0.0,
                run_hours=(0.5,),
            )
        ]
    )
    await coordinator._apply_load_switching(result, off_at + timedelta(minutes=1))
    await hass.async_block_till_done()
    assert ("turn_off", ENABLE) in calls or ("turn_off", PLUG) in calls
    assert sub_id not in coordinator._load_run_deadline
    coordinator._cancel_off_timer(sub_id)


async def test_energy_limited_plan_off_before_deadline_clears_timer(hass):
    """F-RESIDUAL-TOPUP R7: the level-driven stop stays PRIMARY — a plan-driven
    OFF before the frozen deadline (target SOC reached early) still switches the
    load off and clears the deadline + one-shot timer."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls, energy_limited=True)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    await coordinator._execute_load_switching([(sub_id, data, True, False, 0.5)])
    assert sub_id in coordinator._load_run_deadline
    assert sub_id in coordinator._load_off_timer
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    await coordinator._execute_load_switching([(sub_id, data, False, True, 0.0)])
    assert sub_id not in coordinator._load_run_deadline
    assert sub_id not in coordinator._load_off_timer


async def test_rec_only_energy_limited_active_flips_at_deadline(hass):
    """F-RESIDUAL-TOPUP R9: a recommendation-only ENERGY-LIMITED load also gets
    its published `active` capped by the frozen sub-hour deadline (before this
    fix the recommendation-deadline machinery skipped energy-limited loads)."""
    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, energy_limited=True, with_control_switch=False, min_runtime_min=30
    )
    plan = LoadPlan(
        load_id=sub_id, schedule=(True,), planned_energy_wh=0.0, run_hours=(0.5,)
    )
    now = dt_util.utcnow()
    coordinator._maintain_recommendation_deadline(sub_id, data, plan, now, (1.0,))
    assert sub_id in coordinator._load_run_deadline  # anchored despite energy_limited
    assert coordinator._effective_load_active(plan, now) is True
    after = coordinator._load_run_deadline[sub_id] + timedelta(seconds=1)
    assert coordinator._effective_load_active(plan, after) is False  # capped
    coordinator._cancel_off_timer(sub_id)


async def test_off_clears_deadline_and_timer(hass):
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls, energy_limited=False)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    await coordinator._execute_load_switching([(sub_id, data, True, False, 0.5)])
    assert sub_id in coordinator._load_run_deadline
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    await coordinator._execute_load_switching([(sub_id, data, False, True, 0.0)])
    assert sub_id not in coordinator._load_run_deadline
    assert sub_id not in coordinator._load_off_timer


async def test_deadline_forces_off_even_when_plan_wants_on(hass):
    """Once the frozen deadline passes, the load is switched OFF even though the
    plan still wants it on (F-SUBHOUR R8)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(hass, calls, energy_limited=False)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    coordinator._load_charging_active[sub_id] = True
    coordinator._load_run_deadline[sub_id] = dt_util.utcnow() - timedelta(minutes=1)
    calls.clear()
    result = SimpleNamespace(
        load_plans=[
            LoadPlan(
                load_id=sub_id,
                schedule=(True,),
                planned_energy_wh=0.0,
                run_hours=(0.5,),
            )
        ]
    )
    await coordinator._apply_load_switching(result, dt_util.utcnow())
    await hass.async_block_till_done()
    assert ("turn_off", ENABLE) in calls or ("turn_off", PLUG) in calls
    assert sub_id not in coordinator._load_run_deadline


async def test_min_off_dwell_blocks_re_on(hass):
    """After a switch-off, the minimum OFF time blocks an immediate re-on even
    when the plan wants the load on (F-SUBHOUR R14)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30, min_off_min=45
    )
    now = dt_util.utcnow()
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    coordinator._load_charging_active[sub_id] = False
    coordinator._last_load_switch[sub_id] = now - timedelta(minutes=10)  # < 45
    calls.clear()
    result = SimpleNamespace(
        load_plans=[
            LoadPlan(
                load_id=sub_id,
                schedule=(True,),
                planned_energy_wh=0.0,
                run_hours=(0.5,),
            )
        ]
    )
    await coordinator._apply_load_switching(result, now)
    await hass.async_block_till_done()
    assert calls == []  # min_off dwell blocked the re-on
    coordinator._last_load_switch[sub_id] = now - timedelta(minutes=46)  # >= 45
    await coordinator._apply_load_switching(result, now)
    await hass.async_block_till_done()
    assert ("turn_on", PLUG) in calls or ("turn_on", ENABLE) in calls
    coordinator._cancel_off_timer(sub_id)  # the successful on armed a timer


async def test_recommendation_only_load_active_flips_at_deadline(hass):
    """F-SUBHOUR R12: a recommendation-only load (no control switch) gets its
    published `active` capped by the frozen sub-hour deadline, so an operator's
    automation stops it instead of running the whole hour (no over-delivery)."""
    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, data = await _setup(
        hass, calls, energy_limited=False, with_control_switch=False, min_runtime_min=30
    )
    plan = LoadPlan(
        load_id=sub_id, schedule=(True,), planned_energy_wh=0.0, run_hours=(0.5,)
    )
    now = dt_util.utcnow()
    coordinator._maintain_recommendation_deadline(sub_id, data, plan, now, (1.0,))
    assert sub_id in coordinator._load_run_deadline  # anchored on first-active
    assert coordinator._effective_load_active(plan, now) is True
    after = coordinator._load_run_deadline[sub_id] + timedelta(seconds=1)
    assert coordinator._effective_load_active(plan, after) is False  # capped
    # plan goes inactive -> deadline cleared so a later window can re-anchor
    off_plan = LoadPlan(
        load_id=sub_id, schedule=(False,), planned_energy_wh=0.0, run_hours=(0.0,)
    )
    coordinator._maintain_recommendation_deadline(sub_id, data, off_plan, after, (1.0,))
    assert sub_id not in coordinator._load_run_deadline
    coordinator._cancel_off_timer(sub_id)


async def test_rec_only_deadline_reanchors_and_is_not_wedged(hass):
    """FIX-5: a recommendation-only load whose plan stays continuously active for a
    long block (a night pre-drain glued to the following day) must not stay wedged
    OFF after the first frozen deadline. The published `active` goes False at the
    deadline, and once the min_off dwell elapses the deadline RE-ANCHORS so it goes
    True again — a controlled load's force-off -> dwell -> re-on, published-only."""
    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    coordinator, sub_id, data = await _setup(
        hass,
        [],
        energy_limited=False,
        with_control_switch=False,
        min_runtime_min=30,
        min_off_min=30,
    )
    # A 4 h contiguous block from slot 0 (deadline = run_start + 240 min).
    plan = LoadPlan(
        load_id=sub_id,
        schedule=(True,) * 4,
        planned_energy_wh=1600.0,
        run_hours=(1.0,) * 4,
    )
    durations = (1.0,) * 4
    now = dt_util.utcnow()

    coordinator._maintain_recommendation_deadline(sub_id, data, plan, now, durations)
    d0 = coordinator._load_run_deadline[sub_id]
    assert coordinator._effective_load_active(plan, now) is True

    # Just past the first deadline: published active is capped OFF.
    t1 = d0 + timedelta(minutes=1)
    assert coordinator._effective_load_active(plan, t1) is False
    # Re-evaluated before min_off elapses: still wedged, deadline unchanged.
    coordinator._maintain_recommendation_deadline(sub_id, data, plan, t1, durations)
    assert coordinator._load_run_deadline[sub_id] == d0
    assert coordinator._effective_load_active(plan, t1) is False

    # Once min_off (30 min) has elapsed since the deadline: re-anchor -> active.
    t2 = d0 + timedelta(minutes=31)
    coordinator._maintain_recommendation_deadline(sub_id, data, plan, t2, durations)
    d1 = coordinator._load_run_deadline[sub_id]
    assert d1 > d0  # re-anchored (not wedged for the rest of the block)
    assert coordinator._effective_load_active(plan, t2) is True
    coordinator._cancel_off_timer(sub_id)


async def test_runtime_counter_rec_only_capped_by_deadline(hass):
    """FIX-12: a recommendation-only load's runtime counter follows the
    DEADLINE-CAPPED published active, not the raw plan flag — so a night block does
    not over-credit runtime while the published active is capped off between the
    deadline and its re-anchor."""
    from homeassistant.util import dt as dt_util

    coordinator, sub_id, _ = await _setup(
        hass, [], energy_limited=False, with_control_switch=False, power_entity=None
    )
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()
    assert sub_id not in coordinator._load_charging_active  # recommendation-only
    coordinator._load_plan_active[sub_id] = True  # BM recommends it active

    t0 = dt_util.now()
    # Deadline already expired -> published active capped OFF -> NOT counted.
    coordinator._load_run_deadline[sub_id] = t0 - timedelta(minutes=1)
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=5))
    assert coordinator.load_runtime_minutes(sub_id) == 0.0  # no over-credit

    # Deadline in the future -> published active True -> counts normally.
    coordinator._load_run_deadline[sub_id] = t0 + timedelta(hours=1)
    coordinator._update_load_runtime(t0 + timedelta(minutes=6))  # arms the cursor
    coordinator._update_load_runtime(t0 + timedelta(minutes=9))  # +3 min
    assert abs(coordinator.load_runtime_minutes(sub_id) - 3.0) < 0.01


async def test_load_control_switch_gates_availability(hass):
    """v0.7.17: the per-load 'BM control' switch off -> the load is held
    unavailable (planner drops it), on -> available again; state is persisted."""
    call_log = []
    coordinator, sub_id, _ = await _setup(hass, call_log)

    assert coordinator.load_bm_enabled(sub_id) is True  # default: BM controls it
    states = {s.load_id: s for s in coordinator._get_load_states()}
    assert states[sub_id].available is True

    coordinator.set_load_enabled(sub_id, False)
    assert coordinator.load_bm_enabled(sub_id) is False
    states = {s.load_id: s for s in coordinator._get_load_states()}
    assert states[sub_id].available is False  # held unavailable -> planner drops it
    assert coordinator._persistent_payload()["load_bm_enabled"][sub_id] is False

    coordinator.set_load_enabled(sub_id, True)
    states = {s.load_id: s for s in coordinator._get_load_states()}
    assert states[sub_id].available is True


async def test_load_control_switch_entity_created_and_toggles(hass):
    """The switch entity exists per load, defaults on, and toggling it drives
    the coordinator flag."""
    from homeassistant.helpers import entity_registry as er

    call_log = []
    coordinator, sub_id, _ = await _setup(hass, call_log)
    entry_id = coordinator.entry.entry_id
    eid = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{entry_id}_load_control_{sub_id}"
    )
    assert eid is not None
    assert hass.states.get(eid).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.load_bm_enabled(sub_id) is False
    assert hass.states.get(eid).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": eid}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.load_bm_enabled(sub_id) is True
    assert hass.states.get(eid).state == "on"


# ---------------------------------------------------------------------------
# v0.7.18: real active-runtime counter + reset button
# ---------------------------------------------------------------------------


async def test_runtime_counter_accumulates_real_active_minutes(hass):
    """The counter adds the elapsed time between ticks while the load really
    draws power (> LOAD_RUNTIME_MIN_W), captures the final partial on the off
    transition, and does not advance while idle (v0.7.18)."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()

    t0 = dt_util.now()
    hass.states.async_set(POWER_FEEDBACK, "300")  # > 5 W -> running
    coordinator._update_load_runtime(t0)  # first tick only arms the cursor
    assert coordinator.load_runtime_minutes(sub_id) == 0.0

    coordinator._update_load_runtime(t0 + timedelta(minutes=5))  # +5 min
    assert abs(coordinator.load_runtime_minutes(sub_id) - 5.0) < 0.01

    hass.states.async_set(POWER_FEEDBACK, "1")  # < 5 W -> stops
    coordinator._update_load_runtime(t0 + timedelta(minutes=7))  # final +2 min
    assert abs(coordinator.load_runtime_minutes(sub_id) - 7.0) < 0.01

    coordinator._update_load_runtime(t0 + timedelta(minutes=30))  # idle: no change
    assert abs(coordinator.load_runtime_minutes(sub_id) - 7.0) < 0.01


async def test_runtime_counter_caps_restart_gap(hass):
    """A gap longer than the tick cap (e.g. HA down mid-run) adds nothing, so
    downtime can never inflate the counter; normal ticks resume after it."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()

    t0 = dt_util.now()
    hass.states.async_set(POWER_FEEDBACK, "300")
    coordinator._update_load_runtime(t0)  # arm cursor
    coordinator._update_load_runtime(t0 + timedelta(hours=2))  # > 900 s cap: dropped
    assert coordinator.load_runtime_minutes(sub_id) == 0.0
    coordinator._update_load_runtime(t0 + timedelta(hours=2, minutes=5))  # +5 min
    assert abs(coordinator.load_runtime_minutes(sub_id) - 5.0) < 0.01


async def test_runtime_counter_reset(hass):
    """Reset zeroes the counter and, for a run still in progress, restarts the
    cursor so only post-reset time counts."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()

    t0 = dt_util.now()
    hass.states.async_set(POWER_FEEDBACK, "300")
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=10))
    assert abs(coordinator.load_runtime_minutes(sub_id) - 10.0) < 0.01

    coordinator.reset_load_runtime(sub_id)
    assert coordinator.load_runtime_minutes(sub_id) == 0.0
    # In-progress run kept its cursor -> resumes counting from the reset moment.
    assert sub_id in coordinator._load_run_since
    tr = coordinator._load_run_since[sub_id]
    coordinator._update_load_runtime(tr + timedelta(minutes=3))
    assert abs(coordinator.load_runtime_minutes(sub_id) - 3.0) < 0.01


async def test_runtime_counter_persists_across_restart(hass):
    """Accumulated seconds and the in-progress cursor survive a restart."""
    from homeassistant.helpers.storage import Store
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()

    t0 = dt_util.now()
    hass.states.async_set(POWER_FEEDBACK, "300")
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=8))
    assert abs(coordinator.load_runtime_minutes(sub_id) - 8.0) < 0.01

    captured: dict = {}
    coordinator._store.async_delay_save = lambda f, _d: captured.update(f())
    coordinator._save_persistent_state()
    assert captured["load_runtime_seconds"][sub_id] > 470  # ~480 s
    assert "load_run_since" not in captured  # cursor deliberately NOT persisted

    await coordinator._store.async_save(captured)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert abs(coordinator.load_runtime_minutes(sub_id) - 8.0) < 0.01
    assert sub_id not in coordinator._load_run_since  # cursor NOT restored


async def test_runtime_counter_uses_charging_state_without_power_sensor(hass):
    """When the power-feedback sensor is unavailable, the counter follows BM's
    own charging state instead."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()

    hass.states.async_set(POWER_FEEDBACK, "unavailable")  # feedback down -> fallback
    coordinator._load_charging_active[sub_id] = True
    t0 = dt_util.now()
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=6))
    assert abs(coordinator.load_runtime_minutes(sub_id) - 6.0) < 0.01

    coordinator._load_charging_active[sub_id] = False
    coordinator._update_load_runtime(t0 + timedelta(minutes=9))  # final +3 min
    assert abs(coordinator.load_runtime_minutes(sub_id) - 9.0) < 0.01
    coordinator._update_load_runtime(t0 + timedelta(minutes=20))  # idle: no change
    assert abs(coordinator.load_runtime_minutes(sub_id) - 9.0) < 0.01


async def test_runtime_sensor_and_reset_button_entities(hass):
    """A runtime sensor (minutes) and a reset button are created per load; the
    sensor reflects the counter and the button zeroes it."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()
    entry_id = coordinator.entry.entry_id
    reg = er.async_get(hass)
    sensor_eid = reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry_id}_load_runtime_{sub_id}"
    )
    button_eid = reg.async_get_entity_id(
        "button", DOMAIN, f"{entry_id}_load_runtime_reset_{sub_id}"
    )
    assert sensor_eid is not None
    assert button_eid is not None

    t0 = dt_util.now()
    hass.states.async_set(POWER_FEEDBACK, "300")
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=12))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert float(hass.states.get(sensor_eid).state) == 12.0

    await hass.services.async_call(
        "button", "press", {"entity_id": button_eid}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.load_runtime_minutes(sub_id) == 0.0
    assert float(hass.states.get(sensor_eid).state) == 0.0


async def test_runtime_counter_recommendation_only_load_counts(hass):
    """Fix: a recommendation-only load (no control switch, no power sensor) has
    no charging state, so the counter must fall back to BM's published
    plan-active recommendation instead of staying stuck at 0 forever."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(
        hass, calls, with_control_switch=False, power_entity=None
    )
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()
    assert sub_id not in coordinator._load_charging_active  # recommendation-only

    coordinator._load_plan_active[sub_id] = True  # BM recommends it active
    t0 = dt_util.now()
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=7))
    assert abs(coordinator.load_runtime_minutes(sub_id) - 7.0) < 0.01

    coordinator._load_plan_active[sub_id] = False  # recommendation ends
    coordinator._update_load_runtime(t0 + timedelta(minutes=10))  # final +3 min
    assert abs(coordinator.load_runtime_minutes(sub_id) - 10.0) < 0.01


async def test_runtime_counter_restart_does_not_credit_downtime(hass):
    """A restart must not credit the downtime gap as runtime: the tick cursor is
    not persisted, so the first post-restart tick only re-arms — even if the load
    stopped while HA was down, no phantom minutes are added."""
    from homeassistant.helpers.storage import Store
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, calls)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()

    t0 = dt_util.now()
    hass.states.async_set(POWER_FEEDBACK, "300")
    coordinator._update_load_runtime(t0)
    coordinator._update_load_runtime(t0 + timedelta(minutes=5))  # 5 min banked
    assert abs(coordinator.load_runtime_minutes(sub_id) - 5.0) < 0.01

    # Persist and simulate a restart with a fresh Store.
    captured: dict = {}
    coordinator._store.async_delay_save = lambda f, _d: captured.update(f())
    coordinator._save_persistent_state()
    await coordinator._store.async_save(captured)
    coordinator._load_runtime_seconds.clear()
    coordinator._load_run_since.clear()
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert abs(coordinator.load_runtime_minutes(sub_id) - 5.0) < 0.01
    assert sub_id not in coordinator._load_run_since  # cursor not restored

    # HA was down 8 min and the load is OFF now: the first tick must not add it.
    hass.states.async_set(POWER_FEEDBACK, "1")  # < 5 W -> stopped
    coordinator._update_load_runtime(t0 + timedelta(minutes=13))
    assert abs(coordinator.load_runtime_minutes(sub_id) - 5.0) < 0.01  # no phantom


async def test_load_control_switch_state_survives_restart(hass):
    """v0.7.17: the BM-control flag round-trips through the Store, so a paused
    load stays paused across a restart (the restore path is load-bearing — the
    CHANGELOG promises the state is persisted)."""
    from homeassistant.helpers.storage import Store

    call_log: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, call_log)
    coordinator.set_load_enabled(sub_id, False)

    captured: dict = {}
    coordinator._store.async_delay_save = lambda f, _d: captured.update(f())
    coordinator._save_persistent_state()
    assert captured["load_bm_enabled"][sub_id] is False

    await coordinator._store.async_save(captured)
    coordinator._load_bm_enabled.clear()
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert coordinator.load_bm_enabled(sub_id) is False  # restored, still paused


async def test_removing_load_subentry_cleans_up_its_entities(hass):
    """v0.7.19: per-load entities are scoped to their subentry (config_subentry_id),
    so removing the load subentry removes ALL its entity-registry rows
    automatically; the shared device's config-entry-level entities survive."""
    from homeassistant.helpers import entity_registry as er

    call_log: list[tuple[str, str]] = []
    coordinator, sub_id, _ = await _setup(hass, call_log)
    entry = coordinator.entry
    reg = er.async_get(hass)

    # All five per-load entities exist and are scoped to the load subentry.
    expected = [
        ("binary_sensor", f"load_{sub_id}"),
        ("binary_sensor", f"load_power_warning_{sub_id}"),
        ("switch", f"load_control_{sub_id}"),
        ("sensor", f"load_runtime_{sub_id}"),
        ("button", f"load_runtime_reset_{sub_id}"),
    ]
    eids = {}
    for platform, key in expected:
        eid = reg.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
        assert eid is not None, f"{key} not created"
        assert reg.async_get(eid).config_subentry_id == sub_id, f"{key} not scoped"
        eids[key] = eid

    # Config-entry-level entities stay at subentry None (not scoped to the load).
    base_eids = [
        e.entity_id
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
        if e.config_subentry_id is None
    ]
    assert base_eids  # e.g. the main SOC/threshold sensors, vacation switch

    # Remove the load subentry -> HA clears exactly its subentry-scoped rows.
    hass.config_entries.async_remove_subentry(entry, sub_id)
    await hass.async_block_till_done()

    for key, eid in eids.items():
        assert reg.async_get(eid) is None, f"{key} should be removed with the load"
    assert [
        e
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
        if e.config_subentry_id == sub_id
    ] == []
    # Config-entry-level entities untouched (shared device survives).
    for eid in base_eids:
        assert reg.async_get(eid) is not None


async def test_setup_rehomes_legacy_unscoped_entity(hass):
    """Migration: an entity left at config_subentry_id=None by a pre-v0.7.19
    install is re-homed to its load subentry on the next setup — updated in
    place (same entity_id, no duplicate), so live installs migrate transparently."""
    from homeassistant.helpers import entity_registry as er

    coordinator, sub_id, _ = await _setup(hass, [])
    entry = coordinator.entry
    reg = er.async_get(hass)
    unique_id = f"{entry.entry_id}_load_runtime_{sub_id}"
    eid = reg.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert eid and reg.async_get(eid).config_subentry_id == sub_id

    # Simulate the old, unscoped state.
    reg.async_update_entity(eid, config_subentry_id=None)
    assert reg.async_get(eid).config_subentry_id is None

    # Reload -> the platform re-adds with config_subentry_id -> row re-homed.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert reg.async_get_entity_id("sensor", DOMAIN, unique_id) == eid  # same row
    assert reg.async_get(eid).config_subentry_id == sub_id  # re-homed
    matches = [
        e
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
        if e.unique_id == unique_id
    ]
    assert len(matches) == 1  # no duplicate created


async def test_appliance_window_removed_when_opportunistic_disabled(hass):
    """v0.7.19: subentry-scoping auto-removes on subentry deletion, but toggling
    an appliance's 'opportunistic' OFF (subentry kept) must still drop its stale
    start-window entity — mirrors the load power-warning cleanup."""
    from homeassistant.config_entries import ConfigSubentryData
    from homeassistant.helpers import entity_registry as er

    from custom_components.battery_manager.const import (
        CONF_APPLIANCE_DETECTION_ENTITY,
        CONF_APPLIANCE_OPPORTUNISTIC,
        CONF_APPLIANCE_POWER_THRESHOLD_W,
        CONF_APPLIANCE_RUN_DURATION_H,
        CONF_APPLIANCE_RUN_ENERGY_WH,
        SUBENTRY_TYPE_APPLIANCE,
    )

    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.dw_power", "0")

    appliance_data = {
        CONF_APPLIANCE_DETECTION_ENTITY: "sensor.dw_power",
        CONF_APPLIANCE_POWER_THRESHOLD_W: 20.0,
        CONF_APPLIANCE_RUN_ENERGY_WH: 1000.0,
        CONF_APPLIANCE_RUN_DURATION_H: 2.0,
        CONF_APPLIANCE_OPPORTUNISTIC: True,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=BASE_DATA,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=appliance_data,
                subentry_type=SUBENTRY_TYPE_APPLIANCE,
                title="Dishwasher",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    reg = er.async_get(hass)
    sub_id = next(iter(entry.subentries))
    uid = f"{entry.entry_id}_appliance_{sub_id}"
    eid = reg.async_get_entity_id("binary_sensor", DOMAIN, uid)
    assert eid is not None  # created while opportunistic
    assert reg.async_get(eid).config_subentry_id == sub_id  # scoped

    # Toggle opportunistic OFF (subentry kept) and reload.
    hass.config_entries.async_update_subentry(
        entry,
        entry.subentries[sub_id],
        data={**appliance_data, CONF_APPLIANCE_OPPORTUNISTIC: False},
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, uid) is None  # dropped


# ---------------------------------------------------------------------------
# F-PREDRAIN T9: contiguous night-block execution via the frozen deadline
# (docs/F-PREDRAIN.md §5). A pre-drain "make room" run is a multi-hour
# contiguous block for a continuous load; the F-SUBHOUR executor must deliver
# exactly the planned hours, ignore a mid-run plan extension, and honour the
# split dwell (min_runtime on OFF, min_off on re-on).
# ---------------------------------------------------------------------------


def _night_plan(sub_id, hours):
    """A LoadPlan for a `hours`-slot contiguous run from slot 0."""
    from custom_components.battery_manager.core.model import LoadPlan

    return LoadPlan(
        load_id=sub_id,
        schedule=(True,) * hours,
        planned_energy_wh=400.0 * hours,
        run_hours=(1.0,) * hours,
    )


async def test_night_block_runs_exactly_planned_hours_then_force_off(hass):
    """(a) A 3 h contiguous planned night block freezes a +180 min deadline,
    stays on until it, and is force-switched OFF at the deadline even though the
    plan still wants it on (F-PREDRAIN §5 T9a / F-SUBHOUR R8)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30, min_off_min=30
    )
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    now = dt_util.utcnow()
    durations = (1.0, 1.0, 1.0)
    result = SimpleNamespace(load_plans=[_night_plan(sub_id, 3)])

    # ON edge: the 3 h block freezes a +180 min off-deadline.
    await coordinator._apply_load_switching(result, now, durations)
    await hass.async_block_till_done()
    assert ("turn_on", PLUG) in calls
    off_at = coordinator._load_run_deadline[sub_id]
    assert 179.0 <= (off_at - now).total_seconds() / 60.0 <= 181.0
    assert coordinator._load_charging_active[sub_id] is True

    # Just before the deadline the plan still wants it on: no premature off.
    calls.clear()
    await coordinator._apply_load_switching(
        result, now + timedelta(minutes=179), durations
    )
    await hass.async_block_till_done()
    assert calls == []
    assert coordinator._load_charging_active[sub_id] is True

    # Past the deadline: force off despite the plan still wanting it on.
    calls.clear()
    await coordinator._apply_load_switching(
        result, off_at + timedelta(minutes=1), durations
    )
    await hass.async_block_till_done()
    assert ("turn_off", PLUG) in calls or ("turn_off", ENABLE) in calls
    assert sub_id not in coordinator._load_run_deadline
    coordinator._cancel_off_timer(sub_id)


async def test_extended_plan_still_force_offs_and_min_off_gates_re_on(hass):
    """(b) If the plan EXTENDS mid-run, the frozen deadline must not move (no
    endless run); at the ORIGINAL deadline the load force-offs, and the min_off
    dwell then blocks the immediate re-on (F-PREDRAIN §5 T9b)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30, min_off_min=45
    )
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    now = dt_util.utcnow()
    durations = (1.0,) * 6

    # ON edge with a 2 h block: deadline at +120 min.
    await coordinator._apply_load_switching(
        SimpleNamespace(load_plans=[_night_plan(sub_id, 2)]), now, durations
    )
    await hass.async_block_till_done()
    off_at = coordinator._load_run_deadline[sub_id]
    assert 119.0 <= (off_at - now).total_seconds() / 60.0 <= 121.0

    # Plan extends to 4 h mid-run: the frozen deadline must NOT move.
    extended = SimpleNamespace(load_plans=[_night_plan(sub_id, 4)])
    calls.clear()
    await coordinator._apply_load_switching(
        extended, now + timedelta(minutes=60), durations
    )
    await hass.async_block_till_done()
    assert coordinator._load_run_deadline[sub_id] == off_at  # unchanged
    assert calls == []  # still running, no switch

    # Past the ORIGINAL deadline: force off despite the extended plan.
    off_now = off_at + timedelta(minutes=1)
    calls.clear()
    await coordinator._apply_load_switching(extended, off_now, durations)
    await hass.async_block_till_done()
    assert ("turn_off", PLUG) in calls or ("turn_off", ENABLE) in calls
    assert sub_id not in coordinator._load_run_deadline

    # min_off (45) blocks the immediate re-on even though the plan wants it on.
    calls.clear()
    await coordinator._apply_load_switching(
        extended, off_now + timedelta(minutes=10), durations
    )
    await hass.async_block_till_done()
    assert calls == []

    # After the min_off dwell elapses the re-on is allowed.
    calls.clear()
    await coordinator._apply_load_switching(
        extended, off_now + timedelta(minutes=46), durations
    )
    await hass.async_block_till_done()
    assert ("turn_on", PLUG) in calls or ("turn_on", ENABLE) in calls
    coordinator._cancel_off_timer(sub_id)


async def test_plan_flap_does_not_switch_off_before_min_runtime(hass):
    """(c) A plan-inactive flap shortly after the block starts must NOT switch the
    load off before its minimum ON time (min_runtime) has elapsed; once it has, a
    still-inactive plan does switch it off (F-PREDRAIN §5 T9c / F-SUBHOUR R14)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, energy_limited=False, min_runtime_min=30, min_off_min=30
    )
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    now = dt_util.utcnow()
    durations = (1.0, 1.0, 1.0)

    # Start the 3 h block.
    await coordinator._apply_load_switching(
        SimpleNamespace(load_plans=[_night_plan(sub_id, 3)]), now, durations
    )
    await hass.async_block_till_done()
    assert coordinator._load_charging_active[sub_id] is True

    # Build the "plan inactive" result explicitly (frozen dataclass -> new one).
    from custom_components.battery_manager.core.model import LoadPlan

    inactive = SimpleNamespace(
        load_plans=[
            LoadPlan(
                load_id=sub_id,
                schedule=(False,),
                planned_energy_wh=0.0,
                run_hours=(0.0,),
            )
        ]
    )

    # Flap inactive 10 min in (< min_runtime 30): must NOT switch off.
    calls.clear()
    await coordinator._apply_load_switching(
        inactive, now + timedelta(minutes=10), durations
    )
    await hass.async_block_till_done()
    assert calls == []
    assert coordinator._load_charging_active[sub_id] is True

    # After min_runtime, a still-inactive plan switches it off.
    calls.clear()
    await coordinator._apply_load_switching(
        inactive, now + timedelta(minutes=31), durations
    )
    await hass.async_block_till_done()
    assert ("turn_off", PLUG) in calls or ("turn_off", ENABLE) in calls
    coordinator._cancel_off_timer(sub_id)


# ---------------------------------------------------------------------------
# F-PLANNER-HONESTY F1: learned planning power (docs/F-PLANNER-HONESTY.md
# R2/R3/R6). The run-max of the accepted-sample EMA survives the run (and
# restarts), so an OFF load is planned at its real power instead of the
# nominal; the v0.6.2 standby bar stays the single sample gate.
# ---------------------------------------------------------------------------


async def test_learned_power_is_run_max_of_ema(hass):
    """R2: the learned value is the RUN-MAX of the accepted-sample EMA — an
    end-of-charge taper cannot erode it — and it keeps serving after the run
    ends (unlike the live EMA, which is deliberately discarded)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_charging_active[sub_id] = True  # BM-initiated charge
    hass.states.async_set(POWER_FEEDBACK, "505")

    states = coordinator._get_load_states()
    assert states[0].measured_power_w == 505.0
    # R2a: the first accepted sample seeds the EMA only — nothing learned yet.
    assert sub_id not in coordinator._load_learned_power_w
    states = coordinator._get_load_states()  # second accepted sample
    assert coordinator._load_learned_power_w[sub_id] == 505.0
    assert states[0].learned_power_w == 505.0

    # End-of-charge taper (still above the standby bar): the EMA sinks, the
    # learned run-max does not.
    hass.states.async_set(POWER_FEEDBACK, "320")
    states = coordinator._get_load_states()
    assert states[0].measured_power_w < 505.0
    assert coordinator._load_learned_power_w[sub_id] == 505.0

    # Run over: the live EMA is dropped (v0.5.1), the learned value serves —
    # an OFF load now plans at its real 505 W, not the nominal 300 W.
    coordinator._load_charging_active[sub_id] = False
    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None
    assert sub_id not in coordinator._load_run_power_max  # run tracker ends
    assert states[0].learned_power_w == 505.0
    from custom_components.battery_manager.core.model import SurplusLoad

    load = SurplusLoad(
        load_id=sub_id, name="t", nominal_power_w=300.0, energy_limited=True
    )
    assert states[0].planning_power_w(load) == 505.0


async def test_learned_power_last_run_wins(hass):
    """R2: the store holds the CURRENT run's max once it has enough accepted
    samples — a later, genuinely lower-powered run replaces the old value."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "505")
    coordinator._get_load_states()
    coordinator._get_load_states()  # second accepted sample (R2a)
    assert coordinator._load_learned_power_w[sub_id] == 505.0

    coordinator._load_charging_active[sub_id] = False
    coordinator._get_load_states()  # run 1 over

    coordinator._load_charging_active[sub_id] = True  # run 2, lower power
    hass.states.async_set(POWER_FEEDBACK, "400")
    coordinator._get_load_states()
    coordinator._get_load_states()  # second accepted sample (R2a)
    assert coordinator._load_learned_power_w[sub_id] == 400.0  # last run wins


async def test_first_sample_spike_is_not_learned(hass):
    """R2a: the EMA seeds VERBATIM from a run's first accepted sample, so the
    run-max tracker starts at the SECOND — a single start-up spike run learns
    nothing, and a spike followed by settled samples learns the EMA-damped
    band (a fresh settled run then replaces it entirely, last run wins)."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)

    # A run consisting of ONE spiked sample learns nothing at all.
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "900")
    coordinator._get_load_states()
    coordinator._load_charging_active[sub_id] = False
    coordinator._get_load_states()  # run over after a single sample
    assert sub_id not in coordinator._load_learned_power_w

    # Spike + settled samples: the spike seeds the EMA but is never learned
    # verbatim; the learned run-max is the EMA-damped second sample
    # (0.3*505 + 0.7*900 = 781.5), decaying — not 900.
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "900")
    coordinator._get_load_states()
    hass.states.async_set(POWER_FEEDBACK, "505")
    for _ in range(3):
        coordinator._get_load_states()
    assert coordinator._load_learned_power_w[sub_id] < 900.0
    assert abs(coordinator._load_learned_power_w[sub_id] - 781.5) < 0.1
    coordinator._load_charging_active[sub_id] = False
    coordinator._get_load_states()  # run over

    # The next clean settled run replaces the damped value: learned ~505.
    coordinator._load_charging_active[sub_id] = True
    coordinator._get_load_states()
    coordinator._get_load_states()
    assert coordinator._load_learned_power_w[sub_id] == 505.0


async def test_standby_sample_never_learns(hass):
    """R2/R6: only samples past the v0.6.2 standby bar feed the learned value
    (the single gate) — an idle 19.6 W of a 400 W load learns nothing, so
    behaviour without accepted samples stays bit-identical to v0.8.2."""
    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_w=400.0)
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "19.6")

    states = coordinator._get_load_states()
    assert states[0].measured_power_w is None
    assert sub_id not in coordinator._load_learned_power_w
    assert states[0].learned_power_w is None

    coordinator._load_charging_active[sub_id] = False
    states = coordinator._get_load_states()
    assert sub_id not in coordinator._load_learned_power_w


async def test_learned_power_persists_and_prunes_vanished_loads(hass):
    """R3: `load_learned_power` round-trips through the Store; entries whose
    load subentry vanished are dropped on restore (a re-created load must not
    inherit another device's power)."""
    from homeassistant.helpers.storage import Store

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_learned_power_w = {sub_id: 505.4, "vanished_load": 300.0}

    captured: dict = {}
    coordinator._store.async_delay_save = lambda f, _d: captured.update(f())
    coordinator._save_persistent_state()
    assert captured["load_learned_power"] == {sub_id: 505.4, "vanished_load": 300.0}

    await coordinator._store.async_save(captured)
    coordinator._load_learned_power_w.clear()
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert coordinator._load_learned_power_w == {sub_id: 505.4}  # ghost pruned


# ---------------------------------------------------------------------------
# F-EXECUTOR-GUARDS G1: dwell-exempt target-SOC stop (docs/F-EXECUTOR-GUARDS.md
# R1-R4). min_runtime protects relays from short cycling; a charge-enable gate
# switches no load current path, so the target stop must not overshoot through
# the dwell. min_off stays fully armed afterwards.
# ---------------------------------------------------------------------------


def _inactive_result(sub_id):
    from types import SimpleNamespace

    from custom_components.battery_manager.core.model import LoadPlan

    return SimpleNamespace(
        load_plans=[
            LoadPlan(
                load_id=sub_id,
                schedule=(False,),
                planned_energy_wh=0.0,
                run_hours=(0.0,),
            )
        ]
    )


async def test_target_soc_stop_is_dwell_exempt_with_enable_gate(hass):
    """R1/R4a: energy-limited + charge-enable + soc >= target: the plan-driven
    OFF executes although min_runtime has NOT elapsed."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, min_runtime_min=30, min_off_min=30
    )
    now = dt_util.utcnow()
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(FOSSI_SOC, "92")  # >= target 90
    coordinator._load_charging_active[sub_id] = True
    coordinator._last_load_switch[sub_id] = now - timedelta(minutes=5)  # < 30
    calls.clear()

    await coordinator._apply_load_switching(_inactive_result(sub_id), now)
    await hass.async_block_till_done()
    assert ("turn_off", ENABLE) in calls  # stopped despite the ON-dwell


async def test_target_soc_stop_below_target_keeps_dwell(hass):
    """R4b: the same load below its target keeps the full ON-dwell."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, min_runtime_min=30, min_off_min=30
    )
    now = dt_util.utcnow()
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(FOSSI_SOC, "40")  # < target 90
    coordinator._load_charging_active[sub_id] = True
    coordinator._last_load_switch[sub_id] = now - timedelta(minutes=5)
    calls.clear()

    await coordinator._apply_load_switching(_inactive_result(sub_id), now)
    await hass.async_block_till_done()
    assert calls == []  # dwell still blocks the OFF


async def test_target_soc_stop_plug_only_keeps_dwell(hass):
    """R3/R4c: a plug-only energy-limited load (no charge-enable) keeps the
    full dwell even at target — the plug relay is what min_runtime protects."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, min_runtime_min=30, min_off_min=30, charge_enable=None
    )
    now = dt_util.utcnow()
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(FOSSI_SOC, "92")  # >= target 90
    coordinator._load_charging_active[sub_id] = True
    coordinator._last_load_switch[sub_id] = now - timedelta(minutes=5)
    calls.clear()

    await coordinator._apply_load_switching(_inactive_result(sub_id), now)
    await hass.async_block_till_done()
    assert calls == []  # dwell still blocks


async def test_target_soc_stop_min_off_still_gates_re_on(hass):
    """R2/R4d: after a dwell-exempt target stop, the confirmed switch stamped
    the dwell — an immediate plan-driven re-on is blocked by min_off."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    from custom_components.battery_manager.core.model import LoadPlan

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(
        hass, calls, min_runtime_min=30, min_off_min=30
    )
    now = dt_util.utcnow()
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(ENABLE, "on")
    hass.states.async_set(FOSSI_SOC, "92")
    coordinator._load_charging_active[sub_id] = True
    coordinator._last_load_switch[sub_id] = now - timedelta(minutes=5)
    calls.clear()
    await coordinator._apply_load_switching(_inactive_result(sub_id), now)
    await hass.async_block_till_done()
    assert ("turn_off", ENABLE) in calls  # target stop executed

    # SOC hovers at the target and the plan books again: min_off blocks.
    active = SimpleNamespace(
        load_plans=[
            LoadPlan(
                load_id=sub_id,
                schedule=(True,),
                planned_energy_wh=150.0,
                run_hours=(0.5,),
            )
        ]
    )
    calls.clear()
    await coordinator._apply_load_switching(active, now + timedelta(minutes=5))
    await hass.async_block_till_done()
    assert calls == []  # min_off dwell gates the re-on (no flapping)


# ---------------------------------------------------------------------------
# F-EXECUTOR-GUARDS G2: stale-SOC guard (docs/F-EXECUTOR-GUARDS.md R5-R9).
# The fossibot integration serves cached SOC with fresh timestamps; a SOC
# frozen for STALE_LOAD_SOC_MIN minutes while demonstrably charging latches
# the load unavailable until the sensor reports a different value.
# ---------------------------------------------------------------------------


async def test_stale_soc_latches_and_warns_once(hass, caplog):
    """R5/R6/R9a: frozen SOC + active charging above the bar for the threshold
    time -> available=False, WARNING exactly once (change-gated)."""
    import logging

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "505")
    hass.states.async_set(FOSSI_SOC, "40")

    states = coordinator._get_load_states()
    assert states[0].available is True  # evidence armed, not latched yet
    # Backdate the evidence past the threshold (the established test pattern
    # for dwell/timer clocks in this suite).
    coordinator._load_soc_frozen[sub_id] = (
        40.0,
        dt_util.utcnow() - timedelta(minutes=13),
    )
    with caplog.at_level(logging.WARNING):
        caplog.clear()
        states = coordinator._get_load_states()
        assert states[0].available is False  # latched -> planner drops it
        warnings = [r for r in caplog.records if "STALE" in r.message]
        assert len(warnings) == 1
        states = coordinator._get_load_states()  # next cycle: still latched
        assert states[0].available is False
        warnings = [r for r in caplog.records if "STALE" in r.message]
        assert len(warnings) == 1  # not logged again every cycle


async def test_stale_soc_unlatches_on_changed_reading(hass, caplog):
    """R6/R9b: a DIFFERENT SOC value unlatches (charging or not), logs INFO
    once, and the load is schedulable again."""
    import logging

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "505")
    hass.states.async_set(FOSSI_SOC, "40")
    coordinator._get_load_states()
    coordinator._load_soc_frozen[sub_id] = (
        40.0,
        dt_util.utcnow() - timedelta(minutes=13),
    )
    assert coordinator._get_load_states()[0].available is False  # latched

    hass.states.async_set(FOSSI_SOC, "41")  # the sensor moves again
    with caplog.at_level(logging.INFO):
        caplog.clear()
        states = coordinator._get_load_states()
        assert states[0].available is True
        assert sub_id not in coordinator._load_soc_stale
        infos = [r for r in caplog.records if "stale latch cleared" in r.message]
        assert len(infos) == 1


async def test_stale_soc_taper_and_idle_reset_evidence(hass):
    """R7/R9c: a taper below the standby bar or a charging stop RESETS the
    evidence clock — no false positive at the end of a charge."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls)
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(POWER_FEEDBACK, "505")
    hass.states.async_set(FOSSI_SOC, "40")
    coordinator._get_load_states()
    backdated = (40.0, dt_util.utcnow() - timedelta(minutes=13))

    # Taper below the bar: the (backdated) evidence is discarded, no latch.
    coordinator._load_soc_frozen[sub_id] = backdated
    hass.states.async_set(POWER_FEEDBACK, "19")  # < bar (75 W for 300 W load)
    states = coordinator._get_load_states()
    assert states[0].available is True
    assert sub_id not in coordinator._load_soc_frozen  # clock reset

    # Charging stopped: same reset, even at full feedback power.
    hass.states.async_set(POWER_FEEDBACK, "505")
    coordinator._load_charging_active[sub_id] = False
    coordinator._load_soc_frozen[sub_id] = backdated
    states = coordinator._get_load_states()
    assert states[0].available is True
    assert sub_id not in coordinator._load_soc_frozen


async def test_stale_soc_needs_both_signals(hass):
    """R7/R9d: without a power-feedback entity (or without a SOC reading) the
    guard has no evidence and never latches."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator, sub_id, _data = await _setup(hass, calls, power_entity=None)
    coordinator._load_charging_active[sub_id] = True
    hass.states.async_set(FOSSI_SOC, "40")
    coordinator._load_soc_frozen[sub_id] = (
        40.0,
        dt_util.utcnow() - timedelta(minutes=13),
    )
    states = coordinator._get_load_states()
    assert states[0].available is True  # no power signal -> no latch
    assert sub_id not in coordinator._load_soc_frozen

    # SOC reading absent (device dropout): equally no evidence, no latch.
    calls2: list[tuple[str, str]] = []
    coordinator2, sub_id2, _ = await _setup(hass, calls2)
    coordinator2._load_charging_active[sub_id2] = True
    hass.states.async_set(POWER_FEEDBACK, "505")
    hass.states.async_set(FOSSI_SOC, "unavailable")
    coordinator2._load_soc_frozen[sub_id2] = (
        40.0,
        dt_util.utcnow() - timedelta(minutes=13),
    )
    states = coordinator2._get_load_states()
    assert states[0].available is True
    assert sub_id2 not in coordinator2._load_soc_frozen
