import asyncio
import hmac
import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agentproof_api import db
from agentproof_api.api_models import (
    OriginChallenge,
    OriginInput,
    OriginView,
    Page,
    VerifyTargetInput,
)
from agentproof_api.auth import DB, User, rate_limit, settings
from agentproof_api.plan_schema import TestScope
from agentproof_api.policy import TargetPolicy, snapshot_signature
from agentproof_api.repository import Repository
from agentproof_api.security import digest, now, token
from agentproof_api.settings import Settings
from agentproof_api.targets import (
    canonical_url,
    connection_targets,
    demo_allowed,
    fetch_proof,
    fetch_txt,
)

router = APIRouter(prefix="/api/v1")


@router.post(
    "/projects/{project_id}/origins",
    response_model=OriginChallenge,
    status_code=201,
    deprecated=True,
)
@router.post(
    "/projects/{project_id}/verification-challenges",
    response_model=OriginChallenge,
    status_code=201,
)
async def challenge(
    project_id: UUID, body: OriginInput, request: Request, session: DB, user: User
) -> OriginChallenge:
    config = settings(request)
    repository = Repository(session, user, config)
    project = await repository.project(project_id, admin=True)
    user.verified()
    await rate_limit(request, "ownership-challenge", str(user.id), 10, 600)
    normalized = canonical_url(body.origin, origin_only=True, allow_ip=body.method == "demo")[0]
    if canonical_url(project["base_url"], allow_ip=body.method == "demo")[0] != normalized:
        raise HTTPException(409, "ORIGIN_MISMATCH")
    if body.method == "demo":
        if await repository.workspace(project["workspace_id"]) != "owner" or not demo_allowed(
            normalized, config
        ):
            raise HTTPException(403, "DEMO_SCOPE_DENIED")
    await asyncio.to_thread(connection_targets, normalized, config, demo=body.method == "demo")
    raw = token()
    row = (
        (
            await session.execute(
                insert(db.verification_challenges)
                .values(
                    project_id=project_id,
                    normalized_origin=normalized,
                    method=body.method,
                    token_hash=digest(raw),
                    expires_at=now() + timedelta(seconds=config.challenge_ttl_seconds),
                )
                .returning(db.verification_challenges)
            )
        )
        .mappings()
        .one()
    )
    instructions = (
        "Owner attestation is required; only the administrative demo allowlist is eligible."
        if body.method == "demo"
        else "Publish the exact token at "
        + (
            "_agentproof-verification." + (urlsplit(normalized).hostname or "") + " TXT"
            if body.method == "dns-txt"
            else normalized + "/.well-known/agentproof-verification"
        )
    )
    await repository.audit(
        project["workspace_id"], "origin.challenged", "verification_challenge", row["id"]
    )
    return OriginChallenge(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        project_id=project_id,
        normalized_origin=normalized,
        method=body.method,
        verified_at=None,
        expires_at=row["expires_at"],
        policy_json={},
        challenge=raw,
        instructions=instructions,
    )


