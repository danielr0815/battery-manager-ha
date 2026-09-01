"""Central cascade actor ordering and proof-window tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import cascade_manager as cascade_manager_module
from custom_components.battery_manager.binary_sensor import CascadeRecommendationSensor
from custom_components.battery_manager.cascade_manager import CascadeManager
from custom_components.battery_manager.const import (
    ACTOR_MODE_SHARED,
    CONF_CASCADE_ACTOR_TIMEOUT_S,
    CONF_CASCADE_MEMBER_IDS,
    CONF_CASCADE_TERMINAL_LOAD_ID,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_INPUT_ACTOR_MODE,
    CONF_LOAD_OUTPUT_ACTOR_MODE,
    CONF_LOAD_OUTPUT_POWER_ENTITY,
    CONF_LOAD_OUTPUT_SWITCH,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_SOC_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator
from custom_components.battery_manager.core import (
    CascadeMember,
    CascadeMemberFlow,
    CascadeSlotFlow,
    CascadeSourceSegment,
    HourSlot,
    LoadCascade,
    PlanInputs,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
    plan,
)
from custom_components.battery_manager.sensor import CascadeModeSensor
from custom_components.battery_manager.switch import CascadeAutomationSwitch


class _States:
    def __init__(self, now: datetime) -> None:
        self.values = {
            "switch.input": SimpleNamespace(state="off", attributes={}),
            "switch.gate": SimpleNamespace(state="off", attributes={}),
            "switch.output": SimpleNamespace(state="off", attributes={}),
            "switch.leaf": SimpleNamespace(state="off", attributes={}),
            "sensor.soc": SimpleNamespace(
                state="90",
                attributes={},
                last_updated=now,
                last_reported=now,
            ),
            "sensor.output_power": SimpleNamespace(
                state="300",
                attributes={},
                last_updated=now,
                last_reported=now,
            ),
            "sensor.input_power": SimpleNamespace(
                state="0",
                attributes={},
                last_updated=now,
                last_reported=now,
            ),
        }

    def get(self, entity_id):
        return self.values.get(entity_id)


class _Coordinator:
    def __init__(
        self,
        now: datetime,
        *,
        shared: bool = False,
        shared_input: bool = False,
    ) -> None:
        storage_data = {
            CONF_LOAD_SOC_ENTITY: "sensor.soc",
            CONF_LOAD_CONTROL_SWITCH: "switch.input",
            CONF_LOAD_CHARGE_ENABLE: "switch.gate",
            CONF_LOAD_OUTPUT_SWITCH: "switch.output",
            CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.output_power",
            CONF_LOAD_POWER_ENTITY: "sensor.input_power",
        }
        if shared:
            storage_data[CONF_LOAD_OUTPUT_ACTOR_MODE] = ACTOR_MODE_SHARED
        if shared_input:
            storage_data[CONF_LOAD_INPUT_ACTOR_MODE] = ACTOR_MODE_SHARED
        self.entry = SimpleNamespace(
            entry_id="entry",
            subentries={
                "b1": SimpleNamespace(
                    subentry_type=SUBENTRY_TYPE_LOAD,
                    data=storage_data,
                    title="B1",
                ),
                "leaf": SimpleNamespace(
                    subentry_type=SUBENTRY_TYPE_LOAD,
                    data={CONF_LOAD_CONTROL_SWITCH: "switch.leaf"},
                    title="Leaf",
                ),
                "chain": SimpleNamespace(
                    subentry_type=SUBENTRY_TYPE_CASCADE,
                    data={
                        CONF_CASCADE_MEMBER_IDS: ["b1"],
                        CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
                    },
                    title="Chain",
                ),
            },
        )
        self.hass = SimpleNamespace(states=_States(now))
        self._cascade_state = {}
        self.calls = []
        self.saved = 0

    def _read_float(self, entity_id):
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return float(state.state)

    def _entity_is_on(self, entity_id):
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    async def _switch_entity(self, entity_id, turn_on, *, actor_owner=None):
        self.calls.append((entity_id, turn_on))
        self.hass.states.values[entity_id].state = "on" if turn_on else "off"
        return True

    def _save_persistent_state(self):
        self.saved += 1

    def load_bm_enabled(self, _load_id):
        return True


class _LiveIncidentCoordinator(_Coordinator):
    """Exact actor topology from the 2026-09-01 Bad cascade incident."""

    def __init__(self, now: datetime) -> None:
        super().__init__(now, shared_input=True)
        self.entry.subentries = {
            "b1": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_LOAD,
                data={
                    CONF_LOAD_SOC_ENTITY: "sensor.b1_soc",
                    CONF_LOAD_CONTROL_SWITCH: "switch.bad_waschmaschine",
                    CONF_LOAD_CHARGE_ENABLE: "input_boolean.charge_b1",
                    CONF_LOAD_INPUT_ACTOR_MODE: ACTOR_MODE_SHARED,
                    CONF_LOAD_OUTPUT_SWITCH: "switch.b1_output",
                    CONF_LOAD_OUTPUT_ACTOR_MODE: ACTOR_MODE_SHARED,
                    CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.b1_output_power",
                    CONF_LOAD_POWER_ENTITY: "sensor.b1_input_power",
                    "wake_timeout_s": 60,
                },
                title="Fossibot F2400-B",
            ),
            # B2's input is deliberately transparent: the live subentry has no
            # control_switch_entity.  It only contributes its output actor.
            "b2": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_LOAD,
                data={
                    CONF_LOAD_SOC_ENTITY: "sensor.b2_soc",
                    CONF_LOAD_CHARGE_ENABLE: "input_boolean.charge_b2",
                    CONF_LOAD_OUTPUT_SWITCH: "switch.b2_output",
                    CONF_LOAD_OUTPUT_ACTOR_MODE: ACTOR_MODE_SHARED,
                    CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.b2_output_power",
                    CONF_LOAD_POWER_ENTITY: "sensor.b2_input_power",
                    "wake_timeout_s": 60,
                },
                title="Fossibot F2400-B2",
            ),
            # The terminal dehumidifier is recommendation-only in production.
            "leaf": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_LOAD,
                data={},
                title="Entfeuchter Keller",
            ),
            "chain": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_CASCADE,
                data={
                    CONF_CASCADE_MEMBER_IDS: ["b1", "b2"],
                    CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
                },
                title="Bad",
            ),
        }
        for entity_id in (
            "switch.bad_waschmaschine",
            "input_boolean.charge_b1",
            "switch.b1_output",
            "input_boolean.charge_b2",
            "switch.b2_output",
        ):
            self.hass.states.values[entity_id] = SimpleNamespace(
                state="off", attributes={}
            )
        for entity_id, value in (
            ("sensor.b1_soc", "89.4"),
            ("sensor.b1_input_power", "0"),
            ("sensor.b1_output_power", "0"),
            ("sensor.b2_soc", "89.1"),
            ("sensor.b2_input_power", "0"),
            ("sensor.b2_output_power", "0"),
        ):
            self.hass.states.values[entity_id] = SimpleNamespace(
                state=value,
                attributes={},
                last_updated=now,
                last_reported=now,
            )


def _live_incident_plan(
    manager: CascadeManager, now: datetime
) -> tuple[SystemConfig, PlanInputs, object]:
    """Plan the live 89.25 % two-Fossibot topology at 22:29."""
    config = SystemConfig(
        loads=(
            SurplusLoad(
                "b1", "Fossibot F2400-B", 250, 0.05, 5, 5, True, 2000, 90, True
            ),
            SurplusLoad(
                "b2", "Fossibot F2400-B2", 499, 0.05, 5, 5, True, 2000, 90, True
            ),
            SurplusLoad("leaf", "Entfeuchter Keller", 426.1, 0.15, 15, 15, False),
        ),
        cascades=(
            LoadCascade(
                "chain",
                (
                    CascadeMember("b1", 20, 50, 0.9, 0.9, output_overhead_w=20),
                    CascadeMember("b2", 20, 50, 1, 1, output_overhead_w=20),
                ),
                "leaf",
            ),
        ),
    )
    # The first slot is the 31-minute remainder at 22:29; two complete night
    # slots follow.  This recreates the live immediate 0.6-kWh Aux booking.
    slots = tuple(
        HourSlot(
            index,
            now + timedelta(hours=index),
            31 / 60 if index == 0 else 1.0,
            (22 + index) % 24,
            0.0,
            0.0,
            0.0,
        )
        for index in range(3)
    )
    inputs = PlanInputs(
        now,
        75.0,
        slots,
        (
            SurplusLoadState(
                "b1", soc_percent=89.4, soc_source="live", soc_observed_at=now
            ),
            SurplusLoadState(
                "b2", soc_percent=89.1, soc_source="live", soc_observed_at=now
            ),
            SurplusLoadState("leaf"),
        ),
        cascade_runtime_states=(manager.runtime_state("chain"),),
    )
    return config, inputs, plan(config, inputs)


def _aux_plan(
    now: datetime,
    *,
    soc_start_percent: float = 90.0,
    soc_end_percent: float = 85.0,
):
    segment = CascadeSourceSegment(0, 0.0, 0.5, "aux", "b1", False, 150.0)
    return SimpleNamespace(
        flows=(
            CascadeSlotFlow(
                segments=(segment,),
                member_flows=(
                    CascadeMemberFlow(
                        "b1",
                        soc_start_percent,
                        soc_end_percent,
                        battery_discharge_wh=160,
                    ),
                ),
            ),
        ),
        provisional_live_soc_required=False,
        recovery_deadline=now + timedelta(hours=6),
        aggregate_soc_percent=90.0,
        aggregate_soc_stale=False,
        planned_root_energy_wh=0.0,
        planned_aux_energy_wh=150.0,
    )


def _root_plan(now: datetime):
    segment = CascadeSourceSegment(0, 0.0, 1.0, "direct_pv", None, True, 300.0)
    return SimpleNamespace(
        flows=(
            CascadeSlotFlow(
                root_input_wh=600.0,
                segments=(segment,),
                member_flows=(
                    CascadeMemberFlow(
                        "b1",
                        50,
                        60,
                        own_charge_input_wh=300,
                        battery_charge_wh=270,
                    ),
                ),
            ),
        ),
        provisional_live_soc_required=False,
        recovery_deadline=now + timedelta(hours=6),
    )


def _publish_soc(
    coordinator: _Coordinator,
    entity_id: str,
    value: str,
    observed_at: datetime,
) -> None:
    state = coordinator.hass.states.values[entity_id]
    state.state = value
    state.last_updated = observed_at
    state.last_reported = observed_at


def test_automation_switch_exposes_hands_off_reason() -> None:
    entity = SimpleNamespace(
        coordinator=SimpleNamespace(
            data={
                "cascade_plans": {
                    "chain": {
                        "phase": "hands_off",
                        "hands_off": True,
                        "fault": None,
                    }
                }
            }
        ),
        _subentry_id="chain",
    )

    attrs = CascadeAutomationSwitch.extra_state_attributes.fget(entity)

    assert attrs == {
        "phase": "hands_off",
        "hands_off": True,
        "fault": None,
        "fault_detail": None,
        "activation_blocked_reason": None,
        "activation_blocked_actors": [],
        "restart_reconcile_pending": False,
        "restart_reconcile_actors": [],
    }


def test_bulky_cascade_timelines_are_not_recorded() -> None:
    assert CascadeRecommendationSensor._unrecorded_attributes == {
        "member_details",
        "schedule",
    }
    assert CascadeModeSensor._unrecorded_attributes == {
        "member_details",
        "schedule",
    }


async def test_wake_order_and_two_sample_power_proof() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    manager._state("chain")["enabled"] = True

    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    assert coordinator.calls == [("switch.input", True)]
    assert manager._state("chain")["phase"] == "waking_members"

    _publish_soc(coordinator, "sensor.soc", "90", now + timedelta(seconds=1))
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(seconds=1),
    )
    assert coordinator.calls == [
        ("switch.input", True),
        ("switch.output", True),
        ("switch.leaf", True),
        ("switch.input", False),
    ]
    assert manager._state("chain")["phase"] == "proving"

    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    power = coordinator.hass.states.get("sensor.output_power")
    power.last_updated = now + timedelta(seconds=61)
    power.state = "310"
    await manager._apply_one(
        "chain",
        _aux_plan(now + timedelta(seconds=61)),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(seconds=61),
    )
    state = manager._state("chain")
    assert state["phase"] == "running"
    assert state["episode_day"] == "2026-08-23"
    assert state["recovery_pending"] == []


async def test_power_proof_tracks_forecast_recovery_below_fifty_percent() -> None:
    """A future below-target slot creates recovery debt before SOC falls."""
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    manager._state("chain")["enabled"] = True
    deep_plan = _aux_plan(now, soc_start_percent=90.0, soc_end_percent=40.0)
    live = (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf"))

    await manager._apply_one("chain", deep_plan, live, now)
    _publish_soc(coordinator, "sensor.soc", "90", now + timedelta(seconds=1))
    await manager._apply_one("chain", deep_plan, live, now + timedelta(seconds=1))
    await manager._apply_one("chain", deep_plan, live, now)
    power = coordinator.hass.states.get("sensor.output_power")
    power.last_updated = now + timedelta(seconds=61)
    power.state = "310"
    await manager._apply_one("chain", deep_plan, live, now + timedelta(seconds=61))

    assert manager._state("chain")["phase"] == "running"
    assert manager._state("chain")["recovery_pending"] == ["b1"]
    assert (
        manager._state("chain")["recovery_deadline"]
        == (now + timedelta(hours=6)).isoformat()
    )


async def test_sleeping_member_wakes_before_terminal_is_energised() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    coordinator.hass.states.values["sensor.soc"].state = "unavailable"
    manager = CascadeManager(coordinator)
    manager._state("chain")["enabled"] = True
    states = (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf"))

    await manager._apply_one("chain", _aux_plan(now), states, now)
    state = manager._state("chain")
    assert state["phase"] == "waking_members"
    assert coordinator.calls == [("switch.input", True)]

    _publish_soc(coordinator, "sensor.soc", "90", now + timedelta(seconds=30))
    await manager._apply_one(
        "chain", _aux_plan(now), states, now + timedelta(seconds=30)
    )
    assert state["phase"] == "proving"
    assert coordinator.calls[-2:] == [
        ("switch.leaf", True),
        ("switch.input", False),
    ]


async def test_fresh_numeric_power_telemetry_proves_member_awake() -> None:
    """A slow unchanged SOC must not hide otherwise live Fossibot telemetry."""
    now = datetime(2026, 9, 1, 7, 48)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    manager._state("chain")["enabled"] = True
    states = (SurplusLoadState("b1", soc_percent=85), SurplusLoadState("leaf"))

    await manager._apply_one("chain", _aux_plan(now), states, now)
    assert coordinator.calls == [("switch.input", True)]

    # Live incident: total-input resumed publishing after Root-ON while SOC's
    # own last_reported remained on its slower cadence.
    _publish_soc(
        coordinator,
        "sensor.input_power",
        "0",
        now + timedelta(seconds=8),
    )
    await manager._apply_one(
        "chain", _aux_plan(now), states, now + timedelta(seconds=10)
    )

    assert ("switch.output", True) in coordinator.calls
    assert manager._state("chain")["phase"] == "proving"


async def test_aux_wake_retry_survives_refreshes_before_episode_day_exists() -> None:
    """A timed-out wake waits 15 minutes instead of cycling Root each refresh."""
    now = datetime(2026, 9, 1, 7, 48)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    live = (SurplusLoadState("b1", soc_percent=85), SurplusLoadState("leaf"))

    await manager._apply_one("chain", _aux_plan(now), live, now)
    await manager._apply_one("chain", _aux_plan(now), live, now + timedelta(seconds=61))
    assert state["phase"] == "idle"
    assert state["retry_used"] is True
    retry_at = state["retry_at"]
    coordinator.calls.clear()

    await manager._apply_one("chain", _aux_plan(now), live, now + timedelta(seconds=70))

    assert state["retry_used"] is True
    assert state["retry_at"] == retry_at
    assert coordinator.calls == []


def test_aux_member_wake_is_reported_as_a_continuing_episode() -> None:
    """Latest-first replans must keep an accepted Aux wake in slot zero."""
    now = datetime(2026, 9, 1, 19, 2)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "phase": "waking_members",
            "source": "b1",
            "wake_mode": "aux",
        }
    )

    runtime = manager.runtime_state("chain")

    assert runtime.phase == "proving"
    assert runtime.active_source_id == "b1"


async def test_aux_member_wake_is_atomic_across_a_withdrawn_rolling_plan() -> None:
    """A rolling replan cannot power-cycle Root during an accepted wake."""
    now = datetime(2026, 9, 1, 19, 2)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    live = (SurplusLoadState("b1", soc_percent=89), SurplusLoadState("leaf"))

    await manager._apply_one("chain", _aux_plan(now), live, now)
    wake_deadline = state["wake_deadline"]
    coordinator.calls.clear()
    withdrawn = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=None,
    )

    await manager._apply_one("chain", withdrawn, live, now + timedelta(seconds=10))

    assert coordinator.calls == []
    assert state["phase"] == "waking_members"
    assert state["source"] == "b1"
    assert state["wake_deadline"] == wake_deadline
    assert state["retry_used"] is False

    # The same temporarily withdrawn plan must not prevent the already powered
    # member from completing the bounded wake once fresh telemetry arrives.
    _publish_soc(coordinator, "sensor.input_power", "0", now + timedelta(seconds=20))
    await manager._apply_one("chain", withdrawn, live, now + timedelta(seconds=20))

    assert coordinator.calls == [
        ("switch.output", True),
        ("switch.leaf", True),
        ("switch.input", False),
    ]
    assert state["phase"] == "proving"


async def test_live_two_fossibot_wake_does_not_cycle_root_every_ten_seconds() -> None:
    """Reproduce the live 22:29 topology and five rolling refreshes 1:1.

    B1 is the sole Root actor, B2 has a transparent input, the terminal has no
    actor, both SOCs stay at their pre-wake publication, and every refresh uses
    a fresh plan with the manager's current runtime state.  The observed bug
    issued Root OFF then ON on each cycle while it remained in waking_members.
    """
    started = datetime(2026, 9, 1, 22, 29, 34)
    coordinator = _LiveIncidentCoordinator(started)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True

    config, inputs, result = _live_incident_plan(manager, started)
    cascade = result.cascade_plans[0]
    assert cascade.aggregate_soc_percent == pytest.approx(89.25)
    assert cascade.planned_aux_energy_wh == pytest.approx(646.513, abs=1.0)
    assert cascade.flows[0].segments[0].source_load_id == "b1"

    await manager.async_apply(config, result, inputs.load_states, started)
    assert coordinator.calls == [("switch.bad_waschmaschine", True)]
    assert state["phase"] == "waking_members"
    wake_deadline = state["wake_deadline"]

    coordinator.calls.clear()
    for elapsed_s in (10, 20, 30, 40, 50):
        cycle_now = started + timedelta(seconds=elapsed_s)
        rolling_inputs = replace(
            inputs,
            now=cycle_now,
            cascade_runtime_states=(manager.runtime_state("chain"),),
        )
        rolling_result = plan(config, rolling_inputs)
        await manager.async_apply(
            config, rolling_result, rolling_inputs.load_states, cycle_now
        )

    assert coordinator.calls == []
    assert coordinator.hass.states.get("switch.bad_waschmaschine").state == "on"
    assert coordinator.hass.states.get("switch.b1_output").state == "off"
    assert state["phase"] == "waking_members"
    assert state["wake_deadline"] == wake_deadline


async def test_live_two_fossibot_full_switch_pass_has_one_root_owner(hass) -> None:
    """Run the exact plan through generic switching and CascadeManager.

    This includes the production order in `_async_update_data`: first the
    detached generic load pass is queued, then the cascade pass applies its
    actor transition.  B1 carries stale historical plug ownership as it did on
    the live system, so any ownership leak would reproduce Root OFF -> ON.
    """
    started = datetime(2026, 9, 1, 22, 29, 34)
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Battery Manager", version=2)
    entry.add_to_hass(hass)

    subentries = (
        ConfigSubentry(
            data={
                CONF_LOAD_SOC_ENTITY: "sensor.b1_soc",
                CONF_LOAD_CONTROL_SWITCH: "switch.bad_waschmaschine",
                CONF_LOAD_CHARGE_ENABLE: "input_boolean.charge_b1",
                CONF_LOAD_INPUT_ACTOR_MODE: ACTOR_MODE_SHARED,
                CONF_LOAD_OUTPUT_SWITCH: "switch.b1_output",
                CONF_LOAD_OUTPUT_ACTOR_MODE: ACTOR_MODE_SHARED,
                CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.b1_output_power",
                CONF_LOAD_POWER_ENTITY: "sensor.b1_input_power",
                "power_w": 250,
                "energy_limited": True,
                "capacity_wh": 2000,
                "target_soc_percent": 90,
                "min_runtime_min": 5,
                "min_off_min": 5,
                "input_off_policy": "auto",
                "wake_timeout_s": 60,
            },
            subentry_id="b1",
            subentry_type=SUBENTRY_TYPE_LOAD,
            title="Fossibot F2400-B",
            unique_id=None,
        ),
        ConfigSubentry(
            data={
                CONF_LOAD_SOC_ENTITY: "sensor.b2_soc",
                CONF_LOAD_CHARGE_ENABLE: "input_boolean.charge_b2",
                CONF_LOAD_OUTPUT_SWITCH: "switch.b2_output",
                CONF_LOAD_OUTPUT_ACTOR_MODE: ACTOR_MODE_SHARED,
                CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.b2_output_power",
                CONF_LOAD_POWER_ENTITY: "sensor.b2_input_power",
                "power_w": 499,
                "energy_limited": True,
                "capacity_wh": 2000,
                "target_soc_percent": 90,
                "min_runtime_min": 5,
                "min_off_min": 5,
                "wake_timeout_s": 60,
            },
            subentry_id="b2",
            subentry_type=SUBENTRY_TYPE_LOAD,
            title="Fossibot F2400-B2",
            unique_id=None,
        ),
        ConfigSubentry(
            data={
                "power_w": 426.1,
                "energy_limited": False,
                "min_runtime_min": 15,
                "min_off_min": 15,
            },
            subentry_id="leaf",
            subentry_type=SUBENTRY_TYPE_LOAD,
            title="Entfeuchter Keller",
            unique_id=None,
        ),
        ConfigSubentry(
            data={
                CONF_CASCADE_MEMBER_IDS: ["b1", "b2"],
                CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
            },
            subentry_id="chain",
            subentry_type=SUBENTRY_TYPE_CASCADE,
            title="Bad",
            unique_id=None,
        ),
    )
    for subentry in subentries:
        assert hass.config_entries.async_add_subentry(entry, subentry)

    calls: list[tuple[str, str]] = []

    async def turn_on(call) -> None:
        entity_id = call.data["entity_id"]
        calls.append(("turn_on", entity_id))
        hass.states.async_set(entity_id, "on")

    async def turn_off(call) -> None:
        entity_id = call.data["entity_id"]
        calls.append(("turn_off", entity_id))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)
    for entity_id in (
        "switch.bad_waschmaschine",
        "input_boolean.charge_b1",
        "switch.b1_output",
        "input_boolean.charge_b2",
        "switch.b2_output",
    ):
        hass.states.async_set(entity_id, "off")
    for entity_id, value in (
        ("sensor.b1_soc", "89.4"),
        ("sensor.b1_input_power", "0"),
        ("sensor.b1_output_power", "0"),
        ("sensor.b2_soc", "89.1"),
        ("sensor.b2_input_power", "0"),
        ("sensor.b2_output_power", "0"),
    ):
        hass.states.async_set(entity_id, value)

    coordinator = BatteryManagerCoordinator(hass, entry)
    # This test drives refreshes explicitly; do not let actor feedback schedule
    # an additional real `_async_update_data` with intentionally absent inputs.
    coordinator._listeners_setup = False
    if coordinator._unsub_state_listener is not None:
        coordinator._unsub_state_listener()
        coordinator._unsub_state_listener = None
    coordinator._cascade_state["chain"] = {"enabled": True, "phase": "idle"}
    coordinator._load_plug_owned["b1"] = True

    config, inputs, result = _live_incident_plan(coordinator.cascade_manager, started)
    durations = tuple(slot.duration for slot in inputs.slots)
    await coordinator._apply_load_switching(result, started, durations)
    await hass.async_block_till_done(wait_background_tasks=False)
    await coordinator.cascade_manager.async_apply(
        config, result, inputs.load_states, started
    )

    assert calls == [("turn_on", "switch.bad_waschmaschine")]
    calls.clear()
    for elapsed_s in (10, 20, 30, 40, 50):
        cycle_now = started + timedelta(seconds=elapsed_s)
        rolling_inputs = replace(
            inputs,
            now=cycle_now,
            cascade_runtime_states=(
                coordinator.cascade_manager.runtime_state("chain"),
            ),
        )
        rolling_result = plan(config, rolling_inputs)
        await coordinator._apply_load_switching(
            rolling_result,
            cycle_now,
            tuple(slot.duration for slot in rolling_inputs.slots),
        )
        await hass.async_block_till_done(wait_background_tasks=False)
        await coordinator.cascade_manager.async_apply(
            config, rolling_result, rolling_inputs.load_states, cycle_now
        )

    assert calls == []
    assert hass.states.get("switch.bad_waschmaschine").state == "on"


async def test_live_root_rejects_generic_off_at_entity_boundary(
    hass, monkeypatch
) -> None:
    """Reproduce the live Root OFF even if the load-ID guard is missed.

    The 2026-09-01 trace proves that a generic OFF reached the shared B1 Root
    while the cascade remained in ``waking_members``.  Recreate that exact
    last-boundary condition: the earlier load-ID ownership filter misses the
    action, but the physical entity is still a configured cascade actor.  The
    service boundary itself must remain fail-closed.
    """
    now = datetime(2026, 9, 1, 22, 29, 44)
    coordinator = _LiveIncidentCoordinator(now)
    manager = CascadeManager(coordinator)
    coordinator.cascade_manager = manager
    coordinator._switch_lock = asyncio.Lock()
    coordinator._cascade_actor_block_warned = set()
    coordinator._floor_guard_active = False
    coordinator._stale_shed_active = False
    coordinator._load_plug_owned = {"b1": True}
    coordinator._load_charging_active = {"b1": True}
    coordinator._last_load_switch = {}
    coordinator._load_last_off = {}
    coordinator._load_latch_hold = set()
    coordinator._load_run_deadline = {}
    coordinator._load_power_calibration_release = set()
    coordinator._load_off_timer = {}
    coordinator.data = None
    coordinator.async_update_listeners = lambda: None
    coordinator._cancel_off_timer = lambda _load_id: None

    async def switch_entity(entity_id: str, turn_on: bool) -> bool:
        return await BatteryManagerCoordinator._switch_entity(
            coordinator, entity_id, turn_on
        )

    coordinator._switch_entity = switch_entity
    coordinator.hass.services = hass.services
    hass.states.async_set("switch.bad_waschmaschine", "on")
    hass.states.async_set("input_boolean.charge_b1", "off")
    # Keep the lightweight topology and real HA states in sync for the central
    # ownership check and for the service handler below.
    coordinator.hass.states = hass.states
    calls: list[tuple[str, str]] = []

    async def turn_on(call) -> None:
        entity_id = call.data["entity_id"]
        calls.append(("turn_on", entity_id))
        hass.states.async_set(entity_id, "on")

    async def turn_off(call) -> None:
        entity_id = call.data["entity_id"]
        calls.append(("turn_off", entity_id))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("homeassistant", "turn_on", turn_on)
    hass.services.async_register("homeassistant", "turn_off", turn_off)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "waking_members",
            "source": "b1",
            "wake_mode": "aux",
        }
    )
    # Model the observed ownership leak before v0.34.3: the generic executor's
    # load-ID guard misses B1 even though its actor entity remains in topology.
    monkeypatch.setattr(manager, "managed_load_ids", lambda: set())

    await BatteryManagerCoordinator._execute_load_switching(
        coordinator,
        [
            (
                "b1",
                coordinator.entry.subentries["b1"].data,
                False,
                True,
                0.0,
                False,
                "plan off",
            )
        ],
        now,
    )

    assert calls == []
    assert hass.states.get("switch.bad_waschmaschine").state == "on"
    assert state["phase"] == "waking_members"


async def test_root_passthrough_and_safe_off_order() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    manager._state("chain")["enabled"] = True
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now,
    )
    assert manager._state("chain")["phase"] == "waking_members"
    _publish_soc(coordinator, "sensor.soc", "50", now + timedelta(seconds=1))
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now + timedelta(seconds=1),
    )
    assert ("switch.input", True) in coordinator.calls
    assert ("switch.output", True) in coordinator.calls
    assert ("switch.gate", True) in coordinator.calls
    assert ("switch.leaf", True) in coordinator.calls

    coordinator.calls.clear()
    assert await manager.async_safe_off("chain", "test")
    assert coordinator.calls == [
        ("switch.leaf", False),
        ("switch.output", False),
        ("switch.gate", False),
        ("switch.input", False),
    ]


async def test_root_start_waits_for_delayed_output_confirmation() -> None:
    """Delayed Fossibot feedback is our transition, not an external override."""
    now = datetime(2026, 8, 29, 23)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True

    async def delayed_switch(
        entity_id: str, turn_on: bool, *, actor_owner: str | None = None
    ) -> bool:
        coordinator.calls.append((entity_id, turn_on))
        entity = coordinator.hass.states.values[entity_id]
        if entity_id == "switch.output" and turn_on:
            asyncio.get_running_loop().call_later(0.02, setattr, entity, "state", "on")
        else:
            entity.state = "on" if turn_on else "off"
        return True

    coordinator._switch_entity = delayed_switch
    live = (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf"))

    await manager._apply_one("chain", _root_plan(now), live, now)
    _publish_soc(coordinator, "sensor.soc", "50", now + timedelta(seconds=1))
    await manager._apply_one("chain", _root_plan(now), live, now + timedelta(seconds=1))
    # A second immediate pass used to see output=off against claim=True and
    # hard-fault the exclusive actor before its delayed state publication.
    await manager._apply_one("chain", _root_plan(now), live, now)

    assert state["fault"] is None
    assert state["enabled"]
    assert state["phase"] == "root"
    assert state["claims"]["switch.output"] is True


async def test_root_wakes_each_member_before_its_output_is_enabled() -> None:
    """B2 cannot receive its AC-output command while it is still asleep."""
    now = datetime(2026, 8, 31, 9)
    coordinator = _Coordinator(now)
    coordinator.entry.subentries["b2"] = SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_LOAD,
        data={
            CONF_LOAD_SOC_ENTITY: "sensor.soc_b2",
            CONF_LOAD_CHARGE_ENABLE: "switch.gate_b2",
            CONF_LOAD_OUTPUT_SWITCH: "switch.output_b2",
            CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.output_power_b2",
        },
        title="B2",
    )
    coordinator.entry.subentries["chain"].data[CONF_CASCADE_MEMBER_IDS] = [
        "b1",
        "b2",
    ]
    coordinator.hass.states.values.update(
        {
            "sensor.soc_b2": SimpleNamespace(
                state="50",
                attributes={},
                last_updated=now,
                last_reported=now,
            ),
            "switch.gate_b2": SimpleNamespace(state="off", attributes={}),
            "switch.output_b2": SimpleNamespace(state="off", attributes={}),
            "sensor.output_power_b2": SimpleNamespace(
                state="0", attributes={}, last_updated=now
            ),
        }
    )
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    live = (
        SurplusLoadState("b1", soc_percent=50),
        SurplusLoadState("b2", soc_percent=50),
        SurplusLoadState("leaf"),
    )

    await manager._apply_one("chain", _root_plan(now), live, now)
    assert coordinator.calls == [("switch.input", True)]

    _publish_soc(coordinator, "sensor.soc", "50", now + timedelta(seconds=1))
    await manager._apply_one("chain", _root_plan(now), live, now + timedelta(seconds=1))
    assert coordinator.calls[-1] == ("switch.output", True)
    assert ("switch.output_b2", True) not in coordinator.calls
    assert state["wake_member_index"] == 1

    _publish_soc(coordinator, "sensor.soc_b2", "50", now + timedelta(seconds=2))
    await manager._apply_one("chain", _root_plan(now), live, now + timedelta(seconds=2))
    assert ("switch.output_b2", True) in coordinator.calls
    assert coordinator.calls.index(("switch.output", True)) < coordinator.calls.index(
        ("switch.output_b2", True)
    )
    assert state["phase"] == "root"


async def test_root_wake_timeout_faults_without_real_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 31, 9)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    monkeypatch.setattr(
        cascade_manager_module.ir, "async_create_issue", lambda *a, **k: None
    )

    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now,
    )
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now + timedelta(seconds=61),
    )

    assert state["fault"] == "root_transition_failed"
    assert state["fault_detail"] == {
        "entity_id": "sensor.soc",
        "target_state": "fresh_numeric_publication",
        "observed_state": "90",
        "kind": "wake_timeout",
    }
    assert state["fault_safe_off_complete"] is True
    assert not state["enabled"]


async def test_completed_fault_safe_off_releases_actors_for_manual_use() -> None:
    """A latched fault diagnoses the chain but does not fight the operator."""
    now = datetime(2026, 8, 31, 10)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": False,
            "phase": "fault",
            "fault": "root_transition_failed",
            "fault_safe_off_complete": False,
        }
    )
    coordinator.hass.states.values["switch.input"].state = "on"

    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now,
    )
    assert coordinator.hass.states.values["switch.input"].state == "off"
    assert state["fault_safe_off_complete"] is True

    coordinator.calls.clear()
    coordinator.hass.states.values["switch.input"].state = "on"
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now + timedelta(seconds=15),
    )

    assert coordinator.calls == []
    assert coordinator.hass.states.values["switch.input"].state == "on"
    assert state["fault"] == "root_transition_failed"


async def test_repeated_safe_off_does_not_rewrite_confirmed_off_actors() -> None:
    """Inactive refreshes cannot turn a toggle-like output into an ON/OFF loop."""
    now = datetime(2026, 8, 29, 23)
    coordinator = _Coordinator(now)
    coordinator.hass.states.values["switch.output"].state = "on"
    manager = CascadeManager(coordinator)

    assert await manager.async_safe_off("chain", "first")
    assert await manager.async_safe_off("chain", "second")

    assert coordinator.calls == [("switch.output", False)]
    assert coordinator.hass.states.values["switch.output"].state == "off"


async def test_actor_requires_confirmation_before_claiming() -> None:
    now = datetime(2026, 8, 29, 23)
    coordinator = _Coordinator(now)
    coordinator.entry.subentries["chain"].data[CONF_CASCADE_ACTOR_TIMEOUT_S] = 0.01
    manager = CascadeManager(coordinator)

    async def unconfirmed_switch(
        entity_id: str, turn_on: bool, *, actor_owner: str | None = None
    ) -> bool:
        coordinator.calls.append((entity_id, turn_on))
        return True

    coordinator._switch_entity = unconfirmed_switch

    assert not await manager._actor("chain", "switch.output", True)
    assert "switch.output" not in manager._state("chain")["claims"]
    assert manager._state("chain")["last_actor_error"] == {
        "entity_id": "switch.output",
        "target_state": "on",
        "observed_state": "off",
        "kind": "confirmation_timeout",
    }


async def test_root_fault_exposes_failed_actor_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 29, 23)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    monkeypatch.setattr(
        cascade_manager_module.ir, "async_create_issue", lambda *a, **k: None
    )

    async def fail_gate(
        entity_id: str, turn_on: bool, *, actor_owner: str | None = None
    ) -> bool:
        coordinator.calls.append((entity_id, turn_on))
        if entity_id == "switch.gate" and turn_on:
            return False
        coordinator.hass.states.values[entity_id].state = "on" if turn_on else "off"
        return True

    coordinator._switch_entity = fail_gate
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now,
    )
    _publish_soc(coordinator, "sensor.soc", "50", now + timedelta(seconds=1))
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now + timedelta(seconds=1),
    )

    assert state["fault"] == "root_transition_failed"
    assert state["fault_detail"] == {
        "entity_id": "switch.gate",
        "target_state": "on",
        "observed_state": "off",
        "kind": "service_failed",
    }


async def test_shared_actor_allows_manager_owned_state_transition() -> None:
    """A new plan may intentionally reverse the manager's previous claim."""
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now, shared_input=True)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True

    assert await manager.async_safe_off("chain", "test setup")
    assert state["claims"]["switch.input"] is False
    coordinator.calls.clear()

    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now,
    )
    _publish_soc(coordinator, "sensor.soc", "50", now + timedelta(seconds=1))
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now + timedelta(seconds=1),
    )

    assert state["enabled"]
    assert not state["hands_off"]
    assert state["fault"] is None
    assert state["phase"] == "root"
    assert state["claims"]["switch.input"] is True
    assert ("switch.input", True) in coordinator.calls


