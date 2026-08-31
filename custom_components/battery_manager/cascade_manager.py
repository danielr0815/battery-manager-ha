"""Central actor owner for linear storage cascades.

The planner is deliberately stateless.  This module owns the physical HA
actors, serialises every make/break operation per cascade and persists only
stable evidence.  In-flight power windows are intentionally volatile: after a
restart a still-consistent chain has to prove its source again.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir

from .const import (
    ACTOR_MODE_EXCLUSIVE,
    ACTOR_MODE_SHARED,
    CONF_CASCADE_ACTOR_TIMEOUT_S,
    CONF_CASCADE_MEMBER_IDS,
    CONF_CASCADE_TERMINAL_LOAD_ID,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_DISCHARGE_FLOOR_SOC,
    CONF_LOAD_GATE_ACTOR_MODE,
    CONF_LOAD_HANDOVER_MIN_POWER_W,
    CONF_LOAD_INPUT_ACTOR_MODE,
    CONF_LOAD_OUTPUT_ACTOR_MODE,
    CONF_LOAD_OUTPUT_POWER_ENTITY,
    CONF_LOAD_OUTPUT_SWITCH,
    CONF_LOAD_RECOVERY_SOC,
    CONF_LOAD_SOC_ENTITY,
    CONF_LOAD_WAKE_TIMEOUT_S,
    DOMAIN,
    SUBENTRY_TYPE_CASCADE,
)
from .core import (
    CascadeRuntimeState,
    HourSlot,
    PlanResult,
    SurplusLoadState,
    SystemConfig,
)

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

_PROOF_SECONDS = 60
_RETRY_DELAY = timedelta(minutes=15)
_ACTOR_CONFIRM_POLL_S = 0.1


class CascadeManager:
    """Execute cascade plans without allowing independent actor ownership."""

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        self.coordinator = coordinator
        self._locks: dict[str, asyncio.Lock] = {}
        self._proof: dict[str, dict[str, Any]] = {}
        self._aux_tick: dict[str, tuple[datetime, float]] = {}
        self._telemetry_unknown_since: dict[str, datetime] = {}

    def _state(self, cascade_id: str) -> dict[str, Any]:
        state = self.coordinator._cascade_state.setdefault(cascade_id, {})
        state.setdefault("enabled", False)
        state.setdefault("phase", "idle")
        state.setdefault("source", None)
        state.setdefault("episode_day", None)
        state.setdefault("recovery_pending", [])
        state.setdefault("recovery_deadline", None)
        state.setdefault("claims", {})
        state.setdefault("fault", None)
        state.setdefault("fault_detail", None)
        state.setdefault("fault_safe_off_complete", False)
        state.setdefault("hands_off", False)
        state.setdefault("retry_used", False)
        state.setdefault("retry_at", None)
        state.setdefault("aux_today_wh", 0.0)
        return state

    @staticmethod
    def _recovery_deadline(state: dict[str, Any], plan: Any) -> datetime | None:
        """Keep a promised deadline visible after the rolling plan withdraws."""
        deadline = getattr(plan, "recovery_deadline", None)
        if isinstance(deadline, datetime):
            return deadline
        persisted = state.get("recovery_deadline")
        if not isinstance(persisted, str):
            return None
        try:
            return datetime.fromisoformat(persisted)
        except ValueError:
            return None

    def runtime_state(self, cascade_id: str) -> CascadeRuntimeState:
        state = self._state(cascade_id)
        episode_day = state.get("episode_day")
        try:
            parsed_day = date.fromisoformat(episode_day) if episode_day else None
        except ValueError:
            parsed_day = None
        phase = state.get("phase", "idle")
        # ``proving`` already consumes terminal energy.  Reporting it as idle
        # made the rolling planner demand a fresh full dwell during the proof
        # minute, so a valid marginal Aux episode could disappear before it
        # reached ``running``.
        if phase not in ("idle", "proving", "running", "recovering", "complete"):
            phase = "idle"
        return CascadeRuntimeState(
            cascade_id=cascade_id,
            episode_day=parsed_day,
            phase=phase,
            active_source_id=state.get("source"),
            recovery_pending_ids=tuple(state.get("recovery_pending", [])),
        )

    def enabled(self, cascade_id: str) -> bool:
        return bool(self._state(cascade_id)["enabled"])

    def managed_load_ids(self) -> set[str]:
        """Return every load reserved by a configured cascade.

        This intentionally reads the stored references instead of the pure-core
        configuration: an invalid topology must remain fail-closed and may not
        fall back to independent load actuation merely because the core omitted
        it from ``SystemConfig.cascades``.
        """
        managed: set[str] = set()
        for subentry in self.coordinator.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_CASCADE:
                continue
            managed.update(subentry.data.get(CONF_CASCADE_MEMBER_IDS, []))
            terminal_id = subentry.data.get(CONF_CASCADE_TERMINAL_LOAD_ID)
            if terminal_id:
                managed.add(terminal_id)
        return managed

    def planning_blocked_load_ids(self) -> set[str]:
        """Loads whose cascade cannot currently execute its published plan."""
        blocked: set[str] = set()
        for cascade_id, subentry in self.coordinator.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_CASCADE:
                continue
            state = self._state(cascade_id)
            if not state.get("fault") and not state.get("hands_off"):
                continue
            blocked.update(subentry.data.get(CONF_CASCADE_MEMBER_IDS, []))
            terminal_id = subentry.data.get(CONF_CASCADE_TERMINAL_LOAD_ID)
            if terminal_id:
                blocked.add(terminal_id)
        return blocked

    async def async_set_enabled(self, cascade_id: str, enabled: bool) -> bool:
        state = self._state(cascade_id)
        if not enabled:
            state["enabled"] = False
            await self.async_safe_off(cascade_id, "automation disabled")
            self.coordinator._save_persistent_state()
            return True
        if state.get("hands_off"):
            # A deliberate OFF -> ON is the sole hands-off reset gesture.
            state["hands_off"] = False
        if state.get("fault"):
            return False
        topology = self._topology(cascade_id)
        if topology is None or not self._fresh_member_socs(topology):
            return False
        # A fully off chain is always safe to adopt.  A non-idle physical
        # state is only adopted by the normal plan pass after enabling.
        if not self._all_actors_off(topology):
            return False
        state["enabled"] = True
        self.coordinator._save_persistent_state()
        return True

    async def async_reset_fault(self, cascade_id: str) -> bool:
        state = self._state(cascade_id)
        state["enabled"] = False
        if not await self.async_safe_off(cascade_id, "fault reset"):
            return False
        state["fault"] = None
        state["fault_detail"] = None
        state["fault_safe_off_complete"] = False
        state.pop("last_actor_error", None)
        state["hands_off"] = False
        state["retry_used"] = False
        state["retry_at"] = None
        ir.async_delete_issue(
            self.coordinator.hass,
            DOMAIN,
            f"cascade_fault_{self.coordinator.entry.entry_id}_{cascade_id}",
        )
        self.coordinator._save_persistent_state()
        return True

    def _topology(self, cascade_id: str) -> dict[str, Any] | None:
        cascade = self.coordinator.entry.subentries.get(cascade_id)
        if cascade is None or cascade.subentry_type != SUBENTRY_TYPE_CASCADE:
            return None
        members = []
        for load_id in cascade.data.get(CONF_CASCADE_MEMBER_IDS, []):
            load = self.coordinator.entry.subentries.get(load_id)
            if load is None:
                return None
            members.append((load_id, load.data))
        terminal_id = cascade.data.get(CONF_CASCADE_TERMINAL_LOAD_ID)
        terminal = self.coordinator.entry.subentries.get(terminal_id)
        if not members or terminal is None:
            return None
        return {
            "id": cascade_id,
            "members": members,
            "terminal_id": terminal_id,
            "terminal": terminal.data,
        }

    def _fresh_member_socs(self, topology: dict[str, Any]) -> bool:
        for _load_id, data in topology["members"]:
            entity_id = data.get(CONF_LOAD_SOC_ENTITY)
            if not entity_id or self.coordinator._read_float(entity_id) is None:
                return False
        return True

    def _member_soc_reported_at(self, data: dict[str, Any]) -> datetime | None:
        """Return the latest SOC publication, including unchanged values."""
        entity_id = data.get(CONF_LOAD_SOC_ENTITY)
        entity_state = (
            self.coordinator.hass.states.get(entity_id) if entity_id else None
        )
        if entity_state is None:
            return None
        return getattr(entity_state, "last_reported", entity_state.last_updated)

    def _wait_for_member_wake(
        self,
        cascade_id: str,
        topology: dict[str, Any],
        member_index: int,
        baseline: datetime | None,
        now: datetime,
        mode: str,
    ) -> None:
        """Remember the publication that must be superseded after power-on."""
        _load_id, data = topology["members"][member_index]
        timeout_s = int(data.get(CONF_LOAD_WAKE_TIMEOUT_S, 60))
        state = self._state(cascade_id)
        state["phase"] = "waking_members"
        state["wake_mode"] = mode
        state["wake_member_index"] = member_index
        state["wake_soc_reported_at"] = baseline.isoformat() if baseline else None
        state["wake_deadline"] = (now + timedelta(seconds=timeout_s)).isoformat()

    def _member_woke(self, cascade_id: str, topology: dict[str, Any]) -> bool:
        """Require a new numeric SOC publication after upstream power arrived."""
        state = self._state(cascade_id)
        index = int(state.get("wake_member_index", -1))
        if not 0 <= index < len(topology["members"]):
            return False
        _load_id, data = topology["members"][index]
        entity_id = data.get(CONF_LOAD_SOC_ENTITY)
        if not entity_id or self.coordinator._read_float(entity_id) is None:
            return False
        reported_at = self._member_soc_reported_at(data)
        if reported_at is None:
            return False
        baseline_value = state.get("wake_soc_reported_at")
        if not baseline_value:
            return True
        try:
            return reported_at > datetime.fromisoformat(baseline_value)
        except ValueError:
            return False

    def _clear_member_wake(self, cascade_id: str) -> None:
        state = self._state(cascade_id)
        for key in (
            "wake_mode",
            "wake_member_index",
            "wake_soc_reported_at",
            "wake_deadline",
        ):
            state.pop(key, None)

    def _all_actors_off(self, topology: dict[str, Any]) -> bool:
        entities = []
        for _load_id, data in topology["members"]:
            entities.extend(
                data.get(key)
                for key in (
                    CONF_LOAD_CONTROL_SWITCH,
                    CONF_LOAD_CHARGE_ENABLE,
                    CONF_LOAD_OUTPUT_SWITCH,
                )
                if data.get(key)
            )
        terminal = topology["terminal"].get(CONF_LOAD_CONTROL_SWITCH)
        if terminal:
            entities.append(terminal)
        return all(not self.coordinator._entity_is_on(entity) for entity in entities)

    def _foreign_override(
        self, cascade_id: str, topology: dict[str, Any]
    ) -> str | None:
        """Return ownership mode when a claimed state changed externally."""
        modes: dict[str, str] = {}
        for _load_id, data in topology["members"]:
            for actor_key, mode_key in (
                (CONF_LOAD_CONTROL_SWITCH, CONF_LOAD_INPUT_ACTOR_MODE),
                (CONF_LOAD_CHARGE_ENABLE, CONF_LOAD_GATE_ACTOR_MODE),
                (CONF_LOAD_OUTPUT_SWITCH, CONF_LOAD_OUTPUT_ACTOR_MODE),
            ):
                entity_id = data.get(actor_key)
                if entity_id:
                    modes[entity_id] = data.get(mode_key, ACTOR_MODE_EXCLUSIVE)
        terminal_actor = topology["terminal"].get(CONF_LOAD_CONTROL_SWITCH)
        if terminal_actor:
            modes[terminal_actor] = ACTOR_MODE_EXCLUSIVE
        for entity_id, claimed in self._state(cascade_id)["claims"].items():
            current = self.coordinator.hass.states.get(entity_id)
            if current is None or current.state in ("unknown", "unavailable"):
                continue
            if current.attributes.get("assumed_state"):
                continue
            actual = current.state == "on"
            if actual != bool(claimed):
                return modes.get(entity_id, ACTOR_MODE_EXCLUSIVE)
        return None

    def _adopt_shared_safe_off(
        self, cascade_id: str, topology: dict[str, Any], plan: Any
    ) -> None:
        """Accept an external Shared-OFF when the fresh plan is already idle."""
        state = self._state(cascade_id)
        if not state.get("enabled"):
            return
        flow = plan.flows[0] if plan.flows else None
        if flow is not None and (
            flow.root_input_wh > 0.0
            or any(
                segment.source == "aux" and segment.terminal_energy_wh > 0.0
                for segment in flow.segments
            )
        ):
            return
        # The reference Root plug intentionally auto-switches off at zero
        # power.  Once the fresh slot plan also wants Safe-OFF, that Shared
        # transition is convergence, not an operator takeover.  Only OFF is
        # adopted: an unexpected external ON still enters hands-off.
        for _load_id, data in topology["members"]:
            for actor_key, mode_key in (
                (CONF_LOAD_CONTROL_SWITCH, CONF_LOAD_INPUT_ACTOR_MODE),
                (CONF_LOAD_CHARGE_ENABLE, CONF_LOAD_GATE_ACTOR_MODE),
                (CONF_LOAD_OUTPUT_SWITCH, CONF_LOAD_OUTPUT_ACTOR_MODE),
            ):
                entity_id = data.get(actor_key)
                if not entity_id or data.get(mode_key) != ACTOR_MODE_SHARED:
                    continue
                current = self.coordinator.hass.states.get(entity_id)
                if (
                    state["claims"].get(entity_id) is True
                    and current is not None
                    and current.state == "off"
                ):
                    state["claims"][entity_id] = False

    async def _actor(
        self,
        cascade_id: str,
        entity_id: str | None,
        turn_on: bool,
    ) -> bool:
        if not entity_id:
            return True
        state = self._state(cascade_id)
        # Shared ownership only changes how an *external* deviation from our
        # last claim is handled.  It must not reject our own next transition
        # merely because the desired state differs from that previous claim.
        # `_foreign_override` performs the actual-state comparison before a
        # plan is applied and enters hands-off without rollback when needed.
        target_state = "on" if turn_on else "off"
        current = self.coordinator.hass.states.get(entity_id)
        if current is not None and current.state == target_state:
            # Live incident 2026-08-29: both Fossibot outputs reported slow,
            # effectively toggle-like behaviour under repeated writes.
            # Re-sending OFF on every inactive plan pass made both outputs
            # oscillate every ~12 s.  A confirmed target state is already a
            # successful, adoptable transition and must never be rewritten.
            state["claims"][entity_id] = turn_on
            return True
        cascade = self.coordinator.entry.subentries.get(cascade_id)
        timeout_s = float(
            cascade.data.get(CONF_CASCADE_ACTOR_TIMEOUT_S, 30) if cascade else 30
        )
        try:
            async with asyncio.timeout(timeout_s):
                ok = await self.coordinator._switch_entity(entity_id, turn_on)
                if not ok:
                    state["last_actor_error"] = {
                        "entity_id": entity_id,
                        "target_state": target_state,
                        "observed_state": current.state
                        if current is not None
                        else None,
                        "kind": "service_failed",
                    }
                    return False
                while True:
                    current = self.coordinator.hass.states.get(entity_id)
                    if current is not None and current.attributes.get("assumed_state"):
                        # A completed service call is the strongest evidence an
                        # assumed-state actor exposes; it cannot confirm more.
                        break
                    if current is not None and current.state == target_state:
                        break
                    await asyncio.sleep(_ACTOR_CONFIRM_POLL_S)
        except TimeoutError:
            current = self.coordinator.hass.states.get(entity_id)
            state["last_actor_error"] = {
                "entity_id": entity_id,
                "target_state": target_state,
                "observed_state": current.state if current is not None else None,
                "kind": "confirmation_timeout",
            }
            return False
        # Only a confirmed (or explicitly assumed-state) transition becomes a
        # claim.  Otherwise the next plan could mistake delayed feedback for
        # an exclusive external override during cascade startup.
        state["claims"][entity_id] = turn_on
        return True

    def _begin_actor_sequence(self, cascade_id: str) -> None:
        """Discard stale transition evidence before a new ordered sequence."""
        self._state(cascade_id).pop("last_actor_error", None)

    async def _fault(self, cascade_id: str, reason: str) -> None:
        state = self._state(cascade_id)
        state["fault"] = reason
        state["fault_detail"] = state.get("last_actor_error")
        state["phase"] = "fault"
        state["enabled"] = False
        state["fault_safe_off_complete"] = False
        display_reason = reason
        if detail := state.get("fault_detail"):
            display_reason = (
                f"{reason}: {detail.get('entity_id')} -> "
                f"{detail.get('target_state')} ({detail.get('kind')}, "
                f"observed={detail.get('observed_state')})"
            )
        ir.async_create_issue(
            self.coordinator.hass,
            DOMAIN,
            f"cascade_fault_{self.coordinator.entry.entry_id}_{cascade_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="cascade_fault",
            translation_placeholders={"reason": display_reason},
        )
        self.coordinator._save_persistent_state()

    async def async_safe_off(self, cascade_id: str, reason: str) -> bool:
        """Break the load path downstream-to-upstream and keep Root open."""
        self._begin_actor_sequence(cascade_id)
        topology = self._topology(cascade_id)
        if topology is None:
            await self._fault(cascade_id, "invalid_topology")
            return False
        ok = True
        terminal = topology["terminal"]
        terminal_actor = terminal.get(CONF_LOAD_CONTROL_SWITCH)
        if terminal_actor:
            ok &= await self._actor(cascade_id, terminal_actor, False)
        for _load_id, data in reversed(topology["members"]):
            ok &= await self._actor(
                cascade_id,
                data.get(CONF_LOAD_OUTPUT_SWITCH),
                False,
            )
        for _load_id, data in topology["members"]:
            ok &= await self._actor(
                cascade_id,
                data.get(CONF_LOAD_CHARGE_ENABLE),
                False,
            )
        root = topology["members"][0][1]
        ok &= await self._actor(
            cascade_id,
            root.get(CONF_LOAD_CONTROL_SWITCH),
            False,
        )
        self._proof.pop(cascade_id, None)
        self._clear_member_wake(cascade_id)
        state = self._state(cascade_id)
        state["source"] = None
        if state.get("phase") != "fault":
            state["phase"] = "idle"
        if not ok:
            await self._fault(cascade_id, f"safe_off_failed:{reason}")
        elif state.get("fault"):
            # The fault stays visible and blocks re-enabling, but the completed
            # break sequence ends actor ownership. Reapplying Safe-OFF on every
            # refresh used to undo intentional manual operation.
            state["fault_safe_off_complete"] = True
        self.coordinator._save_persistent_state()
        return ok

    async def async_safety_off(
        self, cascade_id: str, reason: str, now: datetime | None = None
    ) -> bool:
        """Dwell-exempt Safe-OFF for a BM-controlled cascade.

        Deliberately disabled/hands-off cascades have released their actors and
        are not touched.  An interrupted Aux episode is complete for the local
        day; otherwise the next rolling plan could advertise a second episode
        even though the executor correctly refuses to wake it again.
        """
        state = self._state(cascade_id)
        if state.get("fault") and state.get("fault_safe_off_complete"):
            return True
        if not state.get("enabled") and not state.get("fault"):
            return True
        phase = state.get("phase")
        aux_active = phase in (
            "waking",
            "waking_socs",
            "proving",
            "running",
        ) or (phase == "waking_members" and state.get("wake_mode") == "aux")
        ok = await self.async_safe_off(cascade_id, reason)
        if ok and aux_active and not state.get("fault"):
            if not state.get("episode_day") and now is not None:
                state["episode_day"] = now.date().isoformat()
            state["phase"] = "complete"
            self.coordinator._save_persistent_state()
        return ok

    async def async_safety_off_active(self, reason: str, now: datetime) -> None:
        """Apply the global fail-safe to every configured active cascade."""
        tasks = []
        for cascade_id, subentry in self.coordinator.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_CASCADE:
                continue
            state = self._state(cascade_id)
            if not state.get("enabled") and not state.get("fault"):
                continue
            lock = self._locks.setdefault(cascade_id, asyncio.Lock())

            async def run_one(
                cid: str = cascade_id, owned: asyncio.Lock = lock
            ) -> None:
                async with owned:
                    await self.async_safety_off(cid, reason, now)

            tasks.append(run_one())
        if tasks:
            await asyncio.gather(*tasks)

    async def _finish_wake(self, cascade_id: str, source_id: str) -> bool:
        """Energise the terminal only after every member published live SOC."""
        topology = self._topology(cascade_id)
        if topology is None:
            return False
        root_data = topology["members"][0][1]
        terminal_actor = topology["terminal"].get(CONF_LOAD_CONTROL_SWITCH)
        if terminal_actor and not await self._actor(cascade_id, terminal_actor, True):
            return False
        source_index = next(
            index
            for index, (load_id, _data) in enumerate(topology["members"])
            if load_id == source_id
        )
        for _load_id, data in reversed(topology["members"][:source_index]):
            if not await self._actor(
                cascade_id,
                data.get(CONF_LOAD_OUTPUT_SWITCH),
                False,
            ):
                return False
        if not await self._actor(
            cascade_id,
            root_data.get(CONF_LOAD_CONTROL_SWITCH),
            False,
        ):
            return False
        state = self._state(cascade_id)
        state["source"] = source_id
        state["phase"] = "proving"
        self._clear_member_wake(cascade_id)
        self._proof.pop(cascade_id, None)
        return True

    async def _wake(self, cascade_id: str, source_id: str, now: datetime) -> bool:
        self._begin_actor_sequence(cascade_id)
        topology = self._topology(cascade_id)
        if topology is None:
            await self._fault(cascade_id, "invalid_topology")
            return False
        state = self._state(cascade_id)
        state["phase"] = "waking"
        # Each member must publish SOC after its upstream supply was made.
        # A cached numeric value is not evidence that a sleeping Fossibot can
        # already receive its own AC-output command.
        for _load_id, data in topology["members"]:
            if not await self._actor(
                cascade_id,
                data.get(CONF_LOAD_CHARGE_ENABLE),
                False,
            ):
                return False
        root_data = topology["members"][0][1]
        baseline = self._member_soc_reported_at(root_data)
        if not await self._actor(
            cascade_id,
            root_data.get(CONF_LOAD_CONTROL_SWITCH),
            True,
        ):
            return False
        state["source"] = source_id
        self._wait_for_member_wake(cascade_id, topology, 0, baseline, now, "aux")
        return True

    def _root_targets(
        self, topology: dict[str, Any], plan: Any
    ) -> tuple[dict[str, Any], bool, int, int]:
        """Return member flows, terminal target and wake/output path lengths."""
        flow = plan.flows[0]
        member_flows = {item.load_id: item for item in flow.member_flows}
        terminal_root = any(
            segment.root_input_on and segment.terminal_energy_wh > 0
            for segment in flow.segments
        )
        deepest = -1
        for index, (load_id, _data) in enumerate(topology["members"]):
            if (
                member_flows.get(load_id)
                and member_flows[load_id].own_charge_input_wh > 0
            ):
                deepest = max(deepest, index)
        output_count = len(topology["members"]) if terminal_root else max(deepest, 0)
        wake_count = output_count if terminal_root else deepest + 1
        return member_flows, terminal_root, output_count, wake_count

    async def _finish_root(
        self, cascade_id: str, topology: dict[str, Any], plan: Any
    ) -> bool:
        """Apply Root consumers after every newly supplied member woke."""
        member_flows, terminal_root, output_count, _wake_count = self._root_targets(
            topology, plan
        )
        for index, (_load_id, data) in enumerate(topology["members"]):
            if not await self._actor(
                cascade_id,
                data.get(CONF_LOAD_OUTPUT_SWITCH),
                index < output_count,
            ):
                return False
        terminal_actor = topology["terminal"].get(CONF_LOAD_CONTROL_SWITCH)
        if terminal_actor and not await self._actor(
            cascade_id, terminal_actor, terminal_root
        ):
            return False
        for load_id, data in topology["members"]:
            charging = bool(
                member_flows.get(load_id)
                and member_flows[load_id].own_charge_input_wh > 0
            )
            if not await self._actor(
                cascade_id,
                data.get(CONF_LOAD_CHARGE_ENABLE),
                charging,
            ):
                return False
        self._clear_member_wake(cascade_id)
        self._state(cascade_id)["phase"] = (
            "recovering" if self._state(cascade_id).get("recovery_pending") else "root"
        )
        return True

    async def _apply_root(self, cascade_id: str, plan: Any, now: datetime) -> bool:
        """Apply one Root-fed slot without counting internal passthrough."""
        self._begin_actor_sequence(cascade_id)
        topology = self._topology(cascade_id)
        if topology is None or not plan.flows:
            return False
        _member_flows, _terminal_root, output_count, wake_count = self._root_targets(
            topology, plan
        )

        # Disable charging before changing the passthrough path.
        for _load_id, data in topology["members"]:
            if not await self._actor(
                cascade_id,
                data.get(CONF_LOAD_CHARGE_ENABLE),
                False,
            ):
                return False
        root = topology["members"][0][1]
        root_actor = root.get(CONF_LOAD_CONTROL_SWITCH)
        root_was_on = bool(root_actor and self.coordinator._entity_is_on(root_actor))
        baseline = self._member_soc_reported_at(root) if not root_was_on else None
        if not await self._actor(
            cascade_id,
            root_actor,
            True,
        ):
            return False
        if not wake_count:
            return await self._finish_root(cascade_id, topology, plan)
        first_missing = next(
            (
                index
                for index, (_load_id, data) in enumerate(
                    topology["members"][:output_count]
                )
                if not self.coordinator._entity_is_on(data.get(CONF_LOAD_OUTPUT_SWITCH))
            ),
            None,
        )
        if not root_was_on:
            first_missing = 0
        if first_missing is None:
            return await self._finish_root(cascade_id, topology, plan)
        # A path containing a gap is rebuilt from that point so no sleeping
        # downstream member receives a premature command.
        for _load_id, data in reversed(topology["members"][first_missing + 1 :]):
            if not await self._actor(
                cascade_id, data.get(CONF_LOAD_OUTPUT_SWITCH), False
            ):
                return False
        if baseline is None:
            baseline = self._member_soc_reported_at(
                topology["members"][first_missing][1]
            )
        self._wait_for_member_wake(
            cascade_id, topology, first_missing, baseline, now, "root"
        )
        return True

    async def _continue_member_wake(
        self,
        cascade_id: str,
        topology: dict[str, Any],
        plan: Any,
        now: datetime,
    ) -> bool:
        """Advance exactly one electrically ordered wake step."""
        state = self._state(cascade_id)
        index = int(state.get("wake_member_index", -1))
        deadline_value = state.get("wake_deadline")
        try:
            deadline = datetime.fromisoformat(deadline_value)
        except TypeError, ValueError:
            deadline = now
        if not self._member_woke(cascade_id, topology):
            if now < deadline:
                return True
            _load_id, data = topology["members"][index]
            entity_id = data.get(CONF_LOAD_SOC_ENTITY)
            observed = (
                self.coordinator.hass.states.get(entity_id) if entity_id else None
            )
            state["last_actor_error"] = {
                "entity_id": entity_id,
                "target_state": "fresh_numeric_publication",
                "observed_state": observed.state if observed is not None else None,
                "kind": "wake_timeout",
            }
            return False

        mode = state.get("wake_mode")
        if mode == "aux":
            output_count = len(topology["members"])
            wake_count = output_count
        elif mode == "root":
            _flows, _terminal, output_count, wake_count = self._root_targets(
                topology, plan
            )
        else:
            return False

        if index < output_count:
            next_index = index + 1
            next_baseline = (
                self._member_soc_reported_at(topology["members"][next_index][1])
                if next_index < wake_count
                else None
            )
            if not await self._actor(
                cascade_id,
                topology["members"][index][1].get(CONF_LOAD_OUTPUT_SWITCH),
                True,
            ):
                return False
            if next_index < wake_count:
                self._wait_for_member_wake(
                    cascade_id,
                    topology,
                    next_index,
                    next_baseline,
                    now,
                    mode,
                )
                return True

        if mode == "aux":
            source_id = state.get("source")
            return bool(source_id) and await self._finish_wake(cascade_id, source_id)
        return await self._finish_root(cascade_id, topology, plan)

    async def _handover(self, cascade_id: str, source_id: str) -> bool:
        """Break skipped upstream outputs before proving the next source."""
        self._begin_actor_sequence(cascade_id)
        topology = self._topology(cascade_id)
        if topology is None:
            return False
        source_index = next(
            index
            for index, (load_id, _data) in enumerate(topology["members"])
            if load_id == source_id
        )
        for index in range(len(topology["members"]) - 1, -1, -1):
            _load_id, data = topology["members"][index]
            needed = index >= source_index
            if not await self._actor(
                cascade_id,
                data.get(CONF_LOAD_OUTPUT_SWITCH),
                needed,
            ):
                return False
        state = self._state(cascade_id)
        state["source"] = source_id
        state["phase"] = "proving"
        self._proof.pop(cascade_id, None)
        return True

    def _power_proven(self, cascade_id: str, now: datetime) -> bool | None:
        topology = self._topology(cascade_id)
        state = self._state(cascade_id)
        source = state.get("source")
        if topology is None or not source:
            return False
        source_data = next(
            data for load_id, data in topology["members"] if load_id == source
        )
        entity_id = source_data.get(CONF_LOAD_OUTPUT_POWER_ENTITY)
        value = self.coordinator._read_float(entity_id) if entity_id else None
        if value is None:
            self._proof.pop(cascade_id, None)
            return None
        entity_state = self.coordinator.hass.states.get(entity_id)
        observed = entity_state.last_updated if entity_state is not None else now
        proof = self._proof.get(cascade_id)
        if proof is None:
            self._proof[cascade_id] = {
                "at": observed,
                "value": value,
                "last": observed,
            }
            return None
        if observed <= proof["last"]:
            return None
        proof["last"] = observed
        if observed - proof["at"] < timedelta(seconds=_PROOF_SECONDS):
            return None
        average = (float(proof["value"]) + value) / 2.0
        threshold = float(source_data.get(CONF_LOAD_HANDOVER_MIN_POWER_W, 10.0))
        return average >= threshold

    async def _apply_one(
        self,
        cascade_id: str,
        plan: Any,
        load_states: tuple[SurplusLoadState, ...],
        now: datetime,
        safety_reason: str | None = None,
    ) -> None:
        state = self._state(cascade_id)
        day = now.date().isoformat()
        if state.get("aux_energy_day") != day:
            state["aux_energy_day"] = day
            state["aux_today_wh"] = 0.0
            self._aux_tick.pop(cascade_id, None)
        # Disabled and Shared-hands-off cascades no longer own their actors.
        # Check that before rollover/topology maintenance, otherwise a later
        # local-day refresh could unexpectedly undo a manual operator change.
        # A fault remains latched until reset, but a successful fault-triggered
        # Safe-OFF releases actor ownership for manual troubleshooting.
        if state.get("fault"):
            if not state.get("fault_safe_off_complete"):
                await self.async_safe_off(cascade_id, "cascade fault")
            return
        if not state["enabled"] or state.get("hands_off"):
            return
        if safety_reason is not None:
            await self.async_safety_off(cascade_id, safety_reason, now)
            return
        if (
            state.get("episode_day")
            and state.get("episode_day") != day
            and state.get("phase")
            in (
                "running",
                "proving",
                "waking",
                "waking_members",
            )
        ):
            pending = bool(state.get("recovery_pending"))
            await self.async_safe_off(cascade_id, "local day rollover")
            if pending and state.get("phase") != "fault":
                # Export-backed discharge may legitimately cross midnight.
                # Preserve its recovery contract and prevent a second Aux
                # episode until the members have reached 50 % again.
                state["episode_day"] = day
                state["phase"] = "recovering"
        if state.get("episode_day") != day:
            state["retry_used"] = False
            state["retry_at"] = None
        topology = self._topology(cascade_id)
        if topology is None:
            await self.async_safe_off(cascade_id, "invalid topology")
            return
        self._adopt_shared_safe_off(cascade_id, topology, plan)
        foreign_mode = self._foreign_override(cascade_id, topology)
        if foreign_mode == ACTOR_MODE_SHARED:
            state["hands_off"] = True
            state["enabled"] = False
            state["phase"] = "hands_off"
            state["claims"] = {}
            self.coordinator._save_persistent_state()
            return
        if foreign_mode == ACTOR_MODE_EXCLUSIVE:
            await self._fault(cascade_id, "exclusive_actor_changed_externally")
            await self.async_safe_off(cascade_id, "exclusive actor override")
            return
        by_id = {item.load_id: item for item in load_states}
        if state.get("recovery_pending"):
            # A stopped episode can remain in ``recovering`` across midnight.
            # Carrying the old episode day into the Core would make it eligible
            # for another Aux start before the promised refill has completed.
            if state.get("episode_day") != day:
                state["episode_day"] = day
            still_pending = []
            for load_id, data in topology["members"]:
                member_state = by_id.get(load_id)
                recovery = float(data.get(CONF_LOAD_RECOVERY_SOC, 50.0))
                if (
                    member_state is None
                    or member_state.soc_source != "live"
                    or member_state.soc_percent is None
                    or member_state.soc_percent < recovery
                ):
                    still_pending.append(load_id)
            state["recovery_pending"] = still_pending
            if not still_pending:
                state["phase"] = "complete"
                state["recovery_deadline"] = None
                ir.async_delete_issue(
                    self.coordinator.hass,
                    DOMAIN,
                    f"cascade_recovery_{self.coordinator.entry.entry_id}_{cascade_id}",
                )
            elif (
                deadline := self._recovery_deadline(state, plan)
            ) is not None and now > deadline:
                ir.async_create_issue(
                    self.coordinator.hass,
                    DOMAIN,
                    f"cascade_recovery_{self.coordinator.entry.entry_id}_{cascade_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="cascade_recovery_missed",
                )
        if any(
            not self.coordinator.load_bm_enabled(load_id)
            for load_id, _data in topology["members"]
        ) or not self.coordinator.load_bm_enabled(topology["terminal_id"]):
            await self.async_safe_off(cascade_id, "member automation disabled")
            return
        aux_segments = [
            segment
            for flow in plan.flows[:1]
            for segment in flow.segments
            if segment.source == "aux"
        ]
        desired_source = aux_segments[0].source_load_id if aux_segments else None
        if state.get("phase") == "waking_members":
            mode = state.get("wake_mode")
            matching_plan = (
                mode == "aux" and desired_source == state.get("source")
            ) or (mode == "root" and plan.flows and plan.flows[0].root_input_wh > 0.0)
            if matching_plan and await self._continue_member_wake(
                cascade_id, topology, plan, now
            ):
                return
            if mode == "root":
                await self._fault(cascade_id, "root_transition_failed")
            else:
                if state["retry_used"]:
                    await self._fault(cascade_id, "wake_failed_after_retry")
                else:
                    state["retry_used"] = True
                    state["retry_at"] = (now + _RETRY_DELAY).isoformat()
            await self.async_safe_off(cascade_id, "member wake failure")
            self.coordinator._save_persistent_state()
            return
        if state.get("phase") == "proving":
            proven = self._power_proven(cascade_id, now)
            if proven is True:
                state["phase"] = "running"
                state["episode_day"] = day
                state["recovery_pending"] = [
                    load_id
                    for member_index, (load_id, data) in enumerate(topology["members"])
                    if (
                        (member_state := by_id.get(load_id)) is None
                        or member_state.soc_source != "live"
                        or member_state.soc_percent is None
                        or member_state.soc_percent
                        < float(data.get(CONF_LOAD_RECOVERY_SOC, 50.0))
                        or any(
                            member_index < len(flow.member_flows)
                            and flow.member_flows[member_index].soc_end_percent
                            < float(data.get(CONF_LOAD_RECOVERY_SOC, 50.0))
                            for flow in plan.flows
                        )
                    )
                ]
                state["recovery_deadline"] = (
                    plan.recovery_deadline.isoformat()
                    if state["recovery_pending"] and plan.recovery_deadline
                    else None
                )
                state["retry_used"] = False
                state["retry_at"] = None
                self.coordinator._save_persistent_state()
            elif proven is False:
                await self._fault(cascade_id, "source_power_proof_failed")
                await self.async_safe_off(cascade_id, "power proof failed")
            return

        if state.get("phase") == "running":
            if desired_source is None:
                # A freshly replanned slot can withdraw Aux immediately (for
                # example after the load was satisfied during the wake/proof
                # window).  `running` describes the old physical episode and
                # must never keep that path energised without a matching new
                # segment.
                pending = bool(state.get("recovery_pending"))
                await self.async_safe_off(cascade_id, "Aux plan withdrawn")
                if state.get("phase") != "fault":
                    state["phase"] = "recovering" if pending else "complete"
                    self.coordinator._save_persistent_state()
                return
            source = state.get("source")
            source_state = next(
                (item for item in load_states if item.load_id == source), None
            )
            source_data = next(
                data for load_id, data in topology["members"] if load_id == source
            )
            floor = float(source_data.get(CONF_LOAD_DISCHARGE_FLOOR_SOC, 20.0))
            planned_source = next(
                (
                    member_flow
                    for flow in plan.flows[:1]
                    for member_flow in flow.member_flows
                    if member_flow.load_id == source
                ),
                None,
            )
            target = max(
                floor,
                min(
                    float(source_data.get(CONF_LOAD_RECOVERY_SOC, 50.0)),
                    planned_source.soc_end_percent
                    if planned_source is not None
                    else float(source_data.get(CONF_LOAD_RECOVERY_SOC, 50.0)),
                ),
            )
            leaf_power_entity = topology["members"][-1][1].get(
                CONF_LOAD_OUTPUT_POWER_ENTITY
            )
            leaf_power = (
                self.coordinator._read_float(leaf_power_entity)
                if leaf_power_entity
                else None
            )
            telemetry_missing = (
                source_state is None
                or source_state.soc_source != "live"
                or source_state.soc_percent is None
                or leaf_power is None
            )
            if telemetry_missing:
                since = self._telemetry_unknown_since.setdefault(cascade_id, now)
                self._aux_tick.pop(cascade_id, None)
                if now - since >= timedelta(minutes=10):
                    await self.async_safe_off(cascade_id, "telemetry unavailable")
                    state["phase"] = "recovering"
                    state["warning"] = "telemetry_unavailable"
                return
            self._telemetry_unknown_since.pop(cascade_id, None)
            previous = self._aux_tick.get(cascade_id)
            if previous is not None:
                elapsed = (now - previous[0]).total_seconds()
                if 0.0 < elapsed <= 600.0:
                    state["aux_today_wh"] += (
                        (previous[1] + leaf_power) / 2.0 * elapsed / 3600.0
                    )
            self._aux_tick[cascade_id] = (now, leaf_power)
            if source_state.soc_percent <= target:
                if desired_source and desired_source != source:
                    if not await self._handover(cascade_id, desired_source):
                        await self._fault(cascade_id, "handover_failed_at_target")
                        await self.async_safe_off(cascade_id, "handover failure")
                else:
                    pending = bool(state.get("recovery_pending"))
                    await self.async_safe_off(cascade_id, "source target")
                    state["phase"] = "recovering" if pending else "complete"
            return

        if desired_source and state.get("episode_day") != day:
            retry_at = state.get("retry_at")
            if retry_at:
                try:
                    if now < datetime.fromisoformat(retry_at):
                        return
                except ValueError:
                    pass
            if not await self._wake(cascade_id, desired_source, now):
                await self.async_safe_off(cascade_id, "wake failure")
                if not state["retry_used"]:
                    state["retry_used"] = True
                    state["retry_at"] = (now + _RETRY_DELAY).isoformat()
                else:
                    await self._fault(cascade_id, "wake_failed_after_retry")
                self.coordinator._save_persistent_state()
            return

        if plan.flows and plan.flows[0].root_input_wh > 0.0:
            if not await self._apply_root(cascade_id, plan, now):
                await self._fault(cascade_id, "root_transition_failed")
                await self.async_safe_off(cascade_id, "Root transition")
            return

        # No auxiliary or Root segment now: all physical actors stay safe-off.
        # `complete` and `recovering` are informative terminal phases, not
        # permission to leave the last Aux path powered.  Preserve the phase
        # across the idempotent Safe-OFF operation so the UI can still explain
        # why the chain is idle.
        terminal_phase = state.get("phase")
        await self.async_safe_off(cascade_id, "no active cascade segment")
        if (
            terminal_phase in ("recovering", "complete")
            and state.get("phase") != "fault"
        ):
            state["phase"] = terminal_phase
            self.coordinator._save_persistent_state()

    async def async_apply(
        self,
        config: SystemConfig,
        result: PlanResult,
        load_states: tuple[SurplusLoadState, ...],
        now: datetime,
        safety_reason: str | None = None,
    ) -> None:
        plans = {plan.cascade_id: plan for plan in result.cascade_plans}
        tasks = []
        for cascade in config.cascades:
            plan = plans.get(cascade.cascade_id)
            if plan is None:
                continue
            lock = self._locks.setdefault(cascade.cascade_id, asyncio.Lock())

            async def run_one(
                cid: str = cascade.cascade_id,
                item: Any = plan,
                owned: asyncio.Lock = lock,
            ) -> None:
                async with owned:
                    await self._apply_one(
                        cid, item, load_states, now, safety_reason=safety_reason
                    )

            tasks.append(run_one())
        if tasks:
            await asyncio.gather(*tasks)

    def _schedule_payload(
        self,
        cascade: Any,
        plan: Any,
        slots: tuple[HourSlot, ...],
    ) -> list[dict[str, Any]]:
        """Publish one inspectable timeline block per occupied cascade slot."""
        if plan is None:
            return []
        subentries = self.coordinator.entry.subentries
        terminal = subentries.get(cascade.terminal_load_id)
        terminal_name = (
            terminal.title if terminal is not None else cascade.terminal_load_id
        )
        member_names = {
            member.load_id: (
                subentries[member.load_id].title
                if member.load_id in subentries
                else member.load_id
            )
            for member in cascade.members
        }
        member_indexes = {
            member.load_id: index for index, member in enumerate(cascade.members)
        }
        schedule: list[dict[str, Any]] = []
        for slot, flow in zip(slots, plan.flows, strict=False):
            activities: list[dict[str, Any]] = []
            for member_flow in flow.member_flows:
                charge_wh = float(member_flow.own_charge_input_wh)
                if charge_wh > 0.0:
                    activities.append(
                        {
                            "kind": "charge",
                            "load_id": member_flow.load_id,
                            "name": member_names.get(
                                member_flow.load_id, member_flow.load_id
                            ),
                            "energy_wh": round(charge_wh, 1),
                            "stored_energy_wh": round(
                                float(member_flow.battery_charge_wh), 1
                            ),
                            "soc_start_percent": round(
                                float(member_flow.soc_start_percent), 1
                            ),
                            "soc_end_percent": round(
                                float(member_flow.soc_end_percent), 1
                            ),
                            "source": "root",
                        }
                    )
                discharge_wh = float(member_flow.battery_discharge_wh)
                if discharge_wh > 0.0:
                    activities.append(
                        {
                            "kind": "discharge",
                            "load_id": member_flow.load_id,
                            "name": member_names.get(
                                member_flow.load_id, member_flow.load_id
                            ),
                            "energy_wh": round(discharge_wh, 1),
                            "soc_start_percent": round(
                                float(member_flow.soc_start_percent), 1
                            ),
                            "soc_end_percent": round(
                                float(member_flow.soc_end_percent), 1
                            ),
                            "source": "aux",
                        }
                    )
            for segment in flow.segments:
                terminal_wh = float(segment.terminal_energy_wh)
                if terminal_wh <= 0.0:
                    continue
                source = "aux" if segment.source == "aux" else "root"
                source_entry = (
                    subentries.get(segment.source_load_id)
                    if segment.source_load_id
                    else None
                )
                activities.append(
                    {
                        "kind": "terminal",
                        "load_id": cascade.terminal_load_id,
                        "name": terminal_name,
                        "energy_wh": round(terminal_wh, 1),
                        "source": source,
                        "source_load_id": segment.source_load_id,
                        "source_name": (
                            source_entry.title if source_entry is not None else None
                        ),
                    }
                )
            if not activities and float(flow.root_input_wh) <= 0.0:
                continue
            sources = list(
                dict.fromkeys(
                    item["source"]
                    for item in activities
                    if item["kind"] in ("charge", "discharge", "terminal")
                )
            )

            # Publish the planned AC-output path explicitly.  The frontend
            # must not have to reconstruct electrical topology from energy
            # totals: Root terminal service needs every output, charging a
            # deeper member needs its upstream outputs, and an Aux source
            # needs its own plus every downstream output.
            active_output_indexes: set[int] = set()
            output_sources: dict[int, set[str]] = {}

            def mark_outputs(
                indexes: range,
                source: str,
                active_indexes: set[int] = active_output_indexes,
                sources_by_index: dict[int, set[str]] = output_sources,
            ) -> None:
                for index in indexes:
                    active_indexes.add(index)
                    sources_by_index.setdefault(index, set()).add(source)

            for activity in activities:
                if activity["kind"] == "charge":
                    member_index = member_indexes.get(activity["load_id"])
                    if member_index is not None:
                        mark_outputs(range(member_index), "root")
                elif activity["kind"] == "terminal":
                    if activity["source"] == "root":
                        mark_outputs(range(len(cascade.members)), "root")
                    else:
                        source_index = member_indexes.get(activity["source_load_id"])
                        if source_index is not None:
                            mark_outputs(
                                range(source_index, len(cascade.members)), "aux"
                            )
            for index in sorted(active_output_indexes):
                member = cascade.members[index]
                activities.append(
                    {
                        "kind": "output",
                        "load_id": member.load_id,
                        "name": member_names[member.load_id],
                        "sources": sorted(output_sources[index]),
                    }
                )
            schedule.append(
                {
                    "start": slot.start.isoformat(),
                    "end": (slot.start + timedelta(hours=slot.duration)).isoformat(),
                    "root_input_wh": round(float(flow.root_input_wh), 1),
                    "terminal_energy_wh": round(
                        sum(
                            float(segment.terminal_energy_wh)
                            for segment in flow.segments
                        ),
                        1,
                    ),
                    "sources": sources,
                    "activities": activities,
                }
            )
        return schedule

    def payload(
        self,
        result: PlanResult,
        config: SystemConfig,
        slots: tuple[HourSlot, ...] = (),
    ) -> dict[str, Any]:
        plans = {plan.cascade_id: plan for plan in result.cascade_plans}
        payload: dict[str, Any] = {}
        for cascade in config.cascades:
            plan = plans.get(cascade.cascade_id)
            state = self._state(cascade.cascade_id)
            # Keep aggregate SOC visible for diagnosis, but never advertise
            # executable energy or schedule blocks after a hard fault/hands-off.
            # The following refresh also removes these loads from the global
            # trajectory through ``planning_blocked_load_ids``; this immediate
            # payload gate closes the UI seam in the faulting refresh itself.
            effective_plan = (
                None if state.get("fault") or state.get("hands_off") else plan
            )
            topology = self._topology(cascade.cascade_id)
            actors = []
            if topology is not None:
                for _load_id, values in topology["members"]:
                    actors.extend(
                        values.get(key)
                        for key in (
                            CONF_LOAD_CONTROL_SWITCH,
                            CONF_LOAD_CHARGE_ENABLE,
                            CONF_LOAD_OUTPUT_SWITCH,
                        )
                        if values.get(key)
                    )
                terminal_actor = topology["terminal"].get(CONF_LOAD_CONTROL_SWITCH)
                if terminal_actor:
                    actors.append(terminal_actor)
            assumed_state_actors = [
                entity_id
                for entity_id in actors
                if (actor_state := self.coordinator.hass.states.get(entity_id))
                is not None
                and actor_state.attributes.get("assumed_state")
            ]
            member_details = []
            for member_index, member in enumerate(cascade.members):
                current_soc = None
                if plan and plan.flows:
                    first_members = plan.flows[0].member_flows
                    if member_index < len(first_members):
                        current_soc = round(
                            float(first_members[member_index].soc_start_percent), 1
                        )
                soc_forecast = []
                if effective_plan:
                    for slot_index, (slot, flow) in enumerate(
                        zip(slots, effective_plan.flows, strict=False)
                    ):
                        if member_index >= len(flow.member_flows):
                            continue
                        member_flow = flow.member_flows[member_index]
                        if slot_index == 0:
                            soc_forecast.append(
                                {
                                    "t": slot.start.isoformat(),
                                    "soc": round(
                                        float(member_flow.soc_start_percent), 1
                                    ),
                                }
                            )
                        soc_forecast.append(
                            {
                                "t": (
                                    slot.start + timedelta(hours=slot.duration)
                                ).isoformat(),
                                "soc": round(float(member_flow.soc_end_percent), 1),
                            }
                        )
                member_details.append(
                    {
                        "load_id": member.load_id,
                        "name": (
                            self.coordinator.entry.subentries[member.load_id].title
                            if member.load_id in self.coordinator.entry.subentries
                            else member.load_id
                        ),
                        "soc_percent": current_soc,
                        "target_soc_percent": member.recovery_soc_percent,
                        "soc_forecast": soc_forecast,
                    }
                )
            payload[cascade.cascade_id] = {
                "name": self.coordinator.entry.subentries[cascade.cascade_id].title,
                "enabled": bool(state["enabled"]),
                "phase": state["phase"],
                "source": state["source"],
                "source_name": (
                    self.coordinator.entry.subentries[state["source"]].title
                    if state.get("source")
                    and state["source"] in self.coordinator.entry.subentries
                    else None
                ),
                "fault": state["fault"],
                "fault_detail": state.get("fault_detail"),
                "warning": state.get("warning"),
                "hands_off": bool(state["hands_off"]),
                "assumed_state_actors": assumed_state_actors,
                "retry_at": state["retry_at"],
                "aggregate_soc_percent": plan.aggregate_soc_percent if plan else None,
                "aggregate_soc_stale": plan.aggregate_soc_stale if plan else False,
                "planned_root_energy_kwh": (
                    round(effective_plan.planned_root_energy_wh / 1000.0, 3)
                    if effective_plan
                    else 0.0
                ),
                "planned_aux_energy_kwh": (
                    round(effective_plan.planned_aux_energy_wh / 1000.0, 3)
                    if effective_plan
                    else 0.0
                ),
                "actual_aux_energy_kwh": round(
                    float(state.get("aux_today_wh", 0.0)) / 1000.0, 3
                ),
                "recovery_deadline": (
                    deadline.isoformat()
                    if (deadline := self._recovery_deadline(state, plan))
                    else None
                ),
                "members": [member.load_id for member in cascade.members],
                "member_details": member_details,
                "terminal_load_id": cascade.terminal_load_id,
                "terminal_name": (
                    self.coordinator.entry.subentries[cascade.terminal_load_id].title
                    if cascade.terminal_load_id in self.coordinator.entry.subentries
                    else cascade.terminal_load_id
                ),
                # F-CASCADE-STORAGE: normal load lanes are intentionally
                # suppressed while the cascade owns them.  This dedicated
                # timeline preserves the per-slot plan instead of leaving the
                # operator with horizon totals only.
                "schedule": self._schedule_payload(cascade, effective_plan, slots),
            }
        return payload
