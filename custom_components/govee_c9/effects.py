"""Effects: each is a function (t seconds, bulb count, base colour) -> list of colours.

STILL effects are drawn once; the rest are animated by the coordinator.
"""
from __future__ import annotations

import colorsys
import math
import random

from .protocol import Colour

RED, GREEN, WHITE, BLACK = (255, 0, 0), (0, 255, 0), (255, 255, 255), (0, 0, 0)
ORANGE, PURPLE, WARM = (255, 80, 0), (140, 0, 255), (255, 150, 40)
BLUE, PINK, GOLD = (0, 40, 255), (255, 20, 110), (255, 140, 0)
PASTELS = [(255, 120, 170), (120, 200, 255), (255, 220, 90), (150, 255, 140), (190, 130, 255)]


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> Colour:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _scale(c: Colour, f: float) -> Colour:
    f = max(0.0, min(1.0, f))
    return int(c[0] * f), int(c[1] * f), int(c[2] * f)


def _pattern(cols: list[Colour], width: int = 1):
    return lambda t, n, base: [cols[(i // width) % len(cols)] for i in range(n)]


def _rainbow(t, n, base):
    return [_hsv(i / n) for i in range(n)]


def _rainbow_flow(t, n, base):
    return [_hsv(i / n + t * 0.15) for i in range(n)]


def _chase(cols: list[Colour], width: int, speed: float):
    def fx(t, n, base):
        shift = int(t * speed)
        return [cols[((i + shift) // width) % len(cols)] for i in range(n)]
    return fx


def _candy_spin(t, n, base):
    """Red and white stripes gliding along the string, with soft edges so the motion looks
    like a rotating candy cane rather than bulbs jumping."""
    stripe, speed = 3.0, 16.0  # bulbs per stripe, bulbs per second
    out = []
    for i in range(n):
        pos = ((i - t * speed) / stripe) % 2.0  # 0-1 red, 1-2 white
        edge = min(pos % 1.0, 1 - pos % 1.0)  # distance to nearest stripe edge
        mix = 0.5 + min(edge * 2.5, 0.5)  # 0.5 at the edge, 1.0 in the middle
        a, b = (RED, WHITE) if pos < 1 else (WHITE, RED)
        out.append(tuple(int(x * mix + y * (1 - mix)) for x, y in zip(a, b)))
    return out


def _comet(t, n, base):
    head = (t * 12) % (n + 10)
    return [_scale(base, 1 - (head - i) / 8) if 0 <= head - i < 8 else BLACK for i in range(n)]


def _theater(t, n, base):
    step = int(t * 6) % 3
    return [base if i % 3 == step else BLACK for i in range(n)]


def _breathe(t, n, base):
    return [_scale(base, 0.15 + 0.85 * (0.5 + 0.5 * math.sin(t * 2)))] * n


_twinkle_state: dict[int, list[float]] = {}


def _twinkle(t, n, base):
    phases = _twinkle_state.setdefault(n, [random.random() * 6.28 for _ in range(n)])
    return [_scale(base, 0.25 + 0.75 * max(0.0, math.sin(t * 1.7 + p)) ** 3) for p in phases]


def _fire(t, n, base):
    return [_hsv(0.02 + random.random() * 0.06, 1, 0.55 + random.random() * 0.45) for _ in range(n)]


def _ocean(t, n, base):
    return [_hsv(0.52 + 0.08 * math.sin(i * 0.4 + t * 1.3), 1, 0.6 + 0.4 * math.sin(i * 0.25 - t)) for i in range(n)]


_fireworks_state: dict[int, list] = {}


def _fireworks(t, n, base):
    """Bursts of colour that pop at random spots, spread out and fade."""
    bursts = _fireworks_state.setdefault(n, [])
    if random.random() < 0.08:
        bursts.append((t, random.randrange(n), _hsv(random.random())))
    bursts[:] = [b for b in bursts if t - b[0] < 1.5]
    out = [BLACK] * n
    for start, centre, colour in bursts:
        age = t - start
        reach, fade = age * 8, 1 - age / 1.5
        for i in range(max(0, int(centre - reach) - 1), min(n, int(centre + reach) + 2)):
            if abs(i - centre) <= reach:
                c = _scale(colour, fade * (1 - abs(i - centre) / (reach + 1) * 0.5))
                out[i] = tuple(max(a, b) for a, b in zip(out[i], c))
    return out


def _witches_brew(t, n, base):
    out = []
    for i in range(n):
        bubble = max(0.0, math.sin(i * 1.3 + t * 2.1)) ** 6
        out.append(_hsv(0.33 - 0.05 * bubble, 1 - 0.5 * bubble, 0.35 + 0.65 * bubble))
    return out


STILL = {
    "Rainbow": _rainbow,
    "Christmas": _pattern([RED, GREEN]),
    "Candy Cane": _pattern([RED, WHITE], 2),
    "Halloween": _pattern([ORANGE, PURPLE]),
    "Valentine's": _pattern([RED, PINK, WHITE]),
    "St. Patrick's": _pattern([GREEN, WHITE, GOLD]),
    "Easter": _pattern(PASTELS),
    "July 4th": _pattern([RED, WHITE, BLUE]),
    "Thanksgiving": _pattern([ORANGE, GOLD, (180, 30, 0)]),
    "Hanukkah": _pattern([BLUE, WHITE]),
    "Classic Multicolour": _pattern([RED, GREEN, (0, 60, 255), ORANGE, (255, 0, 120)]),
    "Warm White": _pattern([WARM]),
}

ANIMATED = {
    "Rainbow Flow": _rainbow_flow,
    "Christmas Chase": _chase([RED, GREEN], 3, 4),
    "Candy Cane Chase": _chase([RED, WHITE], 2, 4),
    "Candy Cane Spin": _candy_spin,
    "Halloween Chase": _chase([ORANGE, PURPLE], 3, 4),
    "Valentine's Chase": _chase([RED, PINK, WHITE], 2, 4),
    "July 4th Chase": _chase([RED, WHITE, BLUE], 3, 4),
    "Fireworks": _fireworks,
    "Comet": _comet,
    "Theater Chase": _theater,
    "Breathe": _breathe,
    "Twinkle": _twinkle,
    "Fire": _fire,
    "Ocean": _ocean,
    "Witches Brew": _witches_brew,
}

EFFECTS = {**STILL, **ANIMATED}
EFFECT_LIST = list(EFFECTS)
