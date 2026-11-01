"""Tests for the schedule window maths; plain pytest, no Home Assistant needed."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PATH = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio" / "schedule_math.py"
spec = importlib.util.spec_from_file_location("schedule_math_under_test", PATH)
sm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sm
spec.loader.exec_module(sm)

UTC = timezone.utc
TZ = ZoneInfo("America/Chicago")
ALL = [0, 1, 2, 3, 4, 5, 6]


def local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def sun(day, event):
    """Fixed sunrise 07:00 / sunset 17:00 local on every day (as UTC)."""
    h = 7 if event == "sunrise" else 17
    return local(day.year, day.month, day.day, h).astimezone(UTC)


def sched(**kw):
    base = {"days": ALL, "start": {"type": "time", "time": "18:00"}, "end": {"type": "time", "time": "22:00"}}
    base.update(kw)
    return base


def wall(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


# --- season ------------------------------------------------------------------
def test_season_plain_and_wrapping():
    assert sm.in_season(date(2026, 12, 25), "12-01", "12-31")
    assert not sm.in_season(date(2026, 11, 30), "12-01", "12-31")
    assert sm.in_season(date(2026, 12, 1), "12-01", "12-31") and sm.in_season(date(2026, 12, 31), "12-01", "12-31")
    # Nov 25 -> Jan 6 wraps the new year
    for d in (date(2026, 11, 25), date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 6)):
        assert sm.in_season(d, "11-25", "01-06"), d
    for d in (date(2026, 11, 24), date(2027, 1, 7), date(2026, 7, 4)):
        assert not sm.in_season(d, "11-25", "01-06"), d
    assert sm.in_season(date(2026, 7, 4), None, None)
    assert sm.in_season(date(2026, 7, 4), "06-01", None) and not sm.in_season(date(2026, 5, 1), "06-01", None)
    assert sm.in_season(date(2026, 5, 1), None, "06-01")


def test_bad_mmdd():
    with pytest.raises(ValueError):
        sm.parse_mmdd("02-30")
    assert sm.parse_mmdd("02-29") == (2, 29)


# --- weekdays ----------------------------------------------------------------
def test_weekday_filter():
    s = sched(days=[4])  # Fridays; 2026-10-16 is a Friday
    now = local(2026, 10, 14, 12)  # Wednesday
    w = sm.next_window(s, now, TZ, sun)
    assert wall(w.start) == "2026-10-16 18:00" and wall(w.end) == "2026-10-16 22:00"
    # on that Friday at 19:00 we are inside; after 22:00 the next one is a week on
    assert sm.current_window(s, local(2026, 10, 16, 19), TZ, sun).day == date(2026, 10, 16)
    assert sm.current_window(s, local(2026, 10, 16, 22, 1), TZ, sun) is None
    assert wall(sm.next_window(s, local(2026, 10, 16, 22, 1), TZ, sun).start) == "2026-10-23 18:00"


def test_no_days_never_runs():
    assert sm.next_window(sched(days=[]), local(2026, 10, 1), TZ, sun) is None


# --- crossing midnight -------------------------------------------------------
def test_window_crossing_midnight():
    s = sched(days=[4], end={"type": "time", "time": "01:00"})  # Friday 18:00 -> Saturday 01:00
    w = sm.next_window(s, local(2026, 10, 15, 9), TZ, sun)
    assert wall(w.start) == "2026-10-16 18:00" and wall(w.end) == "2026-10-17 01:00"
    # still open on Saturday 00:30 even though Saturday isn't a start day
    cur = sm.current_window(s, local(2026, 10, 17, 0, 30), TZ, sun)
    assert cur is not None and cur.day == date(2026, 10, 16)
    assert sm.current_window(s, local(2026, 10, 17, 1, 0), TZ, sun) is None


def test_sun_end_before_start_rolls_to_next_day():
    s = sched(start={"type": "time", "time": "23:00"}, end={"type": "sunrise", "offset_min": 0})
    w = sm.window_for_day(s, date(2026, 10, 16), TZ, sun)
    assert wall(w.start) == "2026-10-16 23:00" and wall(w.end) == "2026-10-17 07:00"


# --- season wrap Nov -> Jan --------------------------------------------------
def test_season_wrap_next_window():
    s = sched(date_from="11-25", date_to="01-06")
    w = sm.next_window(s, local(2026, 10, 16, 12), TZ, sun)
    assert wall(w.start) == "2026-11-25 18:00"
    w = sm.next_window(s, local(2026, 12, 31, 23), TZ, sun)
    assert wall(w.start) == "2027-01-01 18:00"
    w = sm.next_window(s, local(2027, 1, 6, 23), TZ, sun)  # the 6th's window is over: back to November
    assert wall(w.start) == "2027-11-25 18:00"


def test_season_checked_on_start_day():
    s = sched(date_from="12-01", date_to="12-31", end={"type": "time", "time": "01:00"})
    w = sm.window_for_day(s, date(2026, 12, 31), TZ, sun)  # starts in season, ends Jan 1
    assert w is not None and wall(w.end) == "2027-01-01 01:00"
    assert sm.window_for_day(s, date(2026, 11, 30), TZ, sun) is None


# --- sunset with a negative offset -------------------------------------------
def test_sunset_negative_offset():
    s = sched(start={"type": "sunset", "offset_min": -15}, end={"type": "time", "time": "22:00"})
    w = sm.window_for_day(s, date(2026, 10, 16), TZ, sun)
    assert wall(w.start) == "2026-10-16 16:45"
    s = sched(start={"type": "sunset", "offset_min": 30}, end={"type": "sunrise", "offset_min": -10})
    w = sm.window_for_day(s, date(2026, 10, 16), TZ, sun)
    assert wall(w.start) == "2026-10-16 17:30" and wall(w.end) == "2026-10-17 06:50"


def test_no_sun_that_day():
    s = sched(start={"type": "sunset", "offset_min": 0})
    assert sm.window_for_day(s, date(2026, 6, 21), TZ, lambda d, e: None) is None


# --- DST: local wall-clock times stay put ------------------------------------
def test_local_time_survives_dst():
    s = sched()
    before = sm.window_for_day(s, date(2026, 3, 7), TZ, sun)  # CST
    after = sm.window_for_day(s, date(2026, 3, 9), TZ, sun)  # CDT
    assert wall(before.start) == "2026-03-07 18:00" and wall(after.start) == "2026-03-09 18:00"
    assert before.start.hour == 0 and after.start.hour == 23  # UTC moved an hour
    assert after.end - after.start == timedelta(hours=4)


def test_window_over_the_spring_forward_gap():
    s = sched(start={"type": "time", "time": "00:30"}, end={"type": "time", "time": "04:30"})
    w = sm.window_for_day(s, date(2026, 3, 8), TZ, sun)  # 02:00-03:00 doesn't exist that night
    assert w.end - w.start == timedelta(hours=3)
    fall = sm.window_for_day(s, date(2026, 11, 1), TZ, sun)  # 01:00-02:00 happens twice
    assert fall.end - fall.start == timedelta(hours=5)


# --- end = None --------------------------------------------------------------
def test_no_end_is_a_one_off_start():
    s = sched(end=None, days=[0])  # Mondays 18:00, play once
    w = sm.next_window(s, local(2026, 10, 16, 12), TZ, sun)
    assert w.end is None and wall(w.start) == "2026-10-19 18:00"
    assert sm.current_window(s, local(2026, 10, 19, 18, 5), TZ, sun) is None
    got = sm.windows_between(s, local(2026, 10, 19), local(2026, 10, 20), TZ, sun)
    assert len(got) == 1


# --- skipping and conflicts --------------------------------------------------
def test_skip_day():
    s = sched()
    now = local(2026, 10, 16, 12)
    assert wall(sm.next_window(s, now, TZ, sun).start) == "2026-10-16 18:00"
    assert wall(sm.next_window(s, now, TZ, sun, skip_day=date(2026, 10, 16)).start) == "2026-10-17 18:00"
    assert sm.current_window(s, local(2026, 10, 16, 19), TZ, sun, skip_day=date(2026, 10, 16)) is None


def test_conflicts():
    a = sched()
    b = sched(start={"type": "time", "time": "21:00"}, end={"type": "time", "time": "23:00"})
    c = sched(start={"type": "time", "time": "22:00"}, end={"type": "time", "time": "23:00"})
    now = local(2026, 10, 16, 12)
    assert sm.conflicts(a, b, now, TZ, sun)
    assert not sm.conflicts(a, c, now, TZ, sun)  # back to back is fine
    assert not sm.conflicts(a, sched(days=[]), now, TZ, sun)
