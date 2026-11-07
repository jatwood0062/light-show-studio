"""Tests for the pure Twinkly helpers; plain pytest, no Home Assistant needed."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _stub(name: str, **attrs) -> None:
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)


def _load_twinkly():
    """Import twinkly.py with just enough of aiohttp / Home Assistant stubbed for its imports."""
    class _Err(Exception):
        pass

    _stub("aiohttp", ClientError=_Err, ClientSession=object, ClientTimeout=lambda **kw: kw)
    _stub("homeassistant")
    _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
    pkg = sys.modules.setdefault("light_show_studio_tw", types.ModuleType("light_show_studio_tw"))
    pkg.__path__ = [str(PKG)]
    for name in ("const", "effects", "protocol", "controller", "twinkly"):
        full = f"light_show_studio_tw.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
    return sys.modules["light_show_studio_tw.twinkly"]


tw = _load_twinkly()
TOKEN = bytes(range(1, 9))


def test_rgb_packets_for_400_leds():
    frame = [(i % 256, 2, 3) for i in range(400)]
    pkts = tw.build_packets(TOKEN, frame)
    assert [len(p) for p in pkts] == [912, 312]  # 12-byte header + 900 and 300 data bytes
    assert pkts[0][:12] == b"\x03" + TOKEN + b"\x00\x00\x00"
    assert pkts[1][:12] == b"\x03" + TOKEN + b"\x00\x00\x01"
    assert (pkts[0][12:] + pkts[1][12:])[:6] == bytes([0, 2, 3, 1, 2, 3])


def test_small_frame_is_one_packet():
    pkts = tw.build_packets(TOKEN, [(1, 2, 3)] * 10)
    assert len(pkts) == 1 and len(pkts[0]) == 12 + 30


def test_rgbw_takes_the_grey_part_out():
    assert tw.frame_bytes([(200, 100, 50), (0, 0, 0), (255, 255, 255)], "RGBW") == bytes(
        [50, 150, 50, 0, 0, 0, 0, 0, 255, 0, 0, 0])
    assert tw.frame_bytes([(200, 100, 50)], "RGB") == bytes([200, 100, 50])


def test_layout_y_is_flipped():
    out = tw.flip_layout([{"x": -0.5, "y": 0.23, "z": 1.0}, {"x": 0.25, "y": 1.0, "z": 0}])
    assert out == [[-0.5, -0.23, 1.0], [0.25, -1.0, 0.0]]  # z kept for 3D mapping
    assert tw.flip_layout([{"x": 1, "y": 2}]) == [[1.0, -2.0]]  # a flat layout stays 2D


def test_discovery_reply():
    reply = bytes([45, 4, 168, 192]) + b"OK" + b"Tree\0"
    assert tw.parse_discovery(reply) == ("192.168.4.45", "Tree")
    assert tw.parse_discovery(b"\x01discover") is None
    assert tw.parse_discovery(b"abc") is None
