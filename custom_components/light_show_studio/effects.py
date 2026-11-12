"""Effects: each is a function (t seconds, bulb count, base colour) -> list of colours.

STILL effects are drawn once; the rest are animated by the coordinator.
"""
from __future__ import annotations

import functools
import math
import random

from .protocol import Colour

RED, GREEN, WHITE, BLACK = (255, 0, 0), (0, 255, 0), (255, 255, 255), (0, 0, 0)
ORANGE, PURPLE, WARM = (255, 80, 0), (140, 0, 255), (255, 150, 40)
BLUE, PINK, GOLD = (0, 40, 255), (255, 20, 110), (255, 140, 0)
PASTELS = [(255, 120, 170), (120, 200, 255), (255, 220, 90), (150, 255, 140), (190, 130, 255)]

# A warm white for RGB-only bulbs (about 3000 K: the red, green and blue ratio of that colour temperature). Pure
# (255, 255, 255) is a cold, blue-green white on RGB LEDs; use this wherever a palette means "warm white". Keep
# (255, 255, 255) for lights with a real white channel.
WARM_WHITE = (255, 176, 100)

# Colour sets that patterns.py and the effects below both use, defined once. Where the two files had their own
# versions of the same name they differ visibly, so both are kept here under explicit names and nothing changes
# on screen: CLASSIC and PASTELS are shared as they were; Halloween and Gold & White exist as the string-effect
# set (two colours; soft gold and a soft white) and the C9 pattern set (three colours; deeper orange and a warm cream).
CLASSIC = [RED, GREEN, (0, 60, 255), ORANGE, (255, 0, 120)]  # "Classic Multicolor" pattern == "Classic Multicolour" effect
HALLOWEEN_EFFECT = [ORANGE, PURPLE]
HALLOWEEN_C9 = [(255, 70, 0), PURPLE, (40, 255, 0)]
GOLD_WHITE_C9 = [(255, 140, 0), (255, 200, 140)]


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> Colour:
    # colorsys.hsv_to_rgb written out: the same arithmetic (so the same colours) without the extra call
    h = h % 1.0
    if s == 0.0:
        return int(v * 255), int(v * 255), int(v * 255)
    i = int(h * 6.0)
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    k = v * (1.0 - s * (1.0 - f))
    i = i % 6
    if i == 0:
        return int(v * 255), int(k * 255), int(p * 255)
    if i == 1:
        return int(q * 255), int(v * 255), int(p * 255)
    if i == 2:
        return int(p * 255), int(v * 255), int(k * 255)
    if i == 3:
        return int(p * 255), int(q * 255), int(v * 255)
    if i == 4:
        return int(k * 255), int(p * 255), int(v * 255)
    return int(v * 255), int(p * 255), int(q * 255)


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
    append = out.append
    shift = t * speed
    for i in range(n):
        pos = ((i - shift) / stripe) % 2.0  # 0-1 red, 1-2 white
        e = pos % 1.0
        edge = min(e, 1 - e)  # distance to nearest stripe edge
        mix = 0.5 + min(edge * 2.5, 0.5)  # 0.5 at the edge, 1.0 in the middle
        a, b = (RED, WHITE) if pos < 1 else (WHITE, RED)
        rest = 1 - mix
        append((int(a[0] * mix + b[0] * rest), int(a[1] * mix + b[1] * rest), int(a[2] * mix + b[2] * rest)))
    return out


def _comet(t, n, base):
    head = (t * 12) % (n + 10)
    return [_scale(base, 1 - (head - i) / 8) if 0 <= head - i < 8 else BLACK for i in range(n)]


def _theater(t, n, base):
    step = int(t * 6) % 3
    return [base if i % 3 == step else BLACK for i in range(n)]


def _breathe(t, n, base):
    return [_scale(base, 0.15 + 0.85 * (0.5 + 0.5 * math.sin(t * 2)))] * n


@functools.lru_cache(maxsize=1 << 16)  # pure, and the same for every frame of a slot: computed once, not every frame
def event_window(i: int, k: int, seed: int, slot: float, prob: float, dmin: float, dmax: float):
    """(start, duration, peak 0.55-1, hash 0-1) in seconds from the start of slot k for bulb i, or None when the
    slot holds no event. Never longer than the slot, so events never overlap."""
    s = i + seed * 7919
    if _hash(s, k * 4 + 1) >= prob:
        return None
    dur = min(slot, dmin + (dmax - dmin) * _hash(s, k * 4 + 2))
    return (slot - dur) * _hash(s, k * 4 + 3), dur, 0.55 + 0.45 * _hash(s, k * 4 + 4), _hash(s, k * 4 + 5)


def event_at(t: float, i: int, seed: int, slot: float, prob: float, dmin: float, dmax: float):
    """Random one-off events for bulb i, as a pure function of wall time t (nothing is remembered, so a late or
    skipped frame just lands on the right state, and drawing more or fewer frames changes nothing).

    Each bulb has its own clock, offset at random, cut into `slot`-second slots. A slot holds an event with
    probability `prob`; the event lasts a random dmin..dmax seconds and starts at a random moment inside the slot,
    with a random peak. Gaps between a bulb's events are therefore irregular (about slot / prob apart on average)
    and bulbs never line up. Returns (progress 0-1 through the event, peak, extra hash) or None when resting."""
    x = t / slot + _offset(i, seed)
    k = math.floor(x)
    w = event_window(i, k, seed, slot, prob, dmin, dmax)
    if w is None:
        return None
    start, dur, peak, h = w
    s = (x - k) * slot - start
    return (s / dur, peak, h) if 0.0 <= s < dur else None


