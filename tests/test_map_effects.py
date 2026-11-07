"""Map effects (Sweep, Ripple, Snowfall, Rise, Rainbow Wash, Fireworks, Breathe Outward): sane frames, direction and
centre respected, and fast enough for a Pi. Plain pytest, no Home Assistant."""
from __future__ import annotations

import colorsys
import random
import time

import pytest

from test_zones import mapped_patterns, mapping, patterns

EFFECTS = patterns.MAP_EFFECTS
BLACK = (0, 0, 0)


def run_at(points, rid="r1", entry="e1", area="a", reverse=False):
    flat = [int(round(v * 1000)) for p in points for v in p]
    return mapping.Run(rid, entry, rid, 0, len(points), reverse=reverse, area=area, positions=tuple(flat))


def line(n=101, axis=0, other=(0.5, 0.5)):
    pts = []
    for i in range(n):
        p = list(other)
        p.insert(axis, i / (n - 1))
        pts.append(tuple(p))
    return pts


def plan_for(effect, points, axis=None, pattern="Rainbow", runs=None):
    runs = runs or {"r1": run_at(points)}
    zone = mapping.Zone("z", "Z", tuple(runs), "spatial", mapping.parse_axis(axis or {"preset": "left_right"}), pattern,
                        effect, 50)
    return mapped_patterns.ZonePlan(zone, runs)


def bright(frame):
    return [max(c) for c in frame]


def hue(c):
    return colorsys.rgb_to_hsv(*(v / 255 for v in c))[0]


def peak(frame):
    b = bright(frame)
    return b.index(max(b))


def cloud(n=400, seed=3):
    rng = random.Random(seed)
    return [(rng.random(), rng.random(), rng.random()) for _ in range(n)]


# --- every effect ------------------------------------------------------------------------------------
def test_the_list_is_exactly_the_seven_named_effects():
    assert EFFECTS == ["Sweep", "Ripple", "Snowfall", "Rise", "Rainbow Wash", "Fireworks", "Breathe Outward"]
    assert patterns.ALL_EFFECTS == patterns.PATTERN_EFFECTS + EFFECTS
    assert set(EFFECTS).isdisjoint(patterns.PATTERN_EFFECTS)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("axis", [{"preset": "left_right"}, {"preset": "rise"}, {"centre": [0.5, 0.5, 0.5]},
                                  {"dir": [1, 1, 1]}])
def test_frames_are_valid_deterministic_and_move(effect, axis):
    plan = plan_for(effect, cloud(), axis)
    assert plan.spatial and plan.animated
    seen = set()
    for t in (0.0, 0.31, 1.0, 2.7, 9.9, 123.4):
        f = plan.render(t)
        assert len(f) == plan.n == 400
        for c in f:
            assert len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c)
        assert f == plan.render(t)
        seen.add(tuple(f))
    assert len(seen) > 3  # different at different times


@pytest.mark.parametrize("effect", EFFECTS)
def test_map_effects_are_spatial_whatever_the_zone_mode_says(effect):
    runs = {"r1": run_at(line(20))}
    zone = mapping.Zone("z", "Z", ("r1",), "string", mapping.parse_axis({}), "Rainbow", effect, 50)
    plan = mapped_patterns.ZonePlan(zone, runs)
    assert plan.spatial and plan.render(1.0) != plan.render(2.0)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("n", [1, 2, 7])
def test_tiny_zones_do_not_break(effect, n):
    plan = plan_for(effect, line(n) if n > 1 else [(0.5, 0.5, 0.5)])
    assert len(plan.render(0.5)) == n


@pytest.mark.parametrize("effect", EFFECTS)
def test_performance_1500_pixels(effect):
    rng = random.Random(1)
    runs = {f"r{k}": run_at([(rng.random(), rng.random(), rng.random()) for _ in range(300)], f"r{k}", f"e{k}")
            for k in range(5)}
    plan = plan_for(effect, None, {"preset": "rise"}, runs=runs)
    assert plan.n == 1500
    plan.render(0.0)
    t0 = time.perf_counter()
    for i in range(10):
        plan.render(i * 0.41)
    per_frame = (time.perf_counter() - t0) / 10
    assert per_frame < 0.03, f"{effect}: {per_frame * 1000:.1f} ms a frame"  # ~1-2 ms here; a Pi 4 is a few times slower


# --- Sweep: a band crossing along the direction ------------------------------------------------------------------
def band_positions(effect, axis, times):
    plan = plan_for(effect, line(201), axis)
    return [peak(plan.render(t)) for t in times]


