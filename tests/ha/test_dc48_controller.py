"""Tests for the R2 battery-voltage controller of the manual 48 V PSU.

F-N3 / v0.7.7 (docs/DC_TOPOLOGY.md §6). While the 48 V PSU is in manual mode
AND a battery-voltage sensor is configured, the PSU is cycled by voltage with
asymmetric hysteresis (ON <= on_voltage, OFF >= off_voltage) instead of being
held permanently on. Operator answer A: the R3 switch is the *sole* mode truth
for the regulated PSU — a controller-caused OFF must never exit manual mode.
"""

from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_PSU48_CTRL_LOG_ONLY,
    CONF_PSU48_OFF_VOLTAGE_V,
    CONF_PSU48_ON_VOLTAGE_V,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC48_SWITCH,
    DOMAIN,
)

DC48 = "switch.psu_48v"
BATT_V = "sensor.batt_voltage"

ON_V = 49.56
OFF_V = 49.8

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
    CONF_SUPPORT_DC48_SWITCH: DC48,
    CONF_BATTERY_VOLTAGE_ENTITY: BATT_V,
    CONF_PSU48_ON_VOLTAGE_V: ON_V,
    CONF_PSU48_OFF_VOLTAGE_V: OFF_V,
    CONF_PSU48_CTRL_LOG_ONLY: False,  # regulating (not shakedown)
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


async def _setup(hass, call_log, *, extra_data=None, remove=()):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    _register_switch_services(hass, call_log)

    data = {**ENTRY_DATA, **(extra_data or {})}
    for key in remove:
        data.pop(key, None)
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, title="Battery Manager", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


def _arm_manual(coordinator):
    """Put dc48 into manual mode as the R3 switch would (without actuating)."""
    coordinator._support_manual["dc48"] = True
    coordinator._support_state["dc48"] = True


async def test_low_voltage_switches_on_after_dwell(hass):
    """Below the on-voltage, sustained past the ON dwell, turns the PSU on."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "49.40")  # below on-voltage
    calls.clear()
    t0 = dt_util.now()

    # First cycle: dwell not yet elapsed -> hold.
    coordinator._run_dc48_controller(t0)
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(DC48).state == "off"
    assert coordinator._dc48_ctrl_diag["decision"] == "hold"

    # Second cycle past the ON dwell -> switch on. The diag is snapshotted
    # synchronously: a debounced refresh (state-change listener) re-runs the
    # controller during async_block_till_done and would overwrite it.
    coordinator._run_dc48_controller(t0 + timedelta(seconds=61))
    assert coordinator._dc48_ctrl_diag["decision"] == "on"
    await hass.async_block_till_done()
    assert ("turn_on", DC48) in calls
    assert hass.states.get(DC48).state == "on"


async def test_high_voltage_switches_off_after_dwell(hass):
    """At/above the off-voltage, sustained past the OFF dwell, turns it off."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "on")
    hass.states.async_set(BATT_V, "49.90")  # above off-voltage
    calls.clear()
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)  # dwell not elapsed
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(DC48).state == "on"

    coordinator._run_dc48_controller(t0 + timedelta(seconds=301))
    await hass.async_block_till_done()
    assert ("turn_off", DC48) in calls
    assert hass.states.get(DC48).state == "off"


async def test_hysteresis_band_holds(hass):
    """Between on- and off-voltage the controller neither switches nor arms."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "on")
    hass.states.async_set(BATT_V, "49.70")  # in the band
    calls.clear()

    coordinator._run_dc48_controller(dt_util.now() + timedelta(hours=1))
    await hass.async_block_till_done()
    assert calls == []
    assert coordinator._dc48_ctrl_diag["decision"] == "hold"
    assert coordinator._dc48_ctrl_diag["reason"] == "hysteresis_band"
    assert coordinator._dc48_below_since is None
    assert coordinator._dc48_above_since is None


async def test_controller_off_does_not_exit_manual(hass):
    """THE key reconciliation (operator answer A): after the controller turns
    the regulated PSU off, the manual-override detector must keep it in manual
    mode — only the R3 switch may exit."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "on")
    hass.states.async_set(BATT_V, "49.90")
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)  # arm the OFF dwell timer
    coordinator._run_dc48_controller(t0 + timedelta(seconds=301))
    await hass.async_block_till_done()
    assert hass.states.get(DC48).state == "off"  # controller turned it off

    # Next cycle's manual-override detection must NOT read this as an exit.
    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is True  # still manual
    assert coordinator._support_state["dc48"] is False  # reflects physical off


async def test_controller_off_survives_log_only_flip(hass):
    """Review round 2 (high): a controller-caused OFF must keep manual mode even
    after the operator flips log_only back on (making the controller no longer
    'regulating'). The persisted caused-off flag, NOT the live log_only, is the
    mode truth here — otherwise toggling a diagnostic setting drops the override
    (operator answer A)."""
    calls: list[tuple[str, str]] = []
    # log_only True = the post-flip state; the controller is not "regulating".
    coordinator = await _setup(hass, calls, extra_data={CONF_PSU48_CTRL_LOG_ONLY: True})
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    coordinator._support_state["dc48"] = False
    coordinator._dc48_ctrl_caused_off = True  # controller turned it off pre-flip

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is True  # stays manual
    assert coordinator._support_state["dc48"] is False


