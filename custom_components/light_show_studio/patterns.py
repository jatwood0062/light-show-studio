"""Patterns (which colour each bulb is) and pattern effects (how they move), picked separately.

A pattern is a list of colours repeated along the string. A pattern effect takes the pattern and
animates it: twinkle, chase, fade and so on. Speed scales time for every effect (1-100, 50 = normal).
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import colorsys
import math

from .protocol import Colour

PATTERN = "Pattern"  # the light's effect name while a pattern + pattern effect is showing

# Classic C9 multicolour: the old ceramic-bulb set order (red, gold, green, blue, orange, pink).
PATTERNS: dict[str, tuple[list[Colour], int]] = {  # name -> (colours, bulbs per colour)
    "Classic Multicolor": ([(255, 0, 0), (255, 150, 0), (0, 200, 0), (0, 40, 255), (255, 70, 0), (255, 20, 120)], 1),
    "Warm White": ([(255, 150, 40)], 1),
    "Cool White": ([(200, 220, 255)], 1),
    "Red & Green": ([(255, 0, 0), (0, 200, 0)], 1),
    "Red, Green & White": ([(255, 0, 0), (0, 200, 0), (255, 200, 140)], 1),
    "Candy Cane": ([(255, 0, 0), (255, 255, 255)], 2),
    "Blue & White": ([(0, 40, 255), (200, 220, 255)], 1),
    "Gold & White": ([(255, 140, 0), (255, 200, 140)], 1),
    "Pastel": ([(255, 120, 170), (120, 200, 255), (255, 220, 90), (150, 255, 140), (190, 130, 255)], 1),
    "Halloween": ([(255, 70, 0), (140, 0, 255), (40, 255, 0)], 1),
    "Red, White & Blue": ([(255, 0, 0), (255, 255, 255), (0, 40, 255)], 1),
    "Valentine": ([(255, 0, 0), (255, 20, 110), (255, 200, 200)], 1),
    "Rainbow": ([], 1),  # special: a smooth rainbow along the string
}
PATTERN_LIST = list(PATTERNS)
DEFAULT_PATTERN = "Classic Multicolor"

STEADY, TWINKLE, CHASE = "Steady", "Twinkle", "Chase"
PATTERN_EFFECTS = [STEADY, TWINKLE, CHASE, "Twinkle Swap", "Marquee", "Fade", "Sparkle", "Alternate", "Wave"]
DEFAULT_EFFECT = TWINKLE
DEFAULT_SPEED = 50


def speed_factor(speed: int | float) -> float:
    """1-100 -> 0.25x-4x on a log scale, with 50 = 1x."""
    s = max(1.0, min(100.0, float(speed)))
    return 2 ** ((s - 50) / 25)


def _hash(a: int, b: int = 0) -> float:
    x = (a * 374761393 + b * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFF) / 65536.0


def _scale(c: Colour, f: float) -> Colour:
    f = max(0.0, min(1.0, f))
    return int(c[0] * f), int(c[1] * f), int(c[2] * f)


def _mix(a: Colour, b: Colour, f: float) -> Colour:
    f = max(0.0, min(1.0, f))
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


def _rainbow_colour(p: float) -> Colour:
    r, g, b = colorsys.hsv_to_rgb(p % 1.0, 1.0, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def _colour_at(name: str, pos: float, n: int) -> Colour:
    """Colour of the pattern at (possibly fractional) bulb position pos, blending between bulbs."""
    cols, width = PATTERNS.get(name, PATTERNS[DEFAULT_PATTERN])
    if not cols:  # rainbow
        return _rainbow_colour(pos / max(1, min(n, 50)))
    k = len(cols) * width
    p = pos % k
    i = int(p)
    a, b = cols[(i // width) % len(cols)], cols[((i + 1) // width) % len(cols)]
    return _mix(a, b, p - i) if a != b else a


def _bulb_colour(name: str, i: int, n: int) -> Colour:
    cols, width = PATTERNS.get(name, PATTERNS[DEFAULT_PATTERN])
    if not cols:
        return _rainbow_colour(i / max(1, min(n, 50)))
    return cols[(i // width) % len(cols)]


def render(pattern: str, effect: str, t: float, n: int) -> list[Colour]:
    """One frame. t is already scaled by the speed."""
    if effect == CHASE:  # the pattern glides along the string, ~3 bulbs a second
        return [_colour_at(pattern, i - t * 3.0, n) for i in range(n)]
    base = [_bulb_colour(pattern, i, n) for i in range(n)]
    if effect == TWINKLE:  # classic twinkle: each bulb fades down and back up at its own pace
        out = []
        for i, c in enumerate(base):
            rate, phase = 0.5 + _hash(i, 1) * 0.9, _hash(i, 2) * 6.283
            v = 0.5 + 0.5 * math.sin(t * rate * 2.2 + phase)
            out.append(_scale(c, 0.12 + 0.88 * v ** 2))
        return out
    if effect == "Twinkle Swap":  # each bulb dims out, comes back as another colour from the pattern
        cols = PATTERNS.get(pattern, PATTERNS[DEFAULT_PATTERN])[0]
        out = []
        for i in range(n):
            period = 2.5 + _hash(i, 3) * 2.5
            x = t / period + _hash(i, 4)
            cycle, f = int(math.floor(x)), x - math.floor(x)
            if cols:
                c = cols[int(_hash(i, cycle) * len(cols)) % len(cols)]
            else:
                c = _rainbow_colour(_hash(i, cycle))
            out.append(_scale(c, min(1.0, math.sin(f * math.pi) * 1.6)))
        return out
    if effect == "Marquee":  # every third bulb lit, stepping along like a theatre sign
        step = int(t * 4) % 3
        return [c if i % 3 == step else _scale(c, 0.08) for i, c in enumerate(base)]
    if effect == "Fade":  # the whole string breathes
        v = 0.5 + 0.5 * math.sin(t * 1.6)
        return [_scale(c, 0.1 + 0.9 * v) for c in base]
    if effect == "Sparkle":  # the pattern with quick white glints
        slot = int(t * 12)
        out = []
        for i, c in enumerate(base):
            out.append((255, 255, 255) if _hash(i, slot) < 0.04 else c)
        return out
    if effect == "Alternate":  # odd and even bulbs take turns
        on_even = int(t * 1.5) % 2 == 0
        return [c if (i % 2 == 0) == on_even else _scale(c, 0.08) for i, c in enumerate(base)]
    if effect == "Wave":  # a slow wave of brightness travels along the string
        return [_scale(c, 0.15 + 0.85 * (0.5 + 0.5 * math.sin(i * 0.45 - t * 2.5)) ** 2) for i, c in enumerate(base)]
    return base  # Steady


def is_animated(effect: str) -> bool:
    return effect != STEADY
