"""Your own patterns: validation, rendering with every effect, painted pixels on lights and in areas, the store,
the websocket commands and the live preview. Plain pytest, no Home Assistant (see wsharness)."""
from __future__ import annotations

import asyncio
import random

import pytest

import wsharness as H
from wsharness import FakeConn, FakeHass, FakeLight, FakeMgr, aio, call, palette, pixels

up_mod, patterns, mapping, mapped, controller = H.user_patterns, H.patterns, H.mapping, H.mapped_patterns, H.controller
pattern_store, patternpreview = H.mod("pattern_store"), H.mod("patternpreview")
ws_patterns = H.mod("ws_patterns")
const = H.mod("const")

R, G, B, W, K = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)
RULES = dict(builtin_names=patterns.PATTERN_LIST, effects=patterns.PATTERN_EFFECTS)


def clean(data, **kw):
    return up_mod.validate(data, **{**RULES, **kw})


def make(data, pid="p1"):
    return up_mod.UserPattern(clean(data), pid)


@pytest.fixture(autouse=True)
def fresh_registry():
    patterns.set_user_patterns([])
    yield
    patterns.set_user_patterns([])


def new_store(hass):
    """A PatternStore on the harness's own fake storage (other test files stub the Store class differently)."""
    store = pattern_store.PatternStore(hass)
    store._store = H.FakeStore(hass, 1, const.USER_PATTERNS_STORE_KEY)
    return store


def register(*items):
    patterns.set_user_patterns(list(items))


# --- validation ----------------------------------------------------------------------------------------
def test_palette_is_cleaned_and_defaults_filled():
    out = clean(palette("  Warm   Glow ", [(255, 100, 0), [0, 0, 255]]))
    assert out == {"name": "Warm Glow", "kind": "palette", "colours": [[255, 100, 0], [0, 0, 255]], "bulbs_per_colour": 1,
                   "default_effect": "Twinkle", "default_speed": 50}
    out = clean(palette(bpc=20, default_effect="Chase", default_speed=1))
    assert out["bulbs_per_colour"] == 20 and out["default_effect"] == "Chase" and out["default_speed"] == 1


@pytest.mark.parametrize("bad", [
    palette(colours=[]), palette(colours=[(1, 2, 3)] * 17), palette(colours=[(1, 2)]), palette(colours=[(1, 2, 256)]),
    palette(colours=[(1, 2, -1)]), palette(colours=[(1.5, 2, 3)]), palette(colours=[(True, 2, 3)]),
    palette(colours=["#ff0000"]), palette(colours="red"), palette(bpc=0), palette(bpc=21), palette(bpc=1.5),
    palette(default_effect="Sweep"), palette(default_effect="Nope"), palette(default_speed=0), palette(default_speed=101),
    palette(default_speed=50.5), palette(default_speed=True),
    {"name": "x", "kind": "weird"}, {"name": "x"}, {"kind": "palette", "colours": [(1, 2, 3)]},
    palette(extra=1), "nope", None, [], palette(lights={}), pixels(colours=[[1, 2, 3]]),
])
def test_bad_palettes_are_refused(bad):
    with pytest.raises(ValueError):
        clean(bad)


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, None, 5, "bell\x07", "nul\x00", "~sneaky", "Rainbow", "rainbow", "CANDY CANE",
                                  "classic multicolor"])
def test_bad_names(name):
    with pytest.raises(ValueError):
        clean(palette(name))


def test_name_may_be_40_characters_and_must_be_unique_ignoring_case():
    assert clean(palette("x" * 40))["name"] == "x" * 40
    with pytest.raises(ValueError, match="already"):
        clean(palette("MINE"), taken=["mine", "Other"])
    assert clean(palette("Fine"), taken=["mine"])["name"] == "Fine"


