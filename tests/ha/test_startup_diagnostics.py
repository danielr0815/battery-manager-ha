"""Startup evidence stays bounded, survives worker failures and drains on unload."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.battery_manager import startup_diagnostics as sd


@pytest.fixture
def rig(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(sd, "monotonic", lambda: clock.now)
    timers = []

    def later(_hass, seconds, fn):
        cancel = Mock()
        timers.append((seconds, fn, cancel))
        return cancel

    monkeypatch.setattr(sd, "async_call_later", later)
    monkeypatch.setattr(sd, "read_memory", lambda: {"pid": 7, "rss_bytes": 100})

    async def executor(fn, *args):
        return fn(*args)

    hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p))),
        async_add_executor_job=executor,
        async_create_background_task=lambda coro, _name: asyncio.create_task(coro),
    )
    return sd.StartupDiagnostics(hass, "entry"), clock, timers, tmp_path


@pytest.fixture
def tracer(monkeypatch):
    state = SimpleNamespace(active=False)
    monkeypatch.setattr(sd.tracemalloc, "is_tracing", lambda: state.active)

    def start(_frames):
        state.active = True

    def stop():
        state.active = False

    state.start, state.stop = Mock(side_effect=start), Mock(side_effect=stop)
    monkeypatch.setattr(sd.tracemalloc, "start", state.start)
    monkeypatch.setattr(sd.tracemalloc, "stop", state.stop)
    monkeypatch.setattr(sd.tracemalloc, "get_traced_memory", lambda: (20, 30))
    monkeypatch.setattr(sd.tracemalloc, "get_tracemalloc_memory", lambda: 10)
    stats = [
        SimpleNamespace(
            size=1000 - i,
            count=30,
            traceback=[
                SimpleNamespace(filename="/private/config/module/code.py", lineno=7)
            ],
        )
        for i in range(30)
    ]
    state.snapshot = Mock(return_value=SimpleNamespace(statistics=lambda _key: stats))
    monkeypatch.setattr(sd.tracemalloc, "take_snapshot", state.snapshot)
    return state


def enable_trace(rig):
    marker = rig[3] / "battery_manager" / sd.TRACE_MARKER
    marker.parent.mkdir()
    marker.touch()
    return marker


@pytest.mark.parametrize(
    "raw", ["VmRSS: 12 kB\nVmHWM: 20 kB\n", "VmRSS: invalid\n", "VmRSS:\n"]
)
def test_proc_metrics_use_bytes_and_missing_values_are_absent(tmp_path, raw):
    path = tmp_path / "proc"
    path.write_text(raw)
    values = sd._proc_values(path, {"VmRSS": "rss", "VmHWM": "peak"})
    if "12" in raw:
        assert values == {"rss": 12288, "peak": 20480}
    else:
        assert values == {}
    assert sd._proc_values(tmp_path / "missing", {"VmRSS": "rss"}) == {}


def test_memory_metrics_have_separate_process_and_host_scope(monkeypatch):
    def values(path, keys):
        return {value: 12 for value in keys.values()}

    monkeypatch.setattr(sd, "_proc_values", values)
    memory = sd.read_memory()
    assert memory["pid"] > 0
    assert memory["rss_bytes"] == memory["host_available_bytes"] == 12
    assert memory["host_swap_free_bytes"] == 12


async def test_passive_sampler_records_phases_and_stops_at_mocked_deadline(rig, caplog):
    diag, clock, timers, _ = rig
    await diag.async_start()
    assert timers[0][0] == sd.SAMPLE_INTERVAL_S == 2
    async with diag.phase("archive"), diag.phase("archive"):
        assert diag.phases == {"archive": 2}
    assert diag.phases == {}
    clock.now = 2
    timers[-1][1](None)
    await diag._task
    clock.now = sd.OBSERVATION_SECONDS
    timers[-1][1](None)
    await diag._task
    assert diag.rows[-1]["event"] == "startup_end"
    assert diag.rows[-1]["elapsed_seconds"] == 120
    assert not diag.active
    assert all("python_allocations" not in row for row in diag.rows)
    assert "archive:begin" in caplog.text
    assert (
        json.loads(json.dumps(diag.snapshot()))["samples"][-1]["event"] == "startup_end"
    )
    await diag.async_stop()


async def test_stop_cancels_timer_and_inactive_phases_do_no_work(rig):
    diag, _, timers, _ = rig
    async with diag.phase("inactive"):
        pass
    await diag.async_sample("inactive")
    assert not diag.rows
    await diag.async_start()
    await diag.async_stop()
    timers[-1][2].assert_called_once_with()
    async with diag.phase("inactive"):
        pass
    assert not diag.phases


async def test_aborted_phase_is_visible_and_original_error_survives(rig):
    diag = rig[0]
    await diag.async_start()
    with pytest.raises(ValueError, match="original"):
        async with diag.phase("archive"):
            raise ValueError("original")
    assert diag.rows[-1]["event"] == "archive:aborted"
    assert not diag.phases
    await diag.async_stop()


async def test_samples_and_trace_snapshots_are_bounded(rig, tracer):
    diag, clock, _, _ = rig
    marker = enable_trace(rig)
    await diag.async_start()
    assert not marker.exists()
    tracer.start.assert_called_once_with(3)
    assert diag.rows[0]["traced_bytes"] == 20
    assert len(diag.rows[0]["python_allocations"]) == 20
    assert diag.rows[0]["python_allocations"][0]["frames"] == [
        "config/module/code.py:7"
    ]
    for _ in range(sd.MAX_SAMPLES + 5):
        clock.now += sd.TRACE_INTERVAL_S
        await diag.async_sample("startup_end")
    assert len(diag.rows) == sd.MAX_SAMPLES
    assert tracer.snapshot.call_count == sd.MAX_TRACE_SNAPSHOTS
    await diag.async_stop()
    tracer.stop.assert_called_once_with()
    assert not diag.snapshot()["tracing"]


async def test_trace_waits_for_growth_or_end_and_never_stops_external_tracer(
    rig, tracer, monkeypatch
):
    diag, clock, _, _ = rig
    enable_trace(rig)
    tracer.active = True
    await diag.async_start()
    tracer.start.assert_not_called()
    clock.now = 10
    await diag.async_sample("sample")
    assert tracer.snapshot.call_count == 1
    monkeypatch.setattr(
        sd, "read_memory", lambda: {"rss_bytes": sd.TRACE_GROWTH_BYTES + 100}
    )
    await diag.async_sample("sample")
    assert tracer.snapshot.call_count == 2
    # A sample within the cooldown collects counters without another snapshot.
    await diag.async_sample("sample")
    assert tracer.snapshot.call_count == 2
    await diag.async_stop()
    tracer.stop.assert_not_called()


async def test_trace_marker_symlinks_and_permission_failures_are_safe(
    rig, tracer, monkeypatch
):
    diag, _, _, root = rig
    marker = enable_trace(rig)
    marker.unlink()
    target = root / "target"
    target.touch()
    marker.symlink_to(target)
    await diag.async_start()
    assert target.exists()
    assert not diag.snapshot()["tracing"]
    await diag.async_stop()
    marker.unlink()
    marker.touch()
    monkeypatch.setattr(Path, "unlink", Mock(side_effect=PermissionError))
    await diag.async_start()
    assert not diag.snapshot()["tracing"]
    await diag.async_stop()


async def test_trace_failure_does_not_break_startup(rig, tracer, monkeypatch, caplog):
    diag = rig[0]
    enable_trace(rig)
    monkeypatch.setattr(sd.tracemalloc, "start", Mock(side_effect=RuntimeError))
    await diag.async_start()
    assert diag.active and diag.rows
    assert "Startup allocation tracing unavailable" in caplog.text
    await diag.async_stop()


async def test_failed_sample_preserves_future_sampling(rig, monkeypatch, caplog):
    diag = rig[0]
    await diag.async_start()
    with monkeypatch.context() as patcher:
        patcher.setattr(diag, "_capture", Mock(side_effect=OSError))
        await diag.async_sample("bad")
    await diag.async_sample("recovered")
    assert diag.rows[-1]["event"] == "recovered"
    assert "Startup memory sample unavailable" in caplog.text
    await diag.async_stop()


@pytest.mark.parametrize("during_start", [False, True])
async def test_cancel_drains_executor_before_ending_phase(
    rig, monkeypatch, during_start
):
    diag = rig[0]
    await diag.async_start()
    submitted, release = asyncio.Event(), asyncio.Event()

    async def blocked(fn, *args):
        submitted.set()
        await release.wait()
        return fn(*args)

    monkeypatch.setattr(diag.hass, "async_add_executor_job", blocked)

    async def phase():
        async with diag.phase("cancelled"):
            pytest.fail("Cancellation must precede the phase work")

    task = asyncio.create_task(diag.async_start() if during_start else phase())
    await submitted.wait()
    task.cancel()
    # The still-owned worker is the barrier, no real production wait.
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not diag.phases
    await diag.async_stop()


async def test_stop_waits_for_inflight_sampler(rig, monkeypatch):
    diag = rig[0]
    await diag.async_start()
    entered, release = asyncio.Event(), asyncio.Event()

    async def sample(_event):
        entered.set()
        await release.wait()

    monkeypatch.setattr(diag, "async_sample", sample)
    diag._tick(None)
    await entered.wait()
    stop = asyncio.create_task(diag.async_stop())
    release.set()
    await stop
    assert not diag.active and diag._task is None
