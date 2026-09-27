"""Authenticated, read-only appliance views without recording large HA attributes."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register_appliance_api(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, appliance_snapshot)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "battery_manager/appliances",
        vol.Optional("entry_id"): str,
        vol.Optional("appliance_ids"): [str],
    }
)
@callback
def appliance_snapshot(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinators = hass.data.get(DOMAIN, {})
    entry_id = msg.get("entry_id")
    if entry_id is None:
        connection.send_result(
            msg["id"],
            {
                "entries": [
                    {"entry_id": key, "title": coordinator.entry.title}
                    for key, coordinator in coordinators.items()
                    if not coordinator._actuation_shutdown
                ]
            },
        )
        return
    coordinator = coordinators.get(entry_id)
    if coordinator is None or coordinator._actuation_shutdown:
        connection.send_error(
            msg["id"], "not_loaded", "Battery Manager entry is not loaded"
        )
        return
    ids = msg.get("appliance_ids")
    if ids is not None and any(
        coordinator.appliances.snapshot(key) is None for key in ids
    ):
        connection.send_error(
            msg["id"], "not_found", "Household appliance does not belong to this entry"
        )
        return
    connection.send_result(msg["id"], coordinator.appliances.payload(ids))
