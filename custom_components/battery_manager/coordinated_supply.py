"""Ordered house-supply transitions and physical confirmation policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .actor_ownership import SUPPLY_OWNER
from .const import (
    CONF_DCDC_SWITCH,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
)
from .core import SystemConfig
from .core.live_ac import LIVE_AC_PLAN_MAX_AGE_S
from .core.model import PlanInputs, SocCoordinateSnapshot
from .core.support import support_state
from .voltage_gate import requested_dc48

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


def reconcile_supply_feedback(
    self: BatteryManagerCoordinator, entity_id: str | None = None
) -> bool:
    """Adopt our confirmed edges; service completion is never physical proof."""
    changed = False
    for entity, request in self._supply_actor_requests.items():
        # A failed OFF service can still reach the relay. Only the physical
        # OFF publication settles that uncertainty.
        awaiting = request.state == "pending" or (
            request.state == "service_failed" and not request.desired
        )
        if (
            (entity_id is not None and entity != entity_id)
            or not awaiting
            or self._entity_tristate(entity) is not request.desired
        ):
            continue
        request.state = "confirmed"
        self.source_phases.confirmed(entity, request.desired, dt_util.utcnow())
        changed = True
        if entity in (
            self.raw_config.get(CONF_SUPPORT_DC24_SWITCH),
            self.raw_config.get(CONF_SUPPORT_DC48_SWITCH),
        ):
            self._last_support_switch = dt_util.now()
    for key, conf_key in (
        ("dc24", CONF_SUPPORT_DC24_SWITCH),
        ("dc48", CONF_SUPPORT_DC48_SWITCH),
    ):
        psu_entity = self.raw_config.get(conf_key)
        psu_request = (
            self._supply_actor_requests.get(psu_entity) if psu_entity else None
        )
        if (
            psu_entity is None
            or psu_request is None
            or psu_request.state != "confirmed"
            or self._entity_tristate(psu_entity) is not psu_request.desired
        ):
            continue
        if (
            key == "dc24"
            and (dcdc := self.raw_config.get(CONF_DCDC_SWITCH))
            and self._entity_tristate(dcdc) is not (not psu_request.desired)
        ):
            continue
        self._support_state[key] = psu_request.desired
    return changed


@dataclass(frozen=True)
class SupplyRequest:
    """A queued intent is valid only for the planner revision that issued it."""

    sources: tuple[bool, bool]
    inverter: bool
    plan_revision: int
    expires: datetime | None
    soc_coordinate: SocCoordinateSnapshot | None = None


def _record_expiry(inputs: PlanInputs) -> datetime:
    """Legacy source plans also end at their first slot or the freshness limit."""
    return min(
        dt_util.as_utc(inputs.now) + timedelta(seconds=LIVE_AC_PLAN_MAX_AGE_S),
        dt_util.as_utc(inputs.slots[0].start)
        + timedelta(hours=inputs.slots[0].duration)
        if inputs.slots
        else dt_util.as_utc(inputs.now),
    )


def protection_targets(
    self: BatteryManagerCoordinator,
    config: SystemConfig,
    soc: float | None,
    now: datetime,
) -> dict[str, bool]:
    """AC protection preserves valid DC intent, including late actor feedback.

    The October rail chatter came from treating an unfinished confirmation as
    an economic OFF request. Expired plans, unknown SOC and grid loss still
    receive only physical protection, never an old economic permission.
    """
    protected = support_state(
        config,
        soc if soc is not None else config.battery.soc_min_percent,
        config.support.dc24_active,
        config.support.dc48_active,
        False,
    )
    planned = (False, False)
    if (
        soc is not None
        and self._reserve_grid_available() is True
        and self.published_pv_authority_valid()
    ):
        envelope = self.live_ac.envelope
        if envelope is not None:
            if dt_util.as_utc(now) < envelope.expires:
                planned = envelope.dc_sources
        elif (record := self._last_planner_recording) is not None:
            _, inputs, result = record
            if dt_util.as_utc(now) < _record_expiry(inputs):
                planned = result.support_dc24_now, requested_dc48(result)
    return {
        key: safety or economic
        for key, safety, economic in zip(
            ("dc24", "dc48"), protected, planned, strict=True
        )
    }


def capture_supply_request(
    self: BatteryManagerCoordinator,
    desired: dict[str, bool],
    inverter: bool,
    now: datetime,
) -> SupplyRequest:
    """Freeze planner provenance synchronously, before creating a queued task."""
    envelope = self.live_ac.envelope
    record = self._last_planner_recording
    return SupplyRequest(
        (desired["dc24"], desired["dc48"]),
        inverter,
        self.live_ac.plan_revision,
        envelope.expires
        if envelope is not None
        else _record_expiry(record[1])
        if record is not None
        else None,
        envelope.soc_coordinate
        if envelope is not None
        else record[1].soc_coordinate
        if record is not None
        else None,
    )


async def execute_coordinated_support(
    self: BatteryManagerCoordinator,
    desired: dict[str, bool],
    inverter: bool,
    config: SystemConfig,
    now: datetime,
    *,
    request: SupplyRequest | None = None,
) -> None:
    if request is None:
        request = capture_supply_request(self, desired, inverter, now)
    desired = dict(zip(("dc24", "dc48"), request.sources, strict=True))
    inverter = request.inverter
    self._coordinated_support_diag = {
        "mode": "coordinated",
        "reason": "waiting_for_lock",
    }
    try:
        async with self._switch_lock:
            reconcile_supply_feedback(self)
            envelope = self.live_ac.envelope
            forecast_valid = self.published_pv_authority_valid()
            if (
                not forecast_valid
                or request.plan_revision != self.live_ac.plan_revision
                or (request.expires is not None and dt_util.utcnow() >= request.expires)
            ):
                valid = (
                    forecast_valid
                    and envelope is not None
                    and dt_util.utcnow() < envelope.expires
                )
                desired = dict(
                    zip(
                        ("dc24", "dc48"),
                        envelope.dc_sources
                        if envelope is not None and valid
                        else (False, False),
                        strict=True,
                    )
                )
                inverter = bool(valid and self.live_ac.planned_limit() > 0)
                if envelope is not None:
                    config = envelope.config
                expires = envelope.expires if envelope is not None and valid else None
                record = self._last_planner_recording
                if (
                    envelope is None
                    and record is not None
                    and not record[0].reserve.enabled
                ):
                    config, recorded_inputs, recorded_result = record
                    expires = _record_expiry(recorded_inputs)
                    valid = (
                        forecast_valid
                        and not self._actuation_shutdown
                        and dt_util.utcnow() < expires
                    )
                    desired = {
                        "dc24": valid and recorded_result.support_dc24_now,
                        "dc48": valid and requested_dc48(recorded_result),
                    }
                    inverter = valid and bool(
                        getattr(
                            self,
                            "_coordinated_inverter_target",
                            recorded_result.inverter_on,
                        )
                    )
                request = SupplyRequest(
                    (desired["dc24"], desired["dc48"]),
                    inverter,
                    self.live_ac.plan_revision,
                    expires if valid else None,
                    envelope.soc_coordinate
                    if envelope is not None
                    else record[1].soc_coordinate
                    if record is not None
                    else None,
                )
            now = dt_util.now()
            psu24 = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
            psu48 = self.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
            dcdc = self.raw_config.get(CONF_DCDC_SWITCH)
            diag: dict[str, Any] = {
                "mode": "coordinated",
                "reason": "settled",
                "desired": dict(desired),
                "grid_restore_pending": [],
            }
            self._coordinated_support_diag = diag
            sources_before_transition = {
                "dc24": bool(psu24 and self._entity_tristate(psu24) is True),
                "dc48": bool(psu48 and self._entity_tristate(psu48) is True),
            }

            async def confirmed(entity: str, on: bool) -> bool:
                if self._entity_tristate(entity) is on:
                    return True
                if not await self._switch_entity(entity, on, actor_owner=SUPPLY_OWNER):
                    diag["reason"] = "command_failed"
                    return False
                if self._entity_tristate(entity) is not on:
                    diag["reason"] = "awaiting_confirmation"
                    return False
                return True

            def inverter_blocked() -> bool:
                if self._inverter_limit_confirmed(True):
                    return True
                self._inverter_recommendation = False
                self._inverter_control["confirmed"] = False
                diag["reason"] = "inverter_block_unconfirmed"
                return False

            def pending_off(entity: str) -> bool:
                # An old ON report cannot authorize removing the other rail
                # source while our OFF request is still catching up.
                command = self._supply_actor_requests.get(entity)
                return bool(
                    command
                    and not command.desired
                    and command.state in ("pending", "service_failed")
                )

            def waiting_for_dcdc_off() -> bool:
                if not dcdc or not pending_off(dcdc):
                    return False
                # A command to turn DC/DC back on would replace our pending
                # OFF request and could adopt the old ON as its confirmation.
                diag["reason"] = "awaiting_confirmation"
                self._schedule_replan()
                return True

            def transfer_verified() -> bool:
                if not config.reserve.enabled or self.raw_config.get(
                    CONF_RESERVE_TRANSFER_VERIFIED
                ):
                    return True
                diag["dc24_block_reason"] = "transfer_unverified"
                return False

            manual_changed = (
                config.support.dc24_forced_on != self._support_manual["dc24"]
                or config.support.dc48_forced_on != self._support_manual["dc48"]
            )
            if manual_changed or config.reserve.enabled:
                # Planning may have waited while either SOC or a manual request
                # changed. The ownership lock protects this final input check.
                soc = self._get_soc(dt_util.now())
                latched = {"dc24": False, "dc48": False}
                if manual_changed:
                    cancelled = {
                        "dc24": config.support.dc24_forced_on
                        and not self._support_manual["dc24"],
                        "dc48": config.support.dc48_forced_on
                        and not self._support_manual["dc48"],
                    }
                    config = replace(
                        config,
                        support=replace(
                            config.support,
                            dc24_forced_on=self._support_manual["dc24"],
                            dc48_forced_on=self._support_manual["dc48"],
                        ),
                    )
                    for key, entity, recovery in (
                        ("dc24", psu24, config.control.support_dc24_recovery_soc),
                        ("dc48", psu48, config.control.support_dc48_recovery_soc),
                    ):
                        actual = self._entity_tristate(entity) if entity else False
                        # Only the cancelled manual path loses its old hold.
                        # Other protection latches retain their core release
                        # rules, including PV recovery for the 24 V rail.
                        latched[key] = (
                            self._support_state[key] if actual is None else actual
                        ) and (not cancelled[key] or soc is None or soc < recovery)
                    # A cancelled manual ON cannot survive in the old targets.
                    # Retain only current protection until a fresh economic plan.
                    desired = {"dc24": False, "dc48": False}
                    inverter = False
                protect24, protect48 = support_state(
                    config,
                    soc if soc is not None else config.battery.soc_min_percent,
                    latched["dc24"],
                    latched["dc48"],
                    False,
                )
                desired = {
                    "dc24": desired["dc24"] or protect24,
                    "dc48": desired["dc48"] or protect48,
                }
                if soc is None or soc <= config.control.inverter_min_soc_percent:
                    inverter = False
                diag["desired"] = dict(desired)

            planned_desired = dict(desired)
            diag["requested"] = dict(planned_desired)

            def phase_changed() -> bool:
                if not config.reserve.enabled or diag.get("pv_priority"):
                    return False
                soc = self._get_soc(dt_util.now())
                protective = support_state(
                    config,
                    soc if soc is not None else config.battery.soc_min_percent,
                    False,
                    False,
                    False,
                )
                if any(protective):
                    return False  # A stale economic intent never delays protection.
                phases = self.source_phases.deadlines(dt_util.utcnow())
                captured = (
                    config.support.dc24_phase_until,
                    config.support.dc48_phase_until,
                )
                return any(
                    current > previous and desired[key] != self._support_state[key]
                    for key, current, previous in zip(
                        ("dc24", "dc48"),
                        phases,
                        captured,
                        strict=True,
                    )
                )

            if phase_changed():
                # A source was confirmed while this plan was computing.
                self._inverter_recommendation = False
                await self._confirm_inverter_limit(True, diag)
                diag["reason"] = "source_phase_changed"
                self._schedule_replan()
                return
            diag["source_phases"] = self.source_phases.diagnostic(dt_util.utcnow())
            desired = self.voltage_gate.apply(config, desired, now)
            diag["desired"] = dict(desired)
            diag["voltage_gate"] = dict(self.voltage_gate.diagnostics)

            def may_remove() -> bool:
                """Every await can revoke source permission, including rail overlap."""
                recorded_revision = getattr(
                    self, "_last_planner_recording_revision", None
                )
                changed = request.plan_revision != self.live_ac.plan_revision or (
                    self.live_ac.envelope is None
                    and recorded_revision is not None
                    and recorded_revision != self.live_ac.plan_revision
                )
                expired = (
                    request.expires is not None and dt_util.utcnow() >= request.expires
                )
                coordinate_valid = self.voltage_gate.coordinate_permission_valid(
                    request.soc_coordinate,
                    direct_pv_proven=bool(diag.get("pv_priority"))
                    and self.live_ac.pv_source_permission(config),
                )
                if not coordinate_valid:
                    diag["reason"] = "soc_coordinate_hold"
                    self._schedule_replan()
                elif changed or expired:
                    diag["reason"] = "plan_changed" if changed else "plan_expired"
                elif (
                    not diag.get("pv_priority")
                    and not self.published_pv_authority_valid()
                ):
                    diag["reason"] = "pv_forecast_evidence_lost"
                    self._schedule_replan()
                elif phase_changed():
                    diag["reason"] = "source_phase_changed"
                    self._schedule_replan()
                elif diag.get("pv_priority") and not self.live_ac.pv_source_permission(
                    config
                ):
                    diag["reason"] = "pv_permission_expired"
                elif (
                    config.reserve.enabled
                    and self._reserve_grid_available() is not True
                ):
                    diag["reason"] = "grid_supply_changed"
                elif (
                    not diag.get("pv_priority")
                    and self.voltage_gate.apply(config, planned_desired, dt_util.now())[
                        "dc48"
                    ]
                    != desired["dc48"]
                ):
                    diag["reason"] = "voltage_gate_changed"
                elif config.reserve.enabled:
                    current_soc = self._get_soc(dt_util.now())
                    protect = support_state(
                        config,
                        current_soc
                        if current_soc is not None
                        else config.battery.soc_min_percent,
                        False,
                        False,
                        False,
                    )
                    if any(
                        required and not desired[key]
                        for key, required in zip(("dc24", "dc48"), protect, strict=True)
                    ):
                        diag["reason"] = "soc_protection"
                    else:
                        return True
                else:
                    return True
                self.live_ac.pv_active = False
                self.live_ac._pv_restore_pending = True
                return False

            async def restore_required() -> None:
                """Restore through this owner before releasing its transition lock."""
                if diag["reason"] == "source_phase_changed":
                    # The old envelope cannot authorize undoing a source whose
                    # late confirmation invalidated this transition.
                    await self._confirm_inverter_limit(True, diag)
                    return
                if self._reserve_grid_available() is not True:
                    return
                current = self.live_ac.envelope
                forecast_valid = self.published_pv_authority_valid()
                target = (
                    dict(planned_desired)
                    if forecast_valid
                    else {"dc24": False, "dc48": False}
                )
                if current is not None:
                    target = dict(
                        zip(
                            ("dc24", "dc48"),
                            current.dc_sources
                            if forecast_valid and dt_util.utcnow() < current.expires
                            else (False, False),
                            strict=True,
                        )
                    )
                restore_config = current.config if current is not None else config
                record = self._last_planner_recording
                if (
                    current is None
                    and record is not None
                    and not record[0].reserve.enabled
                ):
                    restore_config, recorded_inputs, recorded_result = record
                    valid = (
                        forecast_valid
                        and not self._actuation_shutdown
                        and dt_util.utcnow() < _record_expiry(recorded_inputs)
                    )
                    target = {
                        "dc24": valid and recorded_result.support_dc24_now,
                        "dc48": valid and requested_dc48(recorded_result),
                    }
                current_soc = self._get_soc(dt_util.now())
                protect = support_state(
                    restore_config,
                    current_soc
                    if current_soc is not None
                    else restore_config.battery.soc_min_percent,
                    False,
                    False,
                    False,
                )
                target = {
                    key: target[key] or required or self._support_manual[key]
                    for key, required in zip(("dc24", "dc48"), protect, strict=True)
                }
                diag["requested"] = dict(target)
                diag["pv_priority"] = False
                target = self.voltage_gate.apply(restore_config, target, dt_util.now())
                if diag["reason"] == "soc_coordinate_hold":
                    # A rejected optional removal cannot be retried via restore.
                    # Restore sources already removed before a late revocation.
                    target = {
                        key: target[key] or sources_before_transition[key]
                        for key in target
                    }
                diag["desired"] = target
                diag["voltage_gate"] = dict(self.voltage_gate.diagnostics)
                if any(target.values()):
                    self._inverter_recommendation = False
                    if not await self._confirm_inverter_limit(True, diag):
                        return
                if (
                    target["dc24"]
                    and psu24
                    and transfer_verified()
                    and self._reserve_grid_available() is True
                ):

                    def may_restore_rail() -> bool:
                        return (
                            self._reserve_grid_available() is True
                            and transfer_verified()
                            and inverter_blocked()
                            and not pending_off(psu24)
                        )

                    if dcdc:
                        restored = await self._sequence_dc24(
                            True,
                            psu24,
                            may_start=may_restore_rail,
                            may_remove=may_restore_rail,
                        )
                        if not restored or self._entity_tristate(dcdc) is not False:
                            return
                    elif not may_restore_rail() or not await confirmed(psu24, True):
                        return
                    self._support_state["dc24"] = True
                if (
                    psu48
                    and self._reserve_grid_available() is True
                    and (
                        not pending_off(psu48) and inverter_blocked()
                        if target["dc48"]
                        else may_remove()
                    )
                    and await confirmed(psu48, target["dc48"])
                ):
                    self._support_state["dc48"] = target["dc48"]

            def grid_unavailable() -> bool:
                return self._reserve_grid_available() is not True

            rail_off_requested = False

            def may_shed_rail() -> bool:
                nonlocal rail_off_requested
                if grid_unavailable():
                    rail_off_requested = True
                    return True
                return False

            async def shed_grid_sources() -> bool:
                """Stop using grid sources only while the outage still holds."""
                if waiting_for_dcdc_off():
                    return False
                if (
                    dcdc
                    and psu24
                    and (
                        self._entity_tristate(dcdc) is not True
                        or self._entity_tristate(psu24) is not False
                    )
                ):
                    if not await self._sequence_dc24(
                        False,
                        psu24,
                        may_start=grid_unavailable,
                        may_remove=may_shed_rail,
                    ):
                        return False
                    if self._entity_tristate(psu24) is not False:
                        diag["reason"] = "awaiting_confirmation"
                        return False
                elif dcdc and not await confirmed(dcdc, True):
                    return False
                if not grid_unavailable():
                    return False
                if psu24 and not dcdc and not await confirmed(psu24, False):
                    return False
                self._support_state["dc24"] = False
                if not grid_unavailable():
                    return False
                if dcdc and self._entity_tristate(dcdc) is not True:
                    return False
                if psu48 and not await confirmed(psu48, False):
                    return False
                self._support_state["dc48"] = False
                return grid_unavailable() and (
                    not dcdc or self._entity_tristate(dcdc) is True
                )

            def protection_after_grid_return(current: SystemConfig) -> dict[str, bool]:
                now = dt_util.now()
                soc = self._get_soc(now)
                required = support_state(
                    current,
                    soc if soc is not None else current.battery.soc_min_percent,
                    False,
                    False,
                    False,
                )
                target = dict(zip(("dc24", "dc48"), required, strict=True))
                # Before the outage sequence sends PSU24-OFF, the existing
                # source can still complete its authorized rail transfer.
                # Once OFF is requested, only current protection can restart it.
                if (
                    not target["dc24"]
                    and not rail_off_requested
                    and desired["dc24"]
                    and current.support.dc24_available
                    and sources_before_transition["dc24"]
                    and psu24
                    and self._entity_tristate(psu24) is True
                    and not pending_off(psu24)
                    and not self._actuation_shutdown
                    and (
                        not config.support.dc24_forced_on
                        or self._support_manual["dc24"]
                    )
                    and may_remove()
                ):
                    target["dc24"] = True
                    if diag["reason"] == "grid_supply_restored":
                        diag["reason"] = "grid_supply_changed"
                diag["requested"] = dict(target)
                target = self.voltage_gate.apply(current, target, now)
                diag["voltage_gate"] = dict(self.voltage_gate.diagnostics)
                return target

            async def restore_grid_protection() -> None:
                """Replace the interrupted outage sequence with current protection."""
                diag["reason"] = "grid_supply_restored"
                diag["pv_priority"] = False
                current = self.build_system_config()

                def may_restore(key: str) -> bool:
                    return (
                        not grid_unavailable()
                        and protection_after_grid_return(current)[key]
                        and inverter_blocked()
                    )

                def may_restore_rail() -> bool:
                    return (
                        transfer_verified()
                        and psu24 is not None
                        and not pending_off(psu24)
                        and may_restore("dc24")
                    )

                if (
                    psu24
                    and dcdc
                    and may_restore_rail()
                    and (
                        self._entity_tristate(psu24) is not True
                        or self._entity_tristate(dcdc) is not False
                    )
                ):
                    await self._sequence_dc24(
                        True,
                        psu24,
                        may_start=may_restore_rail,
                        may_remove=may_restore_rail,
                    )
                if psu48 and not pending_off(psu48) and may_restore("dc48"):
                    await confirmed(psu48, True)
                if grid_unavailable():
                    return
                target = protection_after_grid_return(current)
                permitted = {
                    **target,
                    "dc24": target["dc24"] and transfer_verified(),
                }
                diag["desired"] = permitted
                for key, entity in (("dc24", psu24), ("dc48", psu48)):
                    self._support_state[key] = bool(
                        entity
                        and not pending_off(entity)
                        and self._entity_tristate(entity) is True
                        and (
                            key != "dc24"
                            or (dcdc and self._entity_tristate(dcdc) is False)
                        )
                    )
                pending = [
                    key
                    for key, needed in permitted.items()
                    if needed and not self._support_state[key]
                ]
                diag["grid_restore_pending"] = pending
                blocked = not any(permitted.values()) or inverter_blocked()
                if pending and diag["reason"] == "grid_supply_restored":
                    diag["reason"] = "grid_restore_pending"
                if pending or not blocked:
                    self._schedule_replan()

            if self.live_ac.pv_source_permission(config):
                desired = {"dc24": False, "dc48": False}
                diag["desired"] = dict(desired)
                diag["pv_priority"] = True

            if config.reserve.enabled and grid_unavailable():
                self._inverter_recommendation = False
                diag["reason"] = "grid_supply_unavailable"
                shed = await shed_grid_sources()
                saw_grid_return = not grid_unavailable()
                # A full-power reserve permission also needs fresh protection
                # evidence. The final actuator guard can revoke an old proposal.
                soc = self._get_soc(dt_util.now())
                blocked = (
                    not shed
                    or self._reserve_grid_available() is False
                    or not inverter
                    or soc is None
                    or soc <= config.control.inverter_min_soc_percent
                )
                confirmed_limit = await self._confirm_inverter_limit(blocked, diag)
                if not grid_unavailable():
                    saw_grid_return = True
                    if not blocked:
                        await self._confirm_inverter_limit(True, diag)
                    if not grid_unavailable():
                        await restore_grid_protection()
                        if not grid_unavailable():
                            return
                if saw_grid_return:
                    # Renewed loss during takeover: prefer the battery rail
                    # again. Further flapping can settle in the normal retry.
                    diag["reason"] = "grid_supply_unavailable"
                    await shed_grid_sources()
                    await self._confirm_inverter_limit(True, diag)
                    self._schedule_replan()
                    return
                if not blocked and self._reserve_grid_available() is False:
                    await self._confirm_inverter_limit(True, diag)
                    return
                self._inverter_recommendation = bool(
                    not blocked
                    and confirmed_limit
                    and diag.get("discharge_limit_target_w", 0) > 0
                )
                if shed:
                    diag["reason"] = "grid_supply_unavailable"
                return

            if not transfer_verified():
                desired = {**desired, "dc24": False}
                diag["desired"] = dict(desired)

            # Even an unexpected external PSU activation blocks AC immediately.
            # Release comes last, after all source confirmations below.
            physical_support = any(
                entity and self._entity_tristate(entity) is not False
                for entity in (psu24, psu48)
            )
            waiting_for_rail = not desired["dc24"] and waiting_for_dcdc_off()
            if (
                waiting_for_rail
                or not inverter
                or any(desired.values())
                or physical_support
            ):
                self._inverter_recommendation = False
                if not await self._confirm_inverter_limit(True, diag):
                    diag["reason"] = "inverter_block_unconfirmed"
                    return
            if waiting_for_rail:
                return

            # Blocking/confirmation above may have consumed real time.
            now = dt_util.now()
            interval = timedelta(seconds=config.control.min_switch_interval_s)
            changes = any(
                entity and self._entity_tristate(entity) is not desired[key]
                for key, entity in (("dc24", psu24), ("dc48", psu48))
            )
            current_soc = (
                self._get_soc(dt_util.now()) if config.reserve.enabled else None
            )
            safety_restore = config.reserve.enabled and (
                current_soc is None
                or current_soc <= config.control.support_dc24_activate_soc
                or current_soc <= config.control.support_dc48_activate_soc
            )
            if (
                not safety_restore
                and changes
                and self._last_support_switch is not None
                and now - self._last_support_switch < interval
            ):
                diag["reason"] = "minimum_switch_interval"
                return
            if changes:
                self._last_support_switch = now

            # Remove 48 V support before returning the rail to the battery.
            if psu48 and not desired["dc48"]:
                if not may_remove():
                    await restore_required()
                    return
                if not await confirmed(psu48, False):
                    return
                self._support_state["dc48"] = False
                if not may_remove():
                    await restore_required()
                    return
            if psu24 and dcdc:
                target24 = desired["dc24"]
                if self._entity_tristate(
                    psu24
                ) is not target24 or self._entity_tristate(dcdc) is not (not target24):

                    def may_start_rail() -> bool:
                        return (
                            may_remove()
                            and transfer_verified()
                            and inverter_blocked()
                            and not pending_off(psu24)
                        )

                    if not await self._sequence_dc24(
                        target24,
                        psu24,
                        may_start=may_start_rail if target24 else None,
                        may_remove=may_start_rail if target24 else may_remove,
                    ):
                        if diag["reason"] != "settled":
                            await restore_required()
                            return
                        diag["reason"] = "rail_transfer_failed"
                        return
                    if self._entity_tristate(
                        psu24
                    ) is not target24 or self._entity_tristate(dcdc) is not (
                        not target24
                    ):
                        diag["reason"] = "rail_transfer_unconfirmed"
                        return
                self._support_state["dc24"] = bool(target24)
                if not may_remove():
                    await restore_required()
                    return
            elif psu24:
                # A hand-edited configuration must not turn off a rail's
                # only confirmed source. The config flow rejects this.
                await self._confirm_inverter_limit(True, diag)
                self._inverter_recommendation = False
                diag["reason"] = "missing_rail_transfer_actuator"
                return
            if psu48 and desired["dc48"]:
                if not may_remove():
                    await restore_required()
                    return
                if pending_off(psu48) or not inverter_blocked():
                    return
                if not await confirmed(psu48, True):
                    return
                self._support_state["dc48"] = True
                if not may_remove():
                    await restore_required()
                    return
            if inverter and not any(desired.values()):
                if dcdc and not await confirmed(dcdc, True):
                    return
                if config.reserve.enabled:
                    # Device confirmations can themselves be delayed. Re-read
                    # SOC at the last possible point before releasing AC.
                    soc = self._get_soc(dt_util.now())
                    if soc is None or soc <= config.control.inverter_min_soc_percent:
                        await self._confirm_inverter_limit(True, diag)
                        self._inverter_recommendation = False
                        diag["reason"] = "soc_protection"
                        return
                if not may_remove():
                    await restore_required()
                    return
                released = await self._confirm_inverter_limit(False, diag)
                current_soc = (
                    self._get_soc(dt_util.now()) if config.reserve.enabled else None
                )
                safe = may_remove() and (
                    not config.reserve.enabled
                    or (
                        current_soc is not None
                        and current_soc > config.control.inverter_min_soc_percent
                    )
                )
                if not released or not safe:
                    # A number-service confirmation can itself outlive the
                    # permission. Revoke physically before leaving this owner.
                    await self._confirm_inverter_limit(True, diag)
                    if not safe:
                        await restore_required()
                    return
                self._inverter_recommendation = bool(
                    diag.get("discharge_limit_target_w", 0) > 0
                )
    finally:
        self._save_persistent_state()
        if self.data:
            self.data["inverter_recommendation"] = self._inverter_recommendation
            self.data["coordinated_support"] = {
                **self.data.get("coordinated_support", {}),
                **self._coordinated_support_diag,
            }
            self.data["support_dc24"] = self._support_state["dc24"]
            self.data["support_dc48"] = self._support_state["dc48"]
            self.async_update_listeners()
