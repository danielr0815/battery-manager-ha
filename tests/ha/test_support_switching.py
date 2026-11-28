"""Tests for make-before-break switching of the 24 V rail supplies.

Requirement N1a (docs/REQUIREMENTS.md): the 24 V rail must never be left
without a supply. Activation: PSU on -> delay -> DC/DC off. Deactivation:
DC/DC on -> delay -> PSU off. An unconfirmed new supply aborts the sequence.
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_SUPPORT_SWITCH_DELAY_S,
    DOMAIN,
)
from custom_components.battery_manager.core import HourSlot, PlanInputs, plan
from custom_components.battery_manager.switch import SupportManualSwitch

DC48 = "switch.psu_48v"

PSU = "switch.psu_24v"
DCDC = "switch.dcdc_converter"

ENTRY_DATA = {
    CONF_SOC_ENTITY: "sensor.test_soc",
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
    CONF_SUPPORT_DC24_SWITCH: PSU,
    CONF_DCDC_SWITCH: DCDC,
    CONF_SUPPORT_SWITCH_DELAY_S: 1,
}


async def test_manual_switch_entity_delegates_mode_and_publishes_state():
    """The UI switch is the F-N2/R3 command boundary: both edges must reach the
    coordinator with the correct PSU key and publish their resulting state."""
    coordinator = SimpleNamespace(async_set_support_manual=AsyncMock())
    entity = SimpleNamespace(
        coordinator=coordinator,
        _psu_key="dc48",
        async_write_ha_state=Mock(),
    )

    await SupportManualSwitch.async_turn_on(entity)
    await SupportManualSwitch.async_turn_off(entity)

    assert coordinator.async_set_support_manual.await_args_list == [
        (("dc48", True),),
        (("dc48", False),),
    ]
    assert entity.async_write_ha_state.call_count == 2


def _register_switch_services(hass, call_log, *, dead_entities=()):
    """Mock the cross-domain services that update states like real devices.

    The coordinator switches via `homeassistant.turn_on/turn_off` so that
    input_boolean entities work as well as switches.
    """

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


async def _setup(hass, call_log, *, dead_entities=(), extra_data=None):
    hass.states.async_set(
        "sensor.test_soc", "55", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set("sensor.pv_today", "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_tomorrow", "12.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_day_after", "8.0", {"unit_of_measurement": "kWh"})
    _register_switch_services(hass, call_log, dead_entities=dead_entities)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **(extra_data or {})},
        title="Battery Manager",
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


@pytest.mark.freeze_time(real_asyncio=True)
@pytest.mark.parametrize("loss_at", [None, "overlap", "psu_off"])
async def test_legacy_psu24_removal_rechecks_forecast(hass, freezer, loss_at):
    calls = []
    c = await _setup(hass, calls)
    c._unsub_state_listener()
    c._unsub_state_listener = None
    c._listeners_setup = False
    hass.states.async_set("sensor.test_soc", "9")
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")
    c._support_state["dc24"] = True
    c._last_support_switch = None
    calls.clear()
    now = dt_util.now()
    config = c.build_system_config()
    slots = tuple(
        HourSlot(i, now + timedelta(hours=i), 1, (now.hour + i) % 24, 550, 100, 100)
        for i in range(3)
    )
    inputs = PlanInputs(now, 9, slots, pv_daily_credit=((now.date(), 10.0),))
    result = plan(config, inputs)
    assert not result.support_dc24_now
    assert plan(
        config, replace(inputs, slots=tuple(replace(slot, pv_wh=0) for slot in slots))
    ).support_dc24_now
    c._last_planner_recording = config, inputs, result

    def revoke():
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set("sensor.pv_today", "0")
        assert not c.pv_plan_evidence_valid(config, inputs)

    overlaps = []

    async def sleep(seconds):
        overlaps.append(seconds)
        assert hass.states.is_state(PSU, "on") and hass.states.is_state(DCDC, "on")
        if loss_at == "overlap" and len(overlaps) == 1:
            revoke()

    async def turn_off(call):
        actor = call.data["entity_id"]
        calls.append(("turn_off", actor))
        hass.states.async_set(actor, "off")
        if loss_at == "psu_off" and actor == PSU:
            revoke()

    hass.services.async_register("homeassistant", "turn_off", turn_off)
    with patch("custom_components.battery_manager.coordinator.asyncio.sleep", sleep):
        await c._apply_support_switching(result, config, now)
        await c._switch_task
    expected = [("turn_on", DCDC)]
    if loss_at != "overlap":
        expected.append(("turn_off", PSU))
    if loss_at == "psu_off":
        expected.append(("turn_on", PSU))
    if loss_at is not None:
        expected.append(("turn_off", DCDC))
    assert calls == expected
    assert overlaps == ([1] if loss_at is None else [1, 1])
    assert hass.states.is_state(PSU, "off" if loss_at is None else "on")
    assert hass.states.is_state(DCDC, "on" if loss_at is None else "off")


async def test_single_48v_entry_recommends_only_the_configured_supply(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(
        hass,
        calls,
        extra_data={
            CONF_SUPPORT_DC24_SWITCH: None,
            CONF_DCDC_SWITCH: None,
            CONF_SUPPORT_DC48_SWITCH: DC48,
        },
    )
    hass.states.async_set(DC48, "off")
    hass.states.async_set("sensor.test_soc", "5")
    for entity_id in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(entity_id, "0", {"unit_of_measurement": "kWh"})

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    config, inputs, result = coordinator._last_planner_recording
    assert not config.support.coordinated
    assert not config.support.dc24_available
    assert config.support.dc48_available
    assert inputs.start_soc_percent == 5.0
    assert not result.support_dc24_now
    assert result.support_dc48_now
    assert all(
        not flow.support_dc24 and flow.psu24_delivered_wh == 0.0
        for flow in result.trajectory.flows
    )
    assert result.trajectory.flows[0].psu48_delivered_wh > 0.0


async def test_activation_turns_psu_on_before_dcdc_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None) as delay:
        assert await coordinator._sequence_dc24(True, PSU) is True

    assert calls == [("turn_on", PSU), ("turn_off", DCDC)]
    assert sum(call.args == (1.0,) for call in delay.await_args_list) == 1


async def test_deactivation_turns_dcdc_on_before_psu_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")
    calls.clear()

    with patch("asyncio.sleep", return_value=None) as delay:
        assert await coordinator._sequence_dc24(False, PSU) is True

    assert calls == [("turn_on", DCDC), ("turn_off", PSU)]
    assert sum(call.args == (1.0,) for call in delay.await_args_list) == 1


async def test_unconfirmed_new_supply_aborts_switchover(hass):
    """A dead PSU must never lead to the DC/DC being switched off."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, dead_entities=(PSU,))
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        assert await coordinator._sequence_dc24(True, PSU) is False

    assert calls == [("turn_on", PSU)]  # no turn_off — old supply stays on
    assert hass.states.get(DCDC).state == "on"


