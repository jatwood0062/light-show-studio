"""The map: runs of pixels (parts of lights) and zones (ordered runs that show one pattern together).

A run is a stretch of one light's pixels, with a polyline that says where it sits (0-1, y down). A zone
lists runs in the order the pattern should travel; `reverse` flips a run within that order. Nothing here
needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import patterns

VERSION = 1
MAX_RUNS = 200
MAX_ZONES = 64
MAX_ZONE_RUNS = 64
MAX_NAME = 60
MAX_ID = 64
MAX_ENTRY_ID = 64
MAX_PTS = 4096  # points in one run's polyline (one per pixel when imported)
MAX_PIXELS = 2040  # a light never has more (E1.31 limit)
MAX_KIND = 32
MODES = ("string", "spatial")
_ID = re.compile(r"^[A-Za-z0-9_.\-]+\Z")

# WLED kitchen (904 LEDs): name, first, count, y row, x from, x to
KITCHEN_PIXELS = 904
KITCHEN_PRESET: list[dict[str, Any]] = [
    {"name": "Toe-kick", "first": 0, "count": 279, "y": 0.9},
    {"name": "Island", "first": 279, "count": 145, "y": 0.65},
    {"name": "Counter", "first": 424, "count": 192, "y": 0.4},
    {"name": "Above cabinets", "first": 616, "count": 288, "y": 0.12},
]


class MapError(ValueError):
    """The map is not valid; the message says why, in words for the person editing it."""


Point = tuple[float, float]


@dataclass(frozen=True)
class Run:
    id: str
    entry_id: str
    name: str
    first: int
    count: int
    reverse: bool = False
    pts: tuple[Point, ...] = ()


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    runs: tuple[str, ...] = ()
    mode: str = "string"
    axis: Mapping[str, Any] = field(default_factory=lambda: {"angle": 0.0})
    pattern: str = patterns.DEFAULT_PATTERN
    effect: str = patterns.CHASE
    speed: int = patterns.DEFAULT_SPEED


@dataclass(frozen=True)
class LightSpec:
    """What the map needs to know about a light."""
    entry_id: str
    title: str
    bulbs: int
    kind: str = "govee"  # govee | twinkly | wled
    layout: Sequence[Sequence[float]] | None = None


@dataclass(frozen=True)
class MapModel:
    runs: tuple[Run, ...] = ()
    zones: tuple[Zone, ...] = ()
    backdrop: Mapping[str, Any] = field(default_factory=lambda: {"kind": "none", "w": 1, "h": 1})

    def run_map(self) -> dict[str, Run]:
        return {r.id: r for r in self.runs}

    def zone(self, ref: str) -> Zone | None:
        """A zone by id, or else by name (any case)."""
        for z in self.zones:
            if z.id == ref:
                return z
        low = ref.casefold()
        return next((z for z in self.zones if z.name.casefold() == low), None)


# --- validation -----------------------------------------------------------------------
def _dict(v: Any, what: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise MapError(f"{what} must be an object")
    return v


def _list(v: Any, what: str, limit: int) -> list[Any]:
    if not isinstance(v, list):
        raise MapError(f"{what} must be a list")
    if len(v) > limit:
        raise MapError(f"{what}: at most {limit} allowed")
    return v


def _text(v: Any, what: str, limit: int = MAX_NAME) -> str:
    if not isinstance(v, str):
        raise MapError(f"{what} must be text")
    v = v.strip()
    if not 0 < len(v) <= limit or "\0" in v:
        raise MapError(f"{what} must be 1-{limit} characters")
    return v


def _id(v: Any, what: str) -> str:
    if not isinstance(v, str) or not 0 < len(v) <= MAX_ID or not _ID.match(v):
        raise MapError(f"{what} must be 1-{MAX_ID} letters, digits, - _ or .")
    return v


def _int(v: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (math.inf, -math.inf) or int(v) != v:
        raise MapError(f"{what} must be a whole number")
    if not lo <= v <= hi:
        raise MapError(f"{what} must be between {lo} and {hi}")
    return int(v)


def _num(v: Any, what: str, lo: float = -10.0, hi: float = 10.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise MapError(f"{what} must be a number")
    if not lo <= v <= hi:
        raise MapError(f"{what} must be between {lo:g} and {hi:g}")
    return float(v)


def _unit(v: Any, what: str) -> float:
    """A coordinate: tolerates a little overshoot from dragging, clamps to 0-1."""
    return max(0.0, min(1.0, _num(v, what)))


def _bool(v: Any, what: str) -> bool:
    if not isinstance(v, bool):
        raise MapError(f"{what} must be true or false")
    return v


def parse_pts(v: Any, what: str = "points") -> tuple[Point, ...]:
    out = []
    for p in _list(v, what, MAX_PTS):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise MapError(f"{what} must be [x, y] pairs")
        out.append((round(_unit(p[0], what), 5), round(_unit(p[1], what), 5)))
    return tuple(out)


def parse_run(v: Any, max_pixels: int | None = None) -> Run:
    d = _dict(v, "a run")
    rid = _id(d.get("id"), "run id")
    name = _text(d.get("name"), "run name")
    top = MAX_PIXELS if max_pixels is None else max_pixels
    first = _int(d.get("first"), f"{name}: first pixel", 0, max(0, top - 1))
    count = _int(d.get("count"), f"{name}: pixel count", 1, MAX_PIXELS)
    if first + count > top:
        raise MapError(f"{name}: pixels {first}-{first + count - 1} don't fit, the light has {top}")
    entry = d.get("entry_id")
    if not isinstance(entry, str) or not 0 < len(entry) <= MAX_ENTRY_ID or "\0" in entry:
        raise MapError(f"{name}: pick a light")
    return Run(rid, entry, name, first, count, _bool(d.get("reverse", False), f"{name}: reverse"),
               parse_pts(d.get("pts", []), f"{name}: points"))


def parse_axis(v: Any) -> dict[str, Any]:
    d = _dict(v, "the axis")
    if "centre" in d:
        c = d["centre"]
        if not isinstance(c, (list, tuple)) or len(c) != 2:
            raise MapError("the centre must be [x, y]")
        return {"centre": [_unit(c[0], "centre x"), _unit(c[1], "centre y")]}
    return {"angle": _num(d.get("angle", 0), "the angle", -3600, 3600)}


def parse_zone(v: Any) -> Zone:
    d = _dict(v, "a zone")
    name = _text(d.get("name"), "zone name")
    runs = tuple(_id(r, f"{name}: run id") for r in _list(d.get("runs", []), f"{name}: runs", MAX_ZONE_RUNS))
    if len(set(runs)) != len(runs):
        raise MapError(f"{name}: a run is listed twice")
    mode = d.get("mode", "string")
    if mode not in MODES:
        raise MapError(f"{name}: mode must be string or spatial")
    pattern = d.get("pattern", patterns.DEFAULT_PATTERN)
    if pattern not in patterns.PATTERNS:
        raise MapError(f"{name}: unknown pattern")
    effect = d.get("effect", patterns.CHASE)
    if effect not in patterns.PATTERN_EFFECTS:
        raise MapError(f"{name}: unknown effect")
    return Zone(_id(d.get("id"), "zone id"), name, runs, mode, parse_axis(d.get("axis", {"angle": 0})),
                pattern, effect, _int(d.get("speed", patterns.DEFAULT_SPEED), f"{name}: speed", 1, 100))


def parse_backdrop(v: Any) -> dict[str, Any]:
    d = _dict(v, "the backdrop")
    kind = d.get("kind", "none")
    if not isinstance(kind, str) or not 0 < len(kind) <= MAX_KIND:
        raise MapError("the backdrop kind must be short text")
    return {"kind": kind, "w": _num(d.get("w", 1), "backdrop width", 1, 100000),
            "h": _num(d.get("h", 1), "backdrop height", 1, 100000)}


def check_overlap(runs: Iterable[Run]) -> None:
    by_light: dict[str, list[Run]] = {}
    for r in runs:
        by_light.setdefault(r.entry_id, []).append(r)
    for rs in by_light.values():
        rs = sorted(rs, key=lambda r: r.first)
        for a, b in zip(rs, rs[1:]):
            if b.first < a.first + a.count:
                raise MapError(f"{a.name} and {b.name} share pixels {b.first}-{min(a.first + a.count, b.first + b.count) - 1}")


def from_dict(data: Any, lights: Mapping[str, int] | None = None, *, strict: bool = True) -> MapModel:
    """Parse a map. `lights` maps entry id -> bulbs and is used to bound the runs (a run on an unknown
    light is an error). Strict: the first problem raises MapError. Not strict (loading a saved map): runs
    and zones that are no longer valid are dropped, so a damaged store never stops setup."""
    d = _dict(data, "the map")
    raw_runs = _list(d.get("runs", []), "runs", MAX_RUNS if strict else 10**6)[:MAX_RUNS]
    raw_zones = _list(d.get("zones", []), "zones", MAX_ZONES if strict else 10**6)[:MAX_ZONES]
    runs: list[Run] = []
    ids: set[str] = set()
    for raw in raw_runs:
        try:
            r = parse_run(raw, None)
            if lights is not None:
                if r.entry_id not in lights:
                    raise MapError(f"{r.name}: that light doesn't exist")
                r = parse_run(raw, lights[r.entry_id])
            if r.id in ids:
                raise MapError(f"run id {r.id} is used twice")
            if not strict:  # keep only what doesn't collide with what is already kept
                check_overlap([*runs, r])
            runs.append(r)
            ids.add(r.id)
        except MapError:
            if strict:
                raise
    if strict:
        check_overlap(runs)
    zones: list[Zone] = []
    zone_ids: set[str] = set()
    names: set[str] = set()
    for raw in raw_zones:
        try:
            z = parse_zone(raw)
            if z.id in zone_ids:
                raise MapError(f"zone id {z.id} is used twice")
            if z.name.casefold() in names:
                raise MapError(f"two zones are called {z.name}")
            missing = [r for r in z.runs if r not in ids]
            if missing:
                if strict:
                    raise MapError(f"{z.name}: unknown run {missing[0]}")
                z = Zone(z.id, z.name, tuple(r for r in z.runs if r in ids), z.mode, z.axis, z.pattern, z.effect, z.speed)
            zones.append(z)
            zone_ids.add(z.id)
            names.add(z.name.casefold())
        except MapError:
            if strict:
                raise
    try:
        backdrop = parse_backdrop(d.get("backdrop", {"kind": "none", "w": 1, "h": 1}))
    except MapError:
        if strict:
            raise
        backdrop = {"kind": "none", "w": 1, "h": 1}
    return MapModel(tuple(runs), tuple(zones), backdrop)


def prune(model: MapModel, entry_ids: Iterable[str]) -> MapModel:
    """Drop runs on lights that no longer exist, and zone references to them."""
    known = set(entry_ids)
    if all(r.entry_id in known for r in model.runs):
        return model
    runs = tuple(r for r in model.runs if r.entry_id in known)
    ids = {r.id for r in runs}
    zones = tuple(Zone(z.id, z.name, tuple(r for r in z.runs if r in ids), z.mode, z.axis, z.pattern, z.effect, z.speed)
                  for z in model.zones)
    return MapModel(runs, zones, model.backdrop)


def to_dict(model: MapModel) -> dict[str, Any]:
    return {
        "version": VERSION,
        "runs": [{"id": r.id, "entry_id": r.entry_id, "name": r.name, "first": r.first, "count": r.count,
                  "reverse": r.reverse, "pts": [list(p) for p in r.pts]} for r in model.runs],
        "zones": [{"id": z.id, "name": z.name, "runs": list(z.runs), "mode": z.mode, "axis": dict(z.axis),
                   "pattern": z.pattern, "effect": z.effect, "speed": z.speed} for z in model.zones],
        "backdrop": dict(model.backdrop),
    }


# --- defaults -----------------------------------------------------------------------------
def _line(n: int, y: float, x0: float = 0.05, x1: float = 0.95) -> tuple[Point, ...]:
    return ((x0, y), (x1, y))


def default_map(lights: Sequence[LightSpec]) -> MapModel:
    """One run per light covering all its bulbs (the WLED kitchen with 904 LEDs gets its four runs);
    Twinkly lights take their positions from the layout the strip reports. No zones."""
    runs: list[Run] = []
    rows = max(1, len(lights))
    for i, light in enumerate(lights):
        if light.bulbs < 1:
            continue
        y = (i + 0.5) / rows
        if light.kind == "wled" and light.bulbs == KITCHEN_PIXELS:
            for part in KITCHEN_PRESET:
                runs.append(Run(f"r{len(runs) + 1}", light.entry_id, part["name"], part["first"], part["count"],
                                False, _line(part["count"], part["y"])))
            continue
        pts = _line(light.bulbs, y)
        if light.layout is not None and len(light.layout) == light.bulbs:
            pts = _fit_layout(light.layout) or pts
        runs.append(Run(f"r{len(runs) + 1}", light.entry_id, light.title[:MAX_NAME] or f"Light {i + 1}",
                        0, light.bulbs, False, pts))
    return MapModel(tuple(runs[:MAX_RUNS]), (), {"kind": "none", "w": 1, "h": 1})


def _fit_layout(layout: Sequence[Sequence[float]]) -> tuple[Point, ...] | None:
    """A strip's own LED positions, scaled to fit 0-1 with a margin and the aspect ratio kept."""
    try:
        xs = [float(p[0]) for p in layout]
        ys = [float(p[1]) for p in layout]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or len(xs) > MAX_PTS or not all(math.isfinite(v) for v in xs + ys):
        return None
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    span = max(w, h) or 1.0
    scale, margin = 0.9 / span, 0.05
    return tuple((round(margin + (x - x0) * scale + (0.9 - w * scale) / 2, 5),
                  round(margin + (y - y0) * scale + (0.9 - h * scale) / 2, 5)) for x, y in zip(xs, ys))


