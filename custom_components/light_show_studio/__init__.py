"""Light Show Studio: per-pixel light shows on Govee, Twinkly and WLED lights, locally."""
from __future__ import annotations

import os

import voluptuous as vol

from homeassistant.components import panel_custom
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_BULBS,
    CONF_DEVICE_ID,
    CONF_LED_PROFILE,
    CONF_SEGMENTED,
    CONF_SPEAKER,
    CONF_SPEAKERS,
    CONF_START_PIXEL,
    CONF_TRANSITION,
    CONF_TRANSITION_EFFECT,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    PLAYER,
    TYPE_PLAYER,
    TYPE_TWINKLY,
    TYPE_WLED,
)
from .controller import C9Controller
from .follow import play_with_speaker
from .fseq import FseqError, FseqFile, load
from .health import HealthMonitor
from .library import SHOW_PLAYER_KEY
from .outputs import configured_speakers
from .playback import SequencePlayer
from . import upload, websocket, ws_shows
from .receiver import PixelReceiver
from .showplayer import async_setup_player, async_unload_player
from .twinkly import TwinklyController, async_gestalt
from .wled import WledController, async_info

PLATFORMS = [Platform.LIGHT]
PLAYER_PLATFORMS = [Platform.MEDIA_PLAYER]
FRONTEND_URL = "/light_show_studio"
CARDS = ("light-show-studio-card.js", "light-show-studio-player-card.js")
PANEL = "light-show-studio-panel"  # the "Light Shows" sidebar app
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

C9ConfigEntry = ConfigEntry[C9Controller]
HEALTH = f"{DOMAIN}_health"  # hass.data key of the HealthMonitor

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
    await panel_custom.async_register_panel(
        hass, frontend_url_path="light-shows", webcomponent_name=PANEL, sidebar_title="Light Shows",
        sidebar_icon="mdi:string-lights", module_url=f"{FRONTEND_URL}/{PANEL}.js?v={integration.version}",
        require_admin=False)
    hass.data.setdefault(DOMAIN, PixelReceiver())  # before any entry, so the show player can use it
    websocket.async_register(hass)
    ws_shows.async_register(hass)
    upload.async_register(hass)
    health = hass.data[HEALTH] = HealthMonitor(hass)
    health.start()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: health.stop())

    async def play(call: ServiceCall) -> None:
        receiver: PixelReceiver | None = hass.data.get(DOMAIN)
        if receiver is None:
            raise ServiceValidationError("No lights are set up")
        path = await hass.async_add_executor_job(_resolve, hass, call.data["file"])
        try:
            seq: FseqFile = await hass.async_add_executor_job(load, path)
        except (FseqError, OSError) as err:
            raise HomeAssistantError(f"Can't play {path}: {err}") from err

        player: SequencePlayer | None = hass.data.get(PLAYER)
        if player is None:
            player = hass.data[PLAYER] = SequencePlayer(
                receiver.show, lambda coro: hass.async_create_background_task(coro, "light_show_studio sequence"))
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
        if shows := hass.data.get(SHOW_PLAYER_KEY):
            await shows.async_stop()

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
        entry.async_on_unload(entry.add_update_listener(_player_listener(entry)))
        return True
    bulbs = _opt(entry, CONF_BULBS, DEFAULT_BULBS)
    if entry.data.get(CONF_TYPE) == TYPE_TWINKLY:
        try:
            info = await async_gestalt(async_get_clientsession(hass), entry.data[CONF_HOST])
        except Exception as err:  # unreachable now; Home Assistant retries
            raise ConfigEntryNotReady(f"Twinkly at {entry.data[CONF_HOST]} is not reachable: {err}") from err
        ctl = entry.runtime_data = TwinklyController(hass, entry.data[CONF_HOST], bulbs,
                                                     entry.data.get(CONF_LED_PROFILE, "RGB"), info.get("frame_rate"))
        ctl.mark_seen()
    elif entry.data.get(CONF_TYPE) == TYPE_WLED:
        try:
            info = await async_info(async_get_clientsession(hass), entry.data[CONF_HOST])
        except Exception as err:  # unreachable now; Home Assistant retries
            raise ConfigEntryNotReady(f"WLED at {entry.data[CONF_HOST]} is not reachable: {err}") from err
        ctl = entry.runtime_data = WledController(hass, entry.data[CONF_HOST], bulbs,
                                                  bool(info.get("leds", {}).get("rgbw")))
        ctl.mark_seen()
    else:
        ctl = entry.runtime_data = C9Controller(hass, entry.data[CONF_HOST], bulbs, _opt(entry, CONF_SEGMENTED, True))
    receiver: PixelReceiver = hass.data.setdefault(DOMAIN, PixelReceiver())
    await receiver.subscribe(entry.entry_id, _opt(entry, CONF_START_PIXEL, 1), ctl.bulbs, ctl.show_external)
    entry.async_on_unload(lambda: receiver.unsubscribe(entry.entry_id))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_change(entry)))
    return True


def _snapshot(entry: ConfigEntry) -> tuple:
    return ({k: v for k, v in entry.data.items() if k != CONF_DEVICE_ID}, dict(entry.options), entry.title)


def _reload_on_change(entry: ConfigEntry):
    """Update listener that reloads, except when only the remembered device id was filled in."""
    last = _snapshot(entry)

    async def listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        nonlocal last
        if (now := _snapshot(entry)) != last:
            last = now
            await _reload(hass, entry)

    return listener


def _player_listener(entry: ConfigEntry):
    """Update listener for the player: a changed between-shows transition or speaker list is applied
    to the running player in place (a reload would stop the show); any other change reloads."""
    def snap(e: ConfigEntry) -> tuple:
        skip = (CONF_TRANSITION, CONF_TRANSITION_EFFECT, CONF_SPEAKER, CONF_SPEAKERS)
        return (dict(e.data), {k: v for k, v in e.options.items() if k not in skip}, e.title)

    last = snap(entry)

    async def listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        nonlocal last
        if (now := snap(entry)) != last:
            last = now
            await _reload(hass, entry)
        elif (player := hass.data.get(SHOW_PLAYER_KEY)) is not None:
            player.set_transition(_opt(entry, CONF_TRANSITION, None), _opt(entry, CONF_TRANSITION_EFFECT, None))
            player.set_speakers(configured_speakers({k: _opt(entry, k, None) for k in (CONF_SPEAKERS, CONF_SPEAKER)}))

    return listener


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
