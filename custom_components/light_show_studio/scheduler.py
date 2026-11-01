"""Playlists and the scheduler: named lists of shows, and windows of time that play one.

One Store ("light_show_studio.schedule") holds both. The Scheduler keeps a single timer armed at the next
moment anything changes (a window opening or closing) and, each time it fires, works out what should be
running from the schedules and the clock, so a restart, an edit or a missed timer all end up in the same place.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import schedule_math as sm
from .const import (
    DOMAIN,
    EVENT_SCHEDULE_ENDED,
    EVENT_SCHEDULE_STARTED,
    SCHEDULE_STORE_KEY,
    SCHEDULER_KEY,
)
from .effects import EFFECT_LIST
from .library import SHOW_PLAYER_KEY

_LOGGER = logging.getLogger(__name__)

IDLE = "idle"  # the show player's idle state (showplayer.IDLE; not imported to keep this module light)
RUNNING, PAUSED, DONE = "running", "paused", "done"
STATUS_IDLE, STATUS_RUNNING, STATUS_PAUSED, STATUS_OFF = "idle", "running", "paused", "off"
STATUSES = [STATUS_IDLE, STATUS_RUNNING, STATUS_PAUSED, STATUS_OFF]
AT_END = ("off", "effect", "leave")
MAX_REARM = timedelta(hours=6)  # wake at least this often, in case the clock or the sun moved under us
SAVE_DELAY = 1.0


# --- validation ---------------------------------------------------------------------
def _hhmm(value: Any) -> str:
    try:
        t = sm.parse_hhmm(str(value))
    except (ValueError, TypeError) as err:
        raise vol.Invalid("expected a time like 18:00") from err
    return f"{t.hour:02d}:{t.minute:02d}"


def _mmdd(value: Any) -> str:
    try:
        m, d = sm.parse_mmdd(str(value))
    except (ValueError, TypeError) as err:
        raise vol.Invalid("expected a date like 12-25 (month-day)") from err
    return f"{m:02d}-{d:02d}"


def _edge(value: Any) -> dict[str, Any]:
    value = vol.Schema({
        vol.Required("type"): vol.In(sm.EDGE_TYPES),
        vol.Optional("time"): _hhmm,
        vol.Optional("offset_min", default=0): vol.All(vol.Coerce(int), vol.Range(min=-720, max=720)),
    })(value)
    if value["type"] == "time":
        if "time" not in value:
            raise vol.Invalid("a time is needed")
        return {"type": "time", "time": value["time"], "offset_min": 0}
    return {"type": value["type"], "offset_min": value["offset_min"]}


def _days(value: list[int]) -> list[int]:
    return sorted(set(value))


SCHEDULE_FIELDS: dict[Any, Any] = {
    vol.Required("name"): vol.All(cv.string, vol.Strip, vol.Length(min=1, max=60)),
    vol.Optional("enabled", default=True): cv.boolean,
    vol.Required("days"): vol.All([vol.All(vol.Coerce(int), vol.Range(min=0, max=6))], vol.Length(min=1), _days),
    vol.Required("start"): _edge,
    vol.Optional("end", default=None): vol.Any(None, _edge),
    vol.Optional("date_from", default=None): vol.Any(None, _mmdd),
    vol.Optional("date_to", default=None): vol.Any(None, _mmdd),
    vol.Required("playlist_id"): cv.string,
    vol.Optional("speakers", default=list): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional("volume", default=None): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0, max=100))),
    vol.Optional("shuffle", default=False): cv.boolean,
    vol.Optional("repeat"): cv.boolean,
    vol.Optional("at_end", default="off"): vol.In(AT_END),
    vol.Optional("end_effect"): vol.In(EFFECT_LIST),
}


def finish_schedule(data: dict[str, Any]) -> dict[str, Any]:
    """Cross-field rules, after the fields are valid."""
    out = dict(data)
    out["speakers"] = list(dict.fromkeys(out.get("speakers") or []))
    if out.get("end") is None:
        out["repeat"] = False  # a play-once schedule has no window to repeat in
    elif "repeat" not in out:
        out["repeat"] = True  # repeat all while the window is open
    if out["at_end"] == "effect":
        if not out.get("end_effect"):
            raise vol.Invalid("pick the effect to show at the end")
    else:
        out.pop("end_effect", None)
    if bool(out.get("date_from")) != bool(out.get("date_to")):
        raise vol.Invalid("set both season dates, or neither")
    return out


def validate_schedule(data: dict[str, Any]) -> dict[str, Any]:
    return finish_schedule(vol.Schema(SCHEDULE_FIELDS, extra=vol.REMOVE_EXTRA)(data))


@dataclass
class _Run:
    """The window that is (or was last) being played."""

    sched: dict[str, Any]  # a copy: the schedule may be edited or deleted while this runs
    window: sm.Window
    state: str = RUNNING
    started: bool = False  # the player was told to play
    prev_speakers: list[str] | None = None
    set_speakers: list[str] | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.sched["id"], self.window.start.isoformat()


class Scheduler:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, SCHEDULE_STORE_KEY)
        self.playlists: list[dict[str, Any]] = []
        self.schedules: list[dict[str, Any]] = []
        self.active_playlist: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._timer: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self._active: _Run | None = None
        self._closed: dict[tuple[str, str], datetime] = {}  # windows stopped by a person / finished: key -> their end
        self._skip: dict[str, date] = {}  # schedule id -> the local day whose window is skipped
        self._last: datetime = dt_util.utcnow()  # when we last looked, to catch one-off starts
        self._next: dict[str, Any] | None = None
        self._player: Any = None
        self._player_unsub: Callable[[], None] | None = None
        self._prev_state: str | None = None
        self._add_entities: Callable[..., None] | None = None
        self._switches: dict[str, Any] = {}
        self._started = False

    # --- storage -------------------------------------------------------------------
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.playlists = []
        for p in data.get("playlists", []):
            if isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("name"), str):
                ids = [i for i in p.get("show_ids", []) if isinstance(i, str)]
                self.playlists.append({"id": p["id"], "name": p["name"], "show_ids": list(dict.fromkeys(ids))})
        self.schedules = []
        for s in data.get("schedules", []):
            try:
                if not isinstance(s, dict) or not isinstance(s.get("id"), str):
                    raise vol.Invalid("no id")
                self.schedules.append({**validate_schedule(s), "id": s["id"]})
            except vol.Invalid as err:
                _LOGGER.warning("Dropping a saved schedule that is no longer valid: %s", err)
        active = data.get("active_playlist")
        self.active_playlist = active if isinstance(active, str) and self.get_playlist(active) else None

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"playlists": self.playlists, "schedules": self.schedules, "active_playlist": self.active_playlist},
            SAVE_DELAY)

    async def async_flush(self) -> None:
        await self._store.async_save(
            {"playlists": self.playlists, "schedules": self.schedules, "active_playlist": self.active_playlist})

    # --- listeners -----------------------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001  one bad listener must not stop the rest
                _LOGGER.exception("Scheduler listener failed")

    # --- playlists -----------------------------------------------------------------
    def get_playlist(self, playlist_id: str | None) -> dict[str, Any] | None:
        return next((p for p in self.playlists if p["id"] == playlist_id), None) if playlist_id else None

    @callback
    def _playlists_changed(self) -> None:
        self._save()
        if (player := self.hass.data.get(SHOW_PLAYER_KEY)) is not None:
            player.playlists_changed()
        self._notify()

    @callback
    def save_playlist(self, playlist_id: str | None, name: str, show_ids: list[str]) -> dict[str, Any]:
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise ValueError("A playlist needs a name of 1 to 60 characters")
        ids = list(dict.fromkeys(show_ids))
        if playlist_id:
            playlist = self.get_playlist(playlist_id)
            if playlist is None:
                raise KeyError(f"No playlist {playlist_id}")
            playlist.update(name=name, show_ids=ids)
        else:
            playlist = {"id": secrets.token_hex(4), "name": name, "show_ids": ids}
            self.playlists.append(playlist)
        self._playlists_changed()
        return playlist

    @callback
    def delete_playlist(self, playlist_id: str) -> None:
        playlist = self.get_playlist(playlist_id)
        if playlist is None:
            raise KeyError(f"No playlist {playlist_id}")
        if used := [s["name"] for s in self.schedules if s["playlist_id"] == playlist_id]:
            raise ValueError(f"{playlist['name']} is used by {', '.join(used)}. Change those schedules first")
        self.playlists.remove(playlist)
        if self.active_playlist == playlist_id:
            self.active_playlist = None
        self._playlists_changed()

    @callback
    def remove_show(self, show_id: str) -> None:
        """A show was deleted from the library: take it out of every playlist."""
        changed = False
        for p in self.playlists:
            if show_id in p["show_ids"]:
                p["show_ids"] = [i for i in p["show_ids"] if i != show_id]
                changed = True
        if changed:
            self._playlists_changed()

    @callback
    def set_active_playlist(self, playlist_id: str | None) -> None:
        if playlist_id != self.active_playlist:
            self.active_playlist = playlist_id
            self._save()

    # --- schedules -----------------------------------------------------------------
    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        return next((s for s in self.schedules if s["id"] == schedule_id), None)

    async def async_save_schedule(self, schedule_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
        sched = {**validate_schedule(data)}
        if self.get_playlist(sched["playlist_id"]) is None:
            raise ValueError("Pick a playlist that exists")
        async with self._lock:
            if schedule_id:
                old = self.get_schedule(schedule_id)
                if old is None:
                    raise KeyError(f"No schedule {schedule_id}")
                sched["id"] = schedule_id
                self.schedules[self.schedules.index(old)] = sched
                await self._drop_run(schedule_id, "edited", at_end=False)
            else:
                sched["id"] = secrets.token_hex(4)
                self.schedules.append(sched)
            self._save()
            self._sync_switches()
        await self._process()
        return sched

    async def async_delete_schedule(self, schedule_id: str) -> None:
        async with self._lock:
            sched = self.get_schedule(schedule_id)
            if sched is None:
                raise KeyError(f"No schedule {schedule_id}")
            await self._drop_run(schedule_id, "deleted", at_end=True)
            self.schedules.remove(sched)
            self._skip.pop(schedule_id, None)
            self._save()
            self._sync_switches()
        await self._process()

    async def async_set_enabled(self, schedule_id: str, enabled: bool) -> None:
        async with self._lock:
            sched = self.get_schedule(schedule_id)
            if sched is None:
                raise KeyError(f"No schedule {schedule_id}")
            if sched["enabled"] == enabled:
                return
            sched["enabled"] = enabled
            if not enabled:
                await self._drop_run(schedule_id, "disabled", at_end=True)
            self._save()
        await self._process()

    async def async_skip(self, schedule_id: str, skip: bool = True) -> None:
        """Skip (or un-skip) the schedule's window for today; one that is running now is ended."""
        async with self._lock:
            sched = self.get_schedule(schedule_id)
            if sched is None:
                raise KeyError(f"No schedule {schedule_id}")
            if not skip:
                self._skip.pop(schedule_id, None)
            else:
                run = self._active
                if run is not None and run.sched["id"] == schedule_id:
                    self._skip[schedule_id] = run.window.day
                    await self._drop_run(schedule_id, "skipped", at_end=True)
                else:
                    self._skip[schedule_id] = dt_util.now().date()
        await self._process()

    async def async_resume(self) -> bool:
        """Start a window that a person stopped, from where the clock is now."""
        async with self._lock:
            run = self._active
            if run is None or run.state != PAUSED:
                return False
            self._closed.pop(run.key, None)
            run.state = RUNNING
            run.started = False
            await self._begin(run)
        self._rearm()
        self._notify()
        return True

    # --- windows -------------------------------------------------------------------
    def _sun(self, day: date, event: str) -> datetime | None:
        return get_astral_event_date(self.hass, event, day)

    @property
    def _tz(self):
        return dt_util.get_default_time_zone()

    def _enabled(self) -> list[dict[str, Any]]:
        return [s for s in self.schedules if s["enabled"]]

    def window_info(self, sched: dict[str, Any], now: datetime) -> dict[str, Any]:
        """next_start / next_end for the list (the running window counts as the next one's predecessor)."""
        skip = self._skip.get(sched["id"])
        cur = sm.current_window(sched, now, self._tz, self._sun, skip)
        nxt = sm.next_window(sched, now, self._tz, self._sun, skip)
        return {
            "next_start": nxt.start.isoformat() if nxt else None,
            "next_end": nxt.end.isoformat() if nxt and nxt.end else None,
            "current_end": cur.end.isoformat() if cur and cur.end else None,
        }

    def conflicts_for(self, sched: dict[str, Any], now: datetime) -> list[dict[str, str]]:
        """Other enabled schedules whose windows overlap this one's in the next two months."""
        if not sched["enabled"]:
            return []
        return [{"id": o["id"], "name": o["name"]} for o in self._enabled()
                if o["id"] != sched["id"] and sm.conflicts(sched, o, now, self._tz, self._sun)]

    def schedule_state(self, schedule_id: str) -> str:
        run = self._active
        if run is not None and run.sched["id"] == schedule_id:
            return run.state if run.state != DONE else "done"
        return "idle"

    def status(self) -> str:
        if not self._enabled():
            return STATUS_OFF
        run = self._active
        if run is None or run.state == DONE:
            return STATUS_IDLE
        return STATUS_RUNNING if run.state == RUNNING else STATUS_PAUSED

    @property
    def next_info(self) -> dict[str, Any] | None:
        return self._next

    def active_info(self) -> dict[str, Any] | None:
        run = self._active
        if run is None or run.state == DONE:
            return None
        return {"schedule_id": run.sched["id"], "schedule": run.sched["name"], "state": run.state,
                "playlist_id": run.sched["playlist_id"], "start": run.window.start.isoformat(),
                "end": run.window.end.isoformat() if run.window.end else None}

    def _compute_next(self, now: datetime) -> dict[str, Any] | None:
        best: tuple[datetime, dict[str, Any], sm.Window] | None = None
        for s in self._enabled():
            w = sm.next_window(s, now, self._tz, self._sun, self._skip.get(s["id"]))
            if w is not None and (best is None or w.start < best[0]):
                best = (w.start, s, w)
        if best is None:
            return None
        _, s, w = best
        playlist = self.get_playlist(s["playlist_id"])
        return {"schedule_id": s["id"], "schedule": s["name"], "playlist_id": s["playlist_id"],
                "playlist": playlist["name"] if playlist else None, "start": w.start,
                "end": w.end}

    # --- timer ---------------------------------------------------------------------
    @callback
    def _rearm(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
        if not self._started:
            return
        now = dt_util.utcnow()
        today = dt_util.now().date()
        self._skip = {k: v for k, v in self._skip.items() if v >= today - timedelta(days=1)}
        self._closed = {k: e for k, e in self._closed.items() if e > now}
        self._next = self._compute_next(now)
        wake = now + MAX_REARM
        if self._active is not None and self._active.window.end and self._active.window.end > now:
            wake = min(wake, self._active.window.end)
        if self._next is not None:
            wake = min(wake, self._next["start"])
        self._timer = async_track_point_in_time(self.hass, self._on_timer, max(wake, now + timedelta(seconds=1)))

    async def _on_timer(self, _now: datetime) -> None:
        self._timer = None
        await self._process()

    async def async_start(self, *_: Any) -> None:
        """Home Assistant is up: look at what should be running right now."""
        self._started = True
        self._last = dt_util.utcnow()
        self._sync_switches()
        await self._process()

    @callback
    def async_stop_timers(self) -> None:
        self._started = False
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def async_player_ready(self) -> None:
        """The show player was (re)created: a window may be open that it can play now."""
        if self._started:
            self.hass.async_create_task(self._process(), "light_show_studio schedule check")

    # --- the player ------------------------------------------------------------------
    def _get_player(self):
        player = self.hass.data.get(SHOW_PLAYER_KEY)
        if player is not self._player:
            if self._player_unsub:
                self._player_unsub()
                self._player_unsub = None
            self._player = player
            self._prev_state = player.state if player else None
            if player is not None:
                self._player_unsub = player.add_listener(self._on_player)
        return player

    @callback
    def _on_player(self) -> None:
        """Tell a person pressing stop from the scheduler stopping and from a queue running out."""
        player = self._player
        if player is None:
            return
        prev, self._prev_state = self._prev_state, player.state
        run = self._active
        if run is None or run.state != RUNNING or not run.started:
            return
        if prev is None or prev == IDLE or player.state != IDLE:
            return
        reason = player.stop_reason
        if reason not in ("user", "ended"):
            return
        if run.window.end is None:  # a play-once show is over (or stopped): nothing more to wait for
            run.state = DONE if reason == "ended" else PAUSED
            self.hass.async_create_task(self._finish_once(run, reason == "ended"), "light_show_studio schedule end")
        elif reason == "user":
            run.state = PAUSED  # a person stopped it: leave it be until this window is over
            self._closed[run.key] = run.window.end
            self._rearm()
            self._notify()
        else:
            run.state = DONE
            self._closed[run.key] = run.window.end
            self._notify()

    async def _finish_once(self, run: _Run, at_end: bool) -> None:
        async with self._lock:
            if self._active is run:
                await self._end_run(run, "finished" if at_end else "stopped", at_end=at_end)
        await self._process()  # a window that is still open takes over again

    # --- deciding what runs ----------------------------------------------------------
    async def _process(self) -> None:
        async with self._lock:
            now = dt_util.utcnow()
            if self._started:
                try:
                    await self._evaluate(now)
                except Exception:  # noqa: BLE001  never leave the scheduler without a timer
                    _LOGGER.exception("Light show scheduler failed")
                self._last = now
        self._rearm()
        self._notify()

    async def _evaluate(self, now: datetime) -> None:
        self._get_player()
        tz = self._tz
        run = self._active
        if run is not None and run.window.end is not None and now >= run.window.end:
            await self._end_run(run, "window_end", at_end=True)
        once: list[tuple[sm.Window, dict[str, Any]]] = []
        opened: list[tuple[sm.Window, dict[str, Any]]] = []
        for s in self._enabled():
            skip = self._skip.get(s["id"])
            if s.get("end"):
                if (w := sm.current_window(s, now, tz, self._sun, skip)) is not None:
                    opened.append((w, s))
            else:
                for w in sm.windows_between(s, self._last + timedelta(microseconds=1),
                                            now + timedelta(microseconds=1), tz, self._sun):
                    if w.day != skip:
                        once.append((w, s))
        if once:  # a one-off start is a new start: it takes over, like any later-starting window
            w, s = max(once, key=lambda x: x[0].start)
        elif opened:
            w, s = max(opened, key=lambda x: x[0].start)  # the later-starting window wins an overlap
        else:
            return
        key = (s["id"], w.start.isoformat())
        run = self._active
        if run is not None and run.key == key:
            return  # already on it (or a person stopped it / it finished: left alone)
        if key in self._closed:
            return
        if run is not None:
            await self._end_run(run, "preempted", at_end=False)
        new = _Run(sched={**s}, window=w)
        self._active = new
        await self._begin(new)

    async def _begin(self, run: _Run) -> None:
        s = run.sched
        playlist = self.get_playlist(s["playlist_id"])
        self.hass.bus.async_fire(EVENT_SCHEDULE_STARTED, {
            "schedule_id": s["id"], "name": s["name"], "playlist_id": s["playlist_id"],
            "playlist": playlist["name"] if playlist else None,
            "start": run.window.start.isoformat(), "end": run.window.end.isoformat() if run.window.end else None})
        _LOGGER.info("Schedule %s started (playlist %s)", s["name"], playlist["name"] if playlist else s["playlist_id"])
        player = self._get_player()
        if player is None:
            _LOGGER.warning("Schedule %s: the Light Show Player is not set up", s["name"])
            run.state = DONE
            return
        try:
            if player.state != IDLE:
                await player.async_stop(reason="scheduler")
            await player.async_set_playlist(s["playlist_id"], remember=False)
            speakers = s.get("speakers") or []
            if speakers:
                run.prev_speakers = player.speakers
                run.set_speakers = list(speakers)
                player.set_speakers(speakers)
            if s.get("volume") is not None:
                for speaker in speakers or player.speakers:
                    try:
                        await self.hass.services.async_call(
                            "media_player", "volume_set", {"entity_id": speaker, "volume_level": s["volume"] / 100},
                            blocking=True)
                    except HomeAssistantError as err:
                        _LOGGER.warning("Schedule %s: couldn't set the volume on %s: %s", s["name"], speaker, err)
            await player.async_set_shuffle(bool(s.get("shuffle")))
            await player.async_set_repeat("all" if s.get("repeat") else "off")
            await player.async_play()
            run.started = True
            self._prev_state = player.state
        except HomeAssistantError as err:
            _LOGGER.warning("Schedule %s couldn't start: %s", s["name"], err)
            run.state = DONE

    async def _end_run(self, run: _Run, reason: str, at_end: bool) -> None:
        s = run.sched
        player = self._get_player()
        if player is not None:
            if run.state == RUNNING and player.state != IDLE:
                await player.async_stop(reason="scheduler")
            if run.set_speakers is not None and run.prev_speakers is not None and player.speakers == run.set_speakers:
                player.set_speakers(run.prev_speakers)  # give the output back, unless a person changed it
        if at_end and run.state != PAUSED:  # a person who stopped the show keeps the lights as they left them
            await self._at_end(s)
        if self._active is run:
            self._active = None
        _LOGGER.info("Schedule %s ended (%s)", s["name"], reason)
        self.hass.bus.async_fire(EVENT_SCHEDULE_ENDED, {
            "schedule_id": s["id"], "name": s["name"], "reason": reason})

    async def _drop_run(self, schedule_id: str, reason: str, at_end: bool) -> None:
        run = self._active
        if run is not None and run.sched["id"] == schedule_id:
            self._closed.pop(run.key, None)
            await self._end_run(run, reason, at_end)

    def string_lights(self) -> list[str]:
        """The integration's whole-strand light entities (not the per-bulb ones)."""
        registry = er.async_get(self.hass)
        return [e.entity_id for e in registry.entities.values()
                if e.platform == DOMAIN and e.domain == "light" and e.unique_id.endswith("_string")
                and e.disabled_by is None]

    async def _at_end(self, s: dict[str, Any]) -> None:
        action = s.get("at_end", "off")
        lights = self.string_lights()
        if action == "leave" or not lights:
            return
        try:
            if action == "effect":
                await self.hass.services.async_call(
                    "light", "turn_on", {"entity_id": lights, "effect": s["end_effect"]}, blocking=True)
            else:
                await self.hass.services.async_call("light", "turn_off", {"entity_id": lights}, blocking=True)
        except (HomeAssistantError, KeyError) as err:
            _LOGGER.warning("Schedule %s: couldn't change the lights at the end: %s", s["name"], err)

    # --- entities ----------------------------------------------------------------------
    @callback
    def attach_platform(self, async_add_entities: Callable[..., None]) -> None:
        self._add_entities = async_add_entities
        self._switches.clear()
        self._sync_switches()

    @callback
    def detach_platform(self) -> None:
        self._add_entities = None
        self._switches.clear()

    @callback
    def _sync_switches(self) -> None:
        if self._add_entities is None:
            return
        from .switch import ScheduleSwitch  # here: the platform module needs Home Assistant's switch component

        known = {s["id"] for s in self.schedules}
        registry = er.async_get(self.hass)
        for sid in [i for i in self._switches if i not in known]:
            entity = self._switches.pop(sid)
            if entity.entity_id and registry.async_get(entity.entity_id):
                registry.async_remove(entity.entity_id)
        new = [ScheduleSwitch(self, s["id"]) for s in self.schedules if s["id"] not in self._switches]
        for entity in new:
            self._switches[entity.schedule_id] = entity
        if new:
            self._add_entities(new)


async def async_setup_scheduler(hass: HomeAssistant) -> Scheduler:
    from homeassistant.helpers.start import async_at_started

    scheduler = Scheduler(hass)
    await scheduler.async_load()
    hass.data[SCHEDULER_KEY] = scheduler
    async_at_started(hass, scheduler.async_start)
    return scheduler
