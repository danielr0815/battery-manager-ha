"""Single-writer, atomically published diagnostic chunks, separate from runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import zlib
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HassJob, callback
from homeassistant.helpers.event import async_call_later

from .archive_codec import MAX_CHUNK_FILE_BYTES, ChunkEncoder, compact, decode_chunk

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DISK_BUDGET_BYTES = 64 * 1024 * 1024
# One transaction can retain the previous active chunk until its manifest
# commits. Reserve space explicitly instead of deleting valid evidence early.
RETAINED_BUDGET_BYTES = 48 * 1024 * 1024
EVENT_BUDGET = 100_000
CHUNK_EVENTS = 512
SAVE_DELAY_S = 10
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def archive_directory(hass: HomeAssistant, entry_id: str) -> Path:
    identity = hashlib.sha256(entry_id.encode()).hexdigest()[:32]
    return Path(hass.config.path(".storage", f"battery_manager.archive.{identity}"))


def _atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 69
        or not value.endswith(".bmar")
        or any(char not in "0123456789abcdef" for char in value[:64])
    ):
        raise ValueError("Invalid archive chunk filename")
    return value


class ArchiveStorage:
    """The pending slot coalesces snapshots, never individual event records.

    Every snapshot owns its row list. A newer snapshot includes all retained
    previous rows, so a single pending slot bounds memory without silently
    dropping intervening commands. Only the worker owns codec/cache/files.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.path = archive_directory(hass, entry_id)
        self.generation = 0
        self.last_error: str | None = None
        self._pending: dict | None = None
        self._task: asyncio.Task | None = None
        self._cancel: Callable[[], None] | None = None
        self._closed = False
        self._cache: dict[tuple, dict] = {}
        self._builders: dict[tuple, ChunkEncoder] = {}
        self._on_commit: Callable[[dict], None] | None = None
        self.legacy_backup: dict | None = None

    def schedule(self, snapshot: dict, on_commit=None) -> None:
        if self._closed:
            return
        self._pending, self._on_commit = snapshot, on_commit
        if self._cancel is None and (self._task is None or self._task.done()):
            self._cancel = async_call_later(
                self.hass,
                SAVE_DELAY_S,
                HassJob(self._start, cancel_on_shutdown=True),
            )

    @callback
    def _start(self, _now=None) -> None:
        self._cancel = None
        if not self._closed and self._pending is not None:
            self._task = self.hass.async_create_background_task(
                self._drain(), "battery_manager_archive_writer"
            )

    async def _drain(self) -> None:
        while self._pending is not None:
            snapshot, self._pending = self._pending, None
            job = self.hass.async_add_executor_job(self._write, snapshot)
            try:
                # Unload waits for this task; cancellation cannot detach a
                # filesystem worker and let it overwrite the next coordinator.
                result = await asyncio.shield(job)
            except asyncio.CancelledError:
                await job
                raise
            except Exception as err:
                self.last_error = type(err).__name__
                self._builders.clear()
            else:
                if self.last_error not in {
                    "manifest_recovery",
                    "chunk_recovery",
                    "migration_recovery",
                }:
                    self.last_error = None
                if self._on_commit is not None:
                    self._on_commit(result)

    async def async_flush(self, snapshot: dict, on_commit=None) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        self._pending, self._on_commit = snapshot, on_commit
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_background_task(
                self._drain(), "battery_manager_archive_flush"
            )
        await asyncio.shield(self._task)

    def stop_scheduling(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        self._closed = True

    async def async_load(self) -> dict | None:
        return await self.hass.async_add_executor_job(self._load)

    def _read_manifest(self) -> dict | None:
        for name in ("manifest.json", "manifest.previous.json"):
            path = self.path / name
            if not path.exists():
                continue
            try:
                if path.stat().st_size > MAX_MANIFEST_BYTES:
                    raise ValueError("Oversized archive manifest")
                raw = path.read_bytes()
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("Oversized archive manifest")
                data = json.loads(raw)
                if data.get("schema_version") != 3 or not isinstance(
                    data["segments"], list
                ):
                    raise ValueError("Unsupported archive manifest")
                return data
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                RecursionError,
            ):
                self.last_error = "manifest_recovery"
        return None

    def _load(self) -> dict | None:
        manifest = self._read_manifest()
        if manifest is None:
            backup = self.path / "legacy.json.zlib"
            if backup.exists():
                decoder = zlib.decompressobj()
                raw = decoder.decompress(backup.read_bytes(), 64 * 1024 * 1024 + 1)
                if len(raw) > 64 * 1024 * 1024 or not decoder.eof:
                    raise ValueError("Invalid migration backup")
                self.last_error = "migration_recovery"
                return json.loads(raw)
            return None
        self.generation = int(manifest["generation"])
        segments = []
        for segment in manifest["segments"]:
            history = {key: value for key, value in segment.items() if key != "chunks"}
            history["events"], history["plans"] = [], {}
            for chunk in segment["chunks"]:
                try:
                    name = _name(chunk["name"])
                    if (self.path / name).stat().st_size > MAX_CHUNK_FILE_BYTES:
                        raise ValueError("Oversized archive file")
                    blob = (self.path / name).read_bytes()
                    if hashlib.sha256(blob).hexdigest() != name[:64]:
                        raise ValueError("Archive file checksum mismatch")
                    events, plans = decode_chunk(blob)
                    if len(events) != chunk["count"]:
                        raise ValueError("Archive event count mismatch")
                    history["events"].extend(events)
                    history["plans"].update(plans)
                    identity = (segment["segment_id"], chunk["hour"], chunk["first"])
                    self._cache[identity] = dict(chunk)
                except OSError, ValueError, KeyError, TypeError, IndexError, zlib.error:
                    self.last_error = "chunk_recovery"
                    history["dropped_events"] += int(chunk.get("count", 0))
            history["events"].sort(key=lambda row: row["sequence"])
            segments.append(history)
        return {"schema_version": 2, "segments": segments}

    def _write(self, snapshot: dict) -> dict:
        self.path.mkdir(parents=True, exist_ok=True)
        previous = self.path / "manifest.json"
        if previous.exists():
            try:
                previous_raw = previous.read_bytes()
                previous_data = json.loads(previous_raw)
                valid_previous = previous_data.get(
                    "schema_version"
                ) == 3 and isinstance(previous_data.get("segments"), list)
            except ValueError, TypeError, AttributeError, RecursionError:
                valid_previous = False
            if valid_previous:
                self._bounded_atomic(self.path / "manifest.previous.json", previous_raw)
        self._collect_unreferenced()
        if (
            self.legacy_backup is not None
            and not (self.path / "legacy.json.zlib").exists()
        ):
            self._bounded_atomic(
                self.path / "legacy.json.zlib",
                zlib.compress(compact(self.legacy_backup)),
            )
        segments, used, active = [], set(), set()
        pending_files: dict[str, bytes] = {}
        for segment in snapshot["segments"]:
            groups: dict[str, list[dict]] = defaultdict(list)
            for event in segment["events"]:
                hour = (
                    datetime.fromisoformat(event["at"]).astimezone(UTC).isoformat()[:13]
                )
                groups[hour].append(event)
            metadata = {
                key: value
                for key, value in segment.items()
                if key not in ("events", "plans")
            }
            metadata["chunks"] = []
            for hour, rows in groups.items():
                for offset in range(0, len(rows), CHUNK_EVENTS):
                    events = rows[offset : offset + CHUNK_EVENTS]
                    identity = (segment["segment_id"], hour, events[0]["sequence"])
                    used.add(identity)
                    cached = self._cache.get(identity)
                    if (
                        cached is None
                        or cached["last"] != events[-1]["sequence"]
                        or not (self.path / cached["name"]).exists()
                    ):
                        encoder = self._builders.setdefault(identity, ChunkEncoder())
                        for event in events:
                            if event["plan_id"] is not None:
                                key = event["plan_id"]
                                encoder.append(key, segment["plans"][key])
                        blob = encoder.finish(events)
                        name = hashlib.sha256(blob).hexdigest() + ".bmar"
                        if not (self.path / name).exists():
                            pending_files[name] = blob
                        cached = {
                            "name": name,
                            "bytes": len(blob),
                            "hour": hour,
                            "first": events[0]["sequence"],
                            "last": events[-1]["sequence"],
                            "count": len(events),
                        }
                        self._cache[identity] = cached
                    metadata["chunks"].append(dict(cached))
                    if offset + CHUNK_EVENTS >= len(rows) and hour == max(groups):
                        active.add(identity)
            segments.append(metadata)
        chunks = sorted(
            ((segment, chunk) for segment in segments for chunk in segment["chunks"]),
            key=lambda pair: (pair[1]["hour"], pair[1]["first"]),
        )
        total_bytes = sum(chunk["bytes"] for _, chunk in chunks)
        total_events = sum(chunk["count"] for _, chunk in chunks)
        while chunks and (
            total_bytes > RETAINED_BUDGET_BYTES or total_events > EVENT_BUDGET
        ):
            segment, chunk = chunks.pop(0)
            segment["chunks"].remove(chunk)
            segment["dropped_events"] += chunk["count"]
            total_bytes -= chunk["bytes"]
            total_events -= chunk["count"]
        manifest: dict[str, Any] = {
            "schema_version": 3,
            "generation": self.generation + 1,
            "segments": segments,
        }
        raw = compact(manifest)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("Archive metadata exceeds bound")
        retained_names = {
            chunk["name"] for segment in segments for chunk in segment["chunks"]
        }
        for name, blob in pending_files.items():
            if name in retained_names:
                self._bounded_atomic(self.path / name, blob)
        # A failed transaction keeps the old manifest authoritative. Temporary
        # files and unreferenced chunks are harmless and reclaimed after commit.
        disk_bytes = sum(
            path.stat().st_size for path in self.path.iterdir() if path.is_file()
        )
        if disk_bytes + len(raw) > DISK_BUDGET_BYTES:
            raise ValueError("Archive transaction exceeds disk budget")
        self._bounded_atomic(previous, raw)
        directory = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self.generation = manifest["generation"]
        self.legacy_backup = None
        self._collect_unreferenced()
        self._cache = {key: value for key, value in self._cache.items() if key in used}
        self._builders = {
            key: value for key, value in self._builders.items() if key in active
        }
        return manifest

    def _bounded_atomic(self, path: Path, payload: bytes) -> None:
        # Include the temporary file before writing. Failure must retain the
        # current evidence without growing an unbounded orphan collection.
        size = sum(
            file.stat().st_size for file in self.path.iterdir() if file.is_file()
        )
        if size + len(payload) > DISK_BUDGET_BYTES:
            raise ValueError("Archive transaction exceeds disk budget")
        _atomic(path, payload)

    def _collect_unreferenced(self) -> None:
        protected: set[str] = set()
        for name in ("manifest.json", "manifest.previous.json"):
            path = self.path / name
            if path.exists():
                try:
                    manifest = json.loads(path.read_bytes())
                    for segment in manifest["segments"]:
                        protected.update(
                            _name(chunk["name"]) for chunk in segment["chunks"]
                        )
                except ValueError, KeyError, TypeError, RecursionError:
                    continue
        for path in self.path.iterdir():
            if path.suffix == ".tmp" or (
                path.suffix == ".bmar" and path.name not in protected
            ):
                path.unlink()

    async def async_mark_restored(self) -> None:
        """Retire migration backup only after a verified successful reload."""
        await self.hass.async_add_executor_job(
            lambda: (self.path / "legacy.json.zlib").unlink(missing_ok=True)
        )

    async def async_remove(self) -> None:
        self.stop_scheduling()
        if self._task is not None:
            await asyncio.shield(self._task)
        await self.hass.async_add_executor_job(shutil.rmtree, self.path, True)
