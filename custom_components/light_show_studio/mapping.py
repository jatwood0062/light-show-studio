"""The map: areas, runs of pixels (parts of lights) and zones (ordered runs that show one pattern together).

An area is a place (a Home Assistant area, or a name of your own) with its own 3D space: x left to right,
y bottom to top, z back to front (towards the viewer), all 0-1. A run is a stretch of one light's pixels in
one area; it sits along a 3D polyline until its pixels are mapped one by one (`positions`, from a camera
scan or a Twinkly layout). A zone lists runs (or whole areas) in the order the pattern should travel;
`reverse` flips a run within that order. Nothing here needs Home Assistant, so it is easy to test.

Version 1 of the store (2D, y down, no areas) is migrated on load by `migrate`.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from . import patterns

VERSION = 2
MAX_AREAS = 64
MAX_ZONE_AREAS = 64
POS_SCALE = 1000  # per-pixel positions are stored as ints 0-1000
DEFAULT_Z = 0.5
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
MAIN_AREA = "main"  # where a version 1 map's runs go: one shared space, as they had before
MAIN_AREA_NAME = "House"
# spatial direction presets (unit vectors; y up, z towards the viewer) and "outward from a point"
PRESETS: dict[str, tuple[float, float, float]] = {
    "left_right": (1.0, 0.0, 0.0), "right_left": (-1.0, 0.0, 0.0),
    "rise": (0.0, 1.0, 0.0), "fall": (0.0, -1.0, 0.0),
    "front_back": (0.0, 0.0, -1.0), "back_front": (0.0, 0.0, 1.0),
}
OUTWARD = "outward"
_ID = re.compile(r"^[A-Za-z0-9_.\-]+\Z")

# WLED kitchen (904 LEDs): name, first, count, y row, x from, x to
KITCHEN_PIXELS = 904
KITCHEN_PRESET: list[dict[str, Any]] = [  # y is up
    {"name": "Toe-kick", "first": 0, "count": 279, "y": 0.1},
    {"name": "Island", "first": 279, "count": 145, "y": 0.35},
    {"name": "Counter", "first": 424, "count": 192, "y": 0.6},
    {"name": "Above cabinets", "first": 616, "count": 288, "y": 0.88},
]


class MapError(ValueError):
    """The map is not valid; the message says why, in words for the person editing it."""


Point = tuple[float, float, float]


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    ha_area_id: str | None = None  # the Home Assistant area this stands for, if any
    outdoor: bool = False  # lights in an outdoor area are what a light schedule's "outdoor" target means


@dataclass(frozen=True)
class Run:
    id: str
    entry_id: str
    name: str
    first: int
    count: int
    reverse: bool = False
    pts: tuple[Point, ...] = ()
    area: str = ""
    positions: tuple[int, ...] = ()  # x, y, z per pixel (0-1000), flat; empty until the run is mapped


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    runs: tuple[str, ...] = ()
    mode: str = "string"
    axis: Mapping[str, Any] = field(default_factory=lambda: default_axis())
    pattern: str = patterns.DEFAULT_PATTERN
    effect: str = patterns.CHASE
    speed: int = patterns.DEFAULT_SPEED
    areas: tuple[str, ...] = ()  # whole areas, after the explicit runs


@dataclass(frozen=True)
class LightSpec:
    """What the map needs to know about a light."""
    entry_id: str
    title: str
    bulbs: int
    kind: str = "govee"  # govee | twinkly | wled
    layout: Sequence[Sequence[float]] | None = None  # [x, y] or [x, y, z] per LED, y down (as the card draws it)
    area_id: str | None = None  # the light's Home Assistant area, if it has one
    area_name: str | None = None


@dataclass(frozen=True)
class MapModel:
    runs: tuple[Run, ...] = ()
    zones: tuple[Zone, ...] = ()
    backdrop: Mapping[str, Any] = field(default_factory=lambda: {"kind": "none", "w": 1, "h": 1})
    areas: tuple[Area, ...] = ()

    def area(self, ref: str) -> Area | None:
        """An area by id, else by name (any case), else by its Home Assistant area id."""
        for a in self.areas:
            if a.id == ref:
                return a
        low = ref.casefold()
        return next((a for a in self.areas if a.name.casefold() == low or (a.ha_area_id or "").casefold() == low), None)

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
        if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
            raise MapError(f"{what} must be [x, y] or [x, y, z]")
        z = _unit(p[2], what) if len(p) == 3 else DEFAULT_Z
        out.append((round(_unit(p[0], what), 5), round(_unit(p[1], what), 5), round(z, 5)))
    return tuple(out)


def parse_positions(v: Any, count: int, what: str = "positions") -> tuple[int, ...]:
    """Per-pixel positions: a flat list [x0, y0, z0, x1, ...] of numbers 0-1000 (clamped and rounded), or
    a list of [x, y, z]. Empty means not mapped. Otherwise there must be one position per pixel."""
    items = _list(v, what, MAX_PIXELS * 3)
    if items and isinstance(items[0], (list, tuple)):
        flat: list[Any] = []
        for p in items:
            if not isinstance(p, (list, tuple)) or len(p) != 3:
                raise MapError(f"{what} must be [x, y, z] triples or a flat list of numbers")
            flat.extend(p)
        items = flat
    if not items:
        return ()
    if len(items) != 3 * count:
        raise MapError(f"{what}: need {count} positions for {count} pixels, got {len(items) / 3:g}")
    out = []
    for x in items:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise MapError(f"{what} must be numbers")
        out.append(0 if x < 0 else POS_SCALE if x > POS_SCALE else int(round(x)))
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
    area = d.get("area")
    area = "" if area is None else _id(area, f"{name}: area")
    return Run(rid, entry, name, first, count, _bool(d.get("reverse", False), f"{name}: reverse"),
               parse_pts(d.get("pts", []), f"{name}: points"), area,
               parse_positions(d.get("positions", []), count, f"{name}: positions"))


def parse_area(v: Any) -> Area:
    d = _dict(v, "an area")
    name = _text(d.get("name"), "area name")
    ha = d.get("ha_area_id")
    if ha is not None and (not isinstance(ha, str) or not 0 < len(ha) <= MAX_ID or "\0" in ha):
        raise MapError(f"{name}: bad Home Assistant area")
    return Area(_id(d.get("id"), "area id"), name, ha, _bool(d.get("outdoor", False), f"{name}: outdoor"))


def default_axis() -> dict[str, Any]:
    return {"dir": [1.0, 0.0, 0.0], "preset": "left_right"}


def _vec(v: Any, what: str, default_z: float | None = None) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) not in ((2, 3) if default_z is not None else (3,)):
        raise MapError(f"{what} must be [x, y, z]")
    return [_num(c, what, -1000, 1000) for c in v] + ([default_z] if len(v) == 2 and default_z is not None else [])


def parse_axis(v: Any) -> dict[str, Any]:
    """A spatial direction. Stored as {"dir": [x, y, z]} (a unit vector; the pattern moves that way) or
    {"centre": [x, y, z]} (outward from a point), plus "preset" when it is one of PRESETS or "outward".
    Accepted on input: a "preset" name alone, "dir", the version 1 "angle" (0 = left to right, 90 = up)."""
    d = _dict(v, "the axis")
    preset = d.get("preset")
    if preset is not None and (not isinstance(preset, str) or (preset not in PRESETS and preset != OUTWARD)):
        raise MapError("unknown direction preset")
    if "centre" in d or preset == OUTWARD:
        c = _vec(d.get("centre", [0.5, 0.5, DEFAULT_Z]), "the centre", DEFAULT_Z)
        out: dict[str, Any] = {"centre": [round(max(0.0, min(1.0, x)), 5) for x in c]}
        if preset == OUTWARD:
            out["preset"] = OUTWARD
        return out
    if "dir" in d:
        vec = _vec(d["dir"], "the direction")
    elif "angle" in d:
        a = math.radians(_num(d["angle"], "the angle", -3600, 3600))
        vec = [math.cos(a), math.sin(a), 0.0]
    elif preset:
        vec = list(PRESETS[preset])
    else:
        vec = [1.0, 0.0, 0.0]
    length = math.sqrt(sum(c * c for c in vec))
    if length < 1e-9:
        raise MapError("the direction can't be zero")
    vec = [round(c / length, 6) + 0.0 for c in vec]
    out = {"dir": vec}
    for name, ref in PRESETS.items():  # a direction that is one of the presets says so (the panel highlights it)
        if all(abs(a - b) < 1e-5 for a, b in zip(vec, ref)):
            out["preset"] = name
            break
    return out


def parse_zone(v: Any) -> Zone:
    d = _dict(v, "a zone")
    name = _text(d.get("name"), "zone name")
    runs = tuple(_id(r, f"{name}: run id") for r in _list(d.get("runs", []), f"{name}: runs", MAX_ZONE_RUNS))
    if len(set(runs)) != len(runs):
        raise MapError(f"{name}: a run is listed twice")
    areas = tuple(_id(a, f"{name}: area id") for a in _list(d.get("areas", []), f"{name}: areas", MAX_ZONE_AREAS))
    if len(set(areas)) != len(areas):
        raise MapError(f"{name}: an area is listed twice")
    mode = d.get("mode", "string")
    if mode not in MODES:
        raise MapError(f"{name}: mode must be string or spatial")
    pattern = patterns.canonical(d.get("pattern", patterns.DEFAULT_PATTERN))  # a built-in or one of your own
    if pattern is None:
        raise MapError(f"{name}: unknown pattern")
    effect = d.get("effect", patterns.CHASE)
    if effect not in patterns.ALL_EFFECTS:
        raise MapError(f"{name}: unknown effect")
    return Zone(_id(d.get("id"), "zone id"), name, runs, mode, parse_axis(d.get("axis", default_axis())),
                pattern, effect, _int(d.get("speed", patterns.DEFAULT_SPEED), f"{name}: speed", 1, 100), areas)


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


def _flip(p: Any) -> Any:
    """A version 1 [x, y] point (y down) as a version 2 [x, y, z] (y up)."""
    if isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in p):
        return [p[0], round(1 - p[1], 5), DEFAULT_Z]
    return p


def migrate(data: Any) -> Any:
    """Bring a saved or sent map up to the current version. Version 1 had no areas, 2D points with y down
    and the axis as an angle. Every run goes into one area ("House") so zones sweep exactly as before; the
    points keep their place (y flips to point up, z is 0.5) and an angle becomes the same direction. Nothing
    is lost: a map that is not a version 1 object is returned as it is."""
    if not isinstance(data, dict) or data.get("version", VERSION) != 1:
        return data
    out = dict(data)
    out["version"] = VERSION
    runs = []
    for r in data.get("runs", []) if isinstance(data.get("runs"), list) else []:
        if not isinstance(r, dict):
            runs.append(r)
            continue
        r = dict(r)
        if isinstance(r.get("pts"), list):
            r["pts"] = [_flip(p) for p in r["pts"]]
        r.setdefault("area", MAIN_AREA)
        runs.append(r)
    out["runs"] = runs
    zones = []
    for z in data.get("zones", []) if isinstance(data.get("zones"), list) else []:
        if isinstance(z, dict) and isinstance(z.get("axis"), dict):
            z = dict(z)
            axis = dict(z["axis"])
            if isinstance(axis.get("centre"), (list, tuple)):
                axis["centre"] = _flip(list(axis["centre"][:2]))
            z["axis"] = axis  # an "angle" is read by parse_axis (0 = left to right, 90 = up, as before)
        zones.append(z)
    out["zones"] = zones
    if runs and "areas" not in out:
        out["areas"] = [{"id": MAIN_AREA, "name": MAIN_AREA_NAME}]
    out.setdefault("areas", [])
    return out


def light_area(light: LightSpec) -> Area:
    """The area a light's runs start in: its Home Assistant area, else a custom area named after the light."""
    if light.area_id:
        return Area(re.sub(r"[^A-Za-z0-9_.\-]", "_", f"ha_{light.area_id}")[:MAX_ID], (light.area_name or light.area_id)[:MAX_NAME],
                    light.area_id[:MAX_ID])
    return Area(re.sub(r"[^A-Za-z0-9_.\-]", "_", f"light_{light.entry_id}")[:MAX_ID], (light.title or "Light")[:MAX_NAME])


