"""Fades and cross-fades of frames over time. Pure helpers: the caller passes the clock, so they are easy to test.

A Ramp is a brightness gain that moves from one level to another (turn on, turn off, a long sunset fade); an Xfade
blends a frozen frame of the old look into the new look, which keeps animating underneath (so the effect clock
never restarts). Nothing here needs Home Assistant.
"""
from __future__ import annotations

import math
from functools import lru_cache

from .protocol import Colour

MAX_TRANSITION = 1800.0  # seconds: the longest fade anything accepts
DEFAULT_TRANSITION = 0.7  # seconds: the default for the `default_transition` option
LONG = 5.0  # a fade longer than this is a slow brightness move: sent in coarse steps, not at the full frame rate


def clean_seconds(value: object, default: float = 0.0, maximum: float = MAX_TRANSITION) -> float:
    """A fade length in seconds from whatever was passed: 0 (instant) to `maximum`; junk gives `default`."""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if v != v or v in (math.inf, -math.inf):
        return default
    return max(0.0, min(maximum, v))


def smoothstep(k: float) -> float:
    k = 0.0 if k != k else max(0.0, min(1.0, k))
    return k * k * (3 - 2 * k)


def lerp_frame(a: list[Colour], b: list[Colour], k: float) -> list[Colour]:
    """Mix frame a into frame b: k = 0 is a, k = 1 is b. A shorter frame is padded with black."""
    if k <= 0:
        return list(a) if len(a) == len(b) else _pad(a, len(b))
    if k >= 1:
        return list(b)
    a = a if len(a) == len(b) else _pad(a, len(b))
    j = 1 - k
    return [(int(p[0] * j + q[0] * k), int(p[1] * j + q[1] * k), int(p[2] * j + q[2] * k)) for p, q in zip(a, b)]


def _pad(frame: list[Colour], n: int) -> list[Colour]:
    return list(frame[:n]) + [(0, 0, 0)] * (n - len(frame))


@lru_cache(maxsize=64)
def scale_table(g: float) -> bytes:
    """What each byte value becomes at brightness g (0-1): int(v * g), for bytes.translate or indexing."""
    return bytes(int(v * g) for v in range(256))


def scale_bytes(data: bytes | bytearray, g: float) -> bytes:
    """Channel bytes at brightness g (0 = black, 1 = unchanged); the same as int(v * g) for every byte."""
    if g >= 1:
        return bytes(data)
    if g <= 0:
        return bytes(len(data))
    return bytes(data).translate(scale_table(g))


def scale_frame(frame: list[Colour], g: float) -> list[Colour]:
    """The frame at brightness g (0 = black, 1 = unchanged)."""
    if g >= 1:
        return frame
    if g <= 0:
        return [(0, 0, 0)] * len(frame)
    table = scale_table(g)
    return [(table[r], table[gr], table[b]) for r, gr, b in frame]


class Ramp:
    """A gain that goes from `start` to `end` over `dur` seconds (smoothly), beginning at `t0`."""

    def __init__(self, start: float, end: float, dur: float, t0: float) -> None:
        self.start, self.end, self.dur, self.t0 = float(start), float(end), max(0.0, float(dur)), float(t0)

    def level(self, now: float) -> float:
        if self.dur <= 0:
            return self.end
        k = (now - self.t0) / self.dur
        return self.start + (self.end - self.start) * smoothstep(k)

    def done(self, now: float) -> bool:
        return self.dur <= 0 or now - self.t0 >= self.dur


class Xfade:
    """The frozen `old` frame blending into whatever the new look draws, over `dur` seconds from `t0`."""

    def __init__(self, old: list[Colour], dur: float, t0: float) -> None:
        self.old, self.dur, self.t0 = old, max(0.0, float(dur)), float(t0)

    def k(self, now: float) -> float:
        return 1.0 if self.dur <= 0 else smoothstep((now - self.t0) / self.dur)

    def done(self, now: float) -> bool:
        return self.dur <= 0 or now - self.t0 >= self.dur

    def apply(self, new: list[Colour], now: float) -> list[Colour]:
        return lerp_frame(self.old, new, self.k(now))


def tick_interval(dur: float, frame_step: float) -> float:
    """How often to send frames during a fade of `dur` seconds: every frame for a short one, coarser for a long
    brightness ramp (about 255 steps cover it), never slower than twice a second."""
    if dur <= LONG:
        return frame_step
    return max(frame_step, min(0.5, dur / 255.0))
