"""Bulbs that stay dark: the text a person types ("3, 7-12, 40") and the set of bulbs it means. Pure."""
from __future__ import annotations

import re
from typing import Any, Iterable

MAX_BULB = 2040  # no light has more bulbs
MAX_TEXT = 4000
_PART = re.compile(r"^(\d{1,4})(?:\s*-\s*(\d{1,4}))?$")


def parse_mask(text: Any, bulbs: int | None = None) -> frozenset[int]:
    """The bulbs a mask names, 0-based (people count from 1: "1" is the bulb at the plug). Parts are single
    bulbs or ranges ("7-12"), separated by commas, semicolons or spaces. Empty / None = no bulbs. Bulbs past
    the end of a light with `bulbs` bulbs are left out (the light may have been shortened). ValueError says
    what is wrong."""
    if text is None:
        return frozenset()
    if isinstance(text, bool) or not isinstance(text, (str, int)):
        raise ValueError("expected bulb numbers like 3, 7-12")
    text = str(text).strip()
    if len(text) > MAX_TEXT:
        raise ValueError("too long")
    out: set[int] = set()
    for part in re.split(r"[,;]|(?<![-\s])\s+(?!\s*-)", text):
        if not (part := (part or "").strip()):
            continue
        if (m := _PART.match(part)) is None:
            raise ValueError(f"{part!r} is not a bulb number or a range like 7-12")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else a
        if a > b:
            a, b = b, a
        if a < 1 or b > MAX_BULB:
            raise ValueError(f"bulb numbers run from 1 to {MAX_BULB}")
        out.update(range(a - 1, b))
    if bulbs is not None:
        out = {i for i in out if i < bulbs}
    return frozenset(out)


def format_mask(indices: Iterable[int]) -> str:
    """The compact text for a set of 0-based bulbs: "3,7-12,40" (1-based, sorted, runs joined)."""
    parts: list[str] = []
    run: list[int] = []
    for i in sorted(set(indices)) + [None]:  # type: ignore[list-item]
        if run and i is not None and i == run[-1] + 1:
            run.append(i)
            continue
        if run:
            parts.append(str(run[0] + 1) if len(run) == 1 else f"{run[0] + 1}-{run[-1] + 1}")
        run = [i] if i is not None else []
    return ",".join(parts)


def clean_mask(text: Any, bulbs: int | None = None) -> str:
    """What a person typed, checked and tidied into the stored form ("" = nothing masked). ValueError if bad."""
    return format_mask(parse_mask(text, bulbs))
