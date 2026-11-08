"""Durable arq orchestration over PostgreSQL truth and broker-owned sandboxes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from agentproof_api import db
from agentproof_api.auth import Principal
from agentproof_api.evidence import redact_tree, store_artifact
from agentproof_api.model_gateway import Budget, ProviderError, VultrGateway
from agentproof_api.plan_routes import authorization, validate_approved
from agentproof_api.plan_schema import GeneratePlanInput, QAPlanV1, TestScope
from agentproof_api.planner import generate
from agentproof_api.policy import verify_snapshot
from agentproof_api.repository import TERMINAL, Repository, transition
from agentproof_api.run_admission import admit_run
from agentproof_api.run_events import append_run_event
from agentproof_api.security import now
from agentproof_api.settings import Settings
from agentproof_api.target_routes import capture_snapshot
from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class BrokerError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"BROKER_HTTP_{status_code}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _broker_signature(key: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    payload = timestamp.encode() + b"." + method.encode() + b"." + path.encode() + b"." + body
    return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


async def startup(ctx: dict[str, Any]) -> None:
    config = Settings()
    engine = create_async_engine(config.database_url.get_secret_value(), pool_pre_ping=True)
    ctx["config"] = config
    ctx["engine"] = engine
    ctx["sessions"] = async_sessionmaker(engine, expire_on_commit=False)
    # trust_env=False ignores SSL_CERT_FILE; pin the operator CA bundle for VPC broker TLS.
    ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    ctx["http"] = httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=20,
        verify=ca_bundle if ca_bundle else True,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


async def publish_outbox(ctx: dict[str, Any]) -> int:
    """Publish committed events and queue delivery; duplicate delivery is expected."""
    redis = ctx["redis"]
    sessions = ctx["sessions"]
    published = 0
    async with sessions() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(db.run_events)
                    .where(db.run_events.c.published_at.is_(None))
                    .order_by(db.run_events.c.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            message = {
                "id": row["id"],
                "run_id": str(row["run_id"]),
                "event": row["event_type"],
                "data": row["payload_json"],
                "created_at": row["created_at"].isoformat(),
            }
            encoded = json.dumps(message, separators=(",", ":"), default=str)
            await redis.publish(f"agentproof:run-events:{row['run_id']}", encoded)
            job_id = f"run-event:{row['id']}"
            if row["event_type"] == "run.queued":
                await redis.enqueue_job(
                    "process_run", str(row["run_id"]), _job_id=job_id, _expires=86400
                )
            elif row["event_type"] == "run.cancel.requested":
                await redis.enqueue_job(
                    "process_cancel", str(row["run_id"]), _job_id=job_id, _expires=86400
                )
            await session.execute(
                update(db.run_events)
                .where(db.run_events.c.id == row["id"], db.run_events.c.published_at.is_(None))
                .values(published_at=now(), publish_attempts=db.run_events.c.publish_attempts + 1)
            )
            published += 1
    return published


async def reconcile(ctx: dict[str, Any]) -> int:
    """Requeue queued or expired leased work with stable database claims."""
    sessions = ctx["sessions"]
    async with sessions() as session:
        rows = (
            (
                await session.execute(
                    select(
                        db.runs.c.id,
                        db.runs.c.status,
                        db.runs.c.worker_lease_expires_at,
                        db.runs.c.attempt_count,
                    )
                    .where(
                        db.runs.c.status.in_(["queued", "planning", "running"]),
                        (db.runs.c.worker_lease_expires_at.is_(None))
                        | (db.runs.c.worker_lease_expires_at <= now()),
                    )
                    .limit(100)
                )
            )
            .mappings()
            .all()
        )
    for row in rows:
        lease = row["worker_lease_expires_at"]
        generation = int(lease.timestamp()) if lease else 0
        await ctx["redis"].enqueue_job(
            "process_run",
            str(row["id"]),
            _job_id=f"reconcile:{row['id']}:{generation}:{row['attempt_count']}",
            _expires=86400,
        )
    return len(rows)


async def process_run(ctx: dict[str, Any], run_id_value: str) -> dict[str, str]:
    run_id = UUID(run_id_value)
    owner = uuid4()
    claimed = await _claim(ctx, run_id, owner)
    if not claimed:
        return {"status": "duplicate_or_leased"}
    heartbeat_task = asyncio.create_task(_lease_heartbeat_loop(ctx, run_id, owner))
    try:
        run, plan, project, principal = await _load_work(ctx, run_id)
        if run is None or run["status"] in TERMINAL:
            return {"status": "terminal_or_missing"}
        if run["cancel_requested_at"] is not None:
            await _finish(ctx, run_id, "cancelled", "INCONCLUSIVE", "CANCEL_REQUESTED")
            return {"status": "cancelled"}
        if plan["plan_json"] is None:
            await _generate_plan(ctx, run, plan, project, principal)
            return {"status": "awaiting_approval"}
        if plan["status"] != "approved":
            if run["status"] == "planning":
                await _transition(ctx, run_id, "planning", "awaiting_approval")
            await _release_lease(ctx, run_id, owner)
            return {"status": "awaiting_approval"}
        await _run_approved(ctx, run_id, owner)
        return {"status": "finished"}
    except ProviderError as exc:
        tries = int(ctx.get("job_try", 1))
        if exc.status in {429, 502, 503, 504} and tries < 3:
            await _release_lease(ctx, run_id, owner, delay_seconds=2**tries)
            raise
        await _finish(ctx, run_id, "failed", "ERROR", exc.code)
        return {"status": "failed", "code": exc.code}
    except Exception as exc:
        tries = int(ctx.get("job_try", 1))
        if (
            isinstance(exc, BrokerError)
            and exc.status_code in {429, 500, 502, 503, 504}
            and tries < 3
        ):
            await _release_lease(ctx, run_id, owner, delay_seconds=2**tries)
            raise
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) and tries < 3:
            await _release_lease(ctx, run_id, owner, delay_seconds=2**tries)
            raise
        # Errors are reduced to a stable class identifier; response text/URLs are not events.
        code = type(exc).__name__[:64]
        if isinstance(exc, BrokerError):
            code = f"BrokerError_{exc.status_code}"[:64]
        elif isinstance(exc, RuntimeError) and str(exc):
            code = str(exc)[:64]
        await _finish(ctx, run_id, "failed", "ERROR", code)
        return {"status": "failed", "code": code}
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _lease_heartbeat_loop(ctx: dict[str, Any], run_id: UUID, owner: UUID) -> None:
    interval = max(3, ctx["config"].worker_lease_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        async with ctx["sessions"]() as session, session.begin():
            result = await session.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id, db.runs.c.worker_lease_id == owner)
                .values(
                    worker_lease_expires_at=now()
                    + timedelta(seconds=ctx["config"].worker_lease_seconds),
                    heartbeat_at=now(),
                )
                .returning(db.runs.c.id)
            )
            if result.scalar_one_or_none() is None:
                return


async def _claim(ctx: dict[str, Any], run_id: UUID, owner: UUID) -> bool:
    config: Settings = ctx["config"]
    expiry = now() + timedelta(seconds=config.worker_lease_seconds)
    async with ctx["sessions"]() as session, session.begin():
        row = (
            (
                await session.execute(
                    select(db.runs).where(db.runs.c.id == run_id).with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["status"] in TERMINAL:
            return False
        if (
            row["worker_lease_expires_at"] is not None
            and row["worker_lease_expires_at"] > now()
            and row["worker_lease_id"] != owner
        ):
            return False
        if row["status"] == "queued":
            if not await transition(session, run_id, "queued", "planning"):
                return False
        await session.execute(
            update(db.runs)
            .where(db.runs.c.id == run_id, db.runs.c.status.not_in(list(TERMINAL)))
            .values(
                worker_lease_id=owner,
                worker_lease_expires_at=expiry,
                attempt_count=db.runs.c.attempt_count + 1,
                heartbeat_at=now(),
            )
        )
        return True


async def _load_work(
    ctx: dict[str, Any], run_id: UUID
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any], Principal]:
    async with ctx["sessions"]() as session:
        run = (
            (await session.execute(select(db.runs).where(db.runs.c.id == run_id)))
            .mappings()
            .first()
        )
        if run is None:
            return (
                None,
                {},
                {},
                Principal({"id": uuid4(), "email": "", "email_verified_at": None}, uuid4()),
            )
        plan = (
            (await session.execute(select(db.plans).where(db.plans.c.id == run["plan_id"])))
            .mappings()
            .one()
        )
        user = (
            (await session.execute(select(db.users).where(db.users.c.id == run["created_by"])))
            .mappings()
            .first()
        )
        project = (
            (
                await session.execute(
                    select(db.projects).where(db.projects.c.id == run["project_id"])
                )
            )
            .mappings()
            .one()
        )
        if user is None:
            raise RuntimeError("RUN_ACTOR_MISSING")
        principal = Principal(dict(user), uuid4())
        repository = Repository(session, principal, ctx["config"])
        await repository.project(run["project_id"], write=True)
        return dict(run), dict(plan), dict(project), principal


async def _generate_plan(
    ctx: dict[str, Any],
    run: dict[str, Any],
    plan: dict[str, Any],
    project: dict[str, Any],
    principal: Principal,
) -> None:
    run_id, plan_id = run["id"], plan["id"]
    config: Settings = ctx["config"]
    scope = TestScope.model_validate(plan["scope_json"])
    async with ctx["sessions"]() as session, session.begin():
        repository = Repository(session, principal, config)
        fresh_project = await repository.project(project["id"], write=True)
        policy, current = await authorization(repository, fresh_project, scope)
        if current != plan["authorization_json"]:
            raise ProviderError("PLAN_AUTHORIZATION_CHANGED")
    budget = Budget(
        max_calls=config.inference_max_calls,
        max_total_tokens=config.inference_total_tokens,
    )
    provider = VultrGateway(config, ctx["http"])
    rejection: str | None = None
    result = None
    try:
        result = await generate(
            GeneratePlanInput(goal=plan["goal"], scope=scope),
            policy,
            current["base_origin"],
            provider,
            budget,
        )
        async with ctx["sessions"]() as session, session.begin():
            repository = Repository(session, principal, config)
            fresh_project = await repository.project(project["id"], write=True)
            _fresh_policy, fresh = await authorization(repository, fresh_project, scope)
            if fresh != current:
                rejection = "PLAN_AUTHORIZATION_CHANGED"
    except ProviderError as exc:
        rejection = exc.code
    except Exception as exc:
        from agentproof_api.plan_risk import PlanRejected

        rejection = exc.code if isinstance(exc, PlanRejected) else type(exc).__name__[:64]

    async with ctx["sessions"]() as session, session.begin():
        repository = Repository(session, principal, config)
        current_run = (
            (await session.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update()))
            .mappings()
            .one()
        )
        if current_run["status"] in TERMINAL:
            return
        if current_run["cancel_requested_at"] is not None:
            await transition(
                session, run_id, current_run["status"], "cancelled", verdict="INCONCLUSIVE"
            )
            return
        for record in budget.records:
            await session.execute(
                insert(db.model_usage).values(
                    run_id=run_id,
                    plan_id=plan_id,
                    attempt=record.attempt,
                    model_requested=record.model_requested,
                    model_actual=record.model_actual,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    cost_estimate=record.cost_estimate,
                    error_code=record.error_code,
                )
            )
        if rejection or result is None:
            await session.execute(
                update(db.plans)
                .where(db.plans.c.id == plan_id, db.plans.c.status == "draft")
                .values(status="rejected", rejection_code=rejection or "PLAN_GENERATION_FAILED")
            )
            await session.execute(
                insert(db.plan_events).values(
                    plan_id=plan_id,
                    event_type="plan.rejected",
                    payload_json={"code": rejection or "PLAN_GENERATION_FAILED"},
                )
            )
            await session.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(error_code=rejection or "PLAN_GENERATION_FAILED")
            )
            await transition(session, run_id, "planning", "failed", verdict="ERROR")
            return

        plan_json = result.plan.model_dump(mode="json")
        await session.execute(
            update(db.plans)
            .where(db.plans.c.id == plan_id, db.plans.c.status == "draft")
            .values(plan_json=plan_json, risk_level=result.plan.risk_level.lower())
        )
        await session.execute(
            insert(db.plan_events).values(
                plan_id=plan_id,
                event_type="plan.generated",
                payload_json={"version": plan["version"], "model_calls": budget.calls},
            )
        )
        await append_run_event(
            session,
            run_id,
            "plan.generated",
            {"plan_id": str(plan_id), "version": plan["version"], "model_calls": budget.calls},
        )
        run_auth = dict(current_run)
        await capture_snapshot(repository, project, run_auth, config)
        await transition(session, run_id, current_run["status"], "awaiting_approval")


async def _transition(ctx: dict[str, Any], run_id: UUID, expected: str, target: str) -> bool:
    async with ctx["sessions"]() as session, session.begin():
        return await transition(session, run_id, expected, target)


async def _release_lease(
    ctx: dict[str, Any], run_id: UUID, owner: UUID, *, delay_seconds: int = 0
) -> None:
    async with ctx["sessions"]() as session, session.begin():
        await session.execute(
            update(db.runs)
            .where(db.runs.c.id == run_id, db.runs.c.worker_lease_id == owner)
            .values(
                worker_lease_id=None,
                worker_lease_expires_at=now() + timedelta(seconds=delay_seconds),
            )
        )


async def _run_approved(ctx: dict[str, Any], run_id: UUID, owner: UUID) -> None:
    config: Settings = ctx["config"]
    run, plan, project, principal = await _load_work(ctx, run_id)
    if run is None or run["status"] in TERMINAL:
        return
    if config.emergency_stop_enabled:
        await _finish(ctx, run_id, "cancelled", "INCONCLUSIVE", "EMERGENCY_STOP")
        return
    if run["cancel_requested_at"] is not None:
        await _finish(ctx, run_id, "cancelled", "INCONCLUSIVE", "CANCEL_REQUESTED")
        return
    if plan["plan_json"] is None or plan["status"] != "approved":
        if run["status"] == "planning":
            await _transition(ctx, run_id, "planning", "awaiting_approval")
        return
    async with ctx["sessions"]() as session:
        repository = Repository(session, principal, config)
        await validate_approved(repository, plan)
        snapshot = (
            (
                await session.execute(
                    select(db.run_authorizations).where(db.run_authorizations.c.run_id == run_id)
                )
            )
            .mappings()
            .one()
        )
        policy = verify_snapshot(config, snapshot["snapshot_json"], snapshot["signature"])
        plan_value = QAPlanV1.model_validate(plan["plan_json"])
    scope = TestScope.model_validate(plan["scope_json"])
    valid_until = datetime.fromisoformat(snapshot["snapshot_json"]["valid_until"])
    if valid_until <= now():
        await _finish(ctx, run_id, "failed", "ERROR", "AUTHORIZATION_EXPIRED")
        return

    limits_json = dict(run["limits_json"])
    duration = int(
        limits_json.get("duration_seconds")
        or limits_json.get("queue_seconds")
        or limits_json.get("max_duration_seconds")
        or 600
    )
    max_duration = min(duration, policy.max_duration_seconds)
    async with ctx["sessions"]() as session, session.begin():
        lease = (
            (
                await session.execute(
                    select(db.sandbox_leases)
                    .where(db.sandbox_leases.c.run_id == run_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if lease is None:
            broker_job_id, broker_nonce = uuid4(), secrets.token_urlsafe(48)
            await session.execute(
                insert(db.sandbox_leases).values(
                    run_id=run_id,
                    status="pending",
                    deadline_at=min(valid_until, now() + timedelta(seconds=max_duration)),
                    broker_job_id=broker_job_id,
                    broker_nonce=broker_nonce,
                    limits_json={
                        "max_actions": policy.max_actions,
                        "max_duration_seconds": max_duration,
                    },
                )
            )
            sandbox_id = None
            last_event_id = 0
        else:
            broker_job_id = lease["broker_job_id"] or uuid4()
            broker_nonce = lease["broker_nonce"] or secrets.token_urlsafe(48)
            sandbox_id = lease["broker_sandbox_id"]
            last_event_id = lease["last_broker_event_id"]
            if lease["broker_job_id"] is None or lease["broker_nonce"] is None:
                await session.execute(
                    update(db.sandbox_leases)
                    .where(db.sandbox_leases.c.id == lease["id"])
                    .values(broker_job_id=broker_job_id, broker_nonce=broker_nonce)
                )

    if sandbox_id is None:
        job = {
            "job_id": str(broker_job_id),
            "run_id": str(run_id),
            "project_id": str(run["project_id"]),
            "policy_hash": hashlib.sha256(_canonical(policy.model_dump(mode="json"))).hexdigest(),
            "approved_origins": policy.authorized_origins,
            "allowed_paths": scope.allowed_paths,
            "allowed_methods": policy.allowed_methods,
            "max_pages": policy.max_pages,
            "max_redirects": policy.max_redirects,
            "plan": plan_value.model_dump(mode="json"),
            "plan_digest": hashlib.sha256(
                _canonical(plan_value.model_dump(mode="json"))
            ).hexdigest(),
            "limits": {
                "max_duration_seconds": max_duration,
                "max_actions": min(policy.max_actions, limits_json["browser_actions"]),
                "memory_bytes": min(int(limits_json["memory_bytes"]), 1_073_741_824),
                "cpus": min(float(limits_json["cpu"]), 1.0),
                "pids": 128,
                # Broker hard-caps artifacts at 25 MiB; clamp before job signature.
                "artifact_bytes": min(int(limits_json["artifact_bytes"]), 26_214_400),
            },
            "authorization_expires_at": valid_until.isoformat(),
            "expires_at": (now() + timedelta(seconds=90)).isoformat(),
            "nonce": broker_nonce,
        }
        envelope = {
            "job": job,
            "signature": hmac.new(
                config.broker_hmac_key.get_secret_value().encode(), _canonical(job), hashlib.sha256
            ).hexdigest(),
        }
        _status_code, response_data = await _broker_request(
            ctx, "POST", "/internal/v1/sandboxes", envelope
        )
        sandbox_id = response_data["sandbox_id"]
        try:
            async with ctx["sessions"]() as session, session.begin():
                await admit_run(session, config, run_id, sandbox_id, broker_job_id, broker_nonce)
                await session.execute(
                    update(db.runs)
                    .where(db.runs.c.id == run_id)
                    .values(
                        worker_lease_id=owner,
                        worker_lease_expires_at=now()
                        + timedelta(seconds=config.worker_lease_seconds),
                    )
                )
        except Exception:
            try:
                await _broker_request(
                    ctx, "POST", f"/internal/v1/sandboxes/{sandbox_id}/cancel", {}
                )
            finally:
                raise

    await _poll_sandbox(
        ctx, run_id, owner, sandbox_id, last_event_id, plan_value, valid_until, max_duration
    )


async def _broker_request(
    ctx: dict[str, Any], method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    config: Settings = ctx["config"]
    key = config.broker_hmac_key.get_secret_value()
    if len(key) < 32:
        raise RuntimeError("BROKER_NOT_CONFIGURED")
    content = _canonical(body) if body is not None else b""
    timestamp = str(int(time.time()))
    signature_value = _broker_signature(key, timestamp, method.upper(), path, content)
    response = await ctx["http"].request(
        method,
        config.broker_url.rstrip("/") + path,
        content=content,
        headers={
            "Content-Type": "application/json",
            "X-Agentproof-Timestamp": timestamp,
            "X-Agentproof-Signature": signature_value,
        },
    )
    if response.status_code >= 400:
        raise BrokerError(response.status_code)
    try:
        data = response.json()
    except ValueError:
        data = {}
    return response.status_code, data if isinstance(data, dict) else {}


async def _poll_sandbox(
    ctx: dict[str, Any],
    run_id: UUID,
    owner: UUID,
    sandbox_id: str,
    last_event_id: int,
    plan: QAPlanV1,
    authorization_expiry: datetime,
    max_duration_seconds: int,
) -> None:
    deadline = min(authorization_expiry, now() + timedelta(seconds=max_duration_seconds))
    step_by_id = {step.id: (ordinal, step) for ordinal, step in enumerate(plan.steps)}
    runner_verdict: str | None = None
    while now() < deadline:
        run = await _get_run(ctx, run_id)
        if run is None or run["status"] in TERMINAL:
            return
        if run["cancel_requested_at"] is not None:
            await _broker_request(ctx, "POST", f"/internal/v1/sandboxes/{sandbox_id}/cancel", {})
        query_path = f"/internal/v1/sandboxes/{sandbox_id}/events?after_id={last_event_id}"
        _code, result = await _broker_request(ctx, "GET", query_path)
        for event in result.get("events", []):
            event_id = int(event.get("id", 0))
            if event_id <= last_event_id:
                continue
            last_event_id = event_id
            name, data = event.get("event"), event.get("data", {})
            await _consume_broker_event(ctx, run_id, sandbox_id, event_id, name, data, step_by_id)
            if name == "sandbox.completed" and data.get("verdict") in {
                "PASS",
                "FAIL",
                "ERROR",
                "INCONCLUSIVE",
            }:
                runner_verdict = data["verdict"]
        await _heartbeat(ctx, run_id, owner, sandbox_id, last_event_id)
        _status_code, status = await _broker_request(
            ctx, "GET", f"/internal/v1/sandboxes/{sandbox_id}/status"
        )
        sandbox_status = status.get("status")
        if sandbox_status in {"completed", "failed", "cancelled", "timed_out"}:
            try:
                await _import_artifacts(ctx, run_id, sandbox_id, plan)
            except Exception as exc:
                async with ctx["sessions"]() as session, session.begin():
                    summary = dict(
                        (
                            await session.execute(
                                select(db.runs.c.summary_json).where(db.runs.c.id == run_id)
                            )
                        ).scalar_one()
                        or {}
                    )
                    summary["evidence_import_error"] = type(exc).__name__[:64]
                    await session.execute(
                        update(db.runs).where(db.runs.c.id == run_id).values(summary_json=summary)
                    )
            fresh = await _get_run(ctx, run_id)
            cancelled = bool(fresh and fresh["cancel_requested_at"] is not None)
            if cancelled or sandbox_status == "cancelled":
                await _finish(ctx, run_id, "cancelled", "INCONCLUSIVE", "CANCEL_REQUESTED")
            elif sandbox_status == "timed_out":
                await _finish(ctx, run_id, "timed_out", "ERROR", "SANDBOX_TIMEOUT")
            elif sandbox_status == "completed" or (
                sandbox_status == "failed" and runner_verdict == "FAIL"
            ):
                verdict = runner_verdict or "INCONCLUSIVE"
                await _finish(ctx, run_id, "completed", verdict, None)
            else:
                await _finish(ctx, run_id, "failed", "ERROR", "SANDBOX_FAILED")
            return
        await asyncio.sleep(1)
    await _broker_request(ctx, "POST", f"/internal/v1/sandboxes/{sandbox_id}/cancel", {})
    try:
        await _import_artifacts(ctx, run_id, sandbox_id, plan)
    except Exception:
        pass
    await _finish(ctx, run_id, "timed_out", "ERROR", "RUN_DEADLINE_EXCEEDED")


async def _download_artifact_bundle(ctx: dict[str, Any], sandbox_id: str) -> bytes:
    config: Settings = ctx["config"]
    path = f"/internal/v1/sandboxes/{sandbox_id}/artifacts"
    timestamp = str(int(time.time()))
    signature_value = _broker_signature(
        config.broker_hmac_key.get_secret_value(), timestamp, "GET", path, b""
    )
    response = await ctx["http"].get(
        config.broker_url.rstrip("/") + path,
        headers={
            "X-Agentproof-Timestamp": timestamp,
            "X-Agentproof-Signature": signature_value,
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise BrokerError(response.status_code)
    if (
        len(response.content) > 110_000_000
        or response.headers.get("content-type", "").split(";")[0] != "application/zip"
    ):
        raise ValueError("ARTIFACT_BUNDLE_INVALID")
    return cast(bytes, response.content)


async def _import_artifacts(
    ctx: dict[str, Any], run_id: UUID, sandbox_id: str, plan: QAPlanV1
) -> None:
    bundle = await _download_artifact_bundle(ctx, sandbox_id)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        infos = archive.infolist()
        if len(infos) > 100 or sum(info.file_size for info in infos) > 104_857_600:
            raise ValueError("ARTIFACT_BUNDLE_LIMIT")
        files: dict[str, bytes] = {}
        for info in infos:
            if info.is_dir() or info.filename != info.filename.rsplit("/", 1)[-1]:
                raise ValueError("ARTIFACT_PATH_REJECTED")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(?:png|webp|json)", info.filename) is None:
                continue
            if info.file_size > 26_214_400:
                raise ValueError("ARTIFACT_TOO_LARGE")
            files[info.filename] = archive.read(info)
    trace_name = f"{run_id}.json"
    trace: dict[str, Any] | None = None
    if trace_name in files:
        parsed = json.loads(files[trace_name])
        if isinstance(parsed, dict) and parsed.get("run_id") == str(run_id):
            trace = parsed
    artifact_step_ids: dict[str, str] = {}
    if trace:
        for record in trace.get("steps", []):
            if isinstance(record, dict) and isinstance(record.get("artifact"), dict):
                artifact_id = record["artifact"].get("artifact_id")
                step_id = record.get("step_id")
                if isinstance(artifact_id, str) and isinstance(step_id, str):
                    artifact_step_ids[artifact_id] = step_id
    run_summary = await _get_run(ctx, run_id)
    if run_summary:
        for item in (run_summary["summary_json"] or {}).get("runner_artifacts", []):
            if (
                isinstance(item, dict)
                and isinstance(item.get("artifact_id"), str)
                and isinstance(item.get("step_id"), str)
            ):
                artifact_step_ids[item["artifact_id"]] = item["step_id"]
    step_ids = {step.id: ordinal for ordinal, step in enumerate(plan.steps)}
    trace_artifact: UUID | None = None
    artifacts_by_plan_step: dict[str, list[UUID]] = {}
    sessions = ctx["sessions"]
    for filename, content in files.items():
        plan_step_id = artifact_step_ids.get(Path(filename).stem)
        step_uuid: UUID | None = None
        if plan_step_id in step_ids:
            async with sessions() as session:
                step_uuid = (
                    await session.execute(
                        select(db.run_steps.c.id).where(
                            db.run_steps.c.run_id == run_id,
                            db.run_steps.c.ordinal == step_ids[plan_step_id],
                        )
                    )
                ).scalar_one_or_none()
        async with sessions() as session, session.begin():
            stored = await store_artifact(
                session, ctx["config"], run_id, filename, content, step_id=step_uuid
            )
            if stored is not None:
                artifact_id, created = stored
                if plan_step_id:
                    artifacts_by_plan_step.setdefault(plan_step_id, []).append(artifact_id)
                if filename == trace_name:
                    trace_artifact = artifact_id
                if created:
                    artifact = (
                        (
                            await session.execute(
                                select(db.artifacts).where(db.artifacts.c.id == artifact_id)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    await append_run_event(
                        session,
                        run_id,
                        "artifact.created",
                        {
                            "artifact_id": str(artifact_id),
                            "mime_type": artifact["mime_type"],
                            "bytes": artifact["bytes"],
                            "sha256": artifact["sha256"],
                        },
                    )
    if trace is not None:
        trace_summary = redact_tree(trace.get("observations", {}))
        async with sessions() as session, session.begin():
            summary = dict(
                (
                    await session.execute(
                        select(db.runs.c.summary_json).where(db.runs.c.id == run_id)
                    )
                ).scalar_one()
                or {}
            )
            summary["runner_observations"] = trace_summary
            await session.execute(
                update(db.runs).where(db.runs.c.id == run_id).values(summary_json=summary)
            )
        await _persist_observed_findings(
            ctx, run_id, plan, trace, trace_artifact, artifacts_by_plan_step
        )


async def _persist_observed_findings(
    ctx: dict[str, Any],
    run_id: UUID,
    plan: QAPlanV1,
    trace: dict[str, Any],
    trace_id: UUID | None,
    step_artifacts: dict[str, list[UUID]],
) -> None:
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    records = trace.get("steps", [])
    if not isinstance(records, list):
        return
    counts: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        step_id = record.get("step_id")
        step = next((value for value in plan.steps if value.id == step_id), None)
        assertion = record.get("assertion")
        if step is None:
            continue
        if isinstance(assertion, dict) and assertion.get("status") == "FAIL":
            observed = assertion.get("observed")
            expected = assertion.get("expected")
            path = str(record.get("observation", {}).get("url", ""))
            identity = {
                "path": path,
                "action": step.action,
                "assertion": assertion.get("kind"),
                "expected": expected,
            }
            fingerprint = hashlib.sha256(_canonical(redact_tree(identity))).hexdigest()
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
            candidates.append(
                {
                    "step": step,
                    "record": record,
                    "assertion": assertion,
                    "fingerprint": fingerprint,
                    "path": path,
                    "index": index,
                }
            )
        elif not assertion and record.get("error_code") in {"STEP_TIMEOUT", "TimeoutError"}:
            fingerprint = hashlib.sha256(
                _canonical({"step_id": step.id, "error": "timeout"})
            ).hexdigest()
            candidates.append(
                {
                    "step": step,
                    "record": record,
                    "assertion": None,
                    "fingerprint": fingerprint,
                    "path": "",
                    "index": index,
                }
            )
    for item in candidates:
        step = item["step"]
        assertion = item["assertion"]
        repeated = counts.get(item["fingerprint"], 0)
        # This phase does not perform a clean-sandbox replay. Repeated assertions
        # in one execution are useful occurrence counts, but are not confirmation.
        classification = "LIKELY" if assertion else "INCONCLUSIVE"
        severity = (
            "high"
            if assertion
            and step.target
            and any(
                word in step.target.name.casefold()
                for word in ("dashboard", "sign in", "login", "account")
            )
            else "medium"
            if assertion
            else "low"
        )
        title = (
            f"Observed {assertion.get('kind', 'assertion')} mismatch at {step.id}: "
            f"{step.target.name if step.target else step.action}"
            if assertion
            else f"Step {step.id} timed out; cause not isolated"
        )[:240]
        repro = []
        for prior in plan.steps[: item["index"] + 1]:
            if prior.action == "navigate":
                repro.append(f"Navigate to {prior.url_path}")
            elif prior.action == "fill":
                value = (
                    "the configured test credential"
                    if (prior.value_ref or "").startswith("credential:")
                    else "synthetic test data"
                )
                repro.append(f"Fill {prior.target.name if prior.target else 'field'} with {value}")
            else:
                target_name = prior.target.name if prior.target else ""
                repro.append(f"{prior.action.replace('_', ' ').capitalize()} {target_name}".strip())
        evidence = [str(value) for value in step_artifacts.get(step.id, [])]
        if trace_id:
            evidence.append(str(trace_id))
        expected = (
            {"kind": assertion.get("kind"), "expected": assertion.get("expected")}
            if assertion
            else {}
        )
        observed = {
            "path": item["path"],
            "step_status": item["record"].get("status"),
            "actual": assertion.get("observed") if assertion else None,
        }
        environment = {"browser": "Chromium", "viewport": "1280x720"}
        hypotheses = (
            []
            if assertion
            else [
                "Timeout cause is unconfirmed; it may involve a precondition, locator, "
                "network delay, or application latency."
            ]
        )
        async with ctx["sessions"]() as session, session.begin():
            statement = pg_insert(db.findings).values(
                run_id=run_id,
                fingerprint=item["fingerprint"],
                title=str(redact_tree(title))[:240],
                severity=severity,
                confidence=0.7 if classification == "LIKELY" else 0.2,
                expected_json=expected,
                observed_json=redact_tree(observed),
                repro_steps_json=redact_tree(repro),
                verdict="FAIL" if assertion else "INCONCLUSIVE",
                status="OPEN",
                classification=classification,
                environment_json=environment,
                hypotheses_json=hypotheses,
                evidence_artifact_ids=list(dict.fromkeys(evidence)),
                occurrence_count=max(1, repeated),
            )
            inserted = await session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=[db.findings.c.run_id, db.findings.c.fingerprint]
                ).returning(db.findings.c.id)
            )
            new_id = inserted.scalar_one_or_none()
            if new_id is None:
                await session.execute(
                    update(db.findings)
                    .where(
                        db.findings.c.run_id == run_id,
                        db.findings.c.fingerprint == item["fingerprint"],
                    )
                    .values(
                        occurrence_count=func.greatest(
                            db.findings.c.occurrence_count, max(1, repeated)
                        )
                    )
                )
            else:
                await append_run_event(session, run_id, "finding.created", {"count": 1})


async def _consume_broker_event(
    ctx: dict[str, Any],
    run_id: UUID,
    sandbox_id: str,
    event_id: int,
    name: str,
    data: dict[str, Any],
    step_by_id: dict[str, tuple[int, Any]],
) -> None:
    async with ctx["sessions"]() as session, session.begin():
        lease = (
            (
                await session.execute(
                    select(db.sandbox_leases)
                    .where(db.sandbox_leases.c.run_id == run_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if lease is None or event_id <= lease["last_broker_event_id"]:
            return
        step_id = data.get("step_id")
        if name in {"sandbox.step.started", "sandbox.step"} and step_id in step_by_id:
            ordinal, step = step_by_id[step_id]
            event_status = data.get(
                "status", "RUNNING" if name == "sandbox.step.started" else "PASS"
            )
            step_status = (
                "running"
                if name == "sandbox.step.started"
                else "failed"
                if event_status in {"FAIL", "ERROR"}
                else "completed"
            )
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            ended_at = None if step_status == "running" else now()

            await session.execute(
                pg_insert(db.run_steps)
                .values(
                    run_id=run_id,
                    ordinal=ordinal,
                    typed_action_json=step.model_dump(mode="json"),
                    expected_json=step.assertion.model_dump(mode="json") if step.assertion else {},
                    observed_json=redact_tree(
                        {
                            "status": event_status,
                            "broker_event_id": event_id,
                            "assertion": data.get("assertion"),
                            "url": data.get("page_url"),
                            "error_code": data.get("error_code"),
                        }
                    ),
                    status=step_status,
                    started_at=now(),
                    ended_at=ended_at,
                )
                .on_conflict_do_update(
                    index_elements=[db.run_steps.c.run_id, db.run_steps.c.ordinal],
                    set_={
                        "observed_json": redact_tree(
                            {
                                "status": event_status,
                                "broker_event_id": event_id,
                                "assertion": data.get("assertion"),
                                "url": data.get("page_url"),
                                "error_code": data.get("error_code"),
                            }
                        ),
                        "status": step_status,
                        "ended_at": ended_at,
                    },
                )
            )
            if name == "sandbox.step.started":
                await append_run_event(
                    session,
                    run_id,
                    "step.started",
                    {
                        "step_id": step_id,
                        "ordinal": ordinal,
                        "sequence": data.get("sequence", ordinal + 1),
                        "action": data.get("action"),
                        "status": "running",
                    },
                )
            else:
                await append_run_event(
                    session,
                    run_id,
                    "step.failed" if step_status == "failed" else "step.completed",
                    {
                        "step_id": step_id,
                        "ordinal": ordinal,
                        "sequence": data.get("sequence", ordinal + 1),
                        "action": data.get("action"),
                        "status": event_status,
                    },
                )
        elif name == "sandbox.artifact":
            # The durable API artifact ID is allocated only after bytes arrive and
            # pass MIME, size, redaction and hash checks in _import_artifacts.
            summary = dict(
                (
                    await session.execute(
                        select(db.runs.c.summary_json).where(db.runs.c.id == run_id)
                    )
                ).scalar_one()
                or {}
            )
            items = list(summary.get("runner_artifacts", []))
            artifact_id = data.get("artifact_id")
            if isinstance(artifact_id, str) and not any(
                x.get("artifact_id") == artifact_id for x in items
            ):
                items.append(
                    {
                        "artifact_id": artifact_id,
                        "step_id": step_id,
                        "mime_type": data.get("mime_type"),
                        "bytes": data.get("bytes"),
                        "sha256": data.get("sha256"),
                    }
                )
            summary["runner_artifacts"] = items[:100]
            await session.execute(
                update(db.runs).where(db.runs.c.id == run_id).values(summary_json=summary)
            )
        elif name == "sandbox.policy_denied":
            await append_run_event(
                session, run_id, "sandbox.policy_denied", {"reason": data.get("reason")}
            )
        elif name == "sandbox.completed":
            summary = dict(
                (
                    await session.execute(
                        select(db.runs.c.summary_json).where(db.runs.c.id == run_id)
                    )
                ).scalar_one()
                or {}
            )
            if data.get("verdict") in {"PASS", "FAIL", "ERROR", "INCONCLUSIVE"}:
                summary["runner_verdict"] = data["verdict"]
            await session.execute(
                update(db.runs).where(db.runs.c.id == run_id).values(summary_json=summary)
            )
        await session.execute(
            update(db.sandbox_leases)
            .where(db.sandbox_leases.c.id == lease["id"])
            .values(last_broker_event_id=event_id)
        )


async def _heartbeat(
    ctx: dict[str, Any], run_id: UUID, owner: UUID, sandbox_id: str, last_event_id: int
) -> None:
    async with ctx["sessions"]() as session, session.begin():
        await session.execute(
            update(db.runs)
            .where(db.runs.c.id == run_id, db.runs.c.worker_lease_id == owner)
            .values(
                worker_lease_expires_at=now()
                + timedelta(seconds=ctx["config"].worker_lease_seconds),
                heartbeat_at=now(),
            )
        )
        await session.execute(
            update(db.sandbox_leases)
            .where(db.sandbox_leases.c.run_id == run_id)
            .values(last_broker_event_id=last_event_id)
        )


async def _get_run(ctx: dict[str, Any], run_id: UUID) -> dict[str, Any] | None:
    async with ctx["sessions"]() as session:
        row = (
            (await session.execute(select(db.runs).where(db.runs.c.id == run_id)))
            .mappings()
            .first()
        )
        return dict(row) if row else None


async def _finish(
    ctx: dict[str, Any], run_id: UUID, target: str, verdict: str, error_code: str | None
) -> None:
    if target != "completed":
        async with ctx["sessions"]() as session:
            lease = (
                (
                    await session.execute(
                        select(db.sandbox_leases.c.broker_sandbox_id).where(
                            db.sandbox_leases.c.run_id == run_id,
                            db.sandbox_leases.c.status.in_(["pending", "active"]),
                        )
                    )
                )
                .scalars()
                .first()
            )
        if lease:
            try:
                await _broker_request(ctx, "POST", f"/internal/v1/sandboxes/{lease}/cancel", {})
            except Exception:
                pass
    async with ctx["sessions"]() as session, session.begin():
        run = (
            (await session.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update()))
            .mappings()
            .first()
        )
        if run is None or run["status"] in TERMINAL:
            return
        summary = dict(run["summary_json"] or {})
        summary["finished_at"] = now().isoformat()
        if error_code:
            summary["error_code"] = error_code
        # Update metadata while the row is still nonterminal. The DB trigger makes
        # every update to a terminal run immutable, including summary-only updates.
        await session.execute(
            update(db.runs)
            .where(db.runs.c.id == run_id)
            .values(summary_json=summary, error_code=error_code)
        )
        if target == "completed" and run["status"] == "running":
            if not await transition(session, run_id, "running", "investigating"):
                return
            current = "investigating"
        else:
            current = run["status"]
        if not await transition(
            session,
            run_id,
            current,
            target,
            verdict=verdict,
            observed_assertions=verdict in {"PASS", "FAIL"},
            error_code=error_code,
        ):
            return
        await session.execute(
            update(db.sandbox_leases)
            .where(db.sandbox_leases.c.run_id == run_id)
            .values(
                status="terminated", terminated_at=now(), termination_reason=error_code or target
            )
        )


async def process_cancel(ctx: dict[str, Any], run_id_value: str) -> dict[str, str]:
    run_id = UUID(run_id_value)
    run = await _get_run(ctx, run_id)
    if run is None or run["status"] in TERMINAL:
        return {"status": "terminal_or_missing"}
    async with ctx["sessions"]() as session:
        lease = (
            (
                await session.execute(
                    select(db.sandbox_leases).where(db.sandbox_leases.c.run_id == run_id)
                )
            )
            .mappings()
            .first()
        )
    if lease is None or lease["broker_sandbox_id"] is None:
        if run["worker_lease_expires_at"] is not None and run["worker_lease_expires_at"] > now():
            return {"status": "cancellation_pending"}
        await _finish(ctx, run_id, "cancelled", "INCONCLUSIVE", "CANCEL_REQUESTED")
        return {"status": "cancelled"}
    sandbox_id = lease["broker_sandbox_id"]
    await _broker_request(ctx, "POST", f"/internal/v1/sandboxes/{sandbox_id}/cancel", {})
    await _finish(ctx, run_id, "cancelled", "INCONCLUSIVE", "CANCEL_REQUESTED")
    return {"status": "cancelled"}


class WorkerSettings:
    functions = [
        func(process_run, timeout=750, max_tries=3, keep_result=3600),
        func(process_cancel, timeout=90, max_tries=3, keep_result=3600),
    ]
    cron_jobs = [
        cron(
            publish_outbox,
            second={
                0,
                2,
                4,
                6,
                8,
                10,
                12,
                14,
                16,
                18,
                20,
                22,
                24,
                26,
                28,
                30,
                32,
                34,
                36,
                38,
                40,
                42,
                44,
                46,
                48,
                50,
                52,
                54,
                56,
                58,
            },
            run_at_startup=True,
        ),
        cron(reconcile, second={0, 10, 20, 30, 40, 50}, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(Settings().redis_url.get_secret_value())
    max_jobs = 8
    job_timeout = 780


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentProof durable arq worker")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration; process no jobs"
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps({"component": "worker", "queue": "arq", "execution_enabled": False}))
        return 0
    from arq.worker import run_worker

    run_worker(cast(Any, WorkerSettings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
