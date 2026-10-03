"""Read-only Home Assistant boundary for the local operating journal."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .archive_storage import ArchiveStorage
from .const import (
    CONF_DCDC_SWITCH,
    CONF_FEEDIN_SETPOINT_ENTITY,
    CONF_INVERTER_BLOCK_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_OUTPUT_SWITCH,
    CONF_LOAD_POWER_ENTITY,
    CONF_LOAD_SOC_ENTITY,
    CONF_SOC_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    OPERATION_POWER_SOURCES,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
)
from .operation_archive import OperationArchive

_LOGGER = logging.getLogger(__name__)


class OperationRecorder:
    """Collect separate observation events; never request a control replan."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.history = OperationArchive(coordinator.hass.config.time_zone)
        self._cancel = None
        self._saved_at = None
        self.last_error = None
        self._decisions: dict[str, tuple] = {}
        self.storage = ArchiveStorage(coordinator.hass, coordinator.entry.entry_id)
        self.legacy_backup = None

    def _safe(self, action, *args):
        try:
            return action(*args)
        except Exception as err:
            self.last_error = type(err).__name__
            _LOGGER.warning("Operating history unavailable: %s", err)
            return None

    def restore(self, data):
        if data is not None:
            self._safe(self.history.restore, data)

    async def async_restore(self, data) -> None:
        """Validate an isolated journal in the worker, then adopt atomically."""
        try:
            stored = await self.storage.async_load()
        except Exception as err:
            self.storage.last_error = type(err).__name__
            stored = None
        if stored is not None:
            data = stored
        elif data is not None:
            # The old runtime Store remains authoritative until the first
            # atomic archive manifest commits, including a migration backup.
            self.legacy_backup = data
            self.storage.legacy_backup = data
        if data is None:
            return
        timezone = self.history.timezone

        def restore():
            history = OperationArchive(timezone)
            history.restore(data)
            return history

        try:
            restored = await self.coordinator.hass.async_add_executor_job(restore)
        except Exception as err:
            self.last_error = type(err).__name__
            _LOGGER.warning("Operating history unavailable: %s", err)
            return
        if not self.coordinator._actuation_shutdown:
            self.history = restored
            if stored is not None and self.storage.last_error is None:
                await self.storage.async_mark_restored()

    def _sources(self):
        c = self.coordinator
        sources = {
            metric: c.raw_config.get(key)
            for key, metric in OPERATION_POWER_SOURCES.items()
        }
        sources["soc"] = c.raw_config.get(CONF_SOC_ENTITY)
        members = {
            lid
            for entry in c.entry.subentries.values()
            if entry.subentry_type == SUBENTRY_TYPE_CASCADE
            for lid in entry.data.get("member_load_ids", [])
        }
        for lid, entry in c.entry.subentries.items():
            if entry.subentry_type == SUBENTRY_TYPE_LOAD:
                role = "cascade_input" if lid in members else "load"
                sources[f"{role}:{lid}"] = entry.data.get(CONF_LOAD_POWER_ENTITY)
                if entity := entry.data.get(CONF_LOAD_SOC_ENTITY):
                    sources[f"soc:{lid}"] = entity
        return sources

    def _actor_ids(self):
        return {
            entity
            for entry in self.coordinator.entry.subentries.values()
            if entry.subentry_type == SUBENTRY_TYPE_LOAD
            for key in (
                CONF_LOAD_CONTROL_SWITCH,
                CONF_LOAD_CHARGE_ENABLE,
                CONF_LOAD_OUTPUT_SWITCH,
            )
            if (entity := entry.data.get(key))
        }

    def start(self):
        self.stop()
        ids = set(self._sources().values()) | self._actor_ids()
        ids.update(
            self.coordinator.raw_config.get(key)
            for key in (
                CONF_SUPPORT_DC24_SWITCH,
                CONF_SUPPORT_DC48_SWITCH,
                CONF_DCDC_SWITCH,
                CONF_FEEDIN_SETPOINT_ENTITY,
                CONF_INVERTER_LIMIT_ENTITY,
                CONF_INVERTER_BLOCK_SWITCH,
            )
        )
        ids.discard(None)
        if ids:
            self._cancel = async_track_state_change_event(
                self.coordinator.hass, ids, self._changed
            )
        self._safe(self.history.break_observation, dt_util.utcnow(), "startup")
        self.sample()

    def stop(self):
        if self._cancel:
            self._cancel()
            self._cancel = None

    def _schedule_save(self):
        now = dt_util.utcnow()
        if self._cancel and (
            self._saved_at is None or now - self._saved_at >= timedelta(seconds=60)
        ):
            self._saved_at = now
            self._safe(self.coordinator._save_persistent_state)

    @callback
    def _changed(self, event):
        data = event.data
        old, new = data.get("old_state"), data.get("new_state")
        self.event(
            "state_changed",
            {
                "entity_id": data["entity_id"],
                "old": old.state if old else None,
                "new": new.state if new else None,
                "reported_at": new.last_reported.isoformat() if new else None,
                "context_id": new.context.id if new else None,
            },
        )
        self.sample()

    def event(self, kind, data):
        number = self._safe(self.history.event, dt_util.utcnow(), kind, data)
        self._schedule_save()
        return number

    def decision(self, source: str, diagnostic: dict) -> None:
        """Record changes, retaining the budget without every five-second tick."""
        keys = ("reason", "limit_w", "planned_limit_w")
        signature = tuple(diagnostic.get(key) for key in keys)
        if self._decisions.get(source) == signature:
            return
        self._decisions[source] = signature
        self.event(
            "controller_decision",
            {
                "source": source,
                **diagnostic,
                "plan_metadata": dict(self.coordinator._plan_metadata),
            },
        )

    def command_context(self) -> dict:
        c = self.coordinator
        return {
            "plan_metadata": dict(c._plan_metadata),
            "floor_guard_active": bool(c._floor_guard_active),
            "live_ac": {
                key: value
                for key, value in c.live_ac.diagnostics.items()
                if key != "curve"
            },
        }

    def sample(self):
        c = self.coordinator
        measurements = {}
        for metric, entity in self._sources().items():
            state = c.hass.states.get(entity) if entity else None
            measurements[metric] = {
                "entity_id": entity,
                "state": state.state if state else None,
                "unit": state.attributes.get("unit_of_measurement") if state else None,
                "reported_at": state.last_reported.isoformat() if state else None,
            }
        actors = {}
        for lid, entry in c.entry.subentries.items():
            if entry.subentry_type != SUBENTRY_TYPE_LOAD:
                continue
            data = entry.data
            ids = [
                data.get(CONF_LOAD_CONTROL_SWITCH),
                data.get(CONF_LOAD_CHARGE_ENABLE),
            ]
            # A terminal without its own plug is supplied by the last output.
            for chain in c.entry.subentries.values():
                members = (
                    chain.data.get("member_load_ids", [])
                    if chain.subentry_type == SUBENTRY_TYPE_CASCADE
                    else []
                )
                if lid in members and members.index(lid) > 0:
                    upstream = c.entry.subentries.get(members[members.index(lid) - 1])
                    if upstream:
                        ids.append(upstream.data.get(CONF_LOAD_OUTPUT_SWITCH))
                if (
                    chain.subentry_type == SUBENTRY_TYPE_CASCADE
                    and chain.data.get("terminal_load_id") == lid
                ):
                    members = chain.data.get("member_load_ids", [])
                    member = c.entry.subentries.get(members[-1]) if members else None
                    if member:
                        ids.append(member.data.get(CONF_LOAD_OUTPUT_SWITCH))
            states = [c.hass.states.get(entity) for entity in ids if entity]
            values = [state.state if state else None for state in states]
            actors[f"load:{lid}"] = (
                False
                if "off" in values
                else True
                if values and all(value == "on" for value in values)
                else None
            )
        self._safe(self.history.sample, dt_util.utcnow(), measurements, actors)
        self._schedule_save()

    def plan(self, config, inputs, result):
        self.sample()  # close the old plan at actual activation time, not forecast time
        key = self._safe(
            self.history.activate_plan,
            dt_util.utcnow(),
            config,
            inputs,
            result,
            self.coordinator.integration_version,
        )
        if key is None:
            # A failed recording cannot attribute the new controller decision
            # to the previously captured plan.
            self._safe(
                self.history.break_observation,
                dt_util.utcnow(),
                "plan_recording_failed",
            )
        self._schedule_save()

    def reserve(self, diagnostic):
        # The linked plan contains the original forecast, bands and factor.
        # Scalar shadow evidence is small enough for the existing bounded log.
        self._safe(
            self.history.event,
            dt_util.utcnow(),
            "reserve_policy",
            {key: value for key, value in diagnostic.items() if key != "curve"},
        )
        self._schedule_save()

    def summary(self):
        return {
            "schema_version": 1,
            "days": deepcopy(self.history.reports()),
            "dropped_events": self.history.dropped_events,
            "last_error": self.last_error or self.storage.last_error,
            "archive_generation": self.storage.generation,
            "sources": self._sources(),
            "retention": self.history.retention(),
            "load_names": {
                lid: entry.title
                for lid, entry in self.coordinator.entry.subentries.items()
                if entry.subentry_type == SUBENTRY_TYPE_LOAD
            },
        }

    def export(self):
        return self.history.export()

    def apply_storage_retention(self, manifest: dict) -> None:
        """Retire only the persisted prefix, preserving newly observed rows."""
        histories = dict(self.history._past)
        histories[self.history.segment_id] = self.history
        self.legacy_backup = None
        for segment in manifest["segments"]:
            history = histories.get(segment["segment_id"])
            if history is None:
                continue
            cutoff = min(
                (chunk["first"] for chunk in segment["chunks"]),
                default=segment["sequence"] + 1,
            )
            while history.events and history.events[0]["sequence"] < cutoff:
                row = history.events.pop(0)
                history._bytes -= len(json.dumps(row, separators=(",", ":"))) + 1
                history.dropped += 1
                history._plan_refs[row["plan_id"]] -= 1
                history._release_plan(row["plan_id"])

    def schedule_storage(self) -> None:
        self.storage.schedule(self.history.snapshot(), self.apply_storage_retention)

    async def async_flush(self) -> None:
        await self.storage.async_flush(
            self.history.snapshot(), self.apply_storage_retention
        )

    async def async_export(self) -> dict:
        snapshot = self.history.snapshot()
        return await self.coordinator.hass.async_add_executor_job(deepcopy, snapshot)
