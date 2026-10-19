"""F-JOINT-MARKET: exchange complete AC/DC blocks within booked energy.

Local flow estimates only construct proposals. Every published proposal receives
a sequential physical proof against the original, fixed PV recharge windows.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta

from .dc_service import preserves_dc_service
from .model import (
    EconomicInterval,
    HourFlows,
    JointDispatchSummary,
    MarketPrice,
    SystemConfig,
    Trajectory,
)
from .planning_control import check_cancelled
from .reserve_energy import ENERGY_EPSILON_WH
from .reserve_sources import (
    SourceStep,
    net_draw,
    phase_locks,
    pv_recharge,
    source_state,
    source_windows,
)

# Operator 2026-10-19: bound expensive full physics for the complete booked plan,
# rather than spending a fresh search allowance on each price or load probe.
MAX_JOINT_VALIDATIONS = 32
MAX_RETAINED_SCHEDULES = 4
JOINT_BLOCK_HOURS = 0.5


@dataclass
class ValidationBudget:
    used: int = 0

    def record(self) -> None:
        assert self.used < MAX_JOINT_VALIDATIONS
        self.used += 1


@dataclass(frozen=True)
class _PriceSlice:
    start: datetime
    end: datetime
    price: float
    share: float


def price_slices(
    steps: Sequence[SourceStep], prices: tuple[MarketPrice, ...]
) -> tuple[tuple[_PriceSlice, ...], ...]:
    """Keep original prices and elapsed overlap; partial coverage stays unknown."""
    result = []
    for step in steps:
        slot = step.slot
        parts: list[_PriceSlice] = []
        covered = 0.0
        if slot.start.tzinfo is not None:
            start = slot.start.astimezone(UTC)
            end = start + timedelta(hours=slot.duration)
            for price in prices:
                left = max(start, price.start.astimezone(UTC))
                right = min(end, price.end.astimezone(UTC))
                while left < right:
                    local = left.astimezone(slot.start.tzinfo)
                    midnight = datetime.combine(
                        local.date() + timedelta(days=1), time.min, slot.start.tzinfo
                    ).astimezone(UTC)
                    boundary = min(right, midnight)
                    seconds = (boundary - left).total_seconds()
                    parts.append(
                        _PriceSlice(
                            local,
                            boundary.astimezone(slot.start.tzinfo),
                            price.eur_per_mwh,
                            seconds / (slot.duration * 3600),
                        )
                    )
                    covered += seconds
                    left = boundary
        result.append(tuple(parts) if covered >= slot.duration * 3600 - 1e-6 else ())
    return tuple(result)


def actual_prices(
    steps: Sequence[SourceStep], prices: tuple[MarketPrice, ...]
) -> tuple[float | None, ...]:
    """Highest actual price touched by each indivisible control quantum."""
    return tuple(
        max((p.price for p in parts), default=None)
        for parts in price_slices(steps, prices)
    )


def proof_failure(
    config: SystemConfig,
    reference: Trajectory,
    trial: Trajectory,
    before: Sequence[SourceStep],
    after: Sequence[SourceStep],
) -> str | None:
    """Physical guarantees are independent of the economic search heuristic."""
    if not preserves_dc_service(trial, baseline=reference):
        return "dc_service"
    if trial.total_import_wh > reference.total_import_wh + ENERGY_EPSILON_WH:
        return "extra_import"
    if trial.total_export_wh > reference.total_export_wh + ENERGY_EPSILON_WH:
        return "extra_export"
    for old, new in zip(before, after, strict=True):
        if (
            new.flow.psu48_battery_charge_wh
            > old.flow.psu48_battery_charge_wh + ENERGY_EPSILON_WH
        ):
            return "extra_psu_charge"
        if new.flow.inverter_limit_w and any(source_state(new.flow)):
            return "source_inverter_loop"
        if (
            new.flow.inverter_limit_w
            and new.flow.soc_end_percent
            < config.control.inverter_min_soc_percent
            + config.control.soc_buffer_percent
            - ENERGY_EPSILON_WH
        ):
            return "reserve_floor"
        if any(
            locked and chosen != original
            for locked, chosen, original in zip(
                phase_locks(config, old.slot),
                source_state(new.flow),
                source_state(old.flow),
                strict=True,
            )
        ):
            return "phase"
    for window in source_windows(before):
        old_flows = [before[i].flow for i in window]
        new_flows = [after[i].flow for i in window]
        if (
            sum(net_draw(f) for f in new_flows)
            > sum(net_draw(f) for f in old_flows) + ENERGY_EPSILON_WH
        ):
            return "energy_budget"
        if (
            new_flows[-1].soc_end_percent
            < old_flows[-1].soc_end_percent - ENERGY_EPSILON_WH
        ):
            return "window_end_energy"
    for source in (0, 1):
        start = 0
        while start < len(after):
            state = source_state(after[start].flow)[source]
            end = start + 1
            while end < len(after) and source_state(after[end].flow)[source] == state:
                end += 1
            changed = (
                any(
                    source_state(before[i].flow)[source] != state
                    for i in range(start, end)
                )
                or (start > 0 and source_state(before[start - 1].flow)[source] == state)
                or (
                    end < len(before)
                    and source_state(before[end].flow)[source] == state
                )
            )
            duration = sum(after[i].slot.duration for i in range(start, end))
            pv_exception = any(
                pv_recharge(after[i].flow)
                for i in range(start, min(end + 1, len(after)))
            )
            protection = (
                end < len(after)
                and source_state(after[end].flow)[source]
                and source_state(before[end].flow)[source]
                and after[end].flow.soc_start_percent
                <= (
                    config.control.support_dc24_recovery_soc
                    if source == 0
                    else config.control.support_dc48_recovery_soc
                )
            )
            if (
                changed
                and duration < JOINT_BLOCK_HOURS - 1e-9
                and end < len(after)
                and not (pv_exception or protection)
            ):
                return "phase"
            start = end
    return None


def uncertainty_failure(
    config: SystemConfig,
    reference: Trajectory,
    trial: Trajectory,
    before: Sequence[SourceStep],
    after: Sequence[SourceStep],
) -> str | None:
    """Keep booked consumers protected at every pessimistic subhour endpoint."""
    if not preserves_dc_service(trial, baseline=reference):
        return "uncertainty_dc_service"
    if trial.total_import_wh > reference.total_import_wh + ENERGY_EPSILON_WH:
        return "uncertainty_import"
    floor = config.battery.soc_min_percent + config.control.soc_buffer_percent
    if any(
        b.flow.soc_end_percent < min(floor, a.flow.soc_end_percent) - ENERGY_EPSILON_WH
        for a, b in zip(before, after, strict=True)
    ):
        return "uncertainty_reserve"
    return None


def dispatch(
    config: SystemConfig,
    reference: Trajectory,
    steps: tuple[SourceStep, ...],
    prices: tuple[MarketPrice, ...],
    evaluate: Callable[
        [tuple[HourFlows, ...]], tuple[Trajectory, tuple[SourceStep, ...]]
    ],
    validate_uncertainty: Callable[[Trajectory, Sequence[SourceStep]], str | None]
    | None = None,
    validation_budget: ValidationBudget | None = None,
    validation_cost: int = 1,
    initial_validation_cost: int = 0,
) -> Trajectory:
    """Try coupled receivers and cheaper compensating donors, fairly by target.

    Each target contributes at most one complete validation per sweep. Failed
    five-minute subdivisions of one expensive hour cannot consume the allowance.
    The reference is always retained; at most three successful alternatives stay
    alongside it, irrespective of horizon length.
    """
    slices = price_slices(steps, prices)
    raw = tuple(max((p.price for p in parts), default=None) for parts in slices)
    known = {p.price for parts in slices for p in parts}
    budget = sum(net_draw(s.flow) for s in steps)
    choices = tuple(
        tuple(s.joint_choice_factory())
        if len(known) > 1 and s.joint_choice_factory
        else (s.flow,)
        for s in steps
    )
    unavoidable = (
        sum(min(net_draw(f) for f in options) for options in choices)
        if len(known) > 1
        else None
    )
    rejection: set[str] = set()
    shared_budget = validation_budget or ValidationBudget()
    best, best_steps = reference, steps
    levels = sorted(known, reverse=True)

    def price_at(index: int) -> float:
        value = raw[index]
        assert value is not None
        return value

    def rank(sequence: Sequence[SourceStep]) -> tuple[float, ...]:
        # Higher actual prices have strict priority, including negative prices.
        return tuple(
            round(
                sum(
                    (a.flow.grid_import_wh - b.flow.grid_import_wh)
                    * sum(part.share for part in slices[i] if part.price == price)
                    for i, (a, b) in enumerate(zip(steps, sequence, strict=True))
                ),
                6,
            )
            for price in levels
        )

    def quality(sequence: Sequence[SourceStep]) -> tuple[tuple[float, ...], int, int]:
        previous = (
            (*source_state(steps[0].flow), bool(steps[0].flow.inverter_limit_w))
            if steps
            else (False, False, False)
        )
        edges = changes = 0
        for before, after in zip(steps, sequence, strict=True):
            state = (*source_state(after.flow), bool(after.flow.inverter_limit_w))
            edges += sum(a != b for a, b in zip(previous, state, strict=True))
            changes += state != (
                *source_state(before.flow),
                bool(before.flow.inverter_limit_w),
            )
            previous = state
        return rank(sequence), -edges, -changes

    def validation_failure(
        trial: Trajectory, trial_steps: Sequence[SourceStep]
    ) -> str | None:
        for i, (old, new) in enumerate(zip(steps, trial_steps, strict=True)):
            if raw[i] is None and (
                source_state(old.flow) != source_state(new.flow)
                or old.flow.inverter_limit_w != new.flow.inverter_limit_w
            ):
                return "unknown_price"
        failure = proof_failure(config, reference, trial, steps, trial_steps)
        if failure is None and validate_uncertainty is not None:
            return validate_uncertainty(trial, trial_steps)
        return failure

    status = (
        "missing_prices"
        if not known
        else "flat_prices"
        if len(known) < 2
        else "no_flex_budget"
        if unavoidable is not None and budget <= unavoidable + ENERGY_EPSILON_WH
        else "no_improvement"
    )
    if status == "no_flex_budget":
        if not (config.support.dc24_available or config.support.dc48_available):
            status = "source_unavailable"
        elif any(any(phase_locks(config, s.slot)) for s in steps):
            status = "phase"
    retained = [(reference, steps)]
    if status == "no_improvement":
        # Price/physical-time boundaries define targets; no daily hour quota.
        targets: list[tuple[int, ...]] = []
        for window in source_windows(steps):
            block: list[int] = []
            duration = 0.0
            for i in window:
                if raw[i] is None:
                    if block:
                        targets.append(tuple(block))
                    block, duration = [], 0.0
                    continue
                if block and (
                    raw[i] != raw[block[0]]
                    or duration + steps[i].slot.duration > JOINT_BLOCK_HOURS + 1e-9
                    or abs(
                        steps[i].slot.start.timestamp()
                        - steps[block[-1]].slot.start.timestamp()
                        - steps[block[-1]].slot.duration * 3600
                    )
                    > 1e-6
                ):
                    targets.append(tuple(block))
                    block, duration = [], 0.0
                block.append(i)
                duration += steps[i].slot.duration
            if block:
                targets.append(tuple(block))
        window_for = {
            i: number
            for number, window in enumerate(source_windows(steps))
            for i in window
        }
        aggregate: list[tuple[int, ...]] = []
        for group in targets:
            if (
                aggregate
                and window_for[aggregate[-1][0]] == window_for[group[0]]
                and raw[aggregate[-1][0]] == raw[group[0]]
                and aggregate[-1][-1] + 1 == group[0]
                and abs(
                    steps[group[0]].slot.start.timestamp()
                    - steps[aggregate[-1][-1]].slot.start.timestamp()
                    - steps[aggregate[-1][-1]].slot.duration * 3600
                )
                < 1e-6
            ):
                aggregate[-1] += group
            else:
                aggregate.append(group)
        targets = list(dict.fromkeys([*aggregate, *targets]))
        targets.sort(
            key=lambda block: (
                -price_at(block[0]),
                -len(block),
                steps[block[0]].slot.start.timestamp(),
            )
        )
        seen: set[tuple[tuple[bool, bool, float], ...]] = set()
        for attempt, original_target in (
            (attempt, target)
            for attempt in range(1 + min(12, max((len(t) for t in targets), default=0)))
            for target in targets
        ):
            target = original_target[:attempt] if attempt else original_target
            check_cancelled()
            seed_steps = best_steps if attempt % 3 != 2 else retained[-1][1]
            current = [s.flow for s in seed_steps]
            proposed = list(current)
            window = next(w for w in source_windows(steps) if target[0] in w)
            for i in target:
                proposed[i] = min(
                    choices[i],
                    key=lambda f: (
                        f.grid_import_wh,
                        net_draw(f),
                        source_state(f) != source_state(current[i]),
                    ),
                )
            if (
                sum(
                    current[i].grid_import_wh - proposed[i].grid_import_wh
                    for i in target
                )
                <= ENERGY_EPSILON_WH
            ):
                continue
            # A receiver and ALL of its compensating donors are one candidate.
            donors = sorted(
                (
                    i
                    for i in window
                    if i not in target
                    and raw[i] is not None
                    and price_at(i)
                    < min(part.price for j in target for part in slices[j])
                ),
                key=lambda i: (price_at(i), steps[i].slot.start.timestamp()),
            )
            donor_indices = set(donors)
            extra = sum(net_draw(proposed[j]) - net_draw(steps[j].flow) for j in window)
            for i in donors:
                if extra <= ENERGY_EPSILON_WH:
                    break
                donor_block: list[int] = []
                duration = 0.0
                for neighbor in range(i, window.stop):
                    if neighbor not in donor_indices or (
                        donor_block
                        and abs(
                            steps[neighbor].slot.start.timestamp()
                            - steps[donor_block[-1]].slot.start.timestamp()
                            - steps[donor_block[-1]].slot.duration * 3600
                        )
                        > 1e-6
                    ):
                        break
                    donor_block.append(neighbor)
                    duration += steps[neighbor].slot.duration
                    if duration >= JOINT_BLOCK_HOURS - 1e-9:
                        break
                block_options: list[
                    tuple[float, float, tuple[int, ...], tuple[HourFlows, ...]]
                ] = []
                for candidate in choices[i]:
                    desired = source_state(candidate)
                    if candidate.inverter_limit_w:
                        continue
                    selected_block = (
                        (i,)
                        if desired == source_state(current[i]) and not any(desired)
                        else tuple(donor_block)
                    )
                    if selected_block != (i,) and duration < JOINT_BLOCK_HOURS - 1e-9:
                        continue
                    option = tuple(
                        next(
                            (
                                f
                                for f in choices[j]
                                if source_state(f) == desired and not f.inverter_limit_w
                            ),
                            current[j],
                        )
                        for j in selected_block
                    )
                    if any(source_state(f) != desired for f in option):
                        continue
                    saving = sum(
                        net_draw(proposed[j]) - net_draw(f)
                        for j, f in zip(selected_block, option, strict=True)
                    )
                    loss = sum(
                        f.grid_import_wh - proposed[j].grid_import_wh
                        for j, f in zip(selected_block, option, strict=True)
                    )
                    if saving > ENERGY_EPSILON_WH:
                        block_options.append(
                            (
                                max(0.0, loss) / saving,
                                abs(extra - saving),
                                selected_block,
                                option,
                            )
                        )
                if not block_options:
                    continue
                chosen = min(block_options, key=lambda item: (item[0], item[1]))
                for j, flow in zip(chosen[2], chosen[3], strict=True):
                    extra -= net_draw(proposed[j]) - net_draw(flow)
                    proposed[j] = flow
            if (
                sum(net_draw(proposed[i]) - net_draw(steps[i].flow) for i in window)
                > ENERGY_EPSILON_WH
            ):
                rejection.add("energy_budget")
                continue
            # Reject known conversion losses before paying for sequential proof.
            if (
                sum(
                    proposed[i].grid_import_wh - steps[i].flow.grid_import_wh
                    for i in range(len(steps))
                )
                > ENERGY_EPSILON_WH
            ):
                rejection.add("extra_import")
                continue
            key = tuple((*source_state(f), f.inverter_limit_w) for f in proposed)
            if key in seen:
                continue
            seen.add(key)
            if (
                shared_budget.used + validation_cost + initial_validation_cost
                > MAX_JOINT_VALIDATIONS
            ):
                status = "search_limit"
                rejection.add("search_limit")
                break
            if validation_budget is None:
                shared_budget.record()
            trial, trial_steps = evaluate(tuple(proposed))
            failure = validation_failure(trial, trial_steps)
            if failure is not None:
                rejection.add(failure)
                continue
            if quality(trial_steps) <= quality(steps):
                rejection.add("no_improvement")
                continue
            if quality(trial_steps) > quality(best_steps):
                best, best_steps = trial, trial_steps
            retained.append((trial, trial_steps))
            retained = [
                retained[0],
                *sorted(retained[1:], key=lambda item: quality(item[1]), reverse=True)[
                    : MAX_RETAINED_SCHEDULES - 1
                ],
            ]
            status = "accepted"
    intervals: list[EconomicInterval] = []
    hours: dict[str, float] = {}
    for i, (old, new) in enumerate(zip(steps, best_steps, strict=True)):
        saving = old.flow.grid_import_wh - new.flow.grid_import_wh
        price = raw[i]
        local_choices = (
            new.joint_choice_factory()
            if price is not None and len(known) > 1 and new.joint_choice_factory
            else choices[i]
        )
        minimum = min(local_choices, key=lambda f: (net_draw(f), f.grid_import_wh))
        allocated = max(0.0, net_draw(new.flow) - net_draw(minimum))
        avoided = max(0.0, minimum.grid_import_wh - new.flow.grid_import_wh)
        if allocated > ENERGY_EPSILON_WH and avoided > ENERGY_EPSILON_WH:
            for part in slices[i]:
                if (
                    intervals
                    and intervals[-1].end.timestamp() == part.start.timestamp()
                    and intervals[-1].eur_per_mwh == part.price
                    and intervals[-1].start.date() == part.start.date()
                ):
                    intervals[-1] = replace(
                        intervals[-1],
                        end=part.end,
                        avoided_import_wh=intervals[-1].avoided_import_wh
                        + saving * part.share,
                        allocated_battery_wh=intervals[-1].allocated_battery_wh
                        + allocated * part.share,
                        avoided_total_grid_import_wh=intervals[
                            -1
                        ].avoided_total_grid_import_wh
                        + avoided * part.share,
                    )
                else:
                    intervals.append(
                        EconomicInterval(
                            part.start,
                            part.end,
                            part.price,
                            saving * part.share,
                            allocated * part.share,
                            avoided * part.share,
                        )
                    )
                day = part.start.date().isoformat()
                hours[day] = (
                    hours.get(day, 0.0)
                    + (part.end.timestamp() - part.start.timestamp()) / 3600
                )
    if status == "no_improvement" and rejection:
        status = (
            "extra_import"
            if "extra_import" in rejection
            else "phase"
            if "phase" in rejection
            else "no_improvement"
        )
    summary = JointDispatchSummary(
        status,
        budget,
        unavoidable,
        shared_budget.used,
        tuple(sorted(rejection)),
        tuple(
            sorted(
                intervals,
                key=lambda interval: (
                    -interval.eur_per_mwh,
                    interval.start.timestamp(),
                ),
            )
        ),
        tuple(sorted(hours.items())),
        reference.total_import_wh,
        best.total_import_wh,
        reference.total_export_wh,
        best.total_export_wh,
        reference.end_soc_percent,
        best.end_soc_percent,
        budget,
        sum(net_draw(s.flow) for s in best_steps),
        sum(
            max(0.0, net_draw(b.flow) - net_draw(a.flow))
            for a, b in zip(steps, best_steps, strict=True)
        ),
    )
    decision = best.reserve_decision
    assert decision is not None
    if len(known) > 1 and best_steps:
        first = best_steps[0].flow
        local = best_steps[0].joint_choice_factory
        off = (
            next(
                (
                    f
                    for f in local()
                    if not f.inverter_limit_w and not any(source_state(f))
                ),
                None,
            )
            if local
            else None
        )
        optional = (
            max(0.0, net_draw(first) - net_draw(off))
            if off is not None and first.inverter_limit_w
            else 0.0
        )
        start_energy = (
            config.battery.energy_wh(first.soc_end_percent)
            + first.battery_discharge_wh
            - first.battery_charge_wh
        )
        floor = config.battery.soc_percent(max(0.0, start_energy - optional))
        # The live manager adds this same uncertainty margin. Its permission is
        # the accepted current step only; moving future AC would be a new joint
        # exchange and must receive the same physical proof on the next plan.
        decision = replace(
            decision,
            inverter_limit_w=first.inverter_limit_w,
            headroom_wh=optional,
            live_ac_floor_percent=max(
                0.0,
                floor
                - (
                    max(
                        config.control.soc_buffer_percent,
                        config.control.hysteresis_percent,
                    )
                    if optional > ENERGY_EPSILON_WH
                    else 0.0
                ),
            ),
            live_ac_override_demand_w=None,
            live_ac_override_floor_percent=100.0,
        )
    return replace(best, reserve_decision=replace(decision, joint=summary))
