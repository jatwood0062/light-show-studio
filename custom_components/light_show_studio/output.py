"""One output thread for the whole integration, so frames leave on time even when Home Assistant's event loop is busy.

Producers on the loop build the packets (colours -> protocol bytes) and hand them to a light's `OutputPort`; the
thread only owns the sockets and the ports' queues, never hass or controller state. Per port the queue is in
submission order: `control()` packets (turn on/off, brightness, realtime arm) always go out in order, and a
`submit()`ted frame replaces the frame before it when that one has not left yet (newest wins, counted as
coalesced), but never jumps over a control packet. A port that fails (OSError) counts the failure and moves on;
no port waits for another. `OUTPUT_THREAD = False` leaves every light on its direct sends.

A second daemon thread, the `RenderDriver`, renders steady effects: each light's `RenderJob` is ticked on the shared
1/FPS beat (the loop only starts and stops jobs), so a busy event loop cannot delay a frame."""
from __future__ import annotations

import logging
import math
import socket
import threading
import time
from collections import deque
from typing import Any

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
OUTPUT_THREAD = True  # False: no thread, every light sends directly from the event loop
OUTPUT_KEY = f"{DOMAIN}_output"
IDLE = 0.5  # seconds the thread sleeps with nothing queued (a submit wakes it at once)
SPIN = 0.002  # the last stretch before a due time is waited out in a tight loop, for the timing


class RenderJob:
    """One light's steady animation. `tick(job, now, due)` renders and submits a frame and returns the seconds to
    the next one (None: done). Ticks run under `lock`, so `cancel()` returns only when no frame is in flight."""

    def __init__(self, tick: Any, step: float) -> None:
        self.tick = tick
        self.step = step
        self.lock = threading.Lock()
        self.live = True
        self.first = True  # the first tick is due "now", not on a beat it could be late for
        self.last = self.due = 0.0  # (time.monotonic) previous tick, and when the next is due

    def cancel(self) -> None:
        """From any thread: no frame is submitted after this returns (a render in flight is waited for, briefly)."""
        self.live = False
        if self.lock.acquire(timeout=0.2):
            self.lock.release()


class RenderDriver:
    """Ticks every job on a fixed beat shared by all lights (the `math.floor(now / step) * step` grid), so their
    frames go out together. Its own thread, apart from the sockets': a slow render never holds a due send."""

    def __init__(self) -> None:
        self._jobs: list[RenderJob] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="light_show_studio render", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        thread, self._thread = self._thread, None
        self._stop.set()
        self._event.set()
        if thread is not None:
            thread.join(timeout)

    def add(self, job: RenderJob) -> None:
        now = time.monotonic()
        job.last, job.due = now, math.floor(now / job.step) * job.step
        with self._lock:
            self._jobs.append(job)
        self._event.set()

    def remove(self, job: RenderJob) -> None:
        job.cancel()
        with self._lock:
            if job in self._jobs:
                self._jobs.remove(job)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._event.clear()
            with self._lock:
                jobs = list(self._jobs)
            nxt = None
            for job in jobs:
                if job.due <= time.monotonic():
                    self._tick(job)
                if job.live and (nxt is None or job.due < nxt):
                    nxt = job.due
            if nxt is None:
                self._event.wait(IDLE)
            elif (left := nxt - time.monotonic()) > 0:
                self._event.wait(left)

    def _tick(self, job: RenderJob) -> None:
        with job.lock:
            if not job.live:
                return
            now = time.monotonic()
            try:
                step = job.tick(job, now, job.due)
            except Exception:  # noqa: BLE001  one bad light must not stop the others
                _LOGGER.exception("Render error")
                step = None
            job.first, job.last = False, now
            if step is None:
                job.live = False
            else:
                job.due += step
                if job.due - time.monotonic() < -step:  # fell behind: start the beat again from now
                    job.due = math.floor(time.monotonic() / step) * step
        if not job.live:
            with self._lock:
                if job in self._jobs:
                    self._jobs.remove(job)


