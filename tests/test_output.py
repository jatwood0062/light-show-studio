"""The output thread: ports coalesce frames (newest wins), control packets keep their order with the frames around
them, one port failing does not stop another, and the thread starts and stops cleanly. Sockets are 127.0.0.1 only."""
from __future__ import annotations

import base64
import asyncio
import json
import socket
import time

import test_zones as tz
from test_govee_stream import aio
from test_library import _load

out = _load("output")


class Sink:
    """A UDP receiver on 127.0.0.1."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.addr = self.sock.getsockname()

    def read(self, n):
        got = []
        for _ in range(n):
            try:
                got.append(self.sock.recvfrom(2048)[0])
            except socket.timeout:
                break
        return got

    def close(self):
        self.sock.close()


def test_the_newest_frame_replaces_one_still_waiting():
    th = out.OutputThread(sync=True)
    sink = Sink()
    port = th.open_port(sink.addr)
    far = time.perf_counter() + 60
    port.submit([b"a"], due=far)
    port.submit([b"b"], due=far)
    port.submit([b"c"], due=far)
    assert port.pending() == 1 and port.coalesced == 2
    th.flush()
    assert sink.read(3) == [b"c"] and port.sent == 1
    port.close()
    sink.close()


def test_control_packets_keep_their_order_with_the_frames():
    th = out.OutputThread(sync=True)
    sink = Sink()
    port = th.open_port(sink.addr)
    far = time.perf_counter() + 60
    port.submit([b"f1"], due=far)
    port.control([b"off"])  # a frame before it is never sent after it
    port.submit([b"f2"], due=far)
    port.submit([b"f3"], due=far)
    port.control([b"on"])
    th.flush()
    assert sink.read(5) == [b"f1", b"off", b"f3", b"on"]
    port.close()
    sink.close()


def test_a_port_that_raises_does_not_stop_the_others():
    th = out.OutputThread(sync=True)
    sink = Sink()
    bad, good = th.open_port(("127.0.0.1", 9)), th.open_port(sink.addr)

    class Broken:
        def sendto(self, *a):
            raise OSError("no route")

        def close(self):
            pass

    bad._sock.close()
    bad._sock = Broken()
    for i in range(3):
        bad.submit([b"x"])
        good.submit([b"%d" % i])
    assert bad.failed == 3 and bad.sent == 0 and good.sent == 3
    assert sink.read(3) == [b"0", b"1", b"2"]
    for p in (bad, good):
        p.close()
    sink.close()


def test_a_closed_port_takes_nothing():
    th = out.OutputThread(sync=True)
    port = th.open_port(("127.0.0.1", 9))
    port.close()
    assert port.submit([b"x"]) is False and port.control([b"x"]) is False and th._ports == []


def test_the_thread_sends_due_frames_in_time_and_stops_cleanly():
    th = out.OutputThread()
    th.start()
    sink = Sink()
    port = th.open_port(sink.addr)
    t0 = time.perf_counter()
    port.submit([b"late"], due=t0 + 0.05)
    port.control([b"c"])  # behind the frame in the queue: waits for it
    assert sink.read(2) == [b"late", b"c"]
    assert time.perf_counter() - t0 >= 0.049
    port.submit([b"now"])
    assert th.flush() and sink.read(1) == [b"now"]
    alive = th._thread
    th.stop()
    assert not alive.is_alive() and th._thread is None
    th.stop()  # twice is fine
    port.close()
    sink.close()


def test_stats_include_what_the_port_counted():
    stats = _load("stats").LinkStats()
    th = out.OutputThread(sync=True)
    port = th.open_port(("127.0.0.1", 9), stats)
    far = time.perf_counter() + 60
    port.submit([b"a"], due=far)
    port.submit([b"b"], due=far)
    assert stats.snapshot()["merged"] == 1 and stats.snapshot()["failed"] == 0
    port.close()


@aio
async def test_a_govee_light_sends_frames_and_commands_through_its_port_in_order():
    th = out.OutputThread(sync=True)
    sink = Sink()
    hass = tz.FakeHass()
    ctl = tz.controller.C9Controller(hass, "192.168.1.10", 3, True)
    th_port = th.open_port(sink.addr, ctl.stats)
    ctl.port = th_port
    ctl.lan.port = th_port
    ctl._hw_frame([(1, 2, 3)] * ctl.bulbs)
    await ctl.async_turn_off()
    th.flush()
    kinds = []
    for p in sink.read(6):
        msg = json.loads(p)["msg"]
        kinds.append(("arm" if base64.b64decode(msg["data"]["pt"])[3] == 0xB1 else "frame") if msg["cmd"] == "razer" else msg["cmd"])
    assert kinds[0] in ("brightness", "arm") and "frame" in kinds and kinds[-1] == "turn", kinds
    assert kinds.index("frame") < len(kinds) - 1
    ctl.shutdown()
    sink.close()


@aio
async def test_wled_ddp_packets_go_out_on_the_port_and_count_as_sent():
    import test_offset_lead as tol
    th = out.OutputThread(sync=True)
    sink = Sink()
    ctl = tol.wl.WledController(tz.FakeHass(), "192.168.4.71", 145, False, 0)
    ctl.port = th.open_port(sink.addr, ctl.stats)
    ctl._on = True
    ctl._send([(5, 5, 5)] * 145)
    th.flush()
    assert len(sink.read(2)) == 1 and ctl.stats.sent == 1 and ctl.port.sent == 1
    assert tol.wl.WledController.UDP_PORT == tol.wl.DDP_PORT
    ctl.shutdown()
    assert ctl.port.submit([b"x"]) is False  # shutdown closes the port
    sink.close()


# --- effects render on the output thread ---------------------------------------------------------
def _threaded_light(bulbs=20):
    """A real Govee controller on a started output thread; every frame handed to its port is timed."""
    th = out.OutputThread()
    th.start()
    ctl = tz.controller.C9Controller(tz.FakeHass(), "192.168.1.10", bulbs, True)
    ctl.use_output(th)
    ctl.is_on, ctl.show_mode = True, False
    stamps = []
    real = ctl.port.submit

    def submit(packets, due=None):
        stamps.append(time.monotonic())
        return real(packets, due)

    ctl.port.submit = submit
    return th, ctl, stamps


def _max_gap(stamps, start, end):
    inside = [t for t in stamps if start <= t <= end]
    return max(b - a for a, b in zip(inside, inside[1:]))


@aio
async def test_a_blocked_event_loop_does_not_stall_an_animated_light():
    th, ctl, stamps = _threaded_light()
    try:
        ctl.speed = 80
        await ctl.async_turn_on(effect="Rainbow Flow", transition=0)  # (a smooth one: every frame differs)
        assert ctl._job is not None and ctl._task is None  # rendering on the thread, not as a loop task
        await asyncio.sleep(0.3)
        start = time.monotonic()
        time.sleep(0.3)  # something slow on the event loop
        await asyncio.sleep(0.3)
        assert len(stamps) > 10
        assert _max_gap(stamps, start, start + 0.3) < 0.08
        assert ctl.frame != [(0, 0, 0)] * ctl.bulbs and ctl._fx_t > 0.5  # the card's frame and the clock kept going
    finally:
        ctl.shutdown()
        th.stop()


@aio
async def test_stopping_or_changing_a_threaded_effect_takes_effect_at_once():
    th, ctl, stamps = _threaded_light()
    try:
        await ctl.async_set_pattern("Classic Multicolor", "Chase", 50, transition=0)
        await asyncio.sleep(0.2)
        first = ctl._job
        await ctl.async_set_pattern("Candy Cane", "Twinkle", None, transition=0)  # a new look: a fresh job
        assert ctl._job is not first and not first.live
        n = len(stamps)
        await asyncio.sleep(0.15)
        assert len(stamps) > n  # the new look is rendering
        await ctl.async_turn_off(transition=0)
        assert ctl._job is None
        n = len(stamps)
        await asyncio.sleep(0.2)
        assert len(stamps) == n  # nothing after off
        assert ctl.frame == [(0, 0, 0)] * ctl.bulbs
    finally:
        ctl.shutdown()
        th.stop()


@aio
async def test_a_fade_runs_on_the_loop_then_the_thread_takes_the_effect_over():
    th, ctl, stamps = _threaded_light()
    try:
        await ctl.async_set_pattern("Classic Multicolor", "Chase", 50, transition=0)
        await asyncio.sleep(0.1)
        await ctl.async_set_pattern("Candy Cane", None, None, transition=0.2)
        assert ctl._job is None and ctl._task is not None  # the fade is the loop's
        await asyncio.sleep(0.5)
        assert ctl._xf is None and ctl._job is not None and ctl._job.live and ctl._task is None
    finally:
        ctl.shutdown()
        th.stop()
