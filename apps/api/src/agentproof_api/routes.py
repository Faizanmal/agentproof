import asyncio
import hashlib
import io
import json
import re
import time
import zipfile
from typing import Annotated, Any, Literal, TypeVar, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from starlette.background import BackgroundTask

from agentproof_api import db
from agentproof_api.api_models import (
    ArtifactView,
    AuditView,
    FindingView,
    MemberInput,
    MemberView,
    Page,
    PlanView,
    ProjectInput,
    ProjectPatch,
    ProjectView,
    RunInput,
    RunReport,
    RunView,
    SecretInput,
    SecretView,
    StepView,
    WorkspaceInput,
    WorkspaceView,
)
from agentproof_api.auth import (
    DB,
    Principal,
    User,
    identity_from_cookie,
    rate_limit,
    settings,
)
from agentproof_api.credential_vault import seal
from agentproof_api.evidence import redact_tree
from agentproof_api.repository import ACTIVE, LIMITS, TERMINAL, Repository, transition
from agentproof_api.run_events import append_run_event
from agentproof_api.security import digest, now, private_path

router = APIRouter(prefix="/api/v1")
Limit = Annotated[int, Query(ge=1, le=100)]
M = TypeVar("M", bound=BaseModel)


def view[M: BaseModel](model: type[M], row: dict[str, Any]) -> M:
    return model.model_validate({key: row[key] for key in model.model_fields})


def repo(request: Request, session: DB, user: User) -> Repository:
    return Repository(session, user, settings(request))


def run_view(row: dict[str, Any]) -> RunView:
    result = RunView.model_validate(
        {key: row[key] for key in RunView.model_fields if key not in {"events_url", "run_id"}}
    )
    result.run_id = row["id"]
    result.events_url = f"/api/v1/runs/{row['id']}/events"
    return result


@router.get("/workspaces", response_model=Page[WorkspaceView])
async def list_workspaces(
    request: Request, session: DB, user: User, limit: Limit = 20, cursor: str | None = None
) -> Page[WorkspaceView]:
    repository = repo(request, session, user)
    query = (
        select(db.workspaces)
        .join(db.memberships, db.memberships.c.workspace_id == db.workspaces.c.id)
        .where(db.memberships.c.user_id == user.id)
    )
    rows, next_cursor = await repository.page(db.workspaces, query, "workspaces", limit, cursor)
    return Page(data=[view(WorkspaceView, r) for r in rows], next_cursor=next_cursor)


@router.post("/workspaces", response_model=WorkspaceView, status_code=201)
async def create_workspace(
    body: WorkspaceInput, request: Request, session: DB, user: User
) -> WorkspaceView:
    row = dict(
        (
            await session.execute(
                insert(db.workspaces)
                .values(name=body.name, owner_user_id=user.id)
                .returning(db.workspaces)
            )
        )
        .mappings()
        .one()
    )
    await session.execute(
        insert(db.memberships).values(workspace_id=row["id"], user_id=user.id, role="owner")
    )
    await repo(request, session, user).audit(row["id"], "workspace.created", "workspace", row["id"])
    return view(WorkspaceView, row)


