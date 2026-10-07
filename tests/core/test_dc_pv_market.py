"""F-DC-PV-MARKET: PV priority and market placement never enlarge DC budgets."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from core.live_dc import pv_covers_dc
from core.simulate import simulate
from test_market import priced
from test_reserve import config, inputs
from test_reserve_priority import plant


def dc_plant():
    c = plant()
    return replace(c, control=replace(c.control, soc_buffer_percent=0))


def test_peak_moves_existing_dc_energy_without_extra_depletion():
    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [300, 100, 100])
    baseline = simulate(c, replace(i, market_prices=()), 20)
    result = simulate(c, i, 20)
    assert [f.psu24_delivered_wh for f in baseline.flows] == pytest.approx([100, 0, 0])
    assert [f.psu24_delivered_wh for f in result.flows] == pytest.approx([0, 100, 0])
    assert result.total_import_wh == pytest.approx(baseline.total_import_wh)
    assert result.total_export_wh == baseline.total_export_wh == 0
    assert result.end_soc_percent == pytest.approx(baseline.end_soc_percent)
    assert result.reserve_decision.dc_budget_wh == pytest.approx(100)
    assert result.reserve_decision.dc_shifted_wh == pytest.approx(100)
    assert result.reserve_decision.reason == "dc_market_supply"
    assert sum(f.inverter_output_wh for f in result.flows) == 0


@pytest.mark.parametrize(
    "prices", [[100, 100, 100], [None, None, None], [-10, -10, -10]]
)
def test_flat_or_missing_prices_preserve_source_placement(prices):
    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], prices)
    result = simulate(c, i, 20)
    baseline = simulate(c, replace(i, market_prices=()), 20)
    assert result.flows == baseline.flows
    assert result.reserve_decision.dc_shifted_wh == 0


@pytest.mark.parametrize("prices", [[300, None, 100], [-10, -150, -150]])
def test_known_peak_survives_missing_or_negative_other_prices(prices):
    result = simulate(
        dc_plant(), priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], prices), 20
    )
    assert result.flows[0].psu24_delivered_wh == 0
    assert result.flows[1].psu24_delivered_wh == pytest.approx(100)
    assert sum(f.psu48_battery_charge_wh for f in result.flows) == 0


def test_peak_with_zero_budget_still_preserves_energy():
    result = simulate(
        dc_plant(), priced([(0, 0, 100), (0, 0, 100), (150, 0, 0)], [300, 100, 100]), 20
    )
    assert result.reserve_decision.dc_budget_wh == 0
    assert result.reserve_decision.dc_shifted_wh == 0
    assert [f.psu24_delivered_wh for f in result.flows[:2]] == pytest.approx([100, 100])


def test_peak_after_recharge_cannot_spend_preceding_budget():
    c = dc_plant()
    i = priced([(0, 0, 100), (250, 0, 0), (0, 0, 100)], [100, 100, 300])
    result = simulate(c, i, 20)
    assert result.flows[-1].psu24_delivered_wh == pytest.approx(100)
    assert result.reserve_decision.dc_shifted_wh == 0


@pytest.mark.parametrize("soc", [80, 95])
def test_full_pv_supplies_both_buses_including_at_full_soc(soc):
    result = simulate(
        config(dc24_active=True, dc48_active=True), inputs(soc, [(200, 100, 60)]), 20
    )
    assert not result.flows[0].support_dc24 and not result.flows[0].support_dc48
    assert result.total_import_wh == 0
    assert result.flows[0].soc_end_percent >= soc
    assert result.reserve_decision.reason == "dc_pv_supply"


def test_partial_pv_keeps_only_necessary_rail_support():
    c = config(dc24_active=True)
    result = simulate(c, inputs(80, [(125, 100, 60)]), 20)
    assert result.flows[0].support_dc24
    assert not result.flows[0].support_dc48
    assert result.flows[0].psu24_delivered_wh == pytest.approx(40)
    assert result.flows[0].soc_end_percent == pytest.approx(78)


@pytest.mark.parametrize("forced", ["dc24_forced_on", "dc48_forced_on"])
def test_manual_source_survives_pv_priority(forced):
    result = simulate(config(**{forced: True}), inputs(80, [(200, 100, 60)]), 20)
    assert getattr(result.flows[0], "support_" + forced.removesuffix("_forced_on"))
    assert result.reserve_decision.reason == "manual_support"


def test_low_soc_protection_survives_pv_priority():
    result = simulate(config(), inputs(5, [(200, 100, 60)]), 20)
    assert result.flows[0].support_dc24 and result.flows[0].support_dc48
    assert result.reserve_decision.reason == "dc_support_protection"


@pytest.mark.parametrize("soc", [80, 95])
def test_live_pv_requires_start_margin_then_full_coverage(soc):
    c = dc_plant()
    now = datetime(2026, 10, 7, tzinfo=UTC)
    assert not pv_covers_dc(c, soc, now, 105, 100, False)
    assert pv_covers_dc(c, soc, now, 110, 100, False)
    assert pv_covers_dc(c, soc, now, 100, 100, True)
    assert not pv_covers_dc(c, soc, now, 99, 100, True)


@pytest.mark.parametrize("surplus,dc", [(None, 100), (100, None), (0, 100), (100, -1)])
def test_unknown_or_invalid_power_never_grants_pv_permission(surplus, dc):
    assert not pv_covers_dc(dc_plant(), 80, datetime.now(UTC), surplus, dc, False)


def test_live_pv_accounts_for_losses_standby_and_limits():
    c = dc_plant()
    c = replace(
        c,
        charger=replace(c.charger, eta=0.8, standby_power_w=10),
        support=replace(c.support, dcdc_eta=0.8),
    )
    now = datetime.now(UTC)
    assert not pv_covers_dc(c, 80, now, 150, 100, False)
    assert pv_covers_dc(c, 80, now, 200, 100, False)
    limited = replace(c, charger=replace(c.charger, max_power_w=100))
    assert not pv_covers_dc(limited, 80, now, 2000, 100, True)
    rail_limited = replace(c, support=replace(c.support, dcdc_max_power_w=50))
    assert not pv_covers_dc(rail_limited, 80, now, 2000, 100, True)


def test_recorded_october_plan_has_no_extra_dc_budget_or_supply_loss():
    """The real October forecast has no valid within-window shift; keep it."""
    import json
    from pathlib import Path

    from core.replay import decode
    from core.reserve import _simulate_reserve_reference

    record = json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("reserve_oct06_morning.json")
        .read_text()
    )
    c, i = decode(record["config"]), decode(record["inputs"])
    baseline = _simulate_reserve_reference(c, i, None, 1, None, traces={})
    result = simulate(c, i, 20)
    assert result.total_import_wh <= baseline.total_import_wh + 1e-6
    assert result.total_export_wh <= baseline.total_export_wh + 1e-6
    assert result.end_soc_percent >= baseline.end_soc_percent - 1e-6
    assert sum(f.unserved_dc_wh for f in result.flows) == 0
    assert result.reserve_decision.dc_shifted_wh == 0
    assert result.flows == baseline.flows


@pytest.mark.parametrize(
    "fault",
    [
        "outage",
        "ac",
        "reserve",
        "depletion",
        "end_soc",
        "import",
        "export",
        "market",
        "grid_charge",
    ],
)
def test_shift_validation_rejects_each_observable_regression(fault):
    """Adversarial proposed flows cannot spend improvements on a later failure."""
    from core.model import HourSlot
    from core.reserve_sources import SourceStep, valid_shift

    slot = HourSlot(0, datetime(2026, 10, 7, tzinfo=UTC), 1, 0, 0, 0, 100)
    from core.simulate import step_hour

    c = dc_plant()
    held = step_hour(c, 80, slot, 100, dc24_from_grid=True)
    natural = step_hour(c, 80, slot, 100)
    later_held = step_hour(c, natural.soc_end_percent, slot, 100, dc24_from_grid=True)
    baseline = (
        SourceStep(slot, held, (held,), 60),
        SourceStep(slot, natural, (natural,), 60),
    )
    trial = (
        SourceStep(slot, natural, (natural,), 60),
        SourceStep(slot, later_held, (later_held,), 60),
    )
    assert valid_shift(c, baseline, trial, [2, 1])
    updates = {
        "outage": {"unserved_dc_wh": 1},
        "ac": {"inverter_output_wh": 1},
        "reserve": {"soc_end_percent": 59},
        "depletion": {"battery_discharge_wh": 101},
        "end_soc": {"soc_end_percent": 69},
        "import": {"grid_import_wh": 101},
        "export": {"grid_export_wh": 1},
        "market": {"psu_grid_import_wh": 101},
        "grid_charge": {"psu48_battery_charge_wh": 1},
    }
    index = 1 if fault == "end_soc" else 0
    bad = list(trial)
    bad[index] = replace(bad[index], flow=replace(bad[index].flow, **updates[fault]))
    assert not valid_shift(c, baseline, tuple(bad), [2, 1])


def test_source_loss_does_not_purchase_a_larger_reference_budget():
    """Alternative source availability can shrink after the original trace."""
    from core.model import HourSlot
    from core.reserve_sources import SourceStep, market_sources, source_windows

    slot = HourSlot(0, datetime(2026, 10, 7, tzinfo=UTC), 1, 0, 0, 0, 100)
    from core.simulate import step_hour

    held = step_hour(dc_plant(), 80, slot, 100, dc24_from_grid=True)
    natural = step_hour(dc_plant(), 80, slot, 100)
    step = SourceStep(slot, held, (natural,), 20)
    assert market_sources(dc_plant(), (step,), [2]) == ((True, False),)
    charged = replace(
        step, flow=replace(held, battery_charge_wh=10, psu48_battery_charge_wh=10)
    )
    assert source_windows((charged, step)) == (range(0, 2),)


@pytest.mark.parametrize("time_case", ["quarter", "dst"])
def test_dc_market_placement_uses_real_price_intervals(time_case):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from core.model import MarketPrice

    c = dc_plant()
    i = priced([(0, 0, 100), (0, 0, 100), (250, 0, 0)], [300, 100, 100])
    if time_case == "quarter":
        prices = tuple(
            MarketPrice(
                p.start + timedelta(minutes=15 * k),
                p.start + timedelta(minutes=15 * (k + 1)),
                p.eur_per_mwh,
            )
            for p in i.market_prices
            for k in range(4)
        )
        i = replace(i, market_prices=prices)
    else:
        start = datetime(2026, 10, 25, tzinfo=UTC)
        slots = tuple(
            replace(slot, start=start + timedelta(hours=k))
            for k, slot in enumerate(i.slots)
        )
        prices = tuple(
            MarketPrice(
                slot.start.astimezone(ZoneInfo("Europe/Berlin")),
                (slot.start + timedelta(hours=1)).astimezone(ZoneInfo("Europe/Berlin")),
                price.eur_per_mwh,
            )
            for slot, price in zip(slots, i.market_prices, strict=True)
        )
        i = replace(i, now=start, slots=slots, market_prices=prices)
        assert prices[0].start.hour == prices[1].start.hour == 2
        assert prices[0].start.utcoffset() != prices[1].start.utcoffset()
    result = simulate(c, i, 20)
    assert [f.psu24_delivered_wh for f in result.flows[:2]] == pytest.approx([0, 100])
    assert result.reserve_decision.dc_shifted_wh == pytest.approx(100)
    assert result.end_soc_percent == pytest.approx(95)


def test_native_48v_peak_shift_uses_only_proven_psu_output():
    c = dc_plant()
    c = replace(
        c,
        battery=replace(c.battery, capacity_wh=5000),
        support=replace(
            c.support,
            native48_base_w=20,
            dc24_available=False,
            psu48_bus_voltage_v=49.56,
            psu48_max_power_w=20,
            dc48_power_w=20,
        ),
    )
    i = priced([(0, 0, 20), (0, 0, 20), (780, 0, 0)], [300, 100, 100])
    reference = simulate(c, replace(i, market_prices=()), 20)
    result = simulate(c, i, 20)
    assert [f.psu48_delivered_wh for f in reference.flows[:2]] == pytest.approx([10, 0])
    assert [f.psu48_delivered_wh for f in result.flows[:2]] == pytest.approx([0, 10])
    assert result.reserve_decision.dc_shifted_wh == pytest.approx(10)
    assert result.end_soc_percent == pytest.approx(reference.end_soc_percent)
    assert result.total_import_wh == pytest.approx(reference.total_import_wh)
    unknown = simulate(
        replace(c, support=replace(c.support, psu48_bus_voltage_v=None)), i, 20
    )
    assert unknown.reserve_decision.dc_shifted_wh == 0
    assert sum(f.psu48_delivered_wh for f in unknown.flows) == 0


@pytest.mark.parametrize("later_pv,source", [(0, True), (300, False)])
def test_partial_pv_can_use_battery_only_if_reference_permits_it(later_pv, source):
    result = simulate(config(), inputs(80, [(125, 100, 60), (later_pv, 0, 0)]), 20)
    assert result.flows[0].support_dc24 is source
    if source:
        assert result.flows[0].soc_end_percent == pytest.approx(78)
    else:
        assert result.flows[0].psu24_delivered_wh == 0
        assert result.flows[0].soc_end_percent == pytest.approx(76.5)