def test_pixels_validation():
    ok = clean(pixels(lights={"e1": {"colours": [[1, 2, 3]] * 5}},
                      areas={"a1": [{"pos": [0, 500, 1000], "colour": [9, 9, 9]}]}))
    assert ok["lights"]["e1"] == {"first": 0, "colours": [[1, 2, 3]] * 5}
    assert ok["areas"]["a1"] == [{"pos": [0, 500, 1000], "colour": [9, 9, 9]}]
    assert clean(pixels(lights={"e1": {"first": 10, "colours": [[1, 2, 3]]}}))["lights"]["e1"]["first"] == 10
    bad = [
        pixels(),  # nothing painted
        pixels(lights={"e1": {"colours": []}}),
        pixels(lights={"e1": {"colours": [[1, 2, 3]] * 2041}}),
        pixels(lights={"e1": {"first": 2040, "colours": [[1, 2, 3]] * 2}}),
        pixels(lights={"e1": {"first": -1, "colours": [[1, 2, 3]]}}),
        pixels(lights={"e1": {"colours": [[1, 2, 300]]}}),
        pixels(lights={"e1": {"colours": [[1, 2, 3]], "extra": 1}}),
        pixels(lights={"": {"colours": [[1, 2, 3]]}}),
        pixels(lights={"e" * 65: {"colours": [[1, 2, 3]]}}),
        pixels(lights=[1]), pixels(lights={"e1": 5}),
        pixels(areas={"a": [{"pos": [0, 0, 1001], "colour": [1, 2, 3]}]}),
        pixels(areas={"a": [{"pos": [0, 0, -1], "colour": [1, 2, 3]}]}),
        pixels(areas={"a": [{"pos": [0, 0], "colour": [1, 2, 3]}]}),
        pixels(areas={"a": [{"pos": [0.5, 0, 0], "colour": [1, 2, 3]}]}),
        pixels(areas={"a": [{"pos": [0, 0, 0], "colour": [1, 2]}]}),
        pixels(areas={"a": [{"pos": [0, 0, 0]}]}),
        pixels(areas={"a": []}),
        pixels(areas={"a": [{"pos": [0, 0, 0], "colour": [1, 2, 3]}] * 2041}),
        pixels(lights={f"e{i}": {"colours": [[1, 2, 3]]} for i in range(65)}),
        pixels(lights={f"e{i}": {"colours": [[1, 2, 3]] * 2040} for i in range(7)}),  # too many painted pixels in all
    ]
    for b in bad:
        with pytest.raises(ValueError):
            clean(b)


def test_preview_data_needs_no_name_and_may_reuse_one():
    out = up_mod.validate({"kind": "palette", "colours": [[1, 2, 3]]}, builtin_names=(), effects=patterns.PATTERN_EFFECTS,
                          require_name=False)
    assert out["name"] == "Preview"
    out = up_mod.validate(palette("Rainbow"), builtin_names=(), effects=patterns.PATTERN_EFFECTS, require_name=False)
    assert out["name"] == "Rainbow"


# --- the registry and rendering --------------------------------------------------------------------------------
def test_user_patterns_are_found_by_name_or_id_and_listed_after_the_built_ins():
    a, b = make(palette("Zed"), "id_a"), make(palette("Alpha"), "id_b")
    register(a, b)
    assert patterns.canonical("zed") == "Zed" and patterns.canonical("id_b") == "Alpha" and patterns.canonical("Rainbow") == "Rainbow"
    assert patterns.canonical("nope") is None and patterns.canonical(5) is None and not patterns.is_pattern(None)
    assert patterns.pattern_names() == patterns.PATTERN_LIST + ["Zed", "Alpha"]  # in the order they were made
    assert patterns.valid_pattern("id_a") == "Zed"
    with pytest.raises(ValueError):
        patterns.valid_pattern("nope")


def test_previews_are_not_listed():
    p = up_mod.UserPattern({**clean(palette("x")), "name": "~preview1"}, "~preview1")
    patterns.register_user_pattern(p)
    assert patterns.pattern_names() == patterns.PATTERN_LIST and patterns.canonical("~preview1") == "~preview1"


@pytest.mark.parametrize("effect", patterns.PATTERN_EFFECTS)
def test_a_palette_pattern_renders_exactly_like_a_built_in_with_the_same_colours(effect):
    cols, width = patterns.PATTERNS["Red, White & Blue"]
    register(make(palette("Copy", cols, width), "c"))
    for n in (1, 7, 50):
        for t in (0.0, 0.7, 3.3):
            assert patterns.render("Copy", effect, t, n) == patterns.render("Red, White & Blue", effect, t, n)
            assert patterns.render("c", effect, t, n) == patterns.render("Red, White & Blue", effect, t, n)


def test_unknown_names_fall_back_to_the_default_pattern():
    assert patterns.render("Ghost", "Steady", 0, 6) == patterns.render(patterns.DEFAULT_PATTERN, "Steady", 0, 6)
    register(make(palette("Gone"), "g"))
    frame = patterns.render("Gone", "Steady", 0, 4)
    patterns.set_user_patterns([])
    assert patterns.render("Gone", "Steady", 0, 4) != frame


def painted(n=10, first=0, name="Paint"):
    cols = [[(i * 25) % 256, 255 - (i * 25) % 256, 40] for i in range(n)]
    return make(pixels(name, lights={"e1": {"first": first, "colours": cols}}), "px")


def test_painted_pixels_show_where_they_were_painted():
    p = painted(10)
    register(p)
    f = patterns.render("Paint", "Steady", 0, 10, "e1")
    assert f == [tuple(c) for c in p.to_dict()["lights"]["e1"]["colours"]]


