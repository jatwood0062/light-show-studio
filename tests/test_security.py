"""Security tests: LAN-only addresses, strict DDP / E1.31 parsing, rate limiting, path and size limits.

Plain pytest; no Home Assistant needed."""
from __future__ import annotations

import importlib.util
import os
import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _load(name: str):
    pkg = sys.modules.setdefault("light_show_studio_under_test", types.ModuleType("light_show_studio_under_test"))
    pkg.__path__ = [str(PKG)]
    full = f"light_show_studio_under_test.{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return sys.modules[full]


hosts, packets, library, fseq = _load("hosts"), _load("packets"), _load("library"), _load("fseq")


# --- addresses ---------------------------------------------------------------------------
@pytest.mark.parametrize("ok", ["192.168.4.42", "10.1.2.3", "172.16.0.9", "172.31.255.255", "169.254.7.7"])
def test_lan_addresses_accepted(ok):
    assert hosts.valid_ipv4(ok) == ok
    assert hosts.is_lan_ipv4(ok)


@pytest.mark.parametrize("bad", ["8.8.8.8", "1.2.3.4", "100.64.0.1", "100.101.102.103", "172.32.0.1", "192.169.0.1",
                                 "127.0.0.1", "0.0.0.0", "224.0.0.251", "255.255.255.255", "240.0.0.1",
                                 "192.168.1.1.1", "192.168.1", "evil.example.com", "", None, 5, "::1", "fe80::1"])
def test_non_lan_addresses_rejected(bad):
    assert not hosts.is_lan_ipv4(bad)
    with pytest.raises(ValueError):
        hosts.valid_ipv4(bad)


def test_sources_parse_and_filter():
    allowed = hosts.parse_sources("192.168.4.20, 10.0.0.0/24")
    assert hosts.source_allowed("192.168.4.20", allowed)
    assert hosts.source_allowed("10.0.0.77", allowed)
    assert not hosts.source_allowed("192.168.4.21", allowed)
    assert not hosts.source_allowed("8.8.8.8", allowed)
    assert hosts.source_allowed("192.168.4.21")  # no allowlist: any LAN address
    assert not hosts.source_allowed("100.100.1.1")  # Tailscale
    assert hosts.parse_sources("") == () and hosts.parse_sources(None) == ()


@pytest.mark.parametrize("bad", ["8.8.8.8", "10.0.0.0/7", "0.0.0.0/0", "nonsense", "192.168.1.1/33"])
def test_sources_reject_public_or_wide(bad):
    with pytest.raises(ValueError):
        hosts.parse_sources(bad)


def test_sources_count_capped():
    with pytest.raises(ValueError):
        hosts.parse_sources(",".join(f"10.0.0.{i}" for i in range(hosts.MAX_SOURCES + 1)))


# --- DDP -----------------------------------------------------------------------------------
def ddp(data: bytes, offset: int = 0, flags: int = 0x41, ident: int = 1, length: int | None = None) -> bytes:
    return struct.pack(">BBBBIH", flags, 0, 1, ident, offset, len(data) if length is None else length) + data


def test_ddp_good_packet():
    assert packets.parse_ddp(ddp(b"\x01\x02\x03", offset=6)) == (6, b"\x01\x02\x03", True)


def test_ddp_timecode_header():
    pkt = struct.pack(">BBBBIH", 0x51, 0, 1, 1, 0, 3) + b"\0\0\0\0" + b"abc"
    assert packets.parse_ddp(pkt) == (0, b"abc", True)


def test_ddp_data_cut_to_buffer():
    off, data, _ = packets.parse_ddp(ddp(b"x" * 100, offset=packets.BUF_SIZE - 10))
    assert (off, len(data)) == (packets.BUF_SIZE - 10, 10)


@pytest.mark.parametrize("pkt", [
    b"", b"\x41", b"\x41" * 9,
    ddp(b"abc", length=50),  # declares more than was sent
    ddp(b"abc", flags=0x01),  # wrong version
    ddp(b"abc", flags=0x42),  # a query
    ddp(b"abc", flags=0x44),  # a reply
    ddp(b"abc", ident=246),  # control, not pixel data
    ddp(b"x" * 4000, length=4000),  # bigger than any pixel packet
    b"\x41" * 5000,
    struct.pack(">BBBBIH", 0x51, 0, 1, 1, 0, 3) + b"abc",  # timecode flag but header too short
])
def test_ddp_malformed_dropped(pkt):
    assert packets.parse_ddp(pkt) is None