@router.post("/projects/{project_id}/verify-target", response_model=OriginView)
async def verify(
    project_id: UUID, body: VerifyTargetInput, request: Request, session: DB, user: User
) -> OriginView:
    config = settings(request)
    repository = Repository(session, user, config)
    project = await repository.project(project_id, admin=True)
    user.verified()
    await rate_limit(request, "verify-target", str(user.id), 10, 600)
    row = (
        (
            await session.execute(
                select(db.verification_challenges)
                .where(
                    db.verification_challenges.c.id == body.origin_id,
                    db.verification_challenges.c.project_id == project_id,
                    db.verification_challenges.c.project_id.in_(repository.visible_projects()),
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "NOT_FOUND")
    if row["consumed_at"] is not None:
        raise HTTPException(409, "CHALLENGE_REUSED")
    if row["expires_at"] <= now():
        raise HTTPException(409, "CHALLENGE_EXPIRED")
    if row["method"] == "demo":
        if not body.attestation or await repository.workspace(project["workspace_id"]) != "owner":
            raise HTTPException(403, "OWNER_ATTESTATION_REQUIRED")
        await asyncio.to_thread(connection_targets, row["normalized_origin"], config, demo=True)
    else:
        observed = (
            await fetch_txt(row["normalized_origin"], config)
            if row["method"] == "dns-txt"
            else [await fetch_proof(row["normalized_origin"], config=config)]
        )
        if not any(hmac.compare_digest(digest(value), row["token_hash"]) for value in observed):
            raise HTTPException(409, "VERIFICATION_FAILED")
    await session.execute(
        update(db.verification_challenges)
        .where(db.verification_challenges.c.id == row["id"])
        .values(consumed_at=now())
    )
    values = dict(
        method=row["method"],
        proof_hash=row["token_hash"],
        verified_at=now(),
        expires_at=now() + timedelta(seconds=config.authorization_ttl_seconds),
        policy_json={"schema_version": 1},
    )
    statement = pg_insert(db.authorized_origins).values(
        project_id=project_id, normalized_origin=row["normalized_origin"], **values
    )
    grant = (
        (
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        db.authorized_origins.c.project_id,
                        db.authorized_origins.c.normalized_origin,
                    ],
                    set_=values,
                ).returning(db.authorized_origins)
            )
        )
        .mappings()
        .one()
    )
    default = TargetPolicy(authorized_origins=[row["normalized_origin"]])
    await session.execute(
        pg_insert(db.project_policies)
        .values(project_id=project_id, policy_json=default.model_dump(mode="json"))
        .on_conflict_do_nothing(index_elements=[db.project_policies.c.project_id])
    )
    await repository.audit(
        project["workspace_id"],
        "origin.verified",
        "authorized_origin",
        grant["id"],
        {"method": row["method"]},
    )
    return OriginView.model_validate({k: grant[k] for k in OriginView.model_fields})


@router.get("/projects/{project_id}/origins", response_model=Page[OriginView], deprecated=True)
@router.get("/projects/{project_id}/authorized-origins", response_model=Page[OriginView])
async def grants(project_id: UUID, request: Request, session: DB, user: User) -> Page[OriginView]:
    repository = Repository(session, user, settings(request))
    await repository.project(project_id)
    rows = (
        (
            await session.execute(
                select(db.authorized_origins)
                .where(
                    db.authorized_origins.c.project_id == project_id,
                    db.authorized_origins.c.project_id.in_(repository.visible_projects()),
                )
                .order_by(db.authorized_origins.c.created_at)
                .limit(100)
            )
        )
        .mappings()
        .all()
    )
    return Page(
        data=[OriginView.model_validate({k: r[k] for k in OriginView.model_fields}) for r in rows]
    )