def test_a_light_the_pattern_does_not_cover_gets_its_palette_repeated():
    p = painted(10)
    register(p)
    pal = p.palette
    f = patterns.render("Paint", "Steady", 0, 25, "other")
    assert f == [pal[i % len(pal)] for i in range(25)]
    assert patterns.render("Paint", "Steady", 0, 25, None) == f  # a light with no entry id too
    assert len(pal) == 10  # the colours it uses, in order of appearance


def test_pixels_beyond_the_painted_range_use_the_palette():
    p = painted(4, first=2)
    register(p)
    f = patterns.render("Paint", "Steady", 0, 10, "e1")
    cols = [tuple(c) for c in p.to_dict()["lights"]["e1"]["colours"]]
    assert f[2:6] == cols
    assert f[0] == p.palette[0] and f[1] == p.palette[1] and f[6] == p.palette[6 % 4]


def test_the_fallback_palette_has_at_most_16_distinct_colours():
    cols = [[i, 0, 0] for i in range(40)] + [[0, 0, 0]] * 3
    p = make(pixels("Many", lights={"e1": {"colours": cols}}), "m")
    assert len(p.palette) == up_mod.FALLBACK_COLOURS


@pytest.mark.parametrize("effect", patterns.PATTERN_EFFECTS)
def test_every_effect_works_on_painted_pixels(effect):
    register(painted(30))
    for n in (1, 30, 80):
        for t in (0.0, 0.4, 2.2, 55.0):
            f = patterns.render("Paint", effect, t, n, "e1")
            assert len(f) == n and all(len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c) for c in f)
            assert f == patterns.render("Paint", effect, t, n, "e1")


def test_effects_apply_on_top_of_painted_pixels():
    p = painted(30)
    register(p)
    base = patterns.render("Paint", "Steady", 0, 30, "e1")
    # Twinkle: every pixel is a dimmed copy of the painted colour
    tw = patterns.render("Paint", "Twinkle", 1.3, 30, "e1")
    assert tw != base and all(all(a <= b for a, b in zip(x, y)) for x, y in zip(tw, base))
    # Fade: the whole string breathes, same hue
    fd = patterns.render("Paint", "Fade", 0.5, 30, "e1")
    assert all(all(a <= b for a, b in zip(x, y)) for x, y in zip(fd, base))
    # Marquee / Alternate: some pixels are the painted colour, the rest dim
    assert sum(1 for x, y in zip(patterns.render("Paint", "Marquee", 0, 30, "e1"), base) if x == y) == 10
    assert sum(1 for x, y in zip(patterns.render("Paint", "Alternate", 0, 30, "e1"), base) if x == y) == 15
    # Sparkle: painted colours with some white glints
    sp = patterns.render("Paint", "Sparkle", 0.0, 30, "e1")
    assert sum(1 for x, y in zip(sp, base) if x != y) <= 4
    # Wave: brightness varies, never brighter than painted
    wv = patterns.render("Paint", "Wave", 0.9, 30, "e1")
    assert wv != base and all(all(a <= b for a, b in zip(x, y)) for x, y in zip(wv, base))
    # Twinkle Swap draws from the palette of used colours
    sw = patterns.render("Paint", "Twinkle Swap", 1.0, 30, "e1")
    assert sw != base


def test_chase_slides_the_painted_pixels_along():
    register(painted(30))
    base = patterns.render("Paint", "Steady", 0, 30, "e1")
    a = patterns.render("Paint", "Chase", 1.0, 30, "e1")  # 3 bulbs in one second
    assert a[3:] == base[:-3] or a[4:] == base[1:-3]
    assert a != base and patterns.render("Paint", "Chase", 0.0, 30, "e1") == base
    assert patterns.render("Paint", "Chase", 10.0, 30, "e1") == base  # 30 bulbs round: back where it began


def test_painting_changes_what_is_rendered_and_clears_the_cache():
    p = painted(10)
    register(p)
    assert patterns.render("Paint", "Steady", 0, 10, "e1")[3] != (1, 2, 3)
    assert p.paint("e1", [(3, (1, 2, 3)), (12, (9, 9, 9))]) == 2
    assert patterns.render("Paint", "Steady", 0, 14, "e1")[3] == (1, 2, 3)
    assert patterns.render("Paint", "Steady", 0, 14, "e1")[12] == (9, 9, 9)
    assert patterns.render("Paint", "Steady", 0, 14, "e1")[10] == K  # the gap is black
    q = painted(3, first=5)
    q.paint("e1", [(1, R)])
    assert q.lights["e1"][0] == 1 and q.lights["e1"][1][0] == R and len(q.lights["e1"][1]) == 7  # grew backwards


