"""Diagnostics for a config entry (Settings > Devices & services > the entry > Download diagnostics): what the
entry is, its options and a summary of what it is doing, with addresses and device ids redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import (CONF_ALLOWED_SOURCES, CONF_DEVICE_ID, CONF_FPP_REMOTES, CONF_MAC, CONF_TYPE, MAP_KEY, SCHEDULER_KEY,
                    TYPE_PLAYER)
from .controller import C9Controller
from .library import SHOW_PLAYER_KEY

TO_REDACT = {CONF_HOST, "ip", "ip_address", CONF_MAC, CONF_DEVICE_ID, CONF_FPP_REMOTES, CONF_ALLOWED_SOURCES, "unique_id"}


def _light(hass: HomeAssistant, entry: ConfigEntry, ctl: C9Controller) -> dict[str, Any]:
    zones = [name for name, _slices in getattr(ctl, "_zones", {}).values()]
    mgr = hass.data.get(MAP_KEY)
    runs = [r for r in mgr.model.runs if r.entry_id == entry.entry_id] if mgr is not None else []
    return {"class": type(ctl).__name__, "online": ctl.online, "last_seen": ctl.last_seen, "is_on": ctl.is_on,
            "brightness": ctl.brightness, "effect": ctl.effect, "pattern": ctl.pattern,
            "pattern_effect": ctl.pattern_effect, "speed": ctl.speed, "show_mode": ctl.show_mode,
            "custom": ctl.custom, "bulbs": ctl.bulbs, "segmented": ctl.segmented, "scanning": ctl.scanning,
            "default_transition": ctl.default_transition, "show_lead_ms": ctl.show_lead_ms,
            "zones": zones, "map_runs": len(runs), "map_areas": sorted({r.area for r in runs if r.area})}


def _player(hass: HomeAssistant) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if (sched := hass.data.get(SCHEDULER_KEY)) is not None:
        out["scheduler"] = {
            "status": sched.status(), "next": sched.next_info, "active": sched.active_info(),
            "active_playlist": sched.active_playlist,
            "playlists": [{"id": p["id"], "name": p["name"], "shows": len(p["show_ids"])} for p in sched.playlists],
            "schedules": [{"id": s["id"], "name": s.get("name"), "enabled": s.get("enabled"),
                           "state": sched.schedule_state(s["id"])} for s in sched.schedules]}
    if (player := hass.data.get(SHOW_PLAYER_KEY)) is not None:
        out["player"] = {k: getattr(player, k, None) for k in ("state", "shuffle", "repeat", "transition",
                                                               "transition_effect", "playlist_id", "playlist_name")}
        out["player"]["queue"] = len(getattr(player, "queue", ()))
        out["player"]["speakers"] = len(getattr(player, "speakers", ()))
    return out


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    kind = entry.data.get(CONF_TYPE) or "govee"
    data: dict[str, Any] = {"entry": {"type": kind, "title": entry.title, "version": f"{entry.version}.{entry.minor_version}",
                                      "state": str(entry.state), "data": dict(entry.data), "options": dict(entry.options)}}
    if kind == TYPE_PLAYER:
        data.update(_player(hass))
    elif isinstance(ctl := getattr(entry, "runtime_data", None), C9Controller):
        data["light"] = _light(hass, entry, ctl)
    return async_redact_data(data, TO_REDACT)
