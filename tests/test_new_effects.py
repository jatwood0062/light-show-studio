"""New effects, palettes and music modes: Palette, Gradient Flow, Plasma, Noise Drift, Storm Flash, Confetti, Dissolve,
Sunrise Ramp, the deterministic Fire, the 3D map effects in map_effects_extra, Beat Scatter / Gravity Drop / Beat Shell.
Every one must be a pure function of t (no hidden state), give n colours of ints 0-255, and survive n = 1 to 904.
Plain pytest, no Home Assistant."""
from __future__ import annotations

import math
import random
import time

import pytest

from test_library import _load

effects = _load("effects")
patterns = _load("patterns")
extra = _load("map_effects_extra")
mapping = _load("mapping")
me = _load("music_engine")

SIZES = (1, 2, 50, 400, 904)
TIMES = (0.0, 0.04, 0.5, 1.37, 3.0, 17.9, 61.0, 600.25)
BASE = (255, 120, 30)
NEW_STRING = ["Gradient Flow", "Plasma", "Noise Drift", "Storm Flash", "Confetti", "Dissolve", "Sunrise Ramp", "Fire"]


def _valid(frame, n):
    assert isinstance(frame, list) and len(frame) == n
    for c in frame:
        assert len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c), c


def _lit(frame, floor=30):
    return sum(1 for c in frame if max(c) > floor)


# --- Palette ----------------------------------------------------------------------------------------------------
def test_palette_endpoints_and_clamp():
    p = effects.Palette([(0.0, (255, 0, 0)), (1.0, (0, 0, 255))], wrap=False)
    assert p.at(0.0) == (255, 0, 0) and p.at(1.0) == (0, 0, 255)
    assert p.at(-5) == (255, 0, 0) and p.at(9) == (0, 0, 255)  # clamped when not wrapping
    mid = p.at(0.5)
    assert 0 < mid[0] < 255 and 0 < mid[2] < 255


def test_palette_wrap_loops_and_closes_the_loop():
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    p = effects.Palette.even(cols)
    assert p.at(0.0) == cols[0] and p.at(1.0) == p.at(0.0) and p.at(2.25) == p.at(0.25)
    for x, want in ((1 / 3, cols[1]), (2 / 3, cols[2])):  # the table has 512 steps, so allow a step of slack
        assert sum(abs(a - b) for a, b in zip(p.at(x), want)) < 40
    # just before the wrap the colour is on its way back to the first stop
    a, b = p.at(0.999), p.at(0.0)
    assert sum(abs(x - y) for x, y in zip(a, b)) < 30


def test_palette_modes_differ_and_oklab_round_trips():
    stops = [(0.0, (255, 0, 0)), (1.0, (0, 255, 0))]
    ok = effects.Palette(stops, "oklab", wrap=False).at(0.5)
    lin = effects.Palette(stops, "linear", wrap=False).at(0.5)
    assert all(abs(x - y) <= 1 for x, y in zip(lin, (127, 127, 0)))
    assert ok != lin
    for c in [(200, 100, 30), (0, 0, 0), (255, 255, 255), (12, 240, 99)]:
        back = effects.oklab_to_rgb(effects.rgb_to_oklab(c))
        assert all(abs(a - b) <= 1 for a, b in zip(c, back))
    with pytest.raises(ValueError):
        effects.Palette([(0, (1, 2, 3))], "hsl")


def test_palette_edge_cases():
    assert effects.Palette([(0.5, (9, 9, 9))]).at(0.3) == (9, 9, 9)
    assert effects.Palette([]).at(0.3) == effects.WHITE
    assert effects.Palette.even([]).at(0.3) == effects.WHITE
    p = effects.Palette([(1.0, (0, 0, 255)), (0.0, (255, 0, 0))], wrap=False)  # given out of order
    assert p.at(0.0) == (255, 0, 0)
    assert effects.palette_for(((1, 2, 3), (4, 5, 6))) is effects.palette_for(((1, 2, 3), (4, 5, 6)))
    for x in (-3.7, 0, 0.123456, 0.99999, 1, 2.5, 1e6):
        _valid([p.at(x), effects.Palette.even([(5, 5, 5), (200, 0, 0)]).at(x)], 2)


def test_noise_is_smooth_bounded_and_deterministic():
    vals = [effects._vnoise(x / 20.0, 3) for x in range(2000)]  # noqa: SLF001
    assert all(0.0 <= v < 1.0 for v in vals)
    assert max(abs(a - b) for a, b in zip(vals, vals[1:])) < 0.2
    assert max(vals) - min(vals) > 0.5
    assert effects._vnoise2(3.3, 4.4, 9) == effects._vnoise2(3.3, 4.4, 9)  # noqa: SLF001
    assert all(0.0 <= effects._vnoise2(x * 0.37, x * 0.11, 5) < 1.0 for x in range(500))  # noqa: SLF001


