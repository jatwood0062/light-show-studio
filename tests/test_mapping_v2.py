"""Tests for map version 2: migration from version 1, areas, 3D positions and directions; plain pytest."""
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


AREAS = [{"id": "a", "name": "Porch"}, {"id": "b", "name": "Kitchen"}]


def v2(runs, zones=(), areas=AREAS, **kw):
    return mapping.from_dict({"version": 2, "areas": areas, "runs": runs, "zones": list(zones), **kw})


def plan_of(model, i=0):
    return mp.ZonePlan(model.zones[i], model.run_map())


# --- migration ---------------------------------------------------------------------------------
def v1_u(positions, axis):
    """The version 1 rule, as it was: y down, angle 0 = left to right, 90 = bottom to top."""
    if "centre" in axis:
        cx, cy = axis["centre"]
        d = [math.hypot(x - cx, y - cy) for x, y in positions]
    else:
        a = math.radians(axis["angle"])
        d = [x * math.cos(a) - y * math.sin(a) for x, y in positions]
    lo, hi = min(d), max(d)
    return [(v - lo) / (hi - lo) for v in d]


V1 = {
    "version": 1,
    "runs": [run("a", count=30, pts=[[0.1, 0.9], [0.6, 0.2]]), run("b", entry="e2", count=20, pts=[[0.7, 0.3], [0.95, 0.8]]),
             run("c", entry="e1", first=30, count=5)],
    "zones": [zone("z1", "Sweep", ("b", "a"), mode="spatial", axis={"angle": 37}, effect="Wave"),
              zone("z2", "Burst", ("a", "b"), mode="spatial", axis={"centre": [0.3, 0.8]}),
              zone("z3", "Line", ("a", "c"))],
    "backdrop": {"kind": "grid", "w": 1999, "h": 1500},
}


def test_v1_migrates_without_losing_anything():
    model = mapping.from_dict(V1, {"e1": 50, "e2": 50})
    assert [r.id for r in model.runs] == ["a", "b", "c"] and [z.id for z in model.zones] == ["z1", "z2", "z3"]
    assert [(a.id, a.name) for a in model.areas] == [("main", "House")]
    assert all(r.area == "main" for r in model.runs)
    assert model.backdrop == {"kind": "grid", "w": 1999.0, "h": 1500.0}
    assert (model.zones[0].mode, model.zones[0].effect, model.zones[0].pattern, model.zones[0].speed) == ("spatial", "Wave", patterns.DEFAULT_PATTERN, 50)
    # the polylines keep their place: y now points up, so it is 1 - the old y, and z is the middle
    old = V1["runs"][0]["pts"]
    assert [(p[0], p[1], p[2]) for p in model.runs[0].pts] == [(old[0][0], pytest.approx(1 - old[0][1]), 0.5), (old[1][0], pytest.approx(1 - old[1][1]), 0.5)]
    # and the result is a valid version 2 map that survives a round trip
    again = mapping.from_dict(mapping.to_dict(model), {"e1": 50, "e2": 50})
    assert again == model and mapping.to_dict(model)["version"] == 2


@pytest.mark.parametrize("zi", [0, 1])
def test_v1_spatial_zones_sweep_exactly_as_before(zi):
    model = mapping.from_dict(V1, {"e1": 50, "e2": 50})
    plan = plan_of(model, zi)
    # v1 positions in zone order (y down)
    old_runs = {r["id"]: r for r in V1["runs"]}
    old = []
    for rid in V1["zones"][zi]["runs"]:
        old.extend(_old_spread(old_runs[rid]))
    expect = v1_u(old, V1["zones"][zi]["axis"])
    assert plan.u == pytest.approx(expect, abs=1e-4)


def _old_spread(r):
    """v1 run_positions for a two-point polyline."""
    (x0, y0), (x1, y1) = r["pts"]
    n = r["count"]
    return [(x0 + (x1 - x0) * k / (n - 1), y0 + (y1 - y0) * k / (n - 1)) for k in range(n)]


def test_v1_string_zone_unchanged_and_version_rules():
    model = mapping.from_dict(V1, {"e1": 50, "e2": 50})
    plan = plan_of(model, 2)
    assert plan.render(1.5) == patterns.render(patterns.DEFAULT_PATTERN, "Chase", 1.5, 35)
    # a map that says nothing about its version is read as the current one: no flipping
    d = mapping.to_dict(model)
    del d["version"]
    assert mapping.from_dict(d, {"e1": 50, "e2": 50}) == model
    assert mapping.migrate(d) is d
    # the input is not changed by migrating it
    snapshot = repr(V1)
    mapping.migrate(V1)
    assert repr(V1) == snapshot


