"""Reader for xLights / FPP .fseq sequence files (v1, and v2 uncompressed, zstd or zlib).

Blocking file I/O; call `load` and `FseqFile.frame` from an executor. Frames come back
as absolute channel data (channel 1 = byte 0), with sparse ranges put back in place.
"""
from __future__ import annotations

import io
import struct
import threading
import zlib
from dataclasses import dataclass

PRELOAD_LIMIT = 32 * 1024 * 1024  # decode the whole show up front when it is at most this big
RAW_BLOCK_FRAMES = 256  # uncompressed files are read in chunks of this many frames
CACHE_BLOCKS = 4
MAX_FRAMES = 10_000_000  # ~110 hours at 25 fps
MAX_CHANNELS = 4 * 1024 * 1024  # per frame (1.4 million pixels); far more than the receiver ever uses
MAX_BLOCK_BYTES = 64 * 1024 * 1024  # decoded size of one block, whatever the header claims


class FseqError(Exception):
    """File is not a sequence this reader understands."""


@dataclass
class _Block:
    first: int  # first frame
    count: int  # frames in the block
    offset: int  # byte offset in the file
    length: int  # stored bytes
    codec: int  # 0 none, 1 zstd, 2 zlib


class FseqFile:
    _preloaded = False

    def __init__(self, path: str, data: bytes | None = None) -> None:
        self.path = path
        if data is None:
            with open(path, "rb") as f:
                head = f.read(65536)  # header + index; the data offset is a u16
                f.seek(0, 2)
                size = f.tell()
        else:
            head, size = data[:65536], len(data)
        self._parse(head, size)
        self._data = data
        self._cache: dict[int, bytes] = {}
        self._lock = threading.Lock()

    # --- header -----------------------------------------------------------
    def _parse(self, h: bytes, size: int) -> None:
        if len(h) < 28 or h[:4] not in (b"PSEQ", b"FSEQ"):
            raise FseqError("not an .fseq file")
        data_off, minor, major, _hdr_len, chans, frames, step = struct.unpack_from("<HBBHIIB", h, 4)
        self.version = f"{major}.{minor}"
        if frames > MAX_FRAMES:
            raise FseqError(f"too many frames ({frames})")
        self.frames, self.step_ms = frames, step or 50
        self.ranges: list[tuple[int, int]] = []
        codec, entries = 0, []
        if major == 1:
            pass
        elif major == 2:
            codec = h[20] & 0x0F
            nblocks = h[21] | ((h[20] & 0xF0) << 4)
            nranges = h[22]
            pos = 32
            if pos + nblocks * 8 + nranges * 6 > len(h):
                raise FseqError("header is truncated")
            for _ in range(nblocks):
                entries.append(struct.unpack_from("<II", h, pos))
                pos += 8
            for _ in range(nranges):
                start = int.from_bytes(h[pos : pos + 3], "little")
                count = int.from_bytes(h[pos + 3 : pos + 6], "little")
                if count:
                    self.ranges.append((start, count))
                pos += 6
        else:
            raise FseqError(f"unsupported .fseq version {self.version}")
        if codec not in (0, 1, 2):
            raise FseqError(f"unsupported compression type {codec}; export uncompressed")

        # bytes per stored frame; with sparse ranges only those channels are stored
        self.stride = sum(c for _, c in self.ranges) if self.ranges else chans
        # highest absolute channel a frame covers
        self.channels = max(s + c for s, c in self.ranges) if self.ranges else chans
        if self.stride <= 0:
            raise FseqError("sequence has no channels")
        if self.stride > MAX_CHANNELS or self.channels > MAX_CHANNELS:
            raise FseqError("sequence has too many channels")
        self.raw_block_frames = max(1, min(RAW_BLOCK_FRAMES, MAX_BLOCK_BYTES // self.stride))

        self._blocks: list[_Block] = []
        if codec == 0:
            if data_off + frames * self.stride > size:
                raise FseqError("file is shorter than its header says")
            for first in range(0, frames, self.raw_block_frames):
                n = min(self.raw_block_frames, frames - first)
                self._blocks.append(_Block(first, n, data_off + first * self.stride, n * self.stride, 0))
        else:
            entries = [e for e in entries if e[1] > 0]  # xLights pads the index with empty entries
            off = data_off
            for i, (first, length) in enumerate(entries):
                nxt = entries[i + 1][0] if i + 1 < len(entries) else frames
                if not 0 < nxt - first <= frames or (nxt - first) * self.stride > MAX_BLOCK_BYTES:
                    raise FseqError("block index is not valid")
                self._blocks.append(_Block(first, nxt - first, off, length, codec))
                off += length
            if off > size:
                raise FseqError("file is shorter than its header says")
            if frames and not self._blocks:
                raise FseqError("compressed file has no block index")
            if codec == 1:
                _zstd()  # fail now, not mid-show

    @property
    def duration(self) -> float:
        return self.frames * self.step_ms / 1000

    # --- frames -----------------------------------------------------------
    def _block_index(self, i: int) -> int:
        lo, hi = 0, len(self._blocks) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._blocks[mid].first <= i:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _read_block(self, b: _Block) -> bytes:
        if self._data is not None:
            raw = self._data[b.offset : b.offset + b.length]
        else:
            with open(self.path, "rb") as f:
                f.seek(b.offset)
                raw = f.read(b.length)
        need = b.count * self.stride  # never decode more than this, whatever the data says
        if b.codec == 1:
            out = _zstd_decode(raw, need)
        elif b.codec == 2:
            d = zlib.decompressobj()
            out = d.decompress(raw, need + 1)
            if len(out) > need or d.unconsumed_tail:
                raise FseqError("block decompresses to more than its header says")
        else:
            out = raw
        if len(out) < need:
            out += bytes(need - len(out))
        return out

    def _unsparse(self, raw: bytes) -> bytes:
        if not self.ranges:
            return raw
        out = bytearray(self.channels)
        pos = 0
        for start, count in self.ranges:
            out[start : start + count] = raw[pos : pos + count]
            pos += count
        return bytes(out)

    def has_block(self, i: int) -> bool:
        """Whether frame i's block is already decoded (cheap: builds no frame)."""
        return 0 <= i < self.frames and self._block_index(i) in self._cache

    def first_uncached(self, start: int, end: int) -> int | None:
        """A frame in the first block not yet decoded among those holding frames start..end, else None."""
        if not self._blocks or end < 0 or start >= self.frames:
            return None
        lo = self._block_index(max(0, start))
        hi = self._block_index(min(self.frames - 1, end))
        for bi in range(lo, hi + 1):
            if bi not in self._cache:
                return max(self._blocks[bi].first, max(0, start))
        return None

    def cached(self, i: int) -> bytes | None:
        """Frame i if its block is already decoded, else None (never blocks on I/O)."""
        bi = self._block_index(i)
        data = self._cache.get(bi)
        if data is None:
            return None
        off = (i - self._blocks[bi].first) * self.stride
        return self._unsparse(data[off : off + self.stride])

    def frame(self, i: int) -> bytes:
        """Absolute channel data for frame i (may read and decompress a block)."""
        if not 0 <= i < self.frames:
            raise IndexError(i)
        bi = self._block_index(i)
        with self._lock:
            if bi not in self._cache:
                data = self._read_block(self._blocks[bi])
                if len(self._cache) >= CACHE_BLOCKS and not self._preloaded:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[bi] = data
        return self.cached(i)

    def preload(self) -> None:
        with self._lock:
            for bi, b in enumerate(self._blocks):
                self._cache[bi] = self._read_block(b)
            self._preloaded = True


def _zstd():
    try:
        import zstandard  # noqa: PLC0415
    except ImportError as err:
        raise FseqError(
            "this .fseq is zstd-compressed and the zstandard module is not available; "
            "export it uncompressed from xLights (Preferences > Output > FSEQ compression: None)"
        ) from err
    return zstandard


def _zstd_decode(raw: bytes, limit: int) -> bytes:
    """Decompress a zstd frame, refusing to produce more than `limit` bytes (decompression bombs)."""
    try:
        with _zstd().ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
            out = bytearray()
            while len(out) <= limit and (piece := reader.read(min(1 << 20, limit + 1 - len(out)))):
                out += piece
    except FseqError:
        raise
    except Exception as err:  # noqa: BLE001  zstandard.ZstdError and friends
        raise FseqError(f"damaged zstd block: {err}") from err
    if len(out) > limit:
        raise FseqError("block decompresses to more than its header says")
    return bytes(out)


def load(path: str) -> FseqFile:
    """Open a sequence, decoding everything up front when it is small. Blocking."""
    seq = FseqFile(path)
    if seq.frames * seq.stride <= PRELOAD_LIMIT:
        seq.preload()
    return seq
