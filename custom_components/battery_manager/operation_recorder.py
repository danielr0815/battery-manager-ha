"""Read-only Home Assistant boundary for the local operating journal."""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DCDC_SWITCH,
    CONF_FEEDIN_SETPOINT_ENTITY,
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
from .operation_history import OperationHistory

_LOGGER = logging.getLogger(__name__)


class OperationRecorder:
    """Collect separate observation events; never request a control replan."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.history = OperationHistory(coordinator.hass.config.time_zone)
        self._cancel = None
        self._saved_at = None
        self.last_error = None

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

    def summary(self):
        return {
            "schema_version": 1,
            "days": deepcopy(list(self.history.daily.values())[-30:]),
            "dropped_events": self.history.dropped,
            "last_error": self.last_error,
            "sources": self._sources(),
            "load_names": {
                lid: entry.title
                for lid, entry in self.coordinator.entry.subentries.items()
                if entry.subentry_type == SUBENTRY_TYPE_LOAD
            },
        }

    def export(self):
        return self.history.export()
