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
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.media_player.browse_media import async_process_play_media_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import transitions
from .const import (
    CONF_AUDIO_OFFSET,
    CONF_GAP,
    CONF_SPEAKERS,
    CONF_TRANSITION,
    CONF_TRANSITION_EFFECT,
    DOMAIN,
    SCHEDULER_KEY,
    SEQUENCER_KEY,
)
from .controller import enable_shows
from .effects import EFFECT_LIST, EFFECTS
from .follow import play_with_speaker
from .fseq import FseqError, FseqFile, load
from .library import SHOW_PLAYER_KEY, Show, ShowLibrary, media_root
from .outputs import Outputs, SpeakerInfo, normalize_speakers, plan_outputs
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
        self._speakers: list[str] = []  # the chosen outputs, first = leader; read at the start of each show
        self._applied: list[str] = []  # the outputs the current show started with
        self._leader: str | None = None  # the speaker the current show's lights follow
        self._joined: list[str] = []  # members this player grouped (and so will ungroup)
        self._joined_leader: str | None = None
        self._warned: set[str] = set()
        self.audio_offset = 0.0
        self.gap = DEFAULT_GAP
        self.transition = transitions.DEFAULT_TRANSITION
        self.transition_effect = transitions.default_effect(EFFECT_LIST)
        self.shuffle = False
        self.repeat = REPEAT_OFF
        self.entity_id: str | None = None  # set by the media_player entity
        self.playlist_id: str | None = None  # None = "All shows"
        self.stop_reason = "user"  # why the player last went idle: "user", "ended" (queue over), "scheduler", ...
        self.shows: dict[str, Show] = {}
        self.order: list[str] = []  # library order
        self.queue: list[str] = []  # play order (shuffled or not)
        self.show: Show | None = None
        self.state = IDLE
        self._index = 0
        self._with_speaker = False  # the current show's song is on the speaker
        self._runner: asyncio.Task | None = None
        self._stop_speaker = False  # stop the last show's song before the next one starts
        self._listeners: list[Callable[[], None]] = []

    # --- setup ------------------------------------------------------------
    def configure(self, entry: ConfigEntry) -> None:
        def opt(key: str, default: Any) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        self._speakers = normalize_speakers(opt(CONF_SPEAKERS, []))
        self.audio_offset = float(opt(CONF_AUDIO_OFFSET, 0.0) or 0.0)
        gap = opt(CONF_GAP, DEFAULT_GAP)
        self.gap = DEFAULT_GAP if gap is None else float(gap)
        self.set_transition(opt(CONF_TRANSITION, None), opt(CONF_TRANSITION_EFFECT, None))

    # --- outputs ------------------------------------------------------------
    @property
    def speakers(self) -> list[str]:
        """The chosen outputs (a change made during a show counts at once; it plays from the next show)."""
        return list(self._speakers)

    @property
    def outputs(self) -> Outputs:
        return self._plan(self._speakers)

    @property
    def speaker(self) -> str | None:
        """The leader: the speaker the lights follow."""
        return self.outputs.leader

    def _plan(self, speakers: list[str]) -> Outputs:
        registry = er.async_get(self.hass)
        rows: list[SpeakerInfo] = []
        for entity_id in speakers:
            state = self.hass.states.get(entity_id)
            entry = registry.async_get(entity_id)
            features = int(state.attributes.get("supported_features") or 0) if state else 0
            rows.append((entity_id, entry.platform if entry else None,
                         bool(features & MediaPlayerEntityFeature.GROUPING),
                         state is not None and state.state != "unavailable"))
        return plan_outputs(rows)

    def set_speakers(self, speakers: list[str]) -> str:
        """Change the outputs in place. Returns "now", or "next_show" when a show is on (it keeps its speakers)."""
        new = normalize_speakers(speakers)
        if new != self._speakers:
            self._speakers = new
            self._warned.clear()
            self._changed()
        return "now" if self.state == IDLE or new == self._applied else "next_show"

    async def _prepare_outputs(self) -> None:
        """Before a show: take the chosen outputs, pick the leader and group the others with it."""
        self._applied = list(self._speakers)
        plan = self._plan(self._speakers)
        if self._joined and plan.leader != self._joined_leader:
            await self._ungroup()
        elif stale := [m for m in self._joined if m not in plan.members]:
            await self._ungroup(stale)
        self._leader = plan.leader
        for entity_id in plan.ungrouped:
            if entity_id not in self._warned:
                self._warned.add(entity_id)
                _LOGGER.warning("%s can't play in sync with %s (grouping unsupported or a different platform); skipping it",
                                entity_id, plan.leader)
        if plan.leader is None or not plan.members:
            return
        leader_state = self.hass.states.get(plan.leader)
        already = set(leader_state.attributes.get("group_members") or []) if leader_state else set()
        todo = [m for m in plan.members if m not in already]
        if not todo:
            return
        try:
            await self.hass.services.async_call(
                "media_player", "join", {"entity_id": plan.leader, "group_members": todo}, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not group %s with %s: %s", ", ".join(todo), plan.leader, err)
            return
        self._joined_leader = plan.leader
        self._joined.extend(m for m in todo if m not in self._joined)

    async def _ungroup(self, members: list[str] | None = None) -> None:
        """Take out of the group exactly the members this player joined (not ones grouped before)."""
        members = list(self._joined) if members is None else members
        self._joined = [m for m in self._joined if m not in members]
        if not self._joined:
            self._joined_leader = None
        if not members:
            return
        try:
            await self.hass.services.async_call("media_player", "unjoin", {"entity_id": members}, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not ungroup %s: %s", ", ".join(members), err)

    def set_transition(self, mode: str | None, effect: str | None = None) -> None:
        """Change the between-shows look in place (takes effect at the next gap); bad values are ignored."""
        self.transition = mode if mode in transitions.MODES else transitions.DEFAULT_TRANSITION
        if effect in EFFECTS:
            self.transition_effect = effect
        elif self.transition_effect not in EFFECTS:
            self.transition_effect = transitions.default_effect(EFFECT_LIST)
        self._changed()

    def _sequencer(self) -> SequencePlayer:
        """The shared SequencePlayer (made in async_setup), so play_sequence / stop_sequence act on the same one."""
        return self.hass.data[SEQUENCER_KEY]

    async def async_refresh(self) -> None:
        """Re-read the library (after an upload, delete or reorder) and rebuild the queue."""
        shows = await self.hass.async_add_executor_job(self.library.scan)
        self.shows = {s.id: s for s in shows}
        self.order = [s.id for s in shows]
        current = self.show.id if self.show else None
        base = self._base_ids()
        if self.shuffle:
            kept = [i for i in self.queue if i in base]
            self.queue = kept + [i for i in base if i not in kept]
        else:
            self.queue = list(base)
        if current in self.queue:
            self._index = self.queue.index(current)
        elif self.state == IDLE:
            self.show, self._index = None, 0
        self._changed()

    # --- playlists ----------------------------------------------------------
    def _scheduler(self):
        return self.hass.data.get(SCHEDULER_KEY)

    def _base_ids(self) -> list[str]:
        """The shows the queue is made of: the active playlist's shows that still exist, in its order, or all shows."""
        if self.playlist_id is not None and (sched := self._scheduler()) is not None:
            if (playlist := sched.get_playlist(self.playlist_id)) is not None:
                return [i for i in playlist["show_ids"] if i in self.shows]
            self.playlist_id = None  # it was deleted: back to all shows
        return list(self.order)

    @property
    def playlist_name(self) -> str | None:
        if self.playlist_id is not None and (sched := self._scheduler()) is not None:
            if (playlist := sched.get_playlist(self.playlist_id)) is not None:
                return playlist["name"]
        return None

    def _rebuild_queue(self) -> None:
        base = self._base_ids()
        current = self.show.id if self.show and self.state != IDLE else None
        if self.shuffle:
            rest = [i for i in base if i != current]
            random.shuffle(rest)
            self.queue = ([current] if current in base else []) + rest
        else:
            self.queue = list(base)
        self._index = self.queue.index(current) if current in self.queue else 0

    async def async_set_playlist(self, playlist_id: str | None, remember: bool = True) -> None:
        """Play from a playlist (None = all shows). A show that is playing and isn't in it stops."""
        sched = self._scheduler()
        if playlist_id is not None and (sched is None or sched.get_playlist(playlist_id) is None):
            raise HomeAssistantError(f"No playlist {playlist_id}")
        self.playlist_id = playlist_id
        if not self.shows:
            await self.async_refresh()
        self._rebuild_queue()
        if self.state != IDLE and (self.show is None or self.show.id not in self.queue):
            await self.async_stop(reason="playlist")
        if remember and sched is not None:
            sched.set_active_playlist(playlist_id)
        self._changed()

    def playlists_changed(self) -> None:
        """A playlist was edited or deleted: rebuild the queue if it is the active one."""
        if self.playlist_id is not None:
            self._rebuild_queue()  # (a deleted active playlist falls back to all shows in _base_ids)
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
            "speaker": (out := self.outputs).leader,
            "speakers": self.speakers,
            "ungrouped": list(out.ungrouped),
            "transition": self.transition,
            "transition_effect": self.transition_effect,
            "queue": list(self.queue),
            "playlist_id": self.playlist_id,
            "playlist": self.playlist_name,
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
        if show_id is None and self.state == PLAYING:
            return  # already playing: don't restart the current show
        if not self.shows or (show_id is not None and show_id not in self.shows):
            await self.async_refresh()
        if show_id is not None and show_id not in self.shows:
            raise HomeAssistantError(f"No show called {show_id}")
        if not self.queue:
            raise HomeAssistantError("There are no shows in this playlist" if self.playlist_id else "There are no shows in the library")
        if show_id is None:
            show_id = self.show.id if self.show and self.show.id in self.queue else self.queue[0]
        if self.shuffle and show_id in self.queue:  # the chosen show leads, the rest stay shuffled
            self.queue.remove(show_id)
            self.queue.insert(0, show_id)
        self._start(self.queue.index(show_id))

    def _start(self, index: int) -> None:
        self._cancel_runner()
        if self._with_speaker:  # the cancelled show's song would keep playing under the next one
            self._with_speaker = False
            self._stop_speaker = True
        self._index = index
        self.show = self.shows[self.queue[index]]
        self.state = PLAYING
        self._runner = self.hass.async_create_background_task(self._run(index), "light_show_studio show queue")
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

    async def async_stop(self, reason: str = "user") -> None:
        self.stop_reason = reason  # the scheduler tells a person's stop from its own and from a queue that ran out
        self._cancel_runner()
        await self._finish()

    async def _finish(self) -> None:
        """Go idle: lights dark, the song stopped, the speakers left as we found them."""
        was_speaker = self._with_speaker
        sequencer = self._sequencer()
        sequencer.hold_on_stop = False  # stopping, or the end of the queue, goes dark
        sequencer.stop()
        if (receiver := self.hass.data.get(DOMAIN)) is not None:
            receiver.show(b"")  # in case the last show held its frame
        self._with_speaker = False
        self.state = IDLE
        if was_speaker:
            await self._speaker_call("media_stop")
        await self._ungroup()
        self._leader = None
        self._changed()

    async def async_next(self) -> None:
        nxt = self._next_index(auto=False)
        if nxt is None:
            await self.async_stop(reason="ended")
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
        self._rebuild_queue()
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
        if not self._leader:
            return
        try:
            await self.hass.services.async_call(
                "media_player", service, {"entity_id": self._leader, **data}, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s on %s failed: %s", service, self._leader, err)

    async def _run(self, index: int) -> None:
        """Play shows from `index` until the queue ends or this task is cancelled."""
        fade_in = 0.0  # the first show starts straight away; later ones may fade in
        try:
            while True:
                if index >= len(self.queue):  # the library shrank during the gap: that was the end
                    if self.repeat != REPEAT_ALL or not self.queue:
                        break
                    index = 0
                self._index = index
                self.show = self.shows.get(self.queue[index])
                self.state = PLAYING
                self._changed()
                if self.show is not None:
                    await self._play_one(self.show, fade_in)
                fade_in = 0.0
                if self.show is not None and self.show.id in self.queue:  # refresh may have moved it
                    self._index = self.queue.index(self.show.id)
                nxt = self._next_index(auto=True)
                if nxt is None:
                    break
                self.show = self.shows.get(self.queue[nxt])  # "up next" while we wait
                self._index = nxt
                self._changed()
                fade_in = await self._between(self.show)
                index = self._index  # a refresh during the gap may have moved the queue
                if self.show is not None and self.show.id in self.queue:
                    index = self.queue.index(self.show.id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Show queue stopped")
        self.stop_reason = "ended"
        await self._finish()  # the queue is over

    async def _between(self, nxt: Show | None) -> float:
        """Fill the gap before `nxt`; returns the seconds the next show should fade in over.

        Never raises (bar cancellation): on any failure the rest of the gap is just waited out."""
        mode, gap = self.transition, self.gap
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        if mode == transitions.NONE or gap <= 0:
            await asyncio.sleep(max(0.0, gap))
            return 0.0
        try:
            await self._transition(mode, nxt, gap)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Transition failed; waiting out the gap")
        await asyncio.sleep(max(0.0, gap - (loop.time() - t0)))
        return transitions.FADE_IN if mode in (transitions.FADE, transitions.EFFECT) else 0.0

    async def _transition(self, mode: str, nxt: Show | None, gap: float) -> None:
        receiver: PixelReceiver | None = self.hass.data.get(DOMAIN)
        if receiver is None:
            return
        last = self._sequencer().last_frame or b""
        first = b""
        if mode == transitions.CROSSFADE and nxt is not None:
            try:
                first = (await self.hass.async_add_executor_job(self._first_frame, nxt.fseq)) or b""
            except (FseqError, OSError) as err:
                _LOGGER.debug("No first frame of %s to crossfade into: %s", nxt.id, err)
        effect_fn = EFFECTS.get(self.transition_effect)
        subs = receiver.subscriptions()
        size = max((start + length for start, length, _ in subs), default=0)
        loop = asyncio.get_running_loop()
        t0, step = loop.time(), 1 / transitions.FPS
        while (t := loop.time() - t0) < gap:
            k = t / gap
            if mode == transitions.FADE:
                buf: bytes | bytearray = transitions.fade(last, 1 - k)
            elif mode == transitions.CROSSFADE:
                buf = transitions.blend(last, first, k)
            elif effect_fn is not None:
                buf = transitions.effect_buffer(subs, effect_fn, t, transitions.TRANSITION_COLOUR, size)
            else:
                return
            receiver.show(buf)
            await asyncio.sleep(step)

    @staticmethod
    def _first_frame(path: str) -> bytes | None:
        seq = load(path)
        return seq.frame(0) if seq.frames else None

    async def _play_one(self, show: Show, fade_in: float = 0.0) -> None:
        self._with_speaker = False
        self._sequencer().hold_on_stop = self.transition != transitions.NONE  # the transition starts from its last frame
        if self._stop_speaker:  # here, not in _start, so it can't land after the new song began
            self._stop_speaker = False
            await self._speaker_call("media_stop")
        await self._prepare_outputs()
        try:
            seq: FseqFile = await self.hass.async_add_executor_job(load, show.fseq)
        except (FseqError, OSError) as err:
            _LOGGER.warning("Skipping %s: %s", show.id, err)
            return
        enable_shows(self.hass)
        sequencer = self._sequencer()
        try:
            sequencer.fade_in = fade_in  # taken by whichever play() starts the lights
            await self._start_lights(show, seq, sequencer)
        finally:
            sequencer.fade_in = 0.0
        self._with_speaker = False

    async def _start_lights(self, show: Show, seq: FseqFile, sequencer: SequencePlayer) -> None:
        url = await self._audio_url(show) if self._leader and show.audio else None
        task = None
        if url is not None:
            self._with_speaker = True
            try:
                await play_with_speaker(self.hass, sequencer, seq, self._leader, url, self.audio_offset)
                task = sequencer.current
            except HomeAssistantError as err:
                _LOGGER.warning("%s could not play %s, playing lights only: %s", self._leader, show.id, err)
                self._with_speaker = False
        if task is None:
            task = sequencer.play(seq, delay=self.audio_offset)
        self._changed()
        if task is not None:
            await asyncio.wait({task})  # doesn't raise if the show was cancelled, e.g. the song ended

    async def _audio_url(self, show: Show) -> str | None:
        try:
            play = await media_source.async_resolve_media(
                self.hass, self.library.media_source_id(show.audio), self._leader)
            return async_process_play_media_url(self.hass, play.url)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("Can't serve %s to %s, playing lights only: %s", show.audio, self._leader, err)
            return None


async def async_setup_player(hass: HomeAssistant, entry: ConfigEntry) -> ShowPlayer:
    """Create the player for its config entry and load the library."""
    source, base = media_root(hass)
    library = ShowLibrary(os.path.join(base, "xlights"), base, source)
    await hass.async_add_executor_job(os.makedirs, library.root, 0o777, True)
    player = ShowPlayer(hass, library)
    player.configure(entry)
    if (sched := hass.data.get(SCHEDULER_KEY)) is not None:
        player.playlist_id = sched.active_playlist if sched.get_playlist(sched.active_playlist or "") else None
    hass.data[SHOW_PLAYER_KEY] = player
    await player.async_refresh()
    if (sched := hass.data.get(SCHEDULER_KEY)) is not None:
        sched.async_player_ready()
    return player


async def async_unload_player(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Stop playback and drop the player."""
    player: ShowPlayer | None = hass.data.pop(SHOW_PLAYER_KEY, None)
    if player is not None:
        await player.async_stop(reason="unload")
        player._listeners.clear()
