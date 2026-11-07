"""Light schedules: lights or areas that come on with a pattern at a time / sunset / sunrise and go off later.

Unlike the show scheduler (scheduler.py, one playlist at a time) any number of light schedules can be active at once,
each on its own lights and areas. One Store ("light_show_studio.light_schedules") holds the schedules and a little
bookkeeping about windows (stopped early / already applied) so a restart converges.

Like the show scheduler there is a single timer at the next change, and every time it fires the wanted state is
worked out again from the clock, so restarts, edits and missed timers all end up in the same place.

Ownership: when a window opens the schedule applies its look to each target and remembers the light's state right
afterwards. When the window closes it turns a light off only if that state is unchanged, so a light a person touched
in the meantime is left alone. Lights with a show playing are never touched.
"""
from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import patterns
from . import schedule_math as sm
from .const import (
    EVENT_LIGHT_SCHEDULE_ENDED,
    EVENT_LIGHT_SCHEDULE_STARTED,
    LIGHT_SCHEDULE_STORE_KEY,
    LIGHT_SCHEDULER_KEY,
    MAP_KEY,
)
from .patterns import PATTERN
from .scheduler import MAX_ID, MAX_NAME, MAX_SCHEDULES, _days, _edge, _mmdd

_LOGGER = logging.getLogger(__name__)

AD_HOC = "area:"  # the id prefix of the zone mapmanager makes for "this area" (mapmanager.AD_HOC)
SPECIAL_AREAS = ("all", "outdoor", "indoor")
MAX_REARM = timedelta(minutes=30)  # wake at least this often (the sun and the map move under us)
RETRY = timedelta(seconds=60)  # look again soon when a target light wasn't ready or had a show playing
STARTUP_DELAY = 8.0  # seconds after Home Assistant starts, so the lights are set up before a window is applied
CATCHUP = timedelta(minutes=10)  # a "turn on and leave" start this recent is still applied after a restart
KEEP = timedelta(hours=2)  # how long bookkeeping about a finished window is kept
SHOW_FRESH = 2.0  # seconds: frames this recent mean a show is playing on the light
SAVE_DELAY = 1.0
MAX_TARGETS = 64
MAX_FADE = 1800  # seconds: the longest fade in / out a schedule can ask for
DEFAULT_FADE = 2


# --- validation ---------------------------------------------------------------------
def _ref(value: Any) -> str:
    if not isinstance(value, str):
        raise vol.Invalid("expected text")
    value = value.strip()
    if not 0 < len(value) <= MAX_ID or "\0" in value:
        raise vol.Invalid(f"expected 1 to {MAX_ID} characters")
    return value.casefold() if value.casefold() in SPECIAL_AREAS else value


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise vol.Invalid("expected text")
    value = value.strip()
    if not 0 < len(value) <= MAX_NAME:
        raise vol.Invalid(f"expected 1 to {MAX_NAME} characters")
    return value


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise vol.Invalid("expected true or false")
    return value


def _unique(value: list[str]) -> list[str]:
    return list(dict.fromkeys(value))


_DURATION = vol.Schema({
    vol.Required("type"): "duration",
    vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
})
_TARGETS = vol.Schema({
    vol.Optional("areas", default=list): vol.All([_ref], vol.Length(max=MAX_TARGETS), _unique),
    vol.Optional("lights", default=list): vol.All([_ref], vol.Length(max=MAX_TARGETS), _unique),
})
def _pattern(value: Any) -> str:
    """A built-in pattern or one of the person's own (by name or id); stored by name."""
    try:
        return patterns.valid_pattern(value)
    except ValueError as err:
        raise vol.Invalid("unknown pattern") from err


def _seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 <= value <= MAX_FADE:
        raise vol.Invalid(f"expected 0 to {MAX_FADE} seconds")
    return value


_LOOK = vol.Schema({
    vol.Required("pattern"): _pattern,
    vol.Required("effect"): vol.In(patterns.ALL_EFFECTS),  # a map effect (Sweep, Ripple ...) needs an area target
    vol.Optional("speed", default=patterns.DEFAULT_SPEED): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional("brightness", default=100): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
})


