"""Private serialized admission; never starts or provisions a sandbox."""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db
from agentproof_api.policy import verify_snapshot
from agentproof_api.repository import transition
from agentproof_api.security import now
from agentproof_api.settings import Settings


async def admit_run(
    session: AsyncSession,
    config: Settings,
    run_id: UUID,
    sandbox_id: str,
    broker_job_id: UUID,
    broker_nonce: str,
) -> None:
    if config.emergency_stop_enabled:
        raise HTTPException(503, "RUN_ADMISSION_EMERGENCY_STOP")
    row = (
        (
            await session.execute(
                select(db.runs.c.project_id, db.runs.c.status, db.projects.c.workspace_id)
                .join(db.projects, db.projects.c.id == db.runs.c.project_id)
                .where(db.runs.c.id == run_id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "NOT_FOUND")
    # All admission paths must take this workspace lock, never a client-supplied counter.
    await session.execute(
        select(db.workspaces.c.id)
        .where(db.workspaces.c.id == row["workspace_id"])
        .with_for_update()
    )
    snapshot = (
        (
            await session.execute(
                select(db.run_authorizations).where(db.run_authorizations.c.run_id == run_id)
            )
        )
        .mappings()
        .first()
    )
    if snapshot is None:
        raise HTTPException(403, "INVALID_POLICY_SNAPSHOT")
    policy = verify_snapshot(config, snapshot["snapshot_json"], snapshot["signature"])
    expiry = datetime.fromisoformat(snapshot["snapshot_json"]["valid_until"])
    if expiry <= now():
        raise HTTPException(403, "AUTHORIZATION_EXPIRED")
    active: list[UUID] = list(
        (
            await session.execute(
                select(db.sandbox_leases.c.id)
                .join(db.runs, db.runs.c.id == db.sandbox_leases.c.run_id)
                .join(db.projects, db.projects.c.id == db.runs.c.project_id)
                .where(
                    db.projects.c.workspace_id == row["workspace_id"],
                    db.sandbox_leases.c.status.in_(["pending", "active"]),
                    db.sandbox_leases.c.run_id != run_id,
                )
                .limit(min(config.max_workspace_concurrent_runs, policy.max_concurrent_runs))
            )
        )
        .scalars()
        .all()
    )
    if active:
        raise HTTPException(409, "CONCURRENCY_LIMIT")
    approved: str = (
        await session.execute(
            select(db.plans.c.status)
            .join(db.runs, db.runs.c.plan_id == db.plans.c.id)
            .where(db.runs.c.id == run_id)
        )
    ).scalar_one()
    if approved != "approved":
        raise HTTPException(409, "APPROVED_PLAN_REQUIRED")
    reviewed = (
        (
            await session.execute(
                select(db.plans)
                .join(db.runs, db.runs.c.plan_id == db.plans.c.id)
                .where(db.runs.c.id == run_id)
            )
        )
        .mappings()
        .one()
    )
    import json

    from pydantic import ValidationError

    from agentproof_api.plan_schema import QAPlanV1
    from agentproof_api.security import digest

    try:
        QAPlanV1.model_validate(reviewed["plan_json"])
    except ValidationError:
        raise HTTPException(409, "CURRENT_PLAN_REQUIRED") from None
    if (
        snapshot["snapshot_json"].get("approved_plan_id") != str(reviewed["id"])
        or snapshot["snapshot_json"].get("approved_plan_version") != reviewed["version"]
        or snapshot["snapshot_json"].get("approved_plan_digest")
        != digest(json.dumps(reviewed["plan_json"], sort_keys=True, separators=(",", ":")))
    ):
        raise HTTPException(409, "PLAN_REVIEW_CHANGED")
    if row["status"] == "planning":
        if not await transition(session, run_id, "planning", "awaiting_approval"):
            raise HTTPException(409, "RUN_STATE_CONFLICT")
    if row["status"] != "running" and not await transition(
        session, run_id, "awaiting_approval", "running"
    ):
        raise HTTPException(409, "RUN_NOT_APPROVED")
    lease_values = {
        "broker_sandbox_id": sandbox_id,
        "broker_job_id": broker_job_id,
        "broker_nonce": broker_nonce,
        "status": "active",
        "deadline_at": min(expiry, now() + timedelta(seconds=policy.max_duration_seconds)),
        "limits_json": {
            "max_actions": policy.max_actions,
            "max_duration_seconds": policy.max_duration_seconds,
        },
    }
    updated = await session.execute(
        update(db.sandbox_leases)
        .where(db.sandbox_leases.c.run_id == run_id, db.sandbox_leases.c.status == "pending")
        .values(**lease_values)
        .returning(db.sandbox_leases.c.id)
    )
    if updated.scalar_one_or_none() is None:
        await session.execute(insert(db.sandbox_leases).values(run_id=run_id, **lease_values))
