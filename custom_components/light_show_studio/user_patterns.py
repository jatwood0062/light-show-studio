"""Your own patterns: a palette (colours repeated along a string) or painted pixels (a colour for each pixel of a
light, and/or colours painted at points in an area's 3D space). Validation and the colour maths only; where they
are stored and shown is pattern_store.py. Nothing here needs Home Assistant, so it is easy to test.

A saved pattern looks like
  {"id", "name", "kind": "palette", "colours": [[r,g,b]...], "bulbs_per_colour": 1, "default_effect", "default_speed"}
  {"id", "name", "kind": "pixels", "lights": {entry_id: {"first": 0, "colours": [[r,g,b]...]}},
   "areas": {area_id: [{"pos": [x,y,z], "colour": [r,g,b]}...]}, "default_effect", "default_speed"}
"""
from __future__ import annotations

import math
import re
import secrets
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

Colour = tuple[int, int, int]

MAX_PATTERNS = 200
MAX_NAME = 40
MAX_COLOURS = 16  # in a palette
MAX_BULBS_PER_COLOUR = 20
MAX_LIGHT_PIXELS = 2040  # a light never has more
MAX_AREA_POINTS = 2040
MAX_TARGETS = 64  # lights (and, separately, areas) in one pixels pattern
MAX_PAINTED = 12000  # colours + points in one pattern, all told
MAX_TOTAL_PAINTED = 100_000  # painted pixels + points across all saved patterns
MAX_PAINT_BATCH = 500  # pixels in one patterns/paint call
POS_MAX = 1000
KINDS = ("palette", "pixels")
RESERVED = "~"  # names starting with this are the live preview's own
_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}\Z")
FALLBACK_COLOURS = 16  # a pixels pattern repeats at most this many of its colours where it has no paint


def new_id() -> str:
    return secrets.token_hex(4)


# --- validation -------------------------------------------------------------------------
def clean_colour(v: Any, what: str = "a colour") -> Colour:
    if isinstance(v, (str, bytes)) or not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError(f"{what} must be [r, g, b]")
    out = []
    for c in v:
        if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= 255:
            raise ValueError(f"{what} must be three whole numbers from 0 to 255")
        out.append(c)
    return out[0], out[1], out[2]


