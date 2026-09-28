"""Historical reserve references, forecast control and confirmed source transfers."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import DCDC, LIMIT, PSU24, PSU48, execute
from test_coordinated_actuation import rig as rig

from custom_components.battery_manager.config_flow import _validate_support_entities
from custom_components.battery_manager.const import (
    CONF_DCDC_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_RESERVE_GRID_ENTITY,
    CONF_RESERVE_MODE,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
)
from custom_components.battery_manager.reserve_runtime import (
    MAX_OBSERVATION_GAP_SECONDS,
    ReserveRuntime,
)

START = datetime(2026, 9, 26, tzinfo=UTC)


def observe(r, seconds, soc=80, solar=True, preparing=False, signature="plant"):
    r.observe(
        START + timedelta(seconds=seconds),
        soc,
        solar_only=solar,
        preparing=preparing,
        signature=signature,
    )


def test_observation_time_is_diagnostic_and_excludes_outages():
    r = ReserveRuntime()
    observe(r, 0)
    observe(r, MAX_OBSERVATION_GAP_SECONDS)
    assert r.observed_seconds == MAX_OBSERVATION_GAP_SECONDS
    r.interrupt()
    observe(r, 48 * 3600)
    assert r.observed_seconds == MAX_OBSERVATION_GAP_SECONDS
    observe(r, 48 * 3600 + 10, signature="replacement_battery")
    assert r.observed_seconds == 0
    assert r.hold_soc == 80


def test_intent_does_not_follow_involuntary_loss_or_grid_charge():
    r = ReserveRuntime()
    observe(r, 0, 80)
    observe(r, 300, 79)
    assert r.hold_soc == 80
    observe(r, 600, 78, preparing=True)
    assert r.hold_soc == 79
    observe(r, 900, 80, solar=False)
    observe(r, 1200, 82, solar=True)
    assert r.hold_soc == 79
    observe(r, 1500, 83, solar=True)
    assert r.hold_soc == 80
    # Neither downtime nor a backwards clock may award observed time/energy.
    before = r.export()
    observe(r, 7200, 90)
    observe(r, 7000, 90)
    assert r.export() == before


def test_restart_restores_intent_without_counting_downtime():
    r = ReserveRuntime()
    observe(r, 0, 80)
    observe(r, 300, 80)
    restored = ReserveRuntime()
    restored.restore(r.export())
    observe(restored, 4000, 90)
    assert restored.hold_soc == 80
    assert restored.observed_seconds == 300


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"hold_soc": float("nan"), "observed_seconds": -1},
        {"hold_soc": 101, "observed_seconds": "bad"},
    ],
)
def test_corrupt_state_cannot_invent_reserve_intent(value):
    r = ReserveRuntime()
    r.restore(value)
    assert r.observed_seconds == 0
    assert r.hold_soc is None


def test_forecast_control_needs_no_shadow_or_additional_sensor_setup():
    assert (
        _validate_support_entities({CONF_RESERVE_MODE: "shadow"})
        == "reserve_requires_coordinated_support"
    )
    c = {CONF_RESERVE_MODE: "active", CONF_INVERTER_LIMIT_ENTITY: LIMIT}
    assert _validate_support_entities(c) is None
    c |= {
        CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
        CONF_SUPPORT_DC24_SWITCH: PSU24,
        CONF_DCDC_SWITCH: DCDC,
    }
    assert _validate_support_entities(c) is None
    c[CONF_RESERVE_TRANSFER_VERIFIED] = True
    assert _validate_support_entities(c) is None


async def test_bounded_inverter_limit_is_written_and_confirmed(rig, hass):
    c, calls, _, _ = rig
    c._reserve_inverter_limit_w = 125
    await execute(c, inverter=True)
    assert (LIMIT, 125) in calls
    assert c._inverter_limit_confirmed(False)
    assert not any(entity == LIMIT and value == 2300 for entity, value in calls)


async def test_grid_loss_restores_dc_before_waiting_for_dead_inverter(rig, hass):
    c, calls, dead, _ = rig
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
        }
    )
    hass.states.async_set("binary_sensor.grid", "off")
    hass.states.async_set(DCDC, "off")
    hass.states.async_set(PSU24, "on")
    dead.add(LIMIT)
    await execute(c, dc24=True, dc48=True)
    assert calls[:2] == [(DCDC, True), (PSU24, False)]
    assert calls.index((DCDC, True)) < calls.index((LIMIT, 0))
    assert c._coordinated_support_diag["reason"] == "grid_supply_unavailable"


async def test_failed_dc_restore_keeps_existing_source(rig, hass):
    c, calls, dead, _ = rig
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c.raw_config.update(
        {
            CONF_RESERVE_MODE: "active",
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: True,
        }
    )
    hass.states.async_set(DCDC, "off")
    hass.states.async_set(PSU24, "on")
    dead.add(DCDC)
    await execute(c, dc24=True)
    assert calls == [(DCDC, True), (LIMIT, 0)]
    assert hass.states.get(PSU24).state == "on"
    assert float(hass.states.get(LIMIT).state) == 0


@pytest.mark.parametrize(
    "value,expected",
    [
        ("on", True),
        ("AC_INPUT_1", True),
        ("AC_INPUT_2", True),
        ("off", False),
        ("DISCONNECTED", False),
        ("unknown", None),
    ],
)
async def test_grid_signal_requires_known_fresh_state(rig, hass, value, expected):
    c, *_ = rig
    c.raw_config[CONF_RESERVE_GRID_ENTITY] = "sensor.grid"
    hass.states.async_set("sensor.grid", value)
    assert c._reserve_grid_available() is expected
    with patch(
        "custom_components.battery_manager.coordinator.dt_util.utcnow",
        return_value=dt_util.utcnow() + timedelta(seconds=31),
    ):
        assert c._reserve_grid_available() is None


async def test_solar_evidence_requires_fresh_power_units(rig, hass):
    c, *_ = rig
    assert c._reserve_power(None) is None
    hass.states.async_set("sensor.power", "1.2", {"unit_of_measurement": "kW"})
    assert c._reserve_power("sensor.power") == 1200
    hass.states.async_set("sensor.power", "12", {"unit_of_measurement": "kWh"})
    assert c._reserve_power("sensor.power") is None


@pytest.mark.parametrize(
    "initial_mode,verified", [("shadow", True), ("active", True), ("active", False)]
)
async def test_immediate_active_and_optional_shadow_use_real_plans(
    rig, hass, freezer, initial_mode, verified
):
    from test_coordinator import ENTRY_DATA

    c, calls, _, _ = rig
    c.raw_config.update(
        {
            **ENTRY_DATA,
            CONF_RESERVE_MODE: initial_mode,
            CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid",
            CONF_RESERVE_TRANSFER_VERIFIED: verified,
            "operation_pv_power_entity": "sensor.actual_pv",
            "operation_import_power_entity": "sensor.actual_import",
        }
    )
    hass.states.async_set("binary_sensor.grid", "on")
    hass.states.async_set("sensor.actual_pv", "100", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.actual_import", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.test_soc", "80")
    for entity in ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"):
        hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})
    # Foreground plan uses real core/actuation. No production delays or
    # state listeners that could immediately replan test-generated reports.
    c._save_persistent_state = Mock()
    # The freezer fixture keeps foreground plans in their original time slot:
    # disabling boundary scheduling alone still lets the stale-input guard
    # reject a calculation that crosses a real hour boundary under coverage.
    # This test owns each transition; boundary behavior is covered separately.
    c._arm_plan_boundary = Mock()
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    assert c.data["reserve"]["mode"] == initial_mode
    assert c.data["reserve"]["dc24_transfer_verified"] is verified
    assert c.data["reserve"]["inverter_limit_w"] == 0
    assert c.data["reserve"]["shadow_required_hours"] == 0
    # Active preservation requests DC sources immediately; shadow only reports.
    assert c.data["support_dc24"] is (initial_mode == "active" and verified)
    assert c.data["support_dc48"] is (initial_mode == "active")
    if initial_mode == "shadow":
        assert not any(entity in (PSU24, PSU48) and value for entity, value in calls)
    c.raw_config[CONF_RESERVE_MODE] = "active"
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    assert c.data["reserve"]["mode"] == "active"
    assert c.data["inverter_recommendation"] is False
    assert (LIMIT, 0) in calls
    assert ((PSU24, True) in calls) is verified
    assert (PSU48, True) in calls
    assert c._persistent_payload()["reserve"]["hold_soc"] == 80
    assert c._reserve_runtime.observed_seconds < 60
    # Turning the policy off clears the retained policy state.
    c.raw_config[CONF_RESERVE_MODE] = "off"
    c.data = await c._async_update_data()
    if c._switch_task:
        await c._switch_task
    assert c.data["reserve"]["mode"] == "off"
    assert c._reserve_runtime.observed_seconds == 0
    # Forecast or SOC loss interrupts observed shadow time as well.
    c._get_soc = Mock(return_value=None)
    c._first_success_done = True
    with pytest.raises(Exception, match="No valid input data"):
        await c._async_update_data()
    assert c._reserve_runtime._last_at is None


@pytest.mark.parametrize(
    "actor,mode,expected",
    [
        (None, None, "off"),
        (LIMIT, None, "active"),
        (LIMIT, "off", "off"),
        (LIMIT, "shadow", "shadow"),
    ],
)
async def test_existing_coordinated_installation_defaults_active_without_history(
    hass, actor, mode, expected
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.battery_manager.config_flow import _d
    from custom_components.battery_manager.const import DOMAIN
    from custom_components.battery_manager.coordinator import BatteryManagerCoordinator

    data = {CONF_INVERTER_LIMIT_ENTITY: actor}
    if mode is not None:
        data[CONF_RESERVE_MODE] = mode
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    c = BatteryManagerCoordinator(hass, entry)
    assert c.raw_config[CONF_RESERVE_MODE] == expected
    assert _d(data, CONF_RESERVE_MODE) == expected
    assert c.build_system_config().reserve.enabled is (expected == "active")
    assert c._reserve_runtime.observed_seconds == 0


async def test_missing_grid_evidence_blocks_psus_but_not_forecast_preparation(
    rig, hass
):
    c, calls, *_ = rig
    c.raw_config[CONF_RESERVE_MODE] = "active"
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c._reserve_inverter_limit_w = 125
    await execute(c, dc24=True, dc48=True, inverter=True)
    assert (LIMIT, 125) in calls
    assert not any(entity in (PSU24, PSU48) and value for entity, value in calls)
    assert hass.states.get(DCDC).state == "on"
    assert c._inverter_recommendation


async def test_unverified_rail_cannot_be_transferred_by_active_policy(rig, hass):
    c, calls, *_ = rig
    c.raw_config[CONF_SOC_ENTITY] = "sensor.test_soc"
    hass.states.async_set("sensor.test_soc", "80")
    c.raw_config.update(
        {CONF_RESERVE_MODE: "active", CONF_RESERVE_GRID_ENTITY: "binary_sensor.grid"}
    )
    hass.states.async_set("binary_sensor.grid", "on")
    await execute(c, dc24=True, dc48=True)
    assert (PSU24, True) not in calls
    assert (DCDC, False) not in calls
    assert (PSU48, True) in calls
    assert c._coordinated_support_diag["dc24_block_reason"] == "transfer_unverified"
    assert c._coordinated_support_diag["desired"]["dc24"] is False


def test_legacy_zero_reference_survives_restart_but_unknown_solar_never_earns_credit():
    runtime = ReserveRuntime()
    runtime.restore({"hold_soc": 0, "observed_seconds": 300, "signature": "plant"})
    observe(runtime, 3600, 6, solar=False)
    observe(runtime, 3900, 30, solar=False)
    assert runtime.hold_soc == 0
    assert runtime.observed_seconds == 600
    assert runtime.export() == {
        "policy_version": 1,
        "hold_soc": 0,
        "observed_seconds": 600,
        "signature": "plant",
    }
    # The first newly verifiable point does not credit the preceding unknown
    # interval; only a second verified endpoint can attribute a subsequent gain.
    observe(runtime, 4200, 50, solar=True)
    assert runtime.hold_soc == 0
    observe(runtime, 4500, 51, solar=True)
    assert runtime.hold_soc == 1


@pytest.mark.parametrize("invalid", [True, 10**1000, float("inf"), "6", [], {}])
def test_malformed_reference_is_discarded_without_inventing_a_control_target(invalid):
    runtime = ReserveRuntime()
    runtime.restore({"hold_soc": invalid, "observed_seconds": invalid, "signature": []})
    assert runtime.export() == {
        "policy_version": 1,
        "hold_soc": None,
        "observed_seconds": 0,
        "signature": None,
    }


@pytest.mark.parametrize(
    "stored, expected", [(None, 1), (True, 1), (0, 1), (1, 1), (2, 2), (3, 3)]
)
def test_policy_migration_marker_survives_observation_until_confirmed(stored, expected):
    runtime = ReserveRuntime()
    assert runtime.policy_version == 2
    runtime.restore({"policy_version": stored, "hold_soc": 80, "signature": "plant"})
    observe(runtime, 0, 80)
    assert runtime.policy_version == expected
    restored = ReserveRuntime()
    restored.restore(runtime.export())
    assert restored.policy_version == expected
    runtime.policy_version = 2
    assert runtime.export()["policy_version"] == 2


def test_diagnostics_use_typed_decision_and_keep_history_out_of_physical_values():
    from dataclasses import replace

    from custom_components.battery_manager.core.model import (
        HourSlot,
        PlanInputs,
        ReserveDecision,
        SystemConfig,
    )
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.reserve_runtime import reserve_diagnostics

    config = SystemConfig()
    inputs = PlanInputs(START, 80, (HourSlot(0, START, 1, 0, 0, 0, 0),))
    baseline = plan(config, inputs)
    decision = ReserveDecision(
        preparation_horizon_end=START + timedelta(days=2),
        inverter_limit_w=125.26,
        headroom_wh=345.67,
        unavoidable_export_wh=678.91,
        reason="pv_headroom_preparation",
    )
    result = replace(
        baseline, trajectory=replace(baseline.trajectory, reserve_decision=decision)
    )
    runtime = ReserveRuntime()
    runtime.restore({"hold_soc": 0, "observed_seconds": 300, "signature": "old"})
    diagnostics = reserve_diagnostics(
        config, inputs, result, baseline, runtime, "active"
    )
    assert (
        diagnostics["preparation_horizon_end"]
        == (START + timedelta(days=2)).isoformat()
    )
    assert diagnostics["decision_reason"] == "pv_headroom_preparation"
    assert diagnostics["headroom_wh"] == 345.7
    assert diagnostics["unavoidable_export_wh"] == 678.9
    assert diagnostics["inverter_limit_w"] == 125.3
    assert diagnostics["historical_reference_soc_percent"] == 0
    assert diagnostics["reference_semantics"] == "historical_observation_only"
    assert runtime.hold_soc == 0
    historical = reserve_diagnostics(
        config, inputs, baseline, baseline, ReserveRuntime(), "shadow"
    )
    assert historical["preparation_horizon_end"] is None
    assert historical["decision_reason"] is None
    assert historical["headroom_wh"] == 0
    assert historical["unavoidable_export_wh"] == 0
    assert historical["inverter_limit_w"] == 0
    assert historical["historical_reference_soc_percent"] is None
