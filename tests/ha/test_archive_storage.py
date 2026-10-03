"""Lossless deltas, atomic persistence, corruption isolation and worker ownership."""

import asyncio
import base64
import hashlib
import json
import struct
import threading
import zlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.battery_manager import archive_codec, archive_storage
from custom_components.battery_manager.archive_codec import ChunkEncoder, decode_chunk
from custom_components.battery_manager.archive_storage import ArchiveStorage
from custom_components.battery_manager.operation_archive import OperationArchive
from custom_components.battery_manager.operation_history import unpack_plan


def _blob(record):
    raw = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest(), base64.b64encode(
        zlib.compress(raw)
    ).decode()


def _event(sequence, key=None, at=None):
    return {
        "sequence": sequence,
        "at": (at or datetime(2026, 10, 2, 10, tzinfo=UTC)).isoformat(),
        "kind": "test",
        "plan_id": key,
        "data": {"observed": sequence},
    }


def test_exact_delta_types_order_signed_zero_and_checkpoint():
    encoder = ChunkEncoder()
    events, expected = [], {}
    for i in range(70):
        record = {"version": i, "nested": [{"value": i / 3}, 0.0]}
        if i == 1:
            record["nested"][1] = -0.0
        elif i == 2:
            record["nested"][1] = 0
        elif i == 3:
            record["nested"].append("new")
        elif i == 4:
            record = {"nested": record["nested"], "version": i}
        key, blob = _blob(record)
        expected[key] = record
        encoder.append(key, blob)
        encoder.append(key, blob)  # repeated references share one exact plan
        events.append(_event(i + 1, key))
    first = encoder.finish(events[:3])
    payload = encoder.finish(events)
    decoded_events, decoded_plans = decode_chunk(payload)
    assert decoded_events == events
    for key, record in expected.items():
        assert json.dumps(unpack_plan(decoded_plans[key])) == json.dumps(record)
    assert decode_chunk(first)[0] == events[:3]


@pytest.mark.parametrize(
    "payload",
    [b"invalid", archive_codec.MAGIC, b"BMAR3\x00\x00\x00\x00\x10\x00\x00\x00\x01x"],
)
def test_truncated_or_unsupported_chunk(payload):
    with pytest.raises((ValueError, struct.error)):
        decode_chunk(payload)


