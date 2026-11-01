"""The Light Show Player media player entity."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .library import SHOW_PLAYER_KEY
from .showplayer import IDLE, PAUSED, REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, ShowPlayer

SHOW_PREFIX = "govee_light_show_show://"
REPEAT_TO_HA = {REPEAT_OFF: RepeatMode.OFF, REPEAT_ALL: RepeatMode.ALL, REPEAT_ONE: RepeatMode.ONE}
HA_TO_REPEAT = {v: k for k, v in REPEAT_TO_HA.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if (player := hass.data.get(SHOW_PLAYER_KEY)) is not None:
        async_add_entities([ShowPlayerEntity(player)])


class ShowPlayerEntity(MediaPlayerEntity):
    _attr_should_poll = False
    _attr_name = "Light Show Player"
    _attr_unique_id = "govee_light_show_show_player"
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.SHUFFLE_SET
        | MediaPlayerEntityFeature.REPEAT_SET
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.SEEK
    )

    def __init__(self, player: ShowPlayer) -> None:
        self._player = player
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "show_player")}, name="Light Show Player",
                                            manufacturer="Govee Light Show")
        self._updated_at: datetime = dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        self._player.entity_id = self.entity_id
        self.async_on_remove(self._player.add_listener(self._changed))

    def _changed(self) -> None:
        self._updated_at = dt_util.utcnow()
        self.async_write_ha_state()

    # --- state --------------------------------------------------------------
    @property
    def state(self) -> MediaPlayerState:
        return {IDLE: MediaPlayerState.IDLE, PAUSED: MediaPlayerState.PAUSED}.get(
            self._player.state, MediaPlayerState.PLAYING)

    @property
    def media_title(self) -> str | None:
        return self._player.show.name if self._player.show else None

    @property
    def media_duration(self) -> int | None:
        d = self._player.duration
        return None if d is None else int(d)

    @property
    def media_position(self) -> int | None:
        return None if self._player.show is None or self._player.state == IDLE else int(self._player.position)

    @property
    def media_position_updated_at(self) -> datetime | None:
        return None if self.media_position is None else self._updated_at

    @property
    def source(self) -> str | None:
        return self.media_title

    @property
    def source_list(self) -> list[str]:
        return [self._player.shows[i].name for i in self._player.queue if i in self._player.shows]

    @property
    def shuffle(self) -> bool:
        return self._player.shuffle

    @property
    def repeat(self) -> RepeatMode:
        return REPEAT_TO_HA[self._player.repeat]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "show_id": self._player.show.id if self._player.show else None,
            "speaker": self._player.speaker,
            "queue": list(self._player.queue),
        }

    # --- controls -------------------------------------------------------------
    async def async_media_play(self) -> None:
        await self._player.async_play()

    async def async_media_pause(self) -> None:
        await self._player.async_pause()

    async def async_media_stop(self) -> None:
        await self._player.async_stop()

    async def async_media_next_track(self) -> None:
        await self._player.async_next()

    async def async_media_previous_track(self) -> None:
        await self._player.async_previous()

    async def async_media_seek(self, position: float) -> None:
        await self._player.async_seek(position)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._player.async_set_shuffle(shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._player.async_set_repeat(HA_TO_REPEAT[repeat])

    def _find(self, key: str) -> str:
        key = key.removeprefix(SHOW_PREFIX)
        for s in self._player.shows.values():
            if key in (s.id, s.name) or key.casefold() == s.name.casefold():
                return s.id
        raise ServiceValidationError(f"No show called {key}")

    async def async_select_source(self, source: str) -> None:
        await self._player.async_play(self._find(source))

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:
        if not self._player.shows:
            await self._player.async_refresh()
        await self._player.async_play(self._find(media_id))

    async def async_browse_media(self, media_content_type: MediaType | str | None = None,
                                 media_content_id: str | None = None) -> BrowseMedia:
        await self._player.async_refresh()
        children = [
            BrowseMedia(
                media_class=MediaClass.MUSIC,
                media_content_id=f"{SHOW_PREFIX}{s.id}",
                media_content_type=MediaType.MUSIC,
                title=s.name,
                can_play=True,
                can_expand=False,
            )
            for s in (self._player.shows[i] for i in self._player.order if i in self._player.shows)
        ]
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id="",
            media_content_type="",
            title="Light shows",
            can_play=False,
            can_expand=True,
            children=children,
        )
