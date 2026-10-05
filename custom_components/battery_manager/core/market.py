"""Bounded market preference; price signals never create an energy budget."""

from collections import defaultdict
from datetime import UTC, date, timedelta

from .model import HourSlot, MarketPrice

# Four elapsed hours, not four rows: EPEX may publish quarter-hour intervals.
PEAK_HOURS = 4.0
# A 50 EUR/MWh spread matters; rounding noise around a flat day must not.
PRICE_SCALE_EUR_MWH = 50.0
MAX_PRICE_BONUS = 2.0


def peak_weights(prices: tuple[MarketPrice, ...]) -> tuple[float, ...]:
    """Prefer the daily top four hours, with strength proportional to spread.

    The capped multiplier allows a modest load difference to lose to a price
    spike without sending a nearly idle inverter ahead of a useful large load.
    All published hours (including past hours) define a stable daily median.
    """
    days: dict[date, list[int]] = defaultdict(list)
    for index, price in enumerate(prices):
        days[price.start.date()].append(index)
    result = [1.0] * len(prices)
    for indices in days.values():
        ordered = sorted(
            indices,
            key=lambda i: (prices[i].eur_per_mwh, prices[i].start.timestamp()),
            reverse=True,
        )
        durations = {
            i: (prices[i].end.timestamp() - prices[i].start.timestamp()) / 3600
            for i in indices
        }
        half = sum(durations.values()) / 2
        elapsed = 0.0
        median = prices[ordered[-1]].eur_per_mwh
        for i in ordered:
            elapsed += durations[i]
            median = prices[i].eur_per_mwh
            if elapsed > half:
                break
        remaining = PEAK_HOURS
        for i in ordered:
            share = min(remaining, durations[i]) / durations[i]
            premium = max(0.0, prices[i].eur_per_mwh - median)
            result[i] += share * min(MAX_PRICE_BONUS, premium / PRICE_SCALE_EUR_MWH)
            remaining = max(0.0, remaining - durations[i])
    return tuple(result)


def slot_weights(
    slots: tuple[HourSlot, ...], prices: tuple[MarketPrice, ...]
) -> list[float | None]:
    """Duration-weighted signals with neutral preference for uncovered seconds.

    An entirely uncovered step stays unknown; a gap cannot erase a known peak.
    Naive legacy replay slots have no reliable market timezone: stay neutral.
    UTC comparisons distinguish the repeated autumn hour and real durations.
    """
    weights = peak_weights(prices)
    # Every candidate visits the same intervals. UTC conversion depends only
    # on the publication, not the slot or overlap; preserve summation order.
    intervals = tuple(
        (price.start.astimezone(UTC), price.end.astimezone(UTC), weight)
        for price, weight in zip(prices, weights, strict=True)
    )
    result: list[float | None] = []
    for slot in slots:
        if slot.start.tzinfo is None:
            result.append(None)
            continue
        start = slot.start.astimezone(UTC)
        end = start + timedelta(hours=slot.duration)
        covered = total = 0.0
        for price_start, price_end, weight in intervals:
            seconds = max(
                0.0,
                (min(end, price_end) - max(start, price_start)).total_seconds(),
            )
            covered += seconds
            total += seconds * weight
        seconds = slot.duration * 3600
        result.append(
            total / covered
            if covered >= seconds - 1e-6
            else (total + seconds - covered) / seconds
            if covered > 0
            else None
        )
    return result
