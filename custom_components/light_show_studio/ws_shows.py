"""Websocket API for the player card: the show library and live player state."""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import SCHEDULER_KEY, CONF_SPEAKERS, CONF_TRANSITION, CONF_TRANSITION_EFFECT, CONF_TYPE, DOMAIN, TYPE_PLAYER
from .effects import EFFECT_LIST
from .library import SHOW_PLAYER_KEY, Show, library_for
from .outputs import normalize_speakers
from .transitions import MODES

TICK = timedelta(seconds=1)


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_delete)
    websocket_api.async_register_command(hass, ws_reorder)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_set_transition)
    websocket_api.async_register_command(hass, ws_set_speakers)


def _row(s: Show) -> dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "duration": s.duration, "has_audio": s.audio is not None,
        "size": s.size, "fseq": os.path.basename(s.fseq),
        "audio": os.path.basename(s.audio) if s.audio else None,
    }


async def _refresh(hass: HomeAssistant) -> None:
    if player := hass.data.get(SHOW_PLAYER_KEY):
        await player.async_refresh()


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/shows/list"})
@websocket_api.async_response
async def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                  msg: dict[str, Any]) -> None:
    shows = await hass.async_add_executor_job(library_for(hass).scan)
    connection.send_result(msg["id"], {"shows": [_row(s) for s in shows]})


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/shows/delete", vol.Required("show_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                    msg: dict[str, Any]) -> None:
    player = hass.data.get(SHOW_PLAYER_KEY)
    if player and player.show and player.show.id == msg["show_id"] and player.state != "idle":
        await player.async_stop(reason="show_deleted")
    try:
        found = await hass.async_add_executor_job(library_for(hass).delete, msg["show_id"])
    except (OSError, ValueError) as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    if not found:
        connection.send_error(msg["id"], "not_found", f"No show {msg['show_id']}")
        return
    if scheduler := hass.data.get(SCHEDULER_KEY):
        scheduler.remove_show(msg["show_id"])  # gone from every playlist
    await _refresh(hass)
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/shows/reorder",
    vol.Required("order"): [str],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_reorder(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                     msg: dict[str, Any]) -> None:
    try:
        await hass.async_add_executor_job(library_for(hass).reorder, msg["order"])
    except OSError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    await _refresh(hass)
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "light_show_studio/player/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                 msg: dict[str, Any]) -> None:
    player = hass.data.get(SHOW_PLAYER_KEY)
    if player is None:
        connection.send_error(msg["id"], "not_found", "The light show player is not set up")
        return

    @callback
    def send(*_: Any) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], player.snapshot()))

    @callback
    def tick(*_: Any) -> None:
        if player.state == "playing":
            send()

    unsub_listener = player.add_listener(send)
    unsub_timer = async_track_time_interval(hass, tick, TICK)

    @callback
    def unsubscribe() -> None:
        unsub_listener()
        unsub_timer()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    send()


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/player/set_transition",
    vol.Required("transition"): vol.In(MODES),
    vol.Optional("transition_effect"): vol.In(EFFECT_LIST),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_transition(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                            msg: dict[str, Any]) -> None:
    player = hass.data.get(SHOW_PLAYER_KEY)
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_TYPE) == TYPE_PLAYER), None)
    if player is None or entry is None:
        connection.send_error(msg["id"], "not_found", "The light show player is not set up")
        return
    options = {**entry.options, CONF_TRANSITION: msg["transition"]}
    if "transition_effect" in msg:
        options[CONF_TRANSITION_EFFECT] = msg["transition_effect"]
    # applied to the running player first, so a playing show is never interrupted; the entry's
    # update listener ignores these keys (see _IN_PLACE_OPTIONS) and does not reload
    player.set_transition(options[CONF_TRANSITION], options.get(CONF_TRANSITION_EFFECT))
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "light_show_studio/player/set_speakers",
    vol.Required("speakers"): [cv.entity_id],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_speakers(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                          msg: dict[str, Any]) -> None:
    player = hass.data.get(SHOW_PLAYER_KEY)
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_TYPE) == TYPE_PLAYER), None)
    if player is None or entry is None:
        connection.send_error(msg["id"], "not_found", "The light show player is not set up")
        return
    speakers = normalize_speakers(msg["speakers"])
    if bad := [s for s in speakers if not s.startswith("media_player.") or s == player.entity_id]:
        connection.send_error(msg["id"], "invalid_format", f"Not a speaker: {', '.join(bad)}")
        return
    # applied to the running player first (a show in progress keeps its speakers until the next show);
    # the entry's update listener ignores this key (see _IN_PLACE_OPTIONS) and does not reload
    applies = player.set_speakers(speakers)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SPEAKERS: speakers})
    connection.send_result(msg["id"], {"applies": applies})
