"""Music sync that follows a speaker: the lights react to a song Home Assistant has as a file, with no microphone.

The song's features are worked out ahead of time (audio_analysis). While the speaker plays, the features at the
speaker's position (its last report, carried forward by the clock) are fed to an ordinary music session about 25
times a second, so every music effect works as it does with a phone. A pause stops the feed (the lights ease to the
pattern's normal look, as when a phone goes quiet). When the speaker moves to another song of the show library the
session follows it; any other song, or the speaker stopping, ends it. The speaker's state is read through
`hass.states`, so nothing here imports Home Assistant and it is easy to test.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from . import audio_analysis, music_engine
from .audio_analysis import Analysis, AnalysisError
from .library import AUDIO_EXTS, library_for
from .music_engine import Features
from .musicmanager import MusicError, MusicManager, MusicSession

_LOGGER = logging.getLogger(__name__)

SHOW_MEDIA = "/xlights/"  # show songs are served from media/xlights (as follow.py)
TICK = 0.04  # seconds between frames to the engine (a phone sends 20-25 a second)
RUNNING = ("playing", "paused", "buffering")
GONE_GRACE = 10.0  # seconds the speaker may be idle / unavailable (between two songs) before the session ends
MAX_SKIP = 1.0  # seconds: a larger step in position is a seek, and the beats in between are not played
MAX_OFFSET = 10.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_song(library: Any, ref: Any) -> str | None:
    """The real path of the show library song a speaker's media_content_id (a URL or media-source id with
    /xlights/ in it) or a name relative to the library points at; None when it is not one of ours. Blocking."""
    if not isinstance(ref, str) or not ref or "\0" in ref or len(ref) > 4096:
        return None
    at = ref.find(SHOW_MEDIA)
    if at >= 0:
        rel = unquote(ref[at + len(SHOW_MEDIA):].split("?", 1)[0].split("#", 1)[0])
    elif "://" in ref or os.path.isabs(ref):
        return None
    else:
        rel = ref
    if not rel or "\0" in rel or not rel.lower().endswith(AUDIO_EXTS):
        return None
    try:
        path = library._inside(os.path.join(library.root, *rel.split("/")))  # noqa: SLF001
    except (ValueError, OSError):
        return None
    return path if os.path.isfile(path) else None


def _when(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def speaker_position(state: Any, now: datetime) -> float | None:
    """Where the speaker is in its song at `now`, in seconds: its last reported position, carried forward from
    media_position_updated_at while it plays. None when it reports no position."""
    pos = state.attributes.get("media_position")
    if isinstance(pos, bool) or not isinstance(pos, (int, float)):
        return None
    at = _when(state.attributes.get("media_position_updated_at"))
    if state.state != "playing" or at is None:
        return float(pos)
    return float(pos) + max(0.0, (now - at).total_seconds())


class SpeakerFollow:
    """Feeds one music session from a song's analysis at a speaker's position. It is the session's owner."""

    def __init__(self, manager: MusicManager, entity_id: str, library: Any, analysis: Analysis, track: Any,
                 fixed: bool, offset: float, load: Callable[[Any, str], Awaitable[Analysis]],
                 utcnow: Callable[[], datetime] = _utcnow, clock: Callable[[], float] = time.monotonic) -> None:
        self.manager, self.hass, self.entity_id, self.library = manager, manager.hass, entity_id, library
        self.analysis: Analysis | None = analysis
        self.track = track  # the media_content_id being followed
        self.fixed = fixed  # the song was named by the caller: any other track ends the session
        self.offset = offset
        self.session: MusicSession | None = None
        self.position: float | None = None  # the last position fed (for tests and the view)
        self._load, self._utcnow, self._clock = load, utcnow, clock
        self._last: int | None = None
        self._gone: float | None = None

    @property
    def active(self) -> bool:
        return self.session is not None and self.manager.sessions.get(self.session.id) is self.session

    def stop(self) -> None:
        if self.session is not None:
            self.manager.stop_session(self.session)

    def tick(self) -> bool:
        """One step: read the speaker, feed the session. False when the session is over."""
        if not self.active:
            return False
        state = self.hass.states.get(self.entity_id)
        if state is None or state.state not in RUNNING:
            now = self._clock()
            if self._gone is None:
                self._gone = now
            elif now - self._gone > GONE_GRACE:
                _LOGGER.debug("%s stopped; ending music", self.entity_id)
                self.stop()
                return False
            return True
        self._gone = None
        track = state.attributes.get("media_content_id")
        if track != self.track and not (self.fixed and self.track is None):
            if self.fixed:
                _LOGGER.debug("%s changed track; ending music", self.entity_id)
                self.stop()
                return False
            self.track, self.analysis, self._last = track, None, None
            self.hass.async_create_background_task(self._switch(track), "light_show_studio music follow song")
            return True
        self.track = track
        a = self.analysis
        if a is None or state.state != "playing":
            return True  # loading the next song, or paused: no frames, so the lights ease to their normal look
        pos = speaker_position(state, self._utcnow())
        if pos is None:
            return True
        self.position = pos = pos + self.offset
        i = a.index(pos)
        if i is None:
            return True
        last = self._last
        beat = bool(a.beats[i]) if last is None or not 0 <= i - last <= MAX_SKIP / a.hop_s else a.beat_between(last, i)
        self._last = i
        self.session.plan.feed(Features(*a.values(i), beat, a.bpm))  # type: ignore[union-attr]
        return True

    async def _switch(self, track: Any) -> None:
        """The speaker moved on: follow the new song if it is one of ours, else end the session."""
        try:
            path = await self.hass.async_add_executor_job(resolve_song, self.library, track)
            analysis = await self._load(self.hass, path) if path else None
        except AnalysisError as err:
            _LOGGER.info("Music can't follow %s any more: %s", self.entity_id, err)
            analysis = None
        if self.track != track or not self.active:
            return  # it moved on again meanwhile
        if analysis is None:
            _LOGGER.debug("%s plays something else; ending music", self.entity_id)
            self.stop()
        else:
            self.analysis = analysis

    async def run(self) -> None:
        try:
            while self.tick():
                await asyncio.sleep(TICK)
        finally:
            self.stop()


