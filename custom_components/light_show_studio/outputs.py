"""Choosing which speaker leads and which others can play in sync with it (pure, no Home Assistant)."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# (entity_id, integration platform or None, supports media_player grouping, available)
SpeakerInfo = tuple[str, "str | None", bool, bool]


@dataclass(frozen=True)
class Outputs:
    leader: str | None  # the lights follow this speaker's position
    members: tuple[str, ...]  # speakers to join to the leader's group
    ungrouped: tuple[str, ...]  # available speakers that can't play in sync with the leader (skipped)


def normalize_speakers(value: Any) -> list[str]:
    """A clean, de-duplicated, ordered list of entity ids from a list (or a single id)."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and (item := item.strip()) and item not in out:
            out.append(item)
    return out


def plan_outputs(speakers: Sequence[SpeakerInfo]) -> Outputs:
    """The first available speaker leads. Others join it only if both can group and share a platform."""
    seen: set[str] = set()
    rows = [s for s in speakers if not (s[0] in seen or seen.add(s[0]))]
    leader = next((s for s in rows if s[3]), None)
    if leader is None:
        return Outputs(None, (), ())
    members: list[str] = []
    ungrouped: list[str] = []
    for entity_id, platform, grouping, available in rows:
        if entity_id == leader[0] or not available:
            continue
        if leader[2] and grouping and platform is not None and platform == leader[1]:
            members.append(entity_id)
        else:
            ungrouped.append(entity_id)
    return Outputs(leader[0], tuple(members), tuple(ungrouped))
