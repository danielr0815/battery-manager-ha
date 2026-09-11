"""F-HOUSE-SUPPLY: house demand precedes optional surplus consumption."""

from dataclasses import replace
from datetime import datetime

import pytest
from core.model import (
    CascadeMember,
    ControlParams,
    LoadCascade,
    LoadProfile,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.optimize import (
    _effective_uncertainty,
    _ramped_stress_floors,
    plan,
    search_threshold,
)
from core.series import build_slots
from core.simulate import simulate


def house_supply_scenario(*, cascade=False, confidence=0.8, solar_kwh=5.5):
    """A modest sunny day followed by a weak day; storage credit hoards at 95%."""
    terminal = SurplusLoad("leaf", "Leaf", 450, min_runtime_min=15)
    storage = SurplusLoad(
        "storage", "Storage", 450, energy_limited=True, capacity_wh=2000
    )
    config = SystemConfig(
        ac_profile=LoadProfile(40, 0, 0, 24),
        dc_profile=LoadProfile(60, 0, 0, 24),
        control=ControlParams(
            predrain_pv_confidence=confidence, soc_buffer_percent=6.9
        ),
        loads=(storage, terminal) if cascade else (terminal,),
        cascades=(LoadCascade("chain", (CascadeMember("storage", 20, 50),), "leaf"),)
        if cascade
        else (),
    )
    inputs = build_slots(
        config,
        datetime(2026, 9, 9, 20),
        45,
        [0, solar_kwh, 2],
        load_states=(
            SurplusLoadState("storage", soc_percent=20),
            SurplusLoadState("leaf"),
        )
        if cascade
        else (SurplusLoadState("leaf"),),
    )
    return config, inputs


@pytest.mark.parametrize("cascade", [False, True])
def test_house_supply_removes_avoidable_surplus_load(cascade, monkeypatch):
    """R1/R3: less import and no optional charging, including a real cascade."""
    config, inputs = house_supply_scenario(cascade=cascade)
    result = plan(config, inputs)
    # Counterfactual: the exact former terminal-credit policy, including its
    # full allocator. Only the newly introduced preference is disabled.
    with monkeypatch.context() as patch:
        patch.setattr("core.optimize._prefer_house_supply", lambda c, i, t, b: (t, b))
        previous = plan(config, inputs)
    assert previous.threshold_percent == 95
    assert previous.grid_import_kwh == pytest.approx(1.2)
    assert result.inverter_on
    assert result.threshold_percent == 31
    assert result.grid_import_kwh == pytest.approx(0.2)
    assert result.grid_export_kwh < previous.grid_export_kwh
    if cascade:
        assert previous.cascade_plans[0].planned_root_energy_wh == pytest.approx(450)
        assert result.cascade_plans[0].planned_root_energy_wh == 0
    else:
        assert previous.load_plans[0].planned_energy_wh == pytest.approx(450)
        assert result.load_plans[0].planned_energy_wh == 0

    # A lower threshold cannot simply ride the cutoff. Verify every slot on
    # both paths, including the second night after the apparent solar refill.
    stress, _, _ = _effective_uncertainty(inputs, 0.8, 1)
    floors = _ramped_stress_floors(config, inputs, stress)
    for scale in (1, stress):
        trajectory = simulate(config, inputs, result.threshold_percent, pv_scale=scale)
        assert all(
            flow.soc_end_percent >= floor - 1e-6
            for flow, floor in zip(trajectory.flows, floors, strict=True)
        )
    unsafe = simulate(config, inputs, 20, pv_scale=stress)
    assert unsafe.min_soc_percent < 20


@pytest.mark.parametrize("banded", [False, True])
def test_pessimistic_shortage_keeps_reserve_even_with_nominal_surplus(banded):
    """R2: scalar fallback and genuine P10 evidence can veto longer operation."""
    config, inputs = house_supply_scenario(confidence=0.8 if banded else 0.1)
    if banded:
        inputs = replace(
            inputs,
            slots=tuple(
                replace(slot, pv_p10_wh=slot.pv_wh * 0.1, pv_p90_wh=slot.pv_wh * 1.2)
                for slot in inputs.slots
            ),
        )
    result = plan(config, inputs)
    assert result.threshold_percent == 95
    assert not result.inverter_on
    assert result.load_plans[0].planned_energy_wh > 0


def test_no_surplus_keeps_terminal_value_policy(monkeypatch):
    """R1: this preference cannot spend reserves without avoidable export."""
    config, inputs = house_supply_scenario(solar_kwh=1)
    actual = search_threshold(config, inputs)
    with monkeypatch.context() as patch:
        patch.setattr("core.optimize._prefer_house_supply", lambda c, i, t, b: (t, b))
        previous = search_threshold(config, inputs)
    assert actual == previous
    assert actual[1].total_export_wh == 0


def test_idle_draw_alone_does_not_justify_longer_inverter_operation():
    """R1: less export without import savings is just additional conversion loss."""
    config, inputs = house_supply_scenario()
    inputs = replace(
        inputs, slots=tuple(replace(slot, ac_wh=0) for slot in inputs.slots)
    )
    threshold, trajectory = search_threshold(config, inputs)
    assert trajectory.total_export_wh > 0
    assert trajectory.total_import_wh == 0
    assert threshold == 95
    assert not any(flow.inverter_on for flow in trajectory.flows)
