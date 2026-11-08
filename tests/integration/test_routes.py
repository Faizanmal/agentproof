import asyncio
import hashlib
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from agentproof_api import db
from agentproof_api.repository import transition
from agentproof_api.security import now
from sqlalchemy import insert, select, update

pytestmark = pytest.mark.integration
PASSWORD = "Integration-password-2097"  # pragma: allowlist secret


async def register(
    client: httpx.AsyncClient, email: str = "owner@example.com", *, verify: bool = True
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    identity: dict[str, Any] = response.json()
    client.headers["X-CSRF-Token"] = identity["csrf_token"]
    if verify:
        mail = await client.get("/api/v1/dev/mail")
        assert mail.status_code == 200, mail.text
        value = urlsplit(mail.json()["verification_url"]).fragment.removeprefix("token=")
        assert (
            await client.post("/api/v1/auth/verify-email", json={"token": value})
        ).status_code == 200
    return identity


async def workspace(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/workspaces", json={"name": "Owned workspace"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def project(client: httpx.AsyncClient, workspace_id: str, name: str = "Owned fixture") -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"workspace_id": workspace_id, "name": name, "base_url": "http://127.0.0.1:19090"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def plan(client: httpx.AsyncClient, project_id: str) -> str:
    issued = await client.post(
        f"/api/v1/projects/{project_id}/verification-challenges",
        json={"origin": "http://127.0.0.1:19090", "method": "demo"},
    )
    assert issued.status_code == 201, issued.text
    verified = await client.post(
        f"/api/v1/projects/{project_id}/verify-target",
        json={"origin_id": issued.json()["id"], "attestation": True},
    )
    assert verified.status_code == 200, verified.text
    response = await client.post(
        f"/api/v1/projects/{project_id}/plans",
        json={"goal": "Inspect the owned fixture", "scope": {"start_path": "/"}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def approve(client: httpx.AsyncClient, plan_id: str) -> httpx.Response:
    row = (await client.get(f"/api/v1/plans/{plan_id}")).json()
    return await client.post(
        f"/api/v1/plans/{plan_id}/approve",
        json={
            "version": row["version"],
            "authorization_digest": row["authorization_digest"],
            "approved_step_ids": [
                s["id"] for s in row["plan_json"]["steps"] if s["requires_approval"]
            ],
            "approve_high_risk": row["risk_level"] == "high",
        },
    )


async def fixture_run(
    env: dict[str, Any], email: str = "owner@example.com"
) -> tuple[str, str, str, str]:
    client: httpx.AsyncClient = env["client"]
    await register(client, email)
    wid = await workspace(client)
    pid = await project(client, wid)
    plid = await plan(client, pid)
    approved = await approve(client, plid)
    assert approved.status_code == 200, approved.text
    response = await client.post(
        f"/api/v1/projects/{pid}/runs", json={"plan_id": plid, "idempotency_key": "fixture-key"}
    )
    assert response.status_code == 202, response.text
    return wid, pid, plid, str(response.json()["id"])


async def test_forgot_and_reset_password_revokes_sessions(
    environment: dict[str, Any],
) -> None:
    client = environment["client"]
    await register(client, "resetter@example.com")
    missing = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["development_reset_url"] is None
    assert "If an account exists" in missing.json()["message"]
    requested = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "resetter@example.com"}
    )
    assert requested.status_code == 200, requested.text
    reset_url = requested.json()["development_reset_url"]
    assert reset_url and "/auth/reset#token=" in reset_url
    token = urlsplit(reset_url).fragment.removeprefix("token=")
    new_password = "Replacement-password-8841"  # pragma: allowlist secret
    assert (
        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": new_password},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": new_password},
        )
    ).status_code == 400
    client.cookies.clear()
    denied = await client.post(
        "/api/v1/auth/login",
        json={"email": "resetter@example.com", "password": PASSWORD},
    )
    assert denied.status_code == 401
    accepted = await client.post(
        "/api/v1/auth/login",
        json={"email": "resetter@example.com", "password": new_password},
    )
    assert accepted.status_code == 200, accepted.text
    client.headers["X-CSRF-Token"] = accepted.json()["csrf_token"]
    assert accepted.json()["user"]["email_verified_at"] is not None


