"""Wall-clock timestamps and stronger terminal/ownership constraints."""

import sqlalchemy as sa
from alembic import op

revision = "6a2329149ea4"
down_revision = "aaf600fe9fda"
branch_labels = None
depends_on = None

TABLES = [
    "users",
    "sessions",
    "email_verifications",
    "mail_outbox",
    "workspaces",
    "memberships",
    "projects",
    "authorized_origins",
    "secret_refs",
    "plans",
    "runs",
    "run_steps",
    "sandbox_leases",
    "artifacts",
    "findings",
    "model_usage",
    "audit_events",
    "artifact_cleanup_jobs",
]


def upgrade() -> None:
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(table, column, server_default=sa.text("clock_timestamp()"))
    op.alter_column("run_events", "created_at", server_default=sa.text("clock_timestamp()"))
    op.create_index(
        "uq_memberships_one_owner",
        "memberships",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_check_constraint(
        "completed_verdict", "runs", "status != 'completed' OR verdict IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint("completed_verdict", "runs", type_="check")
    op.drop_index("uq_memberships_one_owner", table_name="memberships")
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(table, column, server_default=sa.text("now()"))
    op.alter_column("run_events", "created_at", server_default=sa.text("now()"))
