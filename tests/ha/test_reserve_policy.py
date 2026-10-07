"""Physical reserve protection cannot depend on an old observation balance."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import BLOCK, DCDC, LIMIT, PSU24, PSU48
from test_coordinated_actuation import rig as rig
from test_coordinator import ENTRY_DATA

from custom_components.battery_manager.const import (
    CONF_RESERVE_GRID_ENTITY,
    CONF_RESERVE_MODE,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SUPPORT_DC48_ACTIVATE_SOC,
)
from custom_components.battery_manager.reserve_runtime import ReserveRuntime


def configure(c, hass, soc, *, evidence=False, sun=0):
    c.raw_config.update(
        {
            **ENTRY_DATA,
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
            CONF_SUPPORT_DC48_ACTIVATE_SOC: 7,
        }
    )
    if evidence:
        c.raw_config.update(
            operation_pv_power_entity="sensor.actual_pv",
            operation_import_power_entity="sensor.actual_import",
        )
        hass.states.async_set("sensor.actual_pv", "100", {"unit_of_measurement": "W"})
        hass.states.async_set("sensor.actual_import", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.test_soc", str(soc))
    for entity in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(entity, str(sun), {"unit_of_measurement": "kWh"})
    c._arm_plan_boundary = Mock()  # This test owns every planning transition.


async def update(c):
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    return c.data


async def test_zero_historical_reference_cannot_turn_off_low_soc_support(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 6)
    first = await update(c)
    assert first["support_dc48"]
    assert hass.states.get(PSU48).state == "on"
    # Reuse the real configuration binding, as an old saved zero balance would
    # after a restart. Only the historical number changes; physics is identical.
    saved = c._reserve_runtime.export() | {"hold_soc": 0}
    c._reserve_runtime = ReserveRuntime()
    c._reserve_runtime.restore(saved)
    calls.clear()
    c._last_support_switch = None
    restored = await update(c)
    assert c._reserve_runtime.hold_soc == 0
    assert restored["support_dc48"]
    assert restored["reserve"]["decision_reason"] == "dc_support_protection"
    assert restored["reserve"]["historical_reference_soc_percent"] == 0
    assert restored["reserve"]["reference_semantics"] == "historical_observation_only"
    assert (PSU48, False) not in calls
    assert hass.states.get(PSU48).state == "on"
    assert float(hass.states.get(LIMIT).state) == 0


@pytest.mark.parametrize("evidence", [False, True])
async def test_high_soc_preserves_dc_energy_without_requiring_solar_meter_setup(
    rig, hass, freezer, evidence
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    await hass.config.async_set_time_zone("Europe/Berlin")
    c, calls, *_ = rig
    configure(c, hass, 80, evidence=evidence)
    result = await update(c)
    assert not result["inverter_recommendation"]
    assert result["support_dc24"] and result["support_dc48"]
    assert hass.states.get(DCDC).state == "off"
    assert calls.index((LIMIT, 0)) < calls.index((PSU24, True))
    assert calls.index((PSU24, True)) < calls.index((DCDC, False))
    assert calls.index((DCDC, False)) < calls.index((PSU48, True))
    diagnostics = result["reserve"]
    assert diagnostics["decision_reason"] == "dc_reserve_holding"
    assert diagnostics["headroom_wh"] == 0
    assert diagnostics["inverter_limit_w"] == 0
    assert diagnostics["solar_credit_verified"] is evidence
    assert dt_util.parse_datetime(
        diagnostics["preparation_horizon_end"]
    ) == dt_util.parse_datetime("2026-09-30T00:00:00+02:00")


async def test_high_soc_restored_grid_holding_returns_to_confirmed_native_source(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80, sun=10)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state = {"dc24": True, "dc48": True}
    hass.states.async_set(LIMIT, "0")
    hass.states.async_set(BLOCK, "on")
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(PSU48, "on")
    hass.states.async_set(DCDC, "off")
    result = await update(c)
    assert not result["support_dc24"] and not result["support_dc48"]
    assert calls.index((PSU48, False)) < calls.index((DCDC, True))
    assert calls.index((DCDC, True)) < calls.index((PSU24, False))
    # The small immediate preparation budget cannot fund full permission
    # plus SOC uncertainty. Restore DC without falsely publishing AC enabled.
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)
    assert float(hass.states.get(LIMIT).state) == 0
    assert not c._inverter_recommendation
    assert hass.states.get(DCDC).state == "on"
    # Confirmation is observed on the next serialized planning/protection pass.
    c._reserve_reconcile_legacy_hold(80)
    assert c._reserve_runtime.policy_version == 2


async def test_old_grid_holding_is_not_removed_before_native_source_confirmation(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, dead, _ = rig
    configure(c, hass, 80, sun=10)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state = {"dc24": True, "dc48": False}
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    dead.add(DCDC)
    result = await update(c)
    assert (DCDC, True) in calls
    assert (PSU24, False) not in calls
    assert hass.states.get(PSU24).state == "on"
    assert c._reserve_runtime.policy_version == 1
    assert c._support_state["dc24"] is True
    assert not result["inverter_recommendation"]
    assert float(hass.states.get(LIMIT).state) == 0


@pytest.mark.parametrize("soc, support", [(6, True), (15, False)])
async def test_stale_ac_proposal_cannot_override_live_battery_protection(
    rig, hass, freezer, soc, support
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, soc)
    # An old healthy-SOC plan is still queued when fresh telemetry arrives.
    # The executor must re-check protection under its actuator ownership lock.
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert calls[0] == (LIMIT, 0)
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)
    assert ((PSU48, True) in calls) is support
    assert ((PSU24, True) in calls) is support
    assert not c._inverter_recommendation


async def test_legacy_manual_support_remains_owned_by_the_explicit_request(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state["dc24"] = True
    c._support_manual["dc24"] = True
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    result = await update(c)
    assert result["support_dc24"]
    assert result["reserve"]["decision_reason"] == "manual_support"
    assert c._reserve_runtime.policy_version == 2
    assert (PSU24, False) not in calls
    assert (DCDC, True) not in calls
    assert not result["inverter_recommendation"]


async def test_legacy_holding_waits_for_soc_and_does_not_fake_physical_state(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, _, *_ = rig
    configure(c, hass, 80)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state["dc24"] = True
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    c._reserve_reconcile_legacy_hold(None)
    assert c._reserve_runtime.policy_version == 1
    assert c.build_system_config().support.dc24_active
    assert c._support_state["dc24"]
    c._reserve_reconcile_legacy_hold(80)
    assert c._reserve_runtime.policy_version == 1
    assert not c.build_system_config().support.dc24_active
    assert c._support_state["dc24"]  # Requested return is not confirmation.
    assert hass.states.get(PSU24).state == "on"


async def test_low_soc_protection_takes_over_legacy_holding_without_a_switch_gap(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 6)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state = {"dc24": True, "dc48": True}
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(PSU48, "on")
    hass.states.async_set(DCDC, "off")
    c._reserve_reconcile_legacy_hold(6)
    assert c._reserve_runtime.policy_version == 2
    assert c.build_system_config().support.dc24_active
    assert c.build_system_config().support.dc48_active
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert (PSU24, False) not in calls
    assert (PSU48, False) not in calls
    assert not c._inverter_recommendation


async def test_grid_loss_does_not_claim_an_unconfirmed_psu_shutdown(rig, hass, freezer):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, dead, _ = rig
    configure(c, hass, 80)
    hass.states.async_set("binary_sensor.grid", "off")
    hass.states.async_set(PSU48, "on")
    c._support_state["dc48"] = True
    dead.add(PSU48)
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert (PSU48, False) in calls
    assert c._support_state["dc48"] is True
    assert c._coordinated_support_diag["reason"] == "awaiting_confirmation"
    assert float(hass.states.get(LIMIT).state) == 0
    assert not c._inverter_recommendation
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)


async def test_failed_dcdc_restore_without_24v_psu_never_releases_ac(
    rig, hass, freezer
):
    from custom_components.battery_manager.const import CONF_SUPPORT_DC24_SWITCH

    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, dead, _ = rig
    configure(c, hass, 80)
    c.raw_config.pop(CONF_SUPPORT_DC24_SWITCH)
    hass.states.async_set(PSU48, "on")
    hass.states.async_set(DCDC, "off")
    dead.add(DCDC)
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert calls.index((LIMIT, 0)) < calls.index((PSU48, False))
    assert calls.index((PSU48, False)) < calls.index((DCDC, True))
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)
    assert not c._inverter_recommendation


async def test_soc_drop_during_psu_confirmation_prevents_final_ac_release(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80)
    hass.states.async_set(PSU48, "on")
    original_switch = c._switch_entity.side_effect

    async def switch_with_fresh_soc(entity, on, **kwargs):
        confirmed = await original_switch(entity, on)
        if entity == PSU48 and not on:
            hass.states.async_set("sensor.test_soc", "15")
        return confirmed

    c._switch_entity.side_effect = switch_with_fresh_soc
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, c.build_system_config(), dt_util.now()
    )
    assert (PSU48, False) in calls
    assert c._coordinated_support_diag["reason"] == "soc_protection"
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)
    assert not c._inverter_recommendation
    assert float(hass.states.get(LIMIT).state) == 0


@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_pending_active_migration_does_not_mask_legacy_or_shadow_state(
    rig, hass, freezer, mode
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80)
    c._reserve_runtime.restore({"hold_soc": 80})
    c._support_state["dc24"] = True
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    c._reserve_reconcile_legacy_hold(80)
    assert not c.build_system_config().support.dc24_active
    assert c._reserve_legacy_hold_sources == {"dc24"}
    c.raw_config[CONF_RESERVE_MODE] = mode
    # Merely building a mode-switched config must not inherit the old mask.
    assert c.build_system_config().support.dc24_active
    result = await update(c)
    assert result["support_dc24"]
    assert (PSU24, False) not in calls
    assert c._reserve_runtime.policy_version == 1
    assert c._reserve_legacy_hold_sources == set()
    assert "reserve_holding_pending" not in c._support_migration
    if mode == "shadow":
        assert result["reserve"]["mode"] == "shadow"
        assert c._reserve_runtime.hold_soc == 80
    else:
        assert c._reserve_runtime.hold_soc is None
    c.raw_config[CONF_RESERVE_MODE] = "active"
    c._reserve_reconcile_legacy_hold(80)
    assert c._reserve_legacy_hold_sources == {"dc24"}
    assert not c.build_system_config().support.dc24_active
    assert c._support_state["dc24"]  # Still no OFF confirmation.


async def test_actual_off_during_restart_clears_stale_on_latch_without_reactivation(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80, sun=10)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state = {"dc24": True, "dc48": True}
    assert hass.states.get(PSU24).state == "off"
    assert hass.states.get(PSU48).state == "off"
    result = await update(c)
    assert not result["support_dc24"] and not result["support_dc48"]
    assert c._reserve_runtime.policy_version == 2
    assert c._support_state == {"dc24": False, "dc48": False}
    assert not any(entity in (PSU24, PSU48) and value for entity, value in calls)


async def test_real_off_confirmation_does_not_clear_a_new_manual_request(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, _, *_ = rig
    configure(c, hass, 80)
    c._reserve_runtime.restore({"hold_soc": 0})
    c._support_state["dc24"] = True
    c._support_manual["dc24"] = True
    assert hass.states.get(PSU24).state == "off"
    c._reserve_reconcile_legacy_hold(80)
    assert not c._support_state["dc24"]
    assert c._support_manual["dc24"]
    assert c.build_system_config().support.dc24_forced_on
    assert c._reserve_runtime.policy_version == 2


@pytest.mark.parametrize("key", ["dc24", "dc48"])
async def test_new_manual_on_overrides_an_older_off_plan(rig, hass, freezer, key):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80)
    old_config = c.build_system_config()
    c._support_manual[key] = True
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, old_config, dt_util.now()
    )
    assert hass.states.get(PSU24).state == "on"
    assert (hass.states.get(PSU48).state == "on") is (key == "dc48")
    assert hass.states.get(DCDC).state == "off"
    assert not c._inverter_recommendation
    assert float(hass.states.get(LIMIT).state) == 0
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)


@pytest.mark.parametrize("key", ["dc24", "dc48"])
@pytest.mark.parametrize("already_on", [False, True])
async def test_cancelled_manual_on_cannot_be_restored_by_an_older_plan(
    rig, hass, freezer, key, already_on
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 80)
    c._support_manual[key] = True
    old_targets = {"dc24": True, "dc48": key == "dc48"}
    if already_on:
        c._support_state = dict(old_targets)
        hass.states.async_set(PSU24, "on")
        hass.states.async_set(DCDC, "off")
        if key == "dc48":
            hass.states.async_set(PSU48, "on")
    old_config = c.build_system_config()
    c._support_manual[key] = False
    await c._execute_coordinated_support(old_targets, True, old_config, dt_util.now())
    preserved24 = already_on and key == "dc48"
    assert (hass.states.get(PSU24).state == "on") is preserved24
    assert hass.states.get(PSU48).state == "off"
    assert (hass.states.get(DCDC).state == "off") is preserved24
    assert not any(entity in (PSU24, PSU48) and on for entity, on in calls)
    if already_on and key == "dc24":
        assert calls.index((DCDC, True)) < calls.index((PSU24, False))
    if preserved24:
        # The automatic 24 V latch was not the revoked manual request. Its
        # normal release still requires PV recovery in a fresh plan.
        assert (PSU48, False) in calls
        assert (PSU24, False) not in calls
        assert (DCDC, True) not in calls
    # The changed manual input invalidates the economic decision too. AC can
    # only resume after a new plan, even with a healthy battery.
    assert not c._inverter_recommendation
    assert float(hass.states.get(LIMIT).state) == 0
    assert not any(entity == LIMIT and value > 0 for entity, value in calls)


@pytest.mark.parametrize("soc", [6, "unknown"])
async def test_manual_off_cannot_cancel_current_low_or_unknown_soc_protection(
    rig, hass, freezer, soc
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, soc)
    c._support_manual["dc48"] = True
    old_config = c.build_system_config()
    c._support_manual["dc48"] = False
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, old_config, dt_util.now()
    )
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(PSU48).state == "on"
    assert calls.index((LIMIT, 0)) < calls.index((PSU48, True))
    assert not c._inverter_recommendation


@pytest.mark.parametrize("feedback", ["on", "unknown", "off"])
async def test_manual_change_preserves_only_current_protection_latches(
    rig, hass, freezer, feedback
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 8)  # Above 48 V activation, below its recovery threshold.
    c._support_state = {"dc24": True, "dc48": True}
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(PSU48, feedback)
    hass.states.async_set(DCDC, "off")
    old_config = c.build_system_config()
    c._support_manual["dc24"] = True
    await c._execute_coordinated_support(
        {"dc24": False, "dc48": False}, True, old_config, dt_util.now()
    )
    assert hass.states.get(PSU24).state == "on"
    assert (hass.states.get(PSU48).state == "on") is (feedback != "off")
    if feedback != "off":
        assert (PSU48, False) not in calls
    else:
        assert (PSU48, True) not in calls
    assert not c._inverter_recommendation


async def test_manual_request_waits_for_actuation_lock_and_refreshes_after_release(rig):
    c, calls, *_ = rig
    entered = asyncio.Event()

    async def refresh():
        assert not c._switch_lock.locked()

    def saved():
        assert c._switch_lock.locked()
        assert c._support_manual["dc48"]

    c.async_request_refresh = AsyncMock(side_effect=refresh)
    c._save_persistent_state = Mock(side_effect=saved)

    async def request():
        entered.set()
        await c.async_set_support_manual("dc48", True)

    await c._switch_lock.acquire()
    task = asyncio.create_task(request())
    try:
        await entered.wait()
        assert not c._support_manual["dc48"]
        c._save_persistent_state.assert_not_called()
        c.async_request_refresh.assert_not_called()
    finally:
        c._switch_lock.release()
        await task
    assert c._support_manual["dc48"]
    c._save_persistent_state.assert_called_once()
    c.async_request_refresh.assert_awaited_once()
    assert not calls  # This path requests a fresh plan rather than actuating.


async def test_cancelled_48v_manual_preserves_unmodified_24v_protection(
    rig, hass, freezer
):
    freezer.move_to("2026-09-27T12:00:00+00:00")
    c, calls, *_ = rig
    configure(c, hass, 13)
    c._support_state["dc24"] = True
    hass.states.async_set(PSU24, "on")
    hass.states.async_set(DCDC, "off")
    c._support_manual["dc48"] = True
    old_config = c.build_system_config()
    assert old_config.control.support_dc24_recovery_soc < 13
    c._support_manual["dc48"] = False
    await c._execute_coordinated_support(
        {"dc24": True, "dc48": True}, True, old_config, dt_util.now()
    )
    # Releasing a different manual request cannot impersonate the PV recovery
    # required by the unchanged automatic 24 V hysteresis contract.
    assert hass.states.get(PSU24).state == "on"
    assert hass.states.get(DCDC).state == "off"
    assert hass.states.get(PSU48).state == "off"
    assert (PSU24, False) not in calls
    assert (DCDC, True) not in calls
    assert not c._inverter_recommendation
    assert float(hass.states.get(LIMIT).state) == 0
