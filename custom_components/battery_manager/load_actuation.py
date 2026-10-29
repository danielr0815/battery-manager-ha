"""Load commands and physical confirmation, independent of planner success."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
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
    changed = False
    event_entity = event.data.get("entity_id") if event is not None else None
    event_state = event.data.get("new_state") if event is not None else None
    settled: set[str] = set()
    managed = coordinator.cascade_manager.managed_load_ids()
    for load_id, sub in coordinator.entry.subentries.items():
        if sub.subentry_type != SUBENTRY_TYPE_LOAD or load_id in managed:
            continue
        plug = sub.data.get(CONF_LOAD_CONTROL_SWITCH)
        if not isinstance(plug, str) or not plug:
            continue
        state = coordinator.hass.states.get(plug)
        request = coordinator._load_actor_requests.get(plug)
        if (
            event_entity == plug
            and event_state is not None
            and event_state.state == "off"
        ):
            # Reading hass.states alone loses a fast OFF→ON: callbacks can
            # both run after the new ON has already become current state.
            # Revoke only the ownership/request that predates this edge, so
            # an old queued OFF cannot cancel a newer independent BM start.
            since = coordinator._load_input_on_since.get(load_id)
            if since is None or since <= event_state.last_changed:
                coordinator._load_plug_owned[load_id] = False
                coordinator._load_plug_owned_entities.pop(load_id, None)
                coordinator._load_input_on_since.pop(load_id, None)
                changed = True
            if (
                request is not None
                and request.desired
                and request.requested_at <= event_state.last_changed
            ):
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
                coordinator._load_actor_requests.pop(plug, None)
                request = None
        if (
            request
            and request.owner == "load:" + load_id
            and request.desired
            and request.state not in ("confirmed", "service_failed")
            and state is not None
            and state.state == "on"
        ):
            coordinator._load_plug_owned[load_id] = True
            coordinator._load_plug_owned_entities[load_id] = plug
            coordinator._load_input_on_since.setdefault(load_id, state.last_changed)
            changed = True
    for entity, request in coordinator._load_actor_requests.items():
        if request.state == "confirmed" or not coordinator.hass.states.is_state(
            entity, "on" if request.desired else "off"
        ):
            continue
        request.state = "confirmed"
        settled.add(entity)
        changed = True
    for load_id, sub in coordinator.entry.subentries.items():
        if sub.subentry_type != SUBENTRY_TYPE_LOAD or load_id in managed:
            continue
        path = settled.intersection(
            (
                sub.data.get(CONF_LOAD_CONTROL_SWITCH),
                sub.data.get(CONF_LOAD_CHARGE_ENABLE),
            )
        )
        active = coordinator._charging_is_active(sub.data)
        if (
            not path
            or active is None
            or coordinator._load_charging_active.get(load_id) is active
        ):
            continue
        # Late device feedback starts a dwell at the actual edge. An input
        # cleanup behind an already-OFF gate leaves the charge clock intact.
        coordinator._load_charging_active[load_id] = active
        coordinator._last_load_switch[load_id] = max(
            state.last_changed
            for entity in path
            if (state := coordinator.hass.states.get(entity)) is not None
        )
        if not active:
            coordinator._load_run_deadline.pop(load_id, None)
            coordinator._cancel_off_timer(load_id)
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


async def execute_load_switching(
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
        activate, plug_was_on = command.activate, command.plug_was_on
        run_h, flicker_eligible = command.run_h, command.flicker_eligible
        reason, bypass_guards = command.reason, command.bypass_guards
        if activate and not self.load_bm_enabled(subentry_id):
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
        generation = self._load_action_generation.get(subentry_id, 0) + 1
        self._load_action_generation[subentry_id] = generation
        plug = data[CONF_LOAD_CONTROL_SWITCH]
        enable = data.get(CONF_LOAD_CHARGE_ENABLE)
        subentry = self.entry.subentries.get(subentry_id)
        label = subentry.title if subentry else subentry_id
        if activate and not bypass_guards and start_blocked(self):
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
        if activate:
            if enable and not await self._switch_load_entity(
                enable, True, actor_owner="load:" + subentry_id
            ):
                continue
            if self._load_action_generation.get(subentry_id) != generation:
                continue
            if not self.load_bm_enabled(subentry_id) or (
                not bypass_guards and start_blocked(self)
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
                # We switched the plug on for charging: ownership
                # allows the 'auto' policy to switch it off again.
                request = self._load_actor_requests.get(plug)
                if request is not None and request.desired:
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
                not bypass_guards and start_blocked(self)
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
            self._load_charging_active[subentry_id] = True
            self._last_load_switch[subentry_id] = now
            # F8: a fresh run started — close the previous off episode
            # so a later stop opens a new flicker window from scratch.
            self._load_last_off.pop(subentry_id, None)
            # F-SUBHOUR (approach A) + F-RESIDUAL-TOPUP R7: freeze the
            # planned contiguous run and arm an active OFF at
            # run_start + max(min_runtime, run_h) so a sub-hour booking is
            # delivered exactly (no ~250 Wh over-run). Energy-limited loads
            # are capped the same way — the deadline is an UPPER bound over
            # their primary level-driven target-SOC stop, so a stale
            # load-SOC sensor (R8) cannot stretch a ~150 Wh top-up into a
            # full real_power × 1 h night charge. For a gate-stop final
            # quantum SHORTER than min_runtime (F-GATE-TOPUP R5) the cap
            # is deliberately LONGER than the booked run: it stays the
            # stale-SOC upper bound only, while G1's dwell-exempt target
            # stop is the primary stop that ends the run at `rem`.
            if run_h > 0.0:
                off_min = max(
                    int(data.get(CONF_LOAD_MIN_RUNTIME_MIN, 30)),
                    round(run_h * 60.0),
                )
                off_at = now + timedelta(minutes=off_min)
                self._load_run_deadline[subentry_id] = off_at
                self._arm_off_timer(subentry_id, off_at)
            else:
                self._load_run_deadline.pop(subentry_id, None)
                self._cancel_off_timer(subentry_id)
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
            turn_plug_off = input_off_allowed(self, subentry_id)
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
