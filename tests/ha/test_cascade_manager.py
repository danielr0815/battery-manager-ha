"""Central cascade actor ordering and proof-window tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.battery_manager import cascade_manager as cascade_manager_module
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
    CONF_LOAD_SOC_ENTITY,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
)
from custom_components.battery_manager.core import (
    CascadeMember,
    CascadeMemberFlow,
    CascadeSlotFlow,
    CascadeSourceSegment,
    HourSlot,
    LoadCascade,
    SurplusLoadState,
)
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
                state="300", attributes={}, last_updated=now
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

    async def _switch_entity(self, entity_id, turn_on):
        self.calls.append((entity_id, turn_on))
        self.hass.states.values[entity_id].state = "on" if turn_on else "off"
        return True

    def _save_persistent_state(self):
        self.saved += 1

    def load_bm_enabled(self, _load_id):
        return True


def _aux_plan(now: datetime):
    segment = CascadeSourceSegment(0, 0.0, 0.5, "aux", "b1", False, 150.0)
    return SimpleNamespace(
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

    async def delayed_switch(entity_id: str, turn_on: bool) -> bool:
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

    async def unconfirmed_switch(entity_id: str, turn_on: bool) -> bool:
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

    async def fail_gate(entity_id: str, turn_on: bool) -> bool:
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
    coordinator.hass.states.values["switch.output"].state = "off"
    assert await manager.async_set_enabled("chain", True)
    assert manager.enabled("chain")

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
    assert not state["hands_off"]

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
