"""Bounded scalar matching for exchanges needing more than one receiver.

Descriptors carry state masks and local accounting, never simulated trajectories.
Rounded signatures only find neighbours; exact accounting and sequential proof
remain the caller's responsibility.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations, product

from .model import HourFlows
from .planning_control import check_cancelled
from .reserve_energy import ENERGY_EPSILON_WH
from .reserve_sources import SourceStep, net_draw, source_state, source_windows

# The 2026-10-19 replay has no single-receiver match but does have a safe pair.
# Bound scalar enumeration independently from the 32 complete physical passes.
MAX_BALANCE_EXPANSIONS = 65536
MAX_BALANCE_SIGNATURES = 4096
MAX_RECEIVER_DESCRIPTORS = 256
MAX_BALANCED_PROPOSALS = 3


@dataclass(frozen=True)
class _Move:
    mask: int
    draw: float
    imports: float
    price: float
    benefit: tuple[float, ...] = ()


@dataclass(frozen=True)
class _Run:
    position: int
    mask: int
    state: bool
    duration: float
    draw: float
    imports: float


def _legal_mask(steps: Sequence[SourceStep], mask: int, source: int) -> bool:
    """Conservative phase prefilter; final physical proof also handles protection."""
    original = tuple(source_state(s.flow)[source] for s in steps)
    states = tuple(value != bool(mask & (1 << i)) for i, value in enumerate(original))
    start = 0
    while start < len(steps):
        state = states[start]
        end = start + 1
        while end < len(steps) and states[end] == state:
            end += 1
        changed = (
            any(original[i] != state for i in range(start, end))
            or (start > 0 and original[start - 1] == state)
            or (end < len(steps) and original[end] == state)
        )
        if (
            changed
            and end < len(steps)
            and sum(s.slot.duration for s in steps[start:end]) < 0.5 - 1e-9
        ):
            return False
        start = end
    return True


def balanced_proposals(
    steps: Sequence[SourceStep],
    choices: Sequence[Sequence[HourFlows]],
    prices: Sequence[float | None],
    price_weights: Sequence[Sequence[tuple[float, float]]] | None = None,
) -> tuple[tuple[tuple[HourFlows, ...], ...], bool]:
    """Match one/two receiver blocks against legal cheaper donor run masks."""
    weights = (
        price_weights
        if price_weights is not None
        else tuple(((price, 1.0),) if price is not None else () for price in prices)
    )
    levels = sorted({price for parts in weights for price, _ in parts}, reverse=True)
    original_masks = tuple(
        sum((1 << i) for i, step in enumerate(steps) if source_state(step.flow)[source])
        for source in (0, 1)
    )
    transition_mask = (1 << len(steps)) - 2 if steps else 0

    def edges(mask: int, source: int) -> int:
        chosen = original_masks[source] ^ mask
        return ((chosen ^ (chosen << 1)) & transition_mask).bit_count() + bool(mask & 1)

    def economic(values: tuple[float, ...]) -> tuple[float, ...]:
        # Match dispatch.rank precision; floating summation noise must not hide
        # a real lower-price improvement behind an equal higher-price bucket.
        return tuple(round(value, 6) for value in values)

    work = 0
    limited = False
    selected: list[tuple[tuple[float, ...], int, int]] = []
    seen: set[tuple[int, int]] = set()

    def finish() -> tuple[tuple[tuple[HourFlows, ...], ...], bool]:
        proposals = []
        for _, source, mask in selected:
            proposed = []
            for i, step in enumerate(steps):
                state = source_state(step.flow)
                desired = tuple(
                    not value if n == source else value for n, value in enumerate(state)
                )
                proposed.append(
                    next(
                        f
                        for f in choices[i]
                        if source_state(f) == desired and not f.inverter_limit_w
                    )
                    if mask & (1 << i)
                    else step.flow
                )
            proposals.append(tuple(proposed))
        return tuple(proposals), limited

    def spend() -> bool:
        nonlocal work, limited
        check_cancelled()
        work += 1
        if work > MAX_BALANCE_EXPANSIONS:
            limited = True
            return False
        return True

    def contiguous(left: int, right: int) -> bool:
        return (
            abs(
                steps[right].slot.start.timestamp()
                - steps[left].slot.start.timestamp()
                - steps[left].slot.duration * 3600
            )
            < 1e-6
        )

    windows = sorted(
        source_windows(steps),
        key=lambda window: (
            -max(
                (
                    price
                    for i in window
                    for price, _ in weights[i]
                    if any(source_state(steps[i].flow))
                    and any(
                        not f.inverter_limit_w
                        and f.grid_import_wh < steps[i].flow.grid_import_wh
                        for f in choices[i]
                    )
                ),
                default=float("-inf"),
            ),
            steps[window.start].slot.start.timestamp(),
        ),
    )
    for window in windows:
        for source in (0, 1):
            alternatives: dict[int, HourFlows] = {}
            for i in window:
                old = steps[i].flow
                state = source_state(old)
                desired = tuple(
                    not value if n == source else value for n, value in enumerate(state)
                )
                if prices[i] is not None and not old.inverter_limit_w:
                    option = next(
                        (
                            f
                            for f in choices[i]
                            if source_state(f) == desired and not f.inverter_limit_w
                        ),
                        None,
                    )
                    if option is not None:
                        alternatives[i] = option
            donor_indices = tuple(
                i
                for i, flow in alternatives.items()
                if not source_state(steps[i].flow)[source]
                and net_draw(steps[i].flow) > net_draw(flow) + ENERGY_EPSILON_WH
            )
            # A high-price later window with only unavoidable DC draw has no
            # donor budget. It must not consume construction work ahead of a
            # physically exchangeable earlier window.
            if not donor_indices:
                continue
            receivers: list[_Move] = []
            family_for: dict[int, int] = {}
            family = 0
            for i in window:
                if i > window.start and (
                    prices[i] != prices[i - 1]
                    or source_state(steps[i].flow)[source]
                    != source_state(steps[i - 1].flow)[source]
                    or not contiguous(i - 1, i)
                ):
                    family += 1
                family_for[i] = family
            for start in window:
                if (
                    start not in alternatives
                    or not source_state(steps[start].flow)[source]
                ):
                    continue
                mask = 0
                draw = imports = duration = 0.0
                for end in range(start, min(window.stop, start + 12)):
                    if not spend():
                        return finish()
                    if (
                        end not in alternatives
                        or not source_state(steps[end].flow)[source]
                        or prices[end] != prices[start]
                        or (end > start and not contiguous(end - 1, end))
                    ):
                        break
                    mask |= 1 << end
                    draw += net_draw(alternatives[end]) - net_draw(steps[end].flow)
                    imports += (
                        steps[end].flow.grid_import_wh
                        - alternatives[end].grid_import_wh
                    )
                    duration += steps[end].slot.duration
                    if (
                        duration >= 0.5 - 1e-9
                        and draw > ENERGY_EPSILON_WH
                        and imports > ENERGY_EPSILON_WH
                    ):
                        price = prices[start]
                        assert price is not None
                        if _legal_mask(steps, mask, source):
                            benefit = tuple(
                                sum(
                                    (
                                        steps[i].flow.grid_import_wh
                                        - alternatives[i].grid_import_wh
                                    )
                                    * sum(
                                        share
                                        for value, share in weights[i]
                                        if value == level
                                    )
                                    for i in range(start, end + 1)
                                )
                                for level in levels
                            )
                            receivers.append(_Move(mask, draw, imports, price, benefit))
            receivers.sort(
                key=lambda m: (
                    economic(m.benefit),
                    -edges(m.mask, source),
                    -m.mask.bit_count(),
                    -m.mask,
                ),
                reverse=True,
            )
            if not receivers:
                continue
            # At most two contiguous receiver blocks. Searching masks, rather
            # than preserving one greedily overcompensated schedule, lets two
            # individually unmatched quantities share the same donor budget.
            if len(receivers) > MAX_RECEIVER_DESCRIPTORS:
                limited = True
            families: dict[int, list[_Move]] = {}
            for move in receivers:
                first = (move.mask & -move.mask).bit_length() - 1
                families.setdefault(family_for[first], []).append(move)
            ordered_families = sorted(
                families.values(),
                key=lambda group: economic(group[0].benefit),
                reverse=True,
            )
            receivers = [
                group[n]
                for n in range(max(len(group) for group in ordered_families))
                for group in ordered_families
                if n < len(group)
            ][:MAX_RECEIVER_DESCRIPTORS]
            requests = [*receivers]
            for a, b in combinations(receivers, 2):
                if not spend():
                    break
                if not a.mask & b.mask:
                    requests.append(
                        _Move(
                            a.mask | b.mask,
                            a.draw + b.draw,
                            a.imports + b.imports,
                            min(a.price, b.price),
                            tuple(
                                x + y for x, y in zip(a.benefit, b.benefit, strict=True)
                            ),
                        )
                    )
            requests.sort(
                key=lambda m: (
                    economic(m.benefit),
                    -edges(m.mask, source),
                    -m.mask.bit_count(),
                    -m.mask,
                ),
                reverse=True,
            )
            cache: dict[float, dict[tuple[float, float], list[_Move]]] = {}
            for receiver in requests:
                if not spend():
                    break
                eligible = tuple(
                    i
                    for i in donor_indices
                    if (price := prices[i]) is not None and price < receiver.price
                )
                if not eligible:
                    continue
                minimum_loss = min(
                    (alternatives[i].grid_import_wh - steps[i].flow.grid_import_wh)
                    / (net_draw(steps[i].flow) - net_draw(alternatives[i]))
                    for i in eligible
                )
                if receiver.imports + ENERGY_EPSILON_WH < receiver.draw * minimum_loss:
                    continue
                if receiver.price not in cache:
                    groups: list[list[int]] = []
                    for i in window:
                        price = prices[i]
                        if (
                            i in alternatives
                            and price is not None
                            and price < receiver.price
                            and not source_state(steps[i].flow)[source]
                            and net_draw(steps[i].flow)
                            > net_draw(alternatives[i]) + ENERGY_EPSILON_WH
                        ):
                            if (
                                groups
                                and groups[-1][-1] + 1 == i
                                and contiguous(i - 1, i)
                            ):
                                groups[-1].append(i)
                            else:
                                groups.append([i])
                    options: list[list[_Move]] = []
                    for group in groups:
                        runs: list[_Move] = []

                        # The fixed neighbouring reference runs count towards
                        # dwell. This admits a 20m suffix joining an existing
                        # ON phase while excluding a new isolated 20m phase.
                        previous = group[0] - 1
                        prior_state = (
                            source_state(steps[previous].flow)[source]
                            if previous >= 0
                            else False
                        )
                        prior_duration = 0.0
                        while (
                            previous >= 0
                            and source_state(steps[previous].flow)[source]
                            == prior_state
                        ):
                            prior_duration += steps[previous].slot.duration
                            previous -= 1
                        # Initial phase has already elapsed before our horizon;
                        # the final full proof checks actual confirmed locks.
                        if group[0] == 0:
                            prior_duration = 0.5
                        stack = [_Run(0, 0, prior_state, prior_duration, 0, 0)]
                        while stack:
                            if not spend():
                                break
                            if len(runs) >= MAX_BALANCE_SIGNATURES:
                                limited = True
                                break
                            node = stack.pop()
                            if node.position == len(group):
                                tail = group[-1] + 1
                                total = node.duration
                                while (
                                    tail < len(steps)
                                    and source_state(steps[tail].flow)[source]
                                    == node.state
                                ):
                                    total += steps[tail].slot.duration
                                    tail += 1
                                if total >= 0.5 - 1e-9 or tail == len(steps):
                                    runs.append(
                                        _Move(
                                            node.mask,
                                            node.draw,
                                            node.imports,
                                            receiver.price,
                                        )
                                    )
                                continue
                            i = group[node.position]
                            for active in (True, False):
                                if active != node.state and node.duration < 0.5 - 1e-9:
                                    continue
                                stack.append(
                                    _Run(
                                        node.position + 1,
                                        node.mask | ((1 << i) if active else 0),
                                        active,
                                        (node.duration if active == node.state else 0)
                                        + steps[i].slot.duration,
                                        node.draw
                                        + (
                                            net_draw(steps[i].flow)
                                            - net_draw(alternatives[i])
                                            if active
                                            else 0
                                        ),
                                        node.imports
                                        + (
                                            alternatives[i].grid_import_wh
                                            - steps[i].flow.grid_import_wh
                                            if active
                                            else 0
                                        ),
                                    )
                                )

                        options.append(runs)
                    signatures: dict[tuple[float, float], list[_Move]] = {}
                    for donor_parts in product(*options):
                        if not spend():
                            break
                        draw = sum(m.draw for m in donor_parts)
                        imports = sum(m.imports for m in donor_parts)
                        mask = 0
                        for move in donor_parts:
                            mask |= move.mask
                        key = (round(draw, 6), round(imports, 6))
                        bucket = signatures.setdefault(key, [])
                        cost = tuple(
                            sum(
                                (
                                    alternatives[i].grid_import_wh
                                    - steps[i].flow.grid_import_wh
                                )
                                * sum(
                                    share
                                    for value, share in weights[i]
                                    if value == level
                                )
                                for i in window
                                if mask & (1 << i)
                            )
                            for level in levels
                        )
                        bucket.append(_Move(mask, draw, imports, receiver.price, cost))
                        bucket.sort(
                            key=lambda m: (m.benefit, m.mask.bit_count(), m.mask)
                        )
                        del bucket[MAX_BALANCED_PROPOSALS:]
                        if len(signatures) >= MAX_BALANCE_SIGNATURES:
                            limited = True
                            break
                    cache[receiver.price] = signatures
                for donor in cache[receiver.price].get(
                    (round(receiver.draw, 6), round(receiver.imports, 6)), []
                ):
                    if (
                        abs(donor.draw - receiver.draw) > ENERGY_EPSILON_WH
                        or donor.imports > receiver.imports + ENERGY_EPSILON_WH
                        or donor.mask & receiver.mask
                        or (source, donor.mask | receiver.mask) in seen
                        or not _legal_mask(steps, donor.mask | receiver.mask, source)
                    ):
                        continue
                    seen.add((source, donor.mask | receiver.mask))
                    benefit = economic(
                        tuple(
                            a - b
                            for a, b in zip(
                                receiver.benefit, donor.benefit, strict=True
                            )
                        )
                    )
                    selected.append((benefit, source, donor.mask | receiver.mask))
                    selected.sort(
                        key=lambda item: (
                            item[0],
                            -edges(item[2], item[1]),
                            -item[2].bit_count(),
                            -item[1],
                            -item[2],
                        ),
                        reverse=True,
                    )
                    del selected[MAX_BALANCED_PROPOSALS:]

            if work > MAX_BALANCE_EXPANSIONS:
                return finish()
    return finish()
