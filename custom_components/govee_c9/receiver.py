"""Pixel-protocol receiver, so sequencers like xLights or Light-O-Rama can drive the strings.

Home Assistant appears to the sequencer as one pixel controller. It listens for DDP
(UDP 4048, what xLights uses for WLED-style controllers) and E1.31/sACN (UDP 5568, 510
channels per universe starting at universe 1). Each string takes its own slice of the
pixels, set by its start pixel: string 1 = pixels 1-50, string 2 = 51-100, ...
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

DDP_PORT = 4048
E131_PORT = 5568
DDP_PUSH, DDP_TIMECODE = 0x01, 0x10
MAX_PIXELS = 1020  # 6 E1.31 universes
UNIVERSE_SIZE = 510
UNIVERSES = MAX_PIXELS * 3 // UNIVERSE_SIZE


class _DDP(asyncio.DatagramProtocol):
    def __init__(self, rx: PixelReceiver) -> None:
        self.rx = rx

    def datagram_received(self, pkt: bytes, addr) -> None:
        if len(pkt) < 10:
            return
        flags = pkt[0]
        head = 14 if flags & DDP_TIMECODE else 10
        offset, length = struct.unpack(">IH", pkt[4:10])
        buf = self.rx.buf
        if offset < len(buf):
            data = pkt[head : head + length][: len(buf) - offset]
            buf[offset : offset + len(data)] = data
        if flags & DDP_PUSH:
            self.rx.push()


class _E131(asyncio.DatagramProtocol):
    def __init__(self, rx: PixelReceiver) -> None:
        self.rx = rx

    def datagram_received(self, pkt: bytes, addr) -> None:
        if len(pkt) <= 126 or pkt[125] != 0:  # 0 = DMX start code
            return
        universe = struct.unpack(">H", pkt[113:115])[0]
        if not 1 <= universe <= UNIVERSES:
            return
        offset = (universe - 1) * UNIVERSE_SIZE
        data = pkt[126 : 126 + UNIVERSE_SIZE]
        self.rx.buf[offset : offset + len(data)] = data
        self.rx.push()


class PixelReceiver:
    """One per Home Assistant; every string subscribes to its slice."""

    def __init__(self) -> None:
        self.buf = bytearray(MAX_PIXELS * 3)
        self._subs: dict[object, tuple[int, int, Callable[[bytes], None]]] = {}
        self._transports: list[asyncio.DatagramTransport] = []

    def push(self) -> None:
        self.show(self.buf)

    def show(self, data: bytes | bytearray) -> None:
        """Give every string its slice of a full channel buffer (channel 1 = byte 0)."""
        for start, size, cb in list(self._subs.values()):
            chunk = bytes(data[start : start + size])
            cb(chunk if len(chunk) == size else chunk.ljust(size, b"\0"))

    async def subscribe(self, key: object, start_pixel: int, bulbs: int, cb: Callable[[bytes], None]) -> None:
        self._subs[key] = ((start_pixel - 1) * 3, bulbs * 3, cb)
        if not self._transports:
            await self._start()

    def unsubscribe(self, key: object) -> None:
        self._subs.pop(key, None)
        if not self._subs:
            for t in self._transports:
                t.close()
            self._transports.clear()

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
            for u in range(1, UNIVERSES + 1):  # sACN is usually multicast to 239.255.<hi>.<lo>
                group = socket.inet_aton(f"239.255.{u >> 8}.{u & 255}")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group + socket.inet_aton("0.0.0.0"))
            t, _ = await loop.create_datagram_endpoint(lambda: _E131(self), sock=sock)
            self._transports.append(t)
        except OSError as err:
            sock.close()
            _LOGGER.warning("E1.31 receiver not started on port %s: %s", E131_PORT, err)
