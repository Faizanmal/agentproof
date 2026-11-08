import asyncio
import copy
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from agentproof_api import db
from sqlalchemy import select
from test_routes import approve, plan, project, register, workspace

pytestmark = pytest.mark.integration


async def setup(environment: dict[str, Any]) -> tuple[httpx.AsyncClient, str, str]:
    client = environment["client"]
    await register(client)
    pid = await project(client, await workspace(client))
    return client, pid, await plan(client, pid)


async def test_generated_plan_persistence_and_scope_invalidates_approval(
    environment: dict[str, Any],
) -> None:
    client, pid, plid = await setup(environment)
    row = (await client.get(f"/api/v1/plans/{plid}")).json()
    assert row["status"] == "draft" and row["plan_json"]["schema_version"] == "1.0"
    assert (
        await client.post(
            f"/api/v1/projects/{pid}/runs",
            json={"plan_id": plid, "idempotency_key": "must-not-run"},
        )
    ).status_code == 409
    assert (await client.post(f"/api/v1/plans/{plid}/approve", json={})).status_code == 422
    assert (
        await client.post(
            f"/api/v1/plans/{plid}/approve",
            json={"version": 999, "authorization_digest": row["authorization_digest"]},
        )
    ).status_code == 409
    assert (await approve(client, plid)).status_code == 200
    events = (await client.get(f"/api/v1/plans/{plid}/events")).json()["data"]
    assert events[0]["event_type"] == "plan.generated"
    async with environment["factory"]() as session:
        usage = (
            (
                await session.execute(
                    select(db.model_usage).where(db.model_usage.c.plan_id == UUID(plid))
                )
            )
            .mappings()
            .one()
        )
        assert usage["prompt_tokens"] == 100 and usage["completion_tokens"] == 80
        assert usage["model_actual"] == "fixture-model"
    changed = await client.patch(
        f"/api/v1/projects/{pid}/target-policy",
        json={"authorized_origins": ["http://127.0.0.1:19090"], "max_pages": 9},
    )
    assert changed.status_code == 200
    assert (await client.get(f"/api/v1/plans/{plid}")).json()["status"] == "rejected"
    assert (await approve(client, plid)).status_code == 409
    assert (
        await client.post(
            f"/api/v1/projects/{pid}/runs", json={"plan_id": plid, "idempotency_key": "stale-scope"}
        )
    ).status_code == 409


async def test_provider_auth_failure_is_persisted_and_redacted(
    environment: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    client, pid, _ = await setup(environment)
    environment["provider_responses"].append(
        httpx.Response(401, json={"error": {"message": "private-provider-error-sentinel"}})
    )
    response = await client.post(f"/api/v1/projects/{pid}/plans", json={"goal": "Denied inference"})
    assert response.status_code == 503
    rows = (await client.get(f"/api/v1/projects/{pid}/plans")).json()["data"]
    rejected = next(r for r in rows if r["goal"] == "Denied inference")
    assert rejected["status"] == "rejected" and rejected["plan_json"] is None
    assert rejected["rejection_code"] == "VULTR_AUTHENTICATION_FAILED"
    async with environment["factory"]() as session:
        usage = (
            (
                await session.execute(
                    select(db.model_usage).where(db.model_usage.c.plan_id == UUID(rejected["id"]))
                )
            )
            .mappings()
            .one()
        )
        assert usage["prompt_tokens"] is None
        assert usage["error_code"] == "VULTR_AUTHENTICATION_FAILED"
    assert "private-provider-error-sentinel" not in response.text + caplog.text
    assert "plan.rejected" in (await client.get(f"/api/v1/plans/{rejected['id']}/events")).text


async def test_high_risk_requires_explicit_step_and_plan_approval(
    environment: dict[str, Any],
) -> None:
    client, pid, _ = await setup(environment)
    assert (
        await client.patch(
            f"/api/v1/projects/{pid}/target-policy",
            json={
                "authorized_origins": ["http://127.0.0.1:19090"],
                "allowed_methods": ["GET", "POST"],
                "side_effect_rules": {"fixture_forms": True, "fixture_paths": ["/demo/*"]},
            },
        )
    ).status_code == 200
    payload = json.loads(
        await asyncio.to_thread(
            (Path(__file__).parents[1] / "fixtures/vultr/completion.json").read_text
        )
    )
    proposed = json.loads(payload["choices"][0]["message"]["content"])
    proposed["steps"][0]["url_path"] = "/demo/login"
    proposed["steps"].insert(
        1,
        {
            "id": "s3",
            "action": "click",
            "target": {"strategy": "role", "role": "button", "name": "Sign in"},
            "requires_approval": False,
        },
    )
    payload["choices"][0]["message"]["content"] = json.dumps(proposed)
    environment["provider_responses"].append(httpx.Response(200, json=payload))
    result = await client.post(
        f"/api/v1/projects/{pid}/plans",
        json={"goal": "Inspect the owned fixture", "scope": {"start_path": "/demo/login"}},
    )
    assert result.status_code == 201, result.text
    row = result.json()
    assert row["risk_level"] == "high"
    assert row["plan_json"]["steps"][1]["requires_approval"] is True
    body = {"version": row["version"], "authorization_digest": row["authorization_digest"]}
    endpoint = f"/api/v1/plans/{row['id']}/approve"
    assert (await client.post(endpoint, json=body)).status_code == 409
    assert (
        await client.post(endpoint, json=body | {"approved_step_ids": ["s3"]})
    ).status_code == 409
    assert (
        await client.post(
            endpoint, json=body | {"approved_step_ids": ["s3"], "approve_high_risk": True}
        )
    ).status_code == 200


async def test_hallucinated_tool_rejected_without_execution(environment: dict[str, Any]) -> None:
    client, pid, _ = await setup(environment)
    payload = json.loads(
        await asyncio.to_thread(
            (Path(__file__).parents[1] / "fixtures/vultr/completion.json").read_text
        )
    )
    proposed = json.loads(payload["choices"][0]["message"]["content"])
    proposed["steps"][0]["action"] = "docker_run"
    payload["choices"][0]["message"]["content"] = json.dumps(proposed)
    environment["provider_responses"].extend(
        [httpx.Response(200, json=copy.deepcopy(payload)) for _ in range(2)]
    )
    result = await client.post(
        f"/api/v1/projects/{pid}/plans", json={"goal": "Inspect the owned fixture"}
    )
    assert result.status_code == 502
    rows = (await client.get(f"/api/v1/projects/{pid}/plans")).json()["data"]
    assert any(r["status"] == "rejected" and r["plan_json"] is None for r in rows)