def test_v1_with_nothing_in_it_and_with_damage():
    assert mapping.from_dict({"version": 1}).areas == ()
    # lenient load of a damaged version 1 store: the good parts stay
    bad = {"version": 1, "runs": [run("ok", pts=[[0.5, 0.5]]), run("bad", first=-1), "junk"],
           "zones": [zone("z", "Z", ("ok",), axis={"centre": [0.2, 0.2]})]}
    model = mapping.from_dict(bad, {"e1": 50}, strict=False)
    assert [r.id for r in model.runs] == ["ok"] and model.zones[0].axis["centre"] == [0.2, pytest.approx(0.8), 0.5]


# --- areas -------------------------------------------------------------------------------------
def test_light_area_defaults():
    ha = mapping.light_area(LightSpec("e1", "Roofline", 50, area_id="front_porch", area_name="Front Porch"))
    assert (ha.id, ha.name, ha.ha_area_id) == ("ha_front_porch", "Front Porch", "front_porch")
    own = mapping.light_area(LightSpec("01HXYZ", "Roofline", 50))
    assert (own.id, own.name, own.ha_area_id) == ("light_01HXYZ", "Roofline", None)
    odd = mapping.light_area(LightSpec("e1", "x", 50, area_id="a b/c", area_name="N"))
    mapping.parse_area({"id": odd.id, "name": odd.name})  # always a valid id


def test_default_map_groups_lights_by_area():
    lights = [LightSpec("a", "Roof", 50, area_id="porch", area_name="Porch"),
              LightSpec("b", "Rail", 40, area_id="porch", area_name="Porch"),
              LightSpec("c", "Tree", 100, "twinkly")]
    model = mapping.default_map(lights)
    assert [(a.id, a.name) for a in model.areas] == [("ha_porch", "Porch"), ("light_c", "Tree")]
    assert [r.area for r in model.runs] == ["ha_porch", "ha_porch", "light_c"]
    ys = [r.pts[0][1] for r in model.runs]
    assert ys[0] != ys[1] and 0 < ys[2] < 1  # two lights in one area get their own rows
    mapping.from_dict(mapping.to_dict(model), {"a": 50, "b": 40, "c": 100})


def test_run_without_area_takes_the_lights_default():
    defaults = {"e1": mapping.Area("ha_porch", "Porch", "porch")}
    model = mapping.from_dict({"runs": [run(), run("r2", entry="e2", first=0)]}, {"e1": 50, "e2": 50}, default_areas=defaults)
    assert model.runs[0].area == "ha_porch"
    assert model.runs[1].area == "main"  # no default known: the shared one
    assert {a.id for a in model.areas} == {"ha_porch", "main"}


def test_areas_validated():
    with pytest.raises(MapError, match="unknown area"):
        v2([run(area="nope")])
    with pytest.raises(MapError, match="used twice"):
        v2([], areas=[{"id": "a", "name": "A"}, {"id": "a", "name": "B"}])
    with pytest.raises(MapError, match="are called"):
        v2([], areas=[{"id": "a", "name": "Porch"}, {"id": "b", "name": "porch"}])
    with pytest.raises(MapError, match="unknown area"):
        v2([run(area="a")], [zone(areas=["zz"])])
    with pytest.raises(MapError, match="listed twice"):
        v2([run(area="a")], [zone(areas=["a", "a"])])
    with pytest.raises(MapError, match="at most 64"):
        v2([], areas=[{"id": f"a{i}", "name": f"A{i}"} for i in range(65)])
    for bad in ({"id": "a b", "name": "x"}, {"id": "a", "name": ""}, {"id": "a", "name": "x", "ha_area_id": 5}, "x"):
        with pytest.raises(MapError):
            v2([], areas=[bad])


def test_lenient_load_repairs_areas():
    data = {"version": 2, "areas": [{"id": "a", "name": "Porch"}, {"id": "a", "name": "Dup"}, "junk"],
            "runs": [run(area="a"), run("r2", first=10, area="ghost")],
            "zones": [zone(runs=("r1", "r2"), areas=["a", "ghost"])]}
    model = mapping.from_dict(data, {"e1": 50}, strict=False, default_areas={"e1": mapping.Area("ha_x", "X")})
    assert [a.id for a in model.areas] == ["a", "ha_x"]
    assert [r.area for r in model.runs] == ["a", "ha_x"]  # the run in a lost area moves to its light's default
    assert model.zones[0].areas == ("a",)


def test_zone_by_areas_orders_by_area_then_run_order():
    model = v2([run("p1", area="a", count=3), run("k1", entry="e2", area="b", count=4), run("p2", first=3, area="a", count=2),
                run("k2", entry="e2", first=4, area="b", count=1)],
               [zone("z1", "Kitchen first", runs=(), areas=["b", "a"]),
                zone("z2", "Mixed", runs=("p2",), areas=["a"]),
                zone("z3", "Dup", runs=("k1", "k2"), areas=["b"])])
    ids = lambda z: [s.run.id for s in mapping.zone_slices(z, model.run_map())]
    assert ids(model.zones[0]) == ["k1", "k2", "p1", "p2"]
    assert ids(model.zones[1]) == ["p2", "p1"]  # listed runs first, then the area's others, none twice
    assert ids(model.zones[2]) == ["k1", "k2"]
    assert plan_of(model).n == 10


