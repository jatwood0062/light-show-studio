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


def _rainbow_chase(t, n, base):
    """Two rainbows along the string, racing (a full lap every ~1.5 s)."""
    return [_hsv(2 * i / n - t * 0.7) for i in range(n)]


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


# --- Deterministic helpers for the newer effects ----------------------------------------------
# These derive their "randomness" from a hash of (pixel or cell, time slot), so a given (t, n)
# always gives the same frame and nothing flickers between frames.

def _hash(a: int, b: int = 0) -> float:
    """Small integer hash -> float in [0, 1)."""
    x = (a * 374761393 + b * 668265263 + 0x9E3779B9) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    x ^= x >> 16
    return x / 4294967296.0


def _rgb(r: float, g: float, b: float) -> Colour:
    return (max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b))))


def _lerp(a: Colour, b: Colour, m: float) -> Colour:
    m = max(0.0, min(1.0, m))
    return _rgb(a[0] + (b[0] - a[0]) * m, a[1] + (b[1] - a[1]) * m, a[2] + (b[2] - a[2]) * m)


def _unit(n: int) -> float:
    """Length scale: 1 on a 50-bulb string, growing (sub-linearly) on long strips so shapes keep
    a similar look without becoming enormous."""
    return max(1.0, n / 50.0) ** 0.75