async def test_shared_foreign_change_enters_hands_off() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now, shared=True)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    state["claims"] = {"switch.output": False}
    coordinator.hass.states.values["switch.output"].state = "on"

    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    assert state["hands_off"]
    assert not state["enabled"]
    assert state["phase"] == "hands_off"
    assert coordinator.calls == []

    # Hands-off is a lasting ownership handover.  A later coordinator refresh
    # must not roll the operator's Shared-ON back to Safe-OFF.
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(seconds=15),
    )
    assert coordinator.hass.states.values["switch.output"].state == "on"
    assert coordinator.calls == []


async def test_shared_auto_off_is_adopted_when_fresh_plan_is_idle() -> None:
    """A zero-power plug's expected OFF is convergence, not a takeover."""
    now = datetime(2026, 8, 29, 23, 28)
    coordinator = _Coordinator(now, shared_input=True)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    state["phase"] = "root"
    state["claims"] = {"switch.input": True}
    coordinator.hass.states.values["switch.input"].state = "off"
    idle_plan = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=None,
    )

    await manager._apply_one(
        "chain",
        idle_plan,
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert state["enabled"]
    assert not state["hands_off"]
    assert state["phase"] == "idle"
    assert state["claims"]["switch.input"] is False
    assert coordinator.calls == []


async def test_running_shared_auto_off_converges_when_plan_is_withdrawn() -> None:
    """A stale runtime phase cannot turn a fresh Safe-OFF plan into hands-off."""
    now = datetime(2026, 8, 30, 0, 8)
    coordinator = _Coordinator(now, shared=True)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "running",
            "source": "b1",
            "episode_day": now.date().isoformat(),
            "recovery_pending": ["b1"],
            "claims": {"switch.output": True},
        }
    )
    # The external zero-power automation was faster than the coordinator; the
    # fresh plan agrees that the chain is now idle.
    coordinator.hass.states.values["switch.output"].state = "off"
    idle_plan = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=now + timedelta(hours=6),
    )

    await manager._apply_one(
        "chain",
        idle_plan,
        (SurplusLoadState("b1", soc_percent=40), SurplusLoadState("leaf")),
        now,
    )

    assert state["enabled"]
    assert not state["hands_off"]
    assert state["phase"] == "recovering"
    assert state["claims"]["switch.output"] is False


