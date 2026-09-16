import socket
from collections.abc import Iterable

import pytest

import netguard


def _fake_addrinfo(*addresses: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            0,
            "",
            (address, 443),
        )
        for address in addresses
    ]


def _resolve_to(monkeypatch: pytest.MonkeyPatch, addresses: Iterable[str]) -> None:
    monkeypatch.setattr(
        netguard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _fake_addrinfo(*addresses),
    )


def test_https_public_ip_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, ["93.184.216.34"])

    netguard.validate_upstream_url("https://api.openai.com/v1/chat/completions")


@pytest.mark.parametrize(
    ("url", "resolved_ip"),
    [
        ("http://127.0.0.1/v1/chat/completions", "127.0.0.1"),
        ("http://169.254.169.254/metadata", "169.254.169.254"),
        ("http://[::ffff:169.254.169.254]/", "::ffff:169.254.169.254"),
        ("http://10.1.2.3/v1/chat/completions", "10.1.2.3"),
    ],
)
def test_blocked_addresses_are_rejected(
    monkeypatch: pytest.MonkeyPatch, url: str, resolved_ip: str
) -> None:
    _resolve_to(monkeypatch, [resolved_ip])

    with pytest.raises(netguard.UpstreamURLBlockedError) as exc:
        netguard.validate_upstream_url(url)

    assert "blocked address" in str(exc.value)


def test_file_scheme_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, ["93.184.216.34"])

    with pytest.raises(netguard.UpstreamURLBlockedError) as exc:
        netguard.validate_upstream_url("file:///etc/passwd")

    assert "scheme" in str(exc.value)


def test_allow_private_upstream_permits_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_UPSTREAM", "1")
    _resolve_to(monkeypatch, ["127.0.0.1"])

    netguard.validate_upstream_url("http://localhost:2009/v1/chat/completions")


def test_dns_failure_is_rejected_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_dns(*_args: object, **_kwargs: object) -> None:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fail_dns)

    with pytest.raises(netguard.UpstreamURLBlockedError) as exc:
        netguard.validate_upstream_url("https://missing.example/v1/chat/completions")

    assert "could not be resolved" in str(exc.value)


def test_allowlist_restricts_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPSTREAM_ALLOWLIST", "api.openai.com,openai.azure.com")
    _resolve_to(monkeypatch, ["93.184.216.34"])

    netguard.validate_upstream_url("https://my-resource.openai.azure.com/openai")
    with pytest.raises(netguard.UpstreamURLBlockedError) as exc:
        netguard.validate_upstream_url("https://example.com/v1/chat/completions")

    assert "UPSTREAM_ALLOWLIST" in str(exc.value)
