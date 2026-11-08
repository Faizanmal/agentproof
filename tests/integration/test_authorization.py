from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from agentproof_api import db, target_routes
from agentproof_api.broker_credentials import broker_signature, login_delivery
from agentproof_api.policy import Action
from agentproof_api.run_admission import admit_run
from agentproof_api.runtime_policy import connect_ticket
from agentproof_api.security import digest, now
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from test_routes import approve, fixture_run, register, workspace

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("method", ["dns-txt", "https-file"])
async def test_ownership_proof_single_use_expiry_and_scope(
    environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    client = environment["client"]
    await register(client)
    wid = await workspace(client)
    result = await client.post(
        "/api/v1/projects",
        json={
            "workspace_id": wid,
            "name": "Owned HTTPS fixture",
            "base_url": "https://owned.example",
        },
    )
    pid = result.json()["id"]
    monkeypatch.setattr(target_routes, "connection_targets", lambda *a, **k: ["8.8.8.8"])
    endpoint = f"/api/v1/projects/{pid}"
    issued = await client.post(
        endpoint + "/verification-challenges",
        json={"origin": "https://owned.example:443", "method": method},
    )
    assert issued.status_code == 201, issued.text
    cid, raw = issued.json()["id"], issued.json()["challenge"]
    async with environment["factory"]() as session:
        row = (
            (
                await session.execute(
                    select(db.verification_challenges).where(
                        db.verification_challenges.c.id == UUID(cid)
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["token_hash"] == digest(raw) and raw not in str(dict(row))
    proof = AsyncMock(return_value=["wrong"] if method == "dns-txt" else "wrong")
    monkeypatch.setattr(target_routes, "fetch_txt" if method == "dns-txt" else "fetch_proof", proof)
    assert (
        await client.post(endpoint + "/verify-target", json={"origin_id": cid})
    ).status_code == 409
    proof.return_value = [raw] if method == "dns-txt" else raw
    result = await client.post(endpoint + "/verify-target", json={"origin_id": cid})
    assert result.status_code == 200, result.text
    assert result.json()["normalized_origin"] == "https://owned.example"
    assert (
        await client.post(endpoint + "/verify-target", json={"origin_id": cid})
    ).status_code == 409
    assert (
        await client.post(endpoint + "/verify-target", json={"origin_id": "malformed"})
    ).status_code == 422
    expired = await client.post(
        endpoint + "/verification-challenges",
        json={"origin": "https://owned.example", "method": method},
    )
    eid = expired.json()["id"]
    async with environment["factory"]() as session:
        await session.execute(
            update(db.verification_challenges)
            .where(db.verification_challenges.c.id == UUID(eid))
            .values(expires_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    assert (
        await client.post(endpoint + "/verify-target", json={"origin_id": eid})
    ).status_code == 409
    client.cookies.clear()
    await register(client, "other@example.com")
    assert (
        await client.post(endpoint + "/verify-target", json={"origin_id": cid})
    ).status_code == 404
    assert (
        await client.patch(
            endpoint + "/target-policy", json={"authorized_origins": ["https://owned.example"]}
        )
    ).status_code == 404


async def test_snapshot_immutable_and_unverified_scope_denied(environment: dict[str, Any]) -> None:
    client = environment["client"]
    _, pid, _, rid = await fixture_run(environment)
    response = await client.patch(
        f"/api/v1/projects/{pid}/target-policy",
        json={"authorized_origins": ["https://third-party.example"]},
    )
    assert response.status_code == 409
    async with environment["factory"]() as session:
        snapshot = (
            (
                await session.execute(
                    select(db.run_authorizations).where(db.run_authorizations.c.run_id == UUID(rid))
                )
            )
            .mappings()
            .one()
        )
        assert snapshot["snapshot_json"]["policy"]["authorized_origins"] == [
            "http://127.0.0.1:19090"
        ]
        with pytest.raises(HTTPException, match="THIRD_PARTY_ORIGIN"):
            await connect_ticket(
                session,
                environment["config"],
                snapshot["snapshot_json"],
                snapshot["signature"],
                Action(url="https://untrusted.example/?private=sentinel"),
                "iframe",
            )
        event = (
            await session.execute(
                select(db.run_events.c.payload_json).where(
                    db.run_events.c.run_id == UUID(rid),
                    db.run_events.c.event_type == "policy.denied",
                )
            )
        ).scalar_one()
        assert event == {"code": "THIRD_PARTY_ORIGIN"}
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(db.run_authorizations)
                    .where(db.run_authorizations.c.run_id == UUID(rid))
                    .values(signature="0" * 64)
                )


async def test_scoped_one_use_broker_delivery(environment: dict[str, Any]) -> None:
    client = environment["client"]
    _, pid, plid, initial = await fixture_run(environment)
    result = await client.post(
        f"/api/v1/projects/{pid}/secrets",
        json={
            "label": "test-login",
            "username": "test-user",
            # pragma: allowlist nextline secret -- intentionally fake fixture
            "password": "private-fixture-value",  # pragma: allowlist secret
            "confirmed_test_account": True,
        },
    )
    assert result.status_code == 201, result.text
    sid = result.json()["id"]
    assert (await client.get(f"/api/v1/projects/{pid}/secrets/{sid}")).status_code == 405
    assert (
        await client.post(
            f"/api/v1/projects/{pid}/secrets",
            json={
                "label": "unconfirmed",
                "username": "test",
                # pragma: allowlist nextline secret -- intentionally fake fixture
                "password": "test",
            },  # pragma: allowlist secret
        )
    ).status_code == 422
    result = await client.patch(
        f"/api/v1/projects/{pid}/target-policy",
        json={"authorized_origins": ["http://127.0.0.1:19090"], "credential_ref_ids": [sid]},
    )
    assert result.status_code == 200
    replacement = await client.post(
        f"/api/v1/projects/{pid}/plans",
        json={"goal": "Inspect the owned fixture", "scope": {"credential_ref_id": sid}},
    )
    assert replacement.status_code == 201, replacement.text
    plid = replacement.json()["id"]
    assert (await approve(client, plid)).status_code == 200
    result = await client.post(
        f"/api/v1/projects/{pid}/runs", json={"plan_id": plid, "idempotency_key": "credential-run"}
    )
    assert result.status_code == 202, result.text
    rid = result.json()["id"]
    config = environment["config"].model_copy(
        update={"broker_hmac_key": SecretStr("broker-test-only-key-" * 3)}
    )
    job = {
        "run_id": rid,
        "secret_id": sid,
        "sandbox_id": "sandbox-one",
        "purpose": "login",
        "expires_at": (now() + timedelta(seconds=25)).isoformat(),
    }
    signature = broker_signature(config, job)
    async with environment["factory"]() as session:
        with pytest.raises(HTTPException, match="BROKER_AUTHENTICATION_FAILED"):
            async with login_delivery(session, config, job, "0" * 64):
                pass
        with pytest.raises(HTTPException, match="SANDBOX_LEASE_REQUIRED"):
            async with login_delivery(session, config, job, signature):
                pass
        for state in ["planning", "awaiting_approval"]:
            await session.execute(
                update(db.runs).where(db.runs.c.id == UUID(rid)).values(status=state)
            )
        await admit_run(
            session,
            config,
            UUID(rid),
            "sandbox-one",
            uuid4(),
            "nonce-one-abcdefghijklmnopqrstuvwxyz",  # pragma: allowlist secret
        )
        await session.commit()
        with pytest.raises(HTTPException, match="CONCURRENCY_LIMIT"):
            await admit_run(
                session,
                config,
                UUID(initial),
                "sandbox-two",
                uuid4(),
                "nonce-two-abcdefghijklmnopqrstuvwxyz",  # pragma: allowlist secret
            )
        async with login_delivery(session, config, job, signature) as credentials:
            assert credentials["password"] == "private-fixture-value"  # pragma: allowlist secret
        assert credentials == {}
        with pytest.raises(HTTPException, match="CREDENTIAL_ALREADY_DELIVERED"):
            async with login_delivery(session, config, job, signature):
                pass
        other = dict(job, sandbox_id="sandbox-other")
        with pytest.raises(HTTPException, match="SANDBOX_LEASE_REQUIRED"):
            async with login_delivery(session, config, other, broker_signature(config, other)):
                pass