def test_user_pattern_to_dict_round_trips():
    for data in (palette("A"), pixels("B", lights={"e1": {"first": 3, "colours": [[1, 2, 3]]}},
                                        areas={"a": [{"pos": [1, 2, 3], "colour": [4, 5, 6]}]})):
        c = clean(data)
        d = up_mod.UserPattern(c, "z").to_dict()
        assert {k: v for k, v in d.items() if k != "id"} == c
        assert d["id"] == "z"


# --- nearest painted point ---------------------------------------------------------------------------------
def test_nearest_colours_matches_brute_force():
    rng = random.Random(7)
    pts = [(rng.randint(0, 1000), rng.randint(0, 1000), rng.randint(0, 1000)) for _ in range(300)]
    cols = [(i % 256, (i * 7) % 256, (i * 13) % 256) for i in range(300)]
    qs = [(rng.randint(0, 1000), rng.randint(0, 1000), rng.randint(0, 1000)) for _ in range(120)]
    got = up_mod.nearest_colours(pts, cols, qs)
    for q, g in zip(qs, got):
        d = [(p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2 for p in pts]
        assert (g == cols[d.index(min(d))]) or min(d) == sorted(d)[0]
        assert sum(1 for x in d if x < min(d)) == 0
    assert up_mod.nearest_colours([(0, 0, 0), (1000, 0, 0)], [R, B], [(100, 0, 0), (900, 0, 0)]) == [R, B]
    far = [(0, 0, 0)] * 20 + [(1000, 1000, 1000)]
    assert up_mod.nearest_colours(far, [R] * 20 + [B], [(990, 990, 990), (10, 10, 10)]) == [B, R]  # sparse grid: far search
    with pytest.raises(ValueError):
        up_mod.nearest_colours([], [], [(0, 0, 0)])


# --- zones and areas -------------------------------------------------------------------------------------------
def run_line(rid, entry, area, n=10, first=0, reverse=False, x0=0.0, x1=1.0, y=0.5):
    pts = [(x0 + (x1 - x0) * i / max(1, n - 1), y, 0.5) for i in range(n)]
    flat = tuple(int(round(v * 1000)) for p in pts for v in p)
    return mapping.Run(rid, entry, rid, first, n, reverse=reverse, area=area, positions=flat)


def zone_plan(pattern, effect="Steady", runs=None, mode="string", axis=None):
    runs = runs or {"r1": run_line("r1", "e1", "a")}
    zone = mapping.Zone("z", "Z", tuple(runs), mode, mapping.parse_axis(axis or {}), pattern, effect, 50)
    return mapped.ZonePlan(zone, runs)


def test_zone_uses_each_lights_own_painted_pixels():
    p = painted(10)
    register(p)
    cols = [tuple(c) for c in p.to_dict()["lights"]["e1"]["colours"]]
    assert zone_plan("Paint").render(0) == cols
    rev = zone_plan("Paint", runs={"r1": run_line("r1", "e1", "a", reverse=True)})
    assert rev.render(0) == list(reversed(cols))  # the virtual string walks the run backwards, the paint stays on its pixels


def test_a_run_starting_part_way_along_the_light_reads_the_right_pixels():
    p = painted(10)
    register(p)
    cols = [tuple(c) for c in p.to_dict()["lights"]["e1"]["colours"]]
    plan = zone_plan("Paint", runs={"r1": run_line("r1", "e1", "a", n=4, first=3)})
    assert plan.render(0) == cols[3:7]


def test_zone_falls_back_to_the_palette_where_nothing_is_painted():
    p = painted(4)
    register(p)
    runs = {"r1": run_line("r1", "e1", "a", n=4), "r2": run_line("r2", "e2", "a", n=6)}
    f = zone_plan("Paint", runs=runs).render(0)
    assert f[:4] == [tuple(c) for c in p.to_dict()["lights"]["e1"]["colours"]]
    assert f[4:] == [p.palette[(4 + i) % 4] for i in range(6)]


def area_paint():
    pts = [{"pos": [0, 500, 500], "colour": list(R)}, {"pos": [1000, 500, 500], "colour": list(B)}]
    return make(pixels("Room", areas={"a": pts}), "room")


def test_area_paint_colours_each_pixel_with_the_nearest_painted_point_by_position():
    register(area_paint())
    f = zone_plan("Room", runs={"r1": run_line("r1", "e1", "a", n=11)}).render(0)
    assert f[:5] == [R] * 5 and f[6:] == [B] * 5  # left half red, right half blue (the middle is a tie, either)
    # the same positions in another area get no paint: the palette is repeated
    g = zone_plan("Room", runs={"r1": run_line("r1", "e1", "elsewhere", n=11)}).render(0)
    assert set(g) <= {R, B} and g != f


def test_light_paint_beats_area_paint_for_the_pixels_it_covers():
    pts = [{"pos": [0, 500, 500], "colour": list(R)}]
    p = make(pixels("Both", lights={"e1": {"first": 0, "colours": [list(G)] * 3}}, areas={"a": pts}), "both")
    register(p)
    f = zone_plan("Both", runs={"r1": run_line("r1", "e1", "a", n=6)}).render(0)
    assert f[:3] == [G] * 3 and f[3:] == [R] * 3


def test_area_paint_follows_spatial_mode_and_effects():
    register(area_paint())
    runs = {"r1": run_line("r1", "e1", "a", n=21)}
    steady = zone_plan("Room", "Steady", runs, "spatial", {"preset": "left_right"}).render(0)
    assert steady[0] == R and steady[-1] == B
    chase = zone_plan("Room", "Chase", runs, "spatial", {"preset": "left_right"})
    assert chase.render(1.0) != steady and chase.render(0.0) == steady
    for effect in ("Twinkle", "Fade", "Wave", "Marquee", "Sparkle", "Alternate", "Twinkle Swap"):
        f = zone_plan("Room", effect, runs, "spatial", {"preset": "left_right"}).render(1.1)
        assert len(f) == 21


def test_chase_in_a_spatial_zone_slides_painted_colours_along_the_direction():
    p = painted(20)
    register(p)
    runs = {"r1": run_line("r1", "e1", "a", n=20)}
    plan = zone_plan("Paint", "Chase", runs, "spatial", {"preset": "left_right"})
    base = plan.render(0)
    assert plan.render(1.0)[3:] == base[:-3] or plan.render(1.0)[4:] == base[1:-3]


# --- on a light -------------------------------------------------------------------------------------------------------
@aio
async def test_a_light_takes_a_user_pattern_by_name_or_id_and_shows_its_painted_pixels(hass):
    p = painted(10)
    register(p)
    ctl = FakeLight(hass, 10, entry_id="e1")
    await ctl.async_set_pattern("px", "Steady", transition=0)  # by id
    await asyncio.sleep(0.02)
    assert ctl.pattern == "Paint"  # stored by name
    assert ctl.sent[-1] == [tuple(c) for c in p.to_dict()["lights"]["e1"]["colours"]]
    other = FakeLight(hass, 10, entry_id="elsewhere")
    await other.async_set_pattern("paint", "Steady", transition=0)
    await asyncio.sleep(0.02)
    assert other.sent[-1] == [p.palette[i % 10] for i in range(10)]  # not covered: palette repeated


@aio
async def test_an_unknown_pattern_or_a_map_effect_on_a_light_falls_back(hass):
    ctl = FakeLight(hass, 10)
    await ctl.async_set_pattern("Nope", "Ripple", transition=0)
    assert ctl.pattern == patterns.DEFAULT_PATTERN and ctl.pattern_effect == "Wave"


# --- the store ---------------------------------------------------------------------------------------------------------------
@aio
async def test_store_creates_updates_renames_deletes_and_announces(hass):
    store = new_store(hass)
    await store.async_load()
    heard = []
    store.add_listener(lambda old, new: heard.append((old, new)))
    a = await store.async_save_pattern(palette("First"))
    assert a["id"] and a["name"] == "First" and patterns.canonical("First") == "First"
    b = await store.async_save_pattern(palette("Second", default_effect="Chase"))
    assert [p["name"] for p in store.view()] == ["First", "Second"]
    with pytest.raises(ValueError, match="already"):
        await store.async_save_pattern(palette("first"))
    with pytest.raises(ValueError):
        await store.async_save_pattern(palette("Rainbow"))
    # edit keeping its name
    again = await store.async_save_pattern({**palette("First", [(1, 2, 3)]), "id": a["id"]})
    assert again["id"] == a["id"] and again["colours"] == [[1, 2, 3]] and heard[-1] == ("First", "First")
    # rename
    await store.async_save_pattern({**palette("Primero"), "id": a["id"]})
    assert heard[-1] == ("First", "Primero") and patterns.canonical("First") is None and patterns.canonical("Primero")
    # a pattern may take the name another gave up, but not one still in use
    with pytest.raises(ValueError):
        await store.async_save_pattern({**palette("second"), "id": a["id"]})
    with pytest.raises(KeyError):
        await store.async_save_pattern({**palette("Ghost"), "id": "nope"})
    await store.async_delete_pattern(b["id"])
    assert heard[-1] == ("Second", None) and patterns.canonical("Second") is None
    with pytest.raises(KeyError):
        await store.async_delete_pattern(b["id"])
    assert [p["name"] for p in store.view()] == ["Primero"]


@aio
async def test_store_allows_at_most_200_patterns(hass):
    store = new_store(hass)
    await store.async_load()
    for i in range(200):
        await store.async_save_pattern(palette(f"P{i}", [(i % 256, 0, 0)]))
    with pytest.raises(ValueError, match="200"):
        await store.async_save_pattern(palette("One too many"))
    first = store.view()[0]
    await store.async_save_pattern({**palette("Renamed"), "id": first["id"]})  # editing is still fine
    assert len(store.view()) == 200


@aio
async def test_store_persists_and_reloads_dropping_invalid_rows(hass):
    store = new_store(hass)
    await store.async_load()
    a = await store.async_save_pattern(palette("Keep"))
    await store.async_save_pattern(pixels("Pix", lights={"e1": {"colours": [[1, 2, 3]] * 4}}))
    assert const.USER_PATTERNS_STORE_KEY == "light_show_studio.user_patterns"
    saved = H.STORAGE[const.USER_PATTERNS_STORE_KEY]
    assert [p["name"] for p in saved["patterns"]] == ["Keep", "Pix"]
    saved["patterns"] += [{"id": a["id"], **palette("Dup id")}, {"id": "bad", "name": "x"}, {"name": "no id", "kind": "palette"},
                          {"id": "clash", **palette("keep")}, "junk", {"id": "ok2", **palette("Second ok")}]
    patterns.set_user_patterns([])
    fresh = new_store(hass)
    await fresh.async_load()
    assert [p["name"] for p in fresh.view()] == ["Keep", "Pix", "Second ok"]
    assert patterns.canonical("keep") == "Keep" and patterns.canonical("ok2") == "Second ok"


@aio
async def test_store_survives_an_unreadable_file(hass):
    class Broken(H.FakeStore):
        async def async_load(self):
            raise OSError("disk")
    store = new_store(hass)
    store._store = Broken(hass, 1, "x")
    await store.async_load()
    assert store.view() == []


@aio
async def test_a_failing_follower_does_not_stop_the_others(hass):
    store = new_store(hass)
    await store.async_load()
    heard = []

    async def boom(old, new):
        raise RuntimeError("x")
    store.add_listener(boom)
    store.add_listener(lambda old, new: heard.append((old, new)))
    await store.async_save_pattern(palette("A"))
    assert heard == [("A", "A")]


# --- websocket commands ----------------------------------------------------------------------------------------------
def test_commands_are_admin_only_for_writes():
    assert ws_patterns.ws_save._admin and ws_patterns.ws_delete._admin
    for f in (ws_patterns.ws_list, ws_patterns.ws_preview, ws_patterns.ws_preview_stop, ws_patterns.ws_paint):
        assert not getattr(f, "_admin", False)


@aio
async def test_list_save_delete_over_the_websocket(hass):
    store = hass.data[const.USER_PATTERNS_KEY] = new_store(hass)
    await store.async_load()
    conn = FakeConn()
    await call(ws_patterns.ws_save, hass, conn, {"type": "light_show_studio/patterns/save", "pattern": palette("Via ws")})
    pid = conn.results[-1][1]["pattern"]["id"]
    await call(ws_patterns.ws_save, hass, conn, {"type": "light_show_studio/patterns/save", "pattern": palette("Via ws")})
    assert conn.errors[-1][1] == "invalid_format" and "already" in conn.errors[-1][2]
    await call(ws_patterns.ws_save, hass, conn, {"type": "light_show_studio/patterns/save",
                                                 "pattern": {**palette("Gone"), "id": "zzz"}})
    assert conn.errors[-1][1] == "not_found"
    await call(ws_patterns.ws_list, hass, conn, {"type": "light_show_studio/patterns/list"})
    res = conn.results[-1][1]
    assert [p["name"] for p in res["patterns"]] == ["Via ws"] and res["builtin"] == patterns.PATTERN_LIST
    assert res["effects"] == patterns.PATTERN_EFFECTS and res["map_effects"] == patterns.MAP_EFFECTS
    assert res["limits"]["max_patterns"] == 200 and res["limits"]["max_name"] == 40 and res["limits"]["max_paint_batch"] == 500
    await call(ws_patterns.ws_delete, hass, conn, {"type": "light_show_studio/patterns/delete", "pattern_id": pid})
    assert conn.results[-1][1] == {}
    await call(ws_patterns.ws_delete, hass, conn, {"type": "light_show_studio/patterns/delete", "pattern_id": pid})
    assert conn.errors[-1][1] == "not_found"


@aio
async def test_commands_say_so_when_nothing_is_set_up(hass):
    conn = FakeConn()
    await call(ws_patterns.ws_list, hass, conn, {"type": "light_show_studio/patterns/list"})
    assert conn.errors[-1][1] == "not_found"


# --- preview and paint ------------------------------------------------------------------------------------------------
def world(hass, lights=None, model=None, idle=3.0):
    lights = lights or {"e1": FakeLight(hass, 10, entry_id="e1"), "e2": FakeLight(hass, 6, entry_id="e2")}
    mgr = FakeMgr(hass, lights, model)
    hass.data[const.PREVIEW_KEY] = patternpreview.PreviewManager(hass, mgr, idle=idle)
    return mgr


async def start_preview(hass, conn, **kw):
    await call(ws_patterns.ws_preview, hass, conn, {"type": "light_show_studio/patterns/preview", **kw})


@aio
async def test_preview_shows_an_unsaved_pattern_and_restores_the_light(hass):
    mgr = world(hass)
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    await asyncio.sleep(0.02)
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern=pixels(lights={"e1": {"colours": [list(G)] * 10}}), effect="Steady")
    assert conn.results[-1][1]["lights"] == ["e1"]
    await asyncio.sleep(0.5)
    assert ctl.sent[-1] == [G] * 10 and ctl.zone == "Preview"
    assert patterns.pattern_names() == patterns.PATTERN_LIST  # not listed or saved
    await call(ws_patterns.ws_preview_stop, hass, conn, {"type": "light_show_studio/patterns/preview_stop"})
    assert conn.results[-1][1] == {"stopped": True}
    await asyncio.sleep(0.6)
    assert ctl.zone is None and ctl.sent[-1] == [R] * 10  # back to red
    assert not [n for n in patterns._USER]  # the temporary pattern is gone


