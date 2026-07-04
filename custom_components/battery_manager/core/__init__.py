"""HA-independent simulation and optimization core for Battery Manager.

Design documents: docs/STRATEGY.md, docs/ALGORITHM.md.
Everything in this package is pure Python without Home Assistant imports:
frozen dataclasses in, frozen dataclasses out, no shared mutable state.
"""

from .load_profile import (
    DAY_TYPE_ABSENCE,
    DAY_TYPE_WEEKDAY,
    DAY_TYPE_WEEKEND,
    DAY_TYPES,
    aggregate_bins,
    balance_day,
    clean_day,
    day_type,
    on_fractions,
    profile_value,
)
from .model import (
    Appliance,
    ApplianceRun,
    BatteryParams,
    ControlParams,
    ConverterParams,
    HourSlot,
    LoadProfile,
    PlanInputs,
    PlanResult,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from .optimize import plan
from .series import build_slots, slot_starts
from .simulate import simulate

__all__ = [
    "DAY_TYPES",
    "DAY_TYPE_ABSENCE",
    "DAY_TYPE_WEEKDAY",
    "DAY_TYPE_WEEKEND",
    "Appliance",
    "ApplianceRun",
    "BatteryParams",
    "ControlParams",
    "ConverterParams",
    "HourSlot",
    "LoadProfile",
    "PVParams",
    "PlanInputs",
    "PlanResult",
    "SupportParams",
    "SurplusLoad",
    "SurplusLoadState",
    "SystemConfig",
    "aggregate_bins",
    "balance_day",
    "build_slots",
    "clean_day",
    "day_type",
    "on_fractions",
    "plan",
    "profile_value",
    "simulate",
    "slot_starts",
]
