"""SSRF guard for server-side upstream HTTP requests."""

import ipaddress
import os
import socket
from urllib.parse import urlparse

BLOCKED_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
ALLOWED_SCHEMES = {"http", "https"}


class UpstreamURLBlockedError(ValueError):
    """Raised when an upstream URL is unsafe for server-side requests."""


def _env_enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalise_host(host: str) -> str:
    return host.rstrip(".").lower()


def _allowlist_entries() -> list[str]:
    raw = os.getenv("UPSTREAM_ALLOWLIST") or ""
    return [_normalise_host(entry.strip()) for entry in raw.split(",") if entry.strip()]


def _host_allowed(host: str, entries: list[str]) -> bool:
    normalised = _normalise_host(host)
    for entry in entries:
        if entry.startswith("."):
            if normalised.endswith(entry):
                return True
            continue
        if normalised == entry or normalised.endswith(f".{entry}"):
            return True
    return False


def _normalise_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return (
        ip.ipv4_mapped
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped
        else ip
    )


def _public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    checked_ip = _normalise_ip(ip)
    if checked_ip in BLOCKED_METADATA_IPS:
        return False
    return not (
        checked_ip.is_private
        or checked_ip.is_loopback
        or checked_ip.is_link_local
        or checked_ip.is_reserved
        or checked_ip.is_multicast
        or checked_ip.is_unspecified
    )


def validate_upstream_url(url: str) -> None:
    """Validate that an upstream URL is safe for server-side proxy requests."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        allowed = ", ".join(sorted(ALLOWED_SCHEMES))
        raise UpstreamURLBlockedError(f"upstream_url scheme must be one of: {allowed}")

    if not parsed.hostname:
        raise UpstreamURLBlockedError("upstream_url must include a hostname")

    host = _normalise_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as exc:
        raise UpstreamURLBlockedError("upstream_url has an invalid port") from exc

    allowlist = _allowlist_entries()
    if allowlist and not _host_allowed(host, allowlist):
        raise UpstreamURLBlockedError(
            f"upstream host {host!r} is not in UPSTREAM_ALLOWLIST"
        )

    try:
        addrinfo = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UpstreamURLBlockedError(
            f"upstream host {host!r} could not be resolved"
        ) from exc

    if not addrinfo:
        raise UpstreamURLBlockedError(f"upstream host {host!r} did not resolve to any address")

    checked: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for _family, _socket_type, _proto, _canonname, sockaddr in addrinfo:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UpstreamURLBlockedError(
                f"upstream host {host!r} resolved to an invalid IP address"
            ) from exc
        checked_ip = _normalise_ip(ip)
        if checked_ip in checked:
            continue
        checked.add(checked_ip)
        if checked_ip in BLOCKED_METADATA_IPS:
            raise UpstreamURLBlockedError(
                f"upstream host {host!r} resolved to blocked address {checked_ip} "
                "(metadata endpoint)"
            )

    if _env_enabled("ALLOW_PRIVATE_UPSTREAM"):
        return

    for checked_ip in checked:
        if not _public_ip(checked_ip):
            raise UpstreamURLBlockedError(
                f"upstream host {host!r} resolved to blocked address {checked_ip}"
            )
