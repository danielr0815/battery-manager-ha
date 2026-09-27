"""Ordered house-supply transitions and physical confirmation policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import (
    CONF_DCDC_SWITCH,
    CONF_RESERVE_TRANSFER_VERIFIED,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
)
from .core import SystemConfig
from .core.support import support_state

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
                # Unknown grid supply rules out PSU credit, not useful
                # forecast-driven battery preparation. Known grid loss
                # still follows the existing island/fallback protection.
                soc = self._get_soc(dt_util.now())
                blocked = (
                    self._reserve_grid_available() is False
                    or not inverter
                    or soc is None
                    or soc <= config.control.inverter_min_soc_percent
                )
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
                if config.reserve.enabled:
                    # Device confirmations can themselves be delayed. Re-read
                    # SOC at the last possible point before releasing AC.
                    soc = self._get_soc(dt_util.now())
                    if soc is None or soc <= config.control.inverter_min_soc_percent:
                        await self._confirm_inverter_limit(True, diag)
                        self._inverter_recommendation = False
                        diag["reason"] = "soc_protection"
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
