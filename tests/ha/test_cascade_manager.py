"""Central cascade actor ordering and proof-window tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.battery_manager import cascade_manager as cascade_manager_module
from custom_components.battery_manager.cascade_manager import CascadeManager
from custom_components.battery_manager.const import (
    ACTOR_MODE_SHARED,
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
    LoadCascade,
    SurplusLoadState,
)


class _States:
    def __init__(self, now: datetime) -> None:
        self.values = {
            "switch.input": SimpleNamespace(state="off", attributes={}),
            "switch.gate": SimpleNamespace(state="off", attributes={}),
            "switch.output": SimpleNamespace(state="off", attributes={}),
            "switch.leaf": SimpleNamespace(state="off", attributes={}),
            "sensor.soc": SimpleNamespace(state="90", attributes={}, last_updated=now),
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
        flows=(CascadeSlotFlow(segments=(segment,)),),
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
                    CascadeMemberFlow("b1", 50, 60, own_charge_input_wh=300),
                ),
            ),
        ),
        provisional_live_soc_required=False,
        recovery_deadline=now + timedelta(hours=6),
    )


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
    assert coordinator.calls == [
        ("switch.gate", False),
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
    assert state["recovery_pending"] == ["b1"]


async def test_sleeping_member_wakes_before_terminal_is_energised() -> None:
    now = datetime(2026, 8, 23, 6)
    coordinator = _Coordinator(now)
    coordinator.hass.states.values["sensor.soc"].state = "unavailable"
    manager = CascadeManager(coordinator)
    manager._state("chain")["enabled"] = True
    states = (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf"))

    await manager._apply_one("chain", _aux_plan(now), states, now)
    state = manager._state("chain")
    assert state["phase"] == "waking_socs"
    assert coordinator.calls == [
        ("switch.gate", False),
        ("switch.input", True),
        ("switch.output", True),
    ]

    coordinator.hass.states.values["sensor.soc"].state = "90"
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


async def test_running_integrates_aux_then_stops_at_floor() -> None:
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

    floor = (SurplusLoadState("b1", soc_percent=20), SurplusLoadState("leaf"))
    await manager._apply_one("chain", _aux_plan(now), floor, now + timedelta(minutes=6))
    assert state["phase"] == "recovering"
    assert state["source"] is None


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

    core_cascade = LoadCascade("chain", (CascadeMember("b1", 20, 50),), "leaf")
    cascade_plan = _aux_plan(now)
    cascade_plan.cascade_id = "chain"
    result = SimpleNamespace(cascade_plans=(cascade_plan,))
    config = SimpleNamespace(cascades=(core_cascade,))
    coordinator.hass.states.values["switch.input"].attributes["assumed_state"] = True
    payload = manager.payload(result, config)["chain"]
    assert payload["fault"] == "demo"
    assert payload["planned_aux_energy_kwh"] == 0.15
    assert payload["actual_aux_energy_kwh"] == 0.125
    assert payload["members"] == ["b1"]
    assert payload["assumed_state_actors"] == ["switch.input"]

    # Inactive/faulted apply still executes the central safe-off contract and
    # exercises the per-chain parallel lock/gather wrapper.
    await manager.async_apply(
        config,
        result,
        (SurplusLoadState("b1", soc_percent=90), SurplusLoadState("leaf")),
        now,
    )
    assert coordinator.calls[-4:] == [
        ("switch.leaf", False),
        ("switch.output", False),
        ("switch.gate", False),
        ("switch.input", False),
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
