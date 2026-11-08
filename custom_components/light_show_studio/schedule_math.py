"""When a schedule's windows start and end (pure: no Home Assistant, so it can be tested alone).

A schedule is a dict with:
  days       list of weekdays 0-6 (Mon..Sun) the window may START on
  start      {"type": "time"|"sunset"|"sunrise", "time": "HH:MM", "offset_min": int}
  end        the same shape, or None (= play once, at the start)
  date_from  optional "MM-DD"; date_to optional "MM-DD" (the season; may wrap the year, e.g. 11-25 .. 01-06)
  random_offset_min  optional 0-60: start and end each move by a deterministic per-day amount of -N..+N minutes
                     (seeded by the schedule's "id" and the day, so every call agrees and a restart changes nothing)

Times of day are local wall-clock times, so they stay put across daylight saving changes. Sunrise and
sunset come from the `sun(day, event)` callable the caller passes in (a UTC datetime or None).
Everything returned is timezone-aware UTC.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Optional

UTC = timezone.utc
SunFn = Callable[[date, str], Optional[datetime]]  # (day, "sunrise"|"sunset") -> aware datetime or None
EDGE_TYPES = ("time", "sunset", "sunrise")
MAX_SEARCH_DAYS = 400  # a season-limited schedule is never more than a year away


@dataclass(frozen=True)
class Window:
    start: datetime  # UTC
    end: Optional[datetime]  # UTC; None = a one-off start with no end
    day: date  # the local day the window starts on


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad time {value!r}")
    return time(h, m)


def parse_mmdd(value: str) -> "tuple[int, int]":
    mm, dd = value.split("-")
    m, d = int(mm), int(dd)
    date(2024, m, d)  # raises ValueError for 02-30 and the like (2024 is a leap year, so 02-29 is fine)
    return m, d


def in_season(day: date, date_from: Optional[str], date_to: Optional[str]) -> bool:
    """Is `day` inside the season? Month and day only; the range may wrap the new year (11-25 -> 01-06).
    No dates = always. Only one end = open on the other side."""
    if not date_from and not date_to:
        return True
    md = (day.month, day.day)
    lo = parse_mmdd(date_from) if date_from else None
    hi = parse_mmdd(date_to) if date_to else None
    if lo and hi:
        return lo <= md <= hi if lo <= hi else (md >= lo or md <= hi)
    if lo:
        return md >= lo
    return md <= hi  # type: ignore[operator]


def resolve_edge(edge: "dict[str, Any]", day: date, tz: tzinfo, sun: SunFn) -> Optional[datetime]:
    """The moment `edge` falls on local day `day` (UTC), or None when there is no sunrise/sunset that day."""
    kind = edge["type"]
    if kind == "time":
        t = parse_hhmm(edge["time"])
        return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=tz).astimezone(UTC)
    base = sun(day, kind)
    if base is None:
        return None
    return (base + timedelta(minutes=int(edge.get("offset_min") or 0))).astimezone(UTC)


def random_offset(salt: str, day: date, kind: str, max_min: int) -> int:
    """A deterministic pseudo-random shift in whole minutes, -max_min..+max_min, for `kind` ("start"/"end") on `day`.
    The same inputs always give the same answer (a hash, not the random module)."""
    if max_min <= 0:
        return 0
    digest = hashlib.sha256(f"{salt}|{day.isoformat()}|{kind}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2 * max_min + 1) - max_min


def rotation_index(count: int, rotate: "dict[str, Any] | None", salt: str, day: date,
                   start: datetime, now: datetime) -> int:
    """Which of `count` looks is showing at `now` in the window that started at `start` on local day `day`.
    rotate {"type": "night"}: one look per night, stepping one place a day (so neighbouring nights differ), offset by a
    per-schedule amount; it depends on the date only. rotate {"type": "interval", "minutes": N}: step to the next look
    every N minutes from the window's start, beginning with the schedule's own per-night offset."""
    if count <= 1 or not rotate:
        return 0
    base = (day.toordinal() + int.from_bytes(hashlib.sha256(salt.encode()).digest()[:4], "big")) % count
    if rotate.get("type") == "interval":
        step = max(0, int((now - start).total_seconds() // (int(rotate["minutes"]) * 60)))
        return (base + step) % count
    return base


def next_rotation(rotate: "dict[str, Any] | None", start: datetime, now: datetime) -> Optional[datetime]:
    """When an interval rotation next steps after `now` (None for no / per-night rotation)."""
    if not rotate or rotate.get("type") != "interval":
        return None
    span = timedelta(minutes=int(rotate["minutes"]))
    return start + span * (int((now - start) // span) + 1)


def window_for_day(s: "dict[str, Any]", day: date, tz: tzinfo, sun: SunFn) -> Optional[Window]:
    """The window that starts on local day `day`, or None (wrong weekday, out of season, no sun that day)."""
    if day.weekday() not in s.get("days", ()):
        return None
    if not in_season(day, s.get("date_from"), s.get("date_to")):
        return None
    start = resolve_edge(s["start"], day, tz, sun)
    if start is None:
        return None
    shake = int(s.get("random_offset_min") or 0)
    salt = str(s.get("id", ""))
    if not s.get("end"):
        return Window(_shift(start, salt, day, "start", shake), None, day)
    end = resolve_edge(s["end"], day, tz, sun)
    if end is None or end <= start:  # the end is after midnight (or the sun hasn't set yet): the next day's
        end = resolve_edge(s["end"], day + timedelta(days=1), tz, sun)
        if end is None or end <= start:
            return None
    if shake:
        start, end = _shift(start, salt, day, "start", shake), _shift(end, salt, day, "end", shake)
        if end <= start:  # a very short window must not turn inside out
            end = start + timedelta(minutes=1)
    return Window(start, end, day)


def _shift(moment: datetime, salt: str, day: date, kind: str, max_min: int) -> datetime:
    return moment + timedelta(minutes=random_offset(salt, day, kind, max_min))


def _days(first: date, count: int) -> Iterator[date]:
    for i in range(count):
        yield first + timedelta(days=i)


def windows_between(s: "dict[str, Any]", frm: datetime, to: datetime, tz: tzinfo, sun: SunFn) -> "list[Window]":
    """Windows that overlap [frm, to) (a one-off counts when its start is inside), in start order."""
    first = frm.astimezone(tz).date() - timedelta(days=2)  # a window that began up to two days ago may still be open
    count = (to.astimezone(tz).date() - first).days + 1
    out = []
    for day in _days(first, count):
        w = window_for_day(s, day, tz, sun)
        if w is None:
            continue
        if w.end is None:
            if frm <= w.start < to:
                out.append(w)
        elif w.end > frm and w.start < to:
            out.append(w)
    return sorted(out, key=lambda w: w.start)


def current_window(s: "dict[str, Any]", now: datetime, tz: tzinfo, sun: SunFn,
                   skip_day: Optional[date] = None) -> Optional[Window]:
    """The window with an end that is open at `now` (the latest-starting one if several), else None."""
    found = [w for w in windows_between(s, now, now + timedelta(seconds=1), tz, sun)
             if w.end is not None and w.start <= now < w.end and w.day != skip_day]
    return found[-1] if found else None


def next_window(s: "dict[str, Any]", after: datetime, tz: tzinfo, sun: SunFn,
                skip_day: Optional[date] = None) -> Optional[Window]:
    """The first window that starts after `after`, ignoring ones that start on local day `skip_day`."""
    first = after.astimezone(tz).date() - timedelta(days=1)
    for day in _days(first, MAX_SEARCH_DAYS):
        w = window_for_day(s, day, tz, sun)
        if w is not None and w.start > after and w.day != skip_day:
            return w
    return None


def overlap(a: Window, b: Window) -> bool:
    """Do two windows with ends run at the same time? (A one-off has no length and never overlaps.)"""
    if a.end is None or b.end is None:
        return False
    return a.start < b.end and b.start < a.end


def conflicts(a: "dict[str, Any]", b: "dict[str, Any]", now: datetime, tz: tzinfo, sun: SunFn,
              days: int = 60) -> bool:
    """Do two schedules' windows overlap within the next `days` days?"""
    to = now + timedelta(days=days)
    wa = windows_between(a, now, to, tz, sun)
    wb = windows_between(b, now, to, tz, sun)
    return any(overlap(x, y) for x in wa for y in wb)
