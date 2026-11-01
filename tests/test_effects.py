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
    "Fireworks", "Twinkle", "Fire")]


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