@router.get("/workspaces/{workspace_id}/members", response_model=Page[MemberView])
async def list_members(
    workspace_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[MemberView]:
    repository = repo(request, session, user)
    await repository.workspace(workspace_id)
    rows, next_cursor = await repository.page(
        db.memberships,
        select(db.memberships).where(
            db.memberships.c.workspace_id == workspace_id,
            db.memberships.c.workspace_id.in_(
                select(db.memberships.c.workspace_id).where(db.memberships.c.user_id == user.id)
            ),
        ),
        f"members:{workspace_id}",
        limit,
        cursor,
    )
    return Page(data=[view(MemberView, r) for r in rows], next_cursor=next_cursor)


@router.post("/workspaces/{workspace_id}/members", response_model=MemberView, status_code=201)
async def set_member(
    workspace_id: UUID, body: MemberInput, request: Request, session: DB, user: User
) -> MemberView:
    repository = repo(request, session, user)
    role = await repository.workspace(workspace_id, admin=True)
    # Serialize membership management, including updates from two admins.
    await session.execute(
        select(db.workspaces.c.id).where(db.workspaces.c.id == workspace_id).with_for_update()
    )
    target = (
        await session.execute(
            select(db.users.c.id).where(
                db.users.c.email == body.email.strip().casefold(), db.users.c.disabled_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "NOT_FOUND")
    existing = (
        await session.execute(
            select(db.memberships.c.role).where(
                db.memberships.c.workspace_id == workspace_id, db.memberships.c.user_id == target
            )
        )
    ).scalar_one_or_none()
    if existing == "owner" or (role == "admin" and (body.role == "admin" or existing == "admin")):
        raise HTTPException(403, "ROLE_FORBIDDEN")
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    statement = pg_insert(db.memberships).values(
        workspace_id=workspace_id, user_id=target, role=body.role
    )
    row = dict(
        (
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[db.memberships.c.workspace_id, db.memberships.c.user_id],
                    set_={"role": body.role},
                ).returning(db.memberships)
            )
        )
        .mappings()
        .one()
    )
    await repository.audit(
        workspace_id, "membership.updated", "membership", row["id"], {"role": body.role}
    )
    return view(MemberView, row)


@router.get("/projects", response_model=Page[ProjectView])
async def list_projects(
    workspace_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[ProjectView]:
    repository = repo(request, session, user)
    await repository.workspace(workspace_id)
    rows, next_cursor = await repository.page(
        db.projects,
        select(db.projects).where(
            db.projects.c.workspace_id == workspace_id,
            db.projects.c.id.in_(repository.visible_projects()),
        ),
        f"projects:{workspace_id}",
        limit,
        cursor,
    )
    return Page(data=[view(ProjectView, r) for r in rows], next_cursor=next_cursor)


@router.post("/projects", response_model=ProjectView, status_code=201)
async def create_project(
    body: ProjectInput, request: Request, session: DB, user: User
) -> ProjectView:
    repository = repo(request, session, user)
    await repository.workspace(body.workspace_id, write=True)
    row = dict(
        (
            await session.execute(
                insert(db.projects)
                .values(**body.model_dump(), created_by=user.id)
                .returning(db.projects)
            )
        )
        .mappings()
        .one()
    )
    await repository.audit(body.workspace_id, "project.created", "project", row["id"])
    return view(ProjectView, row)


@router.get("/projects/{project_id}", response_model=ProjectView)
async def get_project(project_id: UUID, request: Request, session: DB, user: User) -> ProjectView:
    return view(ProjectView, await repo(request, session, user).project(project_id))


@router.patch("/projects/{project_id}", response_model=ProjectView)
async def patch_project(
    project_id: UUID, body: ProjectPatch, request: Request, session: DB, user: User
) -> ProjectView:
    repository = repo(request, session, user)
    project = await repository.project(project_id, write=True)
    values = body.model_dump(exclude_unset=True)
    if not values or any(v is None for v in values.values()):
        raise HTTPException(422, "INVALID_PROJECT_UPDATE")
    row = dict(
        (
            await session.execute(
                update(db.projects)
                .where(
                    db.projects.c.id == project_id,
                    db.projects.c.id.in_(repository.visible_projects()),
                )
                .values(**values)
                .returning(db.projects)
            )
        )
        .mappings()
        .one()
    )
    await repository.audit(project["workspace_id"], "project.updated", "project", project_id)
    return view(ProjectView, row)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: UUID, request: Request, session: DB, user: User) -> None:
    repository = repo(request, session, user)
    project = await repository.project(project_id, admin=True)
    active = (
        await session.execute(
            select(db.runs.c.id)
            .where(db.runs.c.project_id == project_id, db.runs.c.status.in_(ACTIVE))
            .limit(1)
        )
    ).first()
    lease = (
        await session.execute(
            select(db.sandbox_leases.c.id)
            .where(
                db.sandbox_leases.c.run_id.in_(
                    select(db.runs.c.id).where(db.runs.c.project_id == project_id)
                ),
                db.sandbox_leases.c.status != "terminated",
            )
            .limit(1)
        )
    ).first()
    if active or lease:
        raise HTTPException(409, "PROJECT_HAS_ACTIVE_RUNS")
    await repository.audit(project["workspace_id"], "project.deleted", "project", project_id)
    # The artifact DELETE trigger writes cleanup jobs even for cascading deletions.
    await session.execute(
        delete(db.projects).where(
            db.projects.c.id == project_id, db.projects.c.id.in_(repository.visible_projects())
        )
    )


@router.get("/projects/{project_id}/plans", response_model=Page[PlanView])
async def list_plans(
    project_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[PlanView]:
    repository = repo(request, session, user)
    await repository.project(project_id)
    rows, next_cursor = await repository.page(
        db.plans,
        select(db.plans).where(
            db.plans.c.project_id == project_id,
            db.plans.c.project_id.in_(repository.visible_projects()),
        ),
        f"plans:{project_id}",
        limit,
        cursor,
    )
    return Page(data=[view(PlanView, r) for r in rows], next_cursor=next_cursor)


@router.get("/plans/{plan_id}", response_model=PlanView)
async def get_plan(plan_id: UUID, request: Request, session: DB, user: User) -> PlanView:
    return view(PlanView, await repo(request, session, user).plan(plan_id))


@router.post("/projects/{project_id}/runs", response_model=RunView, status_code=202)
async def create_run(
    project_id: UUID, body: RunInput, request: Request, response: Response, session: DB, user: User
) -> RunView:
    if settings(request).emergency_stop_enabled:
        raise HTTPException(503, "RUN_ADMISSION_EMERGENCY_STOP")
    repository = repo(request, session, user)
    content_length = request.headers.get("content-length")
    if content_length is None or not content_length.isdecimal():
        raise HTTPException(411, "CONTENT_LENGTH_REQUIRED")
    if int(content_length) > 65536:
        raise HTTPException(413, "REQUEST_TOO_LARGE")
    project = await repository.project(project_id, write=True)
    if project["status"] != "active":
        raise HTTPException(409, "PROJECT_ARCHIVED")
    user.verified()
    await rate_limit(request, "run-create", str(project["workspace_id"]), 20, 3600)
    existing = (
        (
            await session.execute(
                select(db.runs).where(
                    db.runs.c.project_id == project_id,
                    db.runs.c.idempotency_key == body.idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    if existing:
        expected = digest(
            json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
        saved = (existing["summary_json"] or {}).get("request_digest")
        if saved is not None and saved != expected:
            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
        response.status_code = 200
        return run_view(dict(existing))

    # Serialize workspace admission and impose a global queued-work ceiling.
    await session.execute(text("SELECT pg_advisory_xact_lock(941207501)"))
    concurrent_existing = (
        (
            await session.execute(
                select(db.runs).where(
                    db.runs.c.project_id == project_id,
                    db.runs.c.idempotency_key == body.idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    if concurrent_existing:
        expected = digest(
            json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
        if (concurrent_existing["summary_json"] or {}).get("request_digest") != expected:
            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
        response.status_code = 200
        return run_view(dict(concurrent_existing))
    queue_depth = int(
        (
            await session.execute(
                select(func.count())
                .select_from(db.runs)
                .where(db.runs.c.status.in_(["queued", "planning"]))
            )
        ).scalar_one()
    )
    if queue_depth >= settings(request).max_queued_runs:
        raise HTTPException(429, "RUN_QUEUE_FULL")

    from agentproof_api.plan_routes import authorization, canonical, signature, validate_approved
    from agentproof_api.security import digest as hash_digest

    if body.plan_id is not None:
        plan = await repository.plan(body.plan_id)
        if plan["project_id"] != project_id or plan["status"] not in {"draft", "approved"}:
            raise HTTPException(409, "PLAN_NOT_RUNNABLE")
        if plan["plan_json"] is None:
            raise HTTPException(409, "PLAN_NOT_READY")
        if plan["status"] == "approved":
            await validate_approved(repository, plan)
        plan_id = plan["id"]
        plan_status = plan["status"]
        plan_ready = True
    else:
        if body.goal is None:
            raise HTTPException(422, "GOAL_OR_PLAN_REQUIRED")
        _policy, authorization_state = await authorization(repository, project, body.scope)
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
        draft = dict(
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
                        authorization_json=authorization_state,
                        authorization_digest=hash_digest(canonical(authorization_state)),
                        authorization_signature=signature(repository, authorization_state),
                        risk_level="high",
                    )
                    .returning(db.plans)
                )
            )
            .mappings()
            .one()
        )
        plan_id = draft["id"]
        plan_status = "draft"
        plan_ready = False
        await session.execute(
            insert(db.plan_events).values(
                plan_id=plan_id,
                event_type="plan.queued",
                payload_json={"version": version, "status": "draft"},
            )
        )

    policy_row = (
        (
            await session.execute(
                select(db.project_policies.c.policy_json).where(
                    db.project_policies.c.project_id == project_id
                )
            )
        )
        .mappings()
        .first()
    )
    if policy_row is None:
        raise HTTPException(409, "TARGET_POLICY_REQUIRED")
    from agentproof_api.policy import TargetPolicy

    concurrency_limit = settings(request).max_workspace_concurrent_runs
    concurrency_limit = min(
        concurrency_limit,
        TargetPolicy.model_validate(policy_row["policy_json"]).max_concurrent_runs,
    )
    active_workspace_runs = int(
        (
            await session.execute(
                select(func.count())
                .select_from(db.runs.join(db.projects, db.runs.c.project_id == db.projects.c.id))
                .where(
                    db.projects.c.workspace_id == project["workspace_id"],
                    db.runs.c.status.in_(
                        ["queued", "planning", "awaiting_approval", "running", "investigating"]
                    ),
                )
            )
        ).scalar_one()
    )
    if active_workspace_runs >= concurrency_limit:
        raise HTTPException(429, "WORKSPACE_RUN_LIMIT")

    limits = dict(LIMITS)
    limits["duration_seconds"] = body.max_duration_seconds
    queue_for_execution = plan_status == "approved" or not plan_ready
    run_status = "queued" if queue_for_execution else "awaiting_approval"
    body_digest = digest(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    row = dict(
        (
            await session.execute(
                insert(db.runs)
                .values(
                    project_id=project_id,
                    plan_id=plan_id,
                    created_by=user.id,
                    idempotency_key=body.idempotency_key,
                    status=run_status,
                    limits_json=limits,
                    summary_json={"request_digest": body_digest},
                )
                .returning(db.runs)
            )
        )
        .mappings()
        .one()
    )
    if plan_ready:
        from agentproof_api.target_routes import capture_snapshot

        await capture_snapshot(repository, project, row, settings(request))
    await append_run_event(
        session,
        row["id"],
        "run.queued" if queue_for_execution else "plan.approval_required",
        {"status": run_status, "plan_id": str(plan_id)},
    )
    await repository.audit(
        project["workspace_id"],
        "run.queued" if queue_for_execution else "run.approval_required",
        "run",
        row["id"],
    )
    return run_view(row)


@router.get("/projects/{project_id}/runs", response_model=Page[RunView])
async def list_runs(
    project_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[RunView]:
    repository = repo(request, session, user)
    await repository.project(project_id)
    rows, next_cursor = await repository.page(
        db.runs,
        select(db.runs).where(
            db.runs.c.project_id == project_id,
            db.runs.c.project_id.in_(repository.visible_projects()),
        ),
        f"runs:{project_id}",
        limit,
        cursor,
    )
    return Page(data=[run_view(r) for r in rows], next_cursor=next_cursor)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: UUID, request: Request, session: DB, user: User) -> RunView:
    return run_view(await repo(request, session, user).run(run_id))


@router.post("/runs/{run_id}/cancel", response_model=RunView, status_code=202)
async def cancel_run(run_id: UUID, request: Request, session: DB, user: User) -> RunView:
    repository = repo(request, session, user)
    run = await repository.run(run_id, write=True)
    if run["status"] in TERMINAL:
        return run_view(run)
    if run["status"] in {"queued", "awaiting_approval"}:
        if not await transition(
            session, run_id, run["status"], "cancelled", verdict="INCONCLUSIVE"
        ):
            raise HTTPException(409, "STATE_CONFLICT")
    else:
        if run["cancel_requested_at"] is None:
            await session.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id, db.runs.c.status == run["status"])
                .values(cancel_requested_at=now())
            )
            await append_run_event(
                session, run_id, "run.cancel.requested", {"status": run["status"]}
            )
    project = await repository.project(run["project_id"])
    await repository.audit(project["workspace_id"], "run.cancelled", "run", run_id)
    return run_view(await repository.run(run_id))


@router.get("/runs/{run_id}/steps", response_model=Page[StepView])
async def list_steps(
    run_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[StepView]:
    repository = repo(request, session, user)
    await repository.run(run_id)
    rows, next_cursor = await repository.page(
        db.run_steps,
        select(db.run_steps).where(
            db.run_steps.c.run_id == run_id, db.run_steps.c.run_id.in_(repository.visible_runs())
        ),
        f"steps:{run_id}",
        limit,
        cursor,
    )
    return Page(data=[view(StepView, r) for r in rows], next_cursor=next_cursor)


@router.get("/runs/{run_id}/artifacts", response_model=Page[ArtifactView])
async def list_artifacts(
    run_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[ArtifactView]:
    repository = repo(request, session, user)
    await repository.run(run_id)
    rows, next_cursor = await repository.page(
        db.artifacts,
        select(db.artifacts).where(
            db.artifacts.c.run_id == run_id, db.artifacts.c.run_id.in_(repository.visible_runs())
        ),
        f"artifacts:{run_id}",
        limit,
        cursor,
    )
    return Page(data=[view(ArtifactView, r) for r in rows], next_cursor=next_cursor)


@router.get("/runs/{run_id}/findings", response_model=Page[FindingView])
async def list_findings(
    run_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[FindingView]:
    repository = repo(request, session, user)
    await repository.run(run_id)
    rows, next_cursor = await repository.page(
        db.findings,
        select(db.findings).where(
            db.findings.c.run_id == run_id, db.findings.c.run_id.in_(repository.visible_runs())
        ),
        f"findings:{run_id}",
        limit,
        cursor,
    )
    return Page(data=[view(FindingView, r) for r in rows], next_cursor=next_cursor)


async def _run_report(repository: Repository, run_id: UUID) -> dict[str, Any]:
    run = await repository.run(run_id)
    plan = (
        (await repository.session.execute(select(db.plans).where(db.plans.c.id == run["plan_id"])))
        .mappings()
        .one()
    )
    steps = (
        (
            await repository.session.execute(
                select(db.run_steps)
                .where(db.run_steps.c.run_id == run_id)
                .order_by(db.run_steps.c.ordinal)
            )
        )
        .mappings()
        .all()
    )
    findings = (
        (
            await repository.session.execute(
                select(db.findings)
                .where(db.findings.c.run_id == run_id)
                .order_by(db.findings.c.created_at)
            )
        )
        .mappings()
        .all()
    )
    artifacts = (
        (
            await repository.session.execute(
                select(db.artifacts)
                .where(db.artifacts.c.run_id == run_id, db.artifacts.c.expires_at > now())
                .order_by(db.artifacts.c.created_at)
            )
        )
        .mappings()
        .all()
    )
    trace_observations: dict[str, Any] = {}
    trace_steps: dict[str, Any] = {}
    for item in artifacts:
        if item["kind"] != "trace" or item["bytes"] > 2_000_000:
            continue
        try:
            file_path = private_path(
                repository.config.artifact_storage_root,
                item["private_storage_key"],
            )
            raw = await asyncio.to_thread(file_path.read_bytes)
            if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                continue
            trace = json.loads(raw)
            if isinstance(trace, dict):
                trace_observations = trace.get("observations", {})
                trace_steps = {
                    str(value.get("step_id")): value.get("observation", {})
                    for value in trace.get("steps", [])
                    if isinstance(value, dict) and isinstance(value.get("step_id"), str)
                }
        except (OSError, ValueError, HTTPException):
            continue
    # UI plans record usage against plan_id; deferred worker generation may use run_id.
    usage = (
        (
            await repository.session.execute(
                select(db.model_usage)
                .where(
                    or_(
                        db.model_usage.c.run_id == run_id,
                        db.model_usage.c.plan_id == run["plan_id"],
                    )
                )
                .order_by(db.model_usage.c.created_at)
            )
        )
        .mappings()
        .all()
    )
    snapshot = (
        (
            await repository.session.execute(
                select(db.run_authorizations).where(db.run_authorizations.c.run_id == run_id)
            )
        )
        .mappings()
        .first()
    )
    policy_digest = (
        hashlib.sha256(
            json.dumps(snapshot["snapshot_json"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if snapshot
        else None
    )
    report = {
        "schema_version": "agentproof.run-report/1.0",
        "run": {
            "id": run_id,
            "created_at": run["created_at"],
            "status": run["status"],
            "verdict": run["verdict"],
            "goal": plan["goal"],
            "plan_version": plan["version"],
            "model": usage[-1]["model_actual"] if usage else None,
            "policy_digest": policy_digest,
        },
        "evidence_is_execution_only": True,
        "steps": [
            {
                "id": step["id"],
                "ordinal": step["ordinal"],
                "action": step["typed_action_json"],
                "expected": step["expected_json"],
                "observed": step["observed_json"],
                "page_observation": trace_steps.get(
                    str(step["typed_action_json"].get("id", "")), {}
                ),
                "status": step["status"],
                "started_at": step["started_at"],
                "ended_at": step["ended_at"],
            }
            for step in steps
        ],
        "findings": [dict(finding) for finding in findings],
        "artifacts": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "mime_type": item["mime_type"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
                "redaction_state": item["redaction_state"],
                "expires_at": item["expires_at"],
                "download_url": f"/api/v1/artifacts/{item['id']}/download",
            }
            for item in artifacts
        ],
        "observations": trace_observations,
    }
    return cast(dict[str, Any], redact_tree(report))


def _markdown_report(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        text_value = str(value if value is not None else "—")
        return (
            text_value.replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    run = report["run"]
    lines = [
        f"# Run report {cell(run['id'])}",
        "",
        f"- Date: {cell(run['created_at'])}",
        f"- Status: {cell(run['status'])}",
        f"- Verdict: {cell(run['verdict'])}",
        f"- Model: {cell(run['model'])}",
        f"- Policy digest: `{cell(run['policy_digest'])}`",
        f"- Goal: {cell(run['goal'])}",
        "",
        "## Observed steps",
        "",
        "| # | Action | Status | Expected | Observed |",
        "|---:|---|---|---|---|",
    ]
    for step in report["steps"]:
        observed = step["observed"]
        assertion = observed.get("assertion") if isinstance(observed, dict) else None
        lines.append(
            f"| {cell(step['ordinal'] + 1)} | {cell(step['action'].get('action'))} | "
            f"{cell(step['status'])} | {cell(step['expected'])} | {cell(assertion or observed)} |"
        )
    lines.extend(["", "## Findings", ""])
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(
                f"- **{cell(finding['classification'])} / {cell(finding['severity'])}:** "
                f"{cell(finding['title'])} (fingerprint `{cell(finding['fingerprint'])}`)"
            )
    else:
        lines.append("No findings were derived from observed execution evidence.")
    lines.extend(["", "## Artifacts", ""])
    for artifact in report["artifacts"]:
        lines.append(
            f"- {cell(artifact['kind'])} `{cell(artifact['sha256'])}` "
            f"({cell(artifact['bytes'])} bytes, expires {cell(artifact['expires_at'])}): "
            f"{cell(artifact['download_url'])}"
        )
    return "\n".join(lines) + "\n"


@router.get("/runs/{run_id}/report", response_model=RunReport)
async def run_report(
    run_id: UUID,
    request: Request,
    session: DB,
    user: User,
    format: Literal["json", "markdown"] = "json",
) -> Response:
    report = RunReport.model_validate(await _run_report(repo(request, session, user), run_id))
    report_data = report.model_dump(mode="json")
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if format == "markdown":
        return PlainTextResponse(
            _markdown_report(report_data),
            media_type="text/markdown",
            headers={**headers, "Content-Disposition": f'attachment; filename="run-{run_id}.md"'},
        )
    return JSONResponse(report_data, headers=headers)


def _locator_ts(target: dict[str, Any] | None) -> str | None:
    if target is None:
        return None
    name = json.dumps(target["name"], ensure_ascii=True)
    strategy = target["strategy"]
    if strategy == "role":
        return f"page.getByRole({json.dumps(target['role'])}, {{ name: {name}, exact: true }})"
    if strategy == "label":
        return f"page.getByLabel({name}, {{ exact: true }})"
    if strategy == "placeholder":
        return f"page.getByPlaceholder({name}, {{ exact: true }})"
    if strategy == "test_id":
        return f"page.getByTestId({name})"
    if strategy == "text":
        return f"page.getByText({name}, {{ exact: true }})"
    if strategy == "css" and re.fullmatch(
        r'body|main|h[1-3]|#[a-zA-Z][a-zA-Z0-9_-]{0,78}|\[data-testid="[a-zA-Z0-9_-]{1,80}"\]',
        target["name"],
    ):
        return f"page.locator({name})"
    return None


def _playwright_export(plan: dict[str, Any], report: dict[str, Any]) -> tuple[str, str]:
    from agentproof_api.plan_schema import QAPlanV1

    validated = QAPlanV1.model_validate(plan["plan_json"])
    run_id = str(report["run"]["id"])
    actual = {step["ordinal"]: step for step in report["steps"]}
    lines = [
        'import { test, expect } from "@playwright/test";',
        "",
        f"// AgentProof run {report['run']['id']} on {report['run']['created_at']}",
        f"// Model: {report['run']['model'] or 'not recorded'}; "
        f"policy digest: {report['run']['policy_digest']}",
        "test.use({ viewport: { width: 1280, height: 720 } });",
        "",
        "test('replay observed AgentProof journey', async ({ page }) => {",
        "  const baseURL = process.env.AGENTPROOF_BASE_URL;",
        "  if (!baseURL) throw new Error('Set AGENTPROOF_BASE_URL to the authorized test origin');",
    ]
    for ordinal, step in enumerate(validated.steps):
        evidence = actual.get(ordinal)
        if evidence is None or evidence["status"] not in {"completed", "failed"}:
            lines.append(
                f"  // TODO_MANUAL {step.id}: no completed Playwright evidence was recorded."
            )
            continue
        target = _locator_ts(step.target.model_dump(mode="json") if step.target else None)
        action = step.action
        if action in {"assert_text", "assert_count", "assert_url"} and step.assertion is None:
            lines.append(f"  // TODO_MANUAL {step.id}: assertion expectation was not recorded.")
            continue
        assertion = step.assertion
        if action == "navigate":
            lines.append(
                f"  await page.goto(new URL({json.dumps(step.url_path)}, baseURL).toString());"
            )
        elif target is None:
            lines.append(f"  // TODO_MANUAL {step.id}: locator strategy cannot be exported safely.")
        elif action == "click":
            lines.append(f"  await {target}.click();")
        elif action == "fill":
            value_ref = step.value_ref or ""
            env_name = {
                "credential:email": "AGENTPROOF_TEST_EMAIL",
                "credential:username": "AGENTPROOF_TEST_USERNAME",
                "credential:password": "AGENTPROOF_TEST_PASSWORD",  # pragma: allowlist secret
            }.get(value_ref)
            if env_name:
                lines.append(f"  await {target}.fill(process.env.{env_name} ?? '');")
            else:
                synthetic = {
                    "synthetic:email": "agentproof-test@example.invalid",
                    "synthetic:name": f"AgentProof-E2E-{run_id}",
                    "synthetic:text": f"AgentProof-E2E-{run_id}",
                }.get(value_ref)
                if synthetic is None:
                    lines.append(f"  // TODO_MANUAL {step.id}: value reference is not exportable.")
                else:
                    lines.append(f"  await {target}.fill({json.dumps(synthetic)});")
        elif action == "select":
            if (step.value_ref or "").startswith("credential:"):
                lines.append(
                    f"  // TODO_MANUAL {step.id}: credential-backed select needs a fixture mapping."
                )
            else:
                choice = {
                    "synthetic:option": "test",
                    "synthetic:yearly": "yearly",
                }.get(step.value_ref or "")
                if choice is None:
                    lines.append(f"  // TODO_MANUAL {step.id}: option mapping was not recorded.")
                else:
                    lines.append(f"  await {target}.selectOption({json.dumps(choice)});")
        elif action in {"check", "uncheck"}:
            lines.append(f"  await {target}.{action}();")
        elif action == "wait_for":
            lines.append(
                f"  await {target}.waitFor({{ state: 'visible', timeout: {step.timeout_ms} }});"
            )
        elif action == "assert_visible":
            lines.append(f"  await expect({target}).toBeVisible();")
        elif action == "assert_text":
            assert assertion is not None
            lines.append(f"  await expect({target}).toHaveText({json.dumps(assertion.expected)});")
        elif action == "assert_count":
            assert assertion is not None
            lines.append(f"  await expect({target}).toHaveCount({assertion.expected});")
        elif action == "assert_url":
            assert assertion is not None
            lines.append(
                f"  await expect(page).toHaveURL("
                f"new URL({json.dumps(assertion.expected)}, baseURL));"
            )
        elif action == "screenshot":
            lines.append(f"  await page.screenshot({{ path: '{step.id}.png' }});")
        else:
            lines.append(f"  // TODO_MANUAL {step.id}: unsupported action.")
        lines.append(
            f"  // Observed in original run: status={evidence['status']}; "
            f"assertion={json.dumps(evidence['observed'].get('assertion'), ensure_ascii=True)}"
        )
    lines.extend(["});", ""])
    readme = (
        "# AgentProof Playwright replay\n\n"
        f"Generated from actual run `{report['run']['id']}` at `{report['run']['created_at']}`.\n"
        f"Model: `{report['run']['model'] or 'not recorded'}`. "
        f"Policy digest: `{report['run']['policy_digest'] or 'not recorded'}`.\n"
        f"Original verdict: `{report['run']['verdict']}`. "
        "The script preserves recorded assertions; it does not claim a new pass.\n\n"
        "## Run\n\n"
        "Install Node.js, `@playwright/test`, and Chromium, then set `AGENTPROOF_BASE_URL` "
        "to the verified test origin.\n"
        "Set `AGENTPROOF_TEST_EMAIL`, `AGENTPROOF_TEST_USERNAME`, or "
        "`AGENTPROOF_TEST_PASSWORD` only to test-account values when referenced.\n\n"
        "```sh\n"
        "npm install --save-dev @playwright/test\n"
        "npx playwright install chromium\n"
        "npx playwright test\n"
        "```\n\n"
        "Steps marked `TODO_MANUAL` lacked a safely exportable completed execution record.\n"
        "\n## Original run evidence\n\n"
        "The links below use AgentProof's authenticated download API and require "
        "a signed-in session with access to the run. They are not public bearer links.\n\n"
        + (
            "\n".join(
                f"- [{artifact['kind']} {artifact['id']}]"
                f"(/api/v1/artifacts/{artifact['id']}/download) — "
                f"SHA-256 `{artifact['sha256']}`, expires `{artifact['expires_at']}`"
                for artifact in report["artifacts"]
            )
            if report["artifacts"]
            else "No artifacts were recorded in the original run."
        )
        + "\n"
    )
    return "\n".join(lines), readme


@router.get("/runs/{run_id}/export/playwright")
async def export_playwright(run_id: UUID, request: Request, session: DB, user: User) -> Response:
    repository = repo(request, session, user)
    run = await repository.run(run_id)
    plan = (
        (await session.execute(select(db.plans).where(db.plans.c.id == run["plan_id"])))
        .mappings()
        .one()
    )
    report = await _run_report(repository, run_id)
    spec, readme = _playwright_export(dict(plan), report)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agentproof-replay.spec.ts", spec)
        archive.writestr("README.md", readme)
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="agentproof-run-{run_id}-playwright.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/artifacts/{artifact_id}/download", response_class=Response)
async def download(artifact_id: UUID, request: Request, session: DB, user: User) -> Response:
    artifact = await repo(request, session, user).artifact(artifact_id)
    if artifact["redaction_state"] != "sanitized" or artifact["expires_at"] <= now():
        raise HTTPException(404, "NOT_FOUND")
    file = private_path(settings(request).artifact_storage_root, artifact["private_storage_key"])
    if not file.is_file():
        raise HTTPException(404, "NOT_FOUND")

    # Verify bounded content before sending; storage keys must be immutable.
    def bounded_read() -> bytes:
        with file.open("rb") as stream:
            return stream.read(int(artifact["bytes"]) + 1)

    content = await asyncio.to_thread(bounded_read)
    if (
        len(content) != artifact["bytes"]
        or hashlib.sha256(content).hexdigest() != artifact["sha256"]
    ):
        raise HTTPException(409, "ARTIFACT_INTEGRITY_ERROR")
    # Bytes are fixed after verification: a subsequent path replacement cannot change the response.
    from fastapi.responses import Response as ByteResponse

    return ByteResponse(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact_id}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get("/workspaces/{workspace_id}/audit-events", response_model=Page[AuditView])
async def audit_events(
    workspace_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[AuditView]:
    repository = repo(request, session, user)
    await repository.workspace(workspace_id, admin=True)
    rows, next_cursor = await repository.page(
        db.audit_events,
        select(db.audit_events).where(
            db.audit_events.c.workspace_id == workspace_id,
            db.audit_events.c.workspace_id.in_(
                select(db.memberships.c.workspace_id).where(db.memberships.c.user_id == user.id)
            ),
        ),
        f"audit:{workspace_id}",
        limit,
        cursor,
    )
    return Page(data=[view(AuditView, r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(
    run_id: UUID,
    request: Request,
    session: DB,
    user: User,
    after_id: Annotated[int, Query(ge=0)] = 0,
    follow: bool = True,
) -> StreamingResponse:
    await repo(request, session, user).run(run_id)
    last_id = request.headers.get("last-event-id")
    if last_id:
        try:
            parsed_id = int(last_id)
            if parsed_id < 0:
                raise ValueError
            after_id = max(after_id, parsed_id)
        except ValueError:
            raise HTTPException(400, "INVALID_EVENT_CURSOR") from None
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(503, "EVENT_STREAM_UNAVAILABLE")
    config = settings(request)
    stream_id = str(uuid4())
    stream_key = "agentproof:sse:leases"
    acquired = await redis.eval(
        "local now=tonumber(ARGV[1]); "
        "redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now); "
        "if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[2]) then return 0 end; "
        "redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[4]); "
        "redis.call('EXPIRE',KEYS[1],math.ceil(tonumber(ARGV[3]))); return 1",
        1,
        stream_key,
        str(time.time()),
        str(config.event_stream_concurrency_limit),
        str(config.event_stream_seconds + 10),
        stream_id,
    )
    if not acquired:
        raise HTTPException(429, "EVENT_STREAM_LIMIT")

    async def release_stream() -> None:
        await redis.zrem(stream_key, stream_id)

    from collections.abc import AsyncIterator

    async def stream() -> AsyncIterator[str]:
        cursor = after_id
        deadline = asyncio.get_running_loop().time() + settings(request).event_stream_seconds
        while True:
            if await request.is_disconnected():
                return
            async with request.app.state.session_factory() as connection:
                try:
                    stored, account = await identity_from_cookie(connection, request)
                    repository = Repository(
                        connection, Principal(account, stored["id"]), settings(request)
                    )
                    run = await repository.run(run_id)
                except HTTPException:
                    return
                rows = (
                    (
                        await connection.execute(
                            select(db.run_events)
                            .where(
                                db.run_events.c.run_id == run_id,
                                db.run_events.c.run_id.in_(repository.visible_runs()),
                                db.run_events.c.id > cursor,
                            )
                            .order_by(db.run_events.c.id)
                            .limit(100)
                        )
                    )
                    .mappings()
                    .all()
                )
                await connection.commit()
            for event in rows:
                cursor = event["id"]
                # Event names are fixed; only redacted worker/service payloads may enter this table.
                payload = json.dumps(dict(event), default=str)
                safe_type = (
                    event["event_type"]
                    if event["event_type"]
                    in {
                        "run.queued",
                        "run.planning",
                        "plan.generated",
                        "plan.approval_required",
                        "run.started",
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "artifact.created",
                        "finding.created",
                        "sandbox.policy_denied",
                        "run.investigating",
                        "run.completed",
                        "run.failed",
                        "run.cancel.requested",
                        "run.cancelled",
                        "run.timed_out",
                    }
                    else "run.event"
                )
                yield f"id: {cursor}\nevent: {safe_type}\ndata: {payload}\n\n"
            if (
                not follow
                or (run["status"] in TERMINAL and len(rows) < 100)
                or asyncio.get_running_loop().time() >= deadline
            ):
                return
            if len(rows) < 100:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        background=BackgroundTask(release_stream),
    )


@router.post("/projects/{project_id}/secrets", response_model=SecretView, status_code=201)
async def create_secret(
    project_id: UUID, body: SecretInput, request: Request, session: DB, user: User
) -> SecretView:
    repository = repo(request, session, user)
    project = await repository.project(project_id, admin=True)
    user.verified()
    secret_id = uuid4()
    payload = json.dumps(
        {
            "username": body.username.get_secret_value(),
            "password": body.password.get_secret_value(),
        },
        separators=(",", ":"),
    )
    ciphertext = seal(settings(request), payload, f"project-secret:{project_id}:{secret_id}")
    try:
        row = dict(
            (
                await session.execute(
                    insert(db.secret_refs)
                    .values(
                        id=secret_id,
                        project_id=project_id,
                        label=body.label,
                        ciphertext=ciphertext,
                        key_version=settings(request).encryption_key_version,
                        created_by=user.id,
                    )
                    .returning(
                        db.secret_refs.c.id,
                        db.secret_refs.c.created_at,
                        db.secret_refs.c.updated_at,
                        db.secret_refs.c.project_id,
                        db.secret_refs.c.label,
                        db.secret_refs.c.key_version,
                        db.secret_refs.c.rotated_at,
                    )
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError:
        raise HTTPException(409, "LABEL_EXISTS") from None
    await repository.audit(
        project["workspace_id"], "secret.created", "secret_ref", secret_id, {"label": body.label}
    )
    return view(SecretView, row)


@router.get("/projects/{project_id}/secrets", response_model=Page[SecretView])
async def list_secrets(
    project_id: UUID,
    request: Request,
    session: DB,
    user: User,
    limit: Limit = 20,
    cursor: str | None = None,
) -> Page[SecretView]:
    repository = repo(request, session, user)
    await repository.project(project_id)
    columns = (
        db.secret_refs.c.id,
        db.secret_refs.c.created_at,
        db.secret_refs.c.updated_at,
        db.secret_refs.c.project_id,
        db.secret_refs.c.label,
        db.secret_refs.c.key_version,
        db.secret_refs.c.rotated_at,
    )
    rows, next_cursor = await repository.page(
        db.secret_refs,
        select(*columns).where(
            db.secret_refs.c.project_id == project_id,
            db.secret_refs.c.project_id.in_(repository.visible_projects()),
        ),
        f"secrets:{project_id}",
        limit,
        cursor,
    )
    return Page(data=[view(SecretView, row) for row in rows], next_cursor=next_cursor)


@router.delete("/projects/{project_id}/secrets/{secret_id}", status_code=204)
async def delete_secret(
    project_id: UUID, secret_id: UUID, request: Request, session: DB, user: User
) -> Response:
    repository = repo(request, session, user)
    project = await repository.project(project_id, admin=True)
    user.verified()
    removed = (
        await session.execute(
            delete(db.secret_refs)
            .where(
                db.secret_refs.c.id == secret_id,
                db.secret_refs.c.project_id == project_id,
                db.secret_refs.c.project_id.in_(repository.visible_projects()),
            )
            .returning(db.secret_refs.c.id, db.secret_refs.c.label)
        )
    ).first()
    if removed is None:
        raise HTTPException(404, "NOT_FOUND")
    await repository.audit(
        project["workspace_id"],
        "secret.deleted",
        "secret_ref",
        secret_id,
        {"label": removed.label},
    )
    return Response(status_code=204)