async def test_caused_off_flag_persists_across_reload(hass):
    """The caused-off flag must survive the entry reload that a config change
    (e.g. flipping log_only) triggers — else the reload re-introduces the bug."""
    from homeassistant.helpers.storage import Store

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    await coordinator._store.async_save(
        {
            "support_manual": {"dc24": False, "dc48": True},
            "support_state": {"dc24": False, "dc48": False},
            "dc48_ctrl_caused_off": True,
        }
    )
    coordinator._support_manual = {"dc24": False, "dc48": False}
    coordinator._support_state = {"dc24": False, "dc48": False}
    coordinator._dc48_ctrl_caused_off = False
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)

    await coordinator.async_load_persistent_state()
    assert coordinator._support_manual["dc48"] is True
    assert coordinator._dc48_ctrl_caused_off is True


async def test_removing_voltage_sensor_does_not_trap_manual(hass):
    """Review round 3 (medium): if the controller had turned the PSU off
    (caused_off) and the operator then removes the voltage sensor, the exemption
    must NOT keep the PSU trapped off in manual forever — with no sensor the
    controller can't cycle it, so a physical off falls back to plain F-N2 exit."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, remove=(CONF_BATTERY_VOLTAGE_ENTITY,))
    _arm_manual(coordinator)
    coordinator._support_state["dc48"] = False
    coordinator._dc48_ctrl_caused_off = True  # left over from before removal
    hass.states.async_set(DC48, "off")

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is False  # exits, not trapped
    assert coordinator._dc48_ctrl_caused_off is False


async def test_idle_controller_clears_caused_off(hass):
    """Defense-in-depth (review round 3): when the controller is not engaged it
    drops the caused-off record, so it can't keep the exemption alive."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, remove=(CONF_BATTERY_VOLTAGE_ENTITY,))
    _arm_manual(coordinator)
    coordinator._dc48_ctrl_caused_off = True

    coordinator._run_dc48_controller(dt_util.now())
    assert coordinator._dc48_ctrl_caused_off is False
    assert coordinator._dc48_ctrl_diag["active"] is False


async def test_flush_persists_state_immediately(hass):
    """Review round 3 (low): the unload flush must write the caused-off / manual
    record synchronously, so a reload can't beat the 10 s delayed save."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    coordinator._dc48_ctrl_caused_off = True

    await coordinator.async_flush_persistent_state()
    data = await coordinator._store.async_load()
    assert data["dc48_ctrl_caused_off"] is True
    assert data["support_manual"]["dc48"] is True


async def test_external_off_still_exits_manual_in_log_only(hass):
    """During the log-only shakedown the controller never switches, so a hard
    OFF is a genuine operator action and F-N2 exit behaviour is preserved."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_PSU48_CTRL_LOG_ONLY: True})
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")  # operator flipped it at the wall

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is False  # exits manual
    assert coordinator._support_state["dc48"] is False


async def test_log_only_does_not_actuate_but_records_decision(hass):
    """Log-only mode: the decision is computed and surfaced, but the PSU is
    not switched (shakedown)."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_PSU48_CTRL_LOG_ONLY: True})
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "on")
    hass.states.async_set(BATT_V, "49.90")
    calls.clear()
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)  # arm the OFF dwell timer
    coordinator._run_dc48_controller(t0 + timedelta(seconds=301))
    assert coordinator._dc48_ctrl_diag["decision"] == "off"  # snapshot pre-await
    assert coordinator._dc48_ctrl_diag["mode"] == "log_only"
    await hass.async_block_till_done()
    assert calls == []  # never actuated
    assert hass.states.get(DC48).state == "on"


async def test_inactive_when_not_manual(hass):
    """In auto mode the controller is hands-off and resets its timers."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._support_manual["dc48"] = False
    coordinator._dc48_below_since = dt_util.now()
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "49.40")
    calls.clear()

    coordinator._run_dc48_controller(dt_util.now() + timedelta(minutes=30))
    await hass.async_block_till_done()
    assert calls == []
    assert coordinator._dc48_ctrl_diag["active"] is False
    assert coordinator._dc48_below_since is None


async def test_inactive_without_voltage_entity(hass):
    """Without a battery-voltage sensor the pre-R2 behaviour is preserved:
    manual 48 V just stays on, the controller does nothing."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, remove=(CONF_BATTERY_VOLTAGE_ENTITY,))
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "on")
    calls.clear()

    coordinator._run_dc48_controller(dt_util.now() + timedelta(hours=2))
    await hass.async_block_till_done()
    assert calls == []
    assert coordinator._dc48_ctrl_diag["active"] is False


async def test_failsafe_on_after_invalid_reading(hass):
    """A missing/implausible reading for long enough forces the PSU on."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "unavailable")
    calls.clear()
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)  # arm the fail-safe timer
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(DC48).state == "off"

    coordinator._run_dc48_controller(t0 + timedelta(minutes=11))
    assert coordinator._dc48_ctrl_diag["reason"] == "failsafe_no_reading"  # pre-await
    await hass.async_block_till_done()
    assert ("turn_on", DC48) in calls
    assert hass.states.get(DC48).state == "on"


