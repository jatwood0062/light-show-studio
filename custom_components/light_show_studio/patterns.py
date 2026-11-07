"""Patterns (which colour each bulb is) and pattern effects (how they move), picked separately.

A pattern is a list of colours repeated along the string. A pattern effect takes the pattern and
animates it: twinkle, chase, fade and so on. Speed scales time for every effect (1-100, 50 = normal).
Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import colorsys
import math

from . import user_patterns
from .protocol import Colour

PATTERN = "Pattern"  # the light's effect name while a pattern + pattern effect is showing

# Classic C9 multicolour: same colours as the "Classic Multicolour" effect (red, green, blue, orange, pink).
PATTERNS: dict[str, tuple[list[Colour], int]] = {  # name -> (colours, bulbs per colour)
    "Classic Multicolor": ([(255, 0, 0), (0, 255, 0), (0, 60, 255), (255, 80, 0), (255, 0, 120)], 1),
    "Warm White": ([(255, 150, 40)], 1),
    "Cool White": ([(200, 220, 255)], 1),
    "Red & Green": ([(255, 0, 0), (0, 200, 0)], 1),
    "Red, Green & White": ([(255, 0, 0), (0, 200, 0), (255, 200, 140)], 1),
    "Candy Cane": ([(255, 0, 0), (255, 255, 255)], 2),
    "Blue & White": ([(0, 40, 255), (200, 220, 255)], 1),
    "Gold & White": ([(255, 140, 0), (255, 200, 140)], 1),
    "Pastel": ([(255, 120, 170), (120, 200, 255), (255, 220, 90), (150, 255, 140), (190, 130, 255)], 1),
    "Halloween": ([(255, 70, 0), (140, 0, 255), (40, 255, 0)], 1),
    "Red, White & Blue": ([(255, 0, 0), (255, 255, 255), (0, 40, 255)], 1),
    "Valentine": ([(255, 0, 0), (255, 20, 110), (255, 200, 200)], 1),
    "Rainbow": ([], 1),  # special: a smooth rainbow along the string
}
PATTERN_LIST = list(PATTERNS)
DEFAULT_PATTERN = "Classic Multicolor"

STEADY, TWINKLE, CHASE = "Steady", "Twinkle", "Chase"
PATTERN_EFFECTS = [STEADY, TWINKLE, CHASE, "Twinkle Swap", "Marquee", "Fade", "Sparkle", "Alternate", "Wave"]
DEFAULT_EFFECT = TWINKLE
DEFAULT_SPEED = 50

# Effects that need positions (an area's 3D map): zones and area schedules only. On one light they become the
# nearest string effect.
MAP_EFFECTS = ["Sweep", "Ripple", "Snowfall", "Rise", "Rainbow Wash", "Fireworks", "Breathe Outward"]
ALL_EFFECTS = PATTERN_EFFECTS + MAP_EFFECTS
STRING_EQUIVALENT = {"Sweep": CHASE, "Ripple": "Wave", "Snowfall": TWINKLE, "Rise": CHASE, "Rainbow Wash": CHASE,
                     "Fireworks": "Sparkle", "Breathe Outward": "Fade"}


def light_effect(effect: str | None) -> str:
    """The pattern effect a single light can show for `effect`: itself, a map effect's nearest string effect,
    else the default."""
    if effect in PATTERN_EFFECTS:
        return effect  # type: ignore[return-value]
    return STRING_EQUIVALENT.get(effect, DEFAULT_EFFECT)  # type: ignore[arg-type]


def valid_effect(effect: object) -> str:
    """For schemas: a pattern effect or a map effect, else ValueError."""
    if effect in ALL_EFFECTS:
        return effect  # type: ignore[return-value]
    raise ValueError(f"unknown effect {effect!r}")


# --- your own patterns (see user_patterns.py and pattern_store.py) ---------------------------------------
_USER: dict[str, user_patterns.UserPattern] = {}  # id -> pattern (saved ones and live previews)
_USER_NAMES: dict[str, str] = {}  # casefolded name -> id
_user_epoch = 0  # bumped whenever the set of patterns changes, so cached plans know


def user_epoch() -> int:
    return _user_epoch


def set_user_patterns(items: list[user_patterns.UserPattern]) -> None:
    """Replace the saved patterns (loading them). Live previews are kept."""
    global _user_epoch
    keep = [p for p in _USER.values() if p.name.startswith(user_patterns.RESERVED)]
    _USER.clear()
    _USER_NAMES.clear()
    for p in [*items, *keep]:
        register_user_pattern(p)
    _user_epoch += 1


def register_user_pattern(p: user_patterns.UserPattern) -> None:
    global _user_epoch
    old = _USER.get(p.id)
    if old is not None:
        _USER_NAMES.pop(old.name.casefold(), None)
    _USER[p.id] = p
    _USER_NAMES[p.name.casefold()] = p.id
    _user_epoch += 1


def unregister_user_pattern(pattern_id: str) -> None:
    global _user_epoch
    if (old := _USER.pop(pattern_id, None)) is not None:
        _USER_NAMES.pop(old.name.casefold(), None)
        _user_epoch += 1


def touch_user_patterns() -> None:
    global _user_epoch
    _user_epoch += 1


def user_pattern(ref: object) -> user_patterns.UserPattern | None:
    """One of your patterns by name (any case) or by id."""
    if not isinstance(ref, str):
        return None
    pid = _USER_NAMES.get(ref.casefold())
    return _USER.get(pid) if pid else _USER.get(ref)


def saved_user_patterns() -> list[user_patterns.UserPattern]:
    return [p for p in _USER.values() if not p.name.startswith(user_patterns.RESERVED)]


def canonical(ref: object) -> str | None:
    """The name of a built-in pattern, or of one of yours (given by name or id); None if there is no such pattern."""
    if isinstance(ref, str) and ref in PATTERNS:
        return ref
    up = user_pattern(ref)
    return up.name if up is not None else None


def is_pattern(ref: object) -> bool:
    return canonical(ref) is not None


def pattern_names() -> list[str]:
    """Every pattern a person can pick: the built-ins, then their own in the order they were made."""
    return [*PATTERN_LIST, *(p.name for p in saved_user_patterns())]


def valid_pattern(ref: object) -> str:
    """For schemas: the pattern's name, else ValueError."""
    if (name := canonical(ref)) is None:
        raise ValueError(f"unknown pattern {ref!r}")
    return name


