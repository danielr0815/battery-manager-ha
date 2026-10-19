"""F-DC-PV-MARKET: finite source choices and budget-neutral DC placement."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC
from heapq import heappop, heappush

from .model import HourFlows, HourSlot, SystemConfig
from .planning_control import check_cancelled
from .reserve_energy import ENERGY_EPSILON_WH

SourceState = tuple[bool, bool]

# Operator 2026-10-13: bundle PSU switching, especially through price peaks.
# A preference, not a safety dwell: smaller budgets and compulsory sources
# can shorten a block. Bound lookahead by elapsed time, not forecast row count.
SOURCE_BLOCK_HOURS = 0.5


def phase_locks(config: SystemConfig, slot: HourSlot) -> SourceState:
    """Economic phases use elapsed time; naive legacy recordings use UTC."""
    if not (config.support.dc24_phase_until or config.support.dc48_phase_until):
        return False, False
    start = slot.start
    at = (start if start.tzinfo else start.replace(tzinfo=UTC)).timestamp()
    return (
        at < config.support.dc24_phase_until,
        at < config.support.dc48_phase_until,
    )


@dataclass(frozen=True)
class SourceStep:
    slot: HourSlot
    flow: HourFlows
    choices: tuple[HourFlows, ...]
    minimum_soc: float
    choice_factory: Callable[[], tuple[HourFlows, ...]] | None = None
    joint_choice_factory: Callable[[], tuple[HourFlows, ...]] | None = None

    def market_choices(self) -> tuple[HourFlows, ...]:
        """AC-ON steps and unweighted windows need no alternative simulation."""
        return (
            self.choice_factory() if self.choice_factory is not None else self.choices
        )


def net_draw(flow: HourFlows) -> float:
    """Stored energy actually lost; PV circulation is not an extra budget."""
    return max(0.0, flow.battery_discharge_wh - flow.battery_charge_wh)


def _allocation_draw(reference: HourFlows, choice: HourFlows) -> float:
    """Debit forgone incidental charging as well as actual battery draw."""
    # October 12: spending the whole DC budget while removing incidental PSU
    # charging lowered the endpoint SOC and discarded the entire peak shift.
    # Charge is already stored Wh (including efficiency). Retain its lost
    # contribution within the same budget; unchanged charging costs nothing.
    return net_draw(choice) + max(
        0.0, reference.psu48_battery_charge_wh - choice.psu48_battery_charge_wh
    )


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
    reference. Choices consume that budget by price band, then marginal
    avoided import, preferring contiguous blocks. Complete steps are indivisible;
    no energy is borrowed from a subsequent PV recharge, nor is incidental
    PSU charging a budget.
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
    candidates: list[tuple[float, float, int, int, int]] = []
    alternatives: dict[int, tuple[HourFlows, ...]] = {}
    revisions: dict[int, int] = {}
    current: dict[int, HourFlows] = {}
    budget = sum(net_draw(steps[i].flow) for i in indices)
    for i in indices:
        step = steps[i]
        locked_sources = phase_locks(config, step.slot)
        # Freeze AC opportunities; DC cannot buy a new AC budget, and an
        # already admitted AC step cannot be financed by a source swap.
        choices = (
            tuple(
                f
                for f in step.market_choices()
                if f.psu48_battery_charge_wh
                <= step.flow.psu48_battery_charge_wh + ENERGY_EPSILON_WH
            )
            if not step.flow.inverter_limit_w
            else (step.flow,)
        )
        # A confirmed OFF can reserve only existing reference battery Wh. If
        # compulsory choices or insufficient budget prevent it, keep reference
        # supply rather than inventing a larger discharge budget for stability.
        choices = tuple(
            f
            for f in choices
            if f.unserved_dc_wh <= step.flow.unserved_dc_wh + ENERGY_EPSILON_WH
            and (
                not any(locked_sources)
                or all(
                    not locked or selected == (reference and active)
                    for locked, selected, reference, active in zip(
                        locked_sources,
                        source_state(f),
                        source_state(step.flow),
                        (config.support.dc24_active, config.support.dc48_active),
                        strict=True,
                    )
                )
            )
        )
        choices = choices or (step.flow,)
        # Calculate each physical cost once. The finite Pareto comparison must
        # not repeatedly reconstruct the same battery/grid attribution.
        costs = [
            (flow, _allocation_draw(step.flow, flow), grid_dc(config, flow))
            for flow in choices
        ]
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
                heappush(candidates, (-weights[i], -saving / extra, -i, choice, 0))
    if budget < -ENERGY_EPSILON_WH:
        return
    # Reprice only the changed step. A bounded heap keeps marginal allocation
    # deterministic without quadratic rescans of the full forecast horizon.
    while candidates:
        _, _, negative_i, choice, revision = heappop(candidates)
        i = -negative_i
        if revision != revisions[i]:
            continue
        flow = alternatives[i][choice]
        extra = _allocation_draw(steps[i].flow, flow) - _allocation_draw(
            steps[i].flow, current[i]
        )
        if extra > budget + ENERGY_EPSILON_WH:
            continue
        block = _source_block(
            config, steps, weights, indices, alternatives, current, i, flow, budget
        )
        for index, chosen in block:
            budget -= _allocation_draw(steps[index].flow, chosen) - _allocation_draw(
                steps[index].flow, current[index]
            )
            current[index] = chosen
            revisions[index] += 1
            for next_choice, other in enumerate(alternatives[index]):
                draw = _allocation_draw(steps[index].flow, other) - _allocation_draw(
                    steps[index].flow, chosen
                )
                gain = grid_dc(config, chosen) - grid_dc(config, other)
                if draw > ENERGY_EPSILON_WH and gain > ENERGY_EPSILON_WH:
                    heappush(
                        candidates,
                        (
                            -weights[index],
                            -gain / draw,
                            -index,
                            next_choice,
                            revisions[index],
                        ),
                    )
    for i, flow in current.items():
        selected[i] = source_state(flow)


def _source_block(
    config: SystemConfig,
    steps: Sequence[SourceStep],
    weights: Sequence[float],
    indices: range,
    alternatives: dict[int, tuple[HourFlows, ...]],
    current: dict[int, HourFlows],
    index: int,
    chosen: HourFlows,
    budget: float,
) -> list[tuple[int, HourFlows]]:
    """Extend a choice within one price band, never borrowing battery energy."""
    block = [(index, chosen)]
    budget -= _allocation_draw(steps[index].flow, chosen) - _allocation_draw(
        steps[index].flow, current[index]
    )
    duration = steps[index].slot.duration
    # F-DC-SOURCE-BLOCKS R2: prefer contiguous OFF opportunities over tiny
    # efficiency differences. Higher price bands are always allocated first.
    # The final full simulation still rejects unsafe or uneconomic shifts.
    for direction in (-1, 1):
        neighbor = index + direction
        while neighbor in indices:
            left, right = sorted((neighbor, neighbor - direction))
            before, after = steps[left].slot, steps[right].slot
            # Legacy naive recordings keep their wall clock; aware prices
            # cross DST in elapsed UTC time. Missing forecast time is a gap.
            start = before.start
            end = after.start
            if start.tzinfo is not None:
                start, end = start.astimezone(UTC), end.astimezone(UTC)
            if (
                weights[neighbor] != weights[index]
                or duration + steps[neighbor].slot.duration > SOURCE_BLOCK_HOURS + 1e-9
                or abs((end - start).total_seconds() - before.duration * 3600) > 1e-6
            ):
                break
            match = next(
                (
                    flow
                    for flow in alternatives[neighbor]
                    if source_state(flow) == source_state(chosen)
                ),
                None,
            )
            if match is None:
                break
            extra = _allocation_draw(steps[neighbor].flow, match) - _allocation_draw(
                steps[neighbor].flow, current[neighbor]
            )
            saving = grid_dc(config, current[neighbor]) - grid_dc(config, match)
            if (
                extra <= ENERGY_EPSILON_WH
                or extra > budget + ENERGY_EPSILON_WH
                or saving <= ENERGY_EPSILON_WH
            ):
                break
            block.append((neighbor, match))
            budget -= extra
            duration += steps[neighbor].slot.duration
            neighbor += direction
    return block


def valid_shift(
    config: SystemConfig,
    reference: Sequence[SourceStep],
    trial: Sequence[SourceStep],
    weights: Sequence[float],
    *,
    allow_phase_tradeoff: bool = False,
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
        phase_window = allow_phase_tradeoff and any(
            any(phase_locks(config, step.slot)) for step in old
        )
        if gain < -ENERGY_EPSILON_WH and not phase_window:
            return False
        changed |= gain > ENERGY_EPSILON_WH or phase_window
        start = end + 1

    # F-DC-SOURCE-STABILITY: a physically/cost-equivalent candidate is an
    # improvement when it removes actual source edges. Do not reject it merely
    # because the previous acceptance gate counted price gain alone.
    def edges(sequence: Sequence[SourceStep]) -> int:
        previous = config.support.dc24_active, config.support.dc48_active
        count = 0
        for step in sequence:
            state = source_state(step.flow)
            count += sum(a != b for a, b in zip(previous, state, strict=True))
            previous = state
        return count

    return changed or edges(trial) < edges(reference)
