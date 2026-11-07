"""The map manager with transitions, map effects and your own patterns: fades on area zones, effects and patterns
accepted by name, and zones following a pattern that is renamed or deleted. Plain pytest, no Home Assistant."""
from __future__ import annotations

import asyncio
import importlib

import pytest

from test_scan import make_manager, mapmanager
import test_zones as tz
from test_zones import FakeLight, aio

patterns, mapping = tz.patterns, tz.mapping
up_mod = importlib.import_module("light_show_studio_zn.user_patterns")
MAP = {"version": 2, "areas": [{"id": "x", "name": "X"}], "zones": [],
       "runs": [{"id": "r1", "entry_id": "a", "name": "A", "first": 0, "count": 10, "area": "x"}]}


def lit(frame):
    return max(max(c) for c in frame)


async def setup(hass, default_fade=0.0):
    a = FakeLight(hass, 10)
    m = make_manager(hass, [("a", a, "Roof")])
    m.default_fade = default_fade
    await m.async_set_map(MAP)
    return a, m


def user(name="Mine", pid="u1", colours=((0, 0, 255),)):
    return up_mod.UserPattern({"name": name, "kind": "palette", "colours": [list(c) for c in colours],
                               "bulbs_per_colour": 1, "default_effect": "Steady", "default_speed": 50}, pid)


@pytest.fixture(autouse=True)
def clean_registry():
    patterns.set_user_patterns([])
    yield
    patterns.set_user_patterns([])


@aio
async def test_area_zone_runs_a_map_effect(hass):
    a, m = await setup(hass)
    await m.async_start_area("x", "Rainbow", "Ripple", 60, "spatial", {"centre": [0.5, 0.5, 0.5]})
    await asyncio.sleep(0.3)
    assert len({tuple(f) for f in a.sent}) > 3
    await m.async_stop_area("x")
    for effect in patterns.MAP_EFFECTS:
        await m.async_start_area("x", "Rainbow", effect)
        assert m.running == ["area:x"]
        await m.async_stop_area("x")
    with pytest.raises(mapmanager.HomeAssistantError, match="Unknown effect"):
        await m.async_start_area("x", effect="Disco")
    m.async_stop_all()


@aio
async def test_saved_zone_with_a_map_effect_starts_and_survives_a_map_change(hass):
    a, m = await setup(hass)
    await m.async_set_map({**MAP, "zones": [{"id": "z", "name": "Z", "areas": ["x"], "mode": "spatial", "effect": "Snowfall"}]})
    await m.async_start_zone("Z")
    await asyncio.sleep(0.15)
    assert m.running == ["z"] and m.model.zones[0].effect == "Snowfall"
    await m.async_start_zone("Z", effect="Fireworks")
    assert m.model.zones[0].effect == "Fireworks"
    await m.async_set_map({**MAP, "zones": [{"id": "z", "name": "Z", "areas": ["x"], "effect": "Rise"}]})
    assert m.running == ["z"]
    m.async_stop_all()


@aio
async def test_zones_take_your_patterns_by_name_or_id(hass):
    a, m = await setup(hass)
    patterns.set_user_patterns([user()])
    await m.async_start_area("x", "mine", "Steady")
    await asyncio.sleep(0.1)
    assert a.sent[-1] == [(0, 0, 255)] * 10
    await m.async_stop_area("x")
    await m.async_start_area("x", "u1", "Steady")
    assert m.runners["area:x"].plan.pattern == "Mine"
    with pytest.raises(mapmanager.HomeAssistantError, match="Unknown pattern"):
        await m.async_start_area("x", "Nope")
    await m.async_set_map({**MAP, "zones": [{"id": "z", "name": "Z", "areas": ["x"], "pattern": "u1"}]})
    assert m.model.zones[0].pattern == "Mine"  # stored by name
    with pytest.raises(mapping.MapError, match="unknown pattern"):
        await m.async_set_map({**MAP, "zones": [{"id": "z", "name": "Z", "areas": ["x"], "pattern": "Nope"}]})
    m.async_stop_all()


@aio
async def test_area_zone_fades_in_and_out_by_default_fade(hass):
    a, m = await setup(hass, default_fade=0.4)
    await m.async_start_area("x", "Warm White", "Steady")
    await asyncio.sleep(0.1)
    assert lit(a.sent[0]) < 150
    await asyncio.sleep(0.6)
    assert a.sent[-1] == [(255, 150, 40)] * 10
    await m.async_stop_area("x")
    assert m.running == [] and a.zone == "X"  # gone from the list at once, the light still fading
    await asyncio.sleep(0.2)
    assert 0 < lit(a.sent[-1]) < 255
    await asyncio.sleep(0.6)
    assert a.zone is None
    m.async_stop_all()