async def test_sync_heals_off_but_never_adopts_foreign_on(hass):
    """ON->OFF desyncs are healed by the idle sync; an OFF->ON transition
    is exclusively judged by _update_support_modes (manual vs late) — the
    sync must not adopt it (review finding: adopted ONs got reverted)."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)

    # OFF->ON: not adopted here.
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")
    coordinator._sync_support_state_from_entities()
    assert coordinator._support_state["dc24"] is False

    # ON->OFF: healed.
    coordinator._support_state["dc24"] = True
    hass.states.async_set(PSU, "off")
    coordinator._sync_support_state_from_entities()
    assert coordinator._support_state["dc24"] is False

    # 'unavailable' (e.g. right after a restart) must never be read as 'off'.
    coordinator._support_state["dc24"] = True
    hass.states.async_set(PSU, "unavailable")
    coordinator._sync_support_state_from_entities()
    assert coordinator._support_state["dc24"] is True


async def test_external_psu_on_enters_manual_mode(hass):
    """F-N2: a PSU switched on externally pauses the automatic control for
    that PSU — the integration must not switch it off again."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "on")  # winter operation, switched by hand
    hass.states.async_set(DCDC, "on")
    calls.clear()

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is True
    assert coordinator._support_state["dc24"] is True

    # The plan does not want support — but manual mode pins the PSU.
    result = SimpleNamespace(support_dc24_now=False, support_dc48_now=False)
    config = coordinator.build_system_config()
    await coordinator._apply_support_switching(result, config, dt_util.now())
    await hass.async_block_till_done()
    assert calls == []  # hands off
    assert hass.states.get(PSU).state == "on"

    # The simulation now runs with the path forced on.
    assert config.support.dc24_forced_on is True


