"""Holiday date maths for 2026-2030 (pure: no Home Assistant)."""
from __future__ import annotations

import calendar
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"
_spec = importlib.util.spec_from_file_location("lss_holiday_calendar", PKG / "holiday_calendar.py")
hc = importlib.util.module_from_spec(_spec)
sys.modules["lss_holiday_calendar"] = hc
_spec.loader.exec_module(hc)

YEARS = range(2026, 2031)
D = date


def on(hid, year):
    return hc.holiday_date(hc.BY_ID[hid], year)


def test_easter_via_the_computus():
    known = {2026: D(2026, 4, 5), 2027: D(2027, 3, 28), 2028: D(2028, 4, 16), 2029: D(2029, 4, 1), 2030: D(2030, 4, 21)}
    for y, d in known.items():
        assert hc.easter(y) == d and d.weekday() == 6
    # classic checkpoints outside the range, and the extremes of the algorithm
    assert hc.easter(2000) == D(2000, 4, 23) and hc.easter(2019) == D(2019, 4, 21)
    assert hc.easter(2038) == D(2038, 4, 25) and hc.easter(2285) == D(2285, 3, 22)
    for y in range(1900, 2200):
        e = hc.easter(y)
        assert e.weekday() == 6 and D(y, 3, 22) <= e <= D(y, 4, 25)


def test_thanksgiving_is_the_fourth_thursday_of_november():
    known = {2026: 26, 2027: 25, 2028: 23, 2029: 22, 2030: 28}
    for y, day in known.items():
        assert on("thanksgiving", y) == D(y, 11, day)
    for y in range(2000, 2100):
        d = on("thanksgiving", y)
        assert d.weekday() == 3 and d.month == 11 and 22 <= d.day <= 28


def test_mothers_fathers_memorial_labor_day():
    assert [on("mothers_day", y) for y in YEARS] == [D(2026, 5, 10), D(2027, 5, 9), D(2028, 5, 14), D(2029, 5, 13), D(2030, 5, 12)]
    assert [on("fathers_day", y) for y in YEARS] == [D(2026, 6, 21), D(2027, 6, 20), D(2028, 6, 18), D(2029, 6, 17), D(2030, 6, 16)]
    assert [on("memorial_day", y) for y in YEARS] == [D(2026, 5, 25), D(2027, 5, 31), D(2028, 5, 29), D(2029, 5, 28), D(2030, 5, 27)]
    assert [on("labor_day", y) for y in YEARS] == [D(2026, 9, 7), D(2027, 9, 6), D(2028, 9, 4), D(2029, 9, 3), D(2030, 9, 2)]
    for y in range(2000, 2100):
        assert on("mothers_day", y).weekday() == 6 and 8 <= on("mothers_day", y).day <= 14
        assert on("fathers_day", y).weekday() == 6 and 15 <= on("fathers_day", y).day <= 21
        m = on("memorial_day", y)
        assert m.weekday() == 0 and m.month == 5 and m.day + 7 > 31  # the last Monday
        assert on("labor_day", y).weekday() == 0 and on("labor_day", y).day <= 7


def test_nth_weekday_handles_first_last_and_december():
    assert hc.nth_weekday(2026, 12, 3, -1) == D(2026, 12, 31)  # last Thursday of December (rolls the year over)
    assert hc.nth_weekday(2026, 2, 0, -1) == D(2026, 2, 23)
    assert hc.nth_weekday(2028, 2, 1, -1) == D(2028, 2, 29)  # leap day
    assert hc.nth_weekday(2026, 6, 0, 1) == D(2026, 6, 1)  # the 1st itself
    for y in (2026, 2027):
        for m in range(1, 13):
            cal = calendar.monthcalendar(y, m)
            for wd in range(7):
                days = [w[wd] for w in cal if w[wd]]
                assert hc.nth_weekday(y, m, wd, 1).day == days[0] and hc.nth_weekday(y, m, wd, -1).day == days[-1]
                assert hc.nth_weekday(y, m, wd, 2).day == days[1]


def test_fixed_holidays():
    for y in YEARS:
        assert on("christmas", y) == D(y, 12, 25) and on("july_4", y) == D(y, 7, 4) and on("halloween", y) == D(y, 10, 31)
        assert on("valentines", y) == D(y, 2, 14) and on("st_patricks", y) == D(y, 3, 17) and on("new_year", y) == D(y, 1, 1)