@functools.lru_cache(maxsize=1 << 16)
def _offset(i: int, seed: int) -> float:
    return _hash(i, seed)


@functools.lru_cache(maxsize=64)
def _offsets(n: int, seed: int) -> tuple[float, ...]:
    """_offset(i, seed) for bulbs 0..n-1, kept: a frame reads them in one go."""
    return tuple(_offset(i, seed) for i in range(n))


def events_row(t: float, n: int, seed: int, slot: float, prob: float, dmin: float, dmax: float) -> list:
    """event_at for every bulb 0..n-1 in one pass (exactly the same values): None, or (progress, peak, hash)."""
    x0 = t / slot
    floor, window = math.floor, event_window
    out = []
    append = out.append
    for i, off in enumerate(_offsets(n, seed)):
        x = x0 + off
        k = floor(x)
        w = window(i, k, seed, slot, prob, dmin, dmax)
        if w is None:
            append(None)
            continue
        s = (x - k) * slot - w[0]
        append((s / w[1], w[2], w[3]) if 0.0 <= s < w[1] else None)
    return out


@functools.lru_cache(maxsize=8)
def _xu(n: int) -> tuple[float, ...]:
    """Bulb positions in units of _unit(n): i / u for every bulb (what the flowing effects measure along)."""
    u = _unit(n)
    return tuple(i / u for i in range(n))


def twinkle_level(t: float, i: int, seed: int = 0) -> float:
    """0-1: how far bulb i is brightened above its resting level right now. Few bulbs (about one in five) are
    swelling at any moment, each for 0.7-1.5 s at a random time; the rest hold still."""
    ev = event_at(t, i, seed + 1, 3.0, 0.6, 0.7, 1.5)
    return 0.0 if ev is None else ev[1] * math.sin(math.pi * ev[0]) ** 2


def sparkle_level(t: float, i: int, seed: int = 0) -> float:
    """0-1: a white glint on bulb i: pops up almost at once, then fades over 0.15-0.45 s. About one bulb in ten
    at a time, at random moments."""
    ev = event_at(t, i, seed + 2, 1.2, 0.4, 0.15, 0.45)
    if ev is None:
        return 0.0
    u, peak, _ = ev
    return (0.7 + 0.3 * peak) * (u / 0.1 if u < 0.1 else (1 - (u - 0.1) / 0.9) ** 2)


TWINKLE_FLOOR = 0.32  # bulbs rest here and swell up to full


def _twinkle(t, n, base):
    rest = _scale(base, TWINKLE_FLOOR + (1 - TWINKLE_FLOOR) * 0.0)
    sin, pi = math.sin, math.pi
    return [rest if ev is None else _scale(base, TWINKLE_FLOOR + (1 - TWINKLE_FLOOR) * (ev[1] * sin(pi * ev[0]) ** 2))
            for ev in events_row(t, n, 6, 3.0, 0.6, 0.7, 1.5)]  # twinkle_level(t, i, 5) for every bulb


@functools.lru_cache(maxsize=8)
def _fire_offsets(n: int) -> tuple[float, ...]:
    return tuple(_offset(i, 91) * 997.0 for i in range(n))


# Fire's noise: for each of its three seeds, lattice point -> (the noise there, the noise at the next point)
_fire_lattice: tuple[dict[int, tuple[float, float]], ...] = ({}, {}, {})


def _fire_pair(k: int, which: int) -> tuple[float, float]:
    pair = _fire_lattice[which][k] = (_offset(k, 92 + which), _offset(k + 1, 92 + which))
    return pair


def _fire(t, n, base):
    """Flames: the same orange-red flicker as ever, but each bulb follows smooth noise in time (and a bit of noise
    along the string, so flames seem to lick upward) instead of a fresh random number every frame. A pure function
    of t: the same t always gives the same frame, however often frames are drawn."""
    out = []
    append = out.append
    floor = math.floor
    for d in _fire_lattice:
        if len(d) > 1 << 15:
            d.clear()
    flicker, lick, tint = (d.get for d in _fire_lattice)
    pair_of = _fire_pair
    t8, t4, t5 = t * 8.0, t * 4.0, t * 5.0
    last, along = None, (0.0, 0.0)
    for i, off in enumerate(_fire_offsets(n)):  # the three _vnoise calls, written out
        x = t8 + off
        k = floor(x)
        f = x - k
        pair = flicker(k) or pair_of(k, 0)
        a = pair[0]
        heat = a + (pair[1] - a) * (f * f * (3 - 2 * f))
        x = i * 0.45 - t4
        k = floor(x)
        f = x - k
        if k != last:  # the noise along the string: neighbours share a lattice cell
            along = lick(k) or pair_of(k, 1)
            last = k
        a = along[0]
        heat = 0.6 * heat + 0.4 * (a + (along[1] - a) * (f * f * (3 - 2 * f)))
        x = t5 + off * 0.5
        k = floor(x)
        f = x - k
        pair = tint(k) or pair_of(k, 2)
        a = pair[0]
        tone = a + (pair[1] - a) * (f * f * (3 - 2 * f))
        heat = max(0.0, min(1.0, (heat - 0.5) * 1.6 + 0.5))
        tone = max(0.0, min(1.0, (tone - 0.5) * 1.6 + 0.5))
        v = 0.55 + heat * 0.45
        # _hsv(0.02 + tone * 0.06, 1, v): the hue stays in the first sixth of the wheel, so red is v and blue is 0
        append((int(v * 255), int(v * (1.0 - (1.0 - (0.02 + tone * 0.06) * 6.0)) * 255), 0))
    return out


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


