"""Websocket API for music sync that follows a speaker: the lights react to a show library song a media player is
playing, with no microphone (see music_follow).

Any signed-in user may use it on lights they may control and a speaker they may see; admins may use any. The
session belongs to no connection: it carries on when the panel closes, and ends on music/follow_stop, when the
speaker stops or plays something else, or when the lights are taken by something else."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import mapping, music_engine
from .musicmanager import MusicError
from .ws_music import P, REF, TARGETS, _manager, _whole, may_control

ENTITY = vol.All(str, vol.Length(min=14, max=255), vol.Match(r"^media_player\.[a-z0-9_]+$"))


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_follow, ws_follow_stop):
        websocket_api.async_register_command(hass, command)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise vol.Invalid("expected a number")
    return float(value)


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/follow",
    vol.Required("media_player"): ENTITY,  # the speaker that is playing a show library song
    vol.Required("targets"): TARGETS,
    vol.Optional("pattern"): REF,
    vol.Optional("effect", default=music_engine.PULSE): vol.In(music_engine.EFFECTS),
    vol.Optional("sensitivity", default=music_engine.DEFAULT_SENSITIVITY): vol.All(_whole, vol.Range(min=1, max=100)),
    vol.Optional("mode"): vol.In(mapping.MODES),
    vol.Optional("axis"): dict,
    vol.Optional("idle_effect"): REF,
    vol.Optional("song"): vol.All(str, vol.Length(min=1, max=512)),  # a library song, when the speaker's id can't be matched
    vol.Optional("offset", default=0.0): vol.All(_number, vol.Range(min=-10, max=10)),  # seconds, + = lights later
})
@websocket_api.async_response
async def ws_follow(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Take the lights for music that follows the speaker. Sent again for the same targets, it swaps the settings."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    targets = msg["targets"]
    if not targets["lights"] and not targets["areas"]:
        connection.send_error(msg["id"], "invalid_format", "Pick at least one light or area")
        return
    user = getattr(connection, "user", None)
    if not may_control(hass, connection, manager.mgr, targets["lights"], targets["areas"]) or not (
            user.is_admin or user.permissions.check_entity(msg["media_player"], POLICY_READ)):
        connection.send_error(msg["id"], "unauthorized", "You may not control those lights or see that speaker")
        return
    try:
        result = await manager.async_follow(
            msg["media_player"], targets["lights"], targets["areas"], pattern=msg.get("pattern"), effect=msg["effect"],
            sensitivity=msg["sensitivity"], mode=msg.get("mode", "string"), axis=msg.get("axis"),
            idle_effect=msg.get("idle_effect"), song=msg.get("song"), offset=msg["offset"])
    except MusicError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/follow_stop",
    vol.Optional("targets"): TARGETS,  # leave out to stop every session that follows a speaker
})
@callback
def ws_follow_stop(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """End the sessions that follow a speaker on lights this user may control."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    targets = msg.get("targets")
    n = 0
    for s in [s for s in manager.sessions.values() if "follow" in s.info]:
        lights = [u for kind, u in s.units if kind == "light"]
        areas = [u for kind, u in s.units if kind == "area"]
        if targets is not None and not s.units & manager._unit_set(targets["lights"], targets["areas"]):  # noqa: SLF001
            continue
        if may_control(hass, connection, manager.mgr, lights, areas):
            manager.stop_session(s)
            n += 1
    connection.send_result(msg["id"], {"stopped": n})
