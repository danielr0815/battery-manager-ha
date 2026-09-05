"""Build hourly input series (PV, AC, DC) for a planning run."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone

from .forecast_hours import coverage_and_residual
from .model import ApplianceRun, HourSlot, PlanInputs, SurplusLoadState, SystemConfig

_LOGGER = logging.getLogger(__name__)


def _slot_duration_hours(index: int, slot_start: datetime) -> float:
    """Slot length in hours: slot 0 is the partial hour from `now` to the next
    full hour; every later slot is a full hour.

    Seconds and microseconds MUST count (code review 2026-07): the old
    `(60 - minute) / 60` dropped them, stretching slot 0 by up to 59 s and
    systematically over-booking PV and load in the first slot of every cycle.
    """
    if index != 0:
        return 1.0
    remaining_s = (
        3600.0
        - slot_start.minute * 60.0
        - slot_start.second
        - slot_start.microsecond / 1e6
    )
    return remaining_s / 3600.0


def _daily_forecast_wh(daily_kwh: float, day_offset: int) -> float:
    """Daily PV forecast in Wh, sanitized (code review 2026-07): NaN (broken
    forecast sensor) counts as no production — one NaN state would otherwise
    poison EVERY slot of the day with NaN Wh; a negative state is physically
    meaningless and clamps to 0."""
    if math.isnan(daily_kwh):
        _LOGGER.debug("discarding NaN daily PV forecast (day offset %d)", day_offset)
        return 0.0
    return max(0.0, daily_kwh) * 1000.0


def _pv_share_total(pv) -> float:
    """Sum of the per-window shares that are actually emitted. A mis-ordered
    (degenerate) window drops its share, so this can be < 1.0."""
    total = 0.0
    if pv.morning_end_hour - pv.morning_start_hour > 0:
        total += pv.morning_ratio
    if pv.afternoon_end_hour - pv.morning_end_hour > 0:
        total += 1.0 - pv.morning_ratio
    return total


def pv_hour_share(pv, hour_of_day: int) -> float:
    """Share of the daily PV energy produced in the given hour (two-window model).

    Renormalized so the emitted shares sum to 1.0 even for a mis-ordered config:
    a degenerate window would otherwise silently discard a fixed fraction of
    every day's forecast. For a well-ordered config the total is already 1.0, so
    this is a no-op (bit-identical). The config flow also validates the ordering.
    """
    morning_hours = pv.morning_end_hour - pv.morning_start_hour
    afternoon_hours = pv.afternoon_end_hour - pv.morning_end_hour
    raw = 0.0
    if morning_hours > 0 and pv.morning_start_hour <= hour_of_day < pv.morning_end_hour:
        raw = pv.morning_ratio / morning_hours
    elif (
        afternoon_hours > 0
        and pv.morning_end_hour <= hour_of_day < pv.afternoon_end_hour
    ):
        raw = (1.0 - pv.morning_ratio) / afternoon_hours
    if raw == 0.0:
        return 0.0
    total = _pv_share_total(pv)
    return raw / total if total > 0.0 else raw


def fixed_local_time(value: datetime) -> datetime:
    """Preserve the local clock and offset without ambiguous DST arithmetic."""
    offset = value.utcoffset()
    return value.replace(tzinfo=timezone(offset)) if offset is not None else value


def slot_starts(now: datetime, num_days: int) -> tuple[datetime, ...]:
    """Real hourly intervals through local midnight, including 23/25-hour days."""
    if num_days <= 0:
        return ()
    end = (now + timedelta(days=num_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    aware = now.utcoffset() is not None
    cursor = now.astimezone(UTC) if aware else now
    end = end.astimezone(UTC) if aware else end
    starts = []
    while cursor < end:
        local = cursor.astimezone(now.tzinfo) if aware else cursor
        starts.append(fixed_local_time(local))
        cursor += timedelta(hours=_slot_duration_hours(len(starts) - 1, local))
    return tuple(starts)


def _series_value(series: tuple[float | None, ...] | None, index: int) -> float | None:
    """Per-slot override lookup: values beyond the series length are None."""
    if series is None or index >= len(series):
        return None
    return series[index]


def _pv_wh_series(
    config: SystemConfig,
    now: datetime,
    daily_forecasts_kwh: list[float],
    starts: tuple[datetime, ...],
    pv_hourly: dict[datetime, float] | None,
) -> list[float]:
    """Per-slot PV Wh for the whole horizon.

    With a non-empty `pv_hourly` map the hourly forecast drives each slot
    (docs/F-PREDRAIN.md F1); otherwise the legacy two-window synthesis is used
    and the result is bit-identical to the pre-F1 code.
    """
    if pv_hourly:
        return _pv_wh_hourly(config, now, daily_forecasts_kwh, starts, pv_hourly)
    result: list[float] = []
    for index, slot_start in enumerate(starts):
        duration = _slot_duration_hours(index, slot_start)
        day_offset = (slot_start.date() - now.date()).days
        daily_wh = (
            _daily_forecast_wh(daily_forecasts_kwh[day_offset], day_offset)
            if 0 <= day_offset < len(daily_forecasts_kwh)
            else 0.0
        )
        pv_w = min(
            daily_wh * pv_hour_share(config.pv, slot_start.hour),
            config.pv.peak_power_w,
        )
        result.append(max(0.0, pv_w) * duration)
    return result


def _pv_wh_hourly(
    config: SystemConfig,
    now: datetime,
    daily_forecasts_kwh: list[float],
    starts: tuple[datetime, ...],
    pv_hourly: dict[datetime, float],
) -> list[float]:
    """Map an hourly PV forecast onto the slot grid (docs/F-PREDRAIN.md F1).

    A slot whose hour is present in the map takes that hour's Wh, prorated by the
    slot duration (the partial first slot gets its share of the hour). An
    uncovered hour of a day takes its two-window SHARE of that day's RESIDUAL
    (daily total minus the day's covered buckets): `residual_wh *
    pv_hour_share(hour) * duration`. There is NO renormalisation over the
    remaining/uncovered slots (FIX-3): the two-window shares already sum to 1
    over the FULL day, so a day with NO buckets reproduces the legacy two-window
    values bit-for-bit — even when `now` is mid-morning and the elapsed hours are
    absent from the horizon (renormalising over only the remaining slots inflated
    the afternoon PV 3-5x). A partially covered day therefore UNDER-fills its
    uncovered slots (the covered hours' share of the residual is not
    redistributed) rather than over-filling — conservative by design. The
    per-slot peak-power cap then applies exactly as in the two-window path.
    """
    peak = config.pv.peak_power_w
    # Sanitize the bucket map ONCE (code review 2026-07): NaN buckets are
    # DROPPED — their hours count as uncovered and take a residual share like
    # any other hour the map does not cover (the documented fallback); kept,
    # they would NaN-poison the day's residual AND their own slot. Negative
    # buckets are physically meaningless and clamp to 0 Wh.
    buckets: dict[datetime, float] = {}
    for key, wh in pv_hourly.items():
        if math.isnan(wh):
            _LOGGER.debug("dropping NaN hourly PV bucket at %s", key)
            continue
        buckets[key] = max(0.0, wh)
    # Per-day residual from the daily forecast state vs. the day's covered buckets.
    residual_by_day: dict[date, float] = {}
    for day_offset, daily_kwh in enumerate(daily_forecasts_kwh):
        day = now.date() + timedelta(days=day_offset)
        _covered_wh, residual_wh = coverage_and_residual(
            (wh for key, wh in buckets.items() if key.date() == day),
            _daily_forecast_wh(daily_kwh, day_offset),
        )
        residual_by_day[day] = residual_wh

    result: list[float] = []
    for index, slot_start in enumerate(starts):
        duration = _slot_duration_hours(index, slot_start)
        cap = peak * duration
        hour_key = slot_start.replace(minute=0, second=0, microsecond=0)
        if hour_key in buckets:
            pv_wh = buckets[hour_key] * duration
        else:
            share = pv_hour_share(config.pv, slot_start.hour)
            pv_wh = residual_by_day.get(slot_start.date(), 0.0) * share * duration
        result.append(min(max(0.0, pv_wh), cap))
    return result


def _quantile_wh(
    series: dict[datetime, float] | None, slot_start: datetime, duration: float
) -> float | None:
    """Per-slot P10/P90 Wh, or None where the quantile buckets do not cover
    the slot (F-QUANTILE-BANDS R2: coverage is per-slot, never per-day; no
    two-window fallback and no residual fill — a band exists only on
    evidence). Prorated by the slot duration like the median buckets."""
    if not series:
        return None
    hour_key = slot_start.replace(minute=0, second=0, microsecond=0)
    if hour_key not in series:
        return None
    value = series[hour_key]
    if math.isnan(value):
        # No band on broken evidence (code review 2026-07): a NaN quantile
        # bucket leaves the slot UNBANDED (same as uncovered) instead of
        # NaN-poisoning the stress simulations reading the band.
        _LOGGER.debug("dropping NaN PV quantile bucket at %s", hour_key)
        return None
    return max(0.0, value) * duration


def build_slots(
    config: SystemConfig,
    now: datetime,
    start_soc_percent: float,
    daily_forecasts_kwh: list[float],
    appliance_runs: tuple[ApplianceRun, ...] = (),
    load_states: tuple[SurplusLoadState, ...] = (),
    ac_load_w: tuple[float | None, ...] | None = None,
    dc_load_w: tuple[float | None, ...] | None = None,
    pv_hourly: dict[datetime, float] | None = None,
    pv_hourly_p10: dict[datetime, float] | None = None,
    pv_hourly_p90: dict[datetime, float] | None = None,
) -> PlanInputs:
    """Assemble PlanInputs from daily forecasts and load profiles.

    The horizon runs from `now` (partial first hour) until midnight after the
    last forecast day (docs/ALGORITHM.md D-A6).

    `ac_load_w` / `dc_load_w` are optional learned-consumption series
    (docs/CONSUMPTION_FORECAST.md D-C5): mean Watt per slot, addressed by
    slot index. A None value (or a series shorter than the horizon) falls
    back to the static profile for that slot only.

    `pv_hourly` is an optional local hour-start -> Wh map (with matching offsets) (docs/F-PREDRAIN.md
    F1). When non-empty it drives the per-slot PV instead of the two-window model;
    None or an empty map keeps the two-window synthesis bit-identical to before.

    `pv_hourly_p10` / `pv_hourly_p90` are the matching empirical quantile maps
    (F-QUANTILE-BANDS R2). They only ever annotate slots their buckets cover;
    None/empty maps leave every slot's band fields None (the R8 bit-identity
    anchor — the planner then falls back to the scalar alpha/beta dials).
    """
    slots: list[HourSlot] = []

    starts = slot_starts(now, len(daily_forecasts_kwh))
    pv_wh_series = _pv_wh_series(config, now, daily_forecasts_kwh, starts, pv_hourly)

    for index, slot_start in enumerate(starts):
        duration = _slot_duration_hours(index, slot_start)

        hour_of_day = slot_start.hour
        ac_override = _series_value(ac_load_w, index)
        dc_override = _series_value(dc_load_w, index)
        ac_w = (
            ac_override
            if ac_override is not None
            else config.ac_profile.power_w(hour_of_day)
        )
        dc_w = (
            dc_override
            if dc_override is not None
            else config.dc_profile.power_w(hour_of_day)
        )

        slots.append(
            HourSlot(
                index=index,
                start=slot_start,
                duration=duration,
                hour_of_day=hour_of_day,
                pv_wh=pv_wh_series[index],
                ac_wh=ac_w * duration,
                dc_wh=dc_w * duration,
                pv_p10_wh=_quantile_wh(pv_hourly_p10, slot_start, duration),
                pv_p90_wh=_quantile_wh(pv_hourly_p90, slot_start, duration),
            )
        )

    slots = _apply_appliance_runs(slots, appliance_runs)

    return PlanInputs(
        now=fixed_local_time(now),
        start_soc_percent=start_soc_percent,
        slots=tuple(slots),
        load_states=load_states,
        appliance_runs=appliance_runs,
    )


def _apply_appliance_runs(
    slots: list[HourSlot], runs: tuple[ApplianceRun, ...]
) -> list[HourSlot]:
    """Spread each running appliance's remaining energy over its remaining hours."""
    if not runs:
        return slots

    extra_wh = [0.0] * len(slots)
    for run in runs:
        if run.remaining_energy_wh <= 0 or run.remaining_hours <= 0:
            continue
        power_w = run.remaining_energy_wh / run.remaining_hours
        budget = run.remaining_energy_wh
        for i, slot in enumerate(slots):
            if budget <= 0:
                break
            portion = min(power_w * slot.duration, budget)
            extra_wh[i] += portion
            budget -= portion

    # dataclasses.replace keeps every other slot field (incl. the optional
    # P10/P90 band, F-QUANTILE-BANDS R2) instead of silently dropping it.
    return [replace(s, ac_wh=s.ac_wh + extra_wh[i]) for i, s in enumerate(slots)]


def insert_appliance_run(
    inputs: PlanInputs, energy_wh: float, duration_h: float
) -> PlanInputs:
    """Return new PlanInputs with a hypothetical appliance run starting now.

    Used by the appliance advisor ("could a full run start right now without
    causing grid import?", docs/ALGORITHM.md D-A5).
    """
    run = ApplianceRun(
        appliance_id="_hypothetical",
        remaining_energy_wh=energy_wh,
        remaining_hours=duration_h,
    )
    new_slots = _apply_appliance_runs(list(inputs.slots), (run,))
    return PlanInputs(
        now=inputs.now,
        start_soc_percent=inputs.start_soc_percent,
        slots=tuple(new_slots),
        load_states=inputs.load_states,
        appliance_runs=inputs.appliance_runs,
    )
