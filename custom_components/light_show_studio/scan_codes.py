"""The flash sequence for camera mapping: every pixel gets a binary code, shown one bit at a time.

Frame 0 is all off (the dark reference) and frame 1 is every pixel on (where each LED is). Then for each bit
b of the pixel index, least significant first, two frames: pixels whose bit b is set are lit (frame 2 + 2b),
then the opposite pixels (frame 3 + 2b). Reading a bit by comparing a pixel's brightness in the two frames
is robust to uneven light and exposure. N pixels need 2 + 2 * ceil(log2(N)) frames.
The phone does the decoding in the panel; `decode_pixel` is the same rule in Python, for the tests.
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

from collections.abc import Sequence

SCAN_LEVEL = 128  # lit pixels are this white (0-255): bright enough for a phone camera, gentle on the exposure
SCAN_COLOUR = (SCAN_LEVEL, SCAN_LEVEL, SCAN_LEVEL)
OFF = (0, 0, 0)
MAX_PIXELS = 2040
# a bit reads as set when its frame beats the inverse by this fraction of the "all on" brightness
MIN_CONTRAST = 0.1


def bit_count(n: int) -> int:
    """ceil(log2(n)): bits to number n pixels (0 for one pixel)."""
    if n < 1:
        raise ValueError("need at least one pixel")
    return (n - 1).bit_length()


def frame_count(n: int) -> int:
    return 2 + 2 * bit_count(n)


def is_lit(index: int, frame: int) -> bool:
    if frame < 2:
        return frame == 1
    bit, inverse = divmod(frame - 2, 2)
    return bool((index >> bit) & 1) != bool(inverse)


def frame_mask(n: int, frame: int) -> list[bool]:
    """Which of the n pixels are lit in a frame."""
    if not 0 <= frame < frame_count(n):
        raise ValueError(f"frame must be 0-{frame_count(n) - 1}")
    return [is_lit(i, frame) for i in range(n)]


def frame_colours(n: int, frame: int, level: int = SCAN_LEVEL) -> list[tuple[int, int, int]]:
    on = (level, level, level)
    return [on if lit else OFF for lit in frame_mask(n, frame)]


def decode_pixel(levels: Sequence[float], n: int) -> int | None:
    """The index of a pixel from its brightness in every frame, or None when the reading isn't clear: too dim
    in the "all on" frame, a bit pair too close to call, or an index that doesn't exist."""
    if len(levels) != frame_count(n):
        raise ValueError("one brightness per frame")
    dark, full = levels[0], levels[1]
    span = full - dark
    if span <= 0:
        return None
    index = 0
    for bit in range(bit_count(n)):
        on, off = levels[2 + 2 * bit] - dark, levels[3 + 2 * bit] - dark
        if abs(on - off) < MIN_CONTRAST * span:
            return None
        if on > off:
            index |= 1 << bit
    return index if index < n else None


def simulate(n: int, index: int, dark: float = 0.0, lit: float = 1.0) -> list[float]:
    """What a camera pixel sitting on LED `index` would read in every frame (for tests)."""
    return [lit if is_lit(index, f) else dark for f in range(frame_count(n))]
