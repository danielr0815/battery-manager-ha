"""Coordinate provenance crosses planner/persistence without declaring a fault."""

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.util import dt as dt_util
from test_coordinated_actuation import PSU48
from test_coordinated_actuation import rig as rig
from test_voltage_gate_runtime import fresh
from test_voltage_gate_runtime import gate as gate

from custom_components.battery_manager.core.model import HourSlot, PlanInputs
from custom_components.battery_manager.runtime_persistence import persistent_payload
from custom_components.battery_manager.voltage_gate import CoordinatePlanChanged


def transition(c, phase, *, new_epoch=False):
    monitor = c.voltage_gate.monitor
    monitor.phase = phase
    monitor.generation += 1
    monitor.has_reconciled = True
    if new_epoch:
        monitor.epoch += 1


async def test_hold_and_settling_completion_require_new_plan_provenance(gate):
    c, *_ = gate
    vg = c.voltage_gate
    old = vg.coordinate_snapshot(dt_util.now())
    vg.published_coordinate = old
    transition(c, "reconciling", new_epoch=True)
    assert not vg.coordinate_permission_valid(old)
    held = vg.coordinate_snapshot(dt_util.now())
    assert not vg.coordinate_permission_valid(held)
    assert vg.coordinate_permission_valid(held, direct_pv_proven=True)
    transition(c, "settling")
    assert not vg.coordinate_permission_valid(held, direct_pv_proven=True)
    settling = vg.coordinate_snapshot(dt_util.now())
    transition(c, "tracking")
    assert not vg.coordinate_permission_valid(settling)
    assert not vg.coordinate_permission_valid(None)  # No revival of legacy grants.
    current = vg.coordinate_snapshot(dt_util.now())
    assert vg.coordinate_permission_valid(current)
    assert vg.published_coordinate is old  # Completion itself never stamps a plan.


async def test_coordinate_change_during_cpu_discards_plan_informatively(gate):
    c, *_ = gate
    now = dt_util.now()
    config = c.build_system_config()
    inputs = PlanInputs(
        now,
        82,
        (HourSlot(0, now, 1, now.hour, 0, 100, 35),),
        soc_coordinate=c.voltage_gate.coordinate_snapshot(now),
    )
    c._async_planning_protection = AsyncMock()
    c._get_soc = Mock(return_value=82)

    async def cpu(*_args):
        transition(c, "reconciling", new_epoch=True)
        return object()

    c._planning.async_run = AsyncMock(side_effect=cpu)
    with pytest.raises(CoordinatePlanChanged):
        await c._async_plan("test", config, inputs)
    assert c._planning.async_run.call_args.args[4] is inputs


async def test_normal_reconciliation_is_pending_not_update_failed(gate):
    c, calls, *_ = gate
    c._schedule_replan = Mock()
    c.data = {"valid": True, "forecast": ["last published forecast"]}
    c._async_update_data_serial = AsyncMock(side_effect=CoordinatePlanChanged)
    result = await c._async_update_data()
    assert result["valid"]
    assert result["forecast"] == ["last published forecast"]
    assert result["planning_pending"] == "soc_coordinate_update"
    c._schedule_replan.assert_called_once()
    assert not calls


async def test_restart_of_settling_has_no_energy_or_settling_credit(
    gate, hass, freezer
):
    c, *_ = gate
    c.voltage_gate.coordinate_snapshot(dt_util.now())
    transition(c, "settling", new_epoch=True)
    c.voltage_gate.monitor._settled_seconds = 500
    payload = persistent_payload(c)["voltage_gate"]
    assert payload["version"] == 2
    assert "_points" not in str(payload)
    epoch = c.voltage_gate.monitor.epoch
    c.voltage_gate.restore(payload)
    assert c.voltage_gate.monitor.epoch == epoch
    assert c.voltage_gate.monitor.phase == "reconciling"
    assert c.voltage_gate.monitor.diagnostic()["settled_covered_seconds"] == 0
    assert c.voltage_gate.monitor.diagnostic()["residual"] is None
    freezer.tick(1000)
    fresh(hass, soc=90, power=600)
    c.voltage_gate.observe(dt_util.now())
    assert c.voltage_gate.monitor.diagnostic()["residual"]["covered_seconds"] == 0
    assert c.voltage_gate.monitor.epoch == epoch
    assert not c.voltage_gate.coordinate_permission_valid(None)


