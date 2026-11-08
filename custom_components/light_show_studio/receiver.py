"""Pixel-protocol receiver, so sequencers like xLights or Light-O-Rama can drive the strings.

Home Assistant appears to the sequencer as one pixel controller. It listens for DDP
(UDP 4048, what xLights uses for WLED-style controllers) and E1.31/sACN (UDP 5568, 510
channels per universe starting at universe 1). Each string takes its own slice of the
pixels, set by its start pixel: string 1 = pixels 1-50, string 2 = 51-100, ...

Security: the sockets exist only while a light is set up. The ports are bound on every interface
(the OS gives no portable way to do better), so each packet's *source* is checked instead: only
private / link-local IPv4 senders are accepted (so nothing arriving over Tailscale or from the
internet gets in), optionally only those on an allowlist; senders are rate limited; packets are
parsed strictly (see packets.py); and bursts of pushes are coalesced into one frame.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Callable

from .hosts import is_lan_ipv4, parse_sources, source_allowed
from .packets import BUF_SIZE, MAX_PIXELS, UNIVERSE_SIZE, UNIVERSES, RateLimiter, parse_ddp, parse_e131

_LOGGER = logging.getLogger(__name__)

DDP_PORT = 4048
E131_PORT = 5568
MIN_PUSH_INTERVAL = 1 / 60  # frames from the network are shown at most this often
IP_MULTICAST_ALL = 49  # Linux: deliver only the groups this socket joined
__all__ = ["DDP_PORT", "E131_PORT", "MAX_PIXELS", "UNIVERSE_SIZE", "UNIVERSES", "PixelReceiver"]


class _Listener(asyncio.DatagramProtocol):
    def __init__(self, rx: PixelReceiver) -> None:
        self.rx = rx

    def datagram_received(self, pkt: bytes, addr) -> None:
        try:
            if self.rx.accept(addr[0]):
                self.handle(pkt)
        except Exception:  # noqa: BLE001  a bad packet must never take down the listener
            _LOGGER.debug("Dropped a packet that could not be handled", exc_info=True)

    def handle(self, pkt: bytes) -> None:
        raise NotImplementedError


class _DDP(_Listener):
    def handle(self, pkt: bytes) -> None:
        if (parsed := parse_ddp(pkt, len(self.rx.buf))) is None:
            return
        offset, data, push = parsed
        self.rx.buf[offset : offset + len(data)] = data
        if push:
            self.rx.push()


class _E131(_Listener):
    def handle(self, pkt: bytes) -> None:
        if (parsed := parse_e131(pkt)) is None:
            return
        offset, data = parsed
        self.rx.buf[offset : offset + len(data)] = data
        self.rx.push()


class PixelReceiver:
    """One per Home Assistant; every string subscribes to its slice."""

    def __init__(self, hass=None) -> None:
        self.hass = hass  # only used to find the LAN interface for multicast
        self.buf = bytearray(BUF_SIZE)
        self._allowed: tuple[ipaddress.IPv4Network, ...] = ()  # empty: any private / link-local sender
        self._verdicts: dict[str, bool] = {}  # source ip -> accepted by address (bounded)
        self._limiter = RateLimiter()
        self._warned: set[str] = set()
        self._noted_open = False
        self._last_push = 0.0
        self._push_timer: asyncio.TimerHandle | None = None
        self._subs: dict[object, tuple[int, int, Callable[[bytes], None]]] = {}
        self._lead: dict[object, int] = {}  # key -> show timing lead in ms (only those not 0)
        self._transports: list[asyncio.DatagramTransport] = []
        self._listening = False  # set before binding: strings set up concurrently must not bind twice

    # --- who may send ----------------------------------------------------------
    def set_allowed_sources(self, value: object) -> None:
        """Restrict senders to these IPs / networks (text or list); empty = any private LAN address."""
        try:
            self._allowed = parse_sources(value)
        except ValueError as err:
            _LOGGER.warning("Ignoring the allowed senders setting: %s", err)
            self._allowed = ()
        self._verdicts.clear()
        if not self._allowed:
            self._note_open()

    def _note_open(self) -> None:
        if not self._noted_open:
            self._noted_open = True
            _LOGGER.info("No allowed senders set: light show data is accepted from any private LAN address")

    def accept(self, ip: str) -> bool:
        """Is a packet from this source address to be processed at all? Counts towards the rate limit."""
        ok = self._verdicts.get(ip)
        if ok is None:
            ok = source_allowed(ip, self._allowed)
            if len(self._verdicts) < 256:
                self._verdicts[ip] = ok
            if not ok and len(self._warned) < 8 and ip not in self._warned:
                self._warned.add(ip)
                _LOGGER.warning("Ignoring light show data from %s: %s", ip,
                                "not on the allowed senders list" if is_lan_ipv4(ip) else "not a private LAN address")
        if not ok:
            return False
        if not self._allowed:
            self._note_open()
        return self._limiter.allow(ip, asyncio.get_running_loop().time())

    def push(self) -> None:
        """Show the buffer, at most MIN_PUSH_INTERVAL apart: a burst of packets becomes one frame."""
        if self._push_timer is not None:
            return  # the pending one will show the latest buffer
        loop = asyncio.get_running_loop()
        wait = MIN_PUSH_INTERVAL - (loop.time() - self._last_push)
        if wait <= 0:
            self._last_push = loop.time()
            self.show(self.buf)
        else:
            self._push_timer = loop.call_later(wait, self._push_now)

    def _push_now(self) -> None:
        self._push_timer = None
        self._last_push = asyncio.get_running_loop().time()
        try:
            self.show(self.buf)
        except Exception:  # noqa: BLE001  a timer callback must not raise
            _LOGGER.debug("Showing a frame failed", exc_info=True)

    def subscriptions(self) -> list[tuple[int, int, Callable[[bytes], None]]]:
        """Each string's (start byte, byte size, callback)."""
        return list(self._subs.values())

    def leads(self) -> list[int]:
        """The distinct non-zero show timing leads (ms) of the strings now subscribed."""
        return sorted(set(self._lead.values()))

    def show_sequence(self, data: bytes | bytearray) -> None:
        """A frame from the .fseq player: the strings with no lead (all of them for an empty, blanking frame)."""
        self.show(data, 0 if data else None)

    def show_lead(self, lead_ms: int, data: bytes | bytearray) -> None:
        """A .fseq frame, already picked for t + lead, for the strings with exactly that lead."""
        self.show(data, lead_ms)

    def show(self, data: bytes | bytearray, lead: int | None = None) -> None:
        """Give every string its slice of a full channel buffer (channel 1 = byte 0); `lead` limits it to the
        strings with that show timing lead."""
        for key, (start, size, cb) in list(self._subs.items()):
            if lead is not None and self._lead.get(key, 0) != lead:
                continue
            chunk = bytes(data[start : start + size])
            try:
                cb(chunk if len(chunk) == size else chunk.ljust(size, b"\0"))
            except Exception:  # noqa: BLE001  one broken string must not starve the others
                _LOGGER.debug("A light show subscriber failed", exc_info=True)

    async def subscribe(self, key: object, start_pixel: int, bulbs: int, cb: Callable[[bytes], None],
                        lead_ms: int = 0) -> None:
        self._subs[key] = ((start_pixel - 1) * 3, bulbs * 3, cb)
        if lead_ms:
            self._lead[key] = lead_ms
        else:
            self._lead.pop(key, None)
        if not self._listening:
            self._listening = True
            await self._start()
            if not self._subs:  # everything unsubscribed while the sockets were opening
                self._close()

    def unsubscribe(self, key: object) -> None:
        self._subs.pop(key, None)
        self._lead.pop(key, None)
        if not self._subs:
            self._close()

    def close(self) -> None:
        """Close the sockets now (Home Assistant is stopping)."""
        self._subs.clear()
        self._lead.clear()
        self._close()

    def _close(self) -> None:
        if self._push_timer is not None:
            self._push_timer.cancel()
            self._push_timer = None
        for t in self._transports:
            t.close()
        self._transports.clear()
        self._listening = False

    async def _start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            t, _ = await loop.create_datagram_endpoint(lambda: _DDP(self), local_addr=("0.0.0.0", DDP_PORT))
            self._transports.append(t)
        except OSError as err:
            _LOGGER.warning("DDP receiver not started, port %s in use: %s", DDP_PORT, err)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", E131_PORT))
        except OSError as err:
            sock.close()
            _LOGGER.warning("E1.31 receiver not started on port %s: %s", E131_PORT, err)
            return
        try:  # only the groups joined below, not whatever other sockets on this machine joined
            sock.setsockopt(socket.IPPROTO_IP, IP_MULTICAST_ALL, 0)
        except OSError:
            pass
        try:
            iface = socket.inet_aton(await self._lan_interface())
            for u in range(1, UNIVERSES + 1):  # sACN is usually multicast to 239.255.<hi>.<lo>
                group = socket.inet_aton(f"239.255.{u >> 8}.{u & 255}")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group + iface)
        except OSError as err:  # keep the socket: unicast E1.31 still works
            _LOGGER.warning("E1.31 multicast not joined, unicast only: %s", err)
        try:
            t, _ = await loop.create_datagram_endpoint(lambda: _E131(self), sock=sock)
        except OSError as err:
            sock.close()
            _LOGGER.warning("E1.31 receiver not started on port %s: %s", E131_PORT, err)
            return
        self._transports.append(t)

    async def _lan_interface(self) -> str:
        """Address of the LAN interface to join multicast on (the first enabled private one), else any."""
        if self.hass is not None:
            try:
                from homeassistant.components.network import async_get_enabled_source_ips  # noqa: PLC0415

                for ip in await async_get_enabled_source_ips(self.hass):
                    if ip.version == 4 and is_lan_ipv4(str(ip)):
                        return str(ip)
            except Exception:  # noqa: BLE001  fall back to the OS's choice
                _LOGGER.debug("Could not look up the LAN interface", exc_info=True)
        return "0.0.0.0"
