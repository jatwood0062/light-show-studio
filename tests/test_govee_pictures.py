"""Govee "pixels" protocol (H70C4 / H70C5 / H70C7): the ptReal picture bytes, the model table, chunked frames on the
output thread, and the PictureController (no razer, the background colour, a rate set by each picture's size, small
pictures in one message, big ones in messages 40 ms apart, newest wins, the dimmer instead of scaled colours).
Sockets are 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import base64
import json
import random
import sys
import time

import pytest

import test_zones as tz
from test_govee_stream import aio
from test_library import _load
from test_output import Sink

proto = sys.modules["light_show_studio_zn.protocol"]
out = sys.modules["light_show_studio_zn.output"]
controller = tz.controller
models = _load("models")


# --- the reference implementation the owner tested on an H70C5 (kept verbatim to compare byte for byte) ----------
def _ref_ble_packet(data):
    cs = 0
    for b in data:
        cs ^= b
    return bytes(data).ljust(19, b"\0") + bytes([cs])


def _ref_multi_packets(raw, kind):
    data = bytearray([0xA3, 0x00, 0x01, 0x00, kind])
    lines, last_marker = 0, 1
    for b in raw:
        if len(data) % 19 == 0:
            lines += 1
            data.append(0xA3)
            last_marker = len(data)
            data.append(lines)
        data.append(b)
    data[last_marker] = 0xFF
    data[3] = lines + 1
    return [_ref_ble_packet(data[i:i + 19]) for i in range(0, len(data), 19)]


def _decode(msgs):
    """ptReal messages -> the 20-byte packets, in order."""
    pkts = []
    for m in msgs:
        msg = json.loads(m)["msg"]
        assert msg["cmd"] == "ptReal"
        pkts += [base64.b64decode(p) for p in msg["data"]["command"]]
    return pkts


def _unpack(pkts):
    """Packets -> the payload (lines joined, headers removed), checking every checksum and line number."""
    assert pkts[-1] == bytes([0x33, 0x05, 0x0A, 0x20, 0x03]).ljust(19, b"\0") + bytes([0x33 ^ 0x05 ^ 0x0A ^ 0x20 ^ 0x03])
    lines = pkts[:-1]
    raw = bytearray()
    for n, p in enumerate(lines):
        assert len(p) == 20 and p[0] == 0xA3
        x = 0
        for b in p[:19]:
            x ^= b
        assert p[19] == x
        assert p[1] == (0xFF if n == len(lines) - 1 else n)
        raw += p[5:19] if n == 0 else p[2:19]
    assert lines[0][2] == 0x01 and lines[0][3] == len(lines) and lines[0][4] == 0x03
    return bytes(raw)


def _ref_payload(frame):
    """The 2.9.0 payload (black background, black bulbs left out), which the owner tested byte for byte."""
    groups = {}
    for i, c in enumerate(frame):
        if c != (0, 0, 0):
            groups.setdefault(c, []).append(i)
    out = bytearray([0x0F, 0, 100, 0, 0, 0]) + len(groups).to_bytes(2, "little")
    for c, idx in groups.items():
        hi = [i for i in idx if i > 255]
        out += len(idx).to_bytes(2, "little") + bytes(c) + len(hi).to_bytes(2, "little")
        out += bytes(i for i in idx if i <= 255) + b"".join(i.to_bytes(2, "little") for i in hi)
    return bytes(out)


def _parse_picture(raw, bulbs):
    """Payload -> the frame it draws (bulbs in no group show the background)."""
    assert raw[:3] == bytes([0x0F, 0, 100])
    groups = int.from_bytes(raw[6:8], "little")
    assert groups >= 1  # a picture always lists a group
    frame = [tuple(raw[3:6])] * bulbs
    i = 8
    for _ in range(groups):
        count = int.from_bytes(raw[i:i + 2], "little")
        rgb = tuple(raw[i + 2:i + 5])
        hi = int.from_bytes(raw[i + 5:i + 7], "little")
        i += 7
        lo = count - hi
        idx = list(raw[i:i + lo])
        i += lo
        for _ in range(hi):
            idx.append(int.from_bytes(raw[i:i + 2], "little"))
            i += 2
        for k in idx:
            frame[k] = rgb
    assert all(b == 0 for b in raw[i:])  # only the last line's padding is left
    return frame


# --- protocol ------------------------------------------------------------------------------------------------------
def test_ble_packet_pads_to_19_and_appends_the_xor():
    assert proto._ble_packet(b"\x33\x05\x0a\x20\x03") == bytes.fromhex("33050a2003" + "00" * 14 + "1f")
    assert len(proto._ble_packet(bytes(range(19)))) == 20


@pytest.mark.parametrize("size", [0, 1, 13, 14, 15, 30, 31, 32, 100, 500, 1234])
def test_multi_packets_match_the_reference_byte_for_byte(size):
    raw = bytes(random.Random(size).randrange(256) for _ in range(size))
    assert proto.multi_packets(raw, 0x03) == _ref_multi_packets(raw, 0x03)


def test_multi_packets_single_line_marks_itself_last():
    pkts = proto.multi_packets(b"\x01\x02", 0x03)
    assert pkts == [bytes.fromhex("a3ff01010301 02".replace(" ", "")).ljust(19, b"\0")
                    + bytes([0xA3 ^ 0xFF ^ 0x01 ^ 0x01 ^ 0x03 ^ 0x01 ^ 0x02])]


def test_payload_encodes_low_and_high_bulb_indices():
    frame = [(0, 0, 0)] * 300
    frame[0] = frame[255] = frame[256] = frame[299] = (10, 20, 30)
    raw = proto.picture_payload(frame)
    assert raw == bytes([0x0F, 0, 100, 0, 0, 0, 1, 0,  # still, speed 0, 100 %, black background, 1 group
                         4, 0, 10, 20, 30, 2, 0,  # 4 bulbs, colour, 2 of them past 255
                         0, 255,  # low indices, one byte each
                         0x00, 0x01, 0x2B, 0x01])  # 256 and 299, two bytes little endian


def test_300_bulbs_in_ten_bands_of_four_colours_uses_the_most_common_as_background():
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    frame = [cols[i // 30 % 4] for i in range(300)]  # red 90, green 90, blue 60, white 60: red (first) is the bg
    raw = proto.picture_payload(frame)
    assert raw[3:8] == bytes([255, 0, 0, 3, 0])  # red background, 3 groups
    msgs = proto.picture_msgs(frame)
    pkts = _decode(msgs)
    assert len(pkts) == 17 and [len(json.loads(m)["msg"]["data"]["command"]) for m in msgs] == [12, 5]  # was 24
    assert pkts == proto.picture_packets(frame)
    assert _parse_picture(_unpack(pkts), 300) == frame


@pytest.mark.parametrize("seed", range(8))
def test_a_mostly_black_frame_is_byte_for_byte_the_tested_2_9_0_picture(seed):
    rng = random.Random(seed)
    cols = [(rng.randrange(1, 256), rng.randrange(256), rng.randrange(256)) for _ in range(rng.randrange(1, 25))]
    frame = [(0, 0, 0) if rng.random() < 0.6 else rng.choice(cols) for _ in range(rng.choice((50, 300, 500)))]
    assert proto.picture_background(frame) == (0, 0, 0)
    assert proto.picture_payload(frame) == _ref_payload(frame)
    assert _parse_picture(_unpack(proto.picture_packets(frame)), len(frame)) == frame


def test_black_bulbs_are_left_out_and_an_all_black_frame_lists_bulb_0():
    assert proto.picture_payload([(0, 0, 0)] * 300) == bytes([0x0F, 0, 100, 0, 0, 0, 1, 0,
                                                              1, 0, 0, 0, 0, 0, 0, 0])  # bulb 0, black
    frame = [(0, 0, 0)] * 50 + [(1, 2, 3)]
    assert _parse_picture(_unpack(proto.picture_packets(frame)), 51) == frame


def test_a_frame_all_one_colour_is_that_background_and_bulb_0():
    raw = proto.picture_payload([(0, 40, 0)] * 300)
    assert raw == bytes([0x0F, 0, 100, 0, 40, 0, 1, 0, 1, 0, 0, 40, 0, 0, 0, 0])
    assert len(proto.picture_packets([(0, 40, 0)] * 300)) == 3 and len(proto.picture_msgs([(9, 9, 9)] * 500)) == 1


def test_a_block_on_a_coloured_background_is_the_tested_4_packet_picture():
    """The owner's 25 fps chase on an H70C5: 20 red bulbs on dim green, 4 packets, one message."""
    frame = [(0, 40, 0)] * 300
    frame[100:120] = [(255, 0, 0)] * 20
    raw = proto.picture_payload(frame)
    assert raw == bytes([0x0F, 0, 100, 0, 40, 0, 1, 0, 20, 0, 255, 0, 0, 0, 0]) + bytes(range(100, 120))
    msgs = proto.picture_msgs(frame)
    assert len(msgs) == 1 and len(_decode(msgs)) == 4
    assert _parse_picture(_unpack(_decode(msgs)), 300) == frame