async def test_resend_verification_issues_fresh_link(environment: dict[str, Any]) -> None:
    client = environment["client"]
    await register(client, "unverified@example.com", verify=False)
    assert (await client.get("/api/v1/auth/me")).json()["user"]["email_verified_at"] is None
    resent = await client.post("/api/v1/auth/resend-verification", json={})
    assert resent.status_code == 200, resent.text
    url = resent.json()["development_verification_url"]
    assert url and "/auth/verify#token=" in url
    token = urlsplit(url).fragment.removeprefix("token=")
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": token})
    ).status_code == 200
    assert (await client.get("/api/v1/auth/me")).json()["user"]["email_verified_at"] is not None


async def test_register_login_refresh_replay_logout(environment: dict[str, Any]) -> None:
    client = environment["client"]
    identity = await register(client)
    assert "password" not in identity and "refresh_token" not in identity
    raw_refresh = client.cookies.get("ap_refresh")
    raw_access = client.cookies.get("ap_session")
    response = await client.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 200, response.text
    assert raw_refresh != client.cookies.get("ap_refresh")
    new_access = client.cookies.get("ap_session")
    response.json()["csrf_token"]
    client.cookies.set("ap_refresh", raw_refresh, domain="localhost.local", path="/api/v1")
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    # The newly rotated access session was revoked by replay detection too.
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login", json={"email": "OWNER@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with environment["factory"]() as session:
        rows = (await session.execute(select(db.sessions))).mappings().all()
        assert all(
            r["refresh_token_hash"] != raw_refresh
            and r["access_token_hash"] not in {raw_access, new_access}
            for r in rows
        )


async def test_origin_csrf_rate_limits_and_verification(environment: dict[str, Any]) -> None:
    client = environment["client"]
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "blocked@example.com", "password": PASSWORD},
        headers={"Origin": "https://untrusted.invalid"},
    )
    assert response.status_code == 403
    await register(client, verify=False)
    wid = await workspace(client)
    pid = await project(client, wid)
    denied = await client.post(f"/api/v1/projects/{pid}/plans", json={"goal": "Inspect fixture"})
    assert denied.status_code == 403
    assert (
        await client.post(
            "/api/v1/workspaces", json={"name": "No CSRF"}, headers={"X-CSRF-Token": "invalid"}
        )
    ).status_code == 403
    bad = {
        "email": "nobody@example.com",
        "password": PASSWORD + "-incorrect",
    }  # pragma: allowlist secret
    statuses = []
    for _ in range(101):
        result = await client.post("/api/v1/auth/login", json=bad)
        statuses.append(result.status_code)
        if result.status_code == 429:
            break
    assert 429 in statuses and set(statuses) <= {401, 429}


async def test_registration_toggle_and_emergency_run_stop(environment: dict[str, Any]) -> None:
    client = environment["client"]
    app = environment["app"]
    original = app.state.settings
    app.state.settings = original.model_copy(update={"registration_enabled": False})
    denied = await client.post(
        "/api/v1/auth/register",
        json={"email": "disabled-signup@example.com", "password": PASSWORD},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "REGISTRATION_DISABLED"

    app.state.settings = original
    await register(client, "emergency-stop@example.com")
    workspace_id = await workspace(client)
    project_id = await project(client, workspace_id)
    app.state.settings = original.model_copy(update={"emergency_stop_enabled": True})
    stopped = await client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"goal": "Must be refused during incident response", "idempotency_key": "stop"},
    )
    assert stopped.status_code == 503
    assert stopped.json()["code"] == "RUN_ADMISSION_EMERGENCY_STOP"


async def test_project_plan_run_idempotency_cancel_and_events(environment: dict[str, Any]) -> None:
    client = environment["client"]
    wid, pid, plid, rid = await fixture_run(environment)
    repeated = await client.post(
        f"/api/v1/projects/{pid}/runs", json={"plan_id": plid, "idempotency_key": "fixture-key"}
    )
    assert repeated.status_code == 200 and repeated.json()["id"] == rid
    events = await client.get(f"/api/v1/runs/{rid}/events?follow=false")
    assert events.status_code == 200 and "queued" in events.text
    event_id = next(
        line.removeprefix("id: ") for line in events.text.splitlines() if line.startswith("id: ")
    )
    replay = await client.get(
        f"/api/v1/runs/{rid}/events?follow=false", headers={"Last-Event-ID": event_id}
    )
    assert "queued" not in replay.text
    assert (await client.delete(f"/api/v1/projects/{pid}")).status_code == 409
    cancelled = await client.post(f"/api/v1/runs/{rid}/cancel", json={})
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    again = await client.post(f"/api/v1/runs/{rid}/cancel", json={})
    assert again.json() == cancelled.json()
    assert (await client.get(f"/api/v1/workspaces/{wid}/audit-events")).json()["data"]