def _end(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict) and value.get("type") == "duration":
        return _DURATION(value)
    return _edge(value)


def _targets(value: Any) -> dict[str, Any]:
    value = _TARGETS(value)
    if not value["areas"] and not value["lights"]:
        raise vol.Invalid("pick at least one area or light")
    return value


LIGHT_SCHEDULE_FIELDS: dict[Any, Any] = {
    vol.Required("name"): _name,
    vol.Optional("enabled", default=True): _bool,
    vol.Required("days"): vol.All([vol.All(vol.Coerce(int), vol.Range(min=0, max=6))], vol.Length(min=1, max=14), _days),
    vol.Required("start"): _edge,
    vol.Optional("end", default=None): _end,
    vol.Optional("date_from", default=None): vol.Any(None, _mmdd),
    vol.Optional("date_to", default=None): vol.Any(None, _mmdd),
    vol.Required("targets"): _targets,
    vol.Required("look"): _LOOK,
    vol.Optional("fade_in_s", default=DEFAULT_FADE): _seconds,  # the lights fade up over this long when a window opens
    vol.Optional("fade_out_s", default=DEFAULT_FADE): _seconds,  # ... and down when it closes (a brightness ramp)
}
_SCHEMA = vol.Schema(LIGHT_SCHEDULE_FIELDS)  # strict: unknown keys are an error


def validate_light_schedule(data: dict[str, Any]) -> dict[str, Any]:
    """A valid schedule (without its id) from what a person sent; vol.Invalid says what is wrong."""
    out = _SCHEMA(data)
    if bool(out.get("date_from")) != bool(out.get("date_to")):
        raise vol.Invalid("set both season dates, or neither")
    return out


def _is_duration(s: dict[str, Any]) -> bool:
    return bool(s.get("end")) and s["end"]["type"] == "duration"


def _math(s: dict[str, Any]) -> dict[str, Any]:
    """The schedule as schedule_math sees it: a duration end is worked out from the start instead."""
    return {**s, "end": None} if _is_duration(s) or not s.get("end") else s


@dataclass
class _Owned:
    """What a schedule set on one unit (a light or an area zone), to tell at the end if anyone changed it."""

    sid: str
    key: str
    applied_at: datetime
    print: Any = None  # the light's state right after the look was applied
    runner: Any = None  # an area: the zone runner started for it
    fade_out: float = 0.0  # seconds to fade out over when the window closes


@dataclass
class _Want:
    sid: str
    key: str
    start: datetime
    look: dict[str, Any]
    fade_in: float = 0.0
    fade_out: float = 0.0


@dataclass
class _Plan:
    """Where a schedule's look goes right now."""

    lights: list[str] = field(default_factory=list)  # entry ids, driven directly
    zones: list[str] = field(default_factory=list)  # area ids, driven as an area zone (lights only partly in them)


