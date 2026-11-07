"""Tests for the map: validation, defaults, ordering, positions and zone rendering; plain pytest."""
from __future__ import annotations

import math
import time

import pytest

from test_library import _load

mapping = _load("mapping")
mp = _load("mapped_patterns")
patterns = _load("patterns")
MapError, LightSpec = mapping.MapError, mapping.LightSpec


def run(id="r1", entry="e1", name="Run", first=0, count=10, reverse=False, pts=None, **extra):
    return {"id": id, "entry_id": entry, "name": name, "first": first, "count": count, "reverse": reverse,
            "pts": [] if pts is None else pts, **extra}


def zone(id="z1", name="Zone", runs=("r1",), **kw):
    return {"id": id, "name": name, "runs": list(runs), **kw}


def mp_of(runs, zones=()):
    return mapping.from_dict({"runs": runs, "zones": list(zones)})


# --- validation ------------------------------------------------------------------------
def test_round_trip():
    data = {"version": 2, "areas": [{"id": "porch", "name": "Porch", "ha_area_id": "porch_ha"}],
            "runs": [{**run(pts=[[0.1, 0.9, 0.5], [0.6, 0.9, 0.2]]), "area": "porch", "positions": [10, 20, 30] * 10}],
            "zones": [zone(mode="spatial", axis={"dir": [0.0, 1.0, 0.0], "preset": "rise"}, areas=["porch"],
                           pattern="Rainbow", effect="Wave", speed=70)],
            "backdrop": {"kind": "grid", "w": 1999.0, "h": 1500.0}}
    model = mapping.from_dict(data, {"e1": 50})
    assert mapping.to_dict(model) == data
    assert mapping.from_dict(mapping.to_dict(model), {"e1": 50}) == model


def test_zone_defaults():
    z = mp_of([run()], [{"id": "z", "name": "A"}]).zones[0]
    assert (z.mode, z.pattern, z.effect, z.speed, z.runs) == ("string", patterns.DEFAULT_PATTERN, "Chase", 50, ())
    assert dict(z.axis) == {"dir": [1.0, 0.0, 0.0], "preset": "left_right"} and z.areas == ()


@pytest.mark.parametrize("bad, text", [
    (run(first=-1), "first pixel"),
    (run(count=0), "pixel count"),
    (run(count=2041), "pixel count"),
    (run(first=1.5), "whole number"),
    (run(first=True), "whole number"),
    (run(first=float("nan")), "whole number"),
    (run(name=""), "run name"),
    (run(name="x" * 61), "run name"),
    (run(name="a\0b"), "run name"),
    (run(id="has space"), "run id"),
    (run(id="x" * 65), "run id"),
    (run(entry=""), "pick a light"),
    (run(reverse="yes"), "true or false"),
    (run(pts=[[0.1]]), "x, y"),
    (run(pts=[[0.1, 0.2, 0.3, 0.4]]), "x, y"),
    (run(area="has space"), "area"),
    (run(positions=[1, 2, 3]), "need 10 positions"),
    (run(positions=[1, 2, "a"] * 10), "numbers"),
    (run(positions=[[1, 2]] * 10), "triples"),
    (run(positions=[1] * 6200), "at most"),
    (run(pts=[[0.1, float("nan")]]), "number"),
    (run(pts=[[0.1, "a"]]), "number"),
    (run(pts=[[0.1, 99]]), "between"),
    (run(pts="abc"), "list"),
    (run(pts=[[0, 0]] * 5000), "at most"),
])
def test_bad_runs_rejected(bad, text):
    with pytest.raises(MapError, match=text):
        mapping.from_dict({"runs": [bad]})


def test_bounds_against_the_light():
    with pytest.raises(MapError, match="don't fit"):
        mapping.from_dict({"runs": [run(first=45, count=10)]}, {"e1": 50})
    mapping.from_dict({"runs": [run(first=40, count=10)]}, {"e1": 50})
    with pytest.raises(MapError, match="doesn't exist"):
        mapping.from_dict({"runs": [run(entry="gone")]}, {"e1": 50})


