"""Patterns across a zone: one frame for the zone's virtual string, in the order its runs are listed.

String mode is exactly `patterns.render` over the whole virtual string, so a chase runs on through the
joins between runs. Spatial mode gives every pixel a place `u` (0-1) along the zone's 3D direction (within
its own area's space), or out from its centre, and lays Steady / Chase / Marquee / Wave / Fade along it; the effects that are random per
bulb (Twinkle, Twinkle Swap, Sparkle) and Alternate go by virtual index as usual.
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from . import patterns, user_patterns
from .map_effects import MapEffects
from .mapping import PRESETS, MapModel, Point, Run, Slice, Zone, run_positions, split_frame, zone_slices
from .protocol import Colour

SPATIAL_EFFECTS = (patterns.STEADY, patterns.CHASE, "Marquee", "Fade", "Wave")
MAP_EFFECTS = tuple(patterns.MAP_EFFECTS)  # Sweep, Ripple ...: always laid out by position, whatever the zone's mode
_TAU = 2 * math.pi


def axis_vector(axis: Mapping[str, object]) -> tuple[float, float, float]:
    """The direction a pattern moves along (y up, z towards the viewer). Reads "dir", else the version 1
    "angle" (0 = left to right, 90 = bottom to top), else a preset's name; left to right when none."""
    d = axis.get("dir")
    if isinstance(d, (list, tuple)) and len(d) == 3:
        return float(d[0]), float(d[1]), float(d[2])  # type: ignore[arg-type]
    if "angle" in axis:
        a = math.radians(float(axis.get("angle") or 0))  # type: ignore[arg-type]
        return math.cos(a), math.sin(a), 0.0
    return PRESETS.get(str(axis.get("preset")), (1.0, 0.0, 0.0))


def axis_u(positions: Sequence[Sequence[float]], axis: Mapping[str, object]) -> list[float]:
    """0-1 for each (x, y, z) position: how far along the direction, or how far from the centre. The nearest
    pixel is 0 and the farthest 1; positions with no spread are all 0.5."""
    if not positions:
        return []
    centre = axis.get("centre")
    if isinstance(centre, (list, tuple)) and len(centre) in (2, 3):
        cx, cy = float(centre[0]), float(centre[1])
        cz = float(centre[2]) if len(centre) == 3 else 0.5
        d = [math.sqrt((p[0] - cx) ** 2 + (p[1] - cy) ** 2 + ((p[2] if len(p) > 2 else 0.5) - cz) ** 2) for p in positions]
    else:
        dx, dy, dz = axis_vector(axis)
        d = [p[0] * dx + p[1] * dy + (p[2] if len(p) > 2 else 0.5) * dz for p in positions]
    lo, hi = min(d), max(d)
    if hi - lo < 1e-9:
        return [0.5] * len(d)
    span = hi - lo
    return [(v - lo) / span for v in d]


def zone_u(slices: Sequence[Slice], axis: Mapping[str, object]) -> list[float]:
    """u for every virtual position. Each area is its own space, so u is worked out (and spans 0-1) within
    each area: a sweep runs through every area at once."""
    n = sum(s.count for s in slices)
    groups: dict[str, tuple[list[int], list[Point]]] = {}
    for s in slices:
        pos = run_positions(s.run)
        if s.run.reverse:
            pos.reverse()
        idx, pts = groups.setdefault(s.run.area, ([], []))
        idx.extend(range(s.start, s.start + s.count))
        pts.extend(pos)
    out = [0.5] * n
    for idx, pts in groups.values():
        for i, u in zip(idx, axis_u(pts, axis)):
            out[i] = u
    return out


def painted_base(up: user_patterns.UserPattern, slices: Sequence[Slice], n: int) -> list[Colour]:
    """The colours of a painted pattern for a zone's virtual string: each pixel its own painted colour on its
    light, else the nearest point painted in its area (by 3D position), else the pattern's palette repeated."""
    pal = up.palette
    out: list[Colour | None] = [None] * n
    for s in slices:
        run = s.run
        count = run.count
        need: list[int] = []
        for k in range(count):
            pixel = run.first + (count - 1 - k if run.reverse else k)
            c = up.painted_pixel(run.entry_id, pixel)
            if c is None:
                need.append(k)
            else:
                out[s.start + k] = c
        area = up.areas.get(run.area)
        if need and area is not None:
            pos = run_positions(run)
            if run.reverse:
                pos.reverse()
            q = [(pos[k][0] * user_patterns.POS_MAX, pos[k][1] * user_patterns.POS_MAX,
                  pos[k][2] * user_patterns.POS_MAX) for k in need]
            for k, c in zip(need, user_patterns.nearest_colours(area[0], area[1], q)):
                out[s.start + k] = c
    return [c if c is not None else pal[i % len(pal)] for i, c in enumerate(out)]