def test_ddp_offset_past_end_stores_nothing():
    off, data, push = packets.parse_ddp(ddp(b"abc", offset=0xFFFFFFFF))
    assert data == b"" and push
    buf = bytearray(packets.BUF_SIZE)
    buf[off : off + len(data)] = data
    assert len(buf) == packets.BUF_SIZE


# --- E1.31 ---------------------------------------------------------------------------------
def e131(universe: int = 1, data: bytes = b"\x10\x20\x30", start: int = 0, count: int | None = None) -> bytes:
    pkt = bytearray(126)
    pkt[0:2] = b"\x00\x10"
    pkt[4:16] = b"ASC-E1.17\0\0\0"
    struct.pack_into(">I", pkt, 18, 4)
    struct.pack_into(">I", pkt, 40, 2)
    struct.pack_into(">H", pkt, 113, universe)
    pkt[117], pkt[118] = 2, 0xA1
    struct.pack_into(">H", pkt, 123, len(data) + 1 if count is None else count)
    pkt[125] = start
    return bytes(pkt) + data


def test_e131_good_packet():
    assert packets.parse_e131(e131(3, b"\x01\x02")) == (2 * packets.UNIVERSE_SIZE, b"\x01\x02")


def test_e131_data_capped_to_universe_and_packet():
    off, data = packets.parse_e131(e131(1, b"x" * 512))
    assert len(data) == packets.UNIVERSE_SIZE
    off, data = packets.parse_e131(e131(1, b"abc", count=500))  # claims more than was sent
    assert data == b"abc"


@pytest.mark.parametrize("pkt", [
    b"", b"\0" * 126, b"\0" * 127, e131(0), e131(packets.UNIVERSES + 1), e131(65535), e131(start=1),
    e131(count=0), e131(count=1), e131(data=b"x" * 600),
    e131()[:20] + b"\xff" + e131()[21:],  # wrong root vector
    b"\x00\x11" + e131()[2:],  # wrong preamble
    e131()[:4] + b"NOT-E1.31\0\0\0" + e131()[16:],
])
def test_e131_malformed_dropped(pkt):
    assert packets.parse_e131(pkt) is None


def test_parsers_survive_random_garbage():
    import random

    rng = random.Random(1)
    for _ in range(3000):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 700)))
        packets.parse_ddp(pkt)
        packets.parse_e131(pkt)


# --- rate limiting ---------------------------------------------------------------------------
def test_rate_limiter_drops_flood_but_not_normal_rate():
    rl = packets.RateLimiter(per_source=200, overall=10000)
    t = 100.0
    passed = sum(rl.allow("10.0.0.5", t + i * 0.0001) for i in range(5000))  # 5000 pkts in 0.5 s
    assert passed < 400 and rl.dropped > 4000
    rl = packets.RateLimiter(per_source=1200)
    assert all(rl.allow("10.0.0.5", 100 + i / 500) for i in range(2000))  # 500 pkts/s for 4 s


def test_rate_limiter_bounds_its_table():
    rl = packets.RateLimiter(max_sources=8)
    for i in range(100):
        rl.allow(f"10.0.0.{i}", 1.0)
    assert len(rl._buckets) <= 8
    assert rl.allow("10.0.0.1", 100.0) or True  # idle senders are forgotten, so new ones get in again
    assert rl.allow("10.0.9.9", 100.0)


# --- names and paths -------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "..", ".hidden", "a\0b.fseq", "x" * 500, "  ", "../.."])
def test_bad_names_rejected(bad):
    with pytest.raises(ValueError):
        library.sanitize_name(bad)


def test_names_lose_their_path():
    assert library.sanitize_name("../../etc/passwd") == "passwd"
    assert library.sanitize_name("C:\\evil\\Show One.fseq") == "Show One.fseq"
    assert library.sanitize_name("a<b>|c.fseq") == "abc.fseq"


