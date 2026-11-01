"""Pure helpers for a light's IP address; no Home Assistant imports."""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable


def valid_ipv4(value: object) -> str:
    """Return the address as a normalised IPv4 string, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("expected an IPv4 address")
    text = value.strip()
    if text.count(".") != 3:
        raise ValueError(f"{value!r} is not an IPv4 address")
    addr = ipaddress.IPv4Address(text)  # ValueError (AddressValueError) when malformed
    if addr.is_unspecified or addr.is_multicast or addr.is_loopback:
        raise ValueError(f"{value!r} can't be a light's address")
    return str(addr)


def host_taken(host: str, others: Iterable[str]) -> bool:
    """True when another entry already uses this host."""
    return any(o == host for o in others)
