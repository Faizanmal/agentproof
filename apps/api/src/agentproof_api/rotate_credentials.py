"""Bounded operator-only key rotation. No plaintext output or HTTP route."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db
from agentproof_api.credential_vault import rotate
from agentproof_api.security import now
from agentproof_api.settings import Settings


async def rotate_batch(session: AsyncSession, config: Settings, batch_size: int = 100) -> int:
    if not 1 <= batch_size <= 100:
        raise ValueError("batch size must be 1..100")
    rows = (
        (
            await session.execute(
                select(db.secret_refs)
                .where(db.secret_refs.c.key_version != config.encryption_key_version)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        context = f"project-secret:{row['project_id']}:{row['id']}"
        ciphertext = rotate(config, row["ciphertext"], context, row["key_version"])
        await session.execute(
            update(db.secret_refs)
            .where(db.secret_refs.c.id == row["id"])
            .values(
                ciphertext=ciphertext, key_version=config.encryption_key_version, rotated_at=now()
            )
        )
    return len(rows)