def test_spatial_zone_is_per_area():
    # two areas, one run each along x; the second area is much smaller. Each area has its own 0-1.
    model = v2([run("p", area="a", count=11, pts=[[0, 0.5], [1, 0.5]]), run("k", entry="e2", area="b", count=11, pts=[[0.4, 0.5], [0.5, 0.5]])],
               [zone(runs=(), areas=["a", "b"], mode="spatial", axis={"preset": "left_right"})])
    u = plan_of(model).u
    assert u[:11] == pytest.approx([k / 10 for k in range(11)]) and u[11:] == pytest.approx([k / 10 for k in range(11)])


# --- 3D positions and directions -----------------------------------------------------------------
def test_axis_parsing():
    p = mapping.parse_axis
    assert p({"preset": "rise"}) == {"dir": [0.0, 1.0, 0.0], "preset": "rise"}
    assert p({"angle": 90}) == {"dir": [0.0, 1.0, 0.0], "preset": "rise"}
    assert p({"angle": 0}) == {"dir": [1.0, 0.0, 0.0], "preset": "left_right"}
    assert p({"preset": "front_back"})["dir"] == [0.0, 0.0, -1.0]
    assert p({}) == {"dir": [1.0, 0.0, 0.0], "preset": "left_right"}
    d = p({"dir": [3, 0, 4]})
    assert d == {"dir": [0.6, 0.0, 0.8]}  # normalised, and not a preset
    assert p({"dir": [0, 2, 0], "preset": "rise"}) == {"dir": [0.0, 1.0, 0.0], "preset": "rise"}
    assert p({"dir": [0, 2, 0], "preset": "fall"}) == {"dir": [0.0, 1.0, 0.0], "preset": "rise"}  # the vector decides
    assert p({"centre": [0.2, 0.4]}) == {"centre": [0.2, 0.4, 0.5]}
    assert p({"preset": "outward"}) == {"centre": [0.5, 0.5, 0.5], "preset": "outward"}
    assert p({"centre": [2, -1, 0.3]})["centre"] == [1.0, 0.0, 0.3]
    for bad in ({"dir": [0, 0, 0]}, {"dir": [1, 2]}, {"dir": "x"}, {"preset": "sideways"}, {"preset": 3}, {"centre": [1]},
                {"angle": "a"}, {"dir": [float("nan"), 0, 0]}, "x"):
        with pytest.raises(MapError):
            p(bad)


def u_of(pts, axis, area="a"):
    model = v2([run(count=len(pts), area=area, pts=pts)], [zone(mode="spatial", axis=axis)])
    return plan_of(model).u


def test_directions_in_3d():
    line = [[0, 0, 0], [1, 1, 1]]
    assert u_of(line, {"preset": "left_right"})[0] == pytest.approx(0) and u_of(line, {"preset": "left_right"})[-1] == pytest.approx(1)
    rise = u_of(line, {"preset": "rise"})
    assert rise[0] < rise[-1]
    back = u_of(line, {"preset": "front_back"})  # z is towards the viewer: front to back starts at the high z
    assert back[0] == pytest.approx(1) and back[-1] == pytest.approx(0)
    diag = u_of([[0, 0, 0], [1, 0, 0], [0, 0, 1], [1, 0, 1]], {"dir": [1, 0, 1]})
    assert diag == pytest.approx([0.0, 0.5, 0.5, 1.0])


def test_outward_from_a_point_in_3d():
    pts = [[0.5, 0.5, 0.5], [0.5, 0.5, 1.0], [1.0, 0.5, 0.5], [0.5, 1.0, 0.0]]
    u = u_of(pts, {"preset": "outward", "centre": [0.5, 0.5, 0.5]})
    assert u[0] == pytest.approx(0) and u[1] == pytest.approx(u[2]) and u[3] == pytest.approx(1)  # z counts