async def test_manual_off_returns_to_auto_and_restores_dcdc(hass):
    """Switching the PSU off externally ends manual mode; a dead 24 V rail
    (DC/DC also off) is healed immediately."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._support_manual["dc24"] = True
    coordinator._support_state["dc24"] = True
    hass.states.async_set(PSU, "off")  # manually switched off
    hass.states.async_set(DCDC, "off")  # user forgot the converter
    calls.clear()

    coordinator._update_support_modes()
    await hass.async_block_till_done()
    assert coordinator._support_manual["dc24"] is False
    assert coordinator._support_state["dc24"] is False
    assert ("turn_on", DCDC) in calls  # rail supply restored


async def test_late_confirming_device_is_not_manual(hass):
    """Within the grace window after an own ON command for THIS PSU, an
    unexpectedly-on PSU is a slow device, not a manual override."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._last_support_cmd["dc24"] = (True, dt_util.now())
    hass.states.async_set(PSU, "on")

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is False
    assert coordinator._support_state["dc24"] is True  # adopted instead


async def test_on_after_bm_off_command_is_manual_not_reverted(hass):
    """Review finding: an operator ON right after a BM OFF command must
    enter manual mode immediately — the old global grace adopted it and
    the next plan reverted the operator's switch (oscillation)."""
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    # BM just deactivated dc24 support (last command: OFF, seconds ago).
    coordinator._last_support_switch = dt_util.now()
    coordinator._last_support_cmd["dc24"] = (False, dt_util.now())
    coordinator._support_state["dc24"] = False
    hass.states.async_set(PSU, "on")  # operator re-enables for winter
    hass.states.async_set(DCDC, "on")
    calls.clear()

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is True  # NOT adopted

    # And no plan cycle may switch the operator's PSU off again.
    result = SimpleNamespace(support_dc24_now=False, support_dc48_now=False)
    config = coordinator.build_system_config()
    await coordinator._apply_support_switching(
        result, config, dt_util.now() + timedelta(seconds=120)
    )
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(PSU).state == "on"


async def test_cross_key_command_does_not_mask_manual_override(hass):
    """A fresh BM command for the 48 V PSU must not make an external 24 V
    ON look like a late confirmation (per-key grace)."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._last_support_switch = dt_util.now()
    coordinator._last_support_cmd["dc48"] = (True, dt_util.now())
    hass.states.async_set(PSU, "on")  # dc24, externally

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is True


async def test_pending_confirmation_adopts_late_on(hass):
    """The BM's own unconfirmed activation must not flip to manual when
    the device reports 'on' after the grace window (review finding:
    60 s grace vs 300 s poll)."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._support_pending_confirm["dc24"] = True  # abort path armed
    hass.states.async_set(PSU, "on")  # late report, grace long expired

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is False
    assert coordinator._support_state["dc24"] is True
    assert coordinator._support_pending_confirm["dc24"] is False


async def test_rail_guard_is_level_triggered(hass):
    """PSU off + DC/DC off is always pathological: the guard restores the
    DC/DC on every cycle, not only on the manual->off edge."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "off")  # dead rail, no mode transition
    calls.clear()

    coordinator._update_support_modes()
    await hass.async_block_till_done()
    assert ("turn_on", DCDC) in calls


async def test_stale_flags_of_removed_switch_are_not_restored(hass):
    """Persisted manual/state flags of a PSU whose switch was removed from
    the config must be dropped on restore (they could never be cleared)."""
    from homeassistant.helpers.storage import Store

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    # The live entry has no dc48 switch configured -> dc48 flags must
    # fall back to defaults even if the store carries stale values.
    await coordinator._store.async_save(
        {
            "support_manual": {"dc24": True, "dc48": True},
            "support_state": {"dc24": True, "dc48": True},
        }
    )
    coordinator._support_manual = {"dc24": False, "dc48": False}
    coordinator._support_state = {"dc24": False, "dc48": False}
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert coordinator._support_manual["dc24"] is True  # switch configured
    assert coordinator._support_manual["dc48"] is False  # switch removed
    assert coordinator._support_state["dc48"] is False


async def test_upgrade_from_pre_ownership_store_adopts_on_psu(hass):
    """Pre-0.6.5 stores carry no support_state: an ON left over from the
    old version's own escalation must be adopted once, not flipped to
    manual on the upgrade restart."""
    from homeassistant.helpers.storage import Store

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    await coordinator._store.async_save({"load_soc": {}})  # old schema
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert coordinator._support_adopt_once is True

    hass.states.async_set(PSU, "on")
    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is False
    assert coordinator._support_state["dc24"] is True
    assert coordinator._support_adopt_once is False


async def test_manual_mode_persists_and_bm_state_disambiguates(hass):
    """Mode and the BM's own support state survive restarts: 'on, but not
    ours' stays distinguishable from 'on, because we switched it'."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._support_manual["dc24"] = True
    coordinator._support_state["dc24"] = True

    captured: dict = {}
    coordinator._store.async_delay_save = lambda data_func, _delay: captured.update(
        data_func()
    )
    coordinator._save_persistent_state()
    assert captured["support_manual"]["dc24"] is True
    assert captured["support_state"]["dc24"] is True

    from homeassistant.helpers.storage import Store

    await coordinator._store.async_save(captured)
    coordinator._support_manual = {"dc24": False, "dc48": False}
    coordinator._support_state = {"dc24": False, "dc48": False}
    coordinator._store = Store(hass, coordinator._store.version, coordinator._store.key)
    await coordinator.async_load_persistent_state()
    assert coordinator._support_manual["dc24"] is True
    assert coordinator._support_state["dc24"] is True


