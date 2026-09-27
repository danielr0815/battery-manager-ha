"""Measured, cancellable executor work with independent protection checks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from threading import Event
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant

from .core.model import PlanInputs, PlanResult, SystemConfig
from .core.planning_control import cancellation_scope

# Check protection during expensive plans even when no sensor changes arrive.
# This is not a delay before planning; a completed worker returns immediately.
PLANNING_PROTECTION_INTERVAL_S = 5.0
# One percentage point accommodates sensor resolution; thresholds still invalidate.
PLANNING_SOC_TOLERANCE_PERCENT = 1.0


class PlanningRunner:
    """One coordinator owns this runner; cancellation also stops CPU work."""

    def __init__(self) -> None:
        self.phase: str | None = None
        self.started: float | None = None
        self.durations: dict[str, float] = {}
        self.status = "idle"

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "elapsed_seconds": monotonic() - self.started
            if self.started is not None
            else None,
            "last_phase_seconds": dict(self.durations),
        }

    async def async_run(
        self,
        hass: HomeAssistant,
        planner: Callable[[SystemConfig, PlanInputs], PlanResult],
        phase: str,
        config: SystemConfig,
        inputs: PlanInputs,
        protect: Callable[[], Awaitable[None]],
    ) -> PlanResult:
        cancel = Event()
        self.phase, self.started, self.status = phase, monotonic(), "running"

        def calculate() -> PlanResult:
            with cancellation_scope(cancel.is_set):
                return planner(config, inputs)

        worker = hass.async_add_executor_job(calculate)
        try:
            while True:
                done, _ = await asyncio.wait(
                    {worker}, timeout=PLANNING_PROTECTION_INTERVAL_S
                )
                if done:
                    result = worker.result()
                    self.status = "complete"
                    return result
                await protect()
        except BaseException:
            task = asyncio.current_task()
            self.status = (
                "cancelled" if task is not None and task.cancelling() else "failed"
            )
            cancel.set()
            # Cancelling an asyncio waiter alone does not stop its CPU thread.
            # Drain the cooperatively cancelled worker before entry teardown.
            with suppress(Exception):
                await asyncio.shield(worker)
            raise
        finally:
            self.durations[phase] = monotonic() - self.started
            self.phase, self.started = None, None
