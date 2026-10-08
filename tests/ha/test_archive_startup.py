"""A startup save cannot replace an archive not yet loaded and validated."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from test_operation_recorder import coordinator as coordinator

from custom_components.battery_manager import archive_storage
from custom_components.battery_manager.archive_storage import ArchiveStorage
from custom_components.battery_manager.operation_archive import OperationArchive


async def seed_archive(coordinator):
    history = OperationArchive(coordinator.hass.config.time_zone)
    history.event(datetime(2026, 10, 8, tzinfo=UTC), "before_restart", {"value": 42})
    store = ArchiveStorage(coordinator.hass, coordinator.entry.entry_id)
    await store.async_flush(history.snapshot())
    return store.path / "manifest.json"


async def assert_unready_save_preserves_archive(rec, manifest, before, monkeypatch):
    timer = Mock()
    monkeypatch.setattr(archive_storage, "async_call_later", timer)
    rec.schedule_storage()
    await rec.async_flush()
    timer.assert_not_called()
    assert manifest.read_bytes() == before
    assert not rec.summary()["persistence_ready"]


async def test_save_before_restore_keeps_archive_then_appends_after_adoption(
    coordinator, monkeypatch
):
    rec = coordinator.operation_recorder
    manifest = await seed_archive(coordinator)
    before = manifest.read_bytes()
    await assert_unready_save_preserves_archive(rec, manifest, before, monkeypatch)
    await rec.async_restore(None)
    assert rec.summary()["persistence_ready"]
    rec.history.event(datetime(2026, 10, 8, 1, tzinfo=UTC), "after_restart", {})
    await rec.async_flush()
    stored = await ArchiveStorage(
        coordinator.hass, coordinator.entry.entry_id
    ).async_load()
    assert [e["kind"] for s in stored["segments"] for e in s["events"]] == [
        "before_restart",
        "after_restart",
    ]


@pytest.mark.parametrize("outcome", ["complete", "cancel", "shutdown"])
@pytest.mark.parametrize("phase", ["async_load", "async_mark_restored"])
async def test_pending_restore_cannot_publish_an_empty_archive(
    coordinator, monkeypatch, outcome, phase
):
    rec = coordinator.operation_recorder
    manifest = await seed_archive(coordinator)
    before = manifest.read_bytes()
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(rec.storage, phase)

    async def delayed_load():
        entered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(rec.storage, phase, delayed_load)
    task = asyncio.create_task(rec.async_restore(None))
    try:
        await entered.wait()
        await assert_unready_save_preserves_archive(rec, manifest, before, monkeypatch)
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            coordinator._actuation_shutdown = outcome == "shutdown"
            release.set()
            await task
        if outcome == "complete":
            assert rec.summary()["persistence_ready"]
            assert rec.history.events[0]["kind"] == "before_restart"
        else:
            await assert_unready_save_preserves_archive(
                rec, manifest, before, monkeypatch
            )
    finally:
        release.set()
        if not task.done():
            await task


@pytest.mark.parametrize("failure", ["read", "validate"])
async def test_failed_restore_keeps_durable_archive(coordinator, monkeypatch, failure):
    rec = coordinator.operation_recorder
    manifest = await seed_archive(coordinator)
    before = manifest.read_bytes()
    if failure == "read":
        monkeypatch.setattr(rec.storage, "async_load", AsyncMock(side_effect=OSError))
        await rec.async_restore(None)
    else:
        monkeypatch.setattr(rec.storage, "async_load", AsyncMock(return_value=None))
        await rec.async_restore({"schema_version": -1})
    await assert_unready_save_preserves_archive(rec, manifest, before, monkeypatch)
    assert rec.summary()["last_error"] == (
        "OSError" if failure == "read" else "ValueError"
    )


async def test_fresh_start_allows_persistence_after_absent_archive(coordinator):
    rec = coordinator.operation_recorder
    assert not rec.storage.path.exists()
    await rec.async_restore(None)
    assert rec.summary()["persistence_ready"]
    rec.history.event(datetime(2026, 10, 8, tzinfo=UTC), "first_start", {})
    await rec.async_flush()
    stored = await rec.storage.async_load()
    assert stored["segments"][0]["events"][0]["kind"] == "first_start"
