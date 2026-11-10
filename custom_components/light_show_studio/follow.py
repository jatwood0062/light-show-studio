"""Starts the song on a media player and keeps the sequence on the player's position."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .fseq import FseqFile
from .playback import SequencePlayer, Timeline, reported_position

_LOGGER = logging.getLogger(__name__)

START_TIMEOUT = 15  # Music Assistant / Sonos can take a few seconds to start
TRACK_ATTRS = ("media_content_id", "media_title")
RUNNING = ("playing", "paused", "buffering")
SHOW_MEDIA = "/xlights/"  # show songs are served from media/xlights
CLEAR_DELAY = 10


def _updated_at(state: State) -> datetime | None:
    v = state.attributes.get("media_position_updated_at")
    return dt_util.parse_datetime(v) if isinstance(v, str) else v


def _position(state: State, offset: float) -> float | None:
    pos = reported_position(state.attributes.get("media_position"), _updated_at(state),
                            dt_util.utcnow(), state.state == "playing")
    return None if pos is None else pos - offset


async def play_with_speaker(hass: HomeAssistant, player: SequencePlayer, seq: FseqFile,
                            entity_id: str, audio: str, offset: float) -> None:
    """Play `audio` on `entity_id`, then run `seq` on the speaker's clock until it stops."""
    if not entity_id.startswith("media_player.") or hass.states.get(entity_id) is None:
        raise ServiceValidationError(f"{entity_id} is not a media player that exists")
    player.stop(blank=False)
    asked = dt_util.utcnow()
    ready: asyncio.Future[State] = hass.loop.create_future()

    def started(s: State | None) -> bool:
        if s is None or s.state != "playing":
            return False
        return (_updated_at(s) or s.last_updated) >= asked  # the new song, not the old one

    @callback
    def waiting(event: Event[EventStateChangedData]) -> None:
        if started(s := event.data["new_state"]) and not ready.done():
            ready.set_result(s)

    unsub = async_track_state_change_event(hass, entity_id, waiting)
    try:
        await hass.services.async_call("media_player", "play_media", {
            ATTR_ENTITY_ID: entity_id, "media_content_id": audio, "media_content_type": "music",
        }, blocking=True)
        state = hass.states.get(entity_id)
        if not started(state):
            try:
                state = await asyncio.wait_for(ready, START_TIMEOUT)
            except TimeoutError:
                _LOGGER.warning("%s did not start playing within %ss; starting lights anyway",
                                entity_id, START_TIMEOUT)
                state = None
    finally:
        unsub()

    tl = Timeline()
    pos = _position(state, offset) if state else None
    if pos is None:
        _LOGGER.info("%s reports no position; lights run on a fixed offset", entity_id)
    tl.set(-offset if pos is None else pos)
    track = {k: state.attributes[k] for k in TRACK_ATTRS if state and state.attributes.get(k) is not None}
    task = player.play(seq, timeline=tl, hold=state is not None)

    @callback
    def changed(event: Event[EventStateChangedData]) -> None:
        if player.current is not task:
            return
        s = event.data["new_state"]
        if s is None or s.state not in RUNNING or any(
            s.attributes.get(k) not in (None, v) for k, v in track.items()
        ):
            _LOGGER.debug("%s stopped or changed track; stopping sequence", entity_id)
            player.stop()
            return
        if s.state != "playing":
            tl.pause()
            return
        p = _position(s, offset)
        if p is None:
            if tl.paused:
                tl.resume()
        elif tl.paused:
            tl.resume(p)
        else:
            _LOGGER.debug("Sequence drift %.3fs", -tl.sync(p))

    if state is not None:
        unsub_follow = async_track_state_change_event(hass, entity_id, changed)
        task.add_done_callback(lambda _: unsub_follow())
    task.add_done_callback(lambda _: hass.async_create_background_task(
        clear_stale_queue(hass, entity_id), "light_show_studio clear speaker queue"))


def _ours(state: State | None) -> bool:
    """The speaker is stopped on a show song (its signed link expires, and Sonos keeps asking for it)."""
    return (state is not None and state.state not in ("playing", "buffering")
            and SHOW_MEDIA in str(state.attributes.get("media_content_id") or ""))


async def clear_stale_queue(hass: HomeAssistant, entity_id: str) -> None:
    """After a show, take its song off the speaker's queue once the speaker has stopped on it."""
    await asyncio.sleep(CLEAR_DELAY)  # a playlist's next show starts its own song in this time
    state = hass.states.get(entity_id)
    if not _ours(state) or not int(state.attributes.get("supported_features") or 0) & MediaPlayerEntityFeature.CLEAR_PLAYLIST:
        return
    try:
        await hass.services.async_call("media_player", "clear_playlist", {ATTR_ENTITY_ID: entity_id}, blocking=True)
    except Exception as err:  # noqa: BLE001  tidying up must never break playback
        _LOGGER.debug("Could not clear %s's queue: %s", entity_id, err)