async def test_bm_activated_psu_stays_auto_after_restart(hass):
    """Restart with a BM-activated PSU: the persisted support state marks
    it as ours — no false manual mode."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    # Restored state says: WE switched it on before the restart.
    coordinator._support_state["dc24"] = True
    hass.states.async_set(PSU, "on")

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc24"] is False  # stays automatic


# ---------------------------------------------------------------------------
# R3 manual-override switches (F-N3 §7, docs/DC_TOPOLOGY.md). The switch is
# the single entry point: it actuates the PSU and pins manual mode so the
# simulation forces the path on.
# ---------------------------------------------------------------------------


async def test_manual_switch_on_forces_and_actuates_dc48(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    coordinator._support_manual["dc48"] = False
    coordinator._support_state["dc48"] = False
    hass.states.async_set(DC48, "off")
    calls.clear()

    await coordinator.async_set_support_manual("dc48", True)
    await hass.async_block_till_done()
    assert ("turn_on", DC48) in calls
    assert coordinator.support_manual("dc48") is True
    # The simulation now treats the 48 V path as permanently on.
    assert coordinator.build_system_config().support.dc48_forced_on is True


async def test_manual_switch_off_restores_auto_dc48(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    coordinator._support_manual["dc48"] = True
    coordinator._support_state["dc48"] = True
    hass.states.async_set(DC48, "on")
    calls.clear()

    await coordinator.async_set_support_manual("dc48", False)
    await hass.async_block_till_done()
    assert ("turn_off", DC48) in calls
    assert coordinator.support_manual("dc48") is False
    assert coordinator.build_system_config().support.dc48_forced_on is False


async def test_manual_switch_dc24_uses_make_before_break(hass):
    """Entering 24 V manual mode must keep the rail sourced: PSU on before
    the DC/DC goes off; exiting reverses it.

    The lower-level sequence tests cover the configured delay. This integration
    test verifies routing, order and state and therefore must not spend two
    seconds waiting on the production wall clock.
    """
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._support_manual["dc24"] = False
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None) as delay:
        await coordinator.async_set_support_manual("dc24", True)
        await hass.async_block_till_done()
        assert calls.index(("turn_on", PSU)) < calls.index(("turn_off", DCDC))
        assert coordinator.support_manual("dc24") is True
        assert coordinator.build_system_config().support.dc24_forced_on is True

        calls.clear()
        await coordinator.async_set_support_manual("dc24", False)
        await hass.async_block_till_done()
    # Restore: DC/DC on before the PSU goes off.
    assert calls.index(("turn_on", DCDC)) < calls.index(("turn_off", PSU))
    assert coordinator.support_manual("dc24") is False
    assert delay.await_count >= 2


async def test_manual_switch_is_idempotent(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    coordinator._support_manual["dc48"] = True
    hass.states.async_set(DC48, "on")
    calls.clear()

    await coordinator.async_set_support_manual("dc48", True)  # already manual
    await hass.async_block_till_done()
    assert calls == []


async def test_manual_switch_entity_reflects_state(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    state = hass.states.get("switch.battery_manager_48_v_support_manual")
    assert state is not None  # created because the dc48 switch is configured
    # Externally-detected manual mode (F-N2) is reflected by the switch too.
    coordinator._support_manual["dc48"] = True
    assert coordinator.support_manual("dc48") is True


async def test_manual_off_lag_does_not_reenter_manual(hass):
    """Review finding: after an operator OFF, a lagging switch still reading
    'on' must not be misread as an external override and bounced back to
    forced-on (symmetric OFF-side grace)."""
    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    # We just commanded dc48 OFF and have NOT yet observed it reach 'off';
    # the entity lags and still reports 'on'.
    coordinator._support_manual["dc48"] = False
    coordinator._support_state["dc48"] = False
    coordinator._last_support_cmd["dc48"] = (False, dt_util.now())
    coordinator._support_pending_off["dc48"] = True
    hass.states.async_set(DC48, "on")
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is False  # stayed auto

    # Once the device IS seen off, a later external ON must still enter
    # manual mode (operator re-enabling at the wall).
    hass.states.async_set(DC48, "off")
    coordinator._update_support_modes()  # observes off -> clears pending_off
    hass.states.async_set(DC48, "on")
    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is True


async def test_manual_off_lag_beyond_grace_still_stays_auto(hass):
    """Review #10: the OFF-lag escape is un-timed — a device slower than
    min_switch_interval_s that still reads 'on' after our OFF command (and
    emits no event) must NOT be misclassified as an external override once the
    grace window has expired."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    # BM commanded dc48 OFF a long time ago (well beyond min_switch_interval_s),
    # but the device has never been observed to reach 'off'.
    coordinator._support_manual["dc48"] = False
    coordinator._support_state["dc48"] = False
    coordinator._last_support_cmd["dc48"] = (
        False,
        dt_util.now() - timedelta(minutes=30),
    )
    coordinator._support_pending_off["dc48"] = True
    hass.states.async_set(DC48, "on")
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")

    coordinator._update_support_modes()
    assert coordinator._support_manual["dc48"] is False  # stays auto, not manual


