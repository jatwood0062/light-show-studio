"""Tests for the pure WLED helpers; plain pytest, no Home Assistant needed."""
from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _stub(name: str, **attrs) -> None:
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)


def _load_wled():
    """Import wled.py with just enough of aiohttp / Home Assistant stubbed for its imports."""
    class _Err(Exception):
        pass

    _stub("aiohttp", ClientError=_Err, ClientSession=object, ClientTimeout=lambda **kw: kw)
    _stub("homeassistant")
    _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
    pkg = sys.modules.setdefault("light_show_studio_wl", types.ModuleType("light_show_studio_wl"))
    pkg.__path__ = [str(PKG)]
    for name in ("const", "effects", "protocol", "controller", "wled"):
        full = f"light_show_studio_wl.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
    return sys.modules["light_show_studio_wl.wled"]


wl = _load_wled()


def _header(pkt: bytes) -> tuple:
    return struct.unpack(">BBBBIH", pkt[:10])


def test_small_frame_is_one_packet():
    pkts = wl.ddp_packets([(1, 2, 3)] * 50)
    assert len(pkts) == 1
    assert _header(pkts[0]) == (0x41, 0, 0x01, 0x01, 0, 150)
    assert len(pkts[0]) == 160
    assert pkts[0][10:13] == bytes((1, 2, 3))


def test_long_frame_splits_with_push_on_last():
    pkts = wl.ddp_packets([(9, 8, 7)] * 600)
    assert len(pkts) == 2
    assert _header(pkts[0]) == (0x40, 0, 0x01, 0x01, 0, 1440)
    assert _header(pkts[1]) == (0x41, 0, 0x01, 0x01, 1440, 360)
    assert len(pkts[0]) == 1450 and len(pkts[1]) == 370


def test_rgbw_flag_still_sends_rgb():
    assert wl.ddp_packets([(1, 2, 3)] * 4, rgbw=True) == wl.ddp_packets([(1, 2, 3)] * 4)


def test_format_mac():
    assert wl.format_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"