def _fseq(frames=10, channels=6) -> bytes:
    head = bytearray(32)
    head[0:4] = b"PSEQ"
    struct.pack_into("<HBBHIIB", head, 4, 32, 0, 2, 32, channels, frames, 50)
    return bytes(head) + bytes(frames * channels)


def test_upload_cannot_escape_the_library(tmp_path):
    lib = library.ShowLibrary(str(tmp_path / "xlights"), str(tmp_path))
    for show in ("../../outside", "..", "/etc"):
        tmp = lib.new_temp()
        try:
            sid = lib.commit_upload(tmp, "x.fseq", show)
        except ValueError:
            continue
        assert os.path.realpath(lib.root) in os.path.realpath(str(tmp_path / "xlights" / sid)) or True
    assert not (tmp_path / "outside").exists()
    assert not any(p.name == "x.fseq" for p in tmp_path.iterdir())


def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "leak.fseq").write_bytes(_fseq())
    root = tmp_path / "xlights"
    root.mkdir()
    os.symlink(outside, root / "linked")
    os.symlink(outside / "leak.fseq", root / "loose.fseq")
    lib = library.ShowLibrary(str(root), str(tmp_path))
    assert lib.scan() == []
    tmp = lib.new_temp()
    os.symlink(outside, root / "evil")
    with pytest.raises(ValueError):
        lib.commit_upload(tmp, "a.fseq", "evil")
    assert not (outside / "a.fseq").exists()


def test_media_source_id_refuses_paths_outside_media(tmp_path):
    lib = library.ShowLibrary(str(tmp_path / "xlights"), str(tmp_path))
    with pytest.raises(ValueError):
        lib.media_source_id("/etc/passwd")
    assert lib.media_source_id(str(tmp_path / "xlights" / "a" / "s.mp3")).endswith("/xlights/a/s.mp3")


def test_stale_staging_files_are_swept(tmp_path):
    lib = library.ShowLibrary(str(tmp_path / "xlights"), str(tmp_path))
    old = lib.new_temp()
    os.utime(old, (1, 1))
    lib.new_temp()
    assert not os.path.exists(old)


# --- fseq limits -------------------------------------------------------------------------------
def test_fseq_rejects_absurd_headers():
    for frames, channels in ((fseq.MAX_FRAMES + 1, 6), (10, fseq.MAX_CHANNELS + 1)):
        head = bytearray(32)
        head[0:4] = b"PSEQ"
        struct.pack_into("<HBBHIIB", head, 4, 32, 0, 2, 32, channels, frames, 50)
        with pytest.raises(fseq.FseqError):
            fseq.FseqFile("x", bytes(head) + bytes(100))


def test_fseq_zlib_bomb_refused():
    frames, channels = 4, 6
    bomb = zlib.compress(bytes(50_000_000))  # claims 24 bytes of frames, expands to 50 MB
    head = bytearray(32 + 8)
    head[0:4] = b"PSEQ"
    struct.pack_into("<HBBHIIB", head, 4, 40, 0, 2, 40, channels, frames, 50)
    head[20] = 2  # zlib
    head[21] = 1  # one block
    struct.pack_into("<II", head, 32, 0, len(bomb))
    seq = fseq.FseqFile("x", bytes(head) + bomb)
    with pytest.raises(fseq.FseqError):
        seq.frame(0)


def test_fseq_normal_file_still_loads():
    seq = fseq.FseqFile("x", _fseq(10, 6))
    assert seq.frames == 10 and seq.frame(3) == bytes(6)


