import base64
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from agentproof_api.api_models import Navigate, RunInput
from agentproof_api.security import decode_cursor, decrypt, encode_cursor, encrypt, private_path
from agentproof_api.settings import Settings
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError


def config() -> Settings:
    return Settings(
        _env_file=None,
        session_signing_key=SecretStr(secrets.token_urlsafe(40)),
        master_encryption_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
    )


def test_authenticated_encryption_binds_context_and_version() -> None:
    settings = config()
    ciphertext = encrypt(settings, "private-secret-sentinel", "project:one")
    assert b"private-secret-sentinel" not in ciphertext
    assert decrypt(settings, ciphertext, "project:one", 1) == "private-secret-sentinel"
    with pytest.raises(InvalidTag):
        decrypt(settings, ciphertext, "project:other", 1)
    with pytest.raises(HTTPException):
        decrypt(settings, ciphertext, "project:one", 2)


def test_cursor_is_scoped_signed_and_tz_aware() -> None:
    settings = config()
    stamp, row_id = datetime.now(UTC), uuid4()
    cursor = encode_cursor(settings, "tenant:one", stamp, row_id)
    assert decode_cursor(settings, "tenant:one", cursor) == (stamp, row_id)
    with pytest.raises(HTTPException):
        decode_cursor(settings, "tenant:other", cursor)
    with pytest.raises(HTTPException):
        decode_cursor(settings, "tenant:one", cursor[:-5] + "wrong")


@pytest.mark.parametrize(
    "value", ["../outside", "/absolute", "a/../../outside", "C:/outside", "a\\b"]
)
def test_storage_path_cannot_escape_root(tmp_path: Path, value: str) -> None:
    with pytest.raises(HTTPException):
        private_path(tmp_path, value)


@pytest.mark.parametrize(
    "value", ["https://untrusted.invalid", "//untrusted.invalid", "/\\untrusted.invalid"]
)
def test_navigation_requires_relative_path(value: str) -> None:
    with pytest.raises(ValidationError):
        Navigate(action="navigate", path=value)


def test_planner_cannot_supply_verdict() -> None:
    with pytest.raises(ValidationError):
        RunInput.model_validate(
            {"plan_id": str(uuid4()), "idempotency_key": "test", "verdict": "PASS"}
        )


def test_development_routes_absent_in_production() -> None:
    from agentproof_api.app import create_app

    settings = config().model_copy(update={"app_env": "production"})
    paths = create_app(settings).openapi()["paths"]
    assert "/api/v1/dev/mail" not in paths
    assert "/api/v1/projects/{project_id}/secrets" in paths
    assert "/api/v1/projects/{project_id}/verify-target" in paths
