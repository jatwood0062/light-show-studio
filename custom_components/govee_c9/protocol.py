"""Govee LAN protocol for per-bulb control.

Official LAN API: JSON over UDP to port 4003 ({"msg": {"cmd": ..., "data": ...}}).
Per-bulb colour uses the undocumented "razer" realtime command (what Govee's PC sync uses):

  BB <len hi> <len lo> <cmd> <data...> <xor of all preceding bytes>
    B1  enable realtime mode, data = 01 / 00
    B0  colours, data = <1 = crisp, 0 = blend> <bulb count> <r g b ...>

A frame stays on the lights after the stream stops, so a still pattern only needs one frame.
"""
from __future__ import annotations

import base64
import json
import socket
import struct

CMD_PORT = 4003
CMD_COLORS = 0xB0
CMD_ENABLE = 0xB1

Colour = tuple[int, int, int]


def _frame(cmd: int, data: bytes) -> bytes:
    body = bytes([0xBB]) + struct.pack(">H", len(data)) + bytes([cmd]) + data
    cs = 0
    for b in body:
        cs ^= b
    return body + bytes([cs])


def _wrap(cmd: str, data: dict) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}, separators=(",", ":")).encode()


def turn_msg(on: bool) -> bytes:
    return _wrap("turn", {"value": 1 if on else 0})


def brightness_msg(percent: int) -> bytes:
    return _wrap("brightness", {"value": max(1, min(100, percent))})


def realtime_msg(on: bool) -> bytes:
    pkt = _frame(CMD_ENABLE, bytes([1 if on else 0]))
    return _wrap("razer", {"pt": base64.b64encode(pkt).decode()})


def colours_msg(colours: list[Colour], blend: bool = False) -> bytes:
    rgb = b"".join(bytes(c) for c in colours)
    pkt = _frame(CMD_COLORS, bytes([0 if blend else 1, len(colours)]) + rgb)
    return _wrap("razer", {"pt": base64.b64encode(pkt).decode()})


class GoveeLan:
    """Fire-and-forget UDP sender. Never binds the reply port, so it can share the
    device with Home Assistant's own Govee integration."""

    def __init__(self, host: str) -> None:
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    def send(self, payload: bytes) -> None:
        try:
            self._sock.sendto(payload, (self.host, CMD_PORT))
        except (BlockingIOError, OSError):
            pass  # UDP: a dropped frame is replaced by the next one

    def close(self) -> None:
        self._sock.close()
