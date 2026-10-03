"""DC-first preparation with high useful AC demand preferred over low-load hours.

The lower bound reserves nominal DC energy before admitting optional AC. The
upper bound makes room for the upper PV forecast. They serve different purposes:
optimistic sunshine may require headroom, but cannot guarantee future DC supply.
"""

from dataclasses import dataclass, replace

from .planning_control import check_cancelled
from .reserve_energy import ENERGY_EPSILON_WH, BatteryStep

# Stay above <= activation checks despite floating-point roundoff. This is
# ten micro-Wh, a numerical tolerance rather than an added SOC reserve.
PROTECTION_MARGIN_WH = 10 * ENERGY_EPSILON_WH


@dataclass(frozen=True)
class PreparationEnvelope:
    ac_ceiling: tuple[float, ...]  # following-state ceiling for each decision
    dc_ceiling: tuple[float, ...]
    dc_minimum: tuple[float, ...]
    unavoidable_export_wh: float


def coherent_market_weights(
    priorities: list[float], market_weights: list[float | None]
) -> list[float | None]:
    """Incomplete useful-opportunity coverage uses one load ordering for the horizon.

    Pairwise missing-price fallback is cyclic (500 W*3 > 600 W > unknown
    550 W > 500 W). A single fallback preserves transitivity and lets the live
    controller use exactly the planner's ordering. Empty/PV-covered intervals
    cannot compete for AC energy and therefore do not require price coverage.
    """
    if any(
        priority > 0 and weight is None
        for priority, weight in zip(priorities, market_weights, strict=True)
    ):
        return [None] * len(priorities)
    return market_weights


def preparation_envelope(
    budgets: list[BatteryStep],
    nominal: list[BatteryStep],
    priorities: list[float],
    protection_floor: float,
    energy: float,
    market_weights: list[float | None] | None = None,
) -> PreparationEnvelope:
    """Bounded passes over the available forecast, no solver dependency.

    Each demand level gets one backwards pass. A current slot only counts equal
    or better future AC opportunities; equal loads therefore keep the existing
    latest-use rule. Priorities are W, independent of partial slot lengths.
    """
    n = len(budgets)
    # With no DC demand there is no future DC obligation. With no available
    # PSU, retain the physical battery floor exactly (no protective epsilon).
    if (
        any(budget.dc > ENERGY_EPSILON_WH for budget in nominal)
        and protection_floor > budgets[0].floor
    ):
        protection_floor += PROTECTION_MARGIN_WH
    else:
        protection_floor = budgets[0].floor
    minimum = [protection_floor] * (n + 1)
    for index in range(n - 1, -1, -1):
        budget = nominal[index]
        intake = min(max(0.0, budget.balance - budget.feedin), budget.charger_limit)
        charge = max(0.0, intake - budget.charger_standby) * budget.charge_eta
        minimum[index] = min(
            budget.maximum,
            max(protection_floor, minimum[index + 1] + budget.dc - charge),
        )
    effective = [
        budget
        if budget.inverter_floor >= minimum[index + 1]
        else replace(budget, inverter_floor=minimum[index + 1])
        for index, budget in enumerate(budgets)
    ]
    spill = []
    for budget in effective:
        energy, exported = budget.project(energy)
        spill.append(exported)
    dc_ceiling = [budgets[0].maximum] * (n + 1)
    for index in range(n - 1, -1, -1):
        # The max-AC reference is not a DC target. Clamping to that reference
        # bought DC grid energy in the afternoon just to discharge it via AC
        # in the evening (operator's 2026-09-28 live recording).
        dc_ceiling[index] = effective[index].incoming_ceiling(
            dc_ceiling[index + 1], spill[index], ac=False
        )
    selected = [0.0] * n
    weights = (
        coherent_market_weights(priorities, market_weights)
        if market_weights is not None
        else [None] * n
    )
    keys = list(zip(priorities, weights, strict=True))
    first_occurrence: dict[tuple[float, float | None], int] = {}
    for index, key in enumerate(keys):
        first_occurrence.setdefault(key, index)
    for (priority, weight), first in first_occurrence.items():
        check_cancelled()
        ceiling = budgets[0].maximum
        # Earlier positions never query this demand level. Avoid repeating
        # irrelevant prefixes for every hourly load-allocation candidate.
        for index in range(n - 1, first - 1, -1):
            if keys[index] == (priority, weight):
                selected[index] = ceiling
            other_weight = weights[index]
            # One consistent fallback applies to all useful opportunities.
            better = (
                priorities[index] * other_weight >= priority * weight
                if weight is not None and other_weight is not None
                else priorities[index] >= priority
            )
            ceiling = effective[index].incoming_ceiling(
                ceiling, spill[index], ac=better
            )
    return PreparationEnvelope(
        tuple(selected), tuple(dc_ceiling[1:]), tuple(minimum[1:]), sum(spill)
    )
