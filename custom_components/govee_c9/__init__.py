"""Govee C9: per-bulb local control of Govee string lights."""
from __future__ import annotations

import os

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_BULBS,
    CONF_SEGMENTED,
    CONF_START_PIXEL,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    PLAYER,
    TYPE_PLAYER,
)
from .controller import C9Controller
from .follow import play_with_speaker
from .fseq import FseqError, FseqFile, load
from .playback import SequencePlayer
from . import upload, websocket, ws_shows
from .receiver import PixelReceiver
from .showplayer import async_setup_player, async_unload_player

PLATFORMS = [Platform.LIGHT]
PLAYER_PLATFORMS = [Platform.MEDIA_PLAYER]
FRONTEND_URL = "/govee_c9"
CARDS = ("govee-c9-card.js", "govee-c9-player-card.js")
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

C9ConfigEntry = ConfigEntry[C9Controller]

PLAY_SCHEMA = vol.Schema({
    vol.Required("file"): cv.string,
    vol.Optional("loop", default=False): cv.boolean,
    vol.Optional("media_player"): cv.entity_id,
    vol.Optional("audio"): cv.string,
    vol.Optional("audio_offset", default=0.0): vol.Coerce(float),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # the dashboard cards ship with the integration: served from here and loaded on every dashboard
    frontend = os.path.join(os.path.dirname(__file__), "frontend")
    await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, frontend, False)])
    integration = await async_get_integration(hass, DOMAIN)
    for card in CARDS:  # the version in the URL makes browsers fetch new copies after an update
        add_extra_js_url(hass, f"{FRONTEND_URL}/{card}?v={integration.version}")
    hass.data.setdefault(DOMAIN, PixelReceiver())  # before any entry, so the show player can use it
    websocket.async_register(hass)
    ws_shows.async_register(hass)
    upload.async_register(hass)

    async def play(call: ServiceCall) -> None:
        receiver: PixelReceiver | None = hass.data.get(DOMAIN)
        if receiver is None:
            raise ServiceValidationError("No Govee C9 strings are set up")
        path = await hass.async_add_executor_job(_resolve, hass, call.data["file"])
        try:
            seq: FseqFile = await hass.async_add_executor_job(load, path)
        except (FseqError, OSError) as err:
            raise HomeAssistantError(f"Can't play {path}: {err}") from err

        player: SequencePlayer | None = hass.data.get(PLAYER)
        if player is None:
            player = hass.data[PLAYER] = SequencePlayer(
                receiver.show, lambda coro: hass.async_create_background_task(coro, "govee_c9 sequence"))
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if isinstance(getattr(entry, "runtime_data", None), C9Controller):
                entry.runtime_data.enable_show()
        speaker, audio = call.data.get("media_player"), call.data.get("audio")
        if speaker and audio:
            await play_with_speaker(hass, player, seq, speaker, audio, call.data["audio_offset"])
        else:
            player.play(seq, call.data["loop"], delay=call.data["audio_offset"])

    async def stop(call: ServiceCall) -> None:
        if player := hass.data.get(PLAYER):
            player.stop()

    hass.services.async_register(DOMAIN, "play_sequence", play, schema=PLAY_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_sequence", stop, schema=vol.Schema({}))
    return True


def _resolve(hass: HomeAssistant, name: str) -> str:
    """A name in <media>/xlights or /config/www/xlights, or an absolute path Home Assistant
    allows (allowlist_external_dirs). Blocking."""
    if os.path.isabs(name):
        if not hass.config.is_allowed_path(name):
            raise ServiceValidationError(f"{name} is not in an allowed folder (allowlist_external_dirs)")
        return name
    dirs = [os.path.join(d, "xlights") for d in hass.config.media_dirs.values()]
    dirs.append(hass.config.path("www", "xlights"))
    for d in dirs:
        path = os.path.realpath(os.path.join(d, name))
        if path.startswith(os.path.realpath(d) + os.sep) and os.path.isfile(path):
            return path
    raise ServiceValidationError(f"{name} not found in {', '.join(dirs)}")


def _opt(entry: ConfigEntry, key: str, default):
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: C9ConfigEntry) -> bool:
    if entry.data.get(CONF_TYPE) == TYPE_PLAYER:
        await async_setup_player(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLAYER_PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_reload))
        return True
    ctl = entry.runtime_data = C9Controller(hass, entry.data[CONF_HOST], _opt(entry, CONF_BULBS, DEFAULT_BULBS),
                                            _opt(entry, CONF_SEGMENTED, True))
    receiver: PixelReceiver = hass.data.setdefault(DOMAIN, PixelReceiver())
    await receiver.subscribe(entry.entry_id, _opt(entry, CONF_START_PIXEL, 1), ctl.bulbs, ctl.show_external)
    entry.async_on_unload(lambda: receiver.unsubscribe(entry.entry_id))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: C9ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: C9ConfigEntry) -> bool:
    if entry.data.get(CONF_TYPE) == TYPE_PLAYER:
        ok = await hass.config_entries.async_unload_platforms(entry, PLAYER_PLATFORMS)
        if ok:
            await async_unload_player(hass, entry)
        return ok
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        entry.runtime_data.shutdown()
    return ok
