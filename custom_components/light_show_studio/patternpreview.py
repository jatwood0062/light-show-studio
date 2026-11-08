"""Pattern previews: show a pattern that isn't saved yet on a light or an area while it is being made, and put the
lights back after. A preview takes its lights the way a zone does (so a person or a show taking them ends it for them),
belongs to the connection that started it, and ends on preview_stop, after PREVIEW_IDLE seconds without a call, or
when the connection closes. Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import adhoc, patterns, user_patterns
from .mapped_patterns import ZonePlan
from .zoneplayer import ZoneRunner

_LOGGER = logging.getLogger(__name__)
PREVIEW_IDLE = 60.0  # seconds without a preview / paint call after which the lights are put back
FADE_IN, FADE_OUT = 0.2, 0.3


class PreviewError(ValueError):
    """A preview can't be started or painted; the message is for the person."""


class PreviewSession:
    def __init__(self, owner: object, up: user_patterns.UserPattern | None, pattern_name: str, zone: Any,
                 runs: dict[str, Any], entries: list[str]) -> None:
        self.owner, self.up, self.pattern_name, self.zone, self.runs, self.entries = owner, up, pattern_name, zone, runs, entries
        self.runner: ZoneRunner | None = None
        self.handle: Any = None


class PreviewManager:
    def __init__(self, hass: Any, mgr: Any, idle: float = PREVIEW_IDLE) -> None:
        self.hass, self.mgr, self.idle = hass, mgr, idle
        self.sessions: dict[int, PreviewSession] = {}  # by owner (the connection), at most one each
        self._owners: dict[int, object] = {}
        self._n = 0

    # --- start -----------------------------------------------------------------------------
    async def async_start(self, owner: object, entry_id: str | None, area: str | None, pattern: Any,
                          effect: str, speed: int) -> dict[str, Any]:
        if (entry_id is None) == (area is None):
            raise PreviewError("Give either entry_id or area")
        if effect not in patterns.ALL_EFFECTS:
            raise PreviewError("Unknown effect")
        if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 100:
            raise PreviewError("Speed must be a whole number from 1 to 100")
        self._n += 1
        up: user_patterns.UserPattern | None = None
        if isinstance(pattern, dict):  # a pattern not saved (yet)
            try:
                clean = user_patterns.validate({k: v for k, v in pattern.items() if k != "id"}, builtin_names=(),
                                               effects=patterns.PATTERN_EFFECTS, require_name=False)
            except ValueError as err:
                raise PreviewError(str(err)) from err
            clean["name"] = f"{user_patterns.RESERVED}preview{self._n}"
            up = user_patterns.UserPattern(clean, f"{user_patterns.RESERVED}preview{self._n}")
            name = up.name
        else:
            name = patterns.canonical(pattern)
            if name is None:
                raise PreviewError("Unknown pattern")
        try:
            zone, runs, entries, _ = adhoc.build(self.mgr, [entry_id] if entry_id else [], [area] if area else [],
                                                 f"preview:{id(owner)}", "Preview", name, effect, speed)
        except adhoc.TargetError as err:
            raise PreviewError(str(err)) from err
        if up is not None:
            patterns.register_user_pattern(up)
        plan = ZonePlan(zone, runs)
        if plan.n == 0:
            self._drop(up)
            raise PreviewError("There are no pixels to light")
        session = PreviewSession(owner, up, name, zone, runs, entries)
        old = self.sessions.get(id(owner))
        runner = ZoneRunner(plan, speed, self.mgr.controller, self.hass.async_create_background_task,
                            lambda r: self._ended(session, r), 0.0 if old else FADE_IN, FADE_OUT)
        session.runner = runner
        self.sessions[id(owner)] = session
        self._owners[id(owner)] = owner
        if old is not None:  # swap without the lights flashing back: the new preview keeps what the old one held
            self._release(old, keep=set(entries))
        try:
            await runner.async_start()
        except BaseException:
            if self.sessions.get(id(owner)) is session:  # not a newer preview of this owner's
                self.stop(owner, fade_out=0)
            else:
                self._release(session, fade_out=0)
            raise
        self._arm(session, owner)
        return {"lights": sorted(runner._claimed), "pattern": name, "idle_seconds": self.idle}  # noqa: SLF001

    @staticmethod
    def _drop(up: user_patterns.UserPattern | None) -> None:
        if up is not None:
            patterns.unregister_user_pattern(up.id)

    def _release(self, session: PreviewSession, keep: set[str] | None = None, fade_out: float | None = None) -> None:
        if session.handle is not None:
            session.handle.cancel()
            session.handle = None
        if session.runner is not None:
            if keep:
                session.runner.stop(restore=True, keep=keep)
            else:
                session.runner.stop(restore=True, fade_out=fade_out)
        self._drop(session.up)

    # --- paint --------------------------------------------------------------------------------
    def paint(self, owner: object, entry_id: str, items: list[tuple[int, tuple[int, int, int]]]) -> int:
        """Set pixels of the previewed pattern on one light, and show them at once."""
        session = self.sessions.get(id(owner))
        if session is None:
            raise PreviewError("No preview is running")
        if session.up is None or session.up.kind != "pixels":
            raise PreviewError("Only a pixels pattern can be painted")
        if entry_id not in session.entries:
            raise PreviewError("That light isn't part of the preview")
        ctl = self.mgr.controller(entry_id)
        if ctl is None:
            raise PreviewError("That light isn't set up")
        if not items or len(items) > user_patterns.MAX_PAINT_BATCH:
            raise PreviewError(f"Paint 1 to {user_patterns.MAX_PAINT_BATCH} pixels at a time")
        if any(not 0 <= i < ctl.bulbs for i, _ in items):
            raise PreviewError(f"A pixel index must be between 0 and {ctl.bulbs - 1}")
        total = sum(len(c) for _, c in session.up.lights.values()) + len(items)
        if total > user_patterns.MAX_PAINTED:
            raise PreviewError("That pattern has too many painted pixels")
        session.up.paint(entry_id, items)
        patterns.touch_user_patterns()
        if session.runner is not None:
            session.runner.replace_plan(ZonePlan(session.zone, session.runs))
        self._arm(session, owner)
        return len(items)

    # --- stop -----------------------------------------------------------------------------------
    def stop(self, owner: object, fade_out: float | None = None, only: PreviewSession | None = None) -> bool:
        """End the owner's preview (only if it is `only`, when given)."""
        session = self.sessions.get(id(owner))
        if session is None or (only is not None and session is not only):
            return False
        del self.sessions[id(owner)]
        self._owners.pop(id(owner), None)
        self._release(session, fade_out=fade_out)
        return True

    def stop_all(self) -> None:
        for key in list(self.sessions):
            session = self.sessions.pop(key)
            self._owners.pop(key, None)
            if session.handle is not None:
                session.handle.cancel()
            if session.runner is not None:
                session.runner.stop(restore=False)
            self._drop(session.up)

    def _arm(self, session: PreviewSession, owner: object) -> None:
        if session.handle is not None:
            session.handle.cancel()
        session.handle = self.hass.loop.call_later(self.idle, self._idle, session, owner)

    def _idle(self, session: PreviewSession, owner: object) -> None:
        session.handle = None
        if self.sessions.get(id(owner)) is session:
            _LOGGER.debug("Pattern preview timed out; putting the lights back")
            self.stop(owner, only=session)

    def _ended(self, session: PreviewSession, runner: ZoneRunner) -> None:
        for key, s in list(self.sessions.items()):
            if s is session:
                del self.sessions[key]
                self._owners.pop(key, None)
        if session.handle is not None:
            session.handle.cancel()
            session.handle = None
        self._drop(session.up)