async def test_enable_requires_all_off_and_fresh_soc() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    coordinator.hass.states.values["sensor.soc"].state = "unavailable"
    assert not await manager.async_set_enabled("chain", True)
    coordinator.hass.states.values["sensor.soc"].state = "90"
    coordinator.hass.states.values["switch.output"].state = "on"
    assert not await manager.async_set_enabled("chain", True)
    assert manager._state("chain")["activation_blocked_reason"] == "actors_not_off"
    assert manager._state("chain")["activation_blocked_actors"] == ["switch.output"]
    coordinator.hass.states.values["switch.output"].state = "unavailable"
    assert not await manager.async_set_enabled("chain", True)
    assert manager._state("chain")["activation_blocked_actors"] == ["switch.output"]
    coordinator.hass.states.values["switch.output"].state = "off"
    assert await manager.async_set_enabled("chain", True)
    assert manager.enabled("chain")
    assert manager._state("chain")["activation_blocked_reason"] is None

    assert await manager.async_set_enabled("chain", False)
    assert not manager.enabled("chain")

    coordinator.calls.clear()
    coordinator.hass.states.values["switch.output"].state = "on"
    state = manager._state("chain")
    state["episode_day"] = now.date().isoformat()
    state["phase"] = "running"
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(days=1),
    )
    assert coordinator.hass.states.values["switch.output"].state == "on"
    assert coordinator.calls == []
    assert manager._state("chain")["fault"] is None


