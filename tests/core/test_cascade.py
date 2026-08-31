"""Pure planner contract for F-CASCADE-STORAGE."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from custom_components.battery_manager.core import (
    CascadeMember,
    CascadeRuntimeState,
    FeedInParams,
    HourSlot,
    LoadCascade,
    PlanInputs,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
    plan,
)
from custom_components.battery_manager.core import cascade as cascade_core

NOW = datetime(2026, 8, 23, 6)


def _slots(*pv: float) -> tuple[HourSlot, ...]:
    return tuple(
        HourSlot(i, NOW + timedelta(hours=i), 1.0, 6 + i, value, 0.0, 0.0)
        for i, value in enumerate(pv)
    )


def _system(
    *,
    members: int = 1,
    socs: tuple[float, ...] | None = None,
    cache: bool = False,
    caps: bool = False,
) -> tuple[SystemConfig, PlanInputs]:
    loads = tuple(
        SurplusLoad(
            f"b{i}",
            f"B{i}",
            300.0,
            0.0,
            30,
            30,
            True,
            2000.0,
            90.0,
            True,
        )
        for i in range(1, members + 1)
    ) + (SurplusLoad("leaf", "Leaf", 300.0, 0.0, 30, 30, False),)
    cascade_members = tuple(
        CascadeMember(
            f"b{i}",
            20.0,
            50.0,
            eta_charge=0.9 if caps else 1.0,
            eta_discharge=0.8 if caps else 1.0,
            max_charge_power_w=250.0 if caps else None,
            max_output_power_w=500.0 if caps else None,
            max_passthrough_power_w=500.0 if caps else None,
            output_overhead_w=5.0 if caps else 0.0,
        )
        for i in range(1, members + 1)
    )
    config = SystemConfig(
        loads=loads,
        cascades=(LoadCascade("chain", cascade_members, "leaf"),),
    )
    socs = socs or tuple(90.0 for _ in range(members))
    source = "cache" if cache else "live"
    states = tuple(
        SurplusLoadState(
            f"b{i}",
            soc_percent=socs[i - 1],
            soc_source=source,
            soc_observed_at=NOW - timedelta(days=1) if cache else NOW,
        )
        for i in range(1, members + 1)
    ) + (SurplusLoadState("leaf"),)
    return config, PlanInputs(NOW, 5.0, _slots(0, 1000, 1000, 1000, 1000, 1000), states)


def test_one_storage_aux_episode_targets_without_recovery_deadline() -> None:
    config, inputs = _system()
    result = plan(config, inputs)
    cascade = result.cascade_plans[0]

    assert cascade.planned_aux_energy_wh == 800.0
    assert cascade.flows[0].segments[0].source_load_id == "b1"
    assert min(flow.member_flows[0].soc_end_percent for flow in cascade.flows) == 50.0
    assert all(
        not (
            any(segment.source == "aux" for segment in flow.segments)
            and flow.member_flows[0].battery_charge_wh > 0
        )
        for flow in cascade.flows
    )
    assert cascade.recovery_deadline is None
    assert cascade.proposed_runtime == CascadeRuntimeState(
        cascade_id="chain",
        episode_day=NOW.date(),
        phase="running",
        active_source_id="b1",
        recovery_pending_ids=(),
    )
    assert all(item.managed_by_cascade for item in result.load_plans)
    assert result.trajectory.flows[0].extra_ac_wh == 0.0


def test_two_storage_skip_caps_efficiency_and_cache() -> None:
    config, inputs = _system(members=2, socs=(20.0, 90.0), cache=True, caps=True)
    config = replace(
        config, loads=(replace(config.loads[0], capacity_wh=500), *config.loads[1:])
    )
    config = replace(
        config,
        cascades=(
            replace(
                config.cascades[0],
                members=(
                    replace(config.cascades[0].members[0], recovery_soc_percent=40),
                    config.cascades[0].members[1],
                ),
            ),
        ),
    )
    result = plan(config, inputs)
    cascade = result.cascade_plans[0]

    assert cascade.planned_aux_energy_wh == pytest.approx(629.5081967213115)
    assert cascade.flows[0].segments[0].source_load_id == "b2"
    assert cascade.aggregate_soc_percent == pytest.approx(76.0)
    assert cascade.aggregate_soc_stale
    assert cascade.provisional_live_soc_required
    assert cascade.flows[0].member_flows[1].battery_discharge_wh > 150.0


@pytest.mark.parametrize(
    ("state", "expected_aux"),
    [
        (SurplusLoadState("b1", soc_percent=None, soc_source="missing"), 0.0),
        (
            SurplusLoadState(
                "b1",
                soc_percent=90,
                soc_source="cache",
                soc_observed_at=NOW - timedelta(days=8),
            ),
            0.0,
        ),
        (SurplusLoadState("b1", available=False, soc_percent=90), 0.0),
    ],
)
def test_missing_stale_or_unavailable_source_is_not_started(
    state: SurplusLoadState, expected_aux: float
) -> None:
    config, inputs = _system()
    inputs = replace(inputs, load_states=(state, SurplusLoadState("leaf")))
    assert plan(config, inputs).cascade_plans[0].planned_aux_energy_wh == expected_aux


def test_no_pv_is_required_but_completed_episode_still_blocks_aux() -> None:
    config, inputs = _system()
    no_pv = replace(inputs, slots=_slots(0, 0))
    cascade = plan(config, no_pv).cascade_plans[0]
    assert cascade.planned_aux_energy_wh == 600
    assert cascade.flows[-1].member_flows[0].soc_end_percent == 60

    complete = replace(
        inputs,
        cascade_runtime_states=(CascadeRuntimeState("chain", NOW.date(), "complete"),),
    )
    assert plan(config, complete).cascade_plans[0].planned_aux_energy_wh == 0


def test_root_service_is_reported_without_aux_double_counting() -> None:
    config, inputs = _system()
    root = replace(inputs, start_soc_percent=50.0)
    result = plan(config, root)
    cascade = result.cascade_plans[0]

    assert cascade.planned_aux_energy_wh == 0
    assert cascade.planned_root_energy_wh > 0
    assert any(
        segment.source in ("direct_pv", "house_battery")
        for flow in cascade.flows
        for segment in flow.segments
    )
    assert sum(flow.extra_ac_wh for flow in result.trajectory.flows) == pytest.approx(
        cascade.planned_root_energy_wh
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"eta_charge": 0},
        {"eta_discharge": 1.1},
        {"max_output_power_w": -1},
        {"output_overhead_w": -1},
    ],
)
def test_member_physics_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        CascadeMember("b1", 20, 50, **kwargs)


def test_topology_validation() -> None:
    member = CascadeMember("b1", 20, 50)
    with pytest.raises(ValueError, match="must not be empty"):
        LoadCascade("chain", (), "leaf")
    with pytest.raises(ValueError, match="unique"):
        LoadCascade("chain", (member, member), "leaf")
    with pytest.raises(ValueError, match="terminal"):
        LoadCascade("chain", (member,), "b1")

    continuous = SurplusLoad("b1", "not storage", 100)
    leaf = SurplusLoad("leaf", "leaf", 100)
    with pytest.raises(ValueError, match="energy-limited"):
        SystemConfig(
            loads=(continuous, leaf),
            cascades=(LoadCascade("chain", (member,), "leaf"),),
        )


def test_legacy_result_remains_without_cascade_payload() -> None:
    config, inputs = _system()
    legacy = plan(replace(config, cascades=()), inputs)
    assert legacy.cascade_plans == ()
    assert not any(item.managed_by_cascade for item in legacy.load_plans)


def test_idle_slot_has_no_phantom_root_overhead() -> None:
    """No active cascade path means zero Root energy even with two outputs."""
    config, inputs = _system(members=2, socs=(90.0, 90.0), caps=True)
    inputs = replace(
        inputs,
        cascade_runtime_states=(CascadeRuntimeState("chain", NOW.date(), "complete"),),
    )

    result = plan(config, inputs)

    assert all(not load_plan.schedule[0] for load_plan in result.load_plans)
    assert result.cascade_plans[0].flows[0].root_input_wh == 0.0
    assert result.cascade_plans[0].flows[0].segments == ()


def test_cascade_rejection_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    config, inputs = _system()
    legacy_config = replace(config, cascades=())
    base = plan(legacy_config, inputs)
    cascade = config.cascades[0]

    assert (
        cascade_core._allocate_aux_now(cascade, config, replace(inputs, slots=()), base)
        is None
    )
    assert (
        cascade_core.augment_cascade_plans(
            legacy_config, inputs, base, lambda _inputs: base
        )
        is base
    )

    allocation = (
        (cascade_core.CascadeSourceSegment(0, 0.0, 0.5, "aux", "b1", False, 150.0),),
        {"b1": 82.5},
        False,
    )
    # Root becoming available in the replan rejects auxiliary service.
    root_inputs = replace(inputs, start_soc_percent=50.0)
    root_result = plan(legacy_config, root_inputs)
    allocations = iter((allocation, None))
    monkeypatch.setattr(
        cascade_core, "_allocate_aux_now", lambda *_args: next(allocations)
    )
    rejected = cascade_core.augment_cascade_plans(
        config, inputs, base, lambda _inputs: root_result
    )
    assert rejected.cascade_plans[0].planned_aux_energy_wh == 0

    monkeypatch.setattr(cascade_core, "_allocate_aux_now", lambda *_args: allocation)
    still_rejected = cascade_core.augment_cascade_plans(
        config, inputs, base, lambda _inputs: root_result
    )
    assert still_rejected.cascade_plans[0].planned_aux_energy_wh == 0


def test_aux_moves_to_next_member_and_finishes_at_target() -> None:
    config, inputs = _system(members=2, socs=(50.0, 90.0))
    no_pv = replace(inputs, slots=_slots(0, 0))

    result = plan(config, no_pv).cascade_plans[0]

    assert result.flows[0].segments[0].source_load_id == "b2"
    assert result.flows[0].member_flows[0].soc_end_percent == 50.0

    target_config, target_inputs = _system(members=2, socs=(50.0, 50.0))
    assert (
        plan(target_config, target_inputs).cascade_plans[0].planned_aux_energy_wh == 0
    )

    # A new episode may not start for a sub-dwell residual, but a running one
    # consumes the tail instead of abandoning the configured 50 % target.
    tail_config, tail_inputs = _system(socs=(54.0,))
    tail_inputs = replace(tail_inputs, slots=_slots(0, 0))
    assert plan(tail_config, tail_inputs).cascade_plans[0].planned_aux_energy_wh == 0
    tail_inputs = replace(
        tail_inputs,
        cascade_runtime_states=(
            CascadeRuntimeState("chain", NOW.date(), "running", active_source_id="b1"),
        ),
    )
    soc = 54.0
    for _ in range(20):
        rolling = replace(
            tail_inputs,
            load_states=(
                SurplusLoadState("b1", soc_percent=soc),
                SurplusLoadState("leaf"),
            ),
        )
        tail = plan(tail_config, rolling).cascade_plans[0]
        if tail.planned_aux_energy_wh == 0:
            break
        soc = tail.flows[0].member_flows[0].soc_end_percent

    assert soc == pytest.approx(50.0)
    assert tail.planned_aux_energy_wh == 0


def test_aux_headroom_is_used_before_avoidable_early_feedin() -> None:
    """Recharge demand for the 50 % targets outranks early grid feed-in."""
    config, inputs = _system(members=2)
    config = replace(
        config,
        feedin=FeedInParams(
            enabled=True,
            max_w=2000.0,
            min_soc_percent=20.0,
            deadline_hour=20,
        ),
    )
    inputs = replace(
        inputs,
        slots=_slots(0, 0, 0, 0, 0, 0, 1200, 1200, 1200, 1200, 1200),
    )

    without_cascade = plan(replace(config, cascades=()), inputs)
    result = plan(config, inputs)
    cascade = result.cascade_plans[0]

    assert cascade.planned_aux_energy_wh == 1600
    assert [
        min(flow.member_flows[index].soc_end_percent for flow in cascade.flows)
        for index in range(2)
    ] == pytest.approx([50, 50])
    cascade_feedin = sum(flow.feedin_wh for flow in result.trajectory.flows)
    baseline_feedin = sum(flow.feedin_wh for flow in without_cascade.trajectory.flows)
    assert baseline_feedin > 0
    assert cascade_feedin < baseline_feedin
    assert cascade_feedin < 100


@pytest.mark.parametrize(
    "pv",
    [
        (0, 1000, 1000, 1000, 1000, 1000),
        (0, 0, 0, 1000, 1000, 1000),
        (0, 0, 500, 1500, 500, 0),
    ],
)
def test_aux_discharge_never_overlaps_member_charge(
    pv: tuple[float, ...],
) -> None:
    config, inputs = _system(members=2)
    cascade = plan(config, replace(inputs, slots=_slots(*pv))).cascade_plans[0]

    for flow in cascade.flows:
        aux_source_ids = {
            segment.source_load_id
            for segment in flow.segments
            if segment.source == "aux"
        }
        if aux_source_ids:
            assert flow.root_input_wh == 0
        assert all(
            member_flow.own_charge_input_wh == 0
            for member_flow in flow.member_flows
            if aux_source_ids
        )


def test_proving_episode_keeps_rolling_tail_instead_of_restarting_dwell() -> None:
    """The proof minute already belongs to the active terminal episode."""
    config, inputs = _system(socs=(54.0,))
    no_pv = replace(inputs, slots=_slots(0, 0))
    assert plan(config, no_pv).cascade_plans[0].planned_aux_energy_wh == 0

    proving = replace(
        no_pv,
        cascade_runtime_states=(
            CascadeRuntimeState("chain", None, "proving", active_source_id="b1"),
        ),
    )

    assert plan(config, proving).cascade_plans[0].planned_aux_energy_wh > 0


def test_source_caps_and_too_short_tail_are_skipped() -> None:
    config, inputs = _system(members=2, socs=(90, 90), caps=True)
    base = plan(replace(config, cascades=()), inputs)
    too_small_output = replace(
        config,
        cascades=(
            replace(
                config.cascades[0],
                members=(
                    replace(config.cascades[0].members[0], max_output_power_w=100),
                    config.cascades[0].members[1],
                ),
            ),
        ),
    )
    allocation = cascade_core._allocate_aux_now(
        too_small_output.cascades[0], too_small_output, inputs, base
    )
    assert allocation is not None
    assert allocation[0][0].source_load_id == "b2"


def test_aux_episode_stops_at_midnight_and_skips_sub_proof_capacity() -> None:
    config, inputs = _system(members=2, socs=(64.925, 90.0))
    one_slot = replace(inputs, slots=_slots(0))
    base = plan(replace(config, cascades=()), one_slot)

    allocation = cascade_core._allocate_aux_now(
        config.cascades[0], config, one_slot, base
    )

    assert allocation is not None
    assert {segment.source_load_id for segment in allocation[0]} == {"b1"}

    today_slot = _slots(0)[0]
    tomorrow_slot = replace(
        today_slot,
        index=1,
        start=NOW + timedelta(days=1),
    )
    across_midnight = replace(inputs, slots=(today_slot, tomorrow_slot))
    across_base = plan(replace(config, cascades=()), across_midnight)
    midnight_allocation = cascade_core._allocate_aux_now(
        config.cascades[0], config, across_midnight, across_base
    )
    assert midnight_allocation is not None
    assert {segment.slot_index for segment in midnight_allocation[0]} == {0}

    tomorrow_only = replace(inputs, slots=(tomorrow_slot,))
    tomorrow_base = plan(replace(config, cascades=()), tomorrow_only)
    assert (
        cascade_core._allocate_aux_now(
            config.cascades[0], config, tomorrow_only, tomorrow_base
        )
        is None
    )


def test_cascade_golden_snapshot() -> None:
    """Freeze direct PV, B1→B2, skip, recovery, return and Root boundary."""
    config, inputs = _system(members=2)
    aux = plan(config, inputs).cascade_plans[0]
    root_result = plan(config, replace(inputs, start_soc_percent=50.0))
    root = root_result.cascade_plans[0]

    skip_config, skip_inputs = _system(
        members=2, socs=(20.0, 90.0), cache=True, caps=True
    )
    skip_config = replace(
        skip_config,
        loads=(replace(skip_config.loads[0], capacity_wh=500), *skip_config.loads[1:]),
        cascades=(
            replace(
                skip_config.cascades[0],
                members=(
                    replace(
                        skip_config.cascades[0].members[0], recovery_soc_percent=40
                    ),
                    skip_config.cascades[0].members[1],
                ),
            ),
        ),
    )
    skip = plan(skip_config, skip_inputs).cascade_plans[0]
    snapshot = {
        "b1_to_b2": {
            "aux_wh": aux.planned_aux_energy_wh,
            "first_source": aux.flows[0].segments[0].source_load_id,
            "root_wh": aux.planned_root_energy_wh,
        },
        "direct_pv": {
            "aux_wh": root.planned_aux_energy_wh,
            "direct_pv_segments": sum(
                segment.source == "direct_pv"
                for flow in root.flows
                for segment in flow.segments
            ),
        },
        "house_battery_return": {
            "first_source": root.flows[0].segments[0].source,
            "root_wh": root.planned_root_energy_wh,
        },
        "recovery": {
            "deadline": aux.recovery_deadline,
            "members_reached": sum(
                value is not None for _, value in aux.recovery_reached_at
            ),
        },
        "root_balance": {
            "extra_ac_wh": sum(
                flow.extra_ac_wh for flow in root_result.trajectory.flows
            ),
            "root_wh": root.planned_root_energy_wh,
        },
        "skip": {
            "aggregate_soc": skip.aggregate_soc_percent,
            "aux_wh": skip.planned_aux_energy_wh,
            "first_source": skip.flows[0].segments[0].source_load_id,
            "root_wh": skip.planned_root_energy_wh,
            "stale": skip.aggregate_soc_stale,
        },
    }
    golden = json.loads(Path(__file__).with_name("golden_cascade.json").read_text())
    assert snapshot == golden

    # B1 can supply only 0.49 h, but the full target episode continues with B2.
    tail_config, short_tail_inputs = _system(members=2, socs=(59.49375, 90), caps=True)
    tail_base = plan(replace(tail_config, cascades=()), short_tail_inputs)
    allocation = cascade_core._allocate_aux_now(
        tail_config.cascades[0], tail_config, short_tail_inputs, tail_base
    )
    assert allocation is not None
    assert [segment.source_load_id for segment in allocation[0]][:2] == ["b1", "b2"]

    blocked_passthrough = replace(
        tail_config,
        cascades=(
            replace(
                tail_config.cascades[0],
                members=(
                    tail_config.cascades[0].members[0],
                    replace(
                        tail_config.cascades[0].members[1],
                        max_passthrough_power_w=100,
                    ),
                ),
            ),
        ),
    )
    allocation = cascade_core._allocate_aux_now(
        blocked_passthrough.cascades[0],
        blocked_passthrough,
        short_tail_inputs,
        tail_base,
    )
    assert allocation is not None
    assert allocation[0][0].source_load_id == "b2"
