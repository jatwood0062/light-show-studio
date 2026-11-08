"""More map effects (Gradient Axis, Plasma Field, Candy Helix), kept apart from map_effects.py.

They have the same interface as the effects there: a function `fn(fx, t) -> list of colours`, where `fx` is a
`map_effects.MapEffects` (it is passed in; this module does not import map_effects, so there is no import cycle).
They read only `fx.n`, `fx.u`, `fx.palette` and each group's `idx`, `xs`, `ys`, `zs` (y is up). Every one is a
pure function of t: no randomness, no memory, so a late or skipped frame just lands on the right picture.

To expose them, map_effects.py needs one line before its closing assert:
    from .map_effects_extra import EXTRA_EFFECTS as _EXTRA; _EFFECTS.update(_EXTRA)
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import math

from .effects import RED, WHITE, _lerp, _smooth, palette_for
from .protocol import Colour

_TAU = 2 * math.pi
CANDY: list[Colour] = [RED, WHITE]  # Candy Helix when the pattern has fewer than two colours


def _palette(fx):
    return palette_for(tuple(fx.palette))


def gradient_axis(fx, t: float) -> list[Colour]:
    """The pattern's colours as a gradient along the zone's direction (set the axis to up for a gradient by
    height; with a centre point it runs outward from it), scrolling slowly."""
    pal = _palette(fx)
    k = len(fx.palette)
    span = (k - 1) / k if k > 1 else 1.0  # first colour at one end, last colour at the other (a looping palette
    shift = t * 0.07  # would otherwise end where it began); scrolling wraps round through the gap
    at = pal.at
    return [at(u * span - shift) for u in fx.u]


def plasma_field(fx, t: float) -> list[Colour]:
    """A plasma in 3D: four sine waves (one along each axis and one spreading from the middle of the area), summed
    and coloured through the pattern's gradient. Each area is scaled to fit, so the blobs look alike in a small
    and a large room."""
    out = [(0, 0, 0)] * fx.n
    at = _palette(fx).at
    sin, sqrt = math.sin, math.sqrt
    drift = t * 0.03
    for g in fx.groups:
        xs, ys, zs = g.xs, g.ys, g.zs
        x0, y0, z0 = min(xs), min(ys), min(zs)
        m = max(max(xs) - x0, max(ys) - y0, max(zs) - z0, 1e-6)  # one scale for all three axes
        for j, x, y, z in zip(g.idx, xs, ys, zs):
            x, y, z = (x - x0) / m, (y - y0) / m, (z - z0) / m
            s = (sin(x * 6.5 + t * 0.8) + sin(y * 8.5 - t * 0.6 + 1.7) + sin(z * 7.0 + t * 0.5 + 0.6)
                 + sin(sqrt((x - 0.5) ** 2 + (y - 0.5) ** 2 + (z - 0.5) ** 2) * 11.0 - t * 0.9))
            out[j] = at(0.5 + 0.5 * s / 3.2 + drift)
    return out


def candy_helix(fx, t: float) -> list[Colour]:
    """Stripes that wind round a vertical axis (a tree): the colour depends on the angle around the middle of
    the area plus the height, so the stripes spiral up like a candy cane, turning slowly. The pattern's colours
    are the stripes (a pattern with fewer than two colours gives red and white); 3 stripes round, climbing 1.5 turns."""
    cols = list(dict.fromkeys(fx.palette))
    if len(cols) < 2:
        cols = CANDY
    k = len(cols)
    arms, twist, spin, soft = 3, 4.5, 0.12, 0.14
    out = [(0, 0, 0)] * fx.n
    atan2 = math.atan2
    for g in fx.groups:
        xs, ys, zs = g.xs, g.ys, g.zs
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        y0 = min(ys)
        yspan = max(max(ys) - y0, 1e-6)
        for j, x, y, z in zip(g.idx, xs, ys, zs):
            # q counts stripes: whole-number arms around, so the seam where the angle wraps is invisible
            q = atan2(z - cz, x - cx) / _TAU * arms - (y - y0) / yspan * twist + t * spin
            f = (q % 1.0) * k
            i = int(f)
            e = f - i  # position inside this stripe; blend into the next one near its end
            m = _smooth((e - (1.0 - soft)) / soft) if e > 1.0 - soft else 0.0
            a, b = cols[i % k], cols[(i + 1) % k]
            out[j] = _lerp(a, b, m) if m > 0.0 else a
    return out


EXTRA_EFFECTS = {"Gradient Axis": gradient_axis, "Plasma Field": plasma_field, "Candy Helix": candy_helix}
