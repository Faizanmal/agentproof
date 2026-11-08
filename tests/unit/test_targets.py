import http.client
import socket
import ssl
from unittest.mock import AsyncMock, Mock

import dns.asyncresolver
import pytest
from agentproof_api import targets
from agentproof_api.settings import Settings
from fastapi import HTTPException


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",
        "https://[::1]",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://127.1",
        "https://0x7f000001",
        # pragma: allowlist nextline secret -- intentionally fake fixture
        "https://user:pw@owned.example",  # pragma: allowlist secret
        "https://owned.example%2f@evil.example",
        "https://%31%32%37.0.0.1",
        "https://owned.example./",
        "https://localhost",
        "https://owned.example:99999",
        "https://owned.example/#secret",
        "https://owned.example\\evil",
    ],
)
def test_parser_rejects_host_tricks(url: str) -> None:
    with pytest.raises(HTTPException):
        targets.canonical_url(url, origin_only=True)


def test_idna_and_default_port_normalization() -> None:
    assert (
        targets.canonical_url("https://BÜCHER.example:443/")[0] == "https://xn--bcher-kva.example"
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:8.8.8.8",
        "224.0.0.1",
        "192.0.2.1",
        "100.64.0.1",
        "2001:db8::1",
    ],
)
def test_dns_private_metadata_reserved_denied(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
        ],
    )
    with pytest.raises(HTTPException):
        targets.public_targets("owned.example", 443)


def test_demo_requires_fixed_allowlist_and_explicit_enable() -> None:
    origin = "http://127.0.0.1:19090"
    config = Settings(_env_file=None, demo_mode_enabled=True, demo_origins=[origin])
    assert targets.connection_targets(origin, config, demo=True) == ["127.0.0.1"]
    for denied in ["http://127.0.0.1:19091", "http://10.0.0.1:19090"]:
        with pytest.raises(HTTPException):
            targets.connection_targets(denied, config, demo=True)
    for c in [Settings(_env_file=None), config.model_copy(update={"app_env": "production"})]:
        with pytest.raises(HTTPException):
            targets.connection_targets(origin, c, demo=True)
    with pytest.raises(HTTPException):
        targets.public_targets("127.0.0.1", 19090, allow_loopback=True)


async def test_https_proof_pins_connection_and_rejects_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(targets, "connection_targets", lambda *a, **k: ["8.8.8.8"])
    read = Mock(return_value=(200, b"single-use-token"))
    monkeypatch.setattr(targets, "_read_proof", read)
    assert await targets.fetch_proof("https://owned.example") == "single-use-token"
    read.assert_called_once_with("8.8.8.8", 443, "owned.example", "https")
    read.return_value = (302, b"single-use-token")
    with pytest.raises(HTTPException):
        await targets.fetch_proof("https://owned.example")
    read.side_effect = OSError("untrusted TLS")
    with pytest.raises(HTTPException):
        await targets.fetch_proof("https://owned.example")


async def test_rebinding_never_contacts_private_decoy(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([["8.8.8.8"], ["127.0.0.1"]])
    monkeypatch.setattr(targets, "connection_targets", lambda *a, **k: next(answers))
    read = Mock()
    monkeypatch.setattr(targets, "_read_proof", read)
    with pytest.raises(HTTPException, match="DNS_REBINDING"):
        await targets.fetch_proof("https://owned.example")
    read.assert_not_called()


async def test_dns_txt_exact_name_and_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(targets, "connection_targets", lambda *a, **k: ["8.8.8.8"])
    resolver = Mock()
    resolver.resolve = AsyncMock(return_value=[Mock(strings=(b"single-", b"use-token"))])
    monkeypatch.setattr(dns.asyncresolver, "Resolver", lambda: resolver)
    assert await targets.fetch_txt("https://owned.example", Settings(_env_file=None)) == [
        "single-use-token"
    ]
    resolver.resolve.assert_awaited_once_with(
        "_agentproof-verification.owned.example", "TXT", search=False
    )


def test_tls_transport_uses_numeric_socket_and_original_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, tls = Mock(), Mock()
    create = Mock(return_value=raw)
    context = Mock()
    context.wrap_socket.return_value = tls
    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    connection = Mock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.read.return_value = b"proof"
    monkeypatch.setattr(http.client, "HTTPConnection", lambda *a, **k: connection)
    assert targets._read_proof("8.8.8.8", 443, "owned.example", "https") == (200, b"proof")
    create.assert_called_once_with(("8.8.8.8", 443), timeout=5)
    context.wrap_socket.assert_called_once_with(raw, server_hostname="owned.example")
    connection.putheader.assert_any_call("Host", "owned.example")
    assert connection.sock is tls
    connection.close.assert_called_once()
    tls.close.assert_called_once()
