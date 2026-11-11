"""A Govee light is found again at a new IP by its MAC address (the scan can't always be heard)."""
from __future__ import annotations

from test_library import _load

protocol = _load("protocol")


def test_moved_to_finds_the_mac_at_another_address():
    macs = {"192.168.4.36": "d4:13:68:2a:f8:32", "192.168.4.42": "d4:13:68:16:f0:98"}
    assert protocol.moved_to("D4:13:68:16:F0:98", "192.168.4.44", macs) == "192.168.4.42"
    assert protocol.moved_to("d4:13:68:2a:f8:32", "192.168.4.36", macs) is None  # still where it was
    assert protocol.moved_to(None, "192.168.4.44", macs) is None
    assert protocol.moved_to("d4:13:68:16:f0:98", "192.168.4.44", None) is None
    assert protocol.moved_to("aa:bb:cc:dd:ee:ff", "192.168.4.44", macs) is None


def test_arp_macs_reads_resolved_entries_only(tmp_path, monkeypatch):
    table = tmp_path / "arp"
    table.write_text(
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.168.4.42     0x1         0x2         D4:13:68:16:F0:98     *        end0\n"
        "192.168.4.44     0x1         0x0         00:00:00:00:00:00     *        end0\n")
    real = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: real(table if p == "/proc/net/arp" else p, *a, **k))
    assert protocol.arp_macs() == {"192.168.4.42": "d4:13:68:16:f0:98"}
