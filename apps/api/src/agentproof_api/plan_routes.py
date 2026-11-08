"""Persisted model-generated drafts and explicit scope/version-bound approvals."""

import asyncio
import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, insert, select, update

from agentproof_api import db
from agentproof_api.api_models import PlanView
from agentproof_api.auth import DB, User, rate_limit, settings
from agentproof_api.model_gateway import Budget, ProviderError
from agentproof_api.plan_risk import PlanRejected, assess
from agentproof_api.plan_schema import ApprovePlanInput, GeneratePlanInput, QAPlanV1, TestScope
from agentproof_api.planner import Gateway, generate
from agentproof_api.policy import TargetPolicy
from agentproof_api.repository import Repository, transition
from agentproof_api.security import digest, now, signing_key
from agentproof_api.target_routes import validate_policy
from agentproof_api.targets import canonical_url

router = APIRouter(prefix="/api/v1")


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def signature(repository: Repository, state: dict[str, Any]) -> str:
    return hmac.new(
        signing_key(repository.config),
        b"plan-scope-v1:" + canonical(state).encode(),
        hashlib.sha256,
    ).hexdigest()


async def authorization(
    repository: Repository, project: dict[str, Any], scope: TestScope
) -> tuple[TargetPolicy, dict[str, Any]]:
    row = (
        (
            await repository.session.execute(
                select(db.project_policies).where(db.project_policies.c.project_id == project["id"])
            )
        )
        .mappings()
        .first()
    )
    if row is None or project["status"] != "active":
        raise HTTPException(409, "TARGET_POLICY_REQUIRED")
    policy = TargetPolicy.model_validate(row["policy_json"])
    grants = await validate_policy(repository, project["id"], policy)
    base = canonical_url(project["base_url"], allow_ip=True)[0]
    if base not in policy.authorized_origins:
        raise HTTPException(409, "BASE_ORIGIN_NOT_AUTHORIZED")
    if scope.credential_ref_id and scope.credential_ref_id not in policy.credential_ref_ids:
        raise HTTPException(403, "CREDENTIAL_SCOPE_DENIED")
    selected = {
        g["normalized_origin"]: g["expires_at"].isoformat()
        for g in grants
        if g["normalized_origin"] in policy.authorized_origins
    }
    state = {
        "schema_version": "1.0",
        "project_id": str(project["id"]),
        "base_origin": base,
        "policy_version": row["version"],
        "policy": policy.model_dump(mode="json"),
        "scope": scope.model_dump(mode="json"),
        "grant_expiries": selected,
    }
    return policy, state


async def validate_approved(repository: Repository, plan: dict[str, Any]) -> None:
    project = await repository.project(plan["project_id"], write=True)
    scope = TestScope.model_validate(plan["scope_json"])
    policy, current = await authorization(repository, project, scope)
    saved = plan["authorization_json"]
    if (
        not saved
        or not plan["authorization_signature"]
        or not hmac.compare_digest(signature(repository, saved), plan["authorization_signature"])
        or digest(canonical(current)) != plan["authorization_digest"]
        or current != saved
    ):
        raise HTTPException(409, "PLAN_AUTHORIZATION_CHANGED")
    try:
        parsed = QAPlanV1.model_validate(plan["plan_json"])
        result = await asyncio.to_thread(
            assess, parsed, plan["goal"], scope, policy, current["base_origin"], repository.config
        )
    except (PlanRejected, ValueError):
        raise HTTPException(409, "PLAN_NO_LONGER_ALLOWED") from None
    if result.plan != parsed or plan["risk_level"] != parsed.risk_level.lower():
        raise HTTPException(409, "PLAN_REVIEW_CHANGED")


def view(row: dict[str, Any]) -> PlanView:
    return PlanView.model_validate({k: row[k] for k in PlanView.model_fields})


