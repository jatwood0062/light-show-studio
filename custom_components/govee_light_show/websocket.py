"""Websocket API for the Govee C9 card: watch a string's bulbs live and paint single bulbs."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BULBS,
    CONF_SKU,
    CONF_START_PIXEL,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    TYPE_PLAYER,
    TYPE_TWINKLY,
)
from .controller import C9Controller
from .receiver import DDP_PORT, E131_PORT, MAX_PIXELS

CARD_FPS = 10  # the card doesn't need every frame of a show


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_set_bulbs)
    websocket_api.async_register_command(hass, ws_strands)
    websocket_api.async_register_command(hass, ws_identify)
    websocket_api.async_register_command(hass, ws_strand_update)


def _controller(hass: HomeAssistant, entity_id: str) -> C9Controller | None:
    ent = er.async_get(hass).async_get(entity_id)
    if ent is None or ent.platform != DOMAIN or ent.config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(ent.config_entry_id)
    ctl = getattr(entry, "runtime_data", None) if entry else None
    return ctl if isinstance(ctl, C9Controller) else None


def _hex(c: tuple[int, int, int]) -> str:
    return "%02x%02x%02x" % c


@websocket_api.websocket_command({
    vol.Required("type"): "govee_light_show/subscribe",
    vol.Required("entity_id"): cv.entity_id,
})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                 msg: dict[str, Any]) -> None:
    ctl = _controller(hass, msg["entity_id"])
    if ctl is None:
        connection.send_error(msg["id"], "not_found", f"{msg['entity_id']} is not a Govee C9 light")
        return
    pending = None
    first = True

    @callback
    def send() -> None:
        nonlocal pending, first
        pending = None
        event = {"on": ctl.is_on, "show": ctl.show_mode, "bulbs": ctl.bulbs,
                 "frame": "".join(_hex(c) for c in ctl.frame)}
        if first and (layout := getattr(ctl, "layout", None)):  # LED positions: only needed once
            event["layout"] = layout
        first = False
        connection.send_message(websocket_api.event_message(msg["id"], event))

    @callback
    def changed() -> None:
        nonlocal pending
        if pending is None:
            pending = hass.loop.call_later(1 / CARD_FPS, send)

    unsubs = [ctl.add_frame_listener(changed), ctl.add_listener(changed)]

    @callback
    def unsubscribe() -> None:
        for u in unsubs:
            u()
        if pending:
            pending.cancel()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    send()


def _colour(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


@websocket_api.websocket_command({
    vol.Required("type"): "govee_light_show/set_bulbs",
    vol.Required("entity_id"): cv.entity_id,
    # bulb number (1-based) -> "#rrggbb", or null to turn that bulb off
    vol.Required("bulbs"): {vol.Coerce(int): vol.Any(None, vol.Match(r"^#?[0-9a-fA-F]{6}$"))},
})
@websocket_api.async_response
async def ws_set_bulbs(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                       msg: dict[str, Any]) -> None:
    ctl = _controller(hass, msg["entity_id"])
    if ctl is None:
        connection.send_error(msg["id"], "not_found", f"{msg['entity_id']} is not a Govee C9 light")
        return
    for n, value in msg["bulbs"].items():
        if 1 <= n <= ctl.bulbs:
            colour = _colour(value)
            on = colour is not None and max(colour) > 0
            await ctl.async_set_bulb(n - 1, on, colour if on else None, 255 if on else None)
    connection.send_result(msg["id"])


# --- the strands list -------------------------------------------------------------
def _light_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_TYPE) != TYPE_PLAYER]


def _loaded_controller(entry: ConfigEntry) -> C9Controller | None:
    ctl = getattr(entry, "runtime_data", None) if entry.state is ConfigEntryState.LOADED else None
    return ctl if isinstance(ctl, C9Controller) else None


@websocket_api.websocket_command({vol.Required("type"): "govee_light_show/strands"})
@callback
def ws_strands(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    ents = er.async_get(hass)
    strands = []
    for entry in _light_entries(hass):
        o = {**entry.data, **entry.options}
        ctl = _loaded_controller(entry)
        uid = entry.unique_id or entry.data[CONF_HOST]
        seen = ctl.last_seen if ctl else None
        strands.append({
            "entry_id": entry.entry_id,
            "title": entry.title,
            "kind": "twinkly" if o.get(CONF_TYPE) == TYPE_TWINKLY else "govee",
            "host": entry.data[CONF_HOST],
            "model": o.get(CONF_SKU) or "",
            "start_pixel": o.get(CONF_START_PIXEL, 1),
            "bulbs": ctl.bulbs if ctl else o.get(CONF_BULBS, DEFAULT_BULBS),
            "online": ctl.online if ctl else None,
            "last_seen": dt_util.utc_from_timestamp(seen).isoformat() if seen else None,
            "loaded": ctl is not None,
            "entity_id": ents.async_get_entity_id("light", DOMAIN, f"{uid}_string"),
        })
    connection.send_result(msg["id"], {"strands": strands, "ddp_port": DDP_PORT, "e131_port": E131_PORT,
                                       "max_pixels": MAX_PIXELS})


@websocket_api.websocket_command({
    vol.Required("type"): "govee_light_show/identify",
    vol.Required("entry_id"): str,
})
@callback
def ws_identify(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    ctl = _loaded_controller(entry) if entry and entry.domain == DOMAIN else None
    if ctl is None:
        connection.send_error(msg["id"], "not_found", "That light is not set up")
        return
    ctl.start_identify()
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "govee_light_show/strand/update",
    vol.Required("entry_id"): str,
    vol.Optional("title"): vol.All(str, vol.Length(min=1, max=100)),
    vol.Optional("start_pixel"): vol.All(int, vol.Range(min=1, max=MAX_PIXELS)),
    vol.Optional("bulbs"): vol.All(int, vol.Range(min=1, max=1020)),
})
@websocket_api.require_admin
@callback
def ws_strand_update(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                     msg: dict[str, Any]) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.data.get(CONF_TYPE) == TYPE_PLAYER:
        connection.send_error(msg["id"], "not_found", "That light is not set up")
        return
    o = {**entry.data, **entry.options}
    start, bulbs = msg.get("start_pixel", o.get(CONF_START_PIXEL, 1)), msg.get("bulbs", o.get(CONF_BULBS, DEFAULT_BULBS))
    if start + bulbs - 1 > MAX_PIXELS:
        connection.send_error(msg["id"], "invalid_format", f"Pixels run past {MAX_PIXELS}")
        return
    if o.get(CONF_TYPE) != TYPE_TWINKLY and bulbs > 255:
        connection.send_error(msg["id"], "invalid_format", "A Govee light takes at most 255 bulbs")
        return
    options = {**entry.options}
    for key in (CONF_START_PIXEL, CONF_BULBS):
        if key in msg:
            options[key] = msg[key]
    hass.config_entries.async_update_entry(entry, title=msg.get("title", entry.title), options=options)
    connection.send_result(msg["id"], {})
