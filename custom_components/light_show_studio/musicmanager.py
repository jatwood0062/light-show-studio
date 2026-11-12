"""Music sync sessions: a phone's audio features drive the pattern on some lights and areas.

A session takes its lights the way a zone does (a zone runner with a music plan), so the same rules apply: a person
picking a colour, a show starting or the light being turned off ends the session for that light, and the
other lights carry on. A session belongs to the connection (the phone) that started it: only that connection's
frames feed it, and it ends when the connection closes. Nothing here needs Home Assistant (the map manager and a way to
start tasks are passed in), so it is easy to test.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

from . import adhoc, music_engine, patterns
from .music_engine import Features, MusicPlan
from .zoneplayer import ZoneRunner

_LOGGER = logging.getLogger(__name__)
MAX_SESSIONS = 16
FADE_IN, FADE_OUT = 0.4, 0.6
AREA_CACHE = 30.0  # seconds an area lookup is kept at most


class MusicError(ValueError):
    """Music can't be started or fed; the message is for the person."""


class MusicRunner(ZoneRunner):
    """A zone runner that tells its lights when the music goes live or quiet, and lets them go cleanly."""

    def __init__(self, *args: Any, session: MusicSession, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.session = session
        self._live: bool | None = None

    def _tick(self, t: float) -> None:
        super()._tick(t)
        live = self.plan.live  # type: ignore[attr-defined]
        if live != self._live:
            self._live = live
            self.session.set_live(live, self._claimed.values())

    def _finish(self, restore: bool, keep=(), notify: bool = True) -> None:
        for ctl in list(self._claimed.values()):
            if hasattr(ctl, "set_music"):
                ctl.set_music(None)
        super()._finish(restore, keep, notify)


class MusicSession:
    def __init__(self, sid: str, owner: object, units: frozenset[tuple[str, str]], plan: MusicPlan, info: dict[str, Any]) -> None:
        self.id, self.owner, self.units, self.plan, self.info = sid, owner, units, plan, info
        self.runner: MusicRunner | None = None

    def set_live(self, live: bool, controllers: Any) -> None:
        self.info["live"] = bool(live)
        for ctl in controllers:
            if hasattr(ctl, "set_music"):
                ctl.set_music(dict(self.info))


class MusicManager:
    def __init__(self, hass: Any, mgr: Any, clock: Callable[[], float] = time.monotonic) -> None:
        self.hass = hass
        self.mgr = mgr  # the MapManager: lights and areas
        self._clock = clock
        self.sessions: dict[str, MusicSession] = {}
        self._n = 0
        self._area_ids: dict[str, str | None] = {}  # area ref -> area id, kept until the map changes
        self._area_at = 0.0
        if hasattr(mgr, "add_listener"):
            mgr.add_listener(self._area_ids.clear)

    # --- start ---------------------------------------------------------------------------------
    async def async_start(self, owner: object, lights: list[str], areas: list[str], pattern: str | None = None,
                          effect: str = music_engine.PULSE, sensitivity: int = music_engine.DEFAULT_SENSITIVITY,
                          mode: str = "string", axis: Any = None, idle_effect: str | None = None) -> dict[str, Any]:
        if effect not in music_engine.EFFECTS:
            raise MusicError(f"Effect must be one of {', '.join(music_engine.EFFECTS)}")
        if isinstance(sensitivity, bool) or not isinstance(sensitivity, int) or not 1 <= sensitivity <= 100:
            raise MusicError("Sensitivity must be a whole number from 1 to 100")
        name = patterns.canonical(pattern) if pattern is not None else patterns.DEFAULT_PATTERN
        if name is None:
            raise MusicError("Unknown pattern")
        if idle_effect is not None and idle_effect not in patterns.ALL_EFFECTS:
            raise MusicError("Unknown idle effect")
        try:  # the zone id depends on the targets, so starting again on the same ones takes over from the old session
            zid = "music:" + hashlib.sha1("|".join(sorted(["l:" + e for e in lights] + ["a:" + a for a in areas])).encode()).hexdigest()[:8]
            zone, runs, entries, found = adhoc.build(self.mgr, lights, areas, zid, "Music", name, mode=mode, axis=axis)
        except adhoc.TargetError as err:
            raise MusicError(str(err)) from err
        sid = zone.id
        plan = music_engine.make_plan(zone, runs, effect, sensitivity, idle_effect, self._clock)
        if plan.n == 0:
            raise MusicError("There are no pixels to light")
        units = frozenset([("light", e) for e in dict.fromkeys(lights)] + [("area", a.id) for a in found])
        for old in [s for s in self.sessions.values() if s.units & units]:  # one music session per light or area
            if old.id == sid and old.runner is not None:  # the same targets again: swap without the lights flashing back
                if self.sessions.get(old.id) is old:
                    del self.sessions[old.id]
                old.runner.stop(restore=True, keep=set(entries))
            else:
                self.stop_session(old)
        if len(self.sessions) >= MAX_SESSIONS:
            raise MusicError("Too many music sessions are running")
        info = {"active": True, "effect": effect, "pattern": name, "sensitivity": sensitivity, "live": False}
        session = MusicSession(sid, owner, units, plan, info)
        runner = MusicRunner(plan, patterns.DEFAULT_SPEED, self.mgr.controller, self.hass.async_create_background_task,
                             lambda r: self._ended(session, r), FADE_IN, FADE_OUT, session=session)
        session.runner = runner
        self.sessions[sid] = session
        try:
            await runner.async_start()
        except BaseException:
            if self.sessions.get(sid) is session:
                del self.sessions[sid]
            runner.stop(restore=True, fade_out=0)
            raise
        for ctl in runner._claimed.values():  # noqa: SLF001
            if hasattr(ctl, "set_music"):
                ctl.set_music(dict(info))
        if not runner._claimed:  # noqa: SLF001
            self.stop_session(session)
            raise MusicError("None of those lights could be used")
        return {"session": sid, "lights": sorted(runner._claimed), "effect": effect, "pattern": name,  # noqa: SLF001
                "sensitivity": sensitivity, "timeout": music_engine.TIMEOUT}

    # --- follow a speaker (no microphone) ---------------------------------------------------------------
    async def async_follow(self, entity_id: str, lights: list[str], areas: list[str], **options: Any) -> dict[str, Any]:
        """Start a session fed from the analysis of the song a media player is playing (see music_follow)."""
        from . import music_follow  # noqa: PLC0415  (it imports this module)
        return await music_follow.async_follow(self, entity_id, lights, areas, **options)

    # --- frames -----------------------------------------------------------------------------------
    def _unit_set(self, lights: list[str], areas: list[str]) -> frozenset[tuple[str, str]]:
        out = {("light", e) for e in lights}
        if areas:  # the map model is only built when an area has to be looked up
            now = self._clock()
            if now - self._area_at > AREA_CACHE:  # the map listener clears it; this covers lights moving areas
                self._area_ids.clear()
                self._area_at = now
            model = None
            for ref in areas:
                if ref not in self._area_ids:
                    model = model or self.mgr.model
                    a = model.area(ref)
                    if len(self._area_ids) < 256:
                        self._area_ids[ref] = a.id if a is not None else None
                    elif a is not None:
                        out.add(("area", a.id))
                        continue
                if self._area_ids.get(ref) is not None:
                    out.add(("area", self._area_ids[ref]))
        return frozenset(out)

    def feed(self, owner: object, lights: list[str], areas: list[str], features: Features) -> int:
        """Give a frame to this owner's sessions that include any of those lights or areas; how many got it."""
        units = self._unit_set(lights, areas)
        n = 0
        for s in self.sessions.values():
            if s.owner is owner and s.units & units:
                s.plan.feed(features)
                n += 1
        return n

    # --- stop -----------------------------------------------------------------------------------------
    def stop(self, owner: object | None = None, lights: list[str] | None = None, areas: list[str] | None = None) -> int:
        """End this owner's sessions (all of them, or those with any of the lights / areas); how many ended."""
        units = self._unit_set(lights or [], areas or []) if (lights or areas) else None
        hit = [s for s in self.sessions.values() if (owner is None or s.owner is owner)
               and (units is None or s.units & units)]
        for s in hit:
            self.stop_session(s)
        return len(hit)

    def stop_session(self, session: MusicSession, fade_out: float | None = None) -> None:
        if self.sessions.get(session.id) is not session:
            return  # already ended, or a newer session has the id
        del self.sessions[session.id]
        if session.runner is not None:
            session.runner.stop(restore=True, fade_out=fade_out)

    def stop_all(self) -> None:
        for s in list(self.sessions.values()):
            if self.sessions.get(s.id) is s:
                del self.sessions[s.id]
            if s.runner is not None:
                s.runner.stop(restore=False)

    def _ended(self, session: MusicSession, runner: ZoneRunner) -> None:
        if self.sessions.get(session.id) is session:
            del self.sessions[session.id]

    def view(self) -> list[dict[str, Any]]:
        return [{"session": s.id, **s.info} for s in self.sessions.values()]