def _int(v: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{what} must be a whole number from {lo} to {hi}")
    return v


def _ref(v: Any, what: str) -> str:
    if not isinstance(v, str) or not 0 < len(v) <= 64 or "\0" in v:
        raise ValueError(f"{what} must be 1 to 64 characters")
    return v


def clean_name(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("The name must be text")
    name = " ".join(v.split())
    if not 1 <= len(name) <= MAX_NAME:
        raise ValueError(f"The name must be 1 to {MAX_NAME} characters")
    if any(ord(ch) < 32 for ch in name):
        raise ValueError("The name has invalid characters")
    if name.startswith(RESERVED):
        raise ValueError(f"The name can't start with {RESERVED}")
    return name


def validate(data: Any, *, builtin_names: Collection[str], effects: Collection[str], taken: Iterable[str] = (),
             default_effect: str = "Twinkle", default_speed: int = 50, require_name: bool = True) -> dict[str, Any]:
    """A clean pattern (without an id) from what a person sent; ValueError says what is wrong in plain words.
    `taken` are the other patterns' names (compared ignoring case); builtin_names can't be reused either."""
    if not isinstance(data, Mapping):
        raise ValueError("A pattern must be an object")
    allowed = {"id", "name", "kind", "colours", "bulbs_per_colour", "lights", "areas", "default_effect", "default_speed"}
    if extra := sorted(str(k) for k in data if k not in allowed):
        raise ValueError(f"Unknown field {extra[0]}")
    if "name" in data or require_name:
        name = clean_name(data.get("name"))
        low = name.casefold()
        if low in {b.casefold() for b in builtin_names}:
            raise ValueError(f"{name} is the name of a built-in pattern")
        if low in {t.casefold() for t in taken}:
            raise ValueError(f"There is already a pattern called {name}")
    else:
        name = "Preview"
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError("The kind must be palette or pixels")
    effect = data.get("default_effect", default_effect)
    if effect not in effects:
        raise ValueError("Unknown default effect")
    speed = _int(data.get("default_speed", default_speed), "The default speed", 1, 100)
    out: dict[str, Any] = {"name": name, "kind": kind, "default_effect": effect, "default_speed": speed}
    if kind == "palette":
        if "lights" in data or "areas" in data:
            raise ValueError("A palette pattern has colours, not painted pixels")
        cols = data.get("colours")
        if not isinstance(cols, (list, tuple)) or not 1 <= len(cols) <= MAX_COLOURS:
            raise ValueError(f"A palette needs 1 to {MAX_COLOURS} colours")
        out["colours"] = [list(clean_colour(c)) for c in cols]
        out["bulbs_per_colour"] = _int(data.get("bulbs_per_colour", 1), "Bulbs per colour", 1, MAX_BULBS_PER_COLOUR)
        return out
    if "colours" in data or "bulbs_per_colour" in data:
        raise ValueError("A pixels pattern has lights and areas, not a palette")
    lights, areas = data.get("lights", {}), data.get("areas", {})
    if not isinstance(lights, Mapping) or not isinstance(areas, Mapping):
        raise ValueError("lights and areas must be objects")
    if len(lights) > MAX_TARGETS or len(areas) > MAX_TARGETS:
        raise ValueError(f"At most {MAX_TARGETS} lights and {MAX_TARGETS} areas")
    total = 0
    out_lights: dict[str, Any] = {}
    for entry_id, row in lights.items():
        _ref(entry_id, "A light id")
        if not isinstance(row, Mapping) or set(row) - {"first", "colours"}:
            raise ValueError("A painted light is {first, colours}")
        first = _int(row.get("first", 0), "first", 0, MAX_LIGHT_PIXELS - 1)
        cols = row.get("colours")
        if not isinstance(cols, (list, tuple)) or not 1 <= len(cols) <= MAX_LIGHT_PIXELS - first:
            raise ValueError(f"A painted light needs 1 to {MAX_LIGHT_PIXELS - first} colours")
        total += len(cols)
        if total > MAX_PAINTED:  # fail before copying any more
            raise ValueError(f"At most {MAX_PAINTED} painted pixels and points in one pattern")
        out_lights[entry_id] = {"first": first, "colours": [list(clean_colour(c)) for c in cols]}
    out_areas: dict[str, Any] = {}
    for area_id, pts in areas.items():
        _ref(area_id, "An area id")
        if not isinstance(pts, (list, tuple)) or not 1 <= len(pts) <= MAX_AREA_POINTS:
            raise ValueError(f"A painted area needs 1 to {MAX_AREA_POINTS} points")
        rows = []
        for p in pts:
            total += 1
            if total > MAX_PAINTED:
                raise ValueError(f"At most {MAX_PAINTED} painted pixels and points in one pattern")
            if not isinstance(p, Mapping) or set(p) - {"pos", "colour"}:
                raise ValueError("A painted point is {pos: [x, y, z], colour: [r, g, b]}")
            pos = p.get("pos")
            if not isinstance(pos, (list, tuple)) or len(pos) != 3:
                raise ValueError("A position is [x, y, z]")
            rows.append({"pos": [_int(c, "A position", 0, POS_MAX) for c in pos], "colour": list(clean_colour(p.get("colour")))})
        out_areas[area_id] = rows
    if not out_lights and not out_areas:
        raise ValueError("Paint at least one light or area")
    out["lights"], out["areas"] = out_lights, out_areas
    return out


def painted_count(data: Mapping[str, Any]) -> int:
    """Painted pixels and points in a clean pixels pattern (0 for a palette)."""
    return (sum(len(r["colours"]) for r in data.get("lights", {}).values())
            + sum(len(p) for p in data.get("areas", {}).values()))


# --- the compiled pattern ----------------------------------------------------------------
def _distinct(colours: Iterable[Colour], limit: int) -> list[Colour]:
    seen: dict[Colour, None] = {}
    for c in colours:
        if c not in seen:
            seen[c] = None
            if len(seen) >= limit:
                break
    return list(seen)


class UserPattern:
    """A saved (or preview) pattern ready to render: tuples instead of lists, the fallback palette worked out."""

    def __init__(self, data: Mapping[str, Any], pattern_id: str) -> None:
        self.id = pattern_id
        self.name: str = data["name"]
        self.kind: str = data["kind"]
        self.default_effect: str = data["default_effect"]
        self.default_speed: int = data["default_speed"]
        self.bulbs_per_colour: int = data.get("bulbs_per_colour", 1)
        self.version = 0  # bumped by paint, so cached frames notice
        self.lights: dict[str, tuple[int, list[Colour]]] = {}
        self.areas: dict[str, tuple[list[tuple[int, int, int]], list[Colour]]] = {}
        self._cache: dict[tuple, list[Colour]] = {}
        if self.kind == "palette":
            self.palette: list[Colour] = [tuple(c) for c in data["colours"]]  # type: ignore[misc]
            return
        for entry_id, row in data["lights"].items():
            self.lights[entry_id] = (row["first"], [tuple(c) for c in row["colours"]])  # type: ignore[misc]
        for area_id, pts in data["areas"].items():
            self.areas[area_id] = ([tuple(p["pos"]) for p in pts], [tuple(p["colour"]) for p in pts])  # type: ignore[misc]
        self._refresh_palette()

    def painted_count(self) -> int:
        return sum(len(c) for _, c in self.lights.values()) + sum(len(c) for _, c in self.areas.values())

    def _refresh_palette(self) -> None:
        def every():
            for _, cols in self.lights.values():
                yield from cols
            for _, cols in self.areas.values():
                yield from cols
        self.palette = _distinct(every(), FALLBACK_COLOURS) or [(255, 255, 255)]
        self._cache.clear()

    @property
    def spec(self) -> tuple[list[Colour], int]:
        """(colours, bulbs per colour): the palette, or for painted pixels the colours it uses, repeated."""
        return self.palette, (self.bulbs_per_colour if self.kind == "palette" else 1)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "name": self.name, "kind": self.kind,
                               "default_effect": self.default_effect, "default_speed": self.default_speed}
        if self.kind == "palette":
            out["colours"] = [list(c) for c in self.palette]
            out["bulbs_per_colour"] = self.bulbs_per_colour
        else:
            out["lights"] = {e: {"first": f, "colours": [list(c) for c in cols]} for e, (f, cols) in self.lights.items()}
            out["areas"] = {a: [{"pos": list(p), "colour": list(c)} for p, c in zip(pts, cols)]
                            for a, (pts, cols) in self.areas.items()}
        return out

    def painted_pixel(self, entry_id: str | None, index: int) -> Colour | None:
        """The colour painted on pixel `index` of that light, or None where it has none."""
        row = self.lights.get(entry_id) if entry_id else None
        if row is None:
            return None
        first, cols = row
        return cols[index - first] if 0 <= index - first < len(cols) else None

    def light_base(self, entry_id: str | None, n: int) -> list[Colour]:
        """One light's colours before any effect: its painted pixels, the palette of used colours repeated where
        nothing is painted (a light it doesn't cover gets the palette all along)."""
        key = (entry_id, n)
        got = self._cache.get(key)
        if got is None:
            pal, k = self.palette, len(self.palette)
            row = self.lights.get(entry_id) if entry_id else None
            if row is None:
                got = [pal[i % k] for i in range(n)]
            else:
                first, cols = row
                got = [cols[i - first] if 0 <= i - first < len(cols) else pal[i % k] for i in range(n)]
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = got
        return got

    def paint(self, entry_id: str, items: Sequence[tuple[int, Colour]]) -> int:
        """Set pixels of one light: [(index, colour)]. The painted range grows to cover them (gaps are black)."""
        first, cols = self.lights.get(entry_id, (0, []))
        cols = list(cols)
        for index, colour in items:
            if index < first:  # painting before the start: move the start back
                cols = [(0, 0, 0)] * (first - index) + cols
                first = index
            if index - first >= len(cols):
                cols.extend([(0, 0, 0)] * (index - first - len(cols) + 1))
            cols[index - first] = colour
        self.lights[entry_id] = (first, cols)
        self.version += 1
        self._refresh_palette()
        return len(items)


# --- nearest painted point ------------------------------------------------------------------
def nearest_colours(points: Sequence[Sequence[float]], colours: Sequence[Colour],
                    queries: Sequence[Sequence[float]]) -> list[Colour]:
    """For each query position (x, y, z in 0-1000) the colour of the nearest painted point. A grid keeps it quick
    with thousands of each."""
    if not points:
        raise ValueError("no points")
    if len(points) <= 16:
        out = []
        for q in queries:
            qx, qy, qz = q[0], q[1], q[2] if len(q) > 2 else 500
            best = min(range(len(points)), key=lambda i: (points[i][0] - qx) ** 2 + (points[i][1] - qy) ** 2
                       + (points[i][2] - qz) ** 2)
            out.append(colours[best])
        return out
    cell = 100.0
    grid: dict[tuple[int, int, int], list[int]] = {}
    for i, p in enumerate(points):
        grid.setdefault((int(p[0] // cell), int(p[1] // cell), int(p[2] // cell)), []).append(i)
    out = []
    for q in queries:
        qx, qy, qz = q[0], q[1], q[2] if len(q) > 2 else 500
        cx, cy, cz = int(qx // cell), int(qy // cell), int(qz // cell)
        best, best_d = -1, math.inf
        for ring in range(0, 12):
            # cells at Chebyshev distance `ring`; anything in a farther ring is at least (ring - 1) * cell + slack away
            if best >= 0 and ((ring - 1) * cell) ** 2 > best_d:
                break
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for i in grid.get((cx + dx, cy + dy, cz + dz), ()):
                            p = points[i]
                            d = (p[0] - qx) ** 2 + (p[1] - qy) ** 2 + (p[2] - qz) ** 2
                            if d < best_d:
                                best, best_d = i, d
        out.append(colours[best])
    return out