def from_dict(data: Any, lights: Mapping[str, int] | None = None, *, strict: bool = True,
              default_areas: Mapping[str, Area] | None = None) -> MapModel:
    """Parse a map (any version). `lights` maps entry id -> bulbs and is used to bound the runs (a run on an
    unknown light is an error). `default_areas` maps entry id -> the area for a run that doesn't name one.
    Strict: the first problem raises MapError. Not strict (loading a saved map): runs, areas and zones that
    are no longer valid are dropped, so a damaged store never stops setup."""
    d = _dict(migrate(data), "the map")
    big = 10**6
    raw_areas = _list(d.get("areas", []), "areas", MAX_AREAS if strict else big)[:MAX_AREAS]
    raw_runs = _list(d.get("runs", []), "runs", MAX_RUNS if strict else big)[:MAX_RUNS]
    raw_zones = _list(d.get("zones", []), "zones", MAX_ZONES if strict else big)[:MAX_ZONES]
    areas: dict[str, Area] = {}
    area_names: set[str] = set()
    for raw in raw_areas:
        try:
            a = parse_area(raw)
            if a.id in areas:
                raise MapError(f"area id {a.id} is used twice")
            if a.name.casefold() in area_names:
                raise MapError(f"two areas are called {a.name}")
            areas[a.id] = a
            area_names.add(a.name.casefold())
        except MapError:
            if strict:
                raise

    def area_for(r: Run) -> Run:
        """Give a run without an area its light's default one; an unknown area is an error (strict) or
        replaced by the default (lenient)."""
        if r.area and r.area in areas:
            return r
        if r.area and strict:
            raise MapError(f"{r.name}: unknown area {r.area}")
        a = (default_areas or {}).get(r.entry_id) or Area(MAIN_AREA, MAIN_AREA_NAME)
        if a.id not in areas:
            if len(areas) >= MAX_AREAS or a.name.casefold() in area_names:
                a = next(iter(areas.values()), a) if areas else a
            if a.id not in areas:
                areas[a.id] = a
                area_names.add(a.name.casefold())
        return replace(r, area=a.id)

    runs: list[Run] = []
    ids: set[str] = set()
    for raw in raw_runs:
        try:
            if not strict and isinstance(raw, dict) and raw.get("positions"):
                try:  # a damaged position list costs the run its positions, not its place
                    parse_positions(raw["positions"], raw.get("count") if isinstance(raw.get("count"), int) else 0)
                except MapError:
                    raw = {k: v for k, v in raw.items() if k != "positions"}
            entry = raw.get("entry_id") if isinstance(raw, dict) else None
            bound = lights.get(entry) if lights is not None and isinstance(entry, str) else None
            r = parse_run(raw, bound)
            if lights is not None and r.entry_id not in lights:
                raise MapError(f"{r.name}: that light doesn't exist")
            if r.id in ids:
                raise MapError(f"run id {r.id} is used twice")
            r = area_for(r)
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
                z = replace(z, runs=tuple(r for r in z.runs if r in ids))
            missing = [a for a in z.areas if a not in areas]
            if missing:
                if strict:
                    raise MapError(f"{z.name}: unknown area {missing[0]}")
                z = replace(z, areas=tuple(a for a in z.areas if a in areas))
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
    return MapModel(tuple(runs), tuple(zones), backdrop, tuple(areas.values()))


