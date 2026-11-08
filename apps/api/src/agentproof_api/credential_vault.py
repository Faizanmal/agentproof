"""Versioned envelope encryption and scoped in-process login secret lifetime."""

import base64
import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agentproof_api.security import decrypt, encrypt
from agentproof_api.settings import Settings


def seal(config: Settings, value: str, context: str) -> bytes:
    key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    wrapped = encrypt(config, base64.b64encode(key).decode(), context + ":dek")
    ciphertext = AESGCM(key).encrypt(nonce, value.encode(), context.encode())
    return json.dumps(
        {
            "format": "envelope-v1",
            "wrapped": base64.b64encode(wrapped).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        },
        separators=(",", ":"),
    ).encode()


def open_secret(config: Settings, value: bytes, context: str, version: int) -> str:
    # Existing direct AES-GCM rows remain decryptable for controlled migration.
    if not value.startswith(b'{"format":"envelope-v1"'):
        return decrypt(config, value, context, version)
    envelope = json.loads(value)
    key = base64.b64decode(
        decrypt(config, base64.b64decode(envelope["wrapped"]), context + ":dek", version),
        validate=True,
    )
    return (
        AESGCM(key)
        .decrypt(
            base64.b64decode(envelope["nonce"]),
            base64.b64decode(envelope["ciphertext"]),
            context.encode(),
        )
        .decode()
    )


def rotate(config: Settings, value: bytes, context: str, old_version: int) -> bytes:
    return seal(config, open_secret(config, value, context, old_version), context)


@contextmanager
def ephemeral_login(
    config: Settings, value: bytes, project_id: UUID, secret_id: UUID, version: int
) -> Iterator[dict[str, str]]:
    credentials = json.loads(
        open_secret(config, value, f"project-secret:{project_id}:{secret_id}", version)
    )
    try:
        yield credentials
    finally:
        credentials.clear()
        # Python strings cannot promise secure RAM erasure. Destroy the disposable runner.


def redact(value: Any, sensitive_values: tuple[str, ...] = ()) -> Any:
    forbidden = {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "username",
        "token",
        "secret",
        "credentials",
    }
    if isinstance(value, dict):
        return {
            str(k): (
                "[REDACTED]" if str(k).casefold() in forbidden else redact(v, sensitive_values)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, sensitive_values) for v in value]
    if isinstance(value, str):
        for secret in sensitive_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value