async def test_version_one_restore_does_not_invent_reconciliation(gate):
    c, *_ = gate
    c.voltage_gate._bind()
    legacy = {
        "version": 1,
        "epoch": c.voltage_gate.learner.epoch,
        "fingerprint": c.voltage_gate.learner.fingerprint,
        "samples": [],
    }
    c.voltage_gate.restore(legacy)
    assert c.voltage_gate.monitor.phase == "tracking"
    assert not c.voltage_gate.monitor.events
    assert not c.voltage_gate.monitor.has_reconciled
    assert c.voltage_gate.coordinate_permission_valid(None)


async def test_incomplete_v2_coordinate_restore_quarantines_active_projection(gate):
    from custom_components.battery_manager.gate_learning import GateSample

    c, *_ = gate
    c.voltage_gate._bind()
    c.voltage_gate.learner.samples = [
        GateSample(dt_util.now().timestamp(), 55, 49.56, 0.7)
    ]
    broken_v2 = c.voltage_gate.learner.export()  # Missing coordinator provenance.
    c.voltage_gate.restore(broken_v2)
    assert c.voltage_gate.monitor.phase == "reconciling"
    assert not c.voltage_gate.learner.samples
    assert c.voltage_gate.learner.history
    assert not c.voltage_gate.coordinate_permission_valid(None)


async def test_new_epoch_quarantines_old_and_running_natural_learning(
    gate, hass, freezer
):
    from custom_components.battery_manager.gate_learning import GateSample

    c, calls, *_ = gate
    c._schedule_replan = Mock()
    c.appliances.plan_failed = Mock(wraps=c.appliances.plan_failed)
    c.voltage_gate.observe(dt_util.now())
    c.voltage_gate.learner.samples = [
        GateSample(dt_util.now().timestamp() - 600, 55, 49.56, 0.7)
    ]
    before = c.voltage_gate.monitor.epoch
    freezer.tick(5)
    fresh(hass, 49.56, 91, -35)
    await hass.async_block_till_done()
    c.voltage_gate.observe(dt_util.now())
    assert c.voltage_gate.monitor.epoch == before + 1
    assert not c.voltage_gate.learner.samples
    assert c.voltage_gate.learner.history
    assert c.voltage_gate.learner.reason == "coordinate_quarantined"
    assert not c.voltage_gate.bands(dt_util.now())
    c.appliances.plan_failed.assert_called_once()
    assert not calls


async def test_psu_electrical_evidence_is_independent_of_soc_epoch(gate, hass):
    c, *_ = gate
    c.raw_config["support_dc48_output_power_entity"] = "sensor.psu_dc"
    hass.states.async_set(PSU48, "on", force_update=True)
    hass.states.async_set("sensor.psu_dc", "40", {"unit_of_measurement": "W"})
    now = dt_util.now()
    before = c.voltage_gate.psu.capture(c, now)[1]
    assert before.quality == "measured"
    transition(c, "reconciling", new_epoch=True)
    after = c.voltage_gate.psu.capture(c, now)[1]
    assert before == after
    diag = c.voltage_gate.diagnostic(now)
    assert diag["psu_observations"][1]["present_delivery_w"] == 40
    assert diag["psu_observations"][1]["efficiency"] is None


