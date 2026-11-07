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
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_BULBS,
    CONF_DEVICE_ID,
    CONF_LED_PROFILE,
    CONF_SEGMENTED,
    CONF_SPEAKERS,
    CONF_START_PIXEL,
    CONF_TRANSITION,
    CONF_TRANSITION_EFFECT,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    MAP_KEY,
    SEQUENCER_KEY,
    TYPE_PLAYER,
    TYPE_TWINKLY,
    TYPE_WLED,
)
from .controller import C9Controller, enable_shows
from .follow import play_with_speaker
from .fseq import FseqError, FseqFile, load
from .health import HealthMonitor
from .hosts import valid_ipv4
from .library import FSEQ_EXT, SHOW_PLAYER_KEY
from .mapmanager import MapManager
from .outputs import normalize_speakers
from .playback import SequencePlayer
from . import patterns, upload, websocket, ws_map, ws_schedule, ws_shows
from .receiver import PixelReceiver
from .scheduler import async_setup_scheduler
from .showplayer import async_setup_player, async_unload_player
from .twinkly import TwinklyController, async_gestalt
from .wled import WledController, async_info

PLATFORMS = [Platform.LIGHT]
PLAYER_PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SWITCH, Platform.SENSOR]
FRONTEND_URL = "/light_show_studio"
CARDS = ("light-show-studio-card.js", "light-show-studio-player-card.js")
PANEL = "light-show-studio-panel"  # the "Light Shows" sidebar app
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

C9ConfigEntry = ConfigEntry[C9Controller]
HEALTH = f"{DOMAIN}_health"  # hass.data key of the HealthMonitor

def _no_nul(value: str) -> str:
    if "\0" in value:
        raise vol.Invalid("invalid characters")
    return value


PLAY_SCHEMA = vol.Schema({
    vol.Required("file"): vol.All(cv.string, vol.Length(min=1, max=512), _no_nul),
    vol.Optional("loop", default=False): cv.boolean,
    vol.Optional("media_player"): cv.entity_id,
    vol.Optional("audio"): vol.All(cv.string, vol.Length(max=2048)),
    vol.Optional("audio_offset", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=-3600, max=3600)),
})
_NAME = vol.All(cv.string, vol.Strip, vol.Length(min=1, max=64))
START_ZONE_SCHEMA = vol.All(vol.Schema({
    vol.Exclusive("zone", "target"): _NAME,  # a zone's name or id
    vol.Exclusive("area", "target"): _NAME,  # or an area's name or id: every run in it, no saved zone needed
    vol.Optional("pattern"): vol.In(patterns.PATTERN_LIST),
    vol.Optional("effect"): vol.In(patterns.PATTERN_EFFECTS),
    vol.Optional("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
}), cv.has_at_least_one_key("zone", "area"))
STOP_ZONE_SCHEMA = vol.All(vol.Schema({vol.Exclusive("zone", "target"): _NAME, vol.Exclusive("area", "target"): _NAME}),
                           cv.has_at_least_one_key("zone", "area"))


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
    receiver: PixelReceiver = hass.data.setdefault(DOMAIN, PixelReceiver(hass))  # before any entry, so the show player can use it
    # the one SequencePlayer: play_sequence, stop_sequence and the show player all drive this one
    sequencer = hass.data[SEQUENCER_KEY] = SequencePlayer(
        receiver.show, lambda coro: hass.async_create_background_task(coro, "light_show_studio sequence"))
    websocket.async_register(hass)
    ws_shows.async_register(hass)
    ws_schedule.async_register(hass)
    ws_map.async_register(hass)
    scheduler = await async_setup_scheduler(hass)  # playlists + schedules, loaded once
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: scheduler.async_stop_timers())
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: receiver.close())
    zones = hass.data[MAP_KEY] = MapManager(hass)  # zones across lights; the map is read now, zones start on request
    await zones.async_load()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: zones.async_stop_all())
    upload.async_register(hass)
    health = hass.data[HEALTH] = HealthMonitor(hass)
    health.start()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: health.stop())

    async def play(call: ServiceCall) -> None:
        path = await hass.async_add_executor_job(_resolve, hass, call.data["file"])
        try:
            seq: FseqFile = await hass.async_add_executor_job(load, path)
        except (FseqError, OSError) as err:
            raise HomeAssistantError(f"Can't play {path}: {err}") from err

        enable_shows(hass)
        speaker, audio = call.data.get("media_player"), call.data.get("audio")
        if speaker and audio:
            await play_with_speaker(hass, sequencer, seq, speaker, audio, call.data["audio_offset"])
        else:
            sequencer.play(seq, call.data["loop"], delay=call.data["audio_offset"])

    async def stop(call: ServiceCall) -> None:
        sequencer.stop()
        if shows := hass.data.get(SHOW_PLAYER_KEY):
            await shows.async_stop()

    async def start_zone(call: ServiceCall) -> None:
        if "area" in call.data:
            await zones.async_start_area(call.data["area"], call.data.get("pattern"), call.data.get("effect"),
                                         call.data.get("speed"))
            return
        await zones.async_start_zone(call.data["zone"], call.data.get("pattern"), call.data.get("effect"),
                                     call.data.get("speed"))

    async def stop_zone(call: ServiceCall) -> None:
        if "area" in call.data:
            await zones.async_stop_area(call.data["area"])
            return
        await zones.async_stop_zone(call.data["zone"])

    hass.services.async_register(DOMAIN, "start_zone", start_zone, schema=START_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_zone", stop_zone, schema=STOP_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, "play_sequence", play, schema=PLAY_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_sequence", stop, schema=vol.Schema({}))
    return True


