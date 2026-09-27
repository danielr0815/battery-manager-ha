"""Cooperative cancellation of pure planning work, scoped to one worker call."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_cancelled: ContextVar[Callable[[], bool] | None] = ContextVar(
    "planning_cancelled", default=None
)


class PlanningCancelled(Exception):
    """The owner no longer needs this calculation."""


def check_cancelled() -> None:
    """Bound cancellation latency to one simulation, without HA dependencies."""
    cancelled = _cancelled.get()
    if cancelled is not None and cancelled():
        raise PlanningCancelled


@contextmanager
def cancellation_scope(cancelled: Callable[[], bool]) -> Iterator[None]:
    """Restore the worker context even after a failed or cancelled plan."""
    token = _cancelled.set(cancelled)
    try:
        check_cancelled()
        yield
    finally:
        _cancelled.reset(token)