@aio
async def test_preview_turns_a_light_on_and_back_off(hass):
    mgr = world(hass)
    ctl = mgr.ctls["e2"] = FakeLight(hass, 6, on=False, entry_id="e2")
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e2", pattern=palette("x", [(0, 0, 255)]), effect="Steady")
    assert ctl.is_on
    await call(ws_patterns.ws_preview_stop, hass, conn, {"type": "light_show_studio/patterns/preview_stop"})
    await asyncio.sleep(0.6)
    assert not ctl.is_on


@aio
async def test_preview_of_a_saved_or_built_in_pattern_by_name(hass):
    mgr = world(hass)
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern="Candy Cane", effect="Steady", speed=70)
    assert conn.results[-1][1]["pattern"] == "Candy Cane"
    await asyncio.sleep(0.4)
    assert mgr.ctls["e1"].sent[-1][0] == R
    await start_preview(hass, conn, entry_id="e1", pattern="Nope")
    assert conn.errors[-1][1] == "failed" and "Unknown pattern" in conn.errors[-1][2]
    conn.close()


@aio
async def test_preview_by_area_and_validation_errors(hass):
    areas = (mapping.Area("room", "Room"),)
    runs = (mapping.Run("r1", "e1", "A", 0, 10, area="room"), mapping.Run("r2", "e2", "B", 0, 6, area="room"))
    mgr = world(hass, model=mapping.MapModel(runs, (), {"kind": "none", "w": 1, "h": 1}, areas))
    conn = FakeConn()
    await start_preview(hass, conn, area="Room", pattern=palette("x", [(0, 255, 0)]), effect="Steady")
    assert sorted(conn.results[-1][1]["lights"]) == ["e1", "e2"]
    await asyncio.sleep(0.4)
    assert mgr.ctls["e1"].sent[-1] == [G] * 10 and mgr.ctls["e2"].sent[-1] == [G] * 6
    for bad in ({"area": "Nowhere"}, {"entry_id": "nope"}):
        await start_preview(hass, conn, pattern=palette("x"), **bad)
        assert conn.errors[-1][1] == "failed"
    await start_preview(hass, conn, entry_id="e1", pattern={"kind": "palette", "colours": []})
    assert conn.errors[-1][1] == "failed" and "palette" in conn.errors[-1][2]
    with pytest.raises(Exception):  # both a light and an area, or neither: the schema or the handler says no
        await start_preview(hass, conn, entry_id="e1", area="Room", pattern=palette("x"))
    await start_preview(hass, conn, pattern=palette("x"))
    assert conn.errors[-1][1] == "failed"
    for bad in ({"effect": "Nope"}, {"speed": 0}, {"speed": 101}, {"speed": 5.5}):
        with pytest.raises(Exception):
            await start_preview(hass, conn, entry_id="e1", pattern=palette("x"), **bad)
    conn.close()


