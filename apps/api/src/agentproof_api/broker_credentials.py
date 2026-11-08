"""Private broker contract only. No public credential-read route exists."""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db
from agentproof_api.credential_vault import ephemeral_login
from agentproof_api.policy import verify_snapshot
from agentproof_api.security import now
from agentproof_api.settings import Settings


def broker_signature(config: Settings, job: dict[str, Any]) -> str:
    key = config.broker_hmac_key.get_secret_value().encode()
    if len(key) < 32:
        raise HTTPException(503, "BROKER_NOT_CONFIGURED")
    data = json.dumps(job, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(key, b"login-delivery-v1:" + data, hashlib.sha256).hexdigest()


@asynccontextmanager
async def login_delivery(
    session: AsyncSession, config: Settings, job: dict[str, Any], signature: str
) -> AsyncIterator[dict[str, str]]:
    if not hmac.compare_digest(broker_signature(config, job), signature):
        raise HTTPException(403, "BROKER_AUTHENTICATION_FAILED")
    try:
        if (
            set(job) != {"run_id", "secret_id", "sandbox_id", "purpose", "expires_at"}
            or job["purpose"] != "login"
        ):
            raise ValueError
        deadline = datetime.fromisoformat(job["expires_at"])
        if deadline.tzinfo is None or not 0 < (deadline - now()).total_seconds() <= 30:
            raise ValueError
        run_id, secret_id = UUID(job["run_id"]), UUID(job["secret_id"])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(403, "INVALID_BROKER_JOB") from None
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
    if (
        secret_id not in policy.credential_ref_ids
        or datetime.fromisoformat(snapshot["snapshot_json"]["valid_until"]) <= now()
    ):
        raise HTTPException(403, "CREDENTIAL_SCOPE_DENIED")
    lease = (
        (
            await session.execute(
                select(db.sandbox_leases)
                .join(db.runs, db.runs.c.id == db.sandbox_leases.c.run_id)
                .where(
                    db.sandbox_leases.c.run_id == run_id,
                    db.sandbox_leases.c.broker_sandbox_id == job["sandbox_id"],
                    db.sandbox_leases.c.status == "active",
                    db.sandbox_leases.c.deadline_at > now(),
                    db.runs.c.status == "running",
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if lease is None:
        raise HTTPException(403, "SANDBOX_LEASE_REQUIRED")
    secret = (
        (
            await session.execute(
                select(db.secret_refs)
                .where(
                    db.secret_refs.c.id == secret_id,
                    db.secret_refs.c.project_id == UUID(snapshot["snapshot_json"]["project_id"]),
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if secret is None:
        raise HTTPException(404, "NOT_FOUND")
    issued = (
        await session.execute(
            insert(db.credential_deliveries)
            .values(run_id=run_id, secret_id=secret_id, broker_sandbox_id=job["sandbox_id"])
            .on_conflict_do_nothing(
                index_elements=[
                    db.credential_deliveries.c.run_id,
                    db.credential_deliveries.c.secret_id,
                ]
            )
            .returning(db.credential_deliveries.c.id)
        )
    ).scalar_one_or_none()
    if issued is None:
        raise HTTPException(409, "CREDENTIAL_ALREADY_DELIVERED")
    # Persist the one-use receipt before decrypting. This private job owns its transaction.
    await session.commit()
    with ephemeral_login(
        config, secret["ciphertext"], secret["project_id"], secret_id, secret["key_version"]
    ) as credentials:
        yield credentials
