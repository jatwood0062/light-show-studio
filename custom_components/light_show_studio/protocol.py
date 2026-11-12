"""Govee LAN protocol: per-segment ("razer") control, whole-light colour and discovery.

Official LAN API: JSON over UDP to port 4003 ({"msg": {"cmd": ..., "data": ...}}).
Per-bulb colour uses the undocumented "razer" realtime command (what Govee's PC sync uses):

  BB <len hi> <len lo> <cmd> <data...> <xor of all preceding bytes>
    B1  enable realtime mode, data = 01 / 00
    B0  colours, data = <1 = crisp, 0 = blend> <bulb count> <r g b ...>

A frame stays on the lights after the stream stops, so a still pattern only needs one frame.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import itertools
import json
import operator
import socket
import struct

from .hosts import is_lan_ipv4, valid_ipv4

CMD_PORT = 4003
SCAN_ADDR = ("239.255.255.250", 4001)
REPLY_PORT = 4002
CMD_COLORS = 0xB0
CMD_ENABLE = 0xB1

Colour = tuple[int, int, int]


def _frame(cmd: int, data: bytes) -> bytes:
    body = bytes([0xBB]) + struct.pack(">H", len(data)) + bytes([cmd]) + data
    return body + bytes([functools.reduce(operator.xor, body)])


def _wrap(cmd: str, data: dict) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}, separators=(",", ":")).encode()


def turn_msg(on: bool) -> bytes:
    return _wrap("turn", {"value": 1 if on else 0})


def brightness_msg(percent: int) -> bytes:
    return _wrap("brightness", {"value": max(1, min(100, percent))})


def colour_msg(c: Colour) -> bytes:
    """Whole-light colour, for lights without per-segment control."""
    return _wrap("colorwc", {"color": {"r": c[0], "g": c[1], "b": c[2]}, "colorTemInKelvin": 0})


def realtime_msg(on: bool) -> bytes:
    pkt = _frame(CMD_ENABLE, bytes([1 if on else 0]))
    return _wrap("razer", {"pt": base64.b64encode(pkt).decode()})


def colours_msg(colours: list[Colour], blend: bool = False) -> bytes:
    rgb = bytes(itertools.chain.from_iterable(colours))  # (one bytes object, not one per bulb)
    pkt = _frame(CMD_COLORS, bytes([0 if blend else 1, len(colours)]) + rgb)
    # the same bytes _wrap makes: base64 needs no JSON escaping, so the hot path skips json.dumps
    return b'{"msg":{"cmd":"razer","data":{"pt":"%s"}}}' % base64.b64encode(pkt)


class GoveeLan:
    """Fire-and-forget UDP sender. Never binds the reply port, so it can share the
    device with Home Assistant's own Govee integration."""

    def __init__(self, host: str) -> None:
        self.host = valid_ipv4(host)  # ValueError for anything but a LAN address
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self.dropped = 0  # packets that couldn't be sent
        self.port = None  # set by C9Controller.use_output: commands then go through the output thread (output.py), in order with the frames

    def send(self, payload: bytes) -> bool:
        """False when the packet couldn't leave (no route to the light, buffer full): UDP, so the next frame replaces it.
        Through the output thread the result is not known yet: True, and the port counts the failures."""
        if self.port is not None:
            return self.port.control([payload])
        try:
            self._sock.sendto(payload, (self.host, CMD_PORT))
        except (BlockingIOError, OSError):
            self.dropped += 1
            return False
        return True

    def close(self) -> None:
        self._sock.close()


