"""Bounded use of measured AC demand before a forecast opportunity arrives."""

from dataclasses import dataclass
from datetime import datetime

# Separate thresholds reject meter noise without toggling at one boundary.
LIVE_AC_ON_W = 100.0
LIVE_AC_OFF_W = 50.0
# Heating cycles pause for minutes; protection always bypasses this hold.
LIVE_AC_OFF_DELAY_S = 600
LIVE_AC_SAMPLE_MAX_AGE_S = 30
LIVE_AC_PLAN_MAX_AGE_S = 300
# Ask for a successor before budget expiry; normal planning may take seconds.
LIVE_AC_REPLAN_LEAD_S = 60
LIVE_AC_INTERVAL_S = 5


@dataclass(frozen=True)
class LiveACState:
    active: bool = False
    low_since: datetime | None = None


@dataclass(frozen=True)
class LiveACDecision:
    state: LiveACState
    limit_w: int
    reason: str


def live_ac_decision(
    previous: LiveACState,
    now: datetime,
    demand_w: float | None,
    available_wh: float,
    max_power_w: float,
    efficiency: float,
    blocked_reason: str | None = None,
) -> LiveACDecision:
    """Limit permission, never force discharge/export irrespective of demand.

    available_wh excludes DC/reserve AND SOC uncertainty. The limit cannot
    spend more than this energy during one measurement freshness window plus
    the next check, even when the SOC sensor has not reported again yet.
    """
    limit = int(
        min(
            max_power_w,
            max(0.0, available_wh)
            * efficiency
            * 3600
            / (LIVE_AC_SAMPLE_MAX_AGE_S + LIVE_AC_INTERVAL_S),
        )
    )
    if blocked_reason or demand_w is None or limit < LIVE_AC_ON_W:
        return LiveACDecision(
            LiveACState(),
            0,
            blocked_reason
            or ("measurement_unavailable" if demand_w is None else "reserve_budget"),
        )
    if demand_w >= LIVE_AC_ON_W:
        return LiveACDecision(LiveACState(True), limit, "measured_ac_demand")
    if previous.active:
        if demand_w >= LIVE_AC_OFF_W:
            return LiveACDecision(LiveACState(True), limit, "measured_ac_demand")
        low_since = previous.low_since or now
        if (now - low_since).total_seconds() < LIVE_AC_OFF_DELAY_S:
            return LiveACDecision(LiveACState(True, low_since), limit, "off_delay")
    return LiveACDecision(LiveACState(), 0, "no_ac_demand")
