"""Websocket API for the Map tab: read and replace the map, start, stop and speed up zones."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from . import patterns
from .mapmanager import MapManager, get_manager
from .mapping import MapError, to_dict

ZONE_ID = vol.All(str, vol.Length(min=1, max=64))


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_set)
    websocket_api.async_register_command(hass, ws_start)
    websocket_api.async_register_command(hass, ws_stop)
    websocket_api.async_register_command(hass, ws_speed)
    websocket_api.async_register_command(hass, ws_subscribe)


def _manager(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> MapManager | None:
    manager = get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_found", "The light map is not set up")
    return manager


def _snapshot(manager: MapManager) -> dict[str, Any]:
    return {"map": to_dict(manager.model), "lights": manager.lights(), "running": manager.running}


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/map/get"})
@callback
def ws_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if manager := _manager(hass, connection, msg):
        connection.send_result(msg["id"], _snapshot(manager))


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/set",
    vol.Required("map"): dict,  # checked in depth, with readable messages, by mapping.from_dict
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    try:
        model = await manager.async_set_map(msg["map"])
    except MapError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    connection.send_result(msg["id"], {"map": to_dict(model)})


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/zone/start",
    vol.Required("zone_id"): ZONE_ID,
    vol.Optional("pattern"): vol.In(patterns.PATTERN_LIST),
    vol.Optional("effect"): vol.In(patterns.PATTERN_EFFECTS),
    vol.Optional("speed"): vol.All(int, vol.Range(min=1, max=100)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_start(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    try:
        await manager.async_start_zone(msg["zone_id"], msg.get("pattern"), msg.get("effect"), msg.get("speed"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/map/zone/stop", vol.Required("zone_id"): ZONE_ID})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_stop(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    try:
        await manager.async_stop_zone(msg["zone_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/zone/speed",
    vol.Required("zone_id"): ZONE_ID,
    vol.Required("speed"): vol.All(int, vol.Range(min=1, max=100)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_speed(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    try:
        await manager.async_set_speed(msg["zone_id"], msg["speed"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/map/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Which zones are running, pushed whenever that changes (a zone ends by itself when its lights are taken)."""
    if not (manager := _manager(hass, connection, msg)):
        return

    @callback
    def send() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"running": manager.running}))

    connection.subscriptions[msg["id"]] = manager.add_listener(send)
    connection.send_result(msg["id"])
    send()
