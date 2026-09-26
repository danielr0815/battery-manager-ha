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
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_INPUT_OFF_POLICY,
    CONF_LOAD_MIN_RUNTIME_MIN,
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


def reconcile_feedback(coordinator: BatteryManagerCoordinator) -> None:
    """Accept late feedback without resending a command or losing input ownership.

    A timed-out ON can still arrive. Its successful service request remains
    evidence of ownership, so a later pause can release that input under auto.
    """
    activated: set[str] = set()
    settled: set[str] = set()
    for entity, request in coordinator._load_actor_requests.items():
        if request.state == "confirmed" or not coordinator.hass.states.is_state(
            entity, "on" if request.desired else "off"
        ):
            continue
        if request.desired and request.state != "service_failed":
            activated.add(entity)
        request.state = "confirmed"
        settled.add(entity)
    managed = coordinator.cascade_manager.managed_load_ids()
    for load_id, subentry in coordinator.entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_LOAD or load_id in managed:
            continue
        plug = subentry.data.get(CONF_LOAD_CONTROL_SWITCH)
        gate = subentry.data.get(CONF_LOAD_CHARGE_ENABLE)
        if plug in activated:
            coordinator._load_plug_owned[load_id] = True
        confirmed_path = settled.intersection((plug, gate))
        if not confirmed_path:
            continue
        if plug in settled and coordinator._entity_tristate(plug) is False:
            coordinator._load_plug_owned[load_id] = False
        active = coordinator._charging_is_active(subentry.data)
        if active is not None:
            # ON and OFF both start their dwell at the reported edge, never at
            # the earlier service ACK or a possibly much later reconciliation.
            coordinator._load_charging_active[load_id] = active
            coordinator._last_load_switch[load_id] = max(
                state.last_changed
                for entity in confirmed_path
                if (state := coordinator.hass.states.get(entity)) is not None
            )
    if settled:
        coordinator._save_persistent_state()


async def execute_load_switching(
    self: BatteryManagerCoordinator,
    actions: Sequence[LoadAction | tuple[Any, ...]],
    now: datetime | None = None,
) -> None:
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
        plug = data[CONF_LOAD_CONTROL_SWITCH]
        enable = data.get(CONF_LOAD_CHARGE_ENABLE)
        subentry = self.entry.subentries.get(subentry_id)
        label = subentry.title if subentry else subentry_id
        if (
            activate
            and not bypass_guards
            and (self._floor_guard_active or self._stale_shed_active)
        ):
            # G4 floor guard / D-A8 stale shed, in-flight race: the
            # guard may have tripped AFTER this ON was queued (a
            # debounced SOC refresh returns early while this task is
            # still running). A queued ON must never fire at/below
            # the floor or into an active data-loss shed — that is
            # exactly the unsupervised start both guards exist to
            # prevent.
            _LOGGER.info(
                "Floor guard or stale shed active: dropping queued switch-ON for %s",
                label,
            )
            continue
        if activate:
            if enable and not await self._switch_load_entity(enable, True):
                continue
            if not self.load_bm_enabled(subentry_id) or (
                not bypass_guards
                and (self._floor_guard_active or self._stale_shed_active)
            ):
                if enable:
                    await self._switch_load_entity(enable, False)
                continue
            # The queue snapshot may predate an external input switch. Only a
            # command issued while the input is not ON can acquire ownership.
            plug_was_on = self._entity_is_on(plug)
            if not plug_was_on:
                if not await self._switch_load_entity(plug, True):
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
                        if await self._switch_load_entity(enable, False):
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
                self._load_plug_owned[subentry_id] = True
            # Feedback can arrive after an operator pause or safety trip.
            # Ownership is recorded above so the compensating OFF obeys
            # the configured input policy, including auto-owned plugs.
            if not self.load_bm_enabled(subentry_id) or (
                not bypass_guards
                and (self._floor_guard_active or self._stale_shed_active)
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
            if enable and not await self._switch_load_entity(enable, False):
                # Charge-enable did not confirm off: charging is not
                # actually stopped — keep state and retry next cycle.
                continue
            owned = self._load_plug_owned.get(subentry_id, False)
            turn_plug_off = policy == INPUT_OFF_POLICY_ALWAYS or (
                policy == INPUT_OFF_POLICY_AUTO and owned
            )
            if not enable and policy != INPUT_OFF_POLICY_KEEP:
                # Without a charge-enable gate, stopping charging is
                # only possible by switching the input off.
                turn_plug_off = True
            if turn_plug_off and not await self._switch_load_entity(plug, False):
                # Turn-off failed: keep ownership so the plug is never
                # recorded as not-ours while physically ON. Without a
                # charge-enable gate charging is still active, so the
                # next cycle re-attempts the off; with a gate the gate
                # already stopped charging and the next charge cycle's
                # stop cleans the plug up (review #3).
                continue
            self._load_plug_owned[subentry_id] = False
            self._load_charging_active[subentry_id] = False
            now = dt_util.now()
            self._last_load_switch[subentry_id] = now
            # F10: the latch-hold run ended (a gate dropped) — clear its
            # marker so a later hold re-arms the classification fresh.
            self._load_latch_hold.discard(subentry_id)
            # F8: record this confirmed OFF and whether it is a flicker
            # continuation candidate (a recommendation stop, not a
            # G4/G1 safety stop) so a re-on within the window can waive
            # min_off.
            self._load_last_off[subentry_id] = (now, flicker_eligible)
            # F-SUBHOUR: run finished — clear the frozen deadline + timer.
            self._load_run_deadline.pop(subentry_id, None)
            self._cancel_off_timer(subentry_id)
            # V9a: exactly one INFO line per confirmed switch action.
            _LOGGER.info(
                "Load %s -> OFF (%s; input %s)",
                label,
                reason,
                "off" if turn_plug_off else "stays on",
            )
            if subentry_id in self._load_power_calibration_release:
                self._complete_power_calibration_release(subentry_id)
    self._save_persistent_state()
    if self.data:
        plans = self.data.get("load_plans") or {}
        for load_id, active in self._load_charging_active.items():
            if load_id in plans:
                plans[load_id]["charging_active"] = active
        self.async_update_listeners()
    if self._floor_guard_active:
        # G4, in-flight race (part 2): a refresh that tripped the guard
        # while this task was running returned early and could not queue
        # its forced OFFs. Re-run the cycle now so any load that this
        # task just switched on (or that is still running) is forced off
        # immediately instead of waiting for the next poll.
        await self.async_request_refresh()
