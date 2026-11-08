"""private run evidence and validated finding metadata"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e542c1a708bf"  # pragma: allowlist secret -- migration identifier
down_revision = "ab91d7f2046c"  # pragma: allowlist secret -- migration identifier
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
    )
    op.add_column(
        "findings",
        sa.Column("classification", sa.String(16), server_default="INCONCLUSIVE", nullable=False),
    )
    op.add_column(
        "findings",
        sa.Column(
            "environment_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "hypotheses_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "evidence_artifact_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.add_column(
        "findings", sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_findings_occurrences_nonnegative"), "findings", "occurrence_count > 0"
    )
    op.create_check_constraint(
        op.f("ck_findings_classification"),
        "findings",
        "classification IN ('CONFIRMED','LIKELY','INCONCLUSIVE')",
    )
    op.create_check_constraint(
        op.f("ck_findings_status"), "findings", "status IN ('OPEN','RESOLVED','ACCEPTED')"
    )
    op.create_table(
        "artifact_redaction_audits",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("redaction_policy", sa.String(64), nullable=False),
        sa.Column(
            "redactions_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_artifact_redaction_audits_artifact_id_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_artifact_redaction_audits_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact_redaction_audits")),
    )
    op.create_index(
        op.f("ix_artifact_redaction_audits_run_id"),
        "artifact_redaction_audits",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_artifact_redaction_audits_artifact_id"),
        "artifact_redaction_audits",
        ["artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_artifact_redaction_audits_artifact_id"),
        table_name="artifact_redaction_audits",
    )
    op.drop_index(
        op.f("ix_artifact_redaction_audits_run_id"), table_name="artifact_redaction_audits"
    )
    op.drop_table("artifact_redaction_audits")
    op.drop_constraint(op.f("ck_findings_classification"), "findings", type_="check")
    op.drop_constraint(op.f("ck_findings_status"), "findings", type_="check")
    op.drop_constraint(op.f("ck_findings_occurrences_nonnegative"), "findings", type_="check")
    op.drop_column("findings", "occurrence_count")
    op.drop_column("findings", "first_seen_at")
    op.drop_column("findings", "evidence_artifact_ids")
    op.drop_column("findings", "hypotheses_json")
    op.drop_column("findings", "environment_json")
    op.drop_column("findings", "classification")
    op.drop_column("findings", "status")
