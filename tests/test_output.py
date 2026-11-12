"""The output thread: ports coalesce frames (newest wins), control packets keep their order with the frames around
them, one port failing does not stop another, and the thread starts and stops cleanly. Sockets are 127.0.0.1 only."""
from __future__ import annotations

import base64
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
