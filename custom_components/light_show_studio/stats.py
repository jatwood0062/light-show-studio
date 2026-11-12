"""How well frames are getting out to one light: counters, and a rolling window of the frame rate and gaps."""
from __future__ import annotations

import time

WINDOW = 10  # seconds the frame rate, longest gap and wake lateness look back over
LATE = 0.5  # an animation tick that wakes more than this share of a frame period late counts as late


class LinkStats:
    """Counted on every frame, so each call is a few additions: the window is one bucket per second (frames,
    longest gap, worst wake), cleared as the clock moves on, never a list of timestamps."""

    def __init__(self) -> None:
        self.sent = 0  # frames put on the wire
        self.failed = 0  # ...of which the socket refused (no route, buffer full)
        self.merged = 0  # show frames that came too soon and were replaced by a newer one
        self.late = 0  # animation ticks that woke late
        self.latency_ms: float | None = None  # the last health check's reply time
        self.port = None  # the output thread's port (output.py), whose failures and replaced frames count here too
        self._n = [0] * WINDOW
        self._gap = [0.0] * WINDOW
        self._wake = [0.0] * WINDOW
        self._sec: int | None = None  # the second the newest bucket holds
        self._last: float | None = None  # when the last frame went out
        self._start = 0.0  # when the frames now in the window began

    def _bucket(self, now: float) -> int:
        sec = int(now)
        if sec != self._sec:
            if self._sec is not None:
                for s in range(max(self._sec + 1, sec - WINDOW + 1), sec + 1):
                    i = s % WINDOW
                    self._n[i], self._gap[i], self._wake[i] = 0, 0.0, 0.0
            self._sec = sec
        return sec % WINDOW

    def frame(self, ok: bool = True, now: float | None = None) -> None:
        """A frame was sent (`ok` False: it couldn't leave)."""
        now = time.monotonic() if now is None else now
        i = self._bucket(now)
        self.sent += 1
        if not ok:
            self.failed += 1
        self._n[i] += 1
        gap = None if self._last is None else now - self._last
        if gap is None or gap >= WINDOW:
            self._start = now  # the first frame, or the first after a pause: a pause is not a gap
        elif gap > self._gap[i]:
            self._gap[i] = gap
        self._last = now

    def merge(self) -> None:
        self.merged += 1

    def woke(self, late: float, period: float, now: float | None = None) -> None:
        """An animation tick woke `late` seconds after it was due; `period` is the frame period."""
        if late > LATE * period:
            self.late += 1
        if late > 0:
            i = self._bucket(time.monotonic() if now is None else now)
            if late > self._wake[i]:
                self._wake[i] = late

    def snapshot(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        self._bucket(now)
        # the newest bucket is a part of a second, so the window is 9 whole seconds and that part; a stream
        # younger than that is measured over its own age (at least a second, so one frame isn't a huge rate)
        span = min(WINDOW - 1 + now % 1.0, now - self._start)
        n = sum(self._n)
        port = self.port
        return {
            "fps": round(n / max(span, 1.0), 1) if n else 0.0,
            "gap_ms": round(max(self._gap) * 1000),
            "wake_ms": round(max(self._wake) * 1000),
            "sent": self.sent, "failed": self.failed + (port.failed if port else 0),
            "merged": self.merged + (port.coalesced if port else 0), "late": self.late,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms),
        }
