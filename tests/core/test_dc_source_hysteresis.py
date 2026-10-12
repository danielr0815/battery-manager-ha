"""Economic source starts use stored Wh; safety and PV space stay independent."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from core.model import (
    BatteryParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.simulate import simulate


def context(active=False):
    now = datetime(2026, 10, 11)
    config = SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        charger=ConverterParams(1000, 1, 0),
        inverter=ConverterParams(1000, 1, 0),
        support=SupportParams(
            configured=True,
            coordinated=True,
            psu48_bus_voltage_v=48,
            dc24_active=active,
            dc48_active=active,
        ),
        reserve=ReserveParams(True, 1, dc_start_margin_percent=1),
    )
    slots = (
        HourSlot(0, now, 1, 0, 0, 100, 60),
        HourSlot(1, now + timedelta(hours=1), 1, 1, 300, 100, 60),
    )
    return config, PlanInputs(now, 86, slots)


@pytest.mark.parametrize(
    "active,soc,expected",
    [
        (False, 86, False),
        (False, 85, True),
        (True, 86, True),
        (True, 87, False),
    ],
)
def test_boundary_crossing_requires_one_energy_quantum_for_new_start(
    active, soc, expected
):
    config, inputs = context(active)
    result = simulate(config, replace(inputs, start_soc_percent=soc), 20)
    first = result.flows[0]
    assert first.support_dc24_start is expected
    assert first.support_dc48_start is expected
    assert sum(f.unserved_dc_wh for f in result.flows) == 0
    assert result.total_export_wh == pytest.approx(0, abs=1e-6)
    assert result.min_soc_percent >= config.battery.soc_min_percent


def test_new_start_band_cannot_delay_low_soc_or_manual_support():
    config, inputs = context()
    for cfg, soc in (
        (config, 5),
        (
            replace(
                config,
                support=replace(
                    config.support, dc24_forced_on=True, dc48_forced_on=True
                ),
            ),
            87,
        ),
    ):
        result = simulate(cfg, replace(inputs, start_soc_percent=soc), 20)
        assert result.flows[0].support_dc24_start
        assert result.flows[0].support_dc48_start
        assert sum(f.unserved_dc_wh for f in result.flows) == 0


def test_native_discharge_band_does_not_raise_grid_import_or_pv_spill():
    config, inputs = context()
    old = replace(config, reserve=replace(config.reserve, dc_start_margin_percent=0))
    before = simulate(old, inputs, 20)
    after = simulate(config, inputs, 20)
    assert before.flows[0].support_dc24_start
    assert not after.flows[0].support_dc24_start
    assert after.total_import_wh <= before.total_import_wh + 1e-6
    assert after.total_export_wh <= before.total_export_wh + 1e-6
    assert (
        sum(f.inverter_output_wh for f in after.flows)
        <= sum(f.inverter_output_wh for f in before.flows) + 1e-6
    )


@pytest.mark.parametrize("margin", [-1, 101, float("nan")])
def test_invalid_energy_band_is_rejected(margin):
    with pytest.raises(ValueError):
        ReserveParams(dc_start_margin_percent=margin)