def test_a_coloured_background_lists_black_bulbs_and_high_indices():
    frame = [(0, 0, 255)] * 300
    frame[1] = frame[256] = (0, 0, 0)
    frame[2] = frame[299] = (255, 0, 0)
    raw = proto.picture_payload(frame)
    assert raw == bytes([0x0F, 0, 100, 0, 0, 255, 2, 0,
                         2, 0, 0, 0, 0, 1, 0, 1, 0x00, 0x01,  # black: bulb 1 and 256
                         2, 0, 255, 0, 0, 1, 0, 2, 0x2B, 0x01])  # red: bulb 2 and 299
    assert _parse_picture(_unpack(proto.picture_packets(frame)), 300) == frame


def test_the_background_ties_go_to_black_then_to_the_first_colour():
    assert proto.picture_background([(1, 1, 1), (0, 0, 0)]) == (0, 0, 0)
    assert proto.picture_background([(1, 1, 1), (2, 2, 2)]) == (1, 1, 1)
    assert proto.picture_background([(1, 1, 1), (2, 2, 2), (2, 2, 2)]) == (2, 2, 2)
    assert proto.picture_background([]) == (0, 0, 0) and proto.picture_payload([]) == bytes([15, 0, 100, 0, 0, 0, 0, 0])


def test_a_reduced_frame_with_a_coloured_background_keeps_at_most_24_groups():
    rng = random.Random(7)
    frame = [(255, 255, 255)] * 200 + [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(90)]
    frame += [(0, 0, 0)] * 10
    reduced = proto.picture_colours(frame)
    raw = proto.picture_payload(reduced)
    assert raw[3:6] == bytes([255, 255, 255]) and int.from_bytes(raw[6:8], "little") <= 24
    assert _parse_picture(_unpack(proto.picture_packets(frame)), 300) == reduced


