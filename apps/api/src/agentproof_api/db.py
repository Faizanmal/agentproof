"""Relational schema. Runtime code never creates tables; Alembic owns DDL."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def common() -> list[Column[Any]]:
    return [
        Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
        Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=func.clock_timestamp(),
        ),
        Column(
            "updated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=func.clock_timestamp(),
        ),
    ]


def ref(
    name: str, target: str, *, nullable: bool = False, ondelete: str = "RESTRICT"
) -> Column[Any]:
    return Column(name, Uuid, ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


def js(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(
        name, JSONB, nullable=nullable, server_default=None if nullable else text("'{}'::jsonb")
    )


def choices(column: str, values: str) -> CheckConstraint:
    return CheckConstraint(
        column + " IN (" + ",".join(repr(v) for v in values.split()) + ")", name=column
    )


users = Table(
    "users",
    metadata,
    *common(),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("email_verified_at", DateTime(timezone=True)),
    Column("disabled_at", DateTime(timezone=True)),
    Column("last_login_at", DateTime(timezone=True)),
    CheckConstraint("email = lower(email) AND position('@' in email) > 1", name="normalized_email"),
)
sessions = Table(
    "sessions",
    metadata,
    *common(),
    ref("user_id", "users.id", ondelete="CASCADE"),
    Column("family_id", Uuid, nullable=False, index=True),
    Column("refresh_token_hash", String(64), nullable=False, unique=True),
    Column("access_token_hash", String(64), nullable=False, unique=True),
    Column("access_expires_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("revoked_at", DateTime(timezone=True)),
    Column("user_agent_hash", String(64), nullable=False),
)
email_verifications = Table(
    "email_verifications",
    metadata,
    *common(),
    ref("user_id", "users.id", ondelete="CASCADE"),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("used_at", DateTime(timezone=True)),
)
password_resets = Table(
    "password_resets",
    metadata,
    *common(),
    ref("user_id", "users.id", ondelete="CASCADE"),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("used_at", DateTime(timezone=True)),
)
mail_outbox = Table(
    "mail_outbox",
    metadata,
    *common(),
    ref("user_id", "users.id", ondelete="CASCADE"),
    Column("purpose", String(32), nullable=False, server_default="verification"),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("key_version", Integer, nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    choices("purpose", "verification password_reset"),
)
workspaces = Table(
    "workspaces",
    metadata,
    *common(),
    Column("name", String(120), nullable=False),
    ref("owner_user_id", "users.id"),
)
memberships = Table(
    "memberships",
    metadata,
    *common(),
    ref("workspace_id", "workspaces.id", ondelete="CASCADE"),
    ref("user_id", "users.id", ondelete="CASCADE"),
    Column("role", String(10), nullable=False),
    choices("role", "owner admin member viewer"),
    UniqueConstraint("workspace_id", "user_id"),
)
projects = Table(
    "projects",
    metadata,
    *common(),
    ref("workspace_id", "workspaces.id", ondelete="CASCADE"),
    Column("name", String(120), nullable=False),
    Column("base_url", Text, nullable=False),
    Column("status", String(16), nullable=False, server_default="active"),
    choices("status", "active archived"),
    ref("created_by", "users.id"),
)
authorized_origins = Table(
    "authorized_origins",
    metadata,
    *common(),
    ref("project_id", "projects.id", ondelete="CASCADE"),
    Column("normalized_origin", Text, nullable=False),
    Column("method", String(32), nullable=False),
    Column("proof_hash", String(64), nullable=False),
    Column("verified_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    js("policy_json"),
    UniqueConstraint("project_id", "normalized_origin"),
)
secret_refs = Table(
    "secret_refs",
    metadata,
    *common(),
    ref("project_id", "projects.id", ondelete="CASCADE"),
    Column("label", String(80), nullable=False),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("key_version", Integer, nullable=False),
    ref("created_by", "users.id"),
    Column("rotated_at", DateTime(timezone=True)),
    CheckConstraint("key_version > 0", name="positive_version"),
    UniqueConstraint("project_id", "label"),
)
plans = Table(
    "plans",
    metadata,
    *common(),
    ref("project_id", "projects.id", ondelete="CASCADE"),
    ref("created_by", "users.id"),
    Column("version", Integer, nullable=False),
    Column("goal", Text, nullable=False),
    js("plan_json", nullable=True),
    js("scope_json"),
    js("authorization_json"),
    Column("authorization_digest", String(64)),
    Column("authorization_signature", String(64)),
    Column("rejection_code", String(64)),
    Column("risk_level", String(12), nullable=False),
    choices("risk_level", "low medium high"),
    Column("status", String(16), nullable=False),
    choices("status", "draft approved rejected"),
    ref("approved_by", "users.id", nullable=True),
    Column("approved_at", DateTime(timezone=True)),
    UniqueConstraint("project_id", "version"),
    UniqueConstraint("id", "project_id"),
    CheckConstraint("version > 0", name="positive_version"),
    CheckConstraint(
        "status != 'approved' OR (approved_by IS NOT NULL AND approved_at IS "
        "NOT NULL AND plan_json IS NOT NULL)",
        name="approval_evidence",
    ),
)
runs = Table(
    "runs",
    metadata,
    *common(),
    ref("project_id", "projects.id", ondelete="CASCADE"),
    Column("plan_id", Uuid, nullable=False, index=True),
    ref("created_by", "users.id"),
    Column("idempotency_key", String(128), nullable=False),
    Column("status", String(24), nullable=False, server_default="queued"),
    choices(
        "status",
        "queued planning awaiting_approval running investigating completed "
        "failed cancelled timed_out",
    ),
    Column("verdict", String(16)),
    choices("verdict", "PASS FAIL ERROR INCONCLUSIVE"),
    Column("error_code", String(64)),
    js("limits_json"),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("worker_lease_id", Uuid),
    Column("worker_lease_expires_at", DateTime(timezone=True), index=True),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("cancel_requested_at", DateTime(timezone=True)),
    js("summary_json"),
    ForeignKeyConstraint(
        ["plan_id", "project_id"], ["plans.id", "plans.project_id"], ondelete="CASCADE"
    ),
    UniqueConstraint("project_id", "idempotency_key"),
    CheckConstraint(
        "(status IN ('completed','failed','cancelled','timed_out')) = (finished_at IS NOT NULL)",
        name="terminal_timestamp",
    ),
    CheckConstraint(
        "verdict IS NULL OR status IN ('completed','failed','cancelled','timed_out')",
        name="terminal_verdict",
    ),
    CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
)
run_steps = Table(
    "run_steps",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    Column("ordinal", Integer, nullable=False),
    js("typed_action_json"),
    js("expected_json"),
    js("observed_json"),
    Column("status", String(16), nullable=False),
    choices("status", "pending running completed failed skipped"),
    Column("started_at", DateTime(timezone=True)),
    Column("ended_at", DateTime(timezone=True)),
    Column("error_code", String(64)),
    UniqueConstraint("run_id", "ordinal"),
    UniqueConstraint("id", "run_id"),
    CheckConstraint("ordinal >= 0 AND ordinal < 50", name="ordinal_bounds"),
)
run_events = Table(
    "run_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    Column("event_type", String(64), nullable=False),
    js("payload_json"),
    Column("published_at", DateTime(timezone=True)),
    Column("publish_attempts", Integer, nullable=False, server_default="0"),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    ),
    CheckConstraint("publish_attempts >= 0", name="publish_attempts_nonnegative"),
)
Index(
    "ix_run_events_pending_outbox",
    run_events.c.id,
    postgresql_where=text("published_at IS NULL"),
)
sandbox_leases = Table(
    "sandbox_leases",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    Column("broker_sandbox_id", String(128), unique=True),
    Column("broker_job_id", Uuid, unique=True),
    Column("broker_nonce", String(128)),
    Column("last_broker_event_id", Integer, nullable=False, server_default="0"),
    Column("status", String(16), nullable=False),
    choices("status", "pending active terminated"),
    Column("deadline_at", DateTime(timezone=True), nullable=False, index=True),
    js("limits_json"),
    Column("terminated_at", DateTime(timezone=True)),
    Column("termination_reason", String(64)),
    UniqueConstraint("run_id"),
)
artifacts = Table(
    "artifacts",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    Column("step_id", Uuid, nullable=True),
    Column("kind", String(32), nullable=False),
    Column("private_storage_key", Text, nullable=False, unique=True),
    Column("sha256", String(64), nullable=False),
    Column("mime_type", String(100), nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("redaction_state", String(16), nullable=False),
    choices("redaction_state", "pending sanitized rejected"),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    ForeignKeyConstraint(
        ["step_id", "run_id"], ["run_steps.id", "run_steps.run_id"], ondelete="CASCADE"
    ),
    CheckConstraint("bytes >= 0 AND bytes <= 268435456", name="byte_bounds"),
)
findings = Table(
    "findings",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    Column("fingerprint", String(64), nullable=False),
    Column("title", String(240), nullable=False),
    Column("severity", String(12), nullable=False),
    choices("severity", "info low medium high critical"),
    Column("confidence", Numeric(4, 3), nullable=False),
    js("expected_json"),
    js("observed_json"),
    js("repro_steps_json"),
    Column("verdict", String(16), nullable=False),
    choices("verdict", "PASS FAIL ERROR INCONCLUSIVE"),
    Column("status", String(16), nullable=False, server_default="OPEN"),
    choices("status", "OPEN RESOLVED ACCEPTED"),
    Column("classification", String(16), nullable=False, server_default="INCONCLUSIVE"),
    choices("classification", "CONFIRMED LIKELY INCONCLUSIVE"),
    js("environment_json"),
    Column("hypotheses_json", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("evidence_artifact_ids", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column(
        "first_seen_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    ),
    Column("occurrence_count", Integer, nullable=False, server_default="1"),
    choices("status", "OPEN RESOLVED ACCEPTED"),
    choices("classification", "CONFIRMED LIKELY INCONCLUSIVE"),
    UniqueConstraint("run_id", "fingerprint"),
    CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_bounds"),
    CheckConstraint("occurrence_count > 0", name="occurrences_nonnegative"),
)
artifact_redaction_audits = Table(
    "artifact_redaction_audits",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    ref("artifact_id", "artifacts.id", ondelete="CASCADE"),
    Column("source_sha256", String(64), nullable=False),
    Column("result_sha256", String(64), nullable=False),
    Column("redaction_policy", String(64), nullable=False),
    js("redactions_json"),
)
model_usage = Table(
    "model_usage",
    metadata,
    *common(),
    ref("run_id", "runs.id", nullable=True, ondelete="SET NULL"),
    Column("model_requested", String(160), nullable=False),
    Column("model_actual", String(160), nullable=False),
    ref("plan_id", "plans.id", nullable=True, ondelete="CASCADE"),
    Column("attempt", Integer),
    Column("error_code", String(64)),
    Column("prompt_tokens", Integer, nullable=True),
    Column("completion_tokens", Integer, nullable=True),
    Column("cost_estimate", Numeric(16, 8)),
    CheckConstraint(
        "prompt_tokens >= 0 AND completion_tokens >= 0 AND (cost_estimate IS "
        "NULL OR cost_estimate >= 0)",
        name="usage_bounds",
    ),
)
audit_events = Table(
    "audit_events",
    metadata,
    *common(),
    ref("workspace_id", "workspaces.id", ondelete="CASCADE"),
    ref("actor_id", "users.id"),
    Column("action", String(64), nullable=False),
    Column("resource_type", String(40), nullable=False),
    Column("resource_id", Uuid, nullable=False),
    js("details_redacted"),
)
artifact_cleanup_jobs = Table(
    "artifact_cleanup_jobs",
    metadata,
    *common(),
    Column("private_storage_key", Text, nullable=False, unique=True),
    Column("completed_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, server_default="0"),
    CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
)
for table in (plans, runs, authorized_origins, secret_refs):
    Index(
        "ix_" + table.name + "_project_created",
        table.c.project_id,
        table.c.created_at.desc(),
        table.c.id.desc(),
    )
Index("ix_run_events_run_event", run_events.c.run_id, run_events.c.id)
Index(
    "ix_audit_workspace_created",
    audit_events.c.workspace_id,
    audit_events.c.created_at.desc(),
    audit_events.c.id.desc(),
)

Index(
    "uq_memberships_one_owner",
    memberships.c.workspace_id,
    unique=True,
    postgresql_where=memberships.c.role == "owner",
)
runs.append_constraint(
    CheckConstraint("status != 'completed' OR verdict IS NOT NULL", name="completed_verdict")
)

verification_challenges = Table(
    "verification_challenges",
    metadata,
    *common(),
    ref("project_id", "projects.id", ondelete="CASCADE"),
    Column("normalized_origin", Text, nullable=False),
    Column("method", String(16), nullable=False),
    choices("method", "https-file dns-txt demo"),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("consumed_at", DateTime(timezone=True)),
)
project_policies = Table(
    "project_policies",
    metadata,
    *common(),
    ref("project_id", "projects.id", ondelete="CASCADE"),
    Column("version", Integer, nullable=False, server_default="1"),
    js("policy_json"),
    UniqueConstraint("project_id"),
)
run_authorizations = Table(
    "run_authorizations",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    js("snapshot_json"),
    Column("signature", String(64), nullable=False),
    UniqueConstraint("run_id"),
)

credential_deliveries = Table(
    "credential_deliveries",
    metadata,
    *common(),
    ref("run_id", "runs.id", ondelete="CASCADE"),
    ref("secret_id", "secret_refs.id", ondelete="CASCADE"),
    Column("broker_sandbox_id", String(128), nullable=False),
    UniqueConstraint("run_id", "secret_id"),
)

plan_events = Table(
    "plan_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    ref("plan_id", "plans.id", ondelete="CASCADE"),
    Column("event_type", String(64), nullable=False),
    js("payload_json"),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    ),
)
Index("ix_plan_events_plan_event", plan_events.c.plan_id, plan_events.c.id)
