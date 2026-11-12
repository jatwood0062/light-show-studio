"""Music sync without a microphone, the maths: a song file -> the features a phone would have sent, ahead of time.

The song is decoded to mono 16-bit PCM at 11025 Hz (ffmpeg) and cut into hops of 256 samples (about 23 ms). Each hop
gets what music_engine expects from the phone: level, bass, mid and treble (0-1, scaled by a slowly falling peak like
the phone's auto-gain) and a beat flag (onsets: the rise in the spectrum against its local average). The result is
kept next to the song as a small compressed sidecar (<song>.lssa), keyed by the song's size and mtime, so a song is
analysed once. The analysis works on plain sequences of samples and needs neither Home Assistant nor numpy (numpy is
used when it is there, because it is much quicker), so it is easy to test.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import tempfile
import zlib
from array import array
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

RATE = 11025  # samples a second after decoding
HOP = 256  # samples between two frames of features (about 23 ms)
WIN = 512  # samples each frame looks at (a Hann window centred on the hop)
HOP_S = HOP / RATE
BIN_HZ = RATE / WIN
BASS_HZ, MID_HZ = 150.0, 2000.0  # bass 20-150 Hz, mid 150-2000 Hz, treble above (to 5.5 kHz, half the sample rate)
BASS = (1, int(math.ceil(BASS_HZ / BIN_HZ)) + 1)  # FFT bins [from, to)
MID = (BASS[1], int(math.ceil(MID_HZ / BIN_HZ)) + 1)
TREBLE = (MID[1], WIN // 2)
DB_RANGE = 70.0  # a bin counts from -70 dB (0) to full scale (1), like the phone's analyser
TREBLE_FLUX = 0.25  # onsets are mostly low and mid; the treble counts a little
PEAK_DECAY = 0.995 ** (HOP_S / 0.05)  # the phone's auto-gain peak falls 0.5% every 50 ms
FLOOR, FLOOR_SHARE = 0.03, 0.3  # the peak never falls below this, nor below this share of the song's loudest
BEAT_WINDOW = 21  # hops each side for the local average (about 1 s in all, as the phone's 43 ticks)
BEAT_RATIO, BEAT_DELTA = 1.5, 0.5  # an onset beats the local average * ratio + the song's average * delta
BEAT_NEAR = 3  # ... and is the largest within this many hops each side
BEAT_GAP = 0.25  # seconds: at most one beat in this time (as the phone)
MIN_BPM, MAX_BPM = 30.0, 300.0
MAX_SECONDS = 20 * 60  # longer songs are not analysed
MAX_FILE_BYTES = 400 * 1024 * 1024
DECODE_TIMEOUT = 180.0
SIDECAR_EXT = ".lssa"
MAGIC = b"LSSA"
VERSION = 1
LOCK_KEY = "light_show_studio_analysis_lock"
CACHE_KEY = "light_show_studio_analysis_cache"
CACHE_SIZE = 4  # analyses kept in memory


class AnalysisError(Exception):
    """The song can't be analysed; the message is for the person."""


@dataclass(frozen=True)
class Analysis:
    """Per hop: level, bass, mid, treble (one byte each, 0-255) and beats (1 where a beat starts)."""
    level: bytes
    bass: bytes
    mid: bytes
    treble: bytes
    beats: bytes
    bpm: float | None = None
    rate: int = RATE
    hop: int = HOP

    @property
    def n(self) -> int:
        return len(self.level)

    @property
    def hop_s(self) -> float:
        return self.hop / self.rate

    @property
    def duration(self) -> float:
        return self.n * self.hop_s

    def index(self, position: float) -> int | None:
        """The hop at `position` seconds, or None before the start or after the end."""
        i = int(round(position / self.hop_s))
        return i if 0 <= i < self.n else None

    def beat_times(self) -> list[float]:
        return [i * self.hop_s for i, b in enumerate(self.beats) if b]

    def beat_between(self, after: int, upto: int) -> bool:
        """A beat in the hops after `after` up to and including `upto`."""
        return self.beats.find(b"\x01", max(0, after + 1), max(0, upto + 1)) >= 0

    def values(self, i: int) -> tuple[float, float, float, float]:
        """(level, bass, mid, treble), each 0-1, at hop `i`."""
        return self.level[i] / 255, self.bass[i] / 255, self.mid[i] / 255, self.treble[i] / 255


# --- frames: raw level, bands and onset strength per hop -----------------------------------------------
def _numpy() -> Any:
    try:
        import numpy  # noqa: PLC0415  (Home Assistant has it; the tests don't need it)
    except Exception:  # noqa: BLE001
        return None
    return numpy