def test_a_frame_with_few_colours_is_exact():
    frame = [(i % 20 + 1, 7, 9) for i in range(300)]  # 20 colours
    assert proto.picture_colours(frame) == frame


def test_many_colours_are_cut_to_24_and_mapped_to_the_nearest():
    rng = random.Random(1)
    frame = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(300)] + [(0, 0, 0)] * 10
    reduced = proto.picture_colours(frame)
    assert len(set(reduced) - {(0, 0, 0)}) <= 24
    assert reduced[-10:] == [(0, 0, 0)] * 10
    rainbow = [(255, i * 255 // 299, 0) for i in range(300)]  # a smooth ramp stays close after rounding
    near = proto.picture_colours(rainbow)
    assert len(set(near)) <= 24 and max(abs(a[1] - b[1]) for a, b in zip(rainbow, near)) <= 20
    assert proto.picture_colours([(255, 255, 255)] * 30 + [(i, 0, 0) for i in range(1, 40)])[0] == (255, 255, 255)
    pkts = proto.picture_packets(frame)
    assert _parse_picture(_unpack(pkts), len(frame)) == reduced


def test_messages_carry_at_most_12_packets_and_the_show_packet_comes_last():
    rng = random.Random(2)
    frame = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(500)]
    msgs = proto.picture_msgs(frame)
    sizes = [len(json.loads(m)["msg"]["data"]["command"]) for m in msgs]
    assert all(s <= 12 for s in sizes) and sum(sizes) == len(proto.picture_packets(frame))
    assert _decode(msgs)[-1][:5] == bytes([0x33, 0x05, 0x0A, 0x20, 0x03])
    assert all(b"razer" not in m for m in msgs)


# --- models / entry protocol ------------------------------------------------------------------------------------
def test_christmas_string_lights_2_use_pictures():
    for sku, bulbs in (("H70C4", 200), ("h70c5", 300), ("H70C7", 500)):
        m = models.lookup(sku)
        assert m.protocol == "pixels" and m.segments == bulbs
    assert models.lookup("H6860").protocol == "razer"


def test_protocol_for_an_entry():
    assert models.protocol_for({"sku": "H70C5"}) == "pixels"  # set up before the choice existed: by its SKU
    assert models.protocol_for({"sku": "H6860"}) == "razer"
    assert models.protocol_for({"sku": ""}) == "razer"
    assert models.protocol_for({"sku": "", "protocol": "pixels"}) == "pixels"  # typed IP, switched by hand
    assert models.protocol_for({"sku": "H70C5", "protocol": "razer"}) == "razer"
    assert models.protocol_for({"sku": "H70C5", "protocol": "bogus"}) == "pixels"


# --- output: chunked frames -------------------------------------------------------------------------------------
def test_a_chunked_frame_leaves_one_packet_per_gap_and_cannot_be_split_by_a_newer_one():
    th = out.OutputThread(sync=True)
    sink = Sink()
    port = th.open_port(sink.addr)
    port.submit([b"a1", b"a2", b"a3"], gap=0.04)
    assert sink.read(3) == [b"a1"] and port.pending() == 1  # the rest waits for the gap
    port.submit([b"b1", b"b2"], gap=0.04)  # comes after the rest of a, does not replace it
    assert port.pending() == 2 and port.coalesced == 0
    th.flush()
    assert sink.read(5) == [b"a2", b"a3", b"b1", b"b2"] and port.sent == 2
    port.close()
    sink.close()


def test_a_chunked_frame_not_started_yet_is_replaced_whole():
    th = out.OutputThread(sync=True)
    sink = Sink()
    port = th.open_port(sink.addr)
    far = time.perf_counter() + 60
    port.submit([b"a1", b"a2"], due=far, gap=0.04)
    port.submit([b"b1", b"b2"], due=far, gap=0.04)
    assert port.pending() == 1 and port.coalesced == 1
    th.flush()
    assert sink.read(4) == [b"b1", b"b2"]
    port.close()
    sink.close()


def test_the_gap_is_kept_on_the_real_thread():
    th = out.OutputThread()
    th.start()
    sink = Sink()
    port = th.open_port(sink.addr)
    try:
        port.submit([b"1", b"2", b"3"], gap=0.04)
        times = []
        for _ in range(3):
            sink.sock.recvfrom(64)
            times.append(time.perf_counter())
        assert times[1] - times[0] >= 0.03 and times[2] - times[1] >= 0.03
    finally:
        port.close()
        sink.close()
        th.stop()


# --- the controller ---------------------------------------------------------------------------------------------
class Lan:
    def __init__(self):
        self.sent, self.port = [], None

    def send(self, payload):
        if self.port is not None:
            return self.port.control([payload])
        self.sent.append((time.monotonic(), payload))
        return True

    def close(self):
        pass


class Light(controller.PictureController):
    def __init__(self, hass, bulbs=300):
        self._lan = Lan()
        super().__init__(hass, "192.168.1.10", bulbs)
        self.is_on = True

    def _make_link(self, host):
        return self._lan


def _pics(sent):
    return [(t, p) for t, p in sent if b'"ptReal"' in p]


def _first(p):
    """Is this message the start of a picture (line 0, or the only line of a one-line picture)?"""
    pkt = _decode([p])[0]
    return pkt[1] == 0 or (pkt[1] == 0xFF and pkt[2:5] == b"\x01\x01\x03")


def _starts(sent):
    """When each picture began."""
    return [t for t, p in _pics(sent) if _first(p)]


def _cmds(payloads):
    return [json.loads(p)["msg"]["cmd"] for p in payloads]


def _levels(payloads):
    """The dimmer levels sent, in order."""
    return [json.loads(p)["msg"]["data"]["value"] for p in payloads if b'"brightness"' in p]


def _shown(sent, bulbs=300):
    """The last picture sent (all its messages)."""
    pics = _pics(sent)
    start = max(i for i, (_, p) in enumerate(pics) if _first(p))
    return _parse_picture(_unpack(_decode([p for _, p in pics[start:]])), bulbs)


BANDS = [[(255, 0, 0)] * 150 + [(0, 0, 255)] * 150, [(0, 255, 0)] * 300, [(255, 255, 0)] * 300]


def test_the_picture_interval_follows_its_size():
    assert controller.picture_interval(4) == pytest.approx(0.04)  # the tested 25 fps chase
    assert controller.picture_interval(1) == pytest.approx(0.04)
    assert controller.picture_interval(12) == pytest.approx(0.2)
    assert 0.4 <= controller.picture_interval(24) <= 0.5


@aio
async def test_direct_sends_never_use_razer_and_keep_the_rate_and_gaps():
    hass = tz.FakeHass()
    light = Light(hass)
    light._send_frame(BANDS[0])  # red bg + 150 blue bulbs: 14 packets, two messages, 0.24 s
    light._send_frame(BANDS[1])  # too soon: waits for its slot...
    light._send_frame(BANDS[2])  # ...and is replaced by the newest
    await asyncio.sleep(0.45)
    sent = light._lan.sent
    assert _cmds(p for _, p in sent)[0] == "brightness" and set(_cmds(p for _, p in sent)) == {"brightness", "ptReal"}
    assert _levels(p for _, p in sent) == [100]  # the string's own dimmer, once, as streaming starts
    pictures = _starts(sent)
    n = len(proto.picture_packets(BANDS[0]))
    assert n == 14 and len(pictures) == 2 and pictures[1] - pictures[0] >= controller.picture_interval(n) - 0.01
    assert _shown(sent) == BANDS[2]
    first = [t for t, p in _pics(sent) if t < pictures[1]]
    assert len(first) == 2 and first[1] - first[0] >= 0.035  # more than 12 packets: two messages, 40 ms apart
    light.shutdown()


@aio
async def test_a_small_picture_is_one_message_and_chases_at_25_a_second():
    hass = tz.FakeHass()
    light = Light(hass)
    for k in range(10):  # the owner's test: 20 red bulbs moving over dim green
        frame = [(0, 40, 0)] * 300
        frame[k * 5:k * 5 + 20] = [(255, 0, 0)] * 20
        light._send_frame(frame)
        await asyncio.sleep(0.042)
    pics = _pics(light._lan.sent)
    assert len(pics) == 10 and light.stats.merged == 0  # every frame shown, none held back
    assert all(len(_decode([p])) == 4 for _, p in pics)  # 4 packets, one message each
    assert _shown(light._lan.sent) == frame
    light.shutdown()


@aio
async def test_a_big_picture_holds_the_next_for_longer():
    hass = tz.FakeHass()
    light = Light(hass)
    rng = random.Random(3)
    frames = [[(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(300)] for _ in range(2)]
    light._send_frame(frames[0])
    light._send_frame(frames[1])
    wait = controller.picture_interval(len(proto.picture_packets(frames[0])))
    assert wait >= 0.4
    await asyncio.sleep(wait + 0.25)
    sent = light._lan.sent
    starts = _starts(sent)
    assert len(starts) == 2 and starts[1] - starts[0] >= wait - 0.01
    times = [t for t, _ in _pics(sent) if t < starts[1]]
    assert len(times) >= 3 and all(b - a >= 0.035 for a, b in zip(times, times[1:]))
    assert _shown(sent) == proto.picture_colours(frames[1])
    light.shutdown()


@aio
async def test_through_the_output_thread_pictures_are_chunked_and_rate_limited():
    hass = tz.FakeHass()
    th = out.OutputThread(sync=True)
    sink = Sink()
    light = Light(hass)
    light.use_output(th)
    light.port.addr = sink.addr
    rng = random.Random(4)
    frames = [[(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(300)] for _ in range(5)]
    for f in frames:  # a 25 fps burst
        light._send_frame(f)
    port = light.port
    assert light.stats.merged == 3  # the first left at once; the 2nd-4th were held, each replaced by the next
    wait = controller.picture_interval(len(proto.picture_packets(frames[0])))
    t0 = time.perf_counter()
    while port.sent < 3 and time.perf_counter() - t0 < 2:  # (the dimmer command, then the two pictures)
        await asyncio.sleep(0.01)
        th.flush()
    assert wait - 0.05 <= time.perf_counter() - t0 < wait + 0.15  # the 5th went out in the next slot
    th.flush()
    got = sink.read(200)
    assert _cmds(got)[0] == "brightness" and set(_cmds(got[1:])) == {"ptReal"} and port.sent == 3
    first_msgs = proto.picture_msgs(frames[0])
    assert got[1:1 + len(first_msgs)] == first_msgs and got[1 + len(first_msgs):] == proto.picture_msgs(frames[4])
    light.shutdown()
    sink.close()


@aio
async def test_an_unchanged_picture_is_not_sent_again_until_the_keep_alive_unless_the_light_lost_it():
    hass = tz.FakeHass()
    light = Light(hass)
    light._hw_frame(BANDS[1])
    light._hw_frame(BANDS[1])
    await asyncio.sleep(0.1)
    assert len(_starts(light._lan.sent)) == 1
    light._realtime = False  # the light came back, or was switched on: it needs the picture (and dimmer) again
    light._hw_frame(BANDS[1])
    await asyncio.sleep(0.1)
    assert len(_starts(light._lan.sent)) == 2 and _levels(p for _, p in light._lan.sent) == [100, 100]
    assert light.KEEPALIVE == controller.PICTURE_KEEPALIVE and light._keep_handle is not None
    light.shutdown()


@aio
async def test_turning_off_drops_a_picture_still_waiting():
    hass = tz.FakeHass()
    light = Light(hass)
    light._hw_frame(BANDS[0])
    light._hw_frame(BANDS[1])  # waits for its slot
    await light.async_turn_off(transition=0)
    await asyncio.sleep(0.35)
    assert _cmds(p for _, p in light._lan.sent)[-1] == "turn" and len(_starts(light._lan.sent)) == 1
    light.shutdown()


@aio
async def test_identify_and_effects_go_out_as_pictures():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=20)
    light.is_on = False
    await light.async_turn_on(colour=(255, 0, 0), brightness=64, transition=0)
    await asyncio.sleep(0.3)
    cmds = _cmds(p for _, p in light._lan.sent)
    assert cmds[:3] == ["turn", "brightness", "ptReal"] and set(cmds[2:]) == {"ptReal"} and "razer" not in cmds
    assert light.RENDER_FPS == 25  # small pictures keep up with full-rate effects; big ones are thinned out
    light._lan.sent.clear()
    await light.async_identify()  # flashes at the full dimmer, then the look comes back at its level
    await asyncio.sleep(0.3)
    sent = [p for _, p in light._lan.sent]
    assert _levels(sent)[0] == 100 and _levels(sent)[-1] == 25 and "razer" not in _cmds(sent)
    assert _shown(light._lan.sent, 20) == [(255, 0, 0)] * 20
    light.shutdown()


def test_the_razer_controller_still_arms_and_sends_razer_frames():
    async def go():
        import test_govee_stream as gs
        hass = tz.FakeHass()
        lan = gs.Lan()
        light = gs.Light(hass, lan)
        light._hw_frame(gs.RED)
        assert lan.kinds() == ["brightness", "arm", "frame"]
        light.shutdown()
    asyncio.run(go())


@aio
async def test_a_frame_held_on_the_render_thread_goes_out_at_its_slot():
    import threading
    hass = tz.FakeHass()
    th = out.OutputThread(sync=True)
    sink = Sink()
    light = Light(hass)
    light.use_output(th)
    light.port.addr = sink.addr

    def render():
        light._hw_push(BANDS[0], time.monotonic())
        light._hw_push(BANDS[1], time.monotonic())  # too soon: held, its timer set on the loop

    t = threading.Thread(target=render)
    t.start()
    t.join()
    await asyncio.sleep(0.35)
    th.flush()
    got = sink.read(20)
    assert got == [proto.brightness_msg(100)] + proto.picture_msgs(BANDS[0]) + proto.picture_msgs(BANDS[1])
    light.shutdown()
    sink.close()


# --- brightness: the light's dimmer, full colours ----------------------------------------------------------------
@aio
async def test_low_brightness_sets_the_dimmer_and_sends_full_colours():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=30)
    light.is_on = False
    await light.async_turn_on(colour=(255, 0, 0), brightness=64, transition=0)
    await asyncio.sleep(0.05)
    sent = light._lan.sent
    assert _levels(p for _, p in sent) == [25]
    assert _cmds(p for _, p in sent)[1:3] == ["brightness", "ptReal"]  # the dimmer before the picture
    assert _shown(sent, 30) == [(255, 0, 0)] * 30  # not scaled to 64: that was too dim to see on the string
    assert light.frame[0][0] < 80  # the card still shows it dimmed
    light.shutdown()


@aio
async def test_a_brightness_change_on_a_still_look_moves_the_dimmer_and_resends():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=30)
    light.is_on = False
    await light.async_turn_on(colour=(0, 0, 255), transition=0)
    await asyncio.sleep(0.3)
    await light.async_turn_on(brightness=128, transition=0)
    await asyncio.sleep(0.02)
    sent = light._lan.sent
    assert _levels(p for _, p in sent) == [100, 50] and _shown(sent, 30) == [(0, 0, 255)] * 30
    light.shutdown()


