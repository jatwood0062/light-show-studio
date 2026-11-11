"""Plays an .fseq file into the same path as frames from the network receiver.

Frames follow a `Timeline` (song time in seconds). It runs off the monotonic clock and
can be re-anchored to a speaker's reported position, so the lights stay on the music.
"""
from __future__ import annotations

import asyncio
import os
import logging
import math
import time
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from . import fade
from .fseq import FseqFile

_LOGGER = logging.getLogger(__name__)

PREFETCH_SECONDS = 2.0
TOLERANCE = 0.08  # drift we live with
JUMP = 1.0  # drift beyond this is a seek, not drift: jump straight there
SLEW_RATE = 0.25  # otherwise run up to 25% fast/slow until caught up


def lead_index(i: int, lead_ms: int, step: float, frames: int) -> int:
    """The frame for song time + lead when frame `i` is current: shifted by whole frames, clamped to the show."""
    return max(0, min(frames - 1, i + round(lead_ms / 1000 / step)))


def reported_position(position: float | None, updated_at: datetime | None, now: datetime,
                      playing: bool) -> float | None:
    """Where a media player is in the song now, from its last report."""
    if position is None:
        return None
    if not playing or updated_at is None:
        return float(position)
    return float(position) + (now - updated_at).total_seconds()


class Timeline:
    """Song time against the monotonic clock: set, pause, resume and drift-corrected sync."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.paused = False
        self.set(0.0)

    def _at(self, t: float) -> float:
        if self.paused:
            return self._p0
        pos = self._p0 + (t - self._t0)
        if self._slew:
            pos += math.copysign(min(abs(self._slew), (t - self._t0) * SLEW_RATE), self._slew)
        return pos

    def now(self) -> float:
        return self._at(self.clock())

    def set(self, pos: float) -> None:
        self._t0, self._p0, self._slew = self.clock(), pos, 0.0

    def pause(self) -> None:
        if not self.paused:
            self.set(self.now())
            self.paused = True

    def resume(self, pos: float | None = None) -> None:
        p = self.now() if pos is None else pos
        self.paused = False
        self.set(p)

    def sync(self, pos: float) -> float:
        """The song is at `pos` right now; correct towards it. Returns the drift found."""
        err = pos - self.now()
        if self.paused or abs(err) > JUMP:
            self.set(pos)
        elif abs(err) > TOLERANCE:
            cur = self.now()
            self.set(cur)
            self._slew = err
        return err


class SequencePlayer:
    """One show at a time; `output` gets each frame's absolute channel data."""

    def __init__(self, output: Callable[[bytes], None],
                 create_task: Callable[[Coroutine[Any, Any, None]], asyncio.Task] | None = None,
                 lead_output: Callable[[int, bytes], None] | None = None,
                 leads: Callable[[], list[int]] | None = None) -> None:
        self.output = output
        # lights with a show timing lead get their own frame: `leads()` lists the leads (ms) in use and
        # `lead_output(lead, data)` shows a frame to the lights with that lead; `output` serves the rest
        self.lead_output = lead_output
        self.leads = leads
        self._create_task = create_task or asyncio.ensure_future
        self._task: asyncio.Task | None = None
        self.timeline = Timeline()
        self.frames_shown = 0
        self.frames_skipped = 0
        self.last_frame: bytes | None = None  # the last frame of the current/finished show, unscaled
        self.hold_on_stop = False  # a show ending keeps its last frame up (a transition takes over from it)
        self.fade_in = 0.0  # seconds the next play() fades in from black; used up by that play()
        self._fade = 0.0
        self._fade_t0 = 0.0
        self.sync = None  # an fpp.FppSync: show controllers holding the show themselves play it in step

    @property
    def current(self) -> asyncio.Task | None:
        return self._task

    @property
    def playing(self) -> bool:
        return self._task is not None and not self._task.done()

    def play(self, seq: FseqFile, loop: bool = False, delay: float = 0.0,
             timeline: Timeline | None = None, hold: bool = False,
             fade_in: float = 0.0) -> asyncio.Task:
        """Start a show. `hold` keeps the last frame up at the end instead of finishing
        (when following a speaker, which decides when the show is over). `fade_in` brings the
        lights up from black over that many seconds (or set `self.fade_in` before play_with_speaker)."""
        self.stop(blank=False)
        self._fade, self.fade_in = max(0.0, fade_in or self.fade_in), 0.0
        self._fade_t0 = time.monotonic()
        self.last_frame = None
        if timeline is None:
            timeline = Timeline()
            timeline.set(-delay)
        self.timeline = timeline
        self._task = self._create_task(self._run(seq, loop, timeline, hold))
        return self._task

    def stop(self, blank: bool = True) -> None:
        if not self.playing:
            return
        self._task.cancel()
        self._task = None
        if blank and not self.hold_on_stop:  # after the external-frame rate limit, so it is not dropped
            asyncio.get_running_loop().call_later(0.1, self.output, b"")

    def _faded(self, data: bytes) -> bytes:
        if self._fade <= 0:
            return data
        k = (time.monotonic() - self._fade_t0) / self._fade
        if k >= 1.0:
            self._fade = 0.0
            return data
        k = max(0.0, k)
        return fade.scale_bytes(data, k)

    async def _lead_frames(self, seq: FseqFile, i: int, step: float, ev: asyncio.AbstractEventLoop,
                           sent: dict[int, int]) -> None:
        """Frames for the lights with a show timing lead: frame i shifted by the lead, clamped to the show."""
        if self.lead_output is None or self.leads is None:
            return
        for lead in self.leads():
            j = lead_index(i, lead, step, seq.frames)
            if sent.get(lead) == j:
                continue
            sent[lead] = j
            data = seq.cached(j)
            if data is None:
                data = await ev.run_in_executor(None, seq.frame, j)
            self.lead_output(lead, self._faded(data))

    async def _run(self, seq: FseqFile, loop: bool, tl: Timeline, hold: bool) -> None:
        if not seq.frames:
            return
        ev = asyncio.get_running_loop()
        step = seq.step_ms / 1000
        ahead = max(1, int(PREFETCH_SECONDS / step))
        self.frames_shown = self.frames_skipped = 0
        _LOGGER.info("Playing %s: %d frames at %d ms", seq.path, seq.frames, seq.step_ms)
        sync, name = self.sync, os.path.basename(str(seq.path))
        if sync is not None:
            sync.start(name)
        try:
            await self._frames(seq, loop, tl, hold, ev, step, ahead)
        except asyncio.CancelledError:
            if sync is not None:
                sync.stop(name)
            raise
        except Exception as err:  # noqa: BLE001  OSError, zstd errors: stop cleanly instead of dying silently
            _LOGGER.warning("Stopped %s: can't read frames: %s", seq.path, err)
            ev.call_later(0.1, self.output, b"")  # after the external-frame rate limit
            if sync is not None:
                sync.stop(name)
            return
        if sync is not None:
            sync.stop(name)
        _LOGGER.info("Finished %s (%d frames skipped)", seq.path, self.frames_skipped)

    async def _frames(self, seq: FseqFile, loop: bool, tl: Timeline, hold: bool,
                      ev: asyncio.AbstractEventLoop, step: float, ahead: int) -> None:
        prefetch: asyncio.Future | None = None
        last = -1
        led: dict[int, int] = {}  # lead ms -> the frame it was last sent
        while True:
            pos = tl.now()
            i = math.floor(pos / step)
            if i >= seq.frames:
                if loop and not hold:
                    tl.set(pos % seq.duration)
                    continue
                if not hold:
                    break
                i = seq.frames - 1
            if i >= 0 and i != last:
                data = seq.cached(i)
                if data is None:
                    data = await ev.run_in_executor(None, seq.frame, i)
                self.last_frame = data
                self.output(self._faded(data))
                if (sync := self.sync) is not None:
                    sync.frame(os.path.basename(str(seq.path)), i, max(0.0, pos))
                await self._lead_frames(seq, i, step, ev, led)
                self.frames_shown += 1
                if last >= 0 and i > last + 1:
                    self.frames_skipped += i - last - 1
                last = i
                if prefetch is None or prefetch.done():  # decode the next block not ready yet, early
                    j = seq.first_uncached(i + 1, min(i + ahead, seq.frames - 1))
                    if j is not None:
                        prefetch = ev.run_in_executor(None, seq.frame, j)
            if tl.paused or pos >= seq.duration:
                if tl.paused and (sync := self.sync) is not None:
                    sync.pause(os.path.basename(str(seq.path)))
                await asyncio.sleep(step)
            else:  # wake at the next frame boundary, allowing for the timeline running fast
                await asyncio.sleep(max(0.001, ((i + 1) * step - pos) / (1 + SLEW_RATE if tl._slew else 1)))
