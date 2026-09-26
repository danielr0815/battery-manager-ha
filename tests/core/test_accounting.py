"""Physical root/Aux interval accounting runs independently of Home Assistant."""

from datetime import UTC, datetime, timedelta

import pytest
from core import HourSlot, PlanInputs, SurplusLoad, SurplusLoadState, SystemConfig, plan
from core.accounting import expected_interval

NOW = datetime(2026, 9, 8, 9, tzinfo=UTC)


def test_cascade_input_meter_is_not_confused_with_stored_energy():
    from core import CascadeMember, LoadCascade
    from core.replay import recording

    config = SystemConfig(
        loads=(
            SurplusLoad("b1", "B1", 300, 0, 15, 15, True, 2000, 90, True),
            SurplusLoad("leaf", "Leaf", 300, 0, 15, 15, False),
        ),
        cascades=(LoadCascade("chain", (CascadeMember("b1", 20, 50),), "leaf"),),
    )
    inputs = PlanInputs(
        NOW,
        95,
        (HourSlot(0, NOW, 1, 9, 2000, 100, 0),),
        (
            SurplusLoadState(
                "b1", soc_percent=50, soc_source="live", soc_observed_at=NOW
            ),
            SurplusLoadState("leaf"),
        ),
    )
    result = plan(config, inputs)
    expected = expected_interval(
        recording(config, inputs, result), NOW, NOW + timedelta(hours=1)
    )
    flow = result.cascade_plans[0].flows[0]
    assert flow.terminal_served_wh > 0
    assert expected["cascade_input:b1"] == pytest.approx(flow.root_input_wh)
    assert expected["load:b1"] == pytest.approx(
        flow.member_flows[0].own_charge_input_wh
    )
    assert expected["cascade_input:b1"] > expected["load:b1"]
    assert expected["load:leaf"] == pytest.approx(flow.terminal_served_wh)
    assert (
        expected_interval(
            recording(config, inputs, result),
            NOW + timedelta(hours=2),
            NOW + timedelta(hours=3),
        )
        == {}
    )


def test_aux_input_boundary_has_downstream_delivery_but_no_source_input():
    from dataclasses import replace

    from core import (
        CascadeMember,
        CascadeMemberFlow,
        CascadeSlotFlow,
        CascadeSourceSegment,
        LoadCascade,
    )
    from core.replay import recording

    config = SystemConfig(
        loads=(
            SurplusLoad("b1", "B1", 300, 0, 15, 15, True, 2000, 90, True),
            SurplusLoad("b2", "B2", 300, 0, 15, 15, True, 2000, 90, True),
            SurplusLoad("leaf", "Leaf", 300),
        ),
        cascades=(
            LoadCascade(
                "chain",
                (
                    CascadeMember("b1", 20, 50, output_overhead_w=10),
                    CascadeMember("b2", 20, 50, output_overhead_w=20),
                ),
                "leaf",
            ),
        ),
    )
    inputs = PlanInputs(
        NOW,
        95,
        (HourSlot(0, NOW, 1, 9, 0, 0, 0),),
        tuple(
            SurplusLoadState(
                lid, soc_percent=80, soc_source="live", soc_observed_at=NOW
            )
            for lid in ("b1", "b2", "leaf")
        ),
    )
    result = plan(config, inputs)
    # Contract fixture: B1 supplies the last half-hour through B2, including
    # B2's 20 W overhead. B1's own overhead never appears at B2's input.
    flow = CascadeSlotFlow(
        terminal_served_wh=150,
        aux_terminal_wh=150,
        segments=(
            CascadeSourceSegment(0, 0.0, 0.0, "root", None, True, 0),
            CascadeSourceSegment(0, 0.5, 0.5, "aux", "b1", False, 150),
        ),
        member_flows=(
            CascadeMemberFlow("b1", 80, 71.75, battery_discharge_wh=165),
            CascadeMemberFlow("b2", 80, 80),
        ),
    )
    result = replace(
        result, cascade_plans=(replace(result.cascade_plans[0], flows=(flow,)),)
    )
    record = recording(config, inputs, result)
    early = expected_interval(record, NOW, NOW + timedelta(minutes=30))
    late = expected_interval(
        record, NOW + timedelta(minutes=30), NOW + timedelta(hours=1)
    )
    assert early["cascade_input:b2"] == 0
    assert late["cascade_input:b1"] == 0
    assert late["cascade_input:b2"] == 160
    assert late["load:leaf"] == 150


def test_no_plan_has_no_expected_energy_or_coverage():
    assert expected_interval(None, NOW, NOW + timedelta(hours=1)) == {}
