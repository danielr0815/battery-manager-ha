"""F-DC-PV-MARKET: finite source choices and budget-neutral DC placement."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from heapq import heappop, heappush

from .model import HourFlows, HourSlot, SystemConfig
from .planning_control import check_cancelled
from .reserve_energy import ENERGY_EPSILON_WH

SourceState = tuple[bool, bool]


@dataclass(frozen=True)
class SourceStep:
    slot: HourSlot
    flow: HourFlows
    choices: tuple[HourFlows, ...]
    minimum_soc: float


def net_draw(flow: HourFlows) -> float:
    """Stored energy actually lost; PV circulation is not an extra budget."""
    return max(0.0, flow.battery_discharge_wh - flow.battery_charge_wh)


def pv_recharge(flow: HourFlows) -> bool:
    """Only solar replenishment closes a window, never PSU charging."""
    return (
        flow.battery_charge_wh - flow.psu48_battery_charge_wh
        > flow.battery_discharge_wh + ENERGY_EPSILON_WH
    )


def grid_dc(config: SystemConfig, flow: HourFlows) -> float:
    """Actual PSU grid intake; old evidence retains its original accounting."""
    if flow.psu_grid_import_wh is not None:
        return flow.psu_grid_import_wh
    return (
        flow.psu24_delivered_wh / config.support.psu24_eta
        + flow.psu48_delivered_wh / config.support.psu48_eta
    )


def source_state(flow: HourFlows) -> SourceState:
    return flow.support_dc24, flow.support_dc48


def source_choices(
    config: SystemConfig,
    required: SourceState,
    evaluate: Callable[[bool, bool], HourFlows],
) -> tuple[HourFlows, ...]:
    """Manual/protection requests are compulsory; unavailable sources stay off."""
    return tuple(
        evaluate(dc24, dc48)
        for dc24 in (False, True)
        for dc48 in (False, True)
        if (not required[0] or dc24)
        and (not required[1] or dc48)
        and (not dc24 or config.support.dc24_available)
        and (not dc48 or config.support.dc48_available)
    )


def least_grid_source(
    baseline: HourFlows,
    choices: Sequence[HourFlows],
    previous: SourceState,
) -> HourFlows:
    """Use PV on either bus without purchasing extra battery depletion."""
    allowed = [
        flow
        for flow in choices
        if net_draw(flow) <= net_draw(baseline) + ENERGY_EPSILON_WH
        and flow.soc_end_percent >= baseline.soc_end_percent - ENERGY_EPSILON_WH
        and flow.unserved_dc_wh <= baseline.unserved_dc_wh + ENERGY_EPSILON_WH
        and flow.grid_export_wh <= baseline.grid_export_wh + ENERGY_EPSILON_WH
        and flow.grid_import_wh <= baseline.grid_import_wh + ENERGY_EPSILON_WH
    ]
    # A protection request can rule out every alternative at an unusual plant
    # configuration; the original protected simulation remains authoritative.
    return min(
        [baseline, *allowed],
        key=lambda f: (
            round(f.grid_import_wh, 6),
            sum(a != b for a, b in zip(source_state(f), previous, strict=True)),
            sum(source_state(f)),
        ),
    )


def source_windows(steps: Sequence[SourceStep]) -> tuple[range, ...]:
    """A recharge closes its preceding window; the following one starts anew."""
    result = []
    start = 0
    for i, step in enumerate(steps):
        if pv_recharge(step.flow):
            result.append(range(start, i + 1))
            start = i + 1
    if start < len(steps):
        result.append(range(start, len(steps)))
    return tuple(result)


def market_sources(
    config: SystemConfig,
    steps: Sequence[SourceStep],
    weights: Sequence[float],
) -> tuple[SourceState, ...]:
    """One sorted allocation per recharge window; full physics validates later.

    Minimum-draw alternatives supply the same finite battery budget as the
    reference. Choices then consume that budget in weighted avoided-import
    order. Complete five-minute choices are indivisible; no energy is borrowed
    from a subsequent PV recharge, nor is incidental PSU charging a budget.
    """
    selected = [source_state(step.flow) for step in steps]
    start = 0
    for end in range(len(steps) + 1):
        recharge = end < len(steps) and pv_recharge(steps[end].flow)
        if end < len(steps) and not recharge:
            continue
        check_cancelled()
        indices = range(start, end)
        if any(weights[i] > 1 for i in indices):
            _allocate_window(config, steps, weights, indices, selected)
        start = end + 1
    return tuple(selected)


def _allocate_window(
    config: SystemConfig,
    steps: Sequence[SourceStep],
    weights: Sequence[float],
    indices: range,
    selected: list[SourceState],
) -> None:
    candidates: list[tuple[float, int, int, int]] = []
    alternatives: dict[int, tuple[HourFlows, ...]] = {}
    revisions: dict[int, int] = {}
    current: dict[int, HourFlows] = {}
    budget = sum(net_draw(steps[i].flow) for i in indices)
    for i in indices:
        step = steps[i]
        # Freeze AC opportunities; DC cannot buy a new AC budget, and an
        # already admitted AC step cannot be financed by a source swap.
        choices = (
            tuple(
                f
                for f in step.choices
                if f.psu48_battery_charge_wh
                <= step.flow.psu48_battery_charge_wh + ENERGY_EPSILON_WH
            )
            if not step.flow.inverter_limit_w
            else (step.flow,)
        )
        choices = tuple(
            f
            for f in choices
            if f.unserved_dc_wh <= step.flow.unserved_dc_wh + ENERGY_EPSILON_WH
        )
        choices = choices or (step.flow,)
        # Calculate each physical cost once. The finite Pareto comparison must
        # not repeatedly reconstruct the same battery/grid attribution.
        costs = [(flow, net_draw(flow), grid_dc(config, flow)) for flow in choices]
        frontier = [
            (flow, draw, grid)
            for flow, draw, grid in costs
            if not any(
                other_draw <= draw + ENERGY_EPSILON_WH
                and other_grid <= grid + ENERGY_EPSILON_WH
                and (
                    other_draw < draw - ENERGY_EPSILON_WH
                    or other_grid < grid - ENERGY_EPSILON_WH
                )
                for _, other_draw, other_grid in costs
            )
        ]
        base, base_draw, base_grid = min(
            frontier,
            key=lambda cost: (
                cost[1],
                cost[2],
                source_state(cost[0]) != source_state(step.flow),
            ),
        )
        current[i] = base
        alternatives[i] = tuple(flow for flow, _, _ in frontier)
        revisions[i] = 0
        budget -= base_draw
        for choice, (_, draw, grid) in enumerate(frontier):
            extra = draw - base_draw
            saving = base_grid - grid
            if extra > ENERGY_EPSILON_WH and saving > ENERGY_EPSILON_WH:
                heappush(candidates, (-saving / extra * weights[i], -i, choice, 0))
    if budget < -ENERGY_EPSILON_WH:
        return
    # Reprice only the changed step. A bounded heap keeps marginal allocation
    # deterministic without quadratic rescans of the full forecast horizon.
    while candidates:
        _, negative_i, choice, revision = heappop(candidates)
        i = -negative_i
        if revision != revisions[i]:
            continue
        flow = alternatives[i][choice]
        extra = net_draw(flow) - net_draw(current[i])
        saving = grid_dc(config, current[i]) - grid_dc(config, flow)
        if extra > budget + ENERGY_EPSILON_WH:
            continue
        budget -= extra
        current[i] = flow
        revisions[i] += 1
        for next_choice, other in enumerate(alternatives[i]):
            draw = net_draw(other) - net_draw(flow)
            gain = grid_dc(config, flow) - grid_dc(config, other)
            if draw > ENERGY_EPSILON_WH and gain > ENERGY_EPSILON_WH:
                heappush(
                    candidates,
                    (-gain / draw * weights[i], -i, next_choice, revisions[i]),
                )
    for i, flow in current.items():
        selected[i] = source_state(flow)


def valid_shift(
    config: SystemConfig,
    reference: Sequence[SourceStep],
    trial: Sequence[SourceStep],
    weights: Sequence[float],
) -> bool:
    """Reject regression in any recharge window, even if a later one improves."""
    start = 0
    changed = False
    for end in range(len(reference)):
        before, after = reference[end], trial[end]
        if (
            after.flow.psu48_battery_charge_wh
            > before.flow.psu48_battery_charge_wh + ENERGY_EPSILON_WH
            or after.flow.unserved_dc_wh
            > before.flow.unserved_dc_wh + ENERGY_EPSILON_WH
            or abs(after.flow.inverter_output_wh - before.flow.inverter_output_wh)
            > ENERGY_EPSILON_WH
            or (
                net_draw(after.flow) > net_draw(before.flow) + ENERGY_EPSILON_WH
                and after.flow.soc_end_percent < before.minimum_soc - ENERGY_EPSILON_WH
            )
        ):
            return False
        recharge = pv_recharge(before.flow)
        if not recharge and end < len(reference) - 1:
            continue
        old = reference[start : end + 1]
        new = trial[start : end + 1]
        if (
            sum(net_draw(s.flow) for s in new)
            > sum(net_draw(s.flow) for s in old) + ENERGY_EPSILON_WH
            or after.flow.soc_end_percent
            < before.flow.soc_end_percent - ENERGY_EPSILON_WH
            or sum(s.flow.grid_import_wh for s in new)
            > sum(s.flow.grid_import_wh for s in old) + ENERGY_EPSILON_WH
            or sum(s.flow.grid_export_wh for s in new)
            > sum(s.flow.grid_export_wh for s in old) + ENERGY_EPSILON_WH
        ):
            return False
        gain = sum(
            (grid_dc(config, a.flow) - grid_dc(config, b.flow)) * weights[i]
            for i, (a, b) in enumerate(zip(old, new, strict=True), start)
        )
        if gain < -ENERGY_EPSILON_WH:
            return False
        changed |= gain > ENERGY_EPSILON_WH
        start = end + 1
    return changed
