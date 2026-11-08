import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request

from agentproof_api.settings import Settings

passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))


def now() -> datetime:
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token() -> str:
    return secrets.token_urlsafe(32)


def check_password(encoded: str, password: str) -> bool:
    try:
        return bool(passwords.verify(encoded, password))
    except (VerificationError, InvalidHashError):
        return False


def signing_key(config: Settings) -> bytes:
    key = config.session_signing_key.get_secret_value().encode()
    if len(key) < 32:
        raise HTTPException(503, "AUTH_NOT_CONFIGURED")
    return key


def csrf(config: Settings, session_id: UUID) -> str:
    return hmac.new(
        signing_key(config), b"csrf:" + str(session_id).encode(), hashlib.sha256
    ).hexdigest()


def require_csrf(request: Request, config: Settings, session_id: UUID) -> None:
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf(config, session_id)):
        raise HTTPException(403, "CSRF_REJECTED")


def encrypt(config: Settings, plaintext: str, context: str) -> bytes:
    try:
        key = base64.b64decode(config.master_encryption_key.get_secret_value(), validate=True)
    except ValueError:
        raise HTTPException(503, "ENCRYPTION_NOT_CONFIGURED") from None
    if len(key) != 32:
        raise HTTPException(503, "ENCRYPTION_NOT_CONFIGURED")
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), context.encode())


def decrypt(config: Settings, ciphertext: bytes, context: str, key_version: int) -> str:
    configured = (
        config.master_encryption_key
        if key_version == config.encryption_key_version
        else config.encryption_previous_keys.get(key_version)
    )
    if configured is None:
        raise HTTPException(503, "ENCRYPTION_KEY_VERSION_UNAVAILABLE")
    key = base64.b64decode(configured.get_secret_value(), validate=True)
    return AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], context.encode()).decode()


def private_path(root: Path, storage_key: str) -> Path:
    base = root.resolve()
    relative = Path(storage_key)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in storage_key
        or ":" in storage_key
    ):
        raise HTTPException(404, "NOT_FOUND")
    candidate = base.joinpath(relative)
    for part in [candidate, *candidate.parents]:
        if part == base:
            break
        if part.is_symlink():
            raise HTTPException(404, "NOT_FOUND")
    if not candidate.resolve().is_relative_to(base) or candidate.resolve() == base:
        raise HTTPException(404, "NOT_FOUND")
    return candidate


def encode_cursor(config: Settings, scope: str, created_at: datetime, row_id: UUID) -> str:
    data = json.dumps([scope, created_at.isoformat(), str(row_id)], separators=(",", ":")).encode()
    signature = hmac.new(signing_key(config), data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + data).decode().rstrip("=")


def decode_cursor(config: Settings, scope: str, value: str) -> tuple[datetime, UUID]:
    try:
        if len(value) > 2048:
            raise ValueError
        packed = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        signature, data = packed[:32], packed[32:]
        if not hmac.compare_digest(
            signature, hmac.new(signing_key(config), data, hashlib.sha256).digest()
        ):
            raise ValueError
        parts: Any = json.loads(data)
        if parts[0] != scope or len(parts) != 3:
            raise ValueError
        timestamp = datetime.fromisoformat(parts[1])
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp, UUID(parts[2])
    except (ValueError, TypeError, KeyError, IndexError):
        raise HTTPException(400, "INVALID_CURSOR") from None
