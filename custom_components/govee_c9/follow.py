"""Starts the song on a media player and keeps the sequence on the player's position."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .fseq import FseqFile
from .playback import SequencePlayer, Timeline, reported_position

_LOGGER = logging.getLogger(__name__)

START_TIMEOUT = 15  # Music Assistant / Sonos can take a few seconds to start
TRACK_ATTRS = ("media_content_id", "media_title")
RUNNING = ("playing", "paused", "buffering")


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