def _particles(t: float, n: int, speed: float, spacing: float, seed: int, margin: float = 0.0):
    """Endless deterministic stream of (position, cell hash) moving along the string.

    One particle lives in every `spacing`-long cell; the stream scrolls at `speed` (pixels per
    second, negative = towards pixel 0). Only particles within `margin` of the string are made.
    """
    shift = t * speed
    lo = int((-margin - shift) // spacing) - 1
    hi = int((n + margin - shift) // spacing) + 1
    for c in range(lo, hi + 1):
        h = _hash(c, seed)
        yield (c + h) * spacing + shift, _hash(c, seed + 101)


def _twinkler(colour, period: float, prob: float, floor: float, seed: int):
    """Each bulb independently fades in and out on its own clock; `colour(r, v)` picks the shade."""
    def fx(t, n, base):
        out = []
        for i in range(n):
            x = t / period + _hash(i, seed)
            k = int(x)
            u = x - k
            r = _hash(i, k * 131 + seed + 5)
            v = floor
            if _hash(i, k * 131 + seed + 9) < prob:
                v = floor + (1 - floor) * math.sin(math.pi * u) ** 2
            out.append(colour(r, v))
        return out
    return fx


VINTAGE = [(230, 18, 0), (255, 90, 0), (255, 170, 20), (20, 150, 25), (0, 50, 200), (230, 60, 90)]
RETRO = [(230, 18, 0), (20, 150, 25), (0, 50, 200), (255, 100, 0), (255, 190, 30)]
ICE, ICE_DEEP = (170, 220, 255), (90, 170, 255)
GOLD_LIGHT, SOFT_WHITE = (255, 170, 20), (255, 244, 225)


def _rainbow_bands(t, n, base):
    return [_hsv(int(i * 6 / n) / 6) for i in range(n)]


def _ombre(t, n, base):
    d = max(1, n - 1)
    return [_scale(base, 1 - 0.9 * i / d) for i in range(n)]


def _snowy(t, n, base):
    return [(120, 190, 255) if _hash(i, 3) < 0.12 else (235, 240, 255) for i in range(n)]


_rainbow_twinkle = _twinkler(lambda r, v: _hsv(r, 1, v), 2.4, 0.4, 0.06, 11)
_vintage_twinkle = _twinkler(lambda r, v: _scale(VINTAGE[int(r * len(VINTAGE))], 0.2 + 0.8 * v), 3.0, 0.5, 0.0, 23)
_christmas_twinkle = _twinkler(
    lambda r, v: _scale([RED, GREEN, GOLD_LIGHT][int(r * 3)], 0.12 + 0.88 * v), 2.2, 0.45, 0.0, 37)


def _sparkle(t, n, base):
    """Base colour with quick white sparkles that pop and fade."""
    rate = 10.0
    slot = int(t * rate)
    f = t * rate - slot
    dim = _scale(base, 0.75)
    out = []
    for i in range(n):
        m = 0.0
        if _hash(i, slot) < 0.05:
            m = 1 - f * 0.6
        elif _hash(i, slot - 1) < 0.05:
            m = 0.4 * (1 - f)
        out.append(_lerp(dim, WHITE, m) if m else dim)
    return out


def _starfield(t, n, base):
    """Dark night with occasional stars that flash up and fade slowly."""
    out = []
    for i in range(n):
        period = 4.0 + 5.0 * _hash(i, 41)
        x = t / period + _hash(i, 42)
        k = int(x)
        u = x - k
        v = 0.0
        if _hash(i, k + 4242) < 0.13:
            v = min(u / 0.05, 1.0) * (1 - u) ** 2
        tint = _hash(i, k + 77)
        star = _lerp((190, 210, 255), (255, 235, 200), tint)
        out.append(_lerp((0, 0, 6), star, v))
    return out


def _snowfall(t, n, base):
    """White flakes drifting along the string in two layers (near flakes bigger and faster)."""
    u = _unit(n)
    buf = [[4, 7, 18] for _ in range(n)]
    for speed, spacing, size, gain, seed in ((5.0, 5.0, 1.1, 1.0, 51), (9.0, 11.0, 1.5, 0.8, 52)):
        for x, h in _particles(t, n, speed * u, spacing * u, seed, size * u + 1):
            if h > 0.6:
                continue
            w = size * u
            for i in range(max(0, int(x - w) - 1), min(n, int(x + w) + 2)):
                v = (1 - abs(i - x) / w) * gain * (0.6 + 0.4 * h)
                if v > 0:
                    px = buf[i]
                    px[0] += 215 * v
                    px[1] += 232 * v
                    px[2] += 250 * v
    return [_rgb(*p) for p in buf]


def _icicle_drip(t, n, base):
    """Cold white drips that run down the string, leaving a fading trail."""
    u = _unit(n)
    buf = [[0, 6, 20] for _ in range(n)]
    tail = 6.0 * u
    for x, h in _particles(t, n, 11.0 * u, 16.0 * u, 61, tail):
        if h > 0.65:
            continue
        for i in range(max(0, int(x - tail)), min(n, int(x) + 2)):
            d = x - i
            v = max(0.0, 1 - d / tail) ** 2 if d >= 0 else max(0.0, 1 + d)
            if v > 0:
                px = buf[i]
                px[0] += 200 * v
                px[1] += 230 * v
                px[2] += 255 * v
    return [_rgb(*p) for p in buf]


_candle_cache: dict[int, list] = {}


def _candle_flicker(t, n, base):
    params = _candle_cache.get(n)
    if params is None:
        if len(_candle_cache) > 8:
            _candle_cache.clear()
        params = _candle_cache[n] = [
            (_hash(i, 71) * 6.28, _hash(i, 72) * 6.28, _hash(i, 73) * 6.28, 0.8 + 0.4 * _hash(i, 74))
            for i in range(n)]
    out = []
    for p1, p2, p3, sp in params:
        v = 0.66 + 0.18 * math.sin(t * 4.1 * sp + p1) + 0.10 * math.sin(t * 9.3 * sp + p2) \
            + 0.06 * math.sin(t * 17.0 * sp + p3)
        v = max(0.0, min(1.0, v))
        out.append(_rgb(255 * v, 105 * v ** 1.6, 12 * v ** 2))
    return out


def _aurora(t, n, base):
    u = _unit(n)
    out = []
    for i in range(n):
        x = i / u
        w = 0.5 + 0.5 * math.sin(x * 0.21 + t * 0.35) * math.sin(x * 0.09 - t * 0.2 + 1.0)
        bright = (0.5 + 0.5 * math.sin(x * 0.16 - t * 0.3 + 1.7)) ** 2
        out.append(_hsv(0.33 + 0.43 * w, 0.85, 0.2 + 0.8 * bright))
    return out


def _lava(t, n, base):
    u = _unit(n)
    out = []
    for i in range(n):
        x = i / u
        v = (math.sin(x * 0.33 + t * 0.30) + math.sin(x * 0.17 - t * 0.23) + math.sin(x * 0.58 + t * 0.41)) / 6 + 0.5
        v = max(0.0, min(1.0, v * 1.15 - 0.05))
        c = _lerp((70, 0, 0), (255, 60, 0), v * 2) if v < 0.5 else _lerp((255, 60, 0), (255, 160, 15), (v - 0.5) * 2)
        out.append(c)
    return out


def _red_blue(t, n, base):
    """Soft red/blue alternation (about 0.8 swaps per second, crossfaded, never a hard strobe)."""
    block = max(2, n // 6)
    s = math.sin(2 * math.pi * 0.8 * t)
    out = []
    for i in range(n):
        sign = 1 if (i // block) % 2 == 0 else -1
        a = 0.5 + 0.5 * max(-1.0, min(1.0, s * sign * 1.6))
        out.append(_lerp(BLUE, RED, a))
    return out


def _color_wipe(t, n, base):
    """The base colour wipes on, holds, then wipes off in the same direction."""
    p = (t % 7.0) / 7.0
    soft = max(1.5, 0.04 * n)
    if p < 0.4:
        front, on = p / 0.4 * (n + soft), True
    elif p < 0.6:
        front, on = n + soft, True
    else:
        front, on = (p - 0.6) / 0.4 * (n + soft), False
    out = []
    for i in range(n):
        m = max(0.0, min(1.0, (front - i) / soft))
        out.append(_scale(base, m if on else 1 - m))
    return out


def _larson(t, n, base):
    """KITT-style scanner: a bright head sweeping back and forth with a fading tail."""
    u = _unit(n)
    period = 2.4
    ph = 2 * math.pi * t / period
    pos = (n - 1) * (0.5 - 0.5 * math.cos(ph))
    direction = 1 if math.sin(ph) >= 0 else -1
    head, tail = 1.2 * u, 7.0 * u
    out = []
    for i in range(n):
        d = (i - pos) * direction
        if d >= 0:
            v = max(0.0, 1 - d / head)
        else:
            v = max(0.0, 1 + d / tail) ** 2
        out.append(_scale(base, 0.04 + 0.96 * v))
    return out


def _rainbow_breathe(t, n, base):
    v = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(t * 1.4))
    return [_hsv(t * 0.04 + 0.1 * i / n, 1, v) for i in range(n)]


def _peppermint_swirl(t, n, base):
    """Red and white stripes that bend and slide, so they read as a twisting peppermint."""
    u = _unit(n)
    out = []
    for i in range(n):
        x = i / u
        pos = x / 2.6 - t * 0.9 + 0.45 * math.sin(x / 7.0 + t * 0.7)
        m = max(0.0, min(1.0, 0.5 + 0.9 * math.cos(2 * math.pi * pos)))
        out.append(_lerp(WHITE, RED, m))
    return out


def _heartbeat(t, n, base):
    """Base colour with a gentle lub-dub double pulse about every 1.2 s."""
    period = 1.2
    u = _unit(n)
    out = []
    for i in range(n):
        delay = 0.12 * (i / max(1, n - 1)) if u else 0
        c = (t - delay) % period
        v = math.exp(-((c - 0.12) / 0.07) ** 2) + 0.7 * math.exp(-((c - 0.40) / 0.08) ** 2)
        out.append(_scale(base, 0.1 + 0.9 * min(1.0, v)))
    return out


def _meteor_shower(t, n, base):
    """Several meteors streaking along the string (white or rainbow), each with a fading tail."""
    u = _unit(n)
    buf = [[0, 0, 0] for _ in range(n)]
    tail = 9.0 * u
    for speed, spacing, seed in ((24.0, 34.0, 81), (15.0, 52.0, 82)):
        for x, h in _particles(t, n, speed * u, spacing * u, seed, tail):
            if h > 0.75:
                continue
            col = WHITE if h < 0.25 else _hsv(h * 1.7)
            for i in range(max(0, int(x - tail)), min(n, int(x) + 2)):
                d = x - i
                v = max(0.0, 1 - d / tail) ** 2.2 if d >= 0 else max(0.0, 1 + d)
                if v > 0:
                    px = buf[i]
                    px[0] += col[0] * v
                    px[1] += col[1] * v
                    px[2] += col[2] * v
    return [_rgb(*p) for p in buf]


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
    # old incandescent C9 colours: warm, a little soft
    "Vintage Rainbow": _pattern([(230, 18, 0), (255, 90, 0), (255, 170, 20), (20, 150, 25), (0, 50, 200), (230, 60, 90)]),
    "Vintage Pastel": _pattern([(255, 182, 193), (176, 224, 230), (255, 239, 170), (197, 240, 200), (215, 190, 240)]),
    "Retro Christmas": _pattern(RETRO),
    "Icy Blue & White": _pattern([ICE, WHITE, ICE_DEEP, WHITE]),
    "Gold & White": _pattern([GOLD_LIGHT, SOFT_WHITE]),
    "Red/Green/White": _pattern([RED, GREEN, WHITE]),
    "Rainbow Bands": _rainbow_bands,
    "Ombre": _ombre,
    "Snowy": _snowy,
}

ANIMATED = {
    "Rainbow Flow": _rainbow_flow,
    "Rainbow Chase": _rainbow_chase,
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
    "Rainbow Twinkle": _rainbow_twinkle,
    "Vintage Twinkle": _vintage_twinkle,
    "Christmas Twinkle": _christmas_twinkle,
    "Sparkle": _sparkle,
    "Starfield": _starfield,
    "Snowfall": _snowfall,
    "Icicle Drip": _icicle_drip,
    "Candle Flicker": _candle_flicker,
    "Aurora": _aurora,
    "Lava": _lava,
    "Red & Blue": _red_blue,
    "Color Wipe": _color_wipe,
    "Larson Scanner": _larson,
    "Rainbow Breathe": _rainbow_breathe,
    "Peppermint Swirl": _peppermint_swirl,
    "Heartbeat": _heartbeat,
    "Meteor Shower": _meteor_shower,
}

EFFECTS = {**STILL, **ANIMATED}
EFFECT_LIST = list(EFFECTS)
