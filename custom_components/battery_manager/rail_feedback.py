"""Qualified feedback for the two sources of the 24 V rail."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import DATA_COMPONENT as SWITCH_COMPONENT
from homeassistant.core import callback
from homeassistant.helpers import entity_registry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DCDC_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
)

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

RAIL_FEEDBACK_KEYS = {
    CONF_SUPPORT_DC24_SWITCH: "support_dc24_feedback",
    CONF_DCDC_SWITCH: "support_dcdc_feedback",
}

# KNX state polling can be hourly. A new command still requires subsequent
# evidence, regardless of how recently the previous status was received.
_KNX_MAX_AGE = timedelta(minutes=65)
_KNX_ON_CONFIRMATION_GRACE = timedelta(seconds=30)
_KNX_NEGATIVE_MAX_AGE = timedelta(minutes=5)
_KNX_PAIR = re.compile(r"(\d{1,2})/(\d)/(\d{1,3})@(\d{1,2})\.(\d{1,2})\.(\d{1,3})")


def valid_binding(value: str) -> bool:
    if re.fullmatch(r"(?:binary_sensor|switch)\.[a-z0-9_]+", value):
        return True
    match = _KNX_PAIR.fullmatch(value)
    return bool(
        match
        and all(
            0 <= int(part) <= limit
            for part, limit in zip(
                match.groups(), (31, 7, 255, 15, 15, 255), strict=True
            )
        )
        and all(str(int(part)) == part for part in match.groups())
    )


class RailFeedback:
    """Keep bus reports separate from the optimistic HA switch state."""

    def __init__(self, coordinator: BatteryManagerCoordinator) -> None:
        self.coordinator = coordinator
        self._reports: dict[str, tuple[bool, datetime, datetime]] = {}
        self._unsub: Callable[[], None] | None = None

    def binding(self, entity: str) -> str | None:
        config = self.coordinator.raw_config
        for actor_key, key in RAIL_FEEDBACK_KEYS.items():
            if config.get(actor_key) == entity:
                return config.get(key) or None
        return None

    def is_rail_actor(self, entity: str) -> bool:
        config = self.coordinator.raw_config
        return any(config.get(key) == entity for key in RAIL_FEEDBACK_KEYS)

    def uses_knx(self, entity: str) -> bool:
        return "@" in (self.binding(entity) or "")

    def is_coordinated_knx_dcdc(self, entity: str) -> bool:
        config = self.coordinator.raw_config
        return (
            entity == config.get(CONF_DCDC_SWITCH)
            and bool(config.get(CONF_INVERTER_LIMIT_ENTITY))
            and self.uses_knx(entity)
        )

    def knx_dcdc_on_unmet(self, entity: str, requested_at: datetime) -> bool:
        """Fresh contrary status permits ON recovery after its grace period."""
        if not self.is_coordinated_knx_dcdc(entity):
            return False
        report = self.report(entity)
        now = dt_util.utcnow()
        return (
            report is not None
            and report[0] is False
            and report[1] > requested_at
            and now - requested_at >= _KNX_ON_CONFIRMATION_GRACE
            and timedelta(0) <= now - report[1] <= _KNX_NEGATIVE_MAX_AGE
        )

    def report(self, entity: str) -> tuple[bool, datetime, datetime] | None:
        """Return value, report time and start of the continuous value."""
        hass = self.coordinator.hass
        binding = self.binding(entity)
        if binding and "@" in binding:
            if not any(hass.states.is_state(entity, value) for value in ("on", "off")):
                return None
            report = self._reports.get(binding)
            if report and dt_util.utcnow() - report[1] <= _KNX_MAX_AGE:
                return report
            return None
        entry = entity_registry.async_get(hass).async_get(binding or entity)
        if binding and binding.startswith("switch."):
            if binding != entity or not entry or entry.platform in {"knx", "template"}:
                return None
        elif not binding:
            component = hass.data.get(SWITCH_COMPONENT)
            registry = entity_registry.async_get(hass)
            config = self.coordinator.raw_config
            if any(
                registry.async_get(actor) is not None
                or (component is not None and component.get_entity(actor) is not None)
                or self.binding(actor) is not None
                for key in RAIL_FEEDBACK_KEYS
                if (actor := config.get(key))
            ):
                # Raw state fixtures can model a rail only when neither actor
                # is an installed HA switch or has a qualified binding.
                return None
        state = hass.states.get(binding or entity)
        if (
            state is None
            or state.state not in ("on", "off")
            or state.attributes.get("assumed_state")
            or state.attributes.get("restored")
            or dt_util.utcnow() - state.last_reported > _KNX_MAX_AGE
        ):
            return None
        return state.state == "on", state.last_reported, state.last_changed

    def invalidate_connection(self, entity: str) -> bool:
        """A known KNX outage invalidates every report from that connection."""
        binding = self.binding(entity)
        if not binding or "@" not in binding or not self._reports:
            return False
        self._reports.clear()
        # A fresh OFF after reconnect can establish present state. It cannot
        # complete the same-connection edge needed for manual handover.
        for request in self.coordinator._supply_actor_requests.values():
            if request.knx_on_retried and not request.desired:
                request.sent = False
        return True

    def start(self) -> None:
        if self._unsub is None and any(
            "@" in (self.coordinator.raw_config.get(key) or "")
            for key in RAIL_FEEDBACK_KEYS.values()
        ):
            self._unsub = async_dispatcher_connect(
                self.coordinator.hass, "knx_telegram", self._telegram
            )

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._reports.clear()

    @callback
    def _telegram(self, _telegram: object, data: dict[str, Any]) -> None:
        if (
            data.get("direction") != "Incoming"
            or data.get("telegramtype") not in {"GroupValueWrite", "GroupValueResponse"}
            or data.get("payload") not in (0, 1)
        ):
            return
        binding = f"{data['destination']}@{data['source']}"
        config = self.coordinator.raw_config
        for actor_key, key in RAIL_FEEDBACK_KEYS.items():
            if config.get(key) != binding or not (actor := config.get(actor_key)):
                continue
            value = bool(data["payload"])
            now = dt_util.utcnow()
            previous = self.report(actor)
            changed = previous is None or previous[0] != value
            self._reports[binding] = (
                value,
                now,
                previous[2] if previous and not changed else now,
            )
            self.coordinator._rail_feedback_updated(actor, changed)
