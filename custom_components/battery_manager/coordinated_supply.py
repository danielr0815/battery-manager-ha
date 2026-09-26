"""Ordered house-supply transitions and physical confirmation policy."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .const import (
    CONF_DCDC_SWITCH,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
)
from .core import SystemConfig

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


async def execute_coordinated_support(
    self: BatteryManagerCoordinator,
    desired: dict[str, bool],
    inverter: bool,
    config: SystemConfig,
    now: datetime,
) -> None:
    self._coordinated_support_diag = {
        "mode": "coordinated",
        "reason": "waiting_for_lock",
    }
    try:
        async with self._switch_lock:
            psu24 = self.raw_config.get(CONF_SUPPORT_DC24_SWITCH)
            psu48 = self.raw_config.get(CONF_SUPPORT_DC48_SWITCH)
            dcdc = self.raw_config.get(CONF_DCDC_SWITCH)
            diag = {
                "mode": "coordinated",
                "reason": "settled",
                "desired": dict(desired),
            }
            self._coordinated_support_diag = diag

            async def confirmed(entity, on):
                if self._entity_tristate(entity) is on:
                    return True
                if not await self._switch_entity(entity, on):
                    diag["reason"] = "command_failed"
                    return False
                if self._entity_tristate(entity) is not on:
                    diag["reason"] = "awaiting_confirmation"
                    return False
                return True

            if config.reserve.enabled and self._reserve_grid_available() is not True:
                self._inverter_recommendation = False
                diag["reason"] = "grid_supply_unavailable"
                if dcdc and not await confirmed(dcdc, True):
                    return
                for key, entity in (("dc24", psu24), ("dc48", psu48)):
                    if entity and not await confirmed(entity, False):
                        return
                    self._support_state[key] = False
                # Unknown grid supply rules out PSU credit, not useful
                # forecast-driven battery preparation. Known grid loss
                # still follows the existing island/fallback protection.
                blocked = self._reserve_grid_available() is False or not inverter
                confirmed_limit = await self._confirm_inverter_limit(blocked, diag)
                self._inverter_recommendation = not blocked and confirmed_limit
                diag["reason"] = "grid_supply_unavailable"
                self._save_persistent_state()
                return

            if config.reserve.enabled and not self.raw_config.get(
                CONF_RESERVE_TRANSFER_VERIFIED
            ):
                desired = {**desired, "dc24": False}

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
            if (
                changes
                and self._last_support_switch is not None
                and now - self._last_support_switch < interval
            ):
                diag["reason"] = "minimum_switch_interval"
                return
            if changes:
                self._last_support_switch = now

            # Remove 48 V support before returning the rail to the battery.
            if psu48 and not desired["dc48"]:
                if not await confirmed(psu48, False):
                    return
                self._support_state["dc48"] = False
            if psu24 and dcdc:
                target24 = desired["dc24"]
                if self._entity_tristate(
                    psu24
                ) is not target24 or self._entity_tristate(dcdc) is not (not target24):
                    if not await self._sequence_dc24(target24, psu24):
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
            if inverter and not any(desired.values()):
                if dcdc and not await confirmed(dcdc, True):
                    return
                if not await self._confirm_inverter_limit(False, diag):
                    return
                self._inverter_recommendation = True
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