async def test_enable_discards_stale_wake_state_before_fresh_takeover() -> None:
    """Live 2026-09-01: OFF -> ON must not resume an old waking_members FSM."""
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": False,
            "phase": "waking_members",
            "source": "b1",
            "claims": {"switch.input": True},
            "retry_used": True,
            "retry_at": (now + timedelta(minutes=15)).isoformat(),
            "wake_mode": "root",
            "wake_member_index": 0,
            "wake_telemetry_reported_at": now.isoformat(),
            "wake_deadline": (now + timedelta(seconds=60)).isoformat(),
        }
    )

    assert await manager.async_set_enabled("chain", True)

    assert state["enabled"] is True
    assert state["phase"] == "idle"
    assert state["source"] is None
    assert state["claims"] == {}
    assert state["retry_used"] is False
    assert state["retry_at"] is None
    assert "wake_mode" not in state
    assert "wake_deadline" not in state


async def test_restored_aux_state_is_adopted_without_switching() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "waking_members",
            "source": "b1",
            "claims": {"switch.input": True, "switch.output": True},
            "wake_mode": "root",
            "wake_member_index": 0,
            "wake_deadline": (now + timedelta(seconds=60)).isoformat(),
        }
    )
    for entity_id in ("switch.output", "switch.leaf"):
        coordinator.hass.states.values[entity_id].state = "on"

    manager.normalize_restored_state()

    assert state["phase"] == "idle"
    assert state["restart_reconcile_pending"] is True
    assert state["restart_source_hint"] == "b1"
    runtime = manager.runtime_state("chain")
    assert runtime.phase == "proving"
    assert runtime.active_source_id == "b1"
    assert "wake_mode" not in state
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    assert coordinator.calls == []
    assert state["enabled"] is True
    assert state["phase"] == "proving"
    assert state["source"] == "b1"
    assert state["claims"] == {
        "switch.input": False,
        "switch.gate": False,
        "switch.output": True,
        "switch.leaf": True,
    }
    assert "restart_reconcile_pending" not in state


