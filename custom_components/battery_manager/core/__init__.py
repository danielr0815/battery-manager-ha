"""HA-independent simulation and optimization core for Battery Manager.

Design documents: docs/STRATEGY.md, docs/ALGORITHM.md.
Everything in this package is pure Python without Home Assistant imports:
frozen dataclasses in, frozen dataclasses out, no shared mutable state.
"""

from .forecast_hours import aggregate_hours, coverage_and_residual
from .load_profile import (
    DAY_TYPE_ABSENCE,
    DAY_TYPE_WEEKDAY,
    DAY_TYPE_WEEKEND,
    DAY_TYPES,
    QUANTILE_KEYS,
    aggregate_bins,
    balance_day,
    clean_day,
    day_type,
    on_fractions,
    profile_value,
    weighted_quantile,
)
from .model import (
    Appliance,
    ApplianceRun,
    BatteryParams,
    CascadeMember,
    CascadeMemberFlow,
    CascadePlan,
    CascadeRuntimeState,
    CascadeSlotFlow,
    CascadeSourceSegment,
    ControlParams,
    ConverterParams,
    FeedInParams,
    HourSlot,
    LoadCascade,
    LoadProfile,
    PlanInputs,
    PlanResult,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from .optimize import plan, quantile_band_slots
from .series import build_slots, slot_starts
from .simulate import simulate

__all__ = [
    "DAY_TYPES",
    "DAY_TYPE_ABSENCE",
    "DAY_TYPE_WEEKDAY",
    "DAY_TYPE_WEEKEND",
    "QUANTILE_KEYS",
    "Appliance",
    "ApplianceRun",
    "BatteryParams",
    "CascadeMember",
    "CascadeMemberFlow",
    "CascadePlan",
    "CascadeRuntimeState",
    "CascadeSlotFlow",
    "CascadeSourceSegment",
    "ControlParams",
    "ConverterParams",
    "FeedInParams",
    "HourSlot",
    "LoadProfile",
    "LoadCascade",
    "PVParams",
    "PlanInputs",
    "PlanResult",
    "SupportParams",
    "SurplusLoad",
    "SurplusLoadState",
    "SystemConfig",
    "aggregate_bins",
    "aggregate_hours",
    "balance_day",
    "build_slots",
    "clean_day",
    "coverage_and_residual",
    "day_type",
    "on_fractions",
    "plan",
    "profile_value",
    "quantile_band_slots",
    "simulate",
    "slot_starts",
    "weighted_quantile",
]
