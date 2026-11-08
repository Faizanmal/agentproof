"""Required adapter for navigation, subframes, redirects, fetch/XHR and sockets.
No browser adapter is enabled until sandbox network egress is installed.
"""

import asyncio
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api.plan_schema import TestScope
from agentproof_api.policy import Action, check_action, log_deny, verify_snapshot
from agentproof_api.security import now
from agentproof_api.settings import Settings
from agentproof_api.targets import canonical_url


async def authorize_connection(
    session: AsyncSession,
    config: Settings,
    payload: dict[str, Any],
    signature: str,
    action: Action,
    channel: Literal["navigation", "iframe", "redirect", "fetch", "xhr", "websocket"],
) -> tuple[str, list[str]]:
    policy = verify_snapshot(config, payload, signature)
    if datetime.fromisoformat(payload["valid_until"]) <= now():
        raise HTTPException(403, "AUTHORIZATION_EXPIRED")
    try:
        if channel == "redirect" and (
            action.previous_url is None
            or canonical_url(action.previous_url, allow_ip=True)[0]
            != canonical_url(action.url, allow_ip=True)[0]
        ):
            raise HTTPException(403, "CROSS_ORIGIN_REDIRECT")
        if channel == "websocket":
            raise HTTPException(403, "WEBSOCKET_DENIED")
        result = await asyncio.to_thread(check_action, policy, action, config)
        if "test_scope" in payload:
            scope = TestScope.model_validate(payload["test_scope"])
            narrowed = policy.model_copy(update={"allowed_paths": scope.allowed_paths})
            scoped = await asyncio.to_thread(check_action, narrowed, action, config)
            if scoped != result:
                raise HTTPException(403, "DNS_REBINDING")
        return result
    except HTTPException as error:
        await log_deny(session, UUID(payload["run_id"]), str(error.detail))
        raise


async def connect_ticket(
    session: AsyncSession,
    config: Settings,
    payload: dict[str, Any],
    signature: str,
    action: Action,
    channel: Literal["navigation", "iframe", "redirect", "fetch", "xhr", "websocket"],
) -> tuple[str, list[str]]:
    # A gateway must connect to a returned IP, retain original TLS SNI/Host and
    # call again for every connection. Browser routing alone is insufficient.
    first = await authorize_connection(session, config, payload, signature, action, channel)
    second = await authorize_connection(session, config, payload, signature, action, channel)
    if first != second:
        await log_deny(session, UUID(payload["run_id"]), "DNS_REBINDING")
        raise HTTPException(403, "DNS_REBINDING")
    return second