def test_overlap_within_a_light_only():
    a, b = run("a", first=0, count=10, name="A"), run("b", first=9, count=5, name="B")
    with pytest.raises(MapError, match="A and B share pixels 9-9"):
        mapping.from_dict({"runs": [b, a]})
    mapping.from_dict({"runs": [a, run("b", first=10, count=5)]})  # touching is fine
    mapping.from_dict({"runs": [a, run("b", entry="e2", first=0, count=10)]})  # another light is fine


def test_unique_ids_and_names():
    with pytest.raises(MapError, match="used twice"):
        mapping.from_dict({"runs": [run("a"), run("a", first=20)]})
    with pytest.raises(MapError, match="used twice"):
        mapping.from_dict({"runs": [run()], "zones": [zone("z", "A"), zone("z", "B")]})
    with pytest.raises(MapError, match="two zones"):
        mapping.from_dict({"runs": [run()], "zones": [zone("z1", "Kitchen"), zone("z2", "kitchen")]})


def test_bad_zones_rejected():
    for bad, text in [(zone(runs=["nope"]), "unknown run"), (zone(runs=["r1", "r1"]), "twice"),
                      (zone(mode="spiral"), "mode"), (zone(pattern="Neon"), "pattern"),
                      (zone(effect="Boom"), "effect"), (zone(speed=0), "speed"), (zone(speed=101), "speed"),
                      (zone(speed="fast"), "whole number"), (zone(axis={"centre": [1]}), "centre"),
                      (zone(axis={"angle": float("inf")}), "number"), (zone(axis="x"), "object"),
                      ({**zone(), "runs": "r1"}, "list")]:
        with pytest.raises(MapError, match=text):
            mapping.from_dict({"runs": [run()], "zones": [bad]})


def test_limits():
    many = [run(f"r{i}", first=i, count=1) for i in range(201)]
    with pytest.raises(MapError, match="at most 200"):
        mapping.from_dict({"runs": many})
    with pytest.raises(MapError, match="at most 64"):
        mapping.from_dict({"runs": [run()], "zones": [zone(f"z{i}", f"Z{i}") for i in range(65)]})
    for bad in ("x", [], None, {"runs": "x"}, {"runs": [1]}, {"backdrop": {"w": -1}}):
        with pytest.raises(MapError):
            mapping.from_dict(bad)


def test_lenient_load_drops_what_is_broken():
    data = {"runs": [run("a", count=10), run("bad", first=-5), run("c", first=5, count=10), run("d", entry="gone"),
                     run("e", first=20)],
            "zones": [zone("z1", "One", runs=("a", "bad", "e")), zone("z2", "Two", pattern="Neon"), "junk"],
            "backdrop": "junk"}
    model = mapping.from_dict(data, {"e1": 100}, strict=False)
    assert [r.id for r in model.runs] == ["a", "e"]  # bad value, overlap with a, unknown light all dropped
    assert [z.id for z in model.zones] == ["z1"] and model.zones[0].runs == ("a", "e")
    assert model.backdrop["kind"] == "none"


def test_prune_dangling():
    model = mp_of([run("a"), run("b", entry="e2", first=0)], [zone("z", "Z", runs=("a", "b"))])
    pruned = mapping.prune(model, ["e1"])
    assert [r.id for r in pruned.runs] == ["a"] and pruned.zones[0].runs == ("a",)
    assert mapping.prune(model, ["e1", "e2"]) is model


def test_pts_clamped():
    model = mp_of([run(pts=[[-0.2, 1.3], [0.5, 0.5]])])
    assert model.runs[0].pts == ((0.0, 1.0, 0.5), (0.5, 0.5, 0.5))  # z defaults to the middle


def test_zone_lookup_by_id_or_name():
    model = mp_of([run()], [zone("z1", "Kitchen")])
    assert model.zone("z1").name == "Kitchen" and model.zone("kitchen").id == "z1" and model.zone("x") is None


# --- defaults -----------------------------------------------------------------------------
def test_default_one_run_per_light():
    model = mapping.default_map([LightSpec("a", "Roof", 50), LightSpec("b", "Tree", 100, "twinkly")])
    assert [(r.entry_id, r.first, r.count, r.name) for r in model.runs] == [("a", 0, 50, "Roof"), ("b", 0, 100, "Tree")]
    assert model.zones == ()
    mapping.from_dict(mapping.to_dict(model), {"a": 50, "b": 100})  # defaults are themselves valid