async def test_every_resource_route_rejects_other_tenant(environment: dict[str, Any]) -> None:
    client = environment["client"]
    wid, pid, plid, rid = await fixture_run(environment)
    aid = uuid4()
    async with environment["factory"]() as session:
        await session.execute(
            insert(db.artifacts).values(
                id=aid,
                run_id=UUID(rid),
                kind="screenshot",
                private_storage_key="owned.png",
                sha256="0" * 64,
                mime_type="image/png",
                bytes=0,
                redaction_state="sanitized",
                expires_at=now() + timedelta(days=1),
            )
        )
        await session.commit()
    client.cookies.clear()
    await register(client, "other@example.com")
    reads = [
        f"/workspaces/{wid}/members",
        f"/projects?workspace_id={wid}",
        f"/projects/{pid}",
        f"/projects/{pid}/plans",
        f"/plans/{plid}",
        f"/projects/{pid}/runs",
        f"/runs/{rid}",
        f"/runs/{rid}/steps",
        f"/runs/{rid}/events?follow=false",
        f"/runs/{rid}/artifacts",
        f"/runs/{rid}/report",
        f"/runs/{rid}/export/playwright",
        f"/runs/{rid}/findings",
        f"/artifacts/{aid}/download",
        f"/workspaces/{wid}/audit-events",
        f"/projects/{pid}/origins",
        f"/projects/{pid}/secrets",
    ]
    for route in reads:
        response = await client.get("/api/v1" + route)
        assert response.status_code == 404, (route, response.text)
    writes = [
        ("POST", f"/workspaces/{wid}/members", {"email": "other@example.com", "role": "member"}),
        (
            "POST",
            "/projects",
            {"workspace_id": wid, "name": "Intrusion", "base_url": "http://127.0.0.1:19090"},
        ),
        ("PATCH", f"/projects/{pid}", {"name": "Intrusion"}),
        ("DELETE", f"/projects/{pid}", None),
        ("POST", f"/projects/{pid}/plans", {"goal": "Intrusion"}),
        ("POST", f"/plans/{plid}/approve", {"version": 1, "authorization_digest": "0" * 64}),
        ("POST", f"/projects/{pid}/runs", {"plan_id": plid, "idempotency_key": "intrusion"}),
        ("POST", f"/runs/{rid}/cancel", {}),
        ("POST", f"/projects/{pid}/origins", {"origin": "http://127.0.0.1:19090"}),
        ("POST", f"/projects/{pid}/verify-target", {"origin_id": str(uuid4())}),
        (
            "POST",
            f"/projects/{pid}/secrets",
            {
                "label": "login",
                "username": "qa",
                # pragma: allowlist nextline secret -- intentionally fake fixture
                "password": "secret-value",  # pragma: allowlist secret
                "confirmed_test_account": True,
            },
        ),
        ("DELETE", f"/projects/{pid}/secrets/{uuid4()}", None),
    ]
    for method, route, body in writes:
        response = await client.request(method, "/api/v1" + route, json=body)
        assert response.status_code == 404, (route, response.text)


async def test_membership_roles_and_pagination(environment: dict[str, Any]) -> None:
    client = environment["client"]
    await register(client)
    owner_cookies, owner_csrf = httpx.Cookies(client.cookies), client.headers["X-CSRF-Token"]
    wid = await workspace(client)
    ids = {await project(client, wid, str(n)) for n in range(5)}
    first = await client.get(f"/api/v1/projects?workspace_id={wid}&limit=2")
    assert len(first.json()["data"]) == 2 and first.json()["next_cursor"]
    await project(client, wid, "Inserted between pages")
    collected = {p["id"] for p in first.json()["data"]}
    cursor = first.json()["next_cursor"]
    while cursor:
        page = await client.get(
            f"/api/v1/projects?workspace_id={wid}&limit=2",
            params={"workspace_id": wid, "limit": 2, "cursor": cursor},
        )
        assert page.status_code == 200, page.text
        collected.update(p["id"] for p in page.json()["data"])
        cursor = page.json()["next_cursor"]
    assert collected == ids
    assert (await client.get(f"/api/v1/projects?workspace_id={wid}&limit=101")).status_code == 422
    assert (
        await client.get(f"/api/v1/projects?workspace_id={wid}&cursor=tampered")
    ).status_code == 400
    client.cookies.clear()
    await register(client, "viewer@example.com")
    viewer_cookies, viewer_csrf = httpx.Cookies(client.cookies), client.headers["X-CSRF-Token"]
    client.cookies = owner_cookies
    client.headers["X-CSRF-Token"] = owner_csrf
    assert (
        await client.post(
            f"/api/v1/workspaces/{wid}/members",
            json={"email": "viewer@example.com", "role": "viewer"},
        )
    ).status_code == 201
    client.cookies = viewer_cookies
    client.headers["X-CSRF-Token"] = viewer_csrf
    assert (await client.get(f"/api/v1/projects?workspace_id={wid}")).status_code == 200
    assert (
        await client.post(
            "/api/v1/projects",
            json={"workspace_id": wid, "name": "Denied", "base_url": "http://127.0.0.1:19090"},
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/workspaces/{wid}/members",
            json={"email": "viewer@example.com", "role": "admin"},
        )
    ).status_code == 403
    assert (await client.get(f"/api/v1/workspaces/{wid}/audit-events")).status_code == 403