# --- geometry -----------------------------------------------------------------------------
def run_positions(run: Run) -> list[Point]:
    """Where each of the run's pixels is (index 0 = pixel `first`, whatever `reverse` says).
    One point per pixel is used as is; otherwise the pixels are spread evenly along the polyline."""
    n = run.count
    pts = list(run.pts)
    if len(pts) == n:
        return pts
    if not pts:
        pts = [(0.05, 0.5), (0.95, 0.5)]
    if len(pts) == 1:
        return [pts[0]] * n
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    total = sum(seg)
    out: list[Point] = []
    j, walked = 0, 0.0
    for k in range(n):
        s = total * (k / (n - 1) if n > 1 else 0.5)
        while j < len(seg) - 1 and walked + seg[j] < s:
            walked += seg[j]
            j += 1
        f = (s - walked) / seg[j] if seg[j] > 0 else 0.0
        a, b = pts[j], pts[j + 1]
        f = max(0.0, min(1.0, f))
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


@dataclass(frozen=True)
class Slice:
    """Virtual indexes [start, start + run.count) belong to this run."""
    run: Run
    start: int

    @property
    def count(self) -> int:
        return self.run.count


def zone_slices(zone: Zone, runs: Mapping[str, Run]) -> list[Slice]:
    """The zone's runs in order (references to runs that don't exist are skipped)."""
    out, start = [], 0
    for rid in zone.runs:
        if (run := runs.get(rid)) is not None:
            out.append(Slice(run, start))
            start += run.count
    return out


def virtual_order(zone: Zone, runs: Mapping[str, Run]) -> list[tuple[str, int]]:
    """The zone as one string: (entry id, pixel index) for each virtual position, a reversed run walked backwards."""
    out: list[tuple[str, int]] = []
    for s in zone_slices(zone, runs):
        r = s.run
        pixels = range(r.first + r.count - 1, r.first - 1, -1) if r.reverse else range(r.first, r.first + r.count)
        out.extend((r.entry_id, p) for p in pixels)
    return out


def virtual_positions(zone: Zone, runs: Mapping[str, Run]) -> list[Point]:
    """Where each virtual position is, in the same order as virtual_order."""
    out: list[Point] = []
    for s in zone_slices(zone, runs):
        pos = run_positions(s.run)
        out.extend(reversed(pos) if s.run.reverse else pos)
    return out


def split_frame(slices: Sequence[Slice], colours: Sequence[Any]) -> list[tuple[str, int, list[Any]]]:
    """Cut a virtual frame into (entry id, first pixel, colours in the light's own order) per run."""
    out = []
    for s in slices:
        seg = list(colours[s.start : s.start + s.count])
        if s.run.reverse:
            seg.reverse()
        out.append((s.run.entry_id, s.run.first, seg))
    return out
