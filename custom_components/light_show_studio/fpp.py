"""FPP MultiSync master: lets show controllers that keep the show on their own storage play it in step.

A Falcon Player remote, an ESPixelStick in FPP remote mode (and WLED builds with .fseq playback) store the .fseq
themselves and draw every frame locally. Over the network they only need to know which show is playing and where
it has got to, so Wi-Fi delays can't make them stutter: a late sync packet is corrected by the next.

Packet (FPP ControlProtocol, UDP 32320, multicast 239.70.80.80 or unicast):
  'FPPD' <type 0x01 MultiSync> <extra length uint16 LE>
  <action: 0 start, 1 stop, 2 sync, 3 open> <file type: 0 sequence> <frame uint32 LE> <seconds float32 LE>
  <file name, NUL terminated>
The remote looks the show up by file name, so it must hold a copy with the same name.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import struct

_LOGGER = logging.getLogger(__name__)

PORT = 32320
MULTICAST = "239.70.80.80"
START, STOP, SYNC, OPEN = 0, 1, 2, 3
TYPE_SEQUENCE = 0


def sync_packet(action: int, name: str, frame: int = 0, seconds: float = 0.0) -> bytes:
    body = struct.pack("<BBIf", action, TYPE_SEQUENCE, max(0, int(frame)), float(seconds)) \
        + name.encode("utf-8", "replace")[:250] + b"\0"
    return b"FPPD" + struct.pack("<BH", 0x01, len(body)) + body


def due(frame: int, last: int | None) -> bool:
    """FPP's own pace, counted from the last sync sent: 4 frames apart for the first 32, then 10. The first frame,
    and any jump backwards (a loop, a seek), is sent at once."""
    return last is None or frame < last or frame - last >= (4 if frame <= 32 else 10)


def parse_targets(text: str) -> list[str]:
    """Comma separated LAN IPv4 addresses; empty = multicast to every remote."""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        if part := part.strip():
            ip = ipaddress.IPv4Address(part)  # ValueError for anything else
            if not ip.is_private:
                raise ValueError(f"{part} is not a home network address")
            out.append(str(ip))
    return out


class FppSync:
    """Sends MultiSync for the show the sequence player is running. Fire-and-forget UDP."""

    def __init__(self, targets: list[str] | None = None) -> None:
        self.targets = targets or [MULTICAST]
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        try:
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        except OSError:
            pass
        self.sent = 0
        self._last: int | None = None

    def _send(self, pkt: bytes) -> None:
        for host in self.targets:
            try:
                self._sock.sendto(pkt, (host, PORT))
                self.sent += 1
            except OSError as err:
                _LOGGER.debug("FPP sync to %s failed: %s", host, err)

    def start(self, name: str) -> None:
        """Get the remotes ready. They start playing at the first sync, which says where the show really is (a
        light delay or a speaker can make that later than now, or not frame 0)."""
        self._last = None
        self._send(sync_packet(OPEN, name))

    def frame(self, name: str, frame: int, seconds: float) -> None:
        if due(frame, self._last):
            self._last = frame
            self._send(sync_packet(SYNC, name, frame, seconds))

    def pause(self, name: str) -> None:
        """MultiSync has no pause: stop the remotes; the first sync after resuming starts them at the right frame."""
        if self._last is not None:
            self.stop(name)

    def stop(self, name: str) -> None:
        self._send(sync_packet(STOP, name))
        self._last = None

    def close(self) -> None:
        self._sock.close()
