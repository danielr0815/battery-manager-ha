"""Load commands and physical confirmation, independent of planner success."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from math import isfinite
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ACTOR_CONFIRM_TIMEOUT_S,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CHARGING_FEEDBACK_ENTITY,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_INPUT_POWER_ENTITY,
    CONF_LOAD_MIN_RUNTIME_MIN,
    CONF_LOAD_OUTPUT_POWER_ENTITY,
    CONF_LOAD_OUTPUT_SWITCH,
    INPUT_OFF_POLICY_ALWAYS,
    INPUT_OFF_POLICY_AUTO,
    INPUT_OFF_POLICY_KEEP,
    SUBENTRY_TYPE_LOAD,
)
from .core.model import PlanInputs, SocCoordinateSnapshot, SystemConfig

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

_LOGGER = logging.getLogger(__name__)

# A service acknowledgement is not device feedback. Match the existing cascade
# grace period without holding the coordinator's shared safety lock while waiting.
LOAD_CONFIRM_TIMEOUT_S = ACTOR_CONFIRM_TIMEOUT_S
# Bound repeat requests to devices that report a known opposite state.
LOAD_RETRY_INTERVAL_S = 60


@dataclass(frozen=True)
class LoadAction:
    load_id: str
    data: dict[str, Any]
    activate: bool
    plug_was_on: bool
    run_h: float = 0.0
    flicker_eligible: bool = False
    reason: str = "plan change"
    bypass_guards: bool = False
    control_generation: int | None = None
    soc_coordinate: SocCoordinateSnapshot | None = None
    coordinate_captured: bool = False
    coordinate_cleanup: int | None = None
    pv_plan: tuple[SystemConfig, PlanInputs] | None = None

    @classmethod
    def from_legacy(cls, action: LoadAction | tuple[Any, ...]) -> LoadAction:
        """Accept old internal callers during the coordinator extraction."""
        if isinstance(action, LoadAction):
            return action
        return cls(*action)


@dataclass
class ActorRequest:
    desired: bool
    requested_at: datetime
    state: str = "pending"
    owner: str | None = None
    sent: bool = False
    command: LoadAction | None = None
    action_generation: int | None = None
    booked_edge: datetime | None = None
    requires_pv_revalidation: bool = False
    context_id: str | None = None


@dataclass(frozen=True)
class LoadStartBinding:
    plug: str
    charge_enable: str | None
    output_switch: str | None
    input_off_policy: str


@dataclass(frozen=True)
class RevokedLoadStart:
    actors: tuple[str, ...]
    control_generation: int
    revision: int
    requested_at: datetime
    binding: LoadStartBinding
    observed_on: bool = False
    contexts: tuple[tuple[str, str], ...] = ()
    observed: tuple[tuple[str, str | None], ...] = ()


class OnOrigin(Enum):
    CURRENT = "current"
    CONFIRMED = "confirmed"
    REVOKED = "revoked"
    AMBIGUOUS = "ambiguous"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConfirmedLoadOn:
    load_id: str
    binding: LoadStartBinding
    generation: int
    request_context: str


@dataclass(frozen=True)
class LoadOnEdge:
    changed: datetime
    context_id: str | None
    parent_id: str | None
    user: bool
    confirmed: ConfirmedLoadOn | None = None

    def links(self, context_id: str | None) -> bool:
        return context_id is not None and context_id in (
            self.context_id,
            self.parent_id,
        )


class CleanupBindingState(TypedDict):
    plug: str
    charge_enable: str | None
    output_switch: str | None
    input_off_policy: str


class CleanupItemState(TypedDict):
    sent: bool
    actors: list[str]
    control_generation: int
    revision: int
    requested_at: str
    observed_on: bool
    binding: CleanupBindingState
    contexts: dict[str, str]
    origins: list[tuple[str, str]]
    observed: list[tuple[str, str | None]]
    exact_observations: bool


class CoordinateCleanupState(TypedDict):
    version: int
    items: dict[str, CleanupItemState]


def _cleanup_binding(data: Any) -> LoadStartBinding | None:
    plug = data.get(CONF_LOAD_CONTROL_SWITCH)
    gate = data.get(CONF_LOAD_CHARGE_ENABLE)
    output = data.get(CONF_LOAD_OUTPUT_SWITCH)
    policy = data.get(CONF_LOAD_INPUT_OFF_POLICY, INPUT_OFF_POLICY_AUTO)
    if (
        not isinstance(plug, str)
        or not plug
        or gate is not None
        and not isinstance(gate, str)
        or output is not None
        and not isinstance(output, str)
        or policy
        not in (INPUT_OFF_POLICY_AUTO, INPUT_OFF_POLICY_ALWAYS, INPUT_OFF_POLICY_KEEP)
    ):
        return None
    return LoadStartBinding(plug, gate, output, policy)


def _cleanup_marker_current(
    c: BatteryManagerCoordinator, load_id: str, marker: RevokedLoadStart
) -> bool:
    sub = c.entry.subentries.get(load_id)
    return (
        sub is not None
        and sub.subentry_type == SUBENTRY_TYPE_LOAD
        and c.load_bm_enabled(load_id)
        and load_id not in c.cascade_manager.managed_load_ids()
        and c._load_control_generation.get(load_id, 0) == marker.control_generation
        and _cleanup_binding(sub.data) == marker.binding
    )


def export_load_booking(
    c: BatteryManagerCoordinator, request: ActorRequest
) -> dict[str, Any] | None:
    """Persist the cap for a sent ON that has not physically confirmed yet."""
    command = request.command
    if (
        command is None
        or not request.sent
        or not request.desired
        or request.state not in ("pending", "confirmation_failed")
        or not command.activate
        or command.bypass_guards
        or not isfinite(command.run_h)
        or command.run_h <= 0
        or request.action_generation
        != c._load_action_generation.get(command.load_id, 0)
        or command.control_generation
        != c._load_control_generation.get(command.load_id, 0)
        or (binding := _cleanup_binding(command.data)) is None
    ):
        return None
    return {
        "run_h": command.run_h,
        "binding": asdict(binding),
        "min_runtime": command.data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30),
        "control_generation": command.control_generation,
        "action_generation": request.action_generation,
        "coordinate": asdict(command.soc_coordinate)
        if command.soc_coordinate is not None
        else None,
        "requires_pv_revalidation": command.pv_plan is not None
        or request.requires_pv_revalidation,
    }


def restore_load_booking(
    c: BatteryManagerCoordinator, entity: str, request: ActorRequest, raw: Any
) -> bool:
    """Restore an outstanding cap only to its original generation and wiring."""
    if (
        not isinstance(raw, dict)
        or not request.desired
        or not request.sent
        or request.state not in ("pending", "confirmation_failed")
        or not isinstance(request.owner, str)
        or not request.owner.startswith("load:")
    ):
        return False
    load_id = request.owner.removeprefix("load:")
    sub = c.entry.subentries.get(load_id)
    run_h = raw.get("run_h")
    action_generation = raw.get("action_generation")
    generation = raw.get("control_generation")
    if (
        sub is None
        or sub.subentry_type != SUBENTRY_TYPE_LOAD
        or not c.load_bm_enabled(load_id)
        or (binding := _cleanup_binding(sub.data)) is None
        or raw.get("binding") != asdict(binding)
        or entity not in (binding.plug, binding.charge_enable)
        or raw.get("min_runtime") != sub.data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)
        or type(generation) is not int
        or generation != c._load_control_generation.get(load_id, 0)
        or type(action_generation) is not int
        or action_generation <= 0
        or c._load_action_generation.get(load_id, action_generation)
        != action_generation
        or not isinstance(run_h, (float, int))
        or isinstance(run_h, bool)
        or not isfinite(run_h)
        or run_h <= 0
        or type(raw.get("requires_pv_revalidation")) is not bool
    ):
        return False
    coordinate = raw.get("coordinate")
    try:
        snapshot = (
            SocCoordinateSnapshot(**coordinate) if coordinate is not None else None
        )
    except TypeError, ValueError:
        return False
    c._load_action_generation[load_id] = action_generation
    request.action_generation = action_generation
    request.requires_pv_revalidation = raw["requires_pv_revalidation"]
    request.command = LoadAction(
        load_id,
        dict(sub.data),
        True,
        False,
        run_h=float(run_h),
        control_generation=generation,
        soc_coordinate=snapshot,
        coordinate_captured=True,
    )
    return True


def export_coordinate_cleanup(c: BatteryManagerCoordinator) -> CoordinateCleanupState:
    """Persist only our sent, still-owned intent; bare legacy ON is no evidence."""
    items: dict[str, CleanupItemState] = {}
    for load_id, marker in c.live_ac.revoked_load_starts.items():
        if not _cleanup_marker_current(c, load_id, marker):
            continue
        binding: CleanupBindingState = {
            "plug": marker.binding.plug,
            "charge_enable": marker.binding.charge_enable,
            "output_switch": marker.binding.output_switch,
            "input_off_policy": marker.binding.input_off_policy,
        }
        items[load_id] = {
            "sent": True,
            "actors": list(marker.actors),
            "control_generation": marker.control_generation,
            "revision": marker.revision,
            "requested_at": marker.requested_at.isoformat(),
            "observed_on": marker.observed_on,
            "binding": binding,
            "contexts": dict(marker.contexts),
            "origins": list(marker.contexts),
            "observed": list(marker.observed),
            "exact_observations": True,
        }
    return {"version": 1, "items": items}


def restore_coordinate_cleanup(c: BatteryManagerCoordinator, raw: Any) -> None:
    """Restore cleanup authority after manual generations/config are restored.

    No actor writes and no dwell are reconstructed. Physical ON is handled by
    the regular feedback/stop path once the coordinator has finished loading.
    """
    restored: dict[str, RevokedLoadStart] = {}
    if (
        not isinstance(raw, dict)
        or type(raw.get("version")) is not int
        or raw["version"] != 1
        or not isinstance(raw.get("items"), dict)
    ):
        c.live_ac.revoked_load_starts = restored
        return
    for load_id, item in raw["items"].items():
        if (
            not isinstance(load_id, str)
            or load_id not in c.entry.subentries
            or not isinstance(item, dict)
            or item.get("sent") is not True
        ):
            continue
        generation, revision = item.get("control_generation"), item.get("revision")
        actors, observed = item.get("actors"), item.get("observed_on")
        contexts = item.get("contexts", {})
        binding_raw = item.get("binding")
        if (
            type(generation) is not int
            or generation < 0
            or type(revision) is not int
            or revision < 0
            or type(observed) is not bool
            or not isinstance(actors, list)
            or not 1 <= len(actors) <= 2
            or not all(isinstance(actor, str) and actor for actor in actors)
            or len(set(actors)) != len(actors)
            or not isinstance(binding_raw, dict)
            or not isinstance(contexts, dict)
            or any(
                actor not in actors or not isinstance(ctx, str) or not ctx
                for actor, ctx in contexts.items()
            )
        ):
            continue
        binding = _cleanup_binding(
            {
                CONF_LOAD_CONTROL_SWITCH: binding_raw.get("plug"),
                CONF_LOAD_CHARGE_ENABLE: binding_raw.get("charge_enable"),
                CONF_LOAD_OUTPUT_SWITCH: binding_raw.get("output_switch"),
                CONF_LOAD_INPUT_OFF_POLICY: binding_raw.get("input_off_policy"),
            }
        )
        if binding is None or not set(actors).issubset(
            {binding.plug, binding.charge_enable}
        ):
            continue
        try:
            stamp = datetime.fromisoformat(item.get("requested_at", ""))
        except TypeError, ValueError:
            continue
        if stamp.tzinfo is None:
            continue
        try:
            origins = _restore_origins(
                item.get("origins", list(contexts.items())), actors
            )
            seen = _restore_origins(
                item.get("observed", []), actors, accept_anonymous=True
            )
        except ValueError:
            continue
        # Before this flag, a neutral ON could be recorded as the oldest
        # named origin. That observation still protects, but consumes no ID.
        if item.get("exact_observations") is not True:
            seen = tuple(dict.fromkeys((actor, None) for actor, _ in seen))
        marker = RevokedLoadStart(
            tuple(actors),
            generation,
            revision,
            stamp,
            binding,
            observed,
            tuple(
                (actor, context) for actor, context in origins if context is not None
            ),
            seen,
        )
        if not _cleanup_marker_current(c, load_id, marker):
            continue
        restored[load_id] = marker
        switched = c._last_load_switch.get(load_id)
        # A dormant obligation for A can coexist with a properly booked B.
        active_proof = _has_confirmed_load_on(c, load_id, binding)
        if not active_proof and (
            marker.observed_on or switched is None or switched <= stamp
        ):
            c._load_charging_active[load_id] = False
            c._load_run_deadline.pop(load_id, None)
            c._cancel_off_timer(load_id)
            if switched == stamp:
                c._last_load_switch.pop(load_id, None)
            since = c._load_input_on_since.get(load_id)
            if binding.plug in marker.actors and since is not None and since == stamp:
                c._load_input_on_since.pop(load_id, None)
        from .load_safety import effective_start_power_w

        power = effective_start_power_w(c, load_id, c.entry.subentries[load_id].data)
        if power is not None:
            c.live_ac.note_start_power(
                "load:" + load_id,
                power,
                input_entity=c.entry.subentries[load_id].data.get(
                    CONF_LOAD_INPUT_POWER_ENTITY
                ),
            )
    c.live_ac.revoked_load_starts = restored


_CURRENT_LOAD_ACTION: ContextVar[LoadAction | None] = ContextVar(
    "current_load_action", default=None
)


def export_load_on_edges(c: BatteryManagerCoordinator) -> dict[str, Any]:
    def payload(
        load_id: str, binding: LoadStartBinding, generation: int, edge: LoadOnEdge
    ) -> dict[str, Any]:
        return {
            "load_id": load_id,
            "binding": asdict(binding),
            "generation": generation,
            "changed": edge.changed.isoformat(),
            "context": edge.context_id,
            "parent": edge.parent_id,
            "user": edge.user,
            "confirmed_context": edge.confirmed.request_context
            if edge.confirmed
            else None,
        }

    items = {}
    for actor, edge in c._load_on_edges.items():
        proof = edge.confirmed
        if proof is not None:
            # Keep enough original scope to invalidate its associated booking
            # if the device switched while the entry was being reloaded.
            items[actor] = payload(proof.load_id, proof.binding, proof.generation, edge)
    for load_id, sub in c.entry.subentries.items():
        if sub.subentry_type != SUBENTRY_TYPE_LOAD or not c.load_bm_enabled(load_id):
            continue
        binding = _cleanup_binding(sub.data)
        if binding is None:
            continue
        for bound_actor in (binding.plug, binding.charge_enable):
            if (
                bound_actor is None
                or bound_actor in items
                or (active_edge := c._load_on_edges.get(bound_actor)) is None
            ):
                continue
            live = c.hass.states.get(bound_actor)
            if (
                live is not None
                and live.state == "on"
                and live.last_changed == active_edge.changed
            ):
                items[bound_actor] = payload(
                    load_id,
                    binding,
                    c._load_control_generation.get(load_id, 0),
                    active_edge,
                )
    return items


def restore_load_on_edges(c: BatteryManagerCoordinator, raw: Any) -> None:
    c._load_on_edges.clear()
    if not isinstance(raw, dict):
        return
    for actor, item in raw.items():
        if not isinstance(item, dict) or not isinstance(
            load_id := item.get("load_id"), str
        ):
            continue
        proof_context = item.get("confirmed_context")
        context, parent = item.get("context"), item.get("parent")
        generation = item.get("generation")
        current_generation = c._load_control_generation.get(load_id, 0)
        sub = c.entry.subentries.get(load_id)
        binding = _cleanup_binding(sub.data) if sub is not None else None
        try:
            stamp = datetime.fromisoformat(item.get("changed", ""))
        except TypeError, ValueError:
            stamp = None
        applicable = (
            sub is not None
            and sub.subentry_type == SUBENTRY_TYPE_LOAD
            and c.load_bm_enabled(load_id)
            and binding is not None
            and item.get("binding") == asdict(binding)
            and actor in (binding.plug, binding.charge_enable)
            and type(generation) is int
            and generation == current_generation
            and stamp is not None
            and stamp.tzinfo is not None
            and type(item.get("user")) is bool
            and all(
                value is None or isinstance(value, str) and value
                for value in (context, parent, proof_context)
            )
        )
        if applicable and binding is not None and stamp is not None:
            edge = LoadOnEdge(stamp, context, parent, item["user"])
            live = c.hass.states.get(actor)
            if (
                live is not None
                and live.state == "on"
                and live.last_changed == stamp
                and (live.last_updated != stamp or _edge_from_state(live) == edge)
            ):
                if proof_context is not None and not edge.user:
                    edge = replace(
                        edge,
                        confirmed=ConfirmedLoadOn(
                            load_id, binding, current_generation, proof_context
                        ),
                    )
                c._load_on_edges[actor] = edge
                continue
        # A known previous confirmed run cannot attach to a different wire
        # or to a new physical edge at startup. Legacy records had no proof.
        if isinstance(proof_context, str) and proof_context:
            c._load_charging_active[load_id] = False
            c._load_run_deadline.pop(load_id, None)
            c._cancel_off_timer(load_id)
            if c._load_plug_owned_entities.get(load_id) == actor:
                _release_load_input(c, load_id)


def _has_confirmed_load_on(
    c: BatteryManagerCoordinator, load_id: str, binding: LoadStartBinding
) -> bool:
    for actor in (binding.plug, binding.charge_enable):
        if (
            actor is not None
            and (edge := _reported_on(c, actor)) is not None
            and _confirmed_edge_current(c, load_id, actor, edge)
        ):
            return True
    return False


def end_interrupted_load_run(
    c: BatteryManagerCoordinator,
    load_id: str,
    data: Any,
    off_edge: State | None = None,
) -> bool:
    """Retire the booking when either charging-path actor leaves its run."""
    if load_id not in c._load_run_deadline and not c._load_charging_active.get(load_id):
        return False
    started = c._last_load_switch.get(load_id)
    plug = data.get(CONF_LOAD_CONTROL_SWITCH)
    for actor in (plug, data.get(CONF_LOAD_CHARGE_ENABLE)):
        if not actor:
            continue
        if (
            off_edge is not None
            and off_edge.entity_id == actor
            and (started is None or off_edge.last_changed >= started)
        ):
            break
        state = c.hass.states.get(actor)
        if state is not None and state.state == "off":
            break
        # Legacy storage might have only a deadline. A genuinely newer
        # foreign ON replaces the run even if this process missed its OFF.
        # An existing external passthrough input may precede a valid run.
        edge = _reported_on(c, actor)
        if (
            edge is not None
            and (started is None or edge.changed > started)
            and _on_origin(c, load_id, actor, edge)[0] is OnOrigin.FOREIGN
        ):
            break
    else:
        return False
    if actor == plug:
        _release_load_input(c, load_id)
    c._load_charging_active[load_id] = False
    c._load_run_deadline.pop(load_id, None)
    c._cancel_off_timer(load_id)
    return True


def restore_confirmed_load_runs(c: BatteryManagerCoordinator) -> None:
    for load_id, deadline in tuple(c._load_run_deadline.items()):
        sub = c.entry.subentries.get(load_id)
        if sub is None or end_interrupted_load_run(c, load_id, sub.data):
            continue
        if (
            (binding := _cleanup_binding(sub.data)) is not None
            and _has_confirmed_load_on(c, load_id, binding)
            and c._charging_is_active(sub.data) is True
        ):
            c._load_charging_active[load_id] = True
            if deadline > dt_util.now():
                c._arm_off_timer(load_id, deadline)


def _restore_origins(
    raw: Any,
    actors: list[str],
    *,
    accept_anonymous: bool = False,
) -> tuple[tuple[str, str | None], ...]:
    if not isinstance(raw, list):
        raise ValueError("invalid ON origin list")
    origins = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("invalid ON origin")
        entity, context = item
        if entity not in actors or (
            not (isinstance(context, str) and context)
            and not (accept_anonymous and context is None)
        ):
            raise ValueError("invalid ON origin fields")
        origins.append((entity, context))
    return tuple(dict.fromkeys(origins))


def _edge_from_state(state: State) -> LoadOnEdge:
    context = state.context
    return LoadOnEdge(
        state.last_changed, context.id, context.parent_id, context.user_id is not None
    )


def _reported_on(
    c: BatteryManagerCoordinator, entity: str, event_state: State | None = None
) -> LoadOnEdge | None:
    """Read the physical edge, never the context of an ON-to-ON update."""
    current = c.hass.states.get(entity)
    edges = getattr(c, "_load_on_edges", {})
    if current is None or current.state != "on":
        edges.pop(entity, None)
        return None
    if (
        event_state is not None
        and event_state.entity_id == entity
        and event_state.last_changed == current.last_changed
    ):
        report = _edge_from_state(event_state)
        previous = edges.get(entity)
        if previous is None or replace(previous, confirmed=None) != report:
            edges[entity] = report
    edge = edges.get(entity)
    if (
        edge is not None
        and edge.changed == current.last_changed
        and (
            current.last_changed != current.last_updated
            or replace(edge, confirmed=None) == _edge_from_state(current)
        )
    ):
        return edge
    if current.last_changed != current.last_updated:
        return None
    edge = edges[entity] = _edge_from_state(current)
    return edge


def _confirmed_edge_current(
    c: BatteryManagerCoordinator, load_id: str, entity: str, edge: LoadOnEdge
) -> bool:
    proof = edge.confirmed
    sub = c.entry.subentries.get(load_id)
    return (
        proof is not None
        and proof.load_id == load_id
        and sub is not None
        and c.load_bm_enabled(load_id)
        and sub.subentry_type == SUBENTRY_TYPE_LOAD
        and load_id not in c.cascade_manager.managed_load_ids()
        and proof.generation == c._load_control_generation.get(load_id, 0)
        and proof.binding == _cleanup_binding(sub.data)
        and entity in (proof.binding.plug, proof.binding.charge_enable)
    )


def _on_origin(
    c: BatteryManagerCoordinator,
    load_id: str,
    entity: str,
    edge: LoadOnEdge,
) -> tuple[OnOrigin, str | None]:
    marker = getattr(getattr(c, "live_ac", None), "revoked_load_starts", {}).get(
        load_id
    )
    outstanding = (
        marker is not None
        and entity in marker.actors
        and _cleanup_marker_current(c, load_id, marker)
        and marker.requested_at <= edge.changed
    )
    origins = (
        tuple(context for actor, context in marker.contexts if actor == entity)
        if marker and outstanding
        else ()
    )
    request = c._load_actor_requests.get(entity)
    active = (
        request is not None
        and request.owner == "load:" + load_id
        and request.desired
        and request.state != "coordinate_changed"
        and request.requested_at <= edge.changed
    )
    if edge.user:
        return OnOrigin.FOREIGN, None
    if (
        request is not None
        and active
        and request.sent
        and edge.links(request.context_id)
    ):
        return OnOrigin.CURRENT, None
    if match := next((context for context in origins if edge.links(context)), None):
        return OnOrigin.REVOKED, match
    if _confirmed_edge_current(c, load_id, entity, edge):
        if (
            request is not None
            and active
            and edge.confirmed is not None
            and request.context_id == edge.confirmed.request_context
        ):
            return OnOrigin.CURRENT, None
        return OnOrigin.CONFIRMED, None
    if edge.parent_id is not None:
        return OnOrigin.FOREIGN, None
    # A neutral device report cannot distinguish pending generations, so
    # give outstanding protection priority over a new runtime booking.
    if outstanding:
        return OnOrigin.AMBIGUOUS, None
    if request is not None and active and request.state != "service_failed":
        return OnOrigin.CURRENT, None
    return OnOrigin.UNKNOWN, None


def _release_load_input(c: BatteryManagerCoordinator, load_id: str) -> None:
    c._load_plug_owned[load_id] = False
    c._load_plug_owned_entities.pop(load_id, None)
    c._load_input_on_since.pop(load_id, None)


def reject_foreign_load_on(
    c: BatteryManagerCoordinator, entity: str, event_state: State | None = None
) -> bool:
    request = c._load_actor_requests.get(entity)
    if (
        request is None
        or not request.desired
        or not request.owner
        or not request.owner.startswith("load:")
    ):
        return False
    edge = _reported_on(c, entity, event_state)
    load_id = request.owner.removeprefix("load:")
    if (
        edge is None
        or edge.changed < request.requested_at
        or _on_origin(c, load_id, entity, edge)[0] is not OnOrigin.FOREIGN
    ):
        return False
    remember_displaced_start(c, entity, request)
    c._load_actor_requests.pop(entity, None)
    sub = c.entry.subentries.get(load_id)
    if sub is not None and sub.data.get(CONF_LOAD_CONTROL_SWITCH) == entity:
        _release_load_input(c, load_id)
    c._save_persistent_state()
    return True


def confirms_load_on(
    c: BatteryManagerCoordinator, entity: str, request: ActorRequest
) -> bool:
    if request.owner is None or not request.owner.startswith("load:"):
        return True
    edge = _reported_on(c, entity)
    load_id = request.owner.removeprefix("load:")
    if edge is None or _on_origin(c, load_id, entity, edge)[0] is not OnOrigin.CURRENT:
        return False
    sub = c.entry.subentries.get(load_id)
    if (
        request.sent
        and request.context_id is not None
        and sub is not None
        and (binding := _cleanup_binding(sub.data)) is not None
    ):
        c._load_on_edges[entity] = replace(
            edge,
            confirmed=ConfirmedLoadOn(
                load_id,
                binding,
                c._load_control_generation.get(load_id, 0),
                request.context_id,
            ),
        )
    return True


def coordinate_cleanup_pending(
    c: BatteryManagerCoordinator, load_id: str, event_state: State | None = None
) -> bool:
    """Reconcile only revoked attempts that caused the current ON edge."""
    marker = getattr(getattr(c, "live_ac", None), "revoked_load_starts", {}).get(
        load_id
    )
    if marker is None:
        return False
    if not _cleanup_marker_current(c, load_id, marker):
        c.live_ac.revoked_load_starts.pop(load_id, None)
        return False
    actors = set(marker.actors)
    contexts = set(marker.contexts)
    observed = set(marker.observed)
    own_cleanup = False
    for entity in marker.actors:
        if c._entity_tristate(entity) is False:
            _reported_on(c, entity)
            seen = {item for item in observed if item[0] == entity}
            contexts.difference_update(seen)
            observed.difference_update(seen)
            if (seen or marker.observed_on and not marker.observed) and not any(
                actor == entity for actor, _ in contexts
            ):
                actors.discard(entity)
            continue
        edge = _reported_on(c, entity, event_state)
        if edge is None:
            continue
        origin, context = _on_origin(c, load_id, entity, edge)
        if origin is OnOrigin.FOREIGN and entity == marker.binding.plug:
            _release_load_input(c, load_id)
        if origin not in (OnOrigin.REVOKED, OnOrigin.AMBIGUOUS):
            continue
        own_cleanup = True
        observed.add((entity, context))
        if entity == marker.binding.plug:
            c._load_plug_owned[load_id] = True
            c._load_plug_owned_entities[load_id] = entity
        c._load_charging_active[load_id] = False
        if c._load_run_deadline.pop(load_id, None) is not None:
            c._cancel_off_timer(load_id)
    if (
        actors != set(marker.actors)
        or contexts != set(marker.contexts)
        or observed != set(marker.observed)
    ):
        if actors:
            c.live_ac.revoked_load_starts[load_id] = replace(
                marker,
                actors=tuple(actor for actor in marker.actors if actor in actors),
                contexts=tuple(item for item in marker.contexts if item in contexts),
                observed_on=bool(observed),
                observed=tuple(
                    (actor, context)
                    for actor in marker.actors
                    for a, context in observed
                    if a == actor
                ),
            )
        else:
            c.live_ac.revoked_load_starts.pop(load_id, None)
        c._save_persistent_state()
    return own_cleanup


def _schedule_coordinate_cleanup(c: BatteryManagerCoordinator) -> None:
    if (
        c._actuation_shutdown
        or c.live_ac.coordinate_cleanup_scheduled
        or c.live_ac.coordinate_cleanup_task is not None
        and not c.live_ac.coordinate_cleanup_task.done()
    ):
        return

    async def cleanup() -> None:
        from .load_safety import stop_unsafe_loads

        try:
            await asyncio.gather(*await stop_unsafe_loads(c), return_exceptions=True)
        finally:
            c.live_ac.coordinate_cleanup_scheduled = False

    # HA starts entry tasks eagerly, before async_create_background_task returns.
    # Claim scheduling ownership before the cleanup can re-enter feedback.
    c.live_ac.coordinate_cleanup_scheduled = True
    c.live_ac.coordinate_cleanup_task = c.entry.async_create_background_task(
        c.hass, cleanup(), name="battery_manager_revoked_start_cleanup"
    )


def bind_coordinate_request(
    c: BatteryManagerCoordinator, request: ActorRequest
) -> None:
    """Keep origin outside task context, so listeners can reject stale feedback."""
    request.command = _CURRENT_LOAD_ACTION.get()
    if request.command is not None:
        request.action_generation = c._load_action_generation.get(
            request.command.load_id, 0
        )


def revoke_guarded_starts(c: BatteryManagerCoordinator) -> None:
    """Fence outstanding starts even when their actuator still reports OFF."""
    revoked: set[str] = set()
    for entity, request in c._load_actor_requests.items():
        command = request.command
        if (
            command is None
            or not request.desired
            or not request.sent
            or command.bypass_guards
            or request.state in ("confirmed", "coordinate_changed", "service_failed")
            or request.action_generation
            != c._load_action_generation.get(command.load_id, 0)
        ):
            continue
        request.state = "coordinate_changed"
        coordinate_request_revoked(c, entity, request, schedule=False)
        revoked.add(command.load_id)
    for load_id in revoked:
        c._load_action_generation[load_id] += 1
    if revoked:
        c._save_persistent_state()


def _revoke_stale_feedback(
    c: BatteryManagerCoordinator, entity: str, request: ActorRequest | None
) -> None:
    if (
        request is not None
        and request.desired
        and request.sent
        and request.state != "confirmed"
        and request.command is not None
        and (
            request.requires_pv_revalidation
            or not c.voltage_gate.coordinate_matches(request.command.soc_coordinate)
            or request.command.pv_plan is not None
            and not _coordinate_action_allowed(c, request.command)
        )
    ):
        request.state = "coordinate_changed"
        coordinate_request_revoked(c, entity, request)


def coordinate_request_revoked(
    c: BatteryManagerCoordinator,
    entity_id: str,
    request: ActorRequest,
    *,
    schedule: bool = True,
) -> None:
    """Register a request that may become physically ON after its grant expired."""
    command = request.command or _CURRENT_LOAD_ACTION.get()
    if (
        command is None
        or not request.sent
        or not command.activate
        or command.bypass_guards
        or request.owner != "load:" + command.load_id
        or not c.load_bm_enabled(command.load_id)
    ):
        return
    generation = c._load_control_generation.get(command.load_id, 0)
    if (
        command.control_generation is not None
        and command.control_generation != generation
    ):
        return  # Manual control was handed over while this service awaited.
    if (
        request.action_generation is not None
        and request.action_generation
        != c._load_action_generation.get(command.load_id, 0)
    ):
        return  # A newer stop or start now owns the action.
    if entity_id not in (
        command.data.get(CONF_LOAD_CONTROL_SWITCH),
        command.data.get(CONF_LOAD_CHARGE_ENABLE),
    ):
        return
    _record_revoked_attempt(c, command.load_id, entity_id, request)
    if schedule and coordinate_cleanup_pending(c, command.load_id):
        _schedule_coordinate_cleanup(c)


def remember_displaced_start(
    c: BatteryManagerCoordinator,
    entity: str,
    request: ActorRequest | None,
) -> None:
    """Retain a sent ON until its physical result or a true ownership change."""
    if (
        request is not None
        and request.owner is not None
        and request.owner.startswith("load:")
        and request.desired
        and request.sent
        and request.state != "confirmed"
    ):
        _record_revoked_attempt(c, request.owner.removeprefix("load:"), entity, request)


def _record_revoked_attempt(
    c: BatteryManagerCoordinator,
    load_id: str,
    entity: str,
    request: ActorRequest,
) -> None:
    sub = c.entry.subentries.get(load_id)
    if (
        sub is None
        or not c.load_bm_enabled(load_id)
        or (binding := _cleanup_binding(sub.data)) is None
        or entity not in (binding.plug, binding.charge_enable)
        or request.command is not None
        and (
            _cleanup_binding(request.command.data) != binding
            or request.command.control_generation
            != c._load_control_generation.get(load_id, 0)
            or request.command.bypass_guards
        )
    ):
        return
    previous = c.live_ac.revoked_load_starts.get(load_id)
    if previous is not None and not _cleanup_marker_current(c, load_id, previous):
        previous = None
    if previous is None:
        previous = RevokedLoadStart(
            (),
            c._load_control_generation.get(load_id, 0),
            c._load_action_generation.get(load_id, 0),
            request.requested_at,
            binding,
        )
    contexts = previous.contexts
    if request.context_id is not None:
        contexts = tuple(dict.fromkeys((*contexts, (entity, request.context_id))))
    marker = replace(
        previous,
        actors=tuple(dict.fromkeys((*previous.actors, entity))),
        contexts=contexts,
        requested_at=min(previous.requested_at, request.requested_at),
    )
    if marker != previous:
        c.live_ac.revoked_load_starts[load_id] = marker
        c._save_persistent_state()


async def confirm_state(hass: HomeAssistant, entity_id: str, desired: bool) -> bool:
    """Wait for reported state; subscribe before checking to avoid lost events."""
    target = "on" if desired else "off"
    confirmed = asyncio.Event()

    @callback
    def changed(_event: Any) -> None:
        if hass.states.is_state(entity_id, target):
            confirmed.set()

    unsubscribe = async_track_state_change_event(hass, [entity_id], changed)
    try:
        changed(None)
        async with asyncio.timeout(LOAD_CONFIRM_TIMEOUT_S):
            await confirmed.wait()
        return hass.states.is_state(entity_id, target)
    except TimeoutError:
        return False
    finally:
        unsubscribe()


def input_decision(c: BatteryManagerCoordinator, load_id: str) -> str:
    """Classify the current input obligation using reported output state only.

    Zero output watts cannot prove an OFF relay: a powered output can begin
    serving its appliance at any time, including between two observations.
    """
    sub = c.entry.subentries.get(load_id)
    if sub is None:
        return "foreign"
    data = sub.data
    plug = data.get(CONF_LOAD_CONTROL_SWITCH)
    if not plug:
        return "off"
    if c._entity_tristate(plug) is False:
        return "off"
    if c._charging_is_active(data) is True:
        return "charging"
    policy = data.get(CONF_LOAD_INPUT_OFF_POLICY, INPUT_OFF_POLICY_AUTO)
    if policy == INPUT_OFF_POLICY_KEEP:
        return "held_policy"
    if policy == INPUT_OFF_POLICY_AUTO:
        if not c._load_plug_owned.get(load_id, False):
            return "foreign"
        output = data.get(CONF_LOAD_OUTPUT_SWITCH)
        if output:
            state = c._entity_tristate(output)
            if state is True:
                return "held_passthrough"
            if state is None:
                return "held_unknown"
    return "pending_off"


def input_off_allowed(c: BatteryManagerCoordinator, load_id: str) -> bool:
    """Final live authorization; no waiting or cached action data."""
    sub = c.entry.subentries.get(load_id)
    if sub is None or load_id in c.cascade_manager.managed_load_ids():
        return False
    data = sub.data
    gate = data.get(CONF_LOAD_CHARGE_ENABLE)
    policy = data.get(CONF_LOAD_INPUT_OFF_POLICY, INPUT_OFF_POLICY_AUTO)
    if gate and c._entity_tristate(gate) is not False:
        return False
    if policy == INPUT_OFF_POLICY_KEEP:
        return False
    if policy == INPUT_OFF_POLICY_ALWAYS or not gate:
        # Legacy output mappings may lack a charge gate. The input is then
        # the only physical stop actuator; passthrough cannot override a
        # charge/safety stop. New configurations reject this combination.
        return True
    output = data.get(CONF_LOAD_OUTPUT_SWITCH)
    if output:
        if not c._load_plug_owned.get(load_id, False):
            return False
        if c._entity_tristate(output) is not False:
            return False
    if gate and input_decision(c, load_id) not in ("pending_off", "off"):
        return False
    gate_request = c._load_actor_requests.get(gate) if isinstance(gate, str) else None
    if (
        gate_request is not None
        and gate_request.desired
        and gate_request.state != "service_failed"
    ):
        return False
    if data.get(CONF_LOAD_OUTPUT_SWITCH):
        since = c._load_input_on_since.get(load_id)
        if since is not None and dt_util.now() < since + timedelta(
            minutes=data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)
        ):
            return False
    return True


def input_diagnostics(c: BatteryManagerCoordinator, load_id: str) -> dict[str, Any]:
    sub = c.entry.subentries.get(load_id)
    if sub is None:
        return {}
    data = sub.data
    decision = input_decision(c, load_id)

    def state(key: str) -> bool | None:
        entity = data.get(key)
        return c._entity_tristate(entity) if entity else None

    def power(key: str) -> float | None:
        entity = data.get(key)
        return c._read_power_w(entity) if entity else None

    return {
        "input_control": {
            "input_on": state(CONF_LOAD_CONTROL_SWITCH),
            "charge_enable": state(CONF_LOAD_CHARGE_ENABLE),
            "charging": state(CONF_LOAD_CHARGING_FEEDBACK_ENTITY),
            "output_on": state(CONF_LOAD_OUTPUT_SWITCH),
            "output_power_w": power(CONF_LOAD_OUTPUT_POWER_ENTITY),
            "owned": c._load_plug_owned.get(load_id, False),
            "reason": decision,
            "paused_input_power_w": power(CONF_LOAD_INPUT_POWER_ENTITY)
            if c._charging_is_active(data) is False
            else None,
        }
    }


def reconcile_feedback(
    coordinator: BatteryManagerCoordinator, event: Any = None
) -> None:
    """Reconcile state and retain OFF evidence across queued OFF/ON events."""
    from .load_safety import LOAD_GRID_IMPORT_TOLERANCE_W, grid_import_w

    changed = False
    event_entity = event.data.get("entity_id") if event is not None else None
    event_state = event.data.get("new_state") if event is not None else None
    event_old = event.data.get("old_state") if event is not None else None
    on_edge = (
        event_state
        if event_state is not None
        and event_state.state == "on"
        and (event_old is None or event_old.state != "on")
        else None
    )
    off_edge = (
        event_state
        if event_state is not None
        and event_state.state == "off"
        and (event_old is None or event_old.state != "off")
        else None
    )
    if on_edge is not None and on_edge.entity_id in coordinator._load_actor_requests:
        _reported_on(coordinator, on_edge.entity_id, on_edge)
    for entity in tuple(coordinator._load_actor_requests):
        reject_foreign_load_on(coordinator, entity)
    imported = grid_import_w(coordinator)
    if imported is not None and imported > LOAD_GRID_IMPORT_TOLERANCE_W:
        revoke_guarded_starts(coordinator)
    interrupted: set[str] = set()
    settled: dict[str, ActorRequest] = {}
    managed = coordinator.cascade_manager.managed_load_ids()
    for load_id, sub in coordinator.entry.subentries.items():
        if (
            sub.subentry_type != SUBENTRY_TYPE_LOAD
            or load_id in managed
            or not coordinator.load_bm_enabled(load_id)
        ):
            continue
        plug = sub.data.get(CONF_LOAD_CONTROL_SWITCH)
        if not isinstance(plug, str) or not plug:
            continue
        was_active = coordinator._load_charging_active.get(load_id) is True
        if end_interrupted_load_run(coordinator, load_id, sub.data, off_edge):
            changed = True
            if was_active:
                interrupted.add(load_id)
        state = coordinator.hass.states.get(plug)
        request = coordinator._load_actor_requests.get(plug)
        _revoke_stale_feedback(coordinator, plug, request)
        if coordinate_cleanup_pending(coordinator, load_id, on_edge):
            _schedule_coordinate_cleanup(coordinator)
        if event_entity == plug and off_edge is not None:
            # Reading hass.states alone loses a fast OFF→ON: callbacks can
            # both run after the new ON has already become current state.
            # Revoke only the ownership/request that predates this edge, so
            # an old queued OFF cannot cancel a newer independent BM start.
            since = coordinator._load_input_on_since.get(load_id)
            if since is None or since <= off_edge.last_changed:
                coordinator._load_plug_owned[load_id] = False
                coordinator._load_plug_owned_entities.pop(load_id, None)
                coordinator._load_input_on_since.pop(load_id, None)
                changed = True
            if (
                request is not None
                and request.desired
                and request.requested_at <= off_edge.last_changed
            ):
                remember_displaced_start(coordinator, plug, request)
                coordinator._load_actor_requests.pop(plug, None)
                request = None
                changed = True
        binding = coordinator._load_plug_owned_entities.get(load_id)
        if binding and binding != plug:
            coordinator._load_plug_owned[load_id] = False
            coordinator._load_plug_owned_entities.pop(load_id, None)
            coordinator._load_input_on_since.pop(load_id, None)
            changed = True
        if state is not None and state.state == "off":
            if coordinator._load_plug_owned.get(load_id, False):
                coordinator._load_plug_owned[load_id] = False
                coordinator._load_plug_owned_entities.pop(load_id, None)
                coordinator._load_input_on_since.pop(load_id, None)
                changed = True
            if (
                request
                and request.desired
                and state.last_changed > request.requested_at
            ):
                remember_displaced_start(coordinator, plug, request)
                coordinator._load_actor_requests.pop(plug, None)
                request = None
        if (
            request
            and request.owner == "load:" + load_id
            and request.desired
            and request.state
            not in ("confirmed", "service_failed", "coordinate_changed")
            and state is not None
            and state.state == "on"
            and confirms_load_on(coordinator, plug, request)
        ):
            coordinator._load_plug_owned[load_id] = True
            coordinator._load_plug_owned_entities[load_id] = plug
            coordinator._load_input_on_since.setdefault(load_id, state.last_changed)
            changed = True
    for entity, request in tuple(coordinator._load_actor_requests.items()):
        _revoke_stale_feedback(coordinator, entity, request)
        if request.state in (
            "confirmed",
            "coordinate_changed",
        ) or not coordinator.hass.states.is_state(
            entity, "on" if request.desired else "off"
        ):
            continue
        if request.desired and not confirms_load_on(coordinator, entity, request):
            continue
        request.state = "confirmed"
        settled[entity] = request
        changed = True
    for load_id, sub in coordinator.entry.subentries.items():
        if (
            sub.subentry_type != SUBENTRY_TYPE_LOAD
            or load_id in managed
            or not coordinator.load_bm_enabled(load_id)
        ):
            continue
        if coordinate_cleanup_pending(coordinator, load_id):
            continue
        path = settled.keys() & set(
            (
                sub.data.get(CONF_LOAD_CONTROL_SWITCH),
                sub.data.get(CONF_LOAD_CHARGE_ENABLE),
            )
        )
        active = coordinator._charging_is_active(sub.data)
        if (
            not path
            or active is None
            or (
                coordinator._load_charging_active.get(load_id) is active
                and (active or load_id not in interrupted)
            )
        ):
            continue
        # Late device feedback starts a dwell at the actual edge. An input
        # cleanup behind an already-OFF gate leaves the charge clock intact.
        coordinator._load_charging_active[load_id] = active
        edge = max(
            state.last_changed
            for entity in path
            if (state := coordinator.hass.states.get(entity)) is not None
        )
        coordinator._last_load_switch[load_id] = edge
        if not active:
            coordinator._load_run_deadline.pop(load_id, None)
            coordinator._cancel_off_timer(load_id)
            continue
        for entity in path:
            request = settled[entity]
            command = request.command
            if (
                request.sent
                and command is not None
                and command.activate
                and request.owner == "load:" + load_id
                and request.action_generation
                == coordinator._load_action_generation.get(load_id, 0)
                and command.control_generation
                == coordinator._load_control_generation.get(load_id, 0)
                and command.data == dict(sub.data)
                and request.requested_at <= edge
            ):
                coordinator._load_last_off.pop(load_id, None)
                _set_run_deadline(coordinator, command, edge)
                request.booked_edge = edge
                break
    if changed:
        coordinator._save_persistent_state()
    publish_input_diagnostics(coordinator)


def publish_input_diagnostics(c: BatteryManagerCoordinator) -> None:
    """Publish physical telemetry even when no forecast can be planned."""
    changed = False
    if not c.data:
        return
    for load_id, plan in (c.data.get("load_plans") or {}).items():
        current = input_diagnostics(c, load_id)
        if plan.get("input_control") != current.get("input_control"):
            plan.update(current)
            changed = True
    if changed:
        c.async_update_listeners()


# One queued action owns its coordinate across service/confirmation awaits.
_COORDINATE_PERMISSION: ContextVar[Callable[[], bool] | None] = ContextVar(
    "load_coordinate_permission", default=None
)


def coordinate_request_current(c: BatteryManagerCoordinator) -> bool:
    """Final service boundary hook; unbound manual/protective calls retain authority."""
    permission = _COORDINATE_PERMISSION.get()
    return permission is None or permission()


def _set_run_deadline(
    c: BatteryManagerCoordinator, command: LoadAction, edge: datetime
) -> None:
    """Freeze a booked run from its confirmed charging edge."""
    if command.run_h <= 0.0:
        c._load_run_deadline.pop(command.load_id, None)
        c._cancel_off_timer(command.load_id)
        return
    # Dwell is the floor of this time cap. The level-driven target SOC may
    # still stop the last charging quantum before the cap.
    minutes = max(
        int(command.data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)),
        round(command.run_h * 60.0),
    )
    deadline = edge + timedelta(minutes=minutes)
    c._load_run_deadline[command.load_id] = deadline
    c._arm_off_timer(command.load_id, deadline)


def _start_awaits_retry(c: BatteryManagerCoordinator, command: LoadAction) -> bool:
    """Keep the first booking while the same ON awaits a chance to retry."""
    for entity in (
        command.data.get(CONF_LOAD_CONTROL_SWITCH),
        command.data.get(CONF_LOAD_CHARGE_ENABLE),
    ):
        if (
            entity
            and (request := c._load_actor_requests.get(entity)) is not None
            and request.desired
            and request.owner == "load:" + command.load_id
            and request.state != "confirmed"
            and (
                (dt_util.now() - request.requested_at).total_seconds()
                < LOAD_RETRY_INTERVAL_S
                or c._entity_tristate(entity) is None
            )
        ):
            return True
    return False


def _coordinate_action_allowed(
    c: BatteryManagerCoordinator, command: LoadAction
) -> bool:
    if not command.activate or command.bypass_guards:
        return True
    gate = getattr(c, "voltage_gate", None)
    if gate is None:
        return True  # Isolated legacy actor adapters have no SOC observer.
    from .load_safety import effective_start_power_w

    power = effective_start_power_w(c, command.load_id, command.data)
    owner = "load:" + command.load_id
    input_entity = command.data.get(CONF_LOAD_INPUT_POWER_ENTITY)
    if gate.coordinate_permission_valid(command.soc_coordinate) and (
        command.pv_plan is None or c.pv_plan_evidence_valid(*command.pv_plan)
    ):
        if power is not None:
            c.live_ac.note_start_power(owner, power, input_entity=input_entity)
        return True
    if not gate.coordinate_permission_valid(
        command.soc_coordinate, direct_pv_proven=True
    ):
        return False
    return power is not None and c.live_ac.reserve_pv_start(
        owner,
        power,
        input_entity=input_entity,
        exclude_load_id=command.load_id,
        config=command.pv_plan[0] if command.pv_plan is not None else None,
    )


async def execute_load_switching(
    self: BatteryManagerCoordinator,
    actions: Sequence[LoadAction | tuple[Any, ...]],
    now: datetime | None = None,
) -> None:
    """Capture no new coordinate here: queued provenance belongs to its plan."""
    if len(actions) != 1:
        await _execute_load_switching(self, actions, now)
        return
    command = LoadAction.from_legacy(actions[0])
    action_token = _CURRENT_LOAD_ACTION.set(command)
    token = _COORDINATE_PERMISSION.set(
        lambda: _coordinate_action_allowed(self, command)
    )
    try:
        await _execute_load_switching(self, (command,), now)
    finally:
        _COORDINATE_PERMISSION.reset(token)
        _CURRENT_LOAD_ACTION.reset(action_token)


async def _execute_load_switching(
    self: BatteryManagerCoordinator,
    actions: Sequence[LoadAction | tuple[Any, ...]],
    now: datetime | None = None,
) -> None:
    # Local import avoids a cycle: load_safety builds typed LoadAction requests.
    from .load_safety import start_blocked

    reconcile_feedback(self)
    if now is None:
        now = dt_util.now()
    if len(actions) > 1:
        await asyncio.gather(
            *(self._execute_load_switching([action], now) for action in actions)
        )
        return
    # Preserve the final queue barrier, but never hold the shared safety
    # lock while waiting for device feedback.
    queued_generations = dict(self._load_control_generation)
    async with self._switch_lock:
        pass
    # F-CASCADE-STORAGE live incident 2026-09-01: a generic action
    # queued before the cascade pass repeatedly switched B1's Root
    # input OFF while CascadeManager switched it back ON.  Filtering
    # while the action list is built is insufficient because ownership
    # can change before this detached task acquires the actor lock.
    # Re-check at the final mutation boundary so a cascade always has
    # exactly one actor owner.
    cascade_managed = self.cascade_manager.managed_load_ids()
    for action in actions:
        command = LoadAction.from_legacy(action)
        subentry_id, data = command.load_id, command.data
        if command.coordinate_cleanup is not None:
            pending = self.live_ac.revoked_load_starts.get(subentry_id)
            if (
                pending is None
                or pending.revision != command.coordinate_cleanup
                or not coordinate_cleanup_pending(self, subentry_id)
            ):
                continue
        activate, plug_was_on = command.activate, command.plug_was_on
        flicker_eligible = command.flicker_eligible
        reason, bypass_guards = command.reason, command.bypass_guards
        control_generation = (
            command.control_generation
            if command.control_generation is not None
            else queued_generations.get(subentry_id, 0)
        )
        if (
            not self.load_bm_enabled(subentry_id)
            or self._load_control_generation.get(subentry_id, 0) != control_generation
        ):
            continue
        if subentry_id in cascade_managed:
            _LOGGER.info(
                "Dropping queued generic switch action for cascade-managed load %s",
                subentry_id,
            )
            continue
        subentry = self.entry.subentries.get(subentry_id)
        if subentry is None or dict(subentry.data) != data:
            continue
        plug = data[CONF_LOAD_CONTROL_SWITCH]
        enable = data.get(CONF_LOAD_CHARGE_ENABLE)
        subentry = self.entry.subentries.get(subentry_id)
        label = subentry.title if subentry else subentry_id
        if (
            activate
            and not bypass_guards
            and (
                start_blocked(self)
                or (
                    (safety := self._load_safety_tasks.get(subentry_id)) is not None
                    and not safety.done()
                )
                or coordinate_cleanup_pending(self, subentry_id)
                or not coordinate_request_current(self)
            )
        ):
            # G4 floor guard / D-A8 stale shed, in-flight race: the
            # guard may have tripped AFTER this ON was queued (a
            # debounced SOC refresh returns early while this task is
            # still running). A queued ON must never fire at/below
            # the floor or into an active data-loss shed — that is
            # exactly the unsupervised start both guards exist to
            # prevent.
            _LOGGER.info(
                "Load safety guard active: dropping queued switch-ON for %s",
                label,
            )
            continue
        if activate and _start_awaits_retry(self, command):
            continue
        generation = self._load_action_generation.get(subentry_id, 0) + 1
        self._load_action_generation[subentry_id] = generation
        if activate:
            if enable and not await self._switch_load_entity(
                enable, True, actor_owner="load:" + subentry_id
            ):
                # A late ON may be real even when its coordinate was revoked
                # before confirmation. Roll back our gate, never enable input.
                if (
                    self._load_action_generation.get(subentry_id) == generation
                    and not coordinate_request_current(self)
                    and (request := self._load_actor_requests.get(enable)) is not None
                    and request.desired
                    and request.action_generation == generation
                    and self._entity_is_on(enable)
                ):
                    await self._switch_load_entity(
                        enable, False, actor_owner="load:" + subentry_id
                    )
                continue
            if self._load_action_generation.get(subentry_id) != generation:
                continue
            if not self.load_bm_enabled(subentry_id):
                continue
            if not bypass_guards and (
                start_blocked(self) or not coordinate_request_current(self)
            ):
                if enable:
                    await self._switch_load_entity(
                        enable, False, actor_owner="load:" + subentry_id
                    )
                continue
            # The queue snapshot may predate an external input switch. Only a
            # command issued while the input is not ON can acquire ownership.
            plug_was_on = self._entity_is_on(plug)
            if not plug_was_on:
                if not await self._switch_load_entity(
                    plug, True, actor_owner="load:" + subentry_id
                ):
                    if self._load_action_generation.get(subentry_id) != generation:
                        continue
                    if not coordinate_request_current(
                        self
                    ) and coordinate_cleanup_pending(self, subentry_id):
                        _schedule_coordinate_cleanup(self)
                    # Rollback (7-day live audit 2026-08-04): the gate
                    # confirmed ON but the plug did not (BLE-RPC
                    # failure) — without this OFF the gate stays
                    # orphaned ON FOREVER: charging never becomes
                    # active, so every later cycle sees desired ==
                    # current and no path ever switches the gate off
                    # (live it sat ON for 3.4 days, bypassing the
                    # legacy automation's 20 % firmware floor). That
                    # violates LOAD_CONTROL.md §3: the gate may only
                    # be on while charging. The G4/shed guard drop
                    # above runs BEFORE the enable ON, so it never
                    # needs this rollback.
                    if enable:
                        if await self._switch_load_entity(
                            enable, False, actor_owner="load:" + subentry_id
                        ):
                            _LOGGER.warning(
                                "Load %s: plug ON failed after the"
                                " charge-enable gate confirmed ON —"
                                " rolled the gate back OFF",
                                label,
                            )
                        else:
                            # The gate stays physically on; the
                            # orphan sweep in _apply_load_switching
                            # retries every cycle until it clears.
                            _LOGGER.warning(
                                "Load %s: plug ON failed and the"
                                " charge-enable rollback OFF failed"
                                " too — the gate stays ON for now",
                                label,
                            )
                    continue
                if (
                    self._load_control_generation.get(subentry_id, 0)
                    != control_generation
                ):
                    continue
                # We switched the plug on for charging: ownership
                # allows the 'auto' policy to switch it off again.
                request = self._load_actor_requests.get(plug)
                if (
                    request is not None
                    and request.desired
                    and request.action_generation == generation
                    and request.state == "confirmed"
                    and self._entity_is_on(plug)
                ):
                    self._load_plug_owned[subentry_id] = True
                    self._load_plug_owned_entities[subentry_id] = plug
                    state = self.hass.states.get(plug)
                    self._load_input_on_since[subentry_id] = (
                        state.last_changed if state is not None else dt_util.now()
                    )
            if self._load_action_generation.get(subentry_id) != generation:
                continue
            # Feedback can arrive after an operator pause or safety trip.
            # Ownership is recorded above so the compensating OFF obeys
            # the configured input policy, including auto-owned plugs.
            if not self.load_bm_enabled(subentry_id) or (
                not bypass_guards
                and (start_blocked(self) or not coordinate_request_current(self))
            ):
                await self._execute_load_switching(
                    [
                        LoadAction(
                            subentry_id,
                            data,
                            False,
                            True,
                            reason="permission withdrawn during confirmation",
                        )
                    ]
                )
                continue
            now = dt_util.now()
            confirm_start = getattr(
                getattr(self, "live_ac", None), "confirm_pv_start", None
            )
            if callable(confirm_start):
                confirm_start("load:" + subentry_id)
            self._load_charging_active[subentry_id] = True
            if not any(
                entity
                and (request := self._load_actor_requests.get(entity)) is not None
                and request.action_generation == generation
                and request.booked_edge is not None
                for entity in (plug, enable)
            ):
                self._last_load_switch[subentry_id] = now
                _set_run_deadline(self, command, now)
            # F8: a fresh run started — close the previous off episode
            # so a later stop opens a new flicker window from scratch.
            self._load_last_off.pop(subentry_id, None)
            # V9a: exactly one INFO line per confirmed switch action,
            # carrying the anlass derived at the decision point.
            _LOGGER.info("Load %s -> ON (%s)", label, reason)
            if subentry_id in self._load_power_calibration_release:
                self._complete_power_calibration_release(subentry_id)
        else:
            charging_was_active = (
                self._load_charging_active.get(
                    subentry_id, self._charging_is_active(data)
                )
                is True
            )
            policy = data.get(CONF_LOAD_INPUT_OFF_POLICY, INPUT_OFF_POLICY_AUTO)
            if not enable and policy == INPUT_OFF_POLICY_KEEP:
                # Misconfiguration (blocked by the flow, but be safe):
                # nothing can stop the charging in this combination.
                _LOGGER.warning(
                    "Load %s: policy 'keep_on' without a charge-enable"
                    " entity cannot stop charging",
                    label,
                )
                continue
            if enable and not await self._switch_load_entity(
                enable, False, actor_owner="load:" + subentry_id
            ):
                # Charge-enable did not confirm off: charging is not
                # actually stopped — keep state and retry next cycle.
                continue
            # The gate's reported edge ends charging even if its input must
            # remain ON or cannot be reached. Input cleanup never creates a
            # second charge stop and never moves dwell/flicker clocks.
            if self._load_action_generation.get(subentry_id) != generation:
                continue
            charging_edge = charging_was_active
            current_sub = self.entry.subentries.get(subentry_id)
            if (
                current_sub is None
                or dict(current_sub.data) != data
                or subentry_id in self.cascade_manager.managed_load_ids()
            ):
                continue
            if enable and self._entity_tristate(enable) is not False:
                continue
            confirmed_stop_at = dt_util.now()
            if enable:
                self._load_charging_active[subentry_id] = False
            turn_plug_off = input_off_allowed(self, subentry_id) and (
                command.coordinate_cleanup is None
                or coordinate_cleanup_pending(self, subentry_id)
            )
            input_confirmed = False
            if turn_plug_off:
                input_confirmed = await self._switch_load_entity(
                    plug, False, actor_owner="load:" + subentry_id
                )
            if self._load_action_generation.get(subentry_id) != generation:
                continue
            if not enable and not input_confirmed:
                continue
            if input_confirmed and self._entity_tristate(plug) is False:
                self._load_plug_owned[subentry_id] = False
                self._load_plug_owned_entities.pop(subentry_id, None)
                self._load_input_on_since.pop(subentry_id, None)
            release_start = getattr(
                getattr(self, "live_ac", None), "release_pv_start", None
            )
            if callable(release_start):
                release_start("load:" + subentry_id)
            self._load_charging_active[subentry_id] = False
            if charging_edge:
                # Confirmed gate OFF ends charging before any independent
                # input timeout. Late feedback is reconciled separately using
                # its reported edge; cleanup never starts another dwell.
                stopped_at = confirmed_stop_at if enable else dt_util.now()
                self._last_load_switch[subentry_id] = stopped_at
                self._load_latch_hold.discard(subentry_id)
                self._load_last_off[subentry_id] = (stopped_at, flicker_eligible)
                self._load_run_deadline.pop(subentry_id, None)
                self._cancel_off_timer(subentry_id)
                _LOGGER.info(
                    "Load %s -> OFF (%s; input %s)",
                    label,
                    reason,
                    "off" if input_confirmed else "stays on",
                )
                if subentry_id in self._load_power_calibration_release:
                    self._complete_power_calibration_release(subentry_id)
    self._save_persistent_state()
    if self.data:
        plans = self.data.get("load_plans") or {}
        for load_id, active in self._load_charging_active.items():
            if load_id in plans:
                plans[load_id]["charging_active"] = active
                plans[load_id].update(input_diagnostics(self, load_id))
        self.async_update_listeners()
    if self._floor_guard_active:
        # G4, in-flight race (part 2): a refresh that tripped the guard
        # while this task was running returned early and could not queue
        # its forced OFFs. Re-run the cycle now so any load that this
        # task just switched on (or that is still running) is forced off
        # immediately instead of waiting for the next poll.
        await self.async_request_refresh()
