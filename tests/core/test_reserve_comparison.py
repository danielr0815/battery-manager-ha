"""Shadow energy benefit must include terminal storage and all PV scenarios."""

from dataclasses import replace

import pytest
from core.planning_control import PlanningCancelled, cancellation_scope
from core.reserve import _reserve_cache
from core.reserve_comparison import compare_reserve_alternatives
from test_reserve_priority import plant, series


def test_fifteen_bounded_variants_report_spent_storage_and_standby_cost():
    config = plant()
    config = replace(
        config,
        reserve=replace(config.reserve, upper_pv_factor=1.5),
        inverter=replace(config.inverter, standby_power_w=15),
        control=replace(config.control, predrain_pv_confidence=0.5),
    )
    inputs = series([(0, 300, 0), (125, 0, 0)], 80)
    rows = compare_reserve_alternatives(config, inputs)
    assert len(rows) == 15
    assert {r.scenario for r in rows} == {"nominal", "pessimistic", "upper"}
    nominal = rows[0]
    # The nominal 125 Wh fits into the existing 150 Wh headroom. A larger
    # upper scenario may prepare, but does not spend nominal stored energy.
    assert nominal.candidate.ac_output_wh == 0
    assert nominal.import_reduction_wh == 0
    assert nominal.terminal_stored_reduction_wh == 0
    assert nominal.terminal_ac_reduction_wh == 0
    assert nominal.terminal_adjusted_gain_wh == 0
    assert nominal.preserves_dc_priority
    assert nominal.dc_service_regression_wh == 0
    assert rows[5].reference.terminal_stored_wh < nominal.reference.terminal_stored_wh
    assert rows[10].candidate.ac_output_wh == pytest.approx(26.25)  # includes standby
    assert rows[10].import_reduction_wh == pytest.approx(25)
    assert rows[10].terminal_stored_reduction_wh == 0
    assert rows[10].terminal_adjusted_gain_wh == pytest.approx(25)
    assert rows[4].candidate.ac_output_wh == 0
    assert rows[4].terminal_adjusted_gain_wh == 0
    assert inputs.start_soc_percent == 80
    assert _reserve_cache.get() is None


@pytest.mark.parametrize(
    "margins", [(), (0, 0), (-1,), (float("nan"),), (float("inf"),), (0, 1, 2, 3, 4, 5)]
)
def test_invalid_or_unbounded_margin_search_is_rejected(margins):
    with pytest.raises(ValueError, match="1–5"):
        compare_reserve_alternatives(plant(), series([(0, 600, 0)]), margins)


def test_comparison_requires_reserve_same_schedule_grid_and_honors_cancellation():
    config, inputs = plant(), series([(0, 600, 0)])
    with pytest.raises(ValueError, match="active reserve"):
        compare_reserve_alternatives(
            replace(config, reserve=replace(config.reserve, enabled=False)), inputs
        )
    with pytest.raises(ValueError, match="complete input horizon"):
        compare_reserve_alternatives(config, inputs, extra_ac_wh=())
    with pytest.raises(PlanningCancelled), cancellation_scope(lambda: True):
        compare_reserve_alternatives(config, inputs)
    assert _reserve_cache.get() is None
    assert (
        len(compare_reserve_alternatives(config, replace(inputs, slots=()), (0,))) == 3
    )