async def test_inflight_debounce_cannot_swallow_post_coordinate_refresh(gate, hass):
    import asyncio

    c, *_ = gate
    c._schedule_replan = Mock()
    finished = asyncio.Event()

    async def active():
        await finished.wait()

    task = c.hass.async_create_task(active())
    c._debounce_task = task
    c.voltage_gate.request_fresh_plan()
    c.voltage_gate.request_fresh_plan()
    assert not c._schedule_replan.called
    finished.set()
    await task
    await hass.async_block_till_done()
    c._schedule_replan.assert_called_once()


async def test_detached_publication_callback_cannot_mutate_unloaded_monitor(gate):
    from types import SimpleNamespace

    c, *_ = gate
    c.voltage_gate.observe(dt_util.now())
    before = c.voltage_gate.monitor.diagnostic()
    event = SimpleNamespace(
        data={"entity_id": "sensor.soc", "new_state": None},
        time_fired=dt_util.utcnow(),
    )
    c.voltage_gate.stop_observing()
    c.voltage_gate._coordinate_publication(event)
    assert c.voltage_gate.monitor.diagnostic() == before


async def test_real_coordinator_publishes_complete_authority_before_actor_await(
    gate, hass
):
    """Adversarial old ON record cannot accompany a new OFF plan revision."""
    from datetime import timedelta

    from custom_components.battery_manager.coordinated_supply import (
        capture_supply_request,
    )
    from custom_components.battery_manager.core.optimize import plan
    from custom_components.battery_manager.voltage_gate import requested_dc48

    c, calls, *_ = gate
    now = dt_util.now()
    for key, entity in (
        ("pv_forecast_today_entity", "sensor.synthetic_today"),
        ("pv_forecast_tomorrow_entity", "sensor.synthetic_tomorrow"),
        ("pv_forecast_day_after_entity", "sensor.synthetic_later"),
    ):
        c.raw_config[key] = entity
        hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})
    config = c.build_system_config()
    old_inputs = PlanInputs(
        now - timedelta(seconds=10),
        82,
        (
            HourSlot(0, now, 1, now.hour, 0, 100, 80),
            HourSlot(
                1, now + timedelta(hours=1), 1, (now.hour + 1) % 24, 4000, 100, 80
            ),
        ),
        soc_coordinate=c.voltage_gate.coordinate_snapshot(now),
    )
    old_result = plan(config, old_inputs)
    assert old_result.inverter_on  # Real Core result, not a forced ON fixture.
    old_record = (config, old_inputs, old_result)
    c._last_planner_recording = old_record
    c._coordinated_inverter_target = True
    old_revision = c.live_ac.plan_revision
    fresh(hass, soc=10, power=-35)
    accepted = []

    async def calculate(_phase, new_config, inputs):
        result = plan(new_config, inputs)
        assert not result.inverter_on
        accepted.append((new_config, inputs, result))
        return result

    class ObservedBoundary(Exception):
        pass

    async def first_await(*_args):
        assert c._last_planner_recording is not old_record
        new_config, inputs, result = accepted[-1]
        assert c._last_planner_recording == accepted[-1]
        assert c.live_ac.plan_revision == old_revision + 1
        assert c._last_planner_recording_revision == c.live_ac.plan_revision
        assert not c._coordinated_inverter_target
        assert c.voltage_gate.published_coordinate is inputs.soc_coordinate
        request = capture_supply_request(
            c,
            {"dc24": result.support_dc24_now, "dc48": requested_dc48(result)},
            c._coordinated_inverter_target,
            now,
        )
        assert request.plan_revision == c.live_ac.plan_revision
        assert request.soc_coordinate is inputs.soc_coordinate
        assert request.sources == (result.support_dc24_now, requested_dc48(result))
        assert not request.inverter
        raise ObservedBoundary

    c._async_plan = AsyncMock(side_effect=calculate)
    c._latch_shadow_active = AsyncMock(side_effect=first_await)
    with pytest.raises(ObservedBoundary):
        await c._async_update_data_serial()
    assert not calls  # Stop at the first post-publication await, before actuators.
