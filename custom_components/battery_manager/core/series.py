"""Build hourly input series (PV, AC, DC) for a planning run."""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import ApplianceRun, HourSlot, PlanInputs, SurplusLoadState, SystemConfig


def pv_hour_share(pv, hour_of_day: int) -> float:
    """Share of the daily PV energy produced in the given hour (two-window model)."""
    morning_hours = pv.morning_end_hour - pv.morning_start_hour
    afternoon_hours = pv.afternoon_end_hour - pv.morning_end_hour
    if morning_hours > 0 and pv.morning_start_hour <= hour_of_day < pv.morning_end_hour:
        return pv.morning_ratio / morning_hours
    if (
        afternoon_hours > 0
        and pv.morning_end_hour <= hour_of_day < pv.afternoon_end_hour
    ):
        return (1.0 - pv.morning_ratio) / afternoon_hours
    return 0.0


def slot_starts(now: datetime, num_days: int) -> tuple[datetime, ...]:
    """Enumerate the slot start times of a planning horizon.

    Single source of truth for the slot grid (partial first hour, then a
    fixed hourly raster until midnight after the last forecast day). Used by
    build_slots AND by callers that construct per-slot input series, so both
    sides can never disagree on slot count or indexing (D-C5).
    """
    if num_days <= 0:
        return ()
    horizon_end = (now + timedelta(days=num_days - 1)).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    starts: list[datetime] = []
    slot_start = now
    index = 0
    while slot_start <= horizon_end:
        starts.append(slot_start)
        if index == 0:
            slot_start = slot_start.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
        else:
            slot_start += timedelta(hours=1)
        index += 1
    return tuple(starts)


def _series_value(series: tuple[float | None, ...] | None, index: int) -> float | None:
    """Per-slot override lookup: values beyond the series length are None."""
    if series is None or index >= len(series):
        return None
    return series[index]


def build_slots(
    config: SystemConfig,
    now: datetime,
    start_soc_percent: float,
    daily_forecasts_kwh: list[float],
    appliance_runs: tuple[ApplianceRun, ...] = (),
    load_states: tuple[SurplusLoadState, ...] = (),
    ac_load_w: tuple[float | None, ...] | None = None,
    dc_load_w: tuple[float | None, ...] | None = None,
) -> PlanInputs:
    """Assemble PlanInputs from daily forecasts and load profiles.

    The horizon runs from `now` (partial first hour) until midnight after the
    last forecast day (docs/ALGORITHM.md D-A6).

    `ac_load_w` / `dc_load_w` are optional learned-consumption series
    (docs/CONSUMPTION_FORECAST.md D-C5): mean Watt per slot, addressed by
    slot index. A None value (or a series shorter than the horizon) falls
    back to the static profile for that slot only.
    """
    slots: list[HourSlot] = []

    for index, slot_start in enumerate(slot_starts(now, len(daily_forecasts_kwh))):
        duration = (60 - slot_start.minute) / 60.0 if index == 0 else 1.0

        hour_of_day = slot_start.hour
        day_offset = (slot_start.date() - now.date()).days
        daily_kwh = (
            daily_forecasts_kwh[day_offset]
            if 0 <= day_offset < len(daily_forecasts_kwh)
            else 0.0
        )

        pv_w = min(
            daily_kwh * 1000.0 * pv_hour_share(config.pv, hour_of_day),
            config.pv.peak_power_w,
        )
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
                pv_wh=pv_w * duration,
                ac_wh=ac_w * duration,
                dc_wh=dc_w * duration,
            )
        )

    slots = _apply_appliance_runs(slots, appliance_runs)

    return PlanInputs(
        now=now,
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

    return [
        HourSlot(
            index=s.index,
            start=s.start,
            duration=s.duration,
            hour_of_day=s.hour_of_day,
            pv_wh=s.pv_wh,
            ac_wh=s.ac_wh + extra_wh[i],
            dc_wh=s.dc_wh,
        )
        for i, s in enumerate(slots)
    ]


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