def prune(model: MapModel, entry_ids: Iterable[str]) -> MapModel:
    """Drop runs on lights that no longer exist, and zone references to them."""
    known = set(entry_ids)
    if all(r.entry_id in known for r in model.runs):
        return model
    runs = tuple(r for r in model.runs if r.entry_id in known)
    ids = {r.id for r in runs}
    zones = tuple(replace(z, runs=tuple(r for r in z.runs if r in ids)) for z in model.zones)
    return replace(model, runs=runs, zones=zones)


def _axis_dict(axis: Mapping[str, Any]) -> dict[str, Any]:
    return {k: list(v) if isinstance(v, (list, tuple)) else v for k, v in axis.items()}


def to_dict(model: MapModel) -> dict[str, Any]:
    runs = []
    for r in model.runs:
        row: dict[str, Any] = {"id": r.id, "entry_id": r.entry_id, "name": r.name, "first": r.first, "count": r.count,
                               "reverse": r.reverse, "area": r.area, "pts": [list(p) for p in r.pts]}
        if r.positions:
            row["positions"] = list(r.positions)
        runs.append(row)
    return {
        "version": VERSION,
        "areas": [{"id": a.id, "name": a.name, **({"ha_area_id": a.ha_area_id} if a.ha_area_id else {}),
                   **({"outdoor": True} if a.outdoor else {})} for a in model.areas],
        "runs": runs,
        "zones": [{"id": z.id, "name": z.name, "runs": list(z.runs), "areas": list(z.areas), "mode": z.mode,
                   "axis": _axis_dict(z.axis), "pattern": z.pattern, "effect": z.effect, "speed": z.speed}
                  for z in model.zones],
        "backdrop": dict(model.backdrop),
    }


