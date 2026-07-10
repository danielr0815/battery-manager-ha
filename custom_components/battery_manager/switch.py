"""Switch platform: vacation mode + per-PSU manual support override."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    DOMAIN,
    ENTITY_SUPPORT_DC24_MANUAL,
    ENTITY_SUPPORT_DC48_MANUAL,
    ENTITY_VACATION_MODE,
    SUBENTRY_TYPE_LOAD,
)
from .coordinator import BatteryManagerCoordinator
from .entity import BatteryManagerEntity, async_add_by_subentry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacation-mode switch and the manual support-override switches."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[Entity] = [BatteryManagerVacationSwitch(coordinator)]

    # A manual-override switch per configured support PSU (F-N2/R3). A
    # leftover switch of a removed PSU is dropped from the registry.
    ent_reg = er.async_get(hass)
    for entity_key, conf_key, psu_key in (
        (ENTITY_SUPPORT_DC24_MANUAL, CONF_SUPPORT_DC24_SWITCH, "dc24"),
        (ENTITY_SUPPORT_DC48_MANUAL, CONF_SUPPORT_DC48_SWITCH, "dc48"),
    ):
        if coordinator.raw_config.get(conf_key):
            entities.append(SupportManualSwitch(coordinator, entity_key, psu_key))
        else:
            stale = ent_reg.async_get_entity_id(
                "switch", DOMAIN, f"{entry.entry_id}_{entity_key}"
            )
            if stale:
                ent_reg.async_remove(stale)

    # A "BM control active" switch per surplus load (v0.7.17): off holds the load
    # unavailable so a device can be paused without removing its control switch.
    # Scoped to its subentry so it is removed automatically with the load (v0.7.19).
    per_subentry: dict[str, list[Entity]] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_LOAD:
            per_subentry[subentry_id] = [
                SurplusLoadControlSwitch(coordinator, subentry_id, subentry.title)
            ]

    async_add_by_subentry(async_add_entities, entities, per_subentry)


class BatteryManagerVacationSwitch(BatteryManagerEntity, SwitchEntity):
    """While on, the planner forecasts with the learned absence profile.

    The state is persisted in the learner store and its recorder history is
    used to tag past days as absence days for learning (D-C4).
    """

    _attr_translation_key = ENTITY_VACATION_MODE
    _attr_icon = "mdi:beach"

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        super().__init__(coordinator, ENTITY_VACATION_MODE)

    @property
    def available(self) -> bool:
        # The switch reflects persisted state, not planner output — it must
        # stay usable even while inputs are missing.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.learner.vacation_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.learner.async_set_vacation(True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.learner.async_set_vacation(False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class SupportManualSwitch(BatteryManagerEntity, SwitchEntity):
    """Operator manual override for a support PSU (F-N2/R3, docs/DC_TOPOLOGY §7).

    On = force the PSU on and pause the automatic control for it (winter
    operation); off = restore automatic control. Actuation and mode both go
    through the coordinator's single entry point.
    """

    _attr_icon = "mdi:hand-back-right"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, entity_key: str, psu_key: str
    ) -> None:
        super().__init__(coordinator, entity_key)
        self._attr_translation_key = entity_key
        self._psu_key = psu_key

    @property
    def available(self) -> bool:
        # Reflects persisted manual state — usable even without plan data.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.support_manual(self._psu_key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_support_manual(self._psu_key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_support_manual(self._psu_key, False)
        self.async_write_ha_state()


class SurplusLoadControlSwitch(BatteryManagerEntity, SwitchEntity):
    """Per-load 'BM control active' switch (v0.7.17).

    On (default) = BM plans and actuates the load. Off = BM holds the load
    unavailable (drops it from the plan and switches it off next cycle) WITHOUT
    removing its control-switch config — a one-tap temporary pause for a device
    that resumes when switched back on.
    """

    _attr_translation_key = "load_control"
    _attr_icon = "mdi:robot"

    def __init__(
        self, coordinator: BatteryManagerCoordinator, subentry_id: str, title: str
    ) -> None:
        super().__init__(coordinator, f"load_control_{subentry_id}")
        self._subentry_id = subentry_id
        self._attr_translation_placeholders = {"name": title}

    @property
    def available(self) -> bool:
        # Reflects persisted state — usable even while the planner has no data.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.load_bm_enabled(self._subentry_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_load_enabled(self._subentry_id, True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_load_enabled(self._subentry_id, False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
