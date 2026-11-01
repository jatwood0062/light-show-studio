"""Websocket API for playlists and schedules."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import SCHEDULER_KEY
from .library import SHOW_PLAYER_KEY
from .scheduler import SCHEDULE_FIELDS, Scheduler, finish_schedule

P = "light_show_studio"


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_playlists_list, ws_playlists_save, ws_playlists_delete, ws_set_playlist,
                    ws_schedules_list, ws_schedules_save, ws_schedules_delete, ws_schedules_toggle,
                    ws_schedules_skip_today, ws_schedules_resume, ws_schedule_subscribe):
        websocket_api.async_register_command(hass, command)


def _scheduler(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> Scheduler | None:
    if (scheduler := hass.data.get(SCHEDULER_KEY)) is None:
        connection.send_error(msg["id"], "not_found", "The scheduler is not set up")
    return scheduler


def _error(connection: websocket_api.ActiveConnection, msg: dict[str, Any], err: Exception) -> None:
    if isinstance(err, KeyError):
        connection.send_error(msg["id"], "not_found", str(err.args[0]) if err.args else "Not found")
    elif isinstance(err, vol.Invalid):
        connection.send_error(msg["id"], "invalid_format", str(err))
    else:
        connection.send_error(msg["id"], "failed", str(err))


# --- playlists ------------------------------------------------------------------------
@websocket_api.websocket_command({vol.Required("type"): f"{P}/playlists/list"})
@callback
def ws_playlists_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    player = hass.data.get(SHOW_PLAYER_KEY)
    connection.send_result(msg["id"], {
        "playlists": scheduler.playlists,
        "active": player.playlist_id if player else scheduler.active_playlist,
    })


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/playlists/save",
    vol.Optional("playlist_id"): str,
    vol.Required("name"): str,
    vol.Required("show_ids"): [str],
})
@websocket_api.require_admin
@callback
def ws_playlists_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        playlist = scheduler.save_playlist(msg.get("playlist_id"), msg["name"], msg["show_ids"])
    except (KeyError, ValueError) as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"playlist": playlist})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/playlists/delete", vol.Required("playlist_id"): str})
@websocket_api.require_admin
@callback
def ws_playlists_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        scheduler.delete_playlist(msg["playlist_id"])
    except (KeyError, ValueError) as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/player/set_playlist",
    vol.Required("playlist_id"): vol.Any(None, str),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_playlist(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                          msg: dict[str, Any]) -> None:
    player = hass.data.get(SHOW_PLAYER_KEY)
    if player is None:
        connection.send_error(msg["id"], "not_found", "The light show player is not set up")
        return
    try:
        await player.async_set_playlist(msg["playlist_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {})


# --- schedules ------------------------------------------------------------------------
def _view(scheduler: Scheduler, s: dict[str, Any], now) -> dict[str, Any]:
    playlist = scheduler.get_playlist(s["playlist_id"])
    return {
        **s,
        **scheduler.window_info(s, now),
        "playlist": playlist["name"] if playlist else None,
        "state": scheduler.schedule_state(s["id"]),
        "skipped": s["id"] in scheduler._skip,
        "conflicts": scheduler.conflicts_for(s, now),
    }


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _summary(scheduler: Scheduler) -> dict[str, Any]:
    nxt = scheduler.next_info
    return {
        "status": scheduler.status(),
        "active": scheduler.active_info(),
        "next": None if nxt is None else {**nxt, "start": _iso(nxt["start"]), "end": _iso(nxt["end"])},
    }


@websocket_api.websocket_command({vol.Required("type"): f"{P}/schedules/list"})
@callback
def ws_schedules_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    now = dt_util.utcnow()
    connection.send_result(msg["id"], {
        "schedules": [_view(scheduler, s, now) for s in scheduler.schedules],
        "playlists": scheduler.playlists,
        **_summary(scheduler),
    })


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/schedules/save",
    vol.Optional("schedule_id"): str,
    **SCHEDULE_FIELDS,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedules_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                            msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    data = {k: v for k, v in msg.items() if k not in ("id", "type", "schedule_id")}
    try:
        data = finish_schedule(data)
        sched = await scheduler.async_save_schedule(msg.get("schedule_id"), data)
    except (KeyError, ValueError, vol.Invalid) as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"schedule": _view(scheduler, sched, dt_util.utcnow())})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/schedules/delete", vol.Required("schedule_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedules_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                              msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        await scheduler.async_delete_schedule(msg["schedule_id"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/schedules/toggle",
    vol.Required("schedule_id"): str,
    vol.Optional("enabled"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedules_toggle(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                              msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    sched = scheduler.get_schedule(msg["schedule_id"])
    if sched is None:
        connection.send_error(msg["id"], "not_found", f"No schedule {msg['schedule_id']}")
        return
    enabled = msg.get("enabled", not sched["enabled"])
    await scheduler.async_set_enabled(sched["id"], enabled)
    connection.send_result(msg["id"], {"enabled": enabled})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/schedules/skip_today",
    vol.Required("schedule_id"): str,
    vol.Optional("skip", default=True): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedules_skip_today(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                                  msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        await scheduler.async_skip(msg["schedule_id"], msg["skip"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/schedules/resume"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedules_resume(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                              msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"resumed": await scheduler.async_resume()})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/schedule/subscribe"})
@callback
def ws_schedule_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                          msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return

    @callback
    def send() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], _summary(scheduler)))

    connection.subscriptions[msg["id"]] = scheduler.add_listener(send)
    connection.send_result(msg["id"])
    send()