# --- defaults -----------------------------------------------------------------------------
def _line(y: float, x0: float = 0.05, x1: float = 0.95) -> tuple[Point, ...]:
    return ((x0, y, DEFAULT_Z), (x1, y, DEFAULT_Z))


def pack_positions(points: Iterable[Sequence[float]]) -> tuple[int, ...]:
    """[(x, y, z) in 0-1] -> the flat ints a run stores."""
    out: list[int] = []
    for p in points:
        out.extend(int(round(max(0.0, min(1.0, c)) * POS_SCALE)) for c in p[:3])
    return tuple(out)


def default_map(lights: Sequence[LightSpec]) -> MapModel:
    """One run per light covering all its bulbs, in the light's area (the WLED kitchen with 904 LEDs gets its
    four runs); Twinkly lights take their positions from the layout the strip reports. No zones."""
    runs: list[Run] = []
    areas: dict[str, Area] = {}
    per_area: dict[str, int] = {}
    for light in lights:
        if light.bulbs >= 1:
            a = light_area(light)
            areas.setdefault(a.id, a)
            per_area[a.id] = per_area.get(a.id, 0) + 1
    seen: dict[str, int] = {}
    for i, light in enumerate(lights):
        if light.bulbs < 1:
            continue
        area = light_area(light)
        row = seen[area.id] = seen.get(area.id, -1) + 1
        y = 1 - (row + 0.5) / per_area[area.id]
        if light.kind == "wled" and light.bulbs == KITCHEN_PIXELS:
            for part in KITCHEN_PRESET:
                runs.append(Run(f"r{len(runs) + 1}", light.entry_id, part["name"], part["first"], part["count"],
                                False, _line(part["y"]), area.id))
            continue
        pos = fit_layout(light.layout) if light.layout is not None and len(light.layout) == light.bulbs else None
        runs.append(Run(f"r{len(runs) + 1}", light.entry_id, light.title[:MAX_NAME] or f"Light {i + 1}",
                        0, light.bulbs, False, _line(y), area.id, pos or ()))
    runs = runs[:MAX_RUNS]
    used = {r.area for r in runs}
    return MapModel(tuple(runs), (), {"kind": "none", "w": 1, "h": 1}, tuple(a for a in areas.values() if a.id in used))