def _resolve(hass: HomeAssistant, name: str) -> str:
    """A name in <media>/xlights or /config/www/xlights, or an absolute path Home Assistant
    allows (allowlist_external_dirs). Always an existing .fseq file, as a real path (symlinks
    resolved and checked, so none can lead out of the folder). Blocking."""
    if not name.lower().endswith(FSEQ_EXT):
        raise ServiceValidationError(f"{name} is not an {FSEQ_EXT} file")
    if os.path.isabs(name):
        if not hass.config.is_allowed_path(name):
            raise ServiceValidationError(f"{name} is not in an allowed folder (allowlist_external_dirs)")
        path = os.path.realpath(name)
        if not hass.config.is_allowed_path(path) or not os.path.isfile(path):  # a symlink must not lead out
            raise ServiceValidationError(f"{name} is not an allowed file")
        return path
    dirs = [os.path.join(d, "xlights") for d in hass.config.media_dirs.values()]
    dirs.append(hass.config.path("www", "xlights"))
    for d in dirs:
        root = os.path.realpath(d)
        path = os.path.realpath(os.path.join(root, name))
        if path.startswith(root + os.sep) and os.path.isfile(path):
            return path
    raise ServiceValidationError(f"{name} not found in {', '.join(dirs)}")


_LEGACY_SPEAKER = "speaker"  # before version 1.2 the player had one speaker


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 -> 1.2: the player's single `speaker` became the `speakers` list."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        data, options = dict(entry.data), dict(entry.options)
        old = data.pop(_LEGACY_SPEAKER, None)
        old = options.pop(_LEGACY_SPEAKER, old)
        if old and CONF_SPEAKERS not in options:
            options[CONF_SPEAKERS] = normalize_speakers(old)
        hass.config_entries.async_update_entry(entry, data=data, options=options, minor_version=2)
    return True


def _opt(entry: ConfigEntry, key: str, default):
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: C9ConfigEntry) -> bool:
    if entry.data.get(CONF_TYPE) == TYPE_PLAYER:
        await async_setup_player(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLAYER_PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_reload_on_change(entry)))
        return True
    bulbs = _opt(entry, CONF_BULBS, DEFAULT_BULBS)
    try:  # a host edited in storage must still be a LAN address
        valid_ipv4(entry.data[CONF_HOST])
    except (KeyError, ValueError) as err:
        raise ConfigEntryError(f"{entry.data.get(CONF_HOST)!r} is not a private (LAN) IPv4 address") from err
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
    receiver: PixelReceiver = hass.data.setdefault(DOMAIN, PixelReceiver(hass))
    await receiver.subscribe(entry.entry_id, _opt(entry, CONF_START_PIXEL, 1), ctl.bulbs, ctl.show_external)
    entry.async_on_unload(lambda: receiver.unsubscribe(entry.entry_id))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_change(entry)))
    return True


# Changes the entry can make without a reload: a light's device id being filled in, and the player's
# speakers / transition, which the websocket commands have already applied to the running player.
_IN_PLACE_DATA = (CONF_DEVICE_ID,)
_IN_PLACE_OPTIONS = (CONF_SPEAKERS, CONF_TRANSITION, CONF_TRANSITION_EFFECT)


def _reload_on_change(entry: ConfigEntry):
    """Update listener: reload when anything but the in-place settings changed."""
    def snapshot(e: ConfigEntry) -> tuple:
        return ({k: v for k, v in e.data.items() if k not in _IN_PLACE_DATA},
                {k: v for k, v in e.options.items() if k not in _IN_PLACE_OPTIONS}, e.title)

    last = snapshot(entry)

    async def listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        nonlocal last
        if (now := snapshot(entry)) != last:
            last = now
            await _reload(hass, entry)

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
