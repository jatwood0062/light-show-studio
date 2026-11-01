"""Transitions between shows: pure helpers on channel buffers (no Home Assistant imports)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence

_LOGGER = logging.getLogger(__name__)

NONE, FADE, CROSSFADE, EFFECT = "none", "fade", "crossfade", "effect"
MODES = (NONE, FADE, CROSSFADE, EFFECT)
DEFAULT_TRANSITION = FADE
FPS = 25  # transition frames per second
FADE_IN = 1.0  # seconds the next show fades in after a fade or effect transition
TRANSITION_COLOUR = (255, 150, 40)  # base colour handed to the intermission effect


def _clamp(k: float) -> float:
    try:
        k = float(k)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if k != k else max(0.0, min(1.0, k))


def fade(frame: bytes, k: float) -> bytes:
    """`frame` at brightness `k` (0 = black, 1 = unchanged)."""
    k = _clamp(k)
    if k >= 1.0:
        return bytes(frame)
    if k <= 0.0:
        return bytes(len(frame))
    return bytes(int(v * k) for v in frame)


def blend(a: bytes, b: bytes, k: float) -> bytes:
    """Mix `a` into `b`: k = 0 gives `a`, k = 1 gives `b`. The shorter one is padded with zeros."""
    k = _clamp(k)
    n = max(len(a), len(b))
    a, b = bytes(a).ljust(n, b"\0"), bytes(b).ljust(n, b"\0")
    return bytes(int(x + (y - x) * k) for x, y in zip(a, b))


def default_effect(effects: Sequence[str]) -> str:
    """The intermission effect when none is chosen: Twinkle if there is one, else the first."""
    return "Twinkle" if "Twinkle" in effects else (effects[0] if effects else "")


def effect_buffer(subs: Iterable[tuple[int, int, object]], effect_fn: Callable, t: float,
                  colour: tuple[int, int, int], size: int) -> bytearray:
    """A full channel buffer of `size` bytes with `effect_fn` run on every light.

    `subs` are the receiver's (start byte, byte size, callback) entries; each light has
    size // 3 bulbs. A failing effect leaves that light dark.
    """
    buf = bytearray(max(0, size))
    for start, length, *_ in subs:
        bulbs = length // 3
        if bulbs <= 0 or start < 0 or start >= len(buf):
            continue
        try:
            cols = effect_fn(t, bulbs, colour)
            out = bytearray()
            for c in list(cols)[:bulbs]:
                out += bytes(max(0, min(255, int(v))) for v in c[:3])
        except Exception:  # noqa: BLE001  a bad effect must not stop the show queue
            _LOGGER.debug("Intermission effect failed", exc_info=True)
            continue
        out = out[: len(buf) - start]
        buf[start : start + len(out)] = out
    return buf
