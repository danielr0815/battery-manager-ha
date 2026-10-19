"""F-JOINT-MARKET: corrupt economic proposals cannot bypass physical proof."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from core.joint_dispatch import proof_failure
from core.model import DCDeficitInterval
from core.optimize import plan
from core.replay import recording, replay
from core.reserve_sources import SourceStep
from core.simulate import simulate
from test_joint_dispatch import forecast, plant


@pytest.fixture
def accepted_physics():
    config = plant()
    inputs = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 300, 0])
    trajectory = simulate(config, inputs, 20)
    assert trajectory.reserve_decision.joint.joint_status == "accepted"
    assert trajectory.flows[0].psu24_delivered_wh == pytest.approx(100)
    assert trajectory.flows[1].inverter_output_wh == pytest.approx(100)
    steps = tuple(
        SourceStep(slot, flow, (flow,), 5)
        for slot, flow in zip(inputs.slots, trajectory.flows, strict=True)
    )
    assert proof_failure(config, trajectory, trajectory, steps, steps) is None
    return config, inputs, trajectory, steps


@pytest.mark.parametrize(
    "hazard",
    [
        "dc_service",
        "extra_import",
        "extra_export",
        "extra_psu_charge",
        "source_inverter_loop",
        "reserve_floor",
        "phase",
        "energy_budget",
        "window_end_energy",
    ],
)
def test_joint_physical_proof_rejects_one_harmful_candidate_without_mutating_reference(
    accepted_physics, hazard
):
    """Start with real served DC/AC and recharge; inject one unsafe proposal."""
    config, inputs, reference, before = accepted_physics
    original = deepcopy(reference)
    trial = reference
    flows = list(reference.flows)
    if hazard == "dc_service":
        # A new outage must fail even when all total grid/energy metrics match.
        interval = DCDeficitInterval(
            inputs.slots[0].start,
            inputs.slots[0].start + timedelta(minutes=5),
            1,
        )
        flows[0] = replace(flows[0], unserved_dc_wh=1, dc_deficit_intervals=(interval,))
    elif hazard == "extra_import":
        flows[0] = replace(flows[0], grid_import_wh=flows[0].grid_import_wh + 1)
        trial = replace(trial, total_import_wh=trial.total_import_wh + 1)
    elif hazard == "extra_export":
        flows[0] = replace(flows[0], grid_export_wh=flows[0].grid_export_wh + 1)
        trial = replace(trial, total_export_wh=trial.total_export_wh + 1)
    elif hazard == "extra_psu_charge":
        # Price arbitrage must never manufacture a new grid-charge permission.
        flows[0] = replace(flows[0], psu48_battery_charge_wh=1)
    elif hazard == "source_inverter_loop":
        # The real expensive AC receiver cannot simultaneously enable a PSU.
        flows[1] = replace(flows[1], support_dc24=True)
    elif hazard == "reserve_floor":
        flows[1] = replace(
            flows[1], soc_end_percent=config.control.inverter_min_soc_percent - 1
        )
    elif hazard == "phase":
        # Even a cheaper native DC choice cannot break a confirmed PSU phase.
        config = replace(
            config,
            support=replace(
                config.support,
                dc24_active=True,
                dc24_phase_until=(inputs.now + timedelta(minutes=30)).timestamp(),
            ),
        )
        flows[0] = replace(flows[0], support_dc24=False)
    elif hazard == "energy_budget":
        # Globally equal grid totals cannot hide a larger booked battery debit.
        flows[0] = replace(
            flows[0], battery_discharge_wh=flows[0].battery_discharge_wh + 1
        )
    else:
        # Equal net-draw sums cannot hide a lower endpoint at the PV boundary.
        flows[-1] = replace(flows[-1], soc_end_percent=flows[-1].soc_end_percent - 1)
        trial = replace(trial, end_soc_percent=trial.end_soc_percent - 1)
    trial = replace(trial, flows=tuple(flows))
    after = tuple(
        replace(step, flow=flow) for step, flow in zip(before, flows, strict=True)
    )
    assert proof_failure(config, reference, trial, before, after) == hazard
    assert reference == original
    assert tuple(step.flow for step in before) == original.flows
    assert proof_failure(config, reference, reference, before, before) is None


def test_replay_without_joint_summary_still_compares_recorded_physical_results():
    """An additive missing proof is compatible; altered imported energy is not."""
    config = plant()
    # Flat known prices preserve the former physical plan; this isolates the
    # additive recording field from intentional new market scheduling behavior.
    inputs = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [100, 100, 100])
    result = plan(config, inputs)
    record = json.loads(json.dumps(recording(config, inputs, result)))
    trajectory = record["result"]["fields"]["trajectory"]["fields"]
    decision = trajectory["reserve_decision"]["fields"]
    assert decision.pop("joint")["fields"]["joint_status"] == "flat_prices"
    assert replay(record) == (result, True)
    trajectory["total_import_wh"] += 1
    assert replay(record) == (result, False)
