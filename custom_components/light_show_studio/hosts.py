"""Pure helpers for IP addresses; no Home Assistant imports.

Every address the integration talks to (lights) or listens to (sequencers) must be a LAN address:
private (RFC 1918), link-local (169.254/16) or, for sources only, the same. Public, loopback,
multicast, reserved and unspecified addresses are refused, so a typed host, a spoofed discovery
reply or a hostile packet can't aim Home Assistant at the internet or at itself.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable

LAN_NETWORKS = tuple(ipaddress.IPv4Network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))
MAX_SOURCES = 32  # entries in a source allowlist


def is_lan_ipv4(value: object) -> bool:
    """True for a private or link-local IPv4 address that could be a light or a sequencer."""
    try:
        addr = ipaddress.IPv4Address(value) if isinstance(value, str) and value.count(".") == 3 else None
    except ValueError:
        return False
    if addr is None or addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_reserved:
        return False
    if addr == ipaddress.IPv4Address("255.255.255.255"):
        return False
    return any(addr in net for net in LAN_NETWORKS)


def valid_ipv4(value: object) -> str:
    """Return the address as a normalised private/link-local IPv4 string, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("expected an IPv4 address")
    text = value.strip()
    if text.count(".") != 3:
        raise ValueError(f"{value!r} is not an IPv4 address")
    addr = ipaddress.IPv4Address(text)  # ValueError (AddressValueError) when malformed
    if not is_lan_ipv4(str(addr)):
        raise ValueError(f"{value!r} must be a private or link-local (LAN) address")
    return str(addr)


def host_taken(host: str, others: Iterable[str]) -> bool:
    """True when another entry already uses this host."""
    return any(o == host for o in others)


# --- who may send frames to the receiver ----------------------------------------------
def parse_sources(value: object) -> tuple[ipaddress.IPv4Network, ...]:
    """'192.168.4.20, 10.0.0.0/24' (or a list of those) -> networks. Empty means "any LAN source".
    Raises ValueError for anything that isn't a LAN address or network."""
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple)) else str(value).replace(";", ",").replace("\n", ",").split(",")
    nets: list[ipaddress.IPv4Network] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        net = ipaddress.IPv4Network(text, strict=False)  # ValueError when malformed
        if not any(net.subnet_of(lan) for lan in LAN_NETWORKS):
            raise ValueError(f"{text} is not a private or link-local network")
        nets.append(net)
    if len(nets) > MAX_SOURCES:
        raise ValueError(f"at most {MAX_SOURCES} allowed sources")
    return tuple(nets)


def source_allowed(ip: str, allowed: tuple[ipaddress.IPv4Network, ...] = ()) -> bool:
    """A packet's source passes when it is a LAN address and, if an allowlist is set, is on it."""
    if not is_lan_ipv4(ip):
        return False
    if not allowed:
        return True
    addr = ipaddress.IPv4Address(ip)
    return any(addr in net for net in allowed)