# --- every new string effect --------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", NEW_STRING)
def test_new_effects_shape_range_and_sizes(name):
    fx = effects.EFFECTS[name]
    for n in SIZES:
        for t in TIMES:
            _valid(fx(t, n, BASE), n)


@pytest.mark.parametrize("name", NEW_STRING)
def test_new_effects_are_pure_functions_of_time(name):
    fx = effects.EFFECTS[name]
    ts = [0.0, 0.9, 3.3, 7.31, 20.0, 99.0, 1000.123]
    forward = [fx(t, 60, BASE) for t in ts]
    # frames drawn in another order, interleaved with other sizes and times, change nothing
    for t in reversed(ts):
        fx(t + 0.01, 17, BASE)
        fx(t, 400, BASE)
    assert [fx(t, 60, BASE) for t in reversed(ts)] == list(reversed(forward))
    # jumping straight to a late time equals getting there frame by frame
    for k in range(40):
        fx(k * 0.05, 60, BASE)
    assert fx(1000.123, 60, BASE) == forward[-1]


@pytest.mark.parametrize("name", NEW_STRING)
def test_new_effects_never_use_the_random_module(name, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("random used")
    for fn in ("random", "randrange", "randint", "choice", "uniform", "shuffle", "sample"):
        monkeypatch.setattr(random, fn, boom)
    effects.EFFECTS[name](3.3, 50, BASE)


@pytest.mark.parametrize("name", NEW_STRING)
def test_new_effects_move(name):
    fx = effects.EFFECTS[name]
    seen = {tuple(fx(t, 60, BASE)) for t in (0.0, 1.1, 2.6, 5.0, 9.7, 20.0, 41.0)}
    assert len(seen) > 2


@pytest.mark.parametrize("name", NEW_STRING)
def test_new_effects_are_fast_enough(name):
    fx = effects.EFFECTS[name]
    fx(0.0, 904, BASE)
    start = time.perf_counter()
    for f in range(25):
        fx(f / 25, 904, BASE)
    assert time.perf_counter() - start < 0.5


def test_fire_keeps_its_look_and_is_stable():
    fx = effects.EFFECTS["Fire"]
    frames = [fx(k * 0.04, 100, BASE) for k in range(100)]
    for f in frames:
        for r, g, b in f:
            assert b == 0 and r >= 130 and g < r  # orange-red, as before (hue 0.02-0.08, value 0.55-1)
    reds = [c[0] for f in frames for c in f]
    assert max(reds) > 240 and min(reds) < 190  # still flickers across the range
    # smooth in time: one frame apart, a bulb never jumps from one end to the other
    jumps = [abs(a[i][0] - b[i][0]) for a, b in zip(frames, frames[1:]) for i in range(100)]
    assert max(jumps) < 120 and sum(j > 60 for j in jumps) < 0.02 * len(jumps)


def test_gradient_flow_scrolls_and_loops():
    pal = effects.palette_for(((255, 0, 0), (0, 255, 0), (0, 0, 255)))
    a = effects.gradient_flow(0.0, 100, pal)
    b = effects.gradient_flow(1.0, 100, pal)
    assert a != b
    loop = 1.0 / 0.07
    c = effects.gradient_flow(loop, 100, pal)
    assert all(sum(abs(x - y) for x, y in zip(p, q)) <= 6 for p, q in zip(a, c))
    # smooth along the string: neighbours are close
    assert max(sum(abs(x - y) for x, y in zip(p, q)) for p, q in zip(a, a[1:])) < 130


def test_plasma_and_noise_drift_are_smooth_and_use_the_palette():
    pal = effects.palette_for(((255, 0, 0), (255, 255, 0)))
    for fn in (effects.plasma, effects.noise_drift):
        f = fn(4.2, 400, pal)
        assert max(sum(abs(x - y) for x, y in zip(p, q)) for p, q in zip(f, f[1:])) < 40
        assert all(c[0] > 200 and c[2] <= 3 for c in f)  # only reds and yellows, nothing else leaks in
        assert len({c[1] for c in f}) > 20  # and it uses the whole range between them


def test_storm_flash_is_dark_with_strikes_that_flicker_and_spread():
    fx = effects.EFFECTS["Storm Flash"]
    n = 100
    peaks = [max(max(c) for c in fx(k * 0.05, n, (200, 200, 200))) for k in range(1200)]  # 60 s
    quiet = sum(p < 30 for p in peaks)
    assert quiet > 0.6 * len(peaks)  # mostly dark
    assert sum(p > 120 for p in peaks) >= 5  # several strikes in a minute
    # a strike lights a stretch of the string, not all of it, and spreads
    k = max(range(len(peaks)), key=peaks.__getitem__)
    lit_at_peak = _lit(fx(k * 0.05, n, (200, 200, 200)), 60)
    assert 1 <= lit_at_peak < n
    # flickers: the brightness goes up and down within one strike
    start = next(i for i, p in enumerate(peaks) if p > 120)
    run = peaks[start:start + 30]
    ups = sum(1 for a, b in zip(run, run[1:]) if b > a + 10)
    assert ups >= 1
    # strikes are at hashed, irregular times
    starts = [i for i in range(1, len(peaks)) if peaks[i] > 120 and peaks[i - 1] <= 120]
    gaps = {b - a for a, b in zip(starts, starts[1:])}
    assert len(gaps) > 3


def test_storm_flash_takes_its_colours_from_the_palette():
    dark, flash = effects.storm_colours([(255, 0, 0), (255, 255, 255)])
    assert max(dark) < 30 and min(flash) > 150
    assert effects.storm_colours([]) == ((3, 5, 14), (225, 232, 255))
    f = effects.storm_flash(0.0, 40, (0, 0, 10), (255, 255, 255), 5)
    assert all(c[0] < 40 for c in f)


def test_confetti_few_at_once_colours_from_the_list_and_fades():
    cols = [(255, 0, 0), (0, 255, 0)]
    n = 100
    counts = []
    seen = set()
    for k in range(300):
        f = effects.confetti(k * 0.1, n, cols)
        counts.append(_lit(f, 40))
        for c in f:
            if max(c) > 100:
                seen.add((c[0] > c[1], c[1] > c[0]))
                assert (c[0] == 0 or c[1] == 0 or abs(c[0] - c[1]) > 0) and c[2] < 40
    assert 1 <= sum(counts) / len(counts) <= 30
    assert seen == {(True, False), (False, True)}  # both colours pop
    # each pop is bright at first and fades away: some bulb goes bright then dim within 2 s
    series = [effects.confetti(k * 0.05, n, cols)[7] for k in range(400)]
    levels = [max(c) for c in series]
    assert max(levels) > 100 and min(levels) < 10
    # backdrop is used when given
    bg = [(10, 20, 30)] * n
    assert any(c == (10, 20, 30) for c in effects.confetti(0.0, n, cols, bg))
    assert effects.confetti(2.2, 5, [])  # no colours: rainbow hues, no error


def test_dissolve_fills_holds_drains():
    base = [(255, 255, 255)] * 200
    fill = [_lit(effects.dissolve(t, 200, base), 128) for t in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)]
    assert fill[0] == 0 and fill == sorted(fill) and fill[-1] == 200
    assert _lit(effects.dissolve(6.0, 200, base), 250) == 200  # held full
    drain = [_lit(effects.dissolve(t, 200, base), 128) for t in (7.0, 8.0, 9.0, 10.0, 11.5)]
    assert drain == sorted(drain, reverse=True) and drain[-1] == 0
    assert _lit(effects.dissolve(11.8, 200, base), 20) == 200 or _lit(effects.dissolve(11.8, 200, base), 20) == 0
    # at the half-way point it is random: neither the first nor the last bulbs only
    half = effects.dissolve(2.5, 200, base)
    on = [i for i, c in enumerate(half) if max(c) > 128]
    assert 40 < len(on) < 160 and min(on) < 50 and max(on) > 150
    # each round fills in a different order
    a = [max(c) > 128 for c in effects.dissolve(2.5, 200, base)]
    b = [max(c) > 128 for c in effects.dissolve(2.5 + 12.0, 200, base)]
    assert a != b
    # it keeps each bulb's own colour
    mixed = [(255, 0, 0), (0, 255, 0)] * 10
    assert effects.dissolve(6.0, 20, mixed) == mixed


