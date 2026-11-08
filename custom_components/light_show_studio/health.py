"""Is each light reachable? Checked every minute; also follows a light that got a new IP."""
from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import callback, CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later

from .const import CONF_DEVICE_ID, CONF_TYPE, DOMAIN, TYPE_PLAYER, TYPE_TWINKLY, TYPE_WLED
from .controller import C9Controller
from .hosts import host_taken, is_lan_ipv4
from .protocol import async_probe, async_scan
from .twinkly import TwinklyController, async_discover, async_gestalt
from .wled import async_info

_LOGGER = logging.getLogger(__name__)

INTERVAL = 60
FIRST_RUN = 10
GOVEE_MISSES = 3
TWINKLY_MISSES = 2
WLED_MISSES = 2
MAX_GESTALT_PROBES = 16  # discovery replies followed up per check


def record_latency(ctl: object, ms: float) -> None:
    """Keep how long the light takes to answer a check: `latency_ms` (smoothed) and `latency_peak_ms` (a recent worst,
    decaying). The camera scan reads them to size its hold time (scan_codes.hold_ms)."""
    old = getattr(ctl, "latency_ms", None)
    peak = getattr(ctl, "latency_peak_ms", None) or 0.0
    try:
        ctl.latency_ms = ms if old is None else 0.7 * old + 0.3 * ms
        ctl.latency_peak_ms = max(ms, peak * 0.85)
    except AttributeError:
        pass


class HealthMonitor:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cancel: CALLBACK_TYPE | None = None
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._cancel = async_call_later(self.hass, FIRST_RUN, self._tick)

    def stop(self) -> None:
        self._stopped = True
        if self._cancel:
            self._cancel()
            self._cancel = None

    @callback
    def _tick(self, _now) -> None:
        # a background task, so Home Assistant's startup never waits on a check
        self._cancel = None
        self.hass.async_create_background_task(self._run(), "light_show_studio health check")

    async def _run(self) -> None:
        try:
            await self.async_check()
        except Exception:  # never let the monitor die
            _LOGGER.debug("Health check failed", exc_info=True)
        if not self._stopped:
            self._cancel = async_call_later(self.hass, INTERVAL, self._tick)

    async def async_check(self) -> None:
        govee: list[ConfigEntry] = []
        twinkly: list[ConfigEntry] = []
        wled: list[ConfigEntry] = []
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            if entry.data.get(CONF_TYPE) == TYPE_PLAYER or not isinstance(
                    getattr(entry, "runtime_data", None), C9Controller):
                continue
            kind = entry.data.get(CONF_TYPE)
            (twinkly if kind == TYPE_TWINKLY else wled if kind == TYPE_WLED else govee).append(entry)
        if govee:
            await self._check_govee(govee)
        if twinkly:
            await self._check_twinkly(twinkly)
        if wled:
            await self._check_wled(wled)

    def _move(self, entry: ConfigEntry, new_host: str) -> None:
        if not is_lan_ipv4(new_host):  # the address came off the network: never follow one to the internet
            return
        others = [e.data.get(CONF_HOST) for e in self.hass.config_entries.async_entries(DOMAIN)
                  if e.entry_id != entry.entry_id]
        if host_taken(new_host, others):  # another light lives there: never merge two entries onto one address
            _LOGGER.debug("%s not moved to %s: another light uses it", entry.title, new_host)
            return
        _LOGGER.warning("%s moved from %s to %s", entry.title, entry.data.get(CONF_HOST), new_host)
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: new_host})

    async def _check_govee(self, entries: list[ConfigEntry]) -> None:
        # the scan's replies can go to another listener on the same port (e.g. Govee light local),
        # so a light it doesn't hear is checked directly before it counts as missing
        found = await async_scan(2.0) or {}
        _LOGGER.debug("Govee scan: %s", found)
        unheard = [e for e in entries if e.data[CONF_HOST] not in found]
        probed = await async_probe([e.data[CONF_HOST] for e in unheard]) if unheard else {}
        _LOGGER.debug("Govee probe: %s", probed)
        for entry in entries:
            ctl: C9Controller = entry.runtime_data
            host, device = entry.data[CONF_HOST], entry.data.get(CONF_DEVICE_ID)
            if host in found:
                ctl.mark_seen()
                if not device and (dev := found[host].get("device")):
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICE_ID: dev})
                continue
            new = next((ip for ip, d in found.items() if device and d.get("device") == device), None)
            if new:
                self._move(entry, new)
            elif probed is None:  # no way to tell (no ARP table): leave it as it is
                continue
            elif probed.get(host):
                ctl.mark_seen()
            else:
                ctl.mark_missed(GOVEE_MISSES)

    async def _check_wled(self, entries: list[ConfigEntry]) -> None:
        session = async_get_clientsession(self.hass)
        for entry in entries:
            t0 = time.monotonic()
            try:
                await async_info(session, entry.data[CONF_HOST])
            except Exception as err:  # unreachable, or a reply we don't understand
                _LOGGER.debug("WLED %s not reachable: %s", entry.data[CONF_HOST], err)
                entry.runtime_data.mark_missed(WLED_MISSES)
            else:
                record_latency(entry.runtime_data, (time.monotonic() - t0) * 1000)
                entry.runtime_data.mark_seen()

    async def _check_twinkly(self, entries: list[ConfigEntry]) -> None:
        session = async_get_clientsession(self.hass)
        missed: list[ConfigEntry] = []
        for entry in entries:
            ctl: TwinklyController = entry.runtime_data
            t0 = time.monotonic()
            try:
                await async_gestalt(session, entry.data[CONF_HOST])
            except Exception as err:  # unreachable, or a reply we don't understand
                _LOGGER.debug("Twinkly %s not reachable: %s", entry.data[CONF_HOST], err)
                ctl.mark_missed(TWINKLY_MISSES)
                missed.append(entry)
                continue
            record_latency(ctl, (time.monotonic() - t0) * 1000)
            ctl.mark_seen()
            await ctl.async_check_mode()
        if not missed:
            return
        macs: dict[str, str] = {}  # mac -> ip, for whatever answers a broadcast
        for ip in list((await async_discover()))[:MAX_GESTALT_PROBES]:
            try:
                info = await async_gestalt(session, ip)
            except Exception:
                continue
            macs[str(info.get("mac", "")).lower()] = ip
        for entry in missed:
            new = macs.get(str(entry.data.get(CONF_DEVICE_ID, "")).lower())
            if new and new != entry.data[CONF_HOST]:
                self._move(entry, new)
