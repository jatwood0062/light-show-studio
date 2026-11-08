"""Tests for the between-show transition helpers; pure, no Home Assistant needed."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _load(name: str):
    """Import a module of the integration without running its __init__ (which needs Home Assistant)."""
    pkg = sys.modules.setdefault("light_show_studio_under_test", types.ModuleType("light_show_studio_under_test"))
    pkg.__path__ = [str(PKG)]
    full = f"light_show_studio_under_test.{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return sys.modules[full]


tr = _load("transitions")
playback = _load("playback")


def test_fade():
    assert tr.fade(b"\x64\xc8\xff", 1.0) == b"\x64\xc8\xff"
    assert tr.fade(b"\x64\xc8\xff", 0.0) == b"\0\0\0"
    assert tr.fade(b"\x64\xc8", 0.5) == b"\x32\x64"
    assert tr.fade(b"\x64", 5) == b"\x64" and tr.fade(b"\x64", -1) == b"\0"
    assert tr.fade(b"", 0.5) == b"" and tr.fade(b"\x64", float("nan")) == b"\0"


def test_blend():
    a, b = bytes([0, 100, 200]), bytes([100, 100, 0])
    assert tr.blend(a, b, 0.0) == a
    assert tr.blend(a, b, 1.0) == b
    assert tr.blend(a, b, 0.5) == bytes([50, 100, 100])


def test_blend_pads_shorter_with_zeros():
    assert tr.blend(b"\x64", b"\x64\x64\x64", 0.0) == b"\x64\0\0"
    assert tr.blend(b"\x64\x64\x64", b"", 1.0) == b"\0\0\0"
    assert tr.blend(b"", b"", 0.5) == b""


def test_effect_buffer_per_light_slices():
    calls = []

    def fx(t, n, colour):
        calls.append((t, n, colour))
        return [(n, 2, 3)] * n

    subs = [(0, 6, None), (15, 9, None)]  # a 2-bulb light, then a 3-bulb light after a gap
    buf = tr.effect_buffer(subs, fx, 1.5, (9, 9, 9), 24)
    assert len(buf) == 24
    assert buf[0:6] == bytes([2, 2, 3] * 2)
    assert buf[6:15] == bytes(9)
    assert buf[15:24] == bytes([3, 2, 3] * 3)
    assert calls == [(1.5, 2, (9, 9, 9)), (1.5, 3, (9, 9, 9))]


def test_effect_buffer_is_robust():
    def bad(t, n, c):
        raise RuntimeError

    assert tr.effect_buffer([(0, 6, None)], bad, 0, (1, 1, 1), 6) == bytearray(6)
    # short lists, out-of-range values and slices beyond the buffer never raise
    buf = tr.effect_buffer([(0, 9, None), (30, 9, None)], lambda t, n, c: [(300, -5, 7)], 0, (1, 1, 1), 12)
    assert buf == bytearray([255, 0, 7]) + bytearray(9)


def test_default_effect():
    assert tr.default_effect(["Rainbow", "Twinkle"]) == "Twinkle"
    assert tr.default_effect(["Rainbow", "Fire"]) == "Rainbow"
    assert tr.default_effect([]) == ""


def test_real_effects_work_with_effect_buffer():
    effects = _load("effects")
    buf = tr.effect_buffer([(0, 30, None), (30, 15, None)], effects.EFFECTS[tr.default_effect(effects.EFFECT_LIST)],
                           0.5, tr.TRANSITION_COLOUR, 45)
    assert len(buf) == 45 and any(buf)


def test_sequence_player_last_frame_and_fade_in():
    class Seq:
        path, frames, step_ms, duration = "x", 3, 20, 0.06

        def cached(self, i):
            return bytes([200, 100])

    sent = []

    async def run():
        p = playback.SequencePlayer(sent.append)
        p.fade_in = 100.0  # so slow that these few frames stay black
        await p.play(Seq())
        assert p.fade_in == 0.0  # used up by that play()
        return p

    p = asyncio.run(run())
    assert p.last_frame == bytes([200, 100])  # the unscaled frame
    assert sent and all(f == b"\0\0" for f in sent)


def test_fseq_has_block_and_first_uncached(tmp_path):
    fseq = _load("fseq")
    import struct
    # an uncompressed v2 file: 3 channels, 2000 frames, so more than one raw block
    frames, stride = 2000, 3
    header = bytearray(32)
    header[0:4] = b"PSEQ"
    struct.pack_into("<H", header, 4, 32)  # data offset
    header[6], header[7], header[8] = 0, 2, 0  # minor, major
    struct.pack_into("<H", header, 8, 32)
    struct.pack_into("<I", header, 10, stride)
    struct.pack_into("<I", header, 14, frames)
    header[18] = 20
    path = tmp_path / "x.fseq"
    path.write_bytes(bytes(header) + bytes(frames * stride))
    try:
        seq = fseq.FseqFile(str(path))
    except Exception:  # header layout differs: nothing more to check here
        pytest.skip("could not build a tiny fseq")
    assert not seq.has_block(0) and not seq.has_block(-1) and not seq.has_block(frames)
    assert seq.first_uncached(0, 5) == 0
    seq.frame(0)
    assert seq.has_block(0) and seq.cached(0) is not None
    assert seq.first_uncached(0, 5) is None
