"""Strict URL normalization and per-connection DNS pinning. No proxy or redirect following."""

import asyncio
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import idna
from fastapi import HTTPException

from agentproof_api.settings import Settings

WELL_KNOWN = "/.well-known/agentproof-verification"
BLOCKED_PORTS = frozenset({22, 25, 53, 3000, 5432, 6379, 8000, 8090})
MANDATORY_POLICY = {
    "prohibited": ["payment", "messaging", "destructive", "captcha_bypass", "shell"]
}


def canonical_url(
    value: str, *, origin_only: bool = False, allow_ip: bool = False
) -> tuple[str, str]:
    try:
        if not value or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "%" in parts.netloc
            or parts.fragment
        ):
            raise ValueError
        host = parts.hostname
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal:
            if not allow_ip:
                raise ValueError
            host = literal.compressed
        else:
            # Reject the alternate integer/octal/short IPv4 forms accepted by some stacks.
            try:
                socket.inet_aton(host)
            except OSError:
                pass
            else:
                raise ValueError
            host = idna.encode(host, uts46=False, std3_rules=True).decode().lower()
            if host.endswith(".") or "." not in host or host.rsplit(".", 1)[-1].isdigit():
                raise ValueError
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if port < 1 or port > 65535:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        normalized = f"{parts.scheme}://{authority}"
        if port != (443 if parts.scheme == "https" else 80):
            normalized += f":{port}"
        if origin_only and (parts.path not in {"", "/"} or parts.query):
            raise ValueError
        return normalized, parts.path or "/"
    except (ValueError, UnicodeError, idna.IDNAError):
        raise HTTPException(422, "ORIGIN_INVALID") from None


def exact_origin(value: str) -> str:
    return canonical_url(value, origin_only=True)[0]


def demo_allowed(value: str, config: Settings) -> bool:
    if not config.demo_mode_enabled or (
        config.app_env == "production" and not config.production_demo_enabled
    ):
        return False
    try:
        normalized = canonical_url(value, origin_only=True, allow_ip=True)[0]
        return normalized in {
            canonical_url(v, origin_only=True, allow_ip=True)[0] for v in config.demo_origins
        }
    except HTTPException:
        return False


def public_targets(host: str, port: int, *, allow_loopback: bool = False) -> list[str]:
    # Compatibility entry point is always public-only. A boolean never authorizes local access.
    if (
        port in BLOCKED_PORTS
        or host.lower()
        in {
            "metadata",
            "metadata.google.internal",
            "metadata.google.com",
            "localhost",
            "localhost.localdomain",
        }
        or host.endswith((".internal", ".local"))
    ):
        raise HTTPException(422, "ORIGIN_NOT_PUBLIC")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal:
        raise HTTPException(422, "ORIGIN_NOT_PUBLIC")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise HTTPException(409, "DNS_UNAVAILABLE") from None
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise HTTPException(422, "ORIGIN_NOT_PUBLIC")
    for value in addresses:
        address = ipaddress.ip_address(value)
        mapped = getattr(address, "ipv4_mapped", None)
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or mapped is not None
            or getattr(address, "sixtofour", None) is not None
            or getattr(address, "teredo", None) is not None
        ):
            raise HTTPException(422, "ORIGIN_NOT_PUBLIC")
    return addresses


def connection_targets(normalized: str, config: Settings, *, demo: bool = False) -> list[str]:
    parts = urlsplit(normalized)
    host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
    if demo:
        if not demo_allowed(normalized, config) or port in BLOCKED_PORTS:
            raise HTTPException(422, "DEMO_SCOPE_DENIED")
        # Demo mode is confined to IPv4 loopback; private LANs and metadata remain denied.
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise HTTPException(422, "DEMO_SCOPE_DENIED") from None
        if address.version != 4 or not address.is_loopback:
            raise HTTPException(422, "DEMO_SCOPE_DENIED")
        return [str(address)]
    canonical_url(normalized, origin_only=True)
    if parts.scheme != "https":
        raise HTTPException(422, "HTTPS_REQUIRED")
    return public_targets(host, port)


def _read_proof(ip: str, port: int, host: str, scheme: str) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(ip, port, timeout=5)
    raw: socket.socket | None = None
    try:
        raw = socket.create_connection((ip, port), timeout=5)
        if scheme == "https":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        connection.sock = raw
        host_header = host if port in (80, 443) else f"{host}:{port}"
        connection.putrequest("GET", WELL_KNOWN, skip_host=True, skip_accept_encoding=True)
        for key, value in {
            "Host": host_header,
            "User-Agent": "agentproof-proof/1",
            "Accept": "text/plain",
            "Connection": "close",
        }.items():
            connection.putheader(key, value)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read(129)
    finally:
        connection.close()
        if raw is not None:
            raw.close()


async def fetch_proof(
    normalized_origin: str, *, allow_loopback: bool = False, config: Settings | None = None
) -> str:
    config = config or Settings(_env_file=None)
    parts = urlsplit(normalized_origin)
    addresses = await asyncio.to_thread(connection_targets, normalized_origin, config)
    try:
        # Resolve afresh immediately before this connection; reject a changed answer set.
        again = await asyncio.to_thread(connection_targets, normalized_origin, config)
        if addresses != again:
            raise HTTPException(409, "DNS_REBINDING")
        status, body = await asyncio.to_thread(
            _read_proof, again[0], parts.port or 443, parts.hostname or "", parts.scheme
        )
        if status != 200 or not 1 <= len(body) <= 128:
            raise HTTPException(409, "VERIFICATION_FAILED")
        return body.decode("ascii").strip()
    except (OSError, UnicodeError, http.client.HTTPException):
        raise HTTPException(409, "VERIFICATION_FAILED") from None


async def fetch_txt(normalized_origin: str, config: Settings) -> list[str]:
    await asyncio.to_thread(connection_targets, normalized_origin, config)
    host = urlsplit(normalized_origin).hostname or ""
    try:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = 5
        answers = await resolver.resolve("_agentproof-verification." + host, "TXT", search=False)
        values = []
        for record in list(answers)[:32]:
            value = b"".join(record.strings)
            if len(value) <= 128:
                values.append(value.decode("ascii"))
        return values
    except (dns.exception.DNSException, UnicodeError):
        raise HTTPException(409, "VERIFICATION_FAILED") from None