def test_mapped_positions_beat_the_polyline():
    pos = [100, 0, 500, 200, 0, 500, 300, 0, 500]  # a straight line along x, but the polyline says up
    model = v2([run(count=3, area="a", pts=[[0.5, 0], [0.5, 1]], positions=pos)])
    r = model.runs[0]
    assert mapping.is_mapped(r) and mapping.run_positions(r) == [(0.1, 0.0, 0.5), (0.2, 0.0, 0.5), (0.3, 0.0, 0.5)]
    u = u_of([[0.5, 0], [0.5, 1]], {"preset": "rise"})
    assert u[0] == 0 and u[-1] == 1
    # the same zone with mapped positions: rising does nothing (all the same height); sweeping along x does
    m2 = v2([run(count=3, area="a", pts=[[0.5, 0], [0.5, 1]], positions=pos)], [zone(mode="spatial", axis={"preset": "rise"})])
    assert plan_of(m2).u == [0.5, 0.5, 0.5]
    m3 = v2([run(count=3, area="a", positions=pos)], [zone(mode="spatial", axis={"preset": "left_right"})])
    assert plan_of(m3).u == pytest.approx([0, 0.5, 1])


def test_positions_are_compact_clamped_and_round_trip():
    n = 904
    pos = [(i * 7) % 1001 for i in range(3 * n)]
    model = v2([run(count=n, area="a", positions=pos)])
    assert list(model.runs[0].positions) == pos
    assert mapping.to_dict(model)["runs"][0]["positions"] == pos
    assert len(__import__("json").dumps(mapping.to_dict(model))) < 20000  # ~ 11 KB for a 904 pixel run
    clamp = v2([run(count=2, area="a", positions=[-5, 1500, 499.6, 0, 0, 0])]).runs[0].positions
    assert clamp == (0, 1000, 500, 0, 0, 0)
    triples = v2([run(count=2, area="a", positions=[[1, 2, 3], [4, 5, 6]])]).runs[0].positions
    assert triples == (1, 2, 3, 4, 5, 6)
    assert "positions" not in mapping.to_dict(v2([run(area="a")]))["runs"][0]  # unmapped runs store none
    assert v2([run(area="a", positions=[])]).runs[0].positions == ()


def test_lenient_load_keeps_a_run_with_damaged_positions():
    data = {"version": 2, "areas": AREAS, "runs": [run(area="a", count=4, positions=[1, 2, 3])]}
    model = mapping.from_dict(data, {"e1": 50}, strict=False)
    assert len(model.runs) == 1 and model.runs[0].positions == ()
    with pytest.raises(MapError):
        mapping.from_dict(data, {"e1": 50})


def test_fit_layout_flat_and_with_z():
    flat = mapping.fit_layout([[0, 0], [10, 0], [10, 20], [0, 20]])
    pts = [tuple(flat[i : i + 3]) for i in range(0, 12, 3)]
    assert pts[0][0] < pts[1][0] and pts[0][1] > pts[3][1] - 1 and pts[0][1] > pts[2][1]  # y flipped to up
    assert all(p[2] == 500 for p in pts)
    deep = mapping.fit_layout([[0, 0, -5], [0, 0, 5], [10, 0, 0]])
    zs = [deep[2], deep[5], deep[8]]
    assert zs[0] < 500 < zs[1] and zs[2] == 500
    assert mapping.fit_layout([]) is None and mapping.fit_layout([["a", 1]]) is None and mapping.fit_layout([[float("inf"), 0]]) is None
    assert all(0 <= v <= 1000 for v in deep)


# --- performance (a Raspberry Pi will be a few times slower) -------------------------------------------
def test_benchmark_3d_prints_ms_per_frame(capsys):
    n = 1404
    pos = [int(500 + 400 * math.sin(i / 40 + k)) for i in range(n) for k in range(3)]
    runs = [run("a", count=904, area="a", positions=pos[: 904 * 3]), run("b", entry="e2", area="b", count=500, positions=pos[904 * 3 :])]
    lines = []
    for effect in ("Steady", "Chase", "Twinkle", "Wave", "Marquee", "Fade", "Sparkle"):
        for axis in ({"preset": "rise"}, {"dir": [1, 1, 1]}, {"preset": "outward", "centre": [0.5, 0.5, 0.5]}):
            model = v2(runs, [zone(runs=("a", "b"), areas=["a", "b"], mode="spatial", effect=effect, pattern="Classic Multicolor", axis=axis)])
            plan = plan_of(model)
            plan.split(plan.render(0))
            start = time.perf_counter()
            for i in range(20):
                plan.split(plan.render(i * 0.04))
            ms = (time.perf_counter() - start) / 20 * 1000
            if effect in ("Chase", "Wave"):
                lines.append(f"3D {effect:8} {next(iter(axis.values()))!s:12.12} {ms:6.2f} ms/frame (N={n})")
            assert ms < 40  # a frame must fit in the 25 fps budget on this machine
    model = v2(runs, [zone(runs=("a", "b"), areas=["a", "b"], mode="spatial", effect="Chase", axis={"dir": [1, 1, 1]})])
    start = time.perf_counter()
    for _ in range(5):
        plan_of(model)
    lines.append(f"3D plan build (u per pixel) {(time.perf_counter() - start) / 5 * 1000:6.2f} ms (N={n})")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