async def test_unload_cancels_actuation_tasks_before_flush(hass):
    """Review #7: in-flight actuation tasks must be cancelled before the unload
    flush, so none can mutate the persisted state after the payload is captured."""
    import asyncio

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)

    never_finishes = asyncio.Event()

    async def _slow() -> None:
        await never_finishes.wait()

    coordinator._switch_task = hass.async_create_task(_slow())
    coordinator._dc48_ctrl_task = hass.async_create_task(_slow())

    await coordinator.async_cancel_actuation_tasks()
    assert coordinator._switch_task.done()
    assert coordinator._dc48_ctrl_task.done()
    # The subsequent flush then persists a stable payload without raising.
    await coordinator.async_flush_persistent_state()


async def test_manual_dc24_off_failure_keeps_manual(hass):
    """Review finding: if the make-before-break restore aborts (DC/DC does
    not confirm), the PSU stays physically on, so manual mode must persist
    rather than desyncing the model."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, dead_entities=(DCDC,))
    coordinator._support_manual["dc24"] = True
    coordinator._support_state["dc24"] = True
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")

    with patch("asyncio.sleep", return_value=None):
        await coordinator.async_set_support_manual("dc24", False)
    # DC/DC restore failed -> stay in manual mode (PSU is still on).
    assert coordinator._support_manual["dc24"] is True


async def test_manual_dc24_on_failure_stays_auto(hass):
    """Symmetric to the OFF-failure case: if the PSU does not confirm 'on'
    during manual activation, the switchover aborts — manual mode is NOT
    entered and the model stays automatic (the DC/DC keeps feeding the
    rail). The pending-confirm latch still arms, so a late 'on' report is
    adopted as ours instead of misread as a foreign manual override (F-N2)."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, dead_entities=(PSU,))
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        await coordinator.async_set_support_manual("dc24", True)

    assert coordinator._support_manual["dc24"] is False
    assert coordinator._support_state["dc24"] is False
    assert coordinator._support_pending_confirm["dc24"] is True
    # The DC/DC was never told to turn off (the sequence aborted first).
    assert calls == [("turn_on", PSU)]


