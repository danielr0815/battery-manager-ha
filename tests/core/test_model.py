"""Fail-fast validation of the core dataclasses (code review 2026-07).

Physically impossible parameters (capacity 0, eta 0, negative powers, a
battery tolerance above 100 %) used to plan silently garbage; the dataclasses
now reject them at construction with a speaking ValueError.
"""

import pytest
from core.model import (
    BatteryParams,
    ConverterParams,
    LoadProfile,
    PVParams,
    SupportParams,
    SurplusLoad,
)


def test_battery_params_valid_defaults():
    BatteryParams()  # must not raise
    BatteryParams(capacity_wh=2000.0, eta_charge=1.0, eta_discharge=0.5)


@pytest.mark.parametrize("capacity", [0.0, -1.0, float("nan")])
def test_battery_capacity_must_be_positive(capacity):
    with pytest.raises(ValueError, match=r"capacity_wh must be > 0"):
        BatteryParams(capacity_wh=capacity)


@pytest.mark.parametrize("eta", [0.0, -0.5, 1.1, float("nan")])
def test_battery_efficiencies_must_be_in_unit_interval(eta):
    with pytest.raises(ValueError, match=r"eta_charge must be in \(0, 1\]"):
        BatteryParams(eta_charge=eta)
    with pytest.raises(ValueError, match=r"eta_discharge must be in \(0, 1\]"):
        BatteryParams(eta_discharge=eta)


def test_converter_params_validation():
    ConverterParams()  # must not raise
    ConverterParams(max_power_w=0.0, eta=1.0, standby_power_w=0.0)  # bounds ok
    with pytest.raises(ValueError, match=r"max_power_w must be >= 0"):
        ConverterParams(max_power_w=-1.0)
    with pytest.raises(ValueError, match=r"eta must be in \(0, 1\]"):
        ConverterParams(eta=0.0)
    with pytest.raises(ValueError, match=r"eta must be in \(0, 1\]"):
        ConverterParams(eta=1.5)
    with pytest.raises(ValueError, match=r"standby_power_w must be >= 0"):
        ConverterParams(standby_power_w=-0.1)


def test_pv_params_peak_power_must_not_be_negative():
    PVParams()  # must not raise
    PVParams(peak_power_w=0.0)  # bound ok
    with pytest.raises(ValueError, match=r"peak_power_w must be >= 0"):
        PVParams(peak_power_w=-3200.0)


def test_load_profile_powers_must_not_be_negative():
    LoadProfile()  # must not raise
    LoadProfile(base_w=0.0, variable_w=0.0)  # bounds ok
    with pytest.raises(ValueError, match=r"base_w must be >= 0"):
        LoadProfile(base_w=-50.0)
    with pytest.raises(ValueError, match=r"variable_w must be >= 0"):
        LoadProfile(variable_w=-25.0)


def test_surplus_load_validation():
    SurplusLoad(load_id="x", name="X", nominal_power_w=0.0)  # bounds ok
    SurplusLoad(
        load_id="x", name="X", nominal_power_w=300.0, battery_tolerance=1.0
    )  # 100 % battery share is allowed
    with pytest.raises(ValueError, match=r"nominal_power_w must be >= 0"):
        SurplusLoad(load_id="x", name="X", nominal_power_w=-300.0)
    with pytest.raises(ValueError, match=r"battery_tolerance must be in \[0, 1\]"):
        SurplusLoad(
            load_id="x", name="X", nominal_power_w=300.0, battery_tolerance=-0.1
        )
    with pytest.raises(ValueError, match=r"battery_tolerance must be in \[0, 1\]"):
        SurplusLoad(load_id="x", name="X", nominal_power_w=300.0, battery_tolerance=1.5)


def test_support_params_validation():
    SupportParams()  # neutral defaults must stay valid
    with pytest.raises(ValueError, match=r"dc48_power_w must be >= 0"):
        SupportParams(dc48_power_w=-60.0)
    with pytest.raises(ValueError, match=r"native48_base_w must be >= 0"):
        SupportParams(native48_base_w=-10.0)
    for field in ("dcdc_eta", "psu24_eta", "psu48_eta"):
        with pytest.raises(ValueError, match=rf"{field} must be in \(0, 1\]"):
            SupportParams(**{field: 0.0})
        with pytest.raises(ValueError, match=rf"{field} must be in \(0, 1\]"):
            SupportParams(**{field: 1.2})
    for field in ("dcdc_max_power_w", "psu24_max_power_w", "psu48_max_power_w"):
        SupportParams(**{field: None})  # uncapped stays valid
        SupportParams(**{field: 0.0})  # bound ok
        with pytest.raises(ValueError, match=rf"{field} must be >= 0 or None"):
            SupportParams(**{field: -500.0})


def test_trajectory_empty_flows_min_max_fall_back_to_end_soc():
    """A zero-slot trajectory (empty horizon) has no flow endpoints — min/max
    SOC must read the end SOC instead of raising on an empty min()/max()."""
    from core.model import Trajectory

    traj = Trajectory(
        flows=(), total_import_wh=0.0, total_export_wh=0.0, end_soc_percent=42.0
    )
    assert traj.min_soc_percent == 42.0
    assert traj.max_soc_percent == 42.0