@aio
async def test_dimmer_changes_are_rate_limited_and_scale_the_colours_meanwhile():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=30)
    light.is_on = False
    await light.async_turn_on(colour=(0, 0, 255), transition=0)  # dimmer 100 now (just sent)
    await asyncio.sleep(0.1)
    await light.async_turn_on(brightness=64, transition=0)  # too soon after the last dimmer command
    await asyncio.sleep(0.02)
    sent = light._lan.sent
    assert _levels(p for _, p in sent) == [100]
    assert _shown(sent, 30)[0][2] in range(60, 66)  # 25 / 100 of full, until the dimmer may change
    await asyncio.sleep(0.3)
    sent = light._lan.sent
    assert _levels(p for _, p in sent) == [100, 25] and _shown(sent, 30) == [(0, 0, 255)] * 30
    light.shutdown()


@aio
async def test_the_dimmer_is_set_again_when_streaming_resumes_after_a_pause():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=30)
    light.brightness = 128
    light._send_frame([(0, 255, 0)] * 30)
    await asyncio.sleep(0.05)
    light._hw_sent -= 3  # nothing for 3 s (another app may have changed the dimmer)
    light._send_frame([(255, 0, 0)] * 30)
    await asyncio.sleep(0.05)
    assert _levels(p for _, p in light._lan.sent) == [50, 50]
    light.shutdown()


