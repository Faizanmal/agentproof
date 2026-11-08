"""Deterministic guards shared by all future browser/egress adapters."""

import hashlib
import hmac
import json
import posixpath
from fnmatch import fnmatchcase
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db
from agentproof_api.security import signing_key
from agentproof_api.settings import Settings
from agentproof_api.targets import canonical_url, connection_targets, demo_allowed


class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SideEffects(Rules):
    fixture_forms: bool = False
    fixture_paths: list[str] = Field(default_factory=list, max_length=20)
    payment: Literal[False] = False
    messaging: Literal[False] = False
    deletion: Literal[False] = False
    account_modification: Literal[False] = False
    production_export: Literal[False] = False


class Approvals(Rules):
    plan_review: Literal[True] = True
    test_accounts_only: Literal[True] = True


class TargetPolicy(Rules):
    authorized_origins: list[str] = Field(min_length=1, max_length=10)
    allowed_paths: list[str] = Field(default_factory=lambda: ["/*"], min_length=1, max_length=30)
    blocked_paths: list[str] = Field(default_factory=list, max_length=30)
    allowed_methods: list[Literal["GET", "HEAD", "POST"]] = Field(
        default=["GET", "HEAD"], min_length=1, max_length=3
    )
    max_pages: int = Field(default=10, ge=1, le=50)
    max_depth: int = Field(default=3, ge=0, le=10)
    max_redirects: int = Field(default=3, ge=0, le=5)
    max_actions: int = Field(default=50, ge=1, le=50)
    max_duration_seconds: int = Field(default=600, ge=1, le=600)
    max_concurrent_runs: int = Field(default=1, ge=1, le=1)
    robots_behavior: Literal["respect"] = "respect"
    side_effect_rules: SideEffects = Field(default_factory=SideEffects)
    approval_rules: Approvals = Field(default_factory=Approvals)
    credential_ref_ids: list[UUID] = Field(default_factory=list, max_length=10)
    safe_click_selectors: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("authorized_origins")
    @classmethod
    def origins(cls, values: list[str]) -> list[str]:
        return sorted(set(canonical_url(v, origin_only=True, allow_ip=True)[0] for v in values))

    @field_validator("allowed_paths", "blocked_paths")
    @classmethod
    def paths(cls, values: list[str]) -> list[str]:
        for value in values:
            if (
                not value.startswith("/")
                or value.startswith("//")
                or ".." in value
                or "%" in value
                or "?" in value
                or chr(92) in value
                or "[" in value
            ):
                raise ValueError("Use absolute path globs without traversal or encodings")
        return values


class Action(Rules):
    url: str = Field(max_length=4096)
    method: str = Field(default="GET", max_length=12)
    action: Literal["read", "navigate", "click", "type", "submit", "download", "websocket"] = (
        "navigate"
    )
    previous_url: str | None = Field(default=None, max_length=4096)
    selector: str | None = Field(default=None, max_length=500)
    synthetic: bool = False
    depth: int = Field(default=0, ge=0)
    redirects: int = Field(default=0, ge=0)
    pages: int = Field(default=0, ge=0)
    actions: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


DANGEROUS = frozenset(
    {
        "pay",
        "payment",
        "payments",
        "checkout",
        "purchase",
        "payout",
        "delete",
        "remove",
        "destroy",
        "invite",
        "invitations",
        "send",
        "email",
        "messages",
        "message",
        "password",
        "reset",
        "account",
        "export",
        "download",
        "logout",
        "unsubscribe",
    }
)


def path_glob_match(path: str, pattern: str) -> bool:
    """Match a path against a policy glob after normalizing both sides.

    ``posixpath.normpath`` strips a trailing slash (except ``/``). Saved rules
    like ``/qa-fixture/`` must still match candidates that normalize to
    ``/qa-fixture``.
    """
    return fnmatchcase(posixpath.normpath(path), posixpath.normpath(pattern))


def path_glob_allowed(path: str, patterns: list[str]) -> bool:
    return any(path_glob_match(path, pattern) for pattern in patterns)


def check_action(policy: TargetPolicy, action: Action, config: Settings) -> tuple[str, list[str]]:
    normalized, path = canonical_url(action.url, allow_ip=True)
    if normalized not in policy.authorized_origins:
        raise HTTPException(403, "THIRD_PARTY_ORIGIN")
    decoded = path
    for _ in range(3):
        updated = unquote(decoded)
        if updated == decoded:
            break
        decoded = updated
    if (
        "%" in decoded
        or chr(92) in decoded
        or decoded.startswith("//")
        or any(ord(c) < 32 for c in decoded)
    ):
        raise HTTPException(403, "PATH_ENCODING_DENIED")
    if any(part in {".", ".."} for part in decoded.split("/")):
        raise HTTPException(403, "PATH_TRAVERSAL_DENIED")
    path = posixpath.normpath(decoded)
    if path_glob_allowed(path, policy.blocked_paths) or not path_glob_allowed(
        path, policy.allowed_paths
    ):
        raise HTTPException(403, "PATH_DENIED")
    words = set(path.casefold().replace("-", "/").replace("_", "/").split("/"))
    query = unquote(urlsplit(action.url).query).casefold()
    if words & DANGEROUS or any(word in query for word in DANGEROUS):
        raise HTTPException(403, "SIDE_EFFECT_DENIED")
    if action.method not in policy.allowed_methods or action.action in {"websocket", "download"}:
        raise HTTPException(403, "METHOD_OR_CHANNEL_DENIED")
    if (
        action.pages >= policy.max_pages
        or action.depth > policy.max_depth
        or action.redirects > policy.max_redirects
        or action.actions >= policy.max_actions
        or action.elapsed_seconds >= policy.max_duration_seconds
    ):
        raise HTTPException(403, "BUDGET_EXCEEDED")
    demo = demo_allowed(normalized, config)
    fixture = (
        demo
        and policy.side_effect_rules.fixture_forms
        and path_glob_allowed(path, policy.side_effect_rules.fixture_paths)
    )
    if action.method == "POST" or action.action in {"type", "submit"}:
        if not fixture or (action.action == "type" and not action.synthetic):
            raise HTTPException(403, "FIXTURE_PERMISSION_REQUIRED")
    if action.action == "click" and action.selector not in policy.safe_click_selectors:
        raise HTTPException(403, "UNREVIEWED_CLICK")
    return normalized, connection_targets(normalized, config, demo=demo)


def snapshot_signature(config: Settings, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(signing_key(config), b"run-policy-v1:" + data, hashlib.sha256).hexdigest()


def verify_snapshot(config: Settings, payload: dict[str, Any], signature: str) -> TargetPolicy:
    if not hmac.compare_digest(snapshot_signature(config, payload), signature):
        raise HTTPException(403, "INVALID_POLICY_SNAPSHOT")
    return TargetPolicy.model_validate(payload["policy"])


async def log_deny(session: AsyncSession, run_id: UUID, code: str) -> None:
    # No target URL, query, selector, body, cookies or credential values enter evidence.
    await session.execute(
        insert(db.run_events).values(
            run_id=run_id, event_type="policy.denied", payload_json={"code": code}
        )
    )
