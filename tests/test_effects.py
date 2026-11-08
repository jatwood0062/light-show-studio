"""Tests for the effect functions; plain pytest, no Home Assistant needed."""
from __future__ import annotations

import time

import pytest

from test_library import _load

effects = _load("effects")
NAMES = list(effects.EFFECTS)
SIZES = (1, 3, 50, 400, 904)
TIMES = (0.0, 0.04, 0.5, 1.37, 3.0, 17.9, 600.25)
BASE = (255, 120, 30)


def test_effect_list_matches_effects():
    assert effects.EFFECT_LIST == NAMES
    assert len(NAMES) == len(set(NAMES))
    assert NAMES[:3] == ["Rainbow", "Christmas", "Candy Cane"]


@pytest.mark.parametrize("name", NAMES)
def test_output_shape_and_range(name):
    fx = effects.EFFECTS[name]
    for n in SIZES:
        for t in TIMES:
            out = fx(t, n, BASE)
            assert isinstance(out, list) and len(out) == n
            for c in out:
                assert len(c) == 3
                for v in c:
                    assert isinstance(v, int) and 0 <= v <= 255


NEW_DETERMINISTIC = [n for n in NAMES if n not in (
    "Fireworks", "Fire")]


@pytest.mark.parametrize("name", NEW_DETERMINISTIC)
def test_deterministic(name):
    fx = effects.EFFECTS[name]
    for n in (3, 50, 400):
        for t in (0.0, 1.37, 42.0):
            assert fx(t, n, BASE) == fx(t, n, BASE)


@pytest.mark.parametrize("name", NAMES)
def test_speed(name):
    fx = effects.EFFECTS[name]
    fx(0.0, 904, BASE)
    start = time.perf_counter()
    for f in range(25):
        fx(f / 25, 904, BASE)
    assert time.perf_counter() - start < 0.5


# --- random (not periodic) twinkle / sparkle -----------------------------------------------------
RANDOM_FX = ["Twinkle", "Rainbow Twinkle", "Vintage Twinkle", "Christmas Twinkle", "Sparkle"]


def _level(frame):
    return [sum(c) for c in frame]


@pytest.mark.parametrize("name", RANDOM_FX)
def test_random_effects_are_a_pure_function_of_time(name):
    """Same t, same frame, however often or in whatever order frames are drawn (no hidden state)."""
    fx = effects.EFFECTS[name]
    a = fx(7.31, 50, BASE)
    for t in (0.0, 3.3, 99.0, 7.31):
        fx(t, 50, BASE)
        fx(t, 17, BASE)
    assert fx(7.31, 50, BASE) == a
    # a frame skipped or late costs nothing: any t is reachable directly
    assert fx(1000.123, 50, BASE) == fx(1000.123, 50, BASE)


@pytest.mark.parametrize("name", RANDOM_FX)
def test_random_effects_are_not_periodic(name):
    """No bulb repeats on a fixed period, and the string is not in step: compare each bulb's pattern over time."""
    fx = effects.EFFECTS[name]
    times = [i * 0.05 for i in range(1200)]  # 60 s
    cols = [_level(fx(t, 30, BASE)) for t in times]
    for i in range(30):
        series = [row[i] for row in cols]
        for lag in (20, 40, 60, 80, 100, 120, 160, 200, 240):  # 1 s .. 12 s
            same = sum(a == b for a, b in zip(series, series[lag:]))
            # a periodic bulb would match itself ~always at its period; resting stretches match but so do all lags
            assert same < len(series) - lag
        # events happen at several different moments, not on a grid
    starts = []
    for i in range(30):
        series = [row[i] for row in cols]
        rest = min(series)
        starts += [times[k] for k in range(1, len(series)) if series[k] != rest and series[k - 1] == rest]
    assert len(starts) > 20
    gaps = sorted({round(b - a, 2) for a, b in zip(starts, starts[1:]) if b > a})
    assert len(gaps) > 10


@pytest.mark.parametrize("name", RANDOM_FX)
def test_random_effects_only_a_few_bulbs_change_at_once(name):
    fx = effects.EFFECTS[name]
    n = 100
    frames = [_level(fx(k * 0.137, n, BASE)) for k in range(400)]
    rest = [min(f[i] for f in frames) for i in range(n)]  # each bulb's resting level
    counts = [sum(1 for i in range(n) if f[i] > rest[i] * 1.1 + 6) for f in frames]
    avg = sum(counts) / len(counts)
    assert 1 <= avg <= 30, avg  # a few at a time, never the whole string
    assert max(counts) < 60


def test_twinkle_keeps_average_brightness_and_floor():
    fx = effects.EFFECTS["Twinkle"]
    n = 100
    tot = peak = 0
    lo = 255
    for k in range(600):
        f = fx(k * 0.11, n, (200, 200, 200))
        tot += sum(c[0] for c in f) / n
        lo = min(lo, min(c[0] for c in f))
        peak = max(peak, max(c[0] for c in f))
    avg = tot / 600
    assert lo >= 0.3 * 200  # never mostly dark: resting bulbs sit at the floor
    assert 0.32 * 200 < avg < 0.5 * 200
    assert peak > 0.9 * 200


def test_twinkle_lights_of_same_length_share_no_state():
    fx = effects.EFFECTS["Twinkle"]
    before = fx(5.0, 40, BASE)
    for t in (0.0, 1.0, 2.0):
        fx(t, 40, BASE)
    assert fx(5.0, 40, BASE) == before


def test_christmas_twinkle_keeps_its_colours():
    fx = effects.EFFECTS["Christmas Twinkle"]
    seen = set()
    for k in range(200):
        for c in fx(k * 0.2, 40, BASE):
            m = max(c)
            if m > 40:
                seen.add(tuple(round(v / m, 1) for v in c))
    # red, green and gold hues only
    assert all((r > 0 and g == 0 and b == 0) or (g > 0 and r == 0 and b == 0) or (r > 0 and g > 0 and b <= 0.1)
               for r, g, b in seen)
    assert len(seen) >= 3


def test_sparkle_glints_are_white_on_the_dimmed_base():
    fx = effects.EFFECTS["Sparkle"]
    dim = tuple(int(v * 0.75) for v in BASE)
    n_glint = 0
    for k in range(300):
        for c in fx(k * 0.1, 50, BASE):
            if c != dim:
                n_glint += 1
                assert all(a >= b for a, b in zip(c, dim))
    assert n_glint > 100
