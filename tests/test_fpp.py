"""FPP MultiSync master: the packets a Falcon Player / ESPixelStick remote expects, and the show player sending them."""
from __future__ import annotations

import asyncio
import struct

import pytest

from test_library import _load
from test_offset_lead import _Seq

fpp = _load("fpp")
playback = _load("playback")


def test_packet_layout_matches_fpp_control_protocol():
    pkt = fpp.sync_packet(fpp.SYNC, "Sarajevo.fseq", 120, 3.0)
    assert pkt[:4] == b"FPPD" and pkt[4] == 0x01
    (length,) = struct.unpack("<H", pkt[5:7])
    body = pkt[7:]
    assert length == len(body)
    action, kind, frame, secs = struct.unpack("<BBIf", body[:10])
    assert (action, kind, frame, secs) == (fpp.SYNC, 0, 120, 3.0)
    assert body[10:] == b"Sarajevo.fseq\0"


def _sent(frames):
    out, last = [], None
    for f in frames:
        if fpp.due(f, last):
            out.append(f)
            last = f
    return out


def test_sync_pace_follows_fpp():
    assert _sent(range(55)) == [0, 4, 8, 12, 16, 20, 24, 28, 32, 42, 52]  # 4 apart, then 10 from the last one sent
    assert _sent([100, 101, 37, 38]) == [100, 37]  # a seek or loop back is sent at once
    assert _sent([57]) == [57]  # the first sync after a start, resume or seek never waits


def test_targets():
    assert fpp.parse_targets("") == []
    assert fpp.parse_targets(" 192.168.4.50, 192.168.4.51 ") == ["192.168.4.50", "192.168.4.51"]
    for bad in ("8.8.8.8", "not-an-ip"):
        with pytest.raises(ValueError):
            fpp.parse_targets(bad)


class Recorder:
    def __init__(self):
        self.log = []

    def start(self, name):
        self.log.append(("start", name))

    def frame(self, name, i, secs):
        self.log.append(("frame", i))

    def stop(self, name):
        self.log.append(("stop", name))

    def pause(self, name):
        self.log.append(("pause", name))


def test_show_player_starts_syncs_and_stops_the_remotes():
    rec = Recorder()

    async def run():
        p = playback.SequencePlayer(lambda d: None)
        p.sync = rec
        await p.play(_Seq())

    asyncio.run(run())
    assert rec.log[0] == ("start", "x") and rec.log[-1] == ("stop", "x")
    assert [e[1] for e in rec.log if e[0] == "frame"][:2] == [0, 1]


def test_stopping_a_show_stops_the_remotes():
    rec = Recorder()

    async def run():
        p = playback.SequencePlayer(lambda d: None)
        p.sync = rec
        p.play(_Seq())
        await asyncio.sleep(0.05)
        p.stop()
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert rec.log[-1] == ("stop", "x")


def test_fpp_sync_sends_to_each_target(monkeypatch):
    s = fpp.FppSync(["127.0.0.1"])
    sent = []
    monkeypatch.setattr(s, "_sock", type("S", (), {"sendto": lambda self, p, a: sent.append((p[7], a)), "close": lambda self: None})())
    s.start("a.fseq")
    s.frame("a.fseq", 0, 0.0)
    s.frame("a.fseq", 0, 0.0)  # same frame twice: once
    s.frame("a.fseq", 3, 0.06)  # not due
    s.pause("a.fseq")
    s.pause("a.fseq")  # already stopped: nothing more
    s.frame("a.fseq", 57, 1.14)  # resumed: synced at once, wherever it is
    s.stop("a.fseq")
    assert [p for p, _ in sent] == [fpp.OPEN, fpp.SYNC, fpp.STOP, fpp.SYNC, fpp.STOP]
    assert sent[0][1] == ("127.0.0.1", fpp.PORT)


def test_pausing_a_show_stops_the_remotes_until_it_resumes():
    rec = Recorder()

    async def run():
        p = playback.SequencePlayer(lambda d: None)
        p.sync = rec
        p.play(_Seq())
        await asyncio.sleep(0.03)
        p.timeline.pause()
        await asyncio.sleep(0.06)
        n = len(rec.log)
        assert ("pause", "x") in rec.log
        assert all(e[0] == "pause" for e in rec.log[n - 2:])  # no frames while paused
        p.stop()
        await asyncio.sleep(0.01)

    asyncio.run(run())