async def test_restored_root_wake_continues_without_power_cycle() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "waking_members"})
    coordinator.hass.states.values["switch.input"].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == []
    assert state["phase"] == "waking_members"
    assert state["wake_mode"] == "root"
    assert state["wake_member_index"] == 0
    assert "restart_reconcile_pending" not in state


async def test_restored_aux_wake_waits_for_fresh_telemetry_without_power_cycle() -> (
    None
):
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "waking_members", "source": "b1"})
    coordinator.hass.states.values["switch.input"].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == []
    assert state["phase"] == "waking_members"
    assert state["wake_mode"] == "aux"
    assert state["wake_member_index"] == 0
    _publish_soc(coordinator, "sensor.soc", "90", now + timedelta(seconds=1))
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(seconds=1),
    )

    assert coordinator.calls == [
        ("switch.output", True),
        ("switch.leaf", True),
        ("switch.input", False),
    ]
    assert state["phase"] == "proving"


async def test_restored_aux_break_side_finishes_without_full_safe_off() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "proving", "source": "b1"})
    for entity_id in ("switch.input", "switch.output", "switch.leaf"):
        coordinator.hass.states.values[entity_id].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == [("switch.input", False)]
    assert state["phase"] == "proving"
    assert state["source"] == "b1"


async def test_restored_safe_off_vector_replans_without_reconciliation_cycle() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "idle"})

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == [("switch.input", True)]
    assert state["phase"] == "waking_members"
    assert "restart_reconcile_pending" not in state


