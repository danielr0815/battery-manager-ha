"""Bounded reuse of immutable five-minute slots across candidate simulations."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, timedelta
from functools import lru_cache

from .model import HourFlows, HourSlot, SwitchingInterval

SUPPORT_STEP_HOURS = 1 / 12
# Eight three-day hourly horizons allow baseline, reserve and appliance probes
# to share their validated grid. Cache keys contain all physical slot inputs.
STEP_CACHE_SLOTS = 8 * 3 * 24


def split_slot(slot: HourSlot) -> tuple[tuple[HourSlot, float], ...]:
    """Include UTC offset in the key: datetime equality ignores DST fold."""
    return _split_slot(slot, slot.start.isoformat())


@lru_cache(maxsize=STEP_CACHE_SLOTS)
def _split_slot(slot: HourSlot, start_iso: str) -> tuple[tuple[HourSlot, float], ...]:
    """Retain exact arithmetic and partial-slot lengths of the original loop."""
    steps = []
    elapsed = 0.0
    while elapsed < slot.duration - 1e-9:
        duration = min(SUPPORT_STEP_HOURS, slot.duration - elapsed)
        ratio = duration / slot.duration
        small = replace(
            slot,
            start=(slot.start.astimezone(UTC) + timedelta(hours=elapsed)).astimezone(
                slot.start.tzinfo
            )
            if slot.start.tzinfo is not None
            else slot.start + timedelta(hours=elapsed),
            duration=duration,
            pv_wh=slot.pv_wh * ratio,
            ac_wh=slot.ac_wh * ratio,
            dc_wh=slot.dc_wh * ratio,
        )
        steps.append((small, ratio))
        elapsed += duration
    return tuple(steps)


def switching_schedule(
    slots: Iterable[HourSlot], flows: Iterable[HourFlows]
) -> tuple[SwitchingInterval, ...]:
    """Compress equal adjacent states without inferring edges from hourly flags.

    Coordinated/reserve flows already carry five-minute decisions. Legacy
    simulation has only slot resolution. UTC arithmetic preserves DST folds.
    """
    result: list[SwitchingInterval] = []
    for slot, flow in zip(slots, flows, strict=True):
        start = slot.start
        end = (
            (start.astimezone(UTC) + timedelta(hours=slot.duration)).astimezone(
                start.tzinfo
            )
            if start.tzinfo is not None
            else start + timedelta(hours=slot.duration)
        )
        intervals = flow.switching_schedule or (
            SwitchingInterval(
                start, end, flow.inverter_on, flow.support_dc24, flow.support_dc48
            ),
        )
        for interval in intervals:
            previous = result[-1] if result else None
            if (
                previous is not None
                and (
                    previous.end.astimezone(UTC)
                    if previous.end.tzinfo
                    else previous.end
                )
                == (
                    interval.start.astimezone(UTC)
                    if interval.start.tzinfo
                    else interval.start
                )
                and previous.inverter_on == interval.inverter_on
                and previous.support_dc24 == interval.support_dc24
                and previous.support_dc48 == interval.support_dc48
            ):
                result[-1] = replace(previous, end=interval.end)
            else:
                result.append(interval)
    return tuple(result)
