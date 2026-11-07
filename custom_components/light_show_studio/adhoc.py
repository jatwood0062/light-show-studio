"""Zones made up on the fly for music sync and pattern previews: some whole lights and/or some areas, never saved.

Nothing here needs Home Assistant (the map manager is passed in), so it is easy to test.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import mapping, patterns
from .mapping import Area, MapError, Run, Zone

MAX_TARGETS = 64


class TargetError(ValueError):
    """The lights or areas asked for can't be used; the message says why, in words for the person."""


def build(mgr: Any, lights: Sequence[str], areas: Sequence[str], zone_id: str, name: str, pattern: str,
          effect: str = patterns.STEADY, speed: int = patterns.DEFAULT_SPEED, mode: str = "string",
          axis: Any = None) -> tuple[Zone, dict[str, Run], list[str], list[Area]]:
    """(zone, run map, light entry ids it touches, areas) for whole `lights` (config entry ids) and `areas` (ids or
    names). A light that already has runs in one of the areas is driven through them rather than as a whole."""
    if not lights and not areas:
        raise TargetError("Pick at least one light or area")
    if len(lights) > MAX_TARGETS or len(areas) > MAX_TARGETS:
        raise TargetError(f"At most {MAX_TARGETS} lights and {MAX_TARGETS} areas")
    if mode not in mapping.MODES:
        raise TargetError("Mode must be string or spatial")
    try:
        ax = mapping.parse_axis(axis if axis is not None else mapping.default_axis())
    except MapError as err:
        raise TargetError(str(err)) from err
    model = mgr.model
    found: list[Area] = []
    for ref in areas:
        area = model.area(ref)
        if area is None:
            raise TargetError(f"No area called {ref}")
        if area not in found:
            found.append(area)
    area_ids = {a.id for a in found}
    runs = dict(model.run_map())
    covered = {r.entry_id for r in model.runs if r.area in area_ids}
    synthetic: list[str] = []
    entries: list[str] = []
    for entry_id in dict.fromkeys(lights):
        ctl = mgr.controller(entry_id)
        if ctl is None:
            raise TargetError("That light isn't set up")
        entries.append(entry_id)
        if entry_id in covered:
            continue
        run = Run(f"{name}:{entry_id}", entry_id, name, 0, ctl.bulbs, area=f"{name}:{entry_id}")
        runs[run.id] = run
        synthetic.append(run.id)
    for r in model.runs:
        if r.area in area_ids and r.entry_id not in entries:
            entries.append(r.entry_id)
    zone = Zone(zone_id, name, tuple(synthetic), mode, ax, pattern, effect, max(1, min(100, int(speed))),
                tuple(a.id for a in found))
    return zone, runs, entries, found
