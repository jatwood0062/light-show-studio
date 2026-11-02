"""WLED lights: the JSON API for setup and power, DDP over UDP for realtime frames."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .controller import C9Controller
from .hosts import valid_ipv4
from .netutil import check, read_json
from .protocol import Colour

_LOGGER = logging.getLogger(__name__)

DDP_PORT = 4048
MAX_DATA = 1440  # data bytes per DDP packet (480 RGB pixels)
KEEPALIVE = 1.0  # WLED leaves realtime mode ~2.5 s after the last packet: resend the last frame
MAX_FPS = 40.0
TIMEOUT = aiohttp.ClientTimeout(total=5)
NET_ERRORS = (aiohttp.ClientError, TimeoutError, ValueError)


# --- pure helpers (no Home Assistant needed) -----------------------------------
def ddp_packets(colours: list[Colour], rgbw: bool = False) -> list[bytes]:
    """Colours -> DDP packets (RGB 8-bit). WLED converts to RGBW itself, so `rgbw` changes nothing.

    Header: flags (0x40 = version 1, +0x01 PUSH on the last packet only), sequence 0, data type 0x01,
    destination 0x01, byte offset (uint32 BE), data length (uint16 BE).
    """
    data = b"".join(bytes(c) for c in colours)
    offsets = list(range(0, max(len(data), 1), MAX_DATA))
    packets = []
    for n, off in enumerate(offsets):
        chunk = data[off : off + MAX_DATA]
        flags = 0x41 if n == len(offsets) - 1 else 0x40
        packets.append(struct.pack(">BBBBIH", flags, 0, 0x01, 0x01, off, len(chunk)) + chunk)
    return packets


def format_mac(raw: str) -> str:
    """'aabbccddeeff' -> 'aa:bb:cc:dd:ee:ff'."""
    mac = str(raw).replace(":", "").replace("-", "").lower()
    return ":".join(mac[i : i + 2] for i in range(0, len(mac), 2))


# --- network ---------------------------------------------------------------------
async def async_info(session: aiohttp.ClientSession, ip: str) -> dict[str, Any]:
    """The device's /json/info (name, ver, leds, mac, arch...). Raises."""
    ip = valid_ipv4(ip)
    async with session.get(f"http://{ip}/json/info", timeout=aiohttp.ClientTimeout(total=3),
                           allow_redirects=False) as resp:
        check(resp)
        data = await read_json(resp)
    if not isinstance(data, dict) or "leds" not in data:
        raise ValueError("not a WLED device")
    return data


class WledController(C9Controller):
    def __init__(self, hass: HomeAssistant, host: str, bulbs: int, rgbw: bool = False) -> None:
        super().__init__(hass, host, bulbs, True)  # always takes a frame per LED
        self.rgbw = rgbw
        self.session = async_get_clientsession(hass)
        self._closed = False
        self._on = False
        self._last: list[Colour] | None = None
        self._last_sent = 0.0
        self._pending: asyncio.TimerHandle | None = None
        self._keepalive: asyncio.TimerHandle | None = None
        self._power_task: asyncio.Task | None = None
        self._state_lock = asyncio.Lock()  # an "off" and a quick "on" must land in order

    def _make_link(self, host: str) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    # --- hooks ---------------------------------------------------------------
    def _hw_power(self, on: bool) -> None:
        if self._closed:
            return
        self._on = on
        if on:
            body: dict[str, Any] = {"on": True, "bri": 255}  # WLED's own dimmer would dim every frame
        else:
            self._stop_streaming()
            body = {"on": False}
        self._power_task = self.hass.async_create_background_task(self._post_state(body), "light_show_studio wled power")

    def _hw_frame(self, frame: list[Colour]) -> None:
        if self._closed:
            return
        self._last = frame
        wait = 1 / MAX_FPS - (time.monotonic() - self._last_sent)
        if wait > 0:  # faster than the cap: send the latest frame when it's due
            if self._pending is None:
                self._pending = self.hass.loop.call_later(wait, self._send_pending)
            return
        self._send(frame)
        if self._keepalive is None:
            self._keepalive = self.hass.loop.call_later(KEEPALIVE, self._tick)

    def _hw_close(self) -> None:
        self._closed = True
        self._stop_streaming()
        if self._power_task:
            self._power_task.cancel()
            self._power_task = None
        self._sock.close()

    async def _hw_wait(self) -> None:
        if task := self._power_task:
            await asyncio.wait([task], timeout=3)

    # --- streaming ---------------------------------------------------------------
    def _send(self, frame: list[Colour]) -> None:
        self._last_sent = time.monotonic()
        for pkt in ddp_packets(frame, self.rgbw):
            try:
                self._sock.sendto(pkt, (self.host, DDP_PORT))
            except OSError:
                return  # UDP: the next frame replaces it

    def _send_pending(self) -> None:
        self._pending = None
        if not self._closed and self._on and self._last is not None:
            self._send(self._last)

    def _stop_streaming(self) -> None:
        for handle in (self._pending, self._keepalive):
            if handle:
                handle.cancel()
        self._pending = self._keepalive = None
        self._last = None

    def _tick(self) -> None:
        self._keepalive = None
        if self._closed or self.show_mode or not (self._on and self._last is not None):  # shows stream their own frames; idle, the strip goes back to its own look
            return
        if time.monotonic() - self._last_sent >= KEEPALIVE - 0.1:
            self._send(self._last)
        self._keepalive = self.hass.loop.call_later(KEEPALIVE, self._tick)

    async def _post_state(self, body: dict[str, Any]) -> None:
        try:
            async with self._state_lock:
                async with self.session.post(f"http://{self.host}/json/state", json=body, timeout=TIMEOUT,
                                             allow_redirects=False) as resp:
                    check(resp)
        except NET_ERRORS as err:
            _LOGGER.debug("WLED %s: can't set %s: %s", self.host, body, err)
        except Exception:  # never let a background task raise
            _LOGGER.debug("WLED %s: state change failed", self.host, exc_info=True)
