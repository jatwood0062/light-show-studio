"""The light show player: a queue of library shows played one after another."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player.browse_media import async_process_play_media_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PLAYER
from .follow import play_with_speaker
from .fseq import FseqError, FseqFile, load
from .library import SHOW_PLAYER_KEY, Show, ShowLibrary, media_root
from .playback import SequencePlayer
from .receiver import PixelReceiver

_LOGGER = logging.getLogger(__name__)

IDLE, PLAYING, PAUSED = "idle", "playing", "paused"
REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = "off", "all", "one"
RESTART_AFTER = 3.0  # "previous" restarts the show when it is further in than this (s)
DEFAULT_GAP = 3.0


class ShowPlayer:
    def __init__(self, hass: HomeAssistant, library: ShowLibrary) -> None:
        self.hass = hass
        self.library = library
        self.speaker: str | None = None
        self.audio_offset = 0.0
        self.gap = DEFAULT_GAP
        self.shuffle = False
        self.repeat = REPEAT_OFF
        self.entity_id: str | None = None  # set by the media_player entity
        self.shows: dict[str, Show] = {}
        self.order: list[str] = []  # library order
        self.queue: list[str] = []  # play order (shuffled or not)
        self.show: Show | None = None
        self.state = IDLE
        self._index = 0
        self._with_speaker = False  # the current show's song is on the speaker
        self._runner: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []

    # --- setup ------------------------------------------------------------
    def configure(self, entry: ConfigEntry) -> None:
        def opt(key: str, default: Any) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        self.speaker = opt("speaker", None) or None
        self.audio_offset = float(opt("audio_offset", 0.0) or 0.0)
        gap = opt("gap", DEFAULT_GAP)
        self.gap = DEFAULT_GAP if gap is None else float(gap)

    def _sequencer(self) -> SequencePlayer:
        """The shared SequencePlayer, so play_sequence / stop_sequence act on the same one."""
        player = self.hass.data.get(PLAYER)
        if player is None:
            receiver: PixelReceiver = self.hass.data.setdefault(DOMAIN, PixelReceiver())
            player = self.hass.data[PLAYER] = SequencePlayer(
                receiver.show, lambda coro: self.hass.async_create_background_task(coro, "govee_c9 sequence"))
        return player

    async def async_refresh(self) -> None:
        """Re-read the library (after an upload, delete or reorder) and rebuild the queue."""
        shows = await self.hass.async_add_executor_job(self.library.scan)
        self.shows = {s.id: s for s in shows}
        self.order = [s.id for s in shows]
        current = self.show.id if self.show else None
        if self.shuffle:
            kept = [i for i in self.queue if i in self.shows]
            self.queue = kept + [i for i in self.order if i not in kept]
        else:
            self.queue = list(self.order)
        if current in self.queue:
            self._index = self.queue.index(current)
        elif self.state == IDLE:
            self.show, self._index = None, 0
        self._changed()

    # --- listeners ----------------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call `cb` on every state change; returns the function that removes it."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _changed(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001  one bad listener must not stop playback
                _LOGGER.exception("Show player listener failed")

    # --- state ------------------------------------------------------------
    @property
    def duration(self) -> float | None:
        return self.show.duration if self.show else None

    @property
    def position(self) -> float:
        """Seconds into the current show (0 between shows)."""
        if self.show is None or self.state == IDLE or not self._sequencer().playing:
            return 0.0
        return max(0.0, min(self._sequencer().timeline.now(), self.show.duration))

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "show_id": self.show.id if self.show else None,
            "position": round(self.position, 2),
            "position_updated_at": dt_util.utcnow().isoformat(),
            "duration": self.duration,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "speaker": self.speaker,
            "queue": list(self.queue),
            "entity_id": self.entity_id,
        }

    def position_at(self) -> tuple[float, datetime]:
        return self.position, dt_util.utcnow()

    # --- controls -----------------------------------------------------------
    async def async_play(self, show_id: str | None = None) -> None:
        """Play `show_id`, or resume if paused, or start the queue from the top."""
        if show_id is None and self.state == PAUSED:
            await self.async_resume()
            return
        if not self.shows or (show_id is not None and show_id not in self.shows):
            await self.async_refresh()
        if show_id is not None and show_id not in self.shows:
            raise HomeAssistantError(f"No show called {show_id}")
        if not self.queue:
            raise HomeAssistantError("There are no shows in the library")
        if show_id is None:
            show_id = self.show.id if self.show and self.show.id in self.queue else self.queue[0]
        if self.shuffle and show_id in self.queue:  # the chosen show leads, the rest stay shuffled
            self.queue.remove(show_id)
            self.queue.insert(0, show_id)
        self._start(self.queue.index(show_id))

    def _start(self, index: int) -> None:
        self._cancel_runner()
        self._index = index
        self.show = self.shows[self.queue[index]]
        self.state = PLAYING
        self._runner = self.hass.async_create_background_task(self._run(index), "govee_c9 show queue")
        self._changed()

    def _cancel_runner(self) -> None:
        if self._runner and not self._runner.done():
            self._runner.cancel()
        self._runner = None

    async def async_pause(self) -> None:
        if self.state != PLAYING:
            return
        if self._with_speaker:
            await self._speaker_call("media_pause")
        else:
            self._sequencer().timeline.pause()
        self.state = PAUSED
        self._changed()

    async def async_resume(self) -> None:
        if self.state != PAUSED:
            return
        if self._with_speaker:
            await self._speaker_call("media_play")
        else:
            self._sequencer().timeline.resume()
        self.state = PLAYING
        self._changed()

    async def async_stop(self) -> None:
        was_speaker = self._with_speaker
        self._cancel_runner()
        self._sequencer().stop()
        self._with_speaker = False
        self.state = IDLE
        if was_speaker:
            await self._speaker_call("media_stop")
        self._changed()

    async def async_next(self) -> None:
        nxt = self._next_index(auto=False)
        if nxt is None:
            await self.async_stop()
        else:
            self._start(nxt)

    async def async_previous(self) -> None:
        if not self.queue:
            return
        if (self.state != IDLE and self.position > RESTART_AFTER) or (self._index == 0 and self.repeat != REPEAT_ALL):
            self._start(self._index)
        else:
            self._start((self._index - 1) % len(self.queue))

    async def async_seek(self, position: float) -> None:
        if self.state == IDLE or self.show is None:
            return
        position = max(0.0, min(position, self.show.duration))
        if self._with_speaker:  # the lights re-sync to the speaker
            await self._speaker_call("media_seek", seek_position=position)
        else:
            self._sequencer().timeline.set(position)
        self._changed()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        if shuffle == self.shuffle:
            return
        self.shuffle = shuffle
        current = self.show.id if self.show and self.state != IDLE else None
        if shuffle:
            rest = [i for i in self.order if i != current]
            random.shuffle(rest)
            self.queue = ([current] if current else []) + rest
        else:
            self.queue = list(self.order)
        self._index = self.queue.index(current) if current in self.queue else 0
        self._changed()

    async def async_set_repeat(self, repeat: str) -> None:
        self.repeat = repeat
        self._changed()

    def _next_index(self, auto: bool) -> int | None:
        if not self.queue:
            return None
        if auto and self.repeat == REPEAT_ONE:
            return self._index
        if self._index + 1 < len(self.queue):
            return self._index + 1
        if self.repeat == REPEAT_ALL:
            if self.shuffle:
                random.shuffle(self.queue)
            return 0
        return None

    # --- running ------------------------------------------------------------
    async def _speaker_call(self, service: str, **data: Any) -> None:
        if not self.speaker:
            return
        try:
            await self.hass.services.async_call(
                "media_player", service, {"entity_id": self.speaker, **data}, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s on %s failed: %s", service, self.speaker, err)

    async def _run(self, index: int) -> None:
        """Play shows from `index` until the queue ends or this task is cancelled."""
        try:
            while True:
                self._index = index
                self.show = self.shows.get(self.queue[index])
                self.state = PLAYING
                self._changed()
                if self.show is not None:
                    await self._play_one(self.show)
                nxt = self._next_index(auto=True)
                if nxt is None:
                    break
                self.show = self.shows.get(self.queue[nxt])  # "up next" while we wait
                self._index = nxt
                self._changed()
                await asyncio.sleep(self.gap)
                index = nxt
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Show queue stopped")
        self._sequencer().stop()
        self._with_speaker = False
        self.state = IDLE
        self._changed()

    async def _play_one(self, show: Show) -> None:
        try:
            seq: FseqFile = await self.hass.async_add_executor_job(load, show.fseq)
        except (FseqError, OSError) as err:
            _LOGGER.warning("Skipping %s: %s", show.id, err)
            return
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            ctl = getattr(entry, "runtime_data", None)  # the player's own entry has none
            if hasattr(ctl, "enable_show"):
                ctl.enable_show()
        sequencer = self._sequencer()
        url = await self._audio_url(show) if self.speaker and show.audio else None
        task = None
        if url is not None:
            self._with_speaker = True
            try:
                await play_with_speaker(self.hass, sequencer, seq, self.speaker, url, self.audio_offset)
                task = sequencer.current
            except HomeAssistantError as err:
                _LOGGER.warning("%s could not play %s, playing lights only: %s", self.speaker, show.id, err)
                self._with_speaker = False
        if task is None:
            task = sequencer.play(seq, delay=self.audio_offset)
        self._changed()
        if task is not None:
            await asyncio.wait({task})  # doesn't raise if the show was cancelled, e.g. the song ended
        self._with_speaker = False

    async def _audio_url(self, show: Show) -> str | None:
        try:
            play = await media_source.async_resolve_media(
                self.hass, self.library.media_source_id(show.audio), self.speaker)
            return async_process_play_media_url(self.hass, play.url)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("Can't serve %s to %s, playing lights only: %s", show.audio, self.speaker, err)
            return None


async def async_setup_player(hass: HomeAssistant, entry: ConfigEntry) -> ShowPlayer:
    """Create the player for its config entry and load the library."""
    source, base = media_root(hass)
    library = ShowLibrary(os.path.join(base, "xlights"), base, source)
    await hass.async_add_executor_job(os.makedirs, library.root, 0o777, True)
    player = ShowPlayer(hass, library)
    player.configure(entry)
    hass.data[SHOW_PLAYER_KEY] = player
    await player.async_refresh()
    return player


async def async_unload_player(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Stop playback and drop the player."""
    player: ShowPlayer | None = hass.data.pop(SHOW_PLAYER_KEY, None)
    if player is not None:
        await player.async_stop()
        player._listeners.clear()