def test_default_kitchen_preset():
    model = mapping.default_map([LightSpec("k", "Kitchen", 904, "wled")])
    assert [(r.first, r.count) for r in model.runs] == [(0, 279), (279, 145), (424, 192), (616, 288)]
    assert sum(r.count for r in model.runs) == 904
    assert model.runs[0].name == "Toe-kick" and model.runs[-1].name == "Above cabinets"
    mapping.from_dict(mapping.to_dict(model), {"k": 904})
    # only a WLED with exactly 904 LEDs gets the preset
    assert len(mapping.default_map([LightSpec("k", "K", 903, "wled")]).runs) == 1
    assert len(mapping.default_map([LightSpec("k", "K", 904, "govee")]).runs) == 1


def test_default_twinkly_uses_layout():
    layout = [[0, 0], [10, 0], [10, 20], [0, 20]]
    r = mapping.default_map([LightSpec("t", "Tree", 4, "twinkly", layout)]).runs[0]
    pos = mapping.run_positions(r)
    assert mapping.is_mapped(r) and pos[0][0] < pos[1][0]
    assert pos[1][1] > pos[2][1]  # the layout is y down (the card), the map y up
    assert all(0 <= x <= 1 and 0 <= y <= 1 and z == pytest.approx(0.5) for x, y, z in pos)
    # a layout that doesn't match the bulb count is ignored
    r5 = mapping.default_map([LightSpec("t", "Tree", 5, "twinkly", layout)]).runs[0]
    assert not mapping.is_mapped(r5) and len(r5.pts) == 2


# --- geometry -----------------------------------------------------------------------------
def test_run_positions_spread_along_polyline():
    r = mp_of([run(count=5, pts=[[0, 0], [1, 0]])]).runs[0]
    assert [round(x, 3) for x, _, _ in mapping.run_positions(r)] == [0, 0.25, 0.5, 0.75, 1.0]
    bent = mp_of([run(count=3, pts=[[0, 0], [0.5, 0], [0.5, 0.5]])]).runs[0]
    assert [tuple(round(v, 3) for v in p) for p in mapping.run_positions(bent)] == [(0, 0, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0.5)]
    one = mp_of([run(count=1, pts=[[0, 0], [1, 0]])]).runs[0]
    assert mapping.run_positions(one) == [(0.5, 0.0, 0.5)]
    deep = mp_of([run(count=3, pts=[[0, 0, 0], [0, 0, 1]])]).runs[0]  # a line going towards the viewer
    assert [round(p[2], 3) for p in mapping.run_positions(deep)] == [0, 0.5, 1]


def test_run_positions_one_point_per_pixel_kept():
    pts = [[0.1, 0.1], [0.9, 0.2], [0.3, 0.8]]
    assert mapping.run_positions(mp_of([run(count=3, pts=pts)]).runs[0]) == [(*p, 0.5) for p in pts]
    assert len(mapping.run_positions(mp_of([run(count=7)]).runs[0])) == 7  # no points: a default line


def test_virtual_order_with_reverse():
    model = mp_of([run("a", first=0, count=3), run("b", entry="e2", first=10, count=3, reverse=True),
                   run("c", first=3, count=2)], [zone(runs=("b", "a", "c"))])
    order = mapping.virtual_order(model.zones[0], model.run_map())
    assert order == [("e2", 12), ("e2", 11), ("e2", 10), ("e1", 0), ("e1", 1), ("e1", 2), ("e1", 3), ("e1", 4)]


def test_slice_round_trip():
    model = mp_of([run("a", first=0, count=3), run("b", entry="e2", first=10, count=4, reverse=True),
                   run("c", first=5, count=2, reverse=True)], [zone(runs=("b", "a", "c"))])
    z, runs = model.zones[0], model.run_map()
    slices = mapping.zone_slices(z, runs)
    assert [(s.start, s.count) for s in slices] == [(0, 4), (4, 3), (7, 2)]
    virtual = list(range(9))  # a colour per virtual position
    order = mapping.virtual_order(z, runs)
    placed = {}
    for entry, first, seg in mapping.split_frame(slices, virtual):
        for k, v in enumerate(seg):
            placed[(entry, first + k)] = v
    assert len(placed) == 9
    for v, key in enumerate(order):  # every light pixel shows the colour of its virtual position
        assert placed[key] == v