@aio
async def test_preview_accepts_map_effects(hass):
    mgr = world(hass)
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern=palette("x"), effect="Sweep")
    await asyncio.sleep(0.15)
    assert len({tuple(f) for f in mgr.ctls["e1"].sent}) > 2
    conn.close()


@aio
async def test_paint_shows_pixels_instantly_and_a_new_preview_swaps_without_flashing_back(hass):
    mgr = world(hass)
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern=pixels(lights={"e1": {"colours": [list(K)] * 10}}), effect="Steady")
    await asyncio.sleep(0.05)
    n = len(ctl.sent)
    await call(ws_patterns.ws_paint, hass, conn, {"type": "light_show_studio/patterns/paint", "entry_id": "e1",
                                                   "set": [[0, 255, 0, 0], [4, 0, 0, 255]]})
    assert conn.results[-1][1] == {"painted": 2}
    assert len(ctl.sent) == n + 1  # shown at once, synchronously
    shown = ctl.sent[-1]
    assert shown[0][0] > 0 and shown[4][2] > 0 and shown[1] == K  # (fading in a little, the brush is visible)
    await asyncio.sleep(0.5)
    assert ctl.sent[-1][0] == R and ctl.sent[-1][4] == B and ctl.sent[-1][2] == K
    # swapping to another preview keeps the light claimed (no flash back to red in between)
    await start_preview(hass, conn, entry_id="e1", pattern=palette("y", [(0, 255, 0)]), effect="Steady")
    assert ctl.zone == "Preview"
    assert all(f != [R] * 10 for f in ctl.sent[n + 2:])
    conn.close()
    await asyncio.sleep(0.5)
    assert ctl.zone is None