def test_hanukkah_diwali_lunar_new_year_tables():
    assert [on("hanukkah", y) for y in YEARS] == [D(2026, 12, 4), D(2027, 12, 24), D(2028, 12, 12), D(2029, 12, 1), D(2030, 12, 20)]
    assert [on("diwali", y) for y in YEARS] == [D(2026, 11, 8), D(2027, 10, 29), D(2028, 10, 17), D(2029, 11, 5), D(2030, 10, 26)]
    assert [on("lunar_new_year", y) for y in YEARS] == [D(2026, 2, 17), D(2027, 2, 6), D(2028, 1, 26), D(2029, 2, 13), D(2030, 2, 3)]
    for hid in ("hanukkah", "diwali", "lunar_new_year"):  # outside the table there is no date, and nothing breaks
        assert on(hid, 2025) is None and on(hid, 2031) is None
    assert hc.active_holiday(D(2031, 12, 5)) is not None  # Christmas still covers it
    assert hc.active_holiday(D(2031, 12, 5))[0].id == "christmas"


def test_every_holiday_has_a_date_in_each_year():
    for h in hc.HOLIDAYS:
        for y in YEARS:
            assert hc.holiday_date(h, y).year == y


def test_windows():
    assert hc.window(hc.BY_ID["christmas"], 2026) == (D(2026, 12, 1), D(2026, 12, 26))
    assert hc.window(hc.BY_ID["halloween"], 2027) == (D(2027, 10, 17), D(2027, 10, 31))
    assert hc.window(hc.BY_ID["hanukkah"], 2026) == (D(2026, 12, 4), D(2026, 12, 11))  # eight nights
    assert hc.window(hc.BY_ID["diwali"], 2031) is None
    assert hc.window(hc.BY_ID["christmas"], 2026, {"christmas": {"before": 3, "after": 0}}) == (D(2026, 12, 22), D(2026, 12, 25))


def test_active_holiday_edges_and_overlap():
    def hid(day, ov=None):
        got = hc.active_holiday(day, ov)
        return got[0].id if got else None

    assert hid(D(2026, 11, 30)) is None and hid(D(2026, 12, 1)) == "christmas"
    assert hid(D(2026, 12, 3)) == "christmas" and hid(D(2026, 12, 4)) == "hanukkah"  # the later window wins
    assert hid(D(2026, 12, 11)) == "hanukkah" and hid(D(2026, 12, 12)) == "christmas"
    assert hid(D(2026, 12, 26)) == "christmas" and hid(D(2026, 12, 27)) is None
    assert hid(D(2026, 12, 31)) == "new_year" and hid(D(2027, 1, 1)) == "new_year" and hid(D(2027, 1, 2)) is None
    assert hid(D(2027, 7, 1)) == "july_4" and hid(D(2027, 7, 5)) == "july_4" and hid(D(2027, 7, 6)) is None
    assert hid(D(2026, 4, 5)) == "easter" and hid(D(2026, 3, 29)) == "easter"
    # overrides: disabled holidays never apply, windows move
    assert hid(D(2026, 12, 20), {"christmas": {"enabled": False}}) is None
    assert hid(D(2026, 12, 20), {"christmas": {"before": 2}}) is None
    assert hid(D(2026, 12, 23), {"christmas": {"before": 2}}) == "christmas"
    got = hc.active_holiday(D(2026, 12, 25), {"christmas": {"look": {"pattern": "Rainbow", "effect": "Wave", "speed": 9,
                                                                    "brightness": 5}}})
    assert got[2]["look"]["pattern"] == "Rainbow" and got[1] == D(2026, 12, 25)
    # every day of every year resolves without error
    d = D(2026, 1, 1)
    while d < D(2031, 1, 1):
        hc.active_holiday(d)
        d += timedelta(days=1)


def test_upcoming_and_next_occurrence():
    ups = hc.upcoming(D(2026, 12, 7), 40)
    ids = [u["id"] for u in ups]
    assert ids[0] == "christmas" and "hanukkah" in ids and "new_year" in ids and "valentines" not in ids
    assert ups == sorted(ups, key=lambda u: (u["window_start"], u["date"]))
    assert all(u["window_end"] >= "2026-12-07" for u in ups)
    far = hc.upcoming(D(2026, 12, 27), 400)
    assert {u["id"] for u in far} >= {"new_year", "easter", "thanksgiving", "halloween", "christmas"}
    assert [u["date"] for u in far if u["id"] == "christmas"] == ["2027-12-25"]
    h = hc.BY_ID["easter"]
    assert hc.next_occurrence(h, D(2026, 4, 5)) == (D(2026, 4, 5), D(2026, 3, 29), D(2026, 4, 5))  # last day of window
    assert hc.next_occurrence(h, D(2026, 4, 6))[0] == D(2027, 3, 28)
    assert hc.next_occurrence(hc.BY_ID["diwali"], D(2030, 11, 1)) is None  # past the table
