"""Tests for the second batch of effects (Bouncing Balls, Ripples, Pulse Wave, Ember Glow, Rainbow Comet, Stack,
Carousel), the pattern effects Bounce, Carousel and Scanner, and the helpers that draw a whole string in one pass.
Plain pytest, no Home Assistant needed."""
from __future__ import annotations

import random
import time

import pytest

from test_library import _load

effects = _load("effects")
patterns = _load("patterns")
transitions = _load("transitions")

SIZES = (1, 2, 3, 50, 400, 904)
TIMES = (0.0, 0.04, 0.5, 1.37, 3.0, 17.9, 61.0, 600.25)
BASE = (255, 120, 30)
MORE_STRING = ["Bouncing Balls", "Ripples", "Pulse Wave", "Ember Glow", "Rainbow Comet", "Stack", "Carousel"]
MORE_PATTERN_FX = ["Bounce", "Carousel", "Scanner"]


def _valid(frame, n):
    assert isinstance(frame, list) and len(frame) == n
    for c in frame:
        assert isinstance(c, tuple) and len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c), c


def _level(frame):
    return sum(sum(c) for c in frame) / max(1, len(frame))


# --- the string effects -----------------------------------------------------------------------------------------
def test_the_new_names_are_listed_once_and_animated():
    for name in MORE_STRING:
        assert name in effects.ANIMATED and name in effects.EFFECT_LIST and name not in effects.STILL
    assert len(set(effects.EFFECT_LIST)) == len(effects.EFFECT_LIST)
    assert effects.EFFECT_LIST[-len(MORE_STRING):] == MORE_STRING


@pytest.mark.parametrize("name", MORE_STRING)
def test_shape_range_and_sizes(name):
    fx = effects.EFFECTS[name]
    for n in SIZES:
        for t in TIMES:
            _valid(fx(t, n, BASE), n)
    assert fx(1.0, 0, BASE) == []
    _valid(fx(2.5, 50, (0, 0, 0)), 50)
    _valid(fx(2.5, 50, (255, 255, 255)), 50)


@pytest.mark.parametrize("name", MORE_STRING)
def test_pure_functions_of_time(name):
    fx = effects.EFFECTS[name]
    ts = [0.0, 0.9, 3.3, 7.31, 20.0, 99.0, 1000.123]
    forward = [fx(t, 60, BASE) for t in ts]
    for t in reversed(ts):  # other sizes and times in between change nothing
        fx(t + 0.01, 17, BASE)
        fx(t, 400, BASE)
    assert [fx(t, 60, BASE) for t in reversed(ts)] == list(reversed(forward))
    for k in range(40):
        fx(k * 0.05, 60, BASE)
    assert fx(1000.123, 60, BASE) == forward[-1]