@aio
async def test_paint_errors(hass):
    mgr = world(hass)
    conn = FakeConn()
    paint = lambda **kw: call(ws_patterns.ws_paint, hass, conn, {"type": "light_show_studio/patterns/paint", **kw})  # noqa: E731
    await paint(entry_id="e1", set=[[0, 1, 2, 3]])
    assert conn.errors[-1][1] == "failed" and "No preview" in conn.errors[-1][2]
    await start_preview(hass, conn, entry_id="e1", pattern=palette("x"), effect="Steady")
    await paint(entry_id="e1", set=[[0, 1, 2, 3]])
    assert "pixels pattern" in conn.errors[-1][2]  # a palette can't be painted
    await start_preview(hass, conn, entry_id="e1", pattern=pixels(lights={"e1": {"colours": [[1, 1, 1]]}}))
    await paint(entry_id="e2", set=[[0, 1, 2, 3]])
    assert "part of the preview" in conn.errors[-1][2]
    await paint(entry_id="e1", set=[[10, 1, 2, 3]])
    assert "between 0 and 9" in conn.errors[-1][2]
    for bad in ([], [[0, 1, 2]], [[0, 1, 2, 256]], [[-1, 1, 2, 3]], [[0, 1, 2, 3.5]], [[True, 1, 2, 3]], ["x"],
                [[0, 0, 0, 0]] * 501):
        with pytest.raises(Exception):
            await paint(entry_id="e1", set=bad)
    await paint(entry_id="e1", set=[[i % 10, 5, 5, 5] for i in range(500)])  # the biggest batch
    assert conn.results[-1][1] == {"painted": 500}
    conn.close()


