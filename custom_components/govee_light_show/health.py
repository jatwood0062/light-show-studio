"""Is each light reachable? Checked every minute; also follows a light that got a new IP."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later

from .const import CONF_DEVICE_ID, CONF_TYPE, DOMAIN, TYPE_PLAYER, TYPE_TWINKLY
from .controller import C9Controller
from .protocol import async_probe, async_scan
from .twinkly import TwinklyController, async_discover, async_gestalt

_LOGGER = logging.getLogger(__name__)

INTERVAL = 60
FIRST_RUN = 10
GOVEE_MISSES = 3
TWINKLY_MISSES = 2


class HealthMonitor:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cancel: CALLBACK_TYPE | None = None
        self._stopped = False
        self._scan_ever_worked = False  # multicast may be blocked: then a silent scan proves nothing

    def start(self) -> None:
        self._stopped = False
        self._cancel = async_call_later(self.hass, FIRST_RUN, self._run)

    def stop(self) -> None:
        self._stopped = True
        if self._cancel:
            self._cancel()
            self._cancel = None

    async def _run(self, _now) -> None:
        try:
            await self.async_check()
        except Exception:  # never let the monitor die
            _LOGGER.debug("Health check failed", exc_info=True)
        if not self._stopped:
            self._cancel = async_call_later(self.hass, INTERVAL, self._run)

    async def async_check(self) -> None:
        govee: list[ConfigEntry] = []
        twinkly: list[ConfigEntry] = []
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            if entry.data.get(CONF_TYPE) == TYPE_PLAYER or not isinstance(
                    getattr(entry, "runtime_data", None), C9Controller):
                continue
            (twinkly if entry.data.get(CONF_TYPE) == TYPE_TWINKLY else govee).append(entry)
        if govee:
            await self._check_govee(govee)
        if twinkly:
            await self._check_twinkly(twinkly)

    def _move(self, entry: ConfigEntry, new_host: str) -> None:
        _LOGGER.info("%s moved from %s to %s", entry.title, entry.data.get(CONF_HOST), new_host)
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: new_host})

    async def _check_govee(self, entries: list[ConfigEntry]) -> None:
        found = await async_scan(2.0)
        _LOGGER.debug("Govee scan: %s", found)
        if found is None:  # couldn't scan (port held, e.g. by Govee light local): check each light directly
            seen = await async_probe([e.data[CONF_HOST] for e in entries])
            _LOGGER.debug("Govee probe: %s", seen)
            for entry in entries if seen is not None else []:
                if seen.get(entry.data[CONF_HOST]):
                    entry.runtime_data.mark_seen()
                else:
                    entry.runtime_data.mark_missed(GOVEE_MISSES)
            return
        if found:
            self._scan_ever_worked = True
        for entry in entries:
            ctl: C9Controller = entry.runtime_data
            host, device = entry.data[CONF_HOST], entry.data.get(CONF_DEVICE_ID)
            if host in found:
                ctl.mark_seen()
                if not device and (dev := found[host].get("device")):
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICE_ID: dev})
                continue
            if not self._scan_ever_worked:
                continue
            new = next((ip for ip, d in found.items() if device and d.get("device") == device), None)
            if new:
                self._move(entry, new)
            else:
                ctl.mark_missed(GOVEE_MISSES)

    async def _check_twinkly(self, entries: list[ConfigEntry]) -> None:
        session = async_get_clientsession(self.hass)
        missed: list[ConfigEntry] = []
        for entry in entries:
            ctl: TwinklyController = entry.runtime_data
            try:
                await async_gestalt(session, entry.data[CONF_HOST])
            except Exception as err:  # unreachable, or a reply we don't understand
                _LOGGER.debug("Twinkly %s not reachable: %s", entry.data[CONF_HOST], err)
                ctl.mark_missed(TWINKLY_MISSES)
                missed.append(entry)
                continue
            ctl.mark_seen()
            await ctl.async_check_mode()
        if not missed:
            return
        macs: dict[str, str] = {}  # mac -> ip, for whatever answers a broadcast
        for ip in await async_discover():
            try:
                info = await async_gestalt(session, ip)
            except Exception:
                continue
            macs[str(info.get("mac", "")).lower()] = ip
        for entry in missed:
            new = macs.get(str(entry.data.get(CONF_DEVICE_ID, "")).lower())
            if new and new != entry.data[CONF_HOST]:
                self._move(entry, new)