async def test_restored_complete_root_vector_is_adopted_without_switching() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "root"})
    for entity_id in (
        "switch.input",
        "switch.gate",
        "switch.output",
        "switch.leaf",
    ):
        coordinator.hass.states.values[entity_id].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _root_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == []
    assert state["phase"] == "root"
    assert state["claims"] == {
        "switch.input": True,
        "switch.gate": True,
        "switch.output": True,
        "switch.leaf": True,
    }


async def test_incoherent_restored_actor_vector_falls_back_to_safe_off() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "proving", "source": "b1"})
    coordinator.hass.states.values["switch.gate"].state = "on"
    coordinator.hass.states.values["switch.output"].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == [
        ("switch.output", False),
        ("switch.gate", False),
    ]
    assert state["phase"] == "idle"
    assert state["claims"] == {
        "switch.input": False,
        "switch.gate": False,
        "switch.output": False,
        "switch.leaf": False,
    }


async def test_restart_waits_for_actor_states_before_safe_off_fallback() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "running", "source": "b1"})
    coordinator.hass.states.values["switch.input"].state = "unavailable"
    coordinator.hass.states.values["switch.output"].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert coordinator.calls == []
    assert state["restart_reconcile_actors"] == ["switch.input"]
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(seconds=61),
    )

    assert coordinator.calls == [
        ("switch.output", False),
        ("switch.input", False),
    ]
    assert state["phase"] == "idle"
    assert "restart_reconcile_pending" not in state


async def test_restart_adopts_actor_states_published_during_grace_period() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "running", "source": "b1"})
    coordinator.hass.states.values["switch.input"].state = "unavailable"
    coordinator.hass.states.values["switch.output"].state = "on"
    coordinator.hass.states.values["switch.leaf"].state = "on"

    manager.normalize_restored_state()
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    coordinator.hass.states.values["switch.input"].state = "off"
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now + timedelta(seconds=10),
    )

    assert coordinator.calls == []
    assert state["phase"] == "proving"
    assert "restart_reconcile_actors" not in state


def test_persistent_snapshot_strips_inflight_wake_evidence() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    manager._state("chain").update(
        {
            "enabled": True,
            "phase": "proving",
            "source": "b1",
            "claims": {"switch.output": True},
            "wake_mode": "aux",
            "wake_deadline": (now + timedelta(seconds=60)).isoformat(),
            "last_actor_error": {"kind": "old"},
        }
    )

    saved = manager.persistent_state_snapshot()["chain"]

    assert saved["phase"] == "idle"
    assert saved["source"] is None
    assert saved["restart_reconcile_pending"] is True
    assert saved["restart_source_hint"] == "b1"
    assert saved["claims"] == {"switch.output": True}
    assert "wake_mode" not in saved
    assert "wake_deadline" not in saved
    assert "last_actor_error" not in saved


async def test_manual_disable_waits_for_cascade_actor_lock() -> None:
    now = datetime(2026, 9, 1, 10, 49)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state["enabled"] = True
    lock = manager._locks.setdefault("chain", asyncio.Lock())
    await lock.acquire()
    task = asyncio.create_task(manager.async_set_enabled("chain", False))
    await asyncio.sleep(0)

    assert not task.done()
    assert state["enabled"] is True

    lock.release()
    assert await task
    assert state["enabled"] is False


async def test_running_integrates_aux_then_stops_at_target() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "running",
            "source": "b1",
            "episode_day": now.date().isoformat(),
        }
    )
    live = (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf"))
    await manager._apply_one("chain", _aux_plan(now), live, now)
    await manager._apply_one("chain", _aux_plan(now), live, now + timedelta(minutes=5))
    assert state["aux_today_wh"] == 25.0

    target = (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf"))
    await manager._apply_one(
        "chain", _aux_plan(now), target, now + timedelta(minutes=6)
    )
    assert state["phase"] == "complete"
    assert state["source"] is None


async def test_running_uses_export_backed_slot_target_below_recovery_soc() -> None:
    """The executor follows the planned target while retaining the hard floor."""
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "running",
            "source": "b1",
            "episode_day": now.date().isoformat(),
            "recovery_pending": ["b1"],
        }
    )
    deep_plan = _aux_plan(now, soc_start_percent=45.0, soc_end_percent=40.0)

    await manager._apply_one(
        "chain",
        deep_plan,
        (SurplusLoadState("b1", soc_percent=45), SurplusLoadState("leaf")),
        now,
    )
    assert state["phase"] == "running"

    await manager._apply_one(
        "chain",
        deep_plan,
        (SurplusLoadState("b1", soc_percent=40), SurplusLoadState("leaf")),
        now + timedelta(minutes=1),
    )
    assert state["phase"] == "recovering"
    assert state["source"] is None