def test_plan_checksum_and_resource_bounds(monkeypatch):
    key, blob = _blob({"n": 1})
    encoder = ChunkEncoder()
    with pytest.raises(ValueError, match="checksum"):
        encoder.append("wrong", blob)
    monkeypatch.setattr(archive_codec, "MAX_CHUNK_RAW_BYTES", 1)
    with pytest.raises(ValueError, match="decoded bound"):
        encoder.append(key, blob)
    with pytest.raises(ValueError, match="decoded bound"):
        encoder.finish([_event(1)])
    monkeypatch.setattr(archive_codec, "MAX_CHUNK_RAW_BYTES", 1000)
    encoder.append(key, blob)
    monkeypatch.setattr(archive_codec, "MAX_CHUNK_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="disk bound"):
        encoder.finish([_event(1, key)])


def _encoded_envelopes(envelopes, events):
    plans = zlib.compress(b"\n".join(archive_codec.compact(row) for row in envelopes))
    observations = zlib.compress(archive_codec.compact(events))
    return (
        archive_codec.MAGIC
        + struct.pack(">II", len(plans), len(observations))
        + plans
        + observations
    )


@pytest.mark.parametrize(
    "envelopes,events,error",
    [
        ([{"hash": "bad", "base": "missing", "replace": []}], [], "checkpoint"),
        ([{"hash": "bad", "checkpoint": {"n": 1}}], [], "checksum"),
        ([], [_event(1, "missing")], "Missing event plan"),
        ([], {}, "Invalid archived events"),
    ],
)
def test_invalid_plan_dependencies_or_event_shape(envelopes, events, error):
    with pytest.raises(ValueError, match=error):
        decode_chunk(_encoded_envelopes(envelopes, events))


def _history():
    history = OperationArchive()
    at = datetime(2026, 10, 2, 10, tzinfo=UTC)
    history.event(at, "command_requested", {"entity_id": "switch.test"})
    history.event(at + timedelta(hours=1), "command_result", {"success": True})
    return history


async def test_separate_store_roundtrip_incremental_chunks_and_removal(hass):
    history = _history()
    store = ArchiveStorage(hass, "exact")
    await store.async_flush(history.snapshot())
    assert store.last_error is None
    assert store.generation == 1
    assert await store.async_load() == history.export()
    sealed = json.loads((store.path / "manifest.json").read_text())["segments"][0][
        "chunks"
    ][0]["name"]
    history.event(datetime(2026, 10, 2, 11, 1, tzinfo=UTC), "test", {})
    await store.async_flush(history.snapshot())
    assert sealed in {path.name for path in store.path.glob("*.bmar")}
    assert await store.async_load() == history.export()
    await store.async_remove()
    assert not store.path.exists()


async def test_one_corrupt_chunk_preserves_other_hour_and_reports(hass):
    history = _history()
    store = ArchiveStorage(hass, "corrupt")
    await store.async_flush(history.snapshot())
    manifest = json.loads((store.path / "manifest.json").read_text())
    name = manifest["segments"][0]["chunks"][0]["name"]
    (store.path / name).write_bytes(b"truncated")
    restored = await store.async_load()
    assert restored["segments"][0]["events"] == history.events[1:]
    assert restored["segments"][0]["daily"] == history.daily
    assert restored["segments"][0]["dropped_events"] == 1
    assert store.last_error == "chunk_recovery"


async def test_atomic_commit_failure_keeps_previous_manifest_authoritative(
    hass, monkeypatch
):
    history = _history()
    store = ArchiveStorage(hass, "crash")
    await store.async_flush(history.snapshot())
    original = deepcopy(history.export())
    real = archive_storage._atomic

    def crash(path, payload):
        if path.name == "manifest.json":
            raise OSError("crash before commit")
        real(path, payload)

    monkeypatch.setattr(archive_storage, "_atomic", crash)
    history.event(datetime(2026, 10, 2, 11, 1, tzinfo=UTC), "test", {})
    await store.async_flush(history.snapshot())
    assert store.last_error == "OSError"
    assert await store.async_load() == original
    monkeypatch.setattr(archive_storage, "_atomic", real)
    await store.async_flush(history.snapshot())
    assert await store.async_load() == history.export()


async def test_backup_manifest_recovers_invalid_primary(hass):
    history = _history()
    store = ArchiveStorage(hass, "backup")
    await store.async_flush(history.snapshot())
    original = history.export()
    history.event(datetime(2026, 10, 2, 11, 1, tzinfo=UTC), "test", {})
    await store.async_flush(history.snapshot())
    (store.path / "manifest.json").write_text("broken")
    assert await store.async_load() == original
    assert store.last_error == "manifest_recovery"


async def test_migration_backup_survives_until_successful_verified_reload(hass):
    history = _history()
    store = ArchiveStorage(hass, "migration")
    store.legacy_backup = history.export()
    await store.async_flush(history.snapshot())
    assert (store.path / "legacy.json.zlib").exists()
    assert await store.async_load() == history.export()
    await store.async_mark_restored()
    assert not (store.path / "legacy.json.zlib").exists()


async def test_event_budget_retires_whole_old_chunk(hass, monkeypatch):
    monkeypatch.setattr(archive_storage, "EVENT_BUDGET", 1)
    history = _history()
    store = ArchiveStorage(hass, "retention")
    await store.async_flush(history.snapshot())
    restored = await store.async_load()
    assert restored["segments"][0]["events"] == history.events[1:]
    assert restored["segments"][0]["dropped_events"] == 1
    assert restored["segments"][0]["daily"] == history.daily


async def test_worker_io_does_not_block_eventloop_and_pending_snapshot_keeps_all_rows(
    hass, monkeypatch
):
    store = ArchiveStorage(hass, "worker")
    entered, release = threading.Event(), threading.Event()
    real_write = store._write

    def controlled(snapshot):
        entered.set()
        assert release.wait(5), "test did not release the worker"
        return real_write(snapshot)

    monkeypatch.setattr(store, "_write", controlled)
    cancel = Mock()
    monkeypatch.setattr(archive_storage, "async_call_later", Mock(return_value=cancel))
    history = _history()
    store.schedule(history.snapshot())
    call = archive_storage.async_call_later.call_args
    assert call.args[:2] == (hass, 10)
    assert call.args[2].target == store._start
    assert call.args[2].cancel_on_shutdown
    store._start()
    while not entered.is_set():
        await asyncio.sleep(0)
    heartbeat = asyncio.Event()
    hass.loop.call_soon(heartbeat.set)
    await heartbeat.wait()
    for i in range(10):
        history.event(datetime(2026, 10, 2, 11, 2, tzinfo=UTC), "test", {"i": i})
        store.schedule(history.snapshot())
    release.set()
    await store._task
    assert await store.async_load() == history.export()
    store.stop_scheduling()
    store.schedule(history.snapshot())
    assert store._pending is None


@pytest.mark.parametrize("path", ["invalid", [0] * 129, ["absent"], ["nested", -1]])
def test_corrupt_delta_cannot_create_keys_or_address_negative_indexes(path):
    key, _ = _blob({"nested": [1]})
    payload = _encoded_envelopes(
        [
            {"hash": key, "checkpoint": {"nested": [1]}},
            {"hash": "invalid", "base": key, "replace": [[path, 2]]},
        ],
        [],
    )
    with pytest.raises(ValueError, match="patch"):
        decode_chunk(payload)


def test_replaced_root_duplicate_and_invalid_compression():
    old_key, _ = _blob({"n": 1})
    new_key, _ = _blob({"different": True})
    envelopes = [
        {"hash": old_key, "checkpoint": {"n": 1}},
        {"hash": new_key, "base": old_key, "replace": [[[], {"different": True}]]},
    ]
    assert unpack_plan(decode_chunk(_encoded_envelopes(envelopes, []))[1][new_key]) == {
        "different": True
    }
    with pytest.raises(ValueError, match="Duplicate"):
        decode_chunk(_encoded_envelopes(envelopes + [envelopes[0]], []))
    with pytest.raises(ValueError, match="patches"):
        archive_codec._apply({}, None)
    for compressed in (
        b"broken",
        zlib.compress(b"[]") + b"trailing",
        zlib.compress(b"[]")[:-1],
    ):
        with pytest.raises(ValueError, match="archive"):
            archive_codec._inflate(compressed)


def test_deep_delta_and_decoding_resource_caps(monkeypatch):
    before, after = 1, 2
    for _ in range(130):
        before, after = {"n": before}, {"n": after}
    with pytest.raises(ValueError, match="nesting"):
        archive_codec._changes(before, after, [])
    key, _ = _blob({"n": 1})
    payload = _encoded_envelopes([{"hash": key, "checkpoint": {"n": 1}}], [])
    monkeypatch.setattr(archive_codec, "MAX_CHUNK_RAW_BYTES", 1)
    with pytest.raises(ValueError, match="oversized"):
        decode_chunk(payload)
    monkeypatch.setattr(archive_codec, "MAX_CHUNK_RAW_BYTES", 1000)
    monkeypatch.setattr(archive_codec, "MAX_PLAN_BYTES", 1)
    with pytest.raises(ValueError, match="checksum"):
        decode_chunk(payload)


async def test_cancelled_writer_is_drained_before_replacement(hass, monkeypatch):
    store = ArchiveStorage(hass, "cancel")
    entered, release = threading.Event(), threading.Event()
    original = store._write

    def controlled(snapshot):
        entered.set()
        assert release.wait(5)
        return original(snapshot)

    monkeypatch.setattr(store, "_write", controlled)
    flush = asyncio.create_task(store.async_flush(_history().snapshot()))
    while not entered.is_set():
        await asyncio.sleep(0)
    store._task.cancel()
    await asyncio.sleep(0)
    assert not store._task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await flush
    assert await store.async_load() == _history().export() or store.generation == 1
    store.stop_scheduling()


@pytest.mark.parametrize("budget", ["DISK_BUDGET_BYTES", "MAX_MANIFEST_BYTES"])
async def test_capacity_failure_retains_committed_evidence(hass, monkeypatch, budget):
    history = _history()
    store = ArchiveStorage(hass, "budget")
    await store.async_flush(history.snapshot())
    committed = (store.path / "manifest.json").read_bytes()
    history.event(datetime(2026, 10, 2, 11, 1, tzinfo=UTC), "test", {})
    monkeypatch.setattr(archive_storage, budget, 1)
    await store.async_flush(history.snapshot())
    assert store.last_error == "ValueError"
    assert (store.path / "manifest.json").read_bytes() == committed


async def test_recovery_warning_survives_new_commit_and_backup_is_preserved(hass):
    history = _history()
    store = ArchiveStorage(hass, "backup-preserve")
    await store.async_flush(history.snapshot())
    await store.async_flush(history.snapshot())
    previous = (store.path / "manifest.previous.json").read_bytes()
    (store.path / "manifest.json").write_bytes(b"broken")
    await store.async_load()
    await store.async_flush(history.snapshot())
    assert store.last_error == "manifest_recovery"
    assert (store.path / "manifest.previous.json").read_bytes() == previous


@pytest.mark.parametrize(
    "damage", ["filename", "oversized_chunk", "oversized_manifest", "count"]
)
async def test_invalid_metadata_and_file_bounds_are_isolated(hass, monkeypatch, damage):
    history = _history()
    store = ArchiveStorage(hass, "bounds")
    await store.async_flush(history.snapshot())
    path = store.path / "manifest.json"
    manifest = json.loads(path.read_bytes())
    chunk = manifest["segments"][0]["chunks"][0]
    if damage == "filename":
        chunk["name"] = "../outside.bmar"
    elif damage == "count":
        chunk["count"] = 99
    elif damage == "oversized_chunk":
        monkeypatch.setattr(archive_storage, "MAX_CHUNK_FILE_BYTES", 1)
    else:
        monkeypatch.setattr(archive_storage, "MAX_MANIFEST_BYTES", 1)
    path.write_text(json.dumps(manifest))
    restored = await store.async_load()
    if damage == "oversized_manifest":
        assert restored is None and store.last_error == "manifest_recovery"
    else:
        assert store.last_error == "chunk_recovery"
        assert restored["segments"][0]["daily"] == history.daily


async def test_legacy_backup_recovery_without_a_committed_manifest(hass):
    store = ArchiveStorage(hass, "legacy-recovery")
    store.path.mkdir(parents=True)
    history = _history()
    backup = store.path / "legacy.json.zlib"
    backup.write_bytes(zlib.compress(archive_codec.compact(history.export())))
    assert await store.async_load() == history.export()
    assert store.last_error == "migration_recovery"
    backup.write_bytes(zlib.compress(b"{}")[:-1])
    with pytest.raises(ValueError, match="migration"):
        await store.async_load()


@pytest.mark.parametrize("invalid", [b"null", b"[]", b"2", b"[" * 1100 + b"]" * 1100])
async def test_malformed_manifest_shape_and_nesting_recover_previous_generation(
    hass, invalid
):
    history = _history()
    store = ArchiveStorage(hass, "manifest-shape")
    await store.async_flush(history.snapshot())
    await store.async_flush(history.snapshot())
    (store.path / "manifest.json").write_bytes(invalid)
    assert await store.async_load() == history.export()
    assert store.last_error == "manifest_recovery"
    await store.async_flush(history.snapshot())
    assert await store.async_load() == history.export()


async def test_disk_bound_is_checked_before_creating_transaction_files(
    hass, monkeypatch
):
    history = _history()
    store = ArchiveStorage(hass, "absolute-budget")
    await store.async_flush(history.snapshot())
    initial = sum(file.stat().st_size for file in store.path.iterdir())
    monkeypatch.setattr(archive_storage, "DISK_BUDGET_BYTES", initial + 1)
    committed = (store.path / "manifest.json").read_bytes()
    history.event(datetime(2026, 10, 2, 11, 1, tzinfo=UTC), "test", {})
    await store.async_flush(history.snapshot())
    assert store.last_error == "ValueError"
    assert sum(file.stat().st_size for file in store.path.iterdir()) <= initial + 1
    assert (store.path / "manifest.json").read_bytes() == committed
