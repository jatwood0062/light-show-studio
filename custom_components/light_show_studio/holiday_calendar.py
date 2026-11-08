"""A built-in holiday calendar for light schedules (pure: no Home Assistant, so it can be tested alone).

Each holiday has a date rule, a window around that date (`before` days before it and `after` days after it, both
inclusive) and a default look made only of EXISTING pattern names (patterns.PATTERNS). The person can override a
holiday's look, window or enabled flag (stored by the light scheduler); `overrides` below is that stored dict:
  {holiday_id: {"look": {...}?, "before": int?, "after": int?, "enabled": bool?}}

Date rules:
  fixed     month and day
  easter    Western Easter Sunday (the computus), plus an offset in days
  nth       the n-th weekday of a month (n = -1 is the last one): Thanksgiving, Mother's / Father's Day, Memorial / Labor Day
  table     a small table of dates for 2026-2030 (Hanukkah, Diwali, Lunar New Year); outside those years the holiday
            has no date and is skipped

Hanukkah is listed by the evening of its first candle (24 Kislev) and runs 8 nights, so its window is 0 days before
and 7 after that date.

When windows overlap (Hanukkah and Christmas, say) the window that opened last wins, like overlapping light schedules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

TABLE_YEARS = (2026, 2030)  # first and last year the table holidays have dates for
MAX_WINDOW_DAYS = 60  # the longest `before` / `after` an override may ask for
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


@dataclass(frozen=True)
class Holiday:
    id: str
    name: str
    rule: str  # "fixed" | "easter" | "nth" | "table"
    before: int  # default window: days before the date
    after: int  # ... and days after it
    look: "dict[str, Any]"  # default look: pattern / effect / speed / brightness
    month: int = 0  # fixed, nth
    day: int = 0  # fixed
    weekday: int = 0  # nth
    nth: int = 0  # nth (-1 = last)
    offset: int = 0  # easter: days from Easter Sunday
    table: "dict[int, tuple[int, int]]" = field(default_factory=dict)  # table: year -> (month, day)
    note: str = ""


def _look(pattern: str, effect: str, speed: int = 50, brightness: int = 100) -> "dict[str, Any]":
    return {"pattern": pattern, "effect": effect, "speed": speed, "brightness": brightness}


# Hanukkah: first candle (24 Kislev, the evening before the first full day). Diwali: main day. Lunar New Year: day one.
HANUKKAH_DATES = {2026: (12, 4), 2027: (12, 24), 2028: (12, 12), 2029: (12, 1), 2030: (12, 20)}
DIWALI_DATES = {2026: (11, 8), 2027: (10, 29), 2028: (10, 17), 2029: (11, 5), 2030: (10, 26)}
LUNAR_NEW_YEAR_DATES = {2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3)}

# In calendar order. The patterns are built-ins; there is no green-only or red-and-gold pattern, so St Patrick's Day
# uses Rainbow and Lunar New Year uses Valentine (red and pink).
HOLIDAYS: "tuple[Holiday, ...]" = (
    Holiday("new_year", "New Year", "fixed", 1, 0, _look("Gold & White", "Sparkle"), month=1, day=1),
    Holiday("lunar_new_year", "Lunar New Year", "table", 1, 6, _look("Valentine", "Twinkle"), table=LUNAR_NEW_YEAR_DATES,
            note="2026-2030 only"),
    Holiday("valentines", "Valentine's Day", "fixed", 3, 0, _look("Valentine", "Twinkle"), month=2, day=14),
    Holiday("st_patricks", "St Patrick's Day", "fixed", 2, 0, _look("Rainbow", "Steady"), month=3, day=17),
    Holiday("easter", "Easter", "easter", 7, 0, _look("Pastel", "Steady")),
    Holiday("mothers_day", "Mother's Day", "nth", 2, 0, _look("Pastel", "Fade"), month=5, weekday=SUN, nth=2),
    Holiday("memorial_day", "Memorial Day", "nth", 3, 0, _look("Red, White & Blue", "Steady"), month=5, weekday=MON, nth=-1),
    Holiday("fathers_day", "Father's Day", "nth", 1, 0, _look("Blue & White", "Steady"), month=6, weekday=SUN, nth=3),
    Holiday("july_4", "Independence Day", "fixed", 3, 1, _look("Red, White & Blue", "Twinkle"), month=7, day=4),
    Holiday("labor_day", "Labor Day", "nth", 2, 0, _look("Red, White & Blue", "Steady"), month=9, weekday=MON, nth=1),
    Holiday("halloween", "Halloween", "fixed", 14, 0, _look("Halloween", "Twinkle"), month=10, day=31),
    Holiday("diwali", "Diwali", "table", 2, 2, _look("Gold & White", "Twinkle"), table=DIWALI_DATES,
            note="2026-2030 only"),
    Holiday("thanksgiving", "Thanksgiving", "nth", 1, 0, _look("Warm White", "Steady"), month=11, weekday=THU, nth=4),
    Holiday("hanukkah", "Hanukkah", "table", 0, 7, _look("Blue & White", "Twinkle"), table=HANUKKAH_DATES,
            note="2026-2030 only; first candle to the eighth night"),
    Holiday("christmas", "Christmas", "fixed", 24, 1, _look("Red & Green", "Twinkle"), month=12, day=25),
)
BY_ID = {h.id: h for h in HOLIDAYS}


# --- date maths -----------------------------------------------------------------------------
def easter(year: int) -> date:
    """Western (Gregorian) Easter Sunday: the anonymous Gregorian computus (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of the month; n = -1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def holiday_date(h: Holiday, year: int) -> Optional[date]:
    """The day the holiday falls on in `year`, or None (a table holiday outside the table's years)."""
    if h.rule == "fixed":
        return date(year, h.month, h.day)
    if h.rule == "easter":
        return easter(year) + timedelta(days=h.offset)
    if h.rule == "nth":
        return nth_weekday(year, h.month, h.weekday, h.nth)
    md = h.table.get(year)
    return date(year, md[0], md[1]) if md else None


# --- overrides -------------------------------------------------------------------------------
def effective(h: Holiday, overrides: "dict[str, dict[str, Any]] | None") -> "dict[str, Any]":
    """The holiday as it applies: its look, window days and enabled flag, with the person's overrides on top."""
    o = (overrides or {}).get(h.id) or {}
    return {"look": dict(o.get("look") or h.look), "before": o.get("before", h.before),
            "after": o.get("after", h.after), "enabled": o.get("enabled", True)}


def window(h: Holiday, year: int, overrides: "dict[str, dict[str, Any]] | None" = None) -> "tuple[date, date] | None":
    """First and last day (inclusive) of the holiday's window around its `year` date, or None without a date."""
    d = holiday_date(h, year)
    if d is None:
        return None
    eff = effective(h, overrides)
    return d - timedelta(days=eff["before"]), d + timedelta(days=eff["after"])


def active_holiday(day: date, overrides: "dict[str, dict[str, Any]] | None" = None
                   ) -> "tuple[Holiday, date, dict[str, Any]] | None":
    """The enabled holiday whose window contains `day`: (holiday, its date, effective settings), else None.
    When windows overlap, the one that opened last wins (then the later in the list)."""
    best: "tuple[date, int, Holiday, date, dict[str, Any]] | None" = None
    for idx, h in enumerate(HOLIDAYS):
        eff = effective(h, overrides)
        if not eff["enabled"]:
            continue
        for year in (day.year - 1, day.year, day.year + 1):  # a New Year window reaches back into December
            d = holiday_date(h, year)
            if d is None:
                continue
            lo, hi = d - timedelta(days=eff["before"]), d + timedelta(days=eff["after"])
            if lo <= day <= hi and (best is None or (lo, idx) > (best[0], best[1])):
                best = (lo, idx, h, d, eff)
    return None if best is None else (best[2], best[3], best[4])


def upcoming(frm: date, days: int, overrides: "dict[str, dict[str, Any]] | None" = None) -> "list[dict[str, Any]]":
    """Holiday occurrences whose window ends on or after `frm` and starts within `days` days, soonest window first:
    [{id, name, date, window_start, window_end, enabled, look}]. Disabled holidays are included (enabled: false)."""
    out = []
    horizon = frm + timedelta(days=days)
    for h in HOLIDAYS:
        eff = effective(h, overrides)
        for year in (frm.year - 1, frm.year, frm.year + 1, frm.year + 2):
            d = holiday_date(h, year)
            if d is None:
                continue
            lo, hi = d - timedelta(days=eff["before"]), d + timedelta(days=eff["after"])
            if hi >= frm and lo <= horizon:
                out.append({"id": h.id, "name": h.name, "date": d.isoformat(), "window_start": lo.isoformat(),
                            "window_end": hi.isoformat(), "enabled": eff["enabled"], "look": eff["look"]})
    return sorted(out, key=lambda x: (x["window_start"], x["date"]))


def next_occurrence(h: Holiday, frm: date, overrides: "dict[str, dict[str, Any]] | None" = None
                    ) -> "tuple[date, date, date] | None":
    """(date, window_start, window_end) of the first occurrence whose window has not ended before `frm`, else None."""
    eff = effective(h, overrides)
    for year in range(frm.year - 1, frm.year + 3):
        d = holiday_date(h, year)
        if d is None:
            continue
        lo, hi = d - timedelta(days=eff["before"]), d + timedelta(days=eff["after"])
        if hi >= frm:
            return d, lo, hi
    return None