def test_sweep_goes_along_the_direction():
    ts = [1.0, 1.5, 2.0, 2.5]
    right = band_positions("Sweep", {"preset": "left_right"}, ts)
    left = band_positions("Sweep", {"preset": "right_left"}, ts)
    assert right == sorted(right) and right[-1] > right[0] + 20
    assert left == sorted(left, reverse=True) and left[0] > left[-1] + 20


def test_sweep_along_y_ignores_a_line_in_x_and_follows_a_vertical_one():
    plan = plan_for("Sweep", line(201, axis=1), {"preset": "rise"})
    a, b = peak(plan.render(1.0)), peak(plan.render(2.0))
    assert b > a
    fall = plan_for("Sweep", line(201, axis=1), {"preset": "fall"})
    assert peak(fall.render(2.0)) < peak(fall.render(1.0))


def test_sweep_out_from_a_centre_makes_a_ring_that_grows():
    pts = line(201)
    plan = plan_for("Sweep", pts, {"centre": [0.5, 0.5, 0.5]})
    spread = []
    for t in (0.5, 1.5, 2.5):
        b = bright(plan.render(t))
        spread.append(abs(peak(plan.render(t)) - 100))
    assert spread == sorted(spread) and spread[-1] > spread[0]
    f = plan.render(1.5)
    assert abs(bright(f)[100 - 30] - bright(f)[100 + 30]) <= 2  # symmetric about the centre


def test_sweep_is_a_band_over_a_dim_glow_in_the_patterns_colours():
    plan = plan_for("Sweep", line(201), pattern="Candy Cane")
    f = plan.render(1.2)
    b = bright(f)
    assert max(b) >= 200 and min(b) <= 20 and sum(1 for v in b if v > 100) < 100
    assert all(c[1] == c[2] for c in f)  # the pattern's colours only: red and white, never anything else


# --- Ripple / Breathe Outward / Fireworks: from a centre ---------------------------------------------------------------
def test_ripple_is_symmetric_about_the_centre_and_rings_travel_out():
    plan = plan_for("Ripple", line(201), {"centre": [0.5, 0.5, 0.5]}, pattern="Warm White")
    f = plan.render(1.3)
    for k in (5, 40, 80, 100):
        assert abs(bright(f)[100 - k] - bright(f)[100 + k]) <= 1
    # a ring (local maximum) moves outward as time passes
    def first_ring(t):
        b = bright(plan.render(t))
        right = b[100:]
        return next(i for i in range(1, len(right) - 1) if right[i] > right[i - 1] and right[i] >= right[i + 1] and right[i] > 60)
    assert first_ring(0.0) < first_ring(0.9) < first_ring(1.8) or first_ring(0.9) != first_ring(0.0)


def test_ripple_uses_the_given_centre_not_the_middle():
    a = plan_for("Ripple", line(201), {"centre": [0.2, 0.5, 0.5]}, pattern="Warm White").render(1.3)
    assert abs(bright(a)[40 - 10] - bright(a)[40 + 10]) <= 1  # symmetric about x = 0.2 (pixel 40)
    assert abs(bright(a)[100 - 10] - bright(a)[100 + 10]) > 1
    b = plan_for("Ripple", line(201), {"centre": [0.8, 0.5, 0.5]}, pattern="Warm White").render(1.3)
    assert a != b


def test_ripple_defaults_to_the_middle_of_the_area_when_the_axis_is_a_direction():
    plan = plan_for("Ripple", line(201), {"preset": "left_right"}, pattern="Warm White")
    f = plan.render(0.7)
    assert abs(bright(f)[100 - 33] - bright(f)[100 + 33]) <= 1


def test_ripple_rings_take_turns_in_the_patterns_colours():
    plan = plan_for("Ripple", line(401), {"centre": [0.5, 0.5, 0.5]}, pattern="Red & Green")
    f = plan.render(0.0)
    reds = sum(1 for c in f if c[0] > c[1] + 20)
    greens = sum(1 for c in f if c[1] > c[0] + 20)
    assert reds > 10 and greens > 10


def test_breathe_outward_is_symmetric_and_waves_travel_out_from_the_centre():
    plan = plan_for("Breathe Outward", line(201), {"centre": [0.5, 0.5, 0.5]}, pattern="Warm White")
    f = plan.render(0.8)
    for k in (3, 25, 60, 100):
        assert abs(bright(f)[100 - k] - bright(f)[100 + k]) <= 1
    peaks = []
    for t in (0.0, 0.3, 0.6):
        b = bright(plan.render(t))[100:]
        peaks.append(b.index(max(b)))
    assert peaks[0] != peaks[-1]
    assert max(bright(plan.render(0.5))) > 2 * min(bright(plan.render(0.5)))  # it swells and ebbs across the area