async def test_valid_reading_clears_failsafe_timer(hass):
    """A single valid reading between invalid ones resets the fail-safe arm."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "unavailable")
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)
    assert coordinator._dc48_invalid_since is not None

    hass.states.async_set(BATT_V, "49.70")  # valid, band
    coordinator._run_dc48_controller(t0 + timedelta(minutes=5))
    assert coordinator._dc48_invalid_since is None

    # Invalid again much later must NOT immediately fail-safe (timer restarts).
    hass.states.async_set(BATT_V, "unavailable")
    coordinator._run_dc48_controller(t0 + timedelta(minutes=20))
    await hass.async_block_till_done()
    assert hass.states.get(DC48).state == "off"


async def test_actuation_does_not_consume_planner_throttle(hass):
    """Review finding: the controller must NOT write _last_support_switch —
    that is the planner's shared dc24/dc48 throttle, and consuming it would
    delay unrelated 24 V switching. The F-N2 command bookkeeping IS recorded."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "49.40")
    coordinator._last_support_switch = None  # isolate from the setup refresh
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)
    coordinator._run_dc48_controller(t0 + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(DC48).state == "on"
    # The planner throttle is untouched...
    assert coordinator._last_support_switch is None
    # ...but the F-N2 bookkeeping is set so the detector owns this actuation.
    assert coordinator._last_support_cmd.get("dc48") is not None
    assert coordinator._last_support_cmd["dc48"][0] is True
    assert coordinator._support_pending_off["dc48"] is False


async def test_inverted_band_disables_regulation(hass):
    """Review finding (defense-in-depth): a hand-edited/legacy config with a
    collapsed band (off_v <= on_v) that bypasses flow validation must NOT
    chatter the PSU — the controller disables regulation and warns."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(
        hass,
        calls,
        extra_data={
            CONF_PSU48_ON_VOLTAGE_V: 49.8,  # inverted: on > off
            CONF_PSU48_OFF_VOLTAGE_V: 49.56,
        },
    )
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "49.40")  # would be ON in a valid band
    calls.clear()

    coordinator._run_dc48_controller(dt_util.now())
    coordinator._run_dc48_controller(dt_util.now() + timedelta(minutes=5))
    assert coordinator._dc48_ctrl_diag["reason"] == "invalid_config_off_le_on"
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(DC48).state == "off"


async def test_invalid_reading_freezes_dwell(hass):
    """Review finding: a brief invalid reading must FREEZE the dwell timers
    (spec §6 "einfrieren"), not reset them — otherwise a flapping sensor could
    stall regulation forever."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "49.40")  # ON region
    calls.clear()
    t0 = dt_util.now()

    coordinator._run_dc48_controller(t0)  # below_since = t0
    assert coordinator._dc48_below_since is not None

    hass.states.async_set(BATT_V, "unavailable")  # brief blip
    coordinator._run_dc48_controller(t0 + timedelta(seconds=30))
    assert coordinator._dc48_below_since is not None  # FROZEN, not reset

    hass.states.async_set(BATT_V, "49.40")  # valid again, dwell now elapsed
    coordinator._run_dc48_controller(t0 + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(DC48).state == "on"  # regulation still fired


async def test_stale_command_skips_when_manual_exited_before_actuation(hass):
    """Race hardening: if the operator exits manual mode after a controller
    command is queued but before its detached task runs, the stale command must
    NOT fire — the R3 switch owns the final state in auto mode."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    coordinator._listeners_setup = False  # no debounced planner refresh
    hass.states.async_set(DC48, "off")
    hass.states.async_set(BATT_V, "49.40")
    calls.clear()

    # Model the real race: the operator's exit sequence holds the switch lock,
    # so the controller's _do() blocks on it. (HA runs tasks eagerly, so the
    # lock must be contended for the re-check to matter.)
    await coordinator._switch_lock.acquire()
    coordinator._dc48_actuate(True, "test", False)  # _do() eager-runs, blocks
    task = coordinator._dc48_ctrl_task
    assert task is not None
    # Operator exits manual while _do() is parked on the lock.
    coordinator._support_manual["dc48"] = False
    coordinator._switch_lock.release()

    await task
    assert calls == []  # stale command bailed on the regulating re-check
    assert hass.states.get(DC48).state == "off"


async def test_idempotent_when_already_in_target_state(hass):
    """No redundant service call when the PSU is already where it should be."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    _arm_manual(coordinator)
    hass.states.async_set(DC48, "on")
    hass.states.async_set(BATT_V, "49.40")  # wants ON, already on
    calls.clear()

    coordinator._run_dc48_controller(dt_util.now() + timedelta(minutes=5))
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(DC48).state == "on"
