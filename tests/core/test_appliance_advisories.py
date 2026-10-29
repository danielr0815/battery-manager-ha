"""Advisor explanations reuse the actual gates without changing energy planning."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from unittest.mock import patch

import pytest
from core.model import (
    Appliance,
    ApplianceAdvisory,
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    SystemConfig,
)
from core.optimize import appliance_windows, plan
from core.simulate import simulate

NOW = datetime(2026, 9, 27, 12)


def _case(*, pv=200, soc=60, energy=100, buffer=5, duration=1):
    config = SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        control=ControlParams(soc_buffer_percent=buffer),
        appliances=(Appliance("washer", "Washer", energy, 1, True),),
    )
    slots = (HourSlot(0, NOW, duration, NOW.hour, pv, 0, 0),) if duration else ()
    return config, PlanInputs(NOW, soc, slots)


@pytest.mark.parametrize(
    "parameters,reasons",
    [
        ({}, ()),
        ({"duration": 0}, ("forecast_horizon_short",)),
        ({"duration": 0.5}, ("forecast_horizon_short",)),
        ({"pv": 0, "soc": 20}, ("extra_grid_import",)),
        ({"pv": 0, "buffer": 50}, ("soc_condition",)),
        (
            {"pv": 0, "buffer": 50, "energy": 800},
            ("extra_grid_import", "soc_condition"),
        ),
    ],
)
def test_advisory_reasons_identify_actual_failed_gates(parameters, reasons):
    config, inputs = _case(**parameters)
    baseline = simulate(config, inputs, 20)
    extra = (0,) * len(inputs.slots)
    expected_windows = appliance_windows(config, inputs, 20, extra, baseline)
    advisories = {}

    # The cost contract matters on every planning cycle: explanations consume
    # the already required trial, including rejected and empty-horizon cases.
    with patch("core.optimize.simulate", wraps=simulate) as trials:
        windows = appliance_windows(
            config, inputs, 20, extra, baseline, advisories=advisories
        )

    assert trials.call_count == 1
    assert windows == expected_windows == {"washer": not reasons}
    assert advisories == {"washer": ApplianceAdvisory(not reasons, reasons)}


def test_published_advisories_are_immutable_and_do_not_change_the_energy_plan():
    config, inputs = _case()
    result = plan(config, inputs)
    disabled = plan(
        replace(
            config,
            appliances=(replace(config.appliances[0], opportunistic_start=False),),
        ),
        inputs,
    )

    assert result.appliance_windows == {"washer": True}
    assert result.appliance_advisories == {"washer": ApplianceAdvisory(True)}
    assert disabled.appliance_windows == disabled.appliance_advisories == {}
    assert replace(result, appliance_windows={}, appliance_advisories={}) == disabled
    with pytest.raises(TypeError):
        result.appliance_advisories["washer"] = ApplianceAdvisory(False)
    with pytest.raises(FrozenInstanceError):
        result.appliance_advisories["washer"].allowed = False


@pytest.mark.parametrize(
    "gate,reason",
    [("preserves_dc_service", "dc_service"), ("recharge_preserved", "house_recharge")],
)
def test_advisory_preserves_independent_house_service_veto(gate, reason):
    """An appliance with free nominal PV must still respect each house proof."""
    config, inputs = _case()
    baseline = simulate(config, inputs, 20)
    advisories = {}
    with patch(f"core.optimize.{gate}", return_value=False) as proof:
        windows = appliance_windows(
            config, inputs, 20, (0,), baseline, advisories=advisories
        )
    assert proof.called
    assert windows == {"washer": False}
    assert advisories == {"washer": ApplianceAdvisory(False, (reason,))}
    assert baseline.total_import_wh == 0


@pytest.mark.parametrize("safe", [False, True])
def test_appliance_advisor_proves_actual_appliance_energy_against_existing_recharge(
    safe,
):
    """Appliance AC lives in slot.ac_wh and must survive the proof adapter."""
    from core.dc_service import preserves_dc_service
    from core.reserve import (
        reserve_planning_scope,
        simulate_fixed_dispatch,
        source_schedule_mode,
    )
    from core.series import insert_appliance_run
    from core.simulation_steps import split_slot
    from test_recharge import public_forecast, public_plant

    values = (
        [(1000, 0, 0), (0, 100, 0), (0, 600, 0), (175, 0, 0)]
        if safe
        else [(0, 100, 0), (0, 600, 0), (325, 0, 0)]
    )
    appliance = Appliance(
        "washer", "Washer", 1 if safe else 5, 1 if safe else 0.25, True
    )
    config = public_plant()
    config = replace(
        config,
        appliances=(appliance,),
        control=replace(config.control, soc_buffer_percent=0),
    )
    inputs = public_forecast(values)
    inputs = replace(
        inputs,
        start_soc_percent=95 if safe else 80,
        slots=tuple(
            small
            for slot in inputs.slots
            for small, _ in split_slot(
                replace(slot, pv_p10_wh=slot.pv_wh, pv_p90_wh=slot.pv_wh)
            )
        ),
    )
    extra = (0,) * len(inputs.slots)
    with reserve_planning_scope(config, inputs), source_schedule_mode(False):
        if safe:
            reference = simulate(config, inputs, 20)
            assert reference.reserve_decision.joint is None
        else:
            with source_schedule_mode(True):
                reference = simulate(config, inputs, 20)
            assert reference.reserve_decision.joint is not None
        assert reference.reserve_decision.recharge.certificates
        test_inputs = insert_appliance_run(
            inputs, appliance.run_energy_wh, appliance.run_duration_h
        )
        actual_trial = simulate_fixed_dispatch(config, test_inputs, extra, reference)
        # The ordinary advisor gates all pass in both examples. Only the
        # unsafe run consumes the already promised final 5 Wh of stored energy.
        assert actual_trial.total_import_wh == pytest.approx(reference.total_import_wh)
        assert actual_trial.min_soc_percent > config.battery.soc_min_percent
        assert preserves_dc_service(actual_trial, baseline=reference)
        if safe:
            assert actual_trial.end_soc_percent == pytest.approx(
                reference.end_soc_percent
            )
        else:
            assert actual_trial.end_soc_percent < reference.end_soc_percent
        advisories = {}
        windows = appliance_windows(
            config, inputs, 20, extra, reference, advisories=advisories
        )
    assert windows == {"washer": safe}
    assert advisories == {
        "washer": ApplianceAdvisory(safe, () if safe else ("house_recharge",))
    }
