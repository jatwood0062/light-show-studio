"""Websocket API for your own patterns: list, save, delete (admin), live preview and paint."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import music_engine, patterns, user_patterns
from .const import PREVIEW_KEY, USER_PATTERNS_KEY
from .patternpreview import PreviewError, PreviewManager
from .pattern_store import PatternStore
from .ws_music import may_control

P = "light_show_studio/patterns"
REF = vol.All(str, vol.Length(min=1, max=64))


PREVIEW_RATE, PAINT_RATE = 5.0, 20.0  # calls a second per connection (painting while dragging sends many small batches)
_limits: dict[tuple[str, int], music_engine.RateLimiter] = {}  # by (kind, connection)


def _allowed(connection: Any, msg: dict[str, Any], kind: str, rate: float, burst: float) -> bool:
    """Per-connection rate limit; sends rate_limited when over."""
    key = (kind, id(connection))
    limiter = _limits.get(key)
    if limiter is None:
        limiter = _limits[key] = music_engine.RateLimiter(rate, burst)
        connection.subscriptions[("pattern_rate", key)] = lambda: _limits.pop(key, None)  # forgotten when it closes
    if limiter.allow():
        return True
    connection.send_error(msg["id"], "rate_limited", "Too many calls; slow down")
    return False


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_list, ws_save, ws_delete, ws_preview, ws_preview_stop, ws_paint):
        websocket_api.async_register_command(hass, command)


def _store(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> PatternStore | None:
    store = hass.data.get(USER_PATTERNS_KEY)
    if store is None:
        connection.send_error(msg["id"], "not_found", "Patterns are not set up")
    return store


def _preview(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> PreviewManager | None:
    preview = hass.data.get(PREVIEW_KEY)
    if preview is None:
        connection.send_error(msg["id"], "not_found", "Pattern previews are not set up")
    return preview


def _error(connection: Any, msg: dict[str, Any], err: Exception) -> None:
    if isinstance(err, KeyError):
        connection.send_error(msg["id"], "not_found", str(err.args[0]) if err.args else "Not found")
    elif isinstance(err, ValueError):
        connection.send_error(msg["id"], "invalid_format", str(err))
    else:
        connection.send_error(msg["id"], "failed", str(err))


def limits() -> dict[str, int]:
    return {"max_patterns": user_patterns.MAX_PATTERNS, "max_name": user_patterns.MAX_NAME,
            "max_palette_colours": user_patterns.MAX_COLOURS, "max_bulbs_per_colour": user_patterns.MAX_BULBS_PER_COLOUR,
            "max_light_pixels": user_patterns.MAX_LIGHT_PIXELS, "max_area_points": user_patterns.MAX_AREA_POINTS,
            "max_painted": user_patterns.MAX_PAINTED, "max_paint_batch": user_patterns.MAX_PAINT_BATCH,
            "position_max": user_patterns.POS_MAX, "max_total_painted": user_patterns.MAX_TOTAL_PAINTED}


@websocket_api.websocket_command({vol.Required("type"): f"{P}/list"})
@callback
def ws_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (store := _store(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"patterns": store.view(), "builtin": patterns.PATTERN_LIST,
                                       "effects": patterns.PATTERN_EFFECTS, "map_effects": patterns.MAP_EFFECTS,
                                       "limits": limits()})


# The pattern is checked in depth, with readable messages, by user_patterns.validate.
@websocket_api.websocket_command({vol.Required("type"): f"{P}/save", vol.Required("pattern"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (store := _store(hass, connection, msg)) is None:
        return
    try:
        saved = await store.async_save_pattern(msg["pattern"])
    except (KeyError, ValueError) as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"pattern": saved})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/delete", vol.Required("pattern_id"): REF})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (store := _store(hass, connection, msg)) is None:
        return
    try:
        await store.async_delete_pattern(msg["pattern_id"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/preview",
    vol.Exclusive("entry_id", "target"): REF,
    vol.Exclusive("area", "target"): REF,
    vol.Required("pattern"): vol.Any(dict, REF),  # an unsaved pattern object, or the name of a saved / built-in one
    vol.Optional("effect", default=patterns.STEADY): vol.In(patterns.ALL_EFFECTS),
    vol.Optional("speed", default=patterns.DEFAULT_SPEED): vol.All(int, vol.Range(min=1, max=100)),
})
@websocket_api.async_response
async def ws_preview(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Show the pattern live on a light or area. Calling it again changes the preview in place. It ends on
    preview_stop, after 60 s without a preview / paint call, or when this connection closes; the lights go back."""
    if (preview := _preview(hass, connection, msg)) is None:
        return
    if not _allowed(connection, msg, "preview", PREVIEW_RATE, 10.0):
        return
    lights = [msg["entry_id"]] if msg.get("entry_id") else []
    if not may_control(hass, connection, preview.mgr, lights, [msg["area"]] if msg.get("area") else []):
        connection.send_error(msg["id"], "unauthorized", "You may not control those lights")
        return
    connection.subscriptions[("pattern_preview", id(connection))] = lambda: preview.stop(connection)
    try:
        result = await preview.async_start(connection, msg.get("entry_id"), msg.get("area"), msg["pattern"],
                                           msg["effect"], msg["speed"])
    except PreviewError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): f"{P}/preview_stop"})
@callback
def ws_preview_stop(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    if (preview := _preview(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"stopped": preview.stop(connection)})


def _pixel(row: Any) -> tuple[int, tuple[int, int, int]]:
    if not isinstance(row, (list, tuple)) or len(row) != 4:
        raise vol.Invalid("each pixel is [index, r, g, b]")
    idx = row[0]
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < user_patterns.MAX_LIGHT_PIXELS:
        raise vol.Invalid("pixel index out of range")
    try:
        return idx, user_patterns.clean_colour(list(row[1:]))
    except ValueError as err:
        raise vol.Invalid(str(err)) from err


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/paint",
    vol.Required("entry_id"): REF,
    vol.Required("set"): vol.All([_pixel], vol.Length(min=1, max=user_patterns.MAX_PAINT_BATCH)),
})
@websocket_api.async_response
async def ws_paint(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Paint pixels of the pattern being previewed (a pixels pattern), shown on the light at once."""
    if (preview := _preview(hass, connection, msg)) is None:
        return
    if not _allowed(connection, msg, "paint", PAINT_RATE, 30.0):
        return
    if not may_control(hass, connection, preview.mgr, [msg["entry_id"]], []):
        connection.send_error(msg["id"], "unauthorized", "You may not control that light")
        return
    try:
        n = preview.paint(connection, msg["entry_id"], msg["set"])
    except PreviewError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {"painted": n})
