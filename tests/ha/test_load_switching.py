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
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_POWER_W,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_TARGET_SOC,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
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
        CONF_LOAD_ENERGY_LIMITED: True,
        CONF_LOAD_CAPACITY_WH: 2000.0,
        CONF_LOAD_TARGET_SOC: 90.0,
        CONF_LOAD_SOC_ENTITY: FOSSI_SOC,
        CONF_LOAD_POWER_ENTITY: POWER_FEEDBACK,
    }
    if with_control_switch:
        load_data |= {
            CONF_LOAD_CONTROL_SWITCH: PLUG,
            CONF_LOAD_CHARGE_ENABLE: ENABLE,
            CONF_LOAD_INPUT_OFF_POLICY: policy,
        }
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
    coordinator._update_power_warnings(result, t0)
    assert coordinator._load_power_warning.get(sub_id, False) is False

    coordinator._update_power_warnings(result, t0 + timedelta(minutes=31))
    assert coordinator._load_power_warning[sub_id] is True

    hass.states.async_set(POWER_FEEDBACK, "395")  # back to normal
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=40))
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
    coordinator._update_power_warnings(result, t0)
    hass.states.async_set(POWER_FEEDBACK, "400")  # compressor back on
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=10))
    hass.states.async_set(POWER_FEEDBACK, "150")  # next defrost
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=45))
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=60))
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
    coordinator._update_power_warnings(result, t0)
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=45))
    assert coordinator._load_power_warning.get(sub_id, False) is False

    # With an active recommendation the same deviation IS a problem.
    active_plan = SimpleNamespace(load_id=sub_id, active_now=True)
    result = SimpleNamespace(load_plans=[active_plan])
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=50))
    coordinator._update_power_warnings(result, t0 + timedelta(minutes=81))
    assert coordinator._load_power_warning[sub_id] is True