hash01 = _hash  # public name for other modules


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


def _twinkler(colour, slot: float, prob: float, floor: float, seed: int, dmin: float = 0.6, dmax: float = 1.8):
    """Each bulb keeps its own colour and, at random moments, swells up from `floor` and settles again;
    `colour(r, v)` picks the shade from the bulb's fixed hash r and the level v. Only a few bulbs at once."""
    @functools.lru_cache(maxsize=8)
    def resting(n):  # what every bulb shows between its events: the same every frame
        return tuple(colour(r, floor) for r in _offsets(n, seed + 5))

    def fx(t, n, base):
        sin, pi = math.sin, math.pi
        return [rest if ev is None else colour(r, floor + (1 - floor) * ev[1] * sin(pi * ev[0]) ** 2)
                for r, rest, ev in zip(_offsets(n, seed + 5), resting(n), events_row(t, n, seed, slot, prob, dmin, dmax))]
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


_rainbow_twinkle = _twinkler(lambda r, v: _hsv(r, 1, v), 3.0, 0.6, 0.12, 11)
_vintage_twinkle = _twinkler(lambda r, v: _scale(VINTAGE[int(r * len(VINTAGE))], 0.2 + 0.8 * v), 3.2, 0.6, 0.0, 23)
_christmas_twinkle = _twinkler(
    lambda r, v: _scale([RED, GREEN, GOLD_LIGHT][int(r * 3)], 0.12 + 0.88 * v), 3.0, 0.6, 0.0, 37)


def _sparkle(t, n, base):
    """Base colour with quick white glints that pop at random bulbs and moments, then fade."""
    dim = _scale(base, 0.75)
    out = []
    append = out.append
    for ev in events_row(t, n, 9, 1.2, 0.4, 0.15, 0.45):  # sparkle_level(t, i, 7) for every bulb
        if ev is None:
            append(dim)
            continue
        u, peak, _ = ev
        m = (0.7 + 0.3 * peak) * (u / 0.1 if u < 0.1 else (1 - (u - 0.1) / 0.9) ** 2)
        append(_lerp(dim, WHITE, m) if m > 0 else dim)
    return out


@functools.lru_cache(maxsize=8)
def _star_clocks(n: int) -> tuple[tuple[float, float], ...]:
    return tuple((4.0 + 5.0 * _offset(i, 41), _offset(i, 42)) for i in range(n))


@functools.lru_cache(maxsize=1 << 14)
def _star(i: int, k: int):
    """The colour of bulb i's star in its slot k, or None when that slot has no star."""
    if _hash(i, k + 4242) < 0.13:
        return _lerp((190, 210, 255), (255, 235, 200), _hash(i, k + 77))
    return None


def _starfield(t, n, base):
    """Dark night with occasional stars that flash up and fade slowly."""
    night = (0, 0, 6)
    out = []
    append = out.append
    star_of = _star
    for i, (period, off) in enumerate(_star_clocks(n)):
        x = t / period + off
        k = int(x)
        star = star_of(i, k)
        if star is None:
            append(night)
            continue
        u = x - k
        append(_lerp(night, star, min(u / 0.05, 1.0) * (1 - u) ** 2))
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
    out = []
    append = out.append
    sin = math.sin
    t1, t2, t3 = t * 0.30, t * 0.23, t * 0.41
    for x in _xu(n):
        v = (sin(x * 0.33 + t1) + sin(x * 0.17 - t2) + sin(x * 0.58 + t3)) / 6 + 0.5
        v = max(0.0, min(1.0, v * 1.15 - 0.05))
        if v < 0.5:  # _lerp((70, 0, 0), (255, 60, 0), v * 2)
            m = v * 2
            append((int(70 + 185 * m), int(60 * m), 0))
        else:  # _lerp((255, 60, 0), (255, 160, 15), (v - 0.5) * 2)
            m = (v - 0.5) * 2
            append((255, int(60 + 100 * m), int(15 * m)))
    return out