async def test_artifact_download_integrity_and_cleanup(environment: dict[str, Any]) -> None:
    client = environment["client"]
    _, pid, _, rid = await fixture_run(environment)
    content = b"sanitized evidence"
    (environment["root"] / "evidence.txt").write_bytes(content)
    aid = uuid4()
    async with environment["factory"]() as session:
        await session.execute(
            insert(db.artifacts).values(
                id=aid,
                run_id=UUID(rid),
                kind="text",
                private_storage_key="evidence.txt",
                sha256=hashlib.sha256(content).hexdigest(),
                mime_type="text/plain",
                bytes=len(content),
                redaction_state="sanitized",
                expires_at=now() + timedelta(days=1),
            )
        )
        await session.commit()
    listing = await client.get(f"/api/v1/runs/{rid}/artifacts")
    assert "private_storage_key" not in listing.text
    response = await client.get(f"/api/v1/artifacts/{aid}/download")
    assert response.status_code == 200 and response.content == content, response.text
    expired_id = uuid4()
    async with environment["factory"]() as session:
        await session.execute(
            insert(db.artifacts).values(
                id=expired_id,
                run_id=UUID(rid),
                kind="text",
                private_storage_key="expired.txt",
                sha256=hashlib.sha256(content).hexdigest(),
                mime_type="text/plain",
                bytes=len(content),
                redaction_state="sanitized",
                expires_at=now() - timedelta(seconds=1),
            )
        )
        await session.commit()
    assert (await client.get(f"/api/v1/artifacts/{expired_id}/download")).status_code == 404
    (environment["root"] / "evidence.txt").write_bytes(b"tampered")
    assert (await client.get(f"/api/v1/artifacts/{aid}/download")).status_code == 409
    assert (await client.post(f"/api/v1/runs/{rid}/cancel", json={})).status_code == 202
    assert (await client.delete(f"/api/v1/projects/{pid}")).status_code == 204
    async with environment["factory"]() as session:
        cleanup = (await session.execute(select(db.artifact_cleanup_jobs))).mappings().all()
        assert any(j["private_storage_key"] == "evidence.txt" for j in cleanup)
        from agentproof_api.cleanup import process_cleanup

        assert await process_cleanup(session, environment["config"]) == 2
        assert await process_cleanup(session, environment["config"]) == 0
        assert not (environment["root"] / "evidence.txt").exists()


async def test_transition_compare_and_swap(environment: dict[str, Any]) -> None:
    _, _, _, rid = await fixture_run(environment)
    # A savepoint fixture cannot run independent transactions concurrently; use two
    # real connections on a separately committed disposable test run for the race.
    url = environment["config"].database_url.get_secret_value()
    from agentproof_api.security import passwords
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    uid, wid, pid, plid, race_id = (uuid4() for _ in range(5))
    try:
        async with factory() as session, session.begin():
            await session.execute(
                insert(db.users).values(
                    id=uid,
                    email=f"race-{uid.hex}@example.com",
                    password_hash=passwords.hash(PASSWORD),
                )
            )
            await session.execute(
                insert(db.workspaces).values(id=wid, name="Race", owner_user_id=uid)
            )
            await session.execute(
                insert(db.projects).values(
                    id=pid,
                    workspace_id=wid,
                    name="Race",
                    base_url="http://127.0.0.1:19090",
                    created_by=uid,
                )
            )
            await session.execute(
                insert(db.plans).values(
                    id=plid,
                    project_id=pid,
                    created_by=uid,
                    version=1,
                    goal="Race",
                    risk_level="low",
                    status="draft",
                )
            )
            await session.execute(
                insert(db.runs).values(
                    id=race_id,
                    project_id=pid,
                    plan_id=plid,
                    created_by=uid,
                    idempotency_key="race",
                    status="queued",
                    limits_json={},
                )
            )

        async def contender() -> bool:
            async with factory() as session, session.begin():
                return await transition(session, race_id, "queued", "planning")

        results = await asyncio.gather(contender(), contender())
        assert sorted(results) == [False, True]
        async with factory() as session:
            with pytest.raises(ValueError):
                await transition(session, race_id, "planning", "completed", verdict="PASS")
    finally:
        await engine.dispose()