def test_sunrise_ramp_warms_up_slowly():
    base = [(255, 200, 120)] * 50
    tot = [sum(sum(c) for c in effects.sunrise_ramp(t, 50, base, 60.0)) for t in (0, 5, 15, 30, 45, 59, 60)]
    assert tot == sorted(tot) and tot[0] < 0.02 * tot[-1] < tot[-1]
    early = effects.sunrise_ramp(12.0, 50, base, 60.0)[0]
    assert early[0] > early[1] > early[2]  # ember first: much more red than green or blue
    assert effects.sunrise_ramp(70.0, 50, base, 60.0, 15.0) == base  # held at the full colours
    first, last = effects.sunrise_ramp(20.0, 50, base, 60.0)[0], effects.sunrise_ramp(20.0, 50, base, 60.0)[-1]
    assert sum(first) > sum(last)  # the glow spreads in from the start
    # loops: dips down then starts again
    period = 60.0 + 15.0 + 4.0
    assert effects.sunrise_ramp(period - 0.1, 50, base, 60.0) != base
    assert effects.sunrise_ramp(period + 3.0, 50, base, 60.0) == effects.sunrise_ramp(3.0, 50, base, 60.0)


def test_effect_list_has_the_new_names_once():
    for name in NEW_STRING:
        assert name in effects.EFFECT_LIST
    assert len(set(effects.EFFECT_LIST)) == len(effects.EFFECT_LIST)