def test_zone_slices_skip_unknown_runs():
    model = mp_of([run("a")])
    z = mapping.Zone("z", "Z", ("a", "ghost"))
    assert len(mapping.zone_slices(z, model.run_map())) == 1


# --- rendering ------------------------------------------------------------------------------
def test_string_mode_is_the_pattern_over_the_whole_virtual_string():
    model = mp_of([run("a", count=7), run("b", entry="e2", count=5)],
                  [zone(runs=("a", "b"), pattern="Candy Cane", effect="Chase")])
    plan = mp.ZonePlan(model.zones[0], model.run_map())
    for t in (0.0, 0.7, 3.3):
        assert plan.render(t) == patterns.render("Candy Cane", "Chase", t, 12)
        assert mp.render_zone(model, model.zones[0], t) == plan.render(t)


def test_chase_continues_across_run_boundaries():
    model = mp_of([run("a", count=10), run("b", entry="e2", count=10)], [zone(runs=("a", "b"), pattern="Rainbow")])
    plan = mp.ZonePlan(model.zones[0], model.run_map())
    single = patterns.render("Rainbow", "Chase", 2.5, 20)
    (_, _, a), (_, _, b) = plan.split(plan.render(2.5))
    assert a + b == single  # no restart at the join: the second run carries on where the first stops
    assert patterns.render("Rainbow", "Chase", 2.5, 10) != b  # ...unlike two separate lights


def test_reversed_run_flows_the_other_way():
    model = mp_of([run("a", count=6, reverse=True)], [zone(runs=("a",), pattern="Rainbow", effect="Steady")])
    plan = mp.ZonePlan(model.zones[0], model.run_map())
    (_, _, seg), = plan.split(plan.render(0))
    assert seg == list(reversed(patterns.render("Rainbow", "Steady", 0, 6)))


def _spatial(runs, **kw):
    kw.setdefault("effect", "Chase")
    model = mp_of(runs, [zone(runs=[r["id"] for r in runs], mode="spatial", **kw)])
    return mp.ZonePlan(model.zones[0], model.run_map())


def test_spatial_u_orders_by_x():
    # the first run is the right half but listed first: u must follow x, not the string order
    plan = _spatial([run("a", count=20, pts=[[0.9, 0.5], [0.5, 0.5]]), run("b", entry="e2", count=20, pts=[[0.4, 0.5], [0.0, 0.5]])],
                    axis={"angle": 0})
    assert min(plan.u) == pytest.approx(0) and max(plan.u) == pytest.approx(1)
    xs = [p[0] for s in plan.slices for p in mapping.run_positions(s.run)]
    order = sorted(range(40), key=lambda i: xs[i])
    assert [plan.u[i] for i in order] == sorted(plan.u)


def test_spatial_angle_directions():
    pts = [[0.0, 0.0], [1.0, 1.0]]  # from the bottom left to the top right (y is up)
    right = _spatial([run(count=11, pts=pts)], axis={"angle": 0}).u
    up = _spatial([run(count=11, pts=pts)], axis={"angle": 90}).u
    left = _spatial([run(count=11, pts=pts)], axis={"angle": 180}).u
    assert right[0] == pytest.approx(0) and right[-1] == pytest.approx(1)
    assert up[0] == pytest.approx(0) and up[-1] == pytest.approx(1)  # bottom to top
    assert left[0] == pytest.approx(1) and left[-1] == pytest.approx(0)


def test_spatial_radial_symmetry():
    plan = _spatial([run("a", count=21, pts=[[0.0, 0.5], [1.0, 0.5]])], axis={"centre": [0.5, 0.5]})
    u = plan.u
    assert u[10] == pytest.approx(0)
    for k in range(1, 11):
        assert u[10 - k] == pytest.approx(u[10 + k])
    assert u[0] == pytest.approx(1)
    assert all(u[i] > u[i + 1] for i in range(10))  # growing out from the middle


