"""Tests for make-before-break switching of the 24 V rail supplies.

Requirement N1a (docs/REQUIREMENTS.md): the 24 V rail must never be left
without a supply. Activation: PSU on -> delay -> DC/DC off. Deactivation:
DC/DC on -> delay -> PSU off. An unconfirmed new supply aborts the sequence.
"""

from datetime import timedelta
from unittest.mock import patch

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


async def test_activation_turns_psu_on_before_dcdc_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        assert await coordinator._sequence_dc24(True, PSU) is True

    assert calls == [("turn_on", PSU), ("turn_off", DCDC)]


async def test_deactivation_turns_dcdc_on_before_psu_off(hass):
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    hass.states.async_set(PSU, "on")
    hass.states.async_set(DCDC, "off")
    calls.clear()

    with patch("asyncio.sleep", return_value=None):
        assert await coordinator._sequence_dc24(False, PSU) is True

    assert calls == [("turn_on", DCDC), ("turn_off", PSU)]


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
    the DC/DC goes off; exiting reverses it."""
    calls: list[tuple[str, str]] = []
    coordinator = await _setup(hass, calls)
    coordinator._support_manual["dc24"] = False
    hass.states.async_set(PSU, "off")
    hass.states.async_set(DCDC, "on")
    calls.clear()

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
