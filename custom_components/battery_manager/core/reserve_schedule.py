"""DC-first preparation with high useful AC demand preferred over low-load hours.

The lower bound reserves DC energy before admitting optional AC. The upper
bound makes room for expected PV plus bounded uncertainty. Explicit offline
scenarios may use different PV, but do not set the normal plan's budget.
"""

from collections.abc import Sequence
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


def effective_market_weights(market_weights: Sequence[float | None]) -> list[float]:
    """Operator 2026-10-04: retain each known signal, neutralise only its gaps.

    Every opportunity uses demand × its own weight, with 1 for missing prices.
    A single score per opportunity preserves transitivity (known 500 W × 3 >
    known 600 W × 1 > unknown 550 W × 1) and the live controller's ordering.
    """
    return [weight if weight is not None else 1.0 for weight in market_weights]


def preparation_envelope(
    budgets: list[BatteryStep],
    nominal: list[BatteryStep],
    priorities: list[float],
    protection_floor: float,
    energy: float,
    market_weights: Sequence[float | None] | None = None,
    *,
    soft_maximum: float | None = None,
    dc_uncertainty_wh: float = 0.0,
) -> PreparationEnvelope:
    """Bounded passes over the available forecast, no solver dependency.

    Each demand level gets one backwards pass. A current slot only counts equal
    or better future AC opportunities; equal loads therefore keep the existing
    latest-use rule. Priorities are W, independent of partial slot lengths.
    """
    n = len(budgets)
    starting_energy = energy
    original_protection_floor = protection_floor
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
    dc_ceiling = [budgets[-1].maximum] * (n + 1)
    for index in range(n - 1, -1, -1):
        # The max-AC reference is not a DC target. Clamping to that reference
        # bought DC grid energy in the afternoon just to discharge it via AC
        # in the evening (operator's 2026-09-28 live recording).
        dc_ceiling[index] = effective[index].incoming_ceiling(
            dc_ceiling[index + 1], spill[index], ac=False
        )
    selected = [0.0] * n
    weights = (
        effective_market_weights(market_weights)
        if market_weights is not None
        else [1.0] * n
    )
    keys = list(zip(priorities, weights, strict=True))
    first_occurrence: dict[tuple[float, float], int] = {}
    for index, key in enumerate(keys):
        first_occurrence.setdefault(key, index)
    for (priority, weight), first in first_occurrence.items():
        check_cancelled()
        ceiling = budgets[-1].maximum
        # Earlier positions never query this demand level. Avoid repeating
        # irrelevant prefixes for every hourly load-allocation candidate.
        for index in range(n - 1, first - 1, -1):
            if keys[index] == (priority, weight):
                selected[index] = ceiling
            other_weight = weights[index]
            # Compare each signal with the same neutral weight for missing data.
            better = priorities[index] * other_weight >= priority * weight
            ceiling = effective[index].incoming_ceiling(
                ceiling, spill[index], ac=better
            )
    envelope = PreparationEnvelope(
        tuple(selected), tuple(dc_ceiling[1:]), tuple(minimum[1:]), sum(spill)
    )
    if soft_maximum is None:
        return envelope
    # The normal-PV target is a second envelope, not another subtraction from
    # the upper-PV envelope. Taking the tighter ceiling preserves the larger
    # of the two buffers without adding them. Only net solar charging imposes
    # a soft peak; a sunless horizon must not create optional AC discharge.
    soft_budgets = []
    for index, budget in enumerate(nominal):
        intake = min(max(0.0, budget.balance - budget.feedin), budget.charger_limit)
        charge = max(0.0, intake - budget.charger_standby) * budget.charge_eta
        maximum = (
            min(
                budget.maximum,
                max(soft_maximum, minimum[index + 1] + dc_uncertainty_wh),
            )
            if charge > budget.dc + ENERGY_EPSILON_WH
            else budget.maximum
        )
        soft_budgets.append(replace(budget, maximum=maximum))
    if all(
        soft.maximum == normal.maximum
        for soft, normal in zip(soft_budgets, nominal, strict=True)
    ):
        return envelope
    soft = preparation_envelope(
        soft_budgets,
        nominal,
        priorities,
        original_protection_floor,
        starting_energy,
        market_weights,
    )
    return PreparationEnvelope(
        tuple(
            min(a, b) for a, b in zip(envelope.ac_ceiling, soft.ac_ceiling, strict=True)
        ),
        tuple(
            min(a, b) for a, b in zip(envelope.dc_ceiling, soft.dc_ceiling, strict=True)
        ),
        envelope.dc_minimum,
        envelope.unavoidable_export_wh,
    )
