"""The flash sequence for camera mapping. Two modes share this module.

COLOUR mode (the default, gentler): frame 0 is dark, frames 1-3 light every pixel red, then green, then blue (so the
phone learns how it sees each LED's red, green and blue: white balance, tint, diffusers). Then one frame per base-3
digit of the pixel's code (code = index + 1, so no pixel is "all zeros"): each pixel shows red, green or blue for its
digit, least significant first, with the colour for a digit rotating from frame to frame (digit d in digit frame j shows
primary (d + j) % 3) so a pixel is never one colour all the time. A last check frame shows (sum of digits) mod 3 the
same way, to throw out misreads. N pixels need 5 + ceil(log3(N + 1)) frames: 904 LEDs = 12 frames, at most 140 per
channel. `decode_colour_pixel` is the rule the phone applies once it has told which primary each frame showed.

WHITE mode (fallback): every pixel gets a binary code, shown one bit at a time.

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


# --- colour mode -----------------------------------------------------------------------------------------------
MODE_COLOUR, MODE_WHITE = "colour", "white"
MODES = (MODE_COLOUR, MODE_WHITE)
DEFAULT_MODE = MODE_COLOUR
COLOUR_LEVEL = 140  # each lit channel of a colour frame (0-255)
CAL_FRAMES = 4  # dark, all red, all green, all blue
PRIMARIES = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


def primary(cls: int, level: int = COLOUR_LEVEL) -> tuple[int, int, int]:
    """The colour of primary 0 (red), 1 (green) or 2 (blue) at `level`."""
    r, g, b = PRIMARIES[cls % 3]
    return (r * level, g * level, b * level)


def digit_count(n: int) -> int:
    """Base-3 digits that hold the codes 1..n (the smallest D with 3**D > n)."""
    if n < 1:
        raise ValueError("need at least one pixel")
    d = 1
    while 3**d <= n:
        d += 1
    return d


def colour_frame_count(n: int) -> int:
    return CAL_FRAMES + digit_count(n) + 1


def colour_digits(index: int, n: int) -> list[int]:
    """The base-3 digits (least significant first) of pixel `index`'s code, index + 1."""
    if not 0 <= index < n:
        raise ValueError("no such pixel")
    code = index + 1
    out = []
    for _ in range(digit_count(n)):
        code, d = divmod(code, 3)
        out.append(d)
    return out


def colour_class(index: int, frame: int, n: int) -> int | None:
    """Which primary (0 red, 1 green, 2 blue) the pixel shows in a frame; None when dark."""
    if not 0 <= frame < colour_frame_count(n):
        raise ValueError(f"frame must be 0-{colour_frame_count(n) - 1}")
    if frame == 0:
        return None
    if frame < CAL_FRAMES:
        return frame - 1
    j = frame - CAL_FRAMES
    digits = colour_digits(index, n)
    d = digits[j] if j < len(digits) else sum(digits) % 3  # the last frame is the check digit
    return (d + j) % 3


def colour_frame_colours(n: int, frame: int, level: int = COLOUR_LEVEL) -> list[tuple[int, int, int]]:
    out = []
    for i in range(n):
        cls = colour_class(i, frame, n)
        out.append(OFF if cls is None else primary(cls, level))
    return out


def decode_colour_pixel(classes: Sequence[int | None], n: int) -> int | None:
    """The index of a pixel from the primary it was read as in every frame (None for dark or unclear), or None when
    a frame is unreadable, the check digit disagrees, or the code is not a pixel."""
    d = digit_count(n)
    if len(classes) != colour_frame_count(n):
        raise ValueError("one reading per frame")
    digits = []
    for j in range(d + 1):
        c = classes[CAL_FRAMES + j]
        if c not in (0, 1, 2):
            return None
        digits.append((c - j) % 3)
    check = digits.pop()
    if sum(digits) % 3 != check:
        return None
    code = sum(v * 3**k for k, v in enumerate(digits))
    return code - 1 if 1 <= code <= n else None


def simulate_colour(n: int, index: int) -> list[int | None]:
    """What a perfect camera would read as the primary of pixel `index` in every frame (for tests)."""
    return [colour_class(index, f, n) for f in range(colour_frame_count(n))]


# --- either mode -------------------------------------------------------------------------------------------------
def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    return mode


def frames_for(n: int, mode: str = DEFAULT_MODE) -> int:
    return colour_frame_count(n) if check_mode(mode) == MODE_COLOUR else frame_count(n)


def level_for(mode: str = DEFAULT_MODE) -> int:
    return COLOUR_LEVEL if check_mode(mode) == MODE_COLOUR else SCAN_LEVEL


def frame_colours_for(n: int, frame: int, mode: str = DEFAULT_MODE) -> list[tuple[int, int, int]]:
    return colour_frame_colours(n, frame) if check_mode(mode) == MODE_COLOUR else frame_colours(n, frame)


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