# --- palettes --------------------------------------------------------------------------------------------------------
OLD_PALETTES = {
    "Classic Multicolor": [(255, 0, 0), (0, 255, 0), (0, 60, 255), (255, 80, 0), (255, 0, 120)],
    "Gold & White": [(255, 140, 0), (255, 200, 140)],
    "Pastel": [(255, 120, 170), (120, 200, 255), (255, 220, 90), (150, 255, 140), (190, 130, 255)],
    "Halloween": [(255, 70, 0), (140, 0, 255), (40, 255, 0)],
    "Warm White": [(255, 150, 40)],
    "Cool White": [(200, 220, 255)],
    "Red & Green": [(255, 0, 0), (0, 200, 0)],
    "Red, Green & White": [(255, 0, 0), (0, 200, 0), (255, 200, 140)],
    "Candy Cane": [(255, 0, 0), (255, 255, 255)],
    "Blue & White": [(0, 40, 255), (200, 220, 255)],
    "Red, White & Blue": [(255, 0, 0), (255, 255, 255), (0, 40, 255)],
    "Valentine": [(255, 0, 0), (255, 20, 110), (255, 200, 200)],
}
NEW_PALETTES = ["Diya Lamps", "Lunar Red & Gold", "Menorah Lights", "Autumn Harvest", "Cranberry & Gold",
                "Evergreen & Gold", "Winter Frost", "Romance Wine", "Lucky Green", "Easter Egg", "Spring Blossom",
                "Pride Six", "Cosy Amber", "Deep Ocean", "Aurora Borealis", "Sunset Blaze"]


def test_existing_palettes_keep_their_colours():
    for name, cols in OLD_PALETTES.items():
        assert patterns.PATTERNS[name][0] == cols, name
    assert patterns.PATTERNS["Candy Cane"][1] == 2 and patterns.PATTERNS["Rainbow"][0] == []


def test_classic_multicolor_is_the_classic_multicolour_effect():
    cols = patterns.PATTERNS["Classic Multicolor"][0]
    eff = effects.STILL["Classic Multicolour"](0.0, len(cols) * 2, BASE)
    assert eff == cols * 2
    assert patterns.PATTERNS["Classic Multicolor"][0] is effects.CLASSIC
    assert patterns.PATTERNS["Pastel"][0] is effects.PASTELS


def test_effects_keep_their_own_colours_for_the_names_that_differ():
    assert effects.STILL["Halloween"](0, 2, BASE) == [(255, 80, 0), (140, 0, 255)]
    assert effects.STILL["Gold & White"](0, 2, BASE) == [(255, 170, 20), (255, 244, 225)]
    assert effects.STILL["Easter"](0, 5, BASE) == effects.PASTELS


def test_warm_white_constant():
    assert effects.WARM_WHITE == (255, 176, 100) and patterns.WARM_WHITE == effects.WARM_WHITE
    r, g, b = effects.WARM_WHITE
    assert r > g > b


@pytest.mark.parametrize("name", NEW_PALETTES)
def test_new_palettes(name):
    assert name in patterns.PATTERN_LIST and patterns.canonical(name) == name
    cols, width = patterns.PATTERNS[name]
    assert 3 <= len(cols) <= 6 and width == 1
    assert len(set(cols)) == len(cols)
    for c in cols:
        assert len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c)
    assert name not in OLD_PALETTES
    for effect in ("Steady", "Twinkle", "Chase", "Gradient Flow", "Confetti"):
        _valid(patterns.render(name, effect, 2.5, 40), 40)
    assert patterns.palette_of(name) == cols


def test_pattern_names_are_unique():
    assert len(set(patterns.PATTERN_LIST)) == len(patterns.PATTERN_LIST)
    assert len({n.casefold() for n in patterns.PATTERN_LIST}) == len(patterns.PATTERN_LIST)


# --- pattern effects -------------------------------------------------------------------------------------------------
NEW_PATTERN_FX = ["Gradient Flow", "Plasma", "Noise Drift", "Storm Flash", "Confetti", "Dissolve", "Sunrise Ramp"]


def test_pattern_effects_list():
    for e in NEW_PATTERN_FX:
        assert e in patterns.PATTERN_EFFECTS and e in patterns.ALL_EFFECTS
        assert patterns.valid_effect(e) == e and patterns.light_effect(e) == e and patterns.is_animated(e)
    assert len(set(patterns.PATTERN_EFFECTS)) == len(patterns.PATTERN_EFFECTS)
    assert set(patterns.MAP_EFFECTS).isdisjoint(patterns.PATTERN_EFFECTS)
    assert patterns.PATTERN_EFFECTS[:9] == ["Steady", "Twinkle", "Chase", "Twinkle Swap", "Marquee", "Fade", "Sparkle",
                                            "Alternate", "Wave"]