async def test_day_rollover_preserves_below_target_recovery_debt() -> None:
    """Midnight stops Aux but cannot erase the promised recovery."""
    now = datetime(2026, 8, 24, 0, 1)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "running",
            "source": "b1",
            "episode_day": "2026-08-23",
            "recovery_pending": ["b1"],
            "recovery_deadline": (now + timedelta(hours=12)).isoformat(),
        }
    )
    idle_plan = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=None,
    )

    await manager._apply_one(
        "chain",
        idle_plan,
        (SurplusLoadState("b1", soc_percent=40), SurplusLoadState("leaf")),
        now,
    )

    assert state["phase"] == "recovering"
    assert state["episode_day"] == "2026-08-24"
    assert state["recovery_pending"] == ["b1"]
    assert state["recovery_deadline"] == (now + timedelta(hours=12)).isoformat()


async def test_recovery_finishing_after_midnight_completes_current_day(
    monkeypatch,
) -> None:
    """A stopped prior-day episode cannot remain recovering or restart Aux."""
    now = datetime(2026, 8, 24, 10)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    monkeypatch.setattr(
        "custom_components.battery_manager.cascade_manager.ir.async_delete_issue",
        lambda *_args, **_kwargs: None,
    )
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "recovering",
            "episode_day": "2026-08-23",
            "recovery_pending": ["b1"],
            "recovery_deadline": (now + timedelta(hours=6)).isoformat(),
        }
    )
    # A stale rolling preview still proposes Aux.  Recovery ownership must
    # suppress it before a new current-day plan is available.
    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=50), SurplusLoadState("leaf")),
        now,
    )

    assert state["phase"] == "complete"
    assert state["episode_day"] == "2026-08-24"
    assert state["recovery_pending"] == []
    assert state["recovery_deadline"] is None
    assert state["source"] is None


async def test_persisted_recovery_deadline_survives_withdrawn_plan(
    monkeypatch,
) -> None:
    """A rolling plan without Aux cannot erase an overdue recovery promise."""
    now = datetime(2026, 8, 24, 18)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    created = []
    monkeypatch.setattr(
        "custom_components.battery_manager.cascade_manager.ir.async_create_issue",
        lambda *_args, **kwargs: created.append(kwargs),
    )
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "recovering",
            "episode_day": now.date().isoformat(),
            "recovery_pending": ["b1"],
            "recovery_deadline": (now - timedelta(minutes=1)).isoformat(),
        }
    )
    idle_plan = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=None,
    )

    await manager._apply_one(
        "chain",
        idle_plan,
        (SurplusLoadState("b1", soc_percent=40), SurplusLoadState("leaf")),
        now,
    )

    assert state["phase"] == "recovering"
    assert state["recovery_pending"] == ["b1"]
    assert created[0]["translation_key"] == "cascade_recovery_missed"


async def test_running_path_is_stopped_when_aux_plan_is_withdrawn() -> None:
    """A completed wake must not outlive the fresh plan that requested it."""
    now = datetime(2026, 8, 30, 0, 8)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "running",
            "source": "b1",
            "episode_day": now.date().isoformat(),
            "recovery_pending": ["b1"],
            "claims": {
                "switch.output": True,
                "switch.leaf": True,
            },
        }
    )
    coordinator.hass.states.values["switch.output"].state = "on"
    coordinator.hass.states.values["switch.leaf"].state = "on"
    idle_plan = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=now + timedelta(hours=6),
    )
    below_recovery = (
        SurplusLoadState("b1", soc_percent=40),
        SurplusLoadState("leaf"),
    )

    await manager._apply_one("chain", idle_plan, below_recovery, now)

    assert state["phase"] == "recovering"
    assert state["source"] is None
    assert coordinator.calls[:2] == [
        ("switch.leaf", False),
        ("switch.output", False),
    ]


async def test_complete_phase_still_enforces_safe_off() -> None:
    """Complete is a diagnosis, never permission for stale outputs to stay on."""
    now = datetime(2026, 8, 30, 0, 8)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "complete",
            "claims": {
                "switch.output": True,
                "switch.leaf": True,
            },
        }
    )
    coordinator.hass.states.values["switch.output"].state = "on"
    coordinator.hass.states.values["switch.leaf"].state = "on"
    idle_plan = SimpleNamespace(
        flows=(CascadeSlotFlow(),),
        recovery_deadline=None,
    )

    await manager._apply_one(
        "chain",
        idle_plan,
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )

    assert state["phase"] == "complete"
    assert state["source"] is None
    assert coordinator.calls[:2] == [
        ("switch.leaf", False),
        ("switch.output", False),
    ]


