"""Patterns across a zone: one frame for the zone's virtual string, in the order its runs are listed.

String mode is exactly `patterns.render` over the whole virtual string, so a chase runs on through the
joins between runs. Spatial mode gives every pixel a place `u` (0-1) along the zone's axis, or out from
its centre, and lays Steady / Chase / Marquee / Wave / Fade along it; the effects that are random per
bulb (Twinkle, Twinkle Swap, Sparkle) and Alternate go by virtual index as usual.
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from . import patterns
from .mapping import MapModel, Run, Slice, Zone, split_frame, virtual_positions, zone_slices
from .protocol import Colour

SPATIAL_EFFECTS = (patterns.STEADY, patterns.CHASE, "Marquee", "Fade", "Wave")
_TAU = 2 * math.pi


def axis_u(positions: Sequence[tuple[float, float]], axis: Mapping[str, object]) -> list[float]:
    """0-1 for each position: how far along the axis (angle: 0 = left to right, 90 = bottom to top) or how
    far from the centre. The nearest pixel is 0 and the farthest 1; a zone with no spread is all 0.5."""
    if not positions:
        return []
    centre = axis.get("centre")
    if isinstance(centre, (list, tuple)) and len(centre) == 2:
        cx, cy = float(centre[0]), float(centre[1])
        d = [math.hypot(x - cx, y - cy) for x, y in positions]
    else:
        a = math.radians(float(axis.get("angle", 0) or 0))  # type: ignore[arg-type]
        dx, dy = math.cos(a), -math.sin(a)  # y points down
        d = [x * dx + y * dy for x, y in positions]
    lo, hi = min(d), max(d)
    if hi - lo < 1e-9:
        return [0.5] * len(d)
    span = hi - lo
    return [(v - lo) / span for v in d]


class ZonePlan:
    """A zone made ready to render: everything that only changes with the map is worked out once."""

    def __init__(self, zone: Zone, runs: Mapping[str, Run]) -> None:
        self.zone = zone
        self.slices: list[Slice] = zone_slices(zone, runs)
        self.n = sum(s.count for s in self.slices)
        self.pattern, self.effect = zone.pattern, zone.effect
        self.spatial = zone.mode == "spatial" and zone.effect in SPATIAL_EFFECTS and self.n > 0
        self.animated = patterns.is_animated(zone.effect)
        if self.spatial:
            self.u = axis_u(virtual_positions(zone, runs), zone.axis)
            top = max(0, self.n - 1)
            self.pos = [u * top for u in self.u]  # in "bulbs", so a pattern keeps the density it has on a string
            self.q = [int(round(p)) for p in self.pos]
            self.base = [patterns.bulb_colour(zone.pattern, q, self.n) for q in self.q]
            self.wave_pos = [p * 0.45 for p in self.pos]
            self.fade_phase = [u * _TAU for u in self.u]

    def render(self, t: float) -> list[Colour]:
        """The virtual string at time t (already scaled by the speed)."""
        n = self.n
        if n == 0:
            return []
        if not self.spatial:
            return patterns.render(self.pattern, self.effect, t, n)
        effect, base = self.effect, self.base
        if effect == patterns.CHASE:
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