@pytest.mark.parametrize("effect", NEW_PATTERN_FX)
@pytest.mark.parametrize("pattern", ["Classic Multicolor", "Rainbow", "Warm White", "Candy Cane", "Pride Six"])
def test_pattern_effects_pure_and_valid(pattern, effect):
    for n in SIZES:
        for t in (0.0, 1.37, 33.3):
            f = patterns.render(pattern, effect, t, n, "entry-1")
            _valid(f, n)
            assert f == patterns.render(pattern, effect, t, n, "entry-1")
    a = patterns.render(pattern, effect, 12.5, 50, "x")
    for t in (0.0, 5.5, 99.0):
        patterns.render(pattern, effect, t, 50, "x")
    assert patterns.render(pattern, effect, 12.5, 50, "x") == a


def test_pattern_effects_use_the_patterns_colours():
    for effect in ("Gradient Flow", "Plasma", "Noise Drift"):
        for t in (0.0, 3.1, 8.8):
            for c in patterns.render("Red & Green", effect, t, 100):
                assert c[2] <= 2  # a red-to-green gradient never has blue
    seen = set()
    for k in range(80):
        for c in patterns.render("Red & Green", "Confetti", k * 0.2, 60):
            if max(c) > 150:
                seen.add(c[0] > c[1])
    assert seen == {True, False}
    # lights with different ids don't pop in step
    assert patterns.render("Classic Multicolor", "Confetti", 5.0, 80, "a") != patterns.render(
        "Classic Multicolor", "Confetti", 5.0, 80, "b")
    # Dissolve and Sunrise Ramp show the pattern's own bulb colours when full
    base = patterns.base_colours("Classic Multicolor", 30)
    assert patterns.render("Classic Multicolor", "Dissolve", 6.0, 30) == base
    assert patterns.render("Classic Multicolor", "Sunrise Ramp", 70.0, 30) == base


def test_pattern_effects_work_in_a_zone_with_given_base():
    base = [(10 * i % 256, 200, 50) for i in range(40)]
    for effect in NEW_PATTERN_FX:
        _valid(patterns.render("Classic Multicolor", effect, 7.7, 40, base=base), 40)


def test_map_effect_lists_are_consistent():
    assert patterns.MAP_EFFECTS[-3:] == ["Gradient Axis", "Plasma Field", "Candy Helix"]
    assert patterns.STRING_EQUIVALENT.keys() == set(patterns.MAP_EFFECTS)
    assert all(v in patterns.PATTERN_EFFECTS for v in patterns.STRING_EQUIVALENT.values())
    assert patterns.light_effect("Plasma Field") == "Plasma" and patterns.light_effect("Candy Helix") == "Chase"
    assert set(extra.EXTRA_EFFECTS) == {"Gradient Axis", "Plasma Field", "Candy Helix"}
    assert set(extra.EXTRA_EFFECTS) <= set(patterns.MAP_EFFECTS)


# --- 3D map effects (map_effects_extra) ------------------------------------------------------------------------------
class Group:
    def __init__(self, idx, pts):
        self.idx, self.xs, self.ys, self.zs = idx, [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts]


