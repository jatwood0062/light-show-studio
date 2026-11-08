"""WLED device_offset (first LED on the controller) and the per-light show timing lead."""
from __future__ import annotations

import asyncio
import struct

import pytest

import test_wled as tw
from test_library import _load
from wsharness import FakeHass

wl = tw.wl
playback = _load("playback")
receiver = _load("receiver")


def _hdr(pkt):
    return struct.unpack(">BBBBIH", pkt[:10])


# --- WLED device_offset ----------------------------------------------------------------------
def test_ddp_offset_is_in_leds_times_three():
    pkts = wl.ddp_packets([(1, 2, 3)] * 145, offset=279)
    assert len(pkts) == 1 and _hdr(pkts[0]) == (0x41, 0, 1, 1, 837, 435)


def test_rgbw_offset_is_the_same_bytes():
    assert wl.ddp_packets([(1, 2, 3)] * 5, rgbw=True, offset=10) == wl.ddp_packets([(1, 2, 3)] * 5, offset=10)
    assert _hdr(wl.ddp_packets([(1, 2, 3)], rgbw=True, offset=10)[0])[4] == 30


def test_long_frame_offsets_stack_on_the_base():
    a, b = wl.ddp_packets([(9, 8, 7)] * 600, offset=100)
    assert _hdr(a)[4] == 300 and _hdr(b)[4] == 300 + 1440 and _hdr(a)[0] == 0x40 and _hdr(b)[0] == 0x41


def test_zero_offset_unchanged():
    assert wl.ddp_packets([(1, 2, 3)] * 600) == wl.ddp_packets([(1, 2, 3)] * 600, offset=0)


def test_fits():
    assert wl.fits(145, 279, 424) and not wl.fits(146, 279, 424)
    assert wl.fits(500, 2000, None) and wl.fits(500, 2000, 0)  # unknown count: can't check


class _Sock:
    def __init__(self):
        self.sent = []

    def sendto(self, pkt, addr):
        self.sent.append((pkt, addr))

    def close(self):
        pass


def _wled(offset):
    ctl = wl.WledController(FakeHass(), "192.168.4.71", 145, False, offset)
    ctl._sock.close()
    ctl._sock = _Sock()
    return ctl


def test_every_wled_send_path_uses_the_offset():
    async def run():
        ctl = _wled(279)
        ctl._on = True
        ctl.show_mode = False
        frame = [(5, 5, 5)] * 145
        ctl._hw_frame(frame)  # frames, shows, identify flashes and scan resends all end here
        ctl._send_pending() if ctl._last else None
        ctl._last_sent = 0
        ctl._tick()  # keepalive
        ctl._last_sent = 0
        ctl._pending = None
        ctl._last = frame
        ctl._send_pending()
        ctl._stop_streaming()
        return ctl._sock.sent

    sent = asyncio.run(run())
    assert len(sent) >= 3
    assert all(_hdr(p)[4] == 837 and a == ("192.168.4.71", wl.DDP_PORT) for p, a in sent)


def test_default_offset_is_zero():
    async def run():
        ctl = wl.WledController(FakeHass(), "192.168.4.71", 4)
        return ctl.offset

    assert asyncio.run(run()) == 0


# --- show timing lead --------------------------------------------------------------------------
def test_lead_index_clamps_and_rounds_to_frames():
    assert playback.lead_index(5, 100, 0.025, 100) == 9
    assert playback.lead_index(5, -100, 0.025, 100) == 1
    assert playback.lead_index(2, -500, 0.025, 100) == 0  # clamp at the start
    assert playback.lead_index(98, 500, 0.025, 100) == 99  # clamp at the end
    assert playback.lead_index(7, 0, 0.025, 100) == 7


def test_receiver_routes_by_lead():
    async def run():
        rx = receiver.PixelReceiver()
        got = {"a": [], "b": [], "c": []}
        for key, lead in (("a", 0), ("b", 60), ("c", -40)):
            await rx.subscribe(key, 1 if key == "a" else 2 if key == "b" else 3, 1, got[key].append, lead)
        assert rx.leads() == [-40, 60]
        rx.show_sequence(bytes([1, 1, 1, 2, 2, 2, 3, 3, 3]))  # only the no-lead string
        assert got == {"a": [bytes([1, 1, 1])], "b": [], "c": []}
        rx.show_lead(60, bytes([4, 4, 4, 5, 5, 5, 6, 6, 6]))
        rx.show_lead(-40, bytes([7, 7, 7, 8, 8, 8, 9, 9, 9]))
        assert got["b"] == [bytes([5, 5, 5])] and got["c"] == [bytes([9, 9, 9])]
        rx.show_sequence(b"")  # a blank reaches everyone
        assert all(g[-1] == bytes(3) for g in got.values())
        rx.show(bytes([1, 1, 1, 2, 2, 2, 3, 3, 3]))  # network (xLights) frames: everyone, lead ignored
        assert got["b"][-1] == bytes([2, 2, 2])
        rx.unsubscribe("b")
        assert rx.leads() == [-40]
        rx.close()

    asyncio.run(run())


class _Seq:
    path, frames, step_ms = "x", 10, 20
    duration = 0.2

    def cached(self, i):
        return bytes([i])

    def first_uncached(self, a, b):
        return None


def test_sequence_player_sends_shifted_frames_without_touching_the_rest():
    log = []
    leads = [60, -40]

    async def run():
        p = playback.SequencePlayer(lambda d: log.append(("m", d[0] if d else None)), None,
                                    lambda lead, d: log.append(("l", lead, d[0])), lambda: leads)
        await p.play(_Seq())
        await asyncio.sleep(0.15)  # blank after the show ends

    asyncio.run(run())
    main = [e[1] for e in log if e[0] == "m" and e[1] is not None]
    assert main[0] == 0 and main[-1] == 9  # the audio-synced lights see the plain show
    cur, fwd, back = None, [], []
    for e in log:
        if e[0] == "m":
            cur = e[1]
        elif e[1] == 60:
            fwd.append((cur, e[2]))
        else:
            back.append((cur, e[2]))
    assert fwd[0] == (0, 3) and back[0] == (0, 0)  # +60 ms = 3 frames ahead; -40 ms clamps at the start
    assert all(f == min(9, c + 3) for c, f in fwd)  # clamped at the end
    assert all(f == max(0, c - 2) for c, f in back)
    assert fwd[-1][1] == 9


def test_no_leads_means_no_extra_work():
    log = []

    async def run():
        p = playback.SequencePlayer(log.append, None, lambda *a: log.append("lead"), lambda: [])
        await p.play(_Seq())

    asyncio.run(run())
    assert "lead" not in log
