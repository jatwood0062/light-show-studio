"""Websocket API for the Map tab: read and replace the map, start, stop and speed up zones, and the camera scan."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from . import fade, mapping, patterns, scan_codes
from .mapmanager import MapManager, get_manager
from .mapping import MapError, to_dict

ZONE_ID = vol.All(str, vol.Length(min=1, max=70))  # a zone id, or "area:<area id>" for an area's own
AREA_REF = vol.All(str, vol.Length(min=1, max=64))  # an area's id or name
ENTRY_ID = vol.All(str, vol.Length(min=1, max=64))
PATTERN = vol.All(str, vol.Length(min=1, max=64), patterns.valid_pattern)  # a built-in pattern or one of your own
EFFECT = vol.All(str, patterns.valid_effect)  # a pattern effect or a map effect
TRANSITION = vol.All(vol.Coerce(float), vol.Range(min=0, max=fade.MAX_TRANSITION))  # seconds
COLOUR = vol.All([vol.All(int, vol.Range(min=0, max=255))], vol.Length(min=3, max=3))  # [r, g, b]


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_set)
    websocket_api.async_register_command(hass, ws_start)
    websocket_api.async_register_command(hass, ws_stop)
    websocket_api.async_register_command(hass, ws_speed)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_scan_start)
    websocket_api.async_register_command(hass, ws_scan_frame)
    websocket_api.async_register_command(hass, ws_scan_segment)
    websocket_api.async_register_command(hass, ws_scan_stop)


def _manager(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> MapManager | None:
    manager = get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_found", "The light map is not set up")
    return manager


def _snapshot(manager: MapManager) -> dict[str, Any]:
    data = to_dict(manager.model)
    return {"map": data, "areas": data["areas"], "lights": manager.lights(), "running": manager.running,
            "scanning": manager.scanning, "spatial_effects": list(patterns.MAP_EFFECTS),
            "effects": list(patterns.PATTERN_EFFECTS), "patterns": patterns.pattern_names()}


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
    vol.Exclusive("zone_id", "target"): ZONE_ID,
    vol.Exclusive("area", "target"): AREA_REF,  # an ad-hoc zone over every run in an area (not saved)
    vol.Optional("pattern"): PATTERN,
    vol.Optional("effect"): EFFECT,  # Steady, Twinkle, Chase ... or a map effect (Sweep, Ripple ...)
    vol.Optional("speed"): vol.All(int, vol.Range(min=1, max=100)),
    vol.Optional("transition"): TRANSITION,  # seconds to fade in (and out when stopped); default 0.7
    vol.Optional("mode"): vol.In(mapping.MODES),  # area zones only
    vol.Optional("axis"): dict,  # area zones only; checked by mapping.parse_axis
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_start(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    if ("zone_id" in msg) == ("area" in msg):
        connection.send_error(msg["id"], "invalid_format", "Give either zone_id or area")
        return
    try:
        if "area" in msg:
            await manager.async_start_area(msg["area"], msg.get("pattern"), msg.get("effect"), msg.get("speed"),
                                           msg.get("mode", "string"), msg.get("axis"), msg.get("transition"),
                                           msg.get("transition"))
        else:
            await manager.async_start_zone(msg["zone_id"], msg.get("pattern"), msg.get("effect"), msg.get("speed"),
                                           msg.get("transition"))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/zone/stop",
    vol.Required("zone_id"): ZONE_ID,
    vol.Optional("transition"): TRANSITION,  # seconds to fade out; default: the zone's own (0.7)
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_stop(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    try:
        await manager.async_stop_zone(msg["zone_id"], msg.get("transition"))
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


# --- camera scan: the light shows a code sequence while the phone records it ----------------------
SEQ = vol.All(int, vol.Range(min=0, max=2**53 - 1))  # the panel's own request counter, echoed back in the reply


def _scan_reply(msg: dict[str, Any]) -> dict[str, Any]:
    return {"seq": msg["seq"]} if "seq" in msg else {}


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/scan/start",
    vol.Required("entry_id"): ENTRY_ID,
    vol.Optional("first"): vol.All(int, vol.Range(min=0, max=mapping.MAX_PIXELS - 1)),  # scan just a run's pixels
    vol.Optional("count"): vol.All(int, vol.Range(min=1, max=mapping.MAX_PIXELS)),
    vol.Optional("mode"): vol.In(scan_codes.MODES),  # "colour" (default) or "white" flashes
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_start(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Take the light for a scan. It is let go (and restored) on scan/stop, after 120 s without a frame, or
    when this connection closes."""
    if not (manager := _manager(hass, connection, msg)):
        return
    token = connection  # the connection owns its scan: only it may drive or stop it
    entry_id = msg["entry_id"]
    connection.subscriptions[msg["id"]] = lambda: manager.scan_stop(entry_id, token)  # runs when the socket closes
    try:
        result = await manager.async_scan_start(entry_id, token, msg.get("first", 0), msg.get("count"),
                                                 msg.get("mode", scan_codes.DEFAULT_MODE))
    except HomeAssistantError as err:
        connection.subscriptions.pop(msg["id"], None)
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], result)  # {bulbs, frames, first, level, mode, hold_ms}


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/scan/frame",
    vol.Required("entry_id"): ENTRY_ID,
    vol.Required("frame"): vol.All(int, vol.Range(min=0, max=scan_codes.frame_count(mapping.MAX_PIXELS) - 1)),
    vol.Optional("seq"): SEQ,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_frame(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if not (manager := _manager(hass, connection, msg)):
        return
    try:
        await manager.async_scan_frame(msg["entry_id"], msg["frame"], connection)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], _scan_reply(msg))


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/map/scan/segment",
    vol.Required("entry_id"): ENTRY_ID,
    # one stretch (start + count, optional colour: white by default) ...
    vol.Optional("start"): vol.All(int, vol.Range(min=0, max=mapping.MAX_PIXELS - 1)),  # a pixel of the light
    vol.Optional("count"): vol.All(int, vol.Range(min=1, max=mapping.MAX_PIXELS)),
    vol.Optional("colour"): COLOUR,
    # ... or up to three at once, each in its own colour (red, green, blue by default)
    vol.Optional("seq"): SEQ,
    vol.Optional("segments"): vol.All([{
        vol.Required("start"): vol.All(int, vol.Range(min=0, max=mapping.MAX_PIXELS - 1)),
        vol.Required("count"): vol.All(int, vol.Range(min=1, max=mapping.MAX_PIXELS)),
        vol.Optional("colour"): COLOUR,
    }], vol.Length(min=1, max=3)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_segment(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Glow scan frame: only pixels start..start+count-1 lit (in `colour`), or up to 3 `segments` lit at once; the rest
    dark (scan/frame 0 is the dark reference)."""
    if not (manager := _manager(hass, connection, msg)):
        return
    if "segments" not in msg and not ("start" in msg and "count" in msg):
        connection.send_error(msg["id"], "invalid_format", "Give start and count, or segments")
        return
    try:
        await manager.async_scan_segment(msg["entry_id"], msg.get("start"), msg.get("count"), msg.get("colour"),
                                         msg.get("segments"), connection)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], _scan_reply(msg))


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/map/scan/stop", vol.Required("entry_id"): ENTRY_ID})
@websocket_api.require_admin
@callback
def ws_scan_stop(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if manager := _manager(hass, connection, msg):
        try:
            manager.check_scan_owner(msg["entry_id"], connection)
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "failed", str(err))
            return
        manager.scan_stop(msg["entry_id"], connection)
        connection.send_result(msg["id"], {})
