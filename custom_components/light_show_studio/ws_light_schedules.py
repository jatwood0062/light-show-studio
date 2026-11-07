"""Websocket API for light schedules (lights and areas on at a time, off later)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import LIGHT_SCHEDULER_KEY
from .light_schedules import LightScheduler

P = "light_show_studio/light_schedules"
ID = vol.All(str, vol.Length(min=1, max=64))
_FIELDS = ("name", "enabled", "days", "start", "end", "date_from", "date_to", "targets", "look", "fade_in_s", "fade_out_s")


def async_register(hass: HomeAssistant) -> None:
    for command in (ws_list, ws_save, ws_delete, ws_toggle, ws_run_now, ws_stop, ws_subscribe):
        websocket_api.async_register_command(hass, command)


def _scheduler(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
               msg: dict[str, Any]) -> LightScheduler | None:
    if (scheduler := hass.data.get(LIGHT_SCHEDULER_KEY)) is None:
        connection.send_error(msg["id"], "not_found", "The light scheduler is not set up")
    return scheduler


def _error(connection: websocket_api.ActiveConnection, msg: dict[str, Any], err: Exception) -> None:
    if isinstance(err, KeyError):
        connection.send_error(msg["id"], "not_found", str(err.args[0]) if err.args else "Not found")
    elif isinstance(err, vol.Invalid):
        connection.send_error(msg["id"], "invalid_format", str(err))
    else:
        connection.send_error(msg["id"], "failed", str(err))


def _one(scheduler: LightScheduler, schedule_id: str) -> dict[str, Any]:
    return next(v for v in scheduler.views() if v["id"] == schedule_id)


@websocket_api.websocket_command({vol.Required("type"): f"{P}/list"})
@callback
def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"schedules": scheduler.views()})


# The fields are checked in depth, with readable messages, by light_schedules.validate_light_schedule.
@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/save",
    vol.Optional("schedule_id"): ID,
    **{vol.Optional(f): object for f in _FIELDS},
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    data = {k: v for k, v in msg.items() if k not in ("id", "type", "schedule_id")}
    try:
        sched = await scheduler.async_save_schedule(msg.get("schedule_id"), data)
    except (KeyError, ValueError, vol.Invalid) as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"schedule": _one(scheduler, sched["id"])})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/delete", vol.Required("schedule_id"): ID})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        await scheduler.async_delete_schedule(msg["schedule_id"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): f"{P}/toggle",
    vol.Required("schedule_id"): ID,
    vol.Optional("enabled"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_toggle(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    sched = scheduler.get_schedule(msg["schedule_id"])
    if sched is None:
        connection.send_error(msg["id"], "not_found", f"No light schedule {msg['schedule_id']}")
        return
    enabled = msg.get("enabled", not sched["enabled"])
    await scheduler.async_set_enabled(sched["id"], enabled)
    connection.send_result(msg["id"], {"enabled": enabled, "schedule": _one(scheduler, sched["id"])})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/run_now", vol.Required("schedule_id"): ID})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_run_now(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        await scheduler.async_run_now(msg["schedule_id"])
    except (KeyError, ValueError) as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"schedule": _one(scheduler, msg["schedule_id"])})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/stop", vol.Required("schedule_id"): ID})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_stop(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return
    try:
        stopped = await scheduler.async_stop(msg["schedule_id"])
    except KeyError as err:
        _error(connection, msg, err)
        return
    connection.send_result(msg["id"], {"stopped": stopped, "schedule": _one(scheduler, msg["schedule_id"])})


@websocket_api.websocket_command({vol.Required("type"): f"{P}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (scheduler := _scheduler(hass, connection, msg)) is None:
        return

    @callback
    def send() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"schedules": scheduler.views()}))

    connection.subscriptions[msg["id"]] = scheduler.add_listener(send)
    connection.send_result(msg["id"])
    send()
