"""Explicit operator delivery of the durable, encrypted mail outbox."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentproof_api import db
from agentproof_api.security import decrypt, now
from agentproof_api.settings import Settings


def send_mail(config: Settings, recipient: str, purpose: str, link: str) -> None:
    if config.mail_backend != "smtp" or not config.smtp_host or not config.mail_from:
        raise RuntimeError("SMTP is not configured")
    if purpose == "password_reset":
        subject = "Reset your AgentProof password"
        body = "Reset your password within one hour:\n" + link
    else:
        subject = "Verify your AgentProof email"
        body = "Verify your email within one hour:\n" + link
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = (
        config.mail_from,
        recipient,
        subject,
    )
    message.set_content(body)
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10) as client:
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        if config.smtp_user:
            client.login(config.smtp_user, config.smtp_password.get_secret_value())
        client.send_message(message)


async def try_send_outbox_now(
    session: AsyncSession,
    config: Settings,
    *,
    outbox_id: UUID,
    recipient: str,
    purpose: str,
    link: str,
) -> bool:
    """Best-effort immediate SMTP send. Failures leave the outbox row for retry."""
    if config.mail_backend != "smtp" or not config.smtp_host or not config.mail_from:
        return False
    try:
        await asyncio.to_thread(send_mail, config, recipient, purpose, link)
    except Exception:
        return False
    await session.execute(
        update(db.mail_outbox)
        .where(db.mail_outbox.c.id == outbox_id, db.mail_outbox.c.delivered_at.is_(None))
        .values(delivered_at=now())
    )
    return True


async def deliver_outbox(config: Settings) -> int:
    if config.mail_backend != "smtp" or not config.smtp_host or not config.mail_from:
        raise RuntimeError("SMTP is not configured")
    engine = create_async_engine(config.database_url.get_secret_value())
    delivered = 0
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(db.mail_outbox, db.users.c.email)
                        .join(db.users, db.users.c.id == db.mail_outbox.c.user_id)
                        .where(
                            db.mail_outbox.c.delivered_at.is_(None),
                            db.mail_outbox.c.expires_at > now(),
                            db.users.c.disabled_at.is_(None),
                        )
                        .order_by(db.mail_outbox.c.created_at)
                        .limit(20)
                        .with_for_update(of=db.mail_outbox, skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                purpose = row["purpose"]
                context = (
                    f"password_reset:{row['user_id']}"
                    if purpose == "password_reset"
                    else f"verification:{row['user_id']}"
                )
                link = decrypt(config, row["ciphertext"], context, row["key_version"])
                await asyncio.to_thread(send_mail, config, row["email"], purpose, link)
                await session.execute(
                    update(db.mail_outbox)
                    .where(db.mail_outbox.c.id == row["id"])
                    .values(delivered_at=now())
                )
                delivered += 1
        return delivered
    finally:
        await engine.dispose()
