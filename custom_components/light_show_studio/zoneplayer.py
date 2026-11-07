"""Runs one zone: one clock, one render of the zone's virtual string per frame, sliced out to its lights.

The runner never owns a light for good. A light that a person takes (a colour, an effect, off) or that a
show starts on tells the runner so on its next push and is left alone from then on; a light that is not
loaded is skipped and rejoins when it comes back. When no light is left the runner ends itself.
Nothing here needs Home Assistant (the manager passes in how to find lights and start tasks).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Collection, Coroutine
from typing import Any, Protocol

from . import patterns
from .const import ZONE_FPS, ZONE_FPS_LARGE, ZONE_LARGE
from .mapped_patterns import ZonePlan

_LOGGER = logging.getLogger(__name__)
STILL_RESEND = 1.0  # a Steady zone is sent again this often, in case a light dropped the frame


class ZoneLight(Protocol):
    async def async_claim_zone(self, zone_id: str, name: str) -> None: ...
    def push_zone_frame(self, zone_id: str, slices: list[tuple[int, list[Any]]]) -> bool: ...
    def release_zone(self, zone_id: str | None = None, restore: bool = False) -> None: ...


class ZoneRunner:
    def __init__(self, plan: ZonePlan, speed: int, resolve: Callable[[str], ZoneLight | None],
                 spawn: Callable[[Coroutine[Any, Any, Any], str], asyncio.Future | asyncio.Task],
                 on_end: Callable[[ZoneRunner], None] | None = None) -> None:
        self.plan = plan
        self.zone_id = plan.zone.id
        self.speed = speed
        self._resolve = resolve
        self._spawn = spawn
        self._on_end = on_end
        self._entries = list(dict.fromkeys(s.run.entry_id for s in plan.slices))
        self._claimed: dict[str, ZoneLight] = {}
        self._claiming: dict[str, asyncio.Future] = {}
        self._dropped: set[str] = set()  # lights a person took: not ours any more
        self._task: asyncio.Future | None = None
        self._ended = False
        self._t = 0.0  # the zone's clock
        self.interval = 1 / (ZONE_FPS_LARGE if plan.n > ZONE_LARGE else ZONE_FPS)

    @property
    def running(self) -> bool:
        return not self._ended

    def set_speed(self, speed: int) -> None:
        self.speed = max(1, min(100, int(speed)))  # the next frame uses it; the clock keeps running

    async def async_start(self) -> None:
        loaded = [(e, c) for e in self._entries if (c := self._resolve(e)) is not None]
        await asyncio.gather(*(self._claim(e, c) for e, c in loaded))
        if not self._ended:
            self._task = self._spawn(self._run(), f"light_show_studio zone {self.plan.zone.name}")

    async def _claim(self, entry: str, ctl: ZoneLight, show_now: bool = False) -> None:
        try:
            await ctl.async_claim_zone(self.zone_id, self.plan.zone.name)
        except Exception:  # noqa: BLE001  one light failing must not stop the others
            _LOGGER.warning("Zone %s: can't use light %s", self.plan.zone.name, entry, exc_info=True)
            return
        if not self._ended:
            self._claimed[entry] = ctl
            if show_now:  # a light that joins late shows the current frame at once, not at the next (maybe 1 s away)
                self._tick(self._t)

    def _rejoin(self, entry: str, ctl: ZoneLight) -> None:
        if entry not in self._claiming:
            task = self._claiming[entry] = self._spawn(self._claim(entry, ctl, True), "light_show_studio zone rejoin")
            task.add_done_callback(lambda _f, e=entry: self._claiming.pop(e, None))

    async def _run(self) -> None:
        t, last = self._t, time.monotonic()
        due = last
        try:
            while not self._ended:
                now = time.monotonic()
                t += min(now - last, 0.25) * patterns.speed_factor(self.speed)  # a long stall doesn't leap ahead
                last = now
                self._t = t
                self._tick(t)
                if not any(e not in self._dropped for e in self._entries):
                    break  # every light was taken
                if self.plan.animated:
                    due += self.interval
                    delay = due - time.monotonic()
                    if delay < -self.interval:  # fell behind (a busy loop): don't rush to catch up
                        due, delay = time.monotonic(), 0.0
                else:
                    delay = STILL_RESEND
                await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  never leave lights claimed by a dead task
            _LOGGER.exception("Zone %s stopped", self.plan.zone.name)
        self._finish(restore=True)

    def _tick(self, t: float) -> None:
        by_light: dict[str, list[tuple[int, list[Any]]]] = {}
        for entry, first, colours in self.plan.split(self.plan.render(t)):
            by_light.setdefault(entry, []).append((first, colours))
        for entry, slices in by_light.items():
            if entry in self._dropped:
                continue
            ctl = self._resolve(entry)
            if ctl is None:  # not loaded now: skip, and join again when it is back
                self._claimed.pop(entry, None)
                continue
            if self._claimed.get(entry) is not ctl:
                self._rejoin(entry, ctl)
                continue
            if not ctl.push_zone_frame(self.zone_id, slices):
                self._dropped.add(entry)
                self._claimed.pop(entry, None)

    def stop(self, restore: bool = True, keep: Collection[str] = ()) -> None:
        """End the zone. Lights go back to what they showed before (restore), except those in `keep`,
        which another run of the same zone is about to take over."""
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
        self._finish(restore, keep, notify=False)

    def _finish(self, restore: bool, keep: Collection[str] = (), notify: bool = True) -> None:
        if self._ended:
            return
        self._ended = True
        for task in self._claiming.values():
            task.cancel()
        for entry, ctl in list(self._claimed.items()):
            if entry not in keep:
                ctl.release_zone(self.zone_id, restore)
        self._claimed.clear()
        if notify and self._on_end:
            self._on_end(self)
