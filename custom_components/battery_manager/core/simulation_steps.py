"""Bounded reuse of immutable five-minute slots across candidate simulations."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    pending: tuple[datetime, datetime, bool, bool, bool] | None = None
    for slot, flow in zip(slots, flows, strict=True):
        intervals: Iterable[tuple[datetime, datetime, bool, bool, bool]]
        if flow.switching_schedule:
            intervals = (
                (
                    interval.start,
                    interval.end,
                    interval.inverter_on,
                    interval.support_dc24,
                    interval.support_dc48,
                )
                for interval in flow.switching_schedule
            )
        else:
            start = slot.start
            end = (
                (start.astimezone(UTC) + timedelta(hours=slot.duration)).astimezone(
                    start.tzinfo
                )
                if start.tzinfo is not None
                else start + timedelta(hours=slot.duration)
            )
            intervals = (
                (start, end, flow.inverter_on, flow.support_dc24, flow.support_dc48),
            )
        for interval in intervals:
            if (
                pending is not None
                and (pending[1].astimezone(UTC) if pending[1].tzinfo else pending[1])
                == (interval[0].astimezone(UTC) if interval[0].tzinfo else interval[0])
                and pending[2:] == interval[2:]
            ):
                pending = (
                    pending[0],
                    interval[1],
                    interval[2],
                    interval[3],
                    interval[4],
                )
            else:
                if pending is not None:
                    result.append(SwitchingInterval(*pending))
                pending = interval
    # Construct only completed runs. Replacing a frozen interval for every
    # equal five-minute step created millions of discarded objects in probes.
    if pending is not None:
        result.append(SwitchingInterval(*pending))
    return tuple(result)