@router.get("/projects/{project_id}/target-policy", response_model=TargetPolicy)
async def get_policy(project_id: UUID, request: Request, session: DB, user: User) -> TargetPolicy:
    repository = Repository(session, user, settings(request))
    await repository.project(project_id)
    row = (
        await session.execute(
            select(db.project_policies.c.policy_json).where(
                db.project_policies.c.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return TargetPolicy.model_validate(row)
    origins: list[str] = list(
        (
            await session.execute(
                select(db.authorized_origins.c.normalized_origin).where(
                    db.authorized_origins.c.project_id == project_id,
                    db.authorized_origins.c.verified_at.is_not(None),
                    db.authorized_origins.c.expires_at > now(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not origins:
        raise HTTPException(404, "NOT_FOUND")
    return TargetPolicy(authorized_origins=origins)


async def validate_policy(
    repository: Repository, project_id: UUID, policy: TargetPolicy
) -> list[dict[str, Any]]:
    grants = [
        dict(r)
        for r in (
            await repository.session.execute(
                select(db.authorized_origins).where(
                    db.authorized_origins.c.project_id == project_id,
                    db.authorized_origins.c.verified_at.is_not(None),
                    db.authorized_origins.c.expires_at > now(),
                )
            )
        )
        .mappings()
        .all()
    ]
    if not set(policy.authorized_origins) <= {r["normalized_origin"] for r in grants}:
        raise HTTPException(409, "UNVERIFIED_POLICY_ORIGIN")
    for grant in grants:
        if grant["normalized_origin"] in policy.authorized_origins:
            await asyncio.to_thread(
                connection_targets,
                grant["normalized_origin"],
                repository.config,
                demo=grant["method"] == "demo",
            )
    if policy.side_effect_rules.fixture_forms:
        if not policy.side_effect_rules.fixture_paths or not all(
            demo_allowed(v, repository.config) for v in policy.authorized_origins
        ):
            raise HTTPException(403, "FIXTURE_PERMISSION_REQUIRED")
        if not all(
            v.startswith(("/demo/", "/__agentproof_fixture__/"))
            and ".." not in v
            and "%" not in v
            and chr(92) not in v
            for v in policy.side_effect_rules.fixture_paths
        ):
            raise HTTPException(422, "INVALID_FIXTURE_PATH")
    if policy.credential_ref_ids:
        ids: list[UUID] = list(
            (
                await repository.session.execute(
                    select(db.secret_refs.c.id).where(
                        db.secret_refs.c.project_id == project_id,
                        db.secret_refs.c.id.in_(policy.credential_ref_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        if set(ids) != set(policy.credential_ref_ids):
            raise HTTPException(404, "NOT_FOUND")
    return grants


@router.patch("/projects/{project_id}/target-policy", response_model=TargetPolicy)
async def patch_policy(
    project_id: UUID, body: TargetPolicy, request: Request, session: DB, user: User
) -> TargetPolicy:
    repository = Repository(session, user, settings(request))
    project = await repository.project(project_id, admin=True)
    user.verified()
    await validate_policy(repository, project_id, body)
    statement = pg_insert(db.project_policies).values(
        project_id=project_id, policy_json=body.model_dump(mode="json")
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[db.project_policies.c.project_id],
            set_={
                "policy_json": body.model_dump(mode="json"),
                "version": db.project_policies.c.version + 1,
            },
        )
    )
    await session.execute(
        update(db.plans)
        .where(db.plans.c.project_id == project_id, db.plans.c.status == "approved")
        .values(status="rejected", rejection_code="PLAN_AUTHORIZATION_CHANGED")
    )
    await repository.audit(project["workspace_id"], "policy.updated", "project", project_id)
    return body


async def capture_snapshot(
    repository: Repository, project: dict[str, Any], run: dict[str, Any], config: Settings
) -> None:
    row = (
        (
            await repository.session.execute(
                select(db.project_policies).where(db.project_policies.c.project_id == project["id"])
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(409, "TARGET_POLICY_REQUIRED")
    policy = TargetPolicy.model_validate(row["policy_json"])
    grants = await validate_policy(repository, project["id"], policy)
    reviewed = (
        (
            await repository.session.execute(
                select(db.plans).where(
                    db.plans.c.id == run["plan_id"], db.plans.c.project_id == project["id"]
                )
            )
        )
        .mappings()
        .one()
    )
    scope = TestScope.model_validate(reviewed["scope_json"])
    base = canonical_url(project["base_url"], allow_ip=True)[0]
    policy = policy.model_copy(
        update={
            "authorized_origins": [base],
            "credential_ref_ids": [scope.credential_ref_id] if scope.credential_ref_id else [],
        }
    )
    limits = dict(run["limits_json"])
    limits["browser_actions"] = min(limits["browser_actions"], policy.max_actions)
    limits["duration_seconds"] = min(limits["duration_seconds"], policy.max_duration_seconds)
    await repository.session.execute(
        update(db.runs).where(db.runs.c.id == run["id"]).values(limits_json=limits)
    )
    run["limits_json"] = limits
    payload = {
        "schema_version": 1,
        "run_id": str(run["id"]),
        "project_id": str(project["id"]),
        "policy_version": row["version"],
        "test_scope": scope.model_dump(mode="json"),
        "approved_plan_id": str(reviewed["id"]),
        "approved_plan_version": reviewed["version"],
        "approved_plan_digest": digest(
            json.dumps(reviewed["plan_json"], sort_keys=True, separators=(",", ":"))
        ),
        "policy": policy.model_dump(mode="json"),
        "valid_until": min(
            r["expires_at"] for r in grants if r["normalized_origin"] in policy.authorized_origins
        ).isoformat(),
    }
    await repository.session.execute(
        insert(db.run_authorizations).values(
            run_id=run["id"], snapshot_json=payload, signature=snapshot_signature(config, payload)
        )
    )