def fit_layout(layout: Sequence[Sequence[float]]) -> tuple[int, ...] | None:
    """A strip's own LED positions ([x, y] or [x, y, z] per LED, y down as the card draws them) scaled to fit
    0-1 with a margin and the aspect ratio kept; y comes out pointing up. Returns the flat ints a run stores,
    or None when the layout is unusable. z is centred on 0.5 (and is 0.5 for a flat layout)."""
    try:
        xs = [float(p[0]) for p in layout]
        ys = [float(p[1]) for p in layout]
        has_z = bool(layout) and all(len(p) >= 3 for p in layout)
        zs = [float(p[2]) for p in layout] if has_z else []
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or len(xs) > MAX_PIXELS or not all(math.isfinite(v) for v in xs + ys + zs):
        return None
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    d = (max(zs) - min(zs)) if has_z else 0.0
    span = max(w, h, d) or 1.0
    scale, margin = 0.9 / span, 0.05
    z0 = (min(zs) + max(zs)) / 2 if has_z else 0.0
    pts = [(margin + (x - x0) * scale + (0.9 - w * scale) / 2,
            1 - (margin + (y - y0) * scale + (0.9 - h * scale) / 2),
            DEFAULT_Z + ((zs[k] - z0) * scale if has_z else 0.0)) for k, (x, y) in enumerate(zip(xs, ys))]
    return pack_positions(pts)


# --- geometry -----------------------------------------------------------------------------
def is_mapped(run: Run) -> bool:
    return len(run.positions) == 3 * run.count


def run_positions(run: Run) -> list[Point]:
    """Where each of the run's pixels is, as (x, y, z) in 0-1 (index 0 = pixel `first`, whatever `reverse`
    says). Mapped pixels use their own positions; otherwise one polyline point per pixel is used as is, or
    the pixels are spread evenly along the polyline."""
    n = run.count
    if is_mapped(run):
        p, k = run.positions, 1 / POS_SCALE
        return [(p[i] * k, p[i + 1] * k, p[i + 2] * k) for i in range(0, 3 * n, 3)]
    pts = list(run.pts)
    if len(pts) == n:
        return pts
    if not pts:
        pts = list(_line(0.5))
    if len(pts) == 1:
        return [pts[0]] * n
    seg = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
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
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f))
    return out


@dataclass(frozen=True)
class Slice:
    """Virtual indexes [start, start + run.count) belong to this run."""
    run: Run
    start: int

    @property
    def count(self) -> int:
        return self.run.count


def zone_runs(zone: Zone, runs: Mapping[str, Run]) -> list[Run]:
    """The zone's runs in order: the listed runs first (references to runs that don't exist are skipped),
    then every run of each listed area in the map's run order, none twice."""
    out: list[Run] = []
    seen: set[str] = set()
    for rid in zone.runs:
        if (run := runs.get(rid)) is not None and rid not in seen:
            out.append(run)
            seen.add(rid)
    for area in zone.areas:
        for run in runs.values():
            if run.area == area and run.id not in seen:
                out.append(run)
                seen.add(run.id)
    return out


def zone_slices(zone: Zone, runs: Mapping[str, Run]) -> list[Slice]:
    out, start = [], 0
    for run in zone_runs(zone, runs):
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