async def test_expired_access_csrf_recovery_and_cookie_security(
    environment: dict[str, Any],
) -> None:
    client = environment["client"]
    await register(client)
    async with environment["factory"]() as session:
        await session.execute(
            update(db.sessions).values(access_expires_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    recovered = await client.get("/api/v1/auth/csrf")
    assert recovered.status_code == 200
    client.headers["X-CSRF-Token"] = recovered.json()["csrf_token"]
    refreshed = await client.post("/api/v1/auth/refresh", json={})
    assert refreshed.status_code == 200, refreshed.text
    cookies = refreshed.headers.get_list("set-cookie")
    assert len(cookies) == 2 and all(
        "HttpOnly" in c and "SameSite=lax" in c and "Path=/api/v1" in c for c in cookies
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_concurrent_idempotent_creation_and_cancellation(environment: dict[str, Any]) -> None:
    from agentproof_api.app import create_app
    from sqlalchemy.exc import DBAPIError

    app = create_app(environment["config"])
    from agentproof_api.planner import gateway

    app.dependency_overrides[gateway] = environment["app"].dependency_overrides[gateway]
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:3000",
            headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
        ) as client,
    ):
        env = {"client": client, "factory": app.state.session_factory}
        _, pid, plid, _ = await fixture_run(env, "idempotency-race@example.com")

        async def start() -> httpx.Response:
            return await client.post(
                f"/api/v1/projects/{pid}/runs",
                json={"plan_id": plid, "idempotency_key": "concurrent-key"},
            )

        results = await asyncio.gather(start(), start())
        assert sorted(r.status_code for r in results) == [200, 202]
        assert results[0].json()["id"] == results[1].json()["id"]
        run_id = results[0].json()["id"]

        async def cancel() -> httpx.Response:
            return await client.post(f"/api/v1/runs/{run_id}/cancel", json={})

        cancelled = await asyncio.gather(cancel(), cancel())
        assert cancelled[0].status_code == cancelled[1].status_code == 202
        assert cancelled[0].json() == cancelled[1].json()
        async with app.state.session_factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(db.runs)
                    .where(db.runs.c.id == UUID(run_id))
                    .values(summary_json={"tamper": True})
                )
            await session.rollback()


async def test_verification_replay_and_reserved_routes(environment: dict[str, Any]) -> None:
    client = environment["client"]
    await register(client, verify=False)
    mail = await client.get("/api/v1/dev/mail")
    value = urlsplit(mail.json()["verification_url"]).fragment.removeprefix("token=")
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": value})
    ).status_code == 200
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": value})
    ).status_code == 400
    wid = await workspace(client)
    pid = await project(client, wid)
    assert (await client.post(f"/api/v1/projects/{pid}/verify-target", json={})).status_code == 422
    assert (await client.post(f"/api/v1/projects/{pid}/secrets", json={})).status_code == 422


