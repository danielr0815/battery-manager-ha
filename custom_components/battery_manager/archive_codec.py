"""Exact, bounded hourly plan deltas; no Home Assistant or filesystem calls."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
import zlib
from copy import deepcopy
from typing import Any

from .operation_history import MAX_PLAN_BYTES, unpack_plan

CHECKPOINT_PLANS = 64
MAX_CHUNK_RAW_BYTES = 64 * 1024 * 1024
MAX_CHUNK_FILE_BYTES = 16 * 1024 * 1024
MAX_PATCH_DEPTH = 128
MAGIC = b"BMAR3\x00"
type Patch = tuple[list[str | int], Any]


def compact(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _changes(before: Any, after: Any, path: list[str | int]) -> list[Patch]:
    if len(path) > MAX_PATCH_DEPTH:
        raise ValueError("Plan nesting exceeds archive bound")
    if type(before) is not type(after):
        return [(path, after)]
    if isinstance(before, dict):
        if list(before) != list(after):
            return [(path, after)]
        return [
            patch
            for key in before
            for patch in _changes(before[key], after[key], [*path, key])
        ]
    if isinstance(before, list):
        if len(before) != len(after):
            return [(path, after)]
        return [
            patch
            for index, (old, new) in enumerate(zip(before, after, strict=True))
            for patch in _changes(old, new, [*path, index])
        ]
    equal = before == after
    if isinstance(before, float):
        equal = equal and math.copysign(1, before) == math.copysign(1, after)
    return [] if equal else [(path, after)]


def _apply(record: Any, patches: list) -> Any:
    if not isinstance(patches, list):
        raise ValueError("Invalid archive patches")
    for path, value in patches:
        if not isinstance(path, list) or len(path) > MAX_PATCH_DEPTH:
            raise ValueError("Invalid archive patch path")
        if not path:
            record = value
            continue
        target = record
        for index, key in enumerate(path):
            if (
                isinstance(target, dict)
                and isinstance(key, str)
                and key in target
                or (
                    isinstance(target, list)
                    and isinstance(key, int)
                    and not isinstance(key, bool)
                    and 0 <= key < len(target)
                )
            ):
                # The validated dict/string or list/integer pairing is safe;
                # type narrowing does not preserve that correlation here.
                container: Any = target
                if index == len(path) - 1:
                    container[key] = value
                    break
                target = container[key]
            else:
                raise ValueError("Invalid archive patch target")
    return record


def _inflate(blob: bytes) -> bytes:
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(blob, MAX_CHUNK_RAW_BYTES + 1)
    except zlib.error as err:
        raise ValueError("Invalid archive compression") from err
    if len(raw) > MAX_CHUNK_RAW_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError("Invalid or oversized archive chunk")
    return raw


class ChunkEncoder:
    """Append only new plans; finishing a copy retains the streaming prefix."""

    def __init__(self) -> None:
        self._stream = zlib.compressobj()
        self._prefix = bytearray()
        self._seen: set[str] = set()
        self._record: dict | None = None
        self._key: str | None = None
        self._count = 0
        self._raw_bytes = 0

    def append(self, key: str, blob: str) -> None:
        if key in self._seen:
            return
        record = unpack_plan(blob)
        if hashlib.sha256(compact(record)).hexdigest() != key:
            raise ValueError("Plan checksum mismatch")
        envelope = (
            {"hash": key, "checkpoint": record}
            if self._count % CHECKPOINT_PLANS == 0
            else {
                "hash": key,
                "base": self._key,
                "replace": _changes(self._record, record, []),
            }
        )
        raw = compact(envelope) + b"\n"
        if self._raw_bytes + len(raw) > MAX_CHUNK_RAW_BYTES:
            raise ValueError("Archive plan chunk exceeds decoded bound")
        self._prefix.extend(self._stream.compress(raw))
        self._raw_bytes += len(raw)
        self._seen.add(key)
        self._record, self._key = record, key
        self._count += 1

    def finish(self, events: list[dict]) -> bytes:
        raw_events = compact(events)
        if len(raw_events) > MAX_CHUNK_RAW_BYTES:
            raise ValueError("Archive event chunk exceeds decoded bound")
        plans = bytes(self._prefix) + self._stream.copy().flush()
        observations = zlib.compress(raw_events)
        blob = (
            MAGIC
            + struct.pack(">II", len(plans), len(observations))
            + plans
            + observations
        )
        if len(blob) > MAX_CHUNK_FILE_BYTES:
            raise ValueError("Archive chunk exceeds disk bound")
        return blob


def decode_chunk(blob: bytes) -> tuple[list[dict], dict[str, str]]:
    """Verify every reconstructed plan; retain the original replay hash."""
    header = len(MAGIC) + 8
    if len(blob) > MAX_CHUNK_FILE_BYTES or not blob.startswith(MAGIC):
        raise ValueError("Unsupported archive chunk")
    if len(blob) < header:
        raise ValueError("Truncated archive header")
    plans_size, events_size = struct.unpack(">II", blob[len(MAGIC) : header])
    if len(blob) != header + plans_size + events_size:
        raise ValueError("Truncated archive chunk")
    plans: dict[str, str] = {}
    previous: dict | None = None
    previous_key = None
    since_checkpoint = 0
    for raw in _inflate(blob[header : header + plans_size]).splitlines():
        envelope = json.loads(raw)
        key = envelope["hash"]
        if "checkpoint" in envelope:
            record = envelope["checkpoint"]
            since_checkpoint = 0
        else:
            since_checkpoint += 1
            if (
                previous is None
                or envelope.get("base") != previous_key
                or since_checkpoint >= CHECKPOINT_PLANS
            ):
                raise ValueError("Missing or distant plan checkpoint")
            record = _apply(deepcopy(previous), envelope["replace"])
        encoded = compact(record)
        if len(encoded) > MAX_PLAN_BYTES or hashlib.sha256(encoded).hexdigest() != key:
            raise ValueError("Reconstructed plan checksum mismatch")
        if key in plans:
            raise ValueError("Duplicate plan in archive chunk")
        plans[key] = base64.b64encode(zlib.compress(encoded)).decode()
        previous, previous_key = record, key
    events = json.loads(_inflate(blob[header + plans_size :]))
    if not isinstance(events, list):
        raise ValueError("Invalid archived events")
    if any(
        row.get("plan_id") is not None and row["plan_id"] not in plans for row in events
    ):
        raise ValueError("Missing event plan")
    return events, plans