def _spec(name: str) -> tuple[list[Colour], int]:
    spec = PATTERNS.get(name)
    if spec is not None:
        return spec
    up = user_pattern(name)
    return up.spec if up is not None else PATTERNS[DEFAULT_PATTERN]


def palette_of(name: str) -> list[Colour]:
    """The colours a pattern is made of (a rainbow is sampled), for effects that pick a colour at random."""
    cols = _spec(name)[0]
    return list(cols) if cols else [_rainbow_colour(i / 12) for i in range(12)]


def speed_factor(speed: int | float) -> float:
    """1-100 -> 0.25x-4x on a log scale, with 50 = 1x."""
    try:
        s = max(1.0, min(100.0, float(speed)))
    except (TypeError, ValueError):
        s = 50.0
    return 4 ** ((s - 50) / 50) if s >= 50 else 4 ** ((s - 50) / 49)


def _hash(a: int, b: int = 0) -> float:
    a, b = int(a) & 0xFFFFFFFF, int(b) & 0xFFFFFFFF
    x = (a * 374761393 + b * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFF) / 65536.0


def _scale(c: Colour, f: float) -> Colour:
    f = max(0.0, min(1.0, f))
    return int(c[0] * f), int(c[1] * f), int(c[2] * f)


def _mix(a: Colour, b: Colour, f: float) -> Colour:
    f = max(0.0, min(1.0, f))
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


