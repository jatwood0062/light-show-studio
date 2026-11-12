"""Delivery statistics: the rolling window's sums with a made-up clock, and that the Govee and WLED send paths count
what they send and what fails. No network: the sockets are fakes."""
from __future__ import annotations

import asyncio

import pytest

from test_govee_stream import Lan, Light, RED, aio
from test_wled import wl
from test_zones import FakeHass, controller

LinkStats = controller.LinkStats


def test_frame_rate_over_the_window():
    s = LinkStats()
    for i in range(1000):  # 40 s at 25 fps
        s.frame(now=100.0 + i * 0.04)
    snap = s.snapshot(now=139.98)
    assert snap["fps"] == pytest.approx(25.0, abs=0.2)
    assert snap["gap_ms"] == 40
    assert snap["sent"] == 1000 and snap["failed"] == 0


def test_a_young_stream_is_measured_over_its_own_age():
    s = LinkStats()
    for i in range(51):  # 2 s at 25 fps
        s.frame(now=7.3 + i * 0.04)
    assert s.snapshot(now=9.3)["fps"] == pytest.approx(25.5, abs=0.1)
    one = LinkStats()
    one.frame(now=5.0)
    assert one.snapshot(now=5.0)["fps"] == 1.0  # not a division by nothing


def test_longest_gap_leaves_the_window():
    s = LinkStats()
    t = 50.0
    for gap in (0.04, 0.04, 0.7, 0.04):
        s.frame(now=t)
        t += gap
    s.frame(now=t)
    assert s.snapshot(now=t)["gap_ms"] == 700
    for _ in range(300):  # 12 more seconds of steady frames
        t += 0.04
        s.frame(now=t)
    assert s.snapshot(now=t)["gap_ms"] == 40


def test_a_pause_is_not_a_gap_and_the_rate_falls_to_zero():
    s = LinkStats()
    for i in range(25):
        s.frame(now=10.0 + i * 0.04)
    assert s.snapshot(now=30.0) == {**s.snapshot(now=30.0), "fps": 0.0, "gap_ms": 0}
    s.frame(now=500.0)  # switched on again an age later
    s.frame(now=500.04)
    snap = s.snapshot(now=500.04)
    assert snap["gap_ms"] == 40 and snap["fps"] == 2.0 and snap["sent"] == 27


def test_failed_merged_and_late_ticks():
    s = LinkStats()
    s.frame(False, now=1.0)
    s.frame(True, now=1.04)
    s.merge()
    s.woke(0.01, 0.04, now=1.0)  # a quarter of a period: on time
    s.woke(0.03, 0.04, now=1.1)  # three quarters: late
    s.woke(-0.001, 0.04, now=1.2)
    snap = s.snapshot(now=1.2)
    assert (snap["sent"], snap["failed"], snap["merged"], snap["late"], snap["wake_ms"]) == (2, 1, 1, 1, 30)
    assert s.snapshot(now=20.0)["wake_ms"] == 0 and s.snapshot(now=20.0)["late"] == 1
    assert snap["latency_ms"] is None


@aio
async def test_govee_frames_and_failures_are_counted(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(controller.time, "monotonic", lambda: now[0])
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    for _ in range(5):
        ctl._hw_frame(RED)
        now[0] += 0.04
    assert (ctl.stats.sent, ctl.stats.failed) == (5, 0)
    lan.ok = False  # the socket refuses: no route to the light
    ctl._hw_frame(RED)
    assert (ctl.stats.sent, ctl.stats.failed) == (6, 1)
    assert ctl.stats.snapshot()["gap_ms"] == 40
    ctl.shutdown()


@aio
async def test_show_frames_that_are_replaced_count_as_merged(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(controller.time, "monotonic", lambda: now[0])
    ctl = Light(FakeHass(), Lan())
    rgb = bytes([255, 0, 0] * 3)
    ctl.show_external(rgb)
    ctl.show_external(rgb)  # too soon: waits, nothing lost yet
    assert ctl.stats.merged == 0
    ctl.show_external(rgb)  # replaces the one waiting
    assert ctl.stats.merged == 1
    ctl.shutdown()


class Sock:
    def __init__(self):
        self.sent, self.fail = [], False

    def sendto(self, pkt, addr):
        if self.fail:
            raise OSError("no route")
        self.sent.append(addr)

    def setblocking(self, flag):
        pass

    def close(self):
        pass


@aio
async def test_wled_frames_and_failures_are_counted(monkeypatch):
    sock = Sock()
    monkeypatch.setattr(wl.socket, "socket", lambda *a: sock)
    ctl = wl.WledController(FakeHass(), "192.168.1.20", 3)
    ctl._send(RED)
    ctl._send(RED)
    assert (ctl.stats.sent, ctl.stats.failed, len(sock.sent)) == (2, 0, 2)
    sock.fail = True
    ctl._send(RED)
    assert (ctl.stats.sent, ctl.stats.failed) == (3, 1)
    ctl.shutdown()
    await asyncio.sleep(0)
