"""Tests for patterns and pattern effects; plain pytest, no Home Assistant needed."""
from __future__ import annotations

import pytest

from test_library import _load

patterns = _load("patterns")
PATTERNS = patterns.PATTERN_LIST
EFFECTS = patterns.PATTERN_EFFECTS
SIZES = (1, 3, 50, 400, 904)
TIMES = (0.0, 0.04, 0.5, 1.37, 3.0, 17.9, 600.25)


def _valid(frame, n):
    assert isinstance(frame, list) and len(frame) == n
    for c in frame:
        assert len(c) == 3
        for v in c:
            assert isinstance(v, int) and 0 <= v <= 255


@pytest.mark.parametrize("pattern", PATTERNS)
@pytest.mark.parametrize("effect", EFFECTS)
def test_shape_and_range(pattern, effect):
    for n in SIZES:
        for t in TIMES:
            _valid(patterns.render(pattern, effect, t, n), n)


@pytest.mark.parametrize("pattern", PATTERNS)
@pytest.mark.parametrize("effect", EFFECTS)
def test_deterministic(pattern, effect):
    assert patterns.render(pattern, effect, 1.37, 50) == patterns.render(pattern, effect, 1.37, 50)


def test_speed_factor_bounds():
    assert patterns.speed_factor(1) == pytest.approx(0.25)
    assert patterns.speed_factor(50) == pytest.approx(1)
    assert patterns.speed_factor(100) == pytest.approx(4)
    assert patterns.speed_factor(-5) == patterns.speed_factor(1)
    assert patterns.speed_factor(900) == patterns.speed_factor(100)
    assert patterns.speed_factor("junk") == 1


@pytest.mark.parametrize("pattern", PATTERNS)
def test_steady_is_static(pattern):
    assert patterns.render(pattern, "Steady", 0.0, 50) == patterns.render(pattern, "Steady", 99.9, 50)
    assert not patterns.is_animated("Steady")
    assert all(patterns.is_animated(e) for e in EFFECTS if e != "Steady")


def test_chase_moves():
    a = patterns.render("Classic Multicolor", "Chase", 0.0, 50)
    b = patterns.render("Classic Multicolor", "Chase", 0.5, 50)
    assert a != b


def test_chase_is_smooth():
    """Frames 40 ms apart move by a fraction of a bulb, so most bulbs barely change."""
    a = patterns.render("Classic Multicolor", "Chase", 1.00, 60)
    b = patterns.render("Classic Multicolor", "Chase", 1.04, 60)
    same = sum(x == y for x, y in zip(a, b))
    assert same > 30


def test_twinkle_bulbs_independent_and_gentle():
    frames = [patterns.render("Warm White", "Twinkle", i * 0.04, 100) for i in range(200)]
    # not in sync: at any moment the bulbs show a spread of brightness
    levels = [c[0] for c in frames[50]]
    assert max(levels) - min(levels) > 60
    # gentle: no bulb jumps more than a small step between 25 fps frames
    worst = max(abs(a[i][0] - b[i][0]) for a, b in zip(frames, frames[1:]) for i in range(100))
    assert worst < 40
    # never fully dark
    assert all(c[0] > 0 for f in frames for c in f)
    # only a few bulbs are swelling at a time
    swelling = [sum(1 for c in f if c[0] > 255 * 0.45) for f in frames]
    assert 0 < sum(swelling) / len(swelling) < 35


def test_continuous_time_has_no_big_jumps():
    for effect in ("Twinkle", "Twinkle Swap", "Fade", "Wave", "Sparkle"):
        a = patterns.render("Pastel", effect, 5.00, 50)
        b = patterns.render("Pastel", effect, 5.04, 50)
        diffs = sum(1 for x, y in zip(a, b) if max(abs(p - q) for p, q in zip(x, y)) > 120)
        assert diffs <= 5, effect


def test_unknown_names_fall_back():
    _valid(patterns.render("Nope", "Steady", 0, 10), 10)
    _valid(patterns.render("Classic Multicolor", "Nope", 1.0, 10), 10)
    assert patterns.render("Nope", "Nope", 1.0, 10) == patterns.render(patterns.DEFAULT_PATTERN, patterns.DEFAULT_EFFECT, 1.0, 10)
    assert patterns.render("Rainbow", "Steady", 0, 0) == []


def test_pattern_not_a_transition_effect():
    effects = _load("effects")
    assert patterns.PATTERN not in effects.EFFECTS
    assert patterns.PATTERN not in effects.EFFECT_LIST


def test_lists_unique():
    assert len(set(PATTERNS)) == len(PATTERNS) and len(set(EFFECTS)) == len(EFFECTS)
    assert patterns.DEFAULT_PATTERN in PATTERNS and patterns.DEFAULT_EFFECT in EFFECTS


@pytest.mark.parametrize("effect", ["Twinkle", "Twinkle Swap", "Sparkle"])
def test_random_pattern_effects_are_pure_in_t_and_not_periodic(effect):
    a = patterns.render("Pastel", effect, 12.34, 50)
    patterns.render("Pastel", effect, 3.0, 50)
    patterns.render("Pastel", effect, 3.0, 20)
    assert patterns.render("Pastel", effect, 12.34, 50) == a
    series = [patterns.render("Pastel", effect, k * 0.1, 40) for k in range(600)]
    for i in range(40):
        s = [f[i] for f in series]
        for lag in (10, 20, 30, 50, 100, 150):
            assert sum(x == y for x, y in zip(s, s[lag:])) < len(s) - lag or len(set(s)) == 1


def test_random_pattern_effects_seed_follows_the_light():
    a = patterns.render("Warm White", "Twinkle", 20.0, 60, "light-a")
    b = patterns.render("Warm White", "Twinkle", 20.0, 60, "light-b")
    assert a != b
    assert a == patterns.render("Warm White", "Twinkle", 20.0, 60, "light-a")


def test_twinkle_swap_changes_colour_only_while_dark_and_only_a_few_dip():
    n = 60
    prev = patterns.render("Pastel", "Twinkle Swap", 0.0, n)
    dips = []
    for k in range(1, 1500):
        cur = patterns.render("Pastel", "Twinkle Swap", k * 0.04, n)
        for i in range(n):
            if cur[i] != prev[i] and max(cur[i]) > 40 and max(prev[i]) > 40:
                # a visible colour change must be a small step in brightness, not a swap of two bright colours
                assert max(abs(a - b) for a, b in zip(cur[i], prev[i])) < 60
        dips.append(sum(1 for c in cur if max(c) < 255 * 0.5))
        prev = cur
    assert 0 < sum(dips) / len(dips) < 25


def test_sparkle_only_adds_white_and_few_at_once():
    base = patterns.render("Pastel", "Steady", 0, 100)
    counts = []
    for k in range(300):
        f = patterns.render("Pastel", "Sparkle", k * 0.13, 100)
        counts.append(sum(1 for x, y in zip(f, base) if x != y))
        assert all(all(a >= b for a, b in zip(x, y)) for x, y in zip(f, base))
    assert 1 <= sum(counts) / len(counts) <= 20


def test_chase_phase_is_continuous_across_wrap():
    """The chase repeats every len(pattern) bulbs; no jump when position wraps."""
    for name in ("Classic Multicolor", "Candy Cane", "Rainbow"):
        prev = patterns.render(name, "Chase", 0.0, 40)
        for s in range(1, 800):
            cur = patterns.render(name, "Chase", s * 0.04, 40)
            moved = sum(1 for a, b in zip(cur, prev) if max(abs(x - y) for x, y in zip(a, b)) > 150)
            assert moved <= 40 // 2 + 1, (name, s)
            prev = cur
