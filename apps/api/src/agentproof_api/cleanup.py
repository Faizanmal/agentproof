import asyncio
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db
from agentproof_api.security import now, private_path
from agentproof_api.settings import Settings


async def process_cleanup(session: AsyncSession, config: Settings) -> int:
    expired: Sequence[Any] = (
        (
            await session.execute(
                select(db.artifacts.c.id)
                .where(db.artifacts.c.expires_at <= now())
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    if expired:
        await session.execute(delete(db.artifacts).where(db.artifacts.c.id.in_(expired)))
    rows = (
        (
            await session.execute(
                select(db.artifact_cleanup_jobs)
                .where(
                    db.artifact_cleanup_jobs.c.completed_at.is_(None),
                    db.artifact_cleanup_jobs.c.attempts < 3,
                )
                .order_by(db.artifact_cleanup_jobs.c.created_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        .mappings()
        .all()
    )
    count = 0
    for job in rows:
        try:
            target = private_path(config.artifact_storage_root, job["private_storage_key"])
            await asyncio.to_thread(target.unlink, missing_ok=True)
        except (OSError, ValueError, HTTPException):
            await session.execute(
                update(db.artifact_cleanup_jobs)
                .where(db.artifact_cleanup_jobs.c.id == job["id"])
                .values(attempts=job["attempts"] + 1)
            )
            continue
        await session.execute(
            update(db.artifact_cleanup_jobs)
            .where(db.artifact_cleanup_jobs.c.id == job["id"])
            .values(completed_at=now(), attempts=job["attempts"] + 1)
        )
        count += 1
    return count
