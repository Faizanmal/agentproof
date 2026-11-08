import base64
import json
import secrets
from uuid import uuid4

import pytest
from agentproof_api.credential_vault import ephemeral_login, open_secret, redact, rotate, seal
from agentproof_api.settings import Settings
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from pydantic import SecretStr


def test_envelope_nonce_context_rotation_and_ephemeral_clear() -> None:
    old = Settings(
        _env_file=None,
        master_encryption_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
    )
    project_id, secret_id = uuid4(), uuid4()
    context = f"project-secret:{project_id}:{secret_id}"
    value = json.dumps(
        # pragma: allowlist nextline secret -- intentionally fake fixture
        {"username": "test-user", "password": "sentinel-private"}
    )  # pragma: allowlist secret
    first, second = seal(old, value, context), seal(old, value, context)
    assert first != second and b"sentinel-private" not in first
    assert open_secret(old, first, context, 1) == value
    with pytest.raises(InvalidTag):
        open_secret(old, first, context + "other", 1)
    envelope = json.loads(first)
    cipher = bytearray(base64.b64decode(envelope["ciphertext"]))
    cipher[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(cipher).decode()
    with pytest.raises(InvalidTag):
        open_secret(old, json.dumps(envelope, separators=(",", ":")).encode(), context, 1)
    new = old.model_copy(
        update={
            "encryption_key_version": 2,
            "encryption_previous_keys": {1: old.master_encryption_key},
            "master_encryption_key": SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
        }
    )
    rotated = rotate(new, first, context, 1)
    retired = new.model_copy(update={"encryption_previous_keys": {}})
    assert open_secret(retired, rotated, context, 2) == value
    with pytest.raises(HTTPException):
        open_secret(retired, first, context, 1)
    with ephemeral_login(new, rotated, project_id, secret_id, 2) as credentials:
        assert credentials["password"] == "sentinel-private"  # pragma: allowlist secret
    assert credentials == {}
    assert redact(
        {
            "Authorization": "bearer",
            "body": "echo sentinel-private",
            "nested": {"Cookie": "session"},
        },
        ("sentinel-private",),
    ) == {
        "Authorization": "[REDACTED]",
        "body": "echo [REDACTED]",
        "nested": {"Cookie": "[REDACTED]"},
    }