async def async_scan(timeout: float = 2.5) -> dict[str, dict] | None:
    """Ask every Govee light with LAN Control on to report itself: {ip: {"sku", "device"}}.
    None when the scan couldn't run at all (port held, no network), as opposed to finding nothing.

    Replies go to UDP 4002, which Home Assistant's own Govee integration may hold; the port is
    shared where the OS allows it, otherwise this finds nothing and the IP is typed in."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    found: dict[str, dict] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", REPLY_PORT))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.sendto(_wrap("scan", {"account_topic": "reserve"}), SCAN_ADDR)
        loop = asyncio.get_running_loop()
        asked = loop.time()
        end = asked + timeout
        while (left := end - loop.time()) > 0:
            try:
                data, (src, _port) = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), left)
            except TimeoutError:
                break
            if not is_lan_ipv4(src) or len(found) >= 64:
                continue
            try:  # any hostile payload (deep nesting raises RecursionError) skips just this packet
                msg = json.loads(data)["msg"]
                if not isinstance(msg, dict) or msg.get("cmd") != "scan":
                    continue
                d = msg["data"]
                if not isinstance(d, dict):
                    continue
                ip = d["ip"]
                # the reply may only speak for the address it came from, never redirect to another
                if ip == src and is_lan_ipv4(ip):
                    found[ip] = {"sku": str(d.get("sku", ""))[:32], "device": str(d.get("device", ""))[:64],
                                 "ms": (loop.time() - asked) * 1000}  # how long the reply took (health check)
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        return None
    finally:
        sock.close()
    return found


def _arp_table() -> dict[str, bool]:
    """{ip: resolved} from the kernel's ARP table. Blocking."""
    out: dict[str, bool] = {}
    with open("/proc/net/arp", encoding="ascii") as f:
        next(f, None)
        for line in f:
            parts = line.split()
            if len(parts) >= 4:  # one line per interface: resolved on any of them counts
                ok = bool(int(parts[2], 16) & 0x2) and parts[3] != "00:00:00:00:00:00"
                out[parts[0]] = out.get(parts[0], False) or ok
    return out


def arp_macs() -> dict[str, str] | None:
    """{ip: mac} for every resolved entry in the kernel's ARP table; None when there is no table (not Linux). Blocking."""
    out: dict[str, str] = {}
    try:
        with open("/proc/net/arp", encoding="ascii") as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and int(parts[2], 16) & 0x2 and parts[3] != "00:00:00:00:00:00":
                    out[parts[0]] = parts[3].lower()
    except (OSError, ValueError):
        return None
    return out


async def async_poke_neighbours(host: str, wait: float = 1.5) -> None:
    """Send a status query to every address in `host`'s /24, so the kernel resolves whoever is there and a light
    that moved shows up in the ARP table. 253 tiny packets; the caller keeps it rare."""
    base = host.rsplit(".", 1)[0]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        for n in range(1, 255):
            ip = f"{base}.{n}"
            if ip != host and is_lan_ipv4(ip):
                try:
                    sock.sendto(_wrap("devStatus", {}), (ip, CMD_PORT))
                except OSError:
                    pass
            if n % 32 == 0:
                await asyncio.sleep(0.05)  # in small bursts, not one flood
        await asyncio.sleep(wait)
    finally:
        sock.close()


def moved_to(mac: str | None, host: str, macs: dict[str, str] | None) -> str | None:
    """The address a light with this MAC answers at now, when that isn't `host`."""
    if not mac or not macs:
        return None
    return next((ip for ip, m in macs.items() if m == mac.lower() and ip != host), None)


async def async_probe(hosts: list[str], wait: float = 6.0) -> dict[str, bool] | None:
    """Fallback presence check when the scan can't run: poke each light (a status query), give the
    kernel time to re-check its address, then see whether it answered ARP. None if there's no ARP
    table to read (not Linux)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        hosts = [h for h in hosts if is_lan_ipv4(h)]
        for _ in range(2):  # a stale entry needs traffic to be probed again
            for host in hosts:
                try:
                    sock.sendto(_wrap("devStatus", {}), (host, CMD_PORT))
                except OSError:
                    pass
            await asyncio.sleep(wait / 2)
        table = await loop.run_in_executor(None, _arp_table)
    except OSError:
        return None
    finally:
        sock.close()
    if not any(h in table for h in hosts):
        return None  # sending always leaves an entry, even a failed one: this isn't our ARP table
    return {h: table.get(h, False) for h in hosts}

