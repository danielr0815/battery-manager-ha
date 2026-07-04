"""HA-independent simulation and optimization core for Battery Manager.

Design documents: docs/STRATEGY.md, docs/ALGORITHM.md.
Everything in this package is pure Python without Home Assistant imports:
frozen dataclasses in, frozen dataclasses out, no shared mutable state.
"""

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
from .series import build_slots
from .simulate import simulate

__all__ = [
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
    "build_slots",
    "plan",
    "simulate",
]