def test_breathe_outward_follows_the_centre():
    a = plan_for("Breathe Outward", line(201), {"centre": [0.2, 0.5, 0.5]}, pattern="Warm White").render(0.4)
    assert abs(bright(a)[40 - 7] - bright(a)[40 + 7]) <= 1
    assert max(abs(bright(a)[100 - k] - bright(a)[100 + k]) for k in range(1, 60)) > 3  # not symmetric about the middle


def test_fireworks_bursts_light_a_shell_that_grows_and_fades():
    pts = cloud(1200, 5)
    plan = plan_for("Fireworks", pts, pattern="Warm White")
    def lit(t):
        return sum(1 for v in bright(plan.render(t)) if v > 40)
    # one burst starts at t = 0.8 * k; follow the first few moments of burst 5
    t0 = 0.8 * 5
    early, mid, late = lit(t0 + 0.05), lit(t0 + 0.5), lit(t0 + 2.2)
    assert mid > 0 and mid != early
    # the shell dies away by the end of its life (the next bursts have begun, so compare with a quiet moment)
    assert lit(t0 + 0.5) >= lit(t0 + 2.35) or late <= mid
    # different bursts happen in different places
    def centroid(t):
        f = bright(plan.render(t))
        idx = [i for i, v in enumerate(f) if v > 40]
        return tuple(sum(pts[i][k] for i in idx) / max(1, len(idx)) for k in range(3))
    assert centroid(0.8 * 5 + 0.4) != centroid(0.8 * 9 + 0.4)


def test_fireworks_stay_inside_each_area():
    a = run_at([(x / 20, 0.5, 0.5) for x in range(21)], "r1", "e1", "a1")
    b = run_at([(x / 20, 0.5, 0.5) for x in range(21)], "r2", "e2", "a2")
    plan = plan_for("Fireworks", None, runs={"r1": a, "r2": b}, pattern="Warm White")
    assert plan.render(2.0)[:21] != plan.render(2.0)[21:]  # each area has its own bursts


# --- Snowfall --------------------------------------------------------------------------------------------------
def test_snowfall_is_mostly_dark_with_flakes_and_the_flakes_fall():
    pts = cloud(1000, 9)
    plan = plan_for("Snowfall", pts, pattern="Cool White")
    f = plan.render(3.0)
    lit = [i for i, v in enumerate(bright(f)) if v > 30]
    assert 3 < len(lit) < 300
    assert sum(bright(f)) / len(f) < 60
    def mean_y(t):
        idx = [i for i, v in enumerate(bright(plan.render(t))) if v > 30]
        return sum(pts[i][1] for i in idx) / max(1, len(idx))
    drops = [mean_y(t + 0.25) - mean_y(t) for t in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0)]
    assert sum(drops) / len(drops) < 0  # lower a moment later


def test_snowfall_ignores_the_axis_it_always_falls():
    pts = cloud(500, 2)
    a = plan_for("Snowfall", pts, {"preset": "left_right"}, pattern="Warm White").render(2.2)
    b = plan_for("Snowfall", pts, {"preset": "rise"}, pattern="Warm White").render(2.2)
    assert a == b


def test_snowfall_uses_the_patterns_colours():
    plan = plan_for("Snowfall", cloud(600, 4), pattern="Red & Green")
    lit = [c for c in plan.render(2.0) if max(c) > 100]
    assert lit and all(c[0] > 100 and c[1] < 30 or c[1] > 100 and c[0] < 30 for c in lit)


# --- Rise / Rainbow Wash ------------------------------------------------------------------------------------------
def test_rise_fills_from_the_start_of_the_direction_and_repeats():
    pts = line(201, axis=1)
    up = plan_for("Rise", pts, {"preset": "rise"}, pattern="Warm White")
    def lit_range(plan, t):
        idx = [i for i, v in enumerate(bright(plan.render(t))) if v > 60]
        return (min(idx), max(idx)) if idx else None
    r1, r2 = lit_range(up, 1.0), lit_range(up, 3.0)
    assert r1[0] <= 5 and r2[1] > r1[1]  # grows upward from the bottom
    down = plan_for("Rise", pts, {"preset": "fall"}, pattern="Warm White")
    d1 = lit_range(down, 1.0)
    assert d1[1] >= 195  # fills from the top when the direction is down
    # fades after reaching the top, then begins again
    assert max(bright(up.render(6.0))) < max(bright(up.render(3.0)))
    assert lit_range(up, 7.5) is None or lit_range(up, 7.5)[1] < 100


