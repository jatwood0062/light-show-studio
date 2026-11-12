"""Websocket API for the settings backup: export one JSON document, import one (both admin)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import backup

P = "light_show_studio/backup"


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_export, ws_import):
        websocket_api.async_register_command(hass, command)


# the document holds every schedule and pattern, so reading it is for admins as well
@websocket_api.websocket_command({vol.Required("type"): f"{P}/export"})
@websocket_api.require_admin
@callback
def ws_export(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], backup.export(hass))


# the document is checked in depth, with readable messages, by backup.validate
@websocket_api.websocket_command({vol.Required("type"): f"{P}/import", vol.Required("backup"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    try:
        result = await backup.async_import(hass, msg["backup"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    connection.send_result(msg["id"], result)
