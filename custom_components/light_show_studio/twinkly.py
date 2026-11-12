"""Twinkly lights: discovery, the HTTP API and realtime UDP streaming."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import socket
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .controller import C9Controller
from .hosts import is_lan_ipv4, valid_ipv4
from .netutil import check, read_json
from .protocol import Colour

_LOGGER = logging.getLogger(__name__)

DISCOVER_PORT = 5555
RT_PORT = 7777
FRAGMENT = 900  # data bytes per realtime packet
KEEPALIVE = 2.0  # resend the last frame this often so a still picture holds in realtime mode
TOKEN_MARGIN = 60  # log in again this long before the token expires
ARM_RETRY = 5.0
DEFAULT_FPS = 12.0  # what the strip can draw; frames sent faster tear between their two packets
TIMEOUT = aiohttp.ClientTimeout(total=5)


# --- pure helpers (no Home Assistant needed) -----------------------------------
def frame_bytes(colours: list[Colour], profile: str = "RGB") -> bytes:
    """Colours -> the LED data: R,G,B each, or W,R,G,B for RGBW strips (W = the grey part)."""
    if profile == "RGBW":
        out = bytearray()
        for r, g, b in colours:
            w = min(r, g, b)
            out += bytes((w, r - w, g - w, b - w))
        return bytes(out)
    return b"".join(bytes(c) for c in colours)


def build_packets(token: bytes, colours: list[Colour], profile: str = "RGB") -> list[bytes]:
    """Realtime v3 packets: 0x03, 8 token bytes, 0x00 0x00, fragment index, up to 900 data bytes."""
    data = frame_bytes(colours, profile)
    return [b"\x03" + token + b"\x00\x00" + bytes([i]) + data[off : off + FRAGMENT]
            for i, off in enumerate(range(0, max(len(data), 1), FRAGMENT))]


def flip_layout(coords: list[dict]) -> list[list[float]]:
    """Twinkly's y points up, the card draws top-down. A 3D layout keeps its z as a third value (the card
    ignores it; mapping uses it for depth)."""
    return [[round(float(c["x"]), 4), round(-float(c["y"]), 4)] + ([round(float(c["z"]), 4)] if "z" in c else [])
            for c in coords]


def parse_discovery(data: bytes) -> tuple[str, str] | None:
    """A discovery reply: the IP reversed (4 bytes), 'OK', the name, NUL -> (ip, name)."""
    if len(data) < 6 or data[4:6] != b"OK":
        return None
    ip = ".".join(str(b) for b in reversed(data[:4]))
    return ip, data[6:].split(b"\0")[0].decode(errors="replace")


# --- network ---------------------------------------------------------------------
async def async_discover(timeout: float = 2.5) -> dict[str, str]:
    """Broadcast to every Twinkly on the network: {ip: name}."""
    found: dict[str, str] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(b"\x01discover", ("255.255.255.255", DISCOVER_PORT))
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while (left := end - loop.time()) > 0:
            try:
                data, (src, _port) = await asyncio.wait_for(loop.sock_recvfrom(sock, 1024), left)
            except TimeoutError:
                break
            # the reply may only name the address it came from
            if (reply := parse_discovery(data)) and reply[0] == src and is_lan_ipv4(src) and len(found) < 64:
                found[src] = reply[1][:100]
    except OSError as err:
        _LOGGER.debug("Twinkly discovery failed: %s", err)
    finally:
        sock.close()
    return found


async def async_gestalt(session: aiohttp.ClientSession, ip: str) -> dict[str, Any]:
    """The device's description (name, product_code, number_of_led, led_profile, mac...). Raises."""
    ip = valid_ipv4(ip)
    async with session.get(f"http://{ip}/xled/v1/gestalt", timeout=aiohttp.ClientTimeout(total=3),
                           allow_redirects=False) as resp:
        check(resp)
        data = await read_json(resp)
    if not isinstance(data, dict):
        raise ValueError("not a Twinkly device")
    return data


