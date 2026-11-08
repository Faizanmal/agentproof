from __future__ import annotations

import io
import zipfile
from typing import Any
from uuid import UUID

import pytest
from agentproof_api import db
from agentproof_worker.main import publish_outbox
from sqlalchemy import select
from test_routes import approve, fixture_run, plan, project, register, workspace

pytestmark = pytest.mark.integration


async def test_draft_run_waits_for_approval_then_queues_with_replayable_events(
    environment: dict[str, Any],
) -> None:
    client = environment["client"]
    await register(client, "workflow-approval@example.com")
    workspace_id = await workspace(client)
    project_id = await project(client, workspace_id)
    plan_id = await plan(client, project_id)
    created = await client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"plan_id": plan_id, "idempotency_key": "approval-required"},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert created.json()["status"] == "awaiting_approval"

    approval = await approve(client, plan_id)
    assert approval.status_code == 200, approval.text
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "queued"
    assert run["events_url"] == f"/api/v1/runs/{run_id}/events"

    events = await client.get(f"/api/v1/runs/{run_id}/events?follow=false")
    ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids)) and len(ids) >= 2
    assert "event: plan.approval_required" in events.text
    assert "event: run.queued" in events.text
    sample = [
        line.removeprefix("event: ")
        for line in events.text.splitlines()
        if line.startswith("event: ")
    ][:3]
    print("redacted event stream sample: " + " -> ".join(sample))
    replay = await client.get(
        f"/api/v1/runs/{run_id}/events?follow=false",
        headers={"Last-Event-ID": str(ids[-2])},
    )
    assert f"id: {ids[-1]}" in replay.text
    assert f"id: {ids[-2]}" not in replay.text


async def test_committed_outbox_publishes_and_deduplicates_queue_job(
    environment: dict[str, Any],
) -> None:
    client = environment["client"]
    await register(client, "workflow-outbox@example.com")
    workspace_id = await workspace(client)
    project_id = await project(client, workspace_id)
    plan_id = await plan(client, project_id)
    await approve(client, plan_id)
    response = await client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={"plan_id": plan_id, "idempotency_key": "outbox-job"},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    redis = environment["app"].state.redis
    async with environment["factory"]() as session:
        context = {"redis": redis, "sessions": environment["factory"]}
        published = await publish_outbox(context)
        assert published >= 1
        events = (
            await session.execute(
                select(
                    db.run_events.c.id,
                    db.run_events.c.event_type,
                    db.run_events.c.published_at,
                )
                .where(db.run_events.c.run_id == UUID(run_id))
                .order_by(db.run_events.c.id)
            )
        ).all()
        assert events and all(row.published_at is not None for row in events)
        assert [row.id for row in events] == sorted(row.id for row in events)
        queued_id = next(
            row.id for row in events if row.event_type == "run.queued" and row.published_at
        )
        assert await redis.zscore("arq:queue", f"run-event:{queued_id}") is not None
    # A repeated dispatcher pass sees no uncommitted or already published row.
    assert await publish_outbox({"redis": redis, "sessions": environment["factory"]}) == 0


async def test_report_does_not_invent_execution_evidence(environment: dict[str, Any]) -> None:
    client = environment["client"]
    _, _, _, run_id = await fixture_run(environment, "report-no-fake-evidence@example.com")
    report = await client.get(f"/api/v1/runs/{run_id}/report?format=json")
    assert report.status_code == 200
    assert report.json()["evidence_is_execution_only"] is True
    assert report.json()["steps"] == []
    assert report.json()["artifacts"] == []
    markdown = await client.get(f"/api/v1/runs/{run_id}/report?format=markdown")
    assert markdown.status_code == 200 and "No findings were derived" in markdown.text
    exported = await client.get(f"/api/v1/runs/{run_id}/export/playwright")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        spec = archive.read("agentproof-replay.spec.ts").decode()
        readme = archive.read("README.md").decode()
    assert "TODO_MANUAL s1" in spec and "expect(" not in spec
    assert "AGENTPROOF_BASE_URL" in readme
