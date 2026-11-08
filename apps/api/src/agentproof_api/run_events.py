"""Transactional, redacted run event log used as the outbox and replay source."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db

EVENT_TYPES = frozenset(
    {
        "run.queued",
        "run.planning",
        "plan.generated",
        "plan.approval_required",
        "run.started",
        "step.started",
        "step.completed",
        "step.failed",
        "artifact.created",
        "finding.created",
        "sandbox.policy_denied",
        "run.investigating",
        "run.completed",
        "run.failed",
        "run.cancel.requested",
        "run.cancelled",
        "run.timed_out",
    }
)
SAFE_FIELDS = frozenset(
    {
        "status",
        "verdict",
        "error_code",
        "step_id",
        "ordinal",
        "sequence",
        "action",
        "attempt",
        "reason",
        "artifact_id",
        "mime_type",
        "bytes",
        "sha256",
        "duration_ms",
        "model_calls",
        "version",
        "plan_id",
        "count",
    }
)


async def append_run_event(
    session: AsyncSession, run_id: UUID, event_type: str, payload: dict[str, Any] | None = None
) -> int:
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported run event")
    safe = {key: value for key, value in (payload or {}).items() if key in SAFE_FIELDS}
    result = await session.execute(
        insert(db.run_events)
        .values(run_id=run_id, event_type=event_type, payload_json=safe)
        .returning(db.run_events.c.id)
    )
    return int(result.scalar_one())