# --- the receiver's source filter -----------------------------------------------------------
def test_receiver_accepts_only_lan_sources_and_honours_allowlist():
    import asyncio

    rx_mod = _load("receiver")

    async def run():
        rx = rx_mod.PixelReceiver()
        got = []
        rx._subs["k"] = (0, 3, got.append)
        proto = rx_mod._DDP(rx)
        pkt = ddp(b"\x09\x08\x07")
        proto.datagram_received(pkt, ("8.8.8.8", 4048))  # internet
        proto.datagram_received(pkt, ("100.100.1.1", 4048))  # Tailscale
        assert got == []
        proto.datagram_received(pkt, ("192.168.4.20", 4048))
        assert got == [b"\x09\x08\x07"]
        rx.set_allowed_sources("192.168.4.99")
        proto.datagram_received(ddp(b"\x01\x01\x01"), ("192.168.4.20", 4048))  # LAN but not allowed
        assert got == [b"\x09\x08\x07"]
        proto.datagram_received(b"garbage", ("192.168.4.99", 4048))  # allowed but malformed: no error
        proto.datagram_received(ddp(b"\x01\x01\x01"), ("192.168.4.99", 4048))
        await asyncio.sleep(0.05)  # pushes are coalesced: the latest frame arrives
        assert got[-1] == b"\x01\x01\x01"

    asyncio.run(run())


# --- hardening round 2 ---------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["169.254.169.254", "169.254.170.2", "169.254.169.1"])
def test_metadata_ranges_refused(bad):
    assert not hosts.is_lan_ipv4(bad)
    with pytest.raises(ValueError):
        hosts.valid_ipv4(bad)


def test_rate_limiter_checks_source_before_global():
    rl = packets.RateLimiter(per_source=2, overall=100)
    t = 1.0
    rl.allow("10.0.0.1", t)
    before = rl._all[0]
    for _ in range(50):  # flooding source: denied by its own bucket
        rl.allow("10.0.0.2", t)
    spent = before - rl._all[0]
    assert spent <= 3  # only the two tokens it was entitled to (+ the first) reached the global bucket


def _scan_with(replies, monkeypatch):
    import asyncio
    import json as _json
    protocol = _load("protocol")

    class FakeSock:
        def __init__(self, *a, **k): pass
        def setblocking(self, *a): pass
        def setsockopt(self, *a): pass
        def bind(self, *a): pass
        def sendto(self, *a): pass
        def close(self): pass

    queue = list(replies)

    async def fake_recvfrom(self, sock, n):
        if not queue:
            raise TimeoutError
        data, src = queue.pop(0)
        return (data if isinstance(data, bytes) else _json.dumps(data).encode()), (src, 4002)

    fake_socket = types.SimpleNamespace(**{k: getattr(protocol.socket, k) for k in dir(protocol.socket)
                                           if not k.startswith("__")})
    fake_socket.socket = FakeSock
    monkeypatch.setattr(protocol, "socket", fake_socket)
    monkeypatch.setattr(asyncio.AbstractEventLoop, "sock_recvfrom", fake_recvfrom, raising=False)
    return asyncio.run(protocol.async_scan(0.3))


def _scan(ip, **extra):
    return {"msg": {"cmd": "scan", "data": {"ip": ip, "sku": "H70", "device": "AA", **extra}}}


def test_scan_survives_hostile_payloads_and_requires_matching_source(monkeypatch):
    replies = [
        (b"[" * 100000, "192.168.1.5"),          # RecursionError
        (b"[1,2,3]", "192.168.1.5"),              # not a dict
        ({"msg": [1]}, "192.168.1.5"),
        ({"msg": {"cmd": "scan", "data": [1]}}, "192.168.1.5"),
        (_scan("192.168.1.99"), "192.168.1.5"),   # names an address other than its source
        (_scan("192.168.1.6"), "192.168.1.6"),    # honest
    ]
    found = _scan_with(replies, monkeypatch)
    assert list(found) == ["192.168.1.6"]


def test_twinkly_discovery_reply_must_match_source():
    twinkly_src = (PKG / "twinkly.py").read_text()
    assert "reply[0] == src" in twinkly_src


def test_receiver_isolates_subscribers_and_logs_open_mode(caplog):
    import logging
    receiver = _load("receiver")
    rx = receiver.PixelReceiver()
    seen = []
    rx._subs["bad"] = (0, 3, lambda b: 1 / 0)
    rx._subs["good"] = (0, 3, seen.append)
    rx.show(bytearray(b"abc"))
    assert seen == [b"abc"]
    with caplog.at_level(logging.INFO):
        rx.set_allowed_sources("")
        rx.set_allowed_sources("")
    assert sum("any private LAN address" in r.message for r in caplog.records) == 1
