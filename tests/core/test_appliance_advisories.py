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