def _red_blue(t, n, base):
    """Soft red/blue alternation (about 0.8 swaps per second, crossfaded, never a hard strobe)."""
    block = max(2, n // 6)
    s = math.sin(2 * math.pi * 0.8 * t)
    even = _lerp(BLUE, RED, 0.5 + 0.5 * max(-1.0, min(1.0, s * 1 * 1.6)))
    odd = _lerp(BLUE, RED, 0.5 + 0.5 * max(-1.0, min(1.0, s * -1 * 1.6)))
    return [even if (i // block) % 2 == 0 else odd for i in range(n)]


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
    out = []
    append = out.append
    sin, cos, tau = math.sin, math.cos, 2 * math.pi
    slide, bend = t * 0.9, t * 0.7
    for x in _xu(n):
        pos = x / 2.6 - slide + 0.45 * sin(x / 7.0 + bend)
        m = max(0.0, min(1.0, 0.5 + 0.9 * cos(tau * pos)))
        g = int(255 + -255 * m)  # _lerp(WHITE, RED, m)
        append((255, g, g))
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


# --- Palettes: gradient stops, sampled smoothly ------------------------------------------------------
# Written from scratch: the OKLab matrices are Bjorn Ottosson's published ones (public domain).

def _to_lin(v: int) -> float:
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_lin(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 255.0 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1 / 3), x)


def rgb_to_oklab(c: Colour) -> tuple[float, float, float]:
    r, g, b = _to_lin(c[0]), _to_lin(c[1]), _to_lin(c[2])
    l_ = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab_to_rgb(lab: tuple[float, float, float]) -> Colour:
    L, a, b = lab
    l_, m_, s_ = L + 0.3963377774 * a + 0.2158037573 * b, L - 0.1055613458 * a - 0.0638541728 * b, \
        L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (int(round(_from_lin(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s))),
            int(round(_from_lin(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s))),
            int(round(_from_lin(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))))


class Palette:
    """A colour gradient: stops of (position 0-1, RGB), blended in OKLab (smooth, even brightness) or plain RGB
    ("linear"). `at(x)` gives the colour at x: with wrap the gradient loops (the last stop blends back into the
    first), otherwise x is clamped to 0-1. Immutable once built; sampling is a table lookup."""

    SIZE = 512
    __slots__ = ("stops", "mode", "wrap", "_lut")

    def __init__(self, stops, mode: str = "oklab", wrap: bool = True) -> None:
        if mode not in ("oklab", "linear"):
            raise ValueError("mode must be 'oklab' or 'linear'")
        pts = sorted(((max(0.0, min(1.0, float(p))), (int(c[0]), int(c[1]), int(c[2]))) for p, c in stops),
                     key=lambda s: s[0])
        self.stops = tuple(pts) if pts else ((0.0, WHITE),)
        self.mode, self.wrap = mode, bool(wrap)
        self._lut = self._build()

    @classmethod
    def even(cls, colours, mode: str = "oklab", wrap: bool = True) -> "Palette":
        """Colours spread evenly (a looping palette puts them at k/K so the loop closes cleanly)."""
        cols = list(colours) or [WHITE]
        k = len(cols)
        step = 1.0 / (k if wrap else max(1, k - 1))
        return cls([(i * step, c) for i, c in enumerate(cols)], mode, wrap)

    def _build(self) -> tuple[Colour, ...]:
        st, wrap, size = self.stops, self.wrap, self.SIZE
        conv = rgb_to_oklab if self.mode == "oklab" else (lambda c: (float(c[0]), float(c[1]), float(c[2])))
        back = oklab_to_rgb if self.mode == "oklab" else (lambda v: _rgb(round(v[0]), round(v[1]), round(v[2])))
        labs = [conv(c) for _, c in st]
        pos = [p for p, _ in st]
        last = len(st) - 1
        lut = []
        for k in range(size):
            x = k / size if wrap else k / (size - 1)
            if x <= pos[0] and not wrap:
                lut.append(st[0][1])
                continue
            if x >= pos[last] and not wrap:
                lut.append(st[last][1])
                continue
            if x < pos[0]:  # wrap: from the last stop round to the first
                a, b, pa, pb = last, 0, pos[last] - 1.0, pos[0]
            elif x >= pos[last]:
                a, b, pa, pb = last, 0, pos[last], pos[0] + 1.0
            else:
                a = 0
                while pos[a + 1] <= x:
                    a += 1
                b, pa, pb = a + 1, pos[a], pos[a + 1]
            f = (x - pa) / (pb - pa) if pb > pa else 0.0
            la, lb = labs[a], labs[b]
            lut.append(back((la[0] + (lb[0] - la[0]) * f, la[1] + (lb[1] - la[1]) * f, la[2] + (lb[2] - la[2]) * f)))
        return tuple(lut)

    def at(self, x: float, wrap: bool | None = None) -> Colour:
        size = self.SIZE
        if self.wrap if wrap is None else wrap:
            return self._lut[int((x % 1.0) * size) % size]
        return self._lut[int(max(0.0, min(1.0, x)) * (size - 1) + 0.5)]


@functools.lru_cache(maxsize=64)
def palette_for(colours: tuple, mode: str = "oklab", wrap: bool = True) -> Palette:
    """A looping palette of evenly spaced colours (cached: building one costs about a millisecond)."""
    return Palette.even(colours, mode, wrap)


# Gradients the effect list uses (the pattern effects use the pattern's own colours instead).
GRAD_DUSK = Palette([(0.0, (50, 0, 110)), (0.28, (255, 30, 100)), (0.55, (255, 110, 0)), (0.8, (255, 200, 50))])
GRAD_PARTY = Palette.even([(255, 0, 140), (120, 0, 255), (0, 120, 255), (0, 230, 200), (255, 220, 0)])
GRAD_DRIFT = Palette.even([(0, 60, 160), (0, 180, 200), (30, 220, 120), (90, 60, 240)])
CONFETTI_COLOURS = [(255, 40, 70), (255, 150, 0), (255, 230, 30), (30, 220, 80), (0, 190, 255), (150, 70, 255), (255, 60, 200)]


# --- Smooth noise (deterministic: a hash at the lattice points, no state) ----------------------------------
def _vnoise(x: float, seed: int = 0) -> float:
    """Smooth 1-D value noise in [0, 1)."""
    i = math.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f)
    a = _offset(i, seed)
    return a + (_offset(i + 1, seed) - a) * u


def _vnoise2(x: float, y: float, seed: int = 0) -> float:
    """Smooth 2-D value noise in [0, 1)."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b, c, d = _corners(ix, seed + 31 * iy)
    top = a + (b - a) * ux
    return top + (c + (d - c) * ux - top) * uy


def _vnoise2_row(xs, y: float, seed: int = 0) -> list[float]:
    """_vnoise2(x, y, seed) for every x in xs (the same values): what depends only on y is worked out once, and a
    lattice cell's corners are looked up once per cell instead of once per bulb."""
    iy = math.floor(y)
    fy = y - iy
    uy = fy * fy * (3 - 2 * fy)
    s = seed + 31 * iy
    floor, corners = math.floor, _corners
    last = None
    a = b = c = d = 0.0
    out = []
    append = out.append
    for x in xs:
        ix = floor(x)
        if ix != last:
            a, b, c, d = corners(ix, s)
            last = ix
        fx = x - ix
        ux = fx * fx * (3 - 2 * fx)
        top = a + (b - a) * ux
        append(top + (c + (d - c) * ux - top) * uy)
    return out


@functools.lru_cache(maxsize=1 << 14)  # a lattice cell's corners repeat for many pixels and frames
def _corners(ix: int, s: int) -> tuple[float, float, float, float]:
    return _hash(ix, s), _hash(ix + 1, s), _hash(ix, s + 31), _hash(ix + 1, s + 31)


def _smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# --- Palette effects: each is (t, n, palette ...) -> n colours, a pure function of t ---------------------------
def gradient_flow(t: float, n: int, pal: Palette, speed: float = 0.07) -> list[Colour]:
    """The palette as a gradient scrolling along the string (about 36 bulbs per loop on 50 bulbs, longer on long
    strips). `speed` is loops per second."""
    k = 1.0 / (36.0 * _unit(n))
    shift = t * speed
    if pal.wrap:  # pal.at, written out
        lut, size = pal._lut, pal.SIZE  # noqa: SLF001
        return [lut[int(((i * k - shift) % 1.0) * size) % size] for i in range(n)]
    at = pal.at
    return [at(i * k - shift) for i in range(n)]


def plasma(t: float, n: int, pal: Palette) -> list[Colour]:
    """A sine-sum plasma field, coloured through the palette: soft blobs of colour that swell and slide."""
    sin = math.sin
    drift = t * 0.03
    t1, t2, t3, t4, t5 = t * 0.9, t * 0.55, t * 0.31, t * 0.7, t * 0.25
    vals = [0.5 + 0.5 * (sin(x * 0.21 + t1) + sin(x * 0.083 - t2 + 1.3) + sin(x * 0.047 + t3 + 2.1)
                         + sin(x * 0.06 + sin(x * 0.13 + t4) * 1.6 + t5)) / 3.2 + drift for x in _xu(n)]
    if pal.wrap:  # pal.at, written out
        lut, size = pal._lut, pal.SIZE  # noqa: SLF001
        return [lut[int((v % 1.0) * size) % size] for v in vals]
    return [pal.at(v) for v in vals]


@functools.lru_cache(maxsize=8)
def _drift_xs(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    xs = _xu(n)
    return tuple(x / 9.0 for x in xs), tuple(x / 3.7 + 17.0 for x in xs)


def noise_drift(t: float, n: int, pal: Palette) -> list[Colour]:
    """Colour drifting through the palette on slow, smooth value noise (two octaves), so patches of the palette
    grow, slide and melt into each other."""
    wide, fine = _drift_xs(n)
    shift = t * 0.012
    vals = [(0.65 * a + 0.35 * b - 0.5) * 1.7 + 0.5 + shift
            for a, b in zip(_vnoise2_row(wide, t * 0.18, 201), _vnoise2_row(fine, t * 0.31, 202))]
    if pal.wrap:  # pal.at, written out
        lut, size = pal._lut, pal.SIZE  # noqa: SLF001
        return [lut[int((v % 1.0) * size) % size] for v in vals]
    return [pal.at(v) for v in vals]


@functools.lru_cache(maxsize=8)
def _storm_xs(n: int) -> tuple[float, ...]:
    return tuple(i / 6.0 for i in range(n))


def storm_flash(t: float, n: int, dark: Colour, flash: Colour, seed: int = 0) -> list[Colour]:
    """A dark, faintly rolling sky with lightning. Every 2.6 s slot may hold one strike (decided by a hash of the
    slot number); a strike is 2-3 quick flickers, each lighting a stretch of the string around a point that
    spreads outward, with a dimmer glow over the whole string. Nothing is remembered between frames."""
    slot = 2.6
    k = math.floor(t / slot)
    out = None
    w = event_window(0, k, seed + 900, slot, 0.7, 0.5, 1.6)
    s = 0.0
    if w is not None:
        start, dur, peak, h = w
        s = t - k * slot - start
        if not 0.0 <= s < dur:
            w = None
    pulses = []
    if w is not None:
        n_pulses = 2 + int(_hash(k, seed + 905) * 2.0)  # 2 or 3
        for j in range(n_pulses):
            o = (0.0 if j == 0 else 0.12 + 0.55 * _hash(k, seed + 906 + j)) * dur
            d = s - o
            if d >= 0.0:
                amp = (1.0 if j == 0 else 0.45 + 0.4 * _hash(k, seed + 910 + j)) * min(1.0, d / 0.02) * math.exp(-d / 0.17)
                centre = (h + (0.0 if j == 0 else 0.12 * (_hash(k, seed + 914 + j) - 0.5))) * (n - 1)
                reach = (0.12 + 0.88 * min(1.0, d / 0.25)) * 0.5 * max(1, n - 1) * (0.5 + 0.5 * peak)
                pulses.append((amp * peak, centre, max(0.5, reach)))
    glow = max((p[0] for p in pulses), default=0.0) * 0.2
    out = []
    skies = _vnoise2_row(_storm_xs(n), t * 0.25, 221)
    if not pulses:  # no strike: just the rolling sky
        d0, d1, d2 = dark
        for nz in skies:
            sky = 0.75 + 0.5 * nz
            out.append((max(0, min(255, int(d0 * sky))), max(0, min(255, int(d1 * sky))),
                        max(0, min(255, int(d2 * sky)))))
        return out
    for i, nz in enumerate(skies):
        sky = 0.75 + 0.5 * nz
        v = glow
        for amp, centre, reach in pulses:
            d = abs(i - centre)
            if d < reach:
                v = max(v, amp * (1 - d / reach) ** 0.7)
        base = (dark[0] * sky, dark[1] * sky, dark[2] * sky)
        m = min(1.0, v)
        out.append(_rgb(base[0] + (flash[0] - base[0]) * m, base[1] + (flash[1] - base[1]) * m,
                        base[2] + (flash[2] - base[2]) * m))
    return out


def confetti(t: float, n: int, cols: list[Colour], bg: list[Colour] | None = None, seed: int = 0) -> list[Colour]:
    """Colours that pop on random bulbs at random moments and fade away over about a second, over a dim backdrop
    (`bg`, one colour per bulb, default near black). Each pop picks its own colour from `cols` (a rainbow hue when
    `cols` is empty)."""
    out = []
    append = out.append
    count = len(cols)
    for i, ev in enumerate(events_row(t, n, seed + 301, 1.6, 0.45, 0.6, 1.3)):
        back = bg[i] if bg is not None else (2, 2, 6)
        if ev is None:
            append(back)
            continue
        u, peak, h = ev
        v = peak * (u / 0.07 if u < 0.07 else (1 - (u - 0.07) / 0.93) ** 2)
        c = cols[int(h * count) % count] if cols else _hsv(h)
        append(_lerp(back, c, v))
    return out


@functools.lru_cache(maxsize=16)
def _dissolve_times(n: int, key: int, fill: float, hold_end: float, drain: float) -> tuple[tuple[float, float], ...]:
    """When each bulb fills in and when it drains, for one round (the same for every frame of the round)."""
    return tuple((_hash(i, key + 401) * fill, hold_end + _hash(i, key + 402) * drain) for i in range(n))


def dissolve(t: float, n: int, base: list[Colour], seed: int = 0, period: float = 12.0) -> list[Colour]:
    """Bulbs fill in at random moments until the string is full, hold, then drain away in another random order,
    then rest dark before the next round. Each round (a `period`) shuffles the order again."""
    cyc = math.floor(t / period)
    q = t - cyc * period
    fill, hold_end, drain = 4.5, 6.5, 4.5  # seconds: filling takes up to 4.5, draining runs 6.5 .. 11
    out = []
    append = out.append
    for i, (a, d) in enumerate(_dissolve_times(n, cyc * 2 + seed, fill, hold_end, drain)):
        x = (q - a) / 0.4  # _smooth((q - a) / 0.4) * (1.0 - _smooth((q - d) / 0.4)), written out
        if x <= 0.0:
            f = 0.03
        else:
            v = 1.0 if x >= 1.0 else x * x * (3 - 2 * x)
            x = (q - d) / 0.4
            if x > 0.0:
                v = v * (1.0 - (1.0 if x >= 1.0 else x * x * (3 - 2 * x)))
            f = max(0.0, min(1.0, 0.03 + 0.97 * v))
        c = base[i]
        append((int(c[0] * f), int(c[1] * f), int(c[2] * f)))
    return out


EMBER = (255, 40, 0)


def sunrise_ramp(t: float, n: int, base: list[Colour], duration: float = 60.0, hold: float = 15.0) -> list[Colour]:
    """A slow warm-up: from a dim ember glow through orange to the full colours over `duration` seconds, spreading
    in from the start of the string, holding for `hold` seconds, dipping for 4 s and starting over."""
    fall = 4.0
    q = t % (duration + hold + fall)
    if q < duration:
        level = _smooth(q / duration)
    elif q < duration + hold:
        level = 1.0
    else:
        level = 1.0 - _smooth((q - duration - hold) / fall)
    top = max(1, n - 1)
    lead = level * 1.35
    e0, e1, e2 = EMBER
    m_dark, f_dark = (1.0 - 0.0) ** 1.5 * 0.85, max(0.0, min(1.0, 0.02 + 0.98 * 0.0 ** 1.8))  # not yet reached
    m_full, f_full = (1.0 - 1.0) ** 1.5 * 0.85, max(0.0, min(1.0, 0.02 + 0.98 * 1.0 ** 1.8))  # fully up
    out = []
    append = out.append
    for i in range(n):
        local = lead - 0.35 * i / top
        if local >= 1.0:
            m, f = m_full, f_full
        elif local <= 0.0:
            m, f = m_dark, f_dark
        else:
            m = (1.0 - local) ** 1.5 * 0.85  # how far towards the ember colour
            f = max(0.0, min(1.0, 0.02 + 0.98 * local ** 1.8))
        c = base[i]
        r, g, b = int(c[0] + (e0 - c[0]) * m), int(c[1] + (e1 - c[1]) * m), int(c[2] + (e2 - c[2]) * m)
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        append((int(r * f), int(g * f), int(b * f)))
    return out


def lightest(cols: list[Colour]) -> Colour:
    """The brightest colour of a list (white when the list is empty)."""
    return max(cols, key=sum) if cols else WHITE


def storm_colours(cols: list[Colour]) -> tuple[Colour, Colour]:
    """(dark sky, flash) for a palette: the sky is the palette's average at about 6 per cent, the bolt its lightest
    colour pulled 60 per cent towards white."""
    if not cols:
        return (3, 5, 14), (225, 232, 255)
    k = len(cols)
    avg = (sum(c[0] for c in cols) / k, sum(c[1] for c in cols) / k, sum(c[2] for c in cols) / k)
    return _rgb(avg[0] * 0.06, avg[1] * 0.06, avg[2] * 0.06), _lerp(lightest(cols), WHITE, 0.6)


def _gradient_flow_fx(t, n, base):
    return gradient_flow(t, n, GRAD_DUSK)


def _plasma_fx(t, n, base):
    return plasma(t, n, GRAD_PARTY)


def _noise_drift_fx(t, n, base):
    return noise_drift(t, n, GRAD_DRIFT)


def _storm_flash_fx(t, n, base):
    return storm_flash(t, n, _scale(base, 0.05), _lerp(base, WHITE, 0.6), 0)


def _confetti_fx(t, n, base):
    return confetti(t, n, CONFETTI_COLOURS)


def _dissolve_fx(t, n, base):
    return dissolve(t, n, [base] * n)


def _sunrise_fx(t, n, base):
    return sunrise_ramp(t, n, [base] * n)


# --- More effects ---------------------------------------------------------------------------------------------
# Pure functions of t like the ones above, and gentle: shapes move and levels swell, nothing flashes on and off
# (no bulb goes dark-bright-dark faster than about twice a second).
BALL_COLOURS = [(255, 30, 20), (255, 170, 0), (0, 120, 255)]
GRAD_EMBER = Palette([(0.0, (24, 0, 0)), (0.4, (140, 10, 0)), (0.75, (255, 55, 0)), (1.0, (255, 150, 30))], wrap=False)


def _dots(n: int, dots, back: Colour = BLACK) -> list[Colour]:
    """n pixels of `back` with soft dots of light added on top: each dot is (centre, radius, colour, level 0-1).
    Only the pixels a dot touches are worked on."""
    out = [back] * n
    acc: dict[int, list[float]] = {}
    for x, w, col, g in dots:
        for i in range(max(0, int(x - w)), min(n, int(x + w) + 2)):
            v = (1 - abs(i - x) / w) * g
            if v > 0:
                px = acc.get(i)
                if px is None:
                    px = acc[i] = [float(back[0]), float(back[1]), float(back[2])]
                px[0] += col[0] * v
                px[1] += col[1] * v
                px[2] += col[2] * v
    for i, px in acc.items():
        out[i] = _rgb(px[0], px[1], px[2])
    return out


@functools.lru_cache(maxsize=16)
def _ball_plan(b: int):
    """Ball b's round: (start, length, height 0-1) of each bounce, each lower and shorter than the last, then how
    long the round takes (with a short rest on the ground) and where in it the ball is at t = 0."""
    first, height = 2.2 + 0.8 * _hash(b, 501), 0.65 + 0.35 * _hash(b, 502)
    plan, at = [], 0.0
    for k in range(4):
        keep = 0.55 ** k
        plan.append((at, first * math.sqrt(keep), height * keep))
        at += first * math.sqrt(keep)
    return tuple(plan), at + 0.5, _hash(b, 503)


def _bouncing_balls(t, n, base):
    """Three coloured balls thrown up from the start of the string: each falls back, bounces lower and lower,
    rests a moment and is thrown again, every ball in its own time. (On a long strip they rise part of the way.)"""
    u = _unit(n)
    size = 1.6 * u
    top = max(0.0, min(n - 1 - size, 45.0 * u))
    dots = []
    for b, col in enumerate(BALL_COLOURS):
        plan, cycle, phase = _ball_plan(b)
        q = (t + phase * cycle) % cycle
        y = 0.0
        for start, length, height in plan:
            if start <= q < start + length:
                p = (q - start) / length
                y = top * height * 4 * p * (1 - p)
                break
        dots.append((y, size, col, 1.0))
    return _dots(n, dots)


def _ripples(t, n, base):
    """Drops landing at random places: from each, two soft wave fronts run outward along the string and fade,
    over a faint glow of the base colour."""
    u = _unit(n)
    slot, life = 0.9, 2.6  # a drop may land in every slot; its rings last this long
    speed, width = 9.0 * u, 2.2 * u
    back, tint = _scale(base, 0.07), _lerp(base, WHITE, 0.3)
    newest = math.floor(t / slot)
    level: dict[int, float] = {}
    for k in range(newest - 3, newest + 1):
        if _hash(k, 521) >= 0.75:
            continue
        age = t - (k + 0.5 * _hash(k, 522)) * slot
        if not 0.0 <= age < life:
            continue
        centre = _hash(k, 523) * (n - 1)
        gain = min(1.0, age / 0.2) * (1 - age / life) ** 1.5
        reach = age * speed
        for x in (centre - reach, centre + reach):
            for i in range(max(0, int(x - width)), min(n, int(x + width) + 2)):
                v = (1 - abs(i - x) / width) * gain
                if v > level.get(i, 0.0):
                    level[i] = v
    out = [back] * n
    for i, v in level.items():
        out[i] = _lerp(back, tint, v)
    return out


def _pulse_wave(t, n, base):
    """Soft pulses of the base colour running from the middle of the string out to both ends, about one every two
    seconds, their crests a little lighter. A smooth swell, never a flash."""
    mid, k, shift = (n - 1) / 2, 0.42 / _unit(n), t * 3.0
    b0, b1, b2 = base
    c0, c1, c2 = _lerp(base, WHITE, 0.35)
    sin = math.sin
    out = []
    append = out.append
    for i in range(n):
        v = 0.5 + 0.5 * sin(abs(i - mid) * k - shift)
        v *= v
        w = v * v  # only the crest lightens
        f = 0.06 + 0.94 * v
        append((int((b0 + (c0 - b0) * w) * f), int((b1 + (c1 - b1) * w) * f), int((b2 + (c2 - b2) * w) * f)))
    return out


@functools.lru_cache(maxsize=8)
def _ember_xs(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    xs = _xu(n)
    return tuple(x / 4.5 for x in xs), tuple(x / 1.6 + 9.0 for x in xs)


def _ember_glow(t, n, base):
    """A bed of coals: deep red that slowly brightens and dims in drifting patches, and now and then one ember
    flares orange and settles again. Much slower and calmer than Fire."""
    bed, fine = _ember_xs(n)
    lut, top = GRAD_EMBER._lut, Palette.SIZE - 1  # noqa: SLF001
    sin, pi = math.sin, math.pi
    out = []
    append = out.append
    for a, b, ev in zip(_vnoise2_row(bed, t * 0.2, 531), _vnoise2_row(fine, t * 0.45, 532),
                        events_row(t, n, 533, 5.0, 0.3, 1.2, 2.8)):
        v = 0.08 + 0.62 * (0.6 * a + 0.4 * b)
        if ev is not None:
            v += 0.45 * ev[1] * sin(pi * ev[0]) ** 2
        append(lut[int((v if v < 1.0 else 1.0) * top + 0.5)])
    return out


def _rainbow_comet(t, n, base):
    """A comet with a white head and a long rainbow tail looping along the string (two on a long strip); the
    tail's colours drift as it flies."""
    u = _unit(n)
    tail = 14.0 * u
    span = n + tail
    count = max(1, int(n / (45.0 * u)))
    hue, head_len = t * 0.08, 1.5 * u
    out = [BLACK] * n
    for j in range(count):
        head = (t * 13.0 * u + j * span / count) % span
        for i in range(max(0, int(head - tail)), min(n, int(head) + 2)):
            d = head - i
            if d < 0:  # the pixel just ahead of the head fades in, so the head glides instead of stepping
                if d > -1:
                    out[i] = _scale(WHITE, 1 + d)
            elif d < tail:
                c = _hsv(hue + 0.5 * j + 0.75 * d / tail, 1.0, (1 - d / tail) ** 2)
                out[i] = _lerp(c, WHITE, 1 - d / head_len) if d < head_len else c
    return out


def _stack(t, n, base):
    """Blocks of light fly in from the far end one after another and stack up from the start until the string is
    full; it holds, fades away and begins again."""
    blocks = max(1, min(10, n))
    size = n / blocks
    fly, hold, fade = 0.7, 1.6, 1.4  # seconds: per block, full, fading out
    q = t % (blocks * fly + hold + fade)
    landed = min(blocks, int(q // fly))
    gain = 1.0 if landed < blocks else 1.0 - _smooth((q - blocks * fly - hold) / fade)
    shades = (_scale(base, gain), _scale(_lerp(base, WHITE, 0.45), gain))
    edge = landed * size
    out = [shades[min(blocks - 1, int(i / size)) % 2] if i < edge else BLACK for i in range(n)]
    if landed < blocks:
        p = (q - landed * fly) / fly
        pos = edge + (n - edge) * (1 - p * p)  # falls faster and faster towards the stack
        col = shades[landed % 2]
        for i in range(max(0, int(pos)), min(n, int(pos + size) + 1)):
            v = min(1.0, i - pos + 1, pos + size - i)
            if v > 0 and i >= edge:
                out[i] = _scale(col, v)
    return out


def _carousel(t, n, base):
    """Blocks of the classic colours that move along one block at a time: a quick, eased slide, then a pause."""
    w = 4.0 * _unit(n)  # bulbs per block
    cols, count = CLASSIC, len(CLASSIC)
    x = t / 1.6
    k = math.floor(x)
    shift = (k + _smooth((x - k) / 0.4)) * w
    soft = 0.8 / w  # the part of a block that blends into the next one
    edge = 1.0 - soft
    floor = math.floor
    out = []
    append = out.append
    for i in range(n):
        p = (i + shift) / w
        b = floor(p)
        e = p - b
        a = cols[b % count]
        append(_lerp(a, cols[(b + 1) % count], _smooth((e - edge) / soft)) if e > edge else a)
    return out


STILL = {
    "Rainbow": _rainbow,
    "Christmas": _pattern([RED, GREEN]),
    "Candy Cane": _pattern([RED, WHITE], 2),
    "Halloween": _pattern(HALLOWEEN_EFFECT),
    "Valentine's": _pattern([RED, PINK, WHITE]),
    "St. Patrick's": _pattern([GREEN, WHITE, GOLD]),
    "Easter": _pattern(PASTELS),
    "July 4th": _pattern([RED, WHITE, BLUE]),
    "Thanksgiving": _pattern([ORANGE, GOLD, (180, 30, 0)]),
    "Hanukkah": _pattern([BLUE, WHITE]),
    "Classic Multicolour": _pattern(CLASSIC),
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
    "Halloween Chase": _chase(HALLOWEEN_EFFECT, 3, 4),
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
    "Gradient Flow": _gradient_flow_fx,
    "Plasma": _plasma_fx,
    "Noise Drift": _noise_drift_fx,
    "Storm Flash": _storm_flash_fx,
    "Confetti": _confetti_fx,
    "Dissolve": _dissolve_fx,
    "Sunrise Ramp": _sunrise_fx,
    "Bouncing Balls": _bouncing_balls,
    "Ripples": _ripples,
    "Pulse Wave": _pulse_wave,
    "Ember Glow": _ember_glow,
    "Rainbow Comet": _rainbow_comet,
    "Stack": _stack,
    "Carousel": _carousel,
}

EFFECTS = {**STILL, **ANIMATED}
EFFECT_LIST = list(EFFECTS)
