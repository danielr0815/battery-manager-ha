"""Version-2 journal envelope preserving each recording timezone."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from . import operation_history as journal
from .operation_history import OperationHistory

ARCHIVE_SCHEMA = 2


class OperationArchive(OperationHistory):
    """Current journal plus immutable-timezone predecessors.

    A timezone change opens a new segment. Older daily attribution is never
    relabelled and an observation interval never bridges the change/restart.
    """

    def __init__(self, timezone: str = "UTC") -> None:
        super().__init__(timezone)
        self._past: list[tuple[str, OperationHistory]] = []
        self.segment_id = uuid4().hex

    def export(self) -> dict:
        segments = [
            {**history.export(), "segment_id": identity}
            for identity, history in self._past
        ]
        segments.append({**super().export(), "segment_id": self.segment_id})
        return {"schema_version": ARCHIVE_SCHEMA, "segments": segments}

    def restore(self, data: dict) -> None:
        if not isinstance(data, dict) or data.get("schema_version") not in (
            1,
            ARCHIVE_SCHEMA,
        ):
            raise ValueError("Invalid operation archive")
        segments = (
            data.get("segments")
            if data.get("schema_version") == ARCHIVE_SCHEMA
            else [data]
        )
        if not isinstance(segments, list) or not segments:
            raise ValueError("Archive must contain ordered segments")
        restored = []
        identities: set[str] = set()
        for segment in segments:
            history = OperationHistory(segment["timezone"])
            history.restore(segment)
            identity = (
                segment.get("segment_id")
                or hashlib.sha256(
                    json.dumps(segment, sort_keys=True).encode()
                ).hexdigest()[:32]
            )
            if not isinstance(identity, str) or identity in identities:
                raise ValueError("Invalid archive segment identity")
            identities.add(identity)
            restored.append((identity, history))
        # Commit only after every segment has passed the legacy checksum and
        # structural validation. Matching zones continue the latest segment.
        if restored[-1][1].timezone == self.timezone:
            identity, current = restored.pop()
            super().restore(current.export())
            self.segment_id = identity
        else:
            super().restore(OperationHistory(self.timezone).export())
            self.segment_id = uuid4().hex
        self._past = restored

    def _trim(self, now: datetime) -> None:
        super()._trim(now)
        for _, history in self._past:
            history._trim(now)
        # Detailed event/plan budgets are global, not multiplied by the number
        # of timezone changes. Evict oldest evidence first, keeping reports.
        histories = [history for _, history in self._past] + [self]
        while (
            sum(len(history.events) for history in histories) > journal.MAX_EVENTS
            or sum(history._bytes for history in histories) > journal.MAX_BYTES
        ):
            oldest = next((history for history in histories if history.events), None)
            if oldest is None:
                break
            row = oldest.events.pop(0)
            oldest._bytes -= len(json.dumps(row, separators=(",", ":"))) + 1
            oldest.dropped += 1
            oldest._plan_refs[row["plan_id"]] -= 1
            oldest._release_plan(row["plan_id"])
        self._past = [
            (identity, history)
            for identity, history in self._past
            if history.events or history.daily
        ]
        # Repeated timezone changes must not multiply the daily-report budget.
        # Retire whole oldest segments so retained events and reports keep their
        # replay/checksum relationship instead of pruning only half the evidence.
        while self._past and (
            len(self.daily) + sum(len(history.daily) for _, history in self._past)
            > journal.REPORT_DAYS
        ):
            _, retired = self._past.pop(0)
            self.dropped += retired.dropped + len(retired.events)

    def reports(self) -> list[dict]:
        result: list[dict] = []
        for identity, history in [*self._past, (self.segment_id, self)]:
            result.extend(
                {**report, "segment_id": identity, "timezone": history.timezone}
                for report in history.daily.values()
            )
        return result[-journal.REPORT_DAYS :]

    @property
    def dropped_events(self) -> int:
        return self.dropped + sum(history.dropped for _, history in self._past)
