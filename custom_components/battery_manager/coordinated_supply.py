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
from .core.model import PlanInputs
from .core.support import support_state

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


@dataclass(frozen=True)
class SupplyRequest:
    """A queued intent is valid only for the planner revision that issued it."""

    sources: tuple[bool, bool]
    inverter: bool
    plan_revision: int
    expires: datetime | None


def _record_expiry(inputs: PlanInputs) -> datetime:
    """Legacy source plans also end at their first slot or the freshness limit."""
    return min(
        dt_util.as_utc(inputs.now) + timedelta(seconds=LIVE_AC_PLAN_MAX_AGE_S),
        dt_util.as_utc(inputs.slots[0].start)
        + timedelta(hours=inputs.slots[0].duration)
        if inputs.slots
        else dt_util.as_utc(inputs.now),
    )


async def execute_coordinated_support(
    self: BatteryManagerCoordinator,
    desired: dict[str, bool],
    inverter: bool,
    config: SystemConfig,
    now: datetime,
) -> None:
    envelope = self.live_ac.envelope
    record = self._last_planner_recording
    request = SupplyRequest(
        (desired["dc24"], desired["dc48"]),
        inverter,
        self.live_ac.plan_revision,
        envelope.expires
        if envelope is not None
        else _record_expiry(record[1])
        if record is not None
        else None,
    )
    self._coordinated_support_diag = {
        "mode": "coordinated",
        "reason": "waiting_for_lock",
    }
    try:
        async with self._switch_lock:
            envelope = self.live_ac.envelope
            if request.plan_revision != self.live_ac.plan_revision or (
                request.expires is not None and dt_util.utcnow() >= request.expires
            ):
                valid = envelope is not None and dt_util.utcnow() < envelope.expires
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
                    valid = not self._actuation_shutdown and dt_util.utcnow() < expires
                    desired = {
                        "dc24": valid and recorded_result.support_dc24_now,
                        "dc48": valid and recorded_result.support_dc48_now,
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
                )
            now = dt_util.now()
            psu24 = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
            psu48 = self.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
            dcdc = self.raw_config.get(CONF_DCDC_SWITCH)
            diag: dict[str, Any] = {
                "mode": "coordinated",
                "reason": "settled",
                "desired": dict(desired),
            }
            self._coordinated_support_diag = diag

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

            def may_remove() -> bool:
                """Every await can revoke source permission, including rail overlap."""
                changed = request.plan_revision != self.live_ac.plan_revision
                expired = (
                    request.expires is not None and dt_util.utcnow() >= request.expires
                )
                if changed or expired:
                    diag["reason"] = "plan_changed" if changed else "plan_expired"
                elif diag.get("pv_priority") and not self.live_ac.pv_source_permission(
                    config
                ):
                    diag["reason"] = "pv_permission_expired"
                elif (
                    config.reserve.enabled
                    and self._reserve_grid_available() is not True
                ):
                    diag["reason"] = "grid_supply_changed"
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
                if self._reserve_grid_available() is not True:
                    return
                current = self.live_ac.envelope
                target = dict(planned_desired)
                if current is not None:
                    target = dict(
                        zip(
                            ("dc24", "dc48"),
                            current.dc_sources
                            if dt_util.utcnow() < current.expires
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
                        not self._actuation_shutdown
                        and dt_util.utcnow() < _record_expiry(recorded_inputs)
                    )
                    target = {
                        "dc24": valid and recorded_result.support_dc24_now,
                        "dc48": valid and recorded_result.support_dc48_now,
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
                diag["desired"] = target
                if target["dc24"] and psu24:
                    if dcdc:
                        restored = await self._sequence_dc24(
                            True,
                            psu24,
                            may_remove=lambda: self._reserve_grid_available() is True,
                        )
                        if not restored:
                            return
                    elif not await confirmed(psu24, True):
                        return
                    self._support_state["dc24"] = True
                if (
                    target["dc48"]
                    and psu48
                    and self._reserve_grid_available() is True
                    and await confirmed(psu48, True)
                ):
                    self._support_state["dc48"] = True

            if self.live_ac.pv_source_permission(config):
                desired = {"dc24": False, "dc48": False}
                diag["desired"] = dict(desired)
                diag["pv_priority"] = True

            if config.reserve.enabled and self._reserve_grid_available() is not True:
                self._inverter_recommendation = False
                diag["reason"] = "grid_supply_unavailable"
                if dcdc and not await confirmed(dcdc, True):
                    # Preserve the existing rail source, but a failed transfer
                    # cannot leave battery AC discharge enabled during outage.
                    await self._confirm_inverter_limit(True, diag)
                    return
                for key, entity in (("dc24", psu24), ("dc48", psu48)):
                    if entity and not await confirmed(entity, False):
                        await self._confirm_inverter_limit(True, diag)
                        return
                    self._support_state[key] = False
                # A full-power reserve permission also needs fresh protection
                # evidence. The final actuator guard can revoke an old proposal.
                soc = self._get_soc(dt_util.now())
                blocked = (
                    self._reserve_grid_available() is False
                    or not inverter
                    or soc is None
                    or soc <= config.control.inverter_min_soc_percent
                )
                confirmed_limit = await self._confirm_inverter_limit(blocked, diag)
                self._inverter_recommendation = bool(
                    not blocked
                    and confirmed_limit
                    and diag.get("discharge_limit_target_w", 0) > 0
                )
                diag["reason"] = "grid_supply_unavailable"
                self._save_persistent_state()
                return

            if config.reserve.enabled and not self.raw_config.get(
                CONF_RESERVE_TRANSFER_VERIFIED
            ):
                desired = {**desired, "dc24": False}
                diag["desired"] = dict(desired)
                diag["dc24_block_reason"] = "transfer_unverified"

            # Even an unexpected external PSU activation blocks AC immediately.
            # Release comes last, after all source confirmations below.
            physical_support = any(
                entity and self._entity_tristate(entity) is not False
                for entity in (psu24, psu48)
            )
            if not inverter or any(desired.values()) or physical_support:
                self._inverter_recommendation = False
                if not await self._confirm_inverter_limit(True, diag):
                    diag["reason"] = "inverter_block_unconfirmed"
                    return

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
                    if not await self._sequence_dc24(
                        target24, psu24, may_remove=may_remove
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