class TwinklyClient:
    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self.session = session
        self.host = valid_ipv4(host)
        self._token = ""
        self._expires = 0.0  # monotonic
        self._lock = asyncio.Lock()

    @property
    def token_bytes(self) -> bytes:
        try:
            return base64.b64decode(self._token)
        except ValueError:
            return b""

    @property
    def token_expiring(self) -> bool:
        return not self._token or time.monotonic() > self._expires - TOKEN_MARGIN

    async def async_login(self) -> None:
        base = f"http://{self.host}/xled/v1"
        challenge = base64.b64encode(os.urandom(32)).decode()
        async with self.session.post(f"{base}/login", json={"challenge": challenge}, timeout=TIMEOUT,
                                     allow_redirects=False) as resp:
            check(resp)
            data = await read_json(resp)
        if not isinstance(data, dict):
            raise ValueError("unexpected login reply")
        token = data["authentication_token"]
        if not isinstance(token, str) or not 0 < len(token) <= 256:
            raise ValueError("unexpected login reply")
        async with self.session.post(f"{base}/verify", json={"challenge-response": data["challenge-response"]},
                                     headers={"X-Auth-Token": token}, timeout=TIMEOUT,
                                     allow_redirects=False) as resp:
            check(resp)
        try:
            lifetime = float(data.get("authentication_token_expires_in", 14400))
        except (TypeError, ValueError):
            lifetime = 14400.0
        if not 0 <= lifetime < 1e7:  # also rejects nan / inf
            lifetime = 14400.0
        self._token = token
        self._expires = time.monotonic() + lifetime

    async def _login_once(self) -> None:
        async with self._lock:
            await self.async_login()

    async def async_request(self, method: str, path: str, json: dict | None = None) -> dict[str, Any]:
        if self.token_expiring:
            await self._login_once()
        for attempt in (0, 1):
            async with self.session.request(
                    method, f"http://{self.host}/xled/v1/{path.lstrip('/')}", json=json, timeout=TIMEOUT,
                    headers={"X-Auth-Token": self._token, "Content-Type": "application/json"},
                    allow_redirects=False) as resp:
                if resp.status == 401 and attempt == 0:
                    pass
                else:
                    check(resp)
                    data = await read_json(resp)
                    return data if isinstance(data, dict) else {}
            await self._login_once()
        return {}

    async def async_set_mode(self, mode: str) -> None:
        await self.async_request("POST", "led/mode", {"mode": mode})

    async def async_get_mode(self) -> str | None:
        return (await self.async_request("GET", "led/mode")).get("mode")

    async def async_set_brightness(self, percent: int) -> None:
        await self.async_request("POST", "led/out/brightness", {"mode": "enabled", "type": "A", "value": percent})

    async def async_layout(self) -> list[list[float]] | None:
        """LED positions for the card, or None when the strip has no real layout."""
        try:
            data = await self.async_request("GET", "led/layout/full")
            if data.get("source") == "linear" or not data.get("coordinates"):
                return None
            return flip_layout(data["coordinates"])
        except (aiohttp.ClientError, TimeoutError, KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Twinkly layout at %s: %s", self.host, err)
            return None


class TwinklyController(C9Controller):
    UDP_PORT = RT_PORT
    THREADED = False  # sends are timed by loop callbacks (arming, rate cap, keep-alive): effects stay on the loop

    def __init__(self, hass: HomeAssistant, host: str, bulbs: int, profile: str = "RGB",
                 frame_rate: float | None = None) -> None:
        super().__init__(hass, host, bulbs, True)  # always takes a frame per LED
        self.profile = profile
        self.max_fps = max(5.0, min(30.0, float(frame_rate or DEFAULT_FPS)))  # the strip's own rate (gestalt)
        self._pending: asyncio.TimerHandle | None = None
        self.client = TwinklyClient(async_get_clientsession(hass), host)
        self.layout: list[list[float]] | None = None
        self._armed = False  # realtime mode is on and our token is the current one
        self._arm_task: asyncio.Task | None = None
        self._arm_after = 0.0
        self._last: list[Colour] | None = None
        self._last_sent = 0.0
        self._keepalive: asyncio.TimerHandle | None = None
        self._closed = False
        self._mode_lock = asyncio.Lock()  # an "off" and a quick "rt" must land in order
        hass.async_create_background_task(self._fetch_layout(), "light_show_studio twinkly layout")

    def _make_link(self, host: str) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    async def _fetch_layout(self) -> None:
        self.layout = await self.client.async_layout()

    # --- hooks ---------------------------------------------------------------
    def _hw_power(self, on: bool) -> None:
        if on:
            return  # the first frame switches realtime mode on
        self._disarm()
        self.hass.async_create_background_task(self._set_mode("off"), "light_show_studio twinkly off")

    def _hw_frame(self, frame: list[Colour]) -> None:
        if self._closed:
            return
        self._last = frame
        if not self._armed:
            self._start_arm()
            return
        wait = 1 / self.max_fps - (time.monotonic() - self._last_sent)
        if wait > 0:  # faster than the strip draws: send the latest frame when it's ready for one
            if self._pending is None:
                self._pending = self.hass.loop.call_later(wait, self._send_pending)
            return
        self._send(frame)
        if self._keepalive is None:
            self._keepalive = self.hass.loop.call_later(KEEPALIVE, self._tick)

    def _hw_close(self) -> None:
        self._closed = True
        self._disarm()
        self._sock.close()

    async def _hw_wait(self) -> None:
        if self._armed:
            return
        self._start_arm()
        if task := self._arm_task:
            await asyncio.wait([task], timeout=6)

    # --- realtime mode --------------------------------------------------------
    def _send(self, frame: list[Colour]) -> None:
        self._last_sent = time.monotonic()
        pkts = build_packets(self.client.token_bytes, frame, self.profile)
        if self.port is not None:  # the output thread sends them
            self.port.submit(pkts)
            self.stats.frame(True, self._last_sent)
            return
        for pkt in pkts:
            try:
                self._sock.sendto(pkt, (self.host, RT_PORT))
            except OSError:
                self.stats.frame(False, self._last_sent)
                return  # UDP: the next frame replaces it
        self.stats.frame(True, self._last_sent)

    def _send_pending(self) -> None:
        self._pending = None
        if self._armed and not self._closed and self._last is not None:
            self._send(self._last)

    def _disarm(self) -> None:
        if self._pending:
            self._pending.cancel()
            self._pending = None
        self._armed = False
        self._arm_after = 0.0
        self._last = None
        if self._arm_task:
            self._arm_task.cancel()
            self._arm_task = None
        if self._keepalive:
            self._keepalive.cancel()
            self._keepalive = None

    def _start_arm(self) -> None:
        if self._closed or self._arm_task or time.monotonic() < self._arm_after:
            return
        self._arm_task = self.hass.async_create_background_task(self._arm(), "light_show_studio twinkly arm")

    async def _arm(self) -> None:
        try:
            await self.client.async_login()
            await self.client.async_set_brightness(100)  # the strip's own dimmer would dim every frame
            async with self._mode_lock:
                await self.client.async_set_mode("rt")
            self._armed = True
            if self._last is not None:
                self._hw_frame(self._last)
        except Exception as err:  # noqa: BLE001  anything: back off rather than storm logins
            _LOGGER.debug("Twinkly %s: can't start realtime mode: %s", self.host, err)
            self._arm_after = time.monotonic() + ARM_RETRY
            if self._keepalive is None and not self._closed:  # a still picture sends no frames: retry from _tick
                self._keepalive = self.hass.loop.call_later(ARM_RETRY + 0.1, self._tick)
        finally:
            self._arm_task = None

    async def _set_mode(self, mode: str) -> None:
        try:
            async with self._mode_lock:
                await self.client.async_set_mode(mode)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Twinkly %s: can't set mode %s: %s", self.host, mode, err)
            self._arm_after = time.monotonic() + ARM_RETRY

    def _tick(self) -> None:
        self._keepalive = None
        if self._closed or not (self.is_on and self._last is not None) \
                or (self.show_mode and (self._wire_raw is None or not any(any(c) for c in self._last))):  # a held show picture is kept up; black lets go
            return
        if not self._armed or self.client.token_expiring:  # dropped out of realtime, or the token is due
            self._armed = False
            self._start_arm()  # it resends the last frame once armed
            self._keepalive = self.hass.loop.call_later(KEEPALIVE, self._tick)
            return
        if time.monotonic() - self._last_sent >= KEEPALIVE - 0.1:
            self._send(self._last)
        self._keepalive = self.hass.loop.call_later(KEEPALIVE, self._tick)

    async def async_check_mode(self) -> None:
        """Someone using the Twinkly app can drop us out of realtime mode: arm again on the next frame."""
        if not (self.is_on and self._armed):
            return
        try:
            if await self.client.async_get_mode() != "rt":
                self._armed = False
                self._start_arm()  # a still picture sends no new frame, so re-arm now (it resends the last one)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Twinkly %s: mode check failed: %s", self.host, err)
            self._arm_after = time.monotonic() + ARM_RETRY
