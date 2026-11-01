"""Websocket API for the Govee C9 card: watch a string's bulbs live and paint single bulbs."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .controller import C9Controller

CARD_FPS = 10  # the card doesn't need every frame of a show


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_set_bulbs)


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

    @callback
    def send() -> None:
        nonlocal pending
        pending = None
        connection.send_message(websocket_api.event_message(msg["id"], {
            "on": ctl.is_on, "show": ctl.show_mode, "bulbs": ctl.bulbs,
            "frame": "".join(_hex(c) for c in ctl.frame),
        }))

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