@pytest.mark.parametrize("name", MORE_STRING)
def test_never_use_the_random_module(name, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("random used")
    for fn in ("random", "randrange", "randint", "choice", "uniform", "shuffle", "sample"):
        monkeypatch.setattr(random, fn, boom)
    effects.EFFECTS[name](3.3, 50, BASE)


@pytest.mark.parametrize("name", MORE_STRING)
def test_they_move_and_light_something(name):
    fx = effects.EFFECTS[name]
    frames = [fx(t, 60, BASE) for t in (0.3, 1.1, 2.6, 5.0, 9.7, 20.0, 41.0)]
    assert len({tuple(f) for f in frames}) > 3
    assert max(max(max(c) for c in f) for f in frames) > 150


@pytest.mark.parametrize("name", MORE_STRING)
def test_fast_enough(name):
    fx = effects.EFFECTS[name]
    fx(0.0, 904, BASE)
    start = time.perf_counter()
    for f in range(25):
        fx(f / 25, 904, BASE)
    assert time.perf_counter() - start < 0.25


def _reversals(series, swing):
    """How many times a series turns round after moving at least `swing` (small wobbles don't count)."""
    turns, ref, way = 0, series[0], 0
    for v in series[1:]:
        if way >= 0 and v < ref - swing:
            turns += way > 0
            way, ref = -1, v
        elif way <= 0 and v > ref + swing:
            turns += way < 0
            way, ref = 1, v
        elif (way > 0 and v > ref) or (way < 0 and v < ref):
            ref = v
    return turns


@pytest.mark.parametrize("name", MORE_STRING)
@pytest.mark.parametrize("n", [50, 904])
def test_no_strobing(name, n):
    """Photosensitivity: neither the whole string nor a single bulb flashes faster than 3 times a second (a flash
    is two turns, so at most 6 turns a second), and a frame never jumps by most of the full brightness at once."""
    fx = effects.EFFECTS[name]
    secs, fps = 12, 50
    frames = [fx(k / fps, n, BASE) for k in range(secs * fps)]
    whole = [_level(f) for f in frames]
    assert _reversals(whole, 765 * 0.1) <= 6 * secs
    assert max(abs(a - b) for a, b in zip(whole, whole[1:])) < 765 * 0.2
    for i in range(0, n, max(1, n // 25)):
        assert _reversals([sum(f[i]) for f in frames], 765 * 0.3) <= 6 * secs, i


def test_bouncing_balls_bounce_lower_and_come_back():
    fx = effects.EFFECTS["Bouncing Balls"]
    plan, cycle, _ = effects._ball_plan(0)
    heights = [h for _, _, h in plan]
    assert heights == sorted(heights, reverse=True) and heights[0] <= 1.0 and len(plan) == 4
    assert fx(5.0 + cycle, 50, BASE) != fx(5.0, 50, BASE)  # the balls each keep their own time
    tops = [max((i for i, c in enumerate(fx(k * 0.05, 50, BASE)) if max(c) > 40), default=0) for k in range(400)]
    assert max(tops) > 25 and min(tops) < 8  # something flies high, and at times everything is near the ground
    lit = [sum(1 for c in fx(k * 0.05, 50, BASE) if max(c) > 40) for k in range(400)]
    assert max(lit) <= 20  # balls, not a filled string


def test_ripples_spread_from_a_point_and_fade():
    fx = effects.EFFECTS["Ripples"]
    back = fx(0.0, 200, BASE)[0]
    assert max(back) < 30
    widths = []
    for k in range(600):
        lit = [i for i, c in enumerate(fx(k * 0.05, 200, BASE)) if c != back]
        widths.append(lit[-1] - lit[0] if lit else 0)
    assert max(widths) > 30 and min(widths) < max(widths)
    assert all(sum(c) >= sum(back) for c in fx(3.3, 200, BASE))  # rings only add light


def test_pulse_wave_is_symmetric_and_runs_outward():
    fx = effects.EFFECTS["Pulse Wave"]
    f = fx(2.2, 51, BASE)
    assert f == f[::-1]
    levels = [[sum(c) for c in fx(t, 51, BASE)] for t in (4.0, 4.15)]
    crest = [max(range(30, 51), key=lv.__getitem__) for lv in levels]
    assert crest[1] >= crest[0]  # a crest on the right half moves towards the end


def test_ember_glow_stays_red_and_dim_with_slow_change():
    fx = effects.EFFECTS["Ember Glow"]
    for t in (0.0, 4.4, 31.0):
        for r, g, b in fx(t, 100, BASE):
            assert r >= g >= b and r >= 20
    a, b = fx(10.0, 100, BASE), fx(10.04, 100, BASE)
    assert max(abs(x[0] - y[0]) for x, y in zip(a, b)) < 40
    assert fx(10.0, 100, BASE) != fx(14.0, 100, BASE)


def test_rainbow_comet_has_a_white_head_and_a_coloured_tail():
    fx = effects.EFFECTS["Rainbow Comet"]
    f = fx(2.0, 50, BASE)
    lit = [i for i, c in enumerate(f) if max(c) > 0]
    assert lit and lit == list(range(lit[0], lit[-1] + 1)) and len(lit) < 20
    head = max(lit, key=lambda i: sum(f[i]))
    assert min(f[head]) > 150  # near white
    assert any(max(f[i]) - min(f[i]) > 60 for i in lit)  # the tail is coloured
    assert len([i for i, c in enumerate(fx(2.0, 904, BASE)) if max(c) > 0]) < 300


def test_stack_fills_holds_fades_and_starts_over():
    fx = effects.EFFECTS["Stack"]
    period = 10 * 0.7 + 1.6 + 1.4
    lit = lambda t: sum(1 for c in fx(t, 50, BASE) if max(c) > 0)  # noqa: E731
    counts = [lit(k * 0.7 + 0.01) for k in range(11)]
    assert counts[0] <= 5 and counts[-1] == 50 and counts == sorted(counts)  # (the first block is flying in)
    full = fx(7.5, 50, BASE)
    assert full[0] == BASE and full[5] != BASE and full[5] == full[9] and full[10] == BASE
    assert 0 < sum(full_c for full_c in fx(9.3, 50, BASE)[0]) < sum(BASE)
    assert lit(period - 0.001) == 0 or max(max(c) for c in fx(period - 0.001, 50, BASE)) < 5
    assert fx(period + 2.0, 50, BASE) == fx(2.0, 50, BASE)


def test_carousel_holds_then_steps_one_block():
    fx = effects.EFFECTS["Carousel"]
    assert fx(1.0, 50, BASE) == fx(1.5, 50, BASE)  # resting between steps
    a, b = fx(1.0, 50, BASE), fx(1.6 + 1.0, 50, BASE)
    assert a != b and a[4:] == b[:-4]  # one step later everything sits one block (4 bulbs) along
    assert set(a) >= set(effects.CLASSIC)


# --- the pattern effects ----------------------------------------------------------------------------------------
def test_pattern_effect_list():
    assert patterns.PATTERN_EFFECTS[-3:] == MORE_PATTERN_FX
    for e in MORE_PATTERN_FX:
        assert patterns.valid_effect(e) == e and patterns.light_effect(e) == e and patterns.is_animated(e)
    assert set(patterns._PALETTE_EFFECTS) == {"Gradient Flow", "Plasma", "Noise Drift", "Storm Flash", "Confetti",
                                              "Dissolve", "Sunrise Ramp"}


@pytest.mark.parametrize("effect", MORE_PATTERN_FX)
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
    if pattern != "Warm White" or effect == "Scanner":
        assert len({tuple(patterns.render(pattern, effect, t, 50)) for t in (0.2, 0.9, 1.7, 2.6, 4.1)}) > 2


@pytest.mark.parametrize("effect", ["Bounce", "Carousel"])
def test_sliding_effects_look_the_same_with_and_without_a_given_base(effect):
    base = patterns.base_colours("Classic Multicolor", 47)
    for t in (0.0, 0.8, 3.1, 40.0):
        assert patterns.render("Classic Multicolor", effect, t, 47, base=base) == patterns.render(
            "Classic Multicolor", effect, t, 47)


def test_bounce_sways_and_returns():
    still = patterns.render("Red & Green", "Steady", 0, 20)
    assert patterns.render("Red & Green", "Bounce", 0.0, 20) == still
    moved = patterns.render("Red & Green", "Bounce", 0.4, 20)
    assert moved != still and all(c[2] == 0 for c in moved)
    seen = {tuple(patterns.render("Classic Multicolor", "Bounce", k * 0.1, 20)) for k in range(200)}
    assert len(seen) > 20


def test_carousel_steps_one_colour_and_rests():
    r = lambda t: patterns.render("Classic Multicolor", "Carousel", t, 20)  # noqa: E731
    assert r(0.7) == r(1.4)  # at rest
    assert r(0.7) != r(0.2)  # mid-slide
    a, b = r(0.7), r(0.7 + 1.5)
    assert a[:-1] == b[1:]  # one bulb along per step
    cane = lambda t: patterns.render("Candy Cane", "Carousel", t, 20)  # noqa: E731
    assert cane(0.7)[:-2] == cane(0.7 + 1.5)[2:]  # two bulbs per colour there: it moves a whole stripe


def test_scanner_dims_the_pattern_and_glides_end_to_end():
    base = patterns.base_colours("Classic Multicolor", 50)
    centres = []
    for k in range(120):
        f = patterns.render("Classic Multicolor", "Scanner", k * 0.05, 50)
        assert all(all(x <= y for x, y in zip(c, b)) for c, b in zip(f, base))
        lv = [sum(c) / max(1, sum(b)) for c, b in zip(f, base)]
        centres.append(max(range(50), key=lv.__getitem__))
        assert min(lv) < 0.15 and max(lv) > 0.9
    assert min(centres) <= 1 and max(centres) >= 48
    assert max(abs(a - b) for a, b in zip(centres, centres[1:])) <= 3


# --- one-pass helpers give exactly what the per-bulb functions give ---------------------------------------------
def test_events_row_is_event_at_for_every_bulb():
    for t in (0.0, 0.7, 12.34, 600.25):
        for seed, slot, prob, dmin, dmax in ((6, 3.0, 0.6, 0.7, 1.5), (9, 1.2, 0.4, 0.15, 0.45), (301, 1.6, 0.45, 0.6, 1.3)):
            assert effects.events_row(t, 70, seed, slot, prob, dmin, dmax) == [
                effects.event_at(t, i, seed, slot, prob, dmin, dmax) for i in range(70)]
    assert effects.events_row(1.0, 0, 6, 3.0, 0.6, 0.7, 1.5) == []


def test_noise_row_is_noise_for_every_x():
    xs = [i * 0.37 - 3.0 for i in range(80)]
    for y in (0.0, 0.3, 7.77, -2.5):
        assert effects._vnoise2_row(xs, y, 201) == [effects._vnoise2(x, y, 201) for x in xs]


def test_hsv_and_rainbow_match_colorsys():
    import colorsys
    rng = random.Random(4)
    for _ in range(3000):
        h, s, v = rng.uniform(-2, 3), rng.choice([0.0, 1.0, rng.random()]), rng.choice([1.0, rng.random()])
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        assert effects._hsv(h, s, v) == (int(r * 255), int(g * 255), int(b * 255))
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
        assert patterns._rainbow_colour(h) == (int(r * 255), int(g * 255), int(b * 255))


@pytest.mark.parametrize("name", ["Classic Multicolor", "Rainbow", "Candy Cane", "Warm White"])
def test_colours_at_is_colour_at_for_every_position(name):
    pos = [i * 0.31 - 20.0 for i in range(300)]
    for n in (1, 50, 904):
        assert patterns.colours_at(name, pos, n) == [patterns.colour_at(name, p, n) for p in pos]
    assert patterns.base_colours(name, 60) == [patterns.bulb_colour(name, i, 60) for i in range(60)]


def test_effect_buffer_fast_path_matches_the_careful_one():
    odd = [(300, -5, 7.9), [1, 2, 3, 4], (9, 8, 7)]
    buf = transitions.effect_buffer([(0, 9, None)], lambda t, n, c: odd, 0.0, BASE, 12)
    assert bytes(buf) == bytes([255, 0, 7, 1, 2, 3, 9, 8, 7, 0, 0, 0])
    short = [(1, 2), (3, 4, 5, 6), (7, 8, 9)]  # lengths that add up to nine bytes, but are not three each
    buf = transitions.effect_buffer([(0, 9, None)], lambda t, n, c: short, 0.0, BASE, 9)
    assert bytes(buf) == bytes([1, 2, 3, 4, 5, 7, 8, 9, 0])
    fx = effects.EFFECTS["Plasma"]
    buf = transitions.effect_buffer([(3, 150, None)], fx, 1.5, BASE, 160)
    assert bytes(buf[3:153]) == bytes(v for c in fx(1.5, 50, BASE) for v in c) and bytes(buf[:3]) == b"\0\0\0"


def test_blend_ends_are_exact():
    a, b = bytes(range(0, 250, 5)), bytes(range(250, 0, -5))
    assert transitions.blend(a, b, 0.0) == a and transitions.blend(a, b, 1.0) == b
    assert transitions.blend(a, b, 0.5) == bytes(int(x + (y - x) * 0.5) for x, y in zip(a, b))
    assert transitions.blend(a[:10], b, 0.0) == a[:10] + bytes(40)
