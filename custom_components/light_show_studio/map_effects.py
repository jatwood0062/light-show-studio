"""Map effects: moving pictures laid over an area's 3D space (Sweep, Ripple, Snowfall, Rise, Rainbow Wash,
Fireworks, Breathe Outward). The geometry is worked out once when the zone starts (or the map changes); a frame is
then plain list arithmetic, about 2-4 ms for 1500 pixels.

They read the zone's axis the way the spatial string effects do: Sweep, Rise and Rainbow Wash go along its direction
(or outward from its centre), Ripple, Fireworks and Breathe Outward spread from its centre point (the middle of the
area's mapped pixels when the axis is a direction), and Snowfall always falls (y, down). Each area is its own space.
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from typing import Any

from . import patterns
from .mapping import Slice, run_positions
from .protocol import Colour

_TAU = 2 * math.pi
BLACK: Colour = (0, 0, 0)
_RAINBOW: list[Colour] = [patterns._rainbow_colour(i / 256) for i in range(256)]  # noqa: SLF001
FIREWORK_LIFE = 2.4  # seconds a burst lasts
FIREWORK_EVERY = 0.8  # a new burst this often (they overlap)
MAX_FLAKES = 60
FLAKE_RADIUS = 0.09
_LUT = 4096  # steps in the ripple / breathe lookup tables (a step is far below one brightness level)
_RING = [(0.5 + 0.5 * math.cos(_TAU * k / _LUT)) ** 4 for k in range(_LUT + 1)]  # a ripple ring's profile
_SWELL = [(0.5 + 0.5 * math.sin(_TAU * (-k / _LUT))) ** 1.5 * 0.92 + 0.08 for k in range(_LUT + 1)]  # breathe, by -phase
_CELL = 0.12  # snow: size of the grid cells pixels are filed in


class _Group:
    """The pixels of one area: where they are, and the centre effects spread from."""

    def __init__(self, index: int, idx: list[int], pts: Sequence[Sequence[float]], centre: Sequence[float] | None) -> None:
        self.index = index
        self.idx = idx
        self.xs = [p[0] for p in pts]
        self.ys = [p[1] for p in pts]
        self.zs = [p[2] if len(p) > 2 else 0.5 for p in pts]
        lo = (min(self.xs), min(self.ys), min(self.zs))
        hi = (max(self.xs), max(self.ys), max(self.zs))
        self.lo, self.span = lo, tuple(max(1e-6, h - l) for h, l in zip(hi, lo))
        if centre is not None and len(centre) in (2, 3):
            self.centre = (float(centre[0]), float(centre[1]), float(centre[2]) if len(centre) == 3 else 0.5)
        else:
            self.centre = tuple((l + h) / 2 for l, h in zip(lo, hi))
        cx, cy, cz = self.centre
        sqrt = math.sqrt
        self.dist = [sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) for x, y, z in zip(self.xs, self.ys, self.zs)]
        self.dmax = max(max(self.dist), 1e-6)
        self.dn = [d / self.dmax for d in self.dist]
        self._cells: dict[tuple[int, int, int], list[int]] | None = None
        self._by_x: tuple[list[float], list[int], list[float], list[float], list[float]] | None = None
        self.ring_phase = [d * 3.0 for d in self.dist]  # ripple: what is the same every frame
        self.ring_gain = [0.95 * (1 - 0.5 * dn) for dn in self.dn]

    @property
    def by_x(self) -> tuple[list[float], list[int], list[float], list[float], list[float]]:
        """The pixels in order of x (xs, indexes in the zone, then each one's x, y, z), so a burst only visits the
        ones whose x is near it. Worked out on first use."""
        if self._by_x is None:
            order = sorted(range(len(self.xs)), key=self.xs.__getitem__)
            self._by_x = ([self.xs[j] for j in order], [self.idx[j] for j in order],
                          [self.xs[j] for j in order], [self.ys[j] for j in order], [self.zs[j] for j in order])
        return self._by_x

    @property
    def cells(self) -> dict[tuple[int, int, int], list[int]]:
        """Pixels filed by grid cell (snow only needs this, so it is worked out on first use)."""
        if self._cells is None:
            cells: dict[tuple[int, int, int], list[int]] = {}
            for j, (x, y, z) in enumerate(zip(self.xs, self.ys, self.zs)):
                cells.setdefault((int(x // _CELL), int(y // _CELL), int(z // _CELL)), []).append(j)
            self._cells = cells
        return self._cells


class MapEffects:
    """Everything a map effect needs for one zone: per-pixel `u` along the axis, the areas' groups, the
    pattern's colours per pixel (`base`) and its palette."""

    def __init__(self, slices: Sequence[Slice], axis: Mapping[str, Any], u: list[float], base: list[Colour],
                 palette: list[Colour]) -> None:
        self.n = sum(s.count for s in slices)
        self.u, self.base, self.palette = u, base, palette or [(255, 255, 255)]
        centre = axis.get("centre")
        by_area: dict[str, tuple[list[int], list[Sequence[float]]]] = {}
        for s in slices:
            pos = run_positions(s.run)
            if s.run.reverse:
                pos.reverse()
            idx, pts = by_area.setdefault(s.run.area, ([], []))
            idx.extend(range(s.start, s.start + s.count))
            pts.extend(pos)
        self.groups = [_Group(gi, idx, pts, centre if isinstance(centre, (list, tuple)) else None)
                       for gi, (idx, pts) in enumerate(by_area.values()) if idx]

    # --- the effects ---------------------------------------------------------------------
    def render(self, effect: str, t: float) -> list[Colour]:
        fn = _EFFECTS.get(effect)
        return fn(self, t) if fn else list(self.base)

    def sweep(self, t: float) -> list[Colour]:
        """A soft band crossing the area along the direction (or out from the centre), over a dim glow."""
        w = 0.2
        c = (t * 0.22) % (1 + 2 * w) - w
        out = []
        append = out.append
        for col, u in zip(self.base, self.u):
            d = 1 - abs(u - c) / w
            v = 0.06 + (0.94 * d * d if d > 0 else 0.0)
            append((int(col[0] * v), int(col[1] * v), int(col[2] * v)))
        return out

    def ripple(self, t: float) -> list[Colour]:
        """Rings spreading out from the centre point, each ring in the next colour of the pattern."""
        out = [BLACK] * self.n
        pal, k = self.palette, len(self.palette)
        floor = math.floor
        ring_lut, steps = _RING, _LUT
        shift = t * 0.35
        for g in self.groups:
            for j, ph, gain in zip(g.idx, g.ring_phase, g.ring_gain):
                phase = ph - shift
                ring = floor(phase)
                v = 0.05 + ring_lut[int((phase - ring) * steps + 0.5)] * gain
                c = pal[ring % k]
                out[j] = (int(c[0] * v), int(c[1] * v), int(c[2] * v))
        return out

    def snowfall(self, t: float) -> list[Colour]:
        """Flakes falling in y with a little sideways drift; a pixel glows as a flake passes near it."""
        glow = [0.0] * self.n
        hash_ = patterns._hash  # noqa: SLF001
        r2 = FLAKE_RADIUS * FLAKE_RADIUS
        sin = math.sin
        for g in self.groups:
            flakes = min(MAX_FLAKES, max(6, len(g.idx) // 15))
            cells, lo, span = g.cells, g.lo, g.span
            for f in range(flakes):
                h = hash_(f, 11 + g.index)
                speed = 0.10 + 0.15 * hash_(f, 12)
                fall = (t * speed + h) % 1.15
                fy = lo[1] + span[1] * (1.05 - fall)
                fx = lo[0] + span[0] * hash_(f, 13) + 0.05 * sin(t * 0.7 + h * _TAU)
                fz = lo[2] + span[2] * hash_(f, 14)
                cx, cy, cz = int(fx // _CELL), int(fy // _CELL), int(fz // _CELL)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            for j in cells.get((cx + dx, cy + dy, cz + dz), ()):
                                d2 = (g.xs[j] - fx) ** 2 + (g.ys[j] - fy) ** 2 + (g.zs[j] - fz) ** 2
                                if d2 < r2:
                                    v = 1 - d2 / r2
                                    i = g.idx[j]
                                    if v > glow[i]:
                                        glow[i] = v
        out = []
        for col, v in zip(self.base, glow):
            v = 0.04 + 0.96 * v * v
            out.append((int(col[0] * v), int(col[1] * v), int(col[2] * v)))
        return out

    def rise(self, t: float) -> list[Colour]:
        """A glow that fills the area from the start of the direction to the end, brightest at its leading edge,
        then fades and begins again."""
        front = (t * 0.18) % 1.35
        fo = 1.0 if front <= 1.0 else max(0.0, 1 - (front - 1.0) / 0.35)
        out = []
        append = out.append
        exp = math.exp
        for col, u in zip(self.base, self.u):
            d = front - u
            if d <= 0:
                v = 0.05
            else:
                fill = min(1.0, d / 0.25)
                v = 0.05 + (0.95 * fill * fill * (3 - 2 * fill) * 0.8 + 0.35 * exp(-(d / 0.06) ** 2)) * fo
                v = min(1.0, v)
            append((int(col[0] * v), int(col[1] * v), int(col[2] * v)))
        return out

    def rainbow_wash(self, t: float) -> list[Colour]:
        """Hue changes along the direction (or with the distance from the centre), drifting with time."""
        shift = t * 0.1
        lut = _RAINBOW
        return [lut[int(((u + shift) % 1.0) * 256) & 255] for u in self.u]

    def fireworks(self, t: float) -> list[Colour]:
        """Random bursts: a shell of light expanding from a point in 3D, in a colour of the pattern, fading."""
        hash_ = patterns._hash  # noqa: SLF001
        pal, k = self.palette, len(self.palette)
        out = [(int(c[0] * 0.04), int(c[1] * 0.04), int(c[2] * 0.04)) for c in self.base]
        level = [0.0] * self.n
        newest = int(t // FIREWORK_EVERY)
        sqrt, exp = math.sqrt, math.exp
        for burst in range(newest - int(FIREWORK_LIFE / FIREWORK_EVERY), newest + 1):
            tau = t - burst * FIREWORK_EVERY
            if not 0 <= tau < FIREWORK_LIFE:
                continue
            life = (1 - tau / FIREWORK_LIFE) ** 1.5
            radius = 0.42 * (1 - exp(-3.2 * tau))
            thick = 0.09 + 0.05 * tau
            for g in self.groups:
                cx = g.lo[0] + g.span[0] * (0.15 + 0.7 * hash_(burst, 21 + g.index))
                cy = g.lo[1] + g.span[1] * (0.25 + 0.65 * hash_(burst, 22 + g.index))
                cz = g.lo[2] + g.span[2] * (0.15 + 0.7 * hash_(burst, 23 + g.index))
                col = pal[int(hash_(burst, 24) * k) % k]
                flash = tau < 0.15
                hi2 = (radius + thick) ** 2
                lo = radius - thick
                lo2 = lo * lo if lo > 0 else -1.0
                reach = max(radius + thick, 0.1) if flash else radius + thick  # nothing further out in x is lit
                sxs, sidx, px, py, pz = g.by_x
                for m in range(bisect_left(sxs, cx - reach), bisect_right(sxs, cx + reach)):
                    j = sidx[m]
                    d2 = (px[m] - cx) ** 2 + (py[m] - cy) ** 2 + (pz[m] - cz) ** 2
                    if flash and d2 < 0.01:
                        v = 1.0
                    elif lo2 < d2 < hi2:  # inside the shell (the cheap test first: most pixels are not)
                        v = (1 - abs(sqrt(d2) - radius) / thick) * life
                    else:
                        continue
                    if v > level[j]:
                        level[j] = v
                        out[j] = (int(col[0] * v), int(col[1] * v), int(col[2] * v))
        return out

    def breathe_outward(self, t: float) -> list[Colour]:
        """Brightness swells and ebbs in waves that travel out from the centre."""
        out = [BLACK] * self.n
        base, lut, steps = self.base, _SWELL, _LUT
        shift = t * 0.35
        for g in self.groups:
            for j, dn in zip(g.idx, g.dn):
                v = lut[int(((dn - shift) % 1.0) * steps + 0.5)]  # the swell, by how far round its cycle (0-1)
                c = base[j]
                out[j] = (int(c[0] * v), int(c[1] * v), int(c[2] * v))
        return out


_EFFECTS = {"Sweep": MapEffects.sweep, "Ripple": MapEffects.ripple, "Snowfall": MapEffects.snowfall,
            "Rise": MapEffects.rise, "Rainbow Wash": MapEffects.rainbow_wash, "Fireworks": MapEffects.fireworks,
            "Breathe Outward": MapEffects.breathe_outward}
from .map_effects_extra import EXTRA_EFFECTS as _EXTRA  # noqa: E402  (Gradient Axis, Plasma Field, Candy Helix)

_EFFECTS.update(_EXTRA)
assert set(_EFFECTS) == set(patterns.MAP_EFFECTS)
