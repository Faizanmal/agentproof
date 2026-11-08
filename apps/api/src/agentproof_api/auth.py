"""Persisted sessions; no access or refresh token is returned in JSON."""

from collections.abc import AsyncIterator, Awaitable
from datetime import datetime, timedelta
from typing import Annotated, Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from agentproof_api import db
from agentproof_api.api_models import (
    Credentials,
    CsrfView,
    DevelopmentMail,
    EmailInput,
    Identity,
    LoginInput,
    Message,
    PasswordResetInput,
    PasswordResetRequested,
    TokenInput,
    UserView,
)
from agentproof_api.security import (
    DUMMY_HASH,
    check_password,
    csrf,
    decrypt,
    digest,
    encrypt,
    now,
    passwords,
    require_csrf,
    signing_key,
    token,
)
from agentproof_api.settings import Settings

router = APIRouter(prefix="/api/v1")


async def database(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


DB = Annotated[AsyncSession, Depends(database, scope="function")]


def settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def origin(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Invalid origin")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{parts.hostname.lower()}:{port}"


def check_origin(request: Request, config: Settings) -> None:
    try:
        source = request.headers.get("origin")
        if source is None:
            source = request.headers.get("referer", "")
        if origin(source) != origin(config.public_app_url):
            raise ValueError
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise ValueError
    except ValueError:
        raise HTTPException(403, "ORIGIN_REJECTED") from None
    if (
        request.method in ("POST", "PATCH")
        and request.headers.get("content-type", "").split(";")[0] != "application/json"
    ):
        raise HTTPException(415, "JSON_REQUIRED")


async def rate_limit(
    request: Request, bucket: str, identity: str, limit: int, seconds: int
) -> None:
    redis: Redis = request.app.state.redis
    # Do not trust X-Forwarded-For from arbitrary clients. Configure a trusted ingress later.
    client_ip = request.client.host if request.client else "unknown"
    keys = [f"auth:{bucket}:identity:{digest(identity)}", f"auth:{bucket}:ip:{digest(client_ip)}"]
    script = (
        "local n=redis.call('INCR',KEYS[1]); "
        "if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"
    )
    try:
        for key in keys:
            count = int(await cast(Awaitable[str], redis.eval(script, 1, key, str(seconds))))
            if count > limit:
                raise HTTPException(429, "RATE_LIMITED")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "RATE_LIMIT_UNAVAILABLE") from None


async def identity_from_cookie(
    session: AsyncSession, request: Request, *, refresh: bool = False, lock: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    table = db.sessions
    hashed = digest(request.cookies.get("ap_refresh" if refresh else "ap_session", ""))
    query = select(table).where(
        (table.c.refresh_token_hash if refresh else table.c.access_token_hash) == hashed
    )
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).mappings().first()
    if row is None:
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    stored = dict(row)
    if refresh and stored["revoked_at"] is not None:
        # Commit the family revocation even though this request will return 401.
        await session.execute(
            update(table).where(table.c.family_id == stored["family_id"]).values(revoked_at=now())
        )
        await session.commit()
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    if (
        stored["revoked_at"] is not None
        or stored["expires_at"] <= now()
        or (not refresh and stored["access_expires_at"] <= now())
    ):
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    user = (
        (
            await session.execute(
                select(db.users).where(
                    db.users.c.id == stored["user_id"], db.users.c.disabled_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if user is None:
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    return stored, dict(user)


class Principal:
    def __init__(self, user: dict[str, Any], session_id: UUID) -> None:
        self.user = UserView(
            id=user["id"], email=user["email"], email_verified_at=user["email_verified_at"]
        )
        self.session_id = session_id

    @property
    def id(self) -> UUID:
        return self.user.id

    def verified(self) -> None:
        if self.user.email_verified_at is None:
            raise HTTPException(403, "EMAIL_VERIFICATION_REQUIRED")


async def current_user(request: Request, session: DB) -> Principal:
    stored, user = await identity_from_cookie(session, request)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        require_csrf(request, settings(request), stored["id"])
    return Principal(user, stored["id"])


User = Annotated[Principal, Depends(current_user)]


async def create_session(
    session: AsyncSession,
    request: Request,
    response: Response,
    user: dict[str, Any],
    family_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> Identity:
    config = settings(request)
    signing_key(config)
    access, refresh = token(), token()
    deadline = expires_at or now() + timedelta(seconds=config.refresh_lifetime_seconds)
    access_lifetime = min(
        config.session_lifetime_seconds, max(0, int((deadline - now()).total_seconds()))
    )
    refresh_lifetime = max(0, int((deadline - now()).total_seconds()))
    session_id = uuid4()
    await session.execute(
        insert(db.sessions).values(
            id=session_id,
            user_id=user["id"],
            family_id=family_id or uuid4(),
            access_token_hash=digest(access),
            refresh_token_hash=digest(refresh),
            access_expires_at=now() + timedelta(seconds=access_lifetime),
            expires_at=deadline,
            user_agent_hash=digest(request.headers.get("user-agent", "")),
        )
    )
    for name, value, lifetime in (
        ("ap_session", access, access_lifetime),
        ("ap_refresh", refresh, refresh_lifetime),
    ):
        response.set_cookie(
            name,
            value,
            max_age=lifetime,
            httponly=True,
            secure=config.app_env == "production",
            samesite="lax",
            path="/api/v1",
        )
    return Identity(
        user=UserView(
            id=user["id"], email=user["email"], email_verified_at=user["email_verified_at"]
        ),
        csrf_token=csrf(config, session_id),
    )


@router.post("/auth/register", response_model=Identity, status_code=201)
async def register(
    body: Credentials, request: Request, response: Response, session: DB
) -> Identity:
    config = settings(request)
    if not config.registration_enabled:
        raise HTTPException(403, "REGISTRATION_DISABLED")
    signing_key(config)
    if not mail_ready(config):
        raise HTTPException(503, "MAIL_NOT_CONFIGURED")
    await rate_limit(request, "register", body.email, 5, 3600)
    # Preflight encryption before writing anything. Verification links stay encrypted at rest.
    verification_token, verification_id, user_id = token(), uuid4(), uuid4()
    link = config.public_app_url.rstrip("/") + "/auth/verify#token=" + verification_token
    ciphertext = encrypt(config, link, f"verification:{user_id}")
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    hashed = await run_in_threadpool(passwords.hash, body.password.get_secret_value())
    result = await session.execute(
        pg_insert(db.users)
        .values(id=user_id, email=body.email, password_hash=hashed)
        .on_conflict_do_nothing(index_elements=[db.users.c.email])
        .returning(db.users)
    )
    user = result.mappings().first()
    if user is None:
        raise HTTPException(409, "REGISTRATION_UNAVAILABLE")
    expires = now() + timedelta(hours=1)
    await session.execute(
        insert(db.email_verifications).values(
            id=verification_id,
            user_id=user_id,
            token_hash=digest(verification_token),
            expires_at=expires,
        )
    )
    await session.execute(
        insert(db.mail_outbox).values(
            user_id=user_id,
            purpose="verification",
            ciphertext=ciphertext,
            key_version=config.encryption_key_version,
            expires_at=expires,
        )
    )
    return await create_session(session, request, response, dict(user))


def mail_ready(config: Settings) -> bool:
    if config.mail_backend == "disabled":
        return False
    if config.app_env == "production" and (
        config.mail_backend != "smtp" or not config.smtp_host or not config.mail_from
    ):
        return False
    return True


@router.post("/auth/forgot-password", response_model=PasswordResetRequested)
async def forgot_password(
    body: EmailInput, request: Request, session: DB
) -> PasswordResetRequested:
    config = settings(request)
    if not mail_ready(config):
        raise HTTPException(503, "MAIL_NOT_CONFIGURED")
    signing_key(config)
    await rate_limit(request, "forgot-password", body.email, 5, 3600)
    notice = "If an account exists for that email, reset instructions were sent."
    row = (
        (
            await session.execute(
                select(db.users).where(
                    db.users.c.email == body.email, db.users.c.disabled_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return PasswordResetRequested(message=notice)
    reset_token, reset_id = token(), uuid4()
    link = config.public_app_url.rstrip("/") + "/auth/reset#token=" + reset_token
    ciphertext = encrypt(config, link, f"password_reset:{row['id']}")
    expires = now() + timedelta(hours=1)
    await session.execute(
        update(db.password_resets)
        .where(
            db.password_resets.c.user_id == row["id"],
            db.password_resets.c.used_at.is_(None),
        )
        .values(used_at=now())
    )
    await session.execute(
        insert(db.password_resets).values(
            id=reset_id,
            user_id=row["id"],
            token_hash=digest(reset_token),
            expires_at=expires,
        )
    )
    await session.execute(
        insert(db.mail_outbox).values(
            user_id=row["id"],
            purpose="password_reset",
            ciphertext=ciphertext,
            key_version=config.encryption_key_version,
            expires_at=expires,
        )
    )
    return PasswordResetRequested(
        message=notice,
        development_reset_url=link if config.mail_backend == "development" else None,
    )


@router.post("/auth/reset-password", response_model=Message)
async def reset_password(body: PasswordResetInput, request: Request, session: DB) -> Message:
    await rate_limit(
        request, "reset-password", digest(body.token.get_secret_value()), 10, 3600
    )
    row = (
        (
            await session.execute(
                select(db.password_resets)
                .where(db.password_resets.c.token_hash == digest(body.token.get_secret_value()))
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["used_at"] is not None or row["expires_at"] <= now():
        raise HTTPException(400, "INVALID_RESET")
    user = (
        (
            await session.execute(
                select(db.users)
                .where(db.users.c.id == row["user_id"], db.users.c.disabled_at.is_(None))
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if user is None:
        raise HTTPException(400, "INVALID_RESET")
    hashed = await run_in_threadpool(passwords.hash, body.password.get_secret_value())
    await session.execute(
        update(db.password_resets)
        .where(db.password_resets.c.id == row["id"])
        .values(used_at=now())
    )
    await session.execute(
        update(db.users).where(db.users.c.id == user["id"]).values(password_hash=hashed)
    )
    await session.execute(
        update(db.sessions)
        .where(db.sessions.c.user_id == user["id"], db.sessions.c.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    return Message(message="Password updated")


@router.post("/auth/login", response_model=Identity)
async def login(body: LoginInput, request: Request, response: Response, session: DB) -> Identity:
    config = settings(request)
    await rate_limit(request, "login", body.email, config.auth_rate_limit, 60)
    row = (
        (await session.execute(select(db.users).where(db.users.c.email == body.email)))
        .mappings()
        .first()
    )
    hashed = row["password_hash"] if row is not None else DUMMY_HASH
    valid = await run_in_threadpool(check_password, hashed, body.password.get_secret_value())
    if not valid or row is None or row["disabled_at"] is not None:
        raise HTTPException(401, "INVALID_CREDENTIALS")
    await session.execute(
        update(db.users).where(db.users.c.id == row["id"]).values(last_login_at=now())
    )
    return await create_session(session, request, response, dict(row))


@router.post("/auth/refresh", response_model=Identity)
async def refresh(request: Request, response: Response, session: DB) -> Identity:
    config = settings(request)
    await rate_limit(request, "refresh", digest(request.cookies.get("ap_refresh", "")), 30, 60)
    stored, user = await identity_from_cookie(session, request, refresh=True, lock=True)
    require_csrf(request, config, stored["id"])
    await session.execute(
        update(db.sessions).where(db.sessions.c.id == stored["id"]).values(revoked_at=now())
    )
    return await create_session(
        session, request, response, user, stored["family_id"], stored["expires_at"]
    )


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, session: DB) -> None:
    stored, _ = await identity_from_cookie(session, request, refresh=True, lock=True)
    require_csrf(request, settings(request), stored["id"])
    await session.execute(
        update(db.sessions)
        .where(db.sessions.c.family_id == stored["family_id"])
        .values(revoked_at=now())
    )
    for name in ("ap_session", "ap_refresh"):
        response.delete_cookie(
            name,
            path="/api/v1",
            httponly=True,
            secure=settings(request).app_env == "production",
            samesite="lax",
        )


@router.get("/auth/me", response_model=Identity)
async def me(request: Request, user: User) -> Identity:
    return Identity(user=user.user, csrf_token=csrf(settings(request), user.session_id))


@router.post("/auth/verify-email", response_model=Message)
async def verify_email(body: TokenInput, session: DB) -> Message:
    row = (
        (
            await session.execute(
                select(db.email_verifications)
                .where(db.email_verifications.c.token_hash == digest(body.token.get_secret_value()))
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["used_at"] is not None or row["expires_at"] <= now():
        raise HTTPException(400, "INVALID_VERIFICATION")
    await session.execute(
        update(db.email_verifications)
        .where(db.email_verifications.c.id == row["id"])
        .values(used_at=now())
    )
    await session.execute(
        update(db.users)
        .where(db.users.c.id == row["user_id"], db.users.c.email_verified_at.is_(None))
        .values(email_verified_at=now())
    )
    return Message(message="Email verified")


def development_mail_router(config: Settings) -> APIRouter:
    dev = APIRouter(prefix="/api/v1")
    if config.app_env != "development" or config.mail_backend != "development":
        return dev

    @dev.get("/dev/mail", response_model=DevelopmentMail)
    async def mailbox(request: Request, session: DB, user: User) -> DevelopmentMail:
        row = (
            (
                await session.execute(
                    select(db.mail_outbox)
                    .where(db.mail_outbox.c.user_id == user.id, db.mail_outbox.c.expires_at > now())
                    .order_by(db.mail_outbox.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "NOT_FOUND")
        purpose = row["purpose"]
        context = (
            f"password_reset:{user.id}"
            if purpose == "password_reset"
            else f"verification:{user.id}"
        )
        return DevelopmentMail(
            purpose=purpose,
            url=decrypt(settings(request), row["ciphertext"], context, row["key_version"]),
            expires_at=row["expires_at"],
        )

    return dev


@router.get("/auth/csrf", response_model=CsrfView)
async def refresh_csrf(request: Request, session: DB) -> CsrfView:
    row = (
        await session.execute(
            select(db.sessions.c.id)
            .join(db.users, db.users.c.id == db.sessions.c.user_id)
            .where(
                db.sessions.c.refresh_token_hash == digest(request.cookies.get("ap_refresh", "")),
                db.sessions.c.revoked_at.is_(None),
                db.sessions.c.expires_at > now(),
                db.users.c.disabled_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    return CsrfView(csrf_token=csrf(settings(request), row.id))