class OutputPort:
    """One light's way out: its own non-blocking UDP socket and a queue of packet lists. Thread-safe."""

    def __init__(self, thread: "OutputThread", addr: tuple[str, int], stats: Any = None) -> None:
        self.addr = addr
        self._thread = thread
        self._sock: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._lock = threading.Lock()
        self._queue: deque[tuple[list[bytes], float, bool]] = deque()  # (packets, due, is_frame)
        self.sent = 0  # packet lists put on the wire (by the thread)
        self.failed = 0  # ...and those the socket refused
        self.coalesced = 0  # frames replaced by a newer one before they left
        if stats is not None:
            stats.port = self  # the light's LinkStats adds these counts to its own

    def submit(self, packets: list[bytes], due: float | None = None) -> bool:
        """A frame, to leave at `due` (time.perf_counter(); None = now). Replaces a frame still waiting at the
        end of the queue. False when the port is closed."""
        return self._put(packets, due, True)

    def control(self, packets: list[bytes]) -> bool:
        """Packets that must leave in order with the frames around them (none is ever replaced)."""
        return self._put(packets, None, False)

    def _put(self, packets: list[bytes], due: float | None, frame: bool) -> bool:
        item = (list(packets), 0.0 if due is None else due, frame)
        with self._lock:
            if self._sock is None:
                return False
            if frame and self._queue and self._queue[-1][2]:
                self._queue[-1] = item
                self.coalesced += 1
            else:
                self._queue.append(item)
        self._thread._wake()  # noqa: SLF001
        return True

    def close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            self._queue.clear()
        self._thread._forget(self)  # noqa: SLF001
        if sock is not None:
            sock.close()

    def _pump(self, now: float) -> tuple[bool, float | None]:
        """Send the head of the queue if it is due: (sent something, when the head is due if it is not yet)."""
        with self._lock:
            if self._sock is None or not self._queue:
                return False, None
            if self._queue[0][1] > now:
                return False, self._queue[0][1]
            packets = self._queue.popleft()[0]
            sock = self._sock
        for pkt in packets:
            try:
                sock.sendto(pkt, self.addr)
            except OSError:  # (BlockingIOError included) UDP: the next frame replaces it
                self.failed += 1
                return True, None
        self.sent += 1
        return True, None

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)


class OutputThread:
    """The daemon thread and its ports. `sync=True` has no thread: submit sends at once (for tests)."""

    def __init__(self, sync: bool = False) -> None:
        self.sync = sync
        self.users = 0  # lights attached (see async_attach); the last one out stops the thread
        self._ports: list[OutputPort] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.driver: RenderDriver | None = None if sync else RenderDriver()  # (sync=True: effects render on the loop)

    def open_port(self, addr: tuple[str, int], stats: Any = None) -> OutputPort:
        port = OutputPort(self, addr, stats)
        with self._lock:
            self._ports.append(port)
        return port

    def _forget(self, port: OutputPort) -> None:
        with self._lock:
            if port in self._ports:
                self._ports.remove(port)

    def start(self) -> None:
        if self.sync or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="light_show_studio output", daemon=True)
        self._thread.start()
        if self.driver is not None:
            self.driver.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and join the thread (blocking: call it from an executor)."""
        if self.driver is not None:
            self.driver.stop(timeout)
        thread, self._thread = self._thread, None
        self._stop.set()
        self._event.set()
        if thread is not None:
            thread.join(timeout)

    def _wake(self) -> None:
        if self.sync:
            self.pump(time.perf_counter())
        else:
            self._event.set()

    def pump(self, now: float) -> float | None:
        """Send what is due on every port, one item per port per round so no port starves another. Returns the
        time the next item is due (None: nothing queued)."""
        while True:
            with self._lock:
                ports = list(self._ports)
            sent_any, nxt = False, None
            for port in ports:
                sent, due = port._pump(now)  # noqa: SLF001
                sent_any = sent_any or sent
                if due is not None and (nxt is None or due < nxt):
                    nxt = due
            if not sent_any:
                return nxt

    def flush(self, timeout: float = 2.0) -> bool:
        """Send everything queued, due or not (tests, shutdown). True when every queue ran empty."""
        if self.sync:
            self.pump(float("inf"))
            return True
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            with self._lock:
                ports = list(self._ports)
            if not any(p.pending() for p in ports):
                return True
            time.sleep(0.001)
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            self._event.clear()  # (before looking, so a submit that lands meanwhile is not slept through)
            try:
                nxt = self.pump(time.perf_counter())
            except Exception:  # noqa: BLE001  the output must not die with one bad port
                _LOGGER.exception("Output thread error")
                nxt = None
            if nxt is None:
                self._event.wait(IDLE)
                continue
            left = nxt - time.perf_counter()
            if left > SPIN:
                self._event.wait(left - SPIN)
            else:
                time.sleep(0)  # yield, then look again: the last milliseconds are waited out here


async def async_attach(hass: Any, ctl: Any) -> None:
    """Give a light the integration's output thread (started with the first light). No-op when disabled."""
    if not OUTPUT_THREAD or not hasattr(ctl, "use_output"):
        return
    thread = hass.data.get(OUTPUT_KEY)
    if thread is None:
        from homeassistant.const import EVENT_HOMEASSISTANT_STOP  # noqa: PLC0415

        async def _stop(_event: Any) -> None:
            await hass.async_add_executor_job(thread.stop)

        thread = hass.data[OUTPUT_KEY] = OutputThread()
        thread.unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
        thread.start()
    thread.users += 1
    ctl.use_output(thread)


async def async_detach(hass: Any, ctl: Any) -> None:
    """The light is gone (its port is already closed by shutdown): the last one out stops and joins the thread."""
    thread = hass.data.get(OUTPUT_KEY)
    if thread is None or getattr(ctl, "port", None) is None:
        return
    ctl.port = None
    thread.users -= 1
    if thread.users <= 0:
        hass.data.pop(OUTPUT_KEY, None)
        if (unsub := getattr(thread, "unsub", None)) is not None:
            unsub()
        await hass.async_add_executor_job(thread.stop)
