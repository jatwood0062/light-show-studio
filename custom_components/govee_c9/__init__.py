"""Govee C9: per-bulb local control of Govee string lights."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_BULBS, CONF_START_PIXEL, DEFAULT_BULBS, DOMAIN
from .controller import C9Controller
from .receiver import PixelReceiver

PLATFORMS = [Platform.LIGHT]

C9ConfigEntry = ConfigEntry[C9Controller]


def _opt(entry: ConfigEntry, key: str, default):
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: C9ConfigEntry) -> bool:
    ctl = entry.runtime_data = C9Controller(hass, entry.data[CONF_HOST], _opt(entry, CONF_BULBS, DEFAULT_BULBS))
    receiver: PixelReceiver = hass.data.setdefault(DOMAIN, PixelReceiver())
    await receiver.subscribe(entry.entry_id, _opt(entry, CONF_START_PIXEL, 1), ctl.bulbs, ctl.show_external)
    entry.async_on_unload(lambda: receiver.unsubscribe(entry.entry_id))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: C9ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: C9ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        entry.runtime_data.shutdown()
    return ok
