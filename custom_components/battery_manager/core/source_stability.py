"""F-DC-SOURCE-STABILITY: fewer edges without moving energy between prices."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from functools import lru_cache
from itertools import permutations

from .model import HourSlot, MarketPrice, SystemConfig
from .planning_control import check_cancelled
from .reserve_sources import SourceState, SourceStep, phase_locks

# Keep each search at most one hourly five-minute grid, even for legacy slots
# longer than an hour. Four combinations then have at most 24 block orders.
MAX_SMOOTHING_STEPS = 12


def switches(previous: SourceState, current: SourceState) -> int:
    return sum(a != b for a, b in zip(previous, current, strict=True))


@dataclass(frozen=True)
class _Path:
    edges: int
    changes: int
    code: int
    states: tuple[SourceState, ...]
    previous: _Path | None = None

    def rank(self) -> tuple[int, int, int]:
        return self.edges, self.changes, self.code


def _state_code(states: tuple[SourceState, ...]) -> int:
    code = 0
    for dc24, dc48 in states:
        code = (code << 2) | (int(dc24) << 1) | int(dc48)
    return code


@lru_cache(maxsize=32)
def _orders(states: tuple[SourceState, ...]) -> tuple[tuple[SourceState, ...], ...]:
    """At most four source combinations, with deterministic bounded rotations."""
    counts = Counter(states)
    candidates = {states}
    for order in permutations(sorted(counts)):
        block = tuple(state for state in order for _ in range(counts[state]))
        for offset in range(len(block)):
            candidates.add(block[offset:] + block[:offset])
    return tuple(sorted(candidates))


def _price_key(slot: HourSlot, intervals: tuple[tuple[float, float], ...]) -> object:
    """Equal capped weights need not mean equal prices or known coverage."""
    at = slot.start
    if at.tzinfo is None:
        return None
    start = at.timestamp()
    end = start + slot.duration * 3600
    for left, right in intervals:
        if left < end and right > start:
            return (left, right) if left <= start and right >= end else (start, end)
    return None


def smooth_sources(
    config: SystemConfig,
    steps: Sequence[SourceStep],
    states: tuple[SourceState, ...],
    weights: Sequence[float],
    prices: tuple[MarketPrice, ...] = (),
) -> tuple[SourceState, ...]:
    """Keep every combination's duration within its original constant input.

    The October plan had five-minute ON islands even at identical hourly cost.
    Grouping by original slot, weight and step duration preserves price and
    power boundaries, including partial hours and DST. Full sequential physics
    still validates the candidate; this is not permission to spend more Wh.
    """
    groups: list[tuple[SourceState, ...]] = []
    # A planner visits the same prices hundreds of times. Convert each once,
    # rather than converting every publication for every five-minute row.
    intervals = tuple((p.start.timestamp(), p.end.timestamp()) for p in prices)
    start = 0
    while start < len(steps):
        check_cancelled()
        end = start + 1
        first = steps[start]
        price = _price_key(first.slot, intervals)
        locked = any(phase_locks(config, first.slot))
        while end < len(steps) and not locked:
            before, after = steps[end - 1].slot, steps[end].slot
            a = before.start
            b = after.start
            elapsed = (
                (b.astimezone(UTC) - a.astimezone(UTC)).total_seconds()
                if a.tzinfo and b.tzinfo
                else (b - a).total_seconds()
            )
            if (
                after.index != first.slot.index
                or abs(after.duration - first.slot.duration) > 1e-9
                or abs(elapsed - before.duration * 3600) > 1e-6
                or weights[end] != weights[start]
                or _price_key(after, intervals) != price
                or any(phase_locks(config, after))
                or end - start >= MAX_SMOOTHING_STEPS
            ):
                break
            end += 1
        groups.append(states[start:end])
        start = end
    return _smooth_groups(
        (config.support.dc24_active, config.support.dc48_active), tuple(groups)
    )


@lru_cache(maxsize=16)
def _smooth_groups(
    initial: SourceState, groups: tuple[tuple[SourceState, ...], ...]
) -> tuple[SourceState, ...]:
    # Pure geometry only: price/phase/gap boundaries are already encoded in
    # groups. Nearby load probes often repeat it. Sixteen horizons bound retained
    # state to a small cache; every returned proposal still receives full physics.
    paths = {initial: _Path(0, 0, 0, ())}
    for original in groups:
        check_cancelled()
        options = (original,) if len(set(original)) == 1 else _orders(original)
        costs = [
            (
                option,
                sum(switches(a, b) for a, b in zip(option, option[1:], strict=False)),
                sum(a != b for a, b in zip(original, option, strict=True)),
                _state_code(option),
            )
            for option in options
        ]
        next_paths: dict[SourceState, _Path] = {}
        for previous, path in paths.items():
            prefix = path.code << (2 * len(original))
            for option, internal_edges, changes, code in costs:
                cost = (
                    path.edges + switches(previous, option[0]) + internal_edges,
                    path.changes + changes,
                )
                incumbent = next_paths.get(option[-1])
                if incumbent is not None and cost > (
                    incumbent.edges,
                    incumbent.changes,
                ):
                    continue
                rank = (*cost, prefix | code)
                if incumbent is None or rank < incumbent.rank():
                    candidate = _Path(*rank, option, path)
                    next_paths[option[-1]] = candidate
        paths = next_paths
    # Backpointers and a compact base-four tie key avoid copying/comparing the
    # entire preceding horizon for every local permutation in every load probe.
    path = min(paths.values(), key=_Path.rank)
    segments = []
    while path.previous is not None:
        segments.append(path.states)
        path = path.previous
    return tuple(state for segment in reversed(segments) for state in segment)