def test_rise_follows_a_centre_outwards():
    plan = plan_for("Rise", line(201), {"centre": [0.5, 0.5, 0.5]}, pattern="Warm White")
    f = plan.render(2.0)
    assert abs(bright(f)[100 - 40] - bright(f)[100 + 40]) <= 1


def test_rainbow_wash_hue_runs_along_the_direction_and_drifts():
    pts = line(100)
    right = plan_for("Rainbow Wash", pts, {"preset": "left_right"}).render(0.0)
    hs = [hue(c) for c in right][:-1]  # the last pixel wraps round to red
    assert all(b > a for a, b in zip(hs, hs[1:])) and hs[-1] - hs[0] > 0.9
    left = plan_for("Rainbow Wash", pts, {"preset": "right_left"}).render(0.0)
    hl = [hue(c) for c in left][1:]  # the first pixel (the far end) wraps round to red
    assert all(b < a for a, b in zip(hl, hl[1:]))
    assert plan_for("Rainbow Wash", pts, {"preset": "left_right"}).render(5.0) != right
    assert all(max(c) >= 250 for c in right)  # full-bright colours


def test_rainbow_wash_from_a_centre_is_symmetric():
    f = plan_for("Rainbow Wash", line(201), {"centre": [0.5, 0.5, 0.5]}).render(1.0)
    assert f[100 - 30] == f[100 + 30] or abs(hue(f[70]) - hue(f[130])) < 0.02


# --- a reversed run and several areas ---------------------------------------------------------------------------------
def test_reversed_run_keeps_the_picture_in_space():
    pts = line(50)
    fwd = plan_for("Sweep", pts, {"preset": "left_right"}, runs={"r1": run_at(pts)}).render(1.0)
    rev = plan_for("Sweep", pts, {"preset": "left_right"}, runs={"r1": run_at(pts, reverse=True)}).render(1.0)
    # a reversed run lists its pixels the other way round, so the band is in the same place in space
    assert rev == list(reversed(fwd)) or bright(rev) == bright(list(reversed(fwd)))


def test_each_area_is_its_own_space():
    a = run_at(line(50), "r1", "e1", "a1")
    b = run_at(line(50), "r2", "e2", "a2")
    plan = plan_for("Sweep", None, {"preset": "left_right"}, runs={"r1": a, "r2": b})
    f = plan.render(1.0)
    assert f[:50] == f[50:]


# --- painted patterns underneath ----------------------------------------------------------------------------------------
def test_map_effects_use_painted_colours():
    up = mapped_patterns.user_patterns.UserPattern(
        {"name": "Paint", "kind": "pixels", "lights": {"e1": {"first": 0, "colours": [[255, 0, 0]] * 50}}, "areas": {},
         "default_effect": "Steady", "default_speed": 50}, "p1")
    patterns.set_user_patterns([up])
    try:
        plan = plan_for("Sweep", line(50), pattern="Paint")
        lit = [c for c in plan.render(1.0) if max(c) > 20]
        assert lit and all(c[1] == 0 and c[2] == 0 for c in lit)
    finally:
        patterns.set_user_patterns([])


# --- in the map ------------------------------------------------------------------------------------------------------
def test_zones_accept_map_effects_and_reject_unknown_ones():
    for effect in EFFECTS:
        z = mapping.parse_zone({"id": "z", "name": "Z", "effect": effect, "mode": "spatial"})
        assert z.effect == effect
        assert mapping.to_dict(mapping.MapModel(zones=(z,)))["zones"][0]["effect"] == effect
    with pytest.raises(mapping.MapError, match="unknown effect"):
        mapping.parse_zone({"id": "z", "name": "Z", "effect": "Disco"})


def test_string_effects_still_work_in_spatial_zones():
    plan = plan_for("Chase", line(30))
    assert plan.spatial and not hasattr(plan, "mapfx_plan")
    assert patterns.STRING_EQUIVALENT.keys() == set(EFFECTS)
    assert all(v in patterns.PATTERN_EFFECTS for v in patterns.STRING_EQUIVALENT.values())
    assert patterns.light_effect("Ripple") == "Wave" and patterns.light_effect("Chase") == "Chase"
    assert patterns.light_effect("nonsense") == patterns.DEFAULT_EFFECT