class ZonePlan:
    """A zone made ready to render: everything that only changes with the map is worked out once."""

    def __init__(self, zone: Zone, runs: Mapping[str, Run]) -> None:
        self.zone = zone
        self.slices: list[Slice] = zone_slices(zone, runs)
        self.n = sum(s.count for s in self.slices)
        self.pattern, self.effect = patterns.canonical(zone.pattern) or patterns.DEFAULT_PATTERN, zone.effect
        self.mapfx = zone.effect in MAP_EFFECTS and self.n > 0
        self.spatial = (zone.mode == "spatial" and zone.effect in SPATIAL_EFFECTS and self.n > 0) or self.mapfx
        self.animated = patterns.is_animated(zone.effect)
        up = None if self.pattern in patterns.PATTERNS else patterns.user_pattern(self.pattern)
        # painted pixels: the pattern's colour for every virtual pixel, worked out once
        self.override: list[Colour] | None = painted_base(up, self.slices, self.n) \
            if up is not None and up.kind == "pixels" and self.n else None
        self.epoch = patterns.user_epoch()
        if self.spatial:
            self.u = zone_u(self.slices, zone.axis)
            top = max(0, self.n - 1)
            self.pos = [u * top for u in self.u]  # in "bulbs", so a pattern keeps the density it has on a string
            self.q = [int(round(p)) for p in self.pos]
            if self.override is not None:
                self.base = list(self.override)
                order = sorted(range(self.n), key=self.u.__getitem__)  # painted colours in order along the axis
                self.ranked = [self.base[i] for i in order]
                self.rank = [0] * self.n
                for r, i in enumerate(order):
                    self.rank[i] = r
            else:
                self.base = [patterns.bulb_colour(self.pattern, q, self.n) for q in self.q]
            self.wave_pos = [p * 0.45 for p in self.pos]
            self.fade_phase = [u * _TAU for u in self.u]
            if self.mapfx:
                self.mapfx_plan = MapEffects(self.slices, zone.axis, self.u, self.base, patterns.palette_of(self.pattern))

    def render(self, t: float) -> list[Colour]:
        """The virtual string at time t (already scaled by the speed)."""
        n = self.n
        if n == 0:
            return []
        if not self.spatial:
            return patterns.render(self.pattern, self.effect, t, n, base=self.override)
        effect, base = self.effect, self.base
        if self.mapfx:
            return self.mapfx_plan.render(effect, t)
        if effect == patterns.CHASE:
            if self.override is not None:  # painted: the colours slide along the axis, in order
                shift, ranked, list_at = t * 3.0, self.ranked, patterns.list_at
                return [list_at(ranked, r - shift) for r in self.rank]
            shift, name, colour_at = t * 3.0, self.pattern, patterns.colour_at
            return [colour_at(name, p - shift, n) for p in self.pos]
        if effect == "Marquee":
            step, scale = int(t * 4) % 3, patterns.scale
            return [c if q % 3 == step else scale(c, 0.08) for c, q in zip(base, self.q)]
        if effect == "Wave":
            shift, scale, sin = t * 2.5, patterns.scale, math.sin
            return [scale(c, 0.15 + 0.85 * (0.5 + 0.5 * sin(w - shift)) ** 2) for c, w in zip(base, self.wave_pos)]
        if effect == "Fade":  # the breathing travels across the zone, one full cycle from end to end
            shift, scale, sin = t * 1.6, patterns.scale, math.sin
            return [scale(c, 0.1 + 0.9 * (0.5 + 0.5 * sin(shift - ph))) for c, ph in zip(base, self.fade_phase)]
        return list(base)  # Steady

    def split(self, colours: Sequence[Colour]) -> list[tuple[str, int, list[Colour]]]:
        """(entry id, first pixel, colours) for each run, ready for a light."""
        return split_frame(self.slices, colours)


def render_zone(model: MapModel, zone: Zone, t: float) -> list[Colour]:
    """One frame of a zone's virtual string. Builds the plan each call: keep a ZonePlan to render many frames."""
    return ZonePlan(zone, model.run_map()).render(t)
