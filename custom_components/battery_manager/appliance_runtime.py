"""Read-only appliance views fed by one event-loop observation path.

Economic planning can take seconds or fail entirely. Device telemetry and
learning must keep working independently; getters never advance a cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .appliance_learning import duration_hours, measurement
from .const import (
    CONF_APPLIANCE_DETECTION_ENTITY,
    CONF_APPLIANCE_ENERGY_ENTITY,
    CONF_APPLIANCE_OPPORTUNISTIC,
    CONF_APPLIANCE_POWER_ENTITY,
    CONF_APPLIANCE_PROGRAM_ENTITY,
    CONF_APPLIANCE_REMAINING_TIME_ENTITY,
    CONF_APPLIANCE_RUN_DURATION_H,
    CONF_APPLIANCE_RUN_ENERGY_WH,
    CONF_APPLIANCE_SELECTED_PROGRAM_ENTITY,
    CONF_APPLIANCE_TOTAL_TIME_ENTITY,
    SUBENTRY_TYPE_APPLIANCE,
)

if TYPE_CHECKING:
    from datetime import datetime

    from .coordinator import BatteryManagerCoordinator
    from .core.model import ApplianceRun, PlanInputs, PlanResult, SystemConfig

# Minute resolution matches the displayed durations without waking the optimizer.
APPLIANCE_VIEW_INTERVAL = timedelta(minutes=1)
# A fast power sensor must not keep resetting HA Store's ten-second save delay.
APPLIANCE_PERSIST_INTERVAL = timedelta(minutes=1)
SOURCE_KEYS = {
    "detection": CONF_APPLIANCE_DETECTION_ENTITY,
    "power": CONF_APPLIANCE_POWER_ENTITY,
    "energy": CONF_APPLIANCE_ENERGY_ENTITY,
    "active_program": CONF_APPLIANCE_PROGRAM_ENTITY,
    "selected_program": CONF_APPLIANCE_SELECTED_PROGRAM_ENTITY,
    "total_time": CONF_APPLIANCE_TOTAL_TIME_ENTITY,
    "remaining_time": CONF_APPLIANCE_REMAINING_TIME_ENTITY,
}


class PlanningValues(TypedDict):
    energy_wh: float
    energy_source: str
    duration_minutes: float
    duration_source: str


class Observation(TypedDict):
    power_w: float | None
    remaining_minutes: float | None
    remaining_source: str | None
    expected_end: str | None
    cycle_energy_wh: float | None
    energy_source: str | None
    complete: bool
    started_at: str | None


class Recommendation(TypedDict):
    allowed: bool | None
    reasons: list[str]


class ApplianceSnapshot(TypedDict):
    id: str
    name: str
    revision: int
    status: str
    program: str | None
    program_source: str | None
    observation: Observation
    planning: PlanningValues
    learning: dict[str, Any]
    recommendation: Recommendation
    sources: list[dict[str, Any]]


@dataclass(frozen=True)
class PlanningSignature:
    """The appliance assumptions actually tested by an economic plan."""

    program: str | None
    energy_wh: float
    energy_source: str
    duration_minutes: float
    duration_source: str
    running: bool


class ApplianceRuntime:
    """Serialized synchronous observation; independent listeners for device entities."""

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        self.coordinator = coordinator
        self.revision = 0
        self._snapshots: dict[str, ApplianceSnapshot] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._unsub: list[Callable[[], None]] = []
        self._last_observed: datetime | None = None
        self._runs: tuple[ApplianceRun, ...] = ()
        self._plan: dict[str, Recommendation] = {}
        self._plan_until: datetime | None = None
        self._last_persisted: datetime | None = None
        self._learning_edges: tuple = ()
        self._floor_guard = False
        self._plan_signature: dict[str, PlanningSignature] = {}

    def duration(
        self, data: Mapping[str, Any], now: datetime, key: str
    ) -> tuple[float, str]:
        c = self.coordinator
        entity = data.get(CONF_APPLIANCE_TOTAL_TIME_ENTITY)
        total = duration_hours(c.hass.states.get(entity) if entity else None, now)
        program = c._appliance_program(key, data)
        if program is not None and not c._appliance_is_running(
            data, key in c._appliance_started
        ):
            total = None
        if total is not None and total > 0:
            return total, "reported"
        learning = c._appliance_learning
        profile = learning.program_samples.get(key, {}).get(program or "", [])
        return (
            learning.duration(key, float(data[CONF_APPLIANCE_RUN_DURATION_H]), program),
            "program_profile" if profile else "configured",
        )

    def planning_values(
        self, key: str, data: Mapping[str, Any], now: datetime
    ) -> PlanningValues:
        c = self.coordinator
        program = c._appliance_program(key, data)
        learner = c._appliance_learning
        source = (
            "program_profile"
            if learner.program_samples.get(key, {}).get(program or "")
            else "device_profile"
            if learner.samples.get(key)
            else "configured"
        )
        hours, duration_source = self.duration(data, now, key)
        return {
            "energy_wh": learner.energy(
                key, float(data[CONF_APPLIANCE_RUN_ENERGY_WH]), program
            ),
            "energy_source": source,
            "duration_minutes": hours * 60,
            "duration_source": duration_source,
        }

    @callback
    def start(self) -> None:
        if self._unsub:
            return
        c = self.coordinator
        # Also clean up if platform setup fails after listeners were installed.
        c.entry.async_on_unload(self.stop)
        entities = {
            entity
            for sub in c.entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_APPLIANCE
            for key in SOURCE_KEYS.values()
            if (entity := sub.data.get(key))
        }
        if entities:
            self._unsub.append(
                async_track_state_change_event(c.hass, entities, self._changed)
            )
        if any(
            sub.subentry_type == SUBENTRY_TYPE_APPLIANCE
            for sub in c.entry.subentries.values()
        ):
            self._unsub.append(
                async_track_time_interval(c.hass, self._tick, APPLIANCE_VIEW_INTERVAL)
            )
        self.update(dt_util.utcnow())

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        self.update(dt_util.utcnow())

    @callback
    def _tick(self, now: datetime) -> None:
        # Expiry must also clear old advisories on idle devices.
        if self.coordinator._appliance_started:
            self.update(now)
        else:
            self.publish(now)

    @callback
    def stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def update(self, now: datetime) -> tuple[ApplianceRun, ...]:
        # Executor completion can carry an older planning timestamp. Never
        # rewind observations made by source events in the meantime.
        if self._last_observed is None or now >= self._last_observed:
            self._runs = self.coordinator._observe_appliance_runs(now)
            self._last_observed = now
            learner = self.coordinator._appliance_learning
            edges = tuple(
                (
                    key,
                    key in self.coordinator._appliance_started,
                    view["history"][-1]["ended_at"] if view["history"] else None,
                )
                for key, sub in self.coordinator.entry.subentries.items()
                if sub.subentry_type == SUBENTRY_TYPE_APPLIANCE
                for view in (learner.snapshot(key),)
            )
            if edges and (
                self._last_persisted is None
                or edges != self._learning_edges
                or now - self._last_persisted >= APPLIANCE_PERSIST_INTERVAL
            ):
                self.coordinator._save_persistent_state()
                self._last_persisted = now
                self._learning_edges = edges
        self.publish(now)
        return self._runs

    def planning_signature(self) -> dict[str, PlanningSignature]:
        c = self.coordinator
        now = dt_util.utcnow()
        return {
            key: PlanningSignature(
                program=c._appliance_program(key, sub.data),
                **self.planning_values(key, sub.data, now),
                running=key in c._appliance_started,
            )
            for key, sub in c.entry.subentries.items()
            if sub.subentry_type == SUBENTRY_TYPE_APPLIANCE
        }

    def set_floor_guard(self, active: bool) -> None:
        self._floor_guard = active
        self.publish(dt_util.utcnow())

    def plan_updated(
        self,
        result: PlanResult,
        inputs: PlanInputs,
        floor_guard: bool,
        config: SystemConfig,
        signature: dict[str, PlanningSignature],
    ) -> None:
        self._plan_signature = signature
        self._plan = {
            key: {"allowed": advisory.allowed, "reasons": list(advisory.reasons)}
            for key, advisory in result.appliance_advisories.items()
        }
        self._plan_until = (
            dt_util.as_utc(inputs.slots[0].start)
            + timedelta(hours=inputs.slots[0].duration)
            if inputs.slots
            else None
        )
        self._floor_guard = floor_guard
        # A cycle may have finished between the config snapshot and observation.
        for appliance in config.appliances:
            sub = self.coordinator.entry.subentries.get(appliance.appliance_id)
            if sub is not None:
                values = self.planning_values(
                    appliance.appliance_id, sub.data, dt_util.utcnow()
                )
                if (
                    values["energy_wh"] != appliance.run_energy_wh
                    or values["duration_minutes"] != appliance.run_duration_h * 60
                ):
                    self._plan.pop(appliance.appliance_id, None)
        self.publish(dt_util.utcnow())

    def plan_failed(self) -> None:
        self._plan.clear()
        self._plan_until = None
        self.publish(dt_util.utcnow())

    def _recommendation(
        self, key: str, data: Mapping[str, Any], now: datetime
    ) -> Recommendation:
        if not data.get(CONF_APPLIANCE_OPPORTUNISTIC):
            return {"allowed": None, "reasons": ["disabled"]}
        if self._floor_guard:
            return {"allowed": False, "reasons": ["safety_block"]}
        if (
            self._plan_until is None
            or now >= self._plan_until
            or key not in self._plan
            or self._plan_signature.get(key) != self.planning_signature().get(key)
        ):
            return {"allowed": None, "reasons": ["no_valid_plan"]}
        return deepcopy(self._plan[key])

    def _snapshot(self, key: str, now: datetime) -> ApplianceSnapshot:
        c = self.coordinator
        sub = c.entry.subentries[key]
        data = sub.data

        def state(conf: str):
            entity = data.get(conf)
            return c.hass.states.get(entity) if entity else None

        detection = state(CONF_APPLIANCE_DETECTION_ENTITY)
        raw = detection.state.lower() if detection else "unknown"
        running = key in c._appliance_started
        if raw in {"unknown", "unavailable", ""}:
            status = "unknown"
        elif raw in {"error", "fault", "aborted", "cancelled", "failure"}:
            status = "error"
        elif raw in {"pause", "paused", "rinse_hold", "actionrequired"}:
            status = "paused" if running else "unknown"
        elif running:
            status = "running"
        elif raw in {"end", "finished"}:
            status = "finished"
        elif c._appliance_finished(detection) or raw in {
            "initial",
            "delayedstart",
            "reserved",
        }:
            status = "idle"
        else:
            status = "unknown"
        program = c._appliance_program(key, data)
        planning = self.planning_values(key, data, now)
        learned = c._appliance_learning.snapshot(key)
        learning: dict[str, Any] = dict(learned)
        learning["status"] = learning.pop("state")
        device_profile = learned["device_profile"]
        learning["sample_count"] = device_profile["count"] if device_profile else 0
        learning["last_learned_at"] = (
            device_profile["last_learned_at"] if device_profile else None
        )
        learning.pop("measurement", None)
        for profile in learning["profiles"]:
            profile["selected"] = profile["program"] == program
        measured = learned["measurement"]
        learning["reasons"] = list(measured["reasons"]) if measured else []
        learning["warnings"] = list(measured["warnings"]) if measured else []
        if learning["device_profile"] is not None:
            learning["device_profile"]["selected"] = (
                planning["energy_source"] == "device_profile"
            )
        started = c._appliance_started.get(key)
        remaining = None
        remaining_source = None
        if running:
            remaining = duration_hours(
                state(CONF_APPLIANCE_REMAINING_TIME_ENTITY), now, remaining=True
            )
            remaining_source = "reported"
            if remaining is None and started is not None:
                remaining = max(
                    0.0,
                    planning["duration_minutes"] / 60
                    - (now - started).total_seconds() / 3600,
                )
                remaining_source = "estimated"
        expected_end = (
            (dt_util.as_utc(now) + timedelta(hours=remaining)).isoformat()
            if remaining is not None
            else None
        )
        observation: Observation = {
            "power_w": measurement(state(CONF_APPLIANCE_POWER_ENTITY), "power"),
            "remaining_minutes": remaining * 60 if remaining is not None else None,
            "remaining_source": remaining_source,
            "expected_end": expected_end,
            "cycle_energy_wh": measured["energy_wh"] if measured else None,
            "energy_source": measured["measurement_source"] if measured else None,
            "complete": bool(measured and measured["complete"]),
            "started_at": started.isoformat() if started else None,
        }
        sources = []
        for kind, conf in SOURCE_KEYS.items():
            source = state(conf)
            sources.append(
                {
                    "kind": kind,
                    "entity_id": data.get(conf),
                    "state": source.state if source else None,
                    "available": bool(
                        source and source.state not in {"unknown", "unavailable"}
                    ),
                    "last_reported": source.last_reported.isoformat()
                    if source
                    else None,
                }
            )
        return {
            "id": key,
            "name": sub.title,
            "revision": 0,
            "status": status,
            "program": program,
            "program_source": ("active" if running else "selected")
            if program
            else None,
            "observation": observation,
            "planning": planning,
            "learning": learning,
            "recommendation": self._recommendation(key, data, now),
            "sources": sources,
        }

    def publish(self, now: datetime) -> None:
        if self._last_observed is not None:
            now = max(now, self._last_observed)
        updated = {}
        changed = False
        for key, sub in self.coordinator.entry.subentries.items():
            if sub.subentry_type != SUBENTRY_TYPE_APPLIANCE:
                continue
            snapshot = self._snapshot(key, now)
            previous = self._snapshots.get(key)
            snapshot["revision"] = previous["revision"] if previous else 0
            if snapshot != previous:
                snapshot["revision"] += 1
                changed = True
            updated[key] = snapshot
        if changed or updated.keys() != self._snapshots.keys():
            self.revision += 1
            self._snapshots = updated
            for listener in tuple(self._listeners):
                listener()

    def entity_snapshot(self, key: str) -> ApplianceSnapshot | None:
        """Avoid copying whole profiles/history for every sensor property read."""
        snapshot = self._snapshots.get(key)
        if snapshot is None:
            return None
        compact: ApplianceSnapshot = {
            **snapshot,
            "learning": {
                field: value
                for field, value in snapshot["learning"].items()
                if field not in {"profiles", "history", "device_profile"}
            },
        }
        return deepcopy(compact)

    def snapshot(self, key: str) -> ApplianceSnapshot | None:
        return deepcopy(self._snapshots.get(key))

    def payload(self, appliance_ids: list[str] | None = None) -> dict[str, Any]:
        return {
            "entry_id": self.coordinator.entry.entry_id,
            "revision": self.revision,
            "appliances": [
                deepcopy(value)
                for key, value in self._snapshots.items()
                if appliance_ids is None or key in appliance_ids
            ],
        }
