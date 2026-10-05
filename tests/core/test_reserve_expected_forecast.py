"""Operator 2026-10-05: retain energy unless the expected PV needs headroom."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from core.optimize import plan
from core.replay import decode
from core.reserve import _simulate_reserve_policy
from core.simulate import simulate
from core.uncertainty import reserve_preparation_scales
from test_reserve_priority import plant, series


@pytest.mark.parametrize("upper_factor", [1.0, 1.2, 1.5])
def test_upper_pv_cannot_create_ac_when_expected_pv_and_small_buffer_fit(upper_factor):
    config = plant()
    config = replace(
        config, reserve=replace(config.reserve, upper_pv_factor=upper_factor)
    )
    inputs = series([(0, 600, 0), (100, 0, 0)])
    inputs = replace(
        inputs,
        slots=(
            inputs.slots[0],
            replace(inputs.slots[1], pv_p10_wh=50, pv_p90_wh=500),
        ),
    )
    result = plan(config, inputs)
    assert not result.inverter_on
    assert sum(f.inverter_output_wh for f in result.trajectory.flows) == 0
    assert result.trajectory.total_export_wh == 0
    assert result.trajectory.end_soc_percent == pytest.approx(90)
    assert result.trajectory.reserve_decision.headroom_wh == 0
    assert result.trajectory.reserve_decision.live_ac_floor_percent >= 80


@pytest.mark.parametrize("spread", [0, 20, 80])
def test_small_uncertainty_reserve_grows_with_forecast_spread(spread):
    config = plant()
    inputs = series([(100, 0, 0)])
    inputs = replace(
        inputs,
        slots=(replace(inputs.slots[0], pv_p10_wh=50, pv_p90_wh=100 + spread),),
    )
    scales = reserve_preparation_scales(config, inputs)
    assert inputs.slots[0].pv_wh * (scales[0] - 1) == pytest.approx(spread / 4)


def test_uncertainty_cap_is_stored_energy_once_across_the_entire_horizon():
    config = plant()
    config = replace(
        config,
        battery=replace(config.battery, eta_charge=0.9),
        charger=replace(config.charger, eta=0.8),
    )
    inputs = series([(500, 0, 0)] * 48)
    inputs = replace(
        inputs,
        slots=tuple(replace(s, pv_p10_wh=250, pv_p90_wh=1000) for s in inputs.slots),
    )
    scales = reserve_preparation_scales(config, inputs)
    extra_wh = sum(s.pv_wh * (f - 1) for s, f in zip(inputs.slots, scales, strict=True))
    assert extra_wh * 0.9 * 0.8 == pytest.approx(50)  # 5% of 1000 Wh, not per day


def test_unknown_band_gets_only_small_fallback_and_peak_clipping_remains_physical():
    config = plant()
    config = replace(config, reserve=replace(config.reserve, upper_pv_factor=1.2))
    inputs = series([(0, 0, 0), (100, 0, 0)])
    assert reserve_preparation_scales(config, inputs) == pytest.approx([1, 1.05])
    capped = replace(config, pv=replace(config.pv, peak_power_w=100))
    assert reserve_preparation_scales(capped, inputs) == [1, 1]
    neutral = replace(config, reserve=replace(config.reserve, upper_pv_factor=1))
    assert reserve_preparation_scales(neutral, inputs) == [1, 1]


def test_bounded_uncertainty_can_prepare_some_space_but_never_full_p90():
    config = plant()
    inputs = series([(0, 120, 0), (200, 0, 0)], soc=75)
    plain = simulate(config, inputs, 20)
    band = replace(
        inputs,
        slots=(
            inputs.slots[0],
            replace(inputs.slots[1], pv_p10_wh=100, pv_p90_wh=400),
        ),
    )
    buffered = simulate(config, band, 20)
    assert sum(f.inverter_output_wh for f in plain.flows) == 0
    # P90 would require 200 Wh extra headroom. Its bounded share is 50 Wh.
    assert sum(f.inverter_output_wh for f in buffered.flows) == pytest.approx(50)
    assert buffered.min_soc_percent == pytest.approx(70)


def test_consumption_uncertainty_is_reserved_before_optional_ac():
    config = plant()
    config = replace(config, control=replace(config.control, soc_buffer_percent=0))
    inputs = series([(0, 600, 0), (0, 0, 650), (900, 0, 0)], soc=85)
    nominal_only = simulate(config, inputs, 20)
    protected = simulate(
        replace(config, control=replace(config.control, soc_buffer_percent=10)),
        inputs,
        20,
    )
    assert nominal_only.flows[0].inverter_output_wh > 0
    assert protected.flows[0].inverter_output_wh == 0
    assert protected.reserve_decision.headroom_wh == 0
    assert protected.min_soc_percent >= 20  # 10% DC floor + 10% uncertainty
    assert (
        sum(f.psu24_delivered_wh + f.psu48_delivered_wh for f in protected.flows) == 0
    )
    assert sum(f.unserved_dc_wh for f in protected.flows) == 0
    # The buffer limits AC permission; it does not purchase a grid recharge.
    assert sum(f.battery_charge_wh for f in protected.flows[:2]) == 0


def test_oct05_evening_keeps_night_energy_without_discarding_known_prices():
    """Anonymised 18:42 snapshot: expected tomorrow peaks at 78% without AC.

    Optional devices/IDs/previous decisions are excluded. Energies, forecast
    bands, partial current slot, physical plant and published prices are kept.
    """
    record = json.loads(
        (
            Path(__file__).with_name("fixtures") / "reserve_oct05_evening.json"
        ).read_text()
    )
    config, inputs = decode(record["config"]), decode(record["inputs"])
    result = plan(config, inputs)
    trajectory = result.trajectory
    assert not result.inverter_on
    assert trajectory.reserve_decision.headroom_wh == 0
    assert trajectory.reserve_decision.market_ranking_reason == "weighted_partial"
    tonight = [
        f
        for s, f in zip(inputs.slots, trajectory.flows, strict=True)
        if s.start.date() == inputs.now.date()
    ]
    assert sum(f.inverter_output_wh for f in tonight) == 0
    assert trajectory.min_soc_percent == pytest.approx(21.08947, abs=1e-5)
    assert sum(f.unserved_dc_wh for f in trajectory.flows) == 0
    # Necessary later preparation is retained; complete ON steps leave only
    # a small export remainder, never the former blanket horizon-wide ban.
    assert sum(f.inverter_output_wh for f in trajectory.flows) > 0
    assert trajectory.total_export_wh == 0


def test_dc_guard_rejects_overstated_preparation_even_after_a_reduced_retry(
    monkeypatch,
):
    """Fault injection: an oversized envelope cannot bypass final DC priority.

    Reproduce the former full-upper assumption with real plant physics. This
    tests the independent final guard if a preparation provider regresses.
    """
    import core.reserve as reserve
    from core.uncertainty import effective_uncertainty

    record = json.loads(
        (
            Path(__file__).with_name("fixtures") / "reserve_october_forecast.json"
        ).read_text()
    )
    config, inputs = decode(record["config"]), decode(record["inputs"])
    config = replace(config, control=replace(config.control, soc_buffer_percent=1))
    full_upper = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.reserve.upper_pv_factor
    )[1]
    monkeypatch.setattr(reserve, "reserve_preparation_scales", lambda *_: full_upper)
    unsafe = _simulate_reserve_policy(config, inputs, None, 1.0, None)
    reference = _simulate_reserve_policy(
        config, inputs, None, 1.0, None, allow_ac=False
    )
    assert sum(f.inverter_output_wh for f in unsafe.flows) > 0
    result = simulate(config, inputs, 20)
    assert result.reserve_decision.reason == "dc_priority"
    assert result.flows == reference.flows
    assert sum(f.inverter_output_wh for f in result.flows) == 0