def _rainbow_colour(p: float) -> Colour:
    r, g, b = colorsys.hsv_to_rgb(p % 1.0, 1.0, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def _colour_at(name: str, pos: float, n: int) -> Colour:
    """Colour of the pattern at (possibly fractional) bulb position pos, blending between bulbs."""
    cols, width = _spec(name)
    if not cols:  # rainbow
        return _rainbow_colour(pos / max(1, min(n, 50)))
    k = len(cols) * width
    p = pos % k
    i = int(p)
    a, b = cols[(i // width) % len(cols)], cols[((i + 1) // width) % len(cols)]
    f = max(0.0, min(1.0, (p - i - 0.25) / 0.5))  # bulbs hold their colour; only the edges blend
    return _mix(a, b, f * f * (3 - 2 * f)) if a != b else a


def _bulb_colour(name: str, i: int, n: int) -> Colour:
    cols, width = _spec(name)
    if not cols:
        return _rainbow_colour(i / max(1, min(n, 50)))
    return cols[(i // width) % len(cols)]


def _list_at(base: list[Colour], pos: float) -> Colour:
    """Colour at a fractional position along a list of colours, wrapping round and blending between neighbours
    (bulbs hold their colour; only the edges blend, as for a palette)."""
    n = len(base)
    p = pos % n
    i = int(p)
    a, b = base[i % n], base[(i + 1) % n]
    f = max(0.0, min(1.0, (p - i - 0.25) / 0.5))
    return _mix(a, b, f * f * (3 - 2 * f)) if a != b else a


def base_colours(pattern: str, n: int, light: str | None = None) -> list[Colour]:
    """The pattern's colours along n bulbs before any effect. `light` (a config entry id) lets painted pixels
    show where they were painted."""
    up = user_pattern(pattern)
    if pattern not in PATTERNS and up is not None and up.kind == "pixels":
        return list(up.light_base(light, n))
    return [_bulb_colour(pattern, i, n) for i in range(n)]


def render(pattern: str, effect: str, t: float, n: int, light: str | None = None,
           base: list[Colour] | None = None) -> list[Colour]:
    """One frame. t is already scaled by the speed. Unknown names fall back to the defaults. Your own patterns
    work like built-in ones; `light` is the config entry id of the light (painted pixels), and `base` is the
    pattern's colours when the caller already worked them out (a zone: it knows where each pixel sits)."""
    n = max(0, int(n))
    if (name := canonical(pattern)) is None:
        name = DEFAULT_PATTERN
    pattern = name
    if effect not in PATTERN_EFFECTS:
        effect = light_effect(effect)
    up = None if pattern in PATTERNS else user_pattern(pattern)
    painted = base is not None or (up is not None and up.kind == "pixels")
    if base is None:
        base = base_colours(pattern, n, light)
    if effect == CHASE:  # the pattern glides along the string, ~3 bulbs a second
        if painted and base:
            return [_list_at(base, i - t * 3.0) for i in range(n)]
        return [_colour_at(pattern, i - t * 3.0, n) for i in range(n)]
    if effect == TWINKLE:  # classic twinkle: each bulb fades down and back up at its own pace
        out = []
        for i, c in enumerate(base):
            rate, phase = 0.5 + _hash(i, 1) * 0.9, _hash(i, 2) * 6.283
            w = t * rate * 2.2 + phase  # a slow swell with a little wobble so no two bulbs repeat alike
            v = 0.5 + 0.38 * math.sin(w) + 0.12 * math.sin(w * 2.3 + phase * 3)
            out.append(_scale(c, 0.1 + 0.9 * max(0.0, min(1.0, v)) ** 2))
        return out
    if effect == "Twinkle Swap":  # each bulb dims out, comes back as another colour from the pattern
        cols = _spec(pattern)[0]
        out = []
        for i in range(n):
            period = 2.5 + _hash(i, 3) * 2.5
            x = t / period + _hash(i, 4)
            cycle, f = int(math.floor(x)), x - math.floor(x)
            if cols:
                c = cols[int(_hash(i, cycle) * len(cols)) % len(cols)]
            else:
                c = _rainbow_colour(_hash(i, cycle))
            out.append(_scale(c, min(1.0, math.sin(f * math.pi) * 1.6)))
        return out
    if effect == "Marquee":  # every third bulb lit, stepping along like a theatre sign
        step = int(t * 4) % 3
        return [c if i % 3 == step else _scale(c, 0.08) for i, c in enumerate(base)]
    if effect == "Fade":  # the whole string breathes
        v = 0.5 + 0.5 * math.sin(t * 1.6)
        return [_scale(c, 0.1 + 0.9 * v) for c in base]
    if effect == "Sparkle":  # the pattern with quick white glints
        x = t * 5  # a few glints a second, each fading out over its slot so it never flickers
        slot, f = int(math.floor(x)), x - math.floor(x)
        return [_mix((255, 255, 255), c, f * f) if _hash(i, slot) < 0.05 else c for i, c in enumerate(base)]
    if effect == "Alternate":  # odd and even bulbs take turns
        on_even = int(t * 1.5) % 2 == 0
        return [c if (i % 2 == 0) == on_even else _scale(c, 0.08) for i, c in enumerate(base)]
    if effect == "Wave":  # a slow wave of brightness travels along the string
        return [_scale(c, 0.15 + 0.85 * (0.5 + 0.5 * math.sin(i * 0.45 - t * 2.5)) ** 2) for i, c in enumerate(base)]
    return list(base)  # Steady


def is_animated(effect: str) -> bool:
    return effect != STEADY


# Building blocks for mapped_patterns (effects laid along a position instead of a bulb number).
colour_at, bulb_colour, scale, mix, list_at = _colour_at, _bulb_colour, _scale, _mix, _list_at