class Fx:
    """Just what the extra effects read from a MapEffects."""

    def __init__(self, pts, palette=None, u=None, groups=1):
        self.n = len(pts)
        self.palette = palette or [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        ys = [p[1] for p in pts]
        lo, hi = min(ys), max(ys)
        self.u = u or [(y - lo) / ((hi - lo) or 1) for y in ys]
        step = max(1, self.n // groups)
        self.groups = [Group(list(range(s, min(self.n, s + step))), pts[s:s + step]) for s in range(0, self.n, step)]


def tree(n=400, seed=3):
    """A cone of points: y up, radius shrinking with height."""
    rng = random.Random(seed)
    pts = []
    for _ in range(n):
        y = rng.random()
        a = rng.random() * 2 * math.pi
        r = 0.4 * (1 - y) * (0.6 + 0.4 * rng.random())
        pts.append((0.5 + r * math.cos(a), y, 0.5 + r * math.sin(a)))
    return pts


@pytest.mark.parametrize("name", sorted(extra.EXTRA_EFFECTS))
def test_extra_map_effects_valid_pure_and_moving(name):
    fn = extra.EXTRA_EFFECTS[name]
    for n in (1, 2, 50, 400, 904):
        fx = Fx(tree(n) if n > 1 else [(0.5, 0.5, 0.5)])
        for t in TIMES:
            f = fn(fx, t)
            _valid(f, n)
            assert f == fn(fx, t)
    fx = Fx(tree(400))
    frames = [fn(fx, t) for t in (0.0, 1.0, 5.0, 30.0)]
    for t in (7.0, 0.5, 1000.0):
        fn(fx, t)
    assert [fn(fx, t) for t in (0.0, 1.0, 5.0, 30.0)] == frames
    assert len({tuple(f) for f in frames}) > 2


@pytest.mark.parametrize("name", sorted(extra.EXTRA_EFFECTS))
def test_extra_map_effects_are_fast(name):
    fx = Fx(tree(1500), groups=5)
    fn = extra.EXTRA_EFFECTS[name]
    fn(fx, 0.0)
    start = time.perf_counter()
    for k in range(20):
        fn(fx, k * 0.04)
    assert (time.perf_counter() - start) / 20 < 0.03


def test_gradient_axis_follows_u():
    pts = [(0.5, i / 99, 0.5) for i in range(100)]
    fx = Fx(pts, [(255, 0, 0), (0, 0, 255)])
    f = extra.gradient_axis(fx, 0.0)
    assert f[0] == (255, 0, 0)
    assert f[50] != f[0] and f[99] != f[50]
    # by height: lower pixels and higher pixels get different ends of the gradient
    flipped = Fx(pts, [(255, 0, 0), (0, 0, 255)], u=[1 - u for u in fx.u])
    assert extra.gradient_axis(flipped, 0.0)[0] != f[0]


def test_plasma_field_is_spatially_smooth_and_uses_the_palette():
    pts = [(i / 399, 0.5, 0.5) for i in range(400)]
    f = extra.plasma_field(Fx(pts, [(255, 0, 0), (255, 255, 0)]), 2.0)
    assert max(sum(abs(a - b) for a, b in zip(p, q)) for p, q in zip(f, f[1:])) < 40
    assert all(c[0] > 200 and c[2] <= 3 for c in f) and len({c[1] for c in f}) > 20
    # it is 3D: moving in z alone changes the colour
    col = [(0.5, 0.5, k / 99) for k in range(100)]
    assert len(set(extra.plasma_field(Fx(col), 1.0))) > 5


def test_candy_helix_stripes_spiral_round_and_up():
    fx = Fx(tree(2000), [(255, 0, 0), (255, 255, 255)])
    f = extra.candy_helix(fx, 0.0)
    assert set(f) <= {(255, 0, 0), (255, 255, 255)} | {c for c in f}  # only blends of the two
    hard = [c for c in f if c in ((255, 0, 0), (255, 255, 255))]
    assert len(hard) > 0.8 * len(f)  # mostly solid stripes
    reds = sum(1 for c in f if c == (255, 0, 0))
    assert 0.3 < reds / len(f) < 0.7  # about half and half
    # same angle, different heights -> the stripe has moved round (it is a spiral, not vertical bars)
    ring = [(0.5 + 0.2, y / 59, 0.5) for y in range(60)]  # one vertical line at angle 0
    line = extra.candy_helix(Fx(ring + [(0.3, 0.0, 0.5), (0.7, 1.0, 0.5)]), 0.0)[:60]
    assert len(set(line)) > 1
    # it turns with time
    assert extra.candy_helix(fx, 0.0) != extra.candy_helix(fx, 2.0)
    # the angle seam is invisible: just either side of angle +-pi the colour is the same
    seam = Fx([(0.5 - 0.3, 0.5, 0.5 + 1e-4), (0.5 - 0.3, 0.5, 0.5 - 1e-4), (0.5 + 0.3, 0.2, 0.5), (0.5 - 0.3, 0.9, 0.5)])
    a = extra.candy_helix(seam, 0.0)
    assert a[0] == a[1]


def test_candy_helix_defaults_to_red_and_white():
    one = Fx(tree(300), [(0, 200, 0)])
    assert set(extra.candy_helix(one, 1.0)) <= {(255, 0, 0), (255, 255, 255)} | set(extra.candy_helix(one, 1.0))
    cols = {c for c in extra.candy_helix(one, 1.0) if max(c) in (255,)}
    assert (255, 0, 0) in cols and (255, 255, 255) in cols
    # with a one-colour palette repeated, still two stripes
    dup = Fx(tree(300), [(0, 200, 0), (0, 200, 0)])
    assert (255, 0, 0) in set(extra.candy_helix(dup, 1.0))
    # and with three colours it uses all three
    three = extra.candy_helix(Fx(tree(600)), 0.0)
    assert {(255, 0, 0), (0, 255, 0), (0, 0, 255)} <= set(three)


# --- music: Beat Scatter, Gravity Drop, Beat Shell -------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def feat(level=0.5, bass=0.5, mid=0.3, treble=0.2, beat=False):
    return me.Features(level, bass, mid, treble, beat)


def state_with_beat(beats=(1.0,), now=None):
    """A MusicState that heard beats at the given times, advanced to `now` (default: just after the last)."""
    st = me.MusicState(50, 0.0)
    for b in beats:
        st.advance(b)
        st.feed(b, feat(beat=True))
    st.advance(now if now is not None else beats[-1])
    return st


def music_args(n, effect_heights=None):
    base = [(200, 100, 50)] * n
    bands = me._bands([i / max(1, n - 1) for i in range(n)])  # noqa: SLF001
    return base, base, list(range(n)), bands, 1


def lum(frame):
    return [max(c) for c in frame]


def test_music_modes_are_listed():
    assert me.EFFECTS[-3:] == ("Beat Scatter", "Gravity Drop", "Beat Shell")
    assert len(set(me.EFFECTS)) == len(me.EFFECTS)


@pytest.mark.parametrize("effect", ["Beat Scatter", "Gravity Drop", "Beat Shell"])
def test_music_modes_valid_pure_before_and_after_beats(effect):
    for n in SIZES:
        args = music_args(n)
        for st in (me.MusicState(50, 0.0), state_with_beat((1.0,)), state_with_beat((1.0, 1.2), 1.35),
                   state_with_beat((1.0,), 1.9), state_with_beat((1.0,), 400.0)):
            f = me.render_music(effect, st, *args)
            _valid(f, n)
            assert f == me.render_music(effect, st, *args)


def test_beat_state_tracks_age_and_gap():
    st = state_with_beat((1.0, 1.4), 1.9)
    assert st.beats == 2 and st.beat_age == pytest.approx(0.5) and st.prev_gap == pytest.approx(0.4)
    st.advance(30.0)
    assert st.beat_age == pytest.approx(28.6)
    st.advance(500.0)
    assert st.beat_age <= 60.0


def test_beat_scatter_flashes_a_subset_then_decays():
    n = 400
    args = music_args(n)
    rest = lum(me.render_music("Beat Scatter", me.MusicState(50, 0.0), *args))
    assert len(set(rest)) == 1
    hit = lum(me.render_music("Beat Scatter", state_with_beat((1.0,), 1.0), *args))
    flashed = sum(1 for v in hit if v > rest[0] + 40)
    assert 0.15 * n < flashed < 0.45 * n  # a share of the bulbs, not all
    assert max(hit) > 200
    later = lum(me.render_music("Beat Scatter", state_with_beat((1.0,), 1.3), *args))
    assert max(later) < max(hit) and sum(later) < sum(hit)
    gone = lum(me.render_music("Beat Scatter", state_with_beat((1.0,), 4.0), *args))
    assert max(gone) - min(gone) <= 2  # all back to the resting glow
    # a different beat flashes different bulbs
    second = lum(me.render_music("Beat Scatter", state_with_beat((1.0, 2.0), 2.0), *args))
    a = {i for i, v in enumerate(hit) if v > rest[0] + 40}
    b = {i for i, v in enumerate(second) if v > rest[0] + 40}
    assert a != b and len(a & b) < 0.6 * len(a)
    # quick beats overlap smoothly: the previous handful is still fading
    both = lum(me.render_music("Beat Scatter", state_with_beat((1.0, 1.15), 1.15), *args))
    assert sum(1 for v in both if v > rest[0] + 40) > flashed


def test_drop_height_falls_lands_and_bounces():
    h = me._drop_height  # noqa: SLF001
    assert h(0.0) == pytest.approx(1.0)
    falling = [h(k * 0.05) for k in range(0, 9)]
    assert falling == sorted(falling, reverse=True) and h(me.DROP_FALL) == pytest.approx(0.0, abs=1e-9)
    # accelerates: the first half of the time covers less than the second half
    assert 1 - h(me.DROP_FALL / 2) < h(me.DROP_FALL / 2)
    after = [h(me.DROP_FALL + k * 0.02) for k in range(0, 60)]
    assert max(after) > 0.15  # the bounce
    peak = max(range(len(after)), key=after.__getitem__)
    assert 0 < peak < len(after) - 1 and all(0.0 <= v <= 1.0 for v in after)
    second = [v for v in after[peak + 1:]]
    assert second.index(min(second)) >= 0
    assert h(5.0) == 0.0 and h(1e6) == 0.0  # settled


def test_gravity_drop_band_falls_with_the_beat():
    n = 200
    base = [(200, 100, 50)] * n
    bands = me._bands([i / (n - 1) for i in range(n)])  # noqa: SLF001
    heights = [i / (n - 1) for i in range(n)]

    def peak_at(age):
        st = state_with_beat((1.0,), 1.0 + age)
        f = lum(me.render_music("Gravity Drop", st, base, base, list(range(n)), bands, 1, heights))
        return max(range(n), key=f.__getitem__) / (n - 1), f

    tops = [peak_at(a)[0] for a in (0.02, 0.15, 0.3, 0.4)]
    assert tops == sorted(tops, reverse=True) and tops[0] > 0.85 and tops[-1] < 0.5
    landed, f = peak_at(0.47)
    assert landed < 0.2  # at the bottom, after landing
    bounce, _ = peak_at(0.45 + 0.2)
    assert bounce > landed  # and up again
    # between beats (long after) the band has settled and faded: just the resting glow
    _, quiet = peak_at(5.0)
    assert max(quiet) - min(quiet) <= 5
    # uses heights when given: reversed heights put the band at the other end
    flip = [1 - h for h in heights]
    st = state_with_beat((1.0,), 1.02)
    g = lum(me.render_music("Gravity Drop", st, base, base, list(range(n)), bands, 1, flip))
    assert max(range(n), key=g.__getitem__) < n * 0.2


def test_beat_shell_opens_from_the_centre():
    n = 300
    base = [(200, 100, 50)] * n
    bands = me._bands([i / (n - 1) for i in range(n)])  # noqa: SLF001
    dist = [i / (n - 1) for i in range(n)]  # pixel 0 is the centre, pixel n-1 the edge

    def front(age):
        st = state_with_beat((1.0,), 1.0 + age)
        f = lum(me.render_music("Beat Shell", st, base, base, list(range(n)), bands, 1, None, dist))
        return max(range(n), key=f.__getitem__) / (n - 1)

    fronts = [front(a) for a in (0.05, 0.2, 0.4, 0.7)]
    assert fronts == sorted(fronts) and fronts[0] < 0.3 and fronts[-1] > 0.7
    quiet = lum(me.render_music("Beat Shell", state_with_beat((1.0,), 20.0), base, base, list(range(n)), bands, 1, None, dist))
    assert max(quiet) - min(quiet) <= 2
    # without distances it opens from the middle of the string
    st = state_with_beat((1.0,), 1.1)
    f = lum(me.render_music("Beat Shell", st, base, base, list(range(n)), bands, 1))
    mid = max(range(n), key=f.__getitem__)
    assert abs(mid - n / 2) > n * 0.05 or f[0] == f[-1]
    assert abs(f[0] - f[-1]) <= 2  # symmetric about the middle


def _zone(runs, mode="string", pattern="Warm White", axis=None):
    return mapping.Zone("music:t", "Music", tuple(runs), mode, mapping.parse_axis(axis or {}), pattern, "Steady", 50)


def _run(rid, pts, area="a"):
    flat = tuple(int(round(v * 1000)) for p in pts for v in p)
    return mapping.Run(rid, "e1", rid, 0, len(pts), area=area, positions=flat)


def _music_plan(effect, pts, mode="spatial", axis=None, clock=None):
    runs = {"r1": _run("r1", pts)}
    return me.make_plan(_zone(runs, mode, axis=axis), runs, effect, 50, None, clock or Clock())


@pytest.mark.parametrize("effect", ["Beat Scatter", "Gravity Drop", "Beat Shell"])
@pytest.mark.parametrize("mode", ["string", "spatial"])
def test_music_plans_run_the_new_modes(effect, mode):
    pts = tree(120)
    c = Clock()
    plan = _music_plan(effect, pts, mode, clock=c)
    assert len(plan.heights) == len(plan.dist) == 120
    assert all(0.0 <= d <= 1.0 for d in plan.dist) and all(0.0 <= h <= 1.0 for h in plan.heights)
    for i in range(60):
        c.t += 0.025
        plan.feed(feat(0.6, 0.7, beat=(i % 12 == 0)))
        f = plan.render(0.0)
        _valid(f, 120)
    assert plan.live and len({tuple(plan.render(0.0))}) == 1


def test_beat_shell_reaches_map_pixels_from_the_area_centre():
    pts = [(x / 10, y / 10, z / 10) for x in range(11) for y in range(11) for z in range(11)]
    plan = _music_plan("Beat Shell", pts, "spatial")
    centre = min(range(len(pts)), key=lambda i: sum((v - 0.5) ** 2 for v in pts[i]))
    corner = pts.index((1.0, 1.0, 1.0))
    assert plan.dist[centre] < 0.05 and plan.dist[corner] > 0.95
    # an explicit centre point moves the shell's origin
    plan2 = _music_plan("Beat Shell", pts, "spatial", axis={"centre": [0.0, 0.0, 0.0]})
    assert plan2.dist[pts.index((0.0, 0.0, 0.0))] == 0.0 and plan2.dist[corner] == 1.0
    # string mode ignores positions
    plan3 = _music_plan("Beat Shell", pts[:21], "string")
    assert plan3.dist[10] == 0.0 and plan3.dist[0] == 1.0 and plan3.dist[20] == 1.0