async def test_manual_dc48_on_service_error_stays_auto(hass):
    """48 V variant: a RAISING turn_on service call makes _switch_entity
    report failure — the activation aborts before manual mode is entered."""
    from homeassistant.exceptions import HomeAssistantError

    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls, extra_data={CONF_SUPPORT_DC48_SWITCH: DC48})
    hass.states.async_set(DC48, "off")

    async def failing_turn_on(call):
        if call.data["entity_id"] == DC48:
            raise HomeAssistantError("psu unreachable")
        calls.append(("turn_on", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("homeassistant", "turn_on", failing_turn_on)

    await coordinator.async_set_support_manual("dc48", True)

    assert coordinator._support_manual["dc48"] is False
    assert coordinator._support_state["dc48"] is False


@pytest.mark.parametrize("coordinated", [False, True])
@pytest.mark.parametrize("activate", [False, True])
async def test_dc24_waits_for_qualified_knx_status(hass, coordinated, activate):
    """D1/D2: no optimistic switch echo can complete either direction."""
    from asyncio import sleep as yield_loop

    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.battery_manager.const import CONF_INVERTER_LIMIT_ENTITY
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            "support_dc24_feedback": "2/1/31@1.1.14",
            "support_dcdc_feedback": "2/1/32@1.1.14",
            **({CONF_INVERTER_LIMIT_ENTITY: "number.limit"} if coordinated else {}),
        },
    )
    entry.add_to_hass(hass)
    coordinator = BatteryManagerCoordinator(hass, entry)
    coordinator._save_persistent_state = Mock()
    coordinator._schedule_replan = Mock()
    coordinator.rail_feedback.start()
    calls = []
    _register_switch_services(hass, calls)
    target, old = (PSU, DCDC) if activate else (DCDC, PSU)
    groups = {PSU: "2/1/31", DCDC: "2/1/32"}

    def status(actor, on, source="1.1.14", direction="Incoming"):
        async_dispatcher_send(
            hass,
            "knx_telegram",
            None,
            {
                "direction": direction,
                "source": source,
                "destination": groups[actor],
                "telegramtype": "GroupValueWrite",
                "payload": int(on),
            },
        )

    try:
        hass.states.async_set(target, "off")
        hass.states.async_set(old, "on")
        status(old, True)
        status(target, False)
        with patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            new=AsyncMock(),
        ) as delay:
            assert not await coordinator._sequence_dc24(activate, PSU)
            assert hass.states.is_state(target, "on")
            assert calls == [("turn_on", target)]
            status(target, True, source="1.1.99")
            status(target, True, direction="Outgoing")
            assert not await coordinator._sequence_dc24(activate, PSU)
            delay.assert_not_called()

            status(target, True)

            async def interrupted_overlap(_seconds):
                status(target, False)
                status(target, True)

            delay.side_effect = interrupted_overlap
            assert not await coordinator._sequence_dc24(activate, PSU)
            assert calls == [("turn_on", target)]

            async def bus_gap(_seconds):
                hass.states.async_set(target, "unavailable")
                hass.states.async_set(target, "on")
                await yield_loop(0)

            delay.side_effect = bus_gap
            assert not await coordinator._sequence_dc24(activate, PSU)
            assert coordinator.rail_feedback.report(target) is None
            assert coordinator.rail_feedback.report(old) is None
            assert calls == [("turn_on", target)]
            status(target, True)
            status(old, True)
            delay.side_effect = None

            assert not await coordinator._sequence_dc24(activate, PSU)
            assert calls == [("turn_on", target), ("turn_off", old)]
            assert hass.states.is_state(old, "off")
            assert coordinator._entity_tristate(old) is None
            status(old, False)
            assert await coordinator._sequence_dc24(activate, PSU)
            assert calls == [("turn_on", target), ("turn_off", old)]
    finally:
        await coordinator.async_cancel_actuation_tasks()