async def test_all_membership_role_boundaries(environment: dict[str, Any]) -> None:
    client = environment["client"]
    wid, pid, plid, rid = await fixture_run(environment)
    owner_cookies, owner_csrf = httpx.Cookies(client.cookies), client.headers["X-CSRF-Token"]
    client.cookies.clear()
    await register(client, "role-check@example.com")
    member_cookies, member_csrf = httpx.Cookies(client.cookies), client.headers["X-CSRF-Token"]
    for role in ("viewer", "member", "admin"):
        client.cookies = owner_cookies
        client.headers["X-CSRF-Token"] = owner_csrf
        result = await client.post(
            f"/api/v1/workspaces/{wid}/members",
            json={"email": "role-check@example.com", "role": role},
        )
        assert result.status_code == 201, result.text
        client.cookies = member_cookies
        client.headers["X-CSRF-Token"] = member_csrf
        for route in (
            f"/projects/{pid}",
            f"/plans/{plid}",
            f"/runs/{rid}",
            f"/runs/{rid}/steps",
            f"/runs/{rid}/events?follow=false",
            f"/runs/{rid}/artifacts",
            f"/runs/{rid}/findings",
            f"/workspaces/{wid}/members",
        ):
            response = await client.get("/api/v1" + route)
            assert response.status_code == 200, (role, route, response.text)
        audit = await client.get(f"/api/v1/workspaces/{wid}/audit-events")
        assert audit.status_code == (200 if role == "admin" else 403)
        patched = await client.patch(f"/api/v1/projects/{pid}", json={"name": "Renamed"})
        assert patched.status_code == (403 if role == "viewer" else 200)
        approval = await approve(client, plid)
        assert approval.status_code == (403 if role == "viewer" else 200)
        cancellation = await client.post(f"/api/v1/runs/{rid}/cancel", json={})
        assert cancellation.status_code == (403 if role == "viewer" else 202)
        denied_owner = await client.post(
            f"/api/v1/workspaces/{wid}/members",
            json={"email": "owner@example.com", "role": "viewer"},
        )
        assert denied_owner.status_code == 403
        if role != "admin":
            assert (await client.delete(f"/api/v1/projects/{pid}")).status_code == 403


async def test_step_pagination_preserves_journey_order(environment: dict[str, Any]) -> None:
    client = environment["client"]
    _, _, _, rid = await fixture_run(environment)
    async with environment["factory"]() as session:
        for ordinal in (2, 0, 1):
            await session.execute(
                insert(db.run_steps).values(
                    run_id=UUID(rid),
                    ordinal=ordinal,
                    typed_action_json={"action": "screenshot", "selector": "body"},
                    expected_json={},
                    observed_json={},
                    status="pending",
                )
            )
        await session.commit()
    values: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        result = await client.get(f"/api/v1/runs/{rid}/steps", params=params)
        assert result.status_code == 200, result.text
        values.extend(step["ordinal"] for step in result.json()["data"])
        cursor = result.json()["next_cursor"]
        if not cursor:
            break
    assert values == [0, 1, 2]


async def test_demo_authorization_and_encrypted_test_login(environment: dict[str, Any]) -> None:
    client = environment["client"]
    await register(client, "origin-owner@example.com")
    wid = await workspace(client)
    pid = await project(client, wid)
    issued = await client.post(
        f"/api/v1/projects/{pid}/verification-challenges",
        json={"origin": "http://127.0.0.1:19090", "method": "demo"},
    )
    assert issued.status_code == 201, issued.text
    cid = issued.json()["id"]
    denied = await client.post(f"/api/v1/projects/{pid}/verify-target", json={"origin_id": cid})
    assert denied.status_code == 403
    verified = await client.post(
        f"/api/v1/projects/{pid}/verify-target", json={"origin_id": cid, "attestation": True}
    )
    assert verified.status_code == 200, verified.text
    assert (
        await client.post(
            f"/api/v1/projects/{pid}/verify-target", json={"origin_id": cid, "attestation": True}
        )
    ).status_code == 409
    stored = await client.post(
        f"/api/v1/projects/{pid}/secrets",
        json={
            "label": "qa-login",
            "username": "qa-user",
            # pragma: allowlist nextline secret -- intentionally fake fixture
            "password": "disposable-test-password",  # pragma: allowlist secret
            "confirmed_test_account": True,
        },
    )
    assert stored.status_code == 201, stored.text
    assert set(stored.json()) == {"id", "label", "created_at"}
    assert "ciphertext" not in stored.text and "password" not in stored.text
    assert (
        await client.get(f"/api/v1/projects/{pid}/secrets/{stored.json()['id']}")
    ).status_code == 405
    policy = {
        "authorized_origins": ["http://127.0.0.1:19090"],
        "credential_ref_ids": [stored.json()["id"]],
    }
    assert (
        await client.patch(f"/api/v1/projects/{pid}/target-policy", json=policy)
    ).status_code == 200
    client.cookies.clear()
    await register(client, "origin-other@example.com")
    assert (await client.get(f"/api/v1/projects/{pid}/authorized-origins")).status_code == 404
    assert (
        await client.delete(f"/api/v1/projects/{pid}/secrets/{stored.json()['id']}")
    ).status_code == 404
