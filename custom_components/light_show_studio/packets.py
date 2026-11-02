"""Strict, allocation-light parsers for DDP and E1.31 packets, and a rate limiter; no Home Assistant.

Everything here treats the packet as hostile: every length is checked against the bytes really
received and against the channel buffer, and a malformed packet gives None (never an exception).
"""
from __future__ import annotations

import struct

MAX_PIXELS = 2040  # 12 E1.31 universes
UNIVERSE_SIZE = 510
UNIVERSES = MAX_PIXELS * 3 // UNIVERSE_SIZE
BUF_SIZE = MAX_PIXELS * 3

DDP_PUSH, DDP_QUERY, DDP_REPLY, DDP_TIMECODE = 0x01, 0x02, 0x04, 0x10
DDP_MAX_PACKET = 1500 + 14
DDP_MAX_DATA = 1440 * 2  # the spec's limit is 1440; some senders use a little more
DDP_IDS = (0, 1, 255)  # default, and "all"; control / config / status ids are not pixel data

E131_MIN = 126
_ACN_ID = b"ASC-E1.17\x00\x00\x00"
E131_MAX_PACKET = 638  # 126 + 512


def parse_ddp(pkt: bytes, buf_size: int = BUF_SIZE) -> tuple[int, bytes, bool] | None:
    """(byte offset, data, push) for a pixel-data packet; None when it is malformed or not pixel data.
    The data is already cut to fit a buffer of `buf_size` bytes."""
    n = len(pkt)
    if n < 10 or n > DDP_MAX_PACKET:
        return None
    flags = pkt[0]
    if (flags >> 6) != 1 or flags & (DDP_QUERY | DDP_REPLY):  # version 1 only; no queries or replies
        return None
    if pkt[3] not in DDP_IDS:
        return None
    head = 14 if flags & DDP_TIMECODE else 10
    offset, length = struct.unpack_from(">IH", pkt, 4)
    if head + length > n or length > DDP_MAX_DATA:  # declared more than was sent
        return None
    push = bool(flags & DDP_PUSH)
    if offset >= buf_size:
        return buf_size, b"", push  # nothing to store, but a PUSH still counts
    return offset, bytes(pkt[head : head + min(length, buf_size - offset)]), push


def parse_e131(pkt: bytes, universes: int = UNIVERSES) -> tuple[int, bytes] | None:
    """(byte offset into the channel buffer, channel data) for an E1.31 DMX data packet, else None."""
    n = len(pkt)
    if n <= E131_MIN or n > E131_MAX_PACKET:
        return None
    if pkt[0:2] != b"\x00\x10" or pkt[4:16] != _ACN_ID:  # preamble size, "ASC-E1.17"
        return None
    if struct.unpack_from(">I", pkt, 18)[0] != 4:  # root vector: E1.31 data
        return None
    if struct.unpack_from(">I", pkt, 40)[0] != 2:  # framing vector: data packet
        return None
    if pkt[117] != 2 or pkt[118] != 0xA1 or pkt[125] != 0:  # DMP vector, address type, DMX start code
        return None
    universe = struct.unpack_from(">H", pkt, 113)[0]
    if not 1 <= universe <= universes:
        return None
    count = struct.unpack_from(">H", pkt, 123)[0] - 1  # property count includes the start code
    if count < 1:
        return None
    data = pkt[E131_MIN : E131_MIN + min(count, UNIVERSE_SIZE, n - E131_MIN)]
    return (universe - 1) * UNIVERSE_SIZE, bytes(data)


class RateLimiter:
    """Token buckets per source and overall, so one noisy sender can't hog the event loop.

    allow(src, now) is True when the packet may be processed. The table of sources is bounded."""

    def __init__(self, per_source: float = 1200.0, overall: float = 3000.0, max_sources: int = 64) -> None:
        self.per_source, self.overall, self.max_sources = per_source, overall, max_sources
        self._buckets: dict[str, list[float]] = {}  # src -> [tokens, last seen]
        self._all = [overall, 0.0]
        self.dropped = 0

    @staticmethod
    def _take(b: list[float], rate: float, now: float) -> bool:
        b[0] = min(rate, b[0] + (now - b[1]) * rate)
        b[1] = now
        if b[0] < 1.0:
            return False
        b[0] -= 1.0
        return True

    def allow(self, src: str, now: float) -> bool:
        if self._all[1] == 0.0:
            self._all[1] = now
        b = self._buckets.get(src)
        if b is None:
            if len(self._buckets) >= self.max_sources:
                self._expire(now)
                if len(self._buckets) >= self.max_sources:
                    self.dropped += 1
                    return False
            b = self._buckets[src] = [self.per_source, now]
        if not self._take(self._all, self.overall, now) or not self._take(b, self.per_source, now):
            self.dropped += 1
            return False
        return True

    def _expire(self, now: float) -> None:
        for k in [k for k, b in self._buckets.items() if now - b[1] > 5.0]:
            del self._buckets[k]