_REV: list[int] = []
_COS: list[float] = []
_SIN: list[float] = []
_HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / WIN) for i in range(WIN)]
_SCALE = 4.0 / WIN  # a full-scale sine comes out near 1


def _fft_tables() -> None:
    bits = WIN.bit_length() - 1
    _REV.extend(int(format(i, f"0{bits}b")[::-1], 2) for i in range(WIN))
    _COS.extend(math.cos(2 * math.pi * k / WIN) for k in range(WIN // 2))
    _SIN.extend(-math.sin(2 * math.pi * k / WIN) for k in range(WIN // 2))


def _magnitudes(frame: Sequence[float]) -> list[float]:
    """|FFT| of a windowed frame of WIN samples, bins 0 to WIN/2 - 1 (plain radix-2)."""
    if not _REV:
        _fft_tables()
    re = [frame[j] for j in _REV]
    im = [0.0] * WIN
    size = 2
    while size <= WIN:
        half, step = size // 2, WIN // size
        for start in range(0, WIN, size):
            k = 0
            for j in range(start, start + half):
                c, s = _COS[k], _SIN[k]
                a, b = re[j + half], im[j + half]
                tr, ti = a * c - b * s, a * s + b * c
                re[j + half], im[j + half] = re[j] - tr, im[j] - ti
                re[j] += tr
                im[j] += ti
                k += step
        size *= 2
    return [math.hypot(re[k], im[k]) for k in range(WIN // 2)]


def _frames_py(samples: Sequence[float]) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    n = len(samples) // HOP + 1
    x = [0.0] * (WIN // 2) + [float(v) for v in samples] + [0.0] * (n * HOP + WIN - len(samples) - WIN // 2)
    level, bass, mid, treble, flux = [], [], [], [], []
    prev = [0.0] * (WIN // 2)
    for i in range(n):
        chunk = x[i * HOP:i * HOP + WIN]
        level.append(2.0 * math.sqrt(sum(v * v for v in chunk) / WIN))
        mags = _magnitudes([v * w for v, w in zip(chunk, _HANN)])
        cur = [min(1.0, max(0.0, (20.0 * math.log10(m * _SCALE + 1e-10) + DB_RANGE) / DB_RANGE)) for m in mags]
        bass.append(sum(cur[BASS[0]:BASS[1]]) / (BASS[1] - BASS[0]))
        mid.append(sum(cur[MID[0]:MID[1]]) / (MID[1] - MID[0]))
        treble.append(sum(cur[TREBLE[0]:TREBLE[1]]) / (TREBLE[1] - TREBLE[0]))
        rise = [c - p if c > p else 0.0 for c, p in zip(cur, prev)]
        flux.append(sum(rise[1:TREBLE[0]]) + TREBLE_FLUX * sum(rise[TREBLE[0]:]))
        prev = cur
    return level, bass, mid, treble, flux


def _frames_np(np: Any, samples: Any) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    src = np.asarray(samples, dtype=np.float32)
    n = len(src) // HOP + 1
    x = np.zeros(n * HOP + WIN, dtype=np.float32)
    x[WIN // 2:WIN // 2 + len(src)] = src
    window = np.asarray(_HANN, dtype=np.float64)
    out: list[list[Any]] = [[], [], [], [], []]
    prev = np.zeros((1, WIN // 2))
    block = 2048  # frames at a time, to bound memory
    for a in range(0, n, block):
        b = min(n, a + block)
        idx = np.arange(a, b)[:, None] * HOP + np.arange(WIN)[None, :]
        frames = x[idx].astype(np.float64)
        out[0].append(2.0 * np.sqrt((frames * frames).mean(axis=1)))
        mags = np.abs(np.fft.rfft(frames * window, axis=1))[:, :WIN // 2]
        cur = np.clip((20.0 * np.log10(mags * _SCALE + 1e-10) + DB_RANGE) / DB_RANGE, 0.0, 1.0)
        out[1].append(cur[:, BASS[0]:BASS[1]].mean(axis=1))
        out[2].append(cur[:, MID[0]:MID[1]].mean(axis=1))
        out[3].append(cur[:, TREBLE[0]:TREBLE[1]].mean(axis=1))
        rise = np.clip(cur - np.concatenate([prev, cur[:-1]]), 0.0, None)
        out[4].append(rise[:, 1:TREBLE[0]].sum(axis=1) + TREBLE_FLUX * rise[:, TREBLE[0]:].sum(axis=1))
        prev = cur[-1:]
    level, bass, mid, treble, flux = (np.concatenate(parts).tolist() for parts in out)
    return level, bass, mid, treble, flux


def frames(samples: Sequence[float], use_numpy: bool | None = None):
    """Raw (level, bass, mid, treble, onset strength) lists, one value per hop, for samples from -1 to 1.
    Hop i is centred on sample i * HOP, so it describes the song at i * HOP_S seconds."""
    np = _numpy() if use_numpy is not False else None
    if use_numpy and np is None:
        raise RuntimeError("numpy is not installed")
    return _frames_np(np, samples) if np is not None else _frames_py(samples)


# --- from raw frames to what the engine is fed -----------------------------------------------------------
def auto_gain(raw: Sequence[float]) -> bytes:
    """Scale by a slowly falling peak (the phone's auto-gain) to 0-255. The peak never falls below a share of the
    song's loudest, so a quiet passage stays quieter than a loud one."""
    floor = max(FLOOR, FLOOR_SHARE * max(raw, default=0.0))
    peak, out = floor, bytearray(len(raw))
    for i, v in enumerate(raw):
        peak = max(v, peak * PEAK_DECAY, floor)
        out[i] = int(round(255 * min(1.0, max(0.0, v) / peak)))
    return bytes(out)


def find_beats(flux: Sequence[float], hop_s: float = HOP_S) -> list[int]:
    """The hops where a beat starts: onset strength above its local average (adaptive threshold), the largest
    nearby, and not within BEAT_GAP of the beat before."""
    n = len(flux)
    if n == 0:
        return []
    total = 0.0
    sums = [0.0]
    for v in flux:
        total += v
        sums.append(total)
    base = BEAT_DELTA * total / n
    gap = max(1, int(round(BEAT_GAP / hop_s)))
    beats: list[int] = []
    for i, v in enumerate(flux):
        if v <= 0.0:
            continue
        a, b = max(0, i - BEAT_WINDOW), min(n, i + BEAT_WINDOW + 1)
        if v <= BEAT_RATIO * (sums[b] - sums[a]) / (b - a) + base:
            continue
        near = flux[max(0, i - BEAT_NEAR):i + BEAT_NEAR + 1]
        if v < max(near) or (beats and i - beats[-1] < gap):
            continue
        beats.append(i)
    return beats


def tempo(beats: Sequence[int], hop_s: float = HOP_S) -> float | None:
    """Beats a minute from the median gap between beats (gaps over 2 s don't count), or None."""
    gaps = sorted(g for g in ((b - a) * hop_s for a, b in zip(beats, beats[1:])) if 0 < g < 2.0)
    if len(gaps) < 3:
        return None
    bpm = 60.0 / gaps[len(gaps) // 2]
    return round(bpm, 1) if MIN_BPM <= bpm <= MAX_BPM else None


def analyse(samples: Sequence[float], use_numpy: bool | None = None) -> Analysis:
    """Features and beats for mono samples (-1 to 1) at RATE samples a second."""
    level, bass, mid, treble, flux = frames(samples, use_numpy)
    if flux:
        flux[0] = 0.0  # the song starting is not a beat
    beats = find_beats(flux)
    marks = bytearray(len(flux))
    for i in beats:
        marks[i] = 1
    return Analysis(auto_gain(level), auto_gain(bass), auto_gain(mid), auto_gain(treble), bytes(marks), tempo(beats))


def analyse_pcm(pcm: bytes) -> Analysis:
    """analyse() for signed 16-bit little-endian mono PCM at RATE samples a second."""
    if len(pcm) > MAX_SECONDS * RATE * 2:
        raise AnalysisError(f"The song is longer than {MAX_SECONDS // 60} minutes")
    pcm = pcm[:len(pcm) - len(pcm) % 2]
    np = _numpy()
    if np is not None:
        return analyse(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0, True)
    ints = array("h")
    ints.frombytes(pcm)
    if ints.itemsize != 2:
        raise AnalysisError("This system can't read 16-bit audio")
    if sys.byteorder != "little":
        ints.byteswap()
    return analyse([v / 32768.0 for v in ints], False)


# --- the sidecar: <song>.lssa ------------------------------------------------------------------------------
def sidecar_path(song: str) -> str:
    return song + SIDECAR_EXT


def file_key(song: str) -> tuple[int, int]:
    st = os.stat(song)
    return st.st_size, st.st_mtime_ns


def pack(a: Analysis, key: tuple[int, int]) -> bytes:
    head = {"v": VERSION, "rate": a.rate, "hop": a.hop, "n": a.n, "bpm": a.bpm, "size": key[0], "mtime_ns": key[1]}
    body = json.dumps(head, separators=(",", ":")).encode() + b"\n" + a.level + a.bass + a.mid + a.treble + a.beats
    return MAGIC + bytes([VERSION]) + zlib.compress(body, 9)


def unpack(data: bytes, key: tuple[int, int] | None = None) -> Analysis | None:
    """The analysis in a sidecar, or None when it is damaged, of another version, or (with `key`) of another file."""
    try:
        if data[:4] != MAGIC or data[4] != VERSION:
            return None
        raw = zlib.decompressobj().decompress(data[5:], MAX_SECONDS * RATE // 16 * 5 + 4096)
        line, _, body = raw.partition(b"\n")
        head = json.loads(line)
        n, bpm = head["n"], head["bpm"]
        if head["v"] != VERSION or head["rate"] != RATE or head["hop"] != HOP or not isinstance(n, int) \
                or n < 0 or len(body) != 5 * n or (key is not None and (head["size"], head["mtime_ns"]) != tuple(key)):
            return None
        if bpm is not None and (isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not MIN_BPM <= bpm <= MAX_BPM):
            bpm = None
        return Analysis(*(body[i * n:(i + 1) * n] for i in range(5)), bpm=float(bpm) if bpm is not None else None)
    except (ValueError, KeyError, IndexError, TypeError, zlib.error):
        return None


def read_sidecar(song: str) -> Analysis | None:
    """The saved analysis of `song` if it is still the same file (size and mtime). Blocking."""
    try:
        key = file_key(song)
        with open(sidecar_path(song), "rb") as f:
            data = f.read(8 * 1024 * 1024)
    except OSError:
        return None
    return unpack(data, key)


def write_sidecar(song: str, a: Analysis, key: tuple[int, int]) -> bool:
    """Save the analysis next to the song (a temp file, then a rename, so it is never half written). Blocking."""
    target = sidecar_path(song)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".lssa-", dir=os.path.dirname(target))
        with os.fdopen(fd, "wb") as f:
            f.write(pack(a, key))
        os.replace(tmp, target)
    except OSError as err:
        _LOGGER.debug("Could not save the analysis of %s: %s", song, err)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False
    return True


# --- decoding and the whole job -------------------------------------------------------------------------------
async def async_decode(path: str) -> bytes:
    """The song as mono signed 16-bit PCM at RATE samples a second, by ffmpeg."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le",
            "-t", str(MAX_SECONDS + 5), "pipe:1",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError as err:
        raise AnalysisError("ffmpeg could not be started") from err
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), DECODE_TIMEOUT)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        raise
    if proc.returncode != 0 or len(out) < 2:
        raise AnalysisError("The song could not be decoded")
    return out


def _check(path: str) -> tuple[int, int]:
    try:
        key = file_key(path)
    except OSError as err:
        raise AnalysisError("The song could not be read") from err
    if key[0] > MAX_FILE_BYTES:
        raise AnalysisError("The song file is too large to analyse")
    return key


def _finish(path: str, pcm: bytes, key: tuple[int, int]) -> Analysis:
    a = analyse_pcm(pcm)
    try:
        if file_key(path) == key:  # the file changed while it was decoded: don't keep the result
            write_sidecar(path, a, key)
    except OSError:
        pass
    return a


async def async_analysis(hass: Any, path: str,
                         decode: Callable[[str], Awaitable[bytes]] | None = None) -> Analysis:
    """The analysis of a song file: from memory, its sidecar, or made now (one song at a time, off the event loop)."""
    cache: dict[str, tuple[tuple[int, int], Analysis]] = hass.data.setdefault(CACHE_KEY, {})
    lock = hass.data.get(LOCK_KEY)
    if lock is None:
        lock = hass.data[LOCK_KEY] = asyncio.Lock()
    async with lock:
        key = await hass.async_add_executor_job(_check, path)
        hit = cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        a = await hass.async_add_executor_job(read_sidecar, path)
        if a is None:
            try:
                pcm = await (decode or async_decode)(path)
            except asyncio.TimeoutError as err:
                raise AnalysisError("Decoding the song took too long") from err
            if len(pcm) > MAX_SECONDS * RATE * 2:
                raise AnalysisError(f"The song is longer than {MAX_SECONDS // 60} minutes")
            _LOGGER.debug("Analysing %s (%.0f s)", path, len(pcm) / 2 / RATE)
            a = await hass.async_add_executor_job(_finish, path, pcm, key)
        cache.pop(path, None)
        cache[path] = (key, a)
        while len(cache) > CACHE_SIZE:
            del cache[next(iter(cache))]
        return a
