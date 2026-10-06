"""Operator 2026-10-06: 85% is a soft solar peak, behind DC obligations."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from core.model import ReserveParams
from core.replay import decode
from core.reserve import _simulate_reserve_policy
from core.simulate import simulate
from test_reserve_priority import plant, series


def soft_plant():
    config = plant()
    return replace(config, reserve=replace(config.reserve, soft_soc_ceiling_percent=85))


def test_soft_peak_uses_normal_pv_without_adding_the_uncertainty_twice():
    inputs = series([(0, 600, 0), (300, 0, 0)])
    inputs = replace(
        inputs,
        slots=(inputs.slots[0], replace(inputs.slots[1], pv_p10_wh=200, pv_p90_wh=500)),
    )
    result = simulate(soft_plant(), inputs, 20)
    assert sum(f.inverter_output_wh for f in result.flows) == pytest.approx(250)
    assert result.flows[-1].soc_end_percent == pytest.approx(85)
    assert result.total_export_wh == 0


def test_a_larger_forecast_spread_can_require_more_space_than_the_soft_peak():
    inputs = series([(0, 600, 0), (600, 0, 0)], soc=50)
    inputs = replace(
        inputs,
        slots=(
            inputs.slots[0],
            replace(inputs.slots[1], pv_p10_wh=300, pv_p90_wh=1200),
        ),
    )
    result = simulate(soft_plant(), inputs, 20)
    assert sum(f.inverter_output_wh for f in result.flows) == pytest.approx(300)
    assert result.flows[-1].soc_end_percent == pytest.approx(80)
    assert result.total_export_wh == 0


def test_future_dc_and_consumption_buffer_can_relax_the_soft_peak_to_95():
    config = soft_plant()
    config = replace(
        config,
        support=replace(config.support, dc24_available=False, dc48_available=False),
    )
    result = simulate(
        config, series([(0, 600, 0), (300, 0, 0), (0, 0, 850), (300, 0, 0)]), 20
    )
    assert result.flows[1].soc_end_percent == pytest.approx(95)
    assert result.flows[2].soc_end_percent == pytest.approx(10)
    assert sum(f.psu24_delivered_wh + f.psu48_delivered_wh for f in result.flows) == 0
    assert sum(f.unserved_dc_wh for f in result.flows) == 0


def test_unreachable_soft_peak_does_not_change_the_physical_charging_limit():
    result = simulate(soft_plant(), series([(0, 0, 0), (300, 0, 0)]), 20)
    assert sum(f.inverter_output_wh for f in result.flows) == 0
    assert result.flows[-1].soc_end_percent == pytest.approx(95)
    assert result.total_export_wh == pytest.approx(150)


def test_sunless_horizon_does_not_discharge_just_to_reach_85():
    result = simulate(soft_plant(), series([(0, 600, 0)], soc=90), 20)
    assert result.flows[0].soc_end_percent == 90
    assert result.flows[0].inverter_output_wh == 0


def test_physical_ceiling_below_soft_target_keeps_its_own_limit():
    config = soft_plant()
    config = replace(config, battery=replace(config.battery, soc_max_percent=80))
    result = simulate(config, series([(0, 600, 0), (300, 0, 0)], soc=60), 20)
    assert result.flows[-1].soc_end_percent == pytest.approx(80)
    assert result.total_export_wh == 0


def test_oct06_soft_peak_does_not_buy_dc_to_preserve_released_energy():
    """Recorded hourly PV/bands, learned DC, partial slot and known prices.

    Device identities and optional appliances/loads are removed. The original
    shared PV allowance left only 110 Wh before tomorrow's physical ceiling.
    """
    record = json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("reserve_oct06_morning.json")
        .read_text()
    )
    config, inputs = decode(record["config"]), decode(record["inputs"])
    reference = _simulate_reserve_policy(config, inputs, None, 1, None, allow_ac=False)
    result = simulate(config, inputs, 20)
    tomorrow = [
        flow
        for slot, flow in zip(inputs.slots, result.flows, strict=True)
        if slot.start.date().isoformat() == "2026-10-07"
    ]
    assert max(f.soc_end_percent for f in tomorrow) == pytest.approx(85, abs=0.1)
    assert sum(f.inverter_output_wh for f in result.flows) > 500
    assert result.total_export_wh == 0
    assert sum(f.unserved_dc_wh for f in result.flows) == 0

    def dc_purchase(trajectory):
        return sum(
            f.psu24_delivered_wh / config.support.psu24_eta
            + f.psu48_delivered_wh / config.support.psu48_eta
            for f in trajectory.flows
        )

    assert dc_purchase(result) <= dc_purchase(reference)
    assert result.total_import_wh < reference.total_import_wh


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_soft_target_rejects_invalid_soc(value):
    with pytest.raises(ValueError, match="soft SOC ceiling"):
        ReserveParams(enabled=True, soft_soc_ceiling_percent=value)
