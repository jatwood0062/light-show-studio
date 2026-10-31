"""Pixel-protocol receiver, so sequencers like xLights or Light-O-Rama can drive the string.

Listens for DDP (UDP 4048, what xLights uses for WLED-style controllers) and E1.31/sACN
(UDP 5568) and hands each complete frame of RGB bytes to a callback.
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


class _DDP(asyncio.DatagramProtocol):
    def __init__(self, size: int, on_frame: Callable[[bytes], None]) -> None:
        self.buf = bytearray(size)
        self.on_frame = on_frame

    def datagram_received(self, pkt: bytes, addr) -> None:
        if len(pkt) < 10:
            return
        flags = pkt[0]
        head = 14 if flags & DDP_TIMECODE else 10
        offset, length = struct.unpack(">IH", pkt[4:10])
        if offset < len(self.buf):
            data = pkt[head : head + length][: len(self.buf) - offset]
            self.buf[offset : offset + len(data)] = data
        if flags & DDP_PUSH:
            self.on_frame(bytes(self.buf))


class _E131(asyncio.DatagramProtocol):
    def __init__(self, size: int, universe: int, on_frame: Callable[[bytes], None]) -> None:
        self.size = size
        self.universe = universe
        self.on_frame = on_frame

    def datagram_received(self, pkt: bytes, addr) -> None:
        if len(pkt) <= 126 or pkt[125] != 0:  # 0 = DMX start code
            return
        if struct.unpack(">H", pkt[113:115])[0] != self.universe:
            return
        self.on_frame(pkt[126 : 126 + self.size].ljust(self.size, b"\0"))


class PixelReceiver:
    def __init__(self, bulbs: int, universe: int, on_frame: Callable[[bytes], None]) -> None:
        self.size = bulbs * 3
        self.universe = universe
        self.on_frame = on_frame
        self._transports: list[asyncio.DatagramTransport] = []

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            t, _ = await loop.create_datagram_endpoint(
                lambda: _DDP(self.size, self.on_frame), local_addr=("0.0.0.0", DDP_PORT)
            )
            self._transports.append(t)
        except OSError as err:
            _LOGGER.warning("DDP receiver not started, port %s in use: %s", DDP_PORT, err)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", E131_PORT))
            group = socket.inet_aton(f"239.255.{self.universe >> 8}.{self.universe & 255}")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group + socket.inet_aton("0.0.0.0"))
            t, _ = await loop.create_datagram_endpoint(
                lambda: _E131(self.size, self.universe, self.on_frame), sock=sock
            )
            self._transports.append(t)
        except OSError as err:
            sock.close()
            _LOGGER.warning("E1.31 receiver not started on port %s: %s", E131_PORT, err)

    def stop(self) -> None:
        for t in self._transports:
            t.close()
        self._transports.clear()
