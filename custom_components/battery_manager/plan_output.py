"""Pure presentation of a completed plan for Home Assistant entities."""

from typing import Any

from .core.model import PlanInputs, PlanResult


def daily_surplus_breakdown(
    inputs: PlanInputs, result: PlanResult
) -> list[dict[str, Any]]:
    """Per-calendar-day lost-surplus / grid-import / load-energy split
    (F-PERDAY-SURPLUS R1 + §5 v2).

    Grouped by ``slot.start.date()`` in planner-local time: a slot belongs to
    the day it STARTS in (hourly grid, D-A7), so a 23:00 slot counts on its
    start day even where it conceptually crosses midnight. lost_surplus mirrors
    grid export (core: lost_surplus_kwh == grid_export_kwh), so the sums over
    the returned entries equal the existing totals (rounding aside).
    ``loads_kwh`` (v2, R-V2-1) sums the final trajectory's per-slot
    ``extra_ac_wh`` — the SURPLUS-LOAD energy scheduled that day; appliances
    are excluded by construction (they enter the AC forecast, never
    ``extra_ac_wh``).

    ``prevented_export_kwh`` (F-STRICT-SURPLUS R4) is the counterfactual:
    the export the load runs prevented that day, taken straight from
    ``result.prevented_export_by_day_wh`` (base minus alloc, both PRE
    support-escalation so support PSUs never deflate it). It answers "why
    is a load running although SOC never reaches max?" on the dashboard.
    """
    export_by_day: dict[str, float] = {}
    import_by_day: dict[str, float] = {}
    loads_by_day: dict[str, float] = {}
    # Grid-support energy per day, so the card's legend can show the
    # support lanes with the same heute/morgen figure as every other lane
    # (operator ask 2026-08-03). Delivered Wh, i.e. what the PSUs actually
    # put on their rail — not their nominal rating.
    dc24_by_day: dict[str, float] = {}
    dc48_by_day: dict[str, float] = {}
    order: list[str] = []
    for slot, flow in zip(inputs.slots, result.trajectory.flows, strict=True):
        day = slot.start.date().isoformat()
        if day not in export_by_day:
            export_by_day[day] = 0.0
            import_by_day[day] = 0.0
            loads_by_day[day] = 0.0
            dc24_by_day[day] = 0.0
            dc48_by_day[day] = 0.0
            order.append(day)
        export_by_day[day] += flow.grid_export_wh
        import_by_day[day] += flow.grid_import_wh
        loads_by_day[day] += flow.extra_ac_wh
        # getattr: the coordinator tests build flows as SimpleNamespace
        # stubs, and a stub without the support fields must not break the
        # breakdown (same defensive read as `feedin_by_day_wh` below).
        dc24_by_day[day] += getattr(flow, "psu24_delivered_wh", 0.0) or 0.0
        dc48_by_day[day] += getattr(flow, "psu48_delivered_wh", 0.0) or 0.0
    prevented = result.prevented_export_by_day_wh
    # F-FEEDIN: planned feed-in per day (ISO date -> Wh), straight from the
    # PlanResult. Emitted ONLY when anything was booked — the card renders
    # its stats line purely on attribute presence (backend-compat, same
    # pattern as prevented_export_kwh != null on the frontend).
    feedin = getattr(result, "feedin_by_day_wh", None) or {}
    entries = []
    for day in order:
        entry = {
            "date": day,
            "lost_surplus_kwh": round(export_by_day[day] / 1000.0, 3),
            "grid_import_kwh": round(import_by_day[day] / 1000.0, 3),
            "loads_kwh": round(loads_by_day[day] / 1000.0, 3),
            "prevented_export_kwh": round(prevented.get(day, 0.0) / 1000.0, 3),
            "support_dc24_kwh": round(dc24_by_day[day] / 1000.0, 3),
            "support_dc48_kwh": round(dc48_by_day[day] / 1000.0, 3),
        }
        if feedin:
            entry["planned_feedin_kwh"] = round(feedin.get(day, 0.0) / 1000.0, 3)
        entries.append(entry)
    return entries