@aio
async def test_transition_given_by_the_caller_overrides_the_default(hass):
    a, m = await setup(hass, default_fade=0.5)
    await m.async_start_area("x", "Warm White", "Steady", fade_in=0, fade_out=0)
    await asyncio.sleep(0.05)
    assert a.sent[0] == [(255, 150, 40)] * 10
    await m.async_stop_area("x", 0)
    assert a.zone is None
    await m.async_start_area("x", "Warm White", "Steady", fade_in=0)
    await m.async_stop_area("x", 0.3)
    await asyncio.sleep(0.1)
    assert a.zone == "X"
    await asyncio.sleep(0.5)
    assert a.zone is None
    m.async_stop_all()


@aio
async def test_changing_the_look_of_a_running_zone_does_not_fade_in_from_black_again(hass):
    a, m = await setup(hass, default_fade=0.3)
    await m.async_start_area("x", "Warm White", "Steady")
    await asyncio.sleep(0.6)
    n = len(a.sent)
    await m.async_start_area("x", "Cool White", "Steady")
    await asyncio.sleep(0.1)
    assert all(lit(f) > 150 for f in a.sent[n:])
    m.async_stop_all()


@aio
async def test_starting_a_zone_again_while_it_fades_out_cuts_the_old_run_off(hass):
    a, m = await setup(hass, default_fade=0.5)
    await m.async_start_area("x", "Warm White", "Steady", fade_in=0)
    await asyncio.sleep(0.1)
    await m.async_stop_area("x")
    assert "area:x" in m._fading
    await m.async_start_area("x", "Cool White", "Steady", fade_in=0)
    assert m._fading == {}
    await asyncio.sleep(0.2)
    assert a.sent[-1] == [(200, 220, 255)] * 10 and a.zone == "X"
    m.async_stop_all()


@aio
async def test_stop_all_also_ends_zones_that_are_fading_out(hass):
    a, m = await setup(hass, default_fade=1.0)
    await m.async_start_area("x", "Warm White", "Steady", fade_in=0)
    await asyncio.sleep(0.05)
    await m.async_stop_area("x")
    m.async_stop_all()
    assert a.zone is None and m._fading == {}


@aio
async def test_editing_a_pattern_restarts_running_zones_with_the_new_colours(hass):
    a, m = await setup(hass)
    patterns.set_user_patterns([user()])
    await m.async_start_area("x", "Mine", "Steady")
    await asyncio.sleep(0.1)
    assert a.sent[-1] == [(0, 0, 255)] * 10
    patterns.set_user_patterns([user(colours=((0, 255, 0),))])
    await m.async_pattern_changed("Mine", "Mine")
    await asyncio.sleep(0.1)
    assert a.sent[-1] == [(0, 255, 0)] * 10 and m.running == ["area:x"]
    m.async_stop_all()


@aio
async def test_renaming_or_deleting_a_pattern_updates_saved_zones_and_running_ones(hass):
    a, m = await setup(hass)
    patterns.set_user_patterns([user()])
    await m.async_set_map({**MAP, "zones": [{"id": "z", "name": "Z", "areas": ["x"], "pattern": "Mine", "effect": "Steady"}]})
    await m.async_start_zone("Z")
    patterns.set_user_patterns([user("Renamed")])
    await m.async_pattern_changed("Mine", "Renamed")
    assert m.model.zones[0].pattern == "Renamed" and m.runners["z"].plan.pattern == "Renamed"
    await asyncio.sleep(0.1)
    assert a.sent[-1] == [(0, 0, 255)] * 10
    patterns.set_user_patterns([])
    await m.async_pattern_changed("Renamed", None)
    assert m.model.zones[0].pattern == patterns.DEFAULT_PATTERN and m.runners["z"].plan.pattern == patterns.DEFAULT_PATTERN
    m.async_stop_all()


def test_ws_map_wiring_by_source():
    """ws_map needs Home Assistant, so read its source: the new fields are bounded and map/get lists the effects."""
    src = (tz.PKG / "ws_map.py").read_text()
    assert '"spatial_effects": list(patterns.MAP_EFFECTS)' in src
    assert "PATTERN = vol.All(str, vol.Length(min=1, max=64), patterns.valid_pattern)" in src
    assert "EFFECT = vol.All(str, patterns.valid_effect)" in src
    assert 'vol.Range(min=0, max=fade.MAX_TRANSITION)' in src
    assert src.count('vol.Optional("transition"): TRANSITION') == 2  # zone/start and zone/stop
    assert 'vol.In(patterns.PATTERN_LIST)' not in src and 'vol.In(patterns.PATTERN_EFFECTS)' not in src