async def test_dc24_does_not_confirm_undispatched_shutdown(hass):
    """A valid-looking OFF during persistence must not settle an unsent edge."""
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            "support_dcdc_feedback": "binary_sensor.dcdc",
            "support_dc24_feedback": "binary_sensor.psu",
        },
    )
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c._save_persistent_state = Mock()
    c._schedule_replan = Mock()
    calls = []
    _register_switch_services(hass, calls)
    hass.states.async_set(PSU, "on")
    hass.states.async_set("binary_sensor.psu", "on")
    hass.states.async_set("binary_sensor.dcdc", "on")

    async def save(_entity, _context, _snapshot):
        hass.states.async_set("binary_sensor.dcdc", "off")
        c._rail_feedback_updated(DCDC, True)
        assert c._supply_actor_requests[DCDC].state == "booked"
        hass.states.async_set("binary_sensor.dcdc", "on")
        return True

    try:
        with (
            patch.object(c._store, "async_book_rail_off", side_effect=save),
            patch(
                "custom_components.battery_manager.coordinator.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            assert not await c._sequence_dc24(True, PSU)
        assert calls == [("turn_off", DCDC)]
        assert c._supply_actor_requests[DCDC].state == "pending"
        assert c._entity_tristate(DCDC) is None
    finally:
        await c.async_cancel_actuation_tasks()


@pytest.mark.parametrize(
    ("binding", "valid"),
    [
        ("binary_sensor.dcdc", True),
        ("switch.dcdc", True),
        ("2/1/31@1.1.14", True),
        ("32/1/31@1.1.14", False),
        ("2/8/31@1.1.14", False),
        ("02/1/31@1.1.14", False),
        ("2/1/31@1.1.999", False),
        ("2/1/31", False),
    ],
)
def test_rail_feedback_binding_format(binding, valid):
    from custom_components.battery_manager.rail_feedback import valid_binding

    assert valid_binding(binding) is valid


async def test_rail_feedback_requires_qualified_switch(hass, freezer):
    from homeassistant.helpers import entity_registry

    from custom_components.battery_manager.rail_feedback import RailFeedback

    registry = entity_registry.async_get(hass)
    entity = registry.async_get_or_create(
        "switch", "knx", "source", suggested_object_id="rail"
    )
    actor = entity.entity_id
    cfg = {CONF_DCDC_SWITCH: actor}
    feedback = RailFeedback(SimpleNamespace(hass=hass, raw_config=cfg))
    hass.states.async_set(actor, "on")
    assert feedback.report(actor) is None
    cfg["support_dcdc_feedback"] = actor
    assert feedback.report(actor) is None
    cfg["support_dcdc_feedback"] = "switch.other"
    assert feedback.report(actor) is None

    qualified = registry.async_get_or_create(
        "switch", "test", "target", suggested_object_id="target"
    )
    cfg[CONF_DCDC_SWITCH] = cfg["support_dcdc_feedback"] = qualified.entity_id
    hass.states.async_set(qualified.entity_id, "on")
    assert feedback.report(qualified.entity_id)[0] is True
    freezer.tick(timedelta(minutes=66))
    assert feedback.report(qualified.entity_id) is None


@pytest.mark.parametrize(
    (
        "prior_target",
        "prior_state",
        "report_age",
        "qualified",
        "outcome",
        "continuity_loss",
    ),
    [
        (True, "pending", 31, True, "on_unmet", None),
        (True, "pending", 31, True, "on_unmet", "knx_disconnect"),
        (True, "service_failed", 31, True, "on_unmet", "restart"),
        (True, "dispatching_on", 31, True, "dispatching_on", None),
        (True, "pending", 10, True, "pending", None),
        (True, "pending", 301, True, "pending", None),
        (True, "pending", 31, False, "pending", None),
        (False, "pending", 31, True, "pending", None),
    ],
)
async def test_knx_dcdc_reports_off_after_unconfirmed_on(
    hass,
    freezer,
    prior_target,
    prior_state,
    report_age,
    qualified,
    outcome,
    continuity_loss,
):
    """An unmet ON allows policy recovery and still blocks manual handover."""
    from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.battery_manager.actor_ownership import SUPPLY_OWNER
    from custom_components.battery_manager.const import CONF_INVERTER_LIMIT_ENTITY
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
    from custom_components.battery_manager.load_actuation import ActorRequest

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **ENTRY_DATA,
            CONF_INVERTER_LIMIT_ENTITY: "number.limit",
            "support_dc24_feedback": "2/1/31@1.1.14",
            "support_dcdc_feedback": "2/1/32@1.1.14",
        },
    )
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    c._save_persistent_state = Mock()
    c._schedule_replan = Mock()
    c.rail_feedback.start()
    calls = []
    _register_switch_services(hass, calls, dead_entities=(DCDC,))
    hass.states.async_set(DCDC, "off")
    hass.states.async_set(PSU, "on")
    prior = ActorRequest(prior_target, dt_util.utcnow(), prior_state)
    c._supply_actor_requests[DCDC] = prior

    def status(on, *, source="1.1.14", group="2/1/32"):
        async_dispatcher_send(
            hass,
            "knx_telegram",
            None,
            {
                "direction": "Incoming",
                "telegramtype": "GroupValueResponse",
                "source": source,
                "destination": group,
                "payload": int(on),
            },
        )

    try:
        freezer.tick(timedelta(seconds=1))
        status(not prior_target, source="1.1.14" if qualified else "1.1.99")
        freezer.tick(timedelta(seconds=report_age))
        c._rail_feedback_updated(DCDC, False)
        assert prior.state == outcome
        if outcome != "on_unmet":
            assert c._entity_tristate(DCDC) is None
            return
        assert c._entity_tristate(DCDC) is False
        assert (
            c._persistent_payload()["support_commands_pending"][DCDC]["state"]
            == "on_unmet"
        )
        with pytest.raises(HomeAssistantError):
            c._ensure_support_commands_settled([DCDC])

        # Current policy needs the grid source. Its overlap finishes without
        # inventing a fresh OFF at the converter or calling its service.
        status(True, group="2/1/31")
        with patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            new=AsyncMock(),
        ):
            assert await c._sequence_dc24(True, PSU) is True
        assert calls == []
        assert c._supply_actor_requests[DCDC] is prior

        assert c.rail_feedback.invalidate_connection(DCDC)
        assert not await c._switch_entity(DCDC, True, actor_owner=SUPPLY_OWNER)
        assert c._supply_actor_requests[DCDC] is prior

        freezer.tick(timedelta(seconds=1))
        status(False)

        with patch.object(c._store, "async_book_knx_on_retry", return_value=False):
            assert not await c._switch_entity(DCDC, True, actor_owner=SUPPLY_OWNER)
        assert calls == []
        assert c._supply_actor_requests[DCDC] is prior

        async def unavailable(_call):
            raise ServiceNotFound("homeassistant", "turn_on")

        hass.services.async_register("homeassistant", "turn_on", unavailable)
        assert not await c._switch_entity(DCDC, True, actor_owner=SUPPLY_OWNER)
        assert c._supply_actor_requests[DCDC] is prior
        _register_switch_services(hass, calls, dead_entities=(DCDC,))

        # If policy later needs the converter, an actual new ON can be sent.
        assert await c._switch_entity(DCDC, True, actor_owner=SUPPLY_OWNER)
        assert calls == [("turn_on", DCDC)]
        assert c._supply_actor_requests[DCDC] is not prior
        assert c._supply_actor_requests[DCDC].state == "pending"
        assert c._entity_tristate(DCDC) is None
        freezer.tick(timedelta(seconds=1))
        status(True)
        assert c._entity_tristate(DCDC) is True
        assert c._supply_actor_requests[DCDC].state == "confirmed"
        assert c._persistent_payload()["support_commands_pending"][DCDC][
            "knx_on_retried"
        ]
        with pytest.raises(HomeAssistantError):
            c._ensure_support_commands_settled([DCDC])
        assert not await c._switch_entity(DCDC, False, actor_owner=SUPPLY_OWNER)

        # Only the usual automatic rail sequence can resynchronize the
        # receiver after a repeated ON, and waits for a fresh KNX OFF.
        freezer.tick(timedelta(seconds=1))
        status(True, group="2/1/31")
        with patch(
            "custom_components.battery_manager.coordinator.asyncio.sleep",
            new=AsyncMock(),
        ):
            assert not await c._sequence_dc24(True, PSU)
        assert calls[-1] == ("turn_off", DCDC)
        with pytest.raises(HomeAssistantError):
            c._ensure_support_commands_settled([DCDC])
        if continuity_loss is not None:
            saved = c._persistent_payload()
            assert c.rail_feedback.invalidate_connection(DCDC)
            if continuity_loss == "restart":
                c._supply_actor_requests.clear()
                with patch.object(c._store, "async_load", return_value=saved):
                    await c.async_load_persistent_state()
            freezer.tick(timedelta(seconds=1))
            status(False)
            assert c._entity_tristate(DCDC) is False
            with pytest.raises(HomeAssistantError):
                c._ensure_support_commands_settled([DCDC])
            return
        freezer.tick(timedelta(seconds=1))
        status(False)
        c._ensure_support_commands_settled([DCDC])
        assert DCDC not in c._persistent_payload()["support_commands_pending"]
        assert hass.states.is_state(PSU, "on")
    finally:
        await c.async_cancel_actuation_tasks()