@aio
async def test_preview_ends_after_it_goes_idle_and_painting_keeps_it_alive(hass):
    mgr = world(hass, idle=0.4)
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern=pixels(lights={"e1": {"colours": [[1, 1, 1]]}}), effect="Steady")
    await asyncio.sleep(0.25)
    await call(ws_patterns.ws_paint, hass, conn, {"type": "light_show_studio/patterns/paint", "entry_id": "e1", "set": [[0, 9, 9, 9]]})
    await asyncio.sleep(0.25)
    assert ctl.zone == "Preview"  # 0.5 s since the start, but painting re-armed the 0.4 s timer
    await asyncio.sleep(0.6)
    assert ctl.zone is None and ctl.sent[-1] == [R] * 10
    assert hass.data[const.PREVIEW_KEY].sessions == {}


@aio
async def test_closing_the_connection_restores_the_light(hass):
    mgr = world(hass)
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern=palette("x", [(0, 0, 255)]), effect="Steady")
    await asyncio.sleep(0.3)
    assert ctl.zone == "Preview"
    conn.close()
    await asyncio.sleep(0.6)
    assert ctl.zone is None and ctl.sent[-1] == [R] * 10


@aio
async def test_a_person_taking_the_light_ends_the_preview_for_it(hass):
    mgr = world(hass)
    ctl = mgr.ctls["e1"]
    conn = FakeConn()
    await start_preview(hass, conn, entry_id="e1", pattern=palette("x", [(0, 0, 255)]), effect="Steady")
    await asyncio.sleep(0.2)
    await ctl.async_turn_on(colour=G, transition=0)
    await asyncio.sleep(0.2)
    assert ctl.zone is None
    last = ctl.sent[-1]
    await asyncio.sleep(1.3)
    assert ctl.sent[-1] == last == [G] * 10 or ctl.sent[-1] == [G] * 10
    conn.close()


@aio
async def test_stop_when_nothing_is_previewing(hass):
    world(hass)
    conn = FakeConn()
    await call(ws_patterns.ws_preview_stop, hass, conn, {"type": "light_show_studio/patterns/preview_stop"})
    assert conn.results[-1][1] == {"stopped": False}
