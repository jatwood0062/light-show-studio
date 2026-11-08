"""Music sync, the maths: audio features from a phone's microphone -> smooth envelopes -> frames.

The phone sends level / bass / mid / treble (0-1) and a beat flag about 20-40 times a second. This turns them into
brightness that rises quickly and falls slowly (attack / release envelopes), so it follows the music without
flickering. If frames stop for TIMEOUT seconds the picture eases back to the pattern's normal look, and eases into
the music again when they return. Nothing here needs Home Assistant, so it is easy to test.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import fade, patterns
from .effects import hash01
from .mapped_patterns import ZonePlan
from .mapping import Zone, run_positions
from .protocol import Colour

PULSE, BEAT_CHASE, SPECTRUM, FLASH = "Pulse", "Beat Chase", "Spectrum", "Flash"
BEAT_SCATTER, GRAVITY_DROP, BEAT_SHELL = "Beat Scatter", "Gravity Drop", "Beat Shell"
EFFECTS = (PULSE, BEAT_CHASE, SPECTRUM, FLASH, BEAT_SCATTER, GRAVITY_DROP, BEAT_SHELL)
TIMEOUT = 3.0  # seconds without a frame before the pattern's normal look takes over
DEFAULT_SENSITIVITY = 50
MAX_BPM = 300.0
MIN_BPM = 30.0
MIN_BEAT_GAP = 0.12  # seconds: closer beats are one beat
# envelopes: time constants in seconds (smaller = quicker)
ATTACK, RELEASE = 0.035, 0.30  # level
BAND_ATTACK, BAND_RELEASE = 0.05, 0.35  # bass / mid / treble
FLASH_DECAY, KICK_DECAY = 0.15, 0.20
CHASE_EASE = 0.07  # the chase glides to its next step
LIVE_UP, LIVE_DOWN = 0.2, 0.9  # blending to / from the idle look
FLOOR = 0.08  # the dimmest the music brightness gets while live
SCATTER_SHARE, SCATTER_DECAY = 0.3, 0.28  # Beat Scatter: the share of bulbs that flash on a beat, and how fast they fade
DROP_FALL = 0.45  # Gravity Drop: seconds for the band to fall from top to bottom
DROP_BOUNCE = 0.45  # ... and the speed it keeps after each bounce
SHELL_SPEED, SHELL_FADE = 3.5, 0.9  # Beat Shell: how quickly the shell opens, and how long it lasts


# --- strict parsing of what the phone sends ---------------------------------------------------------
@dataclass(frozen=True)
class Features:
    level: float
    bass: float
    mid: float
    treble: float
    beat: bool
    bpm: float | None = None


def _unit(msg: Mapping[str, Any], key: str) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0.0 <= v <= 1.0:
        raise ValueError(f"{key} must be a number from 0 to 1")
    return float(v)


def parse_features(msg: Mapping[str, Any]) -> Features:
    """Level, bass, mid, treble (numbers 0-1), beat (true / false), bpm (optional, 30-300). Anything else -> ValueError."""
    beat = msg.get("beat")
    if not isinstance(beat, bool):
        raise ValueError("beat must be true or false")
    bpm = msg.get("bpm")
    if bpm is not None:
        if isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not math.isfinite(bpm) \
                or not MIN_BPM <= bpm <= MAX_BPM:
            raise ValueError(f"bpm must be a number from {MIN_BPM:g} to {MAX_BPM:g}")
        bpm = float(bpm)
    return Features(_unit(msg, "level"), _unit(msg, "bass"), _unit(msg, "mid"), _unit(msg, "treble"), beat, bpm)


def gain_for(sensitivity: int) -> float:
    """Sensitivity 1-100 (50 = as sent) -> a gain from 0.2 to 5 on a log scale."""
    return 5.0 ** ((max(1, min(100, int(sensitivity))) - 50) / 50.0)


def _follow(value: float, target: float, dt: float, attack: float, release: float) -> float:
    tau = attack if target > value else release
    return value + (target - value) * (1 - math.exp(-dt / tau)) if dt > 0 else value


# --- the state: envelopes that follow the features -----------------------------------------------------
class MusicState:
    def __init__(self, sensitivity: int = DEFAULT_SENSITIVITY, now: float = 0.0) -> None:
        self.gain = gain_for(sensitivity)
        self.level = self.bass = self.mid = self.treble = 0.0  # smoothed 0-1
        self.flash = self.kick = 0.0  # jump to 1 on a beat, then decay
        self.chase_target = 0  # beats counted
        self.chase_pos = 0.0  # eases to chase_target
        self.live_w = 0.0  # 1 = all music, 0 = all idle look
        self.beats = 0
        self.beat_age = 60.0  # seconds since the last beat (capped at 60)
        self.prev_gap = 60.0  # seconds between the last two beats
        self.bpm: float | None = None
        self._raw = (0.0, 0.0, 0.0, 0.0)
        self._last_frame: float | None = None
        self._last_beat = -1e9
        self._last_adv = now

    def feed(self, now: float, f: Features) -> None:
        g = self.gain
        self._raw = tuple(min(1.0, v * g) for v in (f.level, f.bass, f.mid, f.treble))  # type: ignore[assignment]
        self._last_frame = now
        if f.bpm is not None:
            self.bpm = f.bpm
        if f.beat and now - self._last_beat >= MIN_BEAT_GAP:
            self.prev_gap = min(60.0, max(0.0, now - self._last_beat))
            self._last_beat = now
            self.beat_age = 0.0
            self.flash = self.kick = 1.0
            self.chase_target += 1
            self.beats += 1

    def is_live(self, now: float) -> bool:
        return self._last_frame is not None and now - self._last_frame <= TIMEOUT

    def advance(self, now: float) -> None:
        """Move the envelopes on to `now`."""
        dt = max(0.0, min(0.5, now - self._last_adv))
        self._last_adv = now
        live = self.is_live(now)
        lv, ba, mi, tr = self._raw if live else (0.0, 0.0, 0.0, 0.0)
        self.level = _follow(self.level, lv, dt, ATTACK, RELEASE)
        self.bass = _follow(self.bass, ba, dt, BAND_ATTACK, BAND_RELEASE)
        self.mid = _follow(self.mid, mi, dt, BAND_ATTACK, BAND_RELEASE)
        self.treble = _follow(self.treble, tr, dt, BAND_ATTACK, BAND_RELEASE)
        self.flash *= math.exp(-dt / FLASH_DECAY)
        self.kick *= math.exp(-dt / KICK_DECAY)
        self.chase_pos += (self.chase_target - self.chase_pos) * (1 - math.exp(-dt / CHASE_EASE))
        self.live_w = _follow(self.live_w, 1.0 if live else 0.0, dt, LIVE_UP, LIVE_DOWN)
        self.beat_age = min(60.0, max(0.0, now - self._last_beat))


# --- frames --------------------------------------------------------------------------------------------
def _bands(tp: Sequence[float]) -> tuple[list[float], list[float], list[float]]:
    """For each position 0-1 (bottom to top, or start to end), how much it belongs to the bass, mid and treble
    third: overlapping triangles, so the edges between thirds blend."""
    w0, w1, w2 = [], [], []
    for p in tp:
        a = max(0.0, 1 - abs(p - 1 / 6) * 3)
        b = max(0.0, 1 - abs(p - 1 / 2) * 3)
        c = max(0.0, 1 - abs(p - 5 / 6) * 3)
        total = (a + b + c) or 1.0
        w0.append(a / total)
        w1.append(b / total)
        w2.append(c / total)
    return w0, w1, w2


def _drop_height(age: float) -> float:
    """Height (1 = top, 0 = bottom) of a body dropped from the top `age` seconds ago, bouncing on the floor and
    losing speed on each bounce. A pure function of the age."""
    g = 2.0 / (DROP_FALL * DROP_FALL)
    if age < DROP_FALL:
        return 1.0 - 0.5 * g * age * age
    a, v = age - DROP_FALL, g * DROP_FALL * DROP_BOUNCE
    for _ in range(8):
        flight = 2.0 * v / g
        if a < flight:
            return v * a - 0.5 * g * a * a
        a -= flight
        v *= DROP_BOUNCE
    return 0.0


def _toward_white(c: Colour, v: float, white: float) -> Colour:
    """The colour c at brightness v, pulled `white` (0-1) of the way to white."""
    r, g, b = c[0] * v, c[1] * v, c[2] * v
    return int(r + (255 - r) * white), int(g + (255 - g) * white), int(b + (255 - b) * white)


def _shell(age: float, dn: float) -> float:
    """0-1 brightness at normalised distance dn (0 centre, 1 edge) from a shell opened `age` seconds ago."""
    r = 1.25 * (1.0 - math.exp(-age * SHELL_SPEED))
    thick = 0.12 + 0.08 * min(age, 1.5)
    d = 1.0 - abs(dn - r) / thick
    return d * d * math.exp(-age / SHELL_FADE) if d > 0.0 else 0.0


def render_music(effect: str, st: MusicState, base: list[Colour], ranked: list[Colour], rank: list[int],
                 bands: tuple[list[float], list[float], list[float]], stride: int,
                 heights: Sequence[float] | None = None, dist: Sequence[float] | None = None) -> list[Colour]:
    """One frame of a music effect from the smoothed state. base = the pattern's colour per pixel; ranked / rank =
    those colours in order along the string (and each pixel's place in it), for the chase. heights (0 bottom to 1
    top) is for Gravity Drop and dist (0 at the centre to 1 at the edge) for Beat Shell; both default to the place
    along the string."""
    n = len(base)
    if effect == BEAT_SCATTER:
        # on every beat a fresh random handful of bulbs flashes to full (and whitens) then fades; the previous
        # beat's handful is still fading, so quick beats overlap smoothly. Which bulbs: a hash of (bulb, beat number).
        rest = FLOOR + 0.22 * st.level
        cur = math.exp(-st.beat_age / SCATTER_DECAY) if st.beats else 0.0
        old = math.exp(-(st.beat_age + st.prev_gap) / SCATTER_DECAY) if st.beats > 1 else 0.0
        k = st.beats
        out = []
        for i, c in enumerate(base):
            f = max(cur if hash01(i, k) < SCATTER_SHARE else 0.0, old if hash01(i, k - 1) < SCATTER_SHARE else 0.0)
            out.append(_toward_white(c, rest + (1 - rest) * f, 0.3 * f))
        return out
    if effect == GRAVITY_DROP:
        # each beat drops a bright band from the top; it falls under gravity, lands with a flash and bounces
        # a few times before settling and fading. Between beats the pattern glows with the bass.
        hs = heights if heights is not None else [i / max(1, n - 1) for i in range(n)]
        age = st.beat_age
        hb = _drop_height(age)
        rest = FLOOR + 0.2 * st.bass
        amp = (0.65 + 0.35 * st.bass) * (1.0 if age < 1.6 else max(0.0, 1.0 - (age - 1.6) / 1.0)) if st.beats else 0.0
        falling = age < DROP_FALL
        land = math.exp(-(age - DROP_FALL) / 0.12) if age >= DROP_FALL else 0.0
        out = []
        for c, h in zip(base, hs):
            d = (h - hb) / 0.13
            band = (1 - abs(d)) ** 2 if abs(d) < 1.0 else 0.0
            if falling and h > hb:
                band = max(band, 0.5 * math.exp(-(h - hb) / 0.2))  # a trail above the falling band
            if land > 0.0 and h < 0.15:
                band = max(band, land * (1 - h / 0.15))  # the flash where it lands
            f = band * amp
            out.append(_toward_white(c, rest + (1 - rest) * f, 0.25 * f))
        return out
    if effect == BEAT_SHELL:
        # each beat opens a ring of light from the centre of the area (or the middle of the string) out to the edge
        ds = dist if dist is not None else [abs(2.0 * i / max(1, n - 1) - 1.0) for i in range(n)]
        rest = FLOOR + 0.2 * st.level
        age, prev = st.beat_age, st.beat_age + st.prev_gap
        out = []
        for c, d in zip(base, ds):
            f = _shell(age, d) if st.beats else 0.0
            if st.beats > 1:
                f = max(f, _shell(prev, d))
            out.append(_toward_white(c, min(1.0, rest + (1 - rest) * f), 0.3 * f))
        return out
    if effect == FLASH:
        v = FLOOR + (1 - FLOOR) * st.flash ** 1.5
        white = 0.35 * st.flash
        out = []
        for c in base:
            r, g, b = c[0] * v, c[1] * v, c[2] * v
            out.append((int(r + (255 - r) * white), int(g + (255 - g) * white), int(b + (255 - b) * white)))
        return out
    if effect == SPECTRUM:
        w0, w1, w2 = bands
        bs, ms, ts = st.bass, st.mid, st.treble
        out = []
        for c, a, b, t in zip(base, w0, w1, w2):
            v = FLOOR + (1 - FLOOR) * min(1.0, a * bs + b * ms + t * ts)
            out.append((int(c[0] * v), int(c[1] * v), int(c[2] * v)))
        return out
    if effect == BEAT_CHASE:
        v = min(1.0, 0.45 + 0.4 * st.level + 0.25 * st.kick)
        pos = st.chase_pos * stride
        list_at = patterns.list_at
        return [fade_px(list_at(ranked, r - pos), v) for r in rank] if ranked else []
    v = FLOOR + (1 - FLOOR) * min(1.0, st.level + st.kick * 0.45)  # Pulse
    return [(int(c[0] * v), int(c[1] * v), int(c[2] * v)) for c in base]


def fade_px(c: Colour, v: float) -> Colour:
    return int(c[0] * v), int(c[1] * v), int(c[2] * v)


class MusicPlan:
    """Looks like a ZonePlan to the zone runner (the zone, the slices, render(t) and split()), but draws music."""

    animated = True

    def __init__(self, plan: ZonePlan, effect: str, sensitivity: int, idle: ZonePlan,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if effect not in EFFECTS:
            raise ValueError("Unknown music effect")
        self.inner, self.idle = plan, idle
        self.zone, self.slices, self.n = plan.zone, plan.slices, plan.n
        self.pattern, self.effect = plan.pattern, effect
        self._clock = clock
        self.state = MusicState(sensitivity, clock())
        n = self.n
        base = plan.render(0.0) if n else []
        self.base = base
        u = getattr(plan, "u", None) or [i / max(1, n - 1) for i in range(n)]
        order = sorted(range(n), key=u.__getitem__)
        self.ranked = [base[i] for i in order]
        self.rank = [0] * n
        for r, i in enumerate(order):
            self.rank[i] = r
        self.heights = self._heights(plan, n)
        self.bands = _bands(self.heights)
        self.dist = self._centre_dist(plan, n)
        self.stride = max(1, n // 30, patterns._spec(plan.pattern)[1])  # noqa: SLF001

    @staticmethod
    def _heights(plan: ZonePlan, n: int) -> list[float]:
        """0 (bottom) to 1 (top) for each pixel: its height in the area in spatial mode, else its place on the string."""
        if getattr(plan, "mapfx", False) or getattr(plan, "spatial", False):
            from .mapped_patterns import zone_u
            return zone_u(plan.slices, {"dir": [0.0, 1.0, 0.0]})
        return [i / max(1, n - 1) for i in range(n)]

    @staticmethod
    def _centre_dist(plan: ZonePlan, n: int) -> list[float]:
        """0 (the centre) to 1 (the farthest) for each pixel, for Beat Shell: its 3D distance from the middle of its
        area in spatial mode (the axis' centre point if it has one, else the middle of the area's pixels; each
        area is its own space), else its distance from the middle of the string."""
        if not (getattr(plan, "mapfx", False) or getattr(plan, "spatial", False)):
            return [abs(2.0 * i / max(1, n - 1) - 1.0) for i in range(n)]
        areas: dict[str, tuple[list[int], list[Sequence[float]]]] = {}
        for s in plan.slices:
            pos = run_positions(s.run)
            if s.run.reverse:
                pos.reverse()
            idx, pts = areas.setdefault(s.run.area, ([], []))
            idx.extend(range(s.start, s.start + s.count))
            pts.extend(pos)
        centre = plan.zone.axis.get("centre") if isinstance(plan.zone.axis, Mapping) else None
        out = [0.0] * n
        for idx, pts in areas.values():
            xyz = [(p[0], p[1], p[2] if len(p) > 2 else 0.5) for p in pts]
            if isinstance(centre, (list, tuple)) and len(centre) in (2, 3):
                c = (float(centre[0]), float(centre[1]), float(centre[2]) if len(centre) == 3 else 0.5)
            else:
                c = tuple((min(q[a] for q in xyz) + max(q[a] for q in xyz)) / 2 for a in range(3))
            d = [math.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2) for x, y, z in xyz]
            top = max(max(d), 1e-6)
            for i, v in zip(idx, d):
                out[i] = v / top
        return out

    def feed(self, f: Features) -> None:
        now = self._clock()
        self.state.advance(now)  # bring the envelopes up to date first, so a beat shows at full strength in the next frame
        self.state.feed(now, f)

    @property
    def live(self) -> bool:
        return self.state.is_live(self._clock())

    def render(self, _t: float) -> list[Colour]:
        if self.n == 0:
            return []
        now = self._clock()
        st = self.state
        st.advance(now)
        w = st.live_w
        if w < 0.01:
            return fade.scale_frame(self.idle.render(now), 0.7)
        music = render_music(self.effect, st, self.base, self.ranked, self.rank, self.bands, self.stride,
                             self.heights, self.dist)
        if w > 0.99:
            return music
        return fade.lerp_frame(fade.scale_frame(self.idle.render(now), 0.7), music, w)

    def split(self, colours: Sequence[Colour]):
        return self.inner.split(colours)


def idle_effect_for(pattern: str) -> str:
    """The pattern's normal look when there is no music: a pattern of yours uses its default effect, a built-in one
    a steady glow."""
    up = patterns.user_pattern(pattern)
    return up.default_effect if up is not None and pattern not in patterns.PATTERNS else patterns.STEADY


def make_plan(zone: Zone, runs: Mapping[str, Any], effect: str, sensitivity: int, idle_effect: str | None = None,
              clock: Callable[[], float] = time.monotonic) -> MusicPlan:
    """A music plan for a zone (made up on the fly from the targets): the music look and the idle look share its pixels."""
    from dataclasses import replace
    pattern = patterns.canonical(zone.pattern) or patterns.DEFAULT_PATTERN
    steady = ZonePlan(replace(zone, pattern=pattern, effect=patterns.STEADY), runs)
    idle_name = idle_effect if idle_effect in patterns.ALL_EFFECTS else idle_effect_for(pattern)
    idle = ZonePlan(replace(zone, pattern=pattern, effect=idle_name), runs)
    return MusicPlan(steady, effect, sensitivity, idle, clock)


class RateLimiter:
    """A token bucket: at most `rate` calls a second on average, with a small burst."""

    def __init__(self, rate: float = 40.0, burst: float = 8.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate, self.burst, self._clock = rate, burst, clock
        self._tokens, self._at = burst, clock()

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._at) * self.rate)
        self._at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
