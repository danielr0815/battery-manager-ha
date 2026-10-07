"""Bounded startup evidence, collected away from Home Assistant's event loop.

Process memory is shared by all integrations: phase deltas identify suspects,
not exclusive ownership. Optional one-shot tracing attributes Python allocations
after this integration starts; it cannot reconstruct earlier allocations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tracemalloc
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)
SAMPLE_INTERVAL_S = 2.0
OBSERVATION_SECONDS = 120.0
MAX_SAMPLES = 96
MAX_TRACE_SNAPSHOTS = 8
TRACE_INTERVAL_S = 10.0
TRACE_GROWTH_BYTES = 64 * 1024 * 1024
TRACE_MARKER = "trace-next-start"


def _proc_values(path: Path, keys: dict[str, str]) -> dict[str, int]:
    """Linux procfs reports these counters in KiB, unlike Supervisor bytes."""
    values = {}
    try:
        for line in path.read_text().splitlines():
            name, _, value = line.partition(":")
            if name in keys:
                values[keys[name]] = int(value.split()[0]) * 1024
    except OSError, ValueError, IndexError:
        # Evidence is best effort; unavailable metrics are absent, never zero.
        pass
    return values


def read_memory() -> dict[str, int]:
    return {
        "pid": os.getpid(),
        **_proc_values(
            Path("/proc/self/status"),
            {"VmRSS": "rss_bytes", "VmHWM": "peak_rss_bytes", "VmSwap": "swap_bytes"},
        ),
        **_proc_values(
            Path("/proc/meminfo"),
            {
                "MemTotal": "host_total_bytes",
                "MemAvailable": "host_available_bytes",
                "SwapTotal": "host_swap_total_bytes",
                "SwapFree": "host_swap_free_bytes",
            },
        ),
    }


class StartupDiagnostics:
    """One entry owns a finite sampler; unload drains it before trace cleanup."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.started: float | None = None
        self.active = False
        self.rows: deque[dict[str, Any]] = deque(maxlen=MAX_SAMPLES)
        self.phases: dict[str, int] = {}
        self._cancel: Callable[[], None] | None = None
        self._task: asyncio.Task | None = None
        self._sample_lock = asyncio.Lock()
        self._tracing = False
        self._owns_trace = False
        self._trace_count = 0
        self._trace_at = float("-inf")
        self._trace_rss = 0

    def _start_trace(self) -> None:
        marker = Path(self.hass.config.path("battery_manager", TRACE_MARKER))
        try:
            if marker.is_symlink() or not marker.is_file():
                return
            # Consume before enabling: an OOM must not trigger tracing again
            # on the recovery boot. Only the entry claiming this file traces.
            marker.unlink()
        except OSError:
            _LOGGER.warning("Could not consume startup trace marker")
            return
        if not tracemalloc.is_tracing():
            tracemalloc.start(3)
            self._owns_trace = True
        self._tracing = True

    async def async_start(self) -> None:
        self.started, self.active = monotonic(), True
        worker = self.hass.async_add_executor_job(self._start_trace)
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            with suppress(Exception):
                await worker
            raise
        except Exception:
            _LOGGER.warning("Startup allocation tracing unavailable", exc_info=True)
        await self.async_sample("startup_begin")
        self._schedule()

    def _schedule(self) -> None:
        if self.active:
            self._cancel = async_call_later(self.hass, SAMPLE_INTERVAL_S, self._tick)

    @callback
    def _tick(self, _now: datetime) -> None:
        self._cancel = None
        self._task = self.hass.async_create_background_task(
            self._observe(), "battery_manager_startup_memory"
        )

    async def _observe(self) -> None:
        if (
            self.started is not None
            and monotonic() - self.started >= OBSERVATION_SECONDS
        ):
            await self.async_sample("startup_end")
            # This task is the sampler itself; no self-await during cleanup.
            self.active = False
            await self.hass.async_add_executor_job(self._stop_trace)
        else:
            await self.async_sample("sample")
            self._schedule()

    def _capture(self, event: str, phases: tuple[str, ...]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(
                monotonic() - self.started if self.started is not None else 0.0, 3
            ),
            "event": event,
            "phases": phases,
            **read_memory(),
        }
        if self._tracing and tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            row.update(
                traced_bytes=current,
                traced_peak_bytes=peak,
                tracing_overhead_bytes=tracemalloc.get_tracemalloc_memory(),
            )
            rss = row.get("rss_bytes", 0)
            now = monotonic()
            if (
                self._trace_count < MAX_TRACE_SNAPSHOTS
                and now - self._trace_at >= TRACE_INTERVAL_S
                and (
                    self._trace_count == 0
                    or rss - self._trace_rss >= TRACE_GROWTH_BYTES
                    or event == "startup_end"
                )
            ):
                row["python_allocations"] = [
                    {
                        "bytes": stat.size,
                        "count": stat.count,
                        # Keep useful module paths, without private absolute roots.
                        "frames": [
                            f"{'/'.join(Path(frame.filename).parts[-3:])}:{frame.lineno}"
                            for frame in stat.traceback
                        ],
                    }
                    for stat in tracemalloc.take_snapshot().statistics("traceback")[:20]
                ]
                self._trace_count += 1
                self._trace_at, self._trace_rss = now, rss
        _LOGGER.info("Startup memory [%s]: %s", self.entry_id, json.dumps(row))
        return row

    async def async_sample(self, event: str) -> None:
        async with self._sample_lock:
            if not self.active:
                return
            worker = self.hass.async_add_executor_job(
                self._capture, event, tuple(sorted(self.phases))
            )
            try:
                row = await asyncio.shield(worker)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await worker
                raise
            except Exception:  # diagnostics must not change protection behavior
                _LOGGER.warning("Startup memory sample unavailable", exc_info=True)
            else:
                self.rows.append(row)

    @asynccontextmanager
    async def phase(self, name: str) -> AsyncIterator[None]:
        if not self.active:
            yield
            return
        self.phases[name] = self.phases.get(name, 0) + 1
        status = "end"
        try:
            await self.async_sample(f"{name}:begin")
            yield
        except BaseException:
            status = "aborted"
            raise
        finally:
            self.phases[name] -= 1
            if not self.phases[name]:
                del self.phases[name]
            await self.async_sample(f"{name}:{status}")

    def _stop_trace(self) -> None:
        if self._owns_trace:
            tracemalloc.stop()
        self._owns_trace = self._tracing = False

    async def async_stop(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        self.active = False
        if self._task is not None:
            # Drain procfs/trace work; cancellation must not orphan a worker.
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._task)
            self._task = None
        async with self._sample_lock:
            await self.hass.async_add_executor_job(self._stop_trace)

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "tracing": self._tracing,
            "scope": "HA process and host; phases can overlap other integrations",
            "samples": [dict(row) for row in self.rows],
        }
