"""Physical connection, replay and finite model contracts for release 0.56."""

from dataclasses import replace

import pytest
from core.model import Appliance, ApplianceRun, SurplusLoadState
from core.replay import decode, encode
from core.reserve import simulate_reserve_variant
from core.reserve_sources import SourceStep, grid_dc, market_sources
from core.simulate import simulate, step_hour
from test_dc_pv_market import dc_plant
from test_market import priced
from test_reserve import config, inputs


@pytest.mark.parametrize("rail", ["24", "48", "both"])
@pytest.mark.parametrize("soc", [80, 95])
def test_shared_connection_ideal_solar_has_no_import_or_export(rail, soc):
    c = config()
    c = replace(
        c, support=replace(c.support, native48_base_w=100 if rail == "48" else 0)
    )
    dc = 100
    if rail == "both":
        c = replace(c, support=replace(c.support, native48_base_w=50))
    slot = inputs(soc, [(200, 100, dc)]).slots[0]
    flow = step_hour(
        c, soc, slot, 100, dc24_from_grid=rail != "48", dc48_support=rail != "24"
    )
    assert flow.grid_import_wh == pytest.approx(0)
    assert flow.grid_export_wh == pytest.approx(0)
    assert flow.psu_grid_import_wh == pytest.approx(0)
    assert flow.soc_end_percent == pytest.approx(soc)


def test_losses_and_charger_limit_allocate_solar_only_once():
    c = config()
    c = replace(
        c,
        support=replace(c.support, native48_base_w=0, psu24_eta=0.5),
        charger=replace(c.charger, max_power_w=20),
    )
    flow = step_hour(
        c, 95, inputs(95, [(200, 100, 100)]).slots[0], 100, dc24_from_grid=True
    )
    assert flow.grid_import_wh == pytest.approx(100)
    assert flow.psu_grid_import_wh == pytest.approx(100)
    assert flow.grid_export_wh == 0
    assert flow.battery_charge_wh == 0
    assert grid_dc(c, replace(flow, psu_grid_import_wh=None)) == pytest.approx(200)


@pytest.mark.parametrize(
    "constructor,values",
    [
        (SurplusLoadState, {"load_id": "x", "soc_percent": 101}),
        (SurplusLoadState, {"load_id": "x", "measured_power_w": -1}),
        (SurplusLoadState, {"load_id": "x", "learned_power_w": float("inf")}),
        (SurplusLoadState, {"load_id": "x", "saturated_power_w": float("nan")}),
        (
            Appliance,
            {
                "appliance_id": "x",
                "name": "X",
                "run_energy_wh": -1,
                "run_duration_h": 1,
            },
        ),
        (
            Appliance,
            {
                "appliance_id": "x",
                "name": "X",
                "run_energy_wh": 100,
                "run_duration_h": 0,
            },
        ),
        (
            ApplianceRun,
            {"appliance_id": "x", "remaining_energy_wh": -1, "remaining_hours": 1},
        ),
        (
            ApplianceRun,
            {"appliance_id": "x", "remaining_energy_wh": 100, "remaining_hours": -1},
        ),
        (
            ApplianceRun,
            {"appliance_id": "x", "remaining_energy_wh": 100, "remaining_hours": 0},
        ),
    ],
)
def test_invalid_model_evidence_is_rejected(constructor, values):
    with pytest.raises(ValueError):
        constructor(**values)


def test_finished_run_and_missing_measurements_remain_valid():
    run = ApplianceRun("x", 0, 0)
    assert decode(encode(run)) == run
    state = SurplusLoadState("x")
    assert state.soc_percent is None and state.measured_power_w is None


@pytest.mark.parametrize(
    "fields",
    [
        {"unexpected": 1},
        {"remaining_hours": -1, "remaining_energy_wh": 0, "appliance_id": "x"},
        None,
    ],
)
def test_replay_reports_invalid_model_with_context(fields):
    with pytest.raises(ValueError, match="Invalid recording ApplianceRun"):
        decode({"type": "ApplianceRun", "fields": fields})


def test_comparison_uses_identical_dc_market_budget():
    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [300, 100, 100])
    normal = simulate(c, i, 20)
    alternative = simulate_reserve_variant(c, i, None, 1, None, allow_ac=False)
    assert alternative.flows == normal.flows
    assert (
        alternative.reserve_decision.dc_budget_wh
        == normal.reserve_decision.dc_budget_wh
    )
    assert alternative.reserve_decision.dc_shifted_wh == pytest.approx(100)


def test_mixed_rails_cannot_spend_budget_on_dominated_upgrade():
    c = dc_plant()
    slot = inputs(80, [(0, 0, 100)]).slots[0]
    held = step_hour(c, 80, slot, 100, dc24_from_grid=True)
    natural = step_hour(c, 80, slot, 100)
    # A mixed 48-V alternative draws more stored energy AND imports more than
    # the natural choice. It must never displace a useful peak allocation.
    dominated = replace(
        natural, support_dc48=True, battery_discharge_wh=110, psu_grid_import_wh=10
    )
    steps = (
        SourceStep(slot, held, (held, natural, dominated), 20),
        SourceStep(slot, natural, (held, natural), 20),
    )
    assert market_sources(c, steps, [3, 1]) == ((False, False), (True, False))


def test_finished_appliance_adds_no_forecast_energy():
    from core.series import _apply_appliance_runs

    slots = list(inputs(80, [(0, 100, 0)]).slots)
    assert _apply_appliance_runs(slots, (ApplianceRun("finished", 0, 0),)) == slots


def test_residual_dc_cost_rejects_both_bounded_ac_corrections(monkeypatch):
    """Adversarial policy evidence cannot authorize AC by retrying indefinitely."""
    from core import reserve
    from core.reserve import _simulate_reserve_policy
    from test_reserve_soft_ceiling import soft_plant

    c = soft_plant()
    i = inputs(80, [(0, 100, 100)])
    reference = _simulate_reserve_policy(c, i, None, 1, None, allow_ac=False)
    flow = reference.flows[0]
    harmful = replace(
        reference,
        flows=(
            replace(
                flow,
                inverter_output_wh=100,
                psu_grid_import_wh=grid_dc(c, flow) + 100,
                grid_import_wh=flow.grid_import_wh + 100,
            ),
        ),
    )
    evidence = iter((harmful, reference, harmful, harmful))
    monkeypatch.setattr(
        reserve, "_simulate_reserve_policy", lambda *args, **kwargs: next(evidence)
    )
    result = reserve._simulate_reserve_reference(c, i, None, 1, None, traces={})
    assert result.flows == reference.flows
    assert sum(f.inverter_output_wh for f in result.flows) == 0
    assert sum(grid_dc(c, f) for f in result.flows) == sum(
        grid_dc(c, f) for f in reference.flows
    )
    with pytest.raises(StopIteration):
        next(evidence)