class LightScheduler:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, LIGHT_SCHEDULE_STORE_KEY)
        self.schedules: list[dict[str, Any]] = []
        self._windows: dict[str, dict[str, Any]] = {}  # window key -> {"end": iso, "stopped": bool, "units": [..]}
        self._manual: dict[str, dict[str, str]] = {}  # schedule id -> {"start": iso, "end": iso} (run now)
        self._owned: dict[str, _Owned] = {}  # unit ("light:<entry id>" / "area:<area id>") -> what we set
        self._active: dict[str, str] = {}  # schedule id -> key of its open window (in memory)
        self._reclaimed: set[tuple[str, str]] = set()
        self._listeners: list[Callable[[], None]] = []
        self._timer: CALLBACK_TYPE | None = None
        self._startup: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._retry = False
        self._last: datetime = dt_util.utcnow()

    # --- storage -------------------------------------------------------------------
    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001  an unreadable store must not stop the integration
            _LOGGER.exception("Could not read the saved light schedules; starting empty")
            data = None
        if not isinstance(data, dict):
            data = {}
        self.schedules = []
        seen: set[str] = set()
        raw = data.get("schedules")
        for s in raw[:MAX_SCHEDULES] if isinstance(raw, list) else []:
            try:
                if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not 0 < len(s["id"]) <= MAX_ID:
                    raise vol.Invalid("no id")
                if s["id"] in seen:
                    raise vol.Invalid("duplicate id")
                seen.add(s["id"])
                self.schedules.append({**validate_light_schedule({k: v for k, v in s.items() if k != "id"}), "id": s["id"]})
            except (vol.Invalid, ValueError, TypeError, KeyError) as err:
                _LOGGER.warning("Dropping a saved light schedule that is no longer valid: %s", err)
        self._windows = {}
        windows = data.get("windows")
        for key, w in windows.items() if isinstance(windows, dict) else []:
            if isinstance(key, str) and isinstance(w, dict) and isinstance(w.get("end"), str):
                units = w.get("units")
                self._windows[key] = {"end": w["end"], "stopped": w.get("stopped") is True,
                                      "announced": w.get("announced") is True,
                                      "units": [u for u in units if isinstance(u, str)] if isinstance(units, list) else []}
        self._manual = {}
        manual = data.get("manual")
        for sid, m in manual.items() if isinstance(manual, dict) else []:
            if sid in seen and isinstance(m, dict) and _parse(m.get("start")) and _parse(m.get("end")):
                self._manual[sid] = {"start": m["start"], "end": m["end"]}

    def _data(self) -> dict[str, Any]:
        return {"schedules": self.schedules, "windows": self._windows, "manual": self._manual}

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

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
                _LOGGER.exception("Light scheduler listener failed")

    # --- the schedules ----------------------------------------------------------------
    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        return next((s for s in self.schedules if s["id"] == schedule_id), None)

    def _check_targets(self, targets: dict[str, Any]) -> None:
        mgr = self.hass.data.get(MAP_KEY)
        if mgr is None:
            return
        known = {row["entry_id"] for row in mgr.lights()}
        if bad := [e for e in targets["lights"] if e not in known]:
            raise ValueError(f"No light {bad[0]}")
        model = mgr.model
        if bad := [a for a in targets["areas"] if a not in SPECIAL_AREAS and model.area(a) is None]:
            raise ValueError(f"No area {bad[0]}")

    async def async_save_schedule(self, schedule_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
        sched = validate_light_schedule(data)
        self._check_targets(sched["targets"])
        async with self._lock:
            if schedule_id:
                old = self.get_schedule(schedule_id)
                if old is None:
                    raise KeyError(f"No light schedule {schedule_id}")
                sched["id"] = schedule_id
                self.schedules[self.schedules.index(old)] = sched
                same = {k: v for k, v in old.items() if k not in ("name", "id")} == \
                       {k: v for k, v in sched.items() if k not in ("name", "id")}
                if not same:  # the old settings stop; the window, if open, starts again with the new ones
                    await self._release_schedule(schedule_id, "edited")
            else:
                if len(self.schedules) >= MAX_SCHEDULES:
                    raise ValueError(f"At most {MAX_SCHEDULES} light schedules")
                sched["id"] = secrets.token_hex(4)
                self.schedules.append(sched)
            self._save()
        await self._process()
        return sched

    async def async_delete_schedule(self, schedule_id: str) -> None:
        async with self._lock:
            sched = self.get_schedule(schedule_id)
            if sched is None:
                raise KeyError(f"No light schedule {schedule_id}")
            self.schedules.remove(sched)
            self._manual.pop(schedule_id, None)
            await self._release_schedule(schedule_id, "deleted")
            self._save()
        await self._process()

    async def async_set_enabled(self, schedule_id: str, enabled: bool) -> None:
        async with self._lock:
            sched = self.get_schedule(schedule_id)
            if sched is None:
                raise KeyError(f"No light schedule {schedule_id}")
            if sched["enabled"] != enabled:
                sched["enabled"] = enabled
                if not enabled:
                    self._manual.pop(schedule_id, None)
                    await self._release_schedule(schedule_id, "disabled")
                self._save()
        await self._process()

    async def async_run_now(self, schedule_id: str) -> None:
        """Open the schedule's window now: until its normal end (the next one), for its duration, or for good
        when it has no end (the look is applied and the lights are left on)."""
        async with self._lock:
            sched = self.get_schedule(schedule_id)
            if sched is None:
                raise KeyError(f"No light schedule {schedule_id}")
            now = dt_util.utcnow()
            end = sched.get("end")
            if end is None:
                key = f"{schedule_id}|now|{now.isoformat()}"
                await self._release_schedule(schedule_id, "restarted")
                self._windows[key] = {"end": (now + KEEP).isoformat(), "stopped": False, "units": []}
                await self._apply_once(sched, key, now)
                self._save()
                self._notify()
                return
            if end["type"] == "duration":
                stop = now + timedelta(minutes=end["minutes"])
            else:
                stop = next((e for d in (0, 1) if (e := sm.resolve_edge(
                    end, dt_util.now().date() + timedelta(days=d), self._tz, self._sun)) and e > now), None)
                if stop is None:
                    raise ValueError("Could not work out when this schedule ends")
            await self._release_schedule(schedule_id, "restarted")
            self._manual[schedule_id] = {"start": now.isoformat(), "end": stop.isoformat()}
            self._save()
        await self._process()

    async def async_stop(self, schedule_id: str) -> bool:
        """End the schedule's open window early (what it turned on goes off). False when none is open."""
        async with self._lock:
            if self.get_schedule(schedule_id) is None:
                raise KeyError(f"No light schedule {schedule_id}")
            now = dt_util.utcnow()
            stopped = False
            for w, manual in self._windows_now(self.get_schedule(schedule_id), now):
                win = self._window(self._key(schedule_id, w, manual), w)
                stopped = stopped or not win["stopped"]
                win["stopped"] = True
            self._manual.pop(schedule_id, None)
            self._save()
        await self._process()
        return stopped

    # --- windows -------------------------------------------------------------------
    def _sun(self, day, event: str) -> datetime | None:
        return get_astral_event_date(self.hass, event, day)

    @property
    def _tz(self):
        return dt_util.get_default_time_zone()

    @staticmethod
    def _key(sid: str, w: sm.Window, manual: bool = False) -> str:
        return f"{sid}|{'manual|' if manual else ''}{w.start.isoformat()}"

    def _window(self, key: str, w: sm.Window) -> dict[str, Any]:
        return self._windows.setdefault(key, {"end": (w.end or w.start + KEEP).isoformat(), "stopped": False, "units": []})

    def _natural(self, s: dict[str, Any], frm: datetime, to: datetime) -> list[sm.Window]:
        """The schedule's own windows overlapping [frm, to) (a window with no end counts by its start)."""
        if _is_duration(s):
            span = timedelta(minutes=s["end"]["minutes"])
            return [sm.Window(w.start, w.start + span, w.day)
                    for w in sm.windows_between(_math(s), frm - span, to, self._tz, self._sun)
                    if w.start + span > frm]
        return sm.windows_between(s, frm, to, self._tz, self._sun)

    def _windows_now(self, s: dict[str, Any], now: datetime) -> list[tuple[sm.Window, bool]]:
        """The windows with an end open at `now`: [(window, is a run-now window)], latest start last."""
        out = [(w, False) for w in self._natural(s, now, now + timedelta(seconds=1))
               if w.end is not None and w.start <= now < w.end] if s["enabled"] else []
        m = self._manual.get(s["id"])
        if m and (start := _parse(m["start"])) and (end := _parse(m["end"])) and start <= now < end:
            out.append((sm.Window(start, end, start.astimezone(self._tz).date()), True))
        return sorted(out, key=lambda x: x[0].start)

    def _current(self, s: dict[str, Any], now: datetime) -> tuple[sm.Window, bool] | None:
        for w, manual in reversed(self._windows_now(s, now)):
            if not self._windows.get(self._key(s["id"], w, manual), {}).get("stopped"):
                return w, manual
        return None

    def _next(self, s: dict[str, Any], after: datetime) -> sm.Window | None:
        w = sm.next_window(_math(s), after, self._tz, self._sun)
        if w is not None and _is_duration(s):
            w = sm.Window(w.start, w.start + timedelta(minutes=s["end"]["minutes"]), w.day)
        return w

    def view(self, s: dict[str, Any], now: datetime) -> dict[str, Any]:
        cur = self._current(s, now) if s["enabled"] or s["id"] in self._manual else None
        nxt = self._next(s, now) if s["enabled"] else None
        off = cur[0].end if cur else (nxt.end if nxt else None)
        return {**s, "active": s["id"] in self._active, "next_on": nxt.start.isoformat() if nxt else None,
                "next_off": off.isoformat() if off else None}

    def views(self) -> list[dict[str, Any]]:
        now = dt_util.utcnow()
        return [self.view(s, now) for s in self.schedules]

    # --- targets -------------------------------------------------------------------
    def _plan(self, s: dict[str, Any], model: Any, rows: dict[str, dict[str, Any]]) -> _Plan:
        """Turn a schedule's targets into lights to drive directly and areas to drive as zones."""
        refs = s["targets"]["areas"]
        areas = set()
        for ref in refs:
            if ref == "all":
                areas |= {a.id for a in model.areas}
            elif ref == "outdoor":
                areas |= {a.id for a in model.areas if a.outdoor}
            elif ref == "indoor":
                areas |= {a.id for a in model.areas if not a.outdoor}
            elif (a := model.area(ref)) is not None:
                areas.add(a.id)
        by_ha = {a.ha_area_id: a.id for a in model.areas if a.ha_area_id}
        runs_of: dict[str, set[str]] = {}
        for r in model.runs:
            runs_of.setdefault(r.entry_id, set()).add(r.area)
        lights = list(dict.fromkeys(s["targets"]["lights"]))
        zones: list[str] = []
        loose = bool({"all", "indoor"} & set(refs))  # lights that sit in no area of the map
        for entry_id, row in rows.items():
            if entry_id in lights:
                continue
            mine = runs_of.get(entry_id)
            if mine:
                if mine <= areas:
                    lights.append(entry_id)
                elif mine & areas:
                    zones.extend(a for a in sorted(mine & areas) if a not in zones)
                continue
            home = (row.get("area") or {})
            home_id = home.get("id") if home.get("id") in {a.id for a in model.areas} else by_ha.get(home.get("ha_area_id"))
            if home_id is not None:
                if home_id in areas:
                    lights.append(entry_id)
            elif loose:
                lights.append(entry_id)
        return _Plan(lights, zones)

    # --- applying and releasing ------------------------------------------------------
    @staticmethod
    def _print(ctl: Any) -> tuple:
        return (ctl.is_on, ctl.effect, ctl.pattern, ctl.pattern_effect, ctl.speed, ctl.brightness, ctl.show_mode,
                ctl.zone)

    @staticmethod
    def _in_show(ctl: Any) -> bool:
        """An xLights / sequencer show is drawing on this light right now."""
        return bool(ctl.show_mode and ctl.is_on and time.monotonic() - getattr(ctl, "_ext_seen", 0.0) < SHOW_FRESH)

    @staticmethod
    def _level(look: dict[str, Any]) -> int:
        return max(1, round(look["brightness"] * 255 / 100))

    async def _apply_light(self, ctl: Any, look: dict[str, Any], fade_in: float = 0.0) -> tuple:
        # one call: a light that is off fades up from black over fade_in, one that is on cross-fades into the look
        await ctl.async_set_pattern(look["pattern"], look["effect"], look["speed"], brightness=self._level(look),
                                    transition=fade_in)
        return self._print(ctl)

    def _reclaimable(self, ctl: Any, look: dict[str, Any]) -> bool:
        """After a restart: is the light still showing this look (so it is ours to turn off)?"""
        return bool(ctl.is_on and ctl.effect == PATTERN and ctl.pattern == look["pattern"]
                    and ctl.pattern_effect == patterns.light_effect(look["effect"]) and ctl.speed == look["speed"]
                    and ctl.brightness == self._level(look) and not ctl.zone)

    def _untouched(self, unit: str, own: _Owned, mgr: Any) -> bool:
        if unit.startswith("area:"):
            runner = mgr.runners.get(AD_HOC + unit[5:]) if mgr else None
            return runner is not None and runner is own.runner and getattr(runner, "running", True)
        ctl = mgr.controller(unit[6:]) if mgr else None
        return ctl is not None and self._print(ctl) == own.print

    async def _release(self, unit: str, own: _Owned, mgr: Any, off: bool) -> None:
        """Let go of a unit; turn it off when it is still as we left it."""
        if not off or mgr is None:
            return
        try:
            if not self._untouched(unit, own, mgr):
                return
            if unit.startswith("area:"):
                await mgr.async_stop_area(unit[5:], own.fade_out)
            elif (ctl := mgr.controller(unit[6:])) is not None:
                await ctl.async_turn_off(own.fade_out)
        except Exception:  # noqa: BLE001  one light failing must not stop the others
            _LOGGER.exception("Could not turn off %s", unit)

    async def _release_schedule(self, sid: str, reason: str) -> None:
        """Stop everything a schedule has on (edited, disabled, deleted, restarted)."""
        mgr = self.hass.data.get(MAP_KEY)
        for unit, own in [(u, o) for u, o in self._owned.items() if o.sid == sid]:
            del self._owned[unit]
            await self._release(unit, own, mgr, True)
        for key in [k for k in self._windows if k.split("|", 1)[0] == sid and not self._windows[k]["stopped"]]:
            del self._windows[key]
        self._reclaimed = {x for x in self._reclaimed if not x[0].startswith(sid + "|")}
        if (key := self._active.pop(sid, None)) is not None:
            self._fire_ended(sid, key, reason)

    async def _apply_once(self, s: dict[str, Any], key: str, now: datetime) -> None:
        """A window with no end: apply the look to what is free and leave it on."""
        mgr = self.hass.data.get(MAP_KEY)
        if mgr is None:
            return
        plan = self._plan(s, mgr.model, {r["entry_id"]: r for r in mgr.lights()})
        done = self._windows.setdefault(key, {"end": (now + KEEP).isoformat(), "stopped": False, "units": []})
        for unit in [f"light:{e}" for e in plan.lights] + [f"area:{a}" for a in plan.zones]:
            self._owned.pop(unit, None)  # whoever had it no longer turns it off
            if await self._apply_unit(unit, s["look"], mgr, None, s["fade_in_s"]):
                done["units"].append(unit)
        self._fire_started(s, key, None)

    async def _apply_unit(self, unit: str, look: dict[str, Any], mgr: Any, own: _Owned | None,
                          fade_in: float = 0.0, fade_out: float = 0.0) -> bool:
        """Apply the look to a light or an area zone. False when it was skipped (not ready, or a show plays)."""
        try:
            if unit.startswith("light:"):
                ctl = mgr.controller(unit[6:])
                if ctl is None or self._in_show(ctl):
                    self._retry = True
                    return False
                print_ = await self._apply_light(ctl, look, fade_in)
                if own is not None:
                    own.print = print_
                return True
            area_id = unit[5:]
            ctls = [c for e in self._zone_lights(mgr, area_id) if (c := mgr.controller(e)) is not None]
            if not ctls or any(self._in_show(c) for c in ctls):
                self._retry = True
                return False
            for c in ctls:
                c.brightness = self._level(look)  # the zone shows at the light's brightness
            await mgr.async_start_area(area_id, look["pattern"], look["effect"], look["speed"],
                                       fade_in=fade_in, fade_out=fade_out)
            if own is not None:
                own.runner = mgr.runners.get(AD_HOC + area_id)
            return True
        except Exception:  # noqa: BLE001  a light that fails must not stop the others
            _LOGGER.exception("Could not apply a light schedule to %s", unit)
            self._retry = True
            return False

    @staticmethod
    def _zone_lights(mgr: Any, area_id: str) -> list[str]:
        return list(dict.fromkeys(r.entry_id for r in mgr.model.runs if r.area == area_id))

    async def async_pattern_changed(self, old: str, new: str | None) -> None:
        """One of the person's patterns was renamed or deleted: schedules that use it follow (the default pattern
        stands in for a deleted one). Editing its colours needs nothing: the lights read them each time."""
        if new == old:
            return
        async with self._lock:
            hit = [s for s in self.schedules if s["look"]["pattern"].casefold() == old.casefold()]
            for s in hit:
                s["look"] = {**s["look"], "pattern": new or patterns.DEFAULT_PATTERN}
            if hit:
                self._save()
        if hit:
            self._notify()

    # --- the heart: work out what should be on, and make it so ---------------------------------
    async def _process(self) -> None:
        async with self._lock:
            try:
                if self._started:
                    await self._evaluate(dt_util.utcnow())
            except Exception:  # noqa: BLE001  never leave the scheduler without a timer
                _LOGGER.exception("Light schedule check failed")
            self._rearm()
            self._notify()

    async def _evaluate(self, now: datetime) -> None:
        mgr = self.hass.data.get(MAP_KEY)
        self._retry = False
        for key in [k for k, w in self._windows.items() if (e := _parse(w["end"])) is None or e + KEEP < now]:
            del self._windows[key]
        # 1. windows open now, per schedule (a run-now window beats the schedule's own)
        active: dict[str, tuple[sm.Window, str]] = {}
        for s in self.schedules:
            if (cur := self._current(s, now)) is not None:
                active[s["id"]] = (cur[0], self._key(s["id"], cur[0], cur[1]))
        # 2. windows that ended or were stopped
        for sid in [i for i in self._active if i not in active or active[i][1] != self._active[i]]:
            key = self._active.pop(sid)
            self._fire_ended(sid, key, "stopped" if self._windows.get(key, {}).get("stopped") else "ended")
        for sid, (w, key) in active.items():
            self._window(key, w)
            if self._active.get(sid) != key:
                self._active[sid] = key
        # 3. who should hold each unit: the window that started latest
        wants: dict[str, _Want] = {}
        if mgr is not None and active:
            model, rows = mgr.model, {r["entry_id"]: r for r in mgr.lights()}
            for sid, (w, key) in sorted(active.items(), key=lambda kv: kv[1][0].start):
                s = self.get_schedule(sid)
                plan = self._plan(s, model, rows)
                for unit in [f"light:{e}" for e in plan.lights] + [f"area:{a}" for a in plan.zones]:
                    wants[unit] = _Want(sid, key, w.start, s["look"], s["fade_in_s"], s["fade_out_s"])
        # 4. let go of units nobody wants any more
        for unit, own in list(self._owned.items()):
            want = wants.get(unit)
            if want is not None:
                continue  # still wanted (by this window or another that takes over, below)
            del self._owned[unit]
            await self._release(unit, own, mgr, True)
        # 5. apply what is wanted
        for unit, want in wants.items():
            own = self._owned.get(unit)
            done = self._windows[want.key]["units"]
            if own is not None and own.key == want.key:
                continue
            if own is not None:  # a different window held it
                if self._untouched(unit, own, mgr):
                    if (old := self._windows.get(own.key)) is not None and unit in old["units"]:
                        old["units"].remove(unit)  # it is given up, so the old window can take it back later
                    if not await self._take(unit, want, mgr, now):
                        del self._owned[unit]
                    continue
                del self._owned[unit]  # a person changed it: only a newer window may take it
                if want.start <= own.applied_at:
                    if unit not in done:
                        done.append(unit)
                    continue
            if unit in done:
                if (want.key, unit) not in self._reclaimed:  # first look since a restart: is it still ours?
                    self._reclaimed.add((want.key, unit))
                    if unit.startswith("light:") and (ctl := mgr.controller(unit[6:])) is not None \
                            and self._reclaimable(ctl, want.look):
                        self._owned[unit] = _Owned(want.sid, want.key, now, self._print(ctl), fade_out=want.fade_out)
                continue
            await self._take(unit, want, mgr, now)
        for sid, (w, key) in active.items():
            if any(o.key == key for o in self._owned.values()) or self._windows[key]["units"]:
                s = self.get_schedule(sid)
                self._fire_started(s, key, w)
        # 6. "turn on and leave" starts
        await self._oneoffs(now)
        self._last = now
        self._save()

    async def _take(self, unit: str, want: _Want, mgr: Any, now: datetime) -> bool:
        own = _Owned(want.sid, want.key, now, fade_out=want.fade_out)
        if not await self._apply_unit(unit, want.look, mgr, own, want.fade_in, want.fade_out):
            return False
        self._owned[unit] = own
        self._reclaimed.add((want.key, unit))
        done = self._windows[want.key]["units"]
        if unit not in done:
            done.append(unit)
        return True

    async def _oneoffs(self, now: datetime) -> None:
        for s in self.schedules:
            if not s["enabled"] or s.get("end") is not None:
                continue
            for w in sm.windows_between(s, self._last - timedelta(seconds=1), now + timedelta(microseconds=1),
                                        self._tz, self._sun):
                key = self._key(s["id"], w)
                if key in self._windows or w.start < now - CATCHUP:
                    continue
                await self._apply_once(s, key, now)

    # --- events ----------------------------------------------------------------------------
    def _fire_started(self, s: dict[str, Any] | None, key: str, w: sm.Window | None) -> None:
        win = self._windows.get(key)
        if s is None or win is None or win.get("announced"):
            return
        win["announced"] = True
        self.hass.bus.async_fire(EVENT_LIGHT_SCHEDULE_STARTED, {
            "schedule_id": s["id"], "name": s["name"], "start": key.rsplit("|", 1)[-1],
            "end": w.end.isoformat() if w and w.end else None})

    def _fire_ended(self, sid: str, key: str, reason: str) -> None:
        s = self.get_schedule(sid)
        self.hass.bus.async_fire(EVENT_LIGHT_SCHEDULE_ENDED, {
            "schedule_id": sid, "name": s["name"] if s else None, "reason": reason})

    # --- timer -----------------------------------------------------------------------------
    @callback
    def _rearm(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
        if not self._started:
            return
        now = dt_util.utcnow()
        wake = now + MAX_REARM
        if self._retry:
            wake = min(wake, now + RETRY)
        for s in self.schedules:
            try:
                for w, _ in self._windows_now(s, now):
                    if w.end:
                        wake = min(wake, w.end)
                if s["enabled"]:
                    for w in self._natural(s, now, now + MAX_REARM):
                        if w.start > now:
                            wake = min(wake, w.start)
            except Exception:  # noqa: BLE001  one odd schedule must not stop the timer
                _LOGGER.exception("Could not work out when %s changes next", s.get("name"))
        self._timer = async_track_point_in_time(self.hass, self._on_timer, max(wake, now + timedelta(seconds=1)))

    async def _on_timer(self, _now: datetime) -> None:
        self._timer = None
        await self._process()

    async def async_start(self, *_: Any) -> None:
        """Home Assistant is up: after a few seconds (the lights are set up by then) look at what is open now."""
        self._started = True
        self._last = dt_util.utcnow() - CATCHUP
        if self._startup is None:
            self._startup = self.hass.loop.call_later(
                STARTUP_DELAY, lambda: self.hass.async_create_task(self._first_look(), "light_show_studio light schedules"))

    async def _first_look(self) -> None:
        self._startup = None
        await self._process()

    @callback
    def async_stop_timers(self) -> None:
        self._started = False
        if self._timer is not None:
            self._timer()
        if self._startup is not None:
            self._startup.cancel()
        self._timer = self._startup = None


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


async def async_setup_light_scheduler(hass: HomeAssistant) -> LightScheduler:
    from homeassistant.helpers.start import async_at_started

    scheduler = LightScheduler(hass)
    await scheduler.async_load()
    hass.data[LIGHT_SCHEDULER_KEY] = scheduler
    async_at_started(hass, scheduler.async_start)
    return scheduler
