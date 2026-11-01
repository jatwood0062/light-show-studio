"""Tests for the light IP address helpers; pure, no Home Assistant."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"
spec = importlib.util.spec_from_file_location("hosts_under_test", PKG / "hosts.py")
h = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = h
spec.loader.exec_module(h)


def test_valid_address_is_trimmed():
    assert h.valid_ipv4(" 192.168.4.42 ") == "192.168.4.42"


@pytest.mark.parametrize("bad", ["", "light.local", "192.168.4", "192.168.4.256", "::1", "0.0.0.0",
                                 "127.0.0.1", "224.0.0.1", "1.2.3.4.5", None, 5])
def test_bad_addresses_rejected(bad):
    with pytest.raises(ValueError):
        h.valid_ipv4(bad)


def test_host_taken():
    assert h.host_taken("1.2.3.4", ["5.6.7.8", "1.2.3.4"])
    assert not h.host_taken("1.2.3.4", ["5.6.7.8"])
