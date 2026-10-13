"""F-DC-SOURCE-BLOCKS: contiguous source choices within physical DC budgets."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from core.model import HourSlot
from core.reserve_sources import SourceStep, market_sources, net_draw, source_state
from core.simulate import simulate, step_hour
from test_dc_pv_market import dc_plant
from test_dc_source_hysteresis import context


def source_steps(count=24, budget_steps=6, minutes=5):
    config = dc_plant()
    start = datetime(2026, 10, 13, tzinfo=UTC)
    steps = []
    for index in range(count):
        slot = HourSlot(
            index,
            start + timedelta(minutes=index * minutes),
            minutes / 60,
            0,
            0,
            0,
            100 * minutes / 60,
        )
        held = step_hour(config, 80, slot, 100, dc24_from_grid=True)
        natural = step_hour(config, 80, slot, 100)
        # Small alternating conversion losses used to select isolated steps.
        held = replace(
            held, psu_grid_import_wh=held.grid_import_wh * (1 + index % 2 / 100)
        )
        steps.append(
            SourceStep(
                slot, natural if index < budget_steps else held, (held, natural), 20
            )
        )
    return config, tuple(steps)


def test_small_efficiency_differences_produce_one_half_hour_off_block():
    config, steps = source_steps()
    selected = market_sources(config, steps, [2] * len(steps))
    assert selected == ((True, False),) * 18 + ((False, False),) * 6
    assert sum(net_draw(s.flow) for s in steps) == pytest.approx(50)


def test_higher_price_band_precedes_more_efficient_cheap_steps():
    config, steps = source_steps()
    # Cheap opportunities save twice as much per battery Wh. The expensive
    # interval must still receive the available OFF budget first.
    steps = tuple(
        replace(
            s,
            choices=tuple(
                replace(f, psu_grid_import_wh=100) if f.support_dc24 else f
                for f in s.choices
            ),
        )
        if index >= 6
        else s
        for index, s in enumerate(steps)
    )
    assert market_sources(config, steps, [3] * 6 + [1] * 18) == (
        ((False, False),) * 6 + ((True, False),) * 18
    )


@pytest.mark.parametrize("minutes,budget_steps", [(5, 2), (15, 2), (60, 1)])
def test_block_uses_elapsed_duration_and_never_exceeds_remaining_energy(
    minutes, budget_steps
):
    config, steps = source_steps(8, budget_steps, minutes)
    selected = market_sources(config, steps, [2] * 8)
    assert sum(not state[0] for state in selected) == budget_steps
    assert selected[-budget_steps:] == ((False, False),) * budget_steps


@pytest.mark.parametrize("boundary", ["price", "required", "already_off", "gap"])
def test_block_stops_at_price_or_compulsory_or_already_selected_boundary(boundary):
    config, steps = source_steps(8, 2)
    weights = [2] * 8
    steps = list(steps)
    if boundary == "price":
        weights[6] = 1
    elif boundary == "required":
        steps[6] = replace(steps[6], choices=(steps[6].choices[0],))
    elif boundary == "gap":
        steps[7] = replace(
            steps[7],
            slot=replace(
                steps[7].slot, start=steps[7].slot.start + timedelta(minutes=5)
            ),
        )
    else:
        # This neighbor's zero-cost base is already OFF; do not debit it twice.
        steps[6] = replace(steps[6], choices=(steps[6].choices[1],))
    selected = market_sources(config, steps, weights)
    assert selected[7] == (False, False)
    assert selected[6] == (
        (False, False) if boundary == "already_off" else (True, False)
    )
    assert sum(state == (False, False) for state in selected) == 2


def test_hysteresis_applies_after_the_first_forecast_step():
    config, inputs = context()
    # Prepend an idle step: a restart at the same SOC one hour later must
    # require the same reporting quantum as an immediate real restart.
    idle = replace(inputs.slots[0], dc_wh=0)
    slots = (idle,) + tuple(
        replace(slot, index=slot.index + 1, start=slot.start + timedelta(hours=1))
        for slot in inputs.slots
    )
    result = simulate(config, replace(inputs, slots=slots), 20)
    assert not result.flows[1].support_dc24_start
    assert not result.flows[1].support_dc48_start
    assert sum(f.unserved_dc_wh for f in result.flows) == 0
    assert result.total_export_wh == pytest.approx(0)


def test_neutral_prices_keep_reference_source_choices():
    config, steps = source_steps()
    assert market_sources(config, steps, [1] * len(steps)) == tuple(
        source_state(step.flow) for step in steps
    )


def test_naive_legacy_clock_can_still_form_contiguous_blocks():
    config, steps = source_steps()
    steps = tuple(
        replace(s, slot=replace(s.slot, start=s.slot.start.replace(tzinfo=None)))
        for s in steps
    )
    assert market_sources(config, steps, [2] * len(steps)) == (
        ((True, False),) * 18 + ((False, False),) * 6
    )