@router.post("/projects/{project_id}/plans", response_model=PlanView, status_code=201)
async def create_plan(
    project_id: UUID,
    body: GeneratePlanInput,
    request: Request,
    session: DB,
    user: User,
    provider: Gateway,
) -> PlanView:
    repository = Repository(session, user, settings(request))
    project = await repository.project(project_id, write=True)
    user.verified()
    await rate_limit(request, "plan-generation", str(project["workspace_id"]), 10, 3600)
    policy, state = await authorization(repository, project, body.scope)
    version = (
        int(
            (
                await session.execute(
                    select(func.coalesce(func.max(db.plans.c.version), 0)).where(
                        db.plans.c.project_id == project_id
                    )
                )
            ).scalar_one()
        )
        + 1
    )
    row = dict(
        (
            await session.execute(
                insert(db.plans)
                .values(
                    project_id=project_id,
                    created_by=user.id,
                    version=version,
                    goal=body.goal,
                    status="draft",
                    scope_json=body.scope.model_dump(mode="json"),
                    authorization_json=state,
                    authorization_digest=digest(canonical(state)),
                    authorization_signature=signature(repository, state),
                    risk_level="high",
                )
                .returning(db.plans)
            )
        )
        .mappings()
        .one()
    )
    await repository.audit(project["workspace_id"], "plan.created", "plan", row["id"])
    # Durable draft before outbound I/O; a crash never produces an approved partial plan.
    await session.commit()
    budget = Budget(
        max_calls=repository.config.inference_max_calls,
        max_total_tokens=repository.config.inference_total_tokens,
    )
    rejection: str | None = None
    status = 422
    result = None
    try:
        result = await generate(body, policy, state["base_origin"], provider, budget)
        project = await repository.project(project_id, write=True)
        _, fresh = await authorization(repository, project, body.scope)
        if fresh != state:
            raise PlanRejected("PLAN_AUTHORIZATION_CHANGED")
    except ProviderError as error:
        rejection, status = error.code, error.status
    except PlanRejected as error:
        rejection = error.code
    except HTTPException:
        rejection, status = "PLAN_AUTHORIZATION_CHANGED", 409
    for record in budget.records:
        await session.execute(
            insert(db.model_usage).values(
                plan_id=row["id"],
                attempt=record.attempt,
                model_requested=record.model_requested,
                model_actual=record.model_actual,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                cost_estimate=record.cost_estimate,
                error_code=record.error_code,
            )
        )
    event = "plan.rejected" if rejection else "plan.generated"
    changes: dict[str, Any] = (
        {"status": "rejected", "rejection_code": rejection}
        if rejection
        else {
            "plan_json": result.plan.model_dump(mode="json") if result else None,
            "risk_level": result.plan.risk_level.lower() if result else "high",
        }
    )
    row = dict(
        (
            await session.execute(
                update(db.plans)
                .where(db.plans.c.id == row["id"])
                .values(**changes)
                .returning(db.plans)
            )
        )
        .mappings()
        .one()
    )
    await session.execute(
        insert(db.plan_events).values(
            plan_id=row["id"],
            event_type=event,
            payload_json={"code": rejection, "version": version, "model_calls": budget.calls},
        )
    )
    await repository.audit(
        project["workspace_id"],
        event,
        "plan",
        row["id"],
        {"code": rejection, "model_calls": budget.calls},
    )
    if rejection:
        # Retain rejected draft, attempt accounting and redacted event despite HTTP failure.
        await session.commit()
        raise HTTPException(status, rejection)
    return view(row)


@router.post("/plans/{plan_id}/approve", response_model=PlanView)
async def approve_plan(
    plan_id: UUID, body: ApprovePlanInput, request: Request, session: DB, user: User
) -> PlanView:
    repository = Repository(session, user, settings(request))
    row = await repository.plan(plan_id, write=True)
    user.verified()
    if body.version != row["version"] or body.authorization_digest != row["authorization_digest"]:
        raise HTTPException(409, "STALE_PLAN_VERSION_OR_SCOPE")
    if row["status"] not in {"draft", "approved"} or row["plan_json"] is None:
        raise HTTPException(409, "PLAN_NOT_REVIEWABLE")
    await validate_approved(repository, row)
    plan = QAPlanV1.model_validate(row["plan_json"])
    required = {s.id for s in plan.steps if s.requires_approval}
    supplied = set(body.approved_step_ids)
    if not required <= supplied or not supplied <= {s.id for s in plan.steps}:
        raise HTTPException(409, "EXPLICIT_STEP_APPROVAL_REQUIRED")
    if row["risk_level"] == "high" and not body.approve_high_risk:
        raise HTTPException(409, "HIGH_RISK_APPROVAL_REQUIRED")
    if row["status"] == "approved":
        return view(row)
    row = dict(
        (
            await session.execute(
                update(db.plans)
                .where(db.plans.c.id == plan_id, db.plans.c.status == "draft")
                .values(status="approved", approved_by=user.id, approved_at=now())
                .returning(db.plans)
            )
        )
        .mappings()
        .one()
    )
    project = await repository.project(row["project_id"])
    await repository.audit(
        project["workspace_id"],
        "plan.approved",
        "plan",
        plan_id,
        {
            "version": row["version"],
            "approved_step_ids": sorted(supplied),
            "approve_high_risk": body.approve_high_risk,
        },
    )
    runs: list[UUID] = list(
        (
            await session.execute(
                select(db.runs.c.id).where(
                    db.runs.c.plan_id == plan_id,
                    db.runs.c.status == "awaiting_approval",
                )
            )
        )
        .scalars()
        .all()
    )
    for run_id in runs:
        await transition(session, run_id, "awaiting_approval", "queued")
    return view(row)


@router.get("/plans/{plan_id}/events")
async def events(plan_id: UUID, request: Request, session: DB, user: User) -> dict[str, Any]:
    repository = Repository(session, user, settings(request))
    await repository.plan(plan_id)
    rows = (
        (
            await session.execute(
                select(db.plan_events)
                .where(db.plan_events.c.plan_id == plan_id)
                .order_by(db.plan_events.c.id)
                .limit(100)
            )
        )
        .mappings()
        .all()
    )
    return {
        "data": [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "payload": r["payload_json"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }
