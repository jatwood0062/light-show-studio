"""Websocket API for music sync: a phone listens with its microphone and sends audio features; the lights follow.

Any signed-in user may use it, but only on lights they may control (checked per light entity; admins may use any).
Frames are rate limited per user (40 a second) and every field is checked strictly. A session ends on music/stop or when the connection closes."""
from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import mapping, music_engine
from .const import MUSIC_KEY
from .musicmanager import MusicError, MusicManager

P = "light_show_studio/music"
REF = vol.All(str, vol.Length(min=1, max=64))
TARGETS = vol.Schema({
    vol.Optional("lights", default=list): vol.All([REF], vol.Length(max=64)),
    vol.Optional("areas", default=list): vol.All([REF], vol.Length(max=64)),
})
RATE = 40.0  # frames a second per user
QUIET = 1.0  # seconds after a rate_limited error during which further over-limit frames are dropped without a reply


class _Limit:
    def __init__(self) -> None:
        self.limiter = music_engine.RateLimiter(RATE)
        self.quiet_until = 0.0


_limits: dict[str, _Limit] = {}  # by user id (many connections of one user share the allowance)
_clock = time.monotonic


def light_entities(hass: Any, entry_id: str) -> list[str]:
    """The light entity ids of a config entry (its whole string and its bulbs)."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415  (kept out of import time for tests)
    return [e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id) if e.domain == "light"]


def may_control(hass: Any, connection: Any, mgr: Any, lights: list[str], areas: list[str]) -> bool:
    """True when this connection's user may control every light entity the targets touch. Areas are resolved to
    the lights that have runs in them first. Admins may control anything; a light with no entities is refused."""
    user = getattr(connection, "user", None)
    if user is None:
        return False
    if user.is_admin:
        return True
    entries = set(lights)
    if areas:
        model = mgr.model
        ids = {a.id for ref in areas if (a := model.area(ref)) is not None}
        entries |= {r.entry_id for r in model.runs if r.area in ids}
    for entry_id in entries:
        entities = light_entities(hass, entry_id)
        if not entities or not all(user.permissions.check_entity(e, POLICY_CONTROL) for e in entities):
            return False
    return True


def _whole(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise vol.Invalid("expected a whole number")
    return value


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_start, ws_frame, ws_stop):
        websocket_api.async_register_command(hass, command)


def _manager(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> MusicManager | None:
    manager = hass.data.get(MUSIC_KEY)
    if manager is None:
        connection.send_error(msg["id"], "not_found", "Music sync is not set up")
    return manager


def _allowed(connection: Any) -> tuple[bool, bool]:
    """(allowed, send an error): over the limit, one error goes out and the rest are dropped for QUIET seconds."""
    user = getattr(connection, "user", None)
    key = getattr(user, "id", None) or f"conn{id(connection)}"
    limit = _limits.get(key)
    if limit is None:
        limit = _limits[key] = _Limit()
    if limit.limiter.allow():
        return True, False
    now = _clock()
    if now < limit.quiet_until:
        return False, False
    limit.quiet_until = now + QUIET
    return False, True


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/start",
    vol.Required("targets"): TARGETS,
    vol.Optional("pattern"): REF,  # a built-in pattern or one of your own, by name
    vol.Optional("effect", default=music_engine.PULSE): vol.In(music_engine.EFFECTS),
    vol.Optional("sensitivity", default=music_engine.DEFAULT_SENSITIVITY): vol.All(_whole, vol.Range(min=1, max=100)),
    vol.Optional("mode"): vol.In(mapping.MODES),  # areas: string (default) or spatial
    vol.Optional("axis"): dict,  # areas, spatial mode; checked by mapping.parse_axis
    vol.Optional("idle_effect"): REF,  # what shows when the music stops (default: the pattern's own, else steady)
})
@websocket_api.async_response
async def ws_start(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Take the lights for music. They are let go (and restored) on music/stop or when this connection closes."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    targets = msg["targets"]
    if not targets["lights"] and not targets["areas"]:
        connection.send_error(msg["id"], "invalid_format", "Pick at least one light or area")
        return
    if not may_control(hass, connection, manager.mgr, targets["lights"], targets["areas"]):
        connection.send_error(msg["id"], "unauthorized", "You may not control those lights")
        return
    connection.subscriptions[msg["id"]] = lambda: manager.stop(connection)  # runs when the socket closes
    try:
        result = await manager.async_start(connection, targets["lights"], targets["areas"], msg.get("pattern"),
                                           msg["effect"], msg["sensitivity"], msg.get("mode", "string"), msg.get("axis"),
                                           msg.get("idle_effect"))
    except MusicError as err:
        connection.subscriptions.pop(msg["id"], None)
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/frame",
    vol.Required("targets"): TARGETS,
    vol.Required("level"): object,  # checked in depth by music_engine.parse_features (numbers 0-1, finite)
    vol.Required("bass"): object,
    vol.Required("mid"): object,
    vol.Required("treble"): object,
    vol.Required("beat"): object,
    vol.Optional("bpm"): object,
})
@callback
def ws_frame(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """One frame of audio features. Over 40 a second per user is refused (one rate_limited error, then frames are dropped silently for a second)."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    ok, tell = _allowed(connection)
    if not ok:
        if tell:  # one error, then silence for a moment
            connection.send_error(msg["id"], "rate_limited", "Too many music frames; send at most 40 a second")
        return
    try:
        features = music_engine.parse_features(msg)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    targets = msg["targets"]
    if not targets["lights"] and not targets["areas"]:
        connection.send_error(msg["id"], "invalid_format", "Pick at least one light or area")
        return
    fed = manager.feed(connection, targets["lights"], targets["areas"], features)
    if not fed:
        connection.send_error(msg["id"], "not_found", "Music isn't running for those lights or areas")
        return
    connection.send_result(msg["id"], {"fed": fed})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/stop",
    vol.Optional("targets"): TARGETS,  # leave out to stop everything this connection started
})
@callback
def ws_stop(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """End this connection's music sessions (or those touching the targets); the lights go back to what they showed."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    targets = msg.get("targets")
    n = manager.stop(connection, targets["lights"] if targets else None, targets["areas"] if targets else None)
    connection.send_result(msg["id"], {"stopped": n})
