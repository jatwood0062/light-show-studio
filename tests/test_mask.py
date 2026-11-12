"""Bulb masks: the parser, and the controller keeping masked bulbs dark (plain pytest, no Home Assistant)."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

import test_zones as tz
from test_zones import FakeLight, aio

_spec = importlib.util.spec_from_file_location(
    "lss_mask", Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio" / "mask.py")
mask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mask)
W, BLACK = (200, 200, 200), (0, 0, 0)


def test_parse_singles_and_ranges_are_one_based():
    assert mask.parse_mask("3,7-12,40") == frozenset({2, *range(6, 12), 39})
    assert mask.parse_mask(" 1 ; 3 4 ,  6 - 7 ") == frozenset({0, 2, 3, 5, 6})
    assert mask.parse_mask("5") == frozenset({4}) and mask.parse_mask(5) == frozenset({4})
    assert mask.parse_mask("9-7") == frozenset({6, 7, 8})


def test_parse_empty_and_clipping():
    assert mask.parse_mask("") == mask.parse_mask(None) == mask.parse_mask("  ") == frozenset()
    assert mask.parse_mask("1,5,20", bulbs=10) == frozenset({0, 4})


@pytest.mark.parametrize("bad", ["0", "a", "3-", "1,x", "2041", "-4", "1.5", True, [1]])
def test_parse_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        mask.parse_mask(bad)


def test_format_and_clean_round_trip():
    assert mask.format_mask({2, 6, 7, 8, 39}) == "3,7-9,40"
    assert mask.format_mask([]) == ""
    assert mask.clean_mask("40, 7-9,3,8") == "3,7-9,40"
    assert mask.clean_mask("") == ""


@aio
async def test_mask_blacks_bulbs_and_survives_brightness(hass):
    ctl = FakeLight(hass, 6)
    ctl.mask = frozenset({1, 4})
    ctl.brightness = 255
    ctl._send_frame([W] * 6)
    assert ctl.sent[-1] == [W, BLACK, W, W, BLACK, W]


@aio
async def test_set_mask_resends_and_look_mask_is_cleared_by_a_new_look(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=(255, 0, 0))
    ctl.set_mask(frozenset({0}))
    assert ctl.sent[-1][0] == BLACK and ctl.sent[-1][1] == (255, 0, 0)
    ctl.look_mask = frozenset({3})
    ctl._send_frame([W] * 4)  # a plain colour: the look mask applies to a pattern only
    assert ctl.sent[-1] == [BLACK, W, W, W]
    await ctl.async_turn_on(colour=(0, 255, 0), off_bulbs=frozenset({2}))
    assert ctl.look_mask == frozenset({2}) and ctl.mask == frozenset({0})
    await ctl.async_turn_on(colour=(0, 0, 255))
    assert ctl.look_mask == frozenset()


@aio
async def test_off_bulbs_with_a_pattern_combine_with_the_stored_mask(hass):
    ctl = FakeLight(hass, 6)
    ctl.mask = frozenset({0})
    await ctl.async_set_pattern(pattern="Classic Multicolor", effect="Steady", off_bulbs=frozenset({5}))
    assert ctl.look_mask == frozenset({5})
    assert ctl._masked([W] * 6, True) == [BLACK, W, W, W, W, BLACK]
    assert ctl._masked([W] * 6, False) == [BLACK] + [W] * 5  # not the pattern: only the stored mask
    await ctl.async_set_pattern(pattern="Classic Multicolor")  # another look: transient mask cleared
    assert ctl.look_mask == frozenset()


@aio
async def test_scan_and_identify_frames_bypass_the_mask(hass):
    ctl = FakeLight(hass, 5)
    ctl.mask = frozenset({0, 1})
    ctl._send_frame([W] * 5, scaled=False)  # what a camera scan sends
    assert ctl.sent[-1] == [W] * 5
    ctl._flash((255, 255, 255))  # what identify sends
    assert ctl.sent[-1] == [(255, 255, 255)] * 5
