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


def _kept(g, key: str, build):
    """Numbers for one area that are the same every frame, worked out once and kept on the group (and worked out
    again if its points are replaced)."""
    hit = getattr(g, key, None)
    if hit is None or hit[0] is not g.xs or hit[1] != len(g.xs):
        hit = (g.xs, len(g.xs), build(g))
        setattr(g, key, hit)
    return hit[2]


def gradient_axis(fx, t: float) -> list[Colour]:
    """The pattern's colours as a gradient along the zone's direction (set the axis to up for a gradient by
    height; with a centre point it runs outward from it), scrolling slowly."""
    pal = _palette(fx)
    k = len(fx.palette)
    span = (k - 1) / k if k > 1 else 1.0  # first colour at one end, last colour at the other (a looping palette
    shift = t * 0.07  # would otherwise end where it began); scrolling wraps round through the gap
    lut, size = pal._lut, pal.SIZE  # noqa: SLF001  (pal.at for a looping palette, written out)
    return [lut[int(((u * span - shift) % 1.0) * size) % size] for u in fx.u]


def _plasma_rows(g) -> list[tuple[int, float, float, float, float]]:
    """For every pixel of an area: its index and the part of each of the four waves that does not change."""
    xs, ys, zs = g.xs, g.ys, g.zs
    x0, y0, z0 = min(xs), min(ys), min(zs)
    m = max(max(xs) - x0, max(ys) - y0, max(zs) - z0, 1e-6)  # one scale for all three axes
    rows = []
    for j, x, y, z in zip(g.idx, xs, ys, zs):
        x, y, z = (x - x0) / m, (y - y0) / m, (z - z0) / m
        rows.append((j, x * 6.5, y * 8.5, z * 7.0, math.sqrt((x - 0.5) ** 2 + (y - 0.5) ** 2 + (z - 0.5) ** 2) * 11.0))
    return rows


def plasma_field(fx, t: float) -> list[Colour]:
    """A plasma in 3D: four sine waves (one along each axis and one spreading from the middle of the area), summed
    and coloured through the pattern's gradient. Each area is scaled to fit, so the blobs look alike in a small
    and a large room."""
    out = [(0, 0, 0)] * fx.n
    pal = _palette(fx)
    lut, size = pal._lut, pal.SIZE  # noqa: SLF001  (pal.at for a looping palette, written out)
    sin = math.sin
    drift = t * 0.03
    t8, t6, t5, t9 = t * 0.8, t * 0.6, t * 0.5, t * 0.9
    for g in fx.groups:
        for j, a, b, c, r in _kept(g, "_plasma_rows", _plasma_rows):
            s = sin(a + t8) + sin(b - t6 + 1.7) + sin(c + t5 + 0.6) + sin(r - t9)
            out[j] = lut[int(((0.5 + 0.5 * s / 3.2 + drift) % 1.0) * size) % size]
    return out


_ARMS, _TWIST, _SPIN, _SOFT = 3, 4.5, 0.12, 0.14  # stripes round, turns of climb (x3), turns a second, edge blend


def _helix_rows(g) -> list[tuple[int, float]]:
    """For every pixel of an area: its index and where it sits in the stripes before they turn (angle and height)."""
    xs, ys, zs = g.xs, g.ys, g.zs
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    y0 = min(ys)
    yspan = max(max(ys) - y0, 1e-6)
    atan2 = math.atan2
    # whole-number arms around, so the seam where the angle wraps is invisible
    return [(j, atan2(z - cz, x - cx) / _TAU * _ARMS - (y - y0) / yspan * _TWIST) for j, x, y, z in zip(g.idx, xs, ys, zs)]


def candy_helix(fx, t: float) -> list[Colour]:
    """Stripes that wind round a vertical axis (a tree): the colour depends on the angle around the middle of
    the area plus the height, so the stripes spiral up like a candy cane, turning slowly. The pattern's colours
    are the stripes (a pattern with fewer than two colours gives red and white); 3 stripes round, climbing 1.5 turns."""
    cols = list(dict.fromkeys(fx.palette))
    if len(cols) < 2:
        cols = CANDY
    k = len(cols)
    soft = _SOFT
    edge = 1.0 - soft
    turn = t * _SPIN
    out = [(0, 0, 0)] * fx.n
    for g in fx.groups:
        for j, q0 in _kept(g, "_helix_rows", _helix_rows):
            f = ((q0 + turn) % 1.0) * k  # q counts stripes
            i = int(f)
            e = f - i  # position inside this stripe; blend into the next one near its end
            if e > edge:
                m = _smooth((e - (1.0 - soft)) / soft)
                a = cols[i % k]
                out[j] = _lerp(a, cols[(i + 1) % k], m) if m > 0.0 else a
            else:
                out[j] = cols[i % k]
    return out


EXTRA_EFFECTS = {"Gradient Axis": gradient_axis, "Plasma Field": plasma_field, "Candy Helix": candy_helix}