async def async_follow(manager: MusicManager, entity_id: str, lights: list[str], areas: list[str],
                       pattern: str | None = None, effect: str = music_engine.PULSE,
                       sensitivity: int = music_engine.DEFAULT_SENSITIVITY, mode: str = "string", axis: Any = None,
                       idle_effect: str | None = None, song: str | None = None, offset: float = 0.0,
                       library: Any = None, load: Callable[[Any, str], Awaitable[Analysis]] | None = None,
                       utcnow: Callable[[], datetime] = _utcnow) -> dict[str, Any]:
    """Start a music session on the lights / areas that follows the song `entity_id` is playing (or `song`, a
    name in the show library, when the speaker's own id for it can't be matched to a file)."""
    hass = manager.hass
    state = hass.states.get(entity_id) if isinstance(entity_id, str) and entity_id.startswith("media_player.") else None
    if state is None:
        raise MusicError(f"{entity_id} is not a media player that exists")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not -MAX_OFFSET <= offset <= MAX_OFFSET:
        raise MusicError(f"Offset must be a number of seconds from -{MAX_OFFSET:g} to {MAX_OFFSET:g}")
    library = library or library_for(hass)
    track = state.attributes.get("media_content_id")
    path = await hass.async_add_executor_job(resolve_song, library, song if song is not None else track)
    if path is None:
        raise MusicError("That song is not in the show library" if song is not None
                         else f"{entity_id} is not playing a song from the show library")
    load = load or audio_analysis.async_analysis
    try:
        analysis = await load(hass, path)
    except AnalysisError as err:
        raise MusicError(str(err)) from err
    follow = SpeakerFollow(manager, entity_id, library, analysis, track, song is not None, float(offset), load, utcnow)
    result = await manager.async_start(follow, lights, areas, pattern, effect, sensitivity, mode, axis, idle_effect)
    session = follow.session = manager.sessions[result["session"]]
    session.info["follow"] = entity_id
    if session.runner is not None:
        session.set_live(session.info["live"], session.runner._claimed.values())  # noqa: SLF001
    hass.async_create_background_task(follow.run(), "light_show_studio music follow")
    return {**result, "media_player": entity_id, "song": os.path.basename(path),
            "duration": round(analysis.duration, 1), "bpm": analysis.bpm}