async def test_stationary_telemetry_loss_gets_ten_minute_grace() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    coordinator.hass.states.values["sensor.output_power"].state = "unavailable"
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "enabled": True,
            "phase": "running",
            "source": "b1",
            "episode_day": now.date().isoformat(),
        }
    )
    live = (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf"))
    await manager._apply_one("chain", _aux_plan(now), live, now)
    assert state["phase"] == "running"
    await manager._apply_one("chain", _aux_plan(now), live, now + timedelta(minutes=10))
    assert state["phase"] == "recovering"
    assert state["warning"] == "telemetry_unavailable"


async def test_payload_runtime_and_parallel_apply_contract() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update(
        {
            "episode_day": "not-a-date",
            "phase": "transient",
            "fault": "demo",
            "hands_off": True,
            "retry_at": now.isoformat(),
            "aux_today_wh": 125.0,
            "wake_mode": "aux",
            "wake_member_index": 0,
            "wake_telemetry_reported_at": now.isoformat(),
            "wake_deadline": (now + timedelta(minutes=1)).isoformat(),
            "wake_evidence_entity": "sensor.input_power",
            "wake_evidence_at": (now + timedelta(seconds=8)).isoformat(),
        }
    )
    runtime = manager.runtime_state("chain")
    assert runtime.episode_day is None
    assert runtime.phase == "idle"

    state["phase"] = "proving"
    runtime = manager.runtime_state("chain")
    assert runtime.phase == "proving"

    assert manager.planning_blocked_load_ids() == {"b1", "leaf"}
    assert manager.managed_load_ids() == {"b1", "leaf"}

    core_cascade = LoadCascade("chain", (CascadeMember("b1", 20, 50),), "leaf")
    cascade_plan = _aux_plan(now)
    cascade_plan.cascade_id = "chain"
    result = SimpleNamespace(cascade_plans=(cascade_plan,))
    config = SimpleNamespace(cascades=(core_cascade,))
    coordinator.hass.states.values["switch.input"].attributes["assumed_state"] = True
    slots = (HourSlot(0, now, 1.0, 6, 0.0, 0.0, 0.0),)
    payload = manager.payload(result, config, slots)["chain"]
    assert payload["fault"] == "demo"
    assert payload["fault_detail"] is None
    assert payload["source_name"] is None
    assert payload["planned_root_energy_kwh"] == 0.0
    assert payload["today_kwh"] == 0.0
    assert payload["tomorrow_kwh"] == 0.0
    assert payload["daily"] == []
    assert payload["planned_aux_energy_kwh"] == 0.0
    assert payload["actual_aux_energy_kwh"] == 0.125
    assert payload["aggregate_soc_percent"] == 90.0
    assert payload["members"] == ["b1"]
    assert payload["member_details"] == [
        {
            "load_id": "b1",
            "name": "B1",
            "soc_percent": 90.0,
            "target_soc_percent": 50,
            "soc_forecast": [],
        }
    ]
    assert payload["terminal_name"] == "Leaf"
    assert payload["assumed_state_actors"] == ["switch.input"]
    assert payload["wake_mode"] == "aux"
    assert payload["wake_member_index"] == 0
    assert payload["wake_baseline_at"] == now.isoformat()
    assert payload["wake_deadline"] == (now + timedelta(minutes=1)).isoformat()
    assert payload["wake_evidence_entity"] == "sensor.input_power"
    assert payload["wake_evidence_at"] == (now + timedelta(seconds=8)).isoformat()
    assert payload["schedule"] == []
    state["fault"] = None
    state["hands_off"] = False
    active_payload = manager.payload(result, config, slots)["chain"]
    assert active_payload["member_details"][0]["soc_forecast"] == [
        {"t": now.isoformat(), "soc": 90.0},
        {"t": (now + timedelta(hours=1)).isoformat(), "soc": 85.0},
    ]
    assert manager._schedule_payload(core_cascade, cascade_plan, slots) == [
        {
            "start": now.isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "root_input_wh": 0.0,
            "terminal_energy_wh": 150.0,
            "sources": ["aux"],
            "activities": [
                {
                    "kind": "discharge",
                    "load_id": "b1",
                    "name": "B1",
                    "energy_wh": 160.0,
                    "soc_start_percent": 90.0,
                    "soc_end_percent": 85.0,
                    "source": "aux",
                },
                {
                    "kind": "terminal",
                    "load_id": "leaf",
                    "name": "Leaf",
                    "energy_wh": 150.0,
                    "source": "aux",
                    "source_load_id": "b1",
                    "source_name": "B1",
                },
                {
                    "kind": "output",
                    "load_id": "b1",
                    "name": "B1",
                    "sources": ["aux"],
                },
            ],
        }
    ]

    root_schedule = manager._schedule_payload(core_cascade, _root_plan(now), slots)
    assert root_schedule[0]["sources"] == ["root"]
    assert root_schedule[0]["root_input_wh"] == 600.0
    assert root_schedule[0]["activities"] == [
        {
            "kind": "charge",
            "load_id": "b1",
            "name": "B1",
            "energy_wh": 300.0,
            "stored_energy_wh": 270.0,
            "soc_start_percent": 50.0,
            "soc_end_percent": 60.0,
            "source": "root",
        },
        {
            "kind": "terminal",
            "load_id": "leaf",
            "name": "Leaf",
            "energy_wh": 300.0,
            "source": "root",
            "source_load_id": None,
            "source_name": None,
        },
        {
            "kind": "output",
            "load_id": "b1",
            "name": "B1",
            "sources": ["root"],
        },
    ]

    # Inactive/faulted apply still evaluates the central safe-off contract and
    # exercises the per-chain parallel lock/gather wrapper. Actors already
    # confirmed OFF receive no redundant service calls.
    await manager.async_apply(
        config,
        result,
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    assert coordinator.calls == []


def test_root_energy_is_split_into_today_tomorrow_and_daily() -> None:
    """The black-box Root lane has the same daily contract as normal loads."""
    today = datetime(2026, 8, 31, 23)
    tomorrow = today + timedelta(hours=1)
    day_after = tomorrow + timedelta(days=1)
    slots = tuple(
        HourSlot(index, start, 1.0, start.hour, 0.0, 0.0, 0.0)
        for index, start in enumerate((today, tomorrow, day_after))
    )
    plan = SimpleNamespace(
        flows=tuple(
            CascadeSlotFlow(root_input_wh=root_wh) for root_wh in (100.0, 350.0, 225.0)
        )
    )

    assert CascadeManager._root_per_day_kwh(plan, slots) == {
        "today_kwh": 0.1,
        "tomorrow_kwh": 0.35,
        "daily": [
            {"date": "2026-08-31", "kwh": 0.1},
            {"date": "2026-09-01", "kwh": 0.35},
            {"date": "2026-09-02", "kwh": 0.225},
        ],
    }


async def test_global_safety_gate_safe_offs_active_but_not_manual_cascade() -> None:
    now = datetime(2026, 8, 30, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")
    state.update({"enabled": True, "phase": "running", "source": "b1"})
    for entity_id in ("switch.input", "switch.gate", "switch.output", "switch.leaf"):
        coordinator.hass.states.values[entity_id].state = "on"

    await manager._apply_one(
        "chain",
        _aux_plan(now),
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
        safety_reason="G4 floor guard",
    )

    assert coordinator.calls == [
        ("switch.leaf", False),
        ("switch.output", False),
        ("switch.gate", False),
        ("switch.input", False),
    ]
    assert state["enabled"] is True
    assert state["phase"] == "complete"
    assert state["episode_day"] == now.date().isoformat()

    coordinator.calls.clear()
    state.update({"enabled": False, "phase": "idle", "episode_day": None})
    coordinator.hass.states.values["switch.output"].state = "on"
    await manager.async_safety_off_active("stale-data load shed", now)
    assert coordinator.calls == []
    assert coordinator.hass.states.values["switch.output"].state == "on"


def test_schedule_payload_exposes_two_member_output_path() -> None:
    """The card receives the real B1→B2 path instead of guessing from Wh."""
    now = datetime(2026, 8, 30, 10)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    cascade = LoadCascade(
        "chain",
        (CascadeMember("b1", 20, 50), CascadeMember("b2", 20, 50)),
        "leaf",
    )
    slots = (HourSlot(0, now, 1.0, 10, 0.0, 0.0, 0.0),)

    # Charging B2 from Root only needs B1's upstream AC output.
    charge_plan = SimpleNamespace(
        flows=(
            CascadeSlotFlow(
                root_input_wh=300,
                member_flows=(
                    CascadeMemberFlow(
                        "b2",
                        50,
                        55,
                        own_charge_input_wh=300,
                        battery_charge_wh=270,
                    ),
                ),
            ),
        )
    )
    charge_activities = manager._schedule_payload(cascade, charge_plan, slots)[0][
        "activities"
    ]
    assert [
        item["load_id"] for item in charge_activities if item["kind"] == "output"
    ] == ["b1"]

    # B1 as Aux source feeds through both B1 and B2 before the terminal load.
    segment = CascadeSourceSegment(0, 0.0, 0.5, "aux", "b1", False, 150)
    aux_plan = SimpleNamespace(
        flows=(
            CascadeSlotFlow(
                segments=(segment,),
                member_flows=(
                    CascadeMemberFlow(
                        "b1",
                        90,
                        85,
                        battery_discharge_wh=160,
                    ),
                ),
            ),
        )
    )
    aux_activities = manager._schedule_payload(cascade, aux_plan, slots)[0][
        "activities"
    ]
    assert [item["load_id"] for item in aux_activities if item["kind"] == "output"] == [
        "b1",
        "b2",
    ]


async def test_handover_power_edge_cases_and_fault_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    manager = CascadeManager(coordinator)
    state = manager._state("chain")

    assert manager._power_proven("chain", now) is False
    state["source"] = "b1"
    coordinator.hass.states.values["sensor.output_power"].state = "unavailable"
    assert manager._power_proven("chain", now) is None
    coordinator.hass.states.values["sensor.output_power"].state = "5"
    assert manager._power_proven("chain", now) is None
    assert manager._power_proven("chain", now) is None
    power = coordinator.hass.states.values["sensor.output_power"]
    power.last_updated = now + timedelta(seconds=30)
    assert manager._power_proven("chain", now + timedelta(seconds=30)) is None
    power.last_updated = now + timedelta(seconds=61)
    assert manager._power_proven("chain", now + timedelta(seconds=61)) is False

    assert await manager._handover("chain", "b1")
    assert state["phase"] == "proving"
    assert state["source"] == "b1"

    state["hands_off"] = True
    state["fault"] = "demo"
    assert not await manager.async_set_enabled("chain", True)
    # A rejected activation is atomic: it must not silently clear hands-off.
    assert state["hands_off"]
    assert state["activation_blocked_reason"] == "fault"

    deleted = []
    monkeypatch.setattr(
        cascade_manager_module.ir,
        "async_delete_issue",
        lambda *args: deleted.append(args),
    )
    assert await manager.async_reset_fault("chain")
    assert state["fault"] is None
    assert not state["enabled"]
    assert deleted