@aio
async def test_scan_frames_run_the_dimmer_at_100():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=30)
    light.brightness = 64
    light._send_frame([(10, 20, 30)] * 30, scaled=False)
    await asyncio.sleep(0.05)
    assert _levels(p for _, p in light._lan.sent) == [100] and _shown(light._lan.sent, 30) == [(10, 20, 30)] * 30
    light.shutdown()


@aio
async def test_a_fade_in_still_scales_the_colours_under_the_dimmer():
    hass = tz.FakeHass()
    light = Light(hass, bulbs=30)
    light.is_on = False
    await light.async_turn_on(colour=(255, 255, 255), brightness=128, transition=1.0)
    await asyncio.sleep(0.3)
    sent = light._lan.sent
    assert set(_levels(p for _, p in sent)) == {50}  # the dimmer stays put through the fade
    assert len(_starts(sent)) >= 3 and _shown(sent, 30)[0][0] < 255  # the ramp rises in the colours
    await asyncio.sleep(1.0)
    assert _shown(light._lan.sent, 30) == [(255, 255, 255)] * 30
    light.shutdown()


# --- colour correction ------------------------------------------------------------------------------------------
def test_green_gamma_turns_the_c9_orange_into_what_matches_on_an_h70c5():
    table = proto.gamma_table(models.lookup("H70C5").green_gamma)
    assert abs(table[80] - 35) <= 1 and table[0] == 0 and table[255] == 255
    assert proto.correct_green([(255, 80, 0), (10, 255, 200)], table) == [(255, table[80], 0), (10, 255, 200)]
    assert proto.gamma_table(None) is None and proto.gamma_table(1.0) is None
    assert proto.correct_green([(1, 2, 3)], None) == [(1, 2, 3)]


def test_green_gamma_for_an_entry():
    assert models.green_gamma_for({"sku": "H70C5"}) == 1.7 and models.green_gamma_for({"sku": "H70C7"}) == 1.7
    assert models.green_gamma_for({"sku": "H6860"}) is None
    assert models.green_gamma_for({"sku": "", "protocol": "pixels"}) == 1.7  # added by IP, switched to pictures
    assert models.green_gamma_for({"sku": ""}) is None


@aio
async def test_the_controller_sends_the_corrected_colour():
    hass = tz.FakeHass()
    light = Light.__new__(Light)
    light._lan = Lan()
    controller.PictureController.__init__(light, hass, "192.168.1.10", 30, 1.7)
    light.is_on = True
    light._hw_frame([(255, 80, 0)] * 30)
    await asyncio.sleep(0.05)
    shown = _shown(light._lan.sent, 30)
    assert shown == [(255, round(255 * (80 / 255) ** 1.7), 0)] * 30
    light.shutdown()
