"""Websocket API for saved looks: list, save what the lights show now (admin), apply, delete (admin)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import fade
from .looks import LOOKS_KEY, MAX_LIGHTS, MAX_LOOKS, MAX_NAME, Looks
from .ws_music import may_control

P = "light_show_studio/looks"
NAME = vol.All(str, vol.Length(min=1, max=MAX_NAME))


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_list, ws_save, ws_apply, ws_delete):
        websocket_api.async_register_command(hass, command)


def _looks(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> Looks | None:
    looks = hass.data.get(LOOKS_KEY)
    if looks is None:
        connection.send_error(msg["id"], "not_found", "Looks are not set up")
    return looks


def _error(connection: Any, msg: dict[str, Any], err: Exception) -> None:
    if isinstance(err, KeyError):
        connection.send_error(msg["id"], "not_found", str(err.args[0]) if err.args else "Not found")
    else:
        connection.send_error(msg["id"], "invalid_format", str(err))


@websocket_api.websocket_command({vol.Required("type"): f"{P}/list"})
@callback
def ws_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (looks := _looks(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"looks": looks.view(), "limits": {"max_looks": MAX_LOOKS, "max_name": MAX_NAME}})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/save",
    vol.Required("name"): NAME,
    vol.Optional("entry_ids"): vol.All([NAME], vol.Length(min=1, max=MAX_LIGHTS)),  # only these lights
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (looks := _looks(hass, connection, msg)) is None:
        return
    try:
        await looks.async_save_current(msg["name"], msg.get("entry_ids"))
    except ValueError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"looks": looks.view()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/apply",
    vol.Required("name"): NAME,
    vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=fade.MAX_TRANSITION)),
})
@websocket_api.async_response
async def ws_apply(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (looks := _looks(hass, connection, msg)) is None:
        return
    try:
        look = looks.get(msg["name"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    # a look with zones moves lights outside its own list too, so only an admin may apply one
    user = getattr(connection, "user", None)
    if (look.get("zones") and not (user and user.is_admin)) \
            or not may_control(hass, connection, looks.mgr, list(look["lights"]), []):
        connection.send_error(msg["id"], "unauthorized", "You may not control those lights")
        return
    connection.send_result(msg["id"], await looks.async_apply(msg["name"], msg.get("transition")))


@websocket_api.websocket_command({vol.Required("type"): f"{P}/delete", vol.Required("name"): NAME})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (looks := _looks(hass, connection, msg)) is None:
        return
    try:
        await looks.async_delete(msg["name"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"looks": looks.view()})
