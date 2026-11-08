"""All tenant predicates are part of SQL, before retrieval."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Table, insert, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from agentproof_api import db
from agentproof_api.auth import Principal
from agentproof_api.run_events import append_run_event
from agentproof_api.security import decode_cursor, encode_cursor, now
from agentproof_api.settings import Settings

TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out"})
ACTIVE = frozenset({"queued", "planning", "awaiting_approval", "running", "investigating"})
EDGES = {
    "queued": "planning",
    "planning": "awaiting_approval",
    "awaiting_approval": "queued",
    "running": "investigating",
    "investigating": "completed",
}
LIMITS = {
    "duration_seconds": 600,
    "browser_actions": 50,
    "model_calls": 4,
    "recoverable_retries": 2,
    "workspace_sandboxes": 1,
    "memory_bytes": 1073741824,
    "cpu": 1,
    "artifact_bytes": 268435456,
    "artifact_retention_days": 7,
}


class Repository:
    def __init__(self, session: AsyncSession, user: Principal, config: Settings) -> None:
        self.session, self.user, self.config = session, user, config

    def visible_projects(self) -> Select[Any]:
        return (
            select(db.projects.c.id)
            .join(db.memberships, db.memberships.c.workspace_id == db.projects.c.workspace_id)
            .where(db.memberships.c.user_id == self.user.id)
        )

    def visible_runs(self) -> Select[Any]:
        return select(db.runs.c.id).where(db.runs.c.project_id.in_(self.visible_projects()))

    async def workspace(
        self, workspace_id: UUID, *, write: bool = False, admin: bool = False
    ) -> str:
        if write or admin:
            await self.session.execute(
                select(db.workspaces.c.id)
                .where(
                    db.workspaces.c.id == workspace_id,
                    db.workspaces.c.id.in_(
                        select(db.memberships.c.workspace_id).where(
                            db.memberships.c.user_id == self.user.id
                        )
                    ),
                )
                .with_for_update()
            )
        role = (
            await self.session.execute(
                select(db.memberships.c.role)
                .where(
                    db.memberships.c.workspace_id == workspace_id,
                    db.memberships.c.user_id == self.user.id,
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if role is None:
            raise HTTPException(404, "NOT_FOUND")
        if (write and role == "viewer") or (admin and role not in ("owner", "admin")):
            raise HTTPException(403, "ROLE_FORBIDDEN")
        return str(role)

    async def project(
        self, project_id: UUID, *, write: bool = False, admin: bool = False
    ) -> dict[str, Any]:
        query = select(db.projects).where(
            db.projects.c.id == project_id, db.projects.c.id.in_(self.visible_projects())
        )
        if write or admin:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().first()
        if row is None:
            raise HTTPException(404, "NOT_FOUND")
        await self.workspace(row["workspace_id"], write=write, admin=admin)
        return dict(row)

    async def plan(self, plan_id: UUID, *, write: bool = False) -> dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    select(db.plans).where(
                        db.plans.c.id == plan_id, db.plans.c.project_id.in_(self.visible_projects())
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "NOT_FOUND")
        await self.project(row["project_id"], write=write)
        current = (
            (
                await self.session.execute(
                    select(db.plans).where(
                        db.plans.c.id == plan_id, db.plans.c.project_id.in_(self.visible_projects())
                    )
                )
            )
            .mappings()
            .one()
        )
        return dict(current)

    async def run(self, run_id: UUID, *, write: bool = False) -> dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    select(db.runs).where(
                        db.runs.c.id == run_id, db.runs.c.project_id.in_(self.visible_projects())
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "NOT_FOUND")
        await self.project(row["project_id"], write=write)
        # Re-read after project lock: cancellation/deletion cannot use a stale lifecycle.
        current = (
            (
                await self.session.execute(
                    select(db.runs).where(
                        db.runs.c.id == run_id, db.runs.c.project_id.in_(self.visible_projects())
                    )
                )
            )
            .mappings()
            .first()
        )
        if current is None:
            raise HTTPException(404, "NOT_FOUND")
        return dict(current)

    async def artifact(self, artifact_id: UUID) -> dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    select(db.artifacts).where(
                        db.artifacts.c.id == artifact_id,
                        db.artifacts.c.run_id.in_(self.visible_runs()),
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "NOT_FOUND")
        return dict(row)

    async def page(
        self, table: Table, query: Select[Any], scope: str, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        scoped = f"{self.user.id}:{scope}"
        ordinal_order = table.name == "run_steps"
        if cursor:
            timestamp, row_id = decode_cursor(self.config, scoped, cursor)
            if ordinal_order:
                ordinal = (
                    await self.session.execute(
                        query.where(
                            table.c.id == row_id, table.c.created_at == timestamp
                        ).with_only_columns(table.c.ordinal)
                    )
                ).scalar_one_or_none()
                if ordinal is None:
                    raise HTTPException(400, "INVALID_CURSOR")
                query = query.where(table.c.ordinal > ordinal)
            else:
                query = query.where(
                    tuple_(table.c.created_at, table.c.id) < tuple_(timestamp, row_id)
                )
        ordered = (
            query.order_by(table.c.ordinal)
            if ordinal_order
            else query.order_by(table.c.created_at.desc(), table.c.id.desc())
        )
        result = (await self.session.execute(ordered.limit(limit + 1))).mappings().all()
        rows = [dict(row) for row in result[:limit]]
        next_cursor = None
        if len(result) > limit and rows:
            last = rows[-1]
            next_cursor = encode_cursor(self.config, scoped, last["created_at"], last["id"])
        return rows, next_cursor

    async def audit(
        self,
        workspace_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self.session.execute(
            insert(db.audit_events).values(
                workspace_id=workspace_id,
                actor_id=self.user.id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details_redacted=details or {},
            )
        )


async def transition(
    session: AsyncSession,
    run_id: UUID,
    expected: str,
    target: str,
    *,
    verdict: str | None = None,
    observed_assertions: bool = False,
    error_code: str | None = None,
) -> bool:
    valid = EDGES.get(expected) == target or (
        expected == "awaiting_approval" and target == "running"
    )
    if expected not in ACTIVE or not (valid or target in {"failed", "cancelled", "timed_out"}):
        raise ValueError("Invalid run transition")
    if target in TERMINAL and verdict is None:
        verdict = "ERROR" if target in {"failed", "timed_out"} else "INCONCLUSIVE"
    if verdict is not None and (
        target not in TERMINAL or (verdict in {"PASS", "FAIL"} and not observed_assertions)
    ):
        raise ValueError("Observed assertions are required for PASS or FAIL")
    values: dict[str, Any] = {"status": target}
    if target == "running":
        values["started_at"] = now()
    if target in TERMINAL:
        values.update(finished_at=now(), verdict=verdict)
    if target in TERMINAL or target in {"queued", "awaiting_approval"}:
        values.update(worker_lease_id=None, worker_lease_expires_at=None)
    result = await session.execute(
        update(db.runs)
        .where(db.runs.c.id == run_id, db.runs.c.status == expected)
        .values(**values)
        .returning(db.runs.c.id)
    )
    changed = result.scalar_one_or_none() is not None
    if changed:
        event_type = {
            "queued": "run.queued",
            "planning": "run.planning",
            "awaiting_approval": "plan.approval_required",
            "running": "run.started",
            "investigating": "run.investigating",
            "completed": "run.completed",
            "failed": "run.failed",
            "cancelled": "run.cancelled",
            "timed_out": "run.timed_out",
        }[target]
        await append_run_event(
            session,
            run_id,
            event_type,
            {"status": target, "verdict": verdict, "error_code": error_code},
        )
    return changed
