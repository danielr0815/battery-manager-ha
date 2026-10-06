"""Market peaks may outweigh 100 W of load, never DC supply or PV deadlines."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.market import peak_weights, slot_weights
from core.model import MarketPrice
from core.simulate import simulate
from test_reserve_priority import plant, series

START = datetime(2026, 10, 2, tzinfo=UTC)


def priced(values, prices, soc=80):
    inputs = series(values, soc)
    slots = tuple(
        replace(s, start=START + timedelta(hours=i)) for i, s in enumerate(inputs.slots)
    )
    data = tuple(
        MarketPrice(START + timedelta(hours=i), START + timedelta(hours=i + 1), value)
        for i, value in enumerate(prices)
        if value is not None
    )
    return replace(inputs, now=START, slots=slots, market_prices=data)


def test_peak_beats_100w_more_load_and_live_controller_keeps_that_energy():
    inputs = priced([(0, 600, 0), (0, 500, 0), (300, 0, 0)], [100, 300, 100])
    baseline = simulate(plant(), replace(inputs, market_prices=()), 20)
    result = simulate(plant(), inputs, 20)
    assert baseline.flows[0].inverter_output_wh == pytest.approx(150)
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh == pytest.approx(125)
    # Even fresh measured AC demand cannot pre-empt the later market window.
    assert result.reserve_decision.live_ac_floor_percent >= inputs.start_soc_percent
    assert all(f.inverter_limit_w in (0, 1000) for f in result.flows)
    assert result.min_soc_percent >= 20


@pytest.mark.parametrize("prices", [[100, 100, 100], [100, 101, 100], [100, None, 100]])
def test_flat_small_or_missing_peak_price_retains_load_priority(prices):
    result = simulate(
        plant(), priced([(0, 600, 0), (0, 500, 0), (300, 0, 0)], prices), 20
    )
    assert result.flows[0].inverter_output_wh == pytest.approx(150)
    assert result.flows[1].inverter_output_wh == 0


def test_peak_cannot_create_ac_budget_or_displace_dc():
    inputs = priced([(0, 600, 100), (0, 500, 200), (300, 0, 0)], [100, 300, 100], 95)
    result = simulate(plant(), inputs, 20)
    assert sum(f.inverter_output_wh for f in result.flows) == 0
    assert sum(f.psu24_delivered_wh for f in result.flows) == 0
    assert result.total_export_wh == pytest.approx(0)


def test_peak_after_pv_deadline_cannot_replace_earlier_headroom():
    result = simulate(
        plant(),
        priced([(0, 100, 0), (300, 0, 0), (0, 600, 0)], [100, 100, 300], 85),
        20,
    )
    assert result.flows[0].inverter_output_wh == pytest.approx(100)
    assert result.flows[2].inverter_output_wh == 0


def test_peak_with_tiny_load_does_not_waste_standby():
    c = plant()
    c = replace(c, inverter=replace(c.inverter, standby_power_w=15))
    result = simulate(
        c, priced([(0, 600, 0), (0, 50, 0), (300, 0, 0)], [100, 1000, 100]), 20
    )
    assert result.flows[0].inverter_output_wh > 100
    baseline = simulate(
        c,
        replace(
            priced([(0, 600, 0), (0, 50, 0), (300, 0, 0)], [100, 1000, 100]),
            market_prices=(),
        ),
        20,
    )
    assert result.flows == baseline.flows
    # Only the remainder too small for another complete high-load step uses
    # the small load; an extreme price does not reverse that order.


def test_negative_prices_are_valid_and_do_not_authorize_grid_charging():
    inputs = priced([(0, 600, 0), (0, 500, 0), (300, 0, 0)], [-150, -10, -150])
    result = simulate(plant(), inputs, 20)
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh > 100
    assert sum(f.psu48_battery_charge_wh for f in result.flows) == 0


@pytest.mark.parametrize("step_minutes", [15, 60])
def test_peak_preference_is_four_hours_not_four_rows(step_minutes):
    count = 24 * 60 // step_minutes
    prices = tuple(
        MarketPrice(
            START + timedelta(minutes=i * step_minutes),
            START + timedelta(minutes=(i + 1) * step_minutes),
            100 + i,
        )
        for i in range(count)
    )
    weights = peak_weights(prices)
    assert sum(step_minutes / 60 for w in weights if w > 1) == 4
    assert weights[-1] > weights[0]


def test_dst_fold_and_partial_price_coverage():
    zone = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 25, 0, tzinfo=UTC)
    prices = tuple(
        MarketPrice(
            (start + timedelta(hours=i)).astimezone(zone),
            (start + timedelta(hours=i + 1)).astimezone(zone),
            100 + 200 * (i == 1),
        )
        for i in range(3)
    )
    inputs = series([(0, 100, 0)] * 3)
    slots = tuple(replace(s, start=prices[i].start) for i, s in enumerate(inputs.slots))
    weights = slot_weights(slots, prices)
    assert weights[0] == 1
    assert weights[1] == 3
    assert slot_weights((replace(slots[0], duration=1.5),), prices[:1]) == [1]
    assert slot_weights(inputs.slots, prices) == [None] * 3


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_invalid_price_rejected(value):
    with pytest.raises(ValueError):
        MarketPrice(START, START + timedelta(hours=1), value)


def test_invalid_time_and_overlap_rejected():
    with pytest.raises(ValueError):
        MarketPrice(START, START, 100)
    with pytest.raises(ValueError):
        MarketPrice(START.replace(tzinfo=None), START + timedelta(hours=1), 100)
    inputs = priced([(0, 100, 0)], [100])
    with pytest.raises(ValueError):
        replace(inputs, market_prices=inputs.market_prices * 2)


def test_future_pv_energy_cannot_be_borrowed_by_a_live_peak_override():
    inputs = priced(
        [(0, 0, 0), (900, 0, 0), (0, 500, 0), (600, 0, 0)], [100, 100, 300, 100]
    )
    result = simulate(plant(), inputs, 20)
    assert result.flows[2].inverter_output_wh > 0
    assert result.reserve_decision.live_ac_override_demand_w is None


def test_october_forecast_uses_dc_first_without_buying_back_early_ac():
    import json
    from pathlib import Path

    from core.replay import decode
    from core.uncertainty import effective_uncertainty

    record = json.loads(
        (
            Path(__file__).with_name("fixtures") / "reserve_october_forecast.json"
        ).read_text()
    )
    config, inputs = decode(record["config"]), decode(record["inputs"])
    nominal = simulate(config, inputs, 20)
    assert sum(f.inverter_output_wh for f in nominal.flows) == 0
    # The uncapped quarter-spread needs some natural DC preparation again,
    # while the full upper-PV scenario must not release early optional AC.
    assert not nominal.flows[0].support_dc24_start
    saturday = [
        f
        for s, f in zip(inputs.slots, nominal.flows, strict=True)
        if s.start.date().isoformat() == "2026-10-03"
    ]
    assert sum(f.unserved_dc_wh for f in saturday) == 0
    assert (
        min(f.soc_end_percent for f in saturday)
        >= config.control.inverter_min_soc_percent
    )
    assert nominal.max_soc_percent < config.battery.soc_max_percent
    assert nominal.reserve_decision.headroom_wh == 0
    assert nominal.reserve_decision.reason == "no_preparation_needed"
    # Actual stronger sunshine can release AC again: the DC comparison uses
    # that scenario's physics, not a permanent ban based on an earlier run.
    upper = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.reserve.upper_pv_factor
    )[1]
    sunny = simulate(config, inputs, 20, pv_scale=upper)
    assert sum(f.inverter_output_wh for f in sunny.flows) > 0


def test_missing_useful_opportunity_preserves_known_peak_with_transitive_scores():
    """Known 500 W × 3 outranks 600 W × 1 and unknown 550 W × 1."""
    inputs = priced(
        [(0, 500, 0), (0, 600, 0), (0, 550, 0), (350, 0, 0)], [300, 100, None, 100]
    )
    result = simulate(plant(), inputs, 20)
    plain = simulate(plant(), replace(inputs, market_prices=()), 20)
    assert [f.inverter_output_wh for f in result.flows] == pytest.approx(
        [500 / 3, 0, 0, 0]
    )
    assert result.flows != plain.flows
    assert result.reserve_decision.market_ranking_reason == "weighted_partial"
    assert result.reserve_decision.live_ac_override_demand_w == pytest.approx(500)
    assert result.min_soc_percent >= 20


@pytest.mark.parametrize("current_price", [None, 100])
def test_known_later_peak_protects_live_budget_even_without_current_price(
    current_price,
):
    inputs = priced(
        [(0, 600, 0), (0, 500, 0), (0, 550, 0), (350, 0, 0)],
        [current_price, 300, None, 100],
    )
    result = simulate(plant(), inputs, 20)
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh == pytest.approx(500 / 3)
    assert result.flows[2].inverter_output_wh == 0
    assert result.reserve_decision.market_ranking_reason == "weighted_partial"
    assert result.reserve_decision.live_ac_floor_percent >= inputs.start_soc_percent
    assert result.reserve_decision.live_ac_override_demand_w == pytest.approx(1500)


def test_unpriced_larger_load_can_still_outrank_a_known_peak():
    result = simulate(
        plant(),
        priced([(0, 100, 0), (0, 600, 0), (300, 0, 0)], [300, None, 100]),
        20,
    )
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh == pytest.approx(150)
    assert result.reserve_decision.live_ac_override_demand_w == pytest.approx(200)


def test_next_day_without_prices_does_not_erase_today_peak():
    inputs = priced(
        [(0, 500, 0), (0, 600, 0)] + [(0, 0, 0)] * 22 + [(0, 550, 0), (350, 0, 0)],
        [300, 100] + [100] * 22 + [None, None],
    )
    result = simulate(plant(), inputs, 20)
    assert inputs.slots[24].start.date() != inputs.now.date()
    assert result.flows[0].inverter_output_wh == pytest.approx(500 / 3)
    assert result.flows[1].inverter_output_wh == 0
    assert result.flows[24].inverter_output_wh == 0
    assert result.reserve_decision.market_ranking_reason == "weighted_partial"


def test_partially_priced_step_retains_bonus_only_for_covered_seconds():
    inputs = priced([(0, 500, 0), (0, 600, 0)], [300, 100])
    prices = (
        replace(inputs.market_prices[0], end=START + timedelta(minutes=2)),
        inputs.market_prices[1],
    )
    assert slot_weights((replace(inputs.slots[0], duration=5 / 60),), prices) == (
        pytest.approx([1.8])
    )
    # An entirely unpriced interval remains unknown for diagnostics.
    assert slot_weights(
        (replace(inputs.slots[0], start=START + timedelta(minutes=5)),), ()
    ) == [None]


def test_missing_price_for_non_opportunity_preserves_known_market_peak():
    for values in (
        [(0, 500, 0), (0, 600, 0), (0, 0, 0), (350, 0, 0)],
        [(0, 500, 0), (0, 600, 0), (100, 100, 0), (350, 0, 0)],
    ):
        result = simulate(plant(), priced(values, [300, 100, None, 100]), 20)
        assert result.flows[0].inverter_output_wh == pytest.approx(500 / 3)
        assert result.flows[1].inverter_output_wh == 0
        assert result.reserve_decision.market_ranking_reason == "weighted"


def test_flat_and_absent_price_diagnostics_remain_distinct():
    values = [(0, 600, 0), (300, 0, 0)]
    assert (
        simulate(
            plant(), priced(values, [100, 100]), 20
        ).reserve_decision.market_ranking_reason
        == "load_priority_flat"
    )
    assert (
        simulate(
            plant(), priced(values, [None, None]), 20
        ).reserve_decision.market_ranking_reason
        == "load_priority_no_prices"
    )
    assert (
        simulate(
            plant(),
            priced([(0, 600, 0), (0, 500, 0), (300, 0, 0)], [100, None, 100]),
            20,
        ).reserve_decision.market_ranking_reason
        == "load_priority_partial"
    )
