from datetime import timedelta
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agentproof_api import policy, runtime_policy
from agentproof_api.policy import Action, SideEffects, TargetPolicy, check_action
from agentproof_api.security import now
from agentproof_api.settings import Settings
from fastapi import HTTPException
from pydantic import SecretStr

ORIGIN = "http://127.0.0.1:19090"


def config() -> Settings:
    return Settings(
        _env_file=None,
        demo_mode_enabled=True,
        demo_origins=[ORIGIN],
        session_signing_key=SecretStr("test-signature-key-" * 3),
    )


@pytest.mark.parametrize(
    "url,method,kind,kwargs",
    [
        (ORIGIN + "/private/view", "GET", "navigate", {}),
        (ORIGIN + "/allowed", "DELETE", "navigate", {}),
        ("https://third-party.example/allowed", "GET", "navigate", {}),
        (ORIGIN + "/checkout", "GET", "navigate", {}),
        (ORIGIN + "/allowed?operation=delete", "GET", "navigate", {}),
        (ORIGIN + "/%2e%2e/allowed", "GET", "navigate", {}),
        (ORIGIN + "/%25252e%25252e/allowed", "GET", "navigate", {}),
        (ORIGIN + "/allowed", "POST", "submit", {}),
        (ORIGIN + "/allowed", "GET", "click", {"selector": "#unreviewed"}),
        (ORIGIN + "/allowed", "GET", "download", {}),
        (ORIGIN + "/allowed", "GET", "websocket", {}),
        (ORIGIN + "/allowed", "GET", "navigate", {"pages": 10}),
        (ORIGIN + "/allowed", "GET", "navigate", {"actions": 50}),
        (ORIGIN + "/allowed", "GET", "navigate", {"elapsed_seconds": 600}),
        (ORIGIN + "/allowed", "GET", "navigate", {"depth": 4}),
        (ORIGIN + "/allowed", "GET", "navigate", {"redirects": 4}),
    ],
)
def test_deterministic_denies(url: str, method: str, kind: Any, kwargs: dict[str, Any]) -> None:
    p = TargetPolicy(authorized_origins=[ORIGIN], blocked_paths=["/private/*"])
    with pytest.raises(HTTPException):
        check_action(p, Action(url=url, method=method, action=kind, **kwargs), config())


def test_only_approved_fixture_form_and_synthetic_type() -> None:
    p = TargetPolicy(
        authorized_origins=[ORIGIN],
        allowed_methods=["GET", "POST"],
        side_effect_rules=SideEffects(fixture_forms=True, fixture_paths=["/demo/form"]),
        safe_click_selectors=["#read-link"],
    )
    assert check_action(
        p, Action(url=ORIGIN + "/demo/form", method="POST", action="submit"), config()
    )[1] == ["127.0.0.1"]
    assert check_action(
        p, Action(url=ORIGIN + "/demo/form", action="type", synthetic=True), config()
    )
    with pytest.raises(HTTPException):
        check_action(p, Action(url=ORIGIN + "/demo/form", action="type"), config())
    assert check_action(
        p, Action(url=ORIGIN + "/read", action="click", selector="#read-link"), config()
    )


async def test_signed_expiring_snapshot_all_connection_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = config()
    payload = {
        "run_id": str(uuid4()),
        "valid_until": (now() + timedelta(minutes=1)).isoformat(),
        "policy": TargetPolicy(authorized_origins=[ORIGIN]).model_dump(mode="json"),
    }
    signature = policy.snapshot_signature(c, payload)
    session = AsyncMock()
    for channel in ["navigation", "iframe", "redirect", "fetch", "xhr"]:
        assert await runtime_policy.connect_ticket(
            session,
            c,
            payload,
            signature,
            Action(url=ORIGIN, previous_url=ORIGIN),
            cast(Any, channel),
        )
    with pytest.raises(HTTPException):
        await runtime_policy.connect_ticket(
            session, c, payload, signature, Action(url=ORIGIN), "websocket"
        )
    session.execute.assert_awaited()
    payload["valid_until"] = (now() - timedelta(seconds=1)).isoformat()
    with pytest.raises(HTTPException, match="INVALID_POLICY_SNAPSHOT"):
        policy.verify_snapshot(c, payload, signature)
    with pytest.raises(HTTPException, match="AUTHORIZATION_EXPIRED"):
        await runtime_policy.connect_ticket(
            session, c, payload, policy.snapshot_signature(c, payload), Action(url=ORIGIN), "fetch"
        )
    payload["valid_until"] = (now() + timedelta(minutes=1)).isoformat()
    signature = policy.snapshot_signature(c, payload)
    answers = iter([("one", ["8.8.8.8"]), ("one", ["127.0.0.1"])])
    monkeypatch.setattr(
        runtime_policy, "authorize_connection", AsyncMock(side_effect=lambda *a: next(answers))
    )
    with pytest.raises(HTTPException, match="DNS_REBINDING"):
        await runtime_policy.connect_ticket(
            session, c, payload, signature, Action(url=ORIGIN), "fetch"
        )


async def test_run_snapshot_cannot_exceed_approved_test_paths() -> None:
    c = config()
    payload = {
        "run_id": str(uuid4()),
        "valid_until": (now() + timedelta(minutes=1)).isoformat(),
        "policy": TargetPolicy(authorized_origins=[ORIGIN]).model_dump(mode="json"),
        "test_scope": {"start_path": "/demo", "allowed_paths": ["/demo/*"]},
    }
    session = AsyncMock()
    with pytest.raises(HTTPException, match="PATH_DENIED"):
        await runtime_policy.connect_ticket(
            session,
            c,
            payload,
            policy.snapshot_signature(c, payload),
            Action(url=ORIGIN + "/other-path"),
            "fetch",
        )
    session.execute.assert_awaited()