def test_spatial_degenerate_zone():
    plan = _spatial([run(count=5, pts=[[0.5, 0.5], [0.5, 0.5]])])
    assert plan.u == [0.5] * 5
    assert len(plan.render(1.0)) == 5


@pytest.mark.parametrize("effect", patterns.PATTERN_EFFECTS)
@pytest.mark.parametrize("mode", ["string", "spatial"])
def test_every_effect_renders_valid_frames(effect, mode):
    model = mp_of([run("a", count=30, pts=[[0, 0], [1, 1]]), run("b", entry="e2", count=20, reverse=True)],
                  [zone(runs=("a", "b"), mode=mode, effect=effect, axis={"centre": [0.3, 0.6]})])
    plan = mp.ZonePlan(model.zones[0], model.run_map())
    for t in (0.0, 0.4, 5.5, 123.4):
        frame = plan.render(t)
        assert len(frame) == 50
        assert all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in frame)
        assert sum(len(seg) for *_, seg in plan.split(frame)) == 50


def test_spatial_chase_sweeps_along_x():
    """A lit pixel moves across the zone in the order of x, whatever the string order is."""
    plan = _spatial([run("a", count=40, pts=[[0.9, 0.5], [0.1, 0.5]])], pattern="Red & Green", axis={"angle": 0})
    # a pixel's colour at time t equals the colour of the pixel `3t` bulbs behind it along u
    f0, f1 = plan.render(0.0), plan.render(1.0)
    assert f0 != f1
    pos = plan.pos
    assert max(pos) == pytest.approx(39) and min(pos) == pytest.approx(0)


def test_spatial_wave_and_marquee_follow_position():
    for effect in ("Wave", "Marquee"):
        plan = _spatial([run("a", count=30, pts=[[0.9, 0.5], [0.1, 0.5]])], effect=effect, pattern="Warm White", axis={"angle": 0})
        frame = plan.render(0.3)
        # the brightness pattern is a function of x: pixels at the same x (u) get the same colour
        by_q = {}
        for q, c in zip(plan.q, frame):
            assert by_q.setdefault(q, c) == c


def test_zone_without_runs_renders_nothing():
    plan = mp.ZonePlan(mapping.Zone("z", "Z"), {})
    assert plan.n == 0 and plan.render(1.0) == []


def test_benchmark_prints_ms_per_frame(capsys):
    """Not a pass/fail speed test: it prints how long a frame of 1404 pixels takes (a Pi will be a few times slower)."""
    n = 1404
    runs = [run("a", count=904, pts=[[0.05, 0.5], [0.95, 0.5]]), run("b", entry="e2", count=500, pts=[[0.5, 0.05], [0.5, 0.95]])]
    lines = []
    for mode in ("string", "spatial"):
        for effect in ("Steady", "Chase", "Twinkle", "Wave", "Marquee", "Fade", "Sparkle"):
            model = mp_of(runs, [zone(runs=("a", "b"), mode=mode, effect=effect, pattern="Classic Multicolor")])
            plan = mp.ZonePlan(model.zones[0], model.run_map())
            plan.split(plan.render(0))
            start = time.perf_counter()
            for i in range(20):
                plan.split(plan.render(i * 0.04))
            lines.append(f"{mode:8} {effect:10} {(time.perf_counter() - start) / 20 * 1000:6.2f} ms/frame (N={n})")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    assert True


def test_map_websocket_writes_are_admin_only():
    """The reads are open to every user; every command that changes the map or a light needs an admin."""
    src = (mapping.__file__ and open(mapping.__file__.replace("mapping.py", "ws_map.py")).read())
    blocks = src.split("@websocket_api.websocket_command")[1:]
    assert len(blocks) == 10
    for block in blocks:
        name = block.split("def ", 1)[1].split("(", 1)[0]
        admin = ("ws_set", "ws_start", "ws_stop", "ws_speed", "ws_scan_start", "ws_scan_frame", "ws_scan_segment", "ws_scan_stop")
        assert ("require_admin" in block.split("def ", 1)[0]) == (name in admin), name
